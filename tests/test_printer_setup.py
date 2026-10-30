# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import pwd
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "deploy" / "printer-setup.sh"
OWNER = pwd.getpwuid(os.getuid()).pw_name


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


def apply(
    tmp_path: Path, stubs: str, attached: bool = True, state: bool = True, env: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess, str]:
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
        f'systemctl() {{ log "systemctl $*"; }}; sleep() {{ log "sleep $*"; }}; hostname() {{ echo testhost; }}; '
    )
    r = sh(
        f"{base}{stubs}; apply_printer",
        STATE_DIR=str(statedir), USB_SYSFS=str(sysfs), SANE_DIR=str(tmp_path / "sane"),
        DROPIN_DIR=str(tmp_path / "dropin"), AVAHI_DIR=str(tmp_path / "avahi"),
        **{"DATA_DIR": str(tmp_path / "nodata"), "OWNER": OWNER, **(env or {})},
    )
    return r, log.read_text() if log.exists() else ""


def test_apply_points_queue_scanner_and_pin_at_the_device(tmp_path):
    r, log = apply(tmp_path, IPPTOOL_OK)
    assert r.returncode == 0, r.stderr
    assert (
        "lpadmin -p OfficeJet -E -v ipp://localhost:60000/ipp/print -m driverless:ipp://localhost:60000/ipp/print "
        "-D HP OfficeJet Pro 6970 -L testhost -o printer-error-policy=retry-job"
    ) in log
    assert "lpadmin -x" not in log  # queued jobs survive a replug
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


QUEUE_ATTRS = """\
        printer-uuid (uri) = urn:uuid:dcde4972-f7a6-3474-6dd2-cd6befb1f050
        document-format-supported (1setOf mimeMediaType) = application/octet-stream,application/pdf,application/postscript,image/jpeg,image/png,image/pwg-raster,image/urf,text/plain
        color-supported (boolean) = true
        sides-supported (1setOf keyword) = one-sided,two-sided-long-edge,two-sided-short-edge
        urf-supported (1setOf keyword) = V1.4,CP1,W8,PQ3-4-5,SRGB24,RS600,DM1,FN3
"""


def test_airprint_advert_looks_like_a_native_airprint_printer(tmp_path):
    # macOS lists CUPS's own advert as "Bonjour Shared" and picks a driver (Generic PostScript); an advert shaped
    # like a printer's own AirPrint one gets AirPrint.
    (tmp_path / "attrs").write_text(QUEUE_ATTRS)
    r = sh(f'airprint_service "$1" OfficeJet <{tmp_path / "attrs"}', "HP OfficeJet Pro 6970")
    assert r.returncode == 0, r.stderr
    xml = r.stdout
    assert "<name>HP OfficeJet Pro 6970 (Pi)</name>" in xml
    assert "<type>_ipp._tcp</type>" in xml and "<subtype>_universal._sub._ipp._tcp</subtype>" in xml
    assert "<type>_ipps._tcp</type>" in xml
    for txt in (
        "rp=printers/OfficeJet", "ty=HP OfficeJet Pro 6970", "product=(HP OfficeJet Pro 6970)",
        "pdl=application/pdf,image/jpeg,image/png,image/pwg-raster,image/urf",
        "URF=V1.4,CP1,W8,PQ3-4-5,SRGB24,RS600,DM1,FN3", "Color=T", "Duplex=T", "air=none",
    ):
        assert f"<txt-record>{txt}</txt-record>" in xml, txt
    assert "postscript" not in xml and "printer-type" not in xml
    # CUPS's own advert carries the queue UUID; sharing it could merge the two entries on clients.
    assert "UUID=" not in xml


def test_airprint_advert_escapes_the_model_for_xml(tmp_path):
    (tmp_path / "attrs").write_text("        sides-supported (1setOf keyword) = one-sided\n")
    xml = sh(f'airprint_service "$1" Q <{tmp_path / "attrs"}', "A & <B>").stdout
    assert "<name>A &amp; &lt;B&gt; (Pi)</name>" in xml
    assert "<txt-record>Duplex=F</txt-record>" in xml and "<txt-record>Color=F</txt-record>" in xml


def test_apply_writes_a_readable_airprint_advert(tmp_path):
    r, log = apply(tmp_path, IPPTOOL_OK)
    assert r.returncode == 0, r.stderr
    service = tmp_path / "avahi" / "printhub-airprint.service"
    assert "ty=HP OfficeJet Pro 6970" in service.read_text()
    assert service.stat().st_mode & 0o777 == 0o644


