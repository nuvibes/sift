# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the hardware probe.

Two things are worth pinning down. The parser is tested against a real, captured `ffmpeg
-encoders` listing, because a future ffmpeg quietly changing that format is exactly how hardware
acceleration would switch itself off with nobody noticing. And the "GPU asked for but not here"
path is tested to both warn and keep going, because continuing on the CPU is the whole promise:
no machine is required to have a GPU.
"""

from __future__ import annotations

import ctypes
import os
import socket
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from sift.kernel import hardware
from sift.kernel.config import Settings, get_settings
from sift.kernel.hardware import (
    MAX_WORKERS,
    Card,
    HardwareReport,
    parse_encoders,
    probe,
    worker_concurrency,
)
from sift.kernel.http import SESSION_COOKIE_NAME
from sift.kernel.log import configure_logging
from sift.kernel.subprocess import SubprocessError, SubprocessResult
from sift.main import create_app
from sift.testing.auth import establish_session
from sift.testing.tools import stand_in_tool

FIXTURES = Path(__file__).parent / "fixtures" / "hardware"


def _sign_in_admin(client: TestClient) -> None:
    """The hardware report at /health is admin-only, so a test that reads it signs in first."""
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    _, token, _ = establish_session(
        db_path, role="admin", username="hw-admin", password="Hardware-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)


# The encoders an ffmpeg build lists, parsed from captured output. Used as
# the "ffmpeg has these compiled in" input to the report tests, so a parser break shows up here too.
REAL_ENCODERS = parse_encoders((FIXTURES / "ffmpeg_encoders.txt").read_text())


# --- the encoder listing parser ----------------------------------------------------------


@pytest.mark.unit
def test_the_parser_reads_encoder_names_from_a_real_listing() -> None:
    """Both hardware and software encoders come through, and the legend above them does not."""
    assert "h264_nvenc" in REAL_ENCODERS  # NVIDIA
    assert "h264_vaapi" in REAL_ENCODERS  # VAAPI
    assert "h264_qsv" in REAL_ENCODERS  # Intel Quick Sync
    assert "libx264" in REAL_ENCODERS  # a plain software encoder

    # The legend uses the same flag column and must not be mistaken for encoders.
    assert "=" not in REAL_ENCODERS
    assert "Video" not in REAL_ENCODERS


@pytest.mark.unit
def test_the_parser_finds_nothing_before_the_table_begins() -> None:
    """No row of dashes means no encoder table, so nothing is claimed rather than legend lines."""
    legend_only = " Encoders:\n V..... = Video\n A..... = Audio\n"
    assert parse_encoders(legend_only) == frozenset()


@pytest.mark.unit
def test_the_parser_skips_lines_in_the_table_that_are_not_encoders() -> None:
    """Blank lines and stray text after the dashes are not encoders and are not counted."""
    listing = " Encoders:\n ------\n V....D h264 A description\n\n a stray note, not an encoder\n"
    assert parse_encoders(listing) == frozenset({"h264"})


# --- the report, from ffmpeg output and the devices actually present ----------------------


async def _probe_with(
    monkeypatch: pytest.MonkeyPatch,
    *,
    encoders: frozenset[str] = REAL_ENCODERS,
    nvidia: bool = False,
    render: bool = False,
    amd_compute: bool = False,
    gpu: Literal["auto", "cuda", "rocm"] = "auto",
) -> HardwareReport:
    async def _fake_encoders(_: Settings) -> frozenset[str]:
        return encoders

    monkeypatch.setattr(hardware, "_ffmpeg_encoders", _fake_encoders)
    monkeypatch.setattr(hardware, "_nvidia_present", lambda: nvidia)
    monkeypatch.setattr(hardware, "_render_node_present", lambda: render)
    monkeypatch.setattr(hardware, "_amd_compute_present", lambda: amd_compute)
    return await probe(Settings(gpu=gpu))


@pytest.mark.unit
async def test_a_cpu_only_machine_reports_no_acceleration(monkeypatch: pytest.MonkeyPatch) -> None:
    """The trap this exists to catch: ffmpeg has the encoders compiled in, but no device can run
    them. Reporting them as usable would pick a path slower than plain CPU."""
    report = await _probe_with(monkeypatch, nvidia=False, render=False, amd_compute=False)

    assert report.transcode_encoders == ()
    assert report.gpu_transcode is False
    assert report.cuda is False
    assert report.rocm is False


@pytest.mark.unit
async def test_an_nvidia_machine_reports_nvenc(monkeypatch: pytest.MonkeyPatch) -> None:
    report = await _probe_with(monkeypatch, nvidia=True, render=False)

    assert report.cuda is True
    assert "h264_nvenc" in report.transcode_encoders
    # No render node, so the VAAPI/QSV encoders that ffmpeg also has stay off.
    assert "h264_vaapi" not in report.transcode_encoders
    assert "h264_qsv" not in report.transcode_encoders


@pytest.mark.unit
async def test_a_render_node_enables_vaapi_and_qsv(monkeypatch: pytest.MonkeyPatch) -> None:
    report = await _probe_with(monkeypatch, nvidia=False, render=True)

    assert "h264_vaapi" in report.transcode_encoders
    assert "h264_qsv" in report.transcode_encoders
    assert "h264_nvenc" not in report.transcode_encoders
    assert report.cuda is False


@pytest.mark.unit
async def test_an_amd_machine_reports_rocm(monkeypatch: pytest.MonkeyPatch) -> None:
    report = await _probe_with(monkeypatch, amd_compute=True, render=True)

    assert report.rocm is True
    assert report.cuda is False


# --- fail loud, then carry on ------------------------------------------------------------


@pytest.mark.unit
async def test_requesting_cuda_without_a_gpu_warns_but_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The load-bearing case: a GPU was asked for on a machine that has none. It must say so, and
    it must keep running on the CPU rather than fall over."""
    configure_logging("INFO", redact_personal=True)
    report = await _probe_with(monkeypatch, gpu="cuda", nvidia=False)

    # It said so, in the report served at /health and in the startup log.
    assert any("NVIDIA" in note for note in report.warnings)
    assert "hardware.acceleration_unavailable" in capsys.readouterr().out

    # And it carried on: a usable report, on the CPU.
    assert isinstance(report, HardwareReport)
    assert report.cuda is False
    assert report.gpu_transcode is False
    assert report.worker_concurrency >= 1


