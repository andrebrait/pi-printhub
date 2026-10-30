# SPDX-License-Identifier: AGPL-3.0-only
import os
import subprocess
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from printhub import printing
from printhub.printing import PrinterInfo, PrintOptions

JOB_LOG = Path(os.environ.get("PRINTHUB_JOB_LOG", "/tmp/printhub-jobs.log"))

INFO = PrinterInfo(
    state="idle",
    state_reasons=[],
    accepting=True,
    supported={
        "media": ["iso_a4_210x297mm", "na_letter_8.5x11in"],
        "print-quality": ["3", "4", "5"],
        "print-color-mode": ["monochrome"],
        "orientation-requested": ["3", "4"],
        "sides": ["one-sided"],
        "print-scaling": ["fit", "none"],
    },
    defaults={},
    margins={},
)
OPTS = PrintOptions(
    media="iso_a4_210x297mm",
    print_quality="4",
    print_color_mode="monochrome",
    orientation_requested="3",
    sides="one-sided",
)


def test_validate_accepts_supported_options():
    printing.validate(OPTS, INFO)


def test_validate_rejects_unsupported_sides():
    with pytest.raises(ValueError, match="sides"):
        printing.validate(replace(OPTS, sides="two-sided-long-edge"), INFO)


@pytest.mark.parametrize("ranges", ["3-", "a", "1,,2", "", "1-3,"])
def test_validate_rejects_bad_page_ranges(ranges):
    with pytest.raises(ValueError, match="page_ranges"):
        printing.validate(replace(OPTS, page_ranges=ranges), INFO)


def test_validate_accepts_page_ranges():
    printing.validate(replace(OPTS, page_ranges="1-3,5"), INFO)


@pytest.mark.parametrize("copies", [0, 100])
def test_validate_rejects_copies_out_of_range(copies):
    with pytest.raises(ValueError, match="copies"):
        printing.validate(replace(OPTS, copies=copies), INFO)


def test_validate_checks_print_scaling_against_ipp_keywords_when_printer_omits_it():
    info = replace(INFO, supported={**INFO.supported, "print-scaling": []})
    printing.validate(replace(OPTS, print_scaling="fit"), info)
    with pytest.raises(ValueError, match="print_scaling"):
        printing.validate(replace(OPTS, print_scaling="bogus"), info)


def test_unoffered_option_is_not_validated_or_submitted(tmp_path):
    class FakeConn:
        def printFile(self, queue, path, title, options):
            self.options = options
            return 1

    info = replace(INFO, supported={**INFO.supported, "sides": [], "orientation-requested": []})
    opts = replace(OPTS, sides="two-sided-long-edge", orientation_requested="4")
    printing.validate(opts, info)
    conn = FakeConn()
    printing.submit(conn, "q", tmp_path / "x.pdf", "t", opts, info)
    assert "sides" not in conn.options
    # without an orientation to keep, CUPS must still be free to turn the page to fit
    assert "orientation-requested" not in conn.options and "pdfAutoRotate" not in conn.options
    assert conn.options["media"] == "iso_a4_210x297mm"
    assert conn.options["print-scaling"] == "fit"


# --- supplies and printer identity ---

# What CUPS reports for the HP OfficeJet Pro 6970 (kept while the printer is unreachable).
HP_INK = {
    "marker-names": ["magenta ink", "cyan ink", "yellow ink", "black ink"],
    "marker-colors": ["#FF00FF", "#00FFFF", "#FFFF00", "#000000"],
    "marker-levels": [60, 20, 60, 30],
    "marker-low-levels": [1, 20, 1, 1],
    "marker-high-levels": [100, 100, 100, 100],
    "marker-types": ["ink-cartridge"] * 4,
}
Supply = printing.Supply


def test_supplies_of_the_real_ink_printer():
    assert printing.supplies(HP_INK) == [
        Supply("Magenta ink", "ink-cartridge", "#FF00FF", 60, False),
        Supply("Cyan ink", "ink-cartridge", "#00FFFF", 20, True),  # at its low level counts as low
        Supply("Yellow ink", "ink-cartridge", "#FFFF00", 60, False),
        Supply("Black ink", "ink-cartridge", "#000000", 30, False),
    ]


