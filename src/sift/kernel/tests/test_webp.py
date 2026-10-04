# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading an animated WebP: what the tools say, and what is made of it.

The tools themselves are stood in for here, with small scripts that print what the real ones print.
That is deliberate and it is the honest split. What can go wrong in this module is the parsing:
a canvas size read from the wrong line, a frame count that counts chunks instead of frames, a
duration summed from a field that is per-frame rather than total, and none of that needs libwebp
to be installed to be wrong.

**The tools running for real is checked in the image**, by `scripts/check_animated_webp.py`, for the
same reason the ffmpeg conformance checks are: the version on the machine the tests run on is not
the version anybody runs, and a decoder verified against a decoder nobody has proves nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel import webp
from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore, DerivativeKind
from sift.kernel.ids import new_id
from sift.testing.tools import stand_in_tool

# What `webpinfo 1.5.0` really prints for a five-frame animated WebP, trimmed to the lines that are
# read. Kept verbatim rather than paraphrased: the spacing is what the patterns match against.
REAL_REPORT = """File: anim.webp
RIFF HEADER:
  File size:   1424
Chunk VP8X at offset     12, length     18
  ICCP: 0
  Alpha: 0
  EXIF: 0
  XMP: 0
  Animation: 1
  Canvas size 64 x 64
Chunk ANIM at offset     30, length     14
  Background color:(ARGB) ff ff ff ff
  Loop count      : 0
Chunk ANMF at offset     44, length    688
  Offset_X: 0
  Offset_Y: 0
  Width: 64
  Height: 64
  Duration: 200
  Dispose: 0
  Blend: 1
Chunk VP8  at offset     68, length    664
  Width: 64
  Height: 64
  Alpha: 0
  Animation: 0
  Format: Lossy (1)
Chunk ANMF at offset    732, length    178
  Offset_X: 0
  Offset_Y: 48
  Width: 64
  Height: 9
  Duration: 300
  Dispose: 0
  Blend: 0
"""

STILL_REPORT = """File: still.webp
RIFF HEADER:
  File size:   200
Chunk VP8X at offset     12, length     18
  Animation: 0
  Canvas size 64 x 64
"""


def stand_in(
    directory: Path, name: str, *, prints: str = "", exits: int = 0, makes: int = 0
) -> str:
    """A program that behaves like one of the tools, and the path to it."""
    body = ["import pathlib, sys"]
    if makes:
        # `anim_dump -folder <dir> -prefix <prefix> <file>`: the folder is the second argument.
        body.append("folder = pathlib.Path(sys.argv[2])")
        body.append("prefix = sys.argv[4]")
        body.append(f"for index in range({makes}):")
        body.append('    (folder / f"{prefix}{index:04d}.png").write_bytes(b"x")')
    if prints:
        body.append(f"sys.stdout.write({prints!r})")
    body.append(f"raise SystemExit({exits})")
    return stand_in_tool(directory, name, "\n".join(body))


@pytest.fixture
def tools(tmp_path: Path) -> Path:
    where = tmp_path / "tools"
    where.mkdir()
    return where


def settings_with(tmp_path: Path, *, webpinfo: str = "false", anim_dump: str = "false") -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        webpinfo_path=webpinfo,
        anim_dump_path=anim_dump,
    )


# --- reading the container --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_canvas_frames_and_duration_are_read(tmp_path: Path, tools: Path) -> None:
    """The frame count is the number of frame blocks, and the duration is their sum.

    Both are easy to get subtly wrong in the same direction: a WebP times each frame separately, so
    a file that pauses on one frame runs far longer than a frame count times any average.
    """
    settings = settings_with(tmp_path, webpinfo=stand_in(tools, "webpinfo", prints=REAL_REPORT))

    animation = await webp.inspect(tmp_path / "anim.webp", settings=settings)

    assert (animation.width, animation.height) == (64, 64)
    assert animation.frames == 2
    assert animation.duration_ms == 500


@pytest.mark.asyncio
async def test_a_still_webp_is_refused_by_name(tmp_path: Path, tools: Path) -> None:
    """It is a perfectly good file that this module is the wrong reader for."""
    settings = settings_with(tmp_path, webpinfo=stand_in(tools, "webpinfo", prints=STILL_REPORT))

    with pytest.raises(webp.WebpError, match="still"):
        await webp.inspect(tmp_path / "still.webp", settings=settings)


