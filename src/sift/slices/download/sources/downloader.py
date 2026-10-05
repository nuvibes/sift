# SPDX-License-Identifier: AGPL-3.0-or-later
"""The seam the download job fetches through: a URL in, files on disk out.

This is the whole of what the rest of the slice knows about how media is fetched. It resolves the URL
into the media behind it, then fetches each piece the way that piece needs: a direct address
streamed straight to disk, or a subprocess tool run on the source URL. The job that calls it never
learns which happened, and a test stands a fake in its place rather than reaching the network.

A direct address is pulled by the streaming fetcher over the guarded session, so it is vetted and the
socket pinned before a byte moves. A subprocess item is handed to the right tool, and a tool that
exits non-zero is read, not echoed: its output is logged through the ordinary redaction (which keeps
the URL, this being an admin's own log, and removes the account name in a home path), and what the
failure was (a login needed, a post gone, a run that did not finish) is decided from the phrasing that
survives. The person reading the queue gets a sentence they can act on.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import aiohttp

from sift.kernel.log import get_logger
from sift.kernel.urls import without_signature
from sift.slices.download.sources import (
    argv,
    cookie_health,
    failures,
    fetcher,
    progress,
    ratelimit,
    subproc,
)
from sift.slices.download.sources.errors import (
    NO_ANSWER_MESSAGE,
    PRIVATE_NETWORK_REFUSED,
    CookiesNeeded,
    DownloadError,
    FetchFailed,
    LoginRequired,
    NoAnswer,
    NothingFound,
    PrivateNetworkRefused,
    UnsupportedURL,
)
from sift.slices.download.sources.hosts import source_host
from sift.slices.download.sources.net import guarded_session
from sift.slices.download.sources.policy import PolicyReader
from sift.slices.download.sources.registry import Backend, chosen_backend, classify, match_site
from sift.slices.download.sources.resolve import resolve
from sift.slices.download.sources.resolved import (
    Fetched,
    NameFacts,
    ResolvedItem,
    item_name_facts,
    stable_key,
    tool_file_name_facts,
)
from sift.slices.download.sources.sites import catalog
from sift.slices.download.sources.tuning import POLICY, RunPolicy

#: Given a site key (or None for an address the catalog does not know), the tool that Site
#: was pointed at by hand, or None to leave it to Sift. Read live, per download, for the reason
#: every other live read in this slice is: a change made while a queue drains should reach the next
#: download rather than the next restart.
DownloaderReader = Callable[[str | None], Awaitable[str | None]]

#: The code a download held for cookies before it started carries: its Site is declared to need
#: them for the method in force, so nothing was asked of the Site at all.
CODE_COOKIES_REQUIRED = "cookies-required"

#: Asks whether one piece of media behind this link has already been fetched and kept. Supplied by
#: the caller that owns the ledger, so nothing here reads a database.
ItemCheck = Callable[[str], Awaitable[bool]]

log = get_logger(__name__)

#: Anything that is not a plain, safe filename character. A name arrives from a remote site, so this
#: is a permit list rather than a list of things to strip: a separator or a run of dots that got
#: through would let the staged file land somewhere other than the directory the caller owns.
#:
#: A space and brackets are permitted, the set `kernel.naming._UNSAFE` and `capture.pipeline.safe_name`
#: already permit: neither can move a file out of a directory, and the staged name is what
#: `{name}` fills from. Without them a page titled "A title" would land as `Someone - A_title.mp4`,
#: every space of the site's title turned into an underscore beside a template's own " - ".
_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._ ()-]+")

#: Long enough to stay recognizable, short enough that a name plus a suffix clears every filesystem
#: limit with room to spare.
_MAX_STEM = 80


def stage_name(item: ResolvedItem) -> str:
    """What one directly-fetched item is called on the way in.

    Not the item's index: that is its position within its source link, **0 for anything that is
    not a carousel**, so every ordinary download would stage as `0.mp4`. Three rules, in order:

    * the name the resolver supplied, cleaned: Instagram and the site extractors read a real one
      out of the response, and it is the only name here with any meaning to a person;
    * failing that, the last segment of the URL, which for most CDNs is the file's own name;
    * failing that, a short digest of the URL. Opaque, but stable: the same address re-fetched on a
      resumed job stages as the same file rather than as a second copy.

    The index is appended only when it is not zero. It is there so the photos of a carousel cannot
    collide when a site gives every one of them the same name, and leaving it off the common case
    is the point.
    """
    stem = _clean_stem(item.filename) or _clean_stem(_last_segment(item.url))
    if not stem:
        stem = hashlib.sha256(item.url.encode("utf-8")).hexdigest()[:12]
    if item.index:
        stem = f"{stem}-{item.index}"
    return f"{stem}{item.ext}"


def _bytes_already_here(dest: Path) -> int:
    """How much of this file a previous run left behind, or zero. Reads the disk, so off the loop.

    Zero for the ordinary case, which is every download that has not been paused: the workspace is
    the job's own and nothing else writes into it.
    """
    return dest.stat().st_size if dest.exists() else 0


def _last_segment(url: str) -> str:
    """The final path segment of a URL, percent-decoding intact. Query and fragment are dropped:
    they are not part of a name, and a signed CDN link's query is longer than the name itself."""
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def _clean_stem(name: str | None) -> str:
    """A name reduced to something that can only ever be a filename, with its extension removed.

    The extension goes because the caller appends the one the resolver negotiated, which is the one
    that matches the bytes: a remote `.jpg` on a file that is really a video would otherwise ride
    through into the library. Leading dots are stripped as well, so a name cannot arrive hidden.
    """
    if not name:
        return ""
    cleaned = re.sub(r" {2,}", " ", _UNSAFE_IN_NAME.sub("_", name)).strip("._ ")
    return Path(cleaned).stem[:_MAX_STEM].strip("._ ")


