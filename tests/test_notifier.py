# SPDX-License-Identifier: AGPL-3.0-only
import json
import logging
import queue
import threading
import time
from datetime import UTC, datetime

import pytest

from printhub import notifications
from printhub.events import EventLog
from printhub.notifier import Notifier
from printhub.senders import SendError

NOW = datetime(2026, 10, 30, 12, 34, tzinfo=UTC)
SECRET = "s3cret-header"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def channel(cid, **over):
    return {
        "id": cid, "name": cid.upper(), "type": "webhook", "enabled": True, "kinds": list(notifications.KINDS),
        "url": "http://hook.example/x", "header_name": "Authorization", "header_value": SECRET, **over,
    }


class Rig:
    """A real EventLog and channel file in tmp; `send` is a fake that records (channel id, title, clock) and
    fails with "down N" for events titled "boom" (N counts the attempts so far)."""

    def __init__(self, tmp_path, cfg):
        self.dir, self.cfg = tmp_path, cfg
        self.events = EventLog(tmp_path)
        self.clock = Clock()
        self.calls: list[tuple[str, str, float]] = []
        self.sent: queue.Queue = queue.Queue()
        self.block: dict[str, threading.Event] = {}
        self.entered = threading.Event()
        self.notifiers: list[Notifier] = []

    def channels(self, *channels, silences=()):
        notifications.save(self.dir, {"channels": list(channels), "silences": list(silences)})

    def send(self, ch, event, cfg):
        assert cfg is self.cfg
        if ch["id"] in self.block:
            self.entered.set()
            assert self.block[ch["id"]].wait(5)
        self.calls.append((ch["id"], event.title, self.clock()))
        self.sent.put((ch["id"], event.title))
        if event.title == "boom":
            raise SendError(f"down {sum(1 for c in self.calls if c[1] == 'boom')}")

    def notifier(self, send=None):
        n = Notifier(self.events, self.dir, lambda: self.cfg, send=send or self.send, clock=self.clock, now=lambda: NOW)
        self.notifiers.append(n)
        return n

    def got(self, count=1):
        return sorted(self.sent.get(timeout=1) for _ in range(count))


@pytest.fixture
def rig(tmp_path, cfg):
    r = Rig(tmp_path, cfg)
    yield r
    for flag in r.block.values():
        flag.set()
    for n in r.notifiers:
        n.close()


def until(condition, timeout=1.0):
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "condition not met in time"
        time.sleep(0.005)


def test_first_start_does_not_replay_history(rig):
    rig.channels(channel("a"))
    rig.events.append("info", "job", "old 1")
    rig.events.append("info", "job", "old 2")
    n = rig.notifier()
    assert json.loads((rig.dir / "notify-cursor.json").read_text()) == {"last_id": 2}
    n.tick()
    rig.events.append("info", "job", "new")
    n.tick()
    assert rig.got() == [("a", "new")]
    assert rig.sent.empty()


def test_restart_resumes_after_cursor_once(rig):
    rig.channels(channel("a"))
    first = rig.notifier()
    for n in (1, 2, 3):
        rig.events.append("info", "job", f"e{n}")
    first.tick()
    assert rig.got(3) == [("a", "e1"), ("a", "e2"), ("a", "e3")]
    first.close()
    # Written while the app was down: a restart that forgot the cursor would take them for history and skip them.
    for n in (4, 5):
        rig.events.append("info", "job", f"e{n}")
    second = rig.notifier()
    second.tick()
    second.tick()
    assert rig.got(2) == [("a", "e4"), ("a", "e5")]
    assert rig.sent.empty()


def test_kinds_and_silences_filter(rig):
    rig.channels(
        channel("jobs", kinds=["job"]),
        channel("all"),
        silences=[
            notifications.new_silence("title", "Muted title", 1, NOW),
            notifications.new_silence("kind", "scan", 1, NOW),
            {"id": "old", "match": "title", "value": "Expired", "until": "2026-10-29T00:00:00+00:00"},
        ],
    )
    n = rig.notifier()
    rig.events.append("info", "job", "Muted title")
    rig.events.append("info", "scan", "Scan done")
    rig.events.append("info", "printer", "Printer ok")
    rig.events.append("info", "job", "Expired")
    n.tick()
    assert rig.got(3) == [("all", "Expired"), ("all", "Printer ok"), ("jobs", "Expired")]
    assert rig.sent.empty()


