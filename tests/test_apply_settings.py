# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import pwd
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "deploy" / "apply-settings.sh"
REQUEST = {"share": False, "expose_usb": True, "at": "2026-10-29T10:00:00+00:00"}
OWNER = pwd.getpwuid(os.getuid()).pw_name


class Box:
    """A fake system: data dir, ipp-usb.conf, avahi dir and CUPS/systemd commands that log their argv."""

    def __init__(self, tmp: Path):
        self.data, self.avahi, self.bin = tmp / "data", tmp / "avahi", tmp / "bin"
        self.conf, self.log, self.uri = tmp / "ipp-usb.conf", tmp / "log", tmp / "uri"
        for d in (self.data, self.avahi, self.bin):
            d.mkdir()
        self.conf.write_text("[http]\n  interface = all\n")
        self.uri.write_text("device for OfficeJet: ipp://printer.local/ipp/print\n")
        self.shared = tmp / "shared"
        self.shared.write_text("true")
        # The description repeats the flag: only the real token, after the quoted values, may count.
        (self.bin / "lpoptions").write_text(
            "#!/bin/sh\necho \"copies=1 printer-info='Shared printer-is-shared=true here' "
            f'printer-is-shared=$(cat {self.shared})"\n'
        )
        (self.bin / "lpoptions").chmod(0o755)
        for name in ("systemctl", "lpadmin", "cupsctl", "lpstat", "sleep"):
            exe = self.bin / name
            exe.write_text(
                f'#!/bin/sh\necho "{name} $*" >>{self.log}\n'
                f'[ "$1" = -v ] && cat {self.uri}\n'
                '[ "$1" = cat ] && [ "$ABSENT" = "$2" ] && exit 1\n'
                '[ "$1" = show ] && echo "$ACTIVE_SINCE"\n'
                f'[ "$FAIL" = {name} ] && exit 1\n[ "$FAIL" = "{name} $1" ] && exit 1\nexit 0\n'
            )
            exe.chmod(0o755)

    def run(self, expr: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", f'source "{SCRIPT}"; {expr}'],
            env={
                "PATH": f"{self.bin}:/usr/bin:/bin", "DATA_DIR": str(self.data), "IPP_USB_CONF": str(self.conf),
                "AVAHI_DIR": str(self.avahi), "OWNER": OWNER, **env,
            },
            capture_output=True,
            text=True,
        )

    def request(self, body: str | dict = REQUEST) -> Path:
        path = self.data / "system-request.json"
        path.write_text(body if isinstance(body, str) else json.dumps(body))
        return path

    def calls(self) -> list[str]:
        """The mutating commands run; the lookups (lpstat, systemctl cat) are not what tests assert on."""
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [c for c in lines if not c.startswith(("lpstat", "systemctl cat", "systemctl show", "systemctl is-active"))]


def test_request_defaults_to_shared_and_exposed(tmp_path):
    r = Box(tmp_path).run("read_request")
    assert (r.returncode, r.stdout) == (0, "true true\n")


def test_request_is_read_as_words(tmp_path):
    box = Box(tmp_path)
    box.request()
    assert box.run("read_request").stdout == "false true\n"


def test_request_refuses_unsafe_files(tmp_path):
    box = Box(tmp_path)
    path = box.data / "system-request.json"
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps(REQUEST))
    stranger = "root" if OWNER == "nobody" else "nobody"
    cases = {
        "symlink": (lambda: path.symlink_to(secret), {}),
        "directory": (lambda: path.mkdir(), {}),
        "fifo": (lambda: os.mkfifo(path), {}),
        "too big": (lambda: box.request(json.dumps(REQUEST) + " " * 1025), {}),
        "string bool": (lambda: box.request({**REQUEST, "share": "false"}), {}),
        "extra key": (lambda: box.request({**REQUEST, "extra": 1}), {}),
        "not JSON": (lambda: box.request("{"), {}),
        "wrong owner": (lambda: box.request(), {"OWNER": stranger}),
        "int bool": (lambda: box.request({**REQUEST, "share": 1}), {}),
    }
    for name, (setup, env) in cases.items():
        if path.is_dir():
            path.rmdir()
        elif path.is_symlink() or path.exists():
            path.unlink()
        setup()
        r = box.run("read_request", **env)
        assert r.returncode != 0 and r.stdout == "" and r.stderr.strip(), name


def test_request_of_exactly_1024_bytes_is_accepted(tmp_path):
    box = Box(tmp_path)
    body = json.dumps(REQUEST)
    box.request(body + " " * (1024 - len(body)))
    assert box.run("read_request").stdout == "false true\n"


