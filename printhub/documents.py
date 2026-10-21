# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import re
import secrets
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

# secrets.token_urlsafe output; also keeps path() from escaping the store root
_ID_RE = re.compile(r"[A-Za-z0-9_-]{22,}")


@dataclass
class Crop:
    x: int
    y: int
    w: int
    h: int


@dataclass
class Page:
    id: str
    rotation: int = 0
    crop: Crop | None = None


@dataclass
class PrintRecord:
    job_id: int
    at: str
    options: dict[str, str]


@dataclass
class MailRecord:
    at: str
    to: list[str]
    subject: str
    format: str


@dataclass
class Doc:
    id: str
    kind: Literal["print", "scan"]
    title: str
    created: str
    modified: str
    pages: list[Page]
    source_name: str | None = None
    scan_resolution: int | None = None
    scanning: bool = False
    scan_error: str | None = None
    prints: list[PrintRecord] = field(default_factory=list)
    mails: list[MailRecord] = field(default_factory=list)


class DocNotFound(KeyError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _doc_from_json(raw: dict) -> Doc:
    pages = [
        Page(id=p["id"], rotation=p["rotation"], crop=Crop(**p["crop"]) if p["crop"] else None)
        for p in raw["pages"]
    ]
    prints = [PrintRecord(**p) for p in raw["prints"]]
    mails = [MailRecord(**m) for m in raw["mails"]]
    return Doc(**{**raw, "pages": pages, "prints": prints, "mails": mails})


class DocStore:
    def __init__(self, root: Path):
        self.root = root

    def path(self, id: str) -> Path:
        if not _ID_RE.fullmatch(id):
            raise DocNotFound(id)
        return self.root / id

    def create(self, kind: Literal["print", "scan"], title: str) -> Doc:
        now = _now()
        doc = Doc(
            id=secrets.token_urlsafe(16),
            kind=kind,
            title=title,
            created=now,
            modified=now,
            pages=[],
        )
        (self.path(doc.id) / "pages").mkdir(parents=True)
        self._write(doc)
        return doc

    def get(self, id: str) -> Doc:
        try:
            raw = json.loads((self.path(id) / "doc.json").read_text())
        except FileNotFoundError as e:
            raise DocNotFound(id) from e
        return _doc_from_json(raw)

    def save(self, doc: Doc) -> None:
        doc.modified = _now()
        self._write(doc)

    def delete(self, id: str) -> None:
        if not self.path(id).is_dir():
            raise DocNotFound(id)
        shutil.rmtree(self.path(id))

    def _write(self, doc: Doc) -> None:
        target = self.path(doc.id) / "doc.json"
        tmp = target.with_name("doc.json.tmp")
        with tmp.open("w") as f:
            json.dump(asdict(doc), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)

    # Defined last: the name shadows the builtin `list` for the class body.
    def list(self, kind: Literal["print", "scan"] | None = None) -> list[Doc]:
        docs = []
        for p in self.root.glob("*/doc.json"):
            # A doc deleted mid-listing or a corrupt/foreign entry must not break every listing.
            try:
                docs.append(self.get(p.parent.name))
            except (DocNotFound, OSError, ValueError, TypeError, KeyError):
                continue
        if kind is not None:
            docs = [d for d in docs if d.kind == kind]
        return sorted(docs, key=lambda d: d.modified, reverse=True)