def test_disabled_channel_gets_nothing(rig):
    rig.channels(channel("on"), channel("off", enabled=False))
    n = rig.notifier()
    rig.events.append("info", "job", "one")
    n.tick()
    assert rig.got() == [("on", "one")]
    rig.channels(channel("on", enabled=False), channel("off", enabled=False))
    rig.events.append("info", "job", "two")
    n.tick()
    rig.channels(channel("on"))
    rig.events.append("info", "job", "three")
    n.tick()
    assert rig.got() == [("on", "three")]
    assert rig.sent.empty()


def test_retry_schedule_then_drop(rig):
    rig.channels(channel("a"))
    n = rig.notifier()
    rig.events.append("error", "job", "boom")
    n.tick()
    until(lambda: n.results.get("a") == "Failed: down 1")
    for step, attempt in ((60, 2), (300, 3), (900, 4)):
        rig.clock.advance(step - 1)
        n.tick()
        rig.clock.advance(1)
        n.tick()
        until(lambda attempt=attempt: n.results.get("a") == f"Failed: down {attempt}")
    assert [(title, t) for _, title, t in rig.calls] == [("boom", 0), ("boom", 60), ("boom", 360), ("boom", 1260)]
    # A fifth retry would be queued before this event, so it would show up in calls first.
    rig.events.append("info", "job", "fine")
    rig.clock.advance(10_000)
    n.tick()
    until(lambda: (rig.calls[-1][1] == "fine"))
    assert [title for _, title, _ in rig.calls] == ["boom"] * 4 + ["fine"]


def test_failures_never_become_events(rig):
    rig.channels(channel("a"))
    n = rig.notifier()
    rig.events.append("error", "job", "boom")
    n.tick()
    until(lambda: n.results.get("a") == "Failed: down 1")
    rig.clock.advance(60)
    n.tick()
    until(lambda: n.results.get("a") == "Failed: down 2")
    assert [e.title for e, _ in rig.events.page()] == ["boom"]


def test_slow_channel_does_not_delay_another(rig):
    rig.channels(channel("slow"), channel("fast"))
    rig.block["slow"] = threading.Event()
    n = rig.notifier()
    for title in ("one", "two"):
        rig.events.append("info", "job", title)
    n.tick()
    assert rig.got(2) == [("fast", "one"), ("fast", "two")]
    assert "slow" not in n.results
    rig.block["slow"].set()
    until(lambda: n.results.get("slow", "").startswith("Delivered"))


def test_results_record_last_outcome_without_secrets(rig, caplog):
    rig.channels(channel("a"), channel("b"))
    outcomes = {"one": SendError("HTTP 500"), "two": RuntimeError(f"surprise with {SECRET}"), "three": None}

    def send(ch, event, _cfg):
        if ch["id"] == "b":
            return
        if outcomes[event.title]:
            raise outcomes[event.title]

    n = rig.notifier(send)
    expected_time = NOW.astimezone().strftime("%H:%M")
    with caplog.at_level(logging.DEBUG):
        rig.events.append("info", "job", "one")
        n.tick()
        until(lambda: n.results.get("a") == "Failed: HTTP 500")
        assert n.results["b"] == f"Delivered {expected_time}"
        rig.events.append("info", "job", "two")
        n.tick()
        until(lambda: n.results.get("a") == "Failed: unexpected error")
        rig.events.append("info", "job", "three")
        n.tick()
        until(lambda: n.results.get("a") == f"Delivered {expected_time}")
    assert SECRET not in json.dumps(n.results)
    assert SECRET not in caplog.text


def test_run_survives_a_failing_tick(rig):
    rig.channels(channel("a"))
    real_after, failed = rig.events.after, []

    def flaky(last_id):
        if not failed:
            failed.append(1)
            raise OSError("disk hiccup")
        return real_after(last_id)

    rig.events.after = flaky
    n = rig.notifier()
    rig.events.append("info", "job", "one")
    stop = threading.Event()
    thread = threading.Thread(target=n.run, args=(stop, 0.01), daemon=True)
    thread.start()
    try:
        assert rig.got() == [("a", "one")]
    finally:
        stop.set()
        thread.join(1)
    assert failed


