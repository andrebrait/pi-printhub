# SPDX-License-Identifier: AGPL-3.0-only
import subprocess
import zipfile

import pikepdf
import pytest
from PIL import Image

from printhub.documents import Crop, DocStore, Page
from printhub.edits import (
    apply_edits,
    export_jpeg,
    export_pdf,
    imcu,
    page_info,
    rotated_size,
    snap_crop,
)


@pytest.fixture
def store(cfg):
    return DocStore(cfg.data_dir / "docs")


def make_jpeg(path, size, *, gray=False, subsampling=2, dpi=300):
    if gray:
        im = Image.new("L", size, 128)
        im.save(path, format="JPEG", quality=90, dpi=(dpi, dpi))
    else:
        im = Image.new("RGB", size, (200, 120, 40))
        im.save(path, format="JPEG", quality=90, dpi=(dpi, dpi), subsampling=subsampling)
    return path


def jpeg_size(path):
    with Image.open(path) as im:
        return im.size


def make_doc(store, title, *specs):
    """One page per (size, make_jpeg kwargs) spec, stored in the page layout."""
    doc = store.create("scan", title)
    for i, (size, kwargs) in enumerate(specs, 1):
        page = Page(id=f"p{i}")
        make_jpeg(store.path(doc.id) / "pages" / f"{page.id}.jpg", size, **kwargs)
        doc.pages.append(page)
    store.save(doc)
    return doc


def original(store, doc, page):
    return store.path(doc.id) / "pages" / f"{page.id}.jpg"


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"subsampling": 2}, (16, 16)),
        ({"subsampling": 1}, (16, 8)),
        ({"subsampling": 0}, (8, 8)),
        ({"gray": True}, (8, 8)),
    ],
)
def test_imcu_values(tmp_path, kwargs, expected):
    assert imcu(make_jpeg(tmp_path / "a.jpg", (100, 60), **kwargs)) == expected


def test_rotated_size_100x60_420():
    assert rotated_size(100, 60, 90, (16, 16)) == (48, 100)
    assert rotated_size(100, 60, 180, (16, 16)) == (96, 48)


@pytest.mark.parametrize("size", [(100, 60), (101, 61), (33, 40), (17, 7)])
@pytest.mark.parametrize(
    "kwargs", [{"subsampling": 2}, {"subsampling": 1}, {"subsampling": 0}, {"gray": True}]
)
@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_size_matches_jpegtran(tmp_path, size, kwargs, rotation):
    src = make_jpeg(tmp_path / "in.jpg", size, **kwargs)
    out = tmp_path / "out.jpg"
    subprocess.run(
        ["jpegtran", "-copy", "all", "-rotate", str(rotation), "-trim", "-outfile", out, src],
        check=True,
    )
    assert rotated_size(*size, rotation, imcu(src)) == jpeg_size(out)


@pytest.mark.parametrize("mcu", [(16, 16), (8, 8)])
@pytest.mark.parametrize("y", [0, 7, 8, 15, 16, 17])
@pytest.mark.parametrize("x", [0, 7, 8, 15, 16, 17])
def test_snap_crop_boundaries(x, y, mcu):
    snapped = snap_crop(Crop(x, y, 40, 30), (100, 60), mcu)
    assert snapped.x % mcu[0] == 0 and snapped.y % mcu[1] == 0
    assert snapped.x + snapped.w == x + 40
    assert snapped.y + snapped.h == y + 30
    assert 0 <= snapped.x and snapped.x + snapped.w <= 100
    assert 0 <= snapped.y and snapped.y + snapped.h <= 60


@pytest.mark.parametrize(
    "rotation, crop",
    [
        (0, Crop(50, 40, 100, 100)),  # extends past the right and bottom edges
        (0, Crop(-5, -3, 30, 20)),  # negative origin
        (90, Crop(0, 0, 48, 100)),  # full image on a rotated, trimmed page
    ],
)
def test_snap_crop_clamps(tmp_path, rotation, crop):
    src = make_jpeg(tmp_path / "in.jpg", (100, 60))
    page = Page(id="p", rotation=rotation, crop=crop)
    info = page_info(src, page)
    snapped = snap_crop(crop, (info["width"], info["height"]), tuple(info["mcu"]))
    assert 0 <= snapped.x and 0 <= snapped.y
    assert snapped.x + snapped.w <= info["width"]
    assert snapped.y + snapped.h <= info["height"]
    assert snapped.w > 0 and snapped.h > 0
    apply_edits(src, page, tmp_path / "out.jpg")
    assert jpeg_size(tmp_path / "out.jpg") == (snapped.w, snapped.h)


def test_snap_crop_rejects_crop_outside_image():
    with pytest.raises(ValueError):
        snap_crop(Crop(100, 0, 10, 10), (100, 60), (16, 16))


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_apply_edits_matches_pillow_size(tmp_path, rotation):
    src = make_jpeg(tmp_path / "in.jpg", (100, 60))
    page = Page(id="p", rotation=rotation, crop=Crop(7, 5, 40, 30))
    info = page_info(src, page)
    expected = snap_crop(page.crop, (info["width"], info["height"]), tuple(info["mcu"]))
    apply_edits(src, page, tmp_path / "out.jpg")
    assert jpeg_size(tmp_path / "out.jpg") == (expected.w, expected.h)


def test_apply_edits_copies_unedited_page(tmp_path):
    src = make_jpeg(tmp_path / "in.jpg", (100, 60))
    apply_edits(src, Page(id="p"), tmp_path / "out.jpg")
    assert (tmp_path / "out.jpg").read_bytes() == src.read_bytes()


