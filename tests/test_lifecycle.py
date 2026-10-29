# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import io
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from test_app import (PLENTY, PRINT_OPTS, client, fake_cups, make_client, scan_doc, space, store)
from printhub import edits, printing, retention, scanning
from printhub.documents import DocStore, PrintRecord
from printhub.mail import RecentRecipients
from printhub.middleware import MAX_BODY
from printhub.scanning import Scanner, ScanSettings


def test_append_pins_target_before_reclamation(client, store, space, monkeypatch):
    target = scan_doc(store, 1)
    other = store.create("print", "can reclaim")
    space["free"] = lambda: 0 if store.path(other.id).exists() else PLENTY
    monkeypatch.setattr(Scanner, "start", lambda *args: None)
    response = client.post("/api/scans", json={"doc_id": target.id, "settings": {"source": "flatbed"}})
    assert response.status_code == 202, response.text
    assert store.path(target.id).exists()
    assert not store.path(other.id).exists()


def test_export_pins_originals_against_parallel_reclamation(client, store, space, monkeypatch):
    target = scan_doc(store, 1)
    entered, release = threading.Event(), threading.Event()
    original = edits.export_pdf

    def export(*args):
        entered.set()
        assert release.wait(10)
        original(*args)

    monkeypatch.setattr(edits, "export_pdf", export)
    with ThreadPoolExecutor() as pool:
        result = pool.submit(client.get, f"/api/docs/{target.id}/export?format=pdf")
        assert entered.wait(10)
        space["free"] = lambda: 0
        try:
            response = client.post("/api/uploads", files={"file": ("a.jpg", b"unused")})
            assert response.status_code == 507
            assert store.path(target.id).exists()
        finally:
            release.set()
        assert result.result(10).status_code == 200


def test_parallel_prints_have_private_pdfs_and_merge_history(client, store, monkeypatch):
    doc = scan_doc(store, 1)
    entered = threading.Barrier(3)
    release = threading.Event()
    paths = []

    def submit(conn, queue, pdf, title, opts, info):
        paths.append(pdf)
        entered.wait(10)
        assert release.wait(10)
        assert pdf.read_bytes().startswith(b"%PDF-")
        return 100 + len(str(pdf))  # identity is irrelevant; both records must survive

    monkeypatch.setattr(printing, "submit", submit)
    with ThreadPoolExecutor() as pool:
        jobs = [pool.submit(client.post, f"/api/docs/{doc.id}/print", json=PRINT_OPTS) for _ in range(2)]
        entered.wait(10)
        try:
            assert client.patch(f"/api/docs/{doc.id}", json={"title": "renamed while submitting"}).status_code == 200
        finally:
            release.set()
        assert [job.result(10).status_code for job in jobs] == [200, 200]
    assert len(set(paths)) == 2
    assert all(not path.exists() for path in paths)
    current = store.get(doc.id)
    assert current.title == "renamed while submitting"
    assert len(current.prints) == 2
    assert not (store.path(doc.id) / "print.pdf").exists()


