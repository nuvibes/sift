# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which download tools this Sift runs, what version each one is, and whether yt-dlp is behind.

Settings > Downloads draws one row per tool from this. Two questions, answered two different ways:

*What is running* is asked of the tools themselves (`--version`, the same as a person would type),
once per process, the first time a screen asks, and kept. Not at boot: four launches on every start
for a screen most starts never open is cost with nobody on the other end of it. Not on every read
either: the answer cannot change under a running Sift, because the release ships the tools and a
release is a restart.

*Is there a newer yt-dlp* is asked of yt-dlp's public release feed, ONLY when a person presses the
button that asks it. It is never checked on a timer, on a page load or at boot: it is the one thing
here that opens a connection to the internet, and unattended outbound work is not something Sift
does on its own. It says "newer available" and does nothing else. Sift does not update yt-dlp in
place: a new yt-dlp arrives with a new Sift, pinned and checked like every other tool it ships.

Everything that goes wrong here is an answer rather than an error: a tool that will not start is
reported as not answering, a feed that cannot be read as "could not check". This is a screen of
facts about the install, and a fact that is not known is still something to say.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Annotated, Literal

import aiohttp
from fastapi import APIRouter, Depends
from pydantic import ConfigDict

from sift.kernel import chromaprint
from sift.kernel.access import Viewer
from sift.kernel.config import get_settings, vendored_tool
from sift.kernel.http import read_capped
from sift.kernel.log import get_logger
from sift.kernel.subprocess import SubprocessError, run
from sift.kernel.wire import Wire
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.download.sources.net import guarded_session
from sift.slices.download.sources.tuning import GALLERYDL_BINARY, JS_RUNTIME, YTDLP_BINARY

log = get_logger(__name__)

router = APIRouter(tags=["download"])

#: yt-dlp's public release feed. A fixed address with nothing appended: the check sends nothing
#: about this install, so a feed fetched identically by everybody cannot count or recognise anyone.
YTDLP_RELEASE_FEED = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"

#: How long a tool may take to say its own version. yt-dlp's one-folder build answers in about a
#: third of a second; this is a ceiling for a machine under load, not an estimate.
VERSION_TIME_LIMIT_SECONDS = 30.0

#: How long the pressed check waits on the feed, and how much of its reply it reads. The document is
#: a few tens of kilobytes; the cap is there so a wrong answer cannot be an unbounded one.
FEED_TIMEOUT_SECONDS = 10.0
MAX_FEED_BYTES = 512 * 1024

#: The engine line in yt-dlp's own debug header: `[debug] JS runtimes: quickjs-ng-0.17.0`, or
#: `none` when it found nothing it can use.
_ENGINE_LINE = re.compile(r"JS runtimes:\s*(?P<found>\S.*)$", re.M)

#: How yt-dlp spells an engine it found, name then version, and the name a person reads for it.
_ENGINE_NAMES = {
    "quickjs-ng": "QuickJS-NG",
    "quickjs": "QuickJS",
    "deno": "Deno",
    "node": "Node.js",
    "bun": "Bun",
}

#: ffmpeg's own first line: `ffmpeg version n7.1.5-16-g9a4bb2c579-20260816 Copyright ...`.
_FFMPEG_VERSION = re.compile(r"^ffmpeg version (?P<version>\S+)")


class DownloadTool(Wire):
    """One tool the downloader runs, as this install runs it."""

    #: Which tool: `ffmpeg`, `yt-dlp`, `gallery-dl` or `js-runtime`.
    key: str
    #: What it is called. For the engine, the one yt-dlp actually found.
    name: str
    #: The version it gave for itself, or None when it would not answer: not installed, or not
    #: starting. A version is never guessed at.
    version: str | None
    #: True when this is the copy Sift ships, False when it is whatever the machine has.
    shipped: bool


class DownloadTools(Wire):
    """Every download tool, in the order Settings draws them."""

    tools: list[DownloadTool]


