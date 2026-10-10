# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three answers every feature that shells out to ffmpeg needs, and the ones nothing else runs.

The slices exercise most of this module; what is here is what they never reach: answers that depend
on hardware a machine may lack (`render_node`, with no `/dev/dri` or two cards), and a tool that
writes something other than the JSON it promised, reported as that rather than a parser trace.
"""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import json
import os
from pathlib import Path
from typing import Any, cast
from zipfile import BadZipFile

import pytest

from sift.kernel import media, media_share, media_sources, subprocess
from sift.kernel.archives import ArchiveRefused
from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore, Location, LocationStatus
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobFailedPermanently, held_for
from sift.kernel.jobs.failure_words import kind_of
from sift.kernel.jobs.retrying import ROOM_WAIT, WaitingForSpace
from sift.kernel.media import Encoder, FFmpegError, MissingAsset, render_node
from sift.testing.tools import stand_in_tool

pytestmark = pytest.mark.unit


def _report(**over: Any) -> HardwareReport:
    fields: dict[str, Any] = {
        "cpu_count": 4,
        "total_ram_bytes": 8 << 30,
        "worker_concurrency": 2,
        "cuda": False,
        "rocm": False,
        "transcode_encoders": (),
        "warnings": (),
    }
    fields.update(over)
    return HardwareReport(**fields)


# --- which encoder this machine gets


def test_a_machine_with_nothing_but_a_processor_gets_the_processor() -> None:
    """A machine with only a processor gets the processor, the answer for most machines."""
    assert media.choose_encoder(_report()) is Encoder.CPU


def test_an_encoder_the_machine_cannot_actually_run_is_not_chosen() -> None:
    """The report lists what WORKS, not what ffmpeg was compiled with: NVENC compiled in with no
    NVIDIA card fails at runtime."""
    assert media.choose_encoder(_report(transcode_encoders=())) is Encoder.CPU


def test_the_preference_order_is_the_one_the_module_declares() -> None:
    """The first in the declared order wins, a judgement about quality per watt."""
    everything = tuple(encoder.value for encoder in media.ENCODER_PREFERENCE)

    chosen = media.choose_encoder(_report(transcode_encoders=everything))

    assert chosen is media.ENCODER_PREFERENCE[0]


# --- where the decoding happens


def test_a_machine_with_an_nvidia_card_decodes_on_it() -> None:
    """A machine with an NVIDIA card decodes on it, which costs a preview far less processor
    time."""
    assert media.decode_flags(_report(cuda=True)) == ("-hwaccel", "cuda")


def test_a_machine_with_no_card_asks_for_nothing() -> None:
    """Empty, not a software name: `-hwaccel cuda` with no device fails the whole command."""
    assert media.decode_flags(_report(cuda=False)) == ()


def test_the_decoder_is_named_rather_than_left_to_ffmpeg_to_choose() -> None:
    """The decoder is named, never `auto`, which silently declines for AV1."""
    assert "auto" not in media.decode_flags(_report(cuda=True))


def test_the_hardware_encoder_and_the_hardware_decoder_are_separate_questions() -> None:
    """Encoding and decoding on a card are separate questions asked of the report."""
    report = _report(cuda=True, transcode_encoders=())

    assert media.choose_encoder(report) is Encoder.CPU
    assert media.decode_flags(report) == ("-hwaccel", "cuda")


# --- giving up on a card that will not do the work


def _card() -> HardwareReport:
    """A machine whose report says the card can encode and decode."""
    return _report(cuda=True, transcode_encoders=("h264_nvenc",))


def _fails(*, times: int) -> Any:
    """An attempt that refuses on the card and works on the processor, `times` times over."""
    used: list[Encoder] = []

    async def attempt(encoder: Encoder, _decode: tuple[str, ...]) -> str:
        used.append(encoder)
        if encoder is not Encoder.CPU and used.count(Encoder.CPU) < times:
            raise FFmpegError("the driver refused")
        return "rendered"

    attempt.used = used  # type: ignore[attr-defined]
    return attempt


async def test_the_card_is_used_while_it_works() -> None:
    accelerator = media.Accelerator(_card())
    seen: list[Encoder] = []

    async def attempt(encoder: Encoder, _decode: tuple[str, ...]) -> str:
        seen.append(encoder)
        return "rendered"

    for _ in range(10):
        assert await accelerator.run(attempt) == "rendered"

    assert seen == [Encoder.NVENC] * 10, "it stopped using a card that was working perfectly"
    assert accelerator.offers_hardware


async def test_a_card_that_keeps_refusing_is_given_up_on() -> None:
    """A card that keeps refusing is given up on, or a broken driver costs an attempt per file."""
    accelerator = media.Accelerator(_card(), patience=3)
    attempt = _fails(times=99)

    for _ in range(6):
        assert await accelerator.run(attempt) == "rendered"

    assert not accelerator.offers_hardware
    assert accelerator.encoder is Encoder.CPU
    assert accelerator.decode == ()
    # Three refused attempts, then it stopped asking.
    assert attempt.used.count(Encoder.NVENC) == 3


async def test_one_bad_file_among_good_ones_does_not_give_up_on_the_card() -> None:
    """Refusals count in a ROW and any success resets it, so a busy card or an awkward file among
    concurrent jobs never adds up."""
    accelerator = media.Accelerator(_card(), patience=3)
    turn = 0

    async def attempt(encoder: Encoder, _decode: tuple[str, ...]) -> str:
        if encoder is not Encoder.CPU and turn % 4 == 0:
            raise FFmpegError("that one file again")
        return "rendered"

    for turn in range(24):  # noqa: B007 (the closure above reads it)
        await accelerator.run(attempt)

    assert accelerator.offers_hardware, "six scattered refusals gave up on a working card"


async def test_a_file_NEITHER_path_can_render_is_not_blamed_on_the_card() -> None:
    """A file that fails on the card and again on the processor is the file's fault, not counted
    against the card."""
    accelerator = media.Accelerator(_card(), patience=2)

    async def attempt(_encoder: Encoder, _decode: tuple[str, ...]) -> str:
        raise FFmpegError("this file does not decode")

    for _ in range(8):
        with pytest.raises(FFmpegError):
            await accelerator.run(attempt)

    assert accelerator.offers_hardware, "a broken file was mistaken for a broken card"


async def test_a_hardware_path_that_cannot_even_be_BUILT_counts_as_a_refusal() -> None:
    """A hardware path that raises before ffmpeg starts (VAAPI with no render node) is a refusal."""
    accelerator = media.Accelerator(_card(), patience=1)

    async def attempt(encoder: Encoder, _decode: tuple[str, ...]) -> str:
        if encoder is not Encoder.CPU:
            raise ValueError("VAAPI needs a render node, and none was given")
        return "rendered"

    assert await accelerator.run(attempt) == "rendered"
    assert not accelerator.offers_hardware


async def test_a_frame_too_small_for_the_card_goes_to_the_processor_and_blames_nobody() -> None:
    """A frame under a card's minimum never reaches the card: the refusals would latch a working
    card off."""
    accelerator = media.Accelerator(_card(), patience=3)
    seen: list[Encoder] = []

    async def attempt(encoder: Encoder, decode: tuple[str, ...]) -> str:
        seen.append(encoder)
        if encoder is not Encoder.CPU:
            raise FFmpegError("Video width 16 not within range from 48 to 8192")
        assert decode == ()
        return "rendered"

    for frame in ((16, 16), (1920, 16), (media.CARD_SMALLEST_SIDE - 1, 1080)):
        assert await accelerator.run(attempt, frame=frame) == "rendered"

    assert seen == [Encoder.CPU] * 3
    assert accelerator.offers_hardware

    async def plays(encoder: Encoder, _decode: tuple[str, ...]) -> str:
        seen.append(encoder)
        return "rendered"

    seen.clear()
    sizes: tuple[tuple[int | None, int | None] | None, ...] = (
        (media.CARD_SMALLEST_SIDE, 1080),
        (None, None),
        None,
    )
    for size in sizes:
        await accelerator.run(plays, frame=size)
    assert seen == [Encoder.NVENC] * 3, "a frame of a size the card takes, or of no known size"


async def test_a_machine_with_no_card_never_attempts_twice() -> None:
    """With no card there is nothing to attempt and nothing to retry."""
    accelerator = media.Accelerator(_report())
    seen: list[Encoder] = []

    async def attempt(encoder: Encoder, decode: tuple[str, ...]) -> str:
        seen.append(encoder)
        assert decode == ()
        return "rendered"

    await accelerator.run(attempt)

    assert seen == [Encoder.CPU]
    assert not accelerator.offers_hardware


# --- the render node, on a machine that may not have one


def test_a_machine_with_no_render_nodes_at_all_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container without the device passed through has no render node."""

    def nothing_there(self: Path) -> Any:
        raise OSError("no such directory")

    monkeypatch.setattr(Path, "iterdir", nothing_there)

    assert render_node() is None


