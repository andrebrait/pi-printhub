# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
import stat
from datetime import UTC, datetime, timedelta

import pytest

from printhub.events import Event
from printhub.notifications import (
    DEFAULT_SUBJECT_PREFIX,
    KINDS,
    StoreError,
    active_silences,
    default_email_templates,
    default_header,
    default_templates,
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
        ("url", webhook(url="https://user:pw@ntfy.example.com/hub")),
        ("url", webhook(url="https://token@ntfy.example.com/hub")),
        ("header_name", webhook(header_name="a b")),
        ("header_value", webhook(header_value="tok\r\nX-Evil: 1")),
        ("header_value", webhook(header_value="tok\n")),
        ("header_value", webhook(header_value="tok\x00")),
        ("header_value", webhook(header_value="tok\x7f")),
        ("bot_token", telegram(bot_token="123:abc def")),
        ("bot_token", telegram(bot_token="123:abc/../x")),
        ("bot_token", telegram(bot_token="123:abc.def")),
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


def test_a_url_with_credentials_says_where_they_belong():
    with pytest.raises(ValueError, match="put credentials in the header, not the URL"):
        validate_channel(webhook(url="https://user:pw@ntfy.example.com/hub"))


def test_telegram_token_charset_and_header_values_with_spaces_are_accepted():
    assert validate_channel(telegram(bot_token="123456:AA-bb_CC9"))["bot_token"] == "123456:AA-bb_CC9"
    assert validate_channel(webhook(header_value="Bearer a b c"))["header_value"] == "Bearer a b c"


@pytest.mark.parametrize("password", [{}, {"smtp_password": ""}, {"smtp_password": None}])
def test_an_email_channel_can_move_to_its_own_server_without_a_login(password):
    stored = validate_channel(email())
    edit = {k: v for k, v in own_email().items() if k != "smtp_password"} | password

    merged = merge_edit(stored, edit)

    assert merged["smtp_password"] == "" and merged["smtp_host"] == "mail.example.com"


def test_changing_the_type_leaves_nothing_of_the_old_secret():
    merged = merge_edit(validate_channel(telegram()), webhook())

    assert merged["type"] == "webhook" and "bot_token" not in merged


def test_a_new_telegram_channel_without_a_parse_mode_is_markdown_v2_with_its_defaults():
    without = {k: v for k, v in telegram().items() if k != "parse_mode"}
    channel = validate_channel(without)
    assert channel["parse_mode"] == "MarkdownV2"
    assert (channel["header"], channel["templates"]) == (default_header("MarkdownV2"), default_templates("MarkdownV2"))
    assert validate_channel(telegram(parse_mode=None))["parse_mode"] is None


EMOJI = {"job": "🧾", "scan": "📄", "printer": "🔔", "conversion": "🔄", "settings": "⚙\ufe0f"}
WRAP = {"MarkdownV2": ("*{}*", "🖨\ufe0f _*Print Hub*_"), "HTML": ("<b>{}</b>", "🖨\ufe0f <i><b>Print Hub</b></i>"),
        "Markdown": ("{}", "🖨\ufe0f *Print Hub*"), None: ("{}", "🖨\ufe0f Print Hub")}


@pytest.mark.parametrize("mode", WRAP)
def test_each_parse_mode_has_its_formatted_header_and_templates(mode):
    bold, header = WRAP[mode]
    assert default_header(mode) == header
    assert default_templates(mode) == {k: f"{EMOJI[k]} {bold.format('{title}')}\n{{detail}}\n{{link}}" for k in KINDS}


def test_email_defaults_follow_the_format():
    assert default_email_templates("html") == default_templates("HTML")
    assert default_email_templates("text") == default_templates(None)
    assert validate_channel(email(format="text"))["templates"] == default_templates(None)
    assert validate_channel(email())["templates"] == default_templates("HTML")


def test_the_markdown_v2_defaults_render_to_text_telegram_accepts():
    from printhub.senders import _telegram_text

    channel = validate_channel(telegram(parse_mode="MarkdownV2", header=default_header("MarkdownV2")) | {"templates": {}})
    event = Event(id=1, at="2026-10-30T10:00:00+00:00", level="info", kind="job", title="a_b.pdf (2)", detail="ok", doc_id=None)
    assert _telegram_text(channel, event, "https://x.example/a_b").split("\n") == [
        "🖨\ufe0f _*Print Hub*_", "🧾 *a\\_b\\.pdf \\(2\\)*", "ok", "https://x\\.example/a\\_b"]


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


MESSAGE_FIELDS = [(telegram, "header", default_header("HTML")), (email, "subject_prefix", DEFAULT_SUBJECT_PREFIX)]


@pytest.mark.parametrize("make, field, default", MESSAGE_FIELDS)
def test_a_stored_channel_without_message_fields_loads_with_the_defaults(tmp_path, make, field, default):
    (tmp_path / "notifications.json").write_text(json.dumps({"channels": [make()], "silences": []}))

    (channel,) = load(tmp_path)["channels"]

    assert (channel[field], channel["templates"]) == (default, default_templates("HTML"))
    assert public(channel)["templates"] == default_templates("HTML") and public(channel)[field] == default


def test_an_email_channel_without_a_format_is_html_and_a_bad_one_is_refused(tmp_path):
    (tmp_path / "notifications.json").write_text(json.dumps({"channels": [email()], "silences": []}))

    (channel,) = load(tmp_path)["channels"]

    assert channel["format"] == "html" and validate_channel(email(format="text"))["format"] == "text"
    with pytest.raises(ValueError, match="^format"):
        validate_channel(email(format="rtf"))


@pytest.mark.parametrize("make, field, default", MESSAGE_FIELDS)
def test_missing_kinds_are_filled_from_the_defaults_and_the_rest_kept(make, field, default):
    channel = validate_channel(make(templates={"job": "{title}"}, **{field: ""}))

    assert channel[field] == ""
    assert channel["templates"] == default_templates("HTML") | {"job": "{title}"}


@pytest.mark.parametrize(
    "field, data",
    [
        ("templates", telegram(templates={"bogus": "x"})),
        ("templates", telegram(templates={"job": "x" * 1001})),
        ("templates", telegram(templates={"job": ""})),
        ("templates", telegram(templates={"job": 5})),
        ("templates", email(templates=["x"])),
        ("templates", email(templates={"scan": "x" * 1001})),
        ("header", telegram(header="x" * 201)),
        ("header", telegram(header=None)),
        ("subject_prefix", email(subject_prefix="x" * 101)),
        ("subject_prefix", email(subject_prefix="a\nBcc: x@example.com")),
        ("subject_prefix", email(subject_prefix="a\rb")),
    ],
)
def test_bad_message_fields_name_the_field(field, data):
    with pytest.raises(ValueError, match=f"^{field}"):
        validate_channel(data)


def test_webhook_has_no_message_fields():
    channel = validate_channel(webhook(header="x", templates={"job": "x"}))

    assert "header" not in channel and "templates" not in channel


def test_edit_may_leave_out_the_message_fields():
    stored = validate_channel(telegram(header="Mine", templates={"job": "{title}"}))

    merged = merge_edit(stored, {"name": "Renamed"})

    assert (merged["header"], merged["templates"]["job"]) == ("Mine", "{title}")
