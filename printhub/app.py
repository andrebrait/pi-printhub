# SPDX-License-Identifier: AGPL-3.0-only
import functools
import re
import secrets
import shutil
import tempfile
import threading
import time
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import BinaryIO, Literal
from urllib.parse import quote

import cups
from fastapi import APIRouter, Depends, FastAPI, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel
from starlette.background import BackgroundTask

from printhub import convert, edits, printing, scanning
from printhub.auth import (
    COOKIE,
    LoginLimiter,
    PendingShares,
    issue_session,
    require_session,
    session_valid,
    set_session_cookie,
    verify_password,
)
from printhub.config import Config, load_config
from printhub.documents import Crop, Doc, DocBusy, DocNotFound, DocStore, MailRecord, Page, PrintRecord
from printhub.events import EventLog
from printhub.mail import (
    MAX_ATTACHMENT_BYTES,
    Attachment,
    AttachmentTooLarge,
    InvalidRecipient,
    MailError,
    RecentRecipients,
    send,
)
from printhub.middleware import MAX_BODY, MAX_UPLOAD, Guard
from printhub.printing import PrintOptions
from printhub.retention import DiskFull, busy_policy, ensure_free_space
from printhub.scanning import Scanner, ScannerBusy, ScannerUnavailable, ScanSettings
from printhub.watcher import Watcher

THUMB_PX = 400
PENDING_MAX_COUNT = 5
PENDING_MAX_BYTES = 200 * 1024 * 1024
ACTIVE_JOB_STATES = {"pending", "held", "processing", "stopped"}
RECENT_JOBS = 20
# cups.Connection() raises RuntimeError when cupsd is unreachable
CUPS_ERRORS = (cups.IPPError, RuntimeError)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class ApiError(Exception):
    def __init__(self, status: int, error: str):
        self.status, self.error = status, error


class Login(BaseModel):
    password: str


class PageEdit(BaseModel):
    id: str
    rotation: Literal[0, 90, 180, 270] = 0
    crop: Crop | None = None


class DocPatch(BaseModel):
    title: str | None = None
    pages: list[PageEdit] | None = None


class ScanRequest(BaseModel):
    settings: ScanSettings
    doc_id: str | None = None


class EmailRequest(BaseModel):
    to: list[str]
    subject: str
    format: Literal["pdf", "jpeg"]


class MarkRead(BaseModel):
    up_to: int | None = None
    ids: list[int] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _disposition(name: str) -> str:
    fallback = re.sub(r'[^\x20-\x7e]|["\\]', "_", name)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def _copy_limited(src: BinaryIO, dest: Path) -> None:
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := src.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise ApiError(413, "File exceeds 100 MB")
                out.write(chunk)
    except ApiError:
        dest.unlink(missing_ok=True)
        raise
    except OSError:
        dest.unlink(missing_ok=True)
        raise ApiError(507, "Disk full") from None

class DocumentResponse(Response):
    def __init__(self, response, guards):
        super().__init__(status_code=response.status_code, background=response.background)
        self.response, self.guards = response, guards

    async def __call__(self, scope, receive, send):
        try:
            await self.response(scope, receive, send)
        finally:
            self.guards.close()