@pytest.mark.unit
async def test_requesting_rocm_without_a_gpu_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    report = await _probe_with(monkeypatch, gpu="rocm", amd_compute=False)

    assert any("AMD" in note for note in report.warnings)
    assert report.rocm is False


@pytest.mark.unit
async def test_auto_on_a_cpu_box_is_not_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """No GPU was asked for, so no GPU is not a fault. The CPU is the expected answer."""
    report = await _probe_with(monkeypatch, gpu="auto", nvidia=False, amd_compute=False)

    assert report.warnings == ()


@pytest.mark.unit
async def test_requesting_a_gpu_that_is_present_does_not_warn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = await _probe_with(monkeypatch, gpu="cuda", nvidia=True)

    assert report.warnings == ()
    assert report.cuda is True


# --- a broken ffmpeg is not fatal --------------------------------------------------------


@pytest.mark.integration
async def test_a_missing_ffmpeg_leaves_no_encoders_rather_than_crashing() -> None:
    """The probe reports what it found. If ffmpeg cannot be run, that is no hardware encoders,
    not an exception: the app notices a missing ffmpeg later, with a better message."""
    settings = Settings(ffmpeg_path="/nonexistent/ffmpeg")

    encoders = await hardware._ffmpeg_encoders(settings)

    assert encoders == frozenset()


@pytest.mark.integration
async def test_a_hanging_ffmpeg_times_out_and_reports_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wedged binary must not hold up startup forever. It is given a bounded time and, when it
    runs out, reported as no encoders rather than waited on."""
    monkeypatch.setattr(hardware, "_FFMPEG_TIMEOUT_SECONDS", 0.2)
    # Longer than the budget above by a wide margin, and short enough that a copy which somehow
    # outlives being killed is gone before the suite is.
    hangs = stand_in_tool(tmp_path / "tools", "ffmpeg-that-hangs", "import time; time.sleep(5)")

    encoders = await hardware._ffmpeg_encoders(Settings(ffmpeg_path=hangs))

    assert encoders == frozenset()


@pytest.mark.integration
async def test_an_ffmpeg_that_exits_nonzero_reports_nothing(tmp_path: Path) -> None:
    fails = stand_in_tool(tmp_path / "tools", "ffmpeg-that-fails", "raise SystemExit(3)")

    encoders = await hardware._ffmpeg_encoders(Settings(ffmpeg_path=fails))

    assert encoders == frozenset()


# --- naming the processor and the card ----------------------------------------------------
#
# Both of these are for one line on a settings screen, and both must fail to a shrug rather than to
# an error: a machine that cannot say what its processor is called still has a core count, and a
# card that will not introduce itself is still a card. Every path back out of them is "say nothing",
# which is exactly the shape that goes untested: there is no wrong answer to notice, only a
# missing one, and a missing one looks the same as a machine that genuinely has nothing to say.


@pytest.mark.unit
def test_the_processor_is_named_from_the_field_this_machine_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy path, and it is here so the three refusals below mean something.

    Without it every one of them would be asserting None against a function that returns None on
    every machine, which passes whatever the code does.
    """
    listing = tmp_path / "cpuinfo"
    listing.write_text("processor\t: 0\nmodel name\t: Some Processor 9000\nstepping\t: 2\n")
    monkeypatch.setattr(hardware, "_WINDOWS", False)
    monkeypatch.setattr(hardware, "_CPU_INFO", listing)

    assert hardware._cpu_model() == "Some Processor 9000"