@pytest.mark.asyncio
async def test_a_report_with_no_canvas_is_not_guessed_at(tmp_path: Path, tools: Path) -> None:
    settings = settings_with(
        tmp_path, webpinfo=stand_in(tools, "webpinfo", prints="File: odd.webp\n")
    )

    with pytest.raises(webp.WebpError, match="canvas"):
        await webp.inspect(tmp_path / "odd.webp", settings=settings)


@pytest.mark.asyncio
async def test_a_GIF_with_no_frame_blocks_is_refused(tmp_path: Path, tools: Path) -> None:
    """The flag says it moves and there is nothing to show. A truncated file reads like this."""
    report = "  Animation: 1\n  Canvas size 64 x 64\n"
    settings = settings_with(tmp_path, webpinfo=stand_in(tools, "webpinfo", prints=report))

    with pytest.raises(webp.WebpError, match="no frames"):
        await webp.inspect(tmp_path / "cut.webp", settings=settings)


@pytest.mark.asyncio
async def test_a_frame_timed_at_zero_is_given_a_real_duration(tmp_path: Path, tools: Path) -> None:
    """Browsers do the same. Taken literally, a whole GIF would play in no time at all."""
    report = "  Animation: 1\n  Canvas size 8 x 8\n  Duration: 0\n  Duration: 0\n"
    settings = settings_with(tmp_path, webpinfo=stand_in(tools, "webpinfo", prints=report))

    animation = await webp.inspect(tmp_path / "instant.webp", settings=settings)

    assert animation.duration_ms == 200
    assert animation.fps == 10


@pytest.mark.asyncio
async def test_a_failing_tool_keeps_what_it_said(tmp_path: Path, tools: Path) -> None:
    """The operator reading a job's error is the person who needs the tool's own words."""
    settings = settings_with(tmp_path, webpinfo=stand_in(tools, "webpinfo", exits=3))

    with pytest.raises(webp.WebpError, match="webpinfo failed"):
        await webp.inspect(tmp_path / "bad.webp", settings=settings)


@pytest.mark.asyncio
async def test_a_tool_that_is_not_installed_says_so(tmp_path: Path) -> None:
    """An image built without the WebP tools. The message has to name the cause, because the file
    is fine and re-downloading it will not help."""
    settings = settings_with(tmp_path, webpinfo="/nowhere/webpinfo")

    with pytest.raises(webp.WebpError):
        await webp.inspect(tmp_path / "anim.webp", settings=settings)


# --- the frames -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frames_come_back_in_order(tmp_path: Path, tools: Path) -> None:
    """Order is the whole content of a GIF. Ten frames sort as strings, not as numbers,
    unless the names are padded, which is what the prefix and the pattern are for."""
    settings = settings_with(tmp_path, anim_dump=stand_in(tools, "anim_dump", makes=12))

    frames = await webp.dump_frames(tmp_path / "anim.webp", tmp_path / "out", settings=settings)

    assert [frame.name for frame in frames] == [f"frame_{index:04d}.png" for index in range(12)]


@pytest.mark.asyncio
async def test_a_dump_that_produced_nothing_is_an_error(tmp_path: Path, tools: Path) -> None:
    """It exits cleanly having written no frames on a file it could not read. Treated as success,
    the encode that follows would fail with something that names ffmpeg rather than the cause."""
    settings = settings_with(tmp_path, anim_dump=stand_in(tools, "anim_dump"))

    with pytest.raises(webp.WebpError, match="no frames"):
        await webp.dump_frames(tmp_path / "anim.webp", tmp_path / "out", settings=settings)


@pytest.mark.asyncio
async def test_a_failing_dump_keeps_what_it_said(tmp_path: Path, tools: Path) -> None:
    settings = settings_with(tmp_path, anim_dump=stand_in(tools, "anim_dump", exits=2))

    with pytest.raises(webp.WebpError, match="anim_dump failed"):
        await webp.dump_frames(tmp_path / "anim.webp", tmp_path / "out", settings=settings)


