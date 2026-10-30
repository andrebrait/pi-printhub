# SPDX-License-Identifier: AGPL-3.0-only
import ipaddress
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import chain, repeat
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

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
class Supply:
    name: str  # "Magenta ink"
    type: str  # marker-type: "ink-cartridge", "toner", "opc", "waste-toner"
    color: str  # "#RRGGBB", or "" when the printer reports none
    level: int | None  # percent of the full level; None when the printer does not know
    low: bool


@dataclass
class PrinterInfo:
    state: Literal["idle", "processing", "stopped"]
    state_reasons: list[str]
    accepting: bool
    supported: dict[str, list[str]]
    defaults: dict[str, str]
    # hundredths of a millimetre; the smallest the printer allows on each side
    margins: dict[str, int]
    # the CUPS device URI ("" when CUPS does not report one); not part of the API response
    device_uri: str = ""
    name: str = ""
    model: str = ""
    # a page about the printer a browser can open ("" when unknown)
    web_page: str = ""
    supplies: list[Supply] = field(default_factory=list)


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


def usb_answers(uri: str, timeout: float = 5.0) -> bool:
    """Whether the printer behind an ipp:// device URI answers an IPP Get-Printer-Attributes request."""

    def attr(tag: int, name: str, value: str) -> bytes:
        n, v = name.encode(), value.encode()
        return bytes([tag]) + len(n).to_bytes(2, "big") + n + len(v).to_bytes(2, "big") + v

    body = (
        b"\x02\x00\x00\x0b\x00\x00\x00\x01\x01"
        + attr(0x47, "attributes-charset", "utf-8")
        + attr(0x48, "attributes-natural-language", "en")
        + attr(0x45, "printer-uri", uri)
        + attr(0x44, "requested-attributes", "printer-state")
        + b"\x03"
    )
    request = urllib.request.Request(
        "http:" + uri.removeprefix("ipp:"), body, {"Content-Type": "application/ipp"}, method="POST"
    )
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as r:
            reply = r.read(4)
            return r.status == 200 and len(reply) == 4 and reply[0] in (1, 2) and reply[2] == 0
    except Exception:
        return False


def _list(value) -> list:
    return value if isinstance(value, list) else [value]


COLOR = re.compile(r"(#[0-9a-fA-F]{6})(#[0-9a-fA-F]{6})*")  # a multi-colour marker lists several; the first is shown
LOW_BELOW_PERCENT = 15  # when the printer gives no low level


def _marker(attrs: dict, key: str, default=None):
    """marker-<key> as a list. With a default the list goes on with it for ever, so a short or absent array covers every supply."""
    value = attrs.get(f"marker-{key}")
    items = [] if value is None else _list(value)
    return items if default is None else chain(items, repeat(default))


def _int(value, default: int) -> int:
    """`value` when the printer sent a whole number, else `default`: a bad value must not break the status."""
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def supplies(attrs: dict) -> list[Supply]:
    """The consumables in CUPS printer attributes. marker-names and -levels set how many; the other arrays are padded with their defaults.

    A negative level is unknown (IPP: -1 unavailable, -2 unknown, -3 some remaining)."""
    rows = zip(
        _marker(attrs, "names"), _marker(attrs, "levels"), _marker(attrs, "colors", ""), _marker(attrs, "types", ""),
        _marker(attrs, "low-levels", 0), _marker(attrs, "high-levels", 100),
    )
    out = []
    for name, level, color, kind, low_at, high in rows:
        level, low_at, high = _int(level, -1), _int(low_at, 0), _int(high, 100)
        kind = str(kind)
        percent = min(100, round(level * 100 / (high if high > 0 else 100))) if level >= 0 else None
        # A waste container's level says how full it is, so "low" means nothing for it.
        low = percent is not None and not kind.startswith("waste") and (level <= low_at if low_at > 0 else percent < LOW_BELOW_PERCENT)
        match = COLOR.fullmatch(str(color))
        name = str(name)
        out.append(Supply(name[:1].upper() + name[1:], kind, match[1] if match else "", percent, low))
    return out


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _web_page(attrs: dict) -> str:
    """printer-supply-info-uri when a browser on another device can open it: http(s), no credentials, not this machine.

    printer-more-info is not used: CUPS fills it with its own page on the Pi."""
    url = str(attrs.get("printer-supply-info-uri") or "")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".")
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not host or "@" in parts.netloc or _is_loopback(host):
        return ""
    return url


def printer_info(conn: cups.Connection, queue: str) -> PrinterInfo:
    attrs = conn.getPrinterAttributes(
        queue,
        requested_attributes=[
            "printer-state",
            "device-uri",
            "printer-state-reasons",
            "printer-is-accepting-jobs",
            "printer-info",
            "printer-make-and-model",
            "printer-supply-info-uri",
            *(f"marker-{k}" for k in ("names", "colors", "levels", "low-levels", "high-levels", "types")),
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
        device_uri=str(attrs.get("device-uri", "")),
        name=str(attrs.get("printer-info") or queue),
        model=str(attrs.get("printer-make-and-model", "")).split(",")[0].strip(),
        web_page=_web_page(attrs),
        supplies=supplies(attrs),
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
    # print the same sheet as the other. Auto leaves that turning on, as portrait so a queue default cannot turn
    # pages the other way; Portrait and Landscape switch it off.
    if "orientation-requested" in options:
        if opts.orientation_requested == "auto":
            options["orientation-requested"] = "3"
        else:
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
