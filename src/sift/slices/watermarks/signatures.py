# SPDX-License-Identifier: AGPL-3.0-or-later
"""Decide whether a line of read text is a site's mark, and whose. Only an exact address files a
file; a fuzzy read is kept as a reading."""

from __future__ import annotations

import re
from dataclasses import dataclass

#: What a mark is; these words are what `watermark_reads.kind` stores.
SITE = "site"
NOTICE = "notice"
CHANNEL = "channel"
USERNAME = "username"

#: The addresses a mark can carry, written whole: a bare site name matches too many captions.
SIGNATURES: tuple[tuple[str, str], ...] = (
    ("OnlyFans", "onlyfans.com"),
    ("Fansly", "fansly.com"),
)

#: Distributor bands: the written phrase, its tidied form, and the site the distributor re-hosts.
NOTICES: tuple[tuple[str, str, str], ...] = (
    ("DMCA PROTECTED CONTENT", "dmcaprotectedcontent", "OnlyFans"),
)

#: Matched whole and in context: `chat.me/x` contains `t.me/x` (see `_channel_in`).
CHANNEL_HOST = "t.me"

#: The tag a kind that names no site puts on the file.
TAG_OF = {
    CHANNEL: "Telegram mirror",
}

#: Bump when a change here would read the same text differently; separate from `weights.REVISION`.
MATCHER_VERSION = 1

MAX_DISTANCE = 2

#: Dropped rather than replaced, so `only fans .com` still matches.
_KEPT = re.compile(r"[^a-z0-9./_-]")

#: No hyphen: it would let a caption's tail run into the username.
_USERNAME = re.compile(r"[a-z0-9_.]{3,30}")

#: Read off the raw line: tidying drops the `@` (see `_username_in`).
_AT_USERNAME = re.compile(r"@([a-z0-9_.]{3,30})")

#: A corner crop leaves `fans.com/`, four edits from `onlyfans.com` but two from `fansly.com`.
MIN_TAIL = 8

#: Short reads are usually a cropped mark, so only an exact match counts.
SHORT_USERNAME = 6


@dataclass(frozen=True, slots=True)
class Mark:
    """One mark found in one line of read text, and what it carried."""

    kind: str
    #: The site to file under; None for a channel or a bare username.
    site: str | None
    host: str | None
    username: str | None
    #: Only zero files anything.
    distance: int
    text: str
    #: Read as a cut-off tail rather than as edits (see `MIN_TAIL`).
    truncated: bool = False

    @property
    def exact(self) -> bool:
        return self.distance == 0


def _tidied(line: str) -> tuple[str, set[int]]:
    """The tidied line and where gaps closed: the address ignores gaps, a username stops at one."""
    kept: list[str] = []
    breaks: set[int] = set()
    gap = False
    for character in line.lower():
        if _KEPT.match(character):
            gap = True
            continue
        if gap and kept:
            breaks.add(len(kept))
        gap = False
        kept.append(character)
    return "".join(kept), breaks


def distance_to(word: str, target: str, *, ceiling: int) -> int:
    """How many edits turn one short word into another, giving up past `ceiling`."""
    if word == target:
        return 0
    if abs(len(word) - len(target)) > ceiling:
        return ceiling + 1
    previous = list(range(len(target) + 1))
    for index, left in enumerate(word, 1):
        row = [index]
        for column, right in enumerate(target, 1):
            row.append(
                min(
                    previous[column] + 1,
                    row[column - 1] + 1,
                    previous[column - 1] + (left != right),
                )
            )
        if min(row) > ceiling:
            return ceiling + 1
        previous = row
    return previous[-1]


def _username_after(text: str, breaks: set[int], at: int) -> str | None:
    """The username straight after the address, cut at the first closed gap, or None."""
    if at >= len(text) or text[at] != "/":
        return None
    start = at + 1
    stop = min((where for where in breaks if where > start), default=len(text))
    found = _USERNAME.match(text[start:stop])
    if found is None:
        return None
    return found.group(0).strip(".") or None


def mark_in(line: str) -> Mark | None:
    """The best mark of any kind in one line of read text, or None."""
    text, breaks = _tidied(line)
    best = _site_in(text, breaks)
    for found in (_notice_in(text), _channel_in(text, breaks), _username_in(line)):
        if found is not None and _better(found, best):
            best = found
    return best


