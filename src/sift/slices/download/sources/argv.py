# SPDX-License-Identifier: AGPL-3.0-or-later
"""The exact command line each tool is run with, written out so a test can pin it.

Sift owns where files go, which files are wanted, what was already downloaded and which
addresses a tool may reach; it decides how hard to lean on a Site and the tool applies it.
The URL is always the last argument, one element, after `--`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

from sift.kernel.public_net import TOOL_PROXY, Traffic
from sift.slices.download.sources.resolved import NameFacts
from sift.slices.download.sources.tuning import (
    DEFAULT_USER_AGENT,
    GALLERYDL_BINARY,
    JS_RUNTIME,
    POLICY,
    QUALITY_BEST,
    YTDLP_BINARY,
    RunPolicy,
)


class Shape(StrEnum):
    """How a decided value becomes an option; no shape turns typed text into one."""

    NUMBER = "number"
    #: Bytes, as plain digits, which both tools read as bytes.
    SIZE = "size"
    SWITCH = "switch"
    #: A number, or nothing where off is the absence of a limit.
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class Concern:
    """One thing Sift decides, and the option each tool takes it as."""

    value: str
    #: None where a tool has no such option, said so it is not mistaken for forgotten.
    ytdlp: str | None
    gallerydl: str | None
    shape: Shape = Shape.NUMBER


#: Every concern Sift owns on this seam; a policy field missing here fails a test.
CONCERNS: tuple[Concern, ...] = (
    Concern(
        "pacing.seconds_between_requests", ytdlp="--sleep-requests", gallerydl="--sleep-request"
    ),
    # The same wait before each file: the option above spaces only extraction requests.
    Concern("pacing.seconds_between_requests", ytdlp="--sleep-interval", gallerydl="--sleep"),
    Concern("pacing.retries", ytdlp="--retries", gallerydl="--retries"),
    Concern("pacing.timeout_seconds", ytdlp="--socket-timeout", gallerydl="--http-timeout"),
    Concern("pacing.wait_after_too_many_requests", ytdlp=None, gallerydl="--sleep-429"),
    Concern(
        "pacing.bytes_per_second",
        ytdlp="--limit-rate",
        gallerydl="--limit-rate",
        shape=Shape.OPTIONAL,
    ),
    Concern(
        "filters.at_least_bytes",
        ytdlp="--min-filesize",
        gallerydl="--filesize-min",
        shape=Shape.SIZE,
    ),
    Concern(
        "filters.at_most_bytes",
        ytdlp="--max-filesize",
        gallerydl="--filesize-max",
        shape=Shape.SIZE,
    ),
    # It changes the error stream the classifier reads, so a test pins it.
    Concern("verbose", ytdlp="--verbose", gallerydl="--verbose", shape=Shape.SWITCH),
)


#: Everything Sift sets itself on every run; `--output` and `--directory` are the containment.
_SIFT_OWNED: frozenset[str] = frozenset(
    {
        "--output",
        "--directory",
        "--no-playlist",
        "--no-warnings",
        "--newline",
        "--progress-template",
        "--print-to-file",
        "--js-runtimes",
        "-S",
        "--restrict-filenames",
        "--continue",
        "--user-agent",
        "--cookies",
        "--proxy",
        "--flat-playlist",
        "--print",
        "--impersonate",
    }
)


#: The family, not a version, so yt-dlp picks the newest target its build carries.
IMPERSONATE_TARGET = "chrome"


def _as_a_browser(wanted: bool) -> list[str]:
    """`--impersonate chrome` when the Site's record says yt-dlp must connect as a browser."""
    return ["--impersonate", IMPERSONATE_TARGET] if wanted else []


#: What yt-dlp prints as it goes: a marker, bytes so far, the total and its estimate.
#: An unknown field prints as `NA`.
PROGRESS_TEMPLATE = (
    "download:sift-progress %(progress.downloaded_bytes)s %(progress.total_bytes)s "
    "%(progress.total_bytes_estimate)s"
)

PROGRESS_MARKER = "sift-progress"

#: What the tool knew about each finished file, one JSON object a line, for `{id}`,
#: `{title}` and `{posted}`; the staged name stays as it was so a paused download resumes.
TOOL_FACTS_FILE = "sift-facts.json"
TOOL_FACTS_TEMPLATE = "after_move:%(.{id,title,upload_date,timestamp,extractor_key,filepath})j"

