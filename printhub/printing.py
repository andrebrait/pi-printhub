# SPDX-License-Identifier: AGPL-3.0-only
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import cups

PRINTER_STATES = {3: "idle", 4: "processing", 5: "stopped"}
JOB_STATES = {
    3: "pending",
    4: "held",
    5: "processing",
    6: "stopped",
    7: "canceled",
    8: "aborted",
    9: "completed",
}
OPTION_KEYS = (
    "media",
    "print-quality",
    "print-color-mode",
    "orientation-requested",
    "sides",
    "print-scaling",
)
# CUPS filters implement scaling whatever the printer advertises
PRINT_SCALING = ["auto", "auto-fit", "fill", "fit", "none"]
MARGIN_SIDES = ("top", "bottom", "left", "right")
PAGE_RANGES = re.compile(r"\d+(-\d+)?(,\d+(-\d+)?)*")


@dataclass
class PrinterInfo:
    state: Literal["idle", "processing", "stopped"]
    state_reasons: list[str]
    accepting: bool
    supported: dict[str, list[str]]
    defaults: dict[str, str]
    # hundredths of a millimetre; the smallest the printer allows on each side
    margins: dict[str, int]


@dataclass
class PrintOptions:
    media: str
    print_quality: str  # IPP enum as a string: "3" draft, "4" normal, "5" high
    print_color_mode: str
    orientation_requested: str  # "3" portrait, "4" landscape
    sides: str
    copies: int = 1
    page_ranges: str | None = None
    print_scaling: str = "fit"


@dataclass
class JobStatus:
    state: Literal[
        "pending", "held", "processing", "stopped", "canceled", "aborted", "completed"
    ]
    reasons: list[str] = field(default_factory=list)


def _allowed(key: str, info: PrinterInfo) -> list[str]:
    """Values accepted for an option; empty means the printer does not offer it."""
    return PRINT_SCALING if key == "print-scaling" else info.supported[key]


def connect() -> cups.Connection:
    return cups.Connection()


def _list(value) -> list:
    return value if isinstance(value, list) else [value]


def printer_info(conn: cups.Connection, queue: str) -> PrinterInfo:
    attrs = conn.getPrinterAttributes(
        queue,
        requested_attributes=[
            "printer-state",
            "printer-state-reasons",
            "printer-is-accepting-jobs",
            *(f"{k}-supported" for k in OPTION_KEYS),
            *(f"{k}-default" for k in OPTION_KEYS),
            *(f"media-{s}-margin-supported" for s in MARGIN_SIDES),
        ],
    )
    margins = {
        s: min(attrs[f"media-{s}-margin-supported"])
        for s in MARGIN_SIDES
        if attrs.get(f"media-{s}-margin-supported")
    }
    return PrinterInfo(
        state=PRINTER_STATES[attrs["printer-state"]],
        state_reasons=[r for r in _list(attrs.get("printer-state-reasons", [])) if r != "none"],
        accepting=attrs["printer-is-accepting-jobs"],
        supported={
            k: [str(v) for v in _list(attrs.get(f"{k}-supported", []))] for k in OPTION_KEYS
        },
        # orientation-requested-default is a no-value attribute (None) on some printers
        defaults={
            k: str(attrs[f"{k}-default"])
            for k in OPTION_KEYS
            if attrs.get(f"{k}-default") is not None
        },
        margins=margins,
    )


def validate(opts: PrintOptions, info: PrinterInfo) -> None:
    for key in OPTION_KEYS:
        value = getattr(opts, key.replace("-", "_"))
        allowed = _allowed(key, info)
        if allowed and value not in allowed:
            raise ValueError(f"{key.replace('-', '_')} {value!r} is not supported by the printer")
    if not 1 <= opts.copies <= 99:
        raise ValueError(f"copies must be 1 to 99, got {opts.copies}")
    if opts.page_ranges is not None and not PAGE_RANGES.fullmatch(opts.page_ranges):
        raise ValueError(f"page_ranges {opts.page_ranges!r} is not a valid range list")


def submit(
    conn: cups.Connection,
    queue: str,
    pdf: Path,
    title: str,
    opts: PrintOptions,
    info: PrinterInfo,
) -> int:
    # options the printer does not offer are left out rather than sent blind
    options = {
        key: getattr(opts, key.replace("-", "_")) for key in OPTION_KEYS if _allowed(key, info)
    }
    options["copies"] = str(opts.copies)
    if opts.page_ranges is not None:
        options["page-ranges"] = opts.page_ranges
    return conn.printFile(queue, str(pdf), title, options)


def job_status(conn: cups.Connection, job_id: int) -> JobStatus:
    attrs = conn.getJobAttributes(
        job_id, requested_attributes=["job-state", "job-state-reasons"]
    )
    reasons = [r for r in _list(attrs.get("job-state-reasons", [])) if r != "none"]
    return JobStatus(state=JOB_STATES[attrs["job-state"]], reasons=reasons)


def cancel(conn: cups.Connection, job_id: int) -> None:
    conn.cancelJob(job_id)


def active_job_ids(conn: cups.Connection, queue: str) -> set[int]:
    # "not-completed" is pending, held, processing and stopped jobs of every queue
    jobs = conn.getJobs(
        which_jobs="not-completed", requested_attributes=["job-id", "job-printer-uri"]
    )
    return {
        job_id
        for job_id, attrs in jobs.items()
        if attrs["job-printer-uri"].rsplit("/", 1)[-1] == queue
    }
