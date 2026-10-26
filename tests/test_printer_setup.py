# SPDX-License-Identifier: AGPL-3.0-only
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "deploy" / "printer-setup.sh"


def sh(expr: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; {expr}', "_", *args],
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
    )


def usb_device(sysfs: Path, name: str, vid: str, pid: str, serial: str) -> None:
    d = sysfs / name
    d.mkdir(parents=True)
    (d / "idVendor").write_text(vid + "\n")
    (d / "idProduct").write_text(pid + "\n")
    (d / "serial").write_text(serial + "\n")


def state_file(state: Path, name: str, port: int) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / name).write_text(f'; device\n[device]\nhttp-port       = {port}\ndns-sd-name     = "x"\n')


def test_port_comes_from_the_attached_devices_state_file(tmp_path):
    state, sysfs = tmp_path / "state", tmp_path / "sysfs"
    state_file(state, "03f0-0c54-TH9152Q0KM-HP-OfficeJet-Pro-6970.state", 60000)
    state_file(state, "04b8-1234-X-Epson.state", 60001)
    usb_device(sysfs, "1-1", "03f0", "0c54", "TH9152Q0KM")
    r = sh('ipp_usb_port "$1" "$2"', str(state), str(sysfs))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "60000"


def test_no_attached_device_fails(tmp_path):
    state, sysfs = tmp_path / "state", tmp_path / "sysfs"
    state_file(state, "04b8-1234-X-Epson.state", 60001)
    sysfs.mkdir()
    r = sh('ipp_usb_port "$1" "$2"', str(state), str(sysfs))
    assert r.returncode == 1
    assert r.stdout == ""


def test_airscan_conf_quotes_the_model():
    r = sh('airscan_conf 60000 "$1"', 'HP "Pro" \\x')
    assert r.returncode == 0, r.stderr
    assert '"HP \\"Pro\\" \\\\x" = http://localhost:60000/eSCL' in r.stdout
    assert "discovery = disable" in r.stdout


def test_scanner_device_is_the_first_airscan_entry():
    assert sh('scanner_device "$1"', "HP OfficeJet Pro 6970").stdout.strip() == "airscan:e0:HP OfficeJet Pro 6970"


def test_scanner_pin_is_written_for_ipp_usb_and_removed_for_hplip(tmp_path):
    dropin = tmp_path / "printhub.service.d"
    assert sh(f'pin_scanner {dropin} "$1"', "airscan:e0:HP OfficeJet Pro 6970").returncode == 0
    # Quoted: systemd splits unquoted Environment= values on spaces.
    assert (dropin / "scanner.conf").read_text() == (
        '[Service]\nEnvironment="PRINTHUB_SCANNER=airscan:e0:HP OfficeJet Pro 6970"\n'
    )
    # The HPLIP path has no airscan device, so a pin left from an earlier install would break scanning.
    assert sh(f"pin_scanner {dropin}").returncode == 0
    assert not (dropin / "scanner.conf").exists()


def test_scanner_pin_escapes_systemd_specials(tmp_path):
    dropin = tmp_path / "d"
    sh(f'pin_scanner {dropin} "$1"', 'airscan:e0:A "B" 100% \\x')
    assert (dropin / "scanner.conf").read_text() == '[Service]\nEnvironment="PRINTHUB_SCANNER=airscan:e0:A \\"B\\" 100%% \\\\x"\n'


IPPTOOL_OK = 'ipptool() { echo "        printer-make-and-model (textWithoutLanguage) = HP OfficeJet Pro 6970"; }'


def apply(tmp_path: Path, stubs: str, attached: bool = True, state: bool = True) -> tuple[subprocess.CompletedProcess, str]:
    """Run apply_printer with CUPS, systemd and sleep stubbed; returns the result and the command log."""
    sysfs, statedir, log = tmp_path / "sysfs", tmp_path / "state", tmp_path / "log"
    sysfs.mkdir(exist_ok=True)
    if attached:
        usb_device(sysfs, "1-1", "03f0", "0c54", "TH9152Q0KM")
    if state:
        state_file(statedir, "03f0-0c54-TH9152Q0KM-HP-OfficeJet-Pro-6970.state", 60000)
    (tmp_path / "sane").mkdir(exist_ok=True)
    base = (
        f'log() {{ echo "$*" >>{log}; }}; lpadmin() {{ log "lpadmin $*"; }}; cupsctl() {{ log "cupsctl $*"; }}; '
        f'systemctl() {{ log "systemctl $*"; }}; sleep() {{ log "sleep $*"; }}; '
    )
    r = sh(
        f"{base}{stubs}; apply_printer",
        STATE_DIR=str(statedir), USB_SYSFS=str(sysfs), SANE_DIR=str(tmp_path / "sane"),
        DROPIN_DIR=str(tmp_path / "dropin"),
    )
    return r, log.read_text() if log.exists() else ""


