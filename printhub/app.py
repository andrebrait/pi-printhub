# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import functools
import logging
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
from pydantic import BaseModel, ConfigDict, StrictBool, StrictFloat, StrictInt, StrictStr
from starlette.background import BackgroundTask

from printhub import convert, edits, printing, scanning, senders
from printhub import notifications as notification_store
from printhub.auth import (
    COOKIE,
    LoginLimiter,
    PendingShares,
    hash_password,
    issue_session,
    require_session,
    session_valid,
    set_session_cookie,
    verify_password,
)
from printhub.config import Config, ConfigError, load_config
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
from printhub.notifier import Notifier
from printhub.printing import PrintOptions
from printhub.retention import DiskFull, busy_policy, ensure_free_space
from printhub.scanning import Scanner, ScannerBusy, ScannerUnavailable, ScanSettings
from printhub.settings import read_system, request_system, save as save_settings
from printhub.watcher import Watcher

THUMB_PX = 400
PENDING_MAX_COUNT = 5
PENDING_MAX_BYTES = 200 * 1024 * 1024
ACTIVE_JOB_STATES = {"pending", "held", "processing", "stopped"}
RECENT_JOBS = 20
WATCH_INTERVAL = 3.0  # seconds between CUPS checks for notifications
# cups.Connection() raises RuntimeError when cupsd is unreachable
CUPS_ERRORS = (cups.IPPError, RuntimeError)
log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class ApiError(Exception):
    def __init__(self, status: int, error: str):
        self.status, self.error = status, error


class Login(BaseModel):
    password: str


class SettingsPatch(BaseModel):
    # Strict types keep "30" from becoming 30; a null or absent key means "leave as is".
    model_config = ConfigDict(extra="forbid")
    retention_days: StrictInt | None = None
    min_free_gb: StrictInt | StrictFloat | None = None
    smtp_host: StrictStr | None = None
    smtp_port: StrictInt | None = None
    smtp_security: StrictStr | None = None
    smtp_user: StrictStr | None = None
    smtp_password: StrictStr | None = None
    mail_from: StrictStr | None = None


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current: StrictStr
    new: StrictStr


class SendTestEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: StrictStr


class SystemSwitches(BaseModel):
    model_config = ConfigDict(extra="forbid")
    share: StrictBool
    expose_usb: StrictBool


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


EMAIL_KEYS = {"smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password", "mail_from"}
SMTP_SERVER_KEYS = {"smtp_host", "smtp_port", "smtp_security", "smtp_user"}


def public_settings(cfg: Config) -> dict:
    keys = ("retention_days", "min_free_gb", "smtp_host", "smtp_port", "smtp_security", "smtp_user", "mail_from")
    return {k: getattr(cfg, k) for k in keys} | {"smtp_password_set": bool(cfg.smtp_password)}