@pytest.mark.unit
def test_a_board_that_writes_a_different_field_is_still_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ARM board writes "Hardware" where an x86 machine writes "model name"."""
    listing = tmp_path / "cpuinfo"
    listing.write_text("Hardware\t: Some Board\n")
    monkeypatch.setattr(hardware, "_WINDOWS", False)
    monkeypatch.setattr(hardware, "_CPU_INFO", listing)

    assert hardware._cpu_model() == "Some Board"


@pytest.mark.unit
def test_a_processor_listing_that_cannot_be_read_names_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No /proc at all is an ordinary answer, not an error: Sift runs on machines without one."""
    monkeypatch.setattr(hardware, "_WINDOWS", False)
    monkeypatch.setattr(hardware, "_CPU_INFO", tmp_path / "not-here")

    assert hardware._cpu_model() is None


@pytest.mark.unit
def test_a_processor_listing_with_no_name_in_it_names_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listing that is there and says nothing useful. Two ways in: a field nobody recognises, and
    a recognised field left empty: the second is why the value is checked as well as the name."""
    listing = tmp_path / "cpuinfo"
    listing.write_text("processor\t: 0\nmodel name\t:\nflags\t: fpu vme\n")
    monkeypatch.setattr(hardware, "_WINDOWS", False)
    monkeypatch.setattr(hardware, "_CPU_INFO", listing)

    assert hardware._cpu_model() is None


@pytest.mark.unit
def test_on_windows_the_processor_is_named_by_the_registry_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OTHER half, and on this site it is the half that runs.

    The four above force the POSIX branch, because `_cpu_model` returns the registry answer on
    Windows and never opens the file they write, so without that they would assert a made-up name
    against whatever processor the machine really has. That leaves this: the Windows branch is
    `pragma: no cover`, so with nothing here NEITHER branch would be exercised on the site Sift
    ships on.

    What this pins is the delegation, not the registry: which reader `_cpu_model` reaches for, on
    each site, checked from either one.
    """
    monkeypatch.setattr(hardware, "_WINDOWS", True)
    monkeypatch.setattr(hardware, "_windows_cpu_model", lambda: "Some Registry Processor")

    assert hardware._cpu_model() == "Some Registry Processor"


@pytest.mark.unit
def test_on_windows_a_registry_that_says_nothing_names_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And None from the registry stays None rather than falling through to the file reader.

    A fall-through would answer with the BUILD MACHINE's processor inside a container, or with
    nothing on Windows where `/proc` does not exist: either way an answer from the wrong place.
    """
    listing = tmp_path / "cpuinfo"
    listing.write_text("model name\t: Some Processor 9000\n")
    monkeypatch.setattr(hardware, "_WINDOWS", True)
    monkeypatch.setattr(hardware, "_CPU_INFO", listing)
    monkeypatch.setattr(hardware, "_windows_cpu_model", lambda: None)

    assert hardware._cpu_model() is None


def _answers(result: SubprocessResult) -> Callable[..., Awaitable[SubprocessResult]]:
    async def _run(*_args: object, **_kwargs: object) -> SubprocessResult:
        return result

    return _run


@pytest.mark.unit
async def test_the_card_is_named_from_what_the_driver_tool_prints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path, for the same reason the processor has one: three of the four cases below are
    a pair of Nones, and a pair of Nones is what a broken function returns too."""
    printed = b"Some Card 9000, 590.12.34, 12282\n"
    monkeypatch.setattr(
        hardware, "run_tool", _answers(SubprocessResult(returncode=0, stdout=printed, stderr=b""))
    )

    assert await hardware._nvidia_identity() == [
        hardware.Card(
            name="Some Card 9000",
            driver="590.12.34",
            vram_bytes=12282 * 1024 * 1024,
            can_compute=True,
        )
    ]


