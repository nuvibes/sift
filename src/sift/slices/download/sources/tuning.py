# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixed knobs for the downloader tools: constants, not settings, so none is one to get wrong."""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.config import vendored_tool

#: The shipped copy where there is one: a machine's own may be months stale.
YTDLP_BINARY = vendored_tool("yt-dlp")
GALLERYDL_BINARY = vendored_tool("gallery-dl")


@dataclass(frozen=True, slots=True)
class Pacing:
    """How hard Sift leans on a site; passed on every run, since a tool's default moves."""

    #: Both tools default to none, and unpaced bulk traffic loses accounts.
    seconds_between_requests: float
    #: Sift retries the whole download too, so these multiply.
    retries: int
    timeout_seconds: float
    wait_after_too_many_requests: float
    bytes_per_second: int | None
    wait_chosen: bool = False


PACING = Pacing(
    seconds_between_requests=0.5,
    retries=3,
    timeout_seconds=30.0,
    wait_after_too_many_requests=60.0,
    bytes_per_second=None,
)


@dataclass(frozen=True, slots=True)
class Filters:
    """Size bounds in bytes, applied to the size the site reports; an unsized file passes."""

    at_least_bytes: int | None
    at_most_bytes: int | None


FILTERS = Filters(at_least_bytes=None, at_most_bytes=None)


#: H.264 in an mp4 decodes in hardware everywhere, though it often stops at 1080p.
QUALITY_COMPATIBLE = "compatible"

#: Needs the shipped JavaScript engine: these are the formats a site puts a challenge on.
QUALITY_BEST = "best"


@dataclass(frozen=True, slots=True)
class RunPolicy:
    """Everything decided about one tool run, as one object so none is inherited by accident."""

    pacing: Pacing = PACING
    filters: Filters = FILTERS
    quality: str = QUALITY_COMPATIBLE
    verbose: bool = False


POLICY = RunPolicy()

#: A tool's own user agent is the first thing a site refuses.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SUBPROCESS_TIMEOUT_SECONDS = 600.0

JS_RUNTIME = "quickjs"

#: Older ones take minutes on a challenge, which looks like a hanging download.
JS_RUNTIME_NG_MIN_VERSION = "0.12.0"
