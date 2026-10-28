# SPDX-License-Identifier: AGPL-3.0-only
import subprocess
from pathlib import Path

INSTALL = Path(__file__).parent.parent / "deploy" / "install.sh"


def make_device(root: Path, name: str, vid: str, pid: str, ifaces: list[tuple[str, str, str]]) -> None:
    dev = root / name
    dev.mkdir()
    (dev / "idVendor").write_text(vid + "\n")
    (dev / "idProduct").write_text(pid + "\n")
    for n, (cls, sub, proto) in enumerate(ifaces):
        iface = dev / f"{name}:1.{n}"
        iface.mkdir()
        (iface / "bInterfaceClass").write_text(cls + "\n")
        (iface / "bInterfaceSubClass").write_text(sub + "\n")
        (iface / "bInterfaceProtocol").write_text(proto + "\n")


def ipp_usb_dev(root: Path) -> str | None:
    r = subprocess.run(
        ["bash", "-c", f'source "{INSTALL}" && ipp_usb_dev'],
        env={"USB_SYSFS": str(root), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def test_ipp_usb_interface_detected(tmp_path):
    make_device(tmp_path, "1-1", "03f0", "0c54", [("07", "01", "02"), ("07", "01", "04")])
    assert ipp_usb_dev(tmp_path) == str(tmp_path / "1-1")


def test_printer_without_ipp_interface(tmp_path):
    make_device(tmp_path, "1-1", "03f0", "0c54", [("07", "01", "02"), ("ff", "01", "04")])
    assert ipp_usb_dev(tmp_path) is None


def test_any_ipp_usb_printer_is_found(tmp_path):
    make_device(tmp_path, "1-1", "03f0", "0c54", [("07", "01", "02")])
    make_device(tmp_path, "1-2", "04b8", "1234", [("07", "01", "04")])
    assert ipp_usb_dev(tmp_path) == str(tmp_path / "1-2")


def test_no_printer(tmp_path):
    make_device(tmp_path, "1-2", "046d", "c52b", [("03", "01", "02")])  # a USB receiver, no printer class
    assert ipp_usb_dev(tmp_path) is None


def test_ipp_usb_listens_on_all_interfaces(tmp_path):
    f = tmp_path / "ipp-usb.conf"
    f.write_text("[network]\n  http-min-port = 60000\n  interface = loopback # all | loopback\n")
    for _ in range(2):
        assert sh(f"ipp_usb_listen_all {f}").returncode == 0
    assert f.read_text() == "[network]\n  http-min-port = 60000\n  interface = all # all | loopback\n"


def test_airsane_ignores_hpaio_network_devices(tmp_path):
    f = tmp_path / "ignore.conf"
    f.write_text("escl:.*\n")
    for _ in range(2):
        assert sh(f"airsane_ignore_hpaio_net {f}").returncode == 0
    assert f.read_text() == "escl:.*\nhpaio:/net/.*\n"


def test_hplip_rerun_leaves_ipp_usb_alone(tmp_path):
    # An HPLIP install re-run with the printer unplugged: no airscan device, no scanner pin, no ipp-usb changes.
    sane, dropin, avahi = tmp_path / "sane", tmp_path / "dropin", tmp_path / "avahi"
    sane.mkdir()
    avahi.mkdir()
    (avahi / "printhub-airprint.service").write_text("stale")
    stubs = (
        'lpstat() { [[ $1 == -v ]] && echo "device for OfficeJet: hp:/usb/OfficeJet_Pro_6970?serial=X"; return 0; }; '
        'lpadmin() { :; }; cupsctl() { echo "CUPSCTL $*"; }; configure_dll_conf() { :; }; '
        'apply_printer() { echo APPLY; }; ipp_usb_listen_all() { echo LISTEN; }; apt-get() { echo APT "$*"; }'
    )
    r = sh(f"{stubs}; setup_printer", USB_SYSFS=str(tmp_path), SANE_DIR=str(sane), DROPIN_DIR=str(dropin),
           AVAHI_DIR=str(avahi))
    assert r.returncode == 0, r.stderr
    assert "APPLY" not in r.stdout and "LISTEN" not in r.stdout and "ipp-usb" not in r.stdout
    assert "[devices]" not in (sane / "airscan.conf").read_text()
    assert not (dropin / "scanner.conf").exists()
    # The printhub announcements belong to the ipp-usb queue; CUPS announces the HPLIP one itself.
    assert not (avahi / "printhub-airprint.service").exists()
    assert "CUPSCTL --share-printers BrowseLocalProtocols=dnssd" in r.stdout


def sh(expr: str, *args: str, input: str | None = None, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'source "{INSTALL}"; {expr}', "_", *args],
        env={"PATH": "/usr/bin:/bin", **env},
        input=input,
        capture_output=True,
        text=True,
    )


def test_port_validation():
    for ok in ("587", "1", "65535", "0587"):
        assert sh(f"valid_port {ok}").returncode == 0, ok
    for bad in ("", "0", "65536", "18446744073709552203", "58a", "-1", "5 87"):
        assert sh('valid_port "$1"', bad).returncode != 0, bad


def test_smtp_port_prompt_retries_invalid_input():
    r = sh(
        'ask port "SMTP port" 587 valid_port; printf "%s" "$port"',
        input="nope\n65536\n587\n",
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "587"


def test_traefik_yml_gets_the_email():
    r = sh("render_traefik_yml me@example.com")
    assert 'email: "me@example.com"' in r.stdout
    assert "@ACME_EMAIL@" not in r.stdout
    r = sh('render_traefik_yml "$1"', 'a"&|\\b@example.com')
    assert r.returncode == 0, r.stderr
    assert 'email: "a\\"&|\\\\b@example.com"' in r.stdout


def test_pick_ppd_prefers_the_hpcups_driver():
    models = "drv:///other.drv/x.ppd Other\ndrv:///hpcups.drv/hp-officejet_pro_6970.ppd HP OfficeJet Pro 6970\n"
    r = sh('pick_ppd "$1"', models)
    assert r.stdout.strip() == "drv:///hpcups.drv/hp-officejet_pro_6970.ppd"


def test_pick_ppd_falls_back_to_name_match_then_nothing():
    def pick(models: str) -> str:
        r = sh('pick_ppd "$1"', models)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    assert pick("foo.ppd Foo\nhplip:0/ppd/6970.ppd HP Officejet Pro 6970 Series\n") == "hplip:0/ppd/6970.ppd"
    assert pick("foo.ppd Foo\n") == ""


def test_dll_conf_enables_airscan_and_disables_escl(tmp_path):
    f = tmp_path / "dll.conf"
    f.write_text("net\nescl\n#airscan\nhpaio\n")
    assert sh(f"configure_dll_conf {f}").returncode == 0
    assert f.read_text().splitlines() == ["net", "#escl", "airscan", "hpaio"]
    f.write_text("net\nescl\n")
    sh(f"configure_dll_conf {f}")
    sh(f"configure_dll_conf {f}")
    assert f.read_text().splitlines() == ["net", "#escl", "airscan"]


def test_airsane_ignore_list_keeps_only_escl(tmp_path):
    f = tmp_path / "ignore.conf"
    f.write_text("# comment\nescl:.*\nairscan:.*\n")
    sh(f"allow_airscan_in_airsane {f}")
    sh(f"allow_airscan_in_airsane {f}")
    assert f.read_text() == "# comment\nescl:.*\n"


def test_write_secret_is_atomic_and_private(tmp_path):
    f = tmp_path / "secret"
    f.write_text("old")
    me = subprocess.check_output(["id", "-un"], text=True).strip()
    r = sh(f"printf new | write_secret {f} {me}")
    assert r.returncode == 0, r.stderr
    assert f.read_text() == "new"
    assert f.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["secret"]


def test_write_secret_failure_preserves_existing_file(tmp_path):
    f = tmp_path / "secret"
    f.write_text("old")
    r = sh(f"printf new | write_secret {f} printhub-nonexistent-user")
    assert r.returncode != 0
    assert f.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["secret"]


def test_setup_printer_defers_when_printer_absent(tmp_path):
    # No queue yet and no USB printer: the rest of the install must go on, touching neither CUPS nor SANE.
    stubs = 'lpstat() { return 1; }; lpadmin() { echo LPADMIN; }; cupsctl() { echo CUPSCTL; }; install() { echo INSTALL; }'
    r = sh(f"{stubs}; setup_printer; echo rc=$?; echo pending=$PRINTER_PENDING", USB_SYSFS=str(tmp_path))
    assert "rc=0" in r.stdout
    assert "pending=1" in r.stdout
    assert "LPADMIN" not in r.stdout and "CUPSCTL" not in r.stdout and "INSTALL" not in r.stdout
    assert "Re-run" in r.stdout


IPP_USB_STUBS = (
    'apt-get() { echo "APT $*"; }; ipp_usb_listen_all() { echo "LISTEN $*"; }; install() { echo "INSTALL $*"; }; '
    'udevadm() { echo "UDEVADM $*"; }; configure_dll_conf() { :; }; lpadmin() { echo "LPADMIN $*"; }; '
)


def test_attached_ipp_usb_printer_is_set_up_through_the_hotplug_service(tmp_path):
    make_device(tmp_path, "1-1", "03f0", "0c54", [("07", "01", "04")])
    stubs = IPP_USB_STUBS + 'lpstat() { return 1; }; systemctl() { echo "SYSTEMCTL $*"; }'
    r = sh(f"{stubs}; setup_printer; echo pending=${{PRINTER_PENDING:-0}}", USB_SYSFS=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "LISTEN /etc/ipp-usb/ipp-usb.conf" in r.stdout
    assert "/usr/local/lib/printhub/printer-setup.sh" in r.stdout
    assert "/etc/udev/rules.d/70-printhub-printer.rules" in r.stdout
    assert "/etc/systemd/system/printhub-printer.service" in r.stdout
    assert "SYSTEMCTL start printhub-printer.service" in r.stdout
    assert "pending=0" in r.stdout


def test_printer_that_does_not_answer_ipp_is_reported_and_left_pending(tmp_path):
    make_device(tmp_path, "1-1", "03f0", "0c54", [("07", "01", "04")])
    stubs = IPP_USB_STUBS + 'lpstat() { return 1; }; systemctl() { [[ $1 != start ]]; }'
    r = sh(f"{stubs}; setup_printer; echo rc=$?; echo pending=${{PRINTER_PENDING:-0}}", USB_SYSFS=str(tmp_path))
    assert "rc=0" in r.stdout
    assert "is IPP enabled" in r.stdout
    assert "pending=1" in r.stdout


def test_ipp_usb_rerun_with_printer_unplugged_keeps_the_queue(tmp_path):
    stubs = IPP_USB_STUBS + (
        'lpstat() { [[ $1 == -v ]] && echo "device for OfficeJet: ipp://localhost:60000/ipp/print"; return 0; }; '
        'systemctl() { echo "SYSTEMCTL $*"; }'
    )
    r = sh(f"{stubs}; setup_printer", USB_SYSFS=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "LISTEN" in r.stdout  # still the ipp-usb install
    assert "start printhub-printer.service" not in r.stdout and "LPADMIN" not in r.stdout
    assert "keeping the existing queue" in r.stdout


def test_a_cups_url_is_not_mistaken_for_ipp_usb(tmp_path):
    sane = tmp_path / "sane"
    sane.mkdir()
    stubs = IPP_USB_STUBS + (
        'lpstat() { [[ $1 == -v ]] && echo "device for OfficeJet: ipp://localhost:631/printers/x"; return 0; }; '
        "cupsctl() { :; }; systemctl() { :; }"
    )
    r = sh(f"{stubs}; setup_printer", USB_SYSFS=str(tmp_path), SANE_DIR=str(sane), DROPIN_DIR=str(tmp_path / "d"))
    assert r.returncode == 0, r.stderr
    assert "LISTEN" not in r.stdout


def test_udev_rule_starts_the_unit_that_runs_the_installed_script():
    deploy = INSTALL.parent
    rule = (deploy / "udev" / "70-printhub-printer.rules").read_text()
    unit = (deploy / "systemd" / "printhub-printer.service").read_text()
    assert 'ENV{ID_USB_INTERFACES}=="*:070104:*"' in rule
    assert 'ENV{SYSTEMD_WANTS}+="printhub-printer.service"' in rule
    assert "ExecStart=/usr/local/lib/printhub/printer-setup.sh\n" in unit
    assert "install -D -m 0755 \"$REPO/deploy/printer-setup.sh\" /usr/local/lib/printhub/printer-setup.sh" in INSTALL.read_text()


def test_printer_seen_before_the_ipp_usb_restart_is_set_up(tmp_path):
    # Restarting ipp-usb and re-triggering udev briefly hides the interface files (seen on the Pi).
    seen = tmp_path / "seen"
    stubs = IPP_USB_STUBS + (
        f'ipp_usb_dev() {{ [[ -e {seen} ]] && return 1; touch {seen}; echo /sys/x; }}; '
        'lpstat() { return 1; }; systemctl() { echo "SYSTEMCTL $*"; }'
    )
    r = sh(f"{stubs}; setup_printer", USB_SYSFS=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "SYSTEMCTL start printhub-printer.service" in r.stdout
    assert "unplugged" not in r.stdout


def test_job_details_are_shown_in_the_default_policy_only(tmp_path):
    # The Jobs page lists names and senders of jobs from every device; CUPS hides them by default.
    f = tmp_path / "cupsd.conf"
    f.write_text(
        "<Policy default>\n  JobPrivateAccess default\n  JobPrivateValues default\n</Policy>\n"
        "<Policy kerberos>\n  JobPrivateValues default\n</Policy>\n"
    )
    assert sh(f"show_job_details {f} && echo changed || echo unchanged").stdout.strip() == "changed"
    assert f.read_text() == (
        "<Policy default>\n  JobPrivateAccess default\n  JobPrivateValues none\n</Policy>\n"
        "<Policy kerberos>\n  JobPrivateValues default\n</Policy>\n"
    )
    # Unchanged on a re-run, so the installer restarts CUPS only once.
    assert sh(f"show_job_details {f} && echo changed || echo unchanged").stdout.strip() == "unchanged"