def _site_in(text: str, breaks: set[int]) -> Mark | None:
    """The closest site address in one tidied line; a tie goes to the one with a username."""
    best: Mark | None = None
    for site, host in SIGNATURES:
        for length in range(len(host) - MAX_DISTANCE, len(host) + MAX_DISTANCE + 1):
            for start in range(0, max(0, len(text) - length) + 1):
                window = text[start : start + length]
                apart = distance_to(window, host, ceiling=MAX_DISTANCE)
                if apart > MAX_DISTANCE:
                    continue
                username = _username_after(text, breaks, start + length)
                found = Mark(
                    kind=SITE,
                    site=site,
                    host=host,
                    username=username,
                    distance=apart,
                    text=window if username is None else f"{window}/{username}",
                )
                if _better(found, best):
                    best = found
        if best is None or not best.exact:
            cut = _tail_in(text, breaks, site, host)
            if cut is not None and _better(cut, best):
                best = cut
    return best


#: Kind before distance: an address read off this copy outranks a band's inference.
_KIND_ORDER = {SITE: 0, NOTICE: 1, CHANNEL: 2, USERNAME: 3}


def _rank(mark: Mark) -> tuple[int, int, int, int]:
    """How good a reading is, lowest first."""
    return (
        _KIND_ORDER[mark.kind],
        mark.distance,
        0 if mark.truncated else 1,
        0 if mark.username else 1,
    )


def _better(found: Mark, best: Mark | None) -> bool:
    return best is None or _rank(found) < _rank(best)


def _tail_in(text: str, breaks: set[int], site: str, host: str) -> Mark | None:
    for length in range(len(host) - 1, MIN_TAIL - 1, -1):
        tail = host[-length:]
        at = text.find(tail)
        while at != -1:
            username = _username_after(text, breaks, at + length)
            if username is not None:
                return Mark(
                    kind=SITE,
                    site=site,
                    host=host,
                    username=username,
                    distance=MAX_DISTANCE,
                    text=f"{tail}/{username}",
                    truncated=True,
                )
            at = text.find(tail, at + 1)
    return None


def _notice_in(text: str) -> Mark | None:
    """A distributor's band in one tidied line, or None; exact containment only."""
    for written, tidied, site in NOTICES:
        if tidied in text:
            return Mark(kind=NOTICE, site=site, host=None, username=None, distance=0, text=written)
    return None


def _channel_in(text: str, breaks: set[int]) -> Mark | None:
    """A `t.me/<name>` channel starting the line, after a non-address symbol or a closed gap."""
    at = text.find(CHANNEL_HOST)
    while at != -1:
        if at == 0 or at in breaks or not text[at - 1].isalnum():
            username = _username_after(text, breaks, at + len(CHANNEL_HOST))
            if username is not None:
                return Mark(
                    kind=CHANNEL,
                    site=None,
                    host=CHANNEL_HOST,
                    username=username,
                    distance=0,
                    text=f"{CHANNEL_HOST}/{username}",
                )
        at = text.find(CHANNEL_HOST, at + 1)
    return None


def _username_in(line: str) -> Mark | None:
    """A bare `@name` from the raw line, since tidying drops the `@`."""
    found = _AT_USERNAME.search(line.lower())
    if found is None:
        return None
    username = found.group(1).strip(".")
    if len(username) < 3:
        return None
    return Mark(
        kind=USERNAME, site=None, host=None, username=username, distance=0, text=f"@{username}"
    )


def best_mark(lines: list[str]) -> Mark | None:
    """The best mark across every line read from one file."""
    best: Mark | None = None
    for line in lines:
        found = mark_in(line)
        if found is not None and _better(found, best):
            best = found
    return best


def tags_in(lines: list[str]) -> list[str]:
    """The tags the channels on one file's frame ask for; a notice files instead."""
    kinds = set()
    for line in lines:
        text, breaks = _tidied(line)
        found = _channel_in(text, breaks)
        if found is not None:
            kinds.add(found.kind)
    return [TAG_OF[kind] for kind in TAG_OF if kind in kinds]


def nearest_username(name: str, known: list[str]) -> str | None:
    """The username the read name points at: exact, or one edit for a long enough name."""
    folded = name.lower()
    for candidate in known:
        if candidate.lower() == folded:
            return candidate
    if len(folded) <= SHORT_USERNAME:
        return None
    nearest: str | None = None
    for candidate in known:
        if distance_to(folded, candidate.lower(), ceiling=1) <= 1:
            if nearest is not None:
                return None
            nearest = candidate
    return nearest
