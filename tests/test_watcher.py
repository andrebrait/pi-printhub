# SPDX-License-Identifier: AGPL-3.0-only
import threading

import pytest
from test_app import FakeCups

from printhub import printing
from printhub.events import EventLog
from printhub.watcher import Watcher

OURS = "ipp://localhost/printers/OfficeJet"


def job(state: int, *, uri=OURS, name="report.pdf", user="andre", host="phone.lan", **extra) -> dict:
    return {
        "job-printer-uri": uri, "job-state": state, "job-name": name, "job-originating-user-name": user,
        "job-originating-host-name": host, "time-at-creation": 1793198008, **extra,
    }


@pytest.fixture
def cups_():
    return FakeCups()


@pytest.fixture
def log(tmp_path):
    return EventLog(tmp_path)


@pytest.fixture
def watcher(cups_, log):
    return Watcher(lambda: cups_, "OfficeJet", log)


def events(log):
    return [(e.level, e.kind, e.title, e.job_id) for e, _ in reversed(log.page())]


def test_queue_jobs_reads_our_queue_newest_first(cups_):
    cups_.jobs = {
        3: job(9, **{"time-at-completed": 1793198050, "job-media-sheets-completed": 2}),
        5: job(5),
        4: job(9, uri="ipp://localhost/printers/Other"),
    }
    jobs = printing.queue_jobs(cups_, "OfficeJet")
    assert [j.id for j in jobs] == [5, 3]
    done = jobs[1]
    assert (done.name, done.user, done.host, done.state, done.sheets) == ("report.pdf", "andre", "phone.lan", "completed", 2)
    assert done.created == "2026-10-28T14:33:28+00:00" and done.completed == "2026-10-28T14:34:10+00:00"
    assert jobs[0].completed is None


def test_queue_jobs_without_private_values(cups_):
    # CUPS hides job-name and the sender unless JobPrivateValues allows them.
    cups_.jobs = {7: {"job-printer-uri": OURS, "job-state": 9}}
    (only,) = printing.queue_jobs(cups_, "OfficeJet")
    assert (only.name, only.user, only.host, only.created, only.sheets) == ("", "", "", None, None)


def test_first_tick_is_a_baseline(cups_, watcher, log):
    cups_.jobs = {1: job(9), 2: job(5)}
    cups_.reasons = ["media-low-report"]
    watcher.tick()
    assert events(log) == []


@pytest.mark.parametrize(
    ("state", "level", "word"),
    [(9, "info", "completed"), (7, "info", "canceled"), (8, "error", "aborted"), (4, "warning", "held"), (6, "warning", "stopped")],
)
def test_job_transitions_become_events(cups_, watcher, log, state, level, word):
    cups_.jobs = {7: job(5)}
    watcher.tick()
    cups_.jobs = {7: job(state)}
    watcher.tick()
    watcher.tick()  # no change, no second event
    assert events(log) == [(level, "job", f"Job 7 {word}", 7)]
    (event, _), = log.page()
    assert "report.pdf" in event.detail and "andre@phone.lan" in event.detail


def test_new_job_finishing_between_ticks_is_reported(cups_, watcher, log):
    watcher.tick()
    cups_.jobs = {8: job(9)}
    watcher.tick()
    assert events(log) == [("info", "job", "Job 8 completed", 8)]


def test_queued_and_printing_are_not_events(cups_, watcher, log):
    watcher.tick()
    cups_.jobs = {8: job(3)}
    watcher.tick()
    cups_.jobs = {8: job(5)}
    watcher.tick()
    assert events(log) == []


def test_vanished_job_is_not_an_event(cups_, watcher, log):
    cups_.jobs = {9: job(5)}
    watcher.tick()
    cups_.jobs = {}
    watcher.tick()
    assert events(log) == []


def test_printer_reasons_appear_and_clear(cups_, watcher, log):
    watcher.tick()
    cups_.reasons = ["media-jam-error", "marker-supply-low-warning", "cups-waiting-for-job-completed"]
    watcher.tick()
    cups_.reasons = ["marker-supply-low-warning"]
    watcher.tick()
    assert events(log) == [
        ("warning", "printer", "Ink low", None),
        ("error", "printer", "Paper jam", None),
        ("info", "printer", "Paper jam cleared", None),
    ]


def test_cups_down_and_back_is_one_event_each(cups_, log):
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        if 1 < calls["n"] <= 4:
            raise RuntimeError("cupsd is not running")
        return cups_

    w = Watcher(factory, "OfficeJet", log)
    cups_.jobs = {1: job(5)}
    w.tick()
    for _ in range(3):
        w.tick()
    cups_.jobs = {1: job(5), 2: job(9)}
    w.tick()
    # Job 1 is unchanged; job 2 finished while CUPS was unreachable, which is still news.
    assert events(log) == [
        ("error", "printer", "Printer status unavailable", None),
        ("info", "printer", "Printer status available again", None),
        ("info", "job", "Job 2 completed", 2),
    ]


def test_cups_down_at_start_then_up_is_a_baseline(cups_, log):
    up = {"ok": False}

    def factory():
        if not up["ok"]:
            raise RuntimeError("cupsd is not running")
        return cups_

    w = Watcher(factory, "OfficeJet", log)
    w.tick()
    up["ok"] = True
    cups_.jobs = {1: job(9)}
    w.tick()
    assert [t for _, _, t, _ in events(log)] == ["Printer status unavailable", "Printer status available again"]


def test_other_queues_jobs_are_ignored(cups_, watcher, log):
    watcher.tick()
    cups_.jobs = {4: job(9, uri="ipp://localhost/printers/Other")}
    watcher.tick()
    assert events(log) == []


def test_run_survives_a_failing_tick(log):
    stop, calls = threading.Event(), []

    class Flaky(Watcher):
        def tick(self):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("boom")
            if len(calls) == 3:
                stop.set()

    Flaky(lambda: None, "OfficeJet", log).run(stop, interval=0)
    assert len(calls) == 3


def test_reasons_with_the_same_label_are_one_problem(cups_, watcher, log):
    watcher.tick()
    cups_.reasons = ["marker-supply-low-warning", "marker-supply-low-report"]
    watcher.tick()
    cups_.reasons = ["marker-supply-low-report"]  # same problem, other severity suffix
    watcher.tick()
    assert events(log) == [("warning", "printer", "Ink low", None)]


def test_problem_level_change_is_reported_as_the_new_level(cups_, watcher, log):
    watcher.tick()
    cups_.reasons = ["media-low-warning"]
    watcher.tick()
    cups_.reasons = ["media-low-error"]
    watcher.tick()
    assert events(log) == [("warning", "printer", "Paper low", None), ("error", "printer", "Paper low", None)]
