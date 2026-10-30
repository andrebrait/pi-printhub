# Settings Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/settings.html` page, reached from a cog in the header, that changes retention, free space, email, the web password, Pi sharing and direct USB exposure without SSH.

**Architecture:** App-level values live in `/var/lib/printhub/settings.json`, overlaid on `config.toml` by `load_config()`; the app swaps its current `Config` on save. Root-level switches go through a request file that a systemd `.path` unit hands to a fixed root script, which reports back through a status file. The installer and `printer-setup.sh` apply the same switches instead of forcing sharing on.

**Tech Stack:** Python 3.13 (FastAPI, pydantic, argon2, itsdangerous), bash + systemd units, vanilla ES modules with Bootstrap 5.3.8.

**Spec:** `docs/superpowers/specs/2026-10-29-settings-page-design.md`

## Global Constraints

- Debian Trixie apt packages only; no new dependencies.
- `settings.json`, `system-request.json` and `system-status.json` live in `cfg.data_dir` (`/var/lib/printhub`), mode 0600, owner `printhub`; written atomically (temp file in the same directory, then `os.replace`).
- Ranges: `retention_days` 1–365; `min_free_gb` 0.5–1000; `smtp_port` 1–65535; `smtp_security` `starttls` or `tls`; new web password at least 8 characters.
- The SMTP password and password hash are never returned by the API, written to events or logged.
- The root script accepts only `{"share": bool, "expose_usb": bool}` from a regular, non-symlink file owned by `printhub`, at most 1024 bytes. No file content reaches a shell command.
- With no request file, behaviour is today's: shared and exposed.
- Tests never check source text. JS: `node --test static/js/*.test.js`. Python: `docker run --rm -v $PWD:/src -w /src printhub-dev python3 -m pytest -q -k "not slow"`. Shell: `shellcheck -x deploy/*.sh`.

## Review Focus

1. **A settings.json from a newer or hand-edited version with an unknown or out-of-range key** — the app must still start on `config.toml` values and say which key was ignored, not crash-loop. Test in Task 1.
2. **Saving email settings with the password field blank** — the stored SMTP password must survive. Test in Task 2.
3. **The page polls while the root unit never runs (path unit missing on an old install)** — the switch must show "Not applied yet", not spin forever or claim success. Test in Task 3 (status older than the request) and Task 6 (UI timeout).
4. **Turning sharing off, then replugging the printer** — `printer-setup.sh` must not re-share it. Test in Task 5.
5. **A request file replaced by a symlink to `/etc/shadow`** — refused, status says why. Test in Task 4.

---

### Task 1: Settings overlay and validation