# What a tool's failure was really about, read from its output. Login is checked first: a private
# post that also 404s to a logged-out client is a login problem, not a missing one.
_LOGIN_MARKERS = re.compile(
    r"(?i)(\b401\b|\b403\b|log[\s-]?in|sign[\s-]?in|authenticat|forbidden|"
    r"private|not\s+available.*account|login\s+required|rate.?limit|\b429\b)"
)
_NOTHING_MARKERS = re.compile(
    r"(?i)(\b404\b|not\s+found|unavailable|deleted|removed|no\s+video|no\s+media|"
    r"unable\s+to\s+extract)"
)
# A site no tool Sift ships can read. yt-dlp says "Unsupported URL", gallery-dl "no suitable
# extractor". This is permanent (the same address is just as unsupported next time), so it must
# be told apart from a run that merely did not finish, which retries. Without this the tool's
# "Unsupported URL" would fall through to the generic message and the job would retry three times
# over, telling the person to "try again in a little while" for something that could never work.
_UNSUPPORTED_MARKERS = re.compile(
    r"(?i)(unsupported\s+url|no\s+suitable\s+extractor|is\s+not\s+a\s+supported\s+(site|url))"
)
# A file a tool left out for its size. Both tools exit 0 and write nothing, and say so in their
# own words: yt-dlp "File is larger than max-filesize (300000 bytes > 102400 bytes). Aborting." on
# its output, gallery-dl "File size larger than allowed maximum (300000 > 102400)" on its errors.
_SIZE_SKIP = re.compile(
    r"(?i)File\s+is\s+(?P<way>larger|smaller)\s+than\s+(?:max|min)-filesize\s+"
    r"\((?P<size>\d+)\s+bytes\s+[<>]\s+(?P<bound>\d+)\s+bytes\)"
    r"|File\s+size\s+(?P<way2>larger|smaller)\s+than\s+allowed\s+(?:maximum|minimum)\s+"
    r"\((?P<size2>\d+)\s+[<>]\s+(?P<bound2>\d+)\)"
)


def _size_skip(said: str) -> fetcher.Skipped | None:
    """The last file a tool said it left out for its size, as Sift's own sentence, or None."""
    found = list(_SIZE_SKIP.finditer(said))
    if not found:
        return None
    last = found[-1]
    way = last.group("way") or last.group("way2")
    size = int(last.group("size") or last.group("size2"))
    bound = int(last.group("bound") or last.group("bound2"))
    return fetcher.skipped_for_size(size, bound, larger=way.lower() == "larger")


#: How a Site says a signed address has run out: refused, or gone. Read only for an item that
#: names its page, so a refusal of any other file keeps its own meaning.
_STALE_SIGNATURE_CODES = frozenset({"http-403", "http-410"})


