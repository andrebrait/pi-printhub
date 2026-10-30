# SPDX-License-Identifier: AGPL-3.0-only
"""Notification events: job outcomes, printer problems and failures, with read state shared by every device."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Level = Literal["info", "warning", "error"]
Kind = Literal["job", "printer", "scan", "conversion", "settings"]


@dataclass
class Event:
    id: int
    at: str
    level: Level
    kind: Kind
    title: str
    detail: str = ""
    job_id: int | None = None
    doc_id: str | None = None


_EVENT_FIELDS = {f.name for f in fields(Event)}


def _replace(path: Path, text: str) -> None:
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


class EventLog:
    def __init__(self, data_dir: Path, keep: int = 500):
        self.dir = Path(data_dir)
        self.keep = keep
        self.events_path = self.dir / "events.jsonl"
        self.read_path = self.dir / "notifications-read.json"

    @contextmanager
    def _locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "events.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _events(self) -> list[Event]:
        try:
            lines = self.events_path.read_text(errors="replace").splitlines()
        except FileNotFoundError:
            return []
        out = []
        for line in lines:
            # A power loss mid-append or a stray edit leaves a bad line; it must not hide the rest.
            try:
                event = Event(**{k: v for k, v in json.loads(line).items() if k in _EVENT_FIELDS})
            except (ValueError, TypeError, AttributeError):
                continue
            if isinstance(event.id, int):
                out.append(event)
        return out

    def _read_state(self) -> tuple[int, set[int]]:
        try:
            raw = json.loads(self.read_path.read_text())
            return int(raw.get("up_to", 0)), {int(i) for i in raw.get("ids", [])}
        except (FileNotFoundError, ValueError, TypeError, AttributeError):
            return 0, set()

    def append(self, level: Level, kind: Kind, title: str, detail: str = "", *,
               job_id: int | None = None, doc_id: str | None = None) -> Event:
        with self._locked():
            events = self._events()
            event = Event(
                id=max((e.id for e in events), default=0) + 1,
                at=datetime.now(UTC).isoformat(timespec="seconds"),
                level=level, kind=kind, title=title, detail=detail, job_id=job_id, doc_id=doc_id,
            )
            events = [*events, event][-self.keep:]
            _replace(self.events_path, "".join(json.dumps(asdict(e)) + "\n" for e in events))
            return event

    def page(self, before: int | None = None, limit: int = 50) -> list[tuple[Event, bool]]:
        events = self._events()
        up_to, read_ids = self._read_state()
        newest = sorted((e for e in events if before is None or e.id < before), key=lambda e: e.id, reverse=True)
        return [(e, e.id <= up_to or e.id in read_ids) for e in newest[:limit]]

    def unread(self) -> int:
        up_to, read_ids = self._read_state()
        return sum(1 for e in self._events() if e.id > up_to and e.id not in read_ids)

    def latest(self) -> int:
        return max((e.id for e in self._events()), default=0)

    def mark_read(self, *, up_to: int | None = None, ids: list[int] | None = None) -> None:
        with self._locked():
            # Never past the newest event: ids not yet issued must not be born read.
            latest = self.latest()
            current, read_ids = self._read_state()
            current = max(current, min(up_to or 0, latest))
            read_ids = {i for i in read_ids | set(ids or []) if current < i <= latest}
            _replace(self.read_path, json.dumps({"up_to": current, "ids": sorted(read_ids)}))
