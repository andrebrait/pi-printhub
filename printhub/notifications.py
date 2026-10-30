# SPDX-License-Identifier: AGPL-3.0-only
"""Notification channels (where events go) and silences (which alerts are held back): storage, validation and matching."""
import json
import logging
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.errors import MessageError
from email.headerregistry import Address
from pathlib import Path
from urllib.parse import urlsplit

from printhub.config import ConfigError
from printhub.settings import sender, write_json

log = logging.getLogger(__name__)

FILE = "notifications.json"
KINDS: tuple[str, ...] = ("job", "scan", "printer", "conversion", "settings")
SILENCE_HOURS = (1, 8, 24, 168)
SECRETS = {"email": "smtp_password", "telegram": "bot_token", "webhook": "header_value"}
TYPES = tuple(SECRETS)
SECRET_LABELS = {"email": "SMTP password", "telegram": "bot token", "webhook": "header value"}
# Re-pointing one of these at another server would send the stored secret there.
GUARDED = {
    "email": ("smtp_host", "smtp_port", "smtp_user", "smtp_security"),
    "telegram": ("chat_id",),
    "webhook": ("url",),
}
PARSE_MODES = (None, "HTML", "Markdown", "MarkdownV2")
BAD_TO = "to must be a list of 1 to 10 single email addresses"


class StoreError(Exception):
    pass


def _read(path: Path) -> tuple[dict, list[str]]:
    empty = {"channels": [], "silences": []}
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return empty, []
    except (OSError, ValueError) as e:
        return empty, [f"cannot be read: {e}"]
    if not isinstance(raw, dict) or not isinstance(raw.get("channels", []), list) or not isinstance(raw.get("silences", []), list):
        return empty, ["has the wrong shape"]
    problems, channels, silences = [], [], []
    for item in raw.get("channels", []):
        try:
            channels.append(validate_channel(item))
        except ValueError as e:
            problems.append(f"channel {item.get('id') if isinstance(item, dict) else None!r} skipped: {e}")
    for item in raw.get("silences", []):
        if isinstance(item, dict) and _until(item) is not None:
            silences.append(item)
        else:
            problems.append(f"silence {item.get('id') if isinstance(item, dict) else None!r} skipped: not a silence with an end time")
    return {"channels": channels, "silences": silences}, problems


def load(data_dir: Path, report: Callable[[str], None] = log.error) -> dict:
    """What the notifier uses: whatever is valid, with each problem reported."""
    path = Path(data_dir) / FILE
    state, problems = _read(path)
    for problem in problems:
        report(f"Notifications file {path}: {problem}")
    return state


def load_strict(data_dir: Path) -> dict:
    """What every write starts from: saving a state that dropped something would destroy it."""
    path = Path(data_dir) / FILE
    state, problems = _read(path)
    if problems:
        raise StoreError(f"{FILE} has a problem ({'; '.join(problems)}); fix or remove it before saving: {path}")
    return state


def save(data_dir: Path, state: dict) -> None:
    write_json(Path(data_dir) / FILE, state)