def test_emptied_event_log_restarts_the_cursor(rig, caplog):
    rig.channels(channel("a"))
    n = rig.notifier()
    for title in ("e1", "e2", "e3"):
        rig.events.append("info", "job", title)
    n.tick()
    assert rig.got(3) == [("a", "e1"), ("a", "e2"), ("a", "e3")]
    rig.events.events_path.unlink()
    rig.events.append("info", "job", "again")  # id 1 again
    with caplog.at_level(logging.WARNING):
        n.tick()
    assert rig.got() == [("a", "again")]
    assert "cursor" in caplog.text
    assert json.loads((rig.dir / "notify-cursor.json").read_text()) == {"last_id": 1}


def test_silence_added_after_a_failure_stops_its_retry(rig):
    rig.channels(channel("a"))
    n = rig.notifier()
    rig.events.append("error", "job", "boom")
    n.tick()
    assert rig.got() == [("a", "boom")]
    until(lambda: n.results.get("a") == "Failed: down 1")
    rig.channels(channel("a"), silences=[notifications.new_silence("title", "boom", 1, NOW)])
    rig.clock.advance(60)
    rig.events.append("info", "job", "fine")
    n.tick()
    # The retry would be queued before this event, so it would arrive first.
    assert rig.got() == [("a", "fine")]
    assert rig.sent.empty()


def test_disabled_channel_stops_without_its_backlog(rig):
    rig.channels(channel("doomed"))
    rig.block["doomed"] = threading.Event()
    n = rig.notifier()
    for title in ("one", "two", "three"):
        rig.events.append("info", "job", title)
    n.tick()
    assert rig.entered.wait(1)
    rig.channels(channel("doomed", enabled=False))
    n.tick()
    rig.block["doomed"].set()
    until(lambda: "notify-doomed" not in {t.name for t in threading.enumerate()})
    assert [title for _, title, _ in rig.calls] == ["one"]


def test_a_standing_load_error_is_logged_once(rig, caplog):
    path = rig.dir / "notifications.json"
    n = rig.notifier()
    with caplog.at_level(logging.ERROR):

        def logged():
            return len([r for r in caplog.records if "notifications.json" in r.getMessage()])

        path.write_text("{not json")
        for _ in range(3):
            n.tick()
        assert logged() == 1
        path.write_text("[]")
        n.tick()
        assert logged() == 2
        rig.channels(channel("a"))
        n.tick()
        path.write_text("{not json")
        n.tick()
        assert logged() == 3


def test_close_waits_for_the_backlog(rig):
    # The cursor already passed these events, so a shutdown must not lose them.
    rig.channels(channel("busy"))
    rig.block["busy"] = threading.Event()
    n = rig.notifier()
    for title in ("one", "two", "three"):
        rig.events.append("info", "job", title)
    n.tick()
    assert rig.entered.wait(1)
    threading.Timer(0.3, rig.block["busy"].set).start()
    n.close()
    assert [title for _, title, _ in rig.calls] == ["one", "two", "three"]


def test_close_gives_up_on_a_send_that_never_returns(rig, caplog):
    rig.channels(channel("stuck"))
    rig.block["stuck"] = threading.Event()
    n = rig.notifier()
    rig.events.append("info", "job", "one")
    n.tick()
    assert rig.entered.wait(1)
    start = time.monotonic()
    with caplog.at_level(logging.WARNING):
        n.close(timeout=0.5)
    assert time.monotonic() - start < 1.5
    assert any("1 " in r.getMessage() and "still sending" in r.getMessage() for r in caplog.records)


def test_silence_reaches_a_message_already_queued(rig):
    rig.channels(channel("a"))
    rig.block["a"] = threading.Event()
    n = rig.notifier()
    rig.events.append("info", "job", "one")
    n.tick()
    assert rig.entered.wait(1)
    rig.events.append("info", "job", "two")
    n.tick()
    rig.channels(channel("a"), silences=[notifications.new_silence("kind", "job", 1, NOW)])
    n.tick()
    rig.block["a"].set()
    n.close()
    assert [title for _, title, _ in rig.calls] == ["one"]
    assert "a" in n.results and not n.results["a"].startswith("Failed")
