# Notification channels

Date: 2026-10-30. Extends [the settings page](2026-10-29-settings-page-design.md) and the event log (the bell) from [the web UI overhaul](2026-10-28-web-ui-overhaul-design.md).

## 1. Goal

The owner gets the Print Hub's notifications where the other home services already send theirs: email, a Telegram channel, or anything that accepts a webhook. Every print and scan event and every printer problem, including a printer that stops answering over USB, can go out. The owner chooses per channel which kinds of event it receives and can silence an alert or a whole kind for a while.

## 2. Channels

Configured on the settings page in a new **Notifications** card. Any number of channels; each has a name, an on/off switch, the event types it receives (section 3), a **Send test** button and its last delivery result.

### 2.1 Email

- Recipients: one or more addresses (comma-separated), each a valid single address.
- **Use the scan email server** (default on): the channel sends through the SMTP host, port, security, user, password and From of the scan email settings, as they are at send time.
- Off: the channel has its own `smtp_host`, `smtp_port` (1–65535), `smtp_security` (`starttls` or `tls`), `smtp_user`, `smtp_password` and `mail_from` (an address, optionally with a name, as for the scan email).
- Subject: the **subject prefix** (default `[Print Hub]`, 0–100 characters, one line, stripped) and the event title, with line breaks removed; with no prefix, the title alone.
- **Format** (`html` or `text`, default `html`, also for a stored channel without it; anything else is refused). Body: the template of the event's kind (section 2.5).
  - `html`: the owner's tags are sent as typed, the values behind the placeholders are escaped (`html.escape`, quotes included, so a value cannot leave an attribute), and newlines become `<br>`. The message is `multipart/alternative`: a plain part made from the escaped render with its tags stripped, then unescaped, and an HTML part.
  - `text`: the template as one plain text part.

### 2.2 Telegram

The fields of Grafana's Telegram contact point:

| Field | Notes |
|---|---|
| Bot token | Secret. |
| Chat ID | A number, or `@channelname`. |
| Message thread ID | Optional; for forum topics. |
| Parse mode | `None`, `HTML` (default), `Markdown` or `MarkdownV2`. |
| Send silently | Telegram `disable_notification`. |
| Protect content | Telegram `protect_content`. |
| Disable link preview | Telegram `link_preview_options.is_disabled`. |

The message is one `sendMessage` call to `https://api.telegram.org/bot<token>/sendMessage`: the **header** (default `🖨️ Print Hub`, 0–200 characters; no header line when empty) followed by the template of the event's kind (section 2.5). Header and template text are sent as typed, so the owner's HTML, Markdown or MarkdownV2 formatting works; the placeholder values are escaped for the chosen parse mode (the link too in HTML and MarkdownV2, so `.` and `-` in the URL are valid; Markdown and None leave it plain). The title is no longer bold on its own: bold it in the template.

### 2.3 Webhook

- URL: `http` or `https` only, with no username or password in it (credentials go in the header). Private addresses are allowed (the Pi is on the LAN; a self-hosted Apprise or ntfy usually is too).
- Optional header name and value (for example `Authorization` / `Bearer …`); the value is a secret and holds no control characters.
- POST with `Content-Type: application/json`, body:
  `{"id", "at", "level", "kind", "title", "detail", "job_id", "doc_id", "url", "source": "printhub"}` where `url` is the related page under the configured origin, or the origin itself.
- Timeout 10 s; redirects are not followed; any 2xx is success.

### 2.4 Secrets and storage

- Channels and silences live in `/var/lib/printhub/notifications.json`, mode 0600, written atomically like `settings.json`.
- A file that cannot be read, or that holds a channel or silence that fails validation, is never overwritten: every save answers 409 naming the file until the owner fixes or removes it. The notifier keeps sending through the channels that are valid.
- Secrets (bot token, SMTP password, webhook header value) are never returned by the API: `GET` reports `<field>_set: true`. A blank secret field on save keeps the stored value. Changing an email channel's own SMTP host, port, user or security, a Telegram channel's chat ID, or a webhook's URL requires re-entering its secret, as for the scan email.
- Secrets never appear in events, logs or delivery results.

### 2.5 Templates (email and Telegram)

