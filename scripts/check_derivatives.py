# SPDX-License-Identifier: AGPL-3.0-or-later
"""Make every fixture's derivatives with the shipped ffmpeg and check a file really came out."""

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
    """A photograph of ordinary size, made here: only the image2 demuxer shows the seek fault."""
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
    """A video of odd height below the preview ceiling, the shape x264 refuses."""
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
            # VP9, because x264 will not encode an odd height either.
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
    """Whether a hover clip could be made from this file, by the job's own command."""
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
    # The check the exit code cannot make: ffmpeg can report success having written nothing.
    return await asyncio.to_thread(lambda: destination.exists() and destination.stat().st_size > 0)


async def decodable_form(
    source: Path, checked: IngressResult, work: Path, settings: Settings
) -> Path:
    """The file a derivative job would actually read: an animated WebP is converted first."""
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
        # Only what Sift would ever hold: both halves of the gate.
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

        # And the moving one, for anything that moves.
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