class LatestAsked(Wire):
    """Which tool to look up. Only yt-dlp has a release feed Sift reads: it is the one that goes
    stale in weeks, where the others move in months and ship with Sift's own updates."""

    model_config = ConfigDict(extra="forbid")

    tool: Literal["yt-dlp"]


class DownloadToolRelease(Wire):
    """What pressing "check" found out about one tool."""

    key: str
    #: The version Sift runs, as it said it.
    running: str | None
    #: The newest release the publisher lists, or None when the feed could not be read.
    latest: str | None
    #: Whether `latest` is a later release than `running`. False whenever either is unknown: an
    #: unreadable version is not evidence that anything is newer.
    newer: bool


@dataclass(frozen=True, slots=True)
class _Said:
    """What one launch of a tool printed, both streams, or None when it would not start."""

    text: str | None


async def _ask(argv: list[str]) -> _Said:
    """Run a tool for what it prints about itself. Never raises."""
    try:
        done = await run(argv, time_limit=VERSION_TIME_LIMIT_SECONDS)
    except SubprocessError as exc:
        log.info("download.tools.no_answer", tool=argv[0], reason=str(exc))
        return _Said(None)
    return _Said(
        done.stdout.decode("utf-8", "replace") + "\n" + done.stderr.decode("utf-8", "replace")
    )


def _first_line(said: _Said) -> str | None:
    """The first line with anything on it, which, for `--version`, is the version."""
    if said.text is None:
        return None
    return next((line.strip() for line in said.text.splitlines() if line.strip()), None)


def read_engine(said: str | None) -> tuple[str, str | None]:
    """The engine yt-dlp found, as (name, version), from its debug header.

    ("none", None) when it said it found none, and ("unknown", None) when it did not say at all:
    a yt-dlp that would not start, or one that stopped printing the line. Those two are different
    answers and are kept apart: "none" is a fact about the install and "unknown" is not.
    """
    if said is None:
        return "unknown", None
    match = _ENGINE_LINE.search(said)
    if match is None:
        return "unknown", None
    # `none` is yt-dlp's own word for having found nothing, and falls through as itself below.
    found = match["found"].strip().split(",")[0].strip()
    # Longest name first, so `quickjs-ng-0.17.0` is not read as `quickjs` version `ng-0.17.0`.
    for spelled in sorted(_ENGINE_NAMES, key=len, reverse=True):
        if found.startswith(f"{spelled}-"):
            return _ENGINE_NAMES[spelled], found[len(spelled) + 1 :]
    return found, None


def ffmpeg_version(banner: str) -> str | None:
    """The version out of ffmpeg's first line, or None if that line is not ffmpeg's."""
    match = _FFMPEG_VERSION.match(banner.strip())
    return match["version"] if match else None


def _is_shipped(path: str, name: str) -> bool:
    """Whether `path` is the copy Sift ships. The bare name is the machine's own, by definition."""
    return path != name and path == vendored_tool(name)


async def _measure() -> DownloadTools:
    """Ask every tool what it is. Four launches at once; see the module note for why this is rare."""
    settings = get_settings()
    banner, ytdlp, gallerydl, engine = await asyncio.gather(
        # The SAME answer the fingerprinting pass records against every track it reads, rather than
        # a second launch of the same question: one ffmpeg, one version, read once per process.
        chromaprint.tool_version(settings),
        _ask([YTDLP_BINARY, "--version"]),
        _ask([GALLERYDL_BINARY, "--version"]),
        # With no address yt-dlp prints its debug header and stops with a usage error: the header
        # is the only place it says which engine it found, so the exit code is not the answer here.
        _ask([YTDLP_BINARY, "-v", "--js-runtimes", JS_RUNTIME]),
    )
    engine_name, engine_version = read_engine(engine.text)
    return DownloadTools(
        tools=[
            DownloadTool(
                key="ffmpeg",
                name="ffmpeg",
                version=ffmpeg_version(banner),
                shipped=_is_shipped(settings.ffmpeg_path, "ffmpeg"),
            ),
            DownloadTool(
                key="yt-dlp",
                name="yt-dlp",
                version=_first_line(ytdlp),
                shipped=_is_shipped(YTDLP_BINARY, "yt-dlp"),
            ),
            DownloadTool(
                key="gallery-dl",
                name="gallery-dl",
                version=_first_line(gallerydl),
                shipped=_is_shipped(GALLERYDL_BINARY, "gallery-dl"),
            ),
            DownloadTool(
                key="js-runtime",
                name=engine_name,
                version=engine_version,
                # Shipped when the engine yt-dlp found is the one beside it. yt-dlp finding SOME
                # engine on a machine that has its own is not the same thing, and says so.
                shipped=engine_version is not None and _is_shipped(vendored_tool("qjs"), "qjs"),
            ),
        ]
    )


