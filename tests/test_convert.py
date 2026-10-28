# SPDX-License-Identifier: AGPL-3.0-only
import io
import random
import shutil
import subprocess
import zipfile
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from printhub import convert
from printhub.convert import ConversionError, Unsupported, normalize, pdf_page_sizes, render_preview

FIXTURES = Path(__file__).parent / "fixtures"


def _exif_orientation_jpeg(path: Path, orientation: int) -> None:
    img = Image.new("RGB", (400, 300), (200, 30, 30))
    exif = img.getexif()
    exif[0x0112] = orientation
    img.save(path, "JPEG", exif=exif)


def test_detect_rejects_unknown(tmp_path):
    blob = tmp_path / "blob.zip"
    rng = random.Random(1)
    with zipfile.ZipFile(blob, "w") as z:
        for i in range(3):
            z.writestr(f"f{i}.bin", bytes(rng.getrandbits(8) for _ in range(256)))
    with pytest.raises(Unsupported):
        convert.detect(blob)


def test_jpeg_exif_rotation(tmp_path):
    src = tmp_path / "photo.jpg"
    _exif_orientation_jpeg(src, 6)
    out = tmp_path / "out.pdf"
    normalize(src, out)
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 1
        assert int(pdf.pages[0].obj.get("/Rotate", 0)) == 90
        assert pdf_page_sizes(out) == [(300.0, 400.0)]


def test_png_alpha_flattened(tmp_path):
    rgba = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    rgba.paste((255, 0, 0, 255), (200, 0, 400, 300))
    rgba_path = tmp_path / "rgba.png"
    rgba.save(rgba_path)

    palette = Image.new("P", (400, 300), 0)
    palette.paste(1, (0, 0, 200, 300))  # index 1 is transparent, left half
    palette.putpalette([255, 0, 0, 0, 0, 0] + [0] * 762)
    palette_path = tmp_path / "palette.png"
    palette.save(palette_path, transparency=1)

    for src in (rgba_path, palette_path):
        out = tmp_path / f"{src.stem}.pdf"
        normalize(src, out)
        with pikepdf.open(out) as pdf:
            assert all(img.get("/SMask") is None for img in pdf.pages[0].images.values())
        previews_dir = tmp_path / src.stem
        previews_dir.mkdir()
        previews = render_preview(out, previews_dir, width=600)
        assert len(previews) == 1
        with Image.open(previews[0]) as page:
            assert page.convert("RGB").getpixel((5, 5)) == (255, 255, 255)
            assert page.convert("RGB").getpixel((580, 5)) == (255, 0, 0)


@pytest.mark.skipif(shutil.which("heif-dec") is None, reason="heif-dec not installed")
def test_heic_converts(tmp_path):
    out = tmp_path / "heic.pdf"
    normalize(FIXTURES / "sample.heic", out)
    assert len(pdf_page_sizes(out)) == 1


@pytest.mark.slow
def test_docx_converts(tmp_path):
    out = tmp_path / "sample.pdf"
    normalize(FIXTURES / "sample.docx", out)
    assert len(pdf_page_sizes(out)) == 1


def test_soffice_timeout_kills_group(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    fake = tmp_path / "fake-soffice"
    fake.write_text(f"#!/bin/sh\necho $$ > {pidfile}\nsleep 600 &\nsleep 600\n")
    fake.chmod(0o755)
    monkeypatch.setattr(convert, "SOFFICE", str(fake))
    monkeypatch.setattr(convert, "SOFFICE_TIMEOUT", 1)

    out = tmp_path / "doc.pdf"
    with pytest.raises(ConversionError, match="timed out"):
        normalize(FIXTURES / "sample.docx", out)

    pgid = pidfile.read_text().strip()
    # A killed orphan stays a zombie until its reaper collects it; zombies hold no resources.
    ps = subprocess.run(["ps", "-g", pgid, "-o", "stat="], capture_output=True, text=True)
    live = [s for s in ps.stdout.split() if not s.startswith("Z")]
    assert live == []


def test_pdf_page_sizes(tmp_path):
    path = tmp_path / "a4.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(595.28, 841.89))
        pdf.save(path)
    sizes = pdf_page_sizes(path)
    assert len(sizes) == 1
    width, height = sizes[0]
    assert abs(width - 595.28) <= 0.5
    assert abs(height - 841.89) <= 0.5


@pytest.mark.parametrize("fmt, ext", [("JPEG", "jpg"), ("PNG", "png")])
def test_truncated_image_raises_conversion_error(tmp_path, fmt, ext):
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 30, 30)).save(buf, fmt)
    src = tmp_path / f"truncated.{ext}"
    src.write_bytes(buf.getvalue()[: len(buf.getvalue()) // 2])
    with pytest.raises(ConversionError):
        normalize(src, tmp_path / "out.pdf")


def test_missing_soffice_raises_conversion_error(tmp_path, monkeypatch):
    monkeypatch.setattr(convert, "SOFFICE", str(tmp_path / "no-such-soffice"))
    with pytest.raises(ConversionError):
        normalize(FIXTURES / "sample.docx", tmp_path / "doc.pdf")


@pytest.mark.skipif(shutil.which("heif-dec") is None, reason="heif-dec not installed")
def test_heic_multi_image_uses_first(tmp_path):
    out = tmp_path / "multi.pdf"
    normalize(FIXTURES / "sample_multi.heic", out)
    assert len(pdf_page_sizes(out)) == 1
    previews_dir = tmp_path / "previews"
    previews_dir.mkdir()
    (preview,) = render_preview(out, previews_dir, width=64)
    with Image.open(preview) as page:
        r, g, _ = page.convert("RGB").getpixel((32, 24))
    assert r > 150 and g < 100  # first image is red, second is green


@pytest.mark.parametrize("fmt, ext", [("JPEG", "jpg"), ("PNG", "png")])
def test_decompression_bomb_raises_conversion_error(tmp_path, monkeypatch, fmt, ext):
    src = tmp_path / f"bomb.{ext}"
    Image.new("RGB", (400, 300), (200, 30, 30)).save(src, fmt)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)  # 400x300 is far above twice the limit
    with pytest.raises(ConversionError):
        normalize(src, tmp_path / "out.pdf")


def test_pdf_page_sizes_without_page_lines_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(convert, "_run", lambda cmd: "Creator: nothing useful\n")
    with pytest.raises(ConversionError):
        pdf_page_sizes(tmp_path / "x.pdf")


def test_transparent_png_keeps_source_density(tmp_path):
    src, out = tmp_path / "dense.png", tmp_path / "dense.pdf"
    Image.new("RGBA", (300, 600), (0, 0, 0, 0)).save(src, dpi=(300, 300))
    normalize(src, out)
    assert pdf_page_sizes(out)[0] == pytest.approx((72, 144), abs=0.1)