# A tool that gave up because the Site went quiet. It has already made its own `--retries` asks,
# each costing a whole `--socket-timeout`, so another attempt of the job would pay them all again.
# Read from the last lines only, where a tool says why it stopped, so a timeout it recovered from
# earlier in the run does not decide it.
_NO_ANSWER_MARKERS = re.compile(r"(?i)(timed\s+out|read\s*timeout|connect\s*timeout)")
#: How many of a failed run's last lines are read for `_NO_ANSWER_MARKERS`.
_NO_ANSWER_TAIL = 5


def _command(
    backend: Backend,
    url: str,
    into: Path,
    *,
    cookies_file: Path | None,
    proxy: str | None,
    policy: RunPolicy,
) -> list[str]:
    """The command one tool is run with for one address.

    yt-dlp's carries `--impersonate` for a Site whose record says it refuses the tool's own client
    (`SiteRecord.impersonate`), read here, from the record for the address being fetched, because
    this is the one place every yt-dlp download command is built.
    """
    if backend is Backend.YTDLP:
        record = match_site(url)
        return argv.build_ytdlp_argv(
            url,
            into,
            cookies_file=cookies_file,
            proxy=proxy,
            policy=policy,
            as_a_browser=record is not None and record.impersonate,
        )
    return argv.build_gallerydl_argv(
        url, into, cookies_file=cookies_file, proxy=proxy, policy=policy
    )


def _watching(backend: Backend, report: progress.Report) -> Callable[[str], None]:
    """A reader for one tool's output that turns what it says into how far along it is.

    The two tools can say different amounts and this is where that asymmetry lives. The video tool
    was asked for a progress line in a shape Sift chose, so it reports real bytes and a real total.
    The gallery tool has no such option, so all it can say is that another file has landed, which
    on a gallery of hundreds of small images is the more useful number anyway, and is what makes the
    difference between a count that moves and a spinner.
    """
    # How long the tool spent before ANY bytes moved.
    #
    # Recorded because "it took a long time to start" is otherwise unanswerable: from the outside a
    # download is one duration, and the part somebody notices is the silence in front of it: the
    # page fetched, the challenge answered, the formats listed, all of it through whatever exit the
    # site was given. Logged once per run, never per line.
    began = time.monotonic()
    reported = False

    def first_bytes() -> None:
        nonlocal reported
        if reported:
            return
        reported = True
        log.info("download.first_bytes", after_ms=round((time.monotonic() - began) * 1000))

    if backend is Backend.YTDLP:
        # Carried between lines because the tool announces the item once and then reports bytes for
        # it. Without holding them, every bytes report would blank the count the row is showing.
        item = 0
        of_how_many = 0

        def watch_bytes(line: str) -> None:
            nonlocal item, of_how_many
            counted = progress.read_playlist_line(line)
            if counted is not None:
                item, of_how_many = counted
                # One less than the item being fetched: "3 of 10" means two are finished.
                report(progress.Progress(done_files=item - 1, total_files=of_how_many))
                return
            reading = progress.read_tool_line(line)
            if reading is not None:
                first_bytes()
                report(
                    progress.Progress(
                        done_bytes=reading.done_bytes,
                        total_bytes=reading.total_bytes,
                        total_is_estimated=reading.total_is_estimated,
                        done_files=max(item - 1, 0),
                        total_files=of_how_many or None,
                    )
                )

        return watch_bytes

    landed = 0

    def watch_files(line: str) -> None:
        nonlocal landed
        if progress.looks_like_a_finished_file(line):
            landed += 1
            first_bytes()
            report(progress.Progress(done_files=landed))

    return watch_files