def test_a_directory_that_holds_no_render_node_is_the_same_as_no_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A `/dev/dri` holding only a card node has none: a card node cannot be encoded through."""
    held = [tmp_path / "card0"]
    monkeypatch.setattr(Path, "iterdir", lambda self: iter(held))

    assert render_node() is None


def test_the_first_of_several_render_nodes_is_taken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With two cards, the first render node by name, not the filesystem's order."""
    held = [tmp_path / name for name in ("renderD129", "card0", "renderD128")]
    monkeypatch.setattr(Path, "iterdir", lambda self: iter(held))

    assert render_node() == str(tmp_path / "renderD128")


# --- a tool that answers with something other than what it promised


async def test_a_tool_that_does_not_answer_in_json_is_reported_as_that(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-JSON output is reported as the tool's fault, not a JSONDecodeError several frames
    deep."""

    async def answers(*_args: object, **_kwargs: object) -> str:
        return "not json at all"

    monkeypatch.setattr(media, "run", answers)

    with pytest.raises(FFmpegError, match="did not return JSON"):
        await media.run_json(["/usr/bin/ffprobe", "-x"], time_limit=1)


async def test_a_tool_that_answers_with_a_list_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid JSON of the wrong shape is refused here, before a caller indexes it by name."""

    async def answers(*_args: object, **_kwargs: object) -> str:
        return json.dumps([1, 2, 3])

    monkeypatch.setattr(media, "run", answers)

    with pytest.raises(FFmpegError, match="other than an object"):
        await media.run_json(["/usr/bin/ffprobe", "-x"], time_limit=1)


async def test_a_tool_that_answers_properly_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def answers(*_args: object, **_kwargs: object) -> str:
        return json.dumps({"streams": []})

    monkeypatch.setattr(media, "run", answers)

    assert await media.run_json(["/usr/bin/ffprobe", "-x"], time_limit=1) == {"streams": []}


# --- turning milliseconds into what ffmpeg reads


@pytest.mark.parametrize(
    ("milliseconds", "expected"),
    [(0, "0.000"), (1, "0.001"), (1_900, "1.900"), (95_000, "95.000")],
)
def test_a_moment_is_written_to_the_millisecond(milliseconds: int, expected: str) -> None:
    """A moment is written to the millisecond, three places, as a frame-accurate cut is measured."""
    assert media.seconds(milliseconds) == expected


# --- running the tool, and every way it can fail to run


async def test_a_tool_that_succeeds_hands_back_what_it_wrote(tmp_path: Path) -> None:
    echo = stand_in_tool(tmp_path, "echo", "import sys; print(' '.join(sys.argv[1:]))")
    written = await media.run([echo, "hello"], time_limit=5, capture=True)
    assert written.strip() == b"hello"


async def test_a_tool_that_exits_badly_is_reported_with_what_it_said(tmp_path: Path) -> None:
    """A tool that exits badly is reported with what it said, or "no detail"."""
    false = stand_in_tool(tmp_path, "false", "raise SystemExit(1)")
    with pytest.raises(FFmpegError, match="failed:"):
        await media.run([false], time_limit=5, capture=True)


async def test_a_tool_windows_could_not_start_says_so_in_place_of_no_detail(
    tmp_path: Path,
) -> None:
    locked = stand_in_tool(tmp_path, "locked", "raise SystemExit(0xC0000043)")
    with pytest.raises(FFmpegError) as raised:
        await media.run([locked], time_limit=5, capture=True)
    assert subprocess.NEVER_STARTED in str(raised.value)
    found = kind_of(f"FFmpegError: {raised.value}")
    assert found is not None
    assert found.name == "never-started"


async def test_a_tool_that_is_not_there_is_a_media_error_and_not_an_os_one() -> None:
    """A missing binary is an FFmpegError, which every caller catches, never an OSError."""
    with pytest.raises(FFmpegError):
        await media.run(["/nonexistent/ffmpeg"], time_limit=5, capture=True)


async def test_bytes_can_be_handed_to_a_tool_on_its_input(tmp_path: Path) -> None:
    """Bytes can be handed to ffmpeg on its input."""
    cat = stand_in_tool(
        tmp_path, "cat", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    )
    written = await media.run([cat], time_limit=5, capture=True, stdin=b"pictures")
    assert written == b"pictures"


# --- the share of the machine one background job may take


def test_a_background_job_gets_a_share_of_the_machine_rather_than_all_of_it(
    tmp_path: Path,
) -> None:
    """ffmpeg is capped to a share of the machine: uncapped, several together starve the application
    itself, which looks like a slow database."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    share = media.background_threads(settings)

    assert share >= 1
    assert share <= (os.cpu_count() or 1)


def test_the_thread_cap_reaches_both_of_ffmpegs_pools(tmp_path: Path) -> None:
    """`-threads` caps decoding and `-filter_threads` the filter graph: one flag reaches one
    pool."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    flags = media.background_flags(settings)

    assert "-threads" in flags
    assert "-filter_threads" in flags
    share = flags[flags.index("-threads") + 1]
    assert flags[flags.index("-filter_threads") + 1] == share
    assert int(share) >= 1


# --- finding a copy of a file that can actually be opened


def _asset(asset_id: str = "01HX0000000000000000000A01") -> Asset:
    return Asset(
        id=asset_id,
        identity="digest",
        media_type="video",
        mime=None,
        width=None,
        height=None,
        duration_ms=None,
        fps=None,
        size_bytes=None,
        container=None,
        vcodec=None,
        acodec=None,
        bit_depth=None,
        phash=None,
        videohash=None,
        original_filename=None,
        added_at=0,
        probed_at=None,
    )


def _location(location_id: str, *, status: LocationStatus = LocationStatus.PRESENT) -> Location:
    return Location(
        id=location_id,
        asset_id="01HX0000000000000000000A01",
        root_id="01HX0000000000000000000R01",
        folder_id=None,
        rel_path=f"clips/{location_id}.mp4",
        filename=f"{location_id}.mp4",
        size_bytes=None,
        mtime=None,
        status=status,
        first_seen_at=0,
        last_seen_at=0,
    )


class _Store:
    """The three questions `resolve` asks a content store: the ORDER of attempts and a failing one
    are under test, which a real store would need a broken root on disk to show."""

    def __init__(
        self,
        *,
        asset: Asset | None,
        locations: list[Location],
        paths: dict[str, Path | Exception],
    ) -> None:
        self._asset = asset
        self._locations = locations
        self._paths = paths

    async def get(self, asset_id: str) -> Asset | None:
        return self._asset

    async def locations(self, asset_id: str) -> list[Location]:
        return self._locations

    async def local_copy(self, asset_id: str) -> Path | None:
        return None

    async def path_of(self, location: Location) -> Path:
        answer = self._paths[location.id]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _store(**kwargs: Any) -> ContentStore:
    return cast("ContentStore", _Store(**kwargs))


async def test_an_asset_that_is_gone_is_told_apart_from_one_that_cannot_be_opened() -> None:
    """A gone asset (a 404) and one that cannot be opened (an unplugged drive) are told apart."""
    with pytest.raises(MissingAsset):
        await media.resolve(
            _store(asset=None, locations=[], paths={}), "01HX0000000000000000000A01"
        )


async def test_the_first_copy_that_opens_is_the_one_used(tmp_path: Path) -> None:
    """The first copy that opens is used: copies of the same bytes make the same thumbnail."""
    second = tmp_path / "second.mp4"
    second.write_bytes(b"data")
    store = _store(
        asset=_asset(),
        locations=[_location("first"), _location("second")],
        paths={"first": tmp_path / "gone.mp4", "second": second},
    )

    found = await media.resolve(store, "01HX0000000000000000000A01")

    assert found.path == second
    assert found.original == second
    assert found.location.id == "second"


async def test_a_copy_already_known_to_be_missing_is_not_even_tried(tmp_path: Path) -> None:
    """A copy marked missing is not tried: it may be what replaced an unmounted share."""
    present = tmp_path / "present.mp4"
    present.write_bytes(b"data")
    store = _store(
        asset=_asset(),
        locations=[
            _location("unmounted", status=LocationStatus.MISSING),
            _location("present"),
        ],
        # Reaching for the missing one would raise a KeyError here.
        paths={"present": present},
    )

    found = await media.resolve(store, "01HX0000000000000000000A01")

    assert found.location.id == "present"


async def test_a_copy_whose_path_no_longer_validates_is_passed_over_for_the_next(
    tmp_path: Path,
) -> None:
    """An unusable copy (a root gone, a path no longer valid) is logged and skipped, so a good copy
    elsewhere is still found."""
    good = tmp_path / "good.mp4"
    good.write_bytes(b"data")
    store = _store(
        asset=_asset(),
        locations=[_location("stale"), _location("good")],
        paths={"stale": LookupError("that root is not registered"), "good": good},
    )

    found = await media.resolve(store, "01HX0000000000000000000A01")

    assert found.location.id == "good"


async def test_no_copy_that_opens_says_what_the_person_should_check(tmp_path: Path) -> None:
    """A copy whose root does not answer waits for it, its attempt handed back."""
    store = _store(
        asset=_asset(),
        locations=[_location("one"), _location("two")],
        paths={
            "one": tmp_path / "unplugged" / "clips" / "one.mp4",
            "two": ValueError("outside its root"),
        },
    )

    with pytest.raises(media_sources.CopiesAway, match="not connected") as raised:
        await media.resolve(store, "01HX0000000000000000000A01")

    assert held_for(raised.value) == media_sources.AWAY_WAIT


async def test_a_copy_gone_from_a_folder_that_answers_is_not_retried(tmp_path: Path) -> None:
    """Its root answers and the file is not in it: no retry brings it back, so none is spent."""
    (tmp_path / "root" / "clips").mkdir(parents=True)
    store = _store(
        asset=_asset(),
        locations=[_location("one")],
        paths={"one": tmp_path / "root" / "clips" / "one.mp4"},
    )

    with pytest.raises(media_sources.CopiesGone, match="moved or deleted") as raised:
        await media.resolve(store, "01HX0000000000000000000A01")

    assert isinstance(raised.value, JobFailedPermanently)
    assert held_for(raised.value) is None


async def test_an_asset_with_no_copies_at_all_is_not_retried() -> None:
    store = _store(asset=_asset(), locations=[], paths={})

    with pytest.raises(media_sources.CopiesGone, match="none of the 0"):
        await media.resolve(store, "01HX0000000000000000000A01")


async def test_a_drive_that_stops_answering_is_waited_for_not_hung_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A share that hangs a stat is given `ANSWER_WITHIN`, then the job waits for it."""
    import threading

    hung = threading.Event()
    store = _store(asset=_asset(), locations=[_location("one")], paths={"one": tmp_path / "x"})
    monkeypatch.setattr(media_sources, "ANSWER_WITHIN", 0.2)
    monkeypatch.setattr(Path, "is_file", lambda _self: hung.wait(5) or False)
    try:
        with pytest.raises(media_sources.CopiesAway):
            await asyncio.wait_for(media.resolve(store, "01HX0000000000000000000A01"), 3)
    finally:
        hung.set()


async def test_a_member_whose_archive_drops_mid_read_waits_and_a_broken_one_is_refused(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "root" / "set.zip"
    archive.parent.mkdir()
    archive.write_bytes(b"zip")
    inside = dataclasses.replace(
        _location("one"), rel_path="set.zip/a.jpg", archive_rel_path="set.zip", member_path="a.jpg"
    )

    async def refused_with(cause: BaseException) -> BaseException:
        fake = _Store(asset=_asset(), locations=[inside], paths={})

        async def container_path_of(location: Location) -> Path:
            return archive

        async def path_of(location: Location) -> Path:
            raise ArchiveRefused("that file could not be read out of the archive") from cause

        fake.container_path_of = container_path_of  # type: ignore[attr-defined]
        fake.path_of = path_of  # type: ignore[method-assign]
        with pytest.raises(Exception) as raised:
            await media.resolve(cast("ContentStore", fake), "01HX0000000000000000000A01")
        return raised.value

    assert isinstance(await refused_with(OSError("the share dropped")), media_sources.CopiesAway)
    assert isinstance(await refused_with(BadZipFile("not a zip")), JobFailedPermanently)
    full = await refused_with(OSError(errno.ENOSPC, "No space left on device"))
    assert isinstance(full, WaitingForSpace) and held_for(full) == ROOM_WAIT


async def test_a_file_a_decoder_can_already_read_is_handed_over_untouched(
    tmp_path: Path,
) -> None:
    """For every format but one, preparing a copy does nothing, so callers need not know which."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"data")
    store = _store(asset=_asset(), locations=[_location("one")], paths={"one": clip})
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    found = await media.resolve_decodable(store, "01HX0000000000000000000A01", settings=settings)

    assert found.path == clip
    assert found.original == clip


async def test_a_format_a_decoder_cannot_read_is_converted_first_and_remembers_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg is pointed at the converted copy while the original is still what is named."""
    animated = tmp_path / "clip.webp"
    animated.write_bytes(b"data")
    readable = tmp_path / "clip.png"
    readable.write_bytes(b"data")
    store = _store(asset=_asset(), locations=[_location("one")], paths={"one": animated})
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    from sift.kernel import webp

    monkeypatch.setattr(webp, "needs_a_readable_copy", lambda asset: True)

    async def converted(*_args: object, **_kwargs: object) -> Path:
        return readable

    monkeypatch.setattr(webp, "readable_copy", converted)

    found = await media.resolve_decodable(store, "01HX0000000000000000000A01", settings=settings)

    assert found.path == readable
    assert found.original == animated


async def test_a_heif_photograph_is_handed_over_as_its_whole_picture_and_never_as_a_webp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A HEIF photograph is decoded from the whole picture libheif made, not one tile; derivatives
    are filed against the original."""
    photograph = tmp_path / "photo.heic"
    photograph.write_bytes(b"data")
    whole = tmp_path / "photo.png"
    whole.write_bytes(b"data")
    asset = dataclasses.replace(_asset(), media_type="image", mime="image/heic")
    store = _store(asset=asset, locations=[_location("one")], paths={"one": photograph})
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    from sift.kernel import heif, webp

    def no_webp(_asset: Asset) -> bool:
        raise AssertionError("a HEIF photograph is never asked the WebP question")

    async def made(_store: object, asked: Asset, original: Path, **_kwargs: object) -> Path:
        assert asked is asset and original == photograph
        return whole

    monkeypatch.setattr(webp, "needs_a_readable_copy", no_webp)
    monkeypatch.setattr(heif, "readable_copy", made)

    found = await media.resolve_decodable(store, "01HX0000000000000000000A01", settings=settings)

    assert found.path == whole
    assert found.original == photograph


@pytest.mark.parametrize("apart", [True, False])
async def test_a_jpeg_two_readers_turn_apart_is_handed_over_as_the_browser_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apart: bool
) -> None:
    """Only a JPEG photograph's head is read, and only one turned apart goes through its copy."""
    photograph = tmp_path / "photo.jpg"
    photograph.write_bytes(b"data")
    copy = tmp_path / "turned.jpg"
    asset = dataclasses.replace(_asset(), media_type="image", mime="image/jpeg")
    store = _store(asset=asset, locations=[_location("one")], paths={"one": photograph})
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    from sift.kernel import jpeg_turn

    async def made(_store: object, asked: Asset, original: Path, **_kwargs: object) -> Path:
        assert asked is asset and original == photograph
        return copy

    monkeypatch.setattr(jpeg_turn, "drawn_apart_in", lambda head: apart)
    monkeypatch.setattr(jpeg_turn, "readable_copy", made)

    found = await media.resolve_decodable(store, "01HX0000000000000000000A01", settings=settings)

    assert found.path == (copy if apart else photograph)
    assert found.original == photograph