**Files:**
- Create: `printhub/settings.py`
- Modify: `printhub/config.py`
- Test: `tests/test_settings.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `printhub.config.validate(cfg: Config) -> None` raises `ConfigError` naming the key for out-of-range values (ranges from Global Constraints).
  - `load_config(path=None) -> Config` overlays `<data_dir>/settings.json` (keys in `settings.APP_KEYS`) after reading `config.toml`. An unreadable or invalid overlay key is skipped with a `logging.warning` naming the key; the rest of the overlay still applies.
  - `printhub.settings.APP_KEYS: frozenset[str]` = `retention_days`, `min_free_gb`, `smtp_host`, `smtp_port`, `smtp_security`, `smtp_user`, `smtp_password`, `mail_from`, `password_hash`, `session_secret`.
  - `printhub.settings.save(cfg: Config, changes: dict) -> Config` validates `dataclasses.replace(cfg, **changes)`, merges `changes` into the existing `settings.json`, writes it atomically with mode 0600, and returns the new `Config`. Raises `ConfigError` and writes nothing on invalid input or a key outside `APP_KEYS`.
  - `printhub.settings.write_json(path: Path, data: dict) -> None`: the atomic 0600 writer, reused by Task 3.

- [ ] **Step 1: Write failing tests** in `tests/test_settings.py` and `tests/test_config.py`:
  - `test_saved_values_override_config_toml`: save `{"retention_days": 7}`, `load_config()` gives 7 and keeps `smtp_host` from the TOML.
  - `test_save_rejects_out_of_range_and_writes_nothing`: parametrize `retention_days` 0 and 366, `min_free_gb` 0.4, `smtp_port` 0 and 65536, `smtp_security` `"ssl"`, key `"origin"`; `ConfigError`, and `settings.json` does not exist afterwards.
  - `test_saved_file_is_private`: mode `0o600`.
  - `test_unknown_or_invalid_overlay_key_is_skipped_with_a_warning` (Review Focus 1): `settings.json` = `{"retention_days": 0, "colour": "red", "min_free_gb": 3}` → `load_config()` keeps TOML retention, applies `min_free_gb == 3`, `caplog` names `retention_days` and `colour`.
  - `test_corrupt_settings_file_falls_back_to_config_toml`: `settings.json` = `"{"` → TOML values, a warning.
  - `test_config_toml_out_of_range_is_an_error` in `test_config.py`: `retention_days = 0` in the TOML → `ConfigError`.
- [ ] **Step 2:** Run `... pytest -q tests/test_settings.py tests/test_config.py`; expect failures (module missing).
- [ ] **Step 3:** Implement `validate`, the overlay in `load_config`, and `printhub/settings.py`. The TOML stays strict (an invalid TOML value is an error); only the overlay is lenient, because the page wrote it.
- [ ] **Step 4:** Run the same command; expect all pass.
- [ ] **Step 5:** Commit: `feat(settings): settings.json overlay on config.toml with validation`.

### Task 2: Live config and the app settings API

**Files:**
- Modify: `printhub/app.py`, `printhub/middleware.py`, `printhub/auth.py`, `printhub/events.py`
- Test: `tests/test_app.py`, `tests/test_auth.py`

**Interfaces:**
- Consumes: `settings.save`, `settings.APP_KEYS` (Task 1).
- Produces:
  - `app.state.cfg: Config` is the current config. Every handler inside `create_app`, the free-space check (`min_free` computed per use), `mail.send` and login read it at use time.
  - `require_session(current: Callable[[], Config])` and `Guard(app, current: Callable[[], Config], ...)` take a getter instead of a `Config`.
  - `events.Kind` gains `"settings"`.
  - `GET /api/settings` → `{"retention_days", "min_free_gb", "smtp_host", "smtp_port", "smtp_security", "smtp_user", "mail_from", "smtp_password_set": bool}`.
  - `PUT /api/settings` body: any subset of those keys plus optional `smtp_password` (`""` or absent keeps the stored one) → 200 with the same shape as GET; 400 `{"error": <ConfigError text>}`.
  - `POST /api/settings/password` `{"current": str, "new": str}` → 204 with a fresh session cookie; 403 wrong current; 400 new shorter than 8.
  - `POST /api/settings/test-email` `{"to": str}` → 204; 400 invalid address; 502 `{"error": <SMTP error>}`.
  - Each successful save appends one `info` event of kind `"settings"`, title naming the change (e.g. `"Retention changed from 30 to 14 days"`, `"Email settings changed"`, `"Web password changed"`); no values of secrets.

- [ ] **Step 1: Write failing tests** in `tests/test_app.py` (existing fixtures `client`, `make_client`, `cfg`):
  - `test_settings_get_never_returns_secrets`: response has `smtp_password_set is True` and no `smtp_password`, `password_hash`, `session_secret` keys.
  - `test_saving_retention_applies_without_restart_and_logs_an_event`: PUT `{"retention_days": 14}` → GET gives 14; latest event title contains `14`.
  - `test_blank_smtp_password_keeps_the_stored_one` (Review Focus 2): PUT `{"smtp_host": "smtp.example.com", "smtp_password": ""}`; `load_config(...)`'s `smtp_password == "smtp-secret"`.
  - `test_settings_rejects_bad_values`: PUT `{"smtp_port": 70000}` → 400 and GET unchanged.
  - `test_password_change_logs_out_other_sessions`: two logged-in clients on one app; A changes password → A gets 204 and stays logged in (GET `/api/settings` 200); B gets 401; login with the new password works, the old one 401.
  - `test_password_change_needs_the_current_password`: wrong current → 403; `"short"` → 400.
  - `test_test_email_reports_smtp_errors`: monkeypatch `printhub.app.send` to raise `MailError("refused")` → 502 with `"refused"`.
  - In `tests/test_auth.py`, update `require_session(cfg)` to `require_session(lambda: cfg)`.
- [ ] **Step 2:** Run `... pytest -q tests/test_app.py tests/test_auth.py`; expect the new tests to fail.
- [ ] **Step 3:** Implement. `create_app` sets `app.state.cfg = cfg` and replaces each `cfg.` use inside it with `app.state.cfg.`; the Guard and the router dependency get `lambda: app.state.cfg`. The `settings` router lives in `create_app` beside the others, under the session router.
- [ ] **Step 4:** Run the full Python suite; expect all pass.
- [ ] **Step 5:** Commit: `feat(settings): app settings API with live config`.

### Task 3: System settings API

**Files:**
- Modify: `printhub/settings.py`, `printhub/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `settings.write_json` (Task 1).
- Produces (file contract shared with Task 4):
  - `system-request.json`: exactly `{"share": bool, "expose_usb": bool, "at": ISO-8601 UTC str}`. `at` lets the page tell whether the status answers this request.
  - `system-status.json` (written by Task 4): `{"state": "applied"|"failed", "share": bool, "expose_usb": bool, "error": str|null, "at": ISO-8601 UTC, "request_at": ISO-8601 UTC|null}`.
  - `GET /api/settings/system` → `{"requested": {"share", "expose_usb"}, "status": <status file or null>, "pending": bool}`; `pending` is true when a request exists and the status's `request_at` differs from the request's `at`. No request file → requested `{true, true}`.
  - `PUT /api/settings/system` `{"share": bool, "expose_usb": bool}` → 202 with the GET shape; 400 for anything else (pydantic `StrictBool`, `extra="forbid"`). Appends an event per switch that changed (`"Sharing turned off"`, `"Direct printer exposure turned on"`).