def test_export_pdf_lossless(store, tmp_path):
    doc = make_doc(store, "t", ((100, 60), {}), ((64, 48), {"gray": True}))
    doc.pages[0].rotation = 90
    doc.pages[0].crop = Crop(7, 5, 40, 30)
    doc.pages[1].rotation = 180
    store.save(doc)

    expected = []
    for i, page in enumerate(doc.pages):
        apply_edits(original(store, doc, page), page, tmp_path / f"{i}.jpg")
        expected.append((tmp_path / f"{i}.jpg").read_bytes())

    export_pdf(store, doc, tmp_path / "out.pdf")

    with pikepdf.open(tmp_path / "out.pdf") as pdf:
        assert len(pdf.pages) == 2
        for pdf_page, want in zip(pdf.pages, expected, strict=True):
            (image,) = pdf_page.images.values()
            assert image.read_raw_bytes() == want


def test_export_pdf_page_size(store, tmp_path):
    doc = make_doc(store, "t", ((2480, 3508), {"dpi": 300}))
    export_pdf(store, doc, tmp_path / "out.pdf")
    with pikepdf.open(tmp_path / "out.pdf") as pdf:
        _, _, width, height = (float(v) for v in pdf.pages[0].mediabox)
    assert abs(width - 595) <= 1
    assert abs(height - 842) <= 1


def test_export_jpeg_single_and_zip(store, tmp_path):
    one = make_doc(store, "Quittung", ((100, 60), {}))
    assert export_jpeg(store, one, tmp_path) == tmp_path / "Quittung.jpg"
    assert (tmp_path / "Quittung.jpg").read_bytes() == original(store, one, one.pages[0]).read_bytes()

    many = make_doc(store, "Multi", ((100, 60), {}), ((64, 48), {}), ((32, 40), {}))
    many.pages[1].rotation = 90
    store.save(many)
    zip_path = export_jpeg(store, many, tmp_path)
    assert zip_path == tmp_path / "Multi.zip"

    expected = []
    for i, page in enumerate(many.pages):
        apply_edits(original(store, many, page), page, tmp_path / f"e{i}.jpg")
        expected.append((tmp_path / f"e{i}.jpg").read_bytes())
    with zipfile.ZipFile(zip_path) as z:
        assert z.namelist() == ["01.jpg", "02.jpg", "03.jpg"]
        assert [z.read(name) for name in z.namelist()] == expected


def test_export_jpeg_title_cannot_escape_out_dir(store, tmp_path):
    doc = make_doc(store, "../../evil", ((100, 60), {}))
    out = export_jpeg(store, doc, tmp_path)
    assert out.parent == tmp_path


def test_original_untouched(store, tmp_path):
    doc = make_doc(store, "t", ((100, 60), {}), ((64, 48), {"gray": True}))
    doc.pages[0].rotation = 90
    doc.pages[0].crop = Crop(7, 5, 40, 30)
    src = original(store, doc, doc.pages[0])
    before = src.read_bytes()

    apply_edits(src, doc.pages[0], tmp_path / "a.jpg")
    page_info(src, doc.pages[0])
    export_pdf(store, doc, tmp_path / "d.pdf")
    export_jpeg(store, doc, tmp_path)

    assert src.read_bytes() == before


@pytest.fixture
def marked_jpeg(tmp_path):
    # Asymmetric four marked quadrants, with MCU-aligned edges.
    from PIL import ImageDraw
    im = Image.new("RGB", (128, 96))
    draw = ImageDraw.Draw(im)
    for box, color, label in [
        ((0, 0, 63, 47), (230, 20, 20), "TL"),
        ((64, 0, 127, 47), (20, 210, 20), "TR"),
        ((0, 48, 63, 95), (20, 20, 230), "BL"),
        ((64, 48, 127, 95), (230, 210, 20), "BR"),
    ]:
        draw.rectangle(box, fill=color)
        draw.text((box[0] + 20, box[1] + 20), label, fill="white")
    src = tmp_path / "marked.jpg"
    im.save(src, quality=95, subsampling=0, dpi=(300, 300))
    return src


@pytest.mark.parametrize("rotation,order", [
    (0, [0, 1, 2, 3]), (90, [2, 0, 3, 1]),
    (180, [3, 2, 1, 0]), (270, [1, 3, 0, 2]),
])
def test_marked_orientation_and_crop_content(marked_jpeg, tmp_path, rotation, order):
    colors = [(230, 20, 20), (20, 210, 20), (20, 20, 230), (230, 210, 20)]
    out = tmp_path / "turned.jpg"
    apply_edits(marked_jpeg, Page("p", rotation), out)
    with Image.open(out) as im:
        w, h = im.size
        actual = [im.getpixel(p) for p in [(8, 8), (w - 9, 8), (8, h - 9), (w - 9, h - 9)]]
    assert actual == [pytest.approx(colors[i], abs=8) for i in order]
    apply_edits(marked_jpeg, Page("p", rotation, Crop(0, 0, 32, 32)), out)
    with Image.open(out) as im:
        assert im.size == (32, 32)
        assert im.getpixel((8, 8)) == pytest.approx(colors[order[0]], abs=8)


def test_page_info_density_matches_rotated_export(marked_jpeg, tmp_path):
    with Image.open(marked_jpeg) as im:
        im.save(tmp_path / "density.jpg", dpi=(150, 300))
    page = Page("p", rotation=90)
    apply_edits(tmp_path / "density.jpg", page, tmp_path / "turned.jpg")
    with Image.open(tmp_path / "turned.jpg") as exported:
        assert page_info(tmp_path / "density.jpg", page)["dpi"] == list(exported.info["dpi"])
