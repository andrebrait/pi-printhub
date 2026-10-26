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


def has_ipp_usb(root: Path) -> bool:
    r = subprocess.run(
        ["bash", "-c", f'source "{INSTALL}" && has_ipp_usb'],
        env={"USB_SYSFS": str(root), "PATH": "/usr/bin:/bin"},
    )
    return r.returncode == 0


def test_ipp_usb_interface_detected(tmp_path):
    make_device(tmp_path, "1-1", "03f0", "0c54", [("07", "01", "02"), ("07", "01", "04")])
    assert has_ipp_usb(tmp_path)


def test_printer_without_ipp_interface(tmp_path):
    make_device(tmp_path, "1-1", "03f0", "0c54", [("07", "01", "02"), ("ff", "01", "04")])
    assert not has_ipp_usb(tmp_path)


def test_ipp_interface_on_other_device_is_ignored(tmp_path):
    make_device(tmp_path, "1-1", "03f0", "0c54", [("07", "01", "02")])
    make_device(tmp_path, "1-2", "04b8", "1234", [("07", "01", "04")])
    assert not has_ipp_usb(tmp_path)


def test_no_printer(tmp_path):
    make_device(tmp_path, "1-2", "04b8", "1234", [("07", "01", "04")])
    assert not has_ipp_usb(tmp_path)


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


def test_wait_for_port():
    import socket

    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        assert sh(f"wait_for_port 127.0.0.1 {srv.getsockname()[1]} 2").returncode == 0
        port = srv.getsockname()[1]
    assert sh(f"wait_for_port 127.0.0.1 {port} 1").returncode != 0


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


def test_scanner_pin_is_written_for_ipp_usb_and_removed_for_hplip(tmp_path):
    dropin = tmp_path / "printhub.service.d"
    assert sh(f"pin_scanner {dropin} airscan:e0:OfficeJet").returncode == 0
    assert (dropin / "scanner.conf").read_text() == "[Service]\nEnvironment=PRINTHUB_SCANNER=airscan:e0:OfficeJet\n"
    # The HPLIP path has no airscan device, so a pin left from an earlier install would break scanning.
    assert sh(f"pin_scanner {dropin}").returncode == 0
    assert not (dropin / "scanner.conf").exists()