@pytest.mark.asyncio
async def test_a_dump_tool_that_is_not_installed_says_so(tmp_path: Path) -> None:
    settings = settings_with(tmp_path, anim_dump="/nowhere/anim_dump")

    with pytest.raises(webp.WebpError):
        await webp.dump_frames(tmp_path / "anim.webp", tmp_path / "out", settings=settings)


# --- the encode -------------------------------------------------------------------------------


def test_the_encode_reads_the_numbered_frames_at_the_rate_they_were_timed_for(
    tmp_path: Path,
) -> None:
    settings = settings_with(tmp_path)

    argv = webp.encode_args(tmp_path, tmp_path / "out.mp4", fps=12.5, settings=settings)

    assert "-framerate" in argv
    assert argv[argv.index("-framerate") + 1] == "12.5000"
    assert argv[argv.index("-i") + 1].endswith("frame_%04d.png")


def test_the_encode_rounds_both_sides_up_to_an_even_number(tmp_path: Path) -> None:
    """x264 refuses an odd dimension outright, and a 405x721 GIF is not unusual. Without
    this the encode fails on exactly the files that arrive from the sites that serve these."""
    argv = webp.encode_args(
        tmp_path, tmp_path / "out.mp4", fps=10, settings=settings_with(tmp_path)
    )

    assert argv[argv.index("-vf") + 1] == "scale=ceil(iw/2)*2:ceil(ih/2)*2"


def test_the_encode_is_h264_in_a_container_everything_here_already_reads(tmp_path: Path) -> None:
    argv = webp.encode_args(
        tmp_path, tmp_path / "out.mp4", fps=10, settings=settings_with(tmp_path)
    )

    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[-1].endswith(".mp4")


def test_the_frame_rate_of_a_GIF_that_says_nothing_is_not_infinite(tmp_path: Path) -> None:
    """Dividing by a duration of zero is the obvious way to write this and the obvious way to
    crash on a malformed file."""
    assert webp.Animation(width=8, height=8, frames=3, duration_ms=0).fps == 10


# --- which assets need any of this ------------------------------------------------------------


@pytest.mark.parametrize(
    ("media_type", "mime", "container", "expected"),
    [
        ("gif", "image/webp", "webp-animated", True),
        # Before the probe has run, which is the first thing that has to decode the file. The
        # container column is empty until the probe writes it, so reading it here would mean the
        # probe itself never got the readable copy, and the probe is where it is needed most.
        ("gif", "image/webp", None, True),
        # An animated WebP a row files as a video is still WebP bytes ffmpeg cannot decode; asking
        # for a GIF would send every pass to the raw file, which each gives up as undecodable.
        ("video", "image/webp", "webp-animated", True),
        # A still WebP is an image and ffmpeg reads it perfectly well.
        ("image", "image/webp", "webp", False),
        # A real GIF goes down the same path in the application and needs no conversion.
        ("gif", "image/gif", "gif", False),
        ("video", "video/mp4", "mp4", False),
    ],
)
def test_only_an_animated_webp_needs_converting_first(
    media_type: str, mime: str, container: str | None, expected: bool
) -> None:
    from sift.kernel.content import Asset

    asset = Asset(
        id="01HX00000000000000000000AA",
        identity="digest",
        media_type=media_type,
        mime=mime,
        width=None,
        height=None,
        duration_ms=None,
        fps=None,
        size_bytes=None,
        container=container,
        vcodec=None,
        acodec=None,
        bit_depth=None,
        phash=None,
        videohash=None,
        original_filename=None,
        added_at=0,
        probed_at=None,
    )

    assert webp.needs_a_readable_copy(asset) is expected


# --- the readable copy ------------------------------------------------------------------------
#
# These run a real ffmpeg over real PNG frames (the encode is the half that has to actually work)
# with the two libwebp tools stood in for. What is asserted is the bookkeeping around it: that
# the copy is recorded so it is made once, that it is found again next time, and that it is rebuilt
# rather than trusted when the file behind the row has been swept away.


async def _an_animated_asset(store: ContentStore, settings: Settings) -> Asset:
    asset_id = new_id()
    await store._db.execute(
        "INSERT INTO assets (id, identity, media_type, mime, added_at) "
        "VALUES (?, ?, 'gif', 'image/webp', 0)",
        (asset_id, f"digest-{asset_id}"),
    )
    got = await store.get(asset_id)
    assert got is not None
    return got


