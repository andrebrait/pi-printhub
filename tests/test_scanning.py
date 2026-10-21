# SPDX-License-Identifier: AGPL-3.0-only
import io
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from printhub import scanning
from printhub.documents import DocStore
from printhub.scanning import ScannerBusy, ScanSettings, parse_options

FIXTURE = Path(__file__).parent / "fixtures" / "scanimage-A.txt"

HPAIO = """\
All options specific to device `hpaio:/usb/Officejet_Pro_6970':
  Scan mode:
    --mode Lineart|Gray|Color [Color]
        Selects the scan mode (e.g., lineart, monochrome, or color).
    --resolution 75|100|150|200|300|600|1200dpi [300]
        Sets the resolution of the scanned image.
  Source:
    --source Flatbed|ADF [Flatbed]
        Selects the scan source (such as a document-feeder).
  Geometry:
    -l 0..215.9mm [0]
        Top-left x position of scan area.
    -t 0..355.6mm [0]
        Top-left y position of scan area.
    -x 0..215.9mm [215.9]
        Width of scan-area.
    -y 0..355.6mm [355.6]
        Height of scan-area.
"""


def have_test_backend() -> bool:
    if not shutil.which("scanimage"):
        return False
    out = subprocess.run(["scanimage", "-L"], capture_output=True, text=True).stdout
    return "`test" in out


sane = pytest.mark.skipif(not have_test_backend(), reason="SANE test backend not available")


@pytest.fixture
def store(tmp_path) -> DocStore:
    root = tmp_path / "docs"
    root.mkdir()
    return DocStore(root)


