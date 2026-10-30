# SPDX-License-Identifier: AGPL-3.0-only
"""Delivers one event to one notification channel. SendError messages never contain secrets."""
import html
import http.client
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from urllib.parse import quote

from printhub import mail
from printhub.config import Config
from printhub.events import Event
from printhub.notifications import PLACEHOLDERS

TELEGRAM_API = "https://api.telegram.org"
TIMEOUT = 10
MAX_REPLY = 64 * 1024  # Telegram's JSON answer is tiny; the rest of any reply is ignored.
_ESCAPE = {"Markdown": r"_*\[`\\", "MarkdownV2": r"_*\[\]()~`>#+\-=|{}.!\\"}


class SendError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Returning None makes urllib raise the 3xx as an HTTPError instead of following it.
    def redirect_request(self, *args, **kwargs):
        return None


# An empty ProxyHandler keeps proxy environment variables from rerouting LAN webhooks.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def test_event(channel_name: str) -> Event:
    return Event(
        id=0,
        at=datetime.now(UTC).isoformat(timespec="seconds"),
        level="info",
        kind="settings",
        title="Test notification",
        detail=f"Sent from Print Hub to {channel_name}.",
    )


def send(channel: dict, event: Event, cfg: Config) -> None:
    {"email": _email, "telegram": _telegram, "webhook": _webhook}[channel["type"]](channel, event, cfg)


def _link(event: Event, cfg: Config) -> str:
    origin = cfg.origin.rstrip("/")
    if event.doc_id:
        page = "scan" if event.kind == "scan" else "print"
        return f"{origin}/{page}.html?doc={quote(event.doc_id, safe='')}"
    return origin + ("/jobs.html" if event.kind == "job" else "/")


def _email(channel: dict, event: Event, cfg: Config) -> None:
    if not channel["use_scan_server"]:
        cfg = replace(
            cfg,
            **{k: channel[k] for k in ("smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password", "mail_from")},
        )
    subject = " ".join(p for p in (channel["subject_prefix"].strip(), event.title) if p)
    link = _link(event, cfg)
    template = channel["templates"][event.kind]
    body, html_body = render(template, event, link, str), None
    if channel["format"] == "html":
        # The tags are the owner's; only the placeholder values need escaping.
        rendered = render(template, event, link, lambda s: html.escape(s, quote=False))
        html_body = f"<!doctype html><html><body>{rendered.replace(chr(10), '<br>' + chr(10))}</body></html>"
        # Strip tags before unescaping, or a value like "<b>" would be taken for a tag.
        body = html.unescape(re.sub(r"<[^>]+>", "", rendered))
    try:
        mail.send(cfg, channel["to"], subject, [], body=body, html=html_body)
    except (mail.MailError, mail.InvalidRecipient) as e:
        raise SendError(str(e)) from e


def render(template: str, event: Event, link: str, escape: Callable[[str], str]) -> str:
    """Fills the placeholders (nothing else in the template is touched) and drops the lines left empty."""
    values = {
        "title": escape(event.title),
        "detail": escape(event.detail),
        "link": escape(link),
        "time": escape(datetime.fromisoformat(event.at).astimezone().strftime("%Y-%m-%d %H:%M")),
    }
    text = re.sub(r"\{(%s)\}" % "|".join(PLACEHOLDERS), lambda m: values[m[1]], template)
    return "\n".join(line for line in text.split("\n") if line.strip())


def _telegram_text(channel: dict, event: Event, link: str) -> str:
    mode = channel["parse_mode"]
    if mode == "HTML":
        escape = lambda s: html.escape(s, quote=False)
    elif mode in _ESCAPE:
        escape = lambda s: re.sub(f"([{_ESCAPE[mode]}])", r"\\\1", s)
    else:
        escape = str
    body = render(channel["templates"][event.kind], event, link, escape)
    return f"{channel['header']}\n{body}" if channel["header"] else body


def _telegram(channel: dict, event: Event, cfg: Config) -> None:
    mode = channel["parse_mode"]
    payload = {
        "chat_id": channel["chat_id"],
        "text": _telegram_text(channel, event, _link(event, cfg)),
        "disable_notification": channel["disable_notification"],
        "protect_content": channel["protect_content"],
        "link_preview_options": {"is_disabled": channel["disable_web_page_preview"]},
    }
    if channel["message_thread_id"] is not None:
        payload["message_thread_id"] = channel["message_thread_id"]
    if mode:
        payload["parse_mode"] = mode
    # The URL holds the token: it must never reach an error message.
    status, body = _post(f"{TELEGRAM_API}/bot{channel['bot_token']}/sendMessage", payload, {})
    try:
        answer = json.loads(body)
    except ValueError:
        answer = None
    answer = answer if isinstance(answer, dict) else {}
    if not 200 <= status < 300 or answer.get("ok") is not True:
        description = answer.get("description")
        raise SendError(description if isinstance(description, str) and description else f"HTTP {status}")


def _webhook(channel: dict, event: Event, cfg: Config) -> None:
    payload = asdict(event) | {"url": _link(event, cfg), "source": "printhub"}
    headers = {channel["header_name"]: channel["header_value"]} if channel["header_name"] else {}
    status, _ = _post(channel["url"], payload, headers)
    if not 200 <= status < 300:
        raise SendError(f"HTTP {status}")


def _post(url: str, payload: dict, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, json.dumps(payload).encode(), {"Content-Type": "application/json", **headers}, method="POST"
    )
    try:
        with _opener.open(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_REPLY)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(MAX_REPLY)
        except (OSError, http.client.HTTPException):
            return e.code, b""
    except (ValueError, http.client.InvalidURL):
        # Their messages quote the header value or the URL path, which hold secrets.
        raise SendError("the request could not be built") from None
    except (OSError, http.client.HTTPException) as e:
        reason = e.reason if isinstance(e, urllib.error.URLError) else e
        raise SendError(str(reason) or type(reason).__name__) from None
