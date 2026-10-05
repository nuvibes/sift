# SPDX-License-Identifier: AGPL-3.0-or-later
"""Getting a large file over HTTP: resumably, reportably, and stoppably.

- **It resumes.** What has arrived is kept beside the destination and asked for by byte range next
  time.
- **It reports**, and **it can be stopped**: the reader simply stops asking, and the partial file
  stays for a later attempt.
- **It never puts anything in place.** It writes a `.part` file and says whether it finished;
  whether the bytes are right is the caller's question, because its two callers check differently.
- **It goes the way the system says**: the environment's and the system's proxy settings apply.
- **A failure is a sentence**: a bad answer, a refused or dropped connection, a name that does not
  resolve, a timeout and a certificate refused each say what to check.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sift.kernel.log import get_logger

log = get_logger(__name__)

#: How much of a download to read at once.
CHUNK = 1 << 20

#: How long to wait for the first byte, and for each subsequent read. Split, because a slow but
#: live transfer of a large file is fine and a dead socket is not.
CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 120.0

#: Told how many bytes have arrived and how many are expected. Returning False stops the transfer,
#: which is how cancelling works.
Progress = Callable[[int, int], bool]

#: How a session is obtained. Typed loosely on purpose: the real one comes from the HTTP client and
#: a test hands in one that answers from memory, and pinning the shape here would either import the
#: client into every test or describe half of it badly.
SessionFactory = Callable[[], Any]


class FetchFailed(Exception):
    """A file could not be downloaded. The message is for a person to read."""


#: Why a transfer failed, after "The <what> couldn't be downloaded: ". The job's plain words
#: (`kernel.jobs.failure_words`) recognise each of these.
ANSWERED = "{host} answered {status}. Try again later."
REFUSED = (
    "the connection to {host} was refused. A firewall, a proxy or the network may be blocking it."
)
NOT_FOUND = (
    "{host} couldn't be found. Check the internet connection, and whether the network blocks"
    " {host}."
)
NO_ANSWER = "{host} didn't answer in time. Check the internet connection, then try again."
UNTRUSTED = (
    "a secure connection to {host} couldn't be made. Check the computer's date and time, and"
    " whether security software inspects secure connections."
)
PROXY = "the proxy didn't let the connection through. Check the system's proxy settings."
UNREACHED = (
    "Sift couldn't connect to {host}. Check the internet connection, and whether a firewall blocks"
    " {host}."
)
DROPPED = "the connection to {host} dropped. Check the internet connection, then try again."
KEPT = " What arrived is kept, so starting again costs only the rest."


async def fetch_resumable(
    url: str,
    partial: Path,
    *,
    what: str = "file",
    progress: Progress | None = None,
    session_factory: SessionFactory | None = None,
) -> bool:
    """Download `url` into `partial`, continuing a previous attempt if there is one.

    True when the whole thing has arrived. False when whoever asked for it said to stop, and then
    what has arrived stays where it is, so the next attempt asks for the remainder.
    """
    import aiohttp

    await asyncio.to_thread(partial.parent.mkdir, parents=True, exist_ok=True)
    have = await asyncio.to_thread(size_of, partial)
    headers = {"Range": f"bytes={have}-"} if have else {}
    timeout = aiohttp.ClientTimeout(connect=CONNECT_TIMEOUT, sock_read=READ_TIMEOUT)
    host = urlsplit(url).hostname or url

    make_session = session_factory or (
        lambda: aiohttp.ClientSession(timeout=timeout, trust_env=True)
    )
    try:
        async with make_session() as client, client.get(url, headers=headers) as response:
            if response.status == 416:
                # Nothing past where we stopped: the partial is the whole file, or too long, which
                # the caller's own check refuses.
                return True
            if response.status not in (200, 206):
                why = ANSWERED.format(host=host, status=response.status)
                raise FetchFailed(await _failed(what, why, partial))
            if response.status == 200:
                # The range was ignored, so what is already here is not a prefix of what arrives.
                have = 0
            total = have + int(response.headers.get("Content-Length") or 0)
            return await _stream(response, partial, have=have, total=total, progress=progress)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise FetchFailed(await _failed(what, _why(exc, host), partial)) from exc


def _why(exc: BaseException, host: str) -> str:
    """What went wrong with the connection, and what to check. Most specific first."""
    import aiohttp

    if isinstance(exc, aiohttp.ClientSSLError | aiohttp.ServerFingerprintMismatch):
        return UNTRUSTED.format(host=host)
    if isinstance(exc, aiohttp.ClientProxyConnectionError | aiohttp.ClientHttpProxyError):
        return PROXY
    if isinstance(exc, aiohttp.ClientConnectorDNSError):
        return NOT_FOUND.format(host=host)
    if isinstance(exc, TimeoutError):
        return NO_ANSWER.format(host=host)
    if isinstance(exc, aiohttp.ClientConnectorError):
        refused = isinstance(exc.os_error, ConnectionRefusedError)
        return (REFUSED if refused else UNREACHED).format(host=host)
    return DROPPED.format(host=host)


async def _failed(what: str, why: str, partial: Path) -> str:
    """The whole sentence, saying what arrived is kept only when something did."""
    kept = KEPT if await asyncio.to_thread(size_of, partial) else ""
    return f"The {what} couldn't be downloaded: {why}{kept}"


async def _stream(
    response: Any, partial: Path, *, have: int, total: int, progress: Progress | None
) -> bool:
    """Write what arrives. False when whoever asked for it said to stop."""
    written = have
    mode = "ab" if have else "wb"
    with await asyncio.to_thread(partial.open, mode) as out:
        async for chunk in response.content.iter_chunked(CHUNK):
            await asyncio.to_thread(out.write, chunk)
            written += len(chunk)
            if progress is not None and not progress(written, total):
                log.info("fetch.cancelled", bytes_written=written)
                return False
    return True


def size_of(path: Path) -> int:
    """How much of a file is already there, and zero where there is none."""
    try:
        return path.stat().st_size
    except OSError:
        return 0