def _frames_written_by(tools: Path, count: int, size: str = "32x32") -> str:
    """A stand-in `anim_dump` that writes real pictures, so the encode has something to read."""
    return stand_in_tool(
        tools,
        "anim_dump",
        f"""
        import pathlib, subprocess, sys

        folder = pathlib.Path(sys.argv[2])
        prefix = sys.argv[4]
        for index in range({count}):
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=red:s={size}:d=1", "-frames:v", "1",
                    str(folder / f"{{prefix}}{{index:04d}}.png"),
                ],
                check=True,
            )
        """,
    )


@pytest.mark.asyncio
async def test_a_readable_copy_is_made_and_recorded(
    content_store: ContentStore, settings: Settings, tmp_path: Path, tools: Path
) -> None:
    settings = settings.model_copy(
        update={
            "webpinfo_path": stand_in(tools, "webpinfo", prints=REAL_REPORT),
            "anim_dump_path": _frames_written_by(tools, 4),
        }
    )
    asset = await _an_animated_asset(content_store, settings)

    made = await webp.readable_copy(content_store, asset, tmp_path / "anim.webp", settings=settings)

    assert made.is_file()
    assert made.suffix == ".mp4"
    kinds = [one.kind for one in await content_store.derivatives(asset.id)]
    assert kinds == [DerivativeKind.RENDITION]


@pytest.mark.asyncio
async def test_the_copy_is_made_once_and_found_again(
    content_store: ContentStore, settings: Settings, tmp_path: Path, tools: Path
) -> None:
    """Converting on every thumbnail, preview, sprite and face pass would be four encodes of the
    same file for one import."""
    settings = settings.model_copy(
        update={
            "webpinfo_path": stand_in(tools, "webpinfo", prints=REAL_REPORT),
            "anim_dump_path": _frames_written_by(tools, 2),
        }
    )
    asset = await _an_animated_asset(content_store, settings)
    # A thumbnail already exists, which is the ordinary state: the readable copy is looked for
    # among everything built from this file, not assumed to be the only thing there.
    await content_store.add_derivative(asset.id, DerivativeKind.THUMB, extension="jpg")
    first = await webp.readable_copy(
        content_store, asset, tmp_path / "anim.webp", settings=settings
    )

    again = await webp.readable_copy(
        content_store, asset, tmp_path / "anim.webp", settings=settings
    )

    assert again == first
    kinds = sorted(one.kind.value for one in await content_store.derivatives(asset.id))
    assert kinds == ["rendition", "thumb"]


@pytest.mark.asyncio
async def test_a_copy_whose_file_has_been_swept_is_made_again(
    content_store: ContentStore, settings: Settings, tmp_path: Path, tools: Path
) -> None:
    """The cache is disposable by design, and clearing it must not leave a library of GIFs
    that quietly stopped drawing. The row is not evidence the file is there."""
    settings = settings.model_copy(
        update={
            "webpinfo_path": stand_in(tools, "webpinfo", prints=REAL_REPORT),
            "anim_dump_path": _frames_written_by(tools, 2),
        }
    )
    asset = await _an_animated_asset(content_store, settings)
    first = await webp.readable_copy(
        content_store, asset, tmp_path / "anim.webp", settings=settings
    )
    first.unlink()

    again = await webp.readable_copy(
        content_store, asset, tmp_path / "anim.webp", settings=settings
    )

    assert again.is_file()


def test_the_tools_are_never_run_through_a_shell(tmp_path: Path) -> None:
    """A filename is an argument, always. These take a path that came from a remote site.

    Asserted on the argument lists themselves: every one is a list of separate strings, and a
    filename with a space in it stays one of them rather than becoming two.
    """
    awkward = tmp_path / "a file; rm -rf .png"
    argv = webp.encode_args(tmp_path, awkward, fps=10, settings=settings_with(tmp_path))

    # One argument, semicolon and spaces included, rather than three words a shell would split.
    assert argv[-1] == str(awkward)
    assert argv.count(str(awkward)) == 1
