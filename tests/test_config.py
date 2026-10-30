# SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from printhub.config import ConfigError, load_config, validate

REQUIRED = """\
password_hash = "$argon2id$hash"
session_secret = "session-secret"
smtp_host = "mail.example.com"
smtp_port = 587
smtp_security = "starttls"
smtp_user = "printhub"
smtp_password = "smtp-secret"
mail_from = "printhub@example.com"
origin = "https://print.example.com"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_load_full_config(tmp_path):
    path = write(
        tmp_path,
        REQUIRED
        + """\
retention_days = 7
min_free_gb = 5.5
printer_queue = "Office2"
data_dir = "/srv/printhub"
""",
    )

    cfg = load_config(path)

    assert cfg.retention_days == 7
    assert cfg.min_free_gb == 5.5
    assert cfg.printer_queue == "Office2"
    assert cfg.data_dir == Path("/srv/printhub")
    assert cfg.origin == "https://print.example.com"
    assert cfg.smtp_host == "mail.example.com"


def test_defaults_applied(tmp_path):
    cfg = load_config(write(tmp_path, REQUIRED))

    assert cfg.retention_days == 30
    assert cfg.min_free_gb == 2
    assert cfg.printer_queue == "OfficeJet"
    assert cfg.data_dir == Path("/var/lib/printhub")


@pytest.mark.parametrize("line", ['smtp_host = "mail.example.com"\n', 'origin = "https://print.example.com"\n'])
def test_missing_required_key(tmp_path, line):
    key = line.split(" ")[0]
    with pytest.raises(ConfigError, match=key):
        load_config(write(tmp_path, REQUIRED.replace(line, "")))


def test_invalid_smtp_security(tmp_path):
    text = REQUIRED.replace('smtp_security = "starttls"', 'smtp_security = "ssl"')

    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text))


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINTHUB_CONFIG", str(write(tmp_path, REQUIRED)))

    assert load_config().smtp_user == "printhub"


def test_config_toml_out_of_range_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="retention_days"):
        load_config(write(tmp_path, REQUIRED + "retention_days = 0\n"))


@pytest.mark.parametrize(
    "value",
    ["a@example.com\nBcc: x@example.com", "a@example.com\r", "a@example.com, b@example.com", "Hub <a@example.com>", "", "no-at-sign", 5],
)
def test_mail_from_must_be_one_plain_address(cfg, value):
    with pytest.raises(ConfigError, match="mail_from"):
        validate(replace(cfg, mail_from=value))


def test_min_free_gb_must_be_below_the_data_disk(cfg, monkeypatch):
    cfg.data_dir.mkdir()
    monkeypatch.setattr("printhub.config.shutil.disk_usage", lambda path: SimpleNamespace(total=10 * 1024**3))
    validate(replace(cfg, min_free_gb=9.5))
    for value in (10, 11):
        with pytest.raises(ConfigError, match="min_free_gb"):
            validate(replace(cfg, min_free_gb=value))


def test_min_free_gb_is_not_compared_to_a_disk_that_is_not_there_yet(cfg, monkeypatch):
    monkeypatch.setattr("printhub.config.shutil.disk_usage", lambda path: SimpleNamespace(total=1024**3))
    validate(replace(cfg, min_free_gb=100))  # data_dir does not exist
