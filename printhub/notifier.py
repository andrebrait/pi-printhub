# SPDX-License-Identifier: AGPL-3.0-only
"""Follows the event log from a saved position and hands each new event to every matching notification channel."""
import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from printhub import notifications, senders
from printhub.config import Config
from printhub.events import Event, EventLog
from printhub.settings import write_json

log = logging.getLogger(__name__)

CURSOR_FILE = "notify-cursor.json"
RETRY_AFTER = (60, 300, 900)


class Notifier:
    def __init__(
        self,
        events: EventLog,
        data_dir: Path,
        current: Callable[[], Config],
        send=senders.send,
        clock=time.monotonic,
        now=lambda: datetime.now(UTC),
    ):
        self.events, self.dir, self.current, self.send, self.clock, self.now = events, Path(data_dir), current, send, clock, now
        # channel id -> "Delivered HH:MM" or "Failed: reason", the last outcome.
        self.results: dict[str, str] = {}
        # A worker thread per enabled channel, fed through its queue; only tick() changes this dict.
        self._queues: dict[str, queue.Queue] = {}
        # (due, channel id, event, failures so far) for sends waiting to be retried.
        self._retries: list[tuple[float, str, Event, int]] = []
        self._lock = threading.Lock()
        # Problems notifications.load reported on the last tick, so a standing one is logged once, not every tick.
        self._load_errors: set[str] = set()
        self._last = self._read_cursor()
        if self._last is None:
            # First start: the history was never promised to this channel.
            self._last = events.latest()
            self._save_cursor()

    def _read_cursor(self) -> int | None:
        try:
            last = json.loads((self.dir / CURSOR_FILE).read_text())["last_id"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return last if isinstance(last, int) and not isinstance(last, bool) else None

    def _save_cursor(self) -> None:
        # The event is already queued: failing to remember it must not make this process send it again.
        try:
            write_json(self.dir / CURSOR_FILE, {"last_id": self._last})
        except OSError:
            log.exception("could not save the notification cursor")

    def tick(self) -> None:
        state = self._load()
        active = {c["id"]: c for c in state["channels"] if c["enabled"]}
        for gone in self._queues.keys() - active.keys():
            self._discard(self._queues.pop(gone))
        with self._lock:
            now = self.clock()
            due = [r for r in self._retries if r[0] <= now]
            self._retries = [r for r in self._retries if r[0] > now]
        for _, channel_id, event, failures in due:
            if channel_id in active and not notifications.silenced(state["silences"], event, self.now()):
                self._enqueue(active[channel_id], event, failures)
        if self.events.latest() < self._last:
            log.warning("The event log is behind the notification cursor (%d); it was probably emptied. Starting over", self._last)
            self._last = 0
            self._save_cursor()
        for event in self.events.after(self._last):
            if not notifications.silenced(state["silences"], event, self.now()):
                for channel in active.values():
                    if event.kind in channel["kinds"]:
                        self._enqueue(channel, event, 0)
            self._last = event.id
            self._save_cursor()

    def _load(self) -> dict:
        seen: set[str] = set()

        def report(message: str) -> None:
            if message not in self._load_errors:
                log.error(message)
            seen.add(message)

        state = notifications.load(self.dir, report)
        self._load_errors = seen
        return state

    @staticmethod
    def _discard(q: queue.Queue) -> None:
        # A disabled or deleted channel must not get its backlog, however long its worker sits in a send.
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            q.put(None)

    def _enqueue(self, channel: dict, event: Event, failures: int) -> None:
        if channel["id"] not in self._queues:
            q = self._queues[channel["id"]] = queue.Queue()
            threading.Thread(target=self._work, args=(q,), name=f"notify-{channel['id']}", daemon=True).start()
        self._queues[channel["id"]].put((channel, event, failures))

    def _work(self, q: queue.Queue) -> None:
        while (item := q.get()) is not None:
            self._deliver(*item)

    def _deliver(self, channel: dict, event: Event, failures: int) -> None:
        try:
            self.send(channel, event, self.current())
        except Exception as e:
            # Only SendError promises a message free of secrets; anything else is named by type alone.
            reason = str(e) if isinstance(e, senders.SendError) else "unexpected error"
            log.warning("notification to %r failed: %s (%s)", channel["name"], reason, type(e).__name__)
            if failures < len(RETRY_AFTER):
                with self._lock:
                    self._retries.append((self.clock() + RETRY_AFTER[failures], channel["id"], event, failures + 1))
            self.results[channel["id"]] = f"Failed: {reason}"
        else:
            self.results[channel["id"]] = "Delivered " + self.now().astimezone().strftime("%H:%M")

    def run(self, stop: threading.Event, interval: float = 2.0) -> None:
        try:
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("notifier tick failed")
                stop.wait(interval)
        finally:
            self.close()

    def close(self) -> None:
        # Events queued so far already moved the cursor, so the workers finish them before they exit.
        for q in self._queues.values():
            q.put(None)
        self._queues.clear()
