# SPDX-License-Identifier: AGPL-3.0-only
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from printhub.config import load_config
from printhub.documents import DocStore


class DiskFull(Exception):
    pass


def purge_expired(
    store: DocStore, retention_days: int, now: datetime, busy: set[str]
) -> list[str]:
    cutoff = now - timedelta(days=retention_days)
    expired = [
        d.id
        for d in store.list()
        if d.id not in busy and datetime.fromisoformat(d.modified) < cutoff
    ]
    for id in expired:
        store.delete(id)
    return expired


def ensure_free_space(
    store: DocStore,
    min_free_bytes: int,
    busy: set[str],
    free_bytes: Callable[[], int],
) -> None:
    # Oldest first: list() is newest first.
    for doc in reversed(store.list()):
        if free_bytes() >= min_free_bytes:
            return
        if doc.id not in busy:
            store.delete(doc.id)
    if free_bytes() < min_free_bytes:
        raise DiskFull(f"need {min_free_bytes} free bytes; only busy documents remain")


def main() -> None:
    cfg = load_config()
    store = DocStore(cfg.data_dir / "docs")
    purge_expired(store, cfg.retention_days, datetime.now(timezone.utc), busy=set())


if __name__ == "__main__":
    main()