def wait_done(store: DocStore, doc_id: str):
    for _ in range(400):
        doc = store.get(doc_id)
        if not doc.scanning:
            return doc
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def fake_scanimage(tmp_path, monkeypatch, body: str) -> Path:
    """A scanimage stand-in: answers -A from the fixture, runs `body` for scans."""
    script = tmp_path / "scanimage"
    script.write_text(
        f"""#!/bin/sh
case " $* " in *" -A "*) cat {FIXTURE}; exit 0;; esac
for a; do case $a in
  --batch=*) out=$(dirname "${{a#--batch=}}") ;;
  -o) want_out=1 ;;
  *) [ -n "$want_out" ] && out=$(dirname "$a") && want_out= ;;
esac; done
{body}
"""
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(scanning, "SCANIMAGE", str(script))
    monkeypatch.setenv("PRINTHUB_SCANNER", "test")
    return script


def make_png(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (40, 40), color).save(path)
    return path


def test_parse_options_test_backend():
    names, caps = parse_options(FIXTURE.read_text())
    assert caps.sources == ["flatbed", "adf"]
    assert names["adf"] == "Automatic Document Feeder"
    assert caps.modes == ["color", "gray"]
    assert caps.resolutions == [150, 200, 300, 600]
    assert caps.bed_mm == (200.0, 200.0)


def test_parse_options_hpaio_style():
    names, caps = parse_options(HPAIO)
    assert names == {"flatbed": "Flatbed", "adf": "ADF", "gray": "Gray", "color": "Color"}
    assert caps.sources == ["flatbed", "adf"]
    assert caps.modes == ["color", "gray"]
    assert caps.resolutions == [75, 100, 150, 200, 300, 600, 1200]
    assert caps.bed_mm == (215.9, 355.6)


def new_scan_doc(store):
    return store.create("scan", "Scan")


@sane
def test_flatbed_scan_single_encodes_jpeg(store, monkeypatch):
    monkeypatch.setenv("PRINTHUB_SCANNER", "test")
    doc = new_scan_doc(store)
    scanning.Scanner().start(store, doc.id, ScanSettings(source="flatbed"))
    doc = wait_done(store, doc.id)
    assert doc.scan_error is None
    assert doc.scan_resolution == 300
    assert len(doc.pages) == 1
    jpg = store.path(doc.id) / "pages" / f"{doc.pages[0].id}.jpg"
    with Image.open(jpg) as im:
        buf = io.BytesIO()
        Image.new("RGB", (8, 8)).save(buf, "JPEG", quality=90)
        assert im.quantization == Image.open(buf).quantization
        assert im.info["dpi"] == (300, 300)
        assert im.size[0] > 1000
    assert not (store.path(doc.id) / "incoming").exists()


@sane
def test_adf_scan_adds_pages_until_feeder_empty(store, monkeypatch):
    # The test backend's feeder holds 10 sheets and then reports "out of documents".
    monkeypatch.setenv("PRINTHUB_SCANNER", "test")
    doc = new_scan_doc(store)
    scanning.Scanner().start(store, doc.id, ScanSettings(source="adf", resolution=150))
    doc = wait_done(store, doc.id)
    assert doc.scan_error is None
    assert len(doc.pages) == 10
    assert len({p.id for p in doc.pages}) == 10
    assert all((store.path(doc.id) / "pages" / f"{p.id}.jpg").is_file() for p in doc.pages)
    assert not (store.path(doc.id) / "incoming").exists()


def test_adf_out_of_documents_is_success_and_ordered(store, tmp_path, monkeypatch):
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    pngs = [make_png(tmp_path / f"src{i}.png", c) for i, c in enumerate(colors)]
    body = "".join(f'cp {p} "$out/p{i:04d}.png"; echo "$out/p{i:04d}.png"\n' for i, p in enumerate(pngs))
    body += 'echo "scanimage: sane_start: Document feeder out of documents" >&2\nexit 1'
    fake_scanimage(tmp_path, monkeypatch, body)
    doc = new_scan_doc(store)
    scanning.Scanner().start(store, doc.id, ScanSettings(source="adf"))
    doc = wait_done(store, doc.id)
    assert doc.scan_error is None
    assert len(doc.pages) == 3
    seen = []
    for p in doc.pages:
        with Image.open(store.path(doc.id) / "pages" / f"{p.id}.jpg") as im:
            r, g, b = im.getpixel((5, 5))
            seen.append((r > 200, g > 200, b > 200))
    assert seen == [(True, False, False), (False, True, False), (False, False, True)]
    assert not (store.path(doc.id) / "incoming").exists()


def test_busy_lock(store, tmp_path, monkeypatch):
    go = tmp_path / "go"
    png = make_png(tmp_path / "src.png", (9, 9, 9))
    fake_scanimage(tmp_path, monkeypatch, f'while [ ! -e {go} ]; do sleep 0.05; done\ncp {png} "$out/page.png"')
    doc = new_scan_doc(store)
    scanner = scanning.Scanner()
    scanner.start(store, doc.id, ScanSettings(source="flatbed"))
    assert store.get(doc.id).scanning is True
    with pytest.raises(ScannerBusy):
        scanner.start(store, doc.id, ScanSettings(source="flatbed"))
    go.touch()
    doc = wait_done(store, doc.id)
    assert doc.scan_error is None and len(doc.pages) == 1
    scanner.start(store, doc.id, ScanSettings(source="flatbed"))  # lock released again
    wait_done(store, doc.id)


def test_failure_mid_batch_keeps_pages(store, tmp_path, monkeypatch):
    png = make_png(tmp_path / "src.png", (9, 9, 9))
    body = (
        f'cp {png} "$out/p0001.png"; echo "$out/p0001.png"\n'
        'echo "Error during device I/O" >&2\nexit 1'
    )
    fake_scanimage(tmp_path, monkeypatch, body)
    doc = new_scan_doc(store)
    scanning.Scanner().start(store, doc.id, ScanSettings(source="adf"))
    doc = wait_done(store, doc.id)
    assert len(doc.pages) == 1
    assert doc.scanning is False
    assert doc.scan_error.startswith("Scanner error")
    assert "Error during device I/O" in doc.scan_error


def test_busy_stderr_maps(store, tmp_path, monkeypatch):
    fake_scanimage(tmp_path, monkeypatch, 'echo "scanimage: open of device test failed: Device busy" >&2\nexit 1')
    doc = new_scan_doc(store)
    scanning.Scanner().start(store, doc.id, ScanSettings(source="flatbed"))
    doc = wait_done(store, doc.id)
    assert doc.scan_error == "Scanner busy"
    assert doc.pages == []


def test_no_scanner_sets_error(store, tmp_path, monkeypatch):
    fake_scanimage(tmp_path, monkeypatch, "exit 0")
    monkeypatch.delenv("PRINTHUB_SCANNER")
    doc = new_scan_doc(store)
    scanning.Scanner().start(store, doc.id, ScanSettings(source="flatbed"))
    doc = wait_done(store, doc.id)
    assert doc.scan_error == "No scanner found"


@pytest.mark.parametrize(
    ("area", "expected"),
    [("a4", "-x 200 -y 200"), ("full", "-x 200 -y 200")],  # test backend bed is 200x200 mm: clamped
)
def test_area_is_clamped_to_bed(store, tmp_path, monkeypatch, area, expected):
    log = tmp_path / "args"
    fake_scanimage(tmp_path, monkeypatch, f'echo "$*" > {log}\nexit 0')
    doc = new_scan_doc(store)
    scanning.Scanner().start(store, doc.id, ScanSettings(source="adf", area=area))
    wait_done(store, doc.id)
    args = log.read_text()
    assert expected in args
    assert "--source Automatic Document Feeder" in args and "--mode Color" in args
