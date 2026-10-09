# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which download tools this Sift runs, their versions, and whether yt-dlp is behind.

Versions are asked once per process; the release feed only when a person presses check."""

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

#: Nothing appended, so the check cannot count or recognise an install.
YTDLP_RELEASE_FEED = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"

VERSION_TIME_LIMIT_SECONDS = 30.0

FEED_TIMEOUT_SECONDS = 10.0
MAX_FEED_BYTES = 512 * 1024

_ENGINE_LINE = re.compile(r"JS runtimes:\s*(?P<found>\S.*)$", re.M)

_ENGINE_NAMES = {
    "quickjs-ng": "QuickJS-NG",
    "quickjs": "QuickJS",
    "deno": "Deno",
    "node": "Node.js",
    "bun": "Bun",
}

_FFMPEG_VERSION = re.compile(r"^ffmpeg version (?P<version>\S+)")


class DownloadTool(Wire):
    """One tool the downloader runs, as this install runs it."""

    key: str
    name: str
    #: Never guessed: None when it would not answer.
    version: str | None
    shipped: bool


class DownloadTools(Wire):
    """Every download tool, in the order Settings draws them."""

    tools: list[DownloadTool]


class LatestAsked(Wire):
    """Which tool to look up: only yt-dlp, the one that goes stale in weeks."""

    model_config = ConfigDict(extra="forbid")

    tool: Literal["yt-dlp"]


class DownloadToolRelease(Wire):
    """What pressing "check" found out about one tool."""

    key: str
    running: str | None
    latest: str | None
    #: False whenever either version is unknown.
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
    """The engine yt-dlp found, as (name, version); "none" is a fact, "unknown" is not."""
    if said is None:
        return "unknown", None
    match = _ENGINE_LINE.search(said)
    if match is None:
        return "unknown", None
    found = match["found"].strip().split(",")[0].strip()
    # Longest first, so `quickjs-ng-0.17.0` is not `quickjs` version `ng-0.17.0`.
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
    """Ask every tool what it is: four launches together, once per process."""
    settings = get_settings()
    banner, ytdlp, gallerydl, engine = await asyncio.gather(
        # The same answer the fingerprinting pass records, not a second launch.
        chromaprint.tool_version(settings),
        _ask([YTDLP_BINARY, "--version"]),
        _ask([GALLERYDL_BINARY, "--version"]),
        # The debug header is the only place it names its engine; the exit code is a usage error.
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
                # Only when the engine found is the one shipped beside yt-dlp.
                shipped=engine_version is not None and _is_shipped(vendored_tool("qjs"), "qjs"),
            ),
        ]
    )


class _Measured:
    """The one answer, per process. A lock so two screens opening together launch the tools once."""

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
    """The newest yt-dlp the publisher lists over the guarded session, or None."""
    try:
        async with (
            # Sift's own feed, not a Site: the machine's own address on purpose.
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
        log.info("download.tools.check_failed")
        return None
    return tag.strip() if isinstance(tag, str) and tag.strip() else None


@router.get("/download-tools")
async def download_tools(
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DownloadTools:
    """The download tools this install runs, and the version of each."""
    del viewer
    return await MEASURED.get()


@router.post("/download-tools/latest", dependencies=[Depends(csrf_protect)])
async def check_latest(
    asked: LatestAsked,
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DownloadToolRelease:
    """Whether a tool has a newer release; pressed by a person, never run on its own."""
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
