# SPDX-License-Identifier: AGPL-3.0-only
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from printhub.documents import DocStore, Page

SCANIMAGE = "scanimage"
_PROBE_TIMEOUT = 60  # device discovery / option listing; a hung backend must not wedge the scanner

AREAS_MM = {"a4": (210.0, 297.0), "letter": (215.9, 279.4)}
_OPTION_RE = re.compile(
    r"^\s+(--source|--mode|--resolution|-x|-y)\s+(\S.*?)(?:\s+\(in steps.*?\))?(?:\s+\[[^\]]*\])?$"
)


class ScannerBusy(Exception):
    pass


class ScannerUnavailable(Exception):
    pass


@dataclass
class ScanSettings:
    source: Literal["flatbed", "adf"]
    resolution: int = 300
    mode: Literal["color", "gray"] = "color"
    area: Literal["a4", "letter", "full"] = "a4"


@dataclass
class ScannerCaps:
    device: str
    sources: list[str]
    resolutions: list[int]
    modes: list[str]
    bed_mm: tuple[float, float]


def _pick(values: list[str], *needles: str) -> str | None:
    return next((v for v in values if any(n in v.lower() for n in needles)), None)


def _max_mm(spec: str) -> float:
    m = re.search(r"\.\.([\d.]+)", spec)
    if not m:
        raise ScannerUnavailable("Scanner reports no scan area")
    return float(m.group(1))


def parse_options(text: str) -> tuple[dict[str, str], ScannerCaps]:
    """Parse `scanimage -A`: our names -> device values, and the capabilities."""
    opts = {m[1]: m[2] for line in text.splitlines() if (m := _OPTION_RE.match(line))}
    names: dict[str, str] = {}
    sources, modes = [], []
    values = opts.get("--source", "").split("|")
    for ours, needles in (("flatbed", ("flatbed",)), ("adf", ("adf", "feeder"))):
        if (v := _pick(values, *needles)) is not None:
            names[ours] = v
            sources.append(ours)
    values = opts.get("--mode", "").split("|")
    for ours, needles in (("color", ("color",)), ("gray", ("gray", "grey"))):
        if (v := _pick(values, *needles)) is not None:
            names[ours] = v
            modes.append(ours)
    res = opts.get("--resolution", "")
    if m := re.match(r"(\d+)\.\.(\d+)", res):
        lo, hi = int(m[1]), int(m[2])
        resolutions = [r for r in (150, 200, 300, 600) if lo <= r <= hi]
    else:
        resolutions = [int(n) for n in re.findall(r"\d+", res)]
    if "-x" not in opts or "-y" not in opts:
        raise ScannerUnavailable("Scanner reports no scan area")
    caps = ScannerCaps("", sources, resolutions, modes, (_max_mm(opts["-x"]), _max_mm(opts["-y"])))
    return names, caps


def _probe(*args: str) -> str:
    try:
        r = subprocess.run([SCANIMAGE, *args], capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ScannerUnavailable(str(e)) from e
    if r.returncode != 0:
        raise ScannerUnavailable(_last_error(r.stderr) or f"scanimage exited {r.returncode}")
    return r.stdout


def find_device() -> str | None:
    if env := os.environ.get("PRINTHUB_SCANNER"):
        return env
    out = _probe("-f", "%d%n")
    return next((line.strip() for line in out.splitlines() if line.strip()), None)


def _options(device: str) -> tuple[dict[str, str], ScannerCaps]:
    names, caps = parse_options(_probe("-d", device, "-A"))
    caps.device = device
    return names, caps


def capabilities(device: str) -> ScannerCaps:
    return _options(device)[1]


def _last_error(stderr: str) -> str:
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    # "Batch terminated, N pages scanned" follows the real error line in batch mode
    prefixed = [ln for ln in lines if ln.startswith("scanimage:")]
    return (prefixed or lines or [""])[-1]


def ingest(png: Path, dest_jpg: Path, resolution: int) -> None:
    """The only JPEG encode a scanned page ever gets."""
    with Image.open(png) as im:
        im.save(dest_jpg, "JPEG", quality=90, dpi=(resolution, resolution))
    png.unlink()


class Scanner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Called with (doc_id, message) after a scan ends in an error.
        self.on_error: Callable[[str, str], None] | None = None

    def start(self, store: DocStore, doc_id: str, settings: ScanSettings) -> None:
        if not self._lock.acquire(blocking=False):
            raise ScannerBusy()
        guards = ExitStack()
        try:
            guards.enter_context(store.use(doc_id))
            with store.transaction(doc_id) as doc:
                doc.scanning, doc.scan_error, doc.scan_resolution = True, None, settings.resolution
            try:
                threading.Thread(target=self._run, args=(store, doc_id, settings, guards), daemon=True).start()
            except BaseException:
                with store.transaction(doc_id) as doc:
                    doc.scanning = False
                    doc.scan_error = "Scan could not start; retry to continue."
                raise
        except BaseException:
            guards.close()
            self._lock.release()
            raise

    def _run(self, store: DocStore, doc_id: str, s: ScanSettings, guards: ExitStack) -> None:
        incoming = store.path(doc_id) / "incoming"
        error = None
        try:
            try:
                error = self._scan(store, doc_id, s, incoming)
            except ScannerUnavailable as e:
                error = str(e)
            except Exception as e:  # e.g. unreadable PNG or full disk while ingesting
                error = f"Scanner error: {e}"
            finally:
                shutil.rmtree(incoming, ignore_errors=True)
                with store.transaction(doc_id) as doc:
                    doc.scanning, doc.scan_error = False, error
                if error and self.on_error:
                    self.on_error(doc_id, error)
        finally:
            guards.close()
            self._lock.release()

    def _scan(self, store: DocStore, doc_id: str, s: ScanSettings, incoming: Path) -> str | None:
        device = find_device()
        if device is None:
            return "No scanner found"
        names, caps = _options(device)
        if s.source not in names or s.mode not in names:
            return f"Scanner does not support {s.source}/{s.mode}"
        bw, bh = caps.bed_mm
        w, h = (bw, bh) if s.area == "full" else AREAS_MM[s.area]
        cmd = [
            SCANIMAGE, "-d", device, "--format=png",
            "--source", names[s.source], "--mode", names[s.mode],
            "--resolution", str(s.resolution),
            "-x", f"{min(w, bw):g}", "-y", f"{min(h, bh):g}",
        ]  # fmt: skip
        adf = s.source == "adf"
        incoming.mkdir(exist_ok=True)
        cmd += [f"--batch={incoming}/p%04d.png", "--batch-print"] if adf else ["-o", str(incoming / "page.png")]

        added = 0

        def add(png: Path) -> None:
            nonlocal added
            page_id = secrets.token_urlsafe(8)
            ingest(png, store.path(doc_id) / "pages" / f"{page_id}.jpg", s.resolution)
            with store.transaction(doc_id) as doc:
                doc.pages.append(Page(page_id))
            added += 1

        with tempfile.TemporaryFile("w+", dir=incoming) as err:
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True) as proc:
                try:
                    for line in proc.stdout:
                        if line.strip():
                            add(Path(line.strip()))
                    rc = proc.wait()
                except BaseException:
                    proc.kill()  # Popen.__exit__ then closes the pipe and reaps it
                    raise
            err.seek(0)
            stderr = err.read()
        low = stderr.lower()
        if rc != 0 and not (adf and "out of documents" in low):
            return "Scanner busy" if "busy" in low else f"Scanner error: {_last_error(stderr)}"
        if not adf:
            add(incoming / "page.png")
        elif added == 0:
            return "Document feeder is empty"
        return None