def test_use_guard_blocks_deletion_in_independent_process(store):
    doc = store.create("scan", "pinned")
    script = """
import sys
from pathlib import Path
from printhub.documents import DocStore
store = DocStore(Path(sys.argv[1]))
with store.use(sys.argv[2]):
    print('ready', flush=True)
    sys.stdin.readline()
"""
    with subprocess.Popen([sys.executable, "-c", script, str(store.root), doc.id],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as child:
        try:
            assert child.stdout.readline().strip() == "ready"
            with pytest.raises(Exception) as exc:
                store.delete(doc.id)
            assert type(exc.value).__name__ == "DocBusy"
            assert store.path(doc.id).exists()
            identity = (store.root / ".locks" / f"{doc.id}.use").stat().st_ino
        finally:
            child.communicate("release\n", timeout=10)
    store.delete(doc.id)
    assert (store.root / ".locks" / f"{doc.id}.use").stat().st_ino == identity


def test_transactions_serialize_independent_process_updates(store):
    doc = store.create("scan", "before")
    script = """
import sys
from pathlib import Path
from printhub.documents import DocStore, PrintRecord
store = DocStore(Path(sys.argv[1]))
print('ready', flush=True)
sys.stdin.readline()
with store.transaction(sys.argv[2]) as doc:
    doc.prints.append(PrintRecord(7, '2026-10-21T00:00:00+00:00', {}))
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(store.root), doc.id],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        with store.transaction(doc.id) as current:
            assert child.stdout.readline().strip() == "ready"
            child.stdin.write("start\n")
            child.stdin.flush()
            current.title = "after"
        child.communicate(timeout=10)
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    current = store.get(doc.id)
    assert current.title == "after"
    assert [p.job_id for p in current.prints] == [7]


def test_retention_rechecks_state_after_selection(store, monkeypatch):
    doc = store.create("scan", "old")
    raw = store.path(doc.id) / "doc.json"
    data = json.loads(raw.read_text())
    data["modified"] = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    raw.write_text(json.dumps(data))
    original = store.list

    def stale_list():
        snapshot = original()
        with store.transaction(doc.id) as current:
            current.scanning = True
        return snapshot

    monkeypatch.setattr(store, "list", stale_list)
    assert retention.purge_expired(store, 30, datetime.now(timezone.utc), busy=lambda d: d.scanning) == []
    assert store.path(doc.id).exists()


def test_timer_protects_active_cups_jobs_and_scanning(cfg, store, fake_cups, monkeypatch):
    for scanning_flag in (True, False):
        doc = store.create("scan", "busy")
        doc.scanning = scanning_flag
        doc.prints = [PrintRecord(7, "2026-10-21T00:00:00+00:00", {})]
        store.save(doc)
        raw = store.path(doc.id) / "doc.json"
        data = json.loads(raw.read_text())
        data["modified"] = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        raw.write_text(json.dumps(data))
    fake_cups.active = {7}
    monkeypatch.setattr(retention, "load_config", lambda: cfg)
    monkeypatch.setattr(printing, "connect", lambda: fake_cups)
    retention.main()
    assert len(store.list()) == 2


def test_startup_recovers_interrupted_scan(make_client, store):
    doc = scan_doc(store, 1)
    original = store.path(doc.id) / "pages/page0.jpg"
    before = original.read_bytes()
    doc.scanning = True
    store.save(doc)
    incoming = store.path(doc.id) / "incoming"
    incoming.mkdir()
    (incoming / "partial.png").write_bytes(b"partial")
    client = make_client()
    recovered = client.get(f"/api/docs/{doc.id}").json()
    assert recovered["scanning"] is False
    assert "retry" in recovered["scan_error"].lower()
    assert original.read_bytes() == before
    assert not incoming.exists()
    assert client.patch(f"/api/docs/{doc.id}", json={"title": "retryable"}).status_code == 200


def test_thread_start_failure_rolls_back_flag_and_releases_pin(store, monkeypatch):
    doc = store.create("scan", "scan")
    scanner = Scanner()
    monkeypatch.setattr(threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("no thread")))
    with pytest.raises(RuntimeError, match="no thread"):
        scanner.start(store, doc.id, ScanSettings("flatbed"))
    assert not store.get(doc.id).scanning
    assert "retry" in store.get(doc.id).scan_error.lower()
    assert not scanner._lock.locked()
    store.delete(doc.id)


def test_recent_recipients_parallel_merges(tmp_path):
    path = tmp_path / "recipients.json"
    start = threading.Barrier(3)

    def add(addr):
        start.wait(10)
        RecentRecipients(path).add([addr])

    with ThreadPoolExecutor() as pool:
        jobs = [pool.submit(add, addr) for addr in ("a@example.com", "b@example.com")]
        start.wait(10)
        for job in jobs:
            job.result(10)
    assert set(RecentRecipients(path).list()) == {"a@example.com", "b@example.com"}


def test_mixed_dpi_page_info_reads_original_density(client, store):
    doc = scan_doc(store, 2)
    for page, dpi in zip(doc.pages, (300, 150)):
        Image.new("RGB", (600, 300)).save(store.path(doc.id) / "pages" / f"{page.id}.jpg", dpi=(dpi, dpi))
    doc.scan_resolution = 150
    store.save(doc)
    pages = client.get(f"/api/docs/{doc.id}").json()["pages"]
    assert [p["page_info"]["dpi"] for p in pages] == [[300, 300], [150, 150]]


def test_share_admission_rejects_before_receiving_and_releases_slot(make_client, cfg, monkeypatch):
    from printhub import app as app_module
    client = make_client(login=False)
    app = client.app

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        reads = []

        async def request(receive):
            sent = []
            scope = {"type": "http", "http_version": "1.1", "scheme": "https", "method": "POST",
                     "path": "/share", "query_string": b"",
                     "headers": [(b"content-type", b"multipart/form-data; boundary=test")],
                     "client": ("127.0.0.1", 1), "server": ("print.example.com", 443)}
            await app(scope, receive, lambda msg: record(sent, msg))
            return [m["status"] for m in sent if m["type"] == "http.response.start"]

        async def record(sent, msg):
            sent.append(msg)

        async def blocked():
            entered.set()
            await release.wait()
            return {"type": "http.disconnect"}

        async def unread():
            reads.append(True)
            return {"type": "http.request", "body": b"", "more_body": False}

        first = asyncio.create_task(request(blocked))
        await entered.wait()
        try:
            assert await request(unread) == [429]
            assert not reads
        finally:
            release.set()
        await first
        assert await request(unread) != [429]  # parser rejection released the slot too
        assert await request(unread) != [429]
        monkeypatch.setattr(app_module, "PENDING_MAX_COUNT", 0)
        reads.clear()
        assert await request(unread) == [429]
        assert not reads

    asyncio.run(run())


@pytest.mark.parametrize("fail_delivery", [False, True])
def test_file_delivery_holds_pin_and_releases_on_error(client, store, cfg, fail_delivery):
    from printhub.auth import COOKIE, issue_session
    from printhub.documents import DocBusy
    doc = scan_doc(store, 1)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        scope = {"type": "http", "http_version": "1.1", "scheme": "https", "method": "GET",
                 "path": f"/api/docs/{doc.id}/pages/page0", "query_string": b"",
                 "headers": [(b"cookie", f"{COOKIE}={issue_session(cfg)}".encode())],
                 "client": ("127.0.0.1", 1), "server": ("print.example.com", 443)}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                entered.set()
                await release.wait()
                if fail_delivery:
                    raise RuntimeError("delivery interrupted")

        task = asyncio.create_task(client.app(scope, receive, send))
        await asyncio.wait_for(entered.wait(), 10)
        try:
            with pytest.raises(DocBusy):
                store.delete(doc.id)
        finally:
            release.set()
        if fail_delivery:
            with pytest.raises(RuntimeError, match="delivery interrupted"):
                await task
        else:
            await task
    asyncio.run(run())
    store.delete(doc.id)


def test_scan_worker_lifetime_pins_document(client, store, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    doc = scan_doc(store, 1)
    scanner = Scanner()

    def scan(*args):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(scanner, "_scan", scan)
    scanner.start(store, doc.id, ScanSettings("flatbed"))
    assert entered.wait(10)
    try:
        assert client.delete(f"/api/docs/{doc.id}").status_code == 409
        assert client.patch(f"/api/docs/{doc.id}", json={"title": "blocked"}).status_code == 409
    finally:
        release.set()
    assert scanner._lock.acquire(timeout=10)
    scanner._lock.release()
    assert not store.get(doc.id).scanning
    store.delete(doc.id)


def test_anonymous_admission_checks_disk_and_pending_bytes_before_body(make_client, space, monkeypatch):
    from test_app import asgi_post
    from printhub import app as app_module
    client = make_client(login=False)
    space["free"] = lambda: MAX_BODY  # below the free-space floor plus body reservation
    assert asgi_post(client.app, "/share", {}) == (507, 0)
    space["free"] = lambda: PLENTY
    monkeypatch.setattr(app_module, "PENDING_MAX_BYTES", MAX_BODY - 2 * 1024 * 1024)
    assert asgi_post(client.app, "/share", {}) == (429, 0)
    monkeypatch.setattr(app_module, "PENDING_MAX_BYTES", 200 * 1024 * 1024)
    assert asgi_post(client.app, "/share", {})[0] != 429  # reservations released on refusal


def test_cups_unavailable_conservatively_protects_old_held_jobs(cfg, store):
    doc = store.create("print", "possibly held")
    doc.prints.append(PrintRecord(1, "2020-01-01T00:00:00+00:00", {}))

    def unavailable():
        raise RuntimeError("CUPS unavailable")

    assert retention.busy_policy(cfg, unavailable)(doc)