class RevalidatedStaticFiles(StaticFiles):
    """Browsers revalidate (cheap 304) on every load, so a deploy never mixes new pages with stale scripts."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    cfg: Config,
    *,
    cups_conn_factory=printing.connect,
    scanner: Scanner | None = None,
    free_bytes=None,
    watch: bool = False,
) -> FastAPI:
    store = DocStore(cfg.data_dir / "docs")
    store.root.mkdir(parents=True, exist_ok=True)
    for interrupted in store.list("scan"):
        if not interrupted.scanning and not (store.path(interrupted.id) / "incoming").exists():
            continue
        try:
            with store.transaction(interrupted.id, exclusive=True) as doc:
                if doc.scanning:
                    doc.scanning = False
                    doc.scan_error = "Scan interrupted; retry to continue."
                shutil.rmtree(store.path(doc.id) / "incoming", ignore_errors=True)
        except DocNotFound:
            pass
    pending_dir, tmp_root = cfg.data_dir / "pending", cfg.data_dir / "tmp"
    # Pending shares live in memory only, so their files are orphans after a restart.
    for d in (pending_dir, tmp_root):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    scanner = scanner or Scanner()
    events = EventLog(cfg.data_dir)
    scanner.on_error = lambda doc_id, message: events.append("error", "scan", "Scan failed", message, doc_id=doc_id)
    free_bytes = free_bytes or (lambda: shutil.disk_usage(cfg.data_dir).free)
    min_free = int(cfg.min_free_gb * 1024**3)
    limiter, shares, share_lock = LoginLimiter(), PendingShares(), threading.Lock()
    recipients = RecentRecipients(cfg.data_dir / "recipients.json")

    def admit_share():
        if not share_lock.acquire(blocking=False):
            return JSONResponse({"error": "Another share is uploading"}, 429)
        try:
            pending = shares.paths(time.monotonic())
            if len(pending) >= PENDING_MAX_COUNT or sum(p.stat().st_size for p in pending if p.exists()) + MAX_UPLOAD > PENDING_MAX_BYTES:
                denied = JSONResponse({"error": "Too many pending shares"}, 429)
            elif free_bytes() < min_free + MAX_BODY:
                denied = JSONResponse({"error": "Disk full"}, 507)
            else:
                return None
        except BaseException:
            share_lock.release()
            raise
        share_lock.release()
        return denied

    @asynccontextmanager
    async def lifespan(_app):
        stop = threading.Event()
        if watch:
            watcher = Watcher(cups_conn_factory, cfg.printer_queue, events)
            threading.Thread(target=watcher.run, args=(stop,), name="watcher", daemon=True).start()
        yield
        stop.set()

    app = FastAPI(openapi_url=None, lifespan=lifespan)
    app.add_middleware(Guard, cfg=cfg, admit_share=admit_share, release_share=share_lock.release)

    def using_document(fn=None, *, exclusive=False):
        def decorate(fn):
            @functools.wraps(fn)
            def guarded(doc_id, *args, **kwargs):
                with ExitStack() as guards:
                    guards.enter_context(store.use(doc_id, exclusive=exclusive))
                    result = fn(doc_id, *args, **kwargs)
                    if isinstance(result, FileResponse):
                        return DocumentResponse(result, guards.pop_all())
                    return result
            return guarded
        return decorate(fn) if fn else decorate

    @app.exception_handler(ApiError)
    async def api_error(request, exc: ApiError):
        return JSONResponse({"error": exc.error}, exc.status)

    @app.exception_handler(DocNotFound)
    async def not_found(request, exc):
        return JSONResponse({"error": "Not found"}, 404)

    @app.exception_handler(DocBusy)
    async def doc_busy(request, exc):
        return JSONResponse({"error": "Document is busy"}, 409)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc: RequestValidationError):
        msg = "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"error": msg}, 400)

    busy = busy_policy(cfg, cups_conn_factory)

    def make_room() -> None:
        try:
            ensure_free_space(store, min_free, busy, free_bytes)
        except DiskFull:
            raise ApiError(507, "Disk full") from None

    def detect(path: Path) -> None:
        try:
            convert.detect(path)
        except convert.Unsupported as e:
            path.unlink()
            raise ApiError(415, f"Unsupported file type: {e}") from None

    @contextmanager
    def ingest(src: BinaryIO, filename: str):
        """Pin the new upload through conversion, before exposing it to reclamation."""
        make_room()
        name = PurePath(filename).name or "document"
        failed = None
        with store.new("print", PurePath(name).stem or name) as doc:
            with store.transaction(doc.id) as current:
                current.source_name = name
            ext = PurePath(name).suffix
            dest = store.path(doc.id) / f"source{ext if re.fullmatch(r'\.[A-Za-z0-9]{1,10}', ext) else ''}"
            try:
                _copy_limited(src, dest)
                detect(dest)
                yield doc.id
            except ApiError as exc:
                failed = exc
        if failed:
            store.delete(doc.id)
            raise failed

    def conversion_error(doc_id: str) -> str | None:
        d = store.path(doc_id)
        source = next(d.glob("source*"), None)
        if source is None:
            raise ApiError(400, "Document has no source file")
        shutil.rmtree(d / "thumbs", ignore_errors=True)
        (d / "thumbs").mkdir()
        try:
            convert.normalize(source, d / "print.pdf")
            convert.render_preview(d / "print.pdf", d / "thumbs")
        except convert.ConversionError as e:
            # normalize may have copied the source to print.pdf, and pdftoppm may have written early pages:
            # a broken conversion must not look converted.
            (d / "print.pdf").unlink(missing_ok=True)
            for png in (d / "thumbs").glob("page-*.png"):
                png.unlink(missing_ok=True)  # a concurrent retry or delete may clear thumbs first
            events.append("error", "conversion", "Conversion failed", str(e), doc_id=doc_id)
            return str(e)
        return None

    def converted(doc_id: str) -> JSONResponse | dict:
        if error := conversion_error(doc_id):
            # The doc stays so the client can retry the conversion.
            return JSONResponse({"error": error, "doc_id": doc_id}, 422)
        return {"doc_id": doc_id}

    def previews(doc_id: str) -> list[Path]:
        return sorted((store.path(doc_id) / "thumbs").glob("page-*.png"))

    def page_count(doc: Doc) -> int:
        return len(doc.pages) if doc.kind == "scan" else len(previews(doc.id))

    def page_path(doc: Doc, page_id: str) -> Path:
        if page_id not in {p.id for p in doc.pages}:
            raise DocNotFound(page_id)
        return store.path(doc.id) / "pages" / f"{page_id}.jpg"

    def doc_json(doc: Doc) -> dict:
        out = asdict(doc)
        if doc.kind == "scan":
            for page, raw in zip(doc.pages, out["pages"]):
                raw["page_info"] = edits.page_info(page_path(doc, page.id), page)
        else:
            pdf = store.path(doc.id) / "print.pdf"
            out["pdf_pages"] = convert.pdf_page_sizes(pdf) if pdf.exists() else []
            out["preview_count"] = len(previews(doc.id))
        return out

    def require_pages(doc: Doc) -> None:
        if not doc.pages:
            raise ApiError(400, "Document has no pages")

    def cups_conn():
        try:
            return cups_conn_factory()
        except CUPS_ERRORS:
            raise ApiError(503, "CUPS unavailable") from None

    def printer_info(conn) -> printing.PrinterInfo:
        try:
            return printing.printer_info(conn, cfg.printer_queue)
        except CUPS_ERRORS:
            raise ApiError(503, "CUPS unavailable") from None

    # Unauthenticated routes

    @app.post("/api/login", status_code=204)
    def login(body: Login, request: Request, response: Response):
        if not limiter.allow(request.client.host, time.monotonic()):
            raise ApiError(429, "Too many attempts; wait a minute")
        if not verify_password(cfg.password_hash, body.password):
            raise ApiError(401, "Wrong password")
        set_session_cookie(response, issue_session(cfg))

    @app.post("/share")
    def share(request: Request, file: UploadFile):
        if session_valid(cfg, request.cookies.get(COOKIE)):
            with ingest(file.file, file.filename or "") as doc_id:
                conversion_error(doc_id)  # print.html shows the failure and offers Retry
                return RedirectResponse(f"/print.html?doc={doc_id}", 303)
        # Anonymous: never evict documents, and bound what strangers can park on disk.
        path = pending_dir / secrets.token_urlsafe(16)
        try:
            _copy_limited(file.file, path)
            detect(path)
            token = shares.put(path, file.filename or "", time.monotonic())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return RedirectResponse(f"/login.html?share={token}", 303)

    # Session routes

    api = APIRouter(prefix="/api", dependencies=[Depends(require_session(cfg))])

    @api.post("/logout", status_code=204)
    def logout(response: Response):
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")

    @api.get("/printer")
    def get_printer():
        return printer_info(cups_conn())

    @api.get("/status")
    def status():
        out = {"printer": None, "error": None, "unread": events.unread(), "latest_event": events.latest()}
        try:
            info = printer_info(cups_conn())
        except ApiError as e:
            return out | {"error": e.error}
        problems = [printing.reason_label(r) for r in info.state_reasons]
        out["printer"] = {
            "state": info.state, "state_reasons": info.state_reasons, "accepting": info.accepting,
            "problems": [{"label": p[0], "level": p[1]} for p in problems if p],
        }
        return out

    def queue_jobs(conn) -> list[printing.JobInfo]:
        try:
            return printing.queue_jobs(conn, cfg.printer_queue)
        except CUPS_ERRORS:
            raise ApiError(503, "CUPS unavailable") from None

    @api.get("/notifications")
    def notifications(before: int | None = None, limit: int = 50):
        page = [asdict(e) | {"read": read} for e, read in events.page(before=before, limit=min(limit, 200))]
        return {"events": page, "unread": events.unread(), "latest": events.latest()}

    @api.post("/notifications/read", status_code=204)
    def notifications_read(body: MarkRead):
        if body.up_to is None and body.ids is None:
            raise ApiError(422, "Give up_to or ids")
        events.mark_read(up_to=body.up_to, ids=body.ids)

    @api.post("/uploads")
    def upload(file: UploadFile):
        with ingest(file.file, file.filename or "") as doc_id:
            return converted(doc_id)

    @api.post("/share/{token}/claim")
    def claim(token: str):
        entry = shares.take(token, time.monotonic())
        if entry is None:
            raise ApiError(410, "Share expired")
        path, filename = entry
        try:
            f = path.open("rb")
        except FileNotFoundError:
            raise ApiError(410, "Share expired") from None
        try:
            with f, ingest(f, filename) as doc_id:
                result = converted(doc_id)
        except ApiError:
            # e.g. disk full: keep the share so the same link can be retried
            shares.put(path, filename, time.monotonic(), token=token)
            raise
        path.unlink(missing_ok=True)
        return result

    @api.post("/docs/{doc_id}/convert")
    @using_document(exclusive=True)
    def retry_convert(doc_id: str):
        if store.get(doc_id).kind != "print":
            raise ApiError(400, "Only print documents are converted")
        if error := conversion_error(doc_id):
            raise ApiError(422, error)
        return {"doc_id": doc_id}

    @api.get("/docs")
    def list_docs(kind: Literal["print", "scan"] | None = None):
        out = []
        for snapshot in store.list(kind):
            try:
                with store.use(snapshot.id) as d:
                    out.append({"id": d.id, "kind": d.kind, "title": d.title, "modified": d.modified, "page_count": page_count(d)})
            except DocNotFound:
                continue
        return out

    @api.get("/docs/{doc_id}")
    @using_document
    def get_doc(doc_id: str):
        return doc_json(store.get(doc_id))

    @api.patch("/docs/{doc_id}")
    def patch_doc(doc_id: str, body: DocPatch):
        removed: set[str] = set()
        with store.transaction(doc_id, exclusive=body.pages is not None, blocking=False) as doc:
            if doc.scanning:
                raise ApiError(409, "Document is being scanned")
            if body.title is not None:
                if not body.title.strip():
                    raise ApiError(400, "Title must not be empty")
                doc.title = body.title.strip()
            if body.pages is not None:
                ids = [p.id for p in body.pages]
                known = {p.id for p in doc.pages}
                if len(set(ids)) != len(ids) or not known.issuperset(ids):
                    raise ApiError(400, "Pages must be distinct pages of this document")
                pages = []
                for edit in body.pages:
                    page = Page(edit.id, edit.rotation)
                    if edit.crop is not None:
                        info = edits.page_info(page_path(doc, edit.id), page)
                        try:
                            page.crop = edits.snap_crop(edit.crop, (info["width"], info["height"]), tuple(info["mcu"]))
                        except ValueError as e:
                            raise ApiError(400, str(e)) from None
                    pages.append(page)
                removed = known - set(ids)
                doc.pages = pages
            out = doc_json(doc)
        out["modified"] = doc.modified
        # The exclusive use guard drained old readers before removing these references.
        for page_id in removed:
            (store.path(doc_id) / "pages" / f"{page_id}.jpg").unlink(missing_ok=True)
            (store.path(doc_id) / "thumbs" / f"{page_id}.jpg").unlink(missing_ok=True)
        return out

    @api.delete("/docs/{doc_id}", status_code=204)
    def delete_doc(doc_id: str):
        if not store.delete(doc_id, lambda doc: not busy(doc)):
            raise ApiError(409, "Document is busy")

    @api.get("/docs/{doc_id}/pages/{page_id}")
    @using_document
    def page_image(doc_id: str, page_id: str, size: Literal["thumb", "full"] = "full", rotated: bool = False):
        doc = store.get(doc_id)
        src = page_path(doc, page_id)
        rotation = next(p.rotation for p in doc.pages if p.id == page_id)
        if size == "full" and rotated and rotation:
            # The crop editor works in the rotated frame, so it gets the page turned exactly as the export turns it.
            tmp = Path(tempfile.mkdtemp(dir=tmp_root))
            try:
                edits.apply_edits(src, Page(page_id, rotation), tmp / "page.jpg")
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            cleanup = BackgroundTask(shutil.rmtree, tmp, ignore_errors=True)
            return FileResponse(tmp / "page.jpg", media_type="image/jpeg", background=cleanup)
        if size == "full":
            return FileResponse(src, media_type="image/jpeg")
        thumb = store.path(doc_id) / "thumbs" / f"{page_id}.jpg"
        if not thumb.exists():
            thumb.parent.mkdir(exist_ok=True)
            tmp = thumb.with_name(f"{page_id}.{secrets.token_hex(4)}.tmp")
            with Image.open(src) as im:
                # draft makes libjpeg decode at a reduced scale: cheap on the Pi
                im.draft(None, (THUMB_PX, THUMB_PX))
                im.thumbnail((THUMB_PX, THUMB_PX))
                im.save(tmp, "JPEG", quality=85)
            tmp.replace(thumb)
        return FileResponse(thumb, media_type="image/jpeg")

    @api.get("/docs/{doc_id}/preview/{n}")
    @using_document
    def preview(doc_id: str, n: int):
        store.get(doc_id)
        pngs = previews(doc_id)
        if not 1 <= n <= len(pngs):
            raise DocNotFound(doc_id)
        return FileResponse(pngs[n - 1], media_type="image/png")

    @api.get("/scanner")
    def get_scanner():
        try:
            device = scanning.find_device()
            if device is None:
                raise ScannerUnavailable()
            return scanning.capabilities(device)
        except ScannerUnavailable:
            raise ApiError(503, "Scanner unavailable") from None

    @api.post("/scans", status_code=202)
    def start_scan(body: ScanRequest):
        if body.doc_id is None:
            make_room()
            with store.new("scan", f"Scan {datetime.now():%Y-%m-%d %H-%M}") as doc:
                try:
                    scanner.start(store, doc.id, body.settings)
                except ScannerBusy:
                    pass
                else:
                    return {"doc_id": doc.id}
            store.delete(doc.id)
            raise ApiError(409, "Scanner busy")
        with store.use(body.doc_id) as doc:
            if doc.kind != "scan":
                raise ApiError(400, "Only scan documents take scanned pages")
            make_room()
            try:
                scanner.start(store, doc.id, body.settings)
            except ScannerBusy:
                raise ApiError(409, "Scanner busy") from None
            return {"doc_id": doc.id}

    @api.get("/docs/{doc_id}/export")
    @using_document
    def export(doc_id: str, format: Literal["pdf", "jpeg"]):
        doc = store.get(doc_id)
        require_pages(doc)
        tmp = Path(tempfile.mkdtemp(dir=tmp_root))
        cleanup = BackgroundTask(shutil.rmtree, tmp, ignore_errors=True)
        try:
            if format == "pdf":
                out = tmp / "export.pdf"
                edits.export_pdf(store, doc, out)
            else:
                out = edits.export_jpeg(store, doc, tmp)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        media = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".zip": "application/zip"}[out.suffix]
        headers = {"Content-Disposition": _disposition(doc.title + out.suffix)}
        return FileResponse(out, media_type=media, headers=headers, background=cleanup)

    @api.post("/docs/{doc_id}/print")
    @using_document
    def print_doc(doc_id: str, opts: PrintOptions):
        doc = store.get(doc_id)
        with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
            pdf = store.path(doc_id) / "print.pdf"
            if doc.kind == "scan":
                require_pages(doc)
                pdf = Path(tmp) / "print.pdf"
                edits.export_pdf(store, doc, pdf)
            elif not pdf.exists():
                raise ApiError(400, "Document has not been converted")
            conn = cups_conn()
            info = printer_info(conn)
            try:
                printing.validate(opts, info)
            except ValueError as e:
                raise ApiError(400, str(e)) from None
            try:
                job_id = printing.submit(conn, cfg.printer_queue, pdf, doc.title, opts, info)
            except CUPS_ERRORS:
                raise ApiError(503, "CUPS unavailable") from None
        options = {k: str(v) for k, v in asdict(opts).items() if v is not None}
        with store.transaction(doc_id) as current:
            current.prints.append(PrintRecord(job_id=job_id, at=_now(), options=options))
        return {"job_id": job_id}

    @api.get("/jobs")
    def jobs():
        doc_of = {p.job_id: d.id for d in store.list() for p in d.prints}
        listed = [asdict(j) | {"doc_id": doc_of.get(j.id)} for j in queue_jobs(cups_conn())]
        active = [j for j in listed if j["state"] in ACTIVE_JOB_STATES]
        recent = [j for j in listed if j["state"] not in ACTIVE_JOB_STATES][:RECENT_JOBS]
        return {"active": active, "recent": recent}

    @api.post("/jobs/{job_id}/cancel", status_code=204)
    def cancel_job(job_id: int):
        conn = cups_conn()
        # Any job of our queue, whoever sent it; never another queue's.
        if job_id not in {j.id for j in queue_jobs(conn)}:
            raise ApiError(404, "Unknown job")
        try:
            printing.cancel(conn, job_id)
        except cups.IPPError:
            raise ApiError(404, "Unknown job") from None

    @api.post("/docs/{doc_id}/email", status_code=204)
    @using_document
    def email(doc_id: str, body: EmailRequest):
        doc = store.get(doc_id)
        require_pages(doc)
        name = re.sub(r"[\r\n]+", " ", doc.title)
        with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
            if body.format == "pdf":
                out = Path(tmp) / "export.pdf"
                edits.export_pdf(store, doc, out)
                exports = [(f"{name}.pdf", out, "application/pdf")]
            else:
                exports = []
                for i, page in enumerate(doc.pages, 1):
                    out = Path(tmp) / f"{i}.jpg"
                    edits.apply_edits(page_path(doc, page.id), page, out)
                    filename = f"{name}.jpg" if len(doc.pages) == 1 else f"{name}-{i:02d}.jpg"
                    exports.append((filename, out, "image/jpeg"))
            if sum(path.stat().st_size for _, path, _ in exports) > MAX_ATTACHMENT_BYTES:
                raise ApiError(413, "Attachments exceed 25 MB")
            attachments = [
                Attachment(filename, path.read_bytes(), mime) for filename, path, mime in exports
            ]
        try:
            send(cfg, body.to, body.subject, attachments)
        except InvalidRecipient as e:
            raise ApiError(400, f"Invalid recipient: {e}") from None
        except AttachmentTooLarge:
            raise ApiError(413, "Attachments exceed 25 MB") from None
        except MailError as e:
            raise ApiError(502, str(e)) from None
        with store.transaction(doc_id) as current:
            current.mails.append(MailRecord(at=_now(), to=body.to, subject=body.subject, format=body.format))
        recipients.add(body.to)

    @api.get("/recipients")
    def get_recipients():
        return recipients.list()

    app.include_router(api)
    app.mount("/", RevalidatedStaticFiles(directory=STATIC_DIR, html=True, check_dir=False), name="static")
    return app


@functools.cache
def _default_app() -> FastAPI:
    return create_app(load_config(), watch=True)


def __getattr__(name: str):
    # Built on first access so importing the module needs no config file.
    if name == "app":
        return _default_app()
    raise AttributeError(name)