#: yt-dlp's reader for a plain file link: its id, title and date describe no post.
_NO_SITE_READER = "Generic"


#: Every option a built command may contain: the downloader's security boundary, since
#: both tools can run commands. Nothing typed becomes an option; only the URL comes from outside.
ALLOWED_OPTIONS: frozenset[str] = _SIFT_OWNED | {
    flag for concern in CONCERNS for flag in (concern.ytdlp, concern.gallerydl) if flag is not None
}


#: Named refusals with their reasons; a test proves none appears in a built command.
REFUSED: dict[str, str] = {
    "--exec": "runs an arbitrary command on the machine, which is not a download setting by any "
    "reading. This is the option the whole allow-list exists for",
    "--exec-after": "runs an arbitrary command once a download finishes, which is the same thing "
    "with a delay in front of it",
    "--config-locations": "reads a configuration file chosen by whoever passed the option, and such "
    "a file can carry every option refused here",
    "--config": "the same for the other tool: a file of options, chosen by whoever passed it",
    "--config-yaml": "the same again, in another format, which is why it is listed separately",
    "--prefer-insecure": "deliberately drops to an unencrypted connection, in an application whose "
    "whole posture is the opposite",
    "--username": "stores a password rather than a session, walks straight into two-factor, and on "
    "exactly the sites that matter is the fastest way to get an account locked. Logins are cookies",
    "--password": "stores the password half of the same thing, with the same consequences",
    "--twofactor": "a one-time code has no place in stored settings; it is stale seconds later",
    "--download-archive": "a second record of what has been downloaded, which will disagree with "
    "the ledger. There is one answer to that question and it is Sift's",
    "--paths": "decides where the tool writes, which is the staging containment every file's one "
    "way into the library depends on",
    "--destination": "the other tool's way of deciding where it writes. Note this is NOT "
    "--directory, which Sift sets itself and which confines the tool to one folder",
    "--load-info-json": "reads a file describing what to fetch, chosen by whoever passed it, so it "
    "is a way to point a download at something nobody typed into the box",
}


def _leaf(policy: RunPolicy, path: str) -> object:
    """One decided value, by its dotted path from the policy root."""
    value: object = policy
    for part in path.split("."):
        value = getattr(value, part)
    return value


def _decided(policy: RunPolicy, tool: str) -> list[str]:
    """The options that hand one tool everything Sift has decided, always all of them."""
    options: list[str] = []
    for concern in CONCERNS:
        flag = getattr(concern, tool)
        if flag is None:
            continue
        value = _leaf(policy, concern.value)
        if concern.shape is Shape.SWITCH:
            if value:
                options.append(flag)
        elif value is not None:
            options += [
                flag,
                str(int(cast(float, value)) if concern.shape is Shape.SIZE else value),
            ]
    return options


#: yt-dlp's format order per quality answer: a sort (`-S`), never a filter that fails outright.
_SORT_BY_QUALITY = {
    # H.264 outranks resolution, which caps the big video sites at 1080p; the default.
    "compatible": "vcodec:h264,ext:mp4",
    QUALITY_BEST: "res,fps,vcodec:av01,ext:mp4",
}


def build_ytdlp_argv(
    url: str,
    dest_dir: Path,
    *,
    cookies_file: Path | None = None,
    proxy: str | None = None,
    policy: RunPolicy = POLICY,
    as_a_browser: bool = False,
) -> list[str]:
    """The command that fetches a video with yt-dlp into `dest_dir`."""
    argv = [
        YTDLP_BINARY,
        "--output",
        # The title and the id, both capped in bytes (Windows' 260-character path).
        # The id keeps two clips of one title apart; it is taken off before the library.
        str(dest_dir / "%(title).80B [%(id).40B].%(ext)s"),
        "--no-playlist",
        "--no-warnings",
        # Progress in a shape Sift declared, on stdout; failures are read from stderr.
        "--newline",
        "--progress-template",
        PROGRESS_TEMPLATE,
        "--print-to-file",
        TOOL_FACTS_TEMPLATE,
        str(dest_dir / TOOL_FACTS_FILE),
        # An engine for a Site's JavaScript challenge, which yt-dlp will not reach for itself.
        "--js-runtimes",
        JS_RUNTIME,
        "-S",
        _SORT_BY_QUALITY.get(policy.quality, _SORT_BY_QUALITY["compatible"]),
        "--restrict-filenames",
        # Its default, stated because pausing depends on it; nothing passed here turns it off.
        "--continue",
        "--user-agent",
        DEFAULT_USER_AGENT,
        *_decided(policy, "ytdlp"),
        *_as_a_browser(as_a_browser),
    ]
    if cookies_file is not None:
        argv += ["--cookies", str(cookies_file)]
    argv += _through_the_proxy(proxy)
    # A URL beginning with `-` is read as the URL, never as a flag.
    argv += ["--", url]
    return argv


