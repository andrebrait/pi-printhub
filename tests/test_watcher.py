# SPDX-License-Identifier: AGPL-3.0-only
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import cups
import pytest
from test_app import FakeCups

from printhub import printing
from printhub.events import EventLog
from printhub.settings import write_json
from printhub.watcher import Watcher

OURS = "ipp://localhost/printers/OfficeJet"


def job(state: int, *, uri=OURS, name="report.pdf", user="user", host="phone.lan", **extra) -> dict:
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
    assert (done.name, done.user, done.host, done.state, done.sheets) == ("report.pdf", "user", "phone.lan", "completed", 2)
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
    assert "report.pdf" in event.detail and "user@phone.lan" in event.detail


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


def test_a_brief_cups_restart_is_quiet(cups_, log):
    # cupsctl and the installer restart cupsd; for a second or two CUPS refuses connections.
    calls, clock = {"n": 0}, Clock()

    def factory():
        calls["n"] += 1
        if 1 < calls["n"] <= 4:
            raise cups.IPPError(1280, "Connection reset by peer")
        return cups_

    w = Watcher(factory, "OfficeJet", log, clock=clock)
    cups_.jobs = {1: job(5)}
    w.tick()
    for _ in range(3):
        clock.now += 3
        w.tick()
    cups_.jobs = {1: job(5), 2: job(9)}
    clock.now += 3
    w.tick()
    # Job 2 finished while CUPS was unreachable, which is still news.
    assert events(log) == [("info", "job", "Job 2 completed", 2)]


def test_cups_down_for_30_seconds_is_one_event_each(cups_, log):
    up, clock = {"ok": True}, Clock()

    def factory():
        if not up["ok"]:
            raise RuntimeError("cupsd is not running")
        return cups_

    w = Watcher(factory, "OfficeJet", log, clock=clock)
    w.tick()
    up["ok"] = False
    for _ in range(9):  # 27 s of failed ticks: still quiet
        clock.now += 3
        w.tick()
    assert events(log) == []
    for _ in range(5):
        clock.now += 3
        w.tick()
    assert events(log) == [("error", "printer", "Printer status unavailable", None)]
    up["ok"] = True
    clock.now += 3
    w.tick()
    assert [t for _, _, t, _ in events(log)] == ["Printer status unavailable", "Printer status available again"]


def test_cups_down_at_start_then_up_is_a_baseline(cups_, log):
    up, clock = {"ok": False}, Clock()

    def factory():
        if not up["ok"]:
            raise RuntimeError("cupsd is not running")
        return cups_

    w = Watcher(factory, "OfficeJet", log, clock=clock)
    w.tick()
    clock.now += 31
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


USB = "ipp://localhost:60000/ipp/print"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def usb(cups_, log):
    """A watcher on an IPP-over-USB queue; answers["up"] switches the fake probe, clock.now moves time."""
    cups_.device_uri = USB
    answers, clock, probed = {"up": True}, Clock(), []

    def probe(uri):
        probed.append(uri)
        return answers["up"]

    watcher = Watcher(lambda: cups_, "OfficeJet", log, usb_probe=probe, clock=clock)
    return watcher, answers, clock, probed


def advance(watcher, clock, seconds, step=3):
    for _ in range(0, seconds, step):
        clock.now += step
        watcher.tick()


def test_short_outage_is_quiet(usb, log):
    watcher, answers, clock, _ = usb
    watcher.tick()
    answers["up"] = False
    advance(watcher, clock, 30)
    answers["up"] = True
    advance(watcher, clock, 30)
    assert events(log) == []


def test_two_minutes_unanswered_is_one_error_then_recovery(usb, log):
    watcher, answers, clock, _ = usb
    watcher.tick()
    answers["up"] = False
    advance(watcher, clock, 300)
    assert events(log) == [("error", "printer", "Printer not answering over USB", None)]
    assert log.page()[0][0].detail == "Power-cycle the printer if this lasts."
    answers["up"] = True
    advance(watcher, clock, 30)
    assert events(log)[1:] == [("info", "printer", "Printer answering over USB again", None)]
    assert len(events(log)) == 2


def test_a_dead_printer_is_probed_at_most_every_15_seconds(usb):
    watcher, answers, clock, probed = usb
    answers["up"] = False
    advance(watcher, clock, 60)
    assert len(probed) == 4
    assert set(probed) == {USB}


def test_hplip_queue_is_not_probed(cups_, log):
    probed = []
    clock = Clock()
    watcher = Watcher(lambda: cups_, "OfficeJet", log, usb_probe=lambda uri: probed.append(uri), clock=clock)
    advance(watcher, clock, 300)
    assert probed == [] and events(log) == []


def test_failed_sharing_request_is_reported_once_in_plain_words(cups_, log, tmp_path):
    watcher = Watcher(lambda: cups_, "OfficeJet", log, data_dir=tmp_path)
    status = tmp_path / "system-status.json"
    watcher.tick()
    assert events(log) == []
    write_json(status, {"state": "failed", "request_at": "t1", "error": "Job for printhub-printer.service failed\nsee journal"})
    watcher.tick()
    watcher.tick()
    write_json(status, {"state": "failed", "request_at": "t2", "error": "x" * 300 + "\nmore"})
    watcher.tick()
    write_json(status, {"state": "applied", "request_at": "t3"})
    watcher.tick()
    rows = [e for e, _ in reversed(log.page())]
    assert [(e.level, e.kind, e.title, e.detail) for e in rows] == [
        ("warning", "settings", "Sharing settings not applied", "The printer is not answering"),
        ("warning", "settings", "Sharing settings not applied", "x" * 200),
    ]


