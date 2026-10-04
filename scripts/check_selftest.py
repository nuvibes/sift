# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the machine measurement's two ffmpeg commands with the ffmpeg that ships.

The measurement decides two instance-wide settings from a curve, and the curve is made of real
encodes. Everything about the WALK between levels is tested with the unit of work injected, which
is the right shape for the decisions, and it means the two commands underneath are the one part
of the feature no unit test exercises.

They are worth exercising because of how they fail. An argument this ffmpeg refuses does not report
itself as a wrong argument: the encode is caught, counted as work that did not finish, and the
level scores zero. A machine that encodes perfectly well is then measured as one that cannot, and
the recommendation that comes out is a real number derived from nothing. Nobody reading the screen
could tell.

Shortened deliberately. The real measurement runs a twenty-second clip at five widths and takes
about a minute; what has to be answered here is whether the commands are accepted and produce
files, not how fast this machine is.

    python scripts/check_selftest.py
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from sift.kernel.config import Settings, vendored_tool
from sift.slices.performance import selftest


async def check(work: Path, settings: Settings) -> list[str]:
    failures: list[str] = []

    clip = await selftest.build_clip(work, settings)
    if not clip.exists() or clip.stat().st_size == 0:
        failures.append("the clip every level encodes was not built")
        print("FAIL source        nothing was written")
        return failures
    print(f"ok   source        {clip.stat().st_size // 1024} KB")

    # Capped, which is the state every level of a real run uses. Two flags and the same flag twice:
    # one pool for decoding and the filter graph, another for the encoder.
    capped = await selftest._encode_once(clip, work, 0, settings, threads=2)
    written = (work / "encoded-0.mp4").exists() and (work / "encoded-0.mp4").stat().st_size > 0
    if not capped or not written:
        failures.append("a capped encode did not finish, so every level would score zero")
    print(f"{'FAIL' if not (capped and written) else 'ok  '} capped encode  threads=2")

    # And uncapped, which is what a caller that says nothing about threads gets.
    loose = await selftest._encode_once(clip, work, 1, settings)
    loose_written = (work / "encoded-1.mp4").exists() and (work / "encoded-1.mp4").stat().st_size
    if not loose or not loose_written:
        failures.append("an uncapped encode did not finish")
    print(f"{'FAIL' if not (loose and loose_written) else 'ok  '} plain encode   no thread cap")

    # A source that is not there has to come back as work that did not finish rather than as an
    # exception: one unit failing must not abandon the whole measurement.
    gone = await selftest._encode_once(work / "not-here.mp4", work, 2, settings)
    if gone is not False:
        failures.append("a missing source was reported as an encode that finished")
    print(f"{'FAIL' if gone is not False else 'ok  '} missing source reported as unfinished")

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
        # A second of 160 by 120 rather than twenty of 1280 by 720. The commands are the same ones.
        selftest.CLIP_SECONDS = 1
        selftest.CLIP_WIDTH = 160
        selftest.CLIP_HEIGHT = 120
        failures = await check(work, settings)

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  {failure}")
        raise SystemExit(1)
    print("\nself-test: ok")


if __name__ == "__main__":
    asyncio.run(main())