- [ ] **Step 1: Write failing tests:**
  - `test_system_defaults_to_shared_and_exposed`: GET → requested both true, status null, pending false.
  - `test_system_request_is_written_and_pending_until_applied` (Review Focus 3): PUT `{"share": false, "expose_usb": true}` → 202, file mode 0600 with those values, GET `pending` true; write a status with matching `request_at` → `pending` false.
  - `test_system_request_rejects_non_booleans`: `{"share": "no", "expose_usb": true}` and an extra key → 400, no file.
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement `settings.request_system(data_dir, share, expose_usb) -> dict` and `settings.read_system(data_dir) -> dict` and the two routes.
- [ ] **Step 4:** Run the Python suite; expect all pass.

### Task 4: Root apply script and units

**Files:**
- Create: `deploy/apply-settings.sh`, `deploy/systemd/printhub-apply.path`, `deploy/systemd/printhub-apply.service`
- Test: `tests/test_apply_settings.py`

**Interfaces:**
- Consumes: the file contract from Task 3.
- Produces (sourced by Task 5; all honour `DATA_DIR` default `/var/lib/printhub`, `IPP_USB_CONF` default `/etc/ipp-usb/ipp-usb.conf`, `AVAHI_DIR` default `/etc/avahi/services`, `PRINTER_QUEUE` default `OfficeJet`, `OWNER` default `printhub`):
  - `read_request` prints `share expose_usb` as `true|false` words; prints `true true` when the file is absent; returns non-zero with a reason on stderr for a symlink, non-regular file, wrong owner, size > 1024, or JSON that is not exactly `{share: bool, expose_usb: bool, at: str}`.
  - `apply_expose true|false`: sets `interface = all|loopback` in `$IPP_USB_CONF`; `systemctl try-restart ipp-usb` only when the file changed.
  - `apply_share true|false`: true → if the queue URI (`lpstat -v`) is an ipp-usb one (`//localhost:6????/`) and `$AVAHI_DIR/printhub-airprint.service` is missing, `systemctl start printhub-printer.service` (it re-shares; Task 5); otherwise `lpadmin -p $PRINTER_QUEUE -o printer-is-shared=true` and `cupsctl --share-printers`; then `systemctl enable --now airsaned`. False → `lpadmin ... printer-is-shared=false`, `cupsctl --no-share-printers`, `rm -f` both announcement files, `systemctl disable --now airsaned`.
  - `write_status applied|failed SHARE EXPOSE ERROR REQUEST_AT` writes the status file atomically, owner `$OWNER`, mode 0600, via `python3 -c` with values passed in the environment.
  - Running the file (not sourcing) does: read request → apply_expose → apply_share → applied status; on any failure a failed status with the error and the state read back (`interface` line, `lpstat` shared flag).
