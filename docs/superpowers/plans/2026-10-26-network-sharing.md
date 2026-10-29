# Network Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the attached USB printer and scanner on the LAN twice: as a spooled CUPS queue and AirSane scanner named after the real model, and as ipp-usb's native `(USB)` passthrough, both following whichever IPP-over-USB printer is plugged in.

**Architecture:** A new `deploy/printer-setup.sh` (sourceable, like `install.sh`) points the CUPS queue, sane-airscan and the printhub scanner pin at the attached device's ipp-usb port. A udev rule starts it through `printhub-printer.service` on plug; `install.sh` runs it once and switches ipp-usb to all interfaces. The HPLIP fallback is unchanged.

**Tech Stack:** bash, CUPS `lpadmin` with cups-filters `driverless`, ipp-usb 0.9.23, sane-airscan, AirSane, systemd, udev; tests in pytest calling bash (`tests/test_install.py` style).

**Spec:** `docs/superpowers/specs/2026-10-26-network-sharing-design.md`

## Global Constraints

- Queue name stays `OfficeJet` (`PRINTER_QUEUE` in `install.sh`, `printer_queue` in `printhub/config.py`).
- The queue is modified in place, never deleted (`lpadmin -x` is never called): queued jobs survive a replug.
- Queue options: `-m driverless:ipp://localhost:<port>/ipp/print`, `-v ipp://localhost:<port>/ipp/print`, `-D "<printer-make-and-model>"`, `-o printer-is-shared=true`, `-o printer-error-policy=retry-job`.
- ipp-usb publishes on all interfaces: `interface = all` in `/etc/ipp-usb/ipp-usb.conf`.
- AirSane ignores `hpaio:/net/.*`.
- HPLIP fallback (no IPP-over-USB interface) stays specific to `03f0:0c54` and keeps today's behavior.
- One printer at a time: the most recently attached IPP-over-USB device wins.
- Runtime dependencies come from Debian packages only.

## Review Focus

