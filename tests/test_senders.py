# SPDX-License-Identifier: AGPL-3.0-only
import json
import threading
import time
from dataclasses import replace
from email import message_from_bytes, policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_mail import Recorder, client_tls, server_tls, smtp_server, tls_files  # noqa: F401

from printhub import senders
from printhub.events import Event
from printhub.notifications import validate_channel
from printhub.senders import SendError, send
from printhub.senders import test_event as make_test_event

TOKEN = "123456:SECRET-token"


def event(**kw):
    base = dict(id=7, at="2026-10-30T10:00:00+00:00", level="error", kind="job", title="Job failed", detail="Paper jam")
    return Event(**(base | kw))


@pytest.fixture
def http_server():
    servers = []

    def start(respond):
        """respond(path, headers, body) -> (status, body bytes, extra headers or None, delay seconds)."""
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                seen.append((self.path, self.headers, json.loads(body)))
                status, answer, extra, delay = respond(self.path, self.headers, body)
                time.sleep(delay)
                try:
                    self.send_response(status)
                    for k, v in (extra or {}).items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(answer)))
                    self.end_headers()
                    self.wfile.write(answer)
                except OSError:
                    pass  # the client gave up (timeout test)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", seen

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def ok(path, headers, body):
    return 200, b'{"ok": true}', None, 0


def channel(type_, **fields):
    return validate_channel({"id": "c1", "name": "N", "type": type_, "enabled": True, "kinds": ["job"]} | fields)


def telegram(**kw):
    return channel(
        "telegram",
        **dict(
            bot_token=TOKEN, chat_id="-100123", message_thread_id=None, parse_mode=None, disable_notification=False,
            protect_content=False, disable_web_page_preview=False,
        ) | kw,
    )


def webhook(url, **kw):
    return channel("webhook", **dict(url=url, header_name="", header_value="") | kw)


def email(**kw):
    return channel("email", **dict(to=["a@example.com"], use_scan_server=True) | kw)


def test_test_event_is_a_settings_info_event():
    e = make_test_event("Ops")
    assert (e.level, e.kind, e.title) == ("info", "settings", "Test notification")


def test_telegram_request_shape(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)
    channel = telegram(message_thread_id=42, parse_mode="HTML", disable_notification=True, protect_content=True)

    send(channel, event(doc_id="d1"), cfg)

    (path, headers, body) = seen[0]
    assert path == f"/bot{TOKEN}/sendMessage"
    assert headers["Content-Type"] == "application/json"
    assert body == {
        "chat_id": "-100123",
        "text": "<b>Job failed</b>\nPaper jam\nhttps://print.example.com/print.html?doc=d1",
        "message_thread_id": 42,
        "parse_mode": "HTML",
        "disable_notification": True,
        "protect_content": True,
        "link_preview_options": {"is_disabled": False},
    }


def test_telegram_plain_omits_thread_and_parse_mode(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(telegram(disable_web_page_preview=True), event(detail=""), cfg)

    body = seen[0][2]
    assert "parse_mode" not in body and "message_thread_id" not in body
    assert body["text"] == "Job failed\nhttps://print.example.com/jobs.html"
    assert body["link_preview_options"] == {"is_disabled": True}


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("HTML", "<b>a&lt;b &amp; *c_ [d]</b>\nx &lt;y&gt;\nhttps://print.example.com/"),
        ("Markdown", "*a<b & \\*c\\_ \\[d]*\nx <y>\nhttps://print.example.com/"),
        ("MarkdownV2", "*a<b & \\*c\\_ \\[d\\]*\nx <y\\>\nhttps://print\\.example\\.com/"),
        (None, "a<b & *c_ [d]\nx <y>\nhttps://print.example.com/"),
    ],
)
def test_telegram_escapes_per_parse_mode(http_server, monkeypatch, cfg, mode, expected):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(telegram(parse_mode=mode), event(kind="settings", title="a<b & *c_ [d]", detail="x <y>"), cfg)

    assert seen[0][2]["text"] == expected


def test_telegram_error_reports_description_without_token(http_server, monkeypatch, cfg):
    url, _ = http_server(lambda *a: (400, b'{"ok": false, "description": "Bad Request: chat not found"}', None, 0))
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    with pytest.raises(SendError) as exc:
        send(telegram(), event(), cfg)

    assert str(exc.value) == "Bad Request: chat not found"


