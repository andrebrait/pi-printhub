# SPDX-License-Identifier: AGPL-3.0-only
import re

import pytest

from printhub.documents import Crop, DocNotFound, DocStore, MailRecord, Page, PrintRecord


@pytest.fixture
def store(cfg):
    return DocStore(cfg.data_dir / "docs")


def test_create_makes_pages_dir_and_doc_json(store):
    doc = store.create("print", "t")
    assert (store.path(doc.id) / "pages").is_dir()
    assert (store.path(doc.id) / "doc.json").is_file()


def test_round_trips_every_field(store):
    doc = store.create("scan", "Quittung für Büro – 日本")
    doc.source_name = "ADF"
    doc.scan_resolution = 300
    doc.scanning = True
    doc.scan_error = "paper jam"
    doc.pages = [Page(id="p1", rotation=90, crop=Crop(x=1, y=2, w=300, h=400)), Page(id="p2")]
    doc.prints = [PrintRecord(job_id=7, at="2026-10-21T10:00:00+00:00", options={"copies": "2"})]
    doc.mails = [
        MailRecord(at="2026-10-21T10:05:00+00:00", to=["a@example.com"], subject="Scan", format="pdf")
    ]
    store.save(doc)

    assert store.get(doc.id) == doc


def test_list_orders_by_modified_newest_first(store):
    a = store.create("scan", "a")
    b = store.create("print", "b")
    assert [d.id for d in store.list()] == [b.id, a.id]

    store.save(a)
    assert [d.id for d in store.list()] == [a.id, b.id]


def test_list_filters_by_kind(store):
    scan = store.create("scan", "s")
    store.create("print", "p")
    assert [d.id for d in store.list(kind="scan")] == [scan.id]


def test_get_missing_raises(store):
    with pytest.raises(DocNotFound):
        store.get("A" * 22)


def test_get_rejects_ids_that_are_not_token_shaped(store):
    with pytest.raises(DocNotFound):
        store.get("../../etc")


def test_save_leaves_no_tmp_files(store):
    doc = store.create("scan", "t")
    store.save(doc)
    assert list(store.path(doc.id).rglob("*.tmp")) == []


def test_ids_are_urlsafe_and_unpredictable(store):
    ids = {store.create("scan", "t").id for _ in range(20)}
    assert len(ids) == 20
    assert all(len(i) >= 22 and re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids)


def test_delete_removes_doc(store):
    doc = store.create("scan", "t")
    store.delete(doc.id)
    with pytest.raises(DocNotFound):
        store.get(doc.id)
    with pytest.raises(DocNotFound):
        store.delete(doc.id)
