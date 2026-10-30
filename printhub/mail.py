# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import fcntl
import json
import os
import smtplib
import ssl
import tempfile
from dataclasses import dataclass
from email.errors import MessageError
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path

from printhub.config import Config

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
RECENT_LIMIT = 20
# A stalled server must not hold the request thread forever.
SMTP_TIMEOUT = 30


class AttachmentTooLarge(Exception):
    pass


class InvalidRecipient(Exception):
    pass


class MailError(Exception):
    pass


@dataclass
class Attachment:
    filename: str
    data: bytes
    mime: str


def send(
    cfg: Config,
    to: list[str],
    subject: str,
    attachments: list[Attachment],
    *,
    body: str = "Attached.",
    ssl_context: ssl.SSLContext | None = None,
    html: str | None = None,
) -> None:
    if sum(len(a.data) for a in attachments) > MAX_ATTACHMENT_BYTES:
        raise AttachmentTooLarge(f"attachments exceed {MAX_ATTACHMENT_BYTES} bytes")
    if not to:
        raise InvalidRecipient("")
    for addr in to:
        try:
            Address(addr_spec=addr)
        except (ValueError, MessageError):
            raise InvalidRecipient(addr) from None

    msg = EmailMessage()
    msg["From"] = cfg.mail_from
    msg["To"] = ", ".join(to)
    # EmailMessage rejects CR/LF in headers; a newline here would also inject headers.
    msg["Subject"] = subject.replace("\r", " ").replace("\n", " ")
    msg.set_content(body)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    for a in attachments:
        maintype, _, subtype = a.mime.partition("/")
        msg.add_attachment(a.data, maintype=maintype, subtype=subtype, filename=a.filename)

    ctx = ssl_context or ssl.create_default_context()
    refused: dict[str, tuple[int, bytes]] = {}
    try:
        if cfg.smtp_security == "tls":
            smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=SMTP_TIMEOUT, context=ctx)
        else:
            smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=SMTP_TIMEOUT)
        with smtp:
            # Raises when the server does not offer STARTTLS, so credentials are never sent in clear.
            if cfg.smtp_security == "starttls":
                smtp.starttls(context=ctx)
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            refused = smtp.send_message(msg, to_addrs=to)
    except smtplib.SMTPRecipientsRefused as e:
        refused = e.recipients
    except (OSError, smtplib.SMTPException) as e:
        raise MailError(_server_text(e)) from e
    # Partial refusal: send_message() returns the refused addresses instead of raising.
    if refused:
        raise MailError(
            "; ".join(f"{addr}: {_reply(code, reply)}" for addr, (code, reply) in refused.items())
        )


def _server_text(e: Exception) -> str:
    if isinstance(e, smtplib.SMTPResponseException):
        return _reply(e.smtp_code, e.smtp_error)
    return str(e)


def _reply(code: int, reply: bytes) -> str:
    return f"{code} {reply.decode(errors='replace')}"


class RecentRecipients:
    def __init__(self, path: Path):
        self.path = path

    def list(self) -> list[str]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text())

    def add(self, addrs: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            seen: set[str] = set()
            merged: list[str] = []
            for addr in [*addrs, *self.list()]:
                if addr.lower() not in seen:
                    seen.add(addr.lower())
                    merged.append(addr)
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, suffix=".tmp", delete=False) as out:
                    tmp = Path(out.name)
                    json.dump(merged[:RECENT_LIMIT], out)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, self.path)
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
