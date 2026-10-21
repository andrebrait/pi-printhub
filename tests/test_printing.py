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

    info = replace(INFO, supported={**INFO.supported, "sides": []})
    opts = replace(OPTS, sides="two-sided-long-edge")
    printing.validate(opts, info)
    conn = FakeConn()
    printing.submit(conn, "q", tmp_path / "x.pdf", "t", opts, info)
    assert "sides" not in conn.options
    assert conn.options["media"] == "iso_a4_210x297mm"
    assert conn.options["print-scaling"] == "fit"


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


def wait_for(conn, job_id: int, state: str, timeout: float = 30) -> printing.JobStatus:
    deadline = time.monotonic() + timeout
    while True:
        status = printing.job_status(conn, job_id)
        if status.state == state or time.monotonic() > deadline:
            return status
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

    assert wait_for(conn, job_id, "completed").state == "completed"
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
    assert wait_for(conn, job_id, "completed").state == "completed"
    assert job_id not in printing.active_job_ids(conn, queue)


@pytest.mark.cups
def test_cancel_held_job(conn, queue, pdf):
    job_id = conn.printFile(queue, str(pdf), uuid.uuid4().hex, {"job-hold-until": "indefinite"})
    assert printing.job_status(conn, job_id).state == "held"
    assert job_id in printing.active_job_ids(conn, queue)

    printing.cancel(conn, job_id)

    assert wait_for(conn, job_id, "canceled").state == "canceled"
    assert job_id not in printing.active_job_ids(conn, queue)