class _Measured:
    """The one answer, per process. A lock so two screens opening at once launch the tools once."""

    def __init__(self) -> None:
        self._answer: DownloadTools | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> DownloadTools:
        async with self._lock:
            if self._answer is None:
                self._answer = await _measure()
            return self._answer

    def forget(self) -> None:
        """Drop the kept answer. For a test that changes what the tools say."""
        self._answer = None


MEASURED = _Measured()


def _calendar(version: str | None) -> tuple[int, ...] | None:
    """yt-dlp's date version as numbers, or None. `2026.08.19` and `2026.8.19` are one release."""
    if not version:
        return None
    parts = version.strip().lstrip("v").split(".")
    if len(parts) < 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def is_newer(latest: str | None, running: str | None) -> bool:
    """Whether `latest` is a later yt-dlp than `running`. False if either will not read."""
    later, now = _calendar(latest), _calendar(running)
    return later is not None and now is not None and later > now


async def fetch_latest_ytdlp() -> str | None:
    """The newest yt-dlp the publisher lists, or None if the feed could not be read.

    Through the guarded session every outbound read in Sift takes, so the name behind the fixed
    address is still held to the public-only rule. Every failure is one answer ("could not check"),
    because a person's only choice after it is the same whichever went wrong.
    """
    try:
        async with (
            # Sift's own release feed, not a Site: it goes out on the machine's own address on
            # purpose, said here so the route gate can see it was decided and not forgotten.
            guarded_session(proxy=None) as session,
            session.get(
                YTDLP_RELEASE_FEED,
                timeout=aiohttp.ClientTimeout(total=FEED_TIMEOUT_SECONDS),
                headers={"Accept": "application/json"},
            ) as response,
        ):
            if response.status != 200:
                return None
            body = await read_capped(response.content, MAX_FEED_BYTES)
        tag = json.loads(body).get("tag_name")
    except Exception:
        # Deliberately everything: see the module note. Nothing about the failure is worth more to
        # the person pressing the button than "could not check", and none of it reaches a screen.
        log.info("download.tools.check_failed")
        return None
    return tag.strip() if isinstance(tag, str) and tag.strip() else None


@router.get("/download-tools")
async def download_tools(
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DownloadTools:
    """The download tools this install runs, and the version of each.

    Admin, like the rest of the downloader. It reaches nothing outside this machine.
    """
    del viewer
    return await MEASURED.get()


@router.post("/download-tools/latest", dependencies=[Depends(csrf_protect)])
async def check_latest(
    asked: LatestAsked,
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DownloadToolRelease:
    """Whether a tool has a newer release than the one Sift runs. Pressed by a person, never run on
    its own (it is the one request here that leaves this machine), and it changes nothing."""
    del viewer
    running = next(tool.version for tool in (await MEASURED.get()).tools if tool.key == asked.tool)
    latest = await fetch_latest_ytdlp()
    return DownloadToolRelease(
        key=asked.tool, running=running, latest=latest, newer=is_newer(latest, running)
    )


__all__ = [
    "MEASURED",
    "YTDLP_RELEASE_FEED",
    "DownloadTool",
    "DownloadToolRelease",
    "DownloadTools",
    "LatestAsked",
    "check_latest",
    "download_tools",
    "fetch_latest_ytdlp",
    "ffmpeg_version",
    "is_newer",
    "read_engine",
    "router",
]
