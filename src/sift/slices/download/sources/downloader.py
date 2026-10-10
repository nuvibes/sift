# SPDX-License-Identifier: AGPL-3.0-or-later
"""The seam the download job fetches through: a URL in, files on disk out.

Each piece is streamed directly or run through its tool; a tool's failure is read into words.
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
    ResolvedMedia,
    item_name_facts,
    stable_key,
    tool_file_name_facts,
)
from sift.slices.download.sources.sites import catalog
from sift.slices.download.sources.tuning import POLICY, RunPolicy

#: A Site key (or None) to the tool it was pointed at, or None; read live per download.
DownloaderReader = Callable[[str | None], Awaitable[str | None]]

#: A download held for cookies before anything was asked of its Site.
CODE_COOKIES_REQUIRED = "cookies-required"

#: Whether one piece of media behind this link was already kept; the caller owns the ledger.
ItemCheck = Callable[[str], Awaitable[bool]]

log = get_logger(__name__)

#: A permit list, since a name comes from a remote site; a space and brackets cannot move
#: a file, and `{name}` fills from the staged name.
_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._ ()-]+")

_MAX_STEM = 80


def stage_name(item: ResolvedItem) -> str:
    """What one directly fetched item is called on the way in.

    Its own name, else the URL's last segment, else a digest; the index only when not zero.
    """
    stem = _clean_stem(item.filename) or _clean_stem(_last_segment(item.url))
    if not stem:
        stem = hashlib.sha256(item.url.encode("utf-8")).hexdigest()[:12]
    if item.index:
        stem = f"{stem}-{item.index}"
    return f"{stem}{item.ext}"


def _bytes_already_here(dest: Path) -> int:
    """How much of this file a paused run left behind, or zero; reads the disk, so off the loop."""
    return dest.stat().st_size if dest.exists() else 0


def _last_segment(url: str) -> str:
    """The final path segment of a URL, percent-decoded; the query is not part of a name."""
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def _clean_stem(name: str | None) -> str:
    """A name reduced to a safe filename stem: the extension goes, the resolver's is appended."""
    if not name:
        return ""
    cleaned = re.sub(r" {2,}", " ", _UNSAFE_IN_NAME.sub("_", name)).strip("._ ")
    return Path(cleaned).stem[:_MAX_STEM].strip("._ ")


# Login first: a private post that 404s to a logged-out client is a login problem.
_LOGIN_MARKERS = re.compile(
    r"(?i)(\b401\b|\b403\b|log[\s-]?in|sign[\s-]?in|authenticat|forbidden|"
    r"private|not\s+available.*account|login\s+required|rate.?limit|\b429\b)"
)
_NOTHING_MARKERS = re.compile(
    r"(?i)(\b404\b|not\s+found|unavailable|deleted|removed|no\s+video|no\s+media|"
    r"unable\s+to\s+extract)"
)
#: A Site's answers that pass: a limit on requests, or trouble of its own. Read before the markers,
#: which take a 429 for a login and a 503's "Service Unavailable" for a removed post.
_COMES_BACK = frozenset(
    {"rate-limited"}
    | {f"http-{status}" for status in (408, 429, 500, 502, 503, 504, 520, 521, 522, 524, 527)}
)
# A Site no tool Sift ships can read: permanent, so never retried.
_UNSUPPORTED_MARKERS = re.compile(
    r"(?i)(unsupported\s+url|no\s+suitable\s+extractor|is\s+not\s+a\s+supported\s+(site|url))"
)
# A file a tool left out for its size: both tools exit 0 and say so in their own words.
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


#: How a Site refuses a signed address that has run out.
_STALE_SIGNATURE_CODES = frozenset({"http-403", "http-410"})


# A tool that gave up after its own retries; read from the last lines only.
_NO_ANSWER_MARKERS = re.compile(r"(?i)(timed\s+out|read\s*timeout|connect\s*timeout)")
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
    """The command one tool runs with for one address, `--impersonate` where a Site needs it."""
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
    """A reader of one tool's output that reports how far along it is."""
    # How long the tool spent before any bytes moved, logged once per run.
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


