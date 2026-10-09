# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read two named fields out of a picture to name the username behind a site's number, and
nothing else."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from sift.kernel.config import Settings
from sift.kernel.media import FFmpegError, run_json
from sift.kernel.subprocess import Priority
from sift.kernel.urls import INSTAGRAM_WORDS

#: The only two fields read: handed to ffprobe and filtered on again, so nothing else is kept.
FIELDS: Final[tuple[str, str]] = ("Artist", "ImageDescription")

#: A site with no host here corroborates nothing, so learns nothing.
SITE_ADDRESSES: Final[Mapping[str, str]] = {"Instagram": "instagram.com"}

#: Route words too: `instagram.com/explore/` looks like a profile (`kernel/urls.py`).
_NOT_A_USERNAME: Final[frozenset[str]] = INSTAGRAM_WORDS

#: Two names most numbers; one would let a stray file invent a username.
AGREEING_PICTURES: Final[int] = 2

_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")

#: A share that has gone away must not hold a pass open.
READ_TIMEOUT_SECONDS: Final[float] = 20.0


@dataclass(frozen=True, slots=True)
class Fields:
    artist: str = ""
    description: str = ""


def probe_args(path: Path, *, settings: Settings) -> list[str]:
    """Ask one picture's first frame for exactly the two `FIELDS`."""
    return [
        settings.ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-read_intervals",
        "%+#1",
        "-show_entries",
        f"frame_tags={','.join(FIELDS)}",
        "-of",
        "json",
        str(path),
    ]


def understand(payload: Mapping[str, Any]) -> Fields:
    """The two fields out of what ffprobe said, keyed on `FIELDS` whatever came back."""
    frames = payload.get("frames") or []
    if not frames or not isinstance(frames[0], Mapping):
        return Fields()
    tags = frames[0].get("tags") or {}
    if not isinstance(tags, Mapping):
        return Fields()
    kept = {name: str(tags.get(name) or "").strip() for name in FIELDS}
    return Fields(artist=kept["Artist"], description=kept["ImageDescription"])


async def read_fields(path: Path, *, settings: Settings) -> Fields:
    """One picture's two fields; empty for anything unreadable, so a dead share fails no scan."""
    try:
        payload = await run_json(
            probe_args(path, settings=settings),
            time_limit=READ_TIMEOUT_SECONDS,
            priority=Priority.BACKGROUND,
            reads=path,
        )
    except FFmpegError:
        return Fields()
    return understand(payload)


def _username_of(text: str) -> str:
    return text.strip().lstrip("@").casefold()


def username_in_address(address: str) -> str | None:
    """The username a profile address names; None for a post address, which names nobody."""
    parts = [one for one in urlsplit(address).path.split("/") if one]
    if len(parts) != 1 or parts[0].casefold() in _NOT_A_USERNAME:
        return None
    return parts[0] if _USERNAME.match(parts[0]) else None


def names_the_site(address: str, site: str) -> bool:
    """Whether this address's host is the site's own host or a subdomain of it."""
    host = SITE_ADDRESSES.get(site)
    if host is None:
        return False
    where = urlsplit(address)
    if where.scheme not in ("http", "https"):
        return False
    name = where.hostname or ""
    return name == host or name.endswith(f".{host}")


def agreed_name(readings: Sequence[Fields], site: str) -> tuple[str, int] | None:
    """The username every speaking picture agrees on, with its count; one dissenter means None."""
    said: dict[str, str] = {}
    spoke = 0
    for reading in readings:
        if not reading.artist or not _USERNAME.match(reading.artist.strip().lstrip("@")):
            continue
        if not names_the_site(reading.description, site):
            continue
        named = username_in_address(reading.description)
        if named is not None and _username_of(named) != _username_of(reading.artist):
            # The two fields name different usernames: write nothing.
            return None
        spoke += 1
        said.setdefault(_username_of(reading.artist), reading.artist.strip().lstrip("@"))
        if len(said) > 1:
            return None
    if spoke < AGREEING_PICTURES or not said:
        return None
    return next(iter(said.values())), spoke
