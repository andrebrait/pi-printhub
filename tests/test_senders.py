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
from printhub.notifications import KINDS, validate_channel
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
        """respond(path, headers, body) -> (status, body bytes, extra headers or None, delay seconds).

        The delay comes before the reply, unless extra sets Content-Length (a lie): then the reply goes out at
        once and the delay stalls the unfinished body."""
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                seen.append((self.path, self.headers, json.loads(body)))
                status, answer, extra, delay = respond(self.path, self.headers, body)
                lie = "Content-Length" in (extra or {})
                time.sleep(0 if lie else delay)
                try:
                    self.send_response(status)
                    for k, v in ({"Content-Length": str(len(answer))} | (extra or {})).items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(answer)
                    self.wfile.flush()
                    time.sleep(delay if lie else 0)
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


def bare_telegram(**kw):
    """Header and templates that add nothing, so a test sees the escaped values alone."""
    return telegram(header="", templates={k: "{title}\n{detail}\n{link}" for k in KINDS}, **kw)


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
        "text": "🖨\ufe0f Print Hub\n🧾 Job failed\nPaper jam\nhttps://print.example.com/print.html?doc=d1",
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
    assert body["text"] == "🖨\ufe0f Print Hub\n🧾 Job failed\nhttps://print.example.com/jobs.html"
    assert body["link_preview_options"] == {"is_disabled": True}


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("HTML", "a&lt;b &amp; *c_ [d]\nx &lt;y&gt;\nhttps://print.example.com/"),
        ("Markdown", "a<b & \\*c\\_ \\[d]\nx <y>\nhttps://print.example.com/"),
        ("MarkdownV2", "a<b & \\*c\\_ \\[d\\]\nx <y\\>\nhttps://print\\.example\\.com/"),
        (None, "a<b & *c_ [d]\nx <y>\nhttps://print.example.com/"),
    ],
)
def test_telegram_escapes_values_per_parse_mode(http_server, monkeypatch, cfg, mode, expected):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(bare_telegram(parse_mode=mode), event(kind="settings", title="a<b & *c_ [d]", detail="x <y>"), cfg)

    assert seen[0][2]["text"] == expected


@pytest.mark.parametrize("mode", ["MarkdownV2", "HTML", None])
def test_a_long_detail_is_cut_so_the_message_fits_telegrams_limit(http_server, monkeypatch, cfg, mode):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(telegram(parse_mode=mode), event(detail="a.b<" * 2500, doc_id="d1"), cfg)

    text = seen[0][2]["text"]
    link = "https://print.example.com/print.html?doc=d1" if mode != "MarkdownV2" else "https://print\\.example\\.com/print\\.html?doc\\=d1"
    lines = text.split("\n")
    assert len(text) <= 4096
    assert lines[0] == "🖨\ufe0f Print Hub" and lines[1] == "🧾 Job failed" and lines[-1] == link
    assert lines[2].endswith("…")


