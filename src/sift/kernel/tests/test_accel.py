# SPDX-License-Identifier: AGPL-3.0-or-later
"""Installing the graphics-card runtime, without downloading anything or touching a card: nothing is
unpacked until every package is proved intact, nothing reads installed until all of it is there,
and a package this cannot install is refused rather than half-installed."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import subprocess
import sys
import zipfile
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from sift.kernel.config import Settings
from sift.kernel.fetch import FetchFailed, Untrusted
from sift.kernel.ml import accel
from sift.kernel.ml import child as ml_child
from sift.kernel.ml.accel import AccelError, Wheel
from sift.kernel.ml.child import DeviceQuestion
from sift.kernel.ml.runtime import DeviceUnavailable
from sift.kernel.subprocess import SubprocessError


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


def a_wheel(name: str, members: dict[str, bytes]) -> tuple[Wheel, bytes]:
    """A real zip, because what is being tested is what unpacking one does."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for member, body in members.items():
            bundle.writestr(member, body)
    payload = buffer.getvalue()
    wheel = Wheel(
        name, "1.0", f"https://example.invalid/{name}.whl", _sha256(payload), len(payload)
    )
    return wheel, payload


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.headers = {"Content-Length": str(len(body))}
        self.content = self

    async def iter_chunked(self, size: int) -> Iterator[bytes]:  # type: ignore[misc]
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeIndex:
    """Answers each package's own address with that package's bytes."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies
        self.asked: list[str] = []

    def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self.asked.append(url)
        return FakeResponse(200, self.bodies[url])

    async def __aenter__(self) -> FakeIndex:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


# --- what is pinned ------------------------------------------------------------------------------


def test_the_pin_names_the_runtime_version_that_is_actually_shipped() -> None:
    """The two builds of the runtime are the same library and only one can be imported.

    A set pinned against a version Sift no longer carries does not warn: it loads, and dies inside a
    C library at the first model. So the version at the front of the pin is checked against the
    package it is a pin for, here, where a mismatch is a failing test rather than a crash on
    somebody's machine.
    """
    runtime = next(wheel for wheel in accel.WHEELS if wheel.name == "onnxruntime-gpu")
    assert accel.PIN.startswith(f"{runtime.version}-")


def test_every_pinned_package_carries_a_digest_and_a_size() -> None:
    """A package with no digest is a package nothing can refuse, and the size is what a screen shows
    somebody BEFORE they agree to a download this large."""
    for wheel in accel.WHEELS:
        assert len(wheel.digest) == 64, wheel.name
        assert wheel.size_bytes > 0, wheel.name
        assert wheel.url.startswith("https://"), wheel.name
    assert sum(wheel.size_bytes for wheel in accel.WHEELS) == accel.TOTAL_BYTES


def test_nothing_pinned_here_is_already_inside_sift() -> None:
    """Fetching a second copy of something Sift already has puts two of it on one path.

    The runtime's other dependencies (numpy and the rest) are already installed, because the
    processor build depends on every one of them. Two numpys on one path is a failure with no useful
    message at either end, so this set is deliberately only the runtime and the card's own
    libraries.
    """
    for wheel in accel.WHEELS:
        assert wheel.name == "onnxruntime-gpu" or wheel.name.startswith("nvidia-"), wheel.name


# --- installing ----------------------------------------------------------------------------------


def _recording(seen: list[tuple[int, int]]) -> Callable[[int, int], bool]:
    """Record every progress report and never ask for the transfer to stop."""

    def watch(received: int, total: int) -> bool:
        seen.append((received, total))
        return True

    return watch


async def test_nothing_reads_as_installed_until_all_of_it_is_there(settings: Settings) -> None:
    assert not accel.installed(settings)
    accel.directory(settings).mkdir(parents=True)
    (accel.directory(settings) / "onnxruntime").mkdir()
    # A folder with something in it is not an installation. Only the marker says so, and the marker
    # is written last.
    assert not accel.installed(settings)


async def test_a_package_that_did_not_arrive_intact_installs_nothing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this is really about is the quiet one: six correct libraries and a seventh that
    is three bytes short, which does not fail until a card is asked to do something."""
    good, good_body = a_wheel("nvidia-cublas", {"nvidia/cublas/bin/x.dll": b"real"})
    bad, bad_body = a_wheel("nvidia-curand", {"nvidia/curand/bin/y.dll": b"real"})
    damaged = Wheel(bad.name, bad.version, bad.url, _sha256(b"something else"), len(bad_body))
    monkeypatch.setattr(accel, "WHEELS", (good, damaged))
    monkeypatch.setattr(accel, "TOTAL_BYTES", good.size_bytes + damaged.size_bytes)
    index = FakeIndex({good.url: good_body, damaged.url: bad_body})

    with pytest.raises(AccelError, match="didn't arrive intact"):
        await accel.install(settings, session_factory=lambda: index)

    assert not accel.installed(settings)
    assert not (accel.directory(settings) / "nvidia" / "cublas").exists()