- One template per event kind (`job`, `scan`, `printer`, `conversion`, `settings`), 1–1000 characters each. A channel stored without them, or without some kinds, uses the defaults; an unknown kind or an empty or over-long template is refused. The defaults are the same for both types: `🧾 {title}\n{detail}\n{link}` for jobs, `📄` for scans, `🔔` for printer status, `🔄` for file conversions and `⚙️` for settings changes.
- Placeholders: `{title}`, `{detail}`, `{link}`, `{time}` (the event's time as local `YYYY-MM-DD HH:MM`). Only these four are replaced, everything else, other braces included, stays as typed. Lines left empty or blank after the replacement are dropped.
- **Send test** is a `settings` event, so it shows the Settings changes template.
- The settings page keeps all of this under **Advanced options** (closed by default) in the channel form, with **Reset to defaults**; the defaults come from `GET /api/notify/defaults`.

## 3. Event types

Each channel has a checkbox per kind; all on for a new channel.

| Checkbox | Event kind | Examples |
|---|---|---|
| Print jobs | `job` | Job completed, aborted, held |
| Scans | `scan` | Scan finished, scan failed |
| Printer status | `printer` | Paper jam, ink low, not answering over USB, cleared |
| File conversions | `conversion` | Conversion failed |
| Settings changes | `settings` | Retention changed, sharing turned off, sharing not applied |

## 4. Silences

- In the bell panel, each notification has **Silence**: "this alert" (same title) or "all <kind>", for 1 hour, 8 hours, 1 day or 1 week.
- The Notifications card lists active silences with what they match and when they end, each with **Unsilence**.
- A silence stops outgoing messages on every channel; the bell still records and shows every event. Expired silences are dropped.

## 5. Delivery

- A notifier thread in the web app reads `events.jsonl` after a saved position in `/var/lib/printhub/notify-cursor.json` (the last event id handled). Each event is handed to the channels once per process, whoever wrote it. A crash right after the cursor is saved can lose events still queued; a crash before it can repeat one. The event log keeps the newest 500 events.
- On the first start (no cursor file) the position is the newest event, so history is not replayed. After a restart it resumes after the saved position.
- For each new event and each enabled channel that takes its kind and is not silenced, the notifier sends it. A failed send is retried after 1, 5 and 15 minutes, then dropped; a slow or failing channel does not delay the others. Pending retries are kept in memory (a restart drops them).
- Each channel's last result (`Delivered <time>` or `Failed: <reason>`) is shown on its card. Delivery failures never become events.
- The notifier never stops on an error: an exception is logged and the next event is handled. A channel that cannot be parsed is skipped with a logged error; the others work.

## 6. New and changed events

### 6.1 Printer not answering over USB

- For an IPP-over-USB queue, the watcher asks the queue's device URI (`ipp://localhost:<port>/ipp/print`) for its printer attributes on each check, with a 5 s timeout.
- No answer for 2 minutes: an `error`, kind `printer`, "Printer not answering over USB", detail "Power-cycle the printer if this lasts." When it answers again: `info` "Printer answering over USB again".
- The 2 minutes keep a normal printer start quiet. HPLIP queues are not checked.

### 6.2 Sharing changes that could not be applied

- When the root apply script reports `failed` for a request, an event is added (kind `settings`, `warning`): "Sharing settings not applied", detail the reason in plain words. One event per failed request.
- The settings card shows "Not applied yet: <plain reason>" as a status line instead of the raw command output; the full error stays in the status file.

### 6.3 Sharing without the printer

- `apply_share false` moves the AirPrint announcement files to `/var/lib/printhub-system/avahi-off/` (created root-owned, mode 0700; never under `/var/lib/printhub`, which the web app's user controls) instead of deleting them; `apply_share true` moves them back when the queue is an IPP-over-USB queue and they exist, and only starts `printhub-printer.service` when there is no copy. `apply_printer` writes fresh announcements and removes any stale copy.
- Turning sharing on therefore works while the printer is off or not answering.

### 6.4 Finished scans and long Telegram messages

- A scan pass that ends without error adds an `info` event, kind `scan`, "Scan finished: <document title>", detail "<n> page(s) added" (the pages of that pass). Scan notifications therefore cover finished and failed scans.
- Telegram refuses text over 4096 characters. A longer message is not split (a split can cut an entity): the event's detail is shortened (halved, ending in "…") until the message fits, then the title if the detail is already empty. The header and the owner's template text are never cut.

## 7. API

All under the session router; state-changing calls pass the Origin check.

| Method and path | Purpose |
|---|---|
| `GET /api/notify/defaults` | `{header, subject_prefix, templates}`: the message options of a new channel |
| `GET /api/notify/channels` | Channels without secrets (their header, subject prefix and templates included), with last results |
| `POST /api/notify/channels` | Add a channel; 400 on invalid fields |
| `PUT /api/notify/channels/{id}` | Edit; secrets as in 2.4 |
| `DELETE /api/notify/channels/{id}` | Delete |
| `POST /api/notify/channels/{id}/test` | Send a test notification now; 502 with the reason on failure |
| `GET /api/notify/silences` | Active silences |
| `POST /api/notify/silences` | `{match: "title" \| "kind", value, hours: 1 \| 8 \| 24 \| 168}` |
| `DELETE /api/notify/silences/{id}` | Unsilence |

Adding, editing and deleting a channel adds a `settings` event naming the channel (never its secrets).

## 8. Testing

- Python: channel validation per type (message fields filled with defaults, unknown kinds, over-long and empty templates, a prefix with a line break); rendering (escaping per parse mode, other braces kept, blank lines dropped, no header, one template per kind, local time); secrets never returned and kept on blank; cursor (each event handed over once per process, resume, no replay on first start); kind filtering; silences by title and kind and their expiry; retry schedule with a fake clock; failures not becoming events; Telegram request shape and escaping per parse mode against a fake HTTP server; webhook payload, header, timeout and no redirects; email through the existing fake SMTP (an HTML message is multipart with escaped values, quotes included, `<br>` between lines and a tag-free plain part; a text channel stays one plain part; a stored channel without `format` is html; a bad format is refused), with and without the scan-server toggle and with each subject prefix; the 2-minute USB check and its clearing; one event per failed sharing request.
- Shell: announcements kept and restored by `apply_share`; sharing on with the printer not answering.
- JS: add, edit and delete channels; Advanced options filled from the defaults, the email Format select and the syntax help that follows it and Telegram's parse mode, edited templates saved, Reset to defaults, none for a webhook; test button and result; silence from the bell; unsilence on the settings page.
- On the Pi: the owner pastes the Telegram bot token and chat ID; a test reaches the Notifications channel; a real print and scan arrive; sharing toggled with the printer off.

## 9. Out of scope

- Digests and automatic rate limiting; silences cover floods.
- Persisting pending retries across restarts.
