# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import io
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

import cups
import pytest
from fastapi.testclient import TestClient
from PIL import Image

# Fixtures and helpers from earlier tasks; pytest registers imported fixtures.
from test_mail import Recorder, server_tls, smtp_server, tls_files  # noqa: F401
from test_scanning import fake_scanimage, make_png

from printhub import convert
from printhub.app import MAX_UPLOAD, create_app
from printhub.auth import COOKIE, issue_session
from printhub.documents import Crop, DocStore, Page, PrintRecord
from printhub.events import EventLog
from printhub.middleware import MAX_BODY
from printhub.scanning import Scanner

ORIGIN = "https://print.example.com"
PLENTY = 10**15


class FakeCups:
    def __init__(self):
        self.printed = []
        self.cancelled = []
        self.active = set()
        self.state, self.reasons = 3, ["none"]
        self.jobs = {}  # job id -> attributes, as getJobs(which_jobs="all") returns them

    def getPrinterAttributes(self, queue, requested_attributes=None):
        return {
            "printer-state": self.state,
            "printer-state-reasons": self.reasons,
            "printer-is-accepting-jobs": True,
            "media-supported": ["iso_a4_210x297mm"],
            "print-quality-supported": [3, 4, 5],
            "print-color-mode-supported": ["color", "monochrome"],
            "orientation-requested-supported": [3, 4],
            "sides-supported": ["one-sided", "two-sided-long-edge"],
        }

    def printFile(self, queue, path, title, options):
        self.printed.append((queue, path, title, options))
        return 41 + len(self.printed)

    def cancelJob(self, job_id):
        self.cancelled.append(job_id)

    def getJobs(self, which_jobs, requested_attributes=None):
        if which_jobs == "all":
            return self.jobs
        return {j: {"job-printer-uri": "ipp://localhost/printers/OfficeJet"} for j in self.active}


@pytest.fixture
def fake_cups():
    return FakeCups()


@pytest.fixture
def space():
    """space["free"]() is the free byte count the app sees."""
    return {"free": lambda: PLENTY}


@pytest.fixture
def make_client(cfg, fake_cups, space):
    def make(cfg=cfg, login=True, cups_conn_factory=lambda: fake_cups):
        app = create_app(
            cfg,
            cups_conn_factory=cups_conn_factory,
            scanner=Scanner(),
            free_bytes=lambda: space["free"](),
        )
        client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}, follow_redirects=False)
        if login:
            assert client.post("/api/login", json={"password": "secret"}).status_code == 204
        return client

    return make


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def store(cfg):
    return DocStore(cfg.data_dir / "docs")


def jpeg_bytes(size=(64, 48), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def scan_doc(store, n=3, title="Scan"):
    doc = store.create("scan", title)
    for i in range(n):
        page = Page(id=f"page{i}")
        (store.path(doc.id) / "pages" / f"{page.id}.jpg").write_bytes(jpeg_bytes())
        doc.pages.append(page)
    store.save(doc)
    return doc


# Auth


def test_api_requires_session(make_client):
    client = make_client(login=False)
    assert client.get("/api/docs").status_code == 401


def test_login_wrong_password_then_rate_limited(make_client):
    client = make_client(login=False)
    for _ in range(5):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "secret"}).status_code == 429