def test_an_uploaded_picture_is_read_off_the_pipe_and_never_seeks(tmp_path: Path) -> None:
    """An uploaded picture is read off `pipe:0`, never written to disk, with NO `-ss` (on ffmpeg 7 a
    seek before a still discards its only frame and writes nothing) and `-frames:v 1`, so an
    animated or multi-frame upload answers with its first frame."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    argv = media.cover_picture_args(
        tmp_path / "cover.jpg", height=720, quality=4, settings=settings
    )

    assert argv[0] == settings.ffmpeg_path
    assert "-i" in argv and argv[argv.index("-i") + 1] == "pipe:0"
    assert "-ss" not in argv, "a seek in front of a still discards the only frame there is"
    assert argv[argv.index("-frames:v") + 1] == "1"
    assert argv[argv.index("-map_metadata") + 1] == "-1"
    assert "-an" in argv
    assert argv[-1] == str(tmp_path / "cover.jpg")


def test_a_cover_is_cut_from_sifts_own_picture_by_name_with_no_seek(tmp_path: Path) -> None:
    """A cover is cut by name from Sift's own still, never seeked; the crop is handed in whole."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    crop = "crop=iw*0.5:ih*0.5:iw*0.25:ih*0.1"

    argv = media.cover_frame_args(
        tmp_path / "still.jpg", tmp_path / "cover.jpg", crop=crop, quality=3, settings=settings
    )

    assert argv[0] == settings.ffmpeg_path
    assert argv[argv.index("-i") + 1] == str(tmp_path / "still.jpg")
    assert "-ss" not in argv, "a seek in front of a still discards the only frame there is"
    assert argv[argv.index("-frames:v") + 1] == "1"
    assert argv[argv.index("-map_metadata") + 1] == "-1"
    assert argv[argv.index("-vf") + 1] == crop
    assert argv[argv.index("-q:v") + 1] == "3"
    assert "-an" in argv
    assert argv[-1] == str(tmp_path / "cover.jpg")


