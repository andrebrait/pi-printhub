# pi-printhub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Raspberry Pi service that shares a USB HP OfficeJet Pro 6970 as a network printer and scanner, and serves a web application at `https://print.brait.dev` for upload-to-print and scan, edit, export and email.

**Architecture:** CUPS, Avahi and AirSane expose the device to native clients. `printhub` is a FastAPI application on `127.0.0.1:8000` behind Traefik. It drives CUPS through `python3-cups`, SANE through the `scanimage` CLI, conversions through `img2pdf`, `heif-dec` and LibreOffice, and lossless page edits through `jpegtran`. State lives in per-document directories under `/var/lib/printhub/docs/`. The front end is plain HTML and ES modules with vendored Cropper.js and SortableJS, without a build step.

**Tech Stack:** Python 3.11+ from Debian Trixie packages only (no pip on the Pi), FastAPI, uvicorn, python3-cups, python3-img2pdf, python3-pil, python3-magic, python3-argon2, python3-itsdangerous, python3-multipart; pytest, httpx and aiosmtpd for tests; Node's built-in `node --test` for front-end pure functions; Traefik v3; bash.

**Spec:** `docs/superpowers/specs/2026-10-21-pi-printhub-design.md`

## Global Constraints

- License: AGPL-3.0-only. Every source file starts with `# SPDX-License-Identifier: AGPL-3.0-only` (or `//` in JS).
- Runtime dependencies come from Debian Trixie apt packages only. No virtualenv and no pip on the Pi.
- Python ≥ 3.11 (`tomllib`).
- Public origin: `https://print.brait.dev`. The app listens only on `127.0.0.1:8000`.
- Data root: `/var/lib/printhub` (configurable as `data_dir` for tests). Config file: `/etc/printhub/config.toml`, mode `0600`, path overridable by env `PRINTHUB_CONFIG`.
- Defaults: `retention_days = 30`, `min_free_gb = 2`, `printer_queue = "OfficeJet"`.
- Upload limit 100 MB. Email attachment limit 25 MB. LibreOffice timeout 120 s, one conversion at a time.
- A scanned page is encoded to JPEG exactly once, at quality 90, with JFIF density set to the scan resolution. No later step re-encodes it.
- Session: cookie `printhub_session`, `Secure; HttpOnly; SameSite=Lax`, max age 30 days. Login limit 5 per minute per client address.
- State-changing requests (POST, PUT, PATCH, DELETE) require `Origin: https://print.brait.dev`, except `POST /share`, which is protected by the session or the pending-share token (section 7 of the spec).
- Share tokens live 10 minutes.
- CI runs in a `debian:trixie` container on GitHub Actions.

## Verified facts the plan relies on

- `jpegtran -rotate 90 -trim -crop WxH+X+Y` applies the crop in rotated coordinates. A single invocation produces bytes identical to rotating then cropping in two invocations. `X` and `Y` snap down to the iMCU grid and `W`/`H` grow by the same amount. JFIF density is preserved with `-copy all`. (Tested with libjpeg-turbo from Debian Trixie: 100×60 at 4:2:0, rotated and trimmed to 48×100, then cropped `30x50+10+5` to 40×55.)
- Debian Trixie packages all of: `python3-fastapi`, `python3-uvicorn`, `python3-multipart`, `python3-argon2`, `python3-itsdangerous`, `python3-img2pdf`, `python3-pil`, `python3-magic`, `python3-cups`, `python3-aiosmtpd`, `libreoffice-nogui`, `libheif-examples`, `ipp-usb`, `hplip`, `sane-airscan`.
- HPLIP `models.dat` entry `officejet_pro_6970`: `plugin=0`, `scan-src=3`, `scan-type=7`, `usb-pid=0C54`.

## Review Focus

1. **Phone photos with an EXIF orientation**, uploaded to print, must print upright. A JPEG with EXIF orientation 6 must yield a PDF page displayed in portrait (Task 4).
2. **PNG with an alpha channel or a palette**, uploaded to print, must print on white instead of failing `img2pdf` (Task 4).
3. **Non-ASCII file names and titles**, such as `Relatório nº 3.pdf`, must survive upload, the download `Content-Disposition`, and email attachment headers (Tasks 7 and 9).
4. **A document feeder jam or a disconnect mid-batch** must keep the pages already scanned, clear the scanning flag, and report the error instead of leaving the document stuck (Task 6).
5. **A crop at or beyond the image edge after rotation**, including a full-image crop on a trimmed rotated page, must clamp to the image and never make `jpegtran` fail (Task 3).

---

### Task 1: Project skeleton, config and CI

