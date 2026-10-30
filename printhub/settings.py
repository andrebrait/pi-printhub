# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
import os
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from email.errors import MessageError
from email.headerregistry import Address
from email.utils import getaddresses
from pathlib import Path

from printhub.config import Config, ConfigError, validate

log = logging.getLogger(__name__)

APP_KEYS: frozenset[str] = frozenset(
    {
        "retention_days",
        "min_free_gb",
        "smtp_host",
        "smtp_port",
        "smtp_security",
        "smtp_user",
        "smtp_password",
        "mail_from",
        "password_hash",
        "session_secret",
    }
)


def apply_overlay(cfg: Config) -> Config:
    path = cfg.data_dir / "settings.json"
    try:
        overlay = json.loads(path.read_text())
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError) as e:
        # Not a warning: the fallback brings back the old web password and session secret.
        log.error("ignoring %s: %s", path, e)
        return cfg
    if not isinstance(overlay, dict):
        log.error("ignoring %s: not a JSON object", path)
        return cfg
    for key, value in overlay.items():
        if key not in APP_KEYS:
            log.warning("ignoring settings key %s: unknown key", key)
            continue
        try:
            candidate = replace(cfg, **{key: value})
            validate(candidate)
        except ConfigError as e:
            log.warning("ignoring settings key %s: %s", key, e)
            continue
        cfg = candidate
    return cfg


def save(cfg: Config, changes: dict) -> Config:
    unknown = set(changes) - APP_KEYS
    if unknown:
        raise ConfigError(f"not a settings key: {', '.join(sorted(unknown))}")
    if "mail_from" in changes:
        # Stricter than validate: the page is where a person types the sender, and config.toml may hold an SMTP login.
        changes = {**changes, "mail_from": _sender(changes["mail_from"])}
    new = replace(cfg, **changes)
    validate(new)
    path = cfg.data_dir / "settings.json"
    write_json(path, {**(_read_json_object(path) or {}), **changes})
    return new


def _sender(value) -> str:
    """One address, optionally with a display name ("Printer <print@example.com>"), in canonical form."""
    try:
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise ValueError
        (name, addr), *rest = getaddresses([value])
        if rest:
            raise ValueError
        return str(Address(display_name=name, addr_spec=addr))  # Address refuses a malformed address
    except (TypeError, ValueError, MessageError):
        raise ConfigError(f"mail_from must be one email address, optionally with a name, got {value!r}") from None


def _read_json_object(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp creates the file 0600 and the rename keeps that mode
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


SYSTEM_REQUEST = "system-request.json"
SYSTEM_STATUS = "system-status.json"
SYSTEM_SWITCHES = ("share", "expose_usb")


def request_system(data_dir: Path, share: bool, expose_usb: bool) -> dict:
    request = {"share": share, "expose_usb": expose_usb, "at": datetime.now(timezone.utc).isoformat()}
    write_json(data_dir / SYSTEM_REQUEST, request)
    return request


def read_system(data_dir: Path) -> dict:
    # A malformed file counts as absent, so a bad write never breaks the page.
    status = _read_json_object(data_dir / SYSTEM_STATUS)
    request = _read_json_object(data_dir / SYSTEM_REQUEST)
    if request is None or not (
        all(isinstance(request.get(k), bool) for k in SYSTEM_SWITCHES) and isinstance(request.get("at"), str)
    ):
        return {"requested": dict.fromkeys(SYSTEM_SWITCHES, True), "status": status, "pending": False}
    return {
        "requested": {k: request[k] for k in SYSTEM_SWITCHES},
        "status": status,
        "pending": status is None or status.get("request_at") != request["at"],
    }
