# Web UI Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every page gets a Bootstrap top bar with a live printer status pill and a notification bell; a Jobs page lists and cancels every job on the queue; a background watcher records job outcomes, printer problems and failed scans or conversions in a server-side event log with shared read state.

**Architecture:** `printhub/events.py` owns the event log file and read state. `printhub/watcher.py` diffs CUPS jobs and printer state every 3 s and appends events; `create_app` runs it in a thread through FastAPI's lifespan. New API endpoints serve status, jobs and notifications. The browser side is a shared `static/js/shell.js` (top bar, offcanvas, toasts) plus `jobs.html`/`jobs.js`, on vendored Bootstrap 5.3.

**Tech Stack:** FastAPI, pycups, fcntl; Bootstrap 5.3 (vendored CSS and bundle JS), plain ES modules; pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-28-web-ui-overhaul-design.md`

## Global Constraints

- No build step and no npm on the Pi; Bootstrap 5.3 lives in `static/vendor/bootstrap/` (`bootstrap.min.css`, `bootstrap.bundle.min.js`, `LICENSE`), recorded in `static/vendor/LICENSES.md` with version and source URL.
- No new Python dependencies.
- Every new endpoint sits on the authenticated `api` router; state-changing ones go through the existing Origin check (they do by being POSTs on that router).
- Event log: `data_dir/events.jsonl`, read state `data_dir/notifications-read.json`, newest 500 kept, writes under an exclusive `flock` on `data_dir/events.lock` with tempfile + fsync + `os.replace` (pattern of `DocStore._write`).
- Watcher period 3 s; top bar polls `/api/status` every 5 s; Jobs page refreshes every 5 s while visible.
- Recent finished jobs: 20. Notifications page size: 50. Toasts: at most 3 visible.
- All text from CUPS or documents (job names, users, hosts, titles) is inserted with `textContent`, never `innerHTML`.
- Phone width 320 px works without horizontal scrolling; level is shown as text or icon label, not only color.

## Review Focus

- CUPS unreachable when the watcher starts: no baseline yet; one "unreachable" event; when CUPS answers, that tick records "reachable again" and becomes the baseline without replaying existing jobs.
- A job vanishing from CUPS between ticks (history purged, CUPS restarted) appends nothing; only observed state changes are events.
- A corrupt or truncated line in `events.jsonl` (power loss) is skipped; listing, ids and appends keep working.
- Job names and hosts from phones contain markup or are very long: rendered as text, ellipsized at 320 px.
- The page is open while the event log is trimmed: `before=<id>` paging past the oldest kept id returns an empty page, not an error.

---

### Task 1: Event log

**Files:**
- Create: `printhub/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Produces:
  - `@dataclass Event: id: int, at: str, level: Literal["info","warning","error"], kind: Literal["job","printer","scan","conversion"], title: str, detail: str = "", job_id: int | None = None, doc_id: str | None = None`
  - `class EventLog(data_dir: Path, keep: int = 500)`:
    - `append(level, kind, title, detail="", *, job_id=None, doc_id=None) -> Event`
    - `page(before: int | None = None, limit: int = 50) -> list[tuple[Event, bool]]` (newest first; bool = read)
    - `unread() -> int`, `latest() -> int` (0 when empty)
    - `mark_read(*, up_to: int | None = None, ids: list[int] | None = None) -> None`

- [ ] **Step 1: Write the failing tests** in `tests/test_events.py`:
  - `test_ids_increase_across_instances`: two appends with one `EventLog`, a third with a new instance on the same dir → ids `[1, 2, 3]`.
  - `test_page_is_newest_first_and_pages_with_before`: 5 events → `page(limit=2)` ids `[5, 4]`; `page(before=4, limit=2)` → `[3, 2]`; `page(before=1)` → `[]`.
  - `test_keeps_the_newest_500`: `EventLog(dir, keep=3)`, 5 appends → `page()` ids `[5, 4, 3]`, next append gets id 6.
  - `test_read_state_with_up_to_and_ids`: 4 events; `mark_read(ids=[4])` → `unread() == 3`, event 4 read; `mark_read(up_to=2)` → `unread() == 1`; `ids` at or below `up_to` are dropped from the file.
  - `test_corrupt_line_is_skipped`: append 2, write `{"id": 3, "at"` plus a newline to the file, append 1 → `page()` ids `[3, 2, 1]` (the new event gets the max parsed id + 1), no exception.
  - `test_concurrent_appends_from_processes_keep_every_line`: 4 `multiprocessing` processes × 25 appends → 100 distinct ids.
