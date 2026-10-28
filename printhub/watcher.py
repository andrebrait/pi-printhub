# SPDX-License-Identifier: AGPL-3.0-only
"""Turns changes in CUPS jobs and printer state into notification events."""
import logging
import threading

import cups

from . import printing
from .events import EventLog

log = logging.getLogger(__name__)

# Job states worth a notification, with the event level; queued and printing show live on the Jobs page.
JOB_EVENTS = {"completed": "info", "canceled": "info", "aborted": "error", "held": "warning", "stopped": "warning"}


class Watcher:
    def __init__(self, conn_factory, queue: str, events: EventLog):
        self.conn_factory = conn_factory
        self.queue = queue
        self.events = events
        # None until CUPS first answers: that tick is a baseline, so restarts do not replay history.
        self.jobs: dict[int, str] | None = None
        self.reasons: set[str] | None = None
        self.down = False

    def tick(self) -> None:
        try:
            conn = self.conn_factory()
            jobs = printing.queue_jobs(conn, self.queue)
            reasons = set(printing.printer_info(conn, self.queue).state_reasons)
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
        if self.reasons is not None:
            for reason in sorted(reasons - self.reasons, key=_severity):
                if label := printing.reason_label(reason):
                    self.events.append(label[1], "printer", label[0])
            for reason in sorted(self.reasons - reasons):
                if label := printing.reason_label(reason):
                    self.events.append("info", "printer", f"{label[0]} cleared")
        self.jobs = {job.id: job.state for job in jobs}
        self.reasons = reasons

    def run(self, stop: threading.Event, interval: float = 3.0) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("watcher tick failed")
            stop.wait(interval)


def _severity(reason: str) -> int:
    # Warnings before errors, so the most severe problem is the newest notification.
    return 1 if reason.endswith("-error") else 0


def _describe(job: printing.JobInfo) -> str:
    sender = "@".join(part for part in (job.user, job.host) if part)
    return " from ".join(part for part in (job.name, sender) if part)
