# SPDX-License-Identifier: AGPL-3.0-only
import re
from dataclasses import dataclass
from datetime import UTC, datetime
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
    orientation_requested: str  # "auto" (CUPS turns each page to fit), "3" portrait, "4" landscape
    sides: str
    copies: int = 1
    page_ranges: str | None = None
    print_scaling: str = "fit"


@dataclass
class JobInfo:
    id: int
    name: str
    user: str
    host: str
    state: str
    reasons: list[str]
    created: str | None
    completed: str | None
    sheets: int | None


# Readable printer and job state reasons. Bookkeeping that only repeats a state is not shown.
ROUTINE_REASONS = {
    "none", "job-incoming", "job-queued", "job-printing", "job-completed-successfully",
    "processing-to-stop-point", "cups-waiting-for-job-completed",
}
REASON_LABELS = {
    "marker-supply-low": "Ink low",
    "marker-supply-empty": "Out of ink",
    "toner-low": "Toner low",
    "toner-empty": "Out of toner",
    "media-low": "Paper low",
    "media-empty": "Out of paper",
    "media-needed": "Load paper",
    "media-jam": "Paper jam",
    "door-open": "Cover open",
    "cover-open": "Cover open",
    "input-tray-missing": "Paper tray missing",
    "output-area-full": "Output tray full",
    "offline": "Offline",
    "connecting-to-device": "Connecting",
    "paused": "Paused",
    "printer-stopped": "Printer stopped",
}


def reason_label(reason: str) -> tuple[str, Literal["warning", "error"]] | None:
    """("Paper jam", "error") for "media-jam-error"; None for routine reasons."""
    if reason in ROUTINE_REASONS:
        return None
    key = re.sub(r"-(report|warning|error)$", "", reason)
    label = REASON_LABELS.get(key) or key[:1].upper() + key[1:].replace("-", " ")
    return label, "error" if reason.endswith("-error") else "warning"


def problems(reasons: list[str]) -> dict[str, Literal["warning", "error"]]:
    """Readable problems, once per label at its worst level: "-warning" and "-report" of one reason are one problem."""
    out: dict[str, Literal["warning", "error"]] = {}
    for reason in reasons:
        if found := reason_label(reason):
            label, level = found
            out[label] = "error" if "error" in (level, out.get(label)) else level
    return out


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
        if key == "orientation-requested" and value == "auto":
            continue
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
    # CUPS (pdftopdf) turns any page whose shape does not match the sheet, which makes an explicit orientation
    # print the same sheet as the other. Auto leaves that turning on; Portrait and Landscape switch it off.
    if opts.orientation_requested == "auto":
        options.pop("orientation-requested", None)
    elif "orientation-requested" in options:
        options["pdfAutoRotate"] = "false"
    options["copies"] = str(opts.copies)
    if opts.page_ranges is not None:
        options["page-ranges"] = opts.page_ranges
    return conn.printFile(queue, str(pdf), title, options)


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


def _iso(seconds) -> str | None:
    return datetime.fromtimestamp(seconds, UTC).isoformat() if seconds else None


def queue_jobs(conn: cups.Connection, queue: str) -> list[JobInfo]:
    """Every job CUPS still lists for the queue (active and finished), newest first."""
    jobs = conn.getJobs(
        which_jobs="all",
        requested_attributes=[
            "job-id", "job-name", "job-originating-user-name", "job-originating-host-name", "job-state",
            "job-state-reasons", "time-at-creation", "time-at-completed", "job-media-sheets-completed",
            "job-printer-uri",
        ],
    )
    return [
        JobInfo(
            id=job_id,
            name=a.get("job-name", ""),
            user=a.get("job-originating-user-name", ""),
            host=a.get("job-originating-host-name", ""),
            state=JOB_STATES[a["job-state"]],
            reasons=[r for r in _list(a.get("job-state-reasons", [])) if r != "none"],
            created=_iso(a.get("time-at-creation")),
            completed=_iso(a.get("time-at-completed")),
            sheets=a.get("job-media-sheets-completed"),
        )
        for job_id, a in sorted(jobs.items(), reverse=True)
        if a.get("job-printer-uri", "").rsplit("/", 1)[-1] == queue
    ]