- The printer refuses IPP (as with the printer's own IPP switch off): `printer-setup.sh` must exit non-zero and leave the queue, `airscan.conf` and the pin untouched.
- ipp-usb has not created the device's state file yet when udev fires: wait up to 30 s, then fail without changes.
- A model string containing `"` or `\`: `airscan.conf` must stay parseable (quote it).
- Re-running `install.sh` on an HPLIP install must not write an airscan entry, a scanner pin or touch ipp-usb.
- A state file for a device that is no longer attached must not be chosen.

---

### Task 1: Device discovery and config rendering in `printer-setup.sh`

**Files:**
- Create: `deploy/printer-setup.sh`
- Modify: `deploy/install.sh` (move `pin_scanner` out; `install.sh` sources `printer-setup.sh`)
- Test: `tests/test_printer_setup.py`

**Interfaces:**
- Produces, all in `deploy/printer-setup.sh`, sourceable without side effects (same guard as `install.sh`):
  - `ipp_usb_port STATE_DIR USB_SYSFS` → prints the `http-port` of the newest `*.state` file whose `<vid>-<pid>-<serial>-` prefix matches an attached device in `USB_SYSFS` (`idVendor`, `idProduct`, `serial`); exit 1 if none.
  - `airscan_conf PORT MODEL` → prints `[devices]\n"MODEL" = http://localhost:PORT/eSCL\n\n[options]\ndiscovery = disable\n` with `MODEL` escaped for `"` and `\`.
  - `pin_scanner DROPIN_DIR [DEVICE]` (moved unchanged from `install.sh`).
  - `scanner_device MODEL` → prints `airscan:e0:MODEL`.

- [ ] **Step 1: Write the failing tests** in `tests/test_printer_setup.py`, with an `sh()` helper like `tests/test_install.py` sourcing `deploy/printer-setup.sh`:
  - `test_port_comes_from_the_attached_devices_state_file`: state files `03f0-0c54-TH9152Q0KM-HP.state` (`http-port = 60000`) and `04b8-1234-X-Epson.state` (`http-port = 60001`); sysfs has only `03f0`/`0c54`/`TH9152Q0KM` → prints `60000`.
  - `test_no_attached_device_fails`: only the Epson state file, no sysfs device → exit 1, no output.
  - `test_airscan_conf_quotes_the_model`: `airscan_conf 60000 'HP "Pro" \x'` → contains `"HP \"Pro\" \\x" = http://localhost:60000/eSCL` and `discovery = disable`.
  - Move `test_scanner_pin_is_written_for_ipp_usb_and_removed_for_hplip` from `tests/test_install.py`, sourcing `printer-setup.sh`.
- [ ] **Step 2:** Run `python3 -m pytest tests/test_printer_setup.py -v`. Expected: FAIL (file missing).
- [ ] **Step 3:** Implement the four functions. Delete `deploy/sane/airscan.conf`; `airscan_conf` replaces it.
- [ ] **Step 4:** Run `python3 -m pytest tests/test_printer_setup.py tests/test_install.py -v` and `shellcheck deploy/*.sh`. Expected: PASS, no findings.
- [ ] **Step 5: Commit** `feat(deploy): printer-setup.sh discovers the attached ipp-usb device`.

### Task 2: Apply the device: queue, scanner, pin

**Files:**
- Modify: `deploy/printer-setup.sh` (add `apply_printer` and the run guard)
- Test: `tests/test_printer_setup.py`

**Interfaces:**
- Consumes: Task 1 functions.
- Produces: `apply_printer` (no args; reads `STATE_DIR=/var/ipp-usb/dev`, `USB_SYSFS`, `SANE_DIR=/etc/sane.d`, `DROPIN_DIR=/etc/systemd/system/printhub.service.d`, `PRINTER_QUEUE=OfficeJet`, overridable from the environment). Running the script directly calls `apply_printer`.

- [ ] **Step 1: Write the failing tests**, stubbing `lpadmin`, `cupsctl`, `ipptool`, `systemctl` and `sleep` as shell functions that log their arguments:
  - `test_apply_points_queue_scanner_and_pin_at_the_device`: `ipptool` prints `printer-make-and-model (textWithoutLanguage) = HP OfficeJet Pro 6970` → `lpadmin` log has `-p OfficeJet -E -v ipp://localhost:60000/ipp/print -m driverless:ipp://localhost:60000/ipp/print -D HP OfficeJet Pro 6970 -o printer-is-shared=true -o printer-error-policy=retry-job`; `SANE_DIR/airscan.conf` names `HP OfficeJet Pro 6970`; the pin is `airscan:e0:HP OfficeJet Pro 6970`; `systemctl` log has `daemon-reload` and `try-restart printhub airsaned`; no `-x` in the `lpadmin` log.
  - `test_refused_ipp_changes_nothing`: `ipptool` exits 1 → exit non-zero, no `lpadmin` call, `airscan.conf` and pin files unchanged.
  - `test_waits_for_ipp_usb_then_gives_up`: no state file → `sleep` called, exit non-zero after the 30 s budget, nothing written.
- [ ] **Step 2:** Run `python3 -m pytest tests/test_printer_setup.py -v`. Expected: the three new tests FAIL.
- [ ] **Step 3:** Implement `apply_printer`: poll `ipp_usb_port` once a second for 30 s; read the model with `ipptool -tv ipp://localhost:$port/ipp/print get-printer-attributes.test`; on failure exit 1 before writing anything; then `lpadmin` (Global Constraints options), `cupsctl --share-printers`, write `airscan.conf` atomically, `pin_scanner`, `systemctl daemon-reload`, `systemctl try-restart printhub airsaned`.
- [ ] **Step 4:** Run `python3 -m pytest tests/test_printer_setup.py -v` and `shellcheck deploy/*.sh`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(deploy): rebuild the queue and scanner for the attached printer`.

### Task 3: Hotplug and installer integration

**Files:**
- Create: `deploy/systemd/printhub-printer.service`, `deploy/udev/70-printhub-printer.rules`
- Modify: `deploy/install.sh` (`setup_printer`, `install_airsane`, `start_services`, new `ipp_usb_listen_all`, `airsane_ignore_hpaio_net`, generic `ipp_usb_dev`)
- Test: `tests/test_install.py`

**Interfaces:**
- Consumes: `deploy/printer-setup.sh` installed to `/usr/local/lib/printhub/printer-setup.sh`.
- Produces:
  - `printhub-printer.service`: `Type=oneshot`, `After=ipp-usb.service cups.service`, `ExecStart=/usr/local/lib/printhub/printer-setup.sh`.
  - Rule: `ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ENV{ID_USB_INTERFACES}=="*:070104:*", TAG+="systemd", ENV{SYSTEMD_WANTS}+="printhub-printer.service"`.
  - `ipp_usb_dev` → sysfs dir of any attached device with a `07/01/04` interface (replaces the HP-only use of `printer_dev` + `has_ipp_usb` on the IPP-over-USB path; `printer_dev` stays for HPLIP).
  - `ipp_usb_listen_all FILE`, `airsane_ignore_hpaio_net FILE` (idempotent).

- [ ] **Step 1: Write the failing tests** in `tests/test_install.py`:
  - `test_any_ipp_usb_printer_is_found`: an `04b8:1234` device with a `07/01/04` interface → `ipp_usb_dev` prints its dir.
  - `test_ipp_usb_listens_on_all_interfaces`: file with `  interface = loopback # all | loopback` → `interface = all`; running twice changes nothing more.
  - `test_airsane_ignores_hpaio_network_devices`: `ignore.conf` gains exactly one `hpaio:/net/.*` line after two runs.
  - `test_hplip_rerun_leaves_ipp_usb_alone`: existing queue with an `hp:/usb/...` URI, stubs logging `printer-setup.sh`, `sed` on `ipp-usb.conf` and `pin_scanner` → none called; the HPLIP `airscan.conf` has no `[devices]` entry. Update `test_setup_printer_defers_when_printer_absent` for the new detection.
- [ ] **Step 2:** Run `python3 -m pytest tests/test_install.py -v`. Expected: new tests FAIL.
- [ ] **Step 3:** Implement: `setup_printer` uses `ipp_usb_dev` first (installs ipp-usb, `ipp_usb_listen_all`, restarts ipp-usb, runs `printer-setup.sh`), else HPLIP via `printer_dev`, else pending. Install the script, unit and rule; `udevadm control --reload`. `install_airsane` calls `airsane_ignore_hpaio_net`.
- [ ] **Step 4:** Run `python3 -m pytest -q -k "not slow"`, `node --test static/js/*.test.js`, `shellcheck deploy/*.sh`, `systemd-analyze verify deploy/systemd/printhub-printer.service`. Expected: PASS.
- [ ] **Step 5:** Update `README.md` (Install: any IPP-over-USB printer; the two network entries) and `docs/hardware-checklist.md` with the spec's section 7 on-device checks.
- [ ] **Step 6: Commit** `feat(deploy): follow the plugged-in printer and publish it natively over ipp-usb`.

### Task 4: Deploy and verify on the Pi

Precondition: IPP is enabled on the printer (`ipptool -t ipp://localhost:60000/ipp/print get-printer-attributes.test` passes on the Pi) and no job or scan is running.

- [ ] **Step 1:** Copy the branch to `/usr/local/src/pi-printhub` and run `sudo deploy/install.sh`. Expected: services active; `lpoptions -p OfficeJet` shows `printer-make-and-model='HP OfficeJet Pro 6970, driverless, ...'` and `printer-info='HP OfficeJet Pro 6970'`; `printer-error-policy=retry-job`.
- [ ] **Step 2:** mDNS from the LAN (`dig -p 5353 @<pi address> _ipp._tcp.local PTR`, same for `_uscan._tcp`). Expected: `HP OfficeJet Pro 6970 @ raspberrypi`, `HP OfficeJet Pro 6970 [053F79] (USB)` and one AirSane scanner; no `officejet_pro_6970` hpaio-net scanner.
- [ ] **Step 3:** Replug the printer (owner) and check `journalctl -u printhub-printer` shows a successful run.
- [ ] **Step 4:** Owner runs the spec's section 7 client checks (Mac AirPrint duplex, phone duplex and cancel, power-cycle mid-job, scans through both entries, web-app scan).