async def test_a_whole_set_is_fetched_unpacked_and_marked(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    one, one_body = a_wheel("onnxruntime-gpu", {"onnxruntime/capi/ort.dll": b"runtime"})
    two, two_body = a_wheel("nvidia-cublas", {"nvidia/cublas/bin/cublas.dll": b"library"})
    monkeypatch.setattr(accel, "WHEELS", (one, two))
    monkeypatch.setattr(accel, "TOTAL_BYTES", one.size_bytes + two.size_bytes)
    seen: list[tuple[int, int]] = []

    finished = await accel.install(
        settings,
        # A named callback rather than a lambda around `append`: it returns None, so any
        # expression built on its value is a type error however it is spelled.
        progress=_recording(seen),
        session_factory=lambda: FakeIndex({one.url: one_body, two.url: two_body}),
    )

    assert finished
    assert accel.installed(settings)
    root = accel.directory(settings)
    assert (root / "onnxruntime" / "capi" / "ort.dll").read_bytes() == b"runtime"
    assert (root / "nvidia" / "cublas" / "bin" / "cublas.dll").read_bytes() == b"library"
    # The half-downloaded packages are not left behind: they are the size of the download again.
    assert not (root / ".parts").exists()
    # And somebody watched it happen, against a total that was known before it started.
    assert seen and seen[-1] == (one.size_bytes + two.size_bytes, one.size_bytes + two.size_bytes)


async def test_stopping_keeps_what_arrived_and_installs_nothing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopping a gigabyte-long download has to leave it resumable. The partly-downloaded packages
    stay exactly where the next attempt will look for them."""
    one, one_body = a_wheel("onnxruntime-gpu", {"onnxruntime/capi/ort.dll": b"runtime"})
    monkeypatch.setattr(accel, "WHEELS", (one,))
    monkeypatch.setattr(accel, "TOTAL_BYTES", one.size_bytes)

    finished = await accel.install(
        settings,
        progress=lambda *_: False,
        session_factory=lambda: FakeIndex({one.url: one_body}),
    )

    assert not finished
    assert not accel.installed(settings)
    assert (accel.directory(settings) / ".parts").is_dir()


async def test_a_second_attempt_does_not_download_what_is_already_verified(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    one, one_body = a_wheel("onnxruntime-gpu", {"onnxruntime/capi/ort.dll": b"runtime"})
    monkeypatch.setattr(accel, "WHEELS", (one,))
    monkeypatch.setattr(accel, "TOTAL_BYTES", one.size_bytes)
    parts = accel.directory(settings) / ".parts"
    parts.mkdir(parents=True)
    (parts / f"{one.name}-{one.version}.whl").write_bytes(one_body)
    index = FakeIndex({})

    assert await accel.install(settings, session_factory=lambda: index)
    assert index.asked == [], "a package already on disk and verified was downloaded again"


# --- what unpacking refuses ----------------------------------------------------------------------


def test_a_package_that_needs_a_real_installer_is_refused(tmp_path: Path) -> None:
    """A `.data` directory means files that have to be sorted into several destinations. Unzipping
    one of those puts libraries where nothing looks for them and reports success."""
    wheel, body = a_wheel("nvidia-cublas", {"nvidia_cublas-1.0.data/scripts/thing.exe": b"x"})
    archive = tmp_path / "w.whl"
    archive.write_bytes(body)

    with pytest.raises(AccelError, match="package installer"):
        accel._unpack(tmp_path / "root", wheel, archive)


@pytest.mark.parametrize(
    "name", ["../../escaped.dll", "C:/escaped.dll", "C:escaped.dll", "a\\..\\..\\escaped.dll"]
)
def test_a_package_naming_a_file_outside_the_folder_is_refused(tmp_path: Path, name: str) -> None:
    """The set is pinned by digest, so a member like this means the file is not what was pinned,
    which is a reason to stop, not a reason to quietly rename it. A drive and the other system's
    separator as well as `..`, on either system, and nothing is written."""
    wheel, body = a_wheel("nvidia-cublas", {name: b"x"})
    archive = tmp_path / "w.whl"
    archive.write_bytes(body)

    with pytest.raises(AccelError, match="outside the folder"):
        accel._unpack(tmp_path / "root", wheel, archive)
    assert not (tmp_path / "root").exists() or not any((tmp_path / "root").rglob("*"))


def test_a_package_reaching_out_through_a_link_already_in_the_folder_is_refused(
    tmp_path: Path,
) -> None:
    """A name that is clean on its face (no drive, no `..`) can still land outside the folder
    when a directory it passes through is a link. Every member's real place is proved before
    anything is unpacked, so the file never arrives where the link points. A junction on Windows,
    where it needs no privilege; a symbolic link anywhere else."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "nvidia"
    if sys.platform == "win32":
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert made.returncode == 0, made.stderr
    else:
        os.symlink(outside, link)
    wheel, body = a_wheel("nvidia-cublas", {"nvidia/escaped.dll": b"x"})
    archive = tmp_path / "w.whl"
    archive.write_bytes(body)

    with pytest.raises(AccelError, match="outside the folder"):
        accel._unpack(root, wheel, archive)
    assert list(outside.iterdir()) == []


# --- switching it on -----------------------------------------------------------------------------


def test_nothing_is_put_on_the_path_when_there_is_no_installation(settings: Settings) -> None:
    before = list(sys.path)
    assert accel.enable(settings) is False
    assert sys.path == before


def test_the_installed_runtime_goes_in_FRONT_of_the_one_that_ships(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sift's own processor build sits beside the application and would otherwise be found first.
    "Found first" is the entire question: only one build of the library can be imported."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    root = accel.directory(settings)
    (root / "nvidia" / "cublas" / "bin").mkdir(parents=True)
    (root / accel._MARKER).write_text("{}", encoding="utf-8")

    assert accel.enable(settings) is True
    assert sys.path[0] == str(root)

    # And twice is the same as once: it is called before every import of the runtime.
    accel.enable(settings)
    assert sys.path.count(str(root)) == 1


def test_a_library_folder_is_handed_to_windows_once_however_often_this_runs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`add_dll_directory` is asked before it adds a folder again: its handles are kept for good,
    and
    Windows' bounded list overflows into `[WinError 206] The filename or extension is too long`."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(accel, "_dll_directories", [])
    monkeypatch.setattr(accel, "_dll_added", set())
    monkeypatch.setattr(accel, "_CAN_ADD_DLL_DIRECTORY", True)
    handed: list[str] = []

    def remember(where: str) -> object:
        handed.append(where)
        return object()

    monkeypatch.setattr(os, "add_dll_directory", remember, raising=False)

    root = accel.directory(settings)
    folder = root / "nvidia" / "cu13" / "bin" / "x86_64"
    folder.mkdir(parents=True)
    (folder / "cublas64_13.dll").write_bytes(b"not really a library")
    (root / accel._MARKER).write_text("{}", encoding="utf-8")

    accel.enable(settings)
    accel.enable(settings)
    accel.enable(settings)

    assert handed == [str(folder)]


async def test_the_card_is_not_claimed_to_work_when_nothing_is_installed(
    settings: Settings,
) -> None:
    assert await accel.works(settings) == "The graphics-card runtime is not installed."


def test_a_machine_whose_runtime_already_drives_a_card_is_offered_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runtime somebody installed themselves is asked about before anything is downloaded over
    it."""
    monkeypatch.setattr(ml_child, "DEVICES", DeviceQuestion(lambda _: ("CUDAExecutionProvider",)))

    assert accel.already_capable() is True


def test_a_machine_whose_runtime_drives_nothing_is_offered_the_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of it, and the one every shipped installation is on."""
    monkeypatch.setattr(ml_child, "DEVICES", DeviceQuestion(lambda _: ("CPUExecutionProvider",)))

    assert accel.already_capable() is False


def test_a_runtime_that_cannot_start_drives_no_card(monkeypatch: pytest.MonkeyPatch) -> None:
    def crashed(_settings: Settings) -> tuple[str, ...]:
        raise DeviceUnavailable("it stopped with code 0xC0000005")

    monkeypatch.setattr(ml_child, "DEVICES", DeviceQuestion(crashed))

    assert accel.already_capable() is False


async def test_a_package_that_cannot_be_downloaded_is_reported_as_one(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transfer that fails is an installation problem, not a stack trace.

    Every caller of this catches `AccelError`; a `FetchFailed` arriving instead would take down the
    job rather than putting a sentence on the screen that started it.
    """
    one, _body = a_wheel("nvidia-cublas", {"nvidia/cublas/bin/cublas.dll": b"library"})
    monkeypatch.setattr(accel, "WHEELS", (one,))

    async def refuse(*_args: object, **_kwargs: object) -> bool:
        raise FetchFailed("the server said 503")

    monkeypatch.setattr(accel, "fetch_resumable", refuse)

    with pytest.raises(accel.AccelError, match="503"):
        await accel.install(settings)


def test_an_installation_with_no_library_folders_still_switches_on(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path is what decides which build is imported; the DLL directories are extra.

    A set whose packages carry no `nvidia/*/bin` at all (which is what a future pin of
    Python-only wheels would be) must still put the folder in front rather than reporting that
    nothing was switched on.
    """
    monkeypatch.setattr(sys, "path", list(sys.path))
    root = accel.directory(settings)
    root.mkdir(parents=True, exist_ok=True)
    (root / accel._MARKER).write_text("{}", encoding="utf-8")

    assert accel.enable(settings) is True
    assert sys.path[0] == str(root)


async def test_saying_stop_between_packages_installs_nothing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling is checked after each package as well as during one.

    Without it, stopping during the last few bytes of a package would still unpack the whole set,
    which is the one thing somebody pressing stop is asking not to happen.
    """
    one, _one_body = a_wheel("nvidia-cublas", {"nvidia/cublas/bin/cublas.dll": b"library"})
    two, _two_body = a_wheel("nvidia-cudnn", {"nvidia/cudnn/bin/cudnn.dll": b"library"})
    monkeypatch.setattr(accel, "WHEELS", (one, two))

    # The transfer itself answers yes and reports nothing, so the only progress call is the one
    # made after a whole package has landed, which is the check this test is about. Left to the
    # real fetch, its own last report carries the same number and stops the run one line earlier.
    async def arrives(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(accel, "fetch_resumable", arrives)

    # Absent before the transfer and intact after it, which is what `install` asks twice about.
    asked: set[str] = set()

    def matches(path: Path, _digest: str) -> bool:
        seen = str(path) in asked
        asked.add(str(path))
        return seen

    monkeypatch.setattr(accel, "_matches", matches)

    finished = await accel.install(
        settings,
        # False the moment a whole package is done.
        progress=lambda received, _total: received != one.size_bytes,
    )

    assert finished is False
    assert not accel.installed(settings)


def test_removing_it_takes_the_whole_folder(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gigabyte somebody may want back is a gigabyte they can see and remove, and the card is no
    longer offered."""
    offered = iter([("CUDAExecutionProvider",), ("CPUExecutionProvider",)])
    monkeypatch.setattr(ml_child, "DEVICES", DeviceQuestion(lambda _: next(offered)))
    assert accel.already_capable() is True
    root = accel.directory(settings)
    (root / "nvidia" / "cublas" / "bin").mkdir(parents=True)
    (root / accel._MARKER).write_text("{}", encoding="utf-8")
    assert accel.installed(settings)

    accel.remove(settings)

    assert not root.exists()
    assert not accel.installed(settings)
    assert accel.already_capable() is False
    # And asking twice is not an error: the button is there whether or not anything is installed.
    accel.remove(settings)


async def test_a_check_that_will_not_run_is_reported_rather_than_raised(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A driver that has stopped responding is the commonest reason this cannot answer, and the
    sentence says what clears it. Raising instead would leave the screen with a stack trace."""
    _installed(settings)

    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise SubprocessError("it never answered")

    monkeypatch.setattr(accel, "run_once", refuse)

    said = await accel.works(settings)

    assert said is not None
    assert "restarting the computer" in said
    assert "it never answered" in said


async def test_a_proof_that_cannot_even_start_is_stopped_by_the_same_limit(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting waits on a pool an import can fill, so the limit covers the start as well."""
    _installed(settings)

    async def stuck(*_args: object, **_kwargs: object) -> object:
        await asyncio.sleep(5)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(accel, "run_once", stuck)
    monkeypatch.setattr(accel, "_PROOF_TIMEOUT", 0.05)

    said = await accel.works(settings)

    assert said is not None
    assert said.endswith("(it did not answer in time)")


async def test_a_card_that_runs_the_model_is_proved_rather_than_assumed(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """None means it really ran something, in a process of its own."""
    _installed(settings)
    monkeypatch.setattr(accel, "run_once", _answers(0, b"", b""))

    assert await accel.works(settings) is None


async def test_a_card_that_refuses_the_model_hands_back_what_it_said(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The LAST line, because a failing runtime writes pages of its own diagnostics first and the
    sentence a person needs is at the end of them."""
    _installed(settings)
    monkeypatch.setattr(
        accel, "run_once", _answers(1, b"", b"pages of noise\nthe card was refused\n")
    )

    said = await accel.works(settings)

    assert said is not None
    assert said.endswith("the card was refused")


async def test_a_check_that_fails_saying_nothing_at_all_still_says_something(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool that fails silently would otherwise produce a sentence ending in nothing."""
    _installed(settings)
    monkeypatch.setattr(accel, "run_once", _answers(1, b"", b""))

    said = await accel.works(settings)

    assert said is not None
    assert said.endswith("nothing at all")


def _installed(settings: Settings) -> None:
    """The marker that makes `installed` true, without a download."""
    root = accel.directory(settings)
    root.mkdir(parents=True, exist_ok=True)
    (root / accel._MARKER).write_text("{}", encoding="utf-8")


def _answers(code: int, out: bytes, err: bytes) -> Callable[..., Awaitable[object]]:
    """A stand-in for the proof subprocess, answering exactly this."""

    async def run(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)

    return run


def test_a_site_with_no_library_search_path_to_widen_still_switches_on(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The packages here are Windows builds, and widening the search path is a Windows mechanism.

    On anything else there is nothing to widen and the path alone decides what gets imported, so
    the answer is still yes. Driven through the flag rather than skipped, because whichever machine
    this runs on has one of the two arms unreachable.
    """
    monkeypatch.setattr(accel, "_CAN_ADD_DLL_DIRECTORY", False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    root = accel.directory(settings)
    (root / "nvidia" / "cublas" / "bin").mkdir(parents=True)
    (root / accel._MARKER).write_text("{}", encoding="utf-8")

    assert accel.enable(settings) is True
    assert sys.path[0] == str(root)


# --- the sentence the screen shows
#
# Built from ANOTHER PROGRAM'S bytes, decoded with `replace`, so an invalid byte would reach the
# screen as a box.


def test_the_sentence_is_the_last_line_with_anything_in_it() -> None:
    assert accel._last_sentence(["first", "the reason", "", "   "]) == "the reason"


def test_a_byte_that_did_not_decode_never_reaches_the_screen() -> None:
    """The box glyph, in the shape the console prints it."""
    assert (
        accel._last_sentence(["\ufffdthe card was refused and the processor was used instead"])
        == "the card was refused and the processor was used instead"
    )


def test_control_characters_go_the_same_way() -> None:
    assert accel._last_sentence(["a\x00b\x1bc"]) == "a b c"


def test_output_with_nothing_in_it_still_answers_a_sentence() -> None:
    """A refusal that names no reason is still a refusal, and the screen has to say something."""
    assert accel._last_sentence([]) == "nothing at all"
    assert accel._last_sentence(["", "\ufffd"]) == "nothing at all"


# --- what the screen is told when the card will not run
#
# One stream carrying two encodings and a colour code, and the line saying WHY is not the last.


def test_the_wide_half_of_the_output_is_read_too() -> None:
    """The runtime is a C++ library and writes wide characters; Python's own last words on the same
    stream are ordinary UTF-8. Decoded as either one alone, half of it is a smear."""
    wide = "Error loading cuda.dll".encode("utf-16-le")
    narrow = b"\nthe card was refused and the processor was used instead\n"

    assert accel._readable(wide + narrow) == [
        "Error loading cuda.dll",
        "the card was refused and the processor was used instead",
    ]


def test_the_reason_beats_the_summary() -> None:
    """The last line is what happened. The line naming a load failure is why."""
    said = [
        "Error loading onnxruntime_providers_cuda.dll which depends on cublasLt64_13.dll",
        "the card was refused and the processor was used instead",
    ]

    assert "cublasLt64_13.dll" in accel._last_sentence(said)


def test_the_summary_is_still_the_answer_when_nothing_says_why() -> None:
    assert (
        accel._last_sentence(["something happened", "the card was refused"])
        == "the card was refused"
    )


def test_a_colour_code_is_not_part_of_the_sentence() -> None:
    """A terminal would have eaten it. The escape is a control character and is already gone; what
    is left is the `[1;31m` that followed it, in the middle of the words."""
    assert accel._last_sentence(["\x1b[1;31mError loading cuda.dll\x1b[m"]) == (
        "Error loading cuda.dll"
    )


def test_the_folders_that_hold_libraries_are_found_by_looking(tmp_path: Path) -> None:
    """The runtime's folders are found by walking: the CUDA 13 wheels put their libraries a level
    deeper than `nvidia/*/bin`."""
    deep = tmp_path / "nvidia" / "cu13" / "bin" / "x86_64"
    deep.mkdir(parents=True)
    (deep / "cublasLt64_13.dll").write_bytes(b"")
    beside = tmp_path / "nvidia" / "cudnn" / "bin"
    beside.mkdir(parents=True)
    (beside / "cudnn64_9.dll").write_bytes(b"")
    (tmp_path / "nvidia" / "cu13" / "bin").joinpath("notes.txt").write_text("", encoding="utf-8")

    found = accel.library_directories(tmp_path)

    assert found == sorted([deep, beside])


def test_a_folder_with_no_libraries_in_it_is_not_offered(tmp_path: Path) -> None:
    """The exact shape that broke it: a `bin` holding only another folder."""
    (tmp_path / "nvidia" / "cu13" / "bin" / "x86_64").mkdir(parents=True)

    assert accel.library_directories(tmp_path) == []


def test_a_refused_certificate_fails_the_download_for_good() -> None:
    """The same answer on the next ask, so the worker spends no retries on it; any other failure
    keeps its three tries."""
    from sift.kernel.jobs.queue_rows import JobFailedPermanently

    refused = accel.refused_for_good(Untrusted("refused"))
    assert issubclass(refused, AccelError) and issubclass(refused, JobFailedPermanently)
    assert accel.refused_for_good(FetchFailed("dropped")) is AccelError