def describe_changes(old: Config, changes: dict) -> str:
    """Event title naming what changed, never a secret."""
    parts = []
    if "retention_days" in changes:
        parts.append(f"Retention changed from {old.retention_days} to {changes['retention_days']} days")
    if "min_free_gb" in changes:
        parts.append(f"Minimum free space changed from {old.min_free_gb:g} to {changes['min_free_gb']:g} GB")
    if changes.keys() & EMAIL_KEYS:
        parts.append("Email settings changed")
    return "; ".join(parts)


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

    def notify(level, kind, title, detail="", **ids) -> None:
        # A notification must never cost the operation it reports, e.g. on a full disk.
        try:
            events.append(level, kind, title, detail, **ids)
        except OSError:
            log.exception("could not record a %s event", kind)

    scanner.on_error = lambda doc_id, message: notify("error", "scan", "Scan failed", message, doc_id=doc_id)
    # job id -> doc id for /api/jobs, rebuilt when documents appear or go (the docs directory changes).
    job_docs: dict[int, str] = {}
    job_docs_stamp = [None]
    free_bytes = free_bytes or (lambda: shutil.disk_usage(cfg.data_dir).free)
    settings_lock = threading.Lock()
    notify_lock = threading.Lock()
    limiter, shares, share_lock = LoginLimiter(), PendingShares(), threading.Lock()
    recipients = RecentRecipients(cfg.data_dir / "recipients.json")

    def min_free() -> int:
        return int(app.state.cfg.min_free_gb * 1024**3)

    def admit_share():
        if not share_lock.acquire(blocking=False):
            return JSONResponse({"error": "Another share is uploading"}, 429)
        try:
            pending = shares.paths(time.monotonic())
            if len(pending) >= PENDING_MAX_COUNT or sum(p.stat().st_size for p in pending if p.exists()) + MAX_UPLOAD > PENDING_MAX_BYTES:
                denied = JSONResponse({"error": "Too many pending shares"}, 429)
            elif free_bytes() < min_free() + MAX_BODY:
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
        notifier = None
        if watch:
            watcher = Watcher(cups_conn_factory, cfg.printer_queue, events, data_dir=cfg.data_dir)
            threading.Thread(target=watcher.run, args=(stop, WATCH_INTERVAL), name="watcher", daemon=True).start()
            notifier = threading.Thread(target=app.state.notifier.run, args=(stop,), name="notifier", daemon=True)
            notifier.start()
        try:
            yield
        finally:
            stop.set()
            if notifier:
                # Its close() waits up to 20 s for sends in flight; the backlog's cursor is already saved.
                await asyncio.to_thread(notifier.join, 25)

    app = FastAPI(openapi_url=None, lifespan=lifespan)
    app.state.cfg = cfg
    app.state.notifier = Notifier(events, cfg.data_dir, lambda: app.state.cfg)
    app.add_middleware(Guard, current=lambda: app.state.cfg, admit_share=admit_share, release_share=share_lock.release)

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

    @app.exception_handler(ConfigError)
    async def invalid_setting(request, exc: ConfigError):
        return JSONResponse({"error": str(exc)}, 400)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc: RequestValidationError):
        msg = "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"error": msg}, 400)

    busy = busy_policy(cfg, cups_conn_factory)

    def make_room() -> None:
        try:
            ensure_free_space(store, min_free(), busy, free_bytes)
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
            notify("error", "conversion", "Conversion failed", str(e), doc_id=doc_id)
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
        if not verify_password(app.state.cfg.password_hash, body.password):
            raise ApiError(401, "Wrong password")
        set_session_cookie(response, issue_session(app.state.cfg))

    @app.post("/share")
    def share(request: Request, file: UploadFile):
        if session_valid(app.state.cfg, request.cookies.get(COOKIE)):
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

    api = APIRouter(prefix="/api", dependencies=[Depends(require_session(lambda: app.state.cfg))])

    @api.post("/logout", status_code=204)
    def logout(response: Response):
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")

    @api.get("/printer")
    def get_printer():
        return {k: v for k, v in asdict(printer_info(cups_conn())).items() if k != "device_uri"}

    @api.get("/status")
    def status():
        out = {"printer": None, "error": None, "unread": events.unread(), "latest_event": events.latest()}
        try:
            info = printer_info(cups_conn())
        except ApiError as e:
            return out | {"error": e.error}
        out["printer"] = {
            "state": info.state, "state_reasons": info.state_reasons, "accepting": info.accepting,
            "problems": [{"label": label, "level": level} for label, level in printing.problems(info.state_reasons).items()],
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
        job_docs[job_id] = doc_id
        return {"job_id": job_id}

    @api.get("/jobs")
    def jobs():
        stamp = store.root.stat().st_mtime_ns
        if stamp != job_docs_stamp[0]:
            job_docs.clear()
            job_docs.update({p.job_id: d.id for d in store.list() for p in d.prints})
            job_docs_stamp[0] = stamp
        doc_of = job_docs
        listed = [
            asdict(j) | {"doc_id": doc_of.get(j.id), "reason_labels": [lab[0] for r in j.reasons if (lab := printing.reason_label(r))]}
            for j in queue_jobs(cups_conn())
        ]
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
            send(app.state.cfg, body.to, body.subject, attachments)
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

    @api.get("/settings")
    def get_settings():
        return public_settings(app.state.cfg)

    @api.put("/settings")
    def put_settings(body: SettingsPatch):
        sent = body.model_dump(exclude_none=True)
        if not sent.get("smtp_password"):  # blank keeps the stored password
            sent.pop("smtp_password", None)
        with settings_lock:
            old = app.state.cfg
            changes = {k: v for k, v in sent.items() if getattr(old, k) != v}
            if changes.keys() & SMTP_SERVER_KEYS and "smtp_password" not in sent:
                # Else a changed host would send the stored password to wherever it points.
                raise ApiError(400, "Enter the SMTP password again to change the server settings")
            if changes:
                app.state.cfg = save_settings(old, changes)
                notify("info", "settings", describe_changes(old, changes))
            return public_settings(app.state.cfg)

    @api.post("/settings/password", status_code=204)
    def change_password(body: PasswordChange, request: Request, response: Response):
        # The same limit as login: the session alone must not allow guessing the current password.
        if not limiter.allow(request.client.host, time.monotonic()):
            raise ApiError(429, "Too many attempts; wait a minute")
        with settings_lock:
            old = app.state.cfg
            if not verify_password(old.password_hash, body.current):
                raise ApiError(403, "Wrong current password")
            if len(body.new) < 8:
                raise ApiError(400, "New password must be at least 8 characters")
            # A new secret signs out every other session.
            app.state.cfg = save_settings(
                old, {"password_hash": hash_password(body.new), "session_secret": secrets.token_urlsafe(32)}
            )
            set_session_cookie(response, issue_session(app.state.cfg))
            notify("info", "settings", "Web password changed")

    @api.post("/settings/test-email", status_code=204)
    def test_email(body: SendTestEmail):
        try:
            send(app.state.cfg, [body.to], "Print Hub test email", [])
        except InvalidRecipient as e:
            raise ApiError(400, f"Invalid recipient: {e}") from None
        except MailError as e:
            raise ApiError(502, str(e)) from None

    @api.get("/settings/system")
    def get_system():
        return read_system(cfg.data_dir)

    @api.put("/settings/system", status_code=202)
    def put_system(body: SystemSwitches):
        with settings_lock:
            before = read_system(cfg.data_dir)["requested"]
            request_system(cfg.data_dir, body.share, body.expose_usb)
            if before["share"] != body.share:
                title = "Sharing turned on" if body.share else "Sharing turned off"
                notify("info", "settings", title)
            if before["expose_usb"] != body.expose_usb:
                title = "Direct printer exposure turned on" if body.expose_usb else "Direct printer exposure turned off"
                notify("info", "settings", title)
            return read_system(cfg.data_dir)

    def channel_at(state: dict, channel_id: str) -> int:
        for i, channel in enumerate(state["channels"]):
            if channel["id"] == channel_id:
                return i
        raise ApiError(404, "No such channel")

    def load_for_write() -> dict:
        try:
            return notification_store.load_strict(cfg.data_dir)
        except notification_store.StoreError as e:
            raise ApiError(409, str(e)) from None

    @api.get("/notify/channels")
    def get_channels():
        return [
            notification_store.public(c) | {"result": app.state.notifier.results.get(c["id"])}
            for c in notification_store.load(cfg.data_dir)["channels"]
        ]

    @api.post("/notify/channels", status_code=201)
    def add_channel(body: dict):
        with notify_lock:
            state = load_for_write()
            try:
                channel = notification_store.validate_channel({**body, "id": secrets.token_urlsafe(8)})
            except ValueError as e:
                raise ApiError(400, str(e)) from None
            state["channels"].append(channel)
            notification_store.save(cfg.data_dir, state)
            notify("info", "settings", f"Notification channel {channel['name']} added")
            return notification_store.public(channel)

    @api.put("/notify/channels/{channel_id}")
    def change_channel(channel_id: str, body: dict):
        with notify_lock:
            state = load_for_write()
            i = channel_at(state, channel_id)
            try:
                channel = state["channels"][i] = notification_store.merge_edit(state["channels"][i], body)
            except ValueError as e:
                raise ApiError(400, str(e)) from None
            notification_store.save(cfg.data_dir, state)
            notify("info", "settings", f"Notification channel {channel['name']} changed")
            return notification_store.public(channel)

    @api.delete("/notify/channels/{channel_id}", status_code=204)
    def remove_channel(channel_id: str):
        with notify_lock:
            state = load_for_write()
            channel = state["channels"].pop(channel_at(state, channel_id))
            notification_store.save(cfg.data_dir, state)
            notify("info", "settings", f"Notification channel {channel['name']} removed")

    @api.post("/notify/channels/{channel_id}/test", status_code=204)
    def test_channel(channel_id: str):
        state = notification_store.load(cfg.data_dir)
        channel = state["channels"][channel_at(state, channel_id)]
        try:
            senders.send(channel, senders.test_event(channel["name"]), app.state.cfg)
        except senders.SendError as e:
            app.state.notifier.record(channel_id, str(e))
            raise ApiError(502, str(e)) from None
        app.state.notifier.record(channel_id)

    @api.get("/notify/silences")
    def get_silences():
        return notification_store.active_silences(notification_store.load(cfg.data_dir)["silences"], datetime.now(timezone.utc))

    @api.post("/notify/silences", status_code=201)
    def add_silence(body: dict):
        now = datetime.now(timezone.utc)
        with notify_lock:
            state = load_for_write()
            try:
                silence = notification_store.new_silence(body.get("match"), body.get("value"), body.get("hours"), now)
            except ValueError as e:
                raise ApiError(400, str(e)) from None
            state["silences"] = [*notification_store.active_silences(state["silences"], now), silence]
            notification_store.save(cfg.data_dir, state)
            return silence

    @api.delete("/notify/silences/{silence_id}", status_code=204)
    def remove_silence(silence_id: str):
        with notify_lock:
            state = load_for_write()
            live = notification_store.active_silences(state["silences"], datetime.now(timezone.utc))
            state["silences"] = [s for s in live if s.get("id") != silence_id]
            if len(state["silences"]) == len(live):
                raise ApiError(404, "No such silence")
            notification_store.save(cfg.data_dir, state)

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
