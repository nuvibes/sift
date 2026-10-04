# SPDX-License-Identifier: AGPL-3.0-or-later
"""When a Discord attachment was posted, read from its own address.

A Discord attachment link names no person and has no title, but it does carry the time it was
posted: the attachment's ID (`/attachments/<channel>/<attachment>/<name>`) is one of Discord's
"snowflake" IDs, whose top bits are the milliseconds since the start of 2015. So `{posted}` for a
Discord file costs no request at all.

Read from the address rather than from the downloader, because the downloader has nothing to say:
an attachment is a plain file to it, and the only date it could offer is the server's
`Last-Modified`, which is when the file was stored rather than when it was posted.

The time read this way comes before the download, by hours to days: a posting time, never one
after the fact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from sift.slices.download.sources.hosts import host_matches, source_host
from sift.slices.download.sources.sites.catalog import hosts_of

#: The first moment a Discord ID can name: the start of 2015, UTC, in milliseconds. Discord's own
#: documented starting point for its IDs.
_DISCORD_EPOCH_MS = 1_420_070_400_000

#: How far an ID is shifted to leave the moment: the low 22 bits are the machine and a counter.
_TIME_SHIFT = 22

_DISCORD_HOSTS = hosts_of("discord")


def posted_from_link(url: str) -> datetime | None:
    """When the attachment at `url` was posted, or None for anything that is not one.

    None for another site's address, for a Discord address that is not an attachment, and for an
    ID that is not a plain number: a name is a convenience, and a guessed date is worse than none.
    """
    if not host_matches(source_host(url), _DISCORD_HOSTS):
        return None
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if len(segments) < 3 or segments[0] != "attachments" or not segments[2].isdigit():
        return None
    # The arithmetic is inside the guard as well as the conversion: an ID of a few hundred digits
    # is a moment too large for a float, and one of thousands is refused by `int` itself.
    try:
        moment_ms = (int(segments[2]) >> _TIME_SHIFT) + _DISCORD_EPOCH_MS
        return datetime.fromtimestamp(moment_ms / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


__all__ = ["posted_from_link"]