class Downloader:
    """Resolves a URL and fetches its media, choosing per item how. Satisfies the downloader seam."""

    def __init__(
        self,
        *,
        read_downloader: DownloaderReader | None = None,
        read_policy: PolicyReader | None = None,
    ) -> None:
        # Which tool a Site was pointed at, read live per download so a change takes effect at
        # once. Absent a reader (the tests, and any build that has not wired one) every Site
        # uses Sift's own answer.
        self._read_downloader = read_downloader
        # Everything else the tools are told (pacing, retries, timeouts, the bandwidth cap, the
        # size bounds, the quality preference) arrives the same way and for the same reason. Absent
        # a reader it is the chosen defaults, which is what the tests and a bare build get.
        self._read_policy = read_policy

    def handles(self, url: str) -> bool:
        """Whether this can fetch a URL at all. Any http(s) address (a matched site, or the
        yt-dlp catch-all behind it) can be tried; nothing else."""
        return urlsplit(url).scheme in {"http", "https"}

    async def fetch(
        self,
        url: str,
        *,
        into: Path,
        cookies_file: Path | None = None,
        proxy: str | None = None,
        already_have: ItemCheck | None = None,
        report: progress.Report = progress.nowhere,
    ) -> Fetched:
        """Resolve `url` and fetch every piece of media into `into`, returning the files produced.

        Direct items are streamed over one guarded session; subprocess items are each run through
        their tool. A cookies file is a decrypted site login, supplied only when one is saved for this
        site, and is used by the subprocess tools. Everything written lands inside `into`.

        The uploader the resolver named comes back with the files, so a download whose address
        names nobody is still filed under whoever resolving it named.

        `already_have` is supplied only for a link whose contents change: a story tray, a highlight.
        Those are re-resolved on every paste, so without it a second paste re-downloads the whole set;
        with it each item is asked about first and only what is new is fetched. It is never supplied
        for an ordinary permalink, which is skipped a step earlier by its address.
        """
        chosen = await self._chosen_downloader(url)
        # BEFORE anything is asked of the Site. A Site that serves nothing without cookies, by the
        # method this download is about to use, would only answer a login wall (after a request
        # that shows the Site this machine's address for nothing), and the answer is known now.
        # So the download waits for cookies from the start, on the same door a refusal opens.
        if cookies_file is None:
            self._refuse_without_cookies(url, tool_chosen=chosen is not None)
        # Read once for the whole fetch rather than once per item: a change made halfway through one
        # album should not leave its first half paced differently from its second. Read BEFORE the
        # resolve, because the readers' own page and API requests are paced, timed out and held
        # after a rate limit by the same settings as the transfer.
        run = await self._policy()
        media = await resolve(
            url,
            downloader=chosen,
            cookies_file=cookies_file,
            proxy=proxy,
            # Resolving an album is minutes of work before any media is asked for. The same
            # reporter the fetch uses carries it, so a screen watching a download sees one
            # continuous count rather than a long pause and then a bar.
            report=report,
            policy=run,
        )
        direct = [item for item in media.items if item.backend == "direct"]
        subprocess_items = [item for item in media.items if item.backend != "direct"]

        files: list[Path] = []
        item_keys: dict[Path, str] = {}
        # What each file can be NAMED from: its post's ID, which file of the post it is, the
        # title, when it was posted. Keyed by the path produced, like `item_keys`, for the same
        # reason: the job reads it before the file is renamed.
        names: dict[Path, NameFacts] = {}
        # How many pieces there are, so an album of forty reads as "12 of 40" rather than as a bar
        # that restarts from nothing forty times.
        total_items = len(direct) or None
        carried = 0
        # The last file refused for good (out of the size settings, or gone from the Site), kept
        # so a download that was ONLY such files says why it has nothing.
        skipped: NothingFound | None = None
        left_out = 0
        if direct:
            async with guarded_session(proxy=proxy, policy=run) as session:
                for item in direct:
                    key = stable_key(item)
                    if already_have is not None and await already_have(key):
                        continue
                    try:
                        landed, moved = await self._fetch_fresh(
                            session,
                            item,
                            into,
                            proxy=proxy,
                            report=report,
                            already_done=carried,
                            files_done=len(files),
                            files_total=total_items,
                            policy=run,
                        )
                    except NothingFound as refused:
                        # One file of an album that is final (out of bounds, or deleted from the
                        # Site) is that file left out, as the tools leave one out and carry on,
                        # not the whole album failed with the rest of it never asked for. A file
                        # that merely did not finish still ends the run, which retries it.
                        skipped = refused
                        left_out += 1
                        continue
                    carried += moved
                    files.append(landed)
                    item_keys[landed] = key
                    names[landed] = item_name_facts(media, item)
        tool_files: list[Path] = []
        for item in subprocess_items:
            tool_files += await self._fetch_subprocess(item, into, cookies_file, proxy, run, report)
        # Once each. Every tool run reads back EVERYTHING in the directory (`subproc.output_files`),
        # so a link resolved to two tool runs (a Reddit post embedding two RedGIFs clips) would list
        # the first run's file a second time, for a job that names and imports every entry.
        tool_files = list(dict.fromkeys(tool_files))
        if tool_files:
            # What the tool itself said about each file it finished (`argv.TOOL_FACTS_FILE`).
            said = await asyncio.to_thread(argv.tool_name_facts, into)
            for path in tool_files:
                names[path] = tool_file_name_facts(
                    media, path, said.get(path.name), one_of=len(tool_files)
                )
        files += tool_files
        if not files and skipped is not None:
            raise skipped
        if left_out:
            log.warning("download.files_left_out", left_out=left_out, of=len(direct))
        return Fetched(
            files=files,
            username=media.username,
            item_keys=item_keys,
            names=names,
            offered=len(direct),
            left_out=left_out,
        )

    def _refuse_without_cookies(self, url: str, *, tool_chosen: bool) -> None:
        """Hold a download for cookies when its Site is declared to need them for this method.

        Only a declared REQUIRED does this. Partial is not enough: public content on such a Site
        downloads without cookies, and holding every download for the minority that needs them
        would be a wait for something most of them never needed. Those wait when refused.
        """
        record = match_site(url)
        if record is None:
            return
        if catalog.cookie_need(record, tool_chosen=tool_chosen) is not catalog.CookieNeed.REQUIRED:
            return
        raise CookiesNeeded(
            f"{record.site} needs cookies before it will download anything this way. Add cookies "
            "for it and the download carries on by itself.",
            code=CODE_COOKIES_REQUIRED,
        )

    async def _policy(self) -> RunPolicy:
        """What the tools are told this run. The chosen defaults when nothing reads preferences."""
        if self._read_policy is None:
            return POLICY
        return await self._read_policy()

    async def _chosen_downloader(self, url: str) -> Backend | None:
        """The tool this URL's Site was pointed at by hand, or None to leave it to Sift.

        The site key comes from the catalog rather than from the address's host, so a mirror
        resolves to the same Site its settings were written under: Bunkr has 25 addresses and
        a choice made against one of them has to hold for all of them.

        An address the catalog does not know still asks, under the key None: the default scope is a
        real answer for it, and somebody who has set a tool for everything means everything.
        """
        if self._read_downloader is None:
            return None
        record = match_site(url)
        return chosen_backend(await self._read_downloader(record.key if record else None))

    async def _fetch_fresh(
        self,
        session: aiohttp.ClientSession,
        item: ResolvedItem,
        into: Path,
        *,
        proxy: str | None,
        report: progress.Report = progress.nowhere,
        already_done: int = 0,
        files_done: int = 0,
        files_total: int | None = None,
        policy: RunPolicy = POLICY,
    ) -> tuple[Path, int]:
        """Fetch one item, and where its signed address has gone stale, sign it again once.

        A Site that signs its addresses signs them for a while (a Bunkr link for about two hours,
        a TikTok photo for seconds), and the files of a large album are signed when it is read, so
        the last of them can be asked for after its signature has run out. The Site answers that
        with a refusal. An item that names its page (`refetch_url`) is read again for a fresh
        address and asked once more; a second refusal is the Site's real answer.
        """

        async def fetch(one: ResolvedItem) -> tuple[Path, int]:
            return await self._fetch_direct(
                session,
                one,
                into,
                here=proxy is None,
                report=report,
                already_done=already_done,
                files_done=files_done,
                files_total=files_total,
                policy=policy,
            )

        try:
            return await fetch(item)
        except (NothingFound, FetchFailed) as refused:
            if item.refetch_url is None or refused.code not in _STALE_SIGNATURE_CODES:
                raise
            fresh = await self._signed_again(item, item.refetch_url, proxy=proxy, policy=policy)
            if fresh is None:
                raise
            log.info("download.signed_again", url=without_signature(item.url))
            return await fetch(fresh)

    async def _signed_again(
        self, item: ResolvedItem, page: str, *, proxy: str | None, policy: RunPolicy
    ) -> ResolvedItem | None:
        """The same item read again from its page, or None when the page no longer offers it.

        Kept under the item's own place and name, so it stages as the same file: a page read on its
        own resolves to one item, first of one, and the album it came from had it forty-first.
        """
        try:
            media = await resolve(page, proxy=proxy, policy=policy)
        except DownloadError:
            return None
        found = [one for one in media.items if one.backend == "direct"]
        if len(found) == 1:
            chosen: ResolvedItem | None = found[0]
        else:
            chosen = next((one for one in found if one.index == item.index), None)
        if chosen is None:
            return None
        return replace(
            chosen,
            index=item.index,
            filename=item.filename or chosen.filename,
            refetch_url=page,
        )

    async def _fetch_direct(
        self,
        session: aiohttp.ClientSession,
        item: ResolvedItem,
        into: Path,
        *,
        report: progress.Report = progress.nowhere,
        already_done: int = 0,
        files_done: int = 0,
        files_total: int | None = None,
        policy: RunPolicy = POLICY,
        here: bool = True,
    ) -> tuple[Path, int]:
        """Stream one direct media address to a file named for what it is. The extension the
        resolver knew is used; an extensionless address gets one from the response content type.

        Returns what it landed and how much it moved, so an album's second file continues the count
        rather than restarting it: one paste of forty images is one download on one row.

        A file of this name already in the directory is what a PAUSED run left behind, and it is
        asked to be continued rather than fetched again. The staging name is what makes that safe:
        it is derived from the address, so the same item resumed is the same file and a different
        item is a different one. On any other run the directory is empty and the size is zero.
        """
        dest = into / stage_name(item)
        return await fetcher.fetch_to_file(
            session,
            url=item.url,
            dest=dest,
            referer=item.referer,
            accept=item.accept,
            cookie=item.cookie,
            report=report,
            already_done=already_done,
            files_done=files_done,
            files_total=files_total,
            resume_from=await asyncio.to_thread(_bytes_already_here, dest),
            policy=policy,
            here=here,
        )

    async def _fetch_subprocess(
        self,
        item: ResolvedItem,
        into: Path,
        cookies_file: Path | None,
        proxy: str | None,
        run: RunPolicy,
        report: progress.Report = progress.nowhere,
    ) -> list[Path]:
        """Run the tool the item names on its source URL. If it finds nothing and the item names a
        fallback tool, try that one: X carries images gallery-dl reads and video yt-dlp reads, and
        neither tool does both. A login problem is not retried with the other tool: it will not fix a
        missing login, so it propagates rather than triggering the fallback."""
        try:
            return await self._run_tool(
                Backend(item.backend), item.url, into, cookies_file, proxy, run, report
            )
        except fetcher.Skipped:
            # The other tool is held to the same size settings and would skip it too.
            raise
        except NothingFound:
            if item.fallback_backend is None:
                raise
            return await self._run_tool(
                Backend(item.fallback_backend), item.url, into, cookies_file, proxy, run, report
            )

    async def _run_tool(
        self,
        backend: Backend,
        url: str,
        into: Path,
        cookies_file: Path | None,
        proxy: str | None,
        run: RunPolicy = POLICY,
        report: progress.Report = progress.nowhere,
    ) -> list[Path]:
        """One tool run: build its command, run it, read the files, or turn its failure into words.

        The tool reports on itself as it goes, on its output stream, and that is read here. Nothing
        about how a FAILURE is read changes: that comes off the error stream, which this does not
        touch, so a caller wanting a progress bar cannot affect a caller wanting an explanation.
        """
        command = _command(backend, url, into, cookies_file=cookies_file, proxy=proxy, policy=run)

        # A Site that said "too many requests" to an earlier run is left alone until the wait after
        # a rate limit is over. yt-dlp has no option for that wait, and a run cannot be paused
        # from outside, but the NEXT run can be held, which is what the setting asks for.
        await ratelimit.wait_out_backoff(url)
        result = await subproc.run(command, on_line=_watching(backend, report), into=into)
        if run.verbose:
            _record_the_run(backend, url, command, result)
        if result.returncode != 0:
            # THE PROXY IS ASKED FIRST. A connection it refused reaches the tool as a 403 and reads,
            # in the tool's words, exactly like a site turning a login away, which with a jar
            # saved would condemn the jar. What actually happened is the tool's own record.
            traffic = argv.proxy_traffic(command)
            if traffic is not None and traffic.refused:
                raise PrivateNetworkRefused(PRIVATE_NETWORK_REFUSED, code="private-network")
            # WHETHER THE RUN CARRIED COOKIES is half of what a refusal means, and only this layer
            # knows it. The same 403, with a jar and without one, is a download that must wait and
            # a jar that has to be replaced, so the file is not merely used here, it is reported.
            failure = self._interpret(url, result.stderr, had_cookies=cookies_file is not None)
            if failure.code in ratelimit.RATE_LIMIT_CODES:
                ratelimit.note_too_many_requests(url, run)
            raise failure

        files = subproc.output_files(into)
        if not files:
            # The tool reported success but wrote nothing: a file it left out for the size
            # settings, which it says in its own words, or else a page that resolved to no media.
            skipped = _size_skip(f"{result.stdout}\n{result.stderr}")
            if skipped is not None:
                raise skipped
            raise NothingFound(self._nothing_message(url))
        return files

    def _interpret(self, url: str, stderr: str, *, had_cookies: bool) -> DownloadError:
        """Turn a tool's standard error into a plain-language failure, and log the detail.

        `had_cookies` says whether this run was given a saved jar, and it is what turns a refusal
        into a wait or into a verdict on the jar. It is asked here rather than in the classifier
        because the classifier answers what a failure IS, which must not depend on what happened to
        be saved on the day.

        The tool's output goes to the log through the ordinary redaction, which keeps the URL and
        the remote host (knowing which address failed is the whole of what makes a failure
        diagnosable, and this log is an admin's own), and removes what is actually sensitive: the
        account name in a home-directory path, and any cookie the tool echoed.

        Two readings, and they answer different questions. The markers below decide the TYPE, which
        is what the job acts on: a login problem waits, a missing post is permanent, an unfinished
        run is worth retrying. The classifier decides the WORDS, from the most specific reading
        available: this site's own refusal, then the conditions every site shares, then the status
        code. Where it recognises nothing the type's own sentence stands, which is the honest
        outcome rather than a confident guess.
        """
        # The address without its signed query, and the last of what the tool said. A signed URL
        # is a credential for as long as it is valid, and the log is read, copied and pasted; the
        # reason for a failure is in the tool's last lines, after everything it printed before.
        log.warning(
            "download.tool_failed", url=without_signature(url), detail=last_lines(stderr, 20)
        )

        known = failures.classify(url, stderr)
        # Typed, because an empty dict beside a populated one widens the value type to `object` and
        # every exception below then takes an argument of the wrong type as far as a checker is
        # concerned.
        extra: dict[str, Any] = (
            {
                "code": known.code,
                "tier": known.tier,
                "a_tunnel_would_help": known.a_tunnel_would_help,
            }
            if known is not None
            else {}
        )

        # Whose words win. A tier 3 phrase was written about this site by somebody who watched it
        # refuse, and a tier 2 one names a condition or a code with a meaning: both are worth more
        # than the general sentence for the type. A tier 1 reading is a status code's standard
        # phrase and knows nothing else: "Pornhub answered 403 Forbidden." is TRUE and less useful
        # than "this site wants cookies, add cookies and try again", which is what the markers
        # below have already worked out. So tier 1 keeps the type's own words, and the code after
        # them, because a row that hid the code would leave somebody guessing.
        # Where nothing was recognised at all, the tool's own words are the one clue left.
        def words(fallback: str) -> str:
            if known is not None and known.tier >= 2:
                return known.sentence
            if known is not None:
                return f"{fallback} {known.sentence}"
            said = failures.tool_words(stderr)
            return f"{fallback} ({said})" if said else fallback

        # Before the markers, because this decides what HAPPENS and they only decide what is said.
        # A refusal cookies get past, on a site cookies get past it on, is one of two things and
        # never the third: with nothing saved it is a WAIT (the row says so and offers the one
        # act that helps), and with a jar saved and sent it is that jar being turned away, which
        # is a real failure and the one signal the killswitch exists to count. Read as a plain
        # login failure it would tell somebody to add cookies under a button that only says Try
        # again.
        if known is not None and known.cookies_would_help:
            if not had_cookies:
                return CookiesNeeded(self._cookies_needed_message(url, known.code), **extra)
            cookie_health.record_auth_failure(source_host(url), detail=known.code)
            return LoginRequired(
                self._cookies_refused_message(url),
                code=failures.CODE_COOKIES_REFUSED,
                a_tunnel_would_help=known.a_tunnel_would_help,
            )

        # An answer that will be the same next time (a private post, a 404, a 410)
        # is recorded once and not retried. Before the markers, which would type a private post as
        # a login problem and a 410 as a run that merely did not finish, retried and told "try
        # again" each time.
        if known is not None and known.final:
            return NothingFound(known.sentence, **extra)

        if _LOGIN_MARKERS.search(stderr):
            return LoginRequired(words(self._login_message(url)), **extra)
        # Before "nothing found": an unsupported site is a permanent, different thing from a post
        # that resolved to nothing, and it must not be retried.
        if _UNSUPPORTED_MARKERS.search(stderr):
            # Its own code, and never the status the site happened to answer with. "No tool here
            # reads this site" is the whole of what this failure is, and a row keyed on the 403 a
            # bot check returned on the way would be filed with every other 403 and given the
            # sentence written for one, which is an invitation to add cookies for a site nothing
            # can read with or without them.
            return UnsupportedURL(
                self._unsupported_message(url),
                code=failures.CODE_UNSUPPORTED,
                a_tunnel_would_help=False,
            )
        if _NOTHING_MARKERS.search(stderr):
            return NothingFound(words(self._nothing_message(url)), **extra)
        if _NO_ANSWER_MARKERS.search(last_lines(stderr, _NO_ANSWER_TAIL)):
            return NoAnswer(NO_ANSWER_MESSAGE, code=failures.CODE_NO_ANSWER)
        return DownloadError(words(self._generic_message(url)), **extra)

    @staticmethod
    def _site(url: str) -> str:
        return classify(url).site or "this site"

    def _login_message(self, url: str) -> str:
        return (
            f"{self._site(url)} wants cookies before it will show this. Add cookies for it, "
            "then try the download again."
        )

    def _cookies_needed_message(self, url: str, code: str) -> str:
        """The wait. It says what the download is doing now, because the row is not over, and
        WHY the site wants cookies where the reading knows, so an age gate's own sentence is the
        waiting row's reason rather than a line nobody sees."""
        if code == "wall-age":
            return (
                f"{self._site(url)} wants proof of age before it will show this. Add cookies from "
                "a browser you are signed in to and the download carries on by itself."
            )
        return (
            f"{self._site(url)} needs cookies before it will show this. Add cookies for it and "
            "the download carries on by itself."
        )

    def _cookies_refused_message(self, url: str) -> str:
        """The jar was sent and came back. Nothing to wait for: it needs replacing."""
        return (
            f"{self._site(url)} turned the saved cookies away. Export them from the browser "
            "again and replace them under Cookies."
        )

    def _nothing_message(self, url: str) -> str:
        return (
            f"Nothing could be downloaded from {self._site(url)}. The post may be private, "
            "deleted, or hold no media."
        )

    def _unsupported_message(self, url: str) -> str:
        return (
            f"Sift cannot download from {self._site(url)}. None of the downloaders Sift includes "
            "support it."
        )

    def _generic_message(self, url: str) -> str:
        return f"The download from {self._site(url)} did not finish. Try again in a few minutes."


