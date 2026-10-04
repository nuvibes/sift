# SPDX-License-Identifier: AGPL-3.0-or-later
"""Getting a large file over HTTP: resumably, reportably, and stoppably.

Extracted from the model store, which had the only copy, when a second thing needed to download
hundreds of megabytes onto somebody's machine. The two callers want the same four properties and
nothing about them is specific to a model:

- **It resumes.** These are hundreds of megabytes; a connection dropping at 90 % must not mean
  starting again. What has arrived is kept beside the destination and asked for by byte range next
  time.
- **It reports.** A transfer nobody can see the progress of is one people assume has hung.
- **It can be stopped**, and stopping leaves the partial file for a later attempt to continue from.
  There is nothing to interrupt: the reader simply stops asking.
- **It never puts anything in place.** This writes a `.part` file and says whether it finished. What
  the bytes are, and whether they are the right ones, is the CALLER'S question, and it stays the
  caller's, because the two callers answer it differently: a model file is verified whole by one
  digest after being taken out of its archive, and a set of packages is verified one by one before
  any of them is unpacked. A shared "and then check the digest" would have to know both.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

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

    make_session = session_factory or (lambda: aiohttp.ClientSession(timeout=timeout))
    session = make_session()
    async with session as client, client.get(url, headers=headers) as response:
        if response.status == 416:
            # The server says there is nothing past where we stopped, which means the partial file
            # is already the whole thing, or is longer than the real file, in which case the
            # caller's own check will refuse it.
            return True
        if response.status not in (200, 206):
            raise FetchFailed(
                f"the {what} could not be downloaded (the server answered {response.status}). "
                "Check the machine's internet connection."
            )
        if response.status == 200:
            # The server ignored the range and is sending the whole file, so what was already
            # downloaded is not a prefix of what is arriving.
            have = 0
        total = have + int(response.headers.get("Content-Length") or 0)
        return await _stream(response, partial, have=have, total=total, progress=progress)


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
