# Notification Channels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send the event log's notifications to email, Telegram and webhook channels configured on the settings page, with per-kind filters and silences, plus a USB "not answering" event and sharing that works without the printer.

**Architecture:** Channels and silences live in `notifications.json`; a notifier thread in the app follows `events.jsonl` from a saved cursor and hands each event to per-type senders with retries. The watcher gains a USB liveness check and reports failed sharing requests; the root apply script keeps a root-owned copy of the AirPrint announcements.

**Tech Stack:** Python 3.13 stdlib (`urllib.request`, `json`, `threading`), FastAPI + pydantic, existing `printhub.mail`, bash, vanilla ES modules + Bootstrap 5.3.8.

**Spec:** `docs/superpowers/specs/2026-10-30-notification-channels-design.md`

## Global Constraints

- No new dependencies (apt or otherwise). HTTP via `urllib.request`.
- `notifications.json` and `notify-cursor.json` in `cfg.data_dir`, mode 0600, written with `printhub.settings.write_json` (atomic, fsynced).
- Secrets: Telegram `bot_token`, email `smtp_password`, webhook `header_value`. Never returned by the API (`<field>_set: bool` instead), never in events, logs or delivery results. Blank secret on save keeps the stored one; changing email `smtp_host`/`smtp_port`/`smtp_user`/`smtp_security`, Telegram `chat_id`, or webhook `url` requires a non-blank secret if one is stored (400 `"Enter the <secret label> again to change <what>"`).
- Kinds: `job`, `scan`, `printer`, `conversion`, `settings` (labels "Print jobs", "Scans", "Printer status", "File conversions", "Settings changes"). New channel: all on.
- Silence hours: exactly 1, 8, 24, 168. Match: `"title"` (exact event title) or `"kind"`.
- Retries after 60, 300, 900 seconds, then drop. Delivery failures never become events.
- Webhook: `http`/`https` only, 10 s timeout, no redirects, 2xx = success. Telegram timeout 10 s.
- USB check: 5 s timeout per probe; error after 120 s without an answer; IPP-over-USB queues only.
- Root copy dir: `/var/lib/printhub-system/avahi-off/`, root-owned, 0700.
- Tests never check source text. Commands: `docker run --rm -v $PWD:/src -w /src printhub-dev python3 -m pytest -q -k "not slow"`, `node --test static/js/*.test.js`, `shellcheck -x deploy/*.sh`.

## Review Focus

1. **A Telegram title with `<`, `&`, `*` or `_` in a document name** — the message must still send in every parse mode and show the text literally. Test in Task 2.
2. **App restart in the middle of a backlog** — events after the cursor are sent once, none twice, none from before the first start. Test in Task 3.
3. **One channel down (timeouts) while another works** — the working channel still gets every event promptly. Test in Task 3.
4. **Editing a channel's name only** — stored secrets stay; editing its URL/chat/server without the secret is refused. Test in Task 4.
5. **Printer powered off for 30 s during a normal restart** — no "not answering" error. Test in Task 5.

---

### Task 1: Channel store and validation

**Files:** Create `printhub/notifications.py`; Test `tests/test_notifications.py`.

**Interfaces — Produces:**
- `KINDS: tuple[str, ...] = ("job", "scan", "printer", "conversion", "settings")`, `SILENCE_HOURS = (1, 8, 24, 168)`, `SECRETS = {"email": "smtp_password", "telegram": "bot_token", "webhook": "header_value"}`.
- `load(data_dir: Path) -> dict` → `{"channels": [dict, ...], "silences": [dict, ...]}`; missing file = empty lists; an unparsable file logs an error and returns empty lists; a channel that fails validation is skipped with a logged error.
- `validate_channel(data: dict) -> dict` raises `ValueError` (message names the field) and returns the normalised channel. Common fields: `id` (str, server-assigned), `name` (1–60 chars), `type` (`email`/`telegram`/`webhook`), `enabled` (bool), `kinds` (subset of KINDS). Email: `to` (list of 1–10 addresses, each `Address(addr_spec=...)`-valid), `use_scan_server` (bool), and when false `smtp_host`, `smtp_port` 1–65535, `smtp_security` in (`starttls`,`tls`), `smtp_user`, `smtp_password`, `mail_from` (via `printhub.settings._sender`). Telegram: `bot_token` (non-empty, no whitespace), `chat_id` (`-?\d+` or `@[A-Za-z0-9_]{5,}`), `message_thread_id` (int or null), `parse_mode` in (`None`,`HTML`,`Markdown`,`MarkdownV2`), `disable_notification`, `protect_content`, `disable_web_page_preview` (bools). Webhook: `url` (scheme http/https, has host), `header_name` (empty or a token `[A-Za-z0-9-]+`), `header_value`.
- `public(channel: dict) -> dict`: the channel without its secret, plus `<secret>_set: bool`.
- `merge_edit(stored: dict, edit: dict) -> dict`: applies an edit, keeping the stored secret when the edit's is blank/absent, raising `ValueError` per the Global Constraints rule when a guarded field changes without the secret.
- `save(data_dir: Path, state: dict) -> None` (via `write_json`).
- `silenced(silences: list[dict], event, now: datetime) -> bool` and `active_silences(silences, now) -> list[dict]` (drops expired).

