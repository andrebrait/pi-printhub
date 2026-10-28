# SPDX-License-Identifier: AGPL-3.0-only
import os
import tomllib
from dataclasses import MISSING, dataclass, fields
from pathlib import Path
from typing import Literal


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
    retention_days: int = 30
    min_free_gb: float = 2
    printer_queue: str = "OfficeJet"
    data_dir: Path = Path("/var/lib/printhub")
    origin: str = "https://print.brait.dev"


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
    if values["smtp_security"] not in ("starttls", "tls"):
        raise ConfigError(f'smtp_security must be "starttls" or "tls", got {values["smtp_security"]!r}')
    return Config(**values)
