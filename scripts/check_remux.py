# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repair a badly interleaved file with the ffmpeg that ships, and check what came out.

The repair is a stream copy: every packet is carried across untouched and only its position in the
container changes. That makes it look like the safest thing in the codebase, and it is the reason
this check exists anyway: a stream copy that goes wrong goes wrong quietly. ffmpeg reports
success for an output it wrote nothing into, so the failure is an empty file and an exit code of
zero, and the first anybody hears of it is a video that will not play.

It has to run with the ffmpeg that ships, the build in vendor/bin, which Sift's own settings find in
a checkout as they do in an installed copy. Two ffmpeg versions can disagree in exactly this way: an
argument that is a no-op on one silently discards the only frame on the next, writes nothing, and
exits 0. An ffmpeg behaviour confirmed on any other build is not confirmed.

The source is built here rather than checked in, because what has to be exercised is a file with
more than one audio track. `-map 0` is what carries them all across; without it ffmpeg keeps one
stream per type and drops the rest, and the output plays perfectly while a track nobody looked for
is gone. A fixture with a single video stream cannot tell those two apart.

    python scripts/check_remux.py
"""

from __future__ import annotations

import asyncio
import shutil
import struct
import tempfile
from pathlib import Path

from sift.kernel.config import Settings, vendored_tool
from sift.slices.media_jobs import ffmpeg

# One picture and two soundtracks, five seconds. Short enough to be quick, long enough that the
# muxer has to interleave rather than write one packet of each.
SOURCE_ARGS = [
    "-nostdin",
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    "-f",
    "lavfi",
    "-i",
    "testsrc=size=160x120:rate=15:duration=5",
    "-f",
    "lavfi",
    "-i",
    "sine=frequency=440:duration=5",
    "-f",
    "lavfi",
    "-i",
    "sine=frequency=880:duration=5",
    "-map",
    "0:v",
    "-map",
    "1:a",
    "-map",
    "2:a",
    "-c:v",
    "libx264",
    "-preset",
    "ultrafast",
    "-c:a",
    "aac",
]


async def run(args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, errors = await process.communicate()
    if process.returncode != 0:
        raise SystemExit(f"{args[0]} exited {process.returncode}: {errors.decode()[:400]}")


def first_boxes(path: Path, how_many: int = 3) -> list[str]:
    """The names of the first few top-level boxes, in the order they sit in the file.

    An MP4 is a sequence of length-prefixed boxes, and `+faststart` is the request to put the index
    (`moov`) in front of the media (`mdat`) rather than after it. Whether that happened is a fact
    about byte positions, which is why it is read here rather than asked of a probe.
    """
    names: list[str] = []
    with path.open("rb") as handle:
        while len(names) < how_many:
            header = handle.read(8)
            if len(header) < 8:
                break
            size = struct.unpack(">I", header[:4])[0]
            names.append(header[4:8].decode("ascii", "replace"))
            if size == 1:  # a 64-bit size follows the name
                size = struct.unpack(">Q", handle.read(8))[0]
                handle.seek(size - 16, 1)
            elif size == 0:  # runs to the end of the file
                break
            else:
                handle.seek(size - 8, 1)
    return names


async def check(work: Path, settings: Settings) -> list[str]:
    failures: list[str] = []

    source = work / "source.mkv"
    await run([settings.ffmpeg_path, *SOURCE_ARGS, str(source)])
    before = ffmpeg.parse_probe(await ffmpeg.run_json(ffmpeg.probe_args(source, settings=settings)))
    print(f"ok   source          {source.stat().st_size} bytes, {before.duration_ms} ms")

    destination = work / "repaired.mp4"
    await run(ffmpeg.remux_args(source, destination, settings=settings))

    # The failure this check exists for: ffmpeg says it succeeded and there is nothing in the file.
    if not destination.is_file() or destination.stat().st_size == 0:
        failures.append("the repair exited 0 and wrote nothing")
        return failures
    print(f"ok   wrote           {destination.stat().st_size} bytes")

    after = ffmpeg.parse_probe(
        await ffmpeg.run_json(ffmpeg.probe_args(destination, settings=settings))
    )
    if after.duration_ms is None or before.duration_ms is None:
        failures.append("the repaired copy has no duration")
    elif abs(after.duration_ms - before.duration_ms) > 250:
        failures.append(f"length changed: {before.duration_ms} ms -> {after.duration_ms} ms")
    if (after.width, after.height) != (before.width, before.height):
        failures.append(
            f"picture changed: {before.width}x{before.height} -> {after.width}x{after.height}"
        )
    if after.vcodec != before.vcodec:
        failures.append(f"the video was re-encoded: {before.vcodec} -> {after.vcodec}")
    print(
        f"ok   same picture    {after.width}x{after.height} {after.vcodec}, {after.duration_ms} ms"
    )

    # What `-map 0` buys. A repair that quietly drops the second soundtrack plays correctly.
    tracks = await ffmpeg.run_json(
        [
            settings.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(destination),
        ]
    )
    kinds = [stream.get("codec_type") for stream in tracks.get("streams", [])]
    wrong = kinds.count("audio") != 2 or kinds.count("video") != 1
    if wrong:
        failures.append(
            f"the copy carries {', '.join(kinds)}; the source carried video, audio, audio"
        )
    # "ok" only where it is ok. A line that says so beside a failure is how a log stops being read.
    print(f"{'FAIL' if wrong else 'ok  '} every stream    {len(kinds)} streams: {', '.join(kinds)}")

    boxes = first_boxes(destination)
    if "moov" not in boxes:
        failures.append(f"no index among the first boxes: {boxes}")
    elif "mdat" in boxes and boxes.index("moov") > boxes.index("mdat"):
        failures.append(f"the index sits behind the media: {boxes}")
    print(f"ok   index in front  {', '.join(boxes)}")

    return failures


async def main() -> None:
    ffmpeg = vendored_tool("ffmpeg")
    # The build Sift's own settings pick: vendor/bin's, in a checkout as in an installed copy.
    if not shutil.which(ffmpeg):
        raise SystemExit(
            f"no ffmpeg at {ffmpeg}: fetch the shipped tools with scripts/fetch_vendor.py"
        )
    version = await asyncio.create_subprocess_exec(
        ffmpeg, "-version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    banner, _ = await version.communicate()
    print(f"     {ffmpeg}: {banner.decode().splitlines()[0]}")

    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        settings = Settings(data_dir=work / "data", cache_dir=work / "cache")
        failures = await check(work, settings)

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  {failure}")
        raise SystemExit(1)
    print("\nremux: ok")


if __name__ == "__main__":
    asyncio.run(main())
