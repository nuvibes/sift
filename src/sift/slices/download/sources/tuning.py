# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixed knobs for the downloader tools.

Constants, not settings: the binaries are the ones the release ships, the user agent is a plain
browser's because a tool's default is what a site blocks first, and the run timeout frees a stuck
tool's worker. Nothing here is a knob a self-hoster needs, so none is one to get wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.config import vendored_tool

#: The two downloaders, run as subprocesses, never imported: the shipped copy (pinned in
#: scripts/vendor_manifest.json) where there is one, since a machine's own may be months stale and
#: fail; the bare name for a checkout that has not fetched them.
YTDLP_BINARY = vendored_tool("yt-dlp")
GALLERYDL_BINARY = vendored_tool("gallery-dl")


@dataclass(frozen=True, slots=True)
class Pacing:
    """How hard Sift is willing to lean on a site, as one set of numbers for every downloader.

    Sift decides them and the tools enforce them, since Sift cannot slow requests inside a tool's
    process. Passed on EVERY run, even where they match the tool's default: a default left in place
    is behaviour nobody chose, and it moves when the tool updates.
    """

    #: Seconds between requests inside a tool's run: both tools default to none, and unpaced bulk
    #: traffic is the fastest way to lose an account.
    seconds_between_requests: float
    #: A tool's own retries per request. Sift retries the whole download too, so these multiply.
    retries: int
    #: How long to wait on a connection that is not answering.
    timeout_seconds: float
    #: The wait after a site says "too many requests": long, because it just said so.
    wait_after_too_many_requests: float
    #: The most a download may take of the connection, in bytes a second, or None for no cap. It
    #: caps BYTES for the household, where the interval paces REQUESTS for the site. None is the one
    #: value not passed on: neither tool caps bandwidth unless told to.
    bytes_per_second: int | None
    #: Whether the rate-limit wait was CHOSEN: a chosen wait raises a middleman service's own floor,
    #: and the default raises nothing, or it would stretch a short limit to a minute
    #: (`ratelimit.middleman_backoff`).
    wait_chosen: bool = False


#: The pacing a fresh install starts from, and what a stored setting that will not read as a number
#: falls back to.
PACING = Pacing(
    seconds_between_requests=0.5,
    retries=3,
    timeout_seconds=30.0,
    wait_after_too_many_requests=60.0,
    bytes_per_second=None,
)


@dataclass(frozen=True, slots=True)
class Filters:
    """Sizes a download is not worth making, in bytes. Absent means no bound at that end.

    Applied to the size the SITE REPORTS, so a site announcing no size passes both, which the
    settings' help says.
    """

    #: Smaller is not worth fetching: a tracking pixel, a thumbnail served in place of the thing.
    at_least_bytes: int | None
    #: Larger is left alone. Not a disk guard: the free-space floor watches what really arrives.
    at_most_bytes: int | None


#: No bounds. A downloader that silently drops files by default would be a downloader nobody trusts.
FILTERS = Filters(at_least_bytes=None, at_most_bytes=None)


#: The default: H.264 in an mp4 plays and decodes in hardware everywhere. On the big video sites
#: that ladder stops at 1080p, a trade the setting says out loud; changing the default would change
#: what every install fetches.
QUALITY_COMPATIBLE = "compatible"

#: The highest a site offers, whatever codec: needs the shipped JavaScript engine, since those
#: formats are the ones a site hands a challenge over.
QUALITY_BEST = "best"


@dataclass(frozen=True, slots=True)
class RunPolicy:
    """Everything Sift has decided about how one tool run behaves, as one thing to pass.

    One object, so a caller cannot pass some of the decisions and inherit the rest.
    """

    pacing: Pacing = PACING
    filters: Filters = FILTERS
    #: Which quality answer above. A word, not a format selector, which could pick a format the
    #: site then refuses.
    quality: str = QUALITY_COMPATIBLE
    #: Whether the tools explain themselves: a diagnostic that multiplies a long queue's output.
    verbose: bool = False


#: What a run uses when nothing has been read from settings: the tests, and any build with no
#: preferences wired.
POLICY = RunPolicy()

#: A plain desktop-browser user agent. A tool's own default is the first thing a site refuses.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: How long a tool may run before it is killed and its worker freed.
SUBPROCESS_TIMEOUT_SECONDS = 600.0

#: The JavaScript engine yt-dlp runs a site's challenge in, by yt-dlp's name for it, so the command
#: and the check of what yt-dlp can see ask for one engine. It ships beside yt-dlp as `qjs.exe`
#: (QuickJS-NG), and scripts/fetch_vendor.py refuses a release where yt-dlp sees none.
JS_RUNTIME = "quickjs"

#: The oldest QuickJS-NG worth shipping: older ones solve a challenge in minutes, which looks like
#: a hanging download. yt-dlp names this the first without the slow path.
JS_RUNTIME_NG_MIN_VERSION = "0.12.0"
