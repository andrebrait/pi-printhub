# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging

import pytest

from printhub.config import ConfigError, load_config
from printhub.settings import save

TOML = """\
password_hash = "$argon2id$hash"
session_secret = "session-secret"
smtp_host = "mail.example.com"
smtp_port = 587
smtp_security = "starttls"
smtp_user = "printhub"
smtp_password = "smtp-secret"
mail_from = "printhub@example.com"
origin = "https://print.example.com"
retention_days = 30
"""


@pytest.fixture
def toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(TOML + f'data_dir = "{tmp_path / "data"}"\n')
    return path


def write_overlay(tmp_path, text):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "settings.json").write_text(text)


def test_saved_values_override_config_toml(toml):
    save(load_config(toml), {"retention_days": 7})

    cfg = load_config(toml)

    assert cfg.retention_days == 7
    assert cfg.smtp_host == "mail.example.com"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("retention_days", 0),
        ("retention_days", 366),
        ("min_free_gb", 0.4),
        ("smtp_port", 0),
        ("smtp_port", 65536),
        ("smtp_security", "ssl"),
        ("origin", "https://elsewhere.example.com"),
    ],
)
def test_save_rejects_out_of_range_and_writes_nothing(cfg, key, value):
    with pytest.raises(ConfigError, match=key):
        save(cfg, {key: value})

    assert not (cfg.data_dir / "settings.json").exists()


def test_saved_file_is_private(cfg):
    save(cfg, {"retention_days": 7})

    assert (cfg.data_dir / "settings.json").stat().st_mode & 0o777 == 0o600


def test_unknown_or_invalid_overlay_key_is_skipped_with_a_warning(toml, tmp_path, caplog):
    write_overlay(tmp_path, json.dumps({"retention_days": 0, "colour": "red", "min_free_gb": 3}))

    with caplog.at_level(logging.WARNING):
        cfg = load_config(toml)

    assert cfg.retention_days == 30
    assert cfg.min_free_gb == 3
    assert "retention_days" in caplog.text
    assert "colour" in caplog.text


def test_corrupt_settings_file_falls_back_to_config_toml(toml, tmp_path, caplog):
    write_overlay(tmp_path, "{")

    with caplog.at_level(logging.WARNING):
        cfg = load_config(toml)

    assert cfg.retention_days == 30
    assert any(r.levelno == logging.WARNING for r in caplog.records)
