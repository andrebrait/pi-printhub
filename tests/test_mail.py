# SPDX-License-Identifier: AGPL-3.0-only
import re
import socket
import ssl
import subprocess
from dataclasses import replace
from email import message_from_bytes, policy

import pytest
from aiosmtpd.controller import Controller

from printhub.mail import (
    MAX_ATTACHMENT_BYTES,
    Attachment,
    AttachmentTooLarge,
    InvalidRecipient,
    MailError,
    RecentRecipients,
    send,
)


class Recorder:
    def __init__(self, reject=None):
        self.reject = reject
        self.envelopes = []

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if self.reject:
            return self.reject
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        self.envelopes.append(envelope)
        return "250 OK"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ignore_probe_resets(loop, context):
    # aiosmtpd's start() probe connection can be reset before the server reads it;
    # the unretrieved future is noise. Every other loop error still reports.
    if not isinstance(context.get("exception"), ConnectionResetError):
        loop.default_exception_handler(context)


@pytest.fixture(scope="session")
def tls_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    cert, key = d / "cert.pem", d / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "1",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(key), "-out", str(cert),
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


@pytest.fixture(scope="session")
def server_tls(tls_files):
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(*tls_files)
    return ctx


@pytest.fixture(scope="session")
def client_tls(tls_files):
    return ssl.create_default_context(cafile=str(tls_files[0]))


@pytest.fixture
def smtp_server():
    controllers = []

    def start(handler, **kwargs):
        port = free_port()
        controller = Controller(handler, hostname="127.0.0.1", port=port, **kwargs)
        controller.loop.set_exception_handler(ignore_probe_resets)
        controller.start()
        controllers.append(controller)
        return port

    yield start
    for controller in controllers:
        controller.stop()


def pdf(name="scan.pdf", data=b"%PDF-1.4 fake"):
    return Attachment(name, data, "application/pdf")


def test_send_two_recipients_pdf(cfg, smtp_server, server_tls, client_tls):
    rec = Recorder()
    port = smtp_server(rec, tls_context=server_tls)
    cfg = replace(cfg, smtp_port=port, smtp_user="")

    send(
        cfg,
        ["a@example.com", "b@example.com"],
        "Scan 2026-10-21 14-32",
        [pdf(data=b"%PDF-1.4 body")],
        ssl_context=client_tls,
    )

    (env,) = rec.envelopes
    assert env.rcpt_tos == ["a@example.com", "b@example.com"]
    msg = message_from_bytes(env.content, policy=policy.default)
    assert str(msg["Subject"]) == "Scan 2026-10-21 14-32"
    (part,) = msg.iter_attachments()
    assert part.get_filename() == "scan.pdf"
    assert part.get_payload(decode=True) == b"%PDF-1.4 body"


def test_unicode_filename(cfg, smtp_server, server_tls, client_tls):
    rec = Recorder()
    port = smtp_server(rec, tls_context=server_tls)
    cfg = replace(cfg, smtp_port=port, smtp_user="")

    send(cfg, ["a@example.com"], "s", [pdf("Relatório nº 3.pdf")], ssl_context=client_tls)

    msg = message_from_bytes(rec.envelopes[0].content, policy=policy.default)
    (part,) = msg.iter_attachments()
    assert part.get_filename() == "Relatório nº 3.pdf"


def test_too_large_fails_before_connecting(cfg):
    # Nothing listens on this port: a connection attempt would raise MailError instead.
    cfg = replace(cfg, smtp_port=free_port())
    big = pdf(data=b"\0" * (MAX_ATTACHMENT_BYTES + 1))

    with pytest.raises(AttachmentTooLarge):
        send(cfg, ["a@example.com"], "s", [big])


def test_server_error_verbatim(cfg, smtp_server, server_tls, client_tls):
    port = smtp_server(Recorder(reject="550 5.1.1 mailbox unavailable"), tls_context=server_tls)
    cfg = replace(cfg, smtp_port=port, smtp_user="")

    with pytest.raises(MailError, match=re.escape("550 5.1.1 mailbox unavailable")):
        send(cfg, ["a@example.com"], "s", [pdf()], ssl_context=client_tls)


def test_starttls_not_offered_raises_instead_of_sending_in_clear(cfg, smtp_server, client_tls):
    rec = Recorder()
    port = smtp_server(rec)  # no tls_context: STARTTLS is not advertised
    cfg = replace(cfg, smtp_port=port, smtp_user="")

    with pytest.raises(MailError, match="STARTTLS"):
        send(cfg, ["a@example.com"], "s", [pdf()], ssl_context=client_tls)
    assert rec.envelopes == []


def test_implicit_tls(cfg, smtp_server, server_tls, client_tls):
    rec = Recorder()
    port = smtp_server(rec, ssl_context=server_tls)
    cfg = replace(cfg, smtp_port=port, smtp_security="tls", smtp_user="")

    send(cfg, ["a@example.com"], "s", [pdf()], ssl_context=client_tls)

    assert rec.envelopes[0].rcpt_tos == ["a@example.com"]


@pytest.mark.parametrize("addr", ["bob", "bob@", "@example.com", "Ana <a@example.com>"])
def test_invalid_recipient(cfg, addr):
    cfg = replace(cfg, smtp_port=free_port())

    with pytest.raises(InvalidRecipient):
        send(cfg, ["a@example.com", addr], "s", [pdf()])


def test_recent_recipients_newest_first_case_insensitive(tmp_path):
    path = tmp_path / "recipients.json"
    rr = RecentRecipients(path)
    assert rr.list() == []

    rr.add(["a@x.com"])
    rr.add(["B@x.com", "c@x.com"])
    rr.add(["a@X.com", "c@x.com"])

    assert RecentRecipients(path).list() == ["a@X.com", "c@x.com", "B@x.com"]


def test_recent_recipients_capped_at_20(tmp_path):
    rr = RecentRecipients(tmp_path / "recipients.json")

    rr.add([f"u{i}@x.com" for i in range(25)])

    assert rr.list() == [f"u{i}@x.com" for i in range(20)]
