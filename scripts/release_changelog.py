# SPDX-License-Identifier: AGPL-3.0-or-later
"""CHANGELOG.md read as a release reads it: its sections and the notes each one publishes."""

from __future__ import annotations

import datetime
import itertools
import re
from typing import NamedTuple

from release_common import ROOT, ReleaseFailed, _version_key

#: The section that collects changes before a version carries them.
UNRELEASED = "Unreleased"


_SECTION = re.compile(r"^##(?!#)\s*(?P<title>.*?)\s*$")


_VERSION_TITLE = re.compile(
    r"^(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)(?:\s+-\s+(?P<date>\d{4}-\d{2}-\d{2}))?$"
)


_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


_LINK_DEFINITION = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s*\S")


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


_CODE_SPAN = re.compile(r"(`+).+?\1")


#: What the Updates screen does not draw: a note written with one of these reads differently there
#: than on the release page. Looked for outside code, where the characters are meant literally.
_UNDRAWN = (
    (re.compile(r"<[A-Za-z/!?][^>\n]*>"), "an HTML tag, which the Updates screen shows as text"),
    (re.compile(r"^\s{0,3}\|"), "a table, which the Updates screen shows as rows of pipes"),
    (re.compile(r"^\s{0,3}>"), "a quote, which the Updates screen shows with its marker"),
)


class ChangelogSection(NamedTuple):
    """One `## ` section: Unreleased or a version, its date if it has one, and its text."""

    title: str
    date: str | None
    body: str


def read_changelog(text: str) -> list[ChangelogSection]:
    """Every section in order: Unreleased first, then each version once, newest first."""
    sections: list[ChangelogSection] = []
    title: tuple[str, str | None] | None = None
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        opened = _FENCE.match(line)
        if fence is not None:
            if opened is not None and opened.group(1) == fence:
                fence = None
        elif opened is not None:
            fence = opened.group(1)
        elif (heading := _SECTION.match(line)) is not None:
            if title is not None:
                sections.append(ChangelogSection(*title, "\n".join(lines)))
            title, lines = _section_title(heading["title"]), []
            continue
        lines.append(line)
    if title is not None:
        sections.append(ChangelogSection(*title, "\n".join(lines)))
    _refuse_out_of_order(sections)
    return sections


def _refuse_out_of_order(sections: list[ChangelogSection]) -> None:
    titles = [one.title for one in sections]
    if UNRELEASED in titles[1:]:
        raise ReleaseFailed(f"CHANGELOG.md has `## {UNRELEASED}` below a version; it goes first.")
    versions = [one for one in titles if one != UNRELEASED]
    for newer, older in itertools.pairwise(versions):
        if _version_key(newer) <= _version_key(older):
            raise ReleaseFailed(
                f"CHANGELOG.md lists {older} below {newer}. Versions run newest first, each once."
            )


def _section_title(title: str) -> tuple[str, str | None]:
    if title == UNRELEASED:
        return UNRELEASED, None
    found = _VERSION_TITLE.match(title)
    if found is None:
        raise ReleaseFailed(
            f"CHANGELOG.md has a section headed {title!r}. A section is `## {UNRELEASED}`, "
            "`## <version>` or `## <version> - <YYYY-MM-DD>`."
        )
    if found["date"] is not None:
        try:
            datetime.date.fromisoformat(found["date"])
        except ValueError:
            raise ReleaseFailed(
                f"CHANGELOG.md dates {found['version']} {found['date']}, which is not a date."
            ) from None
    return found["version"], found["date"]


def release_notes(body: str) -> str:
    """A section's published text; what the Updates screen would draw differently is refused."""
    kept: list[str] = []
    problems: list[str] = []
    fence: str | None = None
    for line in _HTML_COMMENT.sub("", body).splitlines():
        opened = _FENCE.match(line)
        if fence is not None:
            if opened is not None and opened.group(1) == fence:
                fence = None
        elif opened is not None:
            fence = opened.group(1)
        elif _LINK_DEFINITION.match(line):
            continue
        else:
            prose = _CODE_SPAN.sub("", line)
            problems += [f"{why}: {line.strip()}" for rule, why in _UNDRAWN if rule.search(prose)]
        kept.append(line.rstrip())
    if problems:
        raise ReleaseFailed("CHANGELOG.md uses " + "\n  ".join(problems))
    notes = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")
    limit = notes_limit()
    if len(notes) > limit:
        raise ReleaseFailed(
            f"the notes are {len(notes)} characters and the Updates screen keeps {limit}. "
            "Say less, or link to the rest from the release page."
        )
    return notes


def notes_limit() -> int:
    """The most release-notes text the backend keeps, read from the backend itself."""
    source = ROOT / "src" / "sift" / "slices" / "update_notify" / "service.py"
    found = re.search(r"^MAX_NOTES_CHARS = ([\d_]+)$", source.read_text(encoding="utf-8"), re.M)
    if found is None:
        raise ReleaseFailed(f"{source} no longer declares MAX_NOTES_CHARS.")
    return int(found.group(1))


def notes_for(version: str, sections: list[ChangelogSection], *, dated: bool) -> str:
    """The published notes of `version`, refusing a section that is missing, undated or empty."""
    section = next((one for one in sections if one.title == version), None)
    if section is None:
        raise ReleaseFailed(
            f"CHANGELOG.md has no section for {version}. Write `## {version} - <YYYY-MM-DD>` with "
            "what it changes for the people who install it."
        )
    if dated and section.date is None:
        raise ReleaseFailed(
            f"CHANGELOG.md's section for {version} has no date. A release is dated the day it is "
            f"published: `## {version} - YYYY-MM-DD`."
        )
    notes = release_notes(section.body)
    if not notes.strip():
        raise ReleaseFailed(f"CHANGELOG.md's section for {version} is empty.")
    return notes


def changelog_covers(version: str, sections: list[ChangelogSection]) -> bool:
    """Whether `version` has a section of its own, or changes waiting under Unreleased."""
    return any(
        one.title in (version, UNRELEASED) and release_notes(one.body).strip() for one in sections
    )