- Units: `.path` with `PathChanged=/var/lib/printhub/system-request.json`, `Unit=printhub-apply.service`, `WantedBy=paths.target`; `.service` `Type=oneshot`, `ExecStart=/usr/local/lib/printhub/apply-settings.sh`.

- [ ] **Step 1: Write failing tests** in `tests/test_apply_settings.py`, sourcing the script like `tests/test_printer_setup.py` and putting fake `systemctl`, `lpadmin`, `cupsctl`, `lpstat` on `PATH` that append their argv to a log file:
  - `test_request_defaults_to_shared_and_exposed`.
  - `test_request_refuses_unsafe_files` (Review Focus 5): symlink to a readable file, a directory, 1025 bytes, `{"share": "false", ...}`, an extra key, `OWNER` set to a user that does not own the file → non-zero, reason on stderr.
  - `test_expose_toggles_the_interface_and_restarts_only_on_change`: `interface = all` → `apply_expose false` gives `loopback` and one `try-restart ipp-usb`; running it again logs no restart.
  - `test_unsharing_unannounces_and_stops_airsane`: the `lpadmin`, `cupsctl --no-share-printers`, `disable --now airsaned` calls; both avahi files gone.
  - `test_resharing_an_ipp_usb_queue_reruns_printer_setup`: fake `lpstat -v` prints a `//localhost:60000/` URI and no avahi files → `systemctl start printhub-printer.service`.
  - `test_failed_command_writes_a_failed_status`: fake `lpadmin` exits 1 → status `failed`, `error` non-empty, `request_at` copied.
- [ ] **Step 2:** Run `... pytest -q tests/test_apply_settings.py`; expect failures.
- [ ] **Step 3:** Implement the script and units. `set -euo pipefail`; the run path uses a trap or explicit checks so a failure still writes the status.
- [ ] **Step 4:** Run the test file and `shellcheck -x deploy/*.sh`; expect pass and clean.
- [ ] **Step 5:** Commit: `feat(settings): root apply script behind a path unit`.

### Task 5: Installer and printer setup honour the switches

**Files:**
- Modify: `deploy/install.sh`, `deploy/printer-setup.sh`, `deploy/systemd/printhub-printer.service` if needed
- Test: `tests/test_install.py`, `tests/test_printer_setup.py`

**Interfaces:**
- Consumes: `read_request`, `apply_share`, `apply_expose` (Task 4).
- Produces: `printer-setup.sh` sources `/usr/local/lib/printhub/apply-settings.sh` (or `$REPO/deploy/apply-settings.sh` when run from the checkout; same lookup style as its other paths). `apply_printer` drops `-o printer-is-shared=true` from `lpadmin`, keeps writing the announcements and `cupsctl BrowseLocalProtocols=none`, then calls `apply_share "$share"` with `share` from `read_request` (an unreadable request: log it and share, the default). The installer installs `apply-settings.sh` next to `printer-setup.sh` and both units, replaces `ipp_usb_listen_all` with `apply_expose`, drops `airsaned` from its unconditional `enable --now`/`try-restart`, replaces the HPLIP branch's unconditional sharing with `apply_share`, and enables `printhub-apply.path`.

- [ ] **Step 1: Write failing tests:**
  - `test_replug_keeps_sharing_off` (Review Focus 4) in `test_printer_setup.py`: request file with `share: false`, run `apply_printer` with the existing fakes → `lpadmin` called with `printer-is-shared=false`, no avahi files remain.
  - `test_replug_shares_by_default`: no request file → `printer-is-shared=true`.
  - In `test_install.py`, replace any test of `ipp_usb_listen_all` with `apply_expose` cases if one exists; add `test_install_respects_expose_off`: request `expose_usb: false` and an `ipp-usb.conf` with `interface = loopback` → stays `loopback` after the installer's ipp-usb step.
