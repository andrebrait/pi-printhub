# SPDX-License-Identifier: AGPL-3.0-only
"""API tokens: creation, verification and revocation. Only the SHA-256 hash of a token is stored."""
import hashlib
import hmac
import json
import logging
import secrets
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from printhub.settings import write_json

log = logging.getLogger(__name__)

FILE = "api-tokens.json"
PREFIX = "ph_"
ACCESS = ("read", "full")
PUBLIC_FIELDS = ("id", "name", "access", "created", "last_used")
TOUCH_EVERY = timedelta(seconds=60)
# Serialises read-modify-write so a verify that records last_used cannot undo a concurrent revoke.
_lock = threading.Lock()


class TokenStoreError(Exception):
    pass


def _read(path: Path) -> list[dict]:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise TokenStoreError(f"{FILE} cannot be read ({e}); fix or remove it before saving: {path}")
    if not isinstance(raw, dict) or not isinstance(raw.get("tokens"), list) or not all(
        _valid_record(t) for t in raw["tokens"]
    ):
        raise TokenStoreError(f"{FILE} has the wrong shape; fix or remove it before saving: {path}")
    return raw["tokens"]


def _write(path: Path, tokens: list[dict]) -> None:
    write_json(path, {"tokens": tokens})


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat()


def _public(record: dict) -> dict:
    return {k: record[k] for k in PUBLIC_FIELDS}


def _valid_record(record: object) -> bool:
    return isinstance(record, dict) and isinstance(record.get("hash"), str) and all(f in record for f in PUBLIC_FIELDS)


def _touch_due(record: dict, now: datetime) -> bool:
    try:
        return now - datetime.fromisoformat(record["last_used"]) >= TOUCH_EVERY
    except (TypeError, ValueError):
        # Unparsable or timezone-less stored values are rewritten rather than trusted.
        return True


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")


def create(data_dir: Path, name: str, access: str, now: datetime) -> tuple[dict, str]:
    """Returns the public record and the token; the token is not stored, so it cannot be shown again."""
    _require_aware(now)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ValueError("name must be 1 to 60 characters")
    if access not in ACCESS:
        raise ValueError("access must be read or full")
    token = PREFIX + secrets.token_urlsafe(32)
    record = {
        "id": secrets.token_urlsafe(8),
        "name": name.strip(),
        "access": access,
        "created": _iso(now),
        "last_used": None,
        "hash": _digest(token),
    }
    path = Path(data_dir) / FILE
    with _lock:
        _write(path, _read(path) + [record])
    return _public(record), token


def list_public(data_dir: Path) -> list[dict]:
    return [_public(record) for record in _read(Path(data_dir) / FILE)]


def revoke(data_dir: Path, token_id: str) -> bool:
    path = Path(data_dir) / FILE
    with _lock:
        records = _read(path)
        kept = [record for record in records if record.get("id") != token_id]
        if len(kept) == len(records):
            return False
        _write(path, kept)
        return True


def verify(data_dir: Path, presented: str, now: datetime) -> dict | None:
    _require_aware(now)
    if not isinstance(presented, str) or not presented.startswith(PREFIX):
        return None
    digest = _digest(presented).encode()
    path = Path(data_dir) / FILE
    with _lock:
        try:
            records = _read(path)
        except TokenStoreError as e:
            log.error("API tokens unreadable; refusing every token: %s", e)
            return None
        match = None
        for record in records:
            # No early exit: the time taken does not reveal which stored hash matched.
            if hmac.compare_digest(record["hash"].encode(), digest):
                match = record
        if match is None:
            return None
        if _touch_due(match, now):
            match["last_used"] = _iso(now)
            try:
                _write(path, records)
            except OSError as e:
                log.error("Could not record API token use: %s", e)
        return _public(match)
