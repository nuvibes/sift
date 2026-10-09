# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether one released version is newer than another; an unreadable one is never an update."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERN = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

_DIGITS = re.compile(r"^\d+$")


@dataclass(frozen=True, slots=True)
class Version:
    """A parsed version; build metadata is dropped, as it names the same release."""

    release: tuple[int, int, int]
    prerelease: tuple[str, ...] = ()

    def __lt__(self, other: Version) -> bool:
        if self.release != other.release:
            return self.release < other.release
        return _prerelease_lt(self.prerelease, other.prerelease)


def parse(text: str) -> Version | None:
    """A version read out of a release feed or a package, or None if it is not one."""
    match = _PATTERN.match(text.strip())
    if match is None:
        return None
    return Version(
        release=(int(match["major"]), int(match["minor"]), int(match["patch"])),
        prerelease=tuple(match["pre"].split(".")) if match["pre"] else (),
    )


def is_newer(candidate: str, current: str) -> bool:
    """Whether `candidate` is a later release than `current`; False if either will not parse."""
    later = parse(candidate)
    running = parse(current)
    if later is None or running is None:
        return False
    return running < later


def _prerelease_lt(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Order two pre-release markers of one release; no marker sorts above every pre-release."""
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
