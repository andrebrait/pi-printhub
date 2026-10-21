# SPDX-License-Identifier: AGPL-3.0-only
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import img2pdf
from PIL import Image

from printhub.documents import Crop, Doc, DocStore, Page


def imcu(jpeg: Path) -> tuple[int, int]:
    with Image.open(jpeg) as im:
        # Pillow lists (component id, h sampling, v sampling, quant table) per component
        return (
            8 * max(h for _, h, _, _ in im.layer),
            8 * max(v for _, _, v, _ in im.layer),
        )


def _trim(n: int, unit: int) -> int:
    # Matches jpegtran -trim: a dimension smaller than one iMCU is kept whole
    return n // unit * unit or n


def rotated_size(w: int, h: int, rotation: int, mcu: tuple[int, int]) -> tuple[int, int]:
    mw, mh = mcu
    if rotation == 0:
        return w, h
    if rotation == 90:
        return _trim(h, mh), w
    if rotation == 180:
        return _trim(w, mw), _trim(h, mh)
    if rotation == 270:
        return h, _trim(w, mw)
    raise ValueError(f"unsupported rotation {rotation}")


def snap_crop(crop: Crop, size: tuple[int, int], mcu: tuple[int, int]) -> Crop:
    width, height = size
    left = min(max(crop.x, 0), width)
    top = min(max(crop.y, 0), height)
    right = min(max(crop.x + crop.w, 0), width)
    bottom = min(max(crop.y + crop.h, 0), height)
    if right <= left or bottom <= top:
        raise ValueError("crop lies outside the image")
    x = left // mcu[0] * mcu[0]
    y = top // mcu[1] * mcu[1]
    return Crop(x, y, right - x, bottom - y)


def _geometry(src: Path, rotation: int) -> tuple[tuple[int, int], tuple[int, int]]:
    with Image.open(src) as im:
        w, h = im.size
    mcu = imcu(src)
    size = rotated_size(w, h, rotation, mcu)
    if rotation in (90, 270):
        mcu = (mcu[1], mcu[0])
    return size, mcu


def page_info(src: Path, page: Page) -> dict:
    (width, height), mcu = _geometry(src, page.rotation)
    with Image.open(src) as im:
        dpi = im.info.get("dpi", (96, 96))
    return {"width": width, "height": height, "mcu": list(mcu), "dpi": list(dpi)}


def apply_edits(src: Path, page: Page, dst: Path) -> None:
    if not page.rotation and page.crop is None:
        shutil.copyfile(src, dst)
        return
    args = ["jpegtran", "-copy", "all"]
    if page.rotation:
        args += ["-rotate", str(page.rotation), "-trim"]
    if page.crop is not None:
        size, mcu = _geometry(src, page.rotation)
        c = snap_crop(page.crop, size, mcu)
        args += ["-crop", f"{c.w}x{c.h}+{c.x}+{c.y}"]
    subprocess.run([*args, "-outfile", dst, src], check=True)


def _edited_pages(store: DocStore, doc: Doc, out_dir: Path) -> list[Path]:
    pages_dir = store.path(doc.id) / "pages"
    paths = []
    for i, page in enumerate(doc.pages, 1):
        dst = out_dir / f"{i:02d}.jpg"
        apply_edits(pages_dir / f"{page.id}.jpg", page, dst)
        paths.append(dst)
    return paths


def export_pdf(store: DocStore, doc: Doc, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pages = _edited_pages(store, doc, Path(tmp))
        with out.open("wb") as f:
            img2pdf.convert(*[str(p) for p in pages], outputstream=f)


def export_jpeg(store: DocStore, doc: Doc, out_dir: Path) -> Path:
    # Titles are user input: keep them from carrying path separators out of out_dir
    name = re.sub(r"[^\w .-]", "_", doc.title).strip() or "document"
    with tempfile.TemporaryDirectory() as tmp:
        pages = _edited_pages(store, doc, Path(tmp))
        if len(pages) == 1:
            out = out_dir / f"{name}.jpg"
            shutil.move(pages[0], out)
            return out
        out = out_dir / f"{name}.zip"
        with zipfile.ZipFile(out, "w") as z:
            for p in pages:
                z.write(p, p.name)
        return out
