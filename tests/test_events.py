# SPDX-License-Identifier: AGPL-3.0-only
import multiprocessing

from printhub.events import EventLog


def ids(log: EventLog, **kw) -> list[int]:
    return [e.id for e, _ in log.page(**kw)]


def test_ids_increase_across_instances(tmp_path):
    log = EventLog(tmp_path)
    log.append("info", "job", "a")
    log.append("info", "job", "b")
    EventLog(tmp_path).append("info", "job", "c")
    assert ids(EventLog(tmp_path)) == [3, 2, 1]


def test_page_is_newest_first_and_pages_with_before(tmp_path):
    log = EventLog(tmp_path)
    for n in range(5):
        log.append("info", "job", str(n))
    assert ids(log, limit=2) == [5, 4]
    assert ids(log, before=4, limit=2) == [3, 2]
    assert ids(log, before=1) == []


def test_keeps_the_newest_events(tmp_path):
    log = EventLog(tmp_path, keep=3)
    for n in range(5):
        log.append("info", "job", str(n))
    assert ids(log) == [5, 4, 3]
    assert log.append("info", "job", "next").id == 6


def test_read_state_with_up_to_and_ids(tmp_path):
    log = EventLog(tmp_path)
    for n in range(4):
        log.append("warning", "printer", str(n))
    log.mark_read(ids=[4])
    assert log.unread() == 3
    assert dict((e.id, read) for e, read in log.page())[4] is True
    log.mark_read(up_to=2)
    assert log.unread() == 1
    assert [e.id for e, read in log.page() if not read] == [3]
    # ids at or below up_to carry no information any more
    assert '"ids": [4]' in (tmp_path / "notifications-read.json").read_text()


def test_corrupt_line_is_skipped(tmp_path):
    # A power loss mid-append leaves a truncated last line.
    log = EventLog(tmp_path)
    log.append("info", "job", "a")
    log.append("info", "job", "b")
    with (tmp_path / "events.jsonl").open("a") as f:
        f.write('{"id": 3, "at"\n')
    assert log.append("error", "scan", "c").id == 3
    assert ids(log) == [3, 2, 1]
    assert log.latest() == 3


def test_event_fields_round_trip(tmp_path):
    log = EventLog(tmp_path)
    log.append("error", "conversion", "Conversion failed", "bad.docx: corrupt", doc_id="d1")
    log.append("info", "job", "Job 7 completed", job_id=7)
    (job, _), (conv, read) = log.page()
    assert (job.kind, job.job_id, job.doc_id) == ("job", 7, None)
    assert (conv.level, conv.title, conv.detail, conv.doc_id, read) == ("error", "Conversion failed", "bad.docx: corrupt", "d1", False)
    assert conv.at.endswith("+00:00")


def _append_many(path, n):
    log = EventLog(path)
    for i in range(n):
        log.append("info", "job", str(i))


def test_concurrent_appends_from_processes_keep_every_line(tmp_path):
    procs = [multiprocessing.Process(target=_append_many, args=(tmp_path, 25)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert sorted(ids(EventLog(tmp_path), limit=1000)) == list(range(1, 101))
