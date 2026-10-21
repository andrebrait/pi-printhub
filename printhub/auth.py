# SPDX-License-Identifier: AGPL-3.0-only
import secrets
from collections import deque
from collections.abc import Callable
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, TimestampSigner

from printhub.config import Config

COOKIE = "printhub_session"
SESSION_MAX_AGE = 30 * 86400
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def hash_password(pw: str) -> str:
    return PasswordHasher().hash(pw)


def verify_password(hash: str, pw: str) -> bool:
    try:
        return PasswordHasher().verify(hash, pw)
    except VerificationError:
        return False


def issue_session(cfg: Config) -> str:
    return TimestampSigner(cfg.session_secret).sign("session").decode()


def session_valid(cfg: Config, value: str | None) -> bool:
    if value is None:
        return False
    try:
        TimestampSigner(cfg.session_secret).unsign(value, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return False
    return True


def set_session_cookie(response: Response, value: str) -> None:
    response.set_cookie(
        COOKIE,
        value,
        max_age=SESSION_MAX_AGE,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


def require_session(cfg: Config) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        if not session_valid(cfg, request.cookies.get(COOKIE)):
            raise HTTPException(status_code=401)

    return dependency


class LoginLimiter:
    def __init__(self, limit: int = 5, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float) -> bool:
        self._prune(now)
        hits = self._hits.setdefault(key, deque())
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def _prune(self, now: float) -> None:
        # Sweep every key so clients that never return do not pin memory.
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if not hits:
                del self._hits[key]


class PendingShares:
    def __init__(self, ttl: float = 600.0):
        self.ttl = ttl
        self._entries: dict[str, tuple[Path, str, float]] = {}

    def put(self, path: Path, filename: str, now: float) -> str:
        self._sweep(now)
        token = secrets.token_urlsafe(32)
        self._entries[token] = (path, filename, now)
        return token

    def take(self, token: str, now: float) -> tuple[Path, str] | None:
        self._sweep(now)
        entry = self._entries.pop(token, None)
        if entry is None:
            return None
        path, filename, _ = entry
        return path, filename

    def _sweep(self, now: float) -> None:
        for token, (path, _, created) in list(self._entries.items()):
            if now - created > self.ttl:
                path.unlink(missing_ok=True)
                del self._entries[token]


def origin_ok(method: str, origin: str | None, cfg: Config) -> bool:
    return method.upper() in _SAFE_METHODS or origin == cfg.origin
