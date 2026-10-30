# SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import replace
import secrets
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from printhub.auth import (
    COOKIE,
    LoginLimiter,
    PendingShares,
    hash_password,
    issue_session,
    origin_ok,
    require_session,
    session_valid,
    verify_password,
)

DAY = 86400


def test_password_hash_round_trip_verifies_and_rejects_wrong_password(cfg):
    hashed = hash_password("hunter2")

    assert verify_password(hashed, "hunter2")
    assert not verify_password(hashed, "hunter3")
    assert verify_password(cfg.password_hash, "secret")


def test_session_issued_now_is_valid(cfg):
    assert session_valid(cfg, issue_session(cfg))


def test_session_issued_31_days_ago_is_invalid(cfg, monkeypatch):
    old = int(time.time()) - 31 * DAY
    with monkeypatch.context() as m:
        m.setattr(TimestampSigner, "get_timestamp", lambda self: old)
        value = issue_session(cfg)

    assert not session_valid(cfg, value)


def test_session_with_tampered_signature_is_invalid(cfg):
    value = issue_session(cfg)
    payload, _, sig = value.rpartition(".")
    tampered_sig = ("A" if sig[0] != "A" else "B") + sig[1:]

    assert not session_valid(cfg, f"{payload}.{tampered_sig}")


def test_session_tamper_is_rejected_for_200_random_secrets(cfg):
    for _ in range(200):
        keyed = replace(cfg, session_secret=secrets.token_hex(16))
        value = issue_session(keyed)
        payload, _, sig = value.rpartition(".")
        tampered_sig = ("A" if sig[0] != "A" else "B") + sig[1:]

        assert session_valid(keyed, value)
        assert not session_valid(keyed, f"{payload}.{tampered_sig}")


def test_missing_session_cookie_is_invalid(cfg):
    assert not session_valid(cfg, None)


def test_require_session_rejects_missing_cookie_and_accepts_valid_one(cfg):
    app = FastAPI()

    @app.get("/private", dependencies=[Depends(require_session(lambda: cfg))])
    def private():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/private").status_code == 401

    client.cookies.set(COOKIE, issue_session(cfg))
    assert client.get("/private").status_code == 200


def test_login_limiter_allows_five_and_denies_sixth_within_window():
    limiter = LoginLimiter(limit=5, window=60.0)

    assert all(limiter.allow("10.0.0.1", now=100.0) for _ in range(5))
    assert not limiter.allow("10.0.0.1", now=160.0)


def test_login_limiter_allows_attempt_after_window():
    limiter = LoginLimiter(limit=5, window=60.0)
    assert all(limiter.allow("10.0.0.1", now=100.0) for _ in range(5))

    assert limiter.allow("10.0.0.1", now=161.0)


def test_login_limiter_keys_are_independent():
    limiter = LoginLimiter(limit=5, window=60.0)
    assert all(limiter.allow("10.0.0.1", now=100.0) for _ in range(5))

    assert limiter.allow("10.0.0.2", now=100.0)


def test_login_limiter_drops_keys_once_their_attempts_expire():
    limiter = LoginLimiter(limit=5, window=60.0)
    limiter.allow("10.0.0.1", now=100.0)
    limiter.allow("10.0.0.2", now=100.0)

    limiter.allow("10.0.0.3", now=200.0)

    assert set(limiter._hits) == {"10.0.0.3"}


def test_pending_share_take_within_ttl_returns_path_once(tmp_path):
    path = tmp_path / "upload.bin"
    path.write_bytes(b"x")
    shares = PendingShares(ttl=600.0)
    token = shares.put(path, "doc.pdf", now=1000.0)

    assert shares.take(token, now=1600.0) == (path, "doc.pdf")
    assert shares.take(token, now=1601.0) is None


def test_pending_share_take_after_ttl_returns_none_and_deletes_file(tmp_path):
    path = tmp_path / "upload.bin"
    path.write_bytes(b"x")
    shares = PendingShares(ttl=600.0)
    token = shares.put(path, "doc.pdf", now=1000.0)

    assert shares.take(token, now=1601.0) is None
    assert not path.exists()


def test_pending_share_put_deletes_files_of_expired_entries(tmp_path):
    old = tmp_path / "old.bin"
    old.write_bytes(b"x")
    new = tmp_path / "new.bin"
    new.write_bytes(b"x")
    shares = PendingShares(ttl=600.0)
    shares.put(old, "old.pdf", now=1000.0)

    shares.put(new, "new.pdf", now=1601.0)

    assert not old.exists()
    assert new.exists()


def test_pending_share_expiry_survives_unlink_failure(tmp_path):
    stuck = tmp_path / "stuck"
    stuck.mkdir()
    shares = PendingShares(ttl=600.0)
    stuck_token = shares.put(stuck, "stuck.pdf", now=1000.0)

    live = tmp_path / "live.bin"
    live.write_bytes(b"x")
    live_token = shares.put(live, "live.pdf", now=1601.0)

    assert shares.take(stuck_token, now=1602.0) is None
    assert shares.take(live_token, now=1602.0) == (live, "live.pdf")


@pytest.mark.parametrize(
    "method, origin, expected",
    [
        ("POST", "https://print.example.com", True),
        ("POST", "https://evil.example", False),
        ("POST", None, False),
        ("GET", None, True),
        ("HEAD", None, True),
        ("OPTIONS", None, True),
    ],
)
def test_origin_check_guards_state_changing_requests(cfg, method, origin, expected):
    assert origin_ok(method, origin, cfg) is expected