def test_supplies_of_a_laser_printer():
    attrs = {
        "marker-names": ["black toner cartridge", "imaging drum", "waste toner box"],
        "marker-colors": ["#000000", "none", "none"],
        "marker-levels": [8, 95, -3],
        "marker-low-levels": [0, 0, 0],
        "marker-high-levels": [100, 100, 100],
        "marker-types": ["toner", "opc", "waste-toner"],
    }
    assert printing.supplies(attrs) == [
        Supply("Black toner cartridge", "toner", "#000000", 8, True),  # no low level: low below 15 %
        Supply("Imaging drum", "opc", "", 95, False),
        Supply("Waste toner box", "waste-toner", "", None, False),
    ]


def test_supplies_of_a_single_black_cartridge_arrive_as_scalars():
    attrs = {
        "marker-names": "black toner", "marker-colors": "#000000", "marker-levels": 50,
        "marker-low-levels": 10, "marker-high-levels": 100, "marker-types": "toner",
    }
    assert printing.supplies(attrs) == [Supply("Black toner", "toner", "#000000", 50, False)]


@pytest.mark.parametrize("attrs", [{}, {"marker-names": ["black ink"]}, {"marker-levels": [50]}])
def test_no_supplies_without_names_and_levels(attrs):
    assert printing.supplies(attrs) == []


@pytest.mark.parametrize("level", [-1, -2, -3])
def test_unknown_levels_are_none_and_not_low(level):
    (only,) = printing.supplies({**HP_INK, "marker-names": ["black ink"], "marker-levels": [level]})
    assert (only.level, only.low) == (None, False)


def test_names_and_levels_set_the_count():
    assert [s.name for s in printing.supplies({**HP_INK, "marker-levels": [60, 20]})] == ["Magenta ink", "Cyan ink"]
    assert [s.name for s in printing.supplies({**HP_INK, "marker-names": ["magenta ink", "cyan ink"]})] == ["Magenta ink", "Cyan ink"]


def test_shorter_colour_type_and_level_arrays_are_padded_with_their_defaults():
    # One entry for four supplies (as some firmwares report): all four are still supplies.
    short = {**HP_INK, "marker-colors": ["#FF00FF"], "marker-types": ["ink-cartridge"], "marker-low-levels": [1], "marker-high-levels": [100]}
    assert printing.supplies(short) == [
        Supply("Magenta ink", "ink-cartridge", "#FF00FF", 60, False),
        Supply("Cyan ink", "", "", 20, False),  # no low level here: low only below 15 %
        Supply("Yellow ink", "", "", 60, False),
        Supply("Black ink", "", "", 30, False),
    ]
    assert printing.supplies({**short, "marker-levels": [60, 10, 60, 30]})[1].low


def test_a_missing_high_level_means_100():
    attrs = {k: v for k, v in HP_INK.items() if k != "marker-high-levels"}
    assert [s.level for s in printing.supplies(attrs)] == [60, 20, 60, 30]


@pytest.mark.parametrize("bad", ["x", None, 2.5])
def test_unusable_low_and_high_levels_fall_back_to_the_defaults(bad):
    attrs = {**HP_INK, "marker-names": ["a"], "marker-levels": [10], "marker-low-levels": [bad], "marker-high-levels": [bad]}
    (only,) = printing.supplies(attrs)
    assert (only.level, only.low) == (10, True)


def test_a_waste_container_is_never_low():
    # Its level is how full it is, so the low-level test means nothing.
    attrs = {
        "marker-names": ["waste toner box", "waste ink"], "marker-types": ["waste-toner", "waste-ink"],
        "marker-levels": [5, 8], "marker-low-levels": [10, 0], "marker-high-levels": [100, 100],
    }
    assert [(s.level, s.low) for s in printing.supplies(attrs)] == [(5, False), (8, False)]


def test_level_is_a_percentage_of_the_high_level():
    attrs = {**HP_INK, "marker-names": ["a", "b", "c"], "marker-levels": [5, 300, 0], "marker-high-levels": [10, 200, 0]}
    # a high level of 0 is no information: the level is taken as a percentage already
    assert [s.level for s in printing.supplies(attrs)] == [50, 100, 0]