- [ ] **Step 1: Failing tests:** `test_valid_channels_of_each_type_round_trip`; `test_invalid_fields_name_the_field` (parametrized: bad email, port 0, chat_id `abc`, url `ftp://x`, header_name `a b`, unknown kind, name too long); `test_public_never_contains_secrets`; `test_blank_secret_keeps_stored_one` (Review Focus 4); `test_changing_guarded_field_without_secret_is_refused` (email server, telegram chat_id, webhook url) and allowed with the secret; `test_silence_by_title_and_kind_and_expiry`; `test_unparsable_file_and_bad_channel_are_skipped_with_errors` (caplog).
- [ ] **Step 2:** Run `tests/test_notifications.py`; expect import failure.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run; expect pass.
- [ ] **Step 5:** Commit `feat(notify): channel store and validation`.

### Task 2: Senders

**Files:** Create `printhub/senders.py`; Modify `printhub/mail.py`; Test `tests/test_senders.py`.

**Interfaces:**
- Consumes: channel dicts from Task 1; `printhub.mail.send`.
- Produces: `class SendError(Exception)` (message safe to show: no secrets); `send(channel: dict, event: Event, cfg: Config) -> None`; `test_event(channel_name: str) -> Event` (a synthetic `info` event, kind `settings`, title `"Test notification"`).
- `mail.send(..., body: str = "Attached.")` keyword; existing callers unchanged.
- Email: subject `"[Print Hub] " + title` (send already strips CR/LF); body = detail, blank line, `at`, link. With `use_scan_server` the Config is `cfg` itself; otherwise `dataclasses.replace(cfg, smtp_host=..., ...)`. `MailError`/`InvalidRecipient` → `SendError(str(e))`.
- Link: `cfg.origin` + `/print.html?doc=<id>` for print docs, `/scan.html?doc=<id>` when `kind == "scan"`, `/jobs.html` for `job` without doc, else `cfg.origin + "/"`.
- Telegram: POST JSON to `https://api.telegram.org/bot<token>/sendMessage` with `chat_id`, `text`, optional `message_thread_id`, `parse_mode` (omitted for `None`), `disable_notification`, `protect_content`, `link_preview_options: {"is_disabled": bool}`. Text: title bold + detail + link, escaped per mode (HTML: `html.escape`; Markdown: escape `_*[\``; MarkdownV2: escape ``_*[]()~`>#+-=|{}.!`` and `\`; None: plain). Non-2xx or `ok: false` → `SendError` with Telegram's `description` (never the URL, which holds the token).
- Webhook: POST JSON (spec 2.3 body, `source: "printhub"`), header if `header_name`; opener without redirect handling (a 3xx is a failure); `SendError("HTTP <code>")` or the network reason.
- Telegram base URL overridable for tests via a module constant `TELEGRAM_API = "https://api.telegram.org"`.

- [ ] **Step 1: Failing tests** with a local `http.server` thread: `test_telegram_request_shape` (path, JSON fields, thread id, silent/protect/preview flags); `test_telegram_escapes_per_parse_mode` (title `a<b & *c_ [d]` — Review Focus 1; assert the server got exactly the escaped text per mode); `test_telegram_error_reports_description_without_token`; `test_webhook_payload_header_and_no_redirects` (302 → SendError); `test_webhook_timeout_is_a_send_error` (server sleeps > timeout with a patched 0.5 s timeout); `test_email_uses_scan_server_or_its_own` and `test_email_body_and_subject` using the fake SMTP pattern in `tests/test_mail.py`.
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run tests/test_senders.py and tests/test_mail.py; expect pass.
- [ ] **Step 5:** Commit `feat(notify): email, Telegram and webhook senders`.

### Task 3: Notifier

**Files:** Create `printhub/notifier.py`; Modify `printhub/events.py` (`EventLog.after(event_id: int) -> list[Event]`, oldest first), `printhub/app.py` (start/stop the thread in `lifespan` when `watch=True`); Test `tests/test_notifier.py`.

**Interfaces:**
- Consumes: `notifications.load`, `notifications.silenced`, `senders.send`, `EventLog`.
- Produces: `class Notifier(events: EventLog, data_dir: Path, current: Callable[[], Config], send=senders.send, clock=time.monotonic, now=lambda: datetime.now(UTC))` with `tick() -> None` (handle new events and due retries), `run(stop: threading.Event, interval: float = 2.0)`, and `results: dict[str, str]` (channel id → `"Delivered <HH:MM>"` / `"Failed: <reason>"`) read by the API.
- Cursor: `notify-cursor.json` `{"last_id": int}`; first start writes the newest id; advance after an event has been dispatched to every channel (queued for retry counts as dispatched).
- Per-channel delivery runs on that channel's own worker thread with an in-memory queue, so one slow channel never delays another (Review Focus 3). Retries: due times at +60, +300, +900 s per failure, then dropped.

- [ ] **Step 1: Failing tests** (fake `send`, fake clock, real EventLog in tmp): `test_first_start_does_not_replay_history`; `test_restart_resumes_after_cursor_once` (Review Focus 2: events 1-3 sent, restart, events 4-5 only); `test_kinds_and_silences_filter`; `test_disabled_channel_gets_nothing`; `test_retry_schedule_then_drop` (fail ×4, clock advanced 60/300/900); `test_failures_never_become_events`; `test_slow_channel_does_not_delay_another` (one fake send blocks on an Event; the other channel still receives within 1 s); `test_results_record_last_outcome_without_secrets`.
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Full Python suite; expect pass.
- [ ] **Step 5:** Commit `feat(notify): notifier follows the event log`.

### Task 4: Notifications API

**Files:** Modify `printhub/app.py`; Test `tests/test_app.py`.

**Interfaces:** Consumes Tasks 1-3. Produces the spec 7 routes:
- `GET /api/notify/channels` → `[public(channel) + {"result": notifier.results.get(id)}]`.
- `POST /api/notify/channels` body = channel without `id` → 201 + public; 400 `{"error"}` on `ValueError`. Server assigns `id = secrets.token_urlsafe(8)`.
- `PUT /api/notify/channels/{id}` → 200 public | 404 | 400 (via `merge_edit`).
- `DELETE /api/notify/channels/{id}` → 204 | 404.
- `POST /api/notify/channels/{id}/test` → 204 | 404 | 502 `{"error": SendError text}` (synchronous `senders.send` with `test_event`).
- `GET /api/notify/silences` → active silences; `POST` `{match, value, hours}` → 201 `{id, match, value, until}` | 400; `DELETE /api/notify/silences/{id}` → 204 | 404.
- Channel add/edit/delete appends `info`/`settings` events: `"Notification channel <name> added"`, `"… changed"`, `"… removed"`.
- Writes under a lock shared with nothing else (`notify_lock`).

- [ ] **Step 1: Failing tests:** CRUD round trip never returning secrets; edit name keeps secret (Review Focus 4, through the API); guarded edit without secret → 400; test endpoint 502 with the sender's message (monkeypatch `printhub.app.senders.send`); silences create/list/expire/delete; bad `hours` 400; events for add/change/remove contain no secrets.
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Full Python suite; expect pass.
- [ ] **Step 5:** Commit `feat(notify): channels and silences API`.

### Task 5: Watcher: USB liveness and failed sharing

**Files:** Modify `printhub/watcher.py`, `printhub/printing.py` (a `usb_answers(uri: str, timeout: float = 5.0) -> bool` probe: an HTTP POST of a minimal hand-built IPP Get-Printer-Attributes request via `urllib` to the `http:` form of the URI; any HTTP 200 with an IPP body = answers), `printhub/app.py` (pass `data_dir` to the Watcher); Test `tests/test_watcher.py`.

**Interfaces:**
- `Watcher(conn_factory, queue, events, *, data_dir: Path | None = None, usb_probe=printing.usb_answers, clock=time.monotonic)`.
- Each tick, for a queue whose `device-uri` matches `ipp://localhost:6\d{4}/`: probe; when unanswered continuously for ≥ 120 s, append `error`/`printer` `"Printer not answering over USB"` detail `"Power-cycle the printer if this lasts."` once; on the first answer after that, `info`/`printer` `"Printer answering over USB again"`.
- Each tick with `data_dir`: `settings.read_system(data_dir)`; when `status.state == "failed"` and its `request_at` was not reported yet, append `warning`/`settings` `"Sharing settings not applied"` with a plain reason: `"The printer is not answering"` when the error mentions `printhub-printer.service`, else the first line of the error (≤ 200 chars).

- [ ] **Step 1: Failing tests** (fake probe, fake clock): `test_short_outage_is_quiet` (Review Focus 5: 30 s unanswered then answers → no events); `test_two_minutes_unanswered_is_one_error_then_recovery`; `test_hplip_queue_is_not_probed`; `test_failed_sharing_request_is_reported_once_in_plain_words`; `test_usb_answers_against_a_fake_ipp_server` (http.server returning an IPP 200 header, and a closed port → False).
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Full Python suite; expect pass.
- [ ] **Step 5:** Commit `feat(notify): report a printer not answering over USB and failed sharing changes`.

### Task 6: Sharing without the printer

**Files:** Modify `deploy/apply-settings.sh`, `deploy/printer-setup.sh` (`apply_printer` removes a stale copy after writing fresh announcements), `deploy/install.sh` (create `/var/lib/printhub-system` root 0700); Test `tests/test_apply_settings.py`, `tests/test_printer_setup.py`.

**Interfaces:** `OFF_DIR` env override, default `/var/lib/printhub-system/avahi-off`. `apply_share false`: `install -d -m 0700 "$OFF_DIR"`, move `printhub-airprint.service` and `printhub-generic.service` there (overwrite). `apply_share true`: if the queue is an IPP-over-USB queue and both copies exist, move them back (0644) and skip starting `printhub-printer.service`; otherwise keep today's behaviour.

- [ ] **Step 1: Failing tests:** `test_unsharing_keeps_a_root_copy`; `test_resharing_restores_the_copy_without_the_printer` (fake systemctl records no `start printhub-printer.service`); `test_resharing_without_a_copy_reruns_printer_setup`; `test_apply_printer_drops_a_stale_copy`; installer creates the dir with mode 0700 (fake `install` argv).
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Those test files and shellcheck; expect pass.
- [ ] **Step 5:** Commit `fix(settings): sharing can be turned on while the printer is off`.

### Task 7: Settings page and bell

**Files:** Modify `static/settings.html`, `static/js/settings.js`, `static/js/shell.js`, `static/css/app.css` if needed; Test `static/js/ui.test.js`.

**Interfaces:** Consumes spec 7 routes.
- Notifications card: list of channels (name, type, enabled switch, kinds summary, last result, Edit, Delete, Send test with its own result line under it, "Sending…" while waiting, like the scan test email); "Add channel" with a type select showing the type's fields (labels as spec 2.1–2.3; secret inputs `type="password"` with placeholder "Saved. Leave blank to keep it" when set); kinds as checkboxes with the Global Constraints labels; active silences list with Unsilence.
- Bell panel: each notification gets a "Silence" button opening a small menu: "This alert" / "All <kind label>" × "1 hour", "8 hours", "1 day", "1 week"; success shows a toast "Silenced until <time>".
- Sharing card: a `failed` status shows `"Not applied yet: <plain reason>"` (use the same mapping as Task 5: `printhub-printer.service` → "the printer is not answering") as a `warning` status line, not a danger alert.

- [ ] **Step 1: Failing tests:** add a Telegram channel (fields sent, secret not echoed), edit keeps secret when blank, delete, test button result line; silence from a bell item posts the right body; unsilence; the sharing failure shows the plain warning.
- [ ] **Step 2:** Run JS tests; expect failures.
- [ ] **Step 3:** Implement. Bump `CACHE` in `static/sw.js` and `tests/test_pwa.py` to `printhub-v6`.
- [ ] **Step 4:** JS + Python suites; browser check on the dev server (desktop and 390 px).
- [ ] **Step 5:** Commit `feat(notify): notifications card and silences in the bell`.

### Task 8: Docs and Pi verification

**Files:** Modify `README.md` (Notifications section), `docs/hardware-checklist.md`.

- [ ] **Step 1:** README: channels, kinds, silences, where secrets live, webhook payload (for Apprise/ntfy), the USB not-answering event.
- [ ] **Step 2:** Controller deploys with the installer (idle check first).
- [ ] **Step 3:** With the owner: paste the Telegram bot token and chat ID; Send test reaches the Notifications channel; a real print and a scan arrive; sharing toggled on with the printer off works; silence "Print jobs" for 1 hour stops job messages.
- [ ] **Step 4:** Commit `docs: notification channels`.