# --- how many jobs really run together, and who gets to say
#
# `jobs_at_once` is what every ffmpeg's thread cap divides by. It is MODULE-LEVEL, so a test that
# boots the application decides the share for every command built after it in the process.


def test_the_pool_answer_wins_over_the_settings_it_is_handed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the pool was told, else the settings' hardware answer: once the pool has spoken, the
    setting is not consulted."""
    monkeypatch.setattr(media_share, "_jobs_at_once", None)
    settings = Settings(worker_concurrency=2)

    assert media.jobs_at_once(settings) == 2

    assert media.set_jobs_at_once(9) is True
    assert media.jobs_at_once(settings) == 9, "the settings were read after the pool had spoken"


def test_setting_the_same_number_again_reports_no_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A change is reported only when the number moved, or the pools would be resized on a timer."""
    monkeypatch.setattr(media_share, "_jobs_at_once", None)

    assert media.set_jobs_at_once(4) is True
    assert media.set_jobs_at_once(4) is False


def test_a_worker_count_below_one_is_refused_rather_than_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A number under one is refused at the door: `background_threads` divides by it."""
    monkeypatch.setattr(media_share, "_jobs_at_once", 3)

    assert media.set_jobs_at_once(0) is False
    assert media.set_jobs_at_once(-1) is False
    assert media.jobs_at_once(Settings(worker_concurrency=2)) == 3


def test_the_thread_share_is_the_machine_divided_by_what_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tools actually running divide the machine, never more of them than the workers; a share
    of at least one thread, whatever the arithmetic says."""
    monkeypatch.setattr(media_share, "_share", None)
    monkeypatch.setattr(media_share, "_jobs_at_once", 4)
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    # Alone, a tool has the machine: a file read by itself is not held to one worker's share.
    monkeypatch.setattr(media_share, "_tools_running", 0)
    assert media.background_threads(Settings()) == 24
    monkeypatch.setattr(media_share, "_tools_running", 3)
    assert media.background_threads(Settings()) == 6
    # Never fewer than one worker's share, however many tools say they run.
    monkeypatch.setattr(media_share, "_tools_running", 40)
    assert media.background_threads(Settings()) == 6

    # More workers than threads, which the self-test can recommend.
    monkeypatch.setattr(media_share, "_jobs_at_once", 50)
    assert media.background_threads(Settings()) == 1


