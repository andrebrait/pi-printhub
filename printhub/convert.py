# SPDX-License-Identifier: AGPL-3.0-only
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

import img2pdf
import magic
from PIL import Image

SOFFICE = "soffice"
SOFFICE_TIMEOUT = 120
# Memory is tight on the Pi: one conversion at a time
_soffice_lock = threading.Lock()

# libmagic MIME strings, as emitted by python-magic in the Trixie image
ACCEPTED: dict[str, str] = {
    "application/pdf": "pdf",
    "image/jpeg": "image",
    "image/png": "image",
    "image/heic": "heic",
    "image/heif": "heic",
    "application/msword": "office",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "office",
    "application/vnd.oasis.opendocument.text": "office",
    "text/rtf": "office",
    "text/plain": "office",
    "application/vnd.ms-excel": "office",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "office",
    "application/vnd.oasis.opendocument.spreadsheet": "office",
    "application/vnd.ms-powerpoint": "office",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "office",
    "application/vnd.oasis.opendocument.presentation": "office",
}


class Unsupported(Exception):
    pass


class ConversionError(Exception):
    pass


# img2pdf raises its own classes for bad input; Pillow raises OSError or, past
# MAX_IMAGE_PIXELS, DecompressionBombError (a bare Exception); missing binaries raise OSError
_CONVERSION_ERRORS = (
    OSError,
    ValueError,
    Image.DecompressionBombError,
    img2pdf.ImageOpenError,
    img2pdf.JpegColorspaceError,
    img2pdf.UnsupportedColorspaceError,
    img2pdf.ExifOrientationError,
    img2pdf.NegativeDimensionError,
    img2pdf.PdfTooLargeError,
)


def detect(path: Path) -> str:
    mime = magic.from_file(path, mime=True)
    if mime not in ACCEPTED:
        raise Unsupported(mime)
    return mime


def normalize(src: Path, out_pdf: Path) -> None:
    mime = detect(src)
    handler = ACCEPTED[mime]
    try:
        if handler == "pdf":
            shutil.copyfile(src, out_pdf)
        elif handler == "image":
            if mime == "image/png":
                _png_to_pdf(src, out_pdf)
            else:
                _image_to_pdf(src, out_pdf)
        elif handler == "heic":
            _heic_to_pdf(src, out_pdf)
        else:
            _office_to_pdf(src, out_pdf)
    except _CONVERSION_ERRORS as e:
        raise ConversionError(f"{mime} conversion failed: {e}") from e


def pdf_page_sizes(pdf: Path) -> list[tuple[float, float]]:
    count = re.search(r"^Pages:\s+(\d+)", _run(["pdfinfo", str(pdf)]), re.M)
    if count is None:
        raise ConversionError("pdfinfo reported no page count")
    pages = int(count.group(1))
    info = _run(["pdfinfo", "-f", "1", "-l", str(pages), str(pdf)])
    rotations = dict(re.findall(r"^Page\s+(\d+) rot:\s+(\d+)", info, re.M))
    sizes = []
    for num, w, h in re.findall(r"^Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+) pts", info, re.M):
        # pdfinfo reports the unrotated media box; /Rotate 90 or 270 swaps the displayed axes
        if rotations.get(num) in ("90", "270"):
            w, h = h, w
        sizes.append((float(w), float(h)))
    if len(sizes) != pages:
        raise ConversionError(f"pdfinfo reported {len(sizes)} page sizes for {pages} pages")
    return sizes


def render_preview(pdf: Path, out_dir: Path, width: int = 600) -> list[Path]:
    cmd = ["pdftoppm", "-png", "-scale-to-x", str(width), "-scale-to-y", "-1", str(pdf)]
    _run([*cmd, str(out_dir / "page")])
    return sorted(out_dir.glob("page-*.png"))


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ConversionError(f"{cmd[0]} failed: {proc.stderr.strip()[-500:]}")
    return proc.stdout


def _image_to_pdf(image: Path, out_pdf: Path) -> None:
    # img2pdf embeds a truncated JPEG without complaint; decoding first makes Pillow reject it
    with Image.open(image) as im:
        im.load()
    # rotation=ifvalid turns EXIF orientation into /Rotate instead of rejecting the file
    out_pdf.write_bytes(img2pdf.convert(str(image), rotation=img2pdf.Rotation.ifvalid))


def _png_to_pdf(src: Path, out_pdf: Path) -> None:
    # img2pdf would embed alpha as a soft mask; flatten so every viewer shows white
    with Image.open(src) as im:
        has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        if not has_alpha:
            _image_to_pdf(src, out_pdf)
            return
        rgba = im.convert("RGBA")
        flat = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
    with tempfile.TemporaryDirectory(dir=out_pdf.parent) as tmp:
        flat_png = Path(tmp) / "flat.png"
        flat.save(flat_png)
        _image_to_pdf(flat_png, out_pdf)


def _heic_to_pdf(src: Path, out_pdf: Path) -> None:
    with tempfile.TemporaryDirectory(dir=out_pdf.parent) as tmp:
        # multi-image files come out as page-1.jpg, page-2.jpg, ...; the first one is used
        _run(["heif-dec", "-q", "92", str(src), str(Path(tmp) / "page.jpg")])
        outputs = sorted(Path(tmp).glob("page*.jpg"))
        if not outputs:
            raise ConversionError("heif-dec wrote no image")
        _image_to_pdf(outputs[0], out_pdf)


def _office_to_pdf(src: Path, out_pdf: Path) -> None:
    with tempfile.TemporaryDirectory(dir=out_pdf.parent.resolve()) as tmp:
        work = Path(tmp)
        outdir = work / "out"
        outdir.mkdir()
        cmd = [
            SOFFICE,
            "--headless",
            "--norestore",
            f"-env:UserInstallation={(work / 'profile').as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(src),
        ]
        with _soffice_lock:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
            )
            try:
                _, err = proc.communicate(timeout=SOFFICE_TIMEOUT)
            except subprocess.TimeoutExpired:
                # Kill the whole session: soffice.bin and any helpers it forked
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                raise ConversionError(f"LibreOffice timed out after {SOFFICE_TIMEOUT} s") from None
        produced = outdir / f"{src.stem}.pdf"
        if proc.returncode != 0 or not produced.exists():
            detail = err.decode(errors="replace").strip()[-500:]
            raise ConversionError(f"LibreOffice failed: {detail}")
        os.replace(produced, out_pdf)
