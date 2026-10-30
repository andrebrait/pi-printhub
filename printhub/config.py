# SPDX-License-Identifier: AGPL-3.0-only
import logging
import os
import tomllib
from dataclasses import MISSING, dataclass, fields
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    password_hash: str
    session_secret: str
    smtp_host: str
    smtp_port: int
    smtp_security: Literal["starttls", "tls"]
    smtp_user: str
    smtp_password: str
    mail_from: str
    origin: str  # the web app's public URL, e.g. "https://print.example.com"
    retention_days: int = 30
    min_free_gb: float = 2
    printer_queue: str = "OfficeJet"
    data_dir: Path = Path("/var/lib/printhub")


def validate(cfg: Config) -> None:
    _check(cfg.retention_days, "retention_days", int, 1, 365)
    _check(cfg.min_free_gb, "min_free_gb", (int, float), 0.5, 1000)
    _check(cfg.smtp_port, "smtp_port", int, 1, 65535)
    if cfg.smtp_security not in ("starttls", "tls"):
        raise ConfigError(f'smtp_security must be "starttls" or "tls", got {cfg.smtp_security!r}')


def _check(value, key: str, kinds: type | tuple[type, ...], lo, hi) -> None:
    if isinstance(value, bool) or not isinstance(value, kinds) or not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo} and {hi}, got {value!r}")


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = Path(os.environ.get("PRINTHUB_CONFIG", "/etc/printhub/config.toml"))
    try:
        raw = tomllib.loads(path.read_text())
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e

    values = {}
    for f in fields(Config):
        if f.name in raw:
            values[f.name] = raw[f.name]
        elif f.default is MISSING:
            raise ConfigError(f"missing required key: {f.name}")
    if "data_dir" in values:
        values["data_dir"] = Path(values["data_dir"])
    cfg = Config(**values)
    validate(cfg)
    # settings imports this module, so load it lazily
    from printhub.settings import apply_overlay

    return apply_overlay(cfg)