def test_expose_toggles_the_interface_and_restarts_only_on_change(tmp_path):
    box = Box(tmp_path)
    assert box.run("apply_expose false").returncode == 0
    assert box.conf.read_text() == "[http]\n  interface = loopback\n"
    assert box.calls() == ["systemctl try-restart ipp-usb"]
    assert box.run("apply_expose false").returncode == 0
    assert box.calls() == ["systemctl try-restart ipp-usb"]
    assert box.run("apply_expose true").returncode == 0
    assert box.conf.read_text() == "[http]\n  interface = all\n"
    assert len(box.calls()) == 2


def test_interface_line_with_a_trailing_comment_is_read_and_rewritten(tmp_path):
    # The first is the line Debian's ipp-usb package ships.
    lines = ("  interface = all # all | loopback", "  interface = all\t# c", "  interface = all#c")
    for n, line in enumerate(lines):
        (tmp_path / str(n)).mkdir()
        box = Box(tmp_path / str(n))
        box.conf.write_text(f"[http]\n{line}\n")
        assert box.run("current_expose").stdout == "true\n", line
        assert box.run("apply_expose true").returncode == 0 and box.calls() == [], line
        assert box.run("apply_expose false").returncode == 0
        assert box.conf.read_text() == f"[http]\n{line.replace('all', 'loopback', 1)}\n", line
        assert box.run("current_expose").stdout == "false\n", line
        assert box.calls() == ["systemctl try-restart ipp-usb"], line


def uptime_us() -> int:
    return int(float(Path("/proc/uptime").read_text().split()[0]) * 1_000_000)


def test_ipp_usb_is_not_restarted_within_30s_of_starting(tmp_path):
    box = Box(tmp_path)
    assert box.run("apply_expose false", ACTIVE_SINCE=str(uptime_us() - 5_500_000)).returncode == 0
    calls = box.calls()
    assert len(calls) == 2 and calls[0] in ("sleep 24", "sleep 25") and calls[1] == "systemctl try-restart ipp-usb"


def test_a_long_running_or_unchanged_ipp_usb_is_restarted_without_waiting(tmp_path):
    box = Box(tmp_path)
    assert box.run("apply_expose false", ACTIVE_SINCE=str(uptime_us() - 3_600_000_000)).returncode == 0
    assert box.calls() == ["systemctl try-restart ipp-usb"]
    # Nothing to restart, so nothing to wait for either.
    assert box.run("apply_expose false", ACTIVE_SINCE=str(uptime_us() - 1_000_000)).returncode == 0
    assert box.calls() == ["systemctl try-restart ipp-usb"]


def test_unsharing_unannounces_and_stops_airsane(tmp_path):
    box = Box(tmp_path)
    for kind in ("airprint", "generic"):
        (box.avahi / f"printhub-{kind}.service").write_text("x")
    assert box.run("apply_share false").returncode == 0
    assert box.calls() == [
        "lpadmin -p OfficeJet -o printer-is-shared=false",
        "cupsctl --no-share-printers",
        "systemctl disable --now airsaned",
        "systemctl reset-failed airsaned",  # AirSane segfaults on SIGTERM, which leaves the unit failed
    ]
    assert list(box.avahi.iterdir()) == []


def test_sharing_a_network_queue_just_flips_cups(tmp_path):
    box = Box(tmp_path)
    assert box.run("apply_share true").returncode == 0
    assert [c for c in box.calls() if not c.startswith("lpstat")] == [
        "lpadmin -p OfficeJet -o printer-is-shared=true",
        "cupsctl --share-printers",
        "systemctl enable --now airsaned",
    ]


def test_resharing_an_ipp_usb_queue_reruns_printer_setup(tmp_path):
    box = Box(tmp_path)
    box.uri.write_text("device for OfficeJet: ipp://localhost:60000/ipp/print\n")
    assert box.run("apply_share true").returncode == 0
    assert "systemctl start printhub-printer.service" in box.calls()
    assert not any(c.startswith("lpadmin") for c in box.calls())


def test_failed_command_status_reports_the_state_read_back(tmp_path):
    # lpadmin fails, so the status must say what the system is now, not what was asked for.
    for shared in (True, False):
        for expose in (True, False):
            (tmp_path / f"{shared}-{expose}").mkdir()
            box = Box(tmp_path / f"{shared}-{expose}")
            box.shared.write_text(str(shared).lower())
            box.request({**REQUEST, "expose_usb": expose})
            r = box.run("run_apply", FAIL="lpadmin")
            assert r.returncode != 0
            status = json.loads((box.data / "system-status.json").read_text())
            assert status["state"] == "failed" and status["error"]
            assert status["request_at"] == REQUEST["at"]
            assert status["share"] is shared
            assert status["expose_usb"] is ("interface = all" in box.conf.read_text())