- [ ] **Step 2:** Run both test files; expect failures.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the Python suite and shellcheck; expect pass.
- [ ] **Step 5:** Commit: `feat(settings): installer and replugs keep the sharing choices`.

### Task 6: Settings page and header cog

**Files:**
- Create: `static/settings.html`, `static/js/settings.js`, `static/vendor/material-symbols/settings.svg` (copied unmodified from `@material-symbols/svg-400@0.48.2` `package/outlined/settings.svg`, integrity-checked as before)
- Modify: `static/js/shell.js`, `static/css/app.css` (one `[data-icon="settings"]` rule), `static/sw.js` (precache the page, script and icon; bump `CACHE` to `printhub-v5`), `tests/test_pwa.py` (`CACHE` constant)
- Test: `static/js/ui.test.js`

**Interfaces:**
- Consumes: the five endpoints from Tasks 2 and 3.
- Produces: header cog `a.btn.btn-outline-secondary` with `data-icon="settings"`, `aria-label="Settings"`, `href="/settings.html"`, placed before the bell in `tools`; `PAGES` gains `["/settings.html", "Settings", "settings"]` for the collapsed menu.
- Page: four cards (Storage, Email with "Send test email", Web password, Network sharing), each with its own Save and an inline `alert-danger`/success line. Switches are `form-switch` checkboxes with labels "Share the Pi's printer and scanner on the network" and "Expose the printer directly (the \"(USB)\" entries)". After a PUT the page polls `GET /api/settings/system` every 2 s while `pending`, showing "Applying…"; after 30 s it shows "Not applied yet: is printhub-apply.path running? Re-run the installer." (Review Focus 3); on `failed` it shows the error and sets the switches to `status.share`/`status.expose_usb`.

- [ ] **Step 1: Write failing tests** in `ui.test.js` with the existing `fakeDocument`/`source()` harness:
  - `test("settings fills the form without the stored password and keeps it when the field is left blank")`: stub `apiFetch`; GET returns `smtp_password_set: true`; saving email sends a body without `smtp_password` when the field is empty.
  - `test("a sharing change shows Applying until the status catches up, then the applied state")`: first poll `pending: true`, second `pending: false` with `state: "applied"`.
  - `test("a sharing change that never applies says so after 30 seconds")`: fake timers; still pending → the not-applied message.
  - `test("a failed apply restores the switches to what is in force")`.
- [ ] **Step 2:** Run `node --test static/js/*.test.js`; expect failures.
- [ ] **Step 3:** Implement page, script, cog, CSS rule, precache, cache bump, `LICENSES.md` unchanged (same package; the row already says "only the icons the pages use").
- [ ] **Step 4:** Run JS tests and the Python suite (`test_pwa` precache checks); expect pass. Browser check with the dev server: header cog on desktop and phone width, each card saves, a bad port shows the inline error.
- [ ] **Step 5:** Commit: `feat(settings): settings page and header cog`.

### Task 7: Docs and verification on the Pi

**Files:**
- Modify: `README.md` (a "Settings" section: what the page changes, that root switches apply through `printhub-apply.path`, where values live), spec status if anything changed.

- [ ] **Step 1:** Update the README.
- [ ] **Step 2:** Deploy with the backend path (idle check first: `lpstat -o; pgrep -a scanimage`).
- [ ] **Step 3:** On the Pi, verify and record results in the PR:
  - `systemctl is-active printhub-apply.path` → active.
  - Toggle sharing off: `lpstat -l -p OfficeJet` shows not shared, `ls /etc/avahi/services/printhub-*` empty, `systemctl is-active airsaned` inactive; from this machine `avahi-browse -rt _ipp._tcp` (or `dns-sd` from the Mac) no longer lists the Pi printer. Toggle on: all restored.
  - Toggle exposure off: `ss -ltnp | grep 60000` bound to `127.0.0.1` only; the web app still prints a test page and scans. On: bound to all.
  - With sharing off, `sudo systemctl start printhub-printer.service` (same as a replug) and `sudo deploy/install.sh </dev/null` leave it off.
  - Send a test email; change retention and see `systemctl start printhub-retention` use it (`journalctl -u printhub-retention`).
  - Change the password from one browser; another browser is logged out.
- [ ] **Step 4:** Commit: `docs: settings page`.
