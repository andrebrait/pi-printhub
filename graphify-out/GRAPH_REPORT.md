# Graph Report - generic  (2026-10-29)

## Corpus Check
- 70 files · ~56,879 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 19 file(s) not represented in the graph (top: (none) 7, .service 4, .css 3)

## Summary
- 1494 nodes · 3733 edges · 65 communities (43 shown, 22 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 489 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `72e914b9`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- scan.js
- Sortable.min.js
- test_app.py
- documents.py
- test_mail.py
- DocumentResponse
- test_printing.py
- test_scanning.py
- create_app
- test_convert.py
- pi-printhub design
- install.sh
- test_install.py
- app.py
- Config
- bootstrap.bundle.min.js
- test_auth.py
- test_lifecycle.py
- PendingShares
- ps
- LoginLimiter
- sw.js
- EventLog
- package.json
- LICENSES.md
- record-job.sh
- start-virtual-printer.sh
- printhub
- retention.py
- St
- run
- test_printer_setup.py
- traefik.yml
- apiFetch
- ui.test.js
- Qi
- shell.js
- Ln
- scanning.py
- Zs
- shell.test.js
- print.js
- test
- B
- Ss
- auth.py
- printhub
- ._queueCallback
- mo
- Network sharing: spooled queue plus native USB passthrough
- FakeElement
- un
- Zi
- Web UI overhaul: status bar, jobs view and notification center
- crop.test.js
- an
- W
- 5. Printing from the web application
- pi-printhub
- 2026-10-21-pi-printhub-design.md
- 6. Scanning and the page editor
- 4. Data and configuration
- RevalidatedStaticFiles

## God Nodes (most connected - your core abstractions)
1. `create_app()` - 90 edges
2. `DocStore` - 43 edges
3. `ps` - 40 edges
4. `EventLog` - 36 edges
5. `St` - 29 edges
6. `Scanner` - 28 edges
7. `Page` - 27 edges
8. `send()` - 26 edges
9. `sh()` - 26 edges
10. `B` - 23 edges

## Surprising Connections (you probably didn't know these)
- `Task 5: CUPS printing` --references--> `validate()`  [INFERRED]
  docs/superpowers/plans/2026-10-21-pi-printhub.md → printhub/printing.py
- `6. Testing` --references--> `ids()`  [INFERRED]
  docs/superpowers/specs/2026-10-28-web-ui-overhaul-design.md → tests/test_events.py
- `Web UI Overhaul Implementation Plan` --references--> `create_app()`  [INFERRED]
  docs/superpowers/plans/2026-10-28-web-ui-overhaul.md → printhub/app.py
- `4.2 Watcher` --references--> `create_app()`  [INFERRED]
  docs/superpowers/specs/2026-10-28-web-ui-overhaul-design.md → printhub/app.py
- `Task 3: API and app wiring` --references--> `lifespan()`  [INFERRED]
  docs/superpowers/plans/2026-10-28-web-ui-overhaul.md → printhub/app.py

## Import Cycles
- None detected.

## Communities (65 total, 22 thin omitted)

### Community 0 - "scan.js"
Cohesion: 0.17
Nodes (27): cropAspect(), docId, docUrl(), editPage(), fillSelect(), loadRecipients(), loadScanner(), main() (+19 more)

### Community 1 - "Sortable.min.js"
Cohesion: 0.05
Nodes (55): Global Constraints, pi-printhub Implementation Plan, Verified facts the plan relies on, qt, A(), b(), C(), D() (+47 more)

### Community 2 - "test_app.py"
Cohesion: 0.06
Nodes (40): Task 3: API and app wiring, asgi_post(), receive(), client(), fake_cups(), jpeg_bytes(), make_client(), make() (+32 more)

### Community 3 - "documents.py"
Cohesion: 0.12
Nodes (19): DocNotFound, _now(), test_active_print_job_keeps_doc_busy(), test_disk_full_when_only_busy_docs(), test_low_space_deletes_old_doc_then_uploads(), test_page_image_full_and_thumb(), test_page_image_rotated(), test_patch_and_delete_conflict_while_scanning() (+11 more)

### Community 4 - "test_mail.py"
Cohesion: 0.07
Nodes (43): Task 1: Project skeleton, config and CI, Task 7: Email, Config, ConfigError, load_config(), Attachment, AttachmentTooLarge, InvalidRecipient (+35 more)

### Community 5 - "DocumentResponse"
Cohesion: 0.29
Nodes (4): using_document(), decorate(), guarded(), DocumentResponse

### Community 6 - "test_printing.py"
Cohesion: 0.11
Nodes (30): active_job_ids(), _allowed(), cancel(), connect(), _iso(), _list(), printer_info(), PrinterInfo (+22 more)

### Community 7 - "test_scanning.py"
Cohesion: 0.19
Nodes (23): Task 6: Scanning, Scanner, ScannerBusy, ScanSettings, test_scan_progress_and_busy(), test_scan_worker_lifetime_pins_document(), test_thread_start_failure_rolls_back_flag_and_releases_pin(), fake_scanimage() (+15 more)

### Community 8 - "create_app"
Cohesion: 0.05
Nodes (87): Review Focus, Task 11: Front end: scan page and editor, Task 12: Progressive web app, Task 13: Deployment, Task 14: On-device acceptance, Task 3: Lossless page edits and export, Task 5: CUPS printing, Task 9: HTTP API (+79 more)

### Community 9 - "test_convert.py"
Cohesion: 0.14
Nodes (26): Task 4: Upload normalization, ConversionError, detect(), _heic_to_pdf(), _image_to_pdf(), normalize(), _office_to_pdf(), pdf_page_sizes() (+18 more)

### Community 10 - "pi-printhub design"
Cohesion: 0.14
Nodes (14): 10. Testing, 11. Installation and operations, 12. Repository layout, 1. Purpose, 2. Hardware and constraints, 3. System layout, 7. Authentication and request security, 8. Progressive web application (+6 more)

### Community 11 - "install.sh"
Cohesion: 0.09
Nodes (35): airsane_ignore_hpaio_net(), allow_airscan_in_airsane(), ask(), ask_secret(), config_host(), configure_dll_conf(), create_hplip_queue(), die() (+27 more)

### Community 12 - "test_install.py"
Cohesion: 0.08
Nodes (44): Task 3: Hotplug and installer integration, ipp_usb_dev(), make_device(), sh(), test_a_cups_url_is_not_mistaken_for_ipp_usb(), test_airsane_ignore_list_keeps_only_escl(), test_airsane_ignores_hpaio_network_devices(), test_any_ipp_usb_printer_is_found() (+36 more)

### Community 13 - "app.py"
Cohesion: 0.16
Nodes (8): _default_app(), DocPatch, EmailRequest, __getattr__(), Login, MarkRead, PageEdit, ScanRequest

### Community 14 - "Config"
Cohesion: 0.15
Nodes (7): origin_ok(), _BodyTooLarge, _error(), Guard, guarded_send(), limited_receive(), test_origin_check_guards_state_changing_requests()

### Community 15 - "bootstrap.bundle.min.js"
Cohesion: 0.07
Nodes (52): be(), bi(), Ce(), ci(), D(), De(), _e(), ei() (+44 more)

### Community 16 - "test_auth.py"
Cohesion: 0.18
Nodes (12): issue_session(), require_session(), dependency(), session_valid(), cfg(), test_missing_session_cookie_is_invalid(), test_require_session_rejects_missing_cookie_and_accepts_valid_one(), test_session_issued_31_days_ago_is_invalid() (+4 more)

### Community 17 - "test_lifecycle.py"
Cohesion: 0.07
Nodes (19): scan_doc(), test_email_rejects_oversized_exports_before_reading(), test_export_jpeg_and_errors(), test_export_pdf_content_disposition(), test_jobs_link_documents_printed_or_deleted_after_the_first_listing(), test_orientation_auto_lets_cups_rotate_and_explicit_orientation_does_not(), test_patch_rejects_bad_rotation_and_unknown_page(), test_print_unsupported_sides() (+11 more)

### Community 18 - "PendingShares"
Cohesion: 0.32
Nodes (6): Task 8: Authentication and request security, PendingShares, test_pending_share_expiry_survives_unlink_failure(), test_pending_share_put_deletes_files_of_expired_entries(), test_pending_share_take_after_ttl_returns_none_and_deletes_file(), test_pending_share_take_within_ttl_returns_path_once()

### Community 19 - "ps"
Cohesion: 0.06
Nodes (3): es, ps, vs

### Community 20 - "LoginLimiter"
Cohesion: 0.43
Nodes (5): LoginLimiter, test_login_limiter_allows_attempt_after_window(), test_login_limiter_allows_five_and_denies_sixth_within_window(), test_login_limiter_drops_keys_once_their_attempts_expire(), test_login_limiter_keys_are_independent()

### Community 22 - "EventLog"
Cohesion: 0.06
Nodes (47): Task 1: Event log, Task 2: Queue jobs and the watcher, 4.1 Events, 4.2 Watcher, 4.3 API, 4. Server design, lifespan(), Event (+39 more)

### Community 29 - "retention.py"
Cohesion: 0.20
Nodes (20): Task 2: Document store and retention, DocBusy, DocStore, busy_policy(), busy(), _delete_if(), DiskFull, ensure_free_space() (+12 more)

### Community 30 - "St"
Cohesion: 0.10
Nodes (3): ot, St, bt()

### Community 31 - "run"
Cohesion: 0.40
Nodes (4): test_share_admission_rejects_before_receiving_and_releases_slot(), run(), record(), request()

### Community 32 - "test_printer_setup.py"
Cohesion: 0.12
Nodes (31): Global Constraints, Network Sharing Implementation Plan, Review Focus, Task 1: Device discovery and config rendering in `printer-setup.sh`, Task 2: Apply the device: queue, scanner, pin, Task 4: Deploy and verify on the Pi, apply(), sh() (+23 more)

### Community 33 - "traefik.yml"
Cohesion: 0.07
Nodes (27): dnsChallenge, email, storage, certificatesResolvers, le, propagation, provider, resolvers (+19 more)

### Community 34 - "apiFetch"
Cohesion: 0.18
Nodes (21): Task 10: Front end: shell, login and print page, apiFetch(), errorMessage(), safeNext(), toLogin(), uploadFile(), ACTIVE, cancel() (+13 more)

### Community 35 - "ui.test.js"
Cohesion: 0.13
Nodes (14): isLoadableMedia(), MEDIA_NAMES, mediaLabel(), mediaSizeMm(), parsePageRanges(), round2(), sheetLayout(), A4 (+6 more)

### Community 37 - "shell.js"
Cohesion: 0.14
Nodes (18): Global Constraints, Review Focus, Task 4: Bootstrap shell, notification center and Jobs page, Task 5: Restyle existing pages, Task 6: Deploy and verify on the Pi, Web UI Overhaul Implementation Plan, buildBar(), buildCenter() (+10 more)

### Community 39 - "scanning.py"
Cohesion: 0.22
Nodes (13): get_scanner(), capabilities(), find_device(), ingest(), _last_error(), _max_mm(), _options(), parse_options() (+5 more)

### Community 41 - "shell.test.js"
Cohesion: 0.20
Nodes (4): fakeDocument(), find(), findAll(), textOf()

### Community 42 - "print.js"
Cohesion: 0.24
Nodes (15): sheetsFor(), buildControls(), buildPages(), CONTROLS, docId, FALLBACK, main(), params (+7 more)

### Community 43 - "test"
Cohesion: 0.13
Nodes (14): PRINTHUB_TEST_QUEUE, jobs, test, name, on, pull_request, push, name (+6 more)

### Community 46 - "auth.py"
Cohesion: 0.16
Nodes (5): login(), hash_password(), set_session_cookie(), verify_password(), test_password_hash_round_trip_verifies_and_rejects_wrong_password()

### Community 47 - "printhub"
Cohesion: 0.14
Nodes (13): http, routers, services, servers, entryPoints, loadBalancer, rule, service (+5 more)

### Community 50 - "Network sharing: spooled queue plus native USB passthrough"
Cohesion: 0.18
Nodes (11): 1. Goal, 2. Findings that shape the design, 3. What clients see, 4.1 A: ipp-usb on all interfaces, 4.2 B: the CUPS queue follows the attached printer, 4.3 B: one AirSane scanner, 4. Design, 5. Out of scope (+3 more)

### Community 54 - "Web UI overhaul: status bar, jobs view and notification center"
Cohesion: 0.20
Nodes (10): 1. Goal, 2. Constraints, 3.1 Top bar (every authenticated page), 3.2 Notification center, 3.3 Jobs page (`/jobs.html`), 3.4 Print and scan pages, 3. What the owner sees, 5. Out of scope (+2 more)

### Community 55 - "crop.test.js"
Cohesion: 0.31
Nodes (6): rotateCrop(), snapCrop(), FRAMES, SNAPPED, trim(), snapped()

### Community 58 - "5. Printing from the web application"
Cohesion: 0.29
Nodes (7): 5. Printing from the web application, History, Job submission and status, Normalization, Options, Preview, Upload

### Community 59 - "pi-printhub"
Cohesion: 0.29
Nodes (7): Document safety, Install, License, Network sharing, pi-printhub, Tests, Web app

### Community 61 - "6. Scanning and the page editor"
Cohesion: 0.40
Nodes (5): 6. Scanning and the page editor, Editor, Export, Lossless application of edits, Scanning

### Community 62 - "4. Data and configuration"
Cohesion: 0.50
Nodes (4): 4. Data and configuration, Configuration, Documents, Retention and disk space

## Knowledge Gaps
- **2 isolated node(s):** `PYTHONDONTWRITEBYTECODE`, `printhub`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 382 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **22 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Task 3: Lossless page edits and export` connect `create_app` to `Sortable.min.js`, `retention.py`, `bootstrap.bundle.min.js`?**
  _High betweenness centrality (0.256) - this node is a cross-community bridge._
- **Are the 17 inferred relationships involving `create_app()` (e.g. with `Web UI Overhaul Implementation Plan` and `4.2 Watcher`) actually correct?**
  _`create_app()` has 17 INFERRED edges - model-reasoned connections that need verification._
- **What connects `PYTHONDONTWRITEBYTECODE`, `printhub` to the rest of the system?**
  _2 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Sortable.min.js` be split into smaller, more focused modules?**
  _Cohesion score 0.052659716653301256 - nodes in this community are weakly interconnected._
- **Why does `h()` connect `Sortable.min.js` to `create_app`, `bootstrap.bundle.min.js`?**
  _High betweenness centrality (0.146) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `DocStore` (e.g. with `Task 3: Lossless page edits and export` and `Task 6: Scanning`) actually correct?**
  _`DocStore` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Should `test_app.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06386066763425254 - nodes in this community are weakly interconnected._