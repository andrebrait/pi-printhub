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
