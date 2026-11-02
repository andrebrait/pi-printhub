# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
import logging
import stat
import threading
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

    with pytest.raises(TokenStoreError):
        tokens.verify(tmp_path, "ph_anything", NOW)
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


def test_create_refuses_a_naive_now(tmp_path):
    with pytest.raises(ValueError):
        tokens.create(tmp_path, "Laptop", "read", NOW.replace(tzinfo=None))
    assert tokens.list_public(tmp_path) == []


def test_verify_refuses_a_naive_now(tmp_path):
    _, token = tokens.create(tmp_path, "Laptop", "read", NOW)

    with pytest.raises(ValueError):
        tokens.verify(tmp_path, token, NOW.replace(tzinfo=None))


@pytest.mark.parametrize("stored", ["2026-11-02T12:00:00", "not a time", None])
def test_an_unusable_last_used_counts_as_due_and_is_rewritten(tmp_path, stored):
    _, token = tokens.create(tmp_path, "Poller", "read", NOW)
    path = tmp_path / FILE
    data = json.loads(path.read_text())
    data["tokens"][0]["last_used"] = stored
    path.write_text(json.dumps(data))

    assert tokens.verify(tmp_path, token, NOW)["last_used"] == NOW.isoformat()
    assert json.loads(path.read_text())["tokens"][0]["last_used"] == NOW.isoformat()


@pytest.mark.parametrize("field", ["id", "name", "access", "created", "last_used"])
def test_a_record_missing_a_public_field_is_a_store_problem(tmp_path, field):
    _, token = tokens.create(tmp_path, "Laptop", "read", NOW)
    path = tmp_path / FILE
    data = json.loads(path.read_text())
    del data["tokens"][0][field]
    path.write_text(json.dumps(data))

    with pytest.raises(TokenStoreError):
        tokens.list_public(tmp_path)
    with pytest.raises(TokenStoreError):
        tokens.create(tmp_path, "Phone", "full", NOW)
    with pytest.raises(TokenStoreError):
        tokens.verify(tmp_path, token, NOW)


def test_repeated_verifies_write_at_most_once_a_minute(tmp_path, monkeypatch):
    _, token = tokens.create(tmp_path, "Poller", "read", NOW)
    writes = []
    real_write = tokens.write_json

    def counting_write(path, data):
        writes.append(path)
        real_write(path, data)

    monkeypatch.setattr(tokens, "write_json", counting_write)
    for seconds in range(0, 300, 10):
        assert tokens.verify(tmp_path, token, NOW + timedelta(seconds=seconds)) is not None

    assert len(writes) == 5


def test_a_failed_last_used_write_still_returns_the_record_and_logs(tmp_path, monkeypatch, caplog):
    record, token = tokens.create(tmp_path, "Poller", "read", NOW)

    def fail(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(tokens, "write_json", fail)
    with caplog.at_level(logging.ERROR):
        got = tokens.verify(tmp_path, token, NOW + timedelta(minutes=5))

    assert got["id"] == record["id"]
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert token not in caplog.text


def test_a_revoke_during_a_last_used_write_is_not_undone(tmp_path, monkeypatch):
    record, token = tokens.create(tmp_path, "Poller", "read", NOW)
    real_write = tokens._write
    revoker = threading.Thread(target=tokens.revoke, args=(tmp_path, record["id"]))

    def write_with_a_revoke_waiting(path, records):
        if threading.current_thread() is revoker:
            return real_write(path, records)
        revoker.start()
        revoker.join(0.3)  # with the lock the revoke waits for verify; without it, it finishes here
        real_write(path, records)

    monkeypatch.setattr(tokens, "_write", write_with_a_revoke_waiting)
    assert tokens.verify(tmp_path, token, NOW + timedelta(minutes=5)) is not None
    revoker.join(5)
    monkeypatch.undo()
    assert tokens.verify(tmp_path, token, NOW + timedelta(minutes=10)) is None
