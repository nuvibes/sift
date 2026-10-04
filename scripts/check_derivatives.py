# SPDX-License-Identifier: AGPL-3.0-or-later
"""Make the derivatives of every fixture with the ffmpeg that ships, and check they came out.

This catches a kind of fault nothing else can, and it is worth being precise about why.

A thumbnail asked for with an input seek, `-ss 0` before `-i`, is the no-op it reads as on
ffmpeg 6. On ffmpeg 7 it is not: a photograph is decoded through the image2 demuxer, which presents
it as a video one frame long, and a seek to 0 lands ON that frame's timestamp rather than before
it. The frame is treated as already passed, and ffmpeg writes nothing and **exits 0**. No error, no
output.

Unit tests that read the argument list stay green through that, and a test machine with a
different ffmpeg cannot see it: the version the tests use is not the version anybody runs. This
runs with the ffmpeg that ships, the build in vendor/bin, which Sift's own settings find in a
checkout as they do in an installed copy, and asserts the only thing that actually matters: that a
picture came out.

A hover preview has a failure of the same shape. It is encoded by x264, which refuses an odd
height outright, and the scale expression pins one axis to the source's own height when the
source is shorter than the ceiling, which an ordinary library has plenty of. Both are "the command
is fine until the input is a shape the fixtures do not have", so the shapes are made here rather
than hoped for.

    python scripts/check_derivatives.py <corpus-directory>
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from sift.kernel import sampling, webp
from sift.kernel.config import Settings
from sift.kernel.ingress import (
    IngressRejected,
    IngressResult,
    Kind,
    Origin,
    verify_decodable,
    verify_ingress,
)
from sift.slices.media_jobs import ffmpeg


async def a_real_sized_still(work: Path, settings: Settings) -> Path:
    """A photograph, made here, because the corpus does not contain one.

    The fixtures are deliberately tiny (16x16, a few hundred bytes), and a JPEG that small is
    demuxed as `jpeg_pipe`, which reports no duration and has nothing for a seek to land past. The
    bug this script exists for needs the `image2` demuxer, which is what a jpeg of ordinary size
    gets, and which reports the single frame as lasting 0.04 s.

    So the corpus cannot show it and would have gone on not showing it. This is one ffmpeg call and
    it is the whole point of the check.
    """
    source = work / "photograph.jpg"
    await ffmpeg.run(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x480:rate=1:duration=1",
            "-frames:v",
            "1",
            str(source),
        ]
    )
    return source


async def an_odd_height_clip(work: Path, settings: Settings) -> Path:
    """A video whose height is an odd number and below the preview ceiling.

    x264 refuses an odd dimension ("height not divisible by 2 (720x405)"), and the preview's
    scale expression keeps the source's own height whenever the source is shorter than the ceiling.
    So the failure needs a clip that is BOTH odd and short, and every fixture in the corpus is
    neither. 405 is an ordinary height: 720x405 is 16:9 at 720 wide.
    """
    source = work / "odd-height.webm"
    await ffmpeg.run(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=720x405:rate=12:duration=2",
            # VP9, because x264 will not ENCODE an odd height either: the source for this test
            # cannot be made with the codec the test is about. VP9 in WebM allows it, Sift accepts
            # the container, and what is being checked is the preview command's own arithmetic.
            "-c:v",
            "libvpx-vp9",
            "-deadline",
            "realtime",
            "-cpu-used",
            "8",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ]
    )
    return source


async def previewed(source: Path, destination: Path, settings: Settings) -> bool:
    """Whether a hover clip could be made from this file. False for anything with no timeline.

    Cut into the pieces the preview job cuts, from the file's own length and the default shape, so
    the command is the one the job runs rather than one built for this check.
    """
    try:
        answer = await ffmpeg.run(ffmpeg.probe_args(source, settings=settings), capture=True)
        probed = ffmpeg.parse_probe(json.loads(answer))
        pieces = sampling.preview_segments(
            sampling.picture_span(probed.duration_ms, probed.video_duration_ms),
            sampling.preview_shape(sampling.DEFAULT_PREVIEW_SHAPE),
        )
        await ffmpeg.run(
            ffmpeg.preview_args(
                source, destination, pieces=pieces, encoder=ffmpeg.Encoder.CPU, settings=settings
            )
        )
    except Exception as failure:
        print(f"     {type(failure).__name__}: {failure}")
        return False
    return await asyncio.to_thread(lambda: destination.exists() and destination.stat().st_size > 0)


async def rendered(source: Path, destination: Path, settings: Settings) -> bool:
    """Whether asking for this file's still actually produced one."""
    try:
        await ffmpeg.run(
            ffmpeg.thumbnail_args(source, destination, timestamp_ms=0, settings=settings)
        )
    except Exception as failure:
        print(f"     {type(failure).__name__}: {failure}")
        return False
    # The check that the exit code cannot make. ffmpeg reporting success having written nothing is
    # the entire failure this script exists for. Off the loop, because a stat is not worth a thread
    # and this script is one file at a time by design.
    return await asyncio.to_thread(lambda: destination.exists() and destination.stat().st_size > 0)


