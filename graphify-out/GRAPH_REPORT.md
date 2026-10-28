# Graph Report - printhub  (2026-10-23)

## Corpus Check
- 50 files · ~37,302 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 16 file(s) not represented in the graph (top: (none) 6, .service 3, .css 2)

## Summary
- 841 nodes · 2331 edges · 32 communities (23 shown, 9 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 341 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `835a6e97`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- scan.js
- Sortable.min.js
- test_app.py
- documents.py
- test_mail.py
- create_app
- test_printing.py
- test_scanning.py
- test_edits.py
- test_convert.py
- pi-printhub design
- install.sh
- test_install.py
- app.py
- Config
- pathlib
- test_auth.py
- test_lifecycle.py
- PendingShares
- test_pwa.py
- LoginLimiter
- sw.js
- FakeCups
- package.json
- LICENSES.md
- record-job.sh
- start-virtual-printer.sh
- printhub
- retention.py
- test_email_rejects_oversized_exports_before_reading
- run

## God Nodes (most connected - your core abstractions)
1. `create_app()` - 78 edges
2. `DocStore` - 43 edges
3. `Page` - 27 edges
4. `Scanner` - 27 edges
5. `send()` - 26 edges
6. `normalize()` - 22 edges
7. `ApiError` - 21 edges
8. `Doc` - 20 edges
9. `apply_edits()` - 20 edges
10. `ScanSettings` - 20 edges

## Surprising Connections (you probably didn't know these)
- `Task 10: Front end: shell, login and print page` --references--> `sheetLayout()`  [INFERRED]
  docs/superpowers/plans/2026-10-21-pi-printhub.md → static/js/layout.js
- `Task 9: HTTP API` --references--> `require_session()`  [INFERRED]
  docs/superpowers/plans/2026-10-21-pi-printhub.md → printhub/auth.py
- `Task 8: Authentication and request security` --references--> `LoginLimiter`  [INFERRED]
  docs/superpowers/plans/2026-10-21-pi-printhub.md → printhub/auth.py
- `Task 9: HTTP API` --references--> `LoginLimiter`  [INFERRED]
  docs/superpowers/plans/2026-10-21-pi-printhub.md → printhub/auth.py
- `Task 8: Authentication and request security` --references--> `origin_ok()`  [INFERRED]
  docs/superpowers/plans/2026-10-21-pi-printhub.md → printhub/auth.py

## Import Cycles
- None detected.

## Communities (32 total, 9 thin omitted)

### Community 0 - "scan.js"
Cohesion: 0.05
Nodes (66): apiFetch(), errorMessage(), safeNext(), toLogin(), uploadFile(), rotateCrop(), snapCrop(), FRAMES (+58 more)

### Community 1 - "Sortable.min.js"
Cohesion: 0.06
Nodes (52): Verified facts the plan relies on, A(), b(), C(), D(), g(), h(), I() (+44 more)

### Community 2 - "test_app.py"
Cohesion: 0.09
Nodes (31): asgi_post(), receive(), jpeg_bytes(), make_client(), make(), multipart_head(), share_anon(), test_active_print_job_keeps_doc_busy() (+23 more)

### Community 3 - "documents.py"
Cohesion: 0.12
Nodes (22): Doc, _doc_from_json(), DocNotFound, DocStore, MailRecord, _now(), PrintRecord, test_export_jpeg_and_errors() (+14 more)

### Community 4 - "test_mail.py"
Cohesion: 0.10
Nodes (33): Task 7: Email, Attachment, AttachmentTooLarge, InvalidRecipient, MailError, RecentRecipients, _reply(), send() (+25 more)

### Community 5 - "create_app"
Cohesion: 0.08
Nodes (34): ApiError, _copy_limited(), create_app(), cancel_job(), claim(), conversion_error(), converted(), cups_conn() (+26 more)

### Community 6 - "test_printing.py"
Cohesion: 0.10
Nodes (32): Task 5: CUPS printing, Task 9: HTTP API, job(), printer_info(), active_job_ids(), _allowed(), cancel(), connect() (+24 more)

### Community 7 - "test_scanning.py"
Cohesion: 0.10
Nodes (37): Task 6: Scanning, get_scanner(), capabilities(), find_device(), ingest(), _last_error(), _max_mm(), _options() (+29 more)

### Community 8 - "test_edits.py"
Cohesion: 0.15
Nodes (37): Task 11: Front end: scan page and editor, Task 3: Lossless page edits and export, page_image(), Crop, Page, apply_edits(), _edited_pages(), export_jpeg() (+29 more)

### Community 9 - "test_convert.py"
Cohesion: 0.13
Nodes (27): Task 4: Upload normalization, _disposition(), ConversionError, detect(), _heic_to_pdf(), _image_to_pdf(), normalize(), _office_to_pdf() (+19 more)

### Community 10 - "pi-printhub design"
Cohesion: 0.05
Nodes (36): Hardware checklist, 10. Testing, 11. Installation and operations, 12. Repository layout, 1. Purpose, 2. Hardware and constraints, 3. System layout, 4. Data and configuration (+28 more)

### Community 11 - "install.sh"
Cohesion: 0.15
Nodes (27): allow_airscan_in_airsane(), ask(), ask_secret(), configure_dll_conf(), create_hplip_queue(), create_ipp_usb_queue(), die(), has_ipp_usb() (+19 more)

### Community 12 - "test_install.py"
Cohesion: 0.11
Nodes (26): Global Constraints, pi-printhub Implementation Plan, Review Focus, Task 10: Front end: shell, login and print page, Task 12: Progressive web app, Task 13: Deployment, Task 14: On-device acceptance, has_ipp_usb() (+18 more)

### Community 13 - "app.py"
Cohesion: 0.10
Nodes (8): _default_app(), DocPatch, EmailRequest, __getattr__(), Login, PageEdit, ScanRequest, set_session_cookie()

### Community 14 - "Config"
Cohesion: 0.16
Nodes (8): origin_ok(), Config, _BodyTooLarge, _error(), Guard, guarded_send(), limited_receive(), test_origin_check_guards_state_changing_requests()

### Community 15 - "pathlib"
Cohesion: 0.25
Nodes (9): Task 1: Project skeleton, config and CI, ConfigError, load_config(), test_defaults_applied(), test_env_override(), test_invalid_smtp_security(), test_load_full_config(), test_missing_required_key() (+1 more)

### Community 16 - "test_auth.py"
Cohesion: 0.15
Nodes (16): login(), hash_password(), issue_session(), require_session(), dependency(), session_valid(), verify_password(), cfg() (+8 more)

### Community 17 - "test_lifecycle.py"
Cohesion: 0.09
Nodes (19): client(), fake_cups(), scan_doc(), space(), store(), test_export_pdf_content_disposition(), test_patch_and_delete_conflict_while_scanning(), test_patch_rejects_bad_rotation_and_unknown_page() (+11 more)

### Community 18 - "PendingShares"
Cohesion: 0.32
Nodes (6): Task 8: Authentication and request security, PendingShares, test_pending_share_expiry_survives_unlink_failure(), test_pending_share_put_deletes_files_of_expired_entries(), test_pending_share_take_after_ttl_returns_none_and_deletes_file(), test_pending_share_take_within_ttl_returns_path_once()

### Community 19 - "test_pwa.py"
Cohesion: 0.27
Nodes (12): precache(), run_sw(), test_activate_deletes_old_caches(), test_cache_write_failure_does_not_discard_successful_network_response(), test_cached_shell_answers_when_fetch_rejects(), test_every_precached_path_exists_under_static(), test_fetch_handler_answers_shell_gets_and_passes_api_and_share_through(), test_fresh_network_response_wins_over_cached_shell() (+4 more)

### Community 20 - "LoginLimiter"
Cohesion: 0.43
Nodes (5): LoginLimiter, test_login_limiter_allows_attempt_after_window(), test_login_limiter_allows_five_and_denies_sixth_within_window(), test_login_limiter_drops_keys_once_their_attempts_expire(), test_login_limiter_keys_are_independent()

### Community 29 - "retention.py"
Cohesion: 0.23
Nodes (17): Task 2: Document store and retention, DocBusy, busy_policy(), busy(), _delete_if(), DiskFull, ensure_free_space(), main() (+9 more)

### Community 31 - "run"
Cohesion: 0.40
Nodes (4): test_share_admission_rejects_before_receiving_and_releases_slot(), run(), record(), request()

## Knowledge Gaps
- **2 isolated node(s):** `PYTHONDONTWRITEBYTECODE`, `printhub`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 199 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Task 3: Lossless page edits and export` connect `test_edits.py` to `Sortable.min.js`, `documents.py`, `test_install.py`?**
  _High betweenness centrality (0.133) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `create_app()` (e.g. with `admit_share()` and `Config`) actually correct?**
  _`create_app()` has 14 INFERRED edges - model-reasoned connections that need verification._
- **What connects `PYTHONDONTWRITEBYTECODE`, `printhub` to the rest of the system?**
  _2 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `scan.js` be split into smaller, more focused modules?**
  _Cohesion score 0.05350140056022409 - nodes in this community are weakly interconnected._
- **Why does `Review Focus` connect `test_install.py` to `test_mail.py`, `test_printing.py`, `test_scanning.py`, `test_edits.py`, `test_convert.py`, `pathlib`, `PendingShares`, `retention.py`?**
  _High betweenness centrality (0.111) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `DocStore` (e.g. with `Task 3: Lossless page edits and export` and `Task 6: Scanning`) actually correct?**
  _`DocStore` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Should `Sortable.min.js` be split into smaller, more focused modules?**
  _Cohesion score 0.062206572769953054 - nodes in this community are weakly interconnected._