- [ ] **Step 2:** Run `python3 -m pytest tests/test_events.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement `printhub/events.py` per Global Constraints; JSON lines via `json.dumps(asdict(e))`; ids from the max id in the file.
- [ ] **Step 4:** Run `python3 -m pytest tests/test_events.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: server-side event log with shared read state`.

### Task 2: Queue jobs and the watcher

**Files:**
- Modify: `printhub/printing.py` (add `JobInfo`, `queue_jobs`)
- Create: `printhub/watcher.py`
- Test: `tests/test_watcher.py`; extend `FakeCups` in `tests/test_app.py` so `getJobs(which_jobs="all", ...)` returns a settable dict

**Interfaces:**
- Consumes: `EventLog.append` (Task 1).
- Produces:
  - `@dataclass JobInfo: id: int, name: str, user: str, host: str, state: str (JobStatus states), reasons: list[str], created: str | None, completed: str | None, sheets: int | None`
  - `printing.queue_jobs(conn, queue: str) -> list[JobInfo]`: `getJobs(which_jobs="all", requested_attributes=[job-id, job-name, job-originating-user-name, job-originating-host-name, job-state, job-state-reasons, time-at-creation, time-at-completed, job-media-sheets-completed, job-printer-uri])`, keeping jobs whose `job-printer-uri` ends with `/printers/<queue>`; times as ISO UTC; newest first.
  - `class Watcher(conn_factory, queue: str, log: EventLog)` with `tick() -> None`; `run(stop: threading.Event, interval: float = 3.0) -> None` (calls `tick` until `stop` is set, logging exceptions).

- [ ] **Step 1: Write the failing tests** in `tests/test_watcher.py` with the extended `FakeCups`:
  - `test_first_tick_is_a_baseline`: one completed and one processing job, printer reasons `["media-low-report"]` → no events.
  - `test_job_transitions_become_events`: baseline with job 7 processing; next ticks set it to `completed` → one `info` `job` event titled `Job 7 completed`, detail containing the job name; separate cases for `canceled` (info), `aborted` (error), `held` and `stopped` (warning); `job_id == 7`.
  - `test_new_job_finishing_between_ticks_is_reported`: job 8 absent at baseline, `completed` at the next tick → one completed event.
  - `test_vanished_job_is_not_an_event`: job 9 processing, then absent → no events.
  - `test_printer_reasons_appear_and_clear`: `["media-jam-error"]` appears → `error` `printer` event titled `Paper jam`; then cleared → `info` event `Paper jam cleared`; `marker-supply-low-warning` → `warning` `Ink low`. Titles use the same labels as `static/js/api.js` `REASONS` (port the table to Python).
  - `test_cups_down_and_back_is_one_event_each`: factory raises `RuntimeError` for three ticks → one `error` event; next tick answers → one `info` event, no job events for jobs already present.
  - `test_other_queues_jobs_are_ignored`: a job on `/printers/Other` completes → no events.
  - `test_run_survives_a_failing_tick`: `tick` raising once then counting → `run` with a stop event set after the third call returns normally, three calls made.
- [ ] **Step 2:** Run `python3 -m pytest tests/test_watcher.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `queue_jobs` and `Watcher` (previous job states by id and previous reason set kept in memory; `None` until the first successful tick).
- [ ] **Step 4:** Run `python3 -m pytest tests/test_watcher.py tests/test_printing.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: watch CUPS jobs and printer state for notifications`.

### Task 3: API and app wiring