def test_a_title_too_long_even_without_detail_is_cut_too(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(telegram(), event(title="T" * 10000, detail="D" * 10000), cfg)

    text = seen[0][2]["text"]
    assert len(text) <= 4096 and text.startswith("🖨\ufe0f Print Hub\n🧾 T") and text.endswith("/jobs.html")
    assert "D" not in text


def test_telegram_markdown_escapes_underscores_in_the_link_too(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(telegram(parse_mode="Markdown", header="", templates={"job": "{link}"}), event(doc_id="a_b"), cfg)

    assert seen[0][2]["text"] == "https://print.example.com/print.html?doc=a\\_b"


@pytest.mark.parametrize("value, field", [("{detail}", "title"), ("{link}", "title"), ("{title}", "detail")])
def test_placeholder_values_stay_literal(http_server, monkeypatch, cfg, value, field):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)
    channel = telegram(parse_mode=None, header="", templates={"job": "{title}\n{detail}"})

    send(channel, event(**{field: value}), cfg)

    title, detail = (value, "Paper jam") if field == "title" else ("Job failed", value)
    assert seen[0][2]["text"] == f"{title}\n{detail}"


def test_telegram_markdown_v2_keeps_the_owners_markup_and_escapes_the_values(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)
    channel = telegram(parse_mode="MarkdownV2", header="*Printer*", templates={"job": "_{title}_\n{link}"})

    send(channel, event(title="a_b*c.pdf", doc_id="d1"), cfg)

    assert seen[0][2]["text"] == "*Printer*\n_a\\_b\\*c\\.pdf_\nhttps://print\\.example\\.com/print\\.html?doc\\=d1"


def test_telegram_html_template_markup_is_kept_and_values_escaped(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(telegram(parse_mode="HTML", header="", templates={"job": "<b>{title}</b>"}), event(title="<x> & y"), cfg)

    assert seen[0][2]["text"] == "<b>&lt;x&gt; &amp; y</b>"


def test_telegram_without_a_parse_mode_leaves_values_and_other_braces_as_typed(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(telegram(parse_mode=None, header="", templates={"job": "{other} {title} {{detail}}"}), event(title="<a_b>"), cfg)

    assert seen[0][2]["text"] == "{other} <a_b> {Paper jam}"


def test_empty_and_blank_lines_are_dropped_and_an_empty_header_has_no_line(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)
    template = "{title}\n{detail}\n  {detail}  \n   \n{link}"

    send(telegram(header="", templates={"job": template}), event(detail=""), cfg)
    send(telegram(header="Head", templates={"job": template}), event(detail=""), cfg)

    assert [s[2]["text"] for s in seen] == [
        "Job failed\nhttps://print.example.com/jobs.html",
        "Head\nJob failed\nhttps://print.example.com/jobs.html",
    ]


def test_each_kind_uses_its_own_template(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)
    channel = telegram(header="", templates={"job": "J {title}", "printer": "P {title}"})

    send(channel, event(kind="job"), cfg)
    send(channel, event(kind="printer"), cfg)

    assert [s[2]["text"] for s in seen] == ["J Job failed", "P Job failed"]


@pytest.fixture
def utc_plus_2(monkeypatch):
    monkeypatch.setenv("TZ", "XYZ-2")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_time_is_the_events_local_minute(http_server, monkeypatch, cfg, utc_plus_2):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(telegram(header="", templates={"job": "{time}"}), event(at="2026-10-30T10:00:42+00:00"), cfg)

    assert seen[0][2]["text"] == "2026-10-30 12:00"


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


@pytest.mark.parametrize("delay", [0, 1.5])
def test_error_with_a_short_or_stalled_body_is_still_a_send_error(http_server, monkeypatch, cfg, delay):
    url, _ = http_server(lambda *a: (500, b"part", {"Content-Length": "100"}, delay))
    monkeypatch.setattr(senders, "TIMEOUT", 0.5)

    with pytest.raises(SendError, match="HTTP 500"):
        send(webhook(url), event(), cfg)


@pytest.mark.parametrize("junk", ["\x01", "\x7f", " ", "\n"])
def test_control_characters_in_url_never_leak_the_secret(monkeypatch, cfg, junk):
    monkeypatch.setattr(senders, "TELEGRAM_API", "http://127.0.0.1:1")
    channel = telegram() | {"bot_token": f"12{junk}SECRETtoken"}

    with pytest.raises(SendError) as exc:
        send(channel, event(), cfg)
    assert "SECRET" not in str(exc.value)

    with pytest.raises(SendError) as exc:
        send(webhook("http://127.0.0.1:1/") | {"url": f"http://127.0.0.1:1/SECRETpath{junk}x"}, event(), cfg)
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("token", ["12\x01abc", "12\x7fabc", "12 abc", "12\nabc", "12é", "12/abc", "12.abc", "12?x=1", ""])
def test_telegram_token_must_be_letters_digits_colon_underscore_hyphen(token):
    with pytest.raises(ValueError, match="bot_token"):
        telegram(bot_token=token)


def test_telegram_markdown_v2_escapes_every_special_character(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)
    specials = "_*[]()~`>#+-=|{}.!\\"

    send(bare_telegram(parse_mode="MarkdownV2"), event(title=specials, detail=specials), cfg)

    escaped = "".join("\\" + c for c in specials)
    assert seen[0][2]["text"].split("\n")[:2] == [escaped, escaped]


def test_telegram_markdown_escapes_only_underscore_star_bracket_backtick_backslash(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(bare_telegram(parse_mode="Markdown"), event(title="_*[`]\\.-", detail=""), cfg)

    assert seen[0][2]["text"].split("\n")[0] == "\\_\\*\\[\\`]\\\\.-"


def test_telegram_html_escapes_only_what_the_markup_needs(http_server, monkeypatch, cfg):
    url, seen = http_server(ok)
    monkeypatch.setattr(senders, "TELEGRAM_API", url)

    send(bare_telegram(parse_mode="HTML"), event(title="say \"hi\" it's <b>", detail=""), cfg)

    assert seen[0][2]["text"].split("\n")[0] == "say \"hi\" it's &lt;b&gt;"


@pytest.mark.parametrize("status", [200, 500])
def test_a_reply_is_read_only_up_to_64_kb(http_server, status):
    url, _ = http_server(lambda *a: (status, b"x" * 200_000, None, 0))

    got, body = senders._post(url, {}, {})

    assert (got, len(body)) == (status, 64 * 1024)


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

    send(email(use_scan_server=True, format="text"), event(title="Job\nfailed", doc_id="d1"), replace(cfg, smtp_port=port, smtp_user=""))

    (env,) = rec.envelopes
    assert env.rcpt_tos == ["a@example.com"]
    msg = message_from_bytes(env.content, policy=policy.default)
    assert str(msg["Subject"]) == "[Print Hub] Job failed"
    assert msg.get_content().replace("\r\n", "\n").strip() == (
        "🧾 Job\nfailed\nPaper jam\nhttps://print.example.com/print.html?doc=d1"
    )


def sent_message(tls_mail, cfg, channel, ev=None):
    rec, port = tls_mail
    send(channel, ev or event(), replace(cfg, smtp_port=port, smtp_user=""))
    return message_from_bytes(rec.envelopes[-1].content, policy=policy.default)


@pytest.mark.parametrize("prefix, subject", [("[Ops]", "[Ops] Job failed"), ("  [Ops]  ", "[Ops] Job failed"), ("", "Job failed"), ("  ", "Job failed")])
def test_email_subject_prefix_is_stripped_and_optional(tls_mail, cfg, prefix, subject):
    msg = sent_message(tls_mail, cfg, email(subject_prefix=prefix))

    assert str(msg["Subject"]) == subject


def test_email_body_comes_from_the_kinds_template_unescaped(tls_mail, cfg):
    channel = email(format="text", templates={"printer": "{title} <{detail}> at {time}\n{link}"})

    msg = sent_message(tls_mail, cfg, channel, event(kind="printer", title="a&b", detail="c<d"))

    body = msg.get_content().replace("\r\n", "\n").strip()
    assert body.startswith("a&b <c<d> at ") and body.endswith("\nhttps://print.example.com/")


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


def test_html_email_is_multipart_with_escaped_values_and_line_breaks(tls_mail, cfg):
    channel = email(templates={"job": "<b>{title}</b>\n{detail}"})

    msg = sent_message(tls_mail, cfg, channel, event(title="a <b> c", detail="Paper jam"))

    assert msg.get_content_type() == "multipart/alternative"
    text, html_part = msg.get_body(("plain",)), msg.get_body(("html",))
    assert "<b>a &lt;b&gt; c</b><br>\nPaper jam" in html_part.get_content().replace("\r\n", "\n")
    assert html_part.get_content().lstrip().lower().startswith("<!doctype html>")
    assert text.get_content().replace("\r\n", "\n").strip() == "a <b> c\nPaper jam"


def test_text_email_stays_a_single_plain_part(tls_mail, cfg):
    msg = sent_message(tls_mail, cfg, email(format="text", templates={"job": "<b>{title}</b>"}))

    assert msg.get_content_type() == "text/plain"
    assert msg.get_content().strip() == "<b>Job failed</b>"