def test_telegram_unreachable_error_has_no_token(monkeypatch, cfg):
    monkeypatch.setattr(senders, "TELEGRAM_API", "http://127.0.0.1:1")

    with pytest.raises(SendError) as exc:
        send(telegram(), event(), cfg)

    assert TOKEN not in str(exc.value) and "127.0.0.1" not in str(exc.value)


def test_webhook_payload_header_and_no_redirects(http_server, cfg):
    url, seen = http_server(ok)
    channel = webhook(url + "/hook", header_name="Authorization", header_value="Bearer s3cret")

    send(channel, event(doc_id="d1", job_id=3), cfg)

    (path, headers, body) = seen[0]
    assert path == "/hook"
    assert headers["Authorization"] == "Bearer s3cret"
    assert body == {
        "id": 7, "at": "2026-10-30T10:00:00+00:00", "level": "error", "kind": "job", "title": "Job failed",
        "detail": "Paper jam", "job_id": 3, "doc_id": "d1",
        "url": "https://print.example.com/print.html?doc=d1", "source": "printhub",
    }

    target, hits = http_server(ok)
    redirect, _ = http_server(lambda *a: (302, b"", {"Location": target + "/elsewhere"}, 0))
    with pytest.raises(SendError, match="HTTP 302"):
        send(webhook(redirect), event(), cfg)
    assert hits == []


def test_webhook_non_2xx_is_a_send_error_without_header_value(http_server, cfg):
    url, _ = http_server(lambda *a: (500, b"boom", None, 0))

    with pytest.raises(SendError) as exc:
        send(webhook(url, header_name="X-Key", header_value="s3cret"), event(), cfg)

    assert str(exc.value) == "HTTP 500"


def test_webhook_timeout_is_a_send_error(http_server, monkeypatch, cfg):
    url, _ = http_server(lambda *a: (200, b"", None, 1.5))
    monkeypatch.setattr(senders, "TIMEOUT", 0.5)

    with pytest.raises(SendError, match="timed out"):
        send(webhook(url), event(), cfg)


def test_webhook_bad_header_value_does_not_leak(cfg):
    channel = webhook("http://127.0.0.1:1", header_name="X-Key") | {"header_value": "s3cret\r\nInjected: 1"}

    with pytest.raises(SendError) as exc:
        send(channel, event(), cfg)

    assert "s3cret" not in str(exc.value)


@pytest.fixture
def tls_mail(monkeypatch, smtp_server, server_tls, client_tls):
    monkeypatch.setattr("printhub.mail.ssl.create_default_context", lambda: client_tls)
    rec = Recorder()
    return rec, smtp_server(rec, tls_context=server_tls)


def test_email_body_and_subject(tls_mail, cfg):
    rec, port = tls_mail

    send(email(use_scan_server=True), event(title="Job\nfailed", doc_id="d1"), replace(cfg, smtp_port=port, smtp_user=""))

    (env,) = rec.envelopes
    assert env.rcpt_tos == ["a@example.com"]
    msg = message_from_bytes(env.content, policy=policy.default)
    assert str(msg["Subject"]) == "[Print Hub] Job failed"
    assert msg.get_content().replace("\r\n", "\n").strip() == (
        "Paper jam\n\n2026-10-30T10:00:00+00:00\nhttps://print.example.com/print.html?doc=d1"
    )


def test_email_uses_scan_server_or_its_own(tls_mail, cfg):
    rec, port = tls_mail
    scan_cfg = replace(cfg, smtp_port=port, smtp_user="", mail_from="scan@example.com")

    send(email(use_scan_server=True), event(), scan_cfg)
    own = email(
        use_scan_server=False, smtp_host="127.0.0.1", smtp_port=port, smtp_security="starttls",
        smtp_user="", smtp_password="", mail_from="own@example.com",
    )
    # cfg points at a dead port: only the channel's own server can deliver.
    send(own, event(), replace(cfg, smtp_port=1))

    senders_ = [message_from_bytes(e.content, policy=policy.default)["From"] for e in rec.envelopes]
    assert senders_ == ["scan@example.com", "own@example.com"]


def test_email_failure_is_a_send_error(cfg):
    with pytest.raises(SendError):
        send(email(use_scan_server=True), event(), replace(cfg, smtp_port=1))