def test_interface_prefixed_with_all_does_not_count_as_exposed(tmp_path):
    box = Box(tmp_path)
    box.conf.write_text("[http]\n  interface = allxyz\n")
    assert box.run("current_expose").stdout == "false\n"


def test_quoted_text_in_the_printer_options_does_not_count_as_shared(tmp_path):
    box = Box(tmp_path)
    box.shared.write_text("false")
    assert box.run("current_share").stdout == "false\n"
    box.shared.write_text("true")
    assert box.run("current_share").stdout == "true\n"


def test_a_failing_reset_failed_does_not_fail_unsharing(tmp_path):
    assert Box(tmp_path).run("apply_share false", FAIL="systemctl reset-failed").returncode == 0


def test_applied_status_is_private_and_matches_the_request(tmp_path):
    box = Box(tmp_path)
    box.request()
    assert box.run("run_apply").returncode == 0
    path = box.data / "system-status.json"
    status = json.loads(path.read_text())
    assert status | {"at": None} == {
        "state": "applied", "share": False, "expose_usb": True, "error": None, "at": None, "request_at": REQUEST["at"],
    }
    assert path.stat().st_mode & 0o777 == 0o600 and path.stat().st_uid == os.getuid()
    assert [p.name for p in box.data.iterdir() if p.name.startswith(".")] == []


def test_unsafe_request_writes_a_failed_status_and_changes_nothing(tmp_path):
    box = Box(tmp_path)
    box.request({**REQUEST, "extra": 1})
    assert box.run("run_apply").returncode != 0
    status = json.loads((box.data / "system-status.json").read_text())
    assert status["state"] == "failed" and status["request_at"] is None
    assert box.calls() == [] or all(c.startswith("lpstat") for c in box.calls())
    assert "interface = all" in box.conf.read_text()


def test_airsane_not_installed_yet_is_skipped_not_failed(tmp_path):
    # First install: setup_printer's sharing step runs before AirSane's unit exists.
    for want in ("true", "false"):
        (tmp_path / want).mkdir()
        box = Box(tmp_path / want)
        r = box.run(f"apply_share {want}", ABSENT="airsaned")
        assert r.returncode == 0, r.stderr
        assert not any("airsaned" in c for c in box.calls())


def test_no_ipp_usb_conf_means_nothing_to_expose(tmp_path):
    box = Box(tmp_path)
    box.conf.unlink()
    box.request({**REQUEST, "share": True})
    r = box.run("run_apply")
    assert r.returncode == 0, r.stderr
    status = json.loads((box.data / "system-status.json").read_text())
    assert status["state"] == "applied"
    assert box.run("current_expose").stdout == "false\n"
    assert "lpadmin -p OfficeJet -o printer-is-shared=true" in box.calls()  # apply_share still ran


def test_no_queue_leaves_cups_alone_but_follows_airsane(tmp_path):
    for want, verbs in (("true", ["enable --now"]), ("false", ["disable --now", "reset-failed"])):
        (tmp_path / want).mkdir()
        box = Box(tmp_path / want)
        r = box.run(f"apply_share {want}", FAIL="lpstat")
        assert r.returncode == 0, r.stderr
        assert box.calls() == [f"systemctl {verb} airsaned" for verb in verbs]


def test_a_request_that_changes_during_the_apply_is_applied_too(tmp_path):
    # The web app writes a newer request (sharing on) while lpadmin runs, for the first REWRITES calls.
    for rewrites, passes, applied_at in ((1, 2, "t1"), (99, 3, "t2")):  # the app keeps writing: stop after 3 passes
        (tmp_path / str(rewrites)).mkdir()
        box = Box(tmp_path / str(rewrites))
        count = tmp_path / str(rewrites) / "count"
        lpadmin = box.bin / "lpadmin"
        lpadmin.write_text(
            f'#!/bin/sh\necho "lpadmin $*" >>{box.log}\n'
            f"n=$(($(cat {count} 2>/dev/null || echo 0) + 1)); echo $n >{count}\n"
            f'[ $n -le "$REWRITES" ] && printf \'{{"share": true, "expose_usb": true, "at": "t%s"}}\' $n >{box.data}/system-request.json\n'
            "exit 0\n"
        )
        box.request()
        r = box.run("run_apply", REWRITES=str(rewrites))
        assert r.returncode == 0, r.stderr
        status = json.loads((box.data / "system-status.json").read_text())
        assert (status["state"], status["share"], status["request_at"]) == ("applied", True, applied_at)
        assert len([c for c in box.calls() if c.startswith("lpadmin")]) == passes
