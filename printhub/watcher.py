# SPDX-License-Identifier: AGPL-3.0-only
"""Turns changes in CUPS jobs and printer state into notification events."""
import logging
import re
import threading
import time
from pathlib import Path

import cups

from . import printing, settings
from .events import EventLog

log = logging.getLogger(__name__)

# Job states worth a notification, with the event level; queued and printing show live on the Jobs page.
JOB_EVENTS = {"completed": "info", "canceled": "info", "aborted": "error", "held": "warning", "stopped": "warning"}

# ipp-usb serves the printer on a local port; HPLIP and network queues have other URIs and are not probed.
USB_URI = re.compile(r"ipp://localhost:6\d{4}/")
USB_PROBE_EVERY = 15.0
USB_SILENT_FOR = 120.0


class Watcher:
    def __init__(
        self, conn_factory, queue: str, events: EventLog, *,
        data_dir: Path | None = None, usb_probe=printing.usb_answers, clock=time.monotonic,
    ):
        self.conn_factory = conn_factory
        self.queue = queue
        self.events = events
        self.data_dir = data_dir
        self.usb_probe = usb_probe
        self.clock = clock
        # None until CUPS first answers: that tick is a baseline, so restarts do not replay history.
        self.jobs: dict[int, str] | None = None
        self.problems: dict[str, str] | None = None
        self.down = False
        self.usb_probed_at: float | None = None
        self.usb_silent_since: float | None = None
        self.usb_reported = False
        # None until the first tick: a failure already on disk then is a baseline, so restarts do not re-announce it.
        self.sharing_reported: set[str] | None = None

    def tick(self) -> None:
        self._check_sharing()
        try:
            conn = self.conn_factory()
            jobs = printing.queue_jobs(conn, self.queue)
            info = printing.printer_info(conn, self.queue)
            problems = printing.problems(info.state_reasons)
        except (cups.IPPError, RuntimeError, OSError) as e:
            if not self.down:
                self.down = True
                self.events.append("error", "printer", "Printer status unavailable", str(e))
            return
        if self.down:
            self.down = False
            self.events.append("info", "printer", "Printer status available again")

        if self.jobs is not None:
            for job in reversed(jobs):  # oldest first, so event order follows job order
                level = JOB_EVENTS.get(job.state)
                if level and self.jobs.get(job.id) != job.state:
                    self.events.append(level, "job", f"Job {job.id} {job.state}", _describe(job), job_id=job.id)
        if self.problems is not None:
            # New problems and changed levels; warnings first, so the worst is the newest notification.
            for label, level in sorted(problems.items(), key=lambda p: p[1] == "error"):
                if self.problems.get(label) != level:
                    self.events.append(level, "printer", label)
            for label in sorted(self.problems.keys() - problems.keys()):
                self.events.append("info", "printer", f"{label} cleared")
        self.jobs = {job.id: job.state for job in jobs}
        self.problems = problems
        self._check_usb(info.device_uri)

    def _check_usb(self, device_uri: str) -> None:
        if not USB_URI.match(device_uri):
            return
        now = self.clock()
        if self.usb_probed_at is not None and now - self.usb_probed_at < USB_PROBE_EVERY:
            return
        self.usb_probed_at = now
        if self.usb_probe(device_uri):
            if self.usb_reported:
                self.events.append("info", "printer", "Printer answering over USB again")
            self.usb_silent_since, self.usb_reported = None, False
            return
        if self.usb_silent_since is None:
            self.usb_silent_since = now
        if not self.usb_reported and now - self.usb_silent_since >= USB_SILENT_FOR:
            self.usb_reported = True
            self.events.append("error", "printer", "Printer not answering over USB", "Power-cycle the printer if this lasts.")

    def _check_sharing(self) -> None:
        if self.data_dir is None:
            return
        status = settings.read_system(self.data_dir)["status"] or {}
        failed = status.get("state") == "failed"
        at = str(status.get("request_at"))
        baseline = self.sharing_reported is None
        if baseline:
            self.sharing_reported = set()
        if not failed or at in self.sharing_reported:
            return
        self.sharing_reported.add(at)
        if baseline:
            return
        error = str(status.get("error") or "")
        reason = "The printer is not answering" if "printhub-printer.service" in error else error.split("\n")[0][:200]
        self.events.append("warning", "settings", "Sharing settings not applied", reason)

    def run(self, stop: threading.Event, interval: float = 3.0) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("watcher tick failed")
            stop.wait(interval)


def _describe(job: printing.JobInfo) -> str:
    sender = "@".join(part for part in (job.user, job.host) if part)
    return " from ".join(part for part in (job.name, sender) if part)