@pytest.mark.unit
async def test_a_driver_tool_that_cannot_be_run_leaves_the_card_unnamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The device node is there and the tool is not, which is ordinary inside a container."""

    async def _refuse(*_args: object, **_kwargs: object) -> SubprocessResult:
        raise SubprocessError("nvidia-smi is not on PATH")

    monkeypatch.setattr(hardware, "run_tool", _refuse)

    assert await hardware._nvidia_identity() == []


@pytest.mark.unit
async def test_a_driver_tool_that_fails_leaves_the_card_unnamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It prints something on the way down, which is the case worth writing.

    A failing nvidia-smi is not silent: it says why. Read without looking at the exit status
    first, that sentence becomes the name of the card on the settings screen.
    """
    monkeypatch.setattr(
        hardware,
        "run_tool",
        _answers(
            SubprocessResult(
                returncode=9,
                stdout=b"Failed to initialize NVML: Driver/library version mismatch\n",
                stderr=b"",
            )
        ),
    )

    assert await hardware._nvidia_identity() == []


@pytest.mark.unit
async def test_a_driver_tool_that_prints_nothing_leaves_the_card_unnamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit zero and an empty answer. Distinct from the failure above and reached the same way."""
    monkeypatch.setattr(
        hardware, "run_tool", _answers(SubprocessResult(returncode=0, stdout=b"", stderr=b""))
    )

    assert await hardware._nvidia_identity() == []


# --- total RAM ---------------------------------------------------------------------------


@pytest.mark.unit
def test_total_ram_is_a_positive_number_on_this_machine() -> None:
    total = hardware._total_ram_bytes()
    assert total is not None
    assert total > 0


@pytest.mark.unit
def test_total_ram_is_none_where_the_site_will_not_say(monkeypatch: pytest.MonkeyPatch) -> None:
    """`raising=False` and the explicit `_WINDOWS`, because otherwise this test does not exist on
    the site Sift ships on: `os.sysconf` is absent from Windows, so setting it raised
    AttributeError at collection, and even patched, the Windows branch never reaches it."""

    def _refuse(_: str) -> int:
        raise ValueError("unknown configuration name")

    monkeypatch.setattr(hardware, "_WINDOWS", False)
    monkeypatch.setattr(os, "sysconf", _refuse, raising=False)
    assert hardware._total_ram_bytes() is None


@pytest.mark.unit
def test_total_ram_is_none_where_windows_will_not_say(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the same dispatch. It is the branch that carries the real risk: a `None`
    here is what the worker sizing and the diagnostics would have to work from."""
    monkeypatch.setattr(hardware, "_WINDOWS", True)
    monkeypatch.setattr(hardware, "_windows_total_memory", lambda: None)
    assert hardware._total_ram_bytes() is None


# --- worker concurrency comes from the machine, not a constant ---------------------------


@pytest.mark.unit
def test_the_worker_count_is_taken_from_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pool size follows the machine, because no two machines Sift runs on are the same."""
    monkeypatch.setattr(os, "cpu_count", lambda: 4)

    assert worker_concurrency(Settings(worker_concurrency=None)) == 3


@pytest.mark.unit
def test_a_small_machine_still_gets_a_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 1)

    assert worker_concurrency(Settings(worker_concurrency=None)) == 1


@pytest.mark.unit
def test_a_machine_that_will_not_say_how_many_cores_it_has_still_gets_a_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: None)

    assert worker_concurrency(Settings(worker_concurrency=None)) == 1


@pytest.mark.unit
def test_a_big_machine_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jobs are ffmpeg and disk. Thirty-two at once on one disk finishes slower than eight."""
    monkeypatch.setattr(os, "cpu_count", lambda: 64)

    assert worker_concurrency(Settings(worker_concurrency=None)) == MAX_WORKERS


@pytest.mark.unit
def test_an_operator_who_says_how_many_workers_they_want_gets_them() -> None:
    assert worker_concurrency(Settings(worker_concurrency=16)) == 16


@pytest.mark.unit
async def test_the_report_takes_its_worker_count_from_the_same_derivation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report carries exactly what the pool would have used: one derivation, one answer."""
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    report = await _probe_with(monkeypatch)

    assert report.worker_concurrency == worker_concurrency(Settings(worker_concurrency=None)) == 3


# --- /health carries the report, and nothing that fingerprints the machine ---------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


@pytest.mark.integration
def test_health_reports_the_real_hardware(client: TestClient) -> None:
    """Booted against a real probe and the machine's real ffmpeg. The report is admin-only."""
    _sign_in_admin(client)
    body = client.get("/health").json()

    assert body["status"] == "ok"
    report = body["hardware"]
    assert report["cpu_count"] >= 1
    assert "gpu" in report
    assert "transcode_encoders" in report


@pytest.mark.integration
def test_health_leaks_no_path_and_no_hostname(client: TestClient) -> None:
    """Even for an admin who is allowed to see it, the hardware summary is a fingerprinting
    surface: it must carry no filesystem path and no hostname."""
    _sign_in_admin(client)
    report = client.get("/health").json()["hardware"]

    as_text = str(report)
    assert "/" not in as_text  # no filesystem path in any field
    assert socket.gethostname() not in as_text