def test_a_restart_does_not_reannounce_a_failure_already_on_disk(cups_, log, tmp_path):
    write_json(tmp_path / "system-status.json", {"state": "failed", "request_at": "t1", "error": "boom"})
    watcher = Watcher(lambda: cups_, "OfficeJet", log, data_dir=tmp_path)
    watcher.tick()
    watcher.tick()
    assert events(log) == []
    write_json(tmp_path / "system-status.json", {"state": "failed", "request_at": "t2", "error": "boom"})
    watcher.tick()
    assert [(e.title, e.detail) for e, _ in log.page()] == [("Sharing settings not applied", "boom")]


def test_a_queue_without_a_device_uri_is_not_probed(cups_, log):
    class NoUri(type(cups_)):
        def getPrinterAttributes(self, *args, **kwargs):
            return {k: v for k, v in super().getPrinterAttributes(*args, **kwargs).items() if k != "device-uri"}

    probed, clock = [], Clock()
    watcher = Watcher(lambda: NoUri(), "OfficeJet", log, usb_probe=lambda uri: probed.append(uri), clock=clock)
    advance(watcher, clock, 60)
    assert probed == [] and events(log) == []


def test_the_120_second_threshold_is_measured_on_the_clock(usb, log):
    watcher, answers, clock, _ = usb
    watcher.tick()
    answers["up"] = False
    advance(watcher, clock, 120)  # 105 s of confirmed silence
    assert events(log) == []
    advance(watcher, clock, 15)  # 120 s
    assert [t for _, _, t, _ in events(log)] == ["Printer not answering over USB"]


def test_usb_answering_follows_the_silence_threshold(usb):
    watcher, answers, clock, _ = usb
    assert watcher.usb_answering is None  # nothing probed yet
    watcher.tick()
    assert watcher.usb_answering is True
    answers["up"] = False
    advance(watcher, clock, 120)  # 105 s of confirmed silence
    assert watcher.usb_answering is True  # silent, but not for 120 s yet
    advance(watcher, clock, 15)
    assert watcher.usb_answering is False
    answers["up"] = True
    advance(watcher, clock, 15)
    assert watcher.usb_answering is True


def test_a_queue_that_stops_being_ipp_usb_forgets_the_usb_state(usb, cups_, log):
    watcher, answers, clock, _ = usb
    watcher.tick()
    answers["up"] = False
    advance(watcher, clock, 135)
    assert watcher.usb_answering is False
    assert len(events(log)) == 1
    cups_.device_uri = "hp:/usb/OfficeJet?serial=1"  # the queue now uses HPLIP
    advance(watcher, clock, 3)
    assert watcher.usb_answering is None
    cups_.device_uri = USB
    advance(watcher, clock, 105)  # silent again; the earlier silence does not count towards the 120 s
    assert watcher.usb_answering is True
    assert len(events(log)) == 1


def test_usb_answering_is_unknown_for_a_queue_that_is_not_probed(cups_, log):
    clock = Clock()
    watcher = Watcher(lambda: cups_, "OfficeJet", log, usb_probe=lambda uri: False, clock=clock)
    advance(watcher, clock, 300)
    assert watcher.usb_answering is None


class _IppHandler(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _IppHandler.seen.append((self.path, self.headers["Content-Type"], body))
        reply = b"\x02\x00\x00\x00\x00\x00\x00\x01\x03"
        self.send_response(200)
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


def test_usb_answers_against_a_fake_ipp_server():
    server = HTTPServer(("127.0.0.1", 0), _IppHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port
    try:
        assert printing.usb_answers(f"ipp://127.0.0.1:{port}/ipp/print", timeout=2)
    finally:
        server.shutdown()
        server.server_close()
    path, content_type, body = _IppHandler.seen[0]
    assert (path, content_type) == ("/ipp/print", "application/ipp")
    assert body[:4] == b"\x02\x00\x00\x0b" and f"ipp://127.0.0.1:{port}/ipp/print".encode() in body
    assert b"requested-attributes" in body and b"printer-state" in body  # keeps the reply small
    assert not printing.usb_answers(f"ipp://127.0.0.1:{port}/ipp/print", timeout=2)  # closed now


def test_usb_answers_rejects_an_ipp_error_and_a_non_200():
    class Bad(_IppHandler):
        reply, code = b"\x02\x00\x04\x00\x00\x00\x00\x01\x03", 200

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(self.code)
            self.send_header("Content-Length", str(len(self.reply)))
            self.end_headers()
            self.wfile.write(self.reply)

    server = HTTPServer(("127.0.0.1", 0), Bad)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    uri = f"ipp://127.0.0.1:{server.server_port}/ipp/print"
    try:
        assert not printing.usb_answers(uri, timeout=2)
        Bad.reply, Bad.code = b"\x02\x00\x00\x00\x00\x00\x00\x01\x03", 500
        assert not printing.usb_answers(uri, timeout=2)
    finally:
        server.shutdown()
        server.server_close()
