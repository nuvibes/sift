# SPDX-License-Identifier: AGPL-3.0-or-later
"""The naming words: a template, what is known about a file, and the stem they make together."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Facts:
    """What is known about a file when it is named; an unknown fact fills to nothing."""

    site: str | None = None
    username: str | None = None
    original: str = "download"
    #: When it was downloaded or added, never when posted: that is `posted`.
    when: datetime | None = None
    id: str | None = None
    #: Counted from 1; None outside a group, so a single video is not named "1".
    n: int | None = None
    title: str | None = None
    #: When it was published; never falls back to `when`.
    posted: datetime | date | None = None


#: Every word a template may use; any other stays as typed, so a typo shows in the preview.
TOKENS: dict[str, str] = {
    "site": "The site it came from, for example YouTube.",
    "creator": "Whoever posted it, where the site says. Empty on sites that have no uploader.",
    "name": "The name the file already had, which is usually the site's own title for it.",
    "date": "The date it was downloaded, as 2026-08-13.",
    "time": "The time it was downloaded, as 14-05.",
    "id": "The post's own ID on the site, for example 7401234567890123456. Empty where the site "
    "gives none.",
    "n": "Which file of the post it is, as 1, 2, 3. Empty when the post holds only one.",
    "title": "The post's title or caption, where the site has one.",
    "posted": "The date it was posted, as 2026-08-13. Empty where the site doesn't say, so it's "
    "never the download date.",
}

#: Public so a check over a template's words reads them the way `fill` does.
TOKEN = re.compile(r"\{(\w+)\}")


#: Rebuilt from what is safe, as values come from remote sites; braces stay so an unknown
#: word is still visible on disk.
_UNSAFE = re.compile(r"[^A-Za-z0-9._ (){}\-]+")

#: Two dots in a row walk up a directory.
_DOT_RUN = re.compile(r"\.{2,}")

#: Leaves room for an extension and a collision number under every filesystem's limit.
_MAX_LENGTH = 120


def fill(template: str, facts: Facts) -> str:
    """A template and what is known, as a filename stem; empty means keep the current name."""
    if not template.strip():
        return ""

    def replace(found: re.Match[str]) -> str:
        word = found.group(1).lower()
        if word not in TOKENS:
            # Left as typed, so a typo shows in the preview.
            return found.group(0)
        return _value(word, facts)

    return _tidy(TOKEN.sub(replace, template))


def without_unsafe(template: str) -> str:
    """A template with each run of unsafe characters already a space, as `fill` makes it."""
    return _UNSAFE.sub(" ", template)


def _value(word: str, facts: Facts) -> str:
    """What one recognised word stands for. Every branch is a name from `TOKENS`."""
    when = _local_time(facts.when)
    filled = {
        "site": facts.site or "",
        "creator": facts.username or "",
        "name": facts.original,
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H-%M"),
        "id": facts.id or "",
        "n": "" if facts.n is None else str(facts.n),
        "title": facts.title or "",
        "posted": _posted(facts.posted),
    }
    return filled[word]


def _posted(posted: datetime | date | None) -> str:
    """A posting time as `{date}` writes it, a bare date as it came; empty when unknown."""
    if posted is None:
        return ""
    if isinstance(posted, datetime):
        return _local_time(posted).strftime("%Y-%m-%d")
    return posted.strftime("%Y-%m-%d")


#: None is the device's zone; a module value so a test on a UTC machine can name another.
_DEVICE_ZONE: tzinfo | None = None


def _local_time(when: datetime | None = None) -> datetime:
    """The moment a name is written with, on this device's clock rather than UTC."""
    return (when or datetime.now(UTC)).astimezone(_DEVICE_ZONE)


def _tidy(name: str) -> str:
    """A filled template reduced to something that can only ever be a filename."""
    cleaned = _DOT_RUN.sub(".", _UNSAFE.sub(" ", name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-_")
    # Punctuation left stranded by an empty word, in the middle rather than at the ends.
    cleaned = re.sub(r"\s*-\s*-\s*", " - ", cleaned).strip(" .-_")
    # Brackets left empty by a word, then the joins again.
    cleaned = re.sub(r"\s*\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\s*-\s*-\s*", " - ", cleaned).strip(" .-_")
    return cleaned[:_MAX_LENGTH].strip(" .-_")


def numbered(stem: str, nth: int) -> str:
    """A taken stem with the next number on it: `holiday` and 2 is `holiday-2`."""
    return f"{stem}-{nth}"


#: Bounds a worst-case quadratic loop; past it the name is left as it was.
MOST_COLLISIONS = 500


def free_name(path: Path, stem: str) -> Path | None:
    """Where `path` can be renamed to for this stem, or None; blocking, for one thread hop."""
    target = path.with_name(f"{stem}{path.suffix}")
    if target == path:
        return None  # already called what the template asks for
    if not target.exists():
        return target
    for nth in range(1, MOST_COLLISIONS + 1):
        candidate = path.with_name(f"{numbered(stem, nth)}{path.suffix}")
        if candidate == path:
            return None
        if not candidate.exists():
            return candidate
    return None


__all__ = [
    "MOST_COLLISIONS",
    "TOKEN",
    "TOKENS",
    "Facts",
    "fill",
    "free_name",
    "numbered",
    "without_unsafe",
]
