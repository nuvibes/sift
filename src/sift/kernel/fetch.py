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
  resolve, a timeout and a certificate refused each say what to check, about the host that failed,
  which after a redirect is not the one asked. The client's own words follow the sentence.
- **It trusts what the machine trusts now, and the bundled roots beside it** (`trust`), read
  afresh for every session, because a root this machine gains while Sift runs counts from then on.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from sift.kernel.log import get_logger

if TYPE_CHECKING:
    import aiohttp

log = get_logger(__name__)

#: How much of a download to read at a time.
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
    """A file could not be downloaded. `sentence` is for a person to read; `words` are the HTTP
    client's own, and the message is the two together."""

    def __init__(self, sentence: str, words: str = "") -> None:
        super().__init__(f"{sentence} {words}" if words else sentence)
        self.sentence = sentence
        self.words = words


class Untrusted(FetchFailed):
    """The certificate was refused: the same answer on the next ask, so not one to ask again."""


def trust() -> ssl.SSLContext:
    """What a secure connection is checked against: this machine's roots as they are now, and the
    bundled set, which holds the roots a Windows install fetches only when a browser needs them.

    Built per session, never the client's own context, which is made once at import and so never
    sees a root the machine gains afterwards.
    """
    import certifi

    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


async def outbound_session(*, resolver: Any = None, **options: Any) -> aiohttp.ClientSession:
    """A session for reaching beyond this machine, checked against `trust`. Every outbound session
    is opened here; `options` are the client's own. The roots are read off the event loop."""
    import aiohttp

    context = await asyncio.to_thread(trust)
    connector = aiohttp.TCPConnector(ssl=context, resolver=resolver, use_dns_cache=resolver is None)
    return aiohttp.ClientSession(connector=connector, **options)


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
UNTRUSTED = "a secure connection to {host} couldn't be made{because}"
#: What the certificate check said, by OpenSSL's verify code; a code not here says no more.
SAYS_NO_MORE = "."
UNKNOWN_ISSUER = ", because its certificate was issued by an authority Sift doesn't trust."
OUT_OF_DATE = (
    ", because its certificate isn't valid at this computer's date and time. Check the computer's"
    " clock."
)
WRONG_NAME = ", because its certificate is for a different name."
WITHDRAWN = ", because its certificate has been withdrawn."
_CHECKS = {
    2: UNKNOWN_ISSUER,
    18: UNKNOWN_ISSUER,
    19: UNKNOWN_ISSUER,
    20: UNKNOWN_ISSUER,
    21: UNKNOWN_ISSUER,
    9: OUT_OF_DATE,
    10: OUT_OF_DATE,
    23: WITHDRAWN,
    62: WRONG_NAME,
}
PROXY = "the proxy didn't let the connection through. Check the system's proxy settings."
UNREACHED = (
    "Sift couldn't connect to {host}. Check the internet connection, and whether a firewall blocks"
    " {host}."
)
DROPPED = "the connection to {host} dropped. Check the internet connection, then try again."
KEPT = " What arrived is kept, so starting again costs only the rest."
#: After a redirect: where the address asked for sent the download.
SENT_ON = " {asked} sent the download on to {host}."


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
    asked = urlsplit(url).hostname or url
    final = asked

    try:
        client = (
            session_factory()
            if session_factory is not None
            else await outbound_session(
                timeout=aiohttp.ClientTimeout(connect=CONNECT_TIMEOUT, sock_read=READ_TIMEOUT),
                trust_env=True,
            )
        )
        async with client, client.get(url, headers=headers) as response:
            final = _host_of(getattr(response, "url", None)) or asked
            if response.status == 416:
                # Nothing past where we stopped: the partial is the whole file, or too long, which
                # the caller's own check refuses.
                return True
            if response.status not in (200, 206):
                log.warning("fetch.failed", host=final, asked=asked, status=response.status)
                why = ANSWERED.format(host=final, status=response.status)
                raise FetchFailed(await _failed(what, why + _sent_on(asked, final), partial))
            if response.status == 200:
                # The range was ignored, so what is already here is not a prefix of what arrives.
                have = 0
            total = have + int(response.headers.get("Content-Length") or 0)
            return await _stream(response, partial, have=have, total=total, progress=progress)
    except (aiohttp.ClientError, TimeoutError) as exc:
        # The connection that failed names its own host, which after a redirect is not `asked`;
        # a proxy's is the proxy's.
        if not isinstance(exc, aiohttp.ClientProxyConnectionError):
            final = str(getattr(exc, "host", None) or final)
        log.warning("fetch.failed", host=final, asked=asked, error=repr(exc))
        sentence = await _failed(what, _why(exc, final) + _sent_on(asked, final), partial)
        words = f"{type(exc).__name__}: {exc}"
        refused = isinstance(exc, aiohttp.ClientSSLError | aiohttp.ServerFingerprintMismatch)
        raise (Untrusted if refused else FetchFailed)(sentence, words) from exc


def _host_of(url: object) -> str | None:
    """The host of the address a response came from, where it says one."""
    host = getattr(url, "host", None)
    return host if isinstance(host, str) and host else None


def _sent_on(asked: str, host: str) -> str:
    return "" if host == asked else SENT_ON.format(asked=asked, host=host)


def _why(exc: BaseException, host: str) -> str:
    """What went wrong with the connection, and what to check. Most specific first."""
    import aiohttp

    if isinstance(exc, aiohttp.ClientConnectorCertificateError):
        code = getattr(exc.certificate_error, "verify_code", None)
        because = _CHECKS.get(code, SAYS_NO_MORE) if isinstance(code, int) else SAYS_NO_MORE
        return UNTRUSTED.format(host=host, because=because)
    if isinstance(exc, aiohttp.ClientSSLError | aiohttp.ServerFingerprintMismatch):
        return UNTRUSTED.format(host=host, because=SAYS_NO_MORE)
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