@pytest.mark.parametrize(
    ("raw", "color"),
    [
        ("#00FFFF#FF00FF", "#00FFFF"),
        ("#ff00ff", "#ff00ff"),
        ("none", ""),
        ("red", ""),
        ("#12345", ""),
        ("#GGGGGG", ""),
        ("#00FFFFxyz", ""),
        ("", ""),
    ],
)
def test_colour_is_a_hex_triplet_or_empty(raw, color):
    (only,) = printing.supplies({**HP_INK, "marker-names": ["x"], "marker-colors": [raw]})
    assert only.color == color


class AttrsConn:
    """getPrinterAttributes with the real printer's values; extra overrides or adds attributes."""

    def __init__(self, **extra):
        self.attrs = {
            "printer-state": 3,
            "printer-is-accepting-jobs": True,
            "printer-info": "HP OfficeJet Pro 6970",
            "printer-make-and-model": "HP OfficeJet Pro 6970, driverless, cups-filters 1.28.17",
            **HP_INK,
            **extra,
        }

    def getPrinterAttributes(self, queue, requested_attributes=None):
        self.requested = requested_attributes
        return self.attrs


def test_printer_info_carries_name_model_and_supplies():
    conn = AttrsConn()
    info = printing.printer_info(conn, "OfficeJet")
    assert (info.name, info.model, info.web_page) == ("HP OfficeJet Pro 6970", "HP OfficeJet Pro 6970", "")
    assert info.supplies == printing.supplies(HP_INK)
    for key in (*HP_INK, "printer-info", "printer-make-and-model", "printer-supply-info-uri"):
        assert key in conn.requested
    assert "printer-more-info" not in conn.requested


def test_printer_info_falls_back_to_the_queue_name():
    conn = AttrsConn()
    del conn.attrs["printer-info"], conn.attrs["printer-make-and-model"]
    info = printing.printer_info(conn, "OfficeJet")
    assert (info.name, info.model) == ("OfficeJet", "")


@pytest.mark.parametrize(
    ("extra", "page"),
    [
        ({"printer-supply-info-uri": "http://192.168.1.50/"}, "http://192.168.1.50/"),
        ({"printer-supply-info-uri": "https://printer.lan/status"}, "https://printer.lan/status"),
        ({"printer-supply-info-uri": "HTTPS://Printer.lan/"}, "HTTPS://Printer.lan/"),
        # printer-more-info is CUPS's own page on the Pi (or nothing): never offered, with or without a supply page
        ({"printer-more-info": "http://printer.lan/"}, ""),
        ({"printer-more-info": "http://localhost/printers/OfficeJet"}, ""),
        ({"printer-supply-info-uri": "http://localhost/", "printer-more-info": "http://printer.lan/"}, ""),
        # this machine's own addresses would not open from another device
        ({"printer-supply-info-uri": "http://localhost/"}, ""),
        ({"printer-supply-info-uri": "http://localhost./"}, ""),
        ({"printer-supply-info-uri": "http://LOCALHOST:631/"}, ""),
        ({"printer-supply-info-uri": "http://127.0.0.1:631/"}, ""),
        ({"printer-supply-info-uri": "http://127.1.2.3/"}, ""),
        ({"printer-supply-info-uri": "http://[::1]/"}, ""),
        # credentials in a link are shown in the page and sent to the printer
        ({"printer-supply-info-uri": "http://user:pw@printer.lan/"}, ""),
        ({"printer-supply-info-uri": "http://user@printer.lan/"}, ""),
        ({"printer-supply-info-uri": "javascript:alert(1)"}, ""),
        ({"printer-supply-info-uri": "ftp://printer.lan/"}, ""),
        ({"printer-supply-info-uri": "http:///nohost"}, ""),
        ({"printer-supply-info-uri": ""}, ""),
    ],
)
def test_web_page_is_the_supply_page_if_it_is_a_reachable_http_url(extra, page):
    assert printing.printer_info(AttrsConn(**extra), "OfficeJet").web_page == page