# --- the NVIDIA probe on each site ---------------------------------------------


@pytest.mark.unit
def test_the_nvidia_probe_does_not_look_for_a_device_node_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/dev/nvidia0` cannot exist on Windows, so the POSIX branch would answer "no card" on every
    machine Sift actually ships to, and that gates every `*_nvenc` encoder out of the ladder, on
    hardware where NVENC works."""
    asked: list[str] = []
    monkeypatch.setattr(hardware, "_WINDOWS", True)
    monkeypatch.setattr(hardware, "_windows_nvidia_present", lambda: True)
    monkeypatch.setattr(os.path, "exists", lambda node: asked.append(node) or False)  # type: ignore[func-returns-value]

    assert hardware._nvidia_present() is True
    assert asked == [], f"the Windows branch fell through to a device node: {asked}"


@pytest.mark.unit
def test_the_nvidia_probe_still_reads_a_device_node_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hardware, "_WINDOWS", False)
    monkeypatch.setattr(
        hardware, "_windows_nvidia_present", lambda: pytest.fail("asked Windows off Windows")
    )
    monkeypatch.setattr(os.path, "exists", lambda node: node == "/dev/nvidiactl")

    assert hardware._nvidia_present() is True


@pytest.mark.unit
def test_a_machine_with_no_management_library_reports_no_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent driver is an ordinary answer, not a start-up failure: the ladder's software path
    is behind it either way."""
    import ctypes

    def _refuse(name: str) -> None:
        raise OSError(f"cannot open {name}")

    monkeypatch.setattr(ctypes, "CDLL", _refuse)

    assert hardware._windows_nvidia_present() is False


@pytest.mark.unit
def test_total_ram_on_the_posix_branch_is_pages_times_page_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The arm that produces a number rather than the one that gives up.

    `sysconf` is absent from `os` on Windows, so on the site Sift ships on this arm is
    unreachable without standing it in, and what is being checked is the arithmetic, which is the
    same everywhere.
    """
    monkeypatch.setattr(hardware, "_WINDOWS", False)
    monkeypatch.setattr(
        os, "sysconf", lambda name: 4096 if name == "SC_PAGE_SIZE" else 1_000, raising=False
    )

    assert hardware._total_ram_bytes() == 4096 * 1_000


@pytest.mark.unit
def test_windows_memory_that_the_call_refuses_to_fill_in_is_no_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GlobalMemoryStatusEx` answering no leaves the structure untouched, so reading it anyway
    would report whatever it was initialised to: a number, and a wrong one."""

    class Kernel32:
        def GlobalMemoryStatusEx(self, *_args: object) -> int:
            return 0

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: Kernel32(), raising=False)

    assert hardware._windows_total_memory() is None


@pytest.mark.unit
def test_a_card_library_that_will_not_start_is_no_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """`nvmlInit_v2` answering non-zero is a driver that is present and not working, which is not
    the same as no card, and is the same answer here: nothing Sift can drive."""

    class Nvml:
        def nvmlInit_v2(self) -> int:
            return 999

    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_kw: Nvml(), raising=False)

    assert hardware._windows_nvidia_present() is False


@pytest.mark.unit
def test_a_memory_question_that_raises_is_no_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The library is not there, or the call refuses the structure it was handed. Same answer, and
    it has to be an answer: the worker sizing and the diagnostics both read this."""
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no kernel32 here")),
        raising=False,
    )

    assert hardware._windows_total_memory() is None


@pytest.mark.unit
def test_a_card_library_that_will_not_count_its_devices_is_no_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Started, and then would not say how many cards there are. Nothing Sift can drive."""

    class Nvml:
        def nvmlInit_v2(self) -> int:
            return 0

        def nvmlDeviceGetCount_v2(self, *_args: object) -> int:
            return 999

        def nvmlShutdown(self) -> int:
            return 0

    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_kw: Nvml(), raising=False)

    assert hardware._windows_nvidia_present() is False


# --- what the machine has, against what it can address ------------------------------------------
#
# Two different numbers for two different questions. Showing only the second would put a figure a
# little under the installed memory on a screen beside a Windows dialog stating the installed
# memory, which reads as Sift being unable to count.


def test_a_site_with_no_firmware_table_to_read_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Answering the addressable total again would be a second copy of the field beside it, dressed
    up as an answer to a question it does not answer."""
    monkeypatch.setattr(hardware, "_WINDOWS", False)

    assert hardware._installed_ram_bytes() is None


