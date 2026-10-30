# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
import os
import tempfile
from dataclasses import replace
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
        log.warning("ignoring %s: %s", path, e)
        return cfg
    if not isinstance(overlay, dict):
        log.warning("ignoring %s: not a JSON object", path)
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
    new = replace(cfg, **changes)
    validate(new)
    path = cfg.data_dir / "settings.json"
    write_json(path, {**_read_overlay(path), **changes})
    return new


def _read_overlay(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
