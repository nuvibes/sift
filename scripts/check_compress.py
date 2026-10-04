# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compress a real file with the ffmpeg that ships, and check what came out.

Every test around the compressor asserts what ffmpeg is ASKED for: the argument list, built by a
pure function, read in a test. None of them hands the list to ffmpeg. That is the right split for
almost everything and it leaves one hole: an argument that is a no-op on one ffmpeg can silently
discard the only frame on the next, write nothing, and exit 0. An ffmpeg behaviour confirmed on any
build but the one that ships is not confirmed.

So this runs the real commands and looks at the files:

* **A size encode makes the file smaller and it still decodes.** Not merely "ffmpeg exited 0":
  that is true of an empty output, which is exactly the failure mode.
* **The sound is carried across untouched.** The promise everywhere except one named case, and the
  only way to check it is to compare the packets on both sides rather than to read the arguments
  back.
* **A rewrap changes the container and nothing else.** The picture that comes out has to be the
  same picture, bit for bit, or "nothing is re-encoded" is not true.
* **The picture is never scaled up**, which is what stops a small source being made bigger and
  worse by a rung whose height is above it.

And the editor's half, which has two holes of its own that only the real tool can close:

* **A cut really is a stream copy.** "It must not silently become a re-encode" is the whole promise
  of trimming, and no argument list can prove it. The packets that come out are hashed against the
  packets that went in over the same range, taken by a command written here rather than by the one
  under test, so a builder that quietly started encoding produces different bytes and fails.
* **Every builder writes to a path with NO extension and it still comes out in the right format.**
  That is not a contrivance: it is exactly what the write seam hands the encoder, so that the folder
  scan walks past a half-written file. Left to infer the format from the name, ffmpeg has nothing to
  infer from, so every one of these is checked by writing to an extension-less path and reading
  back what the file turned out to be.

    python scripts/check_compress.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import struct
import tempfile
from pathlib import Path

from sift.kernel.config import Settings, vendored_tool
from sift.slices.media_edit import encode, operations, orientation, tuning
from sift.slices.media_edit.operations import MOVING_FORMATS, STILL_FORMATS, StillFormat, Turn

#: The builds Sift's own settings pick, vendor/bin's in a checkout as in an installed copy. The
#: reference commands below use them too, so both sides of every comparison are read by what ships.
FFMPEG = vendored_tool("ffmpeg")
FFPROBE = vendored_tool("ffprobe")


