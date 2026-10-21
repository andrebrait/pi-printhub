# SPDX-License-Identifier: AGPL-3.0-only
import json
from datetime import datetime, timedelta, timezone

import pytest

from printhub import retention
from printhub.documents import DocStore
from printhub.retention import DiskFull, ensure_free_space, purge_expired

NOW = datetime.now(timezone.utc)


@pytest.fixture
def store(cfg):
    return DocStore(cfg.data_dir / "docs")


def make_doc(store, age_days):
    doc = store.create("scan", f"{age_days} days old")
    path = store.path(doc.id) / "doc.json"
    raw = json.loads(path.read_text())
    raw["modified"] = (NOW - timedelta(days=age_days)).isoformat()
    path.write_text(json.dumps(raw))
    return doc.id


def fake_disk(store, docs_at_start):
    # Each deleted document frees 100 bytes; nothing is free at the start.
    return lambda: 100 * (docs_at_start - len(store.list()))


def test_purge_deletes_only_docs_older_than_retention(store):
    young = make_doc(store, 29)
    old31 = make_doc(store, 31)
    old40 = make_doc(store, 40)

    deleted = purge_expired(store, 30, NOW, busy=set())

    assert sorted(deleted) == sorted([old31, old40])
    assert [d.id for d in store.list()] == [young]


def test_purge_spares_busy_docs(store):
    old = make_doc(store, 40)

    assert purge_expired(store, 30, NOW, busy={old}) == []
    assert [d.id for d in store.list()] == [old]


def test_ensure_free_space_deletes_oldest_first_and_stops_when_enough(store):
    ages = {age: make_doc(store, age) for age in (5, 10, 20, 40)}

    ensure_free_space(store, 200, busy=set(), free_bytes=fake_disk(store, 4))

    assert {d.id for d in store.list()} == {ages[10], ages[5]}


def test_ensure_free_space_skips_busy_docs(store):
    oldest = make_doc(store, 40)
    make_doc(store, 20)
    make_doc(store, 5)

    ensure_free_space(store, 200, busy={oldest}, free_bytes=fake_disk(store, 3))

    assert [d.id for d in store.list()] == [oldest]


def test_ensure_free_space_raises_when_only_busy_docs_remain(store):
    oldest = make_doc(store, 40)
    mid = make_doc(store, 20)
    make_doc(store, 5)

    with pytest.raises(DiskFull):
        ensure_free_space(store, 300, busy={oldest, mid}, free_bytes=fake_disk(store, 3))
    assert {d.id for d in store.list()} == {oldest, mid}


def test_main_purges_using_loaded_config(cfg, monkeypatch):
    monkeypatch.setattr(retention, "load_config", lambda: cfg)
    store = DocStore(cfg.data_dir / "docs")
    make_doc(store, 31)
    young = make_doc(store, 1)

    retention.main()

    assert [d.id for d in store.list()] == [young]