def test_a_firmware_table_that_will_not_be_read_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The call is there and answers false, which is what it does when the caller lacks the right
    to ask. Zero would then be read out of a buffer nothing wrote to, and zero installed bytes is a
    figure no machine has."""
    monkeypatch.setattr(hardware, "_WINDOWS", True)

    class _Refuses:
        def GetPhysicallyInstalledSystemMemory(self, _buffer: object) -> int:
            return 0

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: _Refuses(), raising=False)

    assert hardware._installed_ram_bytes() is None


def test_a_site_that_has_no_such_call_says_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not hypothetical: the entry point is a Windows one, and asking a library that does not
    export it raises rather than answering. A description of a machine with one field missing is
    worth more than one that stops loading."""
    monkeypatch.setattr(hardware, "_WINDOWS", True)

    def _explode(*_a: object, **_k: object) -> object:
        raise OSError("no such library")

    monkeypatch.setattr(ctypes, "WinDLL", _explode, raising=False)

    assert hardware._installed_ram_bytes() is None


@pytest.mark.skipif(os.name != "nt", reason="reads a Windows firmware table")
def test_the_installed_total_is_never_less_than_what_can_be_addressed() -> None:
    """The relationship, which is the whole reason both are kept: what the firmware and the
    hardware reserve comes OFF the installed total, so the addressable figure is the smaller one.

    Asserted as a relationship rather than a number, because the number is this machine's."""
    installed = hardware._installed_ram_bytes()
    usable = hardware._total_ram_bytes()

    assert installed is not None
    assert usable is not None
    assert installed >= usable
    # And a sane figure: memory comes in powers of two, so an installed total is a whole number of
    # gibibytes. A reading in the wrong unit (kilobytes taken for bytes) would not be.
    assert installed % (1024**3) == 0


# --- what the driver's tool says about the card -------------------------------------------------
#
# Parsed field by field rather than all-or-nothing: a driver that names the card but will not report
# its memory should still put the card's name on the screen. And the memory is reported in
# MEBIBYTES, which is the one thing here easy to get wrong by a factor of a million.


def test_the_card_line_is_read_into_all_three_fields() -> None:
    card = hardware._card_from("NVIDIA Some Card, 500.10, 12282")

    assert card.name == "NVIDIA Some Card"
    assert card.driver == "500.10"
    # Mebibytes, so 12282 is just under 12 GiB, not 12282 bytes and not 12.3 GB.
    assert card.vram_bytes == 12282 * 1024 * 1024


def test_a_driver_that_will_not_report_the_memory_still_names_the_card() -> None:
    """Older drivers answer `[N/A]`, which is why this is parsed rather than trusted."""
    card = hardware._card_from("NVIDIA GeForce GTX 1060, 470.05, [N/A]")

    assert card.name == "NVIDIA GeForce GTX 1060"
    assert card.driver == "470.05"
    assert card.vram_bytes is None


def test_a_line_with_fewer_fields_than_expected_answers_what_it_has() -> None:
    """A tool asked for three things does not always answer three."""
    card = hardware._card_from("NVIDIA Some Card")

    assert card.name == "NVIDIA Some Card"
    assert card.driver is None
    assert card.vram_bytes is None


def test_a_card_reporting_no_memory_at_all_is_not_reported_as_having_none() -> None:
    """Zero is not an answer about a graphics card; it is the absence of one, and a row saying
    "0 GB" is worse than no row."""
    assert hardware._card_from("A card, 1.0, 0").vram_bytes is None


def test_every_card_in_the_machine_is_reported() -> None:
    """A machine can have more than one, and naming only the first is a description of somebody's
    computer that is quietly wrong. The FIRST is still the one that does the work: it is the device
    CUDA uses unless it is told otherwise."""
    printed = b"Card A, 590.1, 12282\nCard B, 590.1, 8192\n"

    report = HardwareReport(
        cpu_count=1,
        total_ram_bytes=None,
        worker_concurrency=1,
        cuda=True,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
        gpu_cards=(
            hardware._card_from(printed.decode().splitlines()[0]),
            hardware._card_from(printed.decode().splitlines()[1]),
        ),
    )

    assert len(report.gpu_cards) == 2
    assert report.gpu_name == "Card A"
    assert report.gpu_vram_bytes == 12282 * 1024 * 1024


def test_a_machine_with_no_card_names_none() -> None:
    """The properties read `[0]`, so the empty case is the one that would raise."""
    report = HardwareReport(
        cpu_count=1,
        total_ram_bytes=None,
        worker_concurrency=1,
        cuda=False,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )

    assert report.gpu_name is None
    assert report.gpu_vram_bytes is None


# --- every adapter in the machine, whoever made it ----------------------------------------------
#
# NVIDIA's own tool knows about NVIDIA cards and nothing else, so on its own it would leave a
# Radeon out of the hardware report. A chip built into the processor beside a card in a
# slot is the ordinary shape of a machine, and both belong in a description of one.