**Files:**
- Modify: `printhub/app.py`, `printhub/scanning.py` (`Scanner.on_error: Callable[[str, str], None] | None = None`, called where `scan_error` is set)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `EventLog`, `Watcher`, `queue_jobs` (Tasks 1 and 2).
- Produces (JSON shapes for Tasks 4 and 5):
  - `GET /api/status` → `{"printer": {"state", "state_reasons", "accepting"} | null, "error": str | null, "unread": int, "latest_event": int}`
  - `GET /api/jobs` → `{"active": [Job], "recent": [Job]}`, `Job` = `JobInfo` fields + `doc_id: str | null`; active = pending/held/processing/stopped; recent = the other states, first 20.
  - `POST /api/jobs/{id}/cancel` → 204; 404 when the id is not in `queue_jobs` of the configured queue.
  - `GET /api/notifications?before=&limit=50` → `{"events": [Event fields + "read"], "unread", "latest"}`
  - `POST /api/notifications/read` body `{"up_to": int}` or `{"ids": [int]}` → 204; 422 for neither.
  - `create_app(..., watch: bool = False)`; `_default_app` passes `watch=True`; with it, a FastAPI `lifespan` starts `Watcher.run` in a daemon thread and sets the stop event on shutdown.

- [ ] **Step 1: Write the failing tests** in `tests/test_app.py`:
  - `test_status_reports_printer_and_unread`, and with a factory raising `RuntimeError`: `printer is None`, `error` non-empty, status 200.
  - `test_jobs_lists_external_jobs_and_links_web_app_jobs`: FakeCups lists job 42 (from a print through the API) and job 900 from user `phone` → 42 has the doc's id, 900 has `doc_id is None`; 25 completed jobs → `recent` has 20.
  - `test_cancel_refuses_another_queues_job`: job on `/printers/Other` → 404, not in `fake_cups.cancelled`; job on our queue → 204.
  - `test_notifications_page_and_mark_read`: 3 events appended to the app's log → list newest first; `read` with `ids` then `up_to` updates `unread`; empty body → 422.
  - `test_conversion_and_scan_failures_become_events`: the existing failing-conversion fixture → one `error` `conversion` event with `doc_id`; a scan failing via `fake_scanimage` → one `scan` event.
  - Delete the `GET /api/jobs/{job_id}` assertions from `test_print_and_job_status` (endpoint removed).
- [ ] **Step 2:** Run `python3 -m pytest tests/test_app.py -v -k "status or jobs or cancel or notifications or failures"`. Expected: FAIL.
- [ ] **Step 3:** Implement: one `EventLog(cfg.data_dir)` per app; remove `GET /api/jobs/{job_id}`; append conversion events in `conversion_error`; set `scanner.on_error`.
- [ ] **Step 4:** Run `python3 -m pytest -q -k "not slow"`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: status, jobs and notifications API`.
  - Delete the `GET /api/jobs/{job_id}` assertions from `test_print_scan_doc` (endpoint removed); keep its cancel assertions.
### Task 4: Bootstrap shell, notification center and Jobs page

**Files:**
- Create: `static/vendor/bootstrap/{bootstrap.min.css,bootstrap.bundle.min.js,LICENSE}`, `static/js/shell.js`, `static/js/shell.test.js`, `static/jobs.html`, `static/js/jobs.js`, `static/js/jobs.test.js`
- Modify: `static/vendor/LICENSES.md`, `static/sw.js` (precache list: add new files, drop `printer-banner.js`), `static/css/app.css`
- Delete: `static/js/printer-banner.js` and its test in `static/js/ui.test.js`

**Interfaces:**
- Consumes: Task 3 JSON shapes; `reasonLabels` from `static/js/api.js`.
- Produces, exported from `shell.js`:
  - `statusPill(status) -> {text: string, level: "ok"|"busy"|"warning"|"error"|"unknown"}`
  - `eventItem(event, document) -> HTMLElement`
  - `showToast(message: string, {href?: string, level?: string})` (used by Task 5)
  - Side effect on import: renders the top bar into `<header id="shell">`, starts the 5 s status poll.

- [ ] **Step 1:** Download Bootstrap 5.3 (latest 5.3.x) `bootstrap.min.css` and `bootstrap.bundle.min.js` from `https://cdn.jsdelivr.net/npm/bootstrap@<version>/dist/`, verify against the release's published integrity hashes, add the MIT `LICENSE`, and record version, URL and hashes in `static/vendor/LICENSES.md`.
- [ ] **Step 2: Write the failing tests:**
  - `shell.test.js`:
    - `statusPill`: idle no reasons → `Ready`/`ok`; processing → `Printing`/`busy`; `["media-jam-error"]` → `Paper jam`/`error`; `["marker-supply-low-warning"]` → `Ink low`/`warning`; stopped → `Stopped`/`error`; `{printer: null, error: "x"}` → `Status unavailable`/`unknown`.
    - `eventItem`: title `<b>x</b>` renders as literal text; level `error` produces visible text `Error`; a `job_id` links to `/jobs.html#job-<id>`, a `doc_id` to the document page.
  - `jobs.test.js`: a row renders name, `user@host`, readable state and reasons, a Cancel button only for active jobs, and a document link only with `doc_id`.