def test_queue_is_read_before_cupsctl_restarts_cups(tmp_path):
    # cupsctl restarts cupsd; querying the queue right after it fails ("Host is down"), as seen on the Pi.
    flag = tmp_path / "restarted"
    stubs = (
        f'cupsctl() {{ log "cupsctl $*"; touch {flag}; }}; '
        f'ipptool() {{ [[ $2 == *:631/* && -e {flag} ]] && return 1; '
        'echo "        printer-make-and-model (textWithoutLanguage) = HP OfficeJet Pro 6970"; '
        'echo "        sides-supported (1setOf keyword) = one-sided,two-sided-long-edge"; }'
    )
    r, _ = apply(tmp_path, stubs)
    assert r.returncode == 0, r.stderr
    assert "<txt-record>Duplex=T</txt-record>" in (tmp_path / "avahi" / "printhub-airprint.service").read_text()


def test_generic_advert_is_the_cups_style_fallback_under_its_own_name(tmp_path):
    (tmp_path / "attrs").write_text(QUEUE_ATTRS + "        printer-type (enum) = 36892\n")
    xml = sh(f'airprint_service "$1" OfficeJet generic <{tmp_path / "attrs"}', "HP OfficeJet Pro 6970").stdout
    assert "<name>HP OfficeJet Pro 6970 (Pi) (Generic)</name>" in xml
    # What CUPS itself announces, and what makes macOS treat it as a shared CUPS printer.
    assert "<txt-record>pdl=application/pdf,application/postscript,image/jpeg,image/png,image/pwg-raster,image/urf</txt-record>" in xml
    assert "<txt-record>printer-type=0x901C</txt-record>" in xml
    assert "air=none" not in xml and "UUID=" not in xml


def test_apply_announces_both_entries_instead_of_cups(tmp_path):
    r, log = apply(tmp_path, IPPTOOL_OK)
    assert r.returncode == 0, r.stderr
    avahi = tmp_path / "avahi"
    assert "<name>HP OfficeJet Pro 6970 (Pi)</name>" in (avahi / "printhub-airprint.service").read_text()
    assert "<name>HP OfficeJet Pro 6970 (Pi) (Generic)</name>" in (avahi / "printhub-generic.service").read_text()
    assert (avahi / "printhub-generic.service").stat().st_mode & 0o777 == 0o644
    assert "cupsctl --share-printers BrowseLocalProtocols=none" in log


def test_location_is_the_hostname_on_the_queue_and_both_adverts(tmp_path):
    # AirSane's scanner already shows the hostname as its location; the printers match it.
    stubs = (
        'hostname() { echo printpi; }; ipptool() { '
        'echo "        printer-make-and-model (textWithoutLanguage) = HP OfficeJet Pro 6970"; '
        'echo "        printer-location (textWithoutLanguage) = printpi"; }'
    )
    r, log = apply(tmp_path, stubs)
    assert r.returncode == 0, r.stderr
    assert "-D HP OfficeJet Pro 6970 -L printpi" in log
    for kind in ("airprint", "generic"):
        assert "<txt-record>note=printpi</txt-record>" in (tmp_path / "avahi" / f"printhub-{kind}.service").read_text()


def request(tmp_path: Path, **values: bool) -> dict[str, str]:
    """Env for a system-request.json owned by the current user (as the app user's file would be)."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "system-request.json").write_text(json.dumps({"share": True, "expose_usb": True, "at": "t", **values}))
    return {"DATA_DIR": str(data), "OWNER": OWNER}


def test_replug_keeps_sharing_off(tmp_path):
    r, log = apply(tmp_path, IPPTOOL_OK, env=request(tmp_path, share=False))
    assert r.returncode == 0, r.stderr
    assert "printer-is-shared=false" in log and "printer-is-shared=true" not in log
    assert "cupsctl --no-share-printers" in log
    assert list((tmp_path / "avahi").glob("printhub-*.service")) == []
    assert "systemctl disable --now airsaned" in log


def test_replug_shares_by_default(tmp_path):
    r, log = apply(tmp_path, IPPTOOL_OK)
    assert r.returncode == 0, r.stderr
    assert "lpadmin -p OfficeJet -o printer-is-shared=true" in log
    assert (tmp_path / "avahi" / "printhub-airprint.service").exists()
    assert "systemctl enable --now airsaned" in log


def test_replug_with_an_unreadable_request_logs_it_and_shares(tmp_path):
    env = request(tmp_path)
    (tmp_path / "data" / "system-request.json").write_text("not json")
    r, log = apply(tmp_path, IPPTOOL_OK, env=env)
    assert r.returncode == 0, r.stderr
    assert "not JSON" in r.stderr
    assert "printer-is-shared=true" in log