async def test_a_running_tool_is_counted_once_and_not_against_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool sizes itself by the others: inside its own count it is not one of them, and a run
    of tools inside one count is one."""
    monkeypatch.setattr(media_share, "_share", None)
    monkeypatch.setattr(media_share, "_jobs_at_once", 12)
    monkeypatch.setattr(media_share, "_tools_running", 1)
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    assert media.tools_sharing(Settings()) == 2
    with media._running_tool():
        assert media_share._tools_running == 2
        assert media.tools_sharing(Settings()) == 2
        with media._running_tool():
            assert media_share._tools_running == 2
    assert media_share._tools_running == 1


def test_the_thread_share_while_stepping_back_is_the_share_of_the_cores_over_what_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three workers at a quarter of twenty-four processors get two threads each, however few
    tools run: the processor rate each tool is held to was set from the share."""
    from sift.kernel import subprocess as tools

    monkeypatch.setattr(media_share, "_jobs_at_once", 12)
    monkeypatch.setattr(media_share, "_tools_running", 2)
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    held: list[int | None] = []
    monkeypatch.setattr(tools, "hold_background", held.append)

    assert media.set_share(running=3, percent=25) is True
    assert media.background_threads(Settings()) == 2
    assert held == [833]
    # Nothing moved: no change, nothing held again.
    assert media.set_share(running=3, percent=25) is False
    assert held == [833]
    # Nobody here: the whole device, divided by the tools running (two others and this one).
    assert media.set_share(running=12, percent=100) is True
    assert media.background_threads(Settings()) == 8
    assert held == [833, None]
    assert media.set_share(running=2, percent=100) is True
    assert media.background_threads(Settings()) == 12


