# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read an animated WebP with the tools that ship, and make the copy everything else reads.

The unit tests stand in for `webpinfo` and `anim_dump`, because what they exercise is the parsing
and the parsing is wrong or right regardless of what is installed. This is the other half, and it
is the half that cannot be faked: the tools are actually there, they actually read the file, and an
ordinary video actually comes out of the frames.

It has to run with the tools that ship, the libwebp build in vendor/bin, which Sift's own settings
find in a checkout as they do in an installed copy. The unit tests stand in for them, so nothing
there can tell a working install from one where the tools never arrived, and that failure is every
animated file in a library sitting on the importing shimmer: the exact fault this feature fixes.

    python scripts/check_animated_webp.py <corpus-directory>
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from sift.kernel import webp
from sift.kernel.config import Settings
from sift.kernel.ingress import Kind, Origin, verify_decodable, verify_ingress
from sift.slices.media_jobs import ffmpeg


async def check(source: Path, settings: Settings, work: Path) -> list[str]:
    failures: list[str] = []

    result = verify_ingress(source, origin=Origin.SCAN, settings=settings)
    if result.media.name != "webp-animated" or result.media.kind is not Kind.GIF:
        failures.append(f"the gate called it {result.media.name}, kind {result.media.kind}")
        return failures
    print(f"ok   gate            {result.media.name}")

    # The decoder check, which for this format asks webpinfo rather than ffprobe. A missing tool
    # shows up here first.
    await verify_decodable(result, settings=settings)
    print("ok   decodable")

    animation = await webp.inspect(source, settings=settings)
    if animation.frames < 2:
        failures.append(f"webpinfo reported {animation.frames} frame(s) in an animation")
    print(
        f"ok   webpinfo        {animation.width}x{animation.height}, "
        f"{animation.frames} frames, {animation.duration_ms} ms"
    )

    frames = await webp.dump_frames(source, work / "frames", settings=settings)
    if len(frames) != animation.frames:
        failures.append(
            f"anim_dump wrote {len(frames)} frames, webpinfo counted {animation.frames}"
        )
    print(f"ok   anim_dump       {len(frames)} frames")

    built = work / "readable.mp4"
    await ffmpeg.run(webp.encode_args(work / "frames", built, fps=animation.fps, settings=settings))
    if not built.is_file() or built.stat().st_size == 0:
        failures.append("the encode produced nothing")
        return failures

    # And it is really a video now, read by the ffprobe that ships, which is the whole point of
    # the conversion. Asserted rather than assumed: an encode can exit 0 having written a header.
    probed = ffmpeg.parse_probe(await ffmpeg.run_json(ffmpeg.probe_args(built, settings=settings)))
    if not probed.width or not probed.height:
        failures.append("the copy has no picture in it")
    print(f"ok   readable copy   {probed.width}x{probed.height} {probed.vcodec}")

    return failures


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <corpus-directory>")
    corpus = Path(sys.argv[1])
    source = corpus / "accepted_animated.webp"
    if not source.is_file():
        raise SystemExit(f"no animated WebP fixture at {source}")

    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        settings = Settings(data_dir=work / "data", cache_dir=work / "cache")
        # The gate moves what it rejects, and the corpus is the checkout's own fixtures.
        staged = work / source.name
        staged.write_bytes(source.read_bytes())
        failures = await check(staged, settings, work)

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  {failure}")
        raise SystemExit(1)
    print("\nanimated WebP: ok")


if __name__ == "__main__":
    asyncio.run(main())
