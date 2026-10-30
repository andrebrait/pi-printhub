# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
import os

import pytest

from printhub.config import ConfigError, load_config
from printhub.settings import read_system, save, write_json

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


@pytest.mark.parametrize(
    "value",
    [
        "apikey", "AKIAXXXXXXXX", "no-at-sign", "a@example.com, b@example.com", "Hub <apikey>", "Hub",
        "", "a@example.com\nBcc: x@example.com", "Hub <a@example.com>\r\nBcc: x@example.com", 5,
    ],
)
def test_the_page_only_saves_one_address_as_sender(cfg, value):
    with pytest.raises(ConfigError, match="mail_from must be one email address"):
        save(cfg, {"mail_from": value})

    assert not (cfg.data_dir / "settings.json").exists()


@pytest.mark.parametrize(
    "value, saved",
    [
        ("hub@example.com", "hub@example.com"),
        ("Printer <print@example.com>", "Printer <print@example.com>"),
        ('"Print, Hub" <print@example.com>', '"Print, Hub" <print@example.com>'),
        ("  Printer   <print@example.com> ", "Printer <print@example.com>"),
    ],
)
def test_the_page_saves_an_address_with_or_without_a_name_as_sender(cfg, value, saved):
    assert save(cfg, {"mail_from": value}).mail_from == saved


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


@pytest.mark.parametrize("text", ["{", "[1]"])
def test_corrupt_settings_file_falls_back_to_config_toml_and_is_logged_as_an_error(toml, tmp_path, caplog, text):
    # The fallback restores the old password and session secret, which the owner must be able to see in the log.
    write_overlay(tmp_path, text)

    with caplog.at_level(logging.WARNING):
        cfg = load_config(toml)

    assert cfg.retention_days == 30
    assert any(r.levelno == logging.ERROR and "settings.json" in r.getMessage() for r in caplog.records)


def test_a_status_for_an_older_request_is_still_pending(tmp_path):
    request = {"share": False, "expose_usb": True, "at": "2026-10-30T10:00:02+00:00"}
    (tmp_path / "system-request.json").write_text(json.dumps(request))
    status = {"state": "applied", "share": True, "expose_usb": True, "error": None,
              "at": "2026-10-30T10:00:01+00:00", "request_at": "2026-10-30T10:00:01+00:00"}
    (tmp_path / "system-status.json").write_text(json.dumps(status))
    assert read_system(tmp_path)["pending"] is True

    (tmp_path / "system-status.json").write_text(json.dumps({**status, "request_at": request["at"]}))
    assert read_system(tmp_path)["pending"] is False


def test_a_saved_file_is_synced_after_its_rename(tmp_path, monkeypatch):
    # The rename is only durable once the directory entry is on disk, so the directory is synced after the replace.
    events = []
    dir_ino = os.stat(tmp_path).st_ino
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        events.append("dir fsync" if os.fstat(fd).st_ino == dir_ino else "file fsync")
        real_fsync(fd)

    def replace(src, dst):
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    write_json(tmp_path / "settings.json", {"retention_days": 7})

    assert events == ["file fsync", "replace", "dir fsync"]
