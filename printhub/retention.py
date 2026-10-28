# SPDX-License-Identifier: AGPL-3.0-only
import cups
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from printhub import printing
from printhub.config import load_config
from printhub.documents import Doc, DocBusy, DocNotFound, DocStore


class DiskFull(Exception):
    pass

def busy_policy(cfg, cups_conn_factory=None) -> Callable[[Doc], bool]:
    def busy(doc):
        if doc.scanning:
            return True
        if not doc.prints:
            return False
        try:
            active = printing.active_job_ids((cups_conn_factory or printing.connect)(), cfg.printer_queue)
            return any(p.job_id in active for p in doc.prints)
        except (cups.IPPError, RuntimeError):
            # Held jobs have no age bound; keep history-bearing docs until CUPS can answer.
            return True
    return busy


def _delete_if(store, id, eligible):
    try:
        return store.delete(id, eligible)
    except (DocBusy, DocNotFound):
        return False



def purge_expired(
    store: DocStore, retention_days: int, now: datetime, busy: Callable[[Doc], bool]
) -> list[str]:
    cutoff = now - timedelta(days=retention_days)
    deleted = []
    for doc in store.list():
        if _delete_if(store, doc.id, lambda current: (
            not busy(current) and datetime.fromisoformat(current.modified) < cutoff
        )):
            deleted.append(doc.id)
    return deleted


def ensure_free_space(
    store: DocStore,
    min_free_bytes: int,
    busy: Callable[[Doc], bool],
    free_bytes: Callable[[], int],
) -> None:
    # Oldest first: list() is newest first.
    for doc in reversed(store.list()):
        if free_bytes() >= min_free_bytes:
            return
        _delete_if(store, doc.id, lambda current: not busy(current))
    if free_bytes() < min_free_bytes:
        raise DiskFull(f"need {min_free_bytes} free bytes; only busy documents remain")


def main() -> None:
    cfg = load_config()
    store = DocStore(cfg.data_dir / "docs")
    purge_expired(store, cfg.retention_days, datetime.now(timezone.utc), busy=busy_policy(cfg))


if __name__ == "__main__":
    main()