async def _run(argv: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await process.communicate()
    if process.returncode != 0:
        raise SystemExit(f"{argv[0]} failed: {err.decode('utf-8', 'replace')[:400]}")


async def _probe(path: Path, stream: str, fields: str) -> list[str]:
    process = await asyncio.create_subprocess_exec(
        FFPROBE,
        "-v",
        "error",
        "-select_streams",
        stream,
        "-show_entries",
        f"stream={fields}",
        "-of",
        "csv=p=0",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await process.communicate()
    return [line for line in out.decode().strip().splitlines() if line]


async def _stream_digest(path: Path, stream: str) -> str:
    """The packets of one stream, hashed. What "carried across untouched" actually means.

    Comparing the FILES would fail on a container change that is supposed to happen; comparing the
    decoded pictures would pass on a re-encode that happened to look similar. The packets are the
    thing being copied, so they are the thing compared.
    """
    process = await asyncio.create_subprocess_exec(
        FFMPEG,
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        stream,
        "-c",
        "copy",
        "-f",
        "md5",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await process.communicate()
    return out.decode().strip()


async def _source(into: Path, settings: Settings) -> Path:
    """A file worth compressing: big enough that a target is a real target, with sound on it."""
    path = into / "source.mp4"
    await _run(
        [
            settings.ffmpeg_path, "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", "6M", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ]
    )  # fmt: skip
    return path


async def check(work: Path, settings: Settings) -> list[str]:
    failures: list[str] = []
    source = await _source(work, settings)
    source_size = source.stat().st_size
    source_audio = await _stream_digest(source, "0:a:0")
    source_video = await _stream_digest(source, "0:v:0")
    print(f"     source {source_size // 1024} KB, 1280x720")

    # --- a size encode ---------------------------------------------------------------------
    rung = tuning.RUNGS[4]  # the first that caps the picture at the floor
    smaller = work / "smaller.mp4"
    await _run(encode.compress_args(source, smaller, rung=rung, settings=settings))

    if not smaller.exists() or smaller.stat().st_size == 0:
        failures.append("the size encode produced nothing: ffmpeg exited 0 and wrote no file")
        return failures

    if smaller.stat().st_size >= source_size:
        failures.append(f"the copy is not smaller: {smaller.stat().st_size} against {source_size}")
    print(f"ok   smaller       {smaller.stat().st_size // 1024} KB")

    shape = await _probe(smaller, "v:0", "width,height")
    if not shape:
        failures.append("the copy has no video stream that ffprobe can read")
    else:
        width, height = (int(part) for part in shape[0].split(","))
        if height != tuning.MINIMUM_HEIGHT:
            failures.append(
                f"the floor rung produced {width}x{height}, not {tuning.MINIMUM_HEIGHT}p"
            )
        print(f"ok   at the floor  {width}x{height}")

    # --- the sound is not touched ----------------------------------------------------------
    if await _stream_digest(smaller, "0:a:0") != source_audio:
        failures.append("the sound changed on a size encode, and it never should")
    print("ok   sound copied  packets identical to the source")

    # --- a rewrap changes the container and nothing else -------------------------------------
    rewrapped = work / "rewrapped.mp4"
    await _run(encode.rewrap_args(source, rewrapped, settings=settings))
    if not rewrapped.exists() or rewrapped.stat().st_size == 0:
        failures.append("the rewrap produced nothing")
    else:
        if await _stream_digest(rewrapped, "0:v:0") != source_video:
            failures.append("the rewrap changed the picture, so it re-encoded something")
        if await _stream_digest(rewrapped, "0:a:0") != source_audio:
            failures.append("the rewrap changed the sound")
        print("ok   rewrap clean  video and sound both bit-identical")

    # --- a small source is never made bigger -------------------------------------------------
    small = work / "small.mp4"
    await _run(
        [
            settings.ffmpeg_path, "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(small),
        ]
    )  # fmt: skip
    upscaled = work / "upscaled.mp4"
    await _run(encode.compress_args(small, upscaled, rung=rung, settings=settings))
    grown = await _probe(upscaled, "v:0", "width,height")
    if grown and int(grown[0].split(",")[1]) != 360:
        failures.append(f"a 360p source came out at {grown[0]}, so it was scaled up")
    print(f"ok   no upscaling  {grown[0] if grown else 'unreadable'}")

    failures.extend(await _check_the_cut(work, source, settings))
    failures.extend(await _check_the_stills(work, settings))
    return failures


# --- the editor's half ----------------------------------------------------------------------


async def _packets_of_the_first(source: Path, seconds: float) -> str:
    """The source's own video packets over the range a cut is about to take.

    Written out here rather than built by the module under test, which is the point: if the cut
    builder ever stops saying `-c copy`, the packets it produces stop matching these and this fails.
    """
    process = await asyncio.create_subprocess_exec(
        FFMPEG, "-nostdin", "-v", "error", "-i", str(source),
        "-t", f"{seconds:.3f}", "-map", "0:v:0", "-c", "copy", "-f", "md5", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await process.communicate()
    return out.decode().strip()


async def _check_the_cut(work: Path, source: Path, settings: Settings) -> list[str]:
    """Trimming, against the real tool. Two claims, and the first is the whole feature.

    **A cut copies rather than encodes.** Everything about trimming being worth having (instant on
    a two-hour film, and identical quality) rests on that, and an argument list cannot show it.
    The packets that come out are compared against the packets that went in.

    **The destination has no extension**, exactly as the write seam hands it over, so this also
    proves the container came from the `-f` that was passed rather than from a name ffmpeg read.
    """
    failures: list[str] = []
    nameless = work / "cut-with-no-extension"
    await _run(
        operations.cut_args(
            source,
            nameless,
            start_ms=0,
            duration_ms=2_000,
            fmt=MOVING_FORMATS["mp4"],
            settings=settings,
        )
    )

    if not nameless.exists() or nameless.stat().st_size == 0:
        failures.append("the cut produced nothing: ffmpeg exited 0 and wrote no file")
        return failures

    container = await _probe_format(nameless)
    if "mp4" not in container:
        failures.append(f"the cut landed as {container}, not mp4, from a path with no extension")
    print(f"ok   cut container {container}")

    length = await _probe(nameless, "v:0", "duration")
    running = float(length[0]) if length and length[0] not in ("", "N/A") else 0.0
    if not 1.0 <= running <= 3.0:
        failures.append(f"the cut runs {running}s, and two seconds were asked for")
    print(f"ok   cut length    {running}s of an 8s source")

    if await _stream_digest(nameless, "0:v:0") != await _packets_of_the_first(source, 2.0):
        failures.append("the cut re-encoded the picture: its packets are not the source's")
    print("ok   cut copied    packets identical to the source")
    return failures


async def _probe_format(path: Path) -> str:
    process = await asyncio.create_subprocess_exec(
        FFPROBE, "-v", "error", "-show_entries", "format=format_name",
        "-of", "csv=p=0", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await process.communicate()
    return out.decode().strip()


async def _a_picture(into: Path, extension: str, settings: Settings) -> Path:
    """One frame, 640 by 480, in whichever still format is being checked."""
    path = into / f"picture.{extension}"
    await _run(
        [
            settings.ffmpeg_path, "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=1:duration=1",
            "-frames:v", "1", str(path),
        ]
    )  # fmt: skip
    return path


async def _check_the_stills(work: Path, settings: Settings) -> list[str]:
    """Cropping, resizing and rotating, on the ffmpeg that ships, into paths with no extension.

    This is where the still builders are most exposed. A photograph's format is not fixed the way a
    compression's is, so each one names its own muxer and codec, and with nothing in the
    destination name to fall back on, a table that is slightly wrong produces a file in the wrong
    format, or no file at all, with a zero exit code either way.
    """
    # What ffprobe calls each of them once written, which is not always what the ENCODER is called:
    # a WebP is written by `libwebp` and read back as `webp`.
    read_back_as = {"jpeg": "mjpeg", "png": "png", "webp": "webp"}

    failures: list[str] = []
    for name in ("jpeg", "png", "webp"):
        fmt = STILL_FORMATS[name]
        source = await _a_picture(work, fmt.extension, settings)

        async def _still(
            where: str, *filters: str, source: Path = source, fmt: StillFormat = fmt
        ) -> Path:
            produced = work / where
            await _run(
                operations.still_args(
                    source, produced, filters=list(filters), fmt=fmt, settings=settings
                )
            )
            return produced

        cropped = await _still(
            f"{name}-cropped-no-extension",
            operations.crop_filter(left=10, top=20, width=320, height=240),
        )
        resized = await _still(f"{name}-resized-no-extension", operations.resize_filter(width=200))
        turned = await _still(f"{name}-turned-no-extension", operations.turn_filter(Turn.RIGHT))
        mirrored = await _still(
            f"{name}-mirrored-no-extension", operations.turn_filter(Turn.MIRROR)
        )
        flipped = await _still(f"{name}-flipped-no-extension", operations.turn_filter(Turn.FLIP))
        # Several operations in one run, which is what a Save carrying more than one produces. The
        # size it comes out at is the proof the chain ran in order: cropped first and then turned,
        # 320 by 240 becomes 240 by 320. Turned first it would be something else entirely.
        together = await _still(
            f"{name}-together-no-extension",
            operations.crop_filter(left=10, top=20, width=320, height=240),
            operations.turn_filter(Turn.RIGHT),
        )

        for what, produced, shape in (
            ("crop", cropped, "320,240"),
            ("resize", resized, "200,150"),
            ("rotate", turned, "480,640"),
            ("mirror", mirrored, "640,480"),
            ("flip", flipped, "640,480"),
            ("crop and turn together", together, "240,320"),
        ):
            if not produced.exists() or produced.stat().st_size == 0:
                failures.append(f"a {name} {what} produced nothing, and ffmpeg exited 0")
                continue
            measured = await _probe(produced, "v:0", "width,height")
            if not measured:
                failures.append(f"a {name} {what} produced something ffprobe cannot read")
                continue
            if measured[0] != shape:
                failures.append(f"a {name} {what} came out {measured[0]}, not {shape}")
            codec = await _probe(produced, "v:0", "codec_name")
            if codec and codec[0] != read_back_as[name]:
                failures.append(f"a {name} {what} came out as {codec[0]}, not {read_back_as[name]}")
        print(
            f"ok   stills {name:<5} crop, resize, both mirrors and a two-step chain all landed "
            "with no extension to go on"
        )
    return failures


async def _check_a_turned_photograph(work: Path, settings: Settings) -> list[str]:
    """A photograph carrying a camera's note, read and then acted on, by the real tool.

    Two halves and both need the tool. **Reading** it: the note lives in the picture rather than in
    the container, and whether ffprobe reports it (and in which of the two places) is a fact
    about ffmpeg rather than about anything here. **Acting** on it: what makes a crop land on the
    part of the picture somebody dragged over is the turn going first in the chain, and the only
    proof of that is the size the file comes out at.

    The photograph is built here rather than kept as a fixture: an EXIF block is a dozen bytes of
    well-documented structure, and a binary file checked in is one nobody can read the contents of.
    """
    failures: list[str] = []
    fmt = STILL_FORMATS["jpeg"]
    plain = await _a_picture(work, "jpg", settings)

    # `Orientation` = 6: taken with the camera held upright, so it is stored on its side and every
    # viewer turns it a quarter clockwise before drawing it. 640 by 480 stored, 480 by 640 seen.
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8)
    entry = struct.pack(">H", 1) + struct.pack(">HHI", 0x0112, 3, 1) + struct.pack(">HH", 6, 0)
    exif = b"Exif\x00\x00" + tiff + entry + struct.pack(">I", 0)
    raw = plain.read_bytes()
    turned = work / "turned.jpg"
    turned.write_bytes(raw[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + raw[2:])

    probed = await _probe_json(orientation.orientation_args(turned, settings=settings))
    read = orientation.understand(probed)
    if read != orientation.Orientation(quarter_turns=1):
        failures.append(f"the camera's note was read as {read}, not a quarter turn clockwise")
        return failures
    print("ok   turned photo  the camera's note is read as a quarter turn")

    # The seen picture is 480 by 640. A rectangle over its top half is 480 by 320, and that is
    # only true if the turn happens BEFORE the crop. The other order cuts 480 by 320 out of a
    # picture that is 640 across and hands back something 320 by 480.
    cut = work / "turned-cropped-no-extension"
    await _run(
        operations.still_args(
            turned,
            cut,
            filters=[*read.filters(), operations.crop_filter(left=0, top=0, width=480, height=320)],
            fmt=fmt,
            settings=settings,
        )
    )
    measured = await _probe(cut, "v:0", "width,height")
    if not measured or measured[0] != "480,320":
        failures.append(
            f"a crop over a turned photograph came out {measured[0] if measured else 'unreadable'},"
            " not 480,320: the note is not being applied before the rectangle"
        )
    else:
        print("ok   turned photo  a rectangle is cut out of the picture as it is SEEN")
    return failures


async def _probe_json(argv: list[str]) -> dict[str, object]:
    """Whatever ffprobe said, parsed. Its own runner because it wants the output rather than the
    exit code, and because the argument list is the one the editor really uses."""
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await process.communicate()
    return json.loads(out or b"{}")  # type: ignore[no-any-return]


async def main() -> None:
    # The build Sift's own settings pick: vendor/bin's, in a checkout as in an installed copy.
    if not shutil.which(FFMPEG):
        raise SystemExit(
            f"no ffmpeg at {FFMPEG}: fetch the shipped tools with scripts/fetch_vendor.py"
        )
    version = await asyncio.create_subprocess_exec(
        FFMPEG, "-version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    banner, _ = await version.communicate()
    print(f"     {FFMPEG}: {banner.decode().splitlines()[0]}")

    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        settings = Settings(data_dir=work / "data", cache_dir=work / "cache")
        failures = await check(work, settings)
        failures += await _check_a_turned_photograph(work, settings)

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  {failure}")
        raise SystemExit(1)
    print("\ncompress: ok")


if __name__ == "__main__":
    asyncio.run(main())