def validate_channel(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("channel must be an object")
    kind = data.get("type")
    if kind not in TYPES:
        raise ValueError("type must be email, telegram or webhook")
    channel = {
        "id": _text(data, "id", 1, 100),
        "name": _text(data, "name", 1, 60, strip=True),
        "type": kind,
        "enabled": _flag(data, "enabled"),
        "kinds": _kinds(data),
    }
    if kind == "email":
        channel.update(_email(data))
    elif kind == "telegram":
        channel.update(_telegram(data))
    else:
        channel.update(_webhook(data))
    return channel


def public(channel: dict) -> dict:
    secret = SECRETS[channel["type"]]
    shown = {k: v for k, v in channel.items() if k != secret}
    shown[f"{secret}_set"] = bool(channel.get(secret))
    return shown


def merge_edit(stored: dict, edit: dict) -> dict:
    kind = stored["type"]
    secret = SECRETS[kind]
    # Fields the edit leaves out keep their stored values.
    merged = {**stored, **edit, "id": stored["id"]}
    # A missing or blank secret keeps the stored one, but only when the type is unchanged.
    kept = merged["type"] == kind and _blank(edit.get(secret)) and bool(stored.get(secret))
    if kept:
        merged[secret] = stored[secret]
    channel = validate_channel(merged)
    if kept:
        for field in GUARDED[kind]:
            if field in channel and channel[field] != stored.get(field):
                raise ValueError(f"Enter the {SECRET_LABELS[kind]} again to change {field}")
    return channel


def new_silence(match: str, value: str, hours: int, now: datetime) -> dict:
    if match not in ("title", "kind"):
        raise ValueError("match must be title or kind")
    if isinstance(hours, bool) or hours not in SILENCE_HOURS:
        raise ValueError("hours must be 1, 8, 24 or 168")
    if not isinstance(value, str) or not value:
        raise ValueError("value must be text")
    if match == "kind" and value not in KINDS:
        raise ValueError("value must be one of the event kinds")
    until = now + timedelta(hours=hours)
    return {"id": secrets.token_urlsafe(8), "match": match, "value": value, "until": until.astimezone(UTC).isoformat()}


def active_silences(silences: list[dict], now: datetime) -> list[dict]:
    live = []
    for silence in silences:
        until = _until(silence)
        if until is not None and now < until:
            live.append(silence)
    return live


def silenced(silences: list[dict], event, now: datetime) -> bool:
    return any(_matches(s, event) for s in active_silences(silences, now))


def _matches(silence: dict, event) -> bool:
    return (silence.get("match") == "title" and silence.get("value") == event.title) or (
        silence.get("match") == "kind" and silence.get("value") == event.kind
    )


def _until(silence: dict) -> datetime | None:
    text = silence.get("until")
    if not isinstance(text, str):
        return None
    try:
        until = datetime.fromisoformat(text)
    except ValueError:
        return None
    return until if until.tzinfo else until.replace(tzinfo=UTC)


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(data: dict, field: str, lo: int, hi: int, strip: bool = False) -> str:
    value = data.get(field)
    if strip and isinstance(value, str):
        value = value.strip()
    if not isinstance(value, str) or not lo <= len(value) <= hi:
        raise ValueError(f"{field} must be text of {lo} to {hi} characters")
    return value


def _flag(data: dict, field: str) -> bool:
    value = data.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false")
    return value


def _int(data: dict, field: str, lo: int, hi: int) -> int:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{field} must be a whole number from {lo} to {hi}")
    return value


def _choice(data: dict, field: str, options: tuple, default=None):
    value = data.get(field, default)
    if value not in options:
        raise ValueError(f"{field} must be one of {list(options)}")
    return value


def _kinds(data: dict) -> list[str]:
    kinds = data.get("kinds")
    if not isinstance(kinds, list) or any(k not in KINDS for k in kinds):
        raise ValueError(f"kinds must be a list of event kinds from {list(KINDS)}")
    return [k for k in KINDS if k in kinds]


def _email(data: dict) -> dict:
    to = data.get("to")
    if not isinstance(to, list) or not 1 <= len(to) <= 10:
        raise ValueError(BAD_TO)
    try:
        addresses = [Address(addr_spec=a).addr_spec for a in to]
    except (TypeError, ValueError, MessageError):
        raise ValueError(BAD_TO) from None
    out = {"to": addresses, "use_scan_server": _flag(data, "use_scan_server")}
    if not out["use_scan_server"]:
        try:
            mail_from = sender(data.get("mail_from"))
        except ConfigError:
            raise ValueError("mail_from must be one email address, optionally with a name") from None
        out |= {
            "smtp_host": _text(data, "smtp_host", 1, 255),
            "smtp_port": _int(data, "smtp_port", 1, 65535),
            "smtp_security": _choice(data, "smtp_security", ("starttls", "tls")),
            "smtp_user": _text(data, "smtp_user", 0, 255),
            "smtp_password": _text(data, "smtp_password", 0, 1000),
            "mail_from": mail_from,
        }
    return out


def _telegram(data: dict) -> dict:
    token = data.get("bot_token")
    if not isinstance(token, str) or not re.fullmatch(r"[\x21-\x7e]+", token):
        raise ValueError("bot_token must be a token of printable characters with no spaces")
    chat = data.get("chat_id")
    if not isinstance(chat, str) or not re.fullmatch(r"-?[0-9]+|@[A-Za-z0-9_]{5,}", chat):
        raise ValueError("chat_id must be a number or @channelname")
    thread = data.get("message_thread_id")
    if thread is not None and (isinstance(thread, bool) or not isinstance(thread, int)):
        raise ValueError("message_thread_id must be a whole number or empty")
    return {
        "bot_token": token,
        "chat_id": chat,
        "message_thread_id": thread,
        "parse_mode": _choice(data, "parse_mode", PARSE_MODES, "HTML"),
        "disable_notification": _flag(data, "disable_notification"),
        "protect_content": _flag(data, "protect_content"),
        "disable_web_page_preview": _flag(data, "disable_web_page_preview"),
    }


def _webhook(data: dict) -> dict:
    url = data.get("url")
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
    except ValueError:
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("url must be an http or https address with a host")
    name = data.get("header_name")
    if not isinstance(name, str) or (name and not re.fullmatch(r"[A-Za-z0-9-]+", name)):
        raise ValueError("header_name must be empty or a header name such as Authorization")
    return {"url": url, "header_name": name, "header_value": _text(data, "header_value", 0, 1000)}