- [ ] **Step 3:** Run `node --test static/js/*.test.js`. Expected: new tests FAIL.
- [ ] **Step 4:** Implement `shell.js` (Bootstrap navbar with collapse, status pill, bell with `badge`, offcanvas list with "Mark all read" and "Load more", toasts for events newer than the last poll), `jobs.html` + `jobs.js` (active and recent tables collapsing to cards below 576 px, Cancel calling the cancel endpoint, 5 s refresh while `document.visibilityState === "visible"`).
- [ ] **Step 5:** Run `node --test static/js/*.test.js` and `python3 -m pytest tests/test_pwa.py -q`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(ui): Bootstrap top bar with status, notification center and jobs page`.

### Task 5: Restyle existing pages

**Files:**
- Modify: `static/index.html`, `static/login.html`, `static/print.html`, `static/scan.html`, `static/js/print.js`, `static/js/scan.js`, `static/css/app.css`, `static/js/ui.test.js`

**Interfaces:**
- Consumes: `shell.js` (`<header id="shell">`, `showToast`).

- [ ] **Step 1: Write the failing test** in `ui.test.js`: `test("printing shows a toast linking to Jobs and no job card")`: run `print()` from `print.js` with a fetch stub returning `{job_id: 42}` and a `showToast` spy → spy called once with `href: "/jobs.html#job-42"`; no element with class `job` created.
- [ ] **Step 2:** Run `node --test static/js/ui.test.js`. Expected: FAIL.
- [ ] **Step 3:** Move every authenticated page to the shell header and Bootstrap layout (grid, cards, form controls, buttons, alerts); login page without shell. Remove `jobCard`, `JOB_LABELS`, `TERMINAL` from `print.js`. Keep custom CSS only for the sheet preview, page thumbnails and crop dialog; drop rules Bootstrap replaces.
- [ ] **Step 4:** Run `node --test static/js/*.test.js` and `python3 -m pytest -q -k "not slow"`. Expected: PASS.
- [ ] **Step 5:** Browser check (local dev server with the real printer's attributes, Chromium at 390 px and 1280 px, light and dark): pill and bell on every page; offcanvas opens, marks read, loads more; Jobs page lists and cancels an external job; print toast links to Jobs; scan view and edit modes still work; no horizontal overflow. Fix what fails.
- [ ] **Step 6:** Update `README.md` (Jobs page, notifications) and `docs/hardware-checklist.md` (cancel a phone's job from the Jobs page; a paper jam appears in the notification center).
- [ ] **Step 7: Commit** `feat(ui): restyle pages on Bootstrap and drop per-page job cards`.

### Task 6: Deploy and verify on the Pi

Precondition: no job or scan running.

- [ ] **Step 1:** Copy the branch to `/usr/local/src/pi-printhub`, run `sudo deploy/install.sh`. Expected: services active; `journalctl -u printhub` shows no watcher exceptions.
- [ ] **Step 2:** From the LAN, `GET /api/status` and `GET /api/jobs` (logged in) return the real printer's state (`Ink low` currently) and the job history.
- [ ] **Step 3:** Owner: print from a phone, see it on the Jobs page, cancel it there, and see the outcome in the notification center on a second device.
