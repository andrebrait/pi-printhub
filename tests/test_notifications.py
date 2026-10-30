# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
import stat
from datetime import UTC, datetime, timedelta

import pytest

from printhub.events import Event
from printhub.notifications import (
    KINDS,
    active_silences,
    load,
    merge_edit,
    new_silence,
    public,
    save,
    silenced,
    validate_channel,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def email(**over):
    return {"id": "c1", "name": "Owner", "type": "email", "enabled": True, "kinds": list(KINDS), "to": ["owner@example.com"], "use_scan_server": True, **over}


OWN_SMTP = {
    "smtp_host": "mail.example.com",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "smtp_user": "printhub",
    "smtp_password": "mail-secret",
    "mail_from": "Print Hub <printhub@example.com>",
}


def own_email(**over):
    return {**email(use_scan_server=False), **OWN_SMTP, **over}


def telegram(**over):
    return {
        "id": "c1",
        "name": "Owner",
        "type": "telegram",
        "enabled": True,
        "kinds": list(KINDS),
        "bot_token": "123:abc",
        "chat_id": "-100123456",
        "message_thread_id": None,
        "parse_mode": "HTML",
        "disable_notification": False,
        "protect_content": False,
        "disable_web_page_preview": False,
        **over,
    }


def webhook(**over):
    return {
        "id": "c1",
        "name": "Owner",
        "type": "webhook",
        "enabled": True,
        "kinds": list(KINDS),
        "url": "https://ntfy.example.com/printhub",
        "header_name": "Authorization",
        "header_value": "Bearer tok",
        **over,
    }


SECRET_CASES = [(own_email, "smtp_password"), (telegram, "bot_token"), (webhook, "header_value")]


def event(title, kind):
    return Event(id=1, at=NOW.isoformat(), level="info", kind=kind, title=title)


def test_valid_channels_of_each_type_round_trip(tmp_path):
    state = {
        "channels": [validate_channel(c) for c in (email(), own_email(), telegram(), webhook(header_name="", header_value=""))],
        "silences": [],
    }
    save(tmp_path, state)
    assert load(tmp_path) == state
    assert stat.S_IMODE((tmp_path / "notifications.json").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "field, data",
    [
        ("to", email(to=["not an address"])),
        ("smtp_port", own_email(smtp_port=0)),
        ("chat_id", telegram(chat_id="abc")),
        ("url", webhook(url="ftp://x")),
        ("header_name", webhook(header_name="a b")),
        ("kinds", email(kinds=["bogus"])),
        ("name", email(name="x" * 61)),
    ],
)
def test_invalid_fields_name_the_field(field, data):
    with pytest.raises(ValueError, match=f"^{field}"):
        validate_channel(data)


@pytest.mark.parametrize("make, secret", SECRET_CASES)
def test_public_never_contains_secrets(make, secret):
    channel = validate_channel(make())
    shown = public(channel)
    assert secret not in shown
    assert shown[f"{secret}_set"] is True
    assert channel[secret] not in json.dumps(shown)


@pytest.mark.parametrize("make, secret", SECRET_CASES)
def test_blank_secret_keeps_stored_one(make, secret):
    stored = validate_channel(make())
    merged = merge_edit(stored, make(name="Renamed", **{secret: ""}))
    assert merged["name"] == "Renamed"
    assert merged[secret] == stored[secret]


@pytest.mark.parametrize(
    "make, secret, change",
    [
        (own_email, "smtp_password", {"smtp_host": "other.example.com"}),
        (telegram, "bot_token", {"chat_id": "-100999"}),
        (webhook, "header_value", {"url": "https://elsewhere.example.com/hook"}),
    ],
)
def test_changing_guarded_field_without_secret_is_refused(make, secret, change):
    stored = validate_channel(make())
    field = next(iter(change))
    with pytest.raises(ValueError, match=f"again to change {field}"):
        merge_edit(stored, make(**change, **{secret: ""}))
    merged = merge_edit(stored, make(**change, **{secret: "new-secret"}))
    assert merged[field] == change[field]
    assert merged[secret] == "new-secret"


def test_silence_by_title_and_kind_and_expiry():
    silences = [new_silence("title", "Paper jam", 1, NOW), new_silence("kind", "scan", 8, NOW)]

    assert silenced(silences, event("Paper jam", "printer"), NOW)
    assert silenced(silences, event("Scan failed", "scan"), NOW)
    assert not silenced(silences, event("Paper jam again", "printer"), NOW)
    assert not silenced(silences, event("Print done", "job"), NOW)

    after_title = NOW + timedelta(hours=1)
    assert active_silences(silences, after_title) == [silences[1]]
    assert not silenced(silences, event("Paper jam", "printer"), after_title)
    assert silenced(silences, event("Scan failed", "scan"), after_title)

    assert active_silences(silences, NOW + timedelta(hours=9)) == []

    with pytest.raises(ValueError):
        new_silence("title", "Paper jam", 2, NOW)
    with pytest.raises(ValueError):
        new_silence("kind", "bogus", 1, NOW)


def test_unparsable_file_and_bad_channel_are_skipped_with_errors(tmp_path, caplog):
    caplog.set_level(logging.ERROR, logger="printhub.notifications")
    empty = {"channels": [], "silences": []}

    assert load(tmp_path) == empty
    assert caplog.text == ""

    (tmp_path / "notifications.json").write_text("{not json")
    assert load(tmp_path) == empty
    assert "notifications.json" in caplog.text

    save(tmp_path, {"channels": [telegram(), telegram(id="c2", chat_id="abc")], "silences": []})
    loaded = load(tmp_path)
    assert [c["id"] for c in loaded["channels"]] == ["c1"]
    assert "c2" in caplog.text
    assert "123:abc" not in caplog.text
