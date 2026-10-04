# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding whether one released version is newer than another.

The comparison follows the usual three-number scheme: a major, a minor and a patch, optionally
followed by a pre-release marker (`1.4.0-rc.1`) and optionally by build metadata (`1.4.0+abc123`)
that names the same release and so takes no part in the ordering.

Parsing is deliberate about two things. A version that will not parse is not guessed at: it comes
back as nothing, and a caller that cannot read either side of a comparison says "no update" rather
than offering one it cannot justify. And a pre-release sorts *below* the release it leads to, so
someone running 1.4.0 is never told that 1.4.0-rc.1 is an upgrade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Optional leading `v`, three numbers, an optional `-pre.release`, an optional `+build`. Written
#: out rather than assembled so the shape it accepts is readable in one line.
_PATTERN = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

_DIGITS = re.compile(r"^\d+$")


@dataclass(frozen=True, slots=True)
class Version:
    """A parsed version. Build metadata is dropped: it does not distinguish one release from
    another, so keeping it would only invite a comparison that used it."""

    release: tuple[int, int, int]
    prerelease: tuple[str, ...] = ()

    def __lt__(self, other: Version) -> bool:
        if self.release != other.release:
            return self.release < other.release
        return _prerelease_lt(self.prerelease, other.prerelease)


def parse(text: str) -> Version | None:
    """A version read out of a release feed or a package, or None if it is not one.

    Leading and trailing whitespace is tolerated because feeds carry tag names, which pick it up.
    Nothing else is repaired.
    """
    match = _PATTERN.match(text.strip())
    if match is None:
        return None
    return Version(
        release=(int(match["major"]), int(match["minor"]), int(match["patch"])),
        prerelease=tuple(match["pre"].split(".")) if match["pre"] else (),
    )


def is_newer(candidate: str, current: str) -> bool:
    """Whether `candidate` is a later release than `current`.

    False if either will not parse. An unreadable version is not evidence of an update, and the
    banner this answer drives is one a person is asked to act on, so the doubtful case is silence.
    """
    later = parse(candidate)
    running = parse(current)
    if later is None or running is None:
        return False
    return running < later


def _prerelease_lt(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Order two pre-release markers, given equal release numbers.

    No marker at all is the finished release and sorts above every pre-release of it. Otherwise the
    dot-separated parts are compared one at a time: all-digit parts numerically, anything else as
    text, and a numeric part sorts below a textual one. If every shared part matches, the one with
    fewer parts sorts first.
    """
    if not left:
        return False
    if not right:
        return True

    for a, b in zip(left, right, strict=False):
        if a == b:
            continue
        a_numeric, b_numeric = _DIGITS.match(a) is not None, _DIGITS.match(b) is not None
        if a_numeric and b_numeric:
            return int(a) < int(b)
        if a_numeric != b_numeric:
            return a_numeric
        return a < b

    return len(left) < len(right)


__all__ = ["Version", "is_newer", "parse"]