def test_foreign_origin_rejected_even_with_session(client):
    r = client.post("/api/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_logout_clears_session(client):
    assert client.post("/api/logout").status_code == 204
    assert client.get("/api/docs").status_code == 401


# Upload


def test_upload_jpeg_unicode_name(client, store):
    r = client.post("/api/uploads", files={"file": ("Relatório nº 3.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 200, r.text
    doc_id = r.json()["doc_id"]
    doc = client.get(f"/api/docs/{doc_id}").json()
    assert doc["title"] == "Relatório nº 3"
    assert doc["source_name"] == "Relatório nº 3.jpg"
    assert (store.path(doc_id) / "print.pdf").is_file()
    assert doc["preview_count"] >= 1
    assert len(doc["pdf_pages"]) == 1
    preview = client.get(f"/api/docs/{doc_id}/preview/1")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert client.get(f"/api/docs/{doc_id}/preview/2").status_code == 404
    listing = client.get("/api/docs", params={"kind": "print"}).json()
    assert listing == [
        {"id": doc_id, "kind": "print", "title": "Relatório nº 3", "modified": doc["modified"], "page_count": 1}
    ]


BOUNDARY = "printhubboundary"
MULTIPART = {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}
MIB = b"\0" * (1024 * 1024)


def multipart_head(filename="big.pdf") -> bytes:
    return (
        f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()


def test_upload_file_over_limit_is_rejected_and_removed(client, store):
    # Under the whole-body cap (multipart allowance), so the per-file copy limit trips.
    def body():
        yield multipart_head()
        for _ in range(MAX_UPLOAD // len(MIB)):
            yield MIB
        yield b"\0"
        yield f"\r\n--{BOUNDARY}--\r\n".encode()

    r = client.post("/api/uploads", content=body(), headers=MULTIPART)
    assert r.status_code == 413
    assert [p for p in store.root.iterdir() if p.name != ".locks"] == []


def asgi_post(app, path: str, headers: dict[str, str]) -> tuple[int, int]:
    """POST an endless chunked body straight through ASGI; returns (status, chunks the app read)."""
    read = 0
    statuses = []

    async def receive():
        nonlocal read
        read += 1
        return {"type": "http.request", "body": multipart_head() if read == 1 else MIB, "more_body": True}

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in {"Origin": ORIGIN, **MULTIPART, **headers}.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("print.example.com", 443),
    }
    asyncio.run(app(scope, receive, send))
    (status,) = statuses
    return status, read


def test_chunked_body_without_session_is_refused_unread(client, store, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "spool"))
    (tmp_path / "spool").mkdir()
    assert asgi_post(client.app, "/api/uploads", {}) == (401, 0)
    assert list((tmp_path / "spool").iterdir()) == []


def test_chunked_body_over_cap_aborts_reading(client, cfg, store, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "spool"))
    (tmp_path / "spool").mkdir()
    status, read = asgi_post(client.app, "/api/uploads", {"Cookie": f"{COOKIE}={issue_session(cfg)}"})
    assert status == 413
    assert read <= MAX_BODY // len(MIB) + 2
    assert list((tmp_path / "spool").iterdir()) == []
    assert list(store.root.iterdir()) == []


def test_content_length_checked_before_reading(client, cfg, store):
    cookie = {"Cookie": f"{COOKIE}={issue_session(cfg)}"}
    assert asgi_post(client.app, "/api/uploads", {**cookie, "Content-Length": str(MAX_BODY + 1)}) == (413, 0)
    assert asgi_post(client.app, "/api/uploads", {**cookie, "Content-Length": "12abc"}) == (400, 0)
    assert list(store.root.iterdir()) == []


def test_disk_error_while_writing_is_disk_full(client, store, monkeypatch):
    real_open = Path.open

    def full_disk(self, mode="r", *args, **kwargs):
        if self.name.startswith("source") and "w" in mode:
            return real_open(Path("/dev/full"), mode, *args, **kwargs)
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", full_disk)
    r = client.post("/api/uploads", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 507
    assert r.json() == {"error": "Disk full"}
    assert [p for p in store.root.iterdir() if p.name != ".locks"] == []


def test_upload_exe_unsupported(client, store):
    exe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + b"\0" * 200
    r = client.post("/api/uploads", files={"file": ("setup.exe", exe, "application/octet-stream")})
    assert r.status_code == 415
    assert r.json()["error"].startswith("Unsupported file type: ")
    assert [p for p in store.root.iterdir() if p.name != ".locks"] == []


def test_upload_conversion_error_keeps_doc_for_retry(client, store):
    r = client.post("/api/uploads", files={"file": ("broken.pdf", b"%PDF-1.4\nnot really", "application/pdf")})
    assert r.status_code == 422
    doc_id = r.json()["doc_id"]
    assert r.json()["error"]
    assert store.get(doc_id).kind == "print"
    assert client.post(f"/api/docs/{doc_id}/convert").status_code == 422


def test_unconverted_doc_stays_readable_and_unprintable(client):
    r = client.post("/api/uploads", files={"file": ("broken.pdf", b"%PDF-1.4\nnot really", "application/pdf")})
    doc_id = r.json()["doc_id"]
    doc = client.get(f"/api/docs/{doc_id}")
    assert doc.status_code == 200
    assert doc.json()["preview_count"] == 0
    assert doc.json()["pdf_pages"] == []
    assert client.post(f"/api/docs/{doc_id}/print", json={}).status_code in (400, 422)


def test_preview_failure_after_some_pages_leaves_no_previews(client, monkeypatch):
    # pdftoppm can write early pages before failing on a later one or a full disk.
    def half_rendered(pdf, out_dir, width=600):
        (out_dir / "page-1.png").write_bytes(b"partial")
        raise convert.ConversionError("pdftoppm failed on page 2")

    monkeypatch.setattr("printhub.app.convert.render_preview", half_rendered)
    r = client.post("/api/uploads", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 422
    doc = client.get(f"/api/docs/{r.json()['doc_id']}").json()
    assert doc["preview_count"] == 0
    assert doc["pdf_pages"] == []


def test_low_space_deletes_old_doc_then_uploads(client, store, space):
    old = store.create("print", "old")
    old_dir = store.path(old.id)
    # Free space appears once the old document is gone.
    space["free"] = lambda: 0 if old_dir.exists() else PLENTY
    r = client.post("/api/uploads", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 200, r.text
    assert not old_dir.exists()


def test_disk_full_when_only_busy_docs(client, store, space):
    busy = store.create("scan", "busy")
    busy.scanning = True
    store.save(busy)
    space["free"] = lambda: 0
    r = client.post("/api/uploads", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 507
    assert r.json() == {"error": "Disk full"}
    assert [d.id for d in store.list()] == [busy.id]


def test_active_print_job_keeps_doc_busy(client, store, space, fake_cups):
    doc = store.create("print", "printing")
    doc.prints.append(PrintRecord(job_id=7, at="2026-10-21T00:00:00+00:00", options={}))
    store.save(doc)
    fake_cups.active = {7}
    space["free"] = lambda: 0
    assert client.delete(f"/api/docs/{doc.id}").status_code == 409
    assert client.post("/api/uploads", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")}).status_code == 507
    fake_cups.active = set()
    assert client.delete(f"/api/docs/{doc.id}").status_code == 204
    assert client.get(f"/api/docs/{doc.id}").status_code == 404


# Editing


def test_patch_reorders_deletes_rotates_and_snaps_crop(client, store):
    doc = scan_doc(store)
    pages_dir = store.path(doc.id) / "pages"
    r = client.patch(
        f"/api/docs/{doc.id}",
        json={
            "title": "Renamed",
            "pages": [
                {"id": "page2", "rotation": 0, "crop": None},
                {"id": "page0", "rotation": 90, "crop": {"x": 5, "y": 21, "w": 20, "h": 20}},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Renamed"
    assert [p["id"] for p in body["pages"]] == ["page2", "page0"]
    assert body["pages"][1]["rotation"] == 90
    # 4:2:0 JPEG: 16 px iMCU; left/top snap down, right/bottom edges stay put
    assert body["pages"][1]["crop"] == {"x": 0, "y": 16, "w": 25, "h": 25}
    assert not (pages_dir / "page1.jpg").exists()
    assert (pages_dir / "page0.jpg").exists()
    assert store.get(doc.id).pages[1].crop.y == 16


def test_patch_rejects_bad_rotation_and_unknown_page(client, store):
    doc = scan_doc(store, n=1)
    r = client.patch(f"/api/docs/{doc.id}", json={"pages": [{"id": "page0", "rotation": 45}]})
    assert r.status_code == 400
    assert "error" in r.json()
    r = client.patch(f"/api/docs/{doc.id}", json={"pages": [{"id": "nope", "rotation": 0}]})
    assert r.status_code == 400


def test_patch_and_delete_conflict_while_scanning(client, store):
    doc = scan_doc(store, n=1)
    doc.scanning = True
    store.save(doc)
    assert client.patch(f"/api/docs/{doc.id}", json={"title": "x"}).status_code == 409
    assert client.delete(f"/api/docs/{doc.id}").status_code == 409


def test_page_image_full_and_thumb(client, store):
    doc = store.create("scan", "big")
    (store.path(doc.id) / "pages" / "p.jpg").write_bytes(jpeg_bytes(size=(1600, 1200)))
    doc.pages.append(Page(id="p"))
    store.save(doc)
    full = client.get(f"/api/docs/{doc.id}/pages/p")
    assert full.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(full.content)).size == (1600, 1200)
    thumb = client.get(f"/api/docs/{doc.id}/pages/p", params={"size": "thumb"})
    assert Image.open(io.BytesIO(thumb.content)).size == (400, 300)
    assert (store.path(doc.id) / "thumbs" / "p.jpg").is_file()
    assert client.get(f"/api/docs/{doc.id}/pages/missing").status_code == 404
    page = client.get(f"/api/docs/{doc.id}").json()["pages"][0]
    assert page["page_info"] == {"width": 1600, "height": 1200, "mcu": [16, 16], "dpi": [96, 96]}


def test_page_image_rotated(client, store, cfg):
    doc = store.create("scan", "turned")
    (store.path(doc.id) / "pages" / "p.jpg").write_bytes(jpeg_bytes(size=(1600, 1200)))
    doc.pages.append(Page(id="p", rotation=90, crop=Crop(0, 0, 100, 100)))
    store.save(doc)
    r = client.get(f"/api/docs/{doc.id}/pages/p", params={"size": "full", "rotated": "1"})
    assert r.headers["content-type"] == "image/jpeg"
    # rotated only: the crop is what the editor changes, so it is not applied
    assert Image.open(io.BytesIO(r.content)).size == (1200, 1600)
    assert not any((cfg.data_dir / "tmp").iterdir())


# Export


def test_export_pdf_content_disposition(client, store):
    doc = scan_doc(store, n=2, title="Relatório nº 3")
    r = client.get(f"/api/docs/{doc.id}/export", params={"format": "pdf"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    cd = r.headers["content-disposition"]
    assert "filename*=UTF-8''Relat%C3%B3rio%20n%C2%BA%203.pdf" in cd
    assert 'filename="Relat_rio n_ 3.pdf"' in cd


def test_export_jpeg_and_errors(client, store):
    doc = scan_doc(store, n=1, title="One")
    r = client.get(f"/api/docs/{doc.id}/export", params={"format": "jpeg"})
    assert r.headers["content-type"] == "image/jpeg"
    assert "One.jpg" in r.headers["content-disposition"]
    assert client.get(f"/api/docs/{doc.id}/export", params={"format": "tiff"}).status_code == 400
    empty = store.create("scan", "empty")
    r = client.get(f"/api/docs/{empty.id}/export", params={"format": "pdf"})
    assert r.status_code == 400
    assert r.json() == {"error": "Document has no pages"}


# Print

PRINT_OPTS = {
    "media": "iso_a4_210x297mm",
    "print_quality": "4",
    "print_color_mode": "color",
    "orientation_requested": "3",
    "sides": "two-sided-long-edge",
}


def test_print_scan_doc(client, store, fake_cups):
    doc = scan_doc(store, n=2)
    r = client.post(f"/api/docs/{doc.id}/print", json=PRINT_OPTS)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    ((queue, path, _, options),) = fake_cups.printed
    assert queue == "OfficeJet"
    assert Path(path).name == "print.pdf"
    assert not Path(path).exists()  # request-private scan PDF is removed after CUPS reads it
    assert options["sides"] == "two-sided-long-edge"
    assert options["copies"] == "1"
    assert [p.job_id for p in store.get(doc.id).prints] == [job_id]

    fake_cups.jobs = {job_id: {"job-printer-uri": "ipp://localhost/printers/OfficeJet", "job-state": 5}}
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 204
    assert fake_cups.cancelled == [job_id]


def test_print_unsupported_sides(client, store, fake_cups):
    doc = scan_doc(store, n=1)
    r = client.post(f"/api/docs/{doc.id}/print", json={**PRINT_OPTS, "sides": "two-sided-short-edge"})
    assert r.status_code == 400
    assert "sides" in r.json()["error"]
    assert fake_cups.printed == []


def test_printer_info_and_cups_down(client, make_client):
    info = client.get("/api/printer").json()
    assert info["state"] == "idle"
    assert info["supported"]["sides"] == ["one-sided", "two-sided-long-edge"]

    def down():
        raise RuntimeError("failed to connect to server")

    r = make_client(cups_conn_factory=down).get("/api/printer")
    assert r.status_code == 503
    assert r.json() == {"error": "CUPS unavailable"}


# Scans


def test_scan_progress_and_busy(client, tmp_path, monkeypatch, store):
    go = tmp_path / "go"
    png = make_png(tmp_path / "src.png", (9, 9, 9))
    fake_scanimage(tmp_path, monkeypatch, f'while [ ! -e {go} ]; do sleep 0.05; done\ncp {png} "$out/page.png"')
    caps = client.get("/api/scanner").json()
    assert caps["sources"] == ["flatbed", "adf"]
    assert caps["bed_mm"] == [200.0, 200.0]
    settings = {"source": "flatbed", "resolution": 300, "mode": "color", "area": "a4"}
    r = client.post("/api/scans", json={"settings": settings})
    assert r.status_code == 202
    doc_id = r.json()["doc_id"]
    doc = client.get(f"/api/docs/{doc_id}").json()
    assert doc["scanning"] is True
    assert doc["title"].startswith("Scan ")

    busy = client.post("/api/scans", json={"settings": settings})
    assert busy.status_code == 409
    assert busy.json() == {"error": "Scanner busy"}
    assert [d.id for d in store.list()] == [doc_id]

    go.touch()
    for _ in range(200):
        doc = client.get(f"/api/docs/{doc_id}").json()
        if not doc["scanning"]:
            break
        time.sleep(0.05)
    assert doc["scanning"] is False
    assert doc["scan_error"] is None
    assert len(doc["pages"]) == 1
    assert doc["pages"][0]["page_info"]["width"] == 40

    # Another scan appends to the same document.
    r = client.post("/api/scans", json={"settings": settings, "doc_id": doc_id})
    assert r.json() == {"doc_id": doc_id}
    for _ in range(200):
        if not store.get(doc_id).scanning:
            break
        time.sleep(0.05)
    assert len(store.get(doc_id).pages) == 2


# Email


def test_email_then_recipients(make_client, cfg, store, smtp_server, server_tls, tls_files, monkeypatch):
    rec = Recorder()
    port = smtp_server(rec, tls_context=server_tls)
    # The app uses ssl.create_default_context(); trust the test certificate through OpenSSL's env.
    monkeypatch.setenv("SSL_CERT_FILE", str(tls_files[0]))
    client = make_client(cfg=replace(cfg, smtp_port=port, smtp_user=""))
    doc = scan_doc(store, n=2, title="Two\r\npages")
    r = client.post(
        f"/api/docs/{doc.id}/email",
        json={"to": ["a@example.com"], "subject": "Hello", "format": "jpeg"},
    )
    assert r.status_code == 204, r.text
    (env,) = rec.envelopes
    assert env.rcpt_tos == ["a@example.com"]
    assert b"Two pages-01.jpg" in env.content
    assert client.get("/api/recipients").json() == ["a@example.com"]
    (mail,) = store.get(doc.id).mails
    assert (mail.to, mail.subject, mail.format) == (["a@example.com"], "Hello", "jpeg")

    r = client.post(f"/api/docs/{doc.id}/email", json={"to": ["not an address"], "subject": "s", "format": "pdf"})
    assert r.status_code == 400


@pytest.mark.parametrize(
    ("format", "sizes"),
    [
        ("pdf", [25 * 1024 * 1024 + 1]),
        ("jpeg", [25 * 1024 * 1024 + 1]),
        ("jpeg", [14 * 1024 * 1024, 14 * 1024 * 1024]),
    ],
)
def test_email_rejects_oversized_exports_before_reading(
    client, store, cfg, monkeypatch, format, sizes
):
    doc = scan_doc(store, n=len(sizes))
    remaining = iter(sizes)

    def exported_file(*args):
        with args[-1].open("wb") as out:
            out.truncate(next(remaining))

    monkeypatch.setattr("printhub.app.edits.export_pdf", exported_file)
    monkeypatch.setattr("printhub.app.edits.apply_edits", exported_file)

    def reject_read(path):
        raise AssertionError(f"Oversized attachments must not be read: {path}")

    monkeypatch.setattr(Path, "read_bytes", reject_read)
    response = client.post(
        f"/api/docs/{doc.id}/email",
        json={"to": ["a@example.com"], "subject": "Too large", "format": format},
    )
    assert response.status_code == 413
    assert store.get(doc.id).mails == []
    assert list((cfg.data_dir / "tmp").iterdir()) == []


# Share


def test_share_without_session_then_claim(make_client, cfg, store):
    anon = make_client(login=False)
    r = anon.post("/share", files={"file": ("photo.jpg", jpeg_bytes(), "image/jpeg")}, headers={"Origin": ""})
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/login.html?share=")
    token = location.split("share=", 1)[1]
    assert len(list((cfg.data_dir / "pending").iterdir())) == 1

    assert anon.post("/api/login", json={"password": "secret"}).status_code == 204
    r = anon.post(f"/api/share/{token}/claim")
    assert r.status_code == 200, r.text
    doc = store.get(r.json()["doc_id"])
    assert doc.title == "photo"
    assert list((cfg.data_dir / "pending").iterdir()) == []
    assert anon.post(f"/api/share/{token}/claim").status_code == 410


def test_share_with_session_redirects_to_print(client, store):
    r = client.post("/share", files={"file": ("photo.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 303
    (doc,) = store.list()
    assert r.headers["location"] == f"/print.html?doc={doc.id}"


def share_anon(client):
    return client.post("/share", files={"file": ("photo.jpg", jpeg_bytes(), "image/jpeg")})


def test_anonymous_share_never_evicts(make_client, store, space):
    old = store.create("print", "old")
    space["free"] = lambda: 0
    r = share_anon(make_client(login=False))
    assert r.status_code == 507
    assert [d.id for d in store.list()] == [old.id]


def test_anonymous_shares_are_capped(make_client, cfg):
    anon = make_client(login=False)
    for _ in range(5):
        assert share_anon(anon).status_code == 303
    assert share_anon(anon).status_code == 429
    assert len(list((cfg.data_dir / "pending").iterdir())) == 5


def test_failed_claim_keeps_share_for_retry(make_client, cfg, space):
    anon = make_client(login=False)
    token = share_anon(anon).headers["location"].split("share=", 1)[1]
    assert anon.post("/api/login", json={"password": "secret"}).status_code == 204
    space["free"] = lambda: 0
    assert anon.post(f"/api/share/{token}/claim").status_code == 507
    space["free"] = lambda: PLENTY
    assert anon.post(f"/api/share/{token}/claim").status_code == 200


def test_claim_with_missing_file_is_gone(make_client, cfg):
    anon = make_client(login=False)
    token = share_anon(anon).headers["location"].split("share=", 1)[1]
    for f in (cfg.data_dir / "pending").iterdir():
        f.unlink()
    assert anon.post("/api/login", json={"password": "secret"}).status_code == 204
    assert anon.post(f"/api/share/{token}/claim").status_code == 410


def test_startup_clears_pending(cfg, make_client):
    pending = cfg.data_dir / "pending"
    pending.mkdir(parents=True)
    (pending / "orphan").write_bytes(b"x")
    make_client(login=False)
    assert list(pending.iterdir()) == []


def test_static_files_are_revalidated_so_a_deploy_never_serves_stale_scripts(client):
    for path in ("/", "/print.html", "/css/app.css", "/js/print.js"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache", path
    etag = client.get("/js/print.js").headers["etag"]
    assert client.get("/js/print.js", headers={"If-None-Match": etag}).status_code == 304


# Status, jobs and notifications

OURS = "ipp://localhost/printers/OfficeJet"


def test_status_reports_printer_and_unread(client, make_client, fake_cups, cfg):
    fake_cups.state, fake_cups.reasons = 4, ["media-jam-error", "job-printing", "media-jam-report"]
    EventLog(cfg.data_dir).append("error", "printer", "Paper jam")
    s = client.get("/api/status").json()
    assert s["printer"] == {
        "state": "processing", "accepting": True, "state_reasons": ["media-jam-error", "job-printing", "media-jam-report"],
        "problems": [{"label": "Paper jam", "level": "error"}],
    }
    assert (s["error"], s["unread"], s["latest_event"]) == (None, 1, 1)

    def down():
        raise RuntimeError("cupsd is not running")

    s = make_client(cups_conn_factory=down).get("/api/status")
    assert s.status_code == 200
    assert s.json()["printer"] is None and s.json()["error"] == "CUPS unavailable"


def test_jobs_lists_external_jobs_and_links_web_app_jobs(client, store, fake_cups):
    doc = scan_doc(store, n=1)
    job_id = client.post(f"/api/docs/{doc.id}/print", json=PRINT_OPTS).json()["job_id"]
    fake_cups.jobs = {
        job_id: {"job-printer-uri": OURS, "job-state": 5, "job-name": "Scan"},
        900: {"job-printer-uri": OURS, "job-state": 3, "job-name": "boarding pass", "job-originating-user-name": "phone",
              "job-state-reasons": ["job-queued", "printer-stopped"]},
        **{n: {"job-printer-uri": OURS, "job-state": 9} for n in range(1, 26)},
        950: {"job-printer-uri": "ipp://localhost/printers/Other", "job-state": 5},
    }
    jobs = client.get("/api/jobs").json()
    active = {j["id"]: j for j in jobs["active"]}
    assert set(active) == {job_id, 900}
    assert active[job_id]["doc_id"] == doc.id
    assert (active[900]["doc_id"], active[900]["name"], active[900]["user"]) == (None, "boarding pass", "phone")
    assert active[900]["reason_labels"] == ["Printer stopped"]
    assert [j["id"] for j in jobs["recent"]] == list(range(25, 5, -1))


def test_cancel_refuses_another_queues_job(client, fake_cups):
    fake_cups.jobs = {
        5: {"job-printer-uri": "ipp://localhost/printers/Other", "job-state": 5},
        6: {"job-printer-uri": OURS, "job-state": 5},
    }
    assert client.post("/api/jobs/5/cancel").status_code == 404
    assert client.post("/api/jobs/7/cancel").status_code == 404
    assert fake_cups.cancelled == []
    assert client.post("/api/jobs/6/cancel").status_code == 204
    assert fake_cups.cancelled == [6]


def test_notifications_page_and_mark_read(client, cfg):
    log = EventLog(cfg.data_dir)
    for title in ("a", "b", "c"):
        log.append("info", "job", title, job_id=1)
    page = client.get("/api/notifications", params={"limit": 2}).json()
    assert [e["title"] for e in page["events"]] == ["c", "b"]
    assert page["events"][0] | {"at": None} == {
        "id": 3, "at": None, "level": "info", "kind": "job", "title": "c", "detail": "", "job_id": 1, "doc_id": None,
        "read": False,
    }
    assert (page["unread"], page["latest"]) == (3, 3)
    assert [e["id"] for e in client.get("/api/notifications", params={"before": 2}).json()["events"]] == [1]

    assert client.post("/api/notifications/read", json={"ids": [3]}).status_code == 204
    assert client.get("/api/status").json()["unread"] == 2
    assert client.post("/api/notifications/read", json={"up_to": 3}).status_code == 204
    assert client.get("/api/status").json()["unread"] == 0
    assert client.post("/api/notifications/read", json={}).status_code == 422


def test_conversion_and_scan_failures_become_events(client, cfg, tmp_path, monkeypatch):
    r = client.post("/api/uploads", files={"file": ("broken.pdf", b"%PDF-1.4\nnot really", "application/pdf")})
    doc_id = r.json()["doc_id"]
    fake_scanimage(tmp_path, monkeypatch, 'echo "scanimage: open of device test failed: Device busy" >&2\nexit 1')
    scan_id = client.post("/api/scans", json={"settings": {"source": "flatbed"}}).json()["doc_id"]
    for _ in range(200):
        if not client.get(f"/api/docs/{scan_id}").json()["scanning"]:
            break
        time.sleep(0.05)
    events = [(e.kind, e.level, e.doc_id) for e, _ in reversed(EventLog(cfg.data_dir).page())]
    assert events == [("conversion", "error", doc_id), ("scan", "error", scan_id)]


def test_failed_conversion_keeps_retry_when_the_event_log_cannot_be_written(client, monkeypatch):
    def full(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(EventLog, "append", full)
    r = client.post("/api/uploads", files={"file": ("broken.pdf", b"%PDF-1.4\nnot really", "application/pdf")})
    assert r.status_code == 422 and r.json()["doc_id"]


def test_jobs_link_documents_printed_or_deleted_after_the_first_listing(client, store, fake_cups):
    assert client.get("/api/jobs").json() == {"active": [], "recent": []}
    doc = scan_doc(store, n=1)
    job_id = client.post(f"/api/docs/{doc.id}/print", json=PRINT_OPTS).json()["job_id"]
    fake_cups.jobs = {job_id: {"job-printer-uri": OURS, "job-state": 9}}
    assert client.get("/api/jobs").json()["recent"][0]["doc_id"] == doc.id
    store.delete(doc.id)
    assert client.get("/api/jobs").json()["recent"][0]["doc_id"] is None


def test_the_running_app_records_events(cfg, fake_cups, monkeypatch):
    # watch=True is what the real service runs; its watcher must actually record job outcomes.
    monkeypatch.setattr("printhub.app.WATCH_INTERVAL", 0.02)
    fake_cups.jobs = {7: {"job-printer-uri": OURS, "job-state": 5}}
    app = create_app(cfg, cups_conn_factory=lambda: fake_cups, scanner=Scanner(), free_bytes=lambda: PLENTY, watch=True)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as c:
        assert c.post("/api/login", json={"password": "secret"}).status_code == 204
        time.sleep(0.1)  # baseline
        fake_cups.jobs = {7: {"job-printer-uri": OURS, "job-state": 9}}
        for _ in range(100):
            if c.get("/api/status").json()["unread"]:
                break
            time.sleep(0.02)
        assert [e["title"] for e in c.get("/api/notifications").json()["events"]] == ["Job 7 completed"]
    assert not any(t.name == "watcher" and t.is_alive() for t in threading.enumerate())


def test_orientation_auto_lets_cups_rotate_and_explicit_orientation_does_not(client, store, fake_cups):
    # CUPS turns a page whose shape does not match the sheet unless told not to, which made Portrait and
    # Landscape print the same sheet.
    doc = scan_doc(store, n=1)
    for orientation in ("auto", "3", "4"):
        r = client.post(f"/api/docs/{doc.id}/print", json={**PRINT_OPTS, "orientation_requested": orientation})
        assert r.status_code == 200, r.text
    auto, portrait, landscape = (options for *_, options in fake_cups.printed)
    # Auto is portrait with turning left on, so a landscape queue default cannot turn pages the other way
    assert (auto["orientation-requested"], "pdfAutoRotate" in auto) == ("3", False)
    assert (portrait["orientation-requested"], portrait["pdfAutoRotate"]) == ("3", "false")
    assert (landscape["orientation-requested"], landscape["pdfAutoRotate"]) == ("4", "false")