async def decodable_form(
    source: Path, checked: IngressResult, work: Path, settings: Settings
) -> Path:
    """The file a derivative job would actually read.

    For everything but one format that is the file itself. An animated WebP is converted first,
    because ffmpeg cannot read one at all, so drawing a thumbnail straight from the original is
    not a stricter version of what the application does, it is a thing the application never does.
    """
    if checked.media.name != "webp-animated":
        return source
    animation = await webp.inspect(source, settings=settings)
    frames = work / f"{source.stem}-frames"
    await webp.dump_frames(source, frames, settings=settings)
    readable = work / f"{source.stem}-readable.mp4"
    await ffmpeg.run(webp.encode_args(frames, readable, fps=animation.fps, settings=settings))
    return readable


async def check(corpus: list[Path], settings: Settings, work: Path) -> list[str]:
    failures = []
    odd = await an_odd_height_clip(work, settings)
    sources = [await a_real_sized_still(work, settings), odd, *corpus]

    for index, original in enumerate(sources):
        source = original
        # Only what Sift would ever hold. A file the gate turns away never reaches a derivative job,
        # and asking ffmpeg to draw a frame from a deliberately corrupt fixture proves nothing. Both
        # halves of the gate, because the byte check admits files the decode check then refuses.
        try:
            checked = verify_ingress(source, origin=Origin.SCAN, settings=settings)
            await verify_decodable(checked, settings=settings)
        except IngressRejected:
            continue
        source = await decodable_form(source, checked, work, settings)

        destination = work / f"{index}.jpg"
        ok = await rendered(source, destination, settings)
        print(
            f"{'ok  ' if ok else 'FAIL'} {original.name:32} "
            f"{'drew a frame' if ok else 'drew nothing'}"
        )

        # And the moving one, for anything that moves. A still has no timeline and no preview is
        # made for it, so asking for one proves nothing about either.
        if ok and checked.media.kind is not Kind.IMAGE:
            clip = await previewed(source, work / f"{index}.mp4", settings)
            print(
                f"{'ok  ' if clip else 'FAIL'} {original.name:32} "
                f"{'made a preview' if clip else 'made no preview'}"
            )
            if not clip:
                failures.append(f"{original.name} (preview)")

        if not ok:
            failures.append(original.name)
    return failures


def main(corpus_directory: Path) -> int:
    corpus = sorted(corpus_directory.iterdir())
    if not corpus:
        print(f"no fixtures in {corpus_directory}")
        return 1

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        settings = Settings(data_dir=root / "data", cache_dir=root / "cache")
        settings.quarantine_dir.mkdir(parents=True)
        work = root / "work"
        work.mkdir()

        failures = asyncio.run(check(corpus, settings, work))

    if failures:
        print(f"\n{len(failures)} fixture(s) produced no still with the shipped ffmpeg: {failures}")
        return 1

    print("\nthe shipped ffmpeg drew a still for every fixture it was given")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: check_derivatives.py <corpus-directory>")
    raise SystemExit(main(Path(sys.argv[1])))