def _through_the_proxy(route: str | None) -> list[str]:
    """`--proxy` naming the tool proxy on every run, chained to a tunnel's `route` where given."""
    return ["--proxy", TOOL_PROXY.address_for(route)]


def proxy_traffic(command: list[str]) -> Traffic | None:
    """What the run of `command` sent through the tool proxy, including what was refused."""
    if "--proxy" not in command:
        return None
    return TOOL_PROXY.traffic_of(command[command.index("--proxy") + 1])


def tool_name_facts(dest_dir: Path) -> dict[str, NameFacts]:
    """What yt-dlp said about each file it finished, by name; an unreadable line is skipped."""
    try:
        text = (dest_dir / TOOL_FACTS_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    found: dict[str, NameFacts] = {}
    for line in text.splitlines():
        try:
            said = json.loads(line)
        except ValueError:
            continue
        if not isinstance(said, dict) or said.get("extractor_key") == _NO_SITE_READER:
            continue
        where = said.get("filepath")
        if not isinstance(where, str) or not where:
            continue
        found[Path(where).name] = NameFacts(
            id=_text(said.get("id")),
            title=_text(said.get("title")),
            posted=_posted(said.get("timestamp"), said.get("upload_date")),
        )
    return found


def _text(value: object) -> str | None:
    """A field the tool gave as text, or None for anything else, including an empty one."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _posted(timestamp: object, upload_date: object) -> datetime | date | None:
    """When the post went up: the moment where known (a UTC day can be tomorrow), else the day."""
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and timestamp > 0:
        try:
            return datetime.fromtimestamp(timestamp, UTC)
        except (OverflowError, OSError, ValueError):
            pass
    if isinstance(upload_date, str) and len(upload_date) == 8 and upload_date.isdigit():
        try:
            return datetime.strptime(upload_date, "%Y%m%d").date()
        except ValueError:
            return None
    return None


def build_enumerate_argv(
    url: str,
    *,
    policy: RunPolicy = POLICY,
    proxy: str | None = None,
    as_a_browser: bool = False,
) -> list[str]:
    """The command that lists what is behind a playlist or channel without fetching it."""
    return [
        YTDLP_BINARY,
        "--flat-playlist",
        "--print",
        "%(url)s",
        "--no-warnings",
        "--user-agent",
        DEFAULT_USER_AGENT,
        *_decided(policy, "ytdlp"),
        *_as_a_browser(as_a_browser),
        *_through_the_proxy(proxy),
        "--",
        url,
    ]


def build_gallerydl_argv(
    url: str,
    dest_dir: Path,
    *,
    cookies_file: Path | None = None,
    proxy: str | None = None,
    policy: RunPolicy = POLICY,
) -> list[str]:
    """The command that fetches an image gallery with gallery-dl into `dest_dir`.

    Not run quiet: its per-file lines are the only progress it can give. A file already there
    is skipped, which is how a paused download resumes.
    """
    argv = [
        GALLERYDL_BINARY,
        "--directory",
        str(dest_dir),
        "--user-agent",
        DEFAULT_USER_AGENT,
        *_decided(policy, "gallerydl"),
    ]
    if cookies_file is not None:
        argv += ["--cookies", str(cookies_file)]
    argv += _through_the_proxy(proxy)
    # A URL beginning with `-` is read as the URL, never as a flag.
    argv += ["--", url]
    return argv
