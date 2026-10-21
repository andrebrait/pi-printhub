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
