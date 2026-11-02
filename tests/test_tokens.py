# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
import logging
import stat
from datetime import UTC, datetime, timedelta

import pytest

from printhub import tokens
from printhub.auth import LoginLimiter
from printhub.tokens import TokenStoreError

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
FILE = "api-tokens.json"


def test_create_returns_the_public_record_and_a_ph_token_once(tmp_path):
    record, token = tokens.create(tmp_path, "  Home Assistant ", "read", NOW)

    assert token.startswith("ph_") and len(token) == len("ph_") + 43
    assert record == {
        "id": record["id"],
        "name": "Home Assistant",
        "access": "read",
        "created": NOW.isoformat(),
        "last_used": None,
    }
    assert tokens.list_public(tmp_path) == [record]
    assert token not in json.dumps(tokens.list_public(tmp_path))


def test_only_the_hash_is_stored_and_the_file_is_private(tmp_path):
    _, token = tokens.create(tmp_path, "Laptop", "full", NOW)
    path = tmp_path / FILE

    text = path.read_text()
    assert token not in text
    assert hashlib.sha256(token.encode()).hexdigest() in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_verify_accepts_the_presented_token_and_revoked_tokens_stop_working(tmp_path):
    record, token = tokens.create(tmp_path, "Laptop", "full", NOW)
    _, other = tokens.create(tmp_path, "Phone", "read", NOW)

    assert tokens.verify(tmp_path, token, NOW)["access"] == "full"
    assert tokens.verify(tmp_path, other, NOW)["name"] == "Phone"
    assert tokens.verify(tmp_path, "ph_wrong", NOW) is None
    assert tokens.revoke(tmp_path, record["id"]) is True
    assert tokens.revoke(tmp_path, record["id"]) is False
    assert tokens.verify(tmp_path, token, NOW) is None
    assert tokens.verify(tmp_path, other, NOW) is not None


def test_a_wrong_token_of_the_same_length_is_rejected(tmp_path):
    _, token = tokens.create(tmp_path, "Laptop", "full", NOW)
    forged = token[:-1] + ("A" if token[-1] != "A" else "B")

    assert len(forged) == len(token)
    assert tokens.verify(tmp_path, forged, NOW) is None


@pytest.mark.parametrize("presented", ["", "Bearer", "pk_abc", "PH_abc"])
def test_a_string_without_the_ph_prefix_is_refused_without_reading_the_file(tmp_path, caplog, presented):
    (tmp_path / FILE).write_text("{not json")

    assert tokens.verify(tmp_path, presented, NOW) is None
    assert not caplog.records


def test_last_used_is_written_at_most_once_a_minute(tmp_path):
    _, token = tokens.create(tmp_path, "Poller", "read", NOW)

    assert tokens.verify(tmp_path, token, NOW)["last_used"] == NOW.isoformat()
    assert tokens.verify(tmp_path, token, NOW + timedelta(seconds=30))["last_used"] == NOW.isoformat()
    later = NOW + timedelta(seconds=60)
    assert tokens.verify(tmp_path, token, later)["last_used"] == later.isoformat()


@pytest.mark.parametrize("content", ["{not json", "[]", json.dumps({"tokens": [{"id": "x"}]})])
def test_a_corrupt_file_fails_verify_and_writes_refuse_without_overwriting(tmp_path, caplog, content):
    path = tmp_path / FILE
    path.write_text(content)

    assert tokens.verify(tmp_path, "ph_anything", NOW) is None
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    with pytest.raises(TokenStoreError):
        tokens.create(tmp_path, "Laptop", "read", NOW)
    with pytest.raises(TokenStoreError):
        tokens.revoke(tmp_path, "x")
    assert path.read_text() == content


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
def test_name_must_be_1_to_60_characters_after_stripping(tmp_path, name):
    with pytest.raises(ValueError):
        tokens.create(tmp_path, name, "read", NOW)
    assert tokens.create(tmp_path, "x" * 60, "read", NOW)[0]["name"] == "x" * 60


def test_access_must_be_read_or_full(tmp_path):
    with pytest.raises(ValueError):
        tokens.create(tmp_path, "Laptop", "admin", NOW)
    assert tokens.list_public(tmp_path) == []


def test_blocked_reports_the_limit_without_counting_as_an_attempt():
    limiter = LoginLimiter(limit=2, window=60)

    assert not limiter.blocked("203.0.113.5", 0)
    assert not limiter.blocked("203.0.113.5", 0)
    assert limiter.allow("203.0.113.5", 0)
    assert limiter.allow("203.0.113.5", 1)
    assert limiter.blocked("203.0.113.5", 2)
    assert not limiter.allow("203.0.113.5", 2)
    assert not limiter.blocked("203.0.113.5", 61)