class TestTellingBrokenBytesFromABadMoment:
    """Which ffmpeg refusals are final, read off the tool's own words.

    A corrupt stream and a share that blinked both exit non-zero; only the first is worth not
    retrying, and only on evidence, so each phrase matched came from a recorded failure.
    """

    def test_a_truncated_h264_stream_is_final(self) -> None:
        """A truncated H.264 stream claiming a packet bigger than its file is final."""
        assert media.is_broken_data(
            "ffmpeg failed: [h264 @ 0000] Invalid NAL unit size (1088342112 > 21767).\n"
            "[h264 @ 0000] missing picture in access unit with size 21771"
        )
        assert media.is_broken_data("Error splitting the input into NAL units.")

    def test_a_corrupt_still_is_final(self) -> None:
        """A corrupt still is final: the decoder read it and it is not a picture."""
        assert media.is_broken_data(
            "ffmpeg failed: [png @ 0000] Invalid sBIT size: 3, expected: 4\n"
            "[vist#0:0/png @ 0000] Decoding error: Invalid data found when processing input\n"
            "[vist#0:0/png @ 0000] Decode error rate 1 exceeds maximum 0.666667"
        )

    def test_a_file_still_being_written_keeps_its_retries(self) -> None:
        """A file still being copied onto a share keeps its retries: it decodes a minute later."""
        assert not media.is_broken_data("ffmpeg failed: moov atom not found")
        # The demuxer's generic text covers a partial read too.
        assert not media.is_broken_data("ffmpeg failed: Invalid data found when processing input")

    def test_a_share_that_went_away_keeps_its_retries(self) -> None:
        """A share that went away keeps its retries."""
        assert not media.is_broken_data("ffmpeg failed: Input/output error")
        assert not media.is_broken_data("timed out after 600 seconds")
        assert not media.is_broken_data("No such file or directory")

    def test_anything_unrecognised_keeps_its_retries(self) -> None:
        """Anything unrecognised keeps its retries: final would cost the file."""
        assert not media.is_broken_data("ffmpeg failed: no detail")
        assert not media.is_broken_data("")

    def test_the_words_are_matched_however_the_tool_capitalised_them(self) -> None:
        """Matched however the tool capitalised it."""
        assert media.is_broken_data("INVALID NAL UNIT SIZE (1 > 2)")


# --- one word for a run's record to keep


async def test_the_state_tells_a_plain_machine_apart_from_a_card_given_up_on() -> None:
    """THREE answers: a machine with no card and a card given up on read alike from
    `offers_hardware`, and are what somebody comparing two runs needs told apart."""
    working = media.Accelerator(_card(), patience=3)
    assert working.state == "on"

    plain = media.Accelerator(_report())
    assert plain.state == "off"

    attempt = _fails(times=99)
    for _ in range(6):
        await working.run(attempt)

    assert working.state == "latched_off"
    # And the pair they are both `False` for, which is what makes the three-way answer worth having.
    assert not working.offers_hardware and not plain.offers_hardware