# --- virtual printer (tests/ci/start-virtual-printer.sh) ---


@pytest.fixture
def queue() -> str:
    return os.environ["PRINTHUB_TEST_QUEUE"]


@pytest.fixture
def conn():
    return printing.connect()


@pytest.fixture
def pdf(tmp_path) -> Path:
    path = tmp_path / "one.pdf"
    Image.new("RGB", (100, 100), "white").save(path, "PDF")
    return path


def job_state(conn, queue: str, job_id: int) -> str:
    return next(j.state for j in printing.queue_jobs(conn, queue) if j.id == job_id)


def wait_for(conn, queue: str, job_id: int, state: str, timeout: float = 30) -> str:
    deadline = time.monotonic() + timeout
    while True:
        current = job_state(conn, queue, job_id)
        if current == state or time.monotonic() > deadline:
            return current
        time.sleep(0.2)


def logged_job(title: str, timeout: float = 30) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if JOB_LOG.exists():
            for line in JOB_LOG.read_text().splitlines():
                env = dict(kv.split("=", 1) for kv in line.split("\t") if "=" in kv)
                if env.get("IPP_JOB_NAME") == title:
                    return env
        time.sleep(0.2)
    raise AssertionError(f"job {title!r} never reached the job log")


@pytest.mark.cups
def test_printer_info(conn, queue):
    info = printing.printer_info(conn, queue)
    assert info.state == "idle"
    assert info.accepting
    assert "none" not in info.state_reasons
    assert "two-sided-long-edge" in info.supported["sides"]
    assert info.supported["media"]
    assert info.supported["print-quality"] == ["3", "4", "5"]
    assert info.defaults["print-quality"] == "4"
    assert info.margins == {"top": 635, "bottom": 635, "left": 340, "right": 340}


@pytest.mark.cups
def test_submitted_options_reach_the_printer(conn, queue, pdf):
    opts = PrintOptions(
        media="iso_a4_210x297mm",
        print_quality="5",
        print_color_mode="monochrome",
        orientation_requested="4",
        sides="two-sided-short-edge",
        copies=2,
    )
    info = printing.printer_info(conn, queue)
    printing.validate(opts, info)
    title = uuid.uuid4().hex
    job_id = printing.submit(conn, queue, pdf, title, opts, info)

    assert wait_for(conn, queue, job_id, "completed") == "completed"
    env = logged_job(title)
    assert env["IPP_SIDES"] == "two-sided-short-edge"
    assert env["IPP_PRINT_COLOR_MODE"] == "monochrome"
    assert env["IPP_PRINT_QUALITY"] == "high"  # ippeveprinter exports enum 5 as its keyword
    assert env["IPP_COPIES"] == "2"
    assert "x-dimension=21000 y-dimension=29700" in env["IPP_MEDIA_COL"]
    # CUPS applies orientation itself (pdftopdf) and does not forward it to the printer;
    # the page that arrives is landscape.
    size = subprocess.run(
        ["pdfinfo", env["FILE"]], capture_output=True, text=True, check=True
    ).stdout
    page_size = next(l for l in size.splitlines() if l.startswith("Page size:")).split()
    assert float(page_size[2]) > float(page_size[4])


@pytest.mark.cups
def test_job_completes(conn, queue, pdf):
    info = printing.printer_info(conn, queue)
    job_id = printing.submit(conn, queue, pdf, uuid.uuid4().hex, OPTS, info)
    assert wait_for(conn, queue, job_id, "completed") == "completed"
    assert job_id not in printing.active_job_ids(conn, queue)


@pytest.mark.cups
def test_cancel_held_job(conn, queue, pdf):
    job_id = conn.printFile(queue, str(pdf), uuid.uuid4().hex, {"job-hold-until": "indefinite"})
    assert job_state(conn, queue, job_id) == "held"
    assert job_id in printing.active_job_ids(conn, queue)

    printing.cancel(conn, job_id)

    assert wait_for(conn, queue, job_id, "canceled") == "canceled"
    assert job_id not in printing.active_job_ids(conn, queue)