**Files:**
- Create: `pyproject.toml`, `LICENSE` (AGPL-3.0 full text from https://www.gnu.org/licenses/agpl-3.0.txt), `printhub/__init__.py`, `printhub/config.py`, `tests/conftest.py`, `tests/test_config.py`, `.github/workflows/ci.yml`, `README.md` (replace the stub)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Config` with fields `password_hash: str`, `session_secret: str`, `smtp_host: str`, `smtp_port: int`, `smtp_security: Literal["starttls", "tls"]`, `smtp_user: str`, `smtp_password: str`, `mail_from: str`, `retention_days: int = 30`, `min_free_gb: float = 2`, `printer_queue: str = "OfficeJet"`, `data_dir: Path = Path("/var/lib/printhub")`, `origin: str = "https://print.brait.dev"`.
  - `load_config(path: Path | None = None) -> Config`: reads `path`, then env `PRINTHUB_CONFIG`, then `/etc/printhub/config.toml`. Raises `ConfigError` naming the missing or invalid key.
  - Fixture `cfg(tmp_path) -> Config` in `tests/conftest.py`, with `data_dir=tmp_path / "data"`, a known password hash for the password `"secret"`, and SMTP settings pointing at `127.0.0.1:8025`.

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py`:
  - `test_load_full_config`: a TOML file with all keys loads, and `retention_days`, `min_free_gb` and `printer_queue` keep their file values.
  - `test_defaults_applied`: a file without the four optional keys yields `30`, `2`, `"OfficeJet"` and `Path("/var/lib/printhub")`.
  - `test_missing_required_key`: without `smtp_host`, `ConfigError` is raised and its message contains `smtp_host`.
  - `test_invalid_smtp_security`: `smtp_security = "ssl"` raises `ConfigError`.
  - `test_env_override`: `PRINTHUB_CONFIG` set via `monkeypatch.setenv` is used when `path` is `None`.
- [ ] **Step 2:** Run `pytest tests/test_config.py -v`. Expected: FAIL, because `printhub.config` does not exist.
- [ ] **Step 3:** Implement `printhub/config.py` with `tomllib`. `pyproject.toml` declares the package, `requires-python = ">=3.11"`, no runtime `dependencies` (they come from apt), and `[tool.pytest.ini_options] testpaths = ["tests"]` plus markers `cups` and `sane`.
- [ ] **Step 4:** Run `pytest tests/test_config.py -v`. Expected: PASS.
- [ ] **Step 5:** Write `.github/workflows/ci.yml`: one job, `runs-on: ubuntu-latest`, `container: debian:trixie`.
  - Install with apt: `python3 python3-pytest python3-httpx python3-aiosmtpd python3-fastapi python3-uvicorn python3-multipart python3-argon2 python3-itsdangerous python3-img2pdf python3-pil python3-magic python3-cups cups cups-ipp-utils sane-utils libjpeg-turbo-progs poppler-utils libheif-examples libreoffice-writer-nogui nodejs shellcheck`.
  - Enable the SANE test backend: `echo test >> /etc/sane.d/dll.conf`.
  - Start `cupsd` and the virtual printer (Task 5 adds the script `tests/ci/start-virtual-printer.sh`; guard the step with `if [ -x tests/ci/start-virtual-printer.sh ]`).
  - Run `pytest -v`, `node --test static/js/`, and `shellcheck deploy/*.sh`. Guard the last two the same way until those paths exist.
- [ ] **Step 6:** Write `README.md`: what the project is, the hardware, a link to the spec, and "Install: see `deploy/install.sh`" (Task 13 fills that in).
- [ ] **Step 7: Commit** `chore: project skeleton, config loader and CI`.

### Task 2: Document store and retention

**Files:**
- Create: `printhub/documents.py`, `printhub/retention.py`, `tests/test_documents.py`, `tests/test_retention.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces:
  - `@dataclass class Crop: x: int; y: int; w: int; h: int`, in pixel coordinates of the page after rotation.
  - `@dataclass class Page: id: str; rotation: int = 0; crop: Crop | None = None`. `rotation` is in `{0, 90, 180, 270}`, clockwise.
  - `@dataclass class PrintRecord: job_id: int; at: str; options: dict[str, str]`
  - `@dataclass class MailRecord: at: str; to: list[str]; subject: str; format: str`
  - `@dataclass class Doc: id: str; kind: Literal["print", "scan"]; title: str; created: str; modified: str; pages: list[Page]; source_name: str | None = None; scan_resolution: int | None = None; scanning: bool = False; scan_error: str | None = None; prints: list[PrintRecord]; mails: list[MailRecord]`. Timestamps are ISO-8601 UTC.
  - `class DocStore(root: Path)`, where `root` is `data_dir / "docs"`:
    - `create(kind, title) -> Doc`: makes the directory with a `pages/` subdirectory and writes `doc.json`.
    - `get(id) -> Doc`: raises `DocNotFound`.
    - `save(doc) -> None`: sets `modified` to now and writes atomically (temp file plus `os.replace`).
    - `list(kind=None) -> list[Doc]`, newest `modified` first.
    - `delete(id) -> None`
    - `path(id) -> Path`
  - `DocNotFound(KeyError)`
  - `printhub/retention.py`:
    - `purge_expired(store, retention_days, now: datetime, busy: set[str]) -> list[str]`: deletes documents whose `modified` is older than `retention_days` and returns their IDs.
    - `ensure_free_space(store, min_free_bytes: int, busy: set[str], free_bytes: Callable[[], int]) -> None`: deletes the oldest non-busy documents until `free_bytes() >= min_free_bytes`, else raises `DiskFull`.
    - `DiskFull(Exception)`
    - `main()`: entry point for `python3 -m printhub.retention`, which loads the config and calls `purge_expired` with `busy=set()`.

- [ ] **Step 1: Write the failing tests.**
  - `test_documents.py`:
    - create, then get, round-trips every field, including a `Crop` and non-ASCII titles.
    - `list()` is ordered by `modified`.
    - `get` on a missing ID raises `DocNotFound`.
    - `save` leaves no `*.tmp` files behind.
    - IDs are URL-safe and unpredictable: 22 or more characters from `secrets.token_urlsafe`.
  - `test_retention.py`:
    - with docs aged 31, 29 and 40 days and `retention_days=30`, the 31- and 40-day docs are deleted.
    - an old busy doc survives `purge_expired`.
    - `ensure_free_space` deletes oldest first and stops as soon as `free_bytes()` meets the threshold. Use a fake `free_bytes` that grows as docs are deleted.
    - `ensure_free_space` skips busy docs.
    - `ensure_free_space` raises `DiskFull` when only busy docs remain and space is still short.
- [ ] **Step 2:** Run `pytest tests/test_documents.py tests/test_retention.py -v`. Expected: FAIL (import errors).
- [ ] **Step 3:** Implement both modules. `doc.json` is `dataclasses.asdict` output. Loading rebuilds the nested dataclasses explicitly.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat: document store with age and space-based retention`.

### Task 3: Lossless page edits and export

**Files:**
- Create: `printhub/edits.py`, `tests/test_edits.py`, `tests/fixtures/` (generated in the test with Pillow; no binary fixtures committed)

**Interfaces:**
- Consumes: `Doc`, `Page`, `Crop`, `DocStore` (Task 2).
- Produces:
  - `imcu(jpeg: Path) -> tuple[int, int]`: the iMCU width and height in pixels, derived from the luma sampling factors via Pillow (`Image.open(p).layer` or the quantization/sampling info). Grayscale and 4:4:4 give `(8, 8)`; 4:2:0 gives `(16, 16)`; 4:2:2 gives `(16, 8)`.
  - `rotated_size(w: int, h: int, rotation: int, mcu: tuple[int, int]) -> tuple[int, int]`: the image size after `jpegtran -rotate R -trim`. Partial iMCUs on the edges that the transform moves are trimmed.
  - `snap_crop(crop: Crop, size: tuple[int, int], mcu: tuple[int, int]) -> Crop`: clamps the crop to the image, snaps `x` and `y` down to multiples of the iMCU, and grows `w` and `h` so the right and bottom edges stay put, still clamped to the image.
  - `apply_edits(src: Path, page: Page, dst: Path) -> None`: runs one `jpegtran -copy all [-rotate R -trim] [-crop WxH+X+Y]` invocation. It copies the file unchanged when there is no rotation and no crop. `mcu` is swapped for 90° and 270°.
  - `page_info(src: Path, page: Page) -> dict`: returns `{"width", "height", "mcu": [w, h]}` for the rotated page, which the editor uses to draw the snapped box.
  - `export_pdf(store, doc, out: Path) -> None`: applies edits to every page into a temp directory, then `img2pdf.convert` writes the PDF.
  - `export_jpeg(store, doc, out_dir: Path) -> Path`: returns `<title>.jpg` for one page, or `<title>.zip` holding `01.jpg`, `02.jpg`, and so on for several.

- [ ] **Step 1: Write the failing tests.** Generate JPEGs with Pillow at quality 90 and 300 dpi, in 4:2:0 color and in grayscale.
  - `test_imcu_values`: 4:2:0 gives `(16, 16)`; grayscale gives `(8, 8)`.
  - `test_rotated_size`: 100×60 at 4:2:0 rotated 90° gives `(48, 100)`, and 180° gives `(96, 48)`. Assert both against Pillow's reading of the actual `jpegtran` output, so the formula is checked against the tool.
  - `test_snap_crop_boundaries`: parametrize over `x, y` in `{0, 7, 8, 15, 16, 17}` with `mcu=(16, 16)` and `(8, 8)`. The snapped `x` and `y` are multiples of the iMCU, `x + w` and `y + h` are unchanged, and everything stays inside the image.
  - `test_snap_crop_clamps` (Review Focus 5): a crop extending past the right and bottom edges, a crop with negative `x`, and a full-image crop on a rotated, trimmed page all yield valid crops, and `apply_edits` succeeds on each.
  - `test_apply_edits_matches_pillow_size`: for each rotation in `{0, 90, 180, 270}` with a crop, the output size equals `snap_crop(...)`'s `w`×`h`.
  - `test_export_pdf_lossless`: the JPEG stream of each PDF page (read with `pikepdf` if available, else by locating the `/DCTDecode` stream bytes) is byte-identical to the `apply_edits` output for that page.
  - `test_export_pdf_page_size`: a 2480×3508 page at 300 dpi yields a 595×842 pt page (±1 pt).
  - `test_export_jpeg_single_and_zip`: one page gives `.jpg`; three pages give `.zip` with `01.jpg`–`03.jpg` in page order.
  - `test_original_untouched`: the source JPEG's bytes are unchanged after every operation.
- [ ] **Step 2:** Run `pytest tests/test_edits.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `printhub/edits.py`. `jpegtran` runs via `subprocess.run(check=True)` with file arguments, never a shell.
- [ ] **Step 4:** Run `pytest tests/test_edits.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: lossless jpegtran page edits and PDF/JPEG export`.

### Task 4: Upload normalization

**Files:**
- Create: `printhub/convert.py`, `tests/test_convert.py`, `tests/fixtures/sample.docx` (a short committed document with a heading and a table)

**Interfaces:**
- Produces:
  - `ACCEPTED: dict[str, str]`: MIME type to handler name (`"pdf"`, `"image"`, `"heic"`, `"office"`). Keys:
    - `application/pdf`, `image/jpeg`, `image/png`, `image/heic`, `image/heif`
    - `application/msword`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `application/vnd.oasis.opendocument.text`, `text/rtf`, `text/plain`
    - `application/vnd.ms-excel`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `application/vnd.oasis.opendocument.spreadsheet`
    - `application/vnd.ms-powerpoint`, `application/vnd.openxmlformats-officedocument.presentationml.presentation`, `application/vnd.oasis.opendocument.presentation`
  - `detect(path: Path) -> str`: libmagic MIME type via `magic.from_file(mime=True)`. Raises `Unsupported(mime)` when the type is not in `ACCEPTED`.
  - `normalize(src: Path, out_pdf: Path) -> None`: dispatches on `detect`. Raises `ConversionError(message)`.
  - `pdf_page_sizes(pdf: Path) -> list[tuple[float, float]]`: points, from `pdfinfo -f 1 -l N`.
  - `render_preview(pdf: Path, out_dir: Path, width: int = 600) -> list[Path]`: `pdftoppm -png -scale-to-x 600 -scale-to-y -1`.
  - Module constant `SOFFICE = "soffice"` and `SOFFICE_TIMEOUT = 120`, patchable in tests. A module-level `threading.Lock` serializes LibreOffice.
- Handler rules:
  - PDF: copy as-is.
  - JPEG: `img2pdf.convert(src, rotation=img2pdf.Rotation.ifvalid)`, so EXIF orientation becomes `/Rotate`.
  - PNG: when the mode is `RGBA`, `LA` or `P` with transparency, flatten onto white with Pillow into a temporary lossless PNG first, then `img2pdf`.
  - HEIC: `heif-dec -q 92 src tmp.jpg`, then the JPEG path.
  - Office and text: `soffice --headless --norestore -env:UserInstallation=file://<fresh tempdir> --convert-to pdf --outdir <tmp> src`, started in a new session (`start_new_session=True`). On timeout, kill the whole process group (`os.killpg`) and raise `ConversionError("LibreOffice timed out after 120 s")`.

- [ ] **Step 1: Write the failing tests.**
  - `test_detect_rejects_unknown`: a ZIP of random files raises `Unsupported`.
  - `test_jpeg_exif_rotation` (Review Focus 1): a 400×300 JPEG with EXIF orientation 6 yields a PDF whose single page has `/Rotate 90`, or a portrait media box.
  - `test_png_alpha_flattened` (Review Focus 2): RGBA and palette-with-transparency PNGs convert without error, and the rendered preview pixel in a transparent area is white.
  - `test_heic_converts`: skipped when `heif-dec` is missing. Create the HEIC with `heif-enc` from a generated PNG.
  - `test_docx_converts`: `sample.docx` yields a 1-page PDF. Mark `@pytest.mark.slow`.
  - `test_soffice_timeout_kills_group`: point `SOFFICE` at a temporary script that spawns `sleep 600 &` and then sleeps, and set `SOFFICE_TIMEOUT=1`. `ConversionError` is raised, and no `sleep 600` from that group remains (check with `pgrep -g <pgid>`).
  - `test_pdf_page_sizes`: an A4 PDF returns `[(595.28, 841.89)]` (±0.5).
- [ ] **Step 2:** Run `pytest tests/test_convert.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `printhub/convert.py`.
- [ ] **Step 4:** Run `pytest tests/test_convert.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: normalize uploads to PDF`.

### Task 5: CUPS printing

**Files:**
- Create: `printhub/printing.py`, `tests/test_printing.py`, `tests/ci/start-virtual-printer.sh`, `tests/ci/record-job.sh`

**Interfaces:**
- Produces:
  - `@dataclass class PrinterInfo`:
    - `state: Literal["idle", "processing", "stopped"]`
    - `state_reasons: list[str]` (`"none"` filtered out)
    - `accepting: bool`
    - `supported: dict[str, list[str]]`, keyed by `media`, `print-quality`, `print-color-mode`, `orientation-requested`, `sides` and `print-scaling`
    - `defaults: dict[str, str]`, with the same keys
    - `margins: dict[str, int]` (`top`, `bottom`, `left`, `right` in hundredths of a millimeter, from `media-*-margin-supported`, taking the minimum)
  - `@dataclass class PrintOptions`:
    - `media: str`
    - `print_quality: str`: IPP enum values as strings, `"3"` draft, `"4"` normal, `"5"` high
    - `print_color_mode: str`
    - `orientation_requested: str` (`"3"` portrait, `"4"` landscape)
    - `sides: str`
    - `copies: int = 1`
    - `page_ranges: str | None = None`: validated against `^\d+(-\d+)?(,\d+(-\d+)?)*$`
    - `print_scaling: str = "fit"`
  - `@dataclass class JobStatus: state: Literal["pending", "held", "processing", "stopped", "canceled", "aborted", "completed"]; reasons: list[str]`
  - `printer_info(conn, queue) -> PrinterInfo`
  - `validate(opts: PrintOptions, info: PrinterInfo) -> None`: raises `ValueError` naming the first unsupported option. `copies` must be 1–99.
  - `submit(conn, queue, pdf: Path, title: str, opts: PrintOptions) -> int`: calls `conn.printFile` with IPP attribute names as option keys.
  - `job_status(conn, job_id) -> JobStatus`
  - `cancel(conn, job_id) -> None`
  - `active_job_ids(conn, queue) -> set[int]`: jobs not yet completed, canceled or aborted.
  - `connect() -> cups.Connection`
- Virtual printer for CI and tests:
  - `tests/ci/start-virtual-printer.sh` starts `cupsd`, then `ippeveprinter -2 -k -c tests/ci/record-job.sh -f application/pdf,image/pwg-raster -p 8631 TestPrinter &`, waits for the port, and runs `lpadmin -p TestPrinter -E -v ipp://localhost:8631/ipp/print -m everywhere`.
  - `record-job.sh` appends the `IPP_*` environment of each job to `$PRINTHUB_JOB_LOG` (default `/tmp/printhub-jobs.log`), one line per job.
  - Tests marked `cups` skip when env `PRINTHUB_TEST_QUEUE` is unset. CI sets it to `TestPrinter`.

- [ ] **Step 1: Write the failing tests.**
  - Pure tests:
    - `validate` rejects `sides="two-sided-long-edge"` when it is not in `supported`.
    - `validate` rejects page ranges `"3-"`, `"a"` and `"1,,2"`, and accepts `"1-3,5"`.
    - `validate` rejects `copies=0` and `copies=100`.
  - `@pytest.mark.cups`:
    - `printer_info` on `TestPrinter` reports `two-sided-long-edge` in `supported["sides"]`, and a non-empty `media` list.
    - Submitting a one-page PDF with `sides=two-sided-short-edge`, `print-color-mode=monochrome`, `orientation-requested=4`, `print-quality=5`, `media=iso_a4_210x297mm` and `copies=2` reaches the job log with those values: `IPP_SIDES=two-sided-short-edge`, and so on.
    - `job_status` eventually reaches `completed`, polling up to 30 s.
    - `cancel` on a held job (`job-hold-until=indefinite`) yields `canceled`.
- [ ] **Step 2:** Run `pytest tests/test_printing.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `printhub/printing.py` and the two CI scripts. Remove the guard on the CI step from Task 1.
- [ ] **Step 4:** Run `tests/ci/start-virtual-printer.sh && PRINTHUB_TEST_QUEUE=TestPrinter pytest tests/test_printing.py -v` in a `debian:trixie` container. Expected: PASS. CI must also pass.
- [ ] **Step 5: Commit** `feat: CUPS printer capabilities, job submission and status`.

### Task 6: Scanning

**Files:**
- Create: `printhub/scanning.py`, `tests/test_scanning.py`, `tests/fixtures/scanimage-A.txt` (captured `scanimage -d test -A` output)

**Interfaces:**
- Consumes: `DocStore`, `Doc`, `Page` (Task 2).
- Produces:
  - `@dataclass class ScanSettings: source: Literal["flatbed", "adf"]; resolution: int = 300; mode: Literal["color", "gray"] = "color"; area: Literal["a4", "letter", "full"] = "a4"`
  - `@dataclass class ScannerCaps: device: str; sources: list[str]; resolutions: list[int]; modes: list[str]; bed_mm: tuple[float, float]`. `sources` and `modes` use our names (`flatbed`/`adf`, `color`/`gray`).
  - `parse_options(text: str) -> tuple[dict[str, str], ScannerCaps]`: parses `scanimage -A` output.
    - Source: `flatbed` maps to the device value containing "flatbed". `adf` maps to the one containing "adf" or "feeder" (case-insensitive).
    - Mode: `gray` maps to "gray" or "grey"; `color` maps to "color".
    - Resolutions: from a list (`75|150|300dpi`) or a range (`50..600dpi`), offering 150, 200, 300 and 600 within the range.
    - Bed size: from `-x 0..W` and `-y 0..H`.
    - Returns the mapping of our names to device values, plus the caps.
  - `find_device() -> str | None`: the first device from `scanimage -f "%d%n"`. Env `PRINTHUB_SCANNER` overrides it, and tests set `test`.
  - `capabilities(device) -> ScannerCaps`. Raises `ScannerUnavailable`.
  - `class Scanner`: holds a `threading.Lock`. `start(store, doc_id, settings) -> None` raises `ScannerBusy` when its lock is held. Otherwise it sets `doc.scanning = True`, clears `scan_error`, and runs the scan in a background thread.
    - Flatbed runs `scanimage --format=png -o page.png`.
    - ADF runs `scanimage --format=png --batch=<tmp>/p%04d.png --batch-print`. Each printed path is ingested as soon as its line arrives.
    - `ingest(png, dest_jpg, resolution)`: Pillow saves JPEG quality 90 with `dpi=(r, r)` and deletes the PNG.
    - Area: A4 is 210×297 mm, Letter 215.9×279.4 mm, full is `bed_mm`, each clamped to `bed_mm`, and passed as `-x`/`-y`.
    - Every page is appended to `doc.pages` and saved immediately.
    - On a non-zero exit: stderr containing "busy" maps to `scan_error = "Scanner busy"`, anything else to `scan_error = "Scanner error: <last stderr line>"`. `scanning` is set back to `False` in a `finally` block. An ADF run that stops at "Document feeder out of documents" is success, not an error.
  - `SCANIMAGE = "scanimage"`, patchable.
  - `ScannerBusy(Exception)`, `ScannerUnavailable(Exception)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_parse_options_test_backend`: the fixture yields `flatbed` and `adf` sources, `color` and `gray` modes, and a non-empty bed size.
  - `test_parse_options_hpaio_style`: an inline sample with `--source Flatbed|ADF`, `--mode Lineart|Gray|Color` and `--resolution 75|100|150|200|300|600|1200dpi` maps correctly.
  - `@pytest.mark.sane`, skipped unless `scanimage -L` lists `test`:
    - a flatbed scan adds one page whose JPEG has quality ≈90 (Pillow `quantization` matches Pillow's own q90 tables) and `dpi == (300, 300)`.
    - an ADF scan with the test backend set to produce three pages (`--source "Automatic Document Feeder"`, the test backend's default ADF page count) adds three pages in order.
  - `test_busy_lock`: a second `start` while the first holds the lock raises `ScannerBusy`.
  - `test_failure_mid_batch_keeps_pages` (Review Focus 4): `SCANIMAGE` points at a temporary script that writes one PNG, prints its path, writes "Error during device I/O" to stderr, and exits 1. Afterwards the doc has one page, `scanning is False`, and `scan_error` starts with `"Scanner error"`.
  - `test_busy_stderr_maps`: a fake scanimage that writes "Device busy" and exits 1 yields `scan_error == "Scanner busy"`.
- [ ] **Step 2:** Run `pytest tests/test_scanning.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `printhub/scanning.py`. Capture the fixture with `scanimage -d test -A > tests/fixtures/scanimage-A.txt` inside the CI container.
- [ ] **Step 4:** Run `pytest tests/test_scanning.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: SANE scanning with single-encode JPEG ingest`.

### Task 7: Email

**Files:**
- Create: `printhub/mail.py`, `tests/test_mail.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces:
  - `MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024`
  - `@dataclass class Attachment: filename: str; data: bytes; mime: str`
  - `send(cfg: Config, to: list[str], subject: str, attachments: list[Attachment]) -> None`
    - Raises `AttachmentTooLarge` when the attachments total more than `MAX_ATTACHMENT_BYTES`.
    - Raises `InvalidRecipient(addr)` for an address that `email.headerregistry.Address` cannot parse or that lacks `@`.
    - Raises `MailError(str)` carrying the SMTP server's response text verbatim, for example `"535 5.7.8 Username and Password not accepted"`.
    - Uses `smtplib.SMTP` plus `starttls()` or `smtplib.SMTP_SSL` according to `smtp_security`. The message is an `email.message.EmailMessage`; attachment file names go through `add_attachment(filename=...)`, which applies RFC 2231 encoding.
  - `RecentRecipients(path: Path)`, stored in `data_dir / "recipients.json"`:
    - `add(addrs: list[str]) -> None`: keeps the 20 most recent, de-duplicated case-insensitively.
    - `list() -> list[str]`

- [ ] **Step 1: Write the failing tests.** Run an `aiosmtpd.controller.Controller` on `127.0.0.1:8025` with a handler that records messages and accepts plain SMTP (set `smtp_security="starttls"` with `require_starttls=False` on the controller; the client skips STARTTLS when the server does not advertise it).
  - `test_send_two_recipients_pdf`: two recipients, subject `"Scan 2026-10-21 14-32"`, and one PDF attachment arrive. The attachment bytes match.
  - `test_unicode_filename` (Review Focus 3): attachment `Relatório nº 3.pdf` decodes back to the same name with `get_filename()`.
  - `test_too_large`: 25 MB + 1 byte raises `AttachmentTooLarge`, and no connection is made.
  - `test_server_error_verbatim`: a handler returning `"550 5.1.1 mailbox unavailable"` raises `MailError` whose text contains that string.
  - `test_invalid_recipient`: `"bob"` raises `InvalidRecipient`.
  - `test_recent_recipients`: the order is newest first, there are no case-insensitive duplicates, and the list is capped at 20.
- [ ] **Step 2:** Run `pytest tests/test_mail.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `printhub/mail.py`. Add `python3-aiosmtpd` to the CI packages if it is missing.
- [ ] **Step 4:** Run `pytest tests/test_mail.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: SMTP sending and recent recipients`.

### Task 8: Authentication and request security

**Files:**
- Create: `printhub/auth.py`, `tests/test_auth.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces:
  - `hash_password(pw: str) -> str` and `verify_password(hash: str, pw: str) -> bool` (`argon2.PasswordHasher`).
  - `issue_session(cfg) -> str`, a value signed with `itsdangerous.TimestampSigner(cfg.session_secret)`, and `session_valid(cfg, value: str | None) -> bool` (`max_age = 30 * 86400`).
  - `COOKIE = "printhub_session"` and `set_session_cookie(response, value)` with `secure=True, httponly=True, samesite="lax", max_age=30*86400, path="/"`.
  - `class LoginLimiter(limit=5, window=60.0)` with `allow(key: str, now: float) -> bool`. It counts every attempt, successful or not, within a sliding window.
  - `class PendingShares(ttl=600.0)`: `put(path: Path, filename: str, now) -> str` (a `secrets.token_urlsafe(32)` token), and `take(token, now) -> tuple[Path, str] | None`, which removes the entry and deletes expired files.
  - `origin_ok(method: str, origin: str | None, cfg) -> bool`: true for GET, HEAD and OPTIONS; otherwise `origin == cfg.origin`.
  - FastAPI dependency `require_session(request) -> None`: raises `HTTPException(401)`.

- [ ] **Step 1: Write the failing tests.**
  - The hash round-trip verifies, and a wrong password fails.
  - A session issued now is valid. One issued 31 days ago is not (patch `time.time` through itsdangerous's `get_timestamp`). One with a tampered signature is not.
  - `LoginLimiter`: 5 attempts are allowed, the 6th within 60 s is denied, and an attempt at t+61 s is allowed. Keys are independent.
  - `PendingShares`: `take` within 600 s returns the path. A second `take` returns `None`. A take after 601 s returns `None`, and the file is gone.
  - `origin_ok`: POST with `https://print.brait.dev` is true; POST with `https://evil.example` or no Origin is false; GET without Origin is true.
- [ ] **Step 2:** Run `pytest tests/test_auth.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `printhub/auth.py`.
- [ ] **Step 4:** Run `pytest tests/test_auth.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: password, sessions, login limit, share tokens, origin check`.

### Task 9: HTTP API

**Files:**
- Create: `printhub/app.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: everything in Tasks 1–8.
- Produces: `create_app(cfg: Config, *, cups_conn_factory=printing.connect, scanner: Scanner | None = None, free_bytes=None) -> FastAPI`, and module-level `app = create_app(load_config())`, which is created lazily so tests can import the module without a config.
- Middleware:
  - The origin check (`origin_ok`; 403 on failure) applies to every route except `POST /share`.
  - Every `/api/*` route except `POST /api/login` uses `require_session`.
- Busy set for retention: `{doc.id for docs with scanning}` ∪ the doc IDs whose `prints` hold a job ID in `active_job_ids`.
- Routes (JSON unless noted):

| Method and path | Behavior | Errors |
|---|---|---|
| `POST /api/login` `{password}` | Sets the cookie; returns 204 | 401 bad password; 429 when `LoginLimiter` denies, keyed by `request.client.host` (uvicorn runs with `--proxy-headers --forwarded-allow-ips 127.0.0.1`) |
| `POST /api/logout` | Clears the cookie; returns 204 | |
| `GET /api/printer` | `PrinterInfo` | 503 `{"error": "CUPS unavailable"}` |
| `POST /api/uploads` multipart `file` | Calls `ensure_free_space`, streams to `source.<ext>` while counting bytes, then `normalize` → `print.pdf` and `render_preview` → `thumbs/`. Creates a `print` doc titled with the file name stem, with `source_name` set. Returns `{doc_id}` | 413 over 100 MB (partial file removed); 415 `{"error": "Unsupported file type: <mime>"}`; 422 `{"error": <ConversionError>}` (the doc is kept so Retry works); 507 `{"error": "Disk full"}` |
| `POST /api/docs/{id}/convert` | Retries normalization for a print doc | 422 as above |
| `GET /api/docs?kind=` | List of `{id, kind, title, modified, page_count}` | |
| `GET /api/docs/{id}` | The doc. Scan pages include `page_info` (Task 3); print docs include `pdf_pages: [[w_pt, h_pt], ...]` and `preview_count` | 404 |
| `PATCH /api/docs/{id}` `{title?, pages?: [{id, rotation, crop}]}` | The `pages` list sets the order; omitted pages are deleted from `pages/`. Rotation must be in {0, 90, 180, 270}. Crops are stored after `snap_crop`. Returns the doc | 400 with a validation message; 409 while `scanning` |
| `DELETE /api/docs/{id}` | 204 | 409 when busy |
| `GET /api/docs/{id}/pages/{page_id}?size=thumb\|full` | `image/jpeg` of the original. `thumb` is 400 px on the long side, cached in `thumbs/<page_id>.jpg`, made with Pillow `draft` | 404 |
| `GET /api/docs/{id}/preview/{n}` | `image/png` from `thumbs/` | 404 |
| `GET /api/scanner` | `ScannerCaps` | 503 `{"error": "Scanner unavailable"}` |
| `POST /api/scans` `{settings, doc_id?}` | Calls `ensure_free_space`, creates or reuses a scan doc (title `Scan YYYY-MM-DD HH-MM` in local time), and calls `Scanner.start`. Returns 202 `{doc_id}` | 409 `{"error": "Scanner busy"}`; 503; 507 |
| `GET /api/docs/{id}/export?format=pdf\|jpeg` | The file, with `Content-Disposition` built by `urllib.parse.quote` in the `filename*=UTF-8''...` form plus an ASCII fallback | 400 for a bad format |
| `POST /api/docs/{id}/print` `PrintOptions` | Scan docs are exported to `print.pdf` first. Then `validate`, `submit`, and a `PrintRecord` is appended. Returns `{job_id}` | 400 `ValueError` text; 503 |
| `GET /api/jobs/{job_id}` | `{job: JobStatus, printer: {state, state_reasons}}` | 404 for an unknown job |
| `POST /api/jobs/{job_id}/cancel` | 204 | |
| `POST /api/docs/{id}/email` `{to: [...], subject, format: "pdf"\|"jpeg"}` | Builds attachments (one PDF, or one JPEG per page), sends, records a `MailRecord`, and updates `RecentRecipients`. Returns 204 | 400 `InvalidRecipient`; 413 `{"error": "Attachments exceed 25 MB"}`; 502 `{"error": <MailError text>}` |
| `GET /api/recipients` | List of strings | |
| `POST /share` multipart `file` | With a session: same as upload, then 303 to `/print.html?doc=<id>`. Without one: saves to `data_dir/pending/`, `PendingShares.put`, then 303 to `/login.html?share=<token>` | 413/415 as upload |
| `POST /api/share/{token}/claim` | With a session: turns the pending file into a print doc (same pipeline as upload). Returns `{doc_id}` | 410 when expired or unknown |
| `GET /` and static files | `StaticFiles(directory="static", html=True)` mounted last. `/` serves `index.html` | |

- [ ] **Step 1: Write the failing tests** with `fastapi.testclient.TestClient`. Pass `base_url="https://print.brait.dev"` and a default `Origin` header. Use a fake CUPS connection object exposing `getPrinterAttributes`, `printFile`, `getJobAttributes`, `cancelJob` and `getJobs`, a `Scanner` with `SCANIMAGE` pointed at a fake script, and an injected `free_bytes`.
  - Auth:
    - `/api/docs` without a cookie returns 401.
    - Login with the wrong password returns 401, and the 6th attempt returns 429.
    - A POST with `Origin: https://evil.example` returns 403 even with a valid cookie.
  - Upload:
    - A JPEG uploaded as `Relatório nº 3.jpg` (Review Focus 3) yields a doc titled `Relatório nº 3`, a PDF, and at least one preview.
    - An upload over 100 MB (use a generator body) returns 413 and leaves no doc directory.
    - A `.exe` returns 415.
    - With `free_bytes` reporting low space and one old doc present, the old doc is deleted and the upload succeeds. With only busy docs, the upload returns 507.
  - Editing: PATCH reorders, deletes one page (its file is gone), rotates, and stores a snapped crop. A rotation of `45` returns 400.
  - Export: the `Content-Disposition` of a PDF export for the title `Relatório nº 3` contains `filename*=UTF-8''Relat%C3%B3rio%20n%C2%BA%203.pdf`.
  - Print: `/api/docs/{id}/print` calls the fake `printFile` with `{"sides": "two-sided-long-edge", ...}` and records the job. Unsupported `sides` returns 400.
  - Scans: `POST /api/scans` returns 202. Polling `GET /api/docs/{id}` shows `scanning` going from true to false and pages appearing. A second concurrent POST returns 409.
  - Email: `/api/docs/{id}/email` against the aiosmtpd controller from Task 7 returns 204, and `/api/recipients` then lists the address.
  - Share:
    - `POST /share` without a session redirects 303 to `/login.html?share=`.
    - After login, `POST /api/share/{token}/claim` returns a `doc_id`.
    - A second claim returns 410.
- [ ] **Step 2:** Run `pytest tests/test_app.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `printhub/app.py`. Blocking work (conversion, CUPS, SMTP, export) runs in `run_in_threadpool`.
- [ ] **Step 4:** Run `pytest -v` (the full suite). Expected: PASS.
- [ ] **Step 5: Commit** `feat: HTTP API`.

### Task 10: Front end: shell, login and print page

**Files:**
- Create:
  - `static/index.html`, `static/login.html`, `static/print.html`, `static/css/app.css`
  - `static/js/api.js`, `static/js/print.js`, `static/js/layout.js`, `static/js/layout.test.js`
  - `static/vendor/` (empty until Task 11)

**Interfaces:**
- Consumes: the HTTP API (Task 9).
- Produces:
  - `api.js`: `apiFetch(path, opts) -> Promise<Response>`, which sends JSON and redirects to `/login.html?next=<current>` on 401.
  - `layout.js`, pure ES module functions:
    - `mediaSizeMm(name: string) -> {w, h} | null`: parses PWG self-describing names such as `iso_a4_210x297mm` or `na_letter_8.5x11in` into millimeters.
    - `sheetLayout({sheetMm, landscape, marginsHmm, pagePt, scaling}) -> {sheet: {w, h}, printable: {x, y, w, h}, page: {x, y, w, h}}`, all in millimeters.
      - `fit` is contain within the printable area, centered.
      - `fill` is cover over the printable area, centered (the page may overflow and be clipped).
      - `none` uses the actual size (pt × 25.4/72), centered.
      - `landscape` swaps the sheet's width and height.
    - `parsePageRanges(str, count) -> number[]`, 1-based. Returns `null` when invalid, using the same grammar as Task 5.
    - `sheetsFor(pages: number[], sides: string) -> Array<{front, back|null}>`
- Pages:
  - `index.html`:
    - the printer status banner (`/api/printer`, refreshed every 10 s; shown when `state` is `stopped` or `state_reasons` is not empty)
    - "Print a file" (upload), "New scan" and "Recent" lists (prints and scans) linking to their pages
    - upload by drag and drop or file picker, with a progress bar (XHR `upload.onprogress`), then navigation to `print.html?doc=`
  - `login.html`: a password form. After success it claims `?share=` if present, then goes to `?next=` or `/`.
  - `print.html?doc=`: the option controls built from `/api/printer`, offering only supported values with defaults preselected. Labels: quality "Draft", "Normal" and "Best" for 3, 4 and 5; sides "One-sided", "Two-sided (long edge)" and "Two-sided (short edge)". Copies and page range inputs. The scaling select is preset to `none` when the doc kind is `scan`.
    - The preview draws each sheet as a div sized from `sheetLayout`, with the page `<img>` positioned and clipped inside it. Grayscale applies `filter: grayscale(1)`. Two-sided shows front and back pairs with a "flip on long/short edge" label.
    - Print starts a job card that polls `/api/jobs/{id}` every 2 s until a terminal state. It shows the state, the printer reasons, and Cancel.
    - Conversion errors show the server's message with a Retry button (`/convert`).

- [ ] **Step 1: Write the failing tests** in `static/js/layout.test.js` (`node:test`, `node:assert`):
  - `mediaSizeMm("iso_a4_210x297mm")` returns `{w: 210, h: 297}`; `"na_letter_8.5x11in"` returns `{w: 215.9, h: 279.4}`; `"custom_foo"` returns `null`.
  - `sheetLayout` with an A4 sheet, 3 mm margins, an A4 page and `fit` gives a page width of 204 mm (±0.1), centered.
  - With `none` and a Letter page on an A4 sheet, the page width is 215.9 mm and may exceed the printable area.
  - With `fill`, the page covers the printable area on both axes.
  - `landscape` swaps the sheet's dimensions.
  - `parsePageRanges("1-3,5", 6)` returns `[1, 2, 3, 5]`; `"7"` with 6 pages returns `null`; `"3-1"` returns `null`.
  - `sheetsFor([1, 2, 3], "two-sided-long-edge")` returns `[{front: 1, back: 2}, {front: 3, back: null}]`.
- [ ] **Step 2:** Run `node --test static/js/`. Expected: FAIL.
- [ ] **Step 3:** Implement `layout.js`, then the pages. Unguard the `node --test` step in CI.
- [ ] **Step 4:** Run `node --test static/js/`. Expected: PASS.
- [ ] **Step 5: Smoke test.** Run `PRINTHUB_CONFIG=<test config> python3 -m uvicorn printhub.app:app --port 8000` against the virtual printer, with a reverse proxy or browser flag that accepts `Secure` cookies on localhost. Upload a PDF, a photo and the DOCX fixture. Check that the preview changes with each option, then print to `TestPrinter` and see the job card reach "completed". Record screenshots in the PR.
- [ ] **Step 6: Commit** `feat: web shell, login and print page with preview`.

### Task 11: Front end: scan page and editor

**Files:**
- Create:
  - `static/scan.html`, `static/js/scan.js`, `static/js/crop.js`, `static/js/crop.test.js`
  - `static/vendor/cropper/` (Cropper.js 1.x `cropper.min.js` and `cropper.min.css`, MIT)
  - `static/vendor/sortable/Sortable.min.js` (SortableJS, MIT)
  - `static/vendor/LICENSES.md`, listing each vendored file with its version, license and source URL

**Interfaces:**
- Consumes: the HTTP API (Task 9); `page_info` (`width`, `height`, `mcu`) per page.
- Produces: `crop.js`, pure functions:
  - `snapCrop(crop, size, mcu) -> crop`: the same rules as `snap_crop` in Task 3.
  - `rotateCrop(crop, size, delta) -> crop`: maps an existing crop rectangle through a ±90° rotation, so a crop survives rotating the page.
- Page `scan.html?doc=` (no `doc` creates a new one on the first scan):
  - Settings from `/api/scanner`: source, resolution, mode and area. Buttons: "Scan page" (flatbed) or "Scan feeder" (ADF).
  - While `scanning`, poll `GET /api/docs/{id}` every 1 s, append new thumbnails, and show `scan_error` with a Retry button.
  - The thumbnail grid uses SortableJS. Each page shows its rotation through CSS `transform: rotate()` and its crop as a clip overlay. Per-page buttons: rotate left, rotate right, crop, delete, reset.
  - Each change sends a `PATCH` with the full `pages` array and re-renders from the response.
  - The crop modal loads `?size=full` into Cropper.js with `rotatable` and `viewMode: 1`. The aspect ratio is free or locked to the selected paper. On `cropend`, the box is redrawn with `snapCrop`, so what the user sees matches the export.
  - Export panel: the title (editable, PATCH on blur); Download PDF; Download JPEG; Email (recipients input with a `<datalist>` from `/api/recipients`, the subject defaulting to the title, and a PDF/JPEG choice; server errors are shown verbatim); and Print, which opens `print.html?doc=<id>`.

- [ ] **Step 1: Write the failing tests** in `static/js/crop.test.js`:
  - `snapCrop` gives the same results as the Python boundary table in Task 3. Copy the parametrized cases as literal expectations.
  - `rotateCrop` four times by +90 returns the original crop.
  - `rotateCrop` keeps the crop inside the new size after a trim (size from Task 3's `rotated_size` examples).
- [ ] **Step 2:** Run `node --test static/js/`. Expected: FAIL.
- [ ] **Step 3:** Implement `crop.js`, `scan.js` and `scan.html`, and vendor the two libraries.
- [ ] **Step 4:** Run `node --test static/js/`. Expected: PASS.
- [ ] **Step 5: Smoke test** with the SANE `test` backend:
  1. Scan 3 ADF pages.
  2. Reorder them, rotate one, crop one, and delete one.
  3. Download the PDF and check the page order, orientation and crop in a viewer.
  4. Email it to the aiosmtpd debug server (`python3 -m aiosmtpd -n -l 127.0.0.1:8025`).
- [ ] **Step 6: Commit** `feat: scan page and lossless page editor`.

### Task 12: Progressive web app

**Files:**
- Create: `static/manifest.webmanifest`, `static/sw.js`, `static/icons/icon-192.png`, `static/icons/icon-512.png`, `static/icons/maskable-512.png`
- Modify: `static/index.html`, `static/login.html`, `static/print.html`, `static/scan.html` (manifest link and service worker registration)

**Interfaces:**
- Manifest:
  - `name: "Print hub"`, `start_url: "/"`, `display: "standalone"`
  - `share_target: {action: "/share", method: "POST", enctype: "multipart/form-data", params: {files: [{name: "file", accept: [<every MIME type in ACCEPTED>, ".pdf", ".jpg", ".jpeg", ".png", ".heic", ".doc", ".docx", ".odt", ".rtf", ".txt", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp"]}]}}`
- `sw.js`: precaches the HTML, CSS, JS, vendor and icon files under a versioned cache name. It serves them cache-first. It never intercepts `/api/*` or `/share`, and it deletes old caches on `activate`.

- [ ] **Step 1: Write the failing test** `tests/test_pwa.py`:
  - The manifest parses as JSON.
  - `share_target.params.files[0].accept` contains every key of `convert.ACCEPTED`.
  - Every path in the `sw.js` precache list exists under `static/`.
- [ ] **Step 2:** Run `pytest tests/test_pwa.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Generate the icons once with Pillow (a printer glyph on a solid background) and commit the PNGs.
- [ ] **Step 4:** Run `pytest tests/test_pwa.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: installable PWA with share target`.

### Task 13: Deployment

**Files:**
- Create:
  - `deploy/install.sh`
  - `deploy/systemd/printhub.service`, `deploy/systemd/traefik.service`, `deploy/systemd/printhub-retention.service`, `deploy/systemd/printhub-retention.timer`
  - `deploy/traefik/traefik.yml`, `deploy/traefik/dynamic.yml`
  - `deploy/sane/airscan.conf`
  - `docs/hardware-checklist.md`
- Modify: `README.md` (install and update instructions)

**Interfaces:**
- `install.sh` (bash, `set -euo pipefail`, re-runnable; every step checks before acting):
  1. `apt-get install -y cups avahi-daemon sane-utils sane-airscan libsane-dev libjpeg-dev libpng-dev libavahi-client-dev libusb-1.0-0-dev cmake g++ git python3-fastapi python3-uvicorn python3-multipart python3-argon2 python3-itsdangerous python3-img2pdf python3-pil python3-magic python3-cups libjpeg-turbo-progs poppler-utils libheif-examples libreoffice-nogui`
  2. `has_ipp_usb`: true when any `/sys/bus/usb/devices/*` interface whose parent's `idVendor` is `03f0` and `idProduct` is `0c54` has `bInterfaceClass=07`, `bInterfaceSubClass=01` and `bInterfaceProtocol=04`. Abort with "Printer not connected" if no such device is found.
     - **True:** install `ipp-usb`, then `lpadmin -p OfficeJet -E -v ipp://localhost:60000/ipp/print -m everywhere`. Write `deploy/sane/airscan.conf` to `/etc/sane.d/airscan.conf` with `[devices] "OfficeJet" = http://localhost:60000/eSCL` and `[options] discovery = disable`.
     - **False:** install `hplip printer-driver-hpcups libsane-hpaio`. Create the queue from `hp-makeuri -c <usb bus:dev>` with `lpadmin -p OfficeJet -E -v <uri> -m drv:///hpcups.drv/hp-officejet_pro_6970.ppd` (fall back to `lpinfo -m | grep -i 'officejet pro 6970'`). Write the same `airscan.conf` with `discovery = disable` and no devices.
     - Both: comment out `escl` in `/etc/sane.d/dll.conf`, then run `cupsctl --share-printers` and `lpadmin -p OfficeJet -o printer-is-shared=true`.
  3. Build AirSane from a pinned tag in `/usr/local/src/AirSane` (`cmake && make && make install`). Set `LOCAL_SCANNERS_ONLY=false` in `/etc/default/airsane`, because with ipp-usb the scanner reaches SANE through sane-airscan. Enable `airsaned`.
  4. Download a pinned Traefik v3 `linux_arm64` tarball from GitHub releases, verify its SHA-256 against the release checksums file, and install it to `/usr/local/bin/traefik`. Install `deploy/traefik/*` to `/etc/traefik/`.
  5. Create the system user `printhub` (groups `lp`, `lpadmin`, `scanner`) and the directory `/var/lib/printhub` (0750). Copy `printhub/` and `static/` to `/opt/printhub/`.
  6. If `/etc/printhub/config.toml` is missing, prompt for the web password (stored hashed via `python3 -c 'from printhub.auth import hash_password…'`) and the SMTP settings, generate `session_secret` with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`, and write the file with mode 0600, owned by `printhub`. If `/etc/traefik/traefik.env` is missing, prompt for `CF_DNS_API_TOKEN` and the ACME email, and write it with mode 0600.
  7. Install the systemd units, `daemon-reload`, then `enable --now printhub traefik airsaned printhub-retention.timer`.
  8. Print the LAN DNS override and DHCP reservation the user must add, with the Pi's current address.
- `printhub.service`:
  - `User=printhub`, `WorkingDirectory=/opt/printhub`, `ExecStart=/usr/bin/python3 -m uvicorn printhub.app:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1`
  - `Environment=PRINTHUB_CONFIG=/etc/printhub/config.toml`, `Restart=on-failure`
  - Hardening: `ProtectSystem=strict`, `ReadWritePaths=/var/lib/printhub`, `PrivateTmp=true`, `NoNewPrivileges=true`
- `traefik.yml`:
  - Entry points: `web` (`:80`, redirecting to `websecure`) and `websecure` (`:443`).
  - `certificatesResolvers.le.acme`: `dnsChallenge.provider: cloudflare`, `storage: /var/lib/traefik/acme.json`.
  - File provider on `/etc/traefik/dynamic.yml`.
- `dynamic.yml`: router ``Host(`print.brait.dev`)`` on `websecure` with `tls.certResolver: le`, service `http://127.0.0.1:8000`.
- `traefik.service`: `EnvironmentFile=/etc/traefik/traefik.env` and `AmbientCapabilities=CAP_NET_BIND_SERVICE`, running as user `traefik`.
- `printhub-retention.timer`: `OnCalendar=hourly`. The service runs `python3 -m printhub.retention` as `printhub`.
- `docs/hardware-checklist.md`, with one checkbox each:
  - every print option from the web app on paper (verify two-sided long and short edge physically)
  - printing from an iPhone (AirPrint), a Mac, Android (default print service / Mopria) and Windows 11
  - a flatbed scan and an ADF scan from the web app
  - AirSane scans from macOS Image Capture and Windows Scan
  - the share target from Android
  - unplugging the printer mid-job shows the banner, and the job resumes on replug
  - confirm whether the ADF is single-sided

- [ ] **Step 1:** Write the scripts and configuration.
- [ ] **Step 2:** Run `shellcheck deploy/install.sh` (expected: no findings) and `systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer` (expected: no errors other than the absent `/opt/printhub` paths). Unguard the CI shellcheck step.
- [ ] **Step 3:** Run `traefik --configFile=deploy/traefik/traefik.yml` with a dummy token for a few seconds to confirm the static configuration loads. Expected: no configuration errors in the log (the ACME failure is expected).
- [ ] **Step 4: Commit** `feat: Raspberry Pi installer, systemd units and Traefik config`.

### Task 14: On-device acceptance

This task needs the Pi's address, SSH access with `~/.ssh/id_ed25519_rpi`, and the printer attached.

- [ ] **Step 1:** Run `deploy/install.sh` on the Pi. Record which branch ran (ipp-usb or HPLIP) in `docs/hardware-checklist.md`.
- [ ] **Step 2:** Check that `curl -sI https://print.brait.dev` from the LAN returns `200` with a Let's Encrypt certificate, and that `lpstat -t` shows `OfficeJet` idle and shared.
- [ ] **Step 3:** Work through `docs/hardware-checklist.md` with the owner. File every failure as a GitHub issue and fix it with a regression test where one is possible.
- [ ] **Step 4: Commit** the completed checklist: `docs: hardware acceptance results`.