def _worded(known: failures.Failure | None, stderr: str, fallback: str) -> str:
    """A failure's words: the reading's own from tier 2 up, else the type's with the code or tool."""
    if known is not None and known.tier >= 2:
        return known.sentence
    if known is not None:
        return f"{fallback} {known.sentence}"
    said = failures.tool_words(stderr)
    return f"{fallback} ({said})" if said else fallback


class Downloader:
    """Resolves a URL and fetches its media, choosing per item how. Satisfies the downloader seam."""

    def __init__(
        self,
        *,
        read_downloader: DownloaderReader | None = None,
        read_policy: PolicyReader | None = None,
    ) -> None:
        self._read_downloader = read_downloader
        self._read_policy = read_policy

    def handles(self, url: str) -> bool:
        """Whether this can fetch a URL at all: any http(s) address can be tried."""
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
        """Resolve `url` and fetch every piece of its media into `into`.

        `already_have` comes only with a link whose contents change, so only the new is fetched.
        """
        chosen = await self._chosen_downloader(url)
        # Before anything is asked of the Site: without cookies it would only answer a login wall.
        if cookies_file is None:
            self._refuse_without_cookies(url, tool_chosen=chosen is not None)
        # Once per fetch, before the resolve, so one album is paced one way.
        run = await self._policy()
        media = await resolve(
            url,
            downloader=chosen,
            cookies_file=cookies_file,
            proxy=proxy,
            report=report,
            policy=run,
        )
        direct = [item for item in media.items if item.backend == "direct"]
        subprocess_items = [item for item in media.items if item.backend != "direct"]

        files, item_keys, names, skipped, left_out = await self._stream_direct(
            media, direct, into, proxy=proxy, already_have=already_have, report=report, run=run
        )
        tool_files: list[Path] = []
        for item in subprocess_items:
            tool_files += await self._fetch_subprocess(item, into, cookies_file, proxy, run, report)
        # Once each: every tool run reads back the whole directory.
        tool_files = list(dict.fromkeys(tool_files))
        if tool_files:
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

    async def _stream_direct(
        self,
        media: ResolvedMedia,
        direct: list[ResolvedItem],
        into: Path,
        *,
        proxy: str | None,
        already_have: ItemCheck | None,
        report: progress.Report,
        run: RunPolicy,
    ) -> tuple[list[Path], dict[Path, str], dict[Path, NameFacts], NothingFound | None, int]:
        """Stream the direct items over one guarded session, leaving out a file refused for good."""
        files: list[Path] = []
        item_keys: dict[Path, str] = {}
        names: dict[Path, NameFacts] = {}
        total_items = len(direct) or None
        carried = 0
        # Kept so a download of only such files says why it has nothing.
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
                        # A final refusal leaves that file out; an unfinished one ends the run.
                        skipped = refused
                        left_out += 1
                        continue
                    carried += moved
                    files.append(landed)
                    item_keys[landed] = key
                    names[landed] = item_name_facts(media, item)
        return files, item_keys, names, skipped, left_out

    def _refuse_without_cookies(self, url: str, *, tool_chosen: bool) -> None:
        """Hold a download for cookies when its Site is declared to REQUIRE them for this method."""
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
        """The tool this URL's Site was pointed at, by catalog key so mirrors agree, or None."""
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
        """Fetch one item, signing its address again once where the Site says it has gone stale."""

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
        """The item read again from its page under its own place and name, or None."""
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
        """Stream one direct media address to a file, returning what landed and how much moved.

        A file of the staging name already there is a paused run's, and is continued.
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
        """Run the item's tool, then its fallback tool where the first finds nothing."""
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
        """One tool run: its command, its files, or its failure in words."""
        command = _command(backend, url, into, cookies_file=cookies_file, proxy=proxy, policy=run)

        # Hold the next run until the wait after a rate limit is over.
        await ratelimit.wait_out_backoff(url)
        result = await subproc.run(command, on_line=_watching(backend, report), into=into)
        if run.verbose:
            _record_the_run(backend, url, command, result)
        if result.returncode != 0:
            # The proxy first: its refusal reads like a Site refusing a login.
            traffic = argv.proxy_traffic(command)
            if traffic is not None and traffic.refused:
                raise PrivateNetworkRefused(PRIVATE_NETWORK_REFUSED, code="private-network")
            # Whether the run carried cookies decides between a wait and a refused jar.
            failure = self._interpret(url, result.stderr, had_cookies=cookies_file is not None)
            if failure.code in ratelimit.RATE_LIMIT_CODES:
                ratelimit.note_too_many_requests(url, run)
            raise failure

        files = subproc.output_files(into)
        if not files:
            skipped = _size_skip(f"{result.stdout}\n{result.stderr}")
            if skipped is not None:
                raise skipped
            raise NothingFound(self._nothing_message(url))
        return files

    def _interpret(self, url: str, stderr: str, *, had_cookies: bool) -> DownloadError:
        """Turn a tool's standard error into a plain-language failure, and log the detail.

        The markers decide the type the job acts on; the failure reader decides the words.
        """
        # A signed URL is a credential; the reason is in the tool's last lines.
        log.warning(
            "download.tool_failed", url=without_signature(url), detail=last_lines(stderr, 20)
        )

        known = failures.classify(url, stderr)
        extra: dict[str, Any] = (
            {
                "code": known.code,
                "tier": known.tier,
                "a_tunnel_would_help": known.a_tunnel_would_help,
            }
            if known is not None
            else {}
        )

        # Before the markers: with no jar it is a wait, with one it is the jar refused.
        if known is not None and known.cookies_would_help:
            if not had_cookies:
                return CookiesNeeded(self._cookies_needed_message(url, known.code), **extra)
            cookie_health.record_auth_failure(source_host(url), detail=known.code)
            return LoginRequired(
                self._cookies_refused_message(url),
                code=failures.CODE_COOKIES_REFUSED,
                a_tunnel_would_help=known.a_tunnel_would_help,
            )

        # Final answers are recorded once and not retried.
        if known is not None and known.final:
            return NothingFound(known.sentence, **extra)
        # A Site that is limiting requests or having trouble answers later: retried, in its words.
        if known is not None and known.code in _COMES_BACK:
            return DownloadError(known.sentence, **extra)

        if _LOGIN_MARKERS.search(stderr):
            return LoginRequired(_worded(known, stderr, self._login_message(url)), **extra)
        if _UNSUPPORTED_MARKERS.search(stderr):
            # Its own code: a 403 on the way is not what this failure is.
            return UnsupportedURL(
                self._unsupported_message(url),
                code=failures.CODE_UNSUPPORTED,
                a_tunnel_would_help=False,
            )
        if _NOTHING_MARKERS.search(stderr):
            return NothingFound(_worded(known, stderr, self._nothing_message(url)), **extra)
        if _NO_ANSWER_MARKERS.search(last_lines(stderr, _NO_ANSWER_TAIL)):
            return NoAnswer(NO_ANSWER_MESSAGE, code=failures.CODE_NO_ANSWER)
        return DownloadError(_worded(known, stderr, self._generic_message(url)), **extra)

    @staticmethod
    def _site(url: str) -> str:
        return classify(url).site or "this site"

    def _login_message(self, url: str) -> str:
        return (
            f"{self._site(url)} wants cookies before it will show this. Add cookies for it, "
            "then try the download again."
        )

    def _cookies_needed_message(self, url: str, code: str) -> str:
        """The wait, and why the Site wants cookies where the reading knows."""
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


#: How much of one run's diagnostics the detailed log keeps, from each end.
VERBOSE_HEAD_LINES = 60
VERBOSE_TAIL_LINES = 60


def _record_the_run(
    backend: Backend, url: str, command: list[str], result: subproc.SubprocessResult
) -> None:
    """The detailed download log: the command as run, how it ended and what it said."""
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
