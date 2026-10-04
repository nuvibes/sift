# SPDX-License-Identifier: AGPL-3.0-or-later
"""The GIF commands, run rather than read.

A `-filter_complex` with an unlabelled first pad passes every argument check and makes ffmpeg
refuse to start, so this runs the vendored ffmpeg and puts the output to Sift's ingress gate. The
source is five generated seconds: a clip too short is written as a single still.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.ingress import Kind, Origin, verify_ingress
from sift.slices.media_edit import operations

pytestmark = [pytest.mark.integration]


def _a_real_clip(ffmpeg: str, into: Path) -> Path:
    """Five seconds of moving picture, made by the ffmpeg that will encode it."""
    source = into / "source.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
        timeout=180,
    )  # fmt: skip
    return source


@pytest.mark.parametrize("chosen", ["gif", "webp", "avif"])
def test_the_command_runs_and_what_it_writes_is_a_gif(
    chosen: str, settings: Settings, tmp_path: Path
) -> None:
    """The command runs, and the ingress gate reads the result as `Kind.GIF`, not a still."""
    source = _a_real_clip(settings.ffmpeg_path, tmp_path)
    fmt = operations.GIF_FORMATS[chosen]
    made = tmp_path / f"made.{fmt.extension}"

    done = subprocess.run(
        operations.gif_args(
            source,
            made,
            start_ms=1_000,
            duration_ms=3_000,
            fmt=fmt,
            landscape=True,
            settings=settings,
        ),
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert done.returncode == 0, f"ffmpeg refused the command: {done.stderr.strip()[-400:]}"
    assert made.exists() and made.stat().st_size > 0, (
        "ffmpeg exited 0 and wrote nothing, which is the hardest kind of failure to attribute"
    )

    checked = verify_ingress(made, origin=Origin.SCAN, settings=settings)
    assert checked.media.kind is Kind.GIF, (
        f"Sift files a {chosen} made this way as {checked.media.name!r}, which is a still picture: "
        "the library would draw one frozen frame"
    )


def test_the_short_edge_is_the_one_that_gets_capped(settings: Settings, tmp_path: Path) -> None:
    """The short edge is the one capped, checked on the picture."""
    source = _a_real_clip(settings.ffmpeg_path, tmp_path)
    fmt = operations.GIF_FORMATS["gif"]
    made = tmp_path / "wide.gif"

    subprocess.run(
        operations.gif_args(
            source, made, start_ms=0, duration_ms=2_000, fmt=fmt, landscape=True, settings=settings
        ),
        check=True,
        capture_output=True,
        timeout=300,
    )

    probed = subprocess.run(
        [
            settings.ffprobe_path, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(made),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )  # fmt: skip
    width, height = (int(one) for one in probed.stdout.strip().split("x"))

    # The source is 640x360, so the short edge is the height and it is what the number caps.
    assert height == 480
    assert width > height, "a landscape source came out portrait, so the axes were swapped"
