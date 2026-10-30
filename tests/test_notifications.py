# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
import stat
from datetime import UTC, datetime, timedelta

import pytest

from printhub.events import Event
from printhub.notifications import (
    KINDS,
    StoreError,
    active_silences,
    load,
    load_strict,
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


@pytest.mark.parametrize("make, secret", SECRET_CASES)
def test_edit_may_send_only_the_changed_fields(make, secret):
    stored = validate_channel(make())
    merged = merge_edit(stored, {"name": "Renamed"})
    assert merged == {**stored, "name": "Renamed"}


@pytest.mark.parametrize(
    "make, change",
    [(own_email, {"smtp_host": "other.example.com"}), (telegram, {"chat_id": "-100999"}), (webhook, {"url": "https://elsewhere.example.com/hook"})],
)
def test_partial_edit_cannot_repoint_a_stored_secret(make, change):
    with pytest.raises(ValueError, match="again to change"):
        merge_edit(validate_channel(make()), change)


def test_telegram_parse_mode_defaults_to_html_but_may_be_plain():
    without = {k: v for k, v in telegram().items() if k != "parse_mode"}
    assert validate_channel(without)["parse_mode"] == "HTML"
    assert validate_channel(telegram(parse_mode=None))["parse_mode"] is None


def test_names_are_stripped_and_blank_ones_refused():
    assert validate_channel(email(name="  Owner  "))["name"] == "Owner"
    with pytest.raises(ValueError, match="^name"):
        validate_channel(email(name="   "))


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


def test_strict_load_returns_a_clean_or_missing_file(tmp_path):
    assert load_strict(tmp_path) == {"channels": [], "silences": []}
    state = {"channels": [validate_channel(telegram())], "silences": [new_silence("kind", "job", 1, datetime.now(UTC))]}
    save(tmp_path, state)
    assert load_strict(tmp_path) == state


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        json.dumps({"channels": [telegram(), telegram(id="c2", chat_id="abc")], "silences": []}),
        json.dumps({"channels": [], "silences": ["junk"]}),
        json.dumps({"channels": [], "silences": [{"id": "s", "match": "kind", "value": "job"}]}),
    ],
)
def test_strict_load_refuses_a_file_that_did_not_load_cleanly(tmp_path, content):
    path = tmp_path / "notifications.json"
    path.write_text(content)
    with pytest.raises(StoreError, match=r"^notifications\.json has a problem \(.+\); fix or remove it before saving: ") as e:
        load_strict(tmp_path)
    assert str(e.value).endswith(str(path))
    assert "123:abc" not in str(e.value)
