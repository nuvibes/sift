# SPDX-License-Identifier: AGPL-3.0-or-later
"""The naming words: a template, what is known about a file, and the stem they make together.

Two features name files from one template language. A download is named in its staging directory
before it is handed to the import, and a batch rename names files already in the library. Both
fill the same words the same way, tidy the result the same way, and number a taken name the same
way, so the words live here, and each feature supplies its own facts and does its own writing.

Nothing here writes. `fill` is pure text, and `free_name` only asks the filesystem whether a name
is taken; renaming is the business of whichever feature owns the file.

A template that produces nothing usable fills to the empty string, and the caller keeps the name
the file already had. A name that silently became `.mp4` because a word was empty would be worse
than no template at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Facts:
    """What is known about a file at the moment it is named.

    Everything is optional because it genuinely is: a file host knows no uploader, a direct link
    knows no title, and a word for something unknown has to fill to nothing rather than to the
    word "None", which is what makes a template safe to write without knowing which files carry
    which facts.
    """

    #: The site's display name, as the library files it.
    site: str | None = None
    #: Whoever posted it, where anything said.
    username: str | None = None
    #: The name the file already has, without its extension. Always present.
    original: str = "download"
    #: The moment `{date}` and `{time}` write: when a download happened, or when a library file
    #: was added. Not when the media was posted: that is `posted`, below, and the two are kept apart
    #: on purpose: a word that silently meant a different date depending on the file would be worse
    #: than no word.
    when: datetime | None = None
    #: The post's own ID on its site: a TikTok post number, an Instagram post code, a RedGIFs slug,
    #: a Pixeldrain file ID. Often the only part of a name that is both short and unique, which is
    #: why it is a fact of its own rather than something to be read back out of `original`.
    id: str | None = None
    #: Which file of a group this is, counted from 1. None where there is no group, so a template
    #: that uses it on a single video does not name every one of them "1".
    n: int | None = None
    #: The post's title or caption, as the site wrote it, or the title a file was given.
    title: str | None = None
    #: When the post was PUBLISHED, where anything says. A moment is shown on this device's clock,
    #: the way `when` is; a bare date (all some sites give) is shown as it is. None where nothing
    #: says, and never the `when` moment, which is what `{date}` already means.
    posted: datetime | date | None = None


#: Every word a template may use, and what fills it in. A closed set: a template is written by a
#: person into a box, and anything not listed here stays as it was typed rather than being quietly
#: dropped, so a typo is visible in the preview instead of producing a shorter name than expected.
#:
#: The sentences are a download's, because that is where a person first meets the words. A feature
#: that names other files says what each word means for them in its own copy, over these same keys.
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

#: One word in a template: `{name}`. Public because a check over a template's words (which words a
#: Site's default uses) must read them the way `fill` does, not with a second pattern of its own.
TOKEN = re.compile(r"\{(\w+)\}")


#: Anything that cannot appear in a filename. A rebuild from what is safe rather than a list of what
#: is not: the values arrive from remote sites, and a separator or a run of dots that got through
#: would move the file out of the folder it was meant for.
#
# Braces are permitted, and that is deliberate rather than an oversight: a word nobody recognises is
# left as it was typed, and it has to stay recognisable as a word both in the preview and on disk.
# A file called `{uploaderr} A video title.mp4` says exactly what went wrong; the same name with the
# braces quietly removed says nothing at all.
_UNSAFE = re.compile(r"[^A-Za-z0-9._ (){}\-]+")

#: A run of dots. Interior dots are ordinary in a filename; two in a row are the thing that walks up
#: a directory, and they arrive here from a remote site.
_DOT_RUN = re.compile(r"\.{2,}")

#: Long enough to stay readable, short enough that a name plus an extension plus a collision number
#: clears every filesystem's limit with room to spare.
_MAX_LENGTH = 120


def fill(template: str, facts: Facts) -> str:
    """A template and what is known, as a filename stem. Empty when the template yields nothing.

    Empty is a real answer and the caller acts on it by keeping the name the file already had. A
    template of `{creator} - {name}` on a file with no uploader would otherwise produce a name
    beginning with a dash and a space, which is a file somebody has to rename by hand.
    """
    if not template.strip():
        return ""

    def replace(found: re.Match[str]) -> str:
        word = found.group(1).lower()
        if word not in TOKENS:
            # Left as typed. A template is written by a person, and a word nobody recognises is a
            # typo, which they can see in the preview, where a silently dropped one is invisible
            # until a hundred files have been named without it.
            return found.group(0)
        return _value(word, facts)

    return _tidy(TOKEN.sub(replace, template))


def without_unsafe(template: str) -> str:
    """A template with each run of characters no name may hold already a space, as `fill` makes it.

    The same name comes out of `fill` either way, because the tidying turns those runs into a space
    and folds spaces together whatever they were next to. What differs is that the template itself
    can then be carried where a slash or a leading `~` would be read as a path.
    """
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
    """A posting time as `{posted}` writes it: the shape `{date}` uses, and nothing when unknown.

    A moment goes through the same clock as `when`, so a post made late in the evening is not
    dated tomorrow for anybody west of Greenwich. A bare date (which is all some sites give) is
    already a calendar day and is written as it came: converting it would move it.

    Empty and never "now". `{date}` already means the other moment, and a `{posted}` that fell
    back to it would put a date in the name that looks exactly like a posting date and is not one.
    """
    if posted is None:
        return ""
    if isinstance(posted, datetime):
        return _local_time(posted).strftime("%Y-%m-%d")
    return posted.strftime("%Y-%m-%d")


#: The zone a name is written in. None is the device's own, with its daylight-saving rules, which is
#: what a name is always written in. A module value rather than a literal `astimezone()` only so a
#: test can name a zone: on a machine whose own zone is UTC (every CI runner), a test of "local,
#: not UTC" would otherwise pass whether or not the conversion happened.
_DEVICE_ZONE: tzinfo | None = None


def _local_time(when: datetime | None = None) -> datetime:
    """The moment a name is written with, on this device's own clock.

    LOCAL, not UTC. A file's name is read by the person whose clock it is: in UTC a download made
    at 03:37 by the clock on the wall would be named `07-37`, and the date would roll over hours
    early for anybody west of Greenwich. `astimezone()` with no argument is the device's zone, so
    an aware moment from anywhere is shown in it, and "now" is taken aware in the first place so
    there is no naive time for the conversion to guess about.

    One place, so a name on disk and every preview of one cannot disagree about which clock they
    read.
    """
    return (when or datetime.now(UTC)).astimezone(_DEVICE_ZONE)


def _tidy(name: str) -> str:
    """A filled template reduced to something that can only ever be a filename.

    The tidying matters as much as the substitution. An empty word leaves the punctuation that was
    around it (a dangling dash, a doubled space, a leading separator), and a library full of
    files called ` - video` is what a template feature looks like when nobody thought about the
    files that do not carry every fact.
    """
    cleaned = _DOT_RUN.sub(".", _UNSAFE.sub(" ", name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-_")
    # Punctuation left stranded by an empty word, in the middle rather than at the ends.
    cleaned = re.sub(r"\s*-\s*-\s*", " - ", cleaned).strip(" .-_")
    # Brackets an empty word was written inside: `{title} ({posted})` where nothing says when
    # something was posted. Then the joins again, since removing them can leave two meeting.
    cleaned = re.sub(r"\s*\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\s*-\s*-\s*", " - ", cleaned).strip(" .-_")
    return cleaned[:_MAX_LENGTH].strip(" .-_")


def numbered(stem: str, nth: int) -> str:
    """A taken stem with the next number on it: `holiday` and 2 is `holiday-2`.

    The one shape for a number that keeps two files apart, whether the second is a photo of the same
    post in a download or the fifth file of a batch rename, so a library does not end up numbering
    the same problem two ways.
    """
    return f"{stem}-{nth}"


#: How many times a taken name is tried with a number on the end before the file is left alone.
#:
#: Generous rather than tuned, because past it the answer is to leave the name, and nothing is lost.
#: It bounds a loop that is O(n^2) in the worst case (every file of one group filling to the same
#: stem, which is exactly what a template with no per-file word does).
MOST_COLLISIONS = 500


def free_name(path: Path, stem: str) -> Path | None:
    """Where `path` can be renamed to, given the stem a template asked for. None to leave it.

    Blocking, and meant for one thread hop, probes and all. Every probe is a filesystem stat, and a
    stat is cheap next to the cost of getting onto a thread and back, so a hop per probe would have
    spent more on the handover than on the question.
    """
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