def test_apply_points_queue_scanner_and_pin_at_the_device(tmp_path):
    r, log = apply(tmp_path, IPPTOOL_OK)
    assert r.returncode == 0, r.stderr
    assert (
        "lpadmin -p OfficeJet -E -v ipp://localhost:60000/ipp/print -m driverless:ipp://localhost:60000/ipp/print "
        "-D HP OfficeJet Pro 6970 -o printer-is-shared=true -o printer-error-policy=retry-job"
    ) in log
    assert "lpadmin -x" not in log  # queued jobs survive a replug
    assert "cupsctl --share-printers" in log
    assert '"HP OfficeJet Pro 6970" = http://localhost:60000/eSCL' in (tmp_path / "sane" / "airscan.conf").read_text()
    assert "PRINTHUB_SCANNER=airscan:e0:HP OfficeJet Pro 6970" in (tmp_path / "dropin" / "scanner.conf").read_text()
    assert "systemctl daemon-reload" in log
    assert "systemctl try-restart printhub airsaned" in log


def test_refused_ipp_changes_nothing(tmp_path):
    # As when the printer's own IPP switch is off: ipp-usb is up but IPP answers nothing.
    (tmp_path / "sane").mkdir()
    (tmp_path / "sane" / "airscan.conf").write_text("previous\n")
    r, log = apply(tmp_path, "ipptool() { return 1; }")
    assert r.returncode != 0
    assert "lpadmin" not in log and "systemctl" not in log
    assert (tmp_path / "sane" / "airscan.conf").read_text() == "previous\n"
    assert not (tmp_path / "dropin").exists()


def test_waits_for_ipp_usb_then_gives_up(tmp_path):
    r, log = apply(tmp_path, IPPTOOL_OK, state=False)
    assert r.returncode != 0
    assert log.count("sleep 1") == 30
    assert "lpadmin" not in log
    assert not (tmp_path / "sane" / "airscan.conf").exists()


def test_waits_until_the_printer_answers_ipp(tmp_path):
    # ipp-usb writes the state file before it serves IPP; a device seen before keeps its old file.
    tries = tmp_path / "tries"
    flaky = f'ipptool() {{ echo x >>{tries}; [[ $(wc -l <{tries}) -ge 3 ]] && {IPPTOOL_OK.split("{ ", 1)[1].rsplit("; }", 1)[0]}; }}'
    r, log = apply(tmp_path, flaky)
    assert r.returncode == 0, r.stderr
    assert log.count("sleep 1") == 2
    assert "lpadmin -p OfficeJet" in log


def test_first_install_without_printhub_units_succeeds(tmp_path):
    # On a first install printhub.service and airsaned do not exist yet; try-restart then fails.
    r, log = apply(tmp_path, IPPTOOL_OK + '; systemctl() { log "systemctl $*"; [[ $1 != try-restart ]]; }')
    assert r.returncode == 0, r.stderr
    assert "systemctl try-restart printhub airsaned" in log


def test_serial_is_sanitized_like_ipp_usb_does(tmp_path):
    state, sysfs = tmp_path / "state", tmp_path / "sysfs"
    state_file(state, "04b8-1234-AB-12-3-EPSON-XP.state", 60002)
    usb_device(sysfs, "1-1", "04b8", "1234", "AB 12.3")
    assert sh('ipp_usb_port "$1" "$2"', str(state), str(sysfs)).stdout.strip() == "60002"


def test_device_without_serial_is_found(tmp_path):
    state, sysfs = tmp_path / "state", tmp_path / "sysfs"
    state_file(state, "04b8-1234-EPSON-XP-2100.state", 60003)
    usb_device(sysfs, "1-1", "04b8", "1234", "")
    (sysfs / "1-1" / "serial").unlink()
    assert sh('ipp_usb_port "$1" "$2"', str(state), str(sysfs)).stdout.strip() == "60003"


def test_airscan_conf_is_world_readable_under_a_strict_umask(tmp_path):
    # saned and printhub read it; root's umask may be 077.
    r, _ = apply(tmp_path, "umask 077; " + IPPTOOL_OK)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "sane" / "airscan.conf").stat().st_mode & 0o777 == 0o644