@pytest.mark.unit
async def test_a_site_with_no_such_listing_reports_no_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The query is a Windows one. Everywhere else the answer is an empty list rather than a
    failure: the report is still a report, with the card NVIDIA's own tool found in it."""
    monkeypatch.setattr(hardware, "_WINDOWS", False)

    assert await hardware._windows_display_adapters() == []


@pytest.mark.unit
async def test_a_listing_that_cannot_be_run_reports_no_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine where that shell is blocked or missing. It is one field of the report, taken once
    at boot, and boot is not the moment to fail over a description."""
    monkeypatch.setattr(hardware, "_WINDOWS", True)

    async def _refuse(*_args: object, **_kwargs: object) -> SubprocessResult:
        raise SubprocessError("powershell.exe is not on PATH")

    monkeypatch.setattr(hardware, "run_tool", _refuse)

    assert await hardware._windows_display_adapters() == []


@pytest.mark.unit
async def test_a_listing_that_fails_reports_no_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """It prints on the way down, and read without looking at the exit status first that sentence
    becomes the name of a card. The same trap as the driver tool next door."""
    monkeypatch.setattr(hardware, "_WINDOWS", True)
    monkeypatch.setattr(
        hardware,
        "run_tool",
        _answers(
            SubprocessResult(
                returncode=1,
                stdout=b"Get-CimInstance : Access is denied.|||\n",
                stderr=b"",
            )
        ),
    )

    assert await hardware._windows_display_adapters() == []


@pytest.mark.unit
async def test_what_the_listing_prints_is_read_into_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    """The happy path, because the three above all answer an empty list and so does a function that
    never reaches its parser."""
    monkeypatch.setattr(hardware, "_WINDOWS", True)
    monkeypatch.setattr(
        hardware,
        "run_tool",
        _answers(
            SubprocessResult(
                returncode=0,
                stdout=b"AMD Some Card XT|32.0.1|17179869184|4293918720\n",
                stderr=b"",
            )
        ),
    )

    assert await hardware._windows_display_adapters() == [
        hardware.Card(
            name="AMD Some Card XT",
            driver="32.0.1",
            vram_bytes=17179869184,
            can_compute=False,
        )
    ]


def test_an_amd_adapter_is_read_out_of_what_windows_lists() -> None:
    found = hardware._adapters_from(
        ["AMD Radeon(TM) Graphics|32.0.10000.1000|2147483648|2147483648"]
    )

    assert found == [
        hardware.Card(
            name="AMD Radeon(TM) Graphics",
            driver="32.0.10000.1000",
            vram_bytes=2147483648,
            # Only an NVIDIA card can have a model put on it today, and saying otherwise here would
            # be the screen offering a choice that cannot work.
            can_compute=False,
        )
    ]


def test_what_the_driver_recorded_beats_the_field_that_stops_counting() -> None:
    """THE ONE THIS EXISTS FOR. `AdapterRAM` is 32 bits, so every card with 4 GB or more reports the
    same 4293918720, so a 16 GB card would come out with no memory beside it at all. The
    registry figure is 64 bits and is what the card really has."""
    found = hardware._adapters_from(["AMD Some Card XT|32.0.1|17179869184|4293918720"])

    assert found[0].vram_bytes == 17179869184


def test_the_capped_field_is_the_fallback_and_only_below_the_cap() -> None:
    """A driver that recorded nothing still has a figure worth showing, up to the point where that
    figure means "four gigabytes or more" and nothing else."""
    fallback = hardware._adapters_from(["An old card|1.0||2147483648"])
    assert fallback[0].vram_bytes == 2147483648

    capped = hardware._adapters_from(["A big card|1.0||4293918720"])
    assert capped[0].vram_bytes is None


def test_an_adapter_that_is_not_hardware_is_left_out() -> None:
    """Every machine that has ever had a remote session lists one, and it is not a card."""
    found = hardware._adapters_from(
        [
            "Microsoft Remote Display Adapter|10.0.20000.1000||",
            "Microsoft Basic Display Adapter|10.0.20000.1||",
            "AMD Radeon(TM) Graphics|1.0|2147483648|2147483648",
        ]
    )

    assert [one.name for one in found] == ["AMD Radeon(TM) Graphics"]


def test_the_card_that_does_the_work_is_not_whichever_comes_first() -> None:
    """Windows enumerates adapters in its own order, so position says nothing about which one can
    run a model. Picking `[0]` names the built-in chip on a machine that lists it first."""
    report = HardwareReport(
        cpu_count=1,
        total_ram_bytes=None,
        worker_concurrency=1,
        cuda=True,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
        gpu_cards=(
            hardware.Card(name="AMD Radeon(TM) Graphics", vram_bytes=2147483648),
            hardware.Card(name="Some Card", vram_bytes=12884901888, can_compute=True),
        ),
    )

    assert report.gpu_name == "Some Card"
    assert report.gpu_vram_bytes == 12884901888


def test_a_machine_whose_only_adapter_cannot_run_a_model_names_none() -> None:
    """A description of the machine still lists it; the question "which card runs Smart Search" has
    no answer, and answering it anyway is how a screen offers a choice that cannot work."""
    report = HardwareReport(
        cpu_count=1,
        total_ram_bytes=None,
        worker_concurrency=1,
        cuda=False,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
        gpu_cards=(hardware.Card(name="AMD Radeon(TM) Graphics"),),
    )

    assert report.gpu_cards
    assert report.gpu_name is None


def test_the_machine_name_is_the_first_label_and_not_the_whole_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine on a domain answers with the fully qualified name. The row this feeds says which
    computer the disk is in, and the domain after the first dot is address rather than name."""
    monkeypatch.setattr(socket, "gethostname", lambda: "attic.lan.example")

    assert hardware.machine_name() == "attic"


