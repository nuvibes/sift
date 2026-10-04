# SPDX-License-Identifier: AGPL-3.0-or-later
"""Building the exact command line each tool is run with.

One function per tool, each returning the argv list `subproc.run` will execute. Written out in full
here so the command a tool receives is a thing that can be read, reviewed, and pinned by a test,
rather than assembled at the call site where a flag is easy to get wrong and impossible to see.

Two things every command shares, and each earns its place. The output goes into a directory the
caller owns, so whatever the tool names its file lands somewhere already confined. And filenames are
restricted to a safe character set, because the name comes from a remote site and an unrestricted
one can carry separators and dots that walk out of that directory.

The URL is always the last argument and always a single element of the list. It is never formatted
into a string, so a URL full of shell metacharacters is one argument the tool reads as a URL, not a
line a shell reads as commands.

**Who owns what, on a seam where both sides have an opinion.** These tools are not passive: each has
its own ideas about pacing, retries and timeouts, and each will act on them if not told otherwise.
Two sides with an opinion and no knowledge of each other produce behaviour nobody designed, so it is
written down here, where both are configured:

* **where the files go**: Sift's, absolutely. The tools write into a directory Sift owns and every
  file then goes through the one import path. A template that let a tool choose its own destination
  would go around the containment that directory exists to provide;
* **which files are wanted**: Sift's, from what was pasted. A playlist is enumerated and queued an
  item at a time rather than handed to a tool as one job;
* **what has already been downloaded**: Sift's ledger, and only it. The tools offer their own
  record of that and it is deliberately not used: a second answer to that question is a second
  answer that will disagree with the first;
* **which addresses a tool may reach**: Sift's, on every connection. Every run is handed the
  kernel's tool proxy (`kernel.public_net.TOOL_PROXY`), which refuses a destination on this device
  or its private network; a Site routed through a tunnel is chained through it to the tunnel's;
* **how hard to lean on a site**: Sift decides, the tool applies. This is the one that has to be
  split, because a gallery is hundreds of requests inside a process Sift can only start and wait
  for. Sift cannot pace what it cannot see, so it passes the number down, on every run, rather than
  leaving four inherited defaults spread across two dependencies.
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
    """How a decided value becomes an option.

    Four shapes and no fifth, and that is the point: a value becomes a number, a size, a switch, or
    nothing at all. There is no shape that turns typed text into an option, which is what makes the
    list below a boundary rather than a convenience.
    """

    #: The value, as it prints. Seconds, counts.
    NUMBER = "number"
    #: A count of bytes. Passed as plain digits rather than "50M": both tools read digits as bytes,
    #: and a suffix is a second spelling of the same number for no gain.
    SIZE = "size"
    #: The option alone when true, and nothing when false.
    SWITCH = "switch"
    #: A number, or nothing at all when there is none. For the settings whose "off" is genuinely the
    #: absence of a limit rather than a number meaning none.
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class Concern:
    """One thing Sift has an opinion about, and the option each tool takes it as.

    Written out as a table rather than typed into each command, because this is the seam where two
    sides both have an opinion and neither knows about the other. Sift decides the value; the tool
    is the only side that can apply it to a request it makes inside its own run. Naming the pair
    here is what keeps that arrangement legible, and what lets a test prove no run goes out with
    one of them left to whatever the tool felt like.
    """

    #: Where the value sits on the policy, as a dotted path from its root.
    value: str
    #: The option, per tool. None where a tool genuinely has no such option, said out loud,
    #: because "cannot be told" and "somebody forgot" look identical in a list otherwise.
    ytdlp: str | None
    gallerydl: str | None
    shape: Shape = Shape.NUMBER


#: Every concern Sift owns on this seam. A field added to the policy and not added here fails a test.
CONCERNS: tuple[Concern, ...] = (
    Concern(
        "pacing.seconds_between_requests", ytdlp="--sleep-requests", gallerydl="--sleep-request"
    ),
    # The same wait, before each file. Both tools' own help says the option above spaces requests
    # "during data extraction" only, so without this an album's files would go out unspaced while
    # Sift's own fetch spaces every request: one setting, both kinds of request.
    Concern("pacing.seconds_between_requests", ytdlp="--sleep-interval", gallerydl="--sleep"),
    Concern("pacing.retries", ytdlp="--retries", gallerydl="--retries"),
    Concern("pacing.timeout_seconds", ytdlp="--socket-timeout", gallerydl="--http-timeout"),
    # yt-dlp has no option for this: it answers a "too many requests" through its retry handling
    # rather than a wait of its own, so there is nothing to pass and nothing to fix.
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
    # Both tools spell it the same, and it is the one setting here that changes what comes back on
    # the error stream rather than what is fetched. The classifier reads that stream, so this is
    # pinned by a test rather than assumed harmless.
    Concern("verbose", ytdlp="--verbose", gallerydl="--verbose", shape=Shape.SWITCH),
)


#: Everything Sift sets itself, on every run, that is not a decided value. Named here so the gate
#: below has one list to check a built command against.
#:
#: These are not preferences and none of them is reachable from a screen. `--output` and
#: `--directory` in particular are the staging containment: they decide where a tool writes, and a
#: template that let somebody else decide that would go around the one import path every file takes.
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


#: The browser yt-dlp pretends to be for a Site that refuses its own client. The family rather than
#: a version: yt-dlp picks the newest Chrome target its build carries, so the handshake keeps up with
#: the tool rather than being pinned to one a site may start refusing as stale.
IMPERSONATE_TARGET = "chrome"


def _as_a_browser(wanted: bool) -> list[str]:
    """`--impersonate chrome` when the Site's record says yt-dlp must connect as a browser.

    A flag handed in rather than looked up here: this module builds commands from typed values and
    reads nothing, and the Site catalog imports the progress reader that imports this module, so
    reading the catalog from here is an import cycle as well as a second source of a fact. The
    callers already hold the record. See `SiteRecord.impersonate` for why it is a measured fact
    about the Site rather than a setting.
    """
    return ["--impersonate", IMPERSONATE_TARGET] if wanted else []


#: What yt-dlp is asked to print as it goes, and the whole of what Sift reads from its stdout.
#:
#: The leading word is a marker: it makes a progress line unmistakable in a stream that also carries
#: whatever else the tool decides to print, so nothing has to be guessed at from shape. Then the
#: bytes so far, the total, and the total's estimate: the last because a fragmented download only
#: learns its real total at the end and reports an estimate until then, and an estimate is a far
#: better answer than no bar at all.
#:
#: Any field that is not known prints as `NA`, which the reader treats as unknown rather than as a
#: number. That is a documented behaviour of the template, not an accident of formatting.
PROGRESS_TEMPLATE = (
    "download:sift-progress %(progress.downloaded_bytes)s %(progress.total_bytes)s "
    "%(progress.total_bytes_estimate)s"
)

#: The marker the line above begins with, so the reader and the template cannot disagree about it.
PROGRESS_MARKER = "sift-progress"

#: What the tool knew about each file, written by the tool itself beside the files: one JSON
#: object a line, once each file has reached its final name (`after_move`). It is how `{id}`,
#: `{title}` and `{posted}` reach a name on every site yt-dlp reads.
#:
#: Not the staged name, although that carries the ID: the name is `--restrict-filenames` spelling
#: capped at 80 bytes, so a title read back out of it has lost its spaces and its end, and a
#: posting date added to it would change the part-file a paused download resumes from. So the name
#: stays exactly as it was and the facts travel in a file of their own, in fields the tool
#: documents: `id`, `title`, `upload_date` (a day, as 20260912), `timestamp` (a moment, in seconds),
#: `extractor_key` (which reader answered) and `filepath` (the file those facts belong to).
#:
#: A `.json` name, so the listing of what the tool produced (`subproc.output_files`) already passes
#: it by as a sidecar rather than handing it to the import as media. The vendored yt-dlp writes one
#: line per finished file, and the progress lines above still print.
TOOL_FACTS_FILE = "sift-facts.json"
TOOL_FACTS_TEMPLATE = "after_move:%(.{id,title,upload_date,timestamp,extractor_key,filepath})j"

#: The reader yt-dlp falls back to for an address no site reader claims: a plain file link. Its
#: "ID" and "title" are the file's own stem and its "upload date" is whatever `Last-Modified` the
#: server sent, so none of the three is a fact about a post, and none is kept.
_NO_SITE_READER = "Generic"


#: Every option any command this module builds may contain. Nothing else may appear, ever.
#:
#: **This is the security boundary of the downloader.** Both tools carry `--exec`, and both accept
#: a configuration file chosen by whoever passes the option, so a free-text flag field is not a
#: convenience with a sharp edge, it is running whatever somebody types, as the server, by the tools'
#: own design. An admin session is not a licence for that: it turns a stolen session or a
#: cross-site request into code on the machine rather than a mess in a library.
#:
#: So options are named here and assembled from typed values. There is no path from text somebody
#: typed to an element of a command line: the only value that comes from outside is the URL, and
#: it goes last, after the `--` that ends the options.
ALLOWED_OPTIONS: frozenset[str] = _SIFT_OWNED | {
    flag for concern in CONCERNS for flag in (concern.ytdlp, concern.gallerydl) if flag is not None
}


#: Named refusals, with the reason each is refused, so a later reader finds an answer rather than an
#: absence. A test proves none of these can appear in a built command.
#:
#: They are not merely "not in the allow-list": several are options a reasonable person would think
#: to add, and the reason not to is not obvious from the option's name.
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
            # A size is handed over whole: a rate or a limit with a fraction on it is not a thing
            # a tool takes. `value` is walked out of the policy by name, so it arrives untyped and
            # the cast says what a SIZE concern is.
            options += [
                flag,
                str(int(cast(float, value)) if concern.shape is Shape.SIZE else value),
            ]
    return options


#: How yt-dlp is asked to order the formats a site offers, per quality answer.
#:
#: A SORT and never a filter. `-S` reorders what is on offer and falls through to the best available
#: when the preferred kind is not there, so a video published only as VP9 still downloads, as itself,
#: never re-encoded. A hard `-f` on one format id is the wrong shape entirely: it fails the download
#: outright when that one format is refused, which on the big video sites happens per format and
#: unpredictably.
_SORT_BY_QUALITY = {
    # Compatibility first. `vcodec:h264` is prepended to yt-dlp's own order, so it outranks
    # resolution, and because the H.264 ladder stops at 1080p on the big video sites, this is
    # also what caps those downloads at 1080p. That is the trade, and it is the default because
    # changing it would silently alter what every existing install fetches.
    "compatible": "vcodec:h264,ext:mp4",
    # Resolution first, codec last. Whatever the site offers at the top, which may be AV1 or VP9.
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
    """The command that fetches a video with yt-dlp into `dest_dir`.

    The output template writes `<title> [<id>].<ext>` inside the directory, with restricted
    filenames so nothing a site chose can contain a path separator. The id is for the staging
    directory only: Sift takes it off before the file reaches the library (`naming.without_tool_id`). A cookies file is added only when one is
    supplied: a saved site login, already decrypted to a temporary file by the caller.

    `policy` is everything Sift has decided about how the run behaves. It is an argument rather than
    something read in here, so where the decisions come from (a constant, a settings row) is the
    caller's business and not this module's.
    """
    argv = [
        YTDLP_BINARY,
        "--output",
        # The title and the id, and the id never reaches the library.
        #
        # The title, because the naming template offers a token documented as "the name the file
        # already had, which is usually the site's own title", and a template of the id alone gives
        # a bare number there. It is capped in bytes rather than characters because the
        # limit a filesystem enforces is bytes. `--restrict-filenames` below decides what characters
        # may survive, so nothing here can produce a separator, and it turns every space into an
        # underscore, which is what lets Sift find the id again: the one space in the name is the
        # one in front of the bracket.
        #
        # The id, because this directory needs it and the library does not. The tool skips a
        # destination already there ("has already been downloaded"), so under a title-only template
        # a post carrying two clips of one title keeps the first and silently drops the second;
        # and a paused download resumes from
        # its part-file only while the template still names that part-file. The staged file is
        # renamed before the import, with the id taken off and a taken name given the next number
        # (`naming.without_tool_id`, `naming.rename`), so the library holds `<title>.<ext>`.
        #
        # The id is capped at forty bytes. On a plain file URL there is no extractor and no id, so
        # yt-dlp uses the filename stem: a 119-character one, twice over, gives a 272-character
        # path against Windows' 260, reported as `unable to open for writing: [Errno 2] No such
        # file or directory`.
        str(dest_dir / "%(title).80B [%(id).40B].%(ext)s"),
        "--no-playlist",
        "--no-warnings",
        # Progress, in a shape Sift chose. This is not reading the tool's chatter: the template says
        # exactly which fields appear and in which order, so what comes back is an interface Sift
        # declared and a test pins, rather than whatever this version prints by default.
        #
        # It goes to STDOUT, and that is what makes it safe: failures are classified from STDERR,
        # which this does not touch. `--newline` stops the progress line being rewritten in place:
        # without it the tool overwrites one line with carriage returns and a reader that splits on
        # newlines buffers an entire download into a single line.
        "--newline",
        "--progress-template",
        PROGRESS_TEMPLATE,
        # What the tool knew about each file it finished, for the naming words. See
        # `TOOL_FACTS_FILE`: the file is inside the directory, so it is confined with the rest.
        "--print-to-file",
        TOOL_FACTS_TEMPLATE,
        str(dest_dir / TOOL_FACTS_FILE),
        # Some sites answer a download with a JavaScript challenge, and yt-dlp needs an engine to
        # run it in. Without one it does not fail cleanly: individual formats come back 403 while
        # others from the same video succeed, which reads as the site being unreliable. The engine
        # ships in the image and is named here because yt-dlp will not reach for it otherwise: only
        # its first choice is automatic, and that one is not packaged for the base image.
        "--js-runtimes",
        JS_RUNTIME,
        # How the formats a site offers are ordered, from the quality preference. A SORT and never a
        # filter. See the table it comes from. Merging is left to yt-dlp: an H.264+AAC pair lands
        # in mp4 on its own, and forcing the container would break the fallback instead.
        "-S",
        _SORT_BY_QUALITY.get(policy.quality, _SORT_BY_QUALITY["compatible"]),
        "--restrict-filenames",
        # Continue a part-file this job already has, which is what PAUSING a download leaves in the
        # workspace. It is yt-dlp's default and it is passed anyway, because the pause feature
        # DEPENDS on it: a default nothing states is a behaviour one release of somebody else's
        # tool can change without anything here noticing, and what it would look like is downloads
        # that quietly start from zero when they are resumed. Verified against the built command by
        # a test, which is the only form of "it is the default" that stays true.
        #
        # Nothing else this module passes turns it off: `--no-part`, `--no-continue` and
        # `--force-overwrites` are in neither the Sift-owned list above nor any decided concern,
        # and the allow-list refuses everything that is not in one of those two.
        "--continue",
        "--user-agent",
        DEFAULT_USER_AGENT,
        *_decided(policy, "ytdlp"),
        *_as_a_browser(as_a_browser),
    ]
    if cookies_file is not None:
        argv += ["--cookies", str(cookies_file)]
    argv += _through_the_proxy(proxy)
    # `--` ends the options, so a URL that begins with `-` is read as the URL and never as a flag.
    # The scheme guard already refuses a non-http(s) address, but this does not lean on that: a
    # future caller that skips it still cannot turn a link into a tool option.
    argv += ["--", url]
    return argv


def _through_the_proxy(route: str | None) -> list[str]:
    """`--proxy` naming the tool proxy, on every run: direct, or chained to a tunnel's `route`.

    Always present, because the tool proxy is what keeps a tool off this device and its private
    network. A tunnel is not handed to the tool directly: the tool is pointed at the tool proxy and
    the tool proxy at the tunnel, so the same refusal holds on both ways out.
    """
    return ["--proxy", TOOL_PROXY.address_for(route)]


def proxy_traffic(command: list[str]) -> Traffic | None:
    """What the run of `command` sent through the tool proxy, including what it was refused.

    A refused connection reaches the tool as a 403 from its proxy, which the tool reports in words
    that read like a site refusing a login. Asking here says what actually happened.
    """
    if "--proxy" not in command:
        return None
    return TOOL_PROXY.traffic_of(command[command.index("--proxy") + 1])


def tool_name_facts(dest_dir: Path) -> dict[str, NameFacts]:
    """What yt-dlp said about each file it finished in `dest_dir`, by the file's name.

    Reads the disk, so a caller on the event loop runs it in a thread. Empty when the tool wrote no
    such file (gallery-dl never does, and a run that finished nothing has nothing to say), and a
    line that does not read is passed over rather than trusted: this is the tool's output, and a
    name is a convenience that must never be what fails a download.
    """
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
    """When the post went up: the moment where the tool knew it, the day where it knew only that.

    The moment first, because a day is a UTC day and the name is written on this device's clock:
    a video posted at 22:00 in New York has an `upload_date` of the NEXT day. Where the moment is
    not known the day is used as it came, which is what the site said and no more.
    """
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
    """The command that asks what is behind a playlist or a channel, without fetching any of it.

    `--flat-playlist` stops yt-dlp opening each entry to find out about it, so a channel of hundreds
    answers in about a second and nothing is downloaded. Printing the addresses rather than the ids
    keeps this honest about what happens next: each one is queued as its own download, so one video
    failing does not take the rest with it, and each has its own progress, its own retry and its own
    place in the ledger.

    Paced like any other run. It is one request, but it is a request to the same site that is about
    to receive a great many more.
    """
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

    `--directory` confines every file the tool writes to the directory the caller owns.

    This is the run that most needs pacing: one gallery is one process making hundreds of requests,
    and Sift can see the process start and finish and nothing in between.

    It is NOT run quiet. This tool has no progress template to ask for, so the only thing it can
    report is which file it is on, which it prints, one line each, on stdout. That is worth having:
    on a gallery of four hundred images "212 of 400" is a better answer than a byte count would be
    anyway, and quiet would suppress exactly that. Failures are still read from stderr, which this
    does not change; only asking for `--verbose` does, and that is pinned by a test.

    A file already in the directory is SKIPPED, which is how a paused download resumes here: the
    tool's default is to leave what it finds alone, and nothing turns that off: `--no-skip` is in
    neither the Sift-owned list nor any decided concern, and the allow-list refuses every option
    that is in neither. So a run asked for the same gallery again fetches what is missing and
    nothing else. There is no flag to pass for it, which is why this says so instead.
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
    # `--` ends the options, so a URL that begins with `-` is read as the URL and never as a flag.
    # The scheme guard already refuses a non-http(s) address, but this does not lean on that: a
    # future caller that skips it still cannot turn a link into a tool option.
    argv += ["--", url]
    return argv
