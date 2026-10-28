# Web UI overhaul: status bar, jobs view and notification center

Date: 2026-10-28. Extends sections 6 to 9 of [the main design](2026-10-21-pi-printhub-design.md).

## 1. Goal

The owner always sees the printer's state, sees every job on the printer (including jobs sent from phones and laptops outside the web app) and can cancel any of them, and finds past job outcomes, printer problems and failed scans or conversions in one notification center whose read state is shared across devices.

Reported problems this replaces: printer and job status shown in job cards under the print options, reachable only by scrolling the options panel; no view of jobs started outside the web app.

## 2. Constraints

- No build step and no npm on the Pi: Bootstrap 5.3 (CSS and bundle JS) is vendored under `static/vendor/bootstrap/` with its license, like Cropper.js and SortableJS.
- Runtime dependencies stay Debian packages; no new Python packages.
- Single shared password, LAN only. Every new endpoint requires the session, and state-changing ones the existing Origin check.
- Works at phone width (320 px) and desktop; keyboard and screen-reader accessible; color is never the only signal.

## 3. What the owner sees

### 3.1 Top bar (every authenticated page)

- Links: Home, Print, Scan, Jobs. At phone width they collapse into a menu; the status pill and the bell stay visible.
- Printer status pill: `Ready`, `Printing`, `Stopped`, or the readable problem list (`Ink low`, `Paper jam`, `Offline`), with a warning or error style and an icon. When `/api/status` fails: `Status unavailable` with the error in its tooltip and accessible name.
- Bell with the unread notification count. It opens the notification center.
- Log out moves into the bar.
- Replaces `static/js/printer-banner.js` and the page banners.

### 3.2 Notification center

- Bootstrap offcanvas from the right.
- Newest first: icon and level (text, not only color), title, detail, relative time, and a link to the job (Jobs page) or the document.
- Mark all read; mark one read by opening it. Older items load on scroll ("Load more").
- New events since the last poll also appear as a toast for a few seconds, at most three at a time.

### 3.3 Jobs page (`/jobs.html`)

- Active jobs (pending, held, processing, stopped): name, submitting user and host, state with reasons, created time, sheets completed when known, and Cancel. Jobs started from the web app link to their document.
- The 20 most recent finished jobs (completed, canceled, aborted) with their outcome and completion time.
- Refreshes every 5 seconds while visible.

### 3.4 Print and scan pages

- After Print: a toast "Job N sent" with a "View in Jobs" link. The job cards and their polling under the options panel are removed.
- Layout, preview, crop and page tools keep their behavior; markup and styles move to Bootstrap components.

## 4. Server design

### 4.1 Events

One JSON object per line in `data_dir/events.jsonl`:

| Field | Value |
|---|---|
| `id` | integer, increasing |
| `at` | ISO 8601 UTC |
| `level` | `info`, `warning`, `error` |
| `kind` | `job`, `printer`, `scan`, `conversion` |
| `title`, `detail` | text shown to the owner |
| `job_id`, `doc_id` | when the event concerns one |

Recorded:

| Event | Level |
|---|---|
| Job completed | info |
| Job canceled | info |
| Job aborted | error |
| Job held or stopped | warning |
| Printer reason appears | `error` for `-error` reasons, `warning` otherwise |
| Printer reason clears | info |
| CUPS unreachable (once until it answers again) | error |
| CUPS reachable again | info |
| Scan failed | error |
| Conversion failed | error |

Not recorded: job queued and job processing (shown live on the Jobs page).

- Read state: `data_dir/notifications-read.json` = `{"up_to": <id>, "ids": [<id>, ...]}`; `ids` holds read ids above `up_to`.
- Retention: the newest 500 events; older lines are dropped when appending.
- Writes are serialized with an exclusive `flock` and replace the file atomically (temporary file, fsync, rename), as the document store does.

### 4.2 Watcher

- `printhub/watcher.py`: `Watcher.tick()` reads `getJobs(which_jobs="all")` for jobs whose `job-printer-uri` names the configured queue, and the printer's state and reasons; compares with the previous tick; appends events.
- The first tick after start records a baseline and appends nothing.
- `create_app` runs `tick()` every 3 seconds in a daemon thread, started on application startup and stopped on shutdown. An exception in a tick is logged and the next tick runs.
- Scan and conversion failures append their event where the app records the error today.

### 4.3 API

| Endpoint | Result |
|---|---|
| `GET /api/status` | `{printer: {state, state_reasons, accepting}, unread, latest_event}`; `printer` is `null` with an `error` text when CUPS does not answer |
| `GET /api/jobs` | `{active: [...], recent: [...]}`: `id`, `name`, `user`, `host`, `state`, `reasons`, `created`, `completed`, `sheets`, `doc_id` (from print records) |
| `POST /api/jobs/{id}/cancel` | cancels any job on the configured queue; 404 for jobs of other queues or unknown ids |
| `GET /api/notifications?before=<id>&limit=50` | `{events: [...], unread, latest}`, each event with `read` |
| `POST /api/notifications/read` | body `{up_to}` or `{ids}`; 204 |

`GET /api/jobs/{id}` is removed with the job cards that used it.

## 5. Out of scope

- Push notifications while the web app is closed (Web Push needs a push service and keys).
- Events for state changes while printhub is not running.
- Jobs on other CUPS queues.

## 6. Testing

- Watcher: baseline tick appends nothing; each job transition (completed, canceled, aborted, held, stopped); a printer reason appearing and clearing with its level; CUPS down then up appends one event each; a job on another queue is ignored; an exception in one tick does not stop the next.
- Event log: ids increase across restarts; trimming keeps the newest 500; read state with `up_to` and `ids`; concurrent appends from two processes keep every line.
- API: status with CUPS down; jobs lists external jobs and links web-app jobs to documents; cancel refused for another queue's job; notifications paging and marking read; scan and conversion failures appear as events.
- JS: status pill text and style for each state; notification rendering escapes text and labels levels.
- Browser (Chromium, 390 px and 1280 px, real printer attributes): pill and bell visible on every page; offcanvas opens and marks read; Jobs page lists and cancels; print toast links to Jobs; no horizontal overflow.