@pytest.mark.parametrize("answer", ["", "   ", "localhost", "LOCALHOST", "localhost.localdomain"])
def test_a_name_that_says_nothing_is_no_name_at_all(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    """`localhost` is true of every machine, so it distinguishes none of them, and an empty answer
    names nothing. The screen falls back to the sentence it drew before the name existed."""
    monkeypatch.setattr(socket, "gethostname", lambda: answer)

    assert hardware.machine_name() is None


def test_a_container_id_is_not_a_name_anybody_can_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container takes twelve hex characters as its hostname and they differ on every run, so the
    row would name a different computer each restart of the same install."""
    monkeypatch.setattr(socket, "gethostname", lambda: "3F2A9C1B4D7E")

    assert hardware.machine_name() is None


def test_twelve_characters_that_are_not_hex_are_a_real_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The container test is length AND alphabet together. Dropping either takes an ordinary
    twelve-letter name with it, which is the direction that loses information."""
    monkeypatch.setattr(socket, "gethostname", lambda: "frontdeskpcs")

    assert hardware.machine_name() == "frontdeskpcs"


def test_a_machine_that_cannot_say_its_own_name_answers_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking the operating system can fail. The library list is not worth an error page, so the
    caller is told there is no name and draws the sentence that needs none."""

    def refuse() -> str:
        raise OSError("no name")

    monkeypatch.setattr(socket, "gethostname", refuse)

    assert hardware.machine_name() is None


# --- naming this shape of machine, twice, for two different readers ------------------------------


def _report(**over: object) -> HardwareReport:
    base = {
        "cpu_count": 16,
        "total_ram_bytes": None,
        "worker_concurrency": 8,
        "cuda": True,
        "rocm": False,
        "transcode_encoders": ("h264_nvenc",),
        "warnings": (),
    }
    return HardwareReport(**{**base, **over})  # type: ignore[arg-type]


@pytest.mark.unit
def test_the_profile_changes_only_with_what_decides_how_fast_this_machine_is() -> None:
    """A machine with a card removed is a different machine for anything measuring speed, and a
    machine whose driver started reporting its card differently is NOT.

    So the digest covers the cores, the workers, the compute device and the usable encoders, and
    deliberately nothing a routine driver update moves: included, those would make every past run
    belong to a machine that no longer exists.
    """
    plain = _report()

    # A driver update: new name, new driver string, a warning, a different make and model.
    dressed = _report(
        warnings=("a new note",),
        cpu_model="Some Registry Processor",
        gpu_cards=(Card(name="Card A", driver="9.9.9", vram_bytes=1),),
    )
    assert dressed.profile == plain.profile

    # And the four that DO decide speed, one at a time.
    assert _report(cpu_count=8).profile != plain.profile
    assert _report(worker_concurrency=2).profile != plain.profile
    assert _report(cuda=False).profile != plain.profile
    assert _report(transcode_encoders=()).profile != plain.profile


@pytest.mark.unit
def test_the_label_is_for_a_person_and_says_only_what_it_knows() -> None:
    """A label and a digest rather than one string doing both: a history keyed on this would split
    in two on the day a driver started spelling a card differently."""
    named = _report(cpu_model="Some Registry Processor", gpu_cards=(Card(name="Card A"),))
    assert named.profile_label == "Some Registry Processor - 16 threads - Card A"

    # A probe that could not name a part leaves it out rather than saying "Unknown".
    assert _report().profile_label == "16 threads"
    assert _report(gpu_cards=(Card(name=None),)).profile_label == "16 threads"