#: How much of one run's diagnostics the detailed download log keeps, from each end.
#:
#: A verbose run of gallery-dl over a large album writes a line per file, and a log line of that
#: length is one nobody reads. The head is where the tool says what it was asked and which version
#: answered; the tail is where it says how it ended. The middle is the part that repeats.
VERBOSE_HEAD_LINES = 60
VERBOSE_TAIL_LINES = 60


def _record_the_run(
    backend: Backend, url: str, command: list[str], result: subproc.SubprocessResult
) -> None:
    """The detailed download log: what the tool was told, how it ended, and what it said.

    Written for every run while the setting is on, the successful ones included, which is the
    whole of what the setting is for: a person turns it on to see what a run was sent, the pace and
    the retries they just chose, say, and `_interpret` reads a tool's output only when a run fails.
    The command is logged as the command line it ran as,
    with the address's signed query taken off the way every other log line takes it off; the
    ordinary redaction then removes any credential either of them carries (a tunnel's proxy
    password, an echoed cookie), in every mode.
    """
    shown = [without_signature(part) if part == url else part for part in command]
    log.info(
        "download.tool_ran",
        tool=backend.value,
        command=subprocess.list2cmdline(shown),
        exit_code=result.returncode,
        detail=ends_of(result.stderr, VERBOSE_HEAD_LINES, VERBOSE_TAIL_LINES),
    )


def ends_of(text: str, head: int, tail: int) -> str:
    """The first `head` and last `tail` lines of a tool's output, with what was left out counted."""
    lines = text.rstrip().splitlines()
    if len(lines) <= head + tail:
        return "\n".join(lines)
    left_out = len(lines) - head - tail
    return "\n".join([*lines[:head], f"... {left_out} lines left out ...", *lines[-tail:]])


def last_lines(text: str, count: int) -> str:
    """The end of a tool's output, where the reason for a failure is: the last `count` lines."""
    lines = text.rstrip().splitlines()
    return "\n".join(lines[-count:])
