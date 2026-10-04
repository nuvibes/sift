# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading one filename for the Site, the username and the post it came from."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sift.kernel.text import clean_stored_text
from sift.slices.suggestions.naming_words import (
    _BRACKETED,
    STOP_WORDS,
    Segment,
    classify,
    reads_like_a_name,
    strip_noise,
)

# --- filenames --------------------------------------------------------------------------------


def _fields(filename: str) -> list[str]:
    """A filename split the way a tool that generated it separated the parts."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    without_ids = _BRACKETED.sub(" ", clean_stored_text(stem))
    return [part.strip() for part in without_ids.split(" - ") if part.strip()]


def repeated_prefix(filenames: Sequence[str]) -> str:
    """The first field every filename in a folder shares, or empty if they do not share one."""
    if len(filenames) < 2:
        return ""
    prefixes = {tuple(_fields(name)[:1]) for name in filenames}
    if len(prefixes) != 1:
        return ""
    (only,) = prefixes
    if not only:
        return ""
    prefix = only[0]
    return prefix if reads_like_a_name(prefix) else ""


#: A whole name that is one identifier and nothing else.
_ALL_ID = re.compile(
    # `(?![0-9a-f])` rather than `\b`: an underscore is a word character to a regex, so `\b` does
    # not fire between a UUID and the `_509539` some client appends to it.
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])|^[0-9a-f]{16,}$",
    re.IGNORECASE,
)


#: A trailing part a machine put there, and the evidence that a machine assembled this name at all.
_STRONG_MACHINE = re.compile(
    r"""(?ix)
    [\s._\-]*
    (?:
        [\[\(\{][^\]\)\}]*[\]\)\}]      # a bracketed id
      | \d{4}[-_.]\d{1,2}[-_.]\d{1,2}         # 2020-05-07
      | \d{1,2}[-_.]\d{1,2}[-_.]\d{2,4}       # 07-05-2020
      | \d{2}[-_.]\d{2}[-_.]\d{2}             # 21.22.22
      | \d{4,}                                 # four or more digits
      | (?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{6,}   # a hash or an opaque id
    )
    $"""
)


#: A trailing part that describes the file rather than identifying it, like `hd` or `1080p`.
_WEAK_MACHINE = re.compile(
    r"""(?ix)
    [\s._\-]*
    (?:
        \d{3,4}[pi]                            # 1080p
      | from[-_]?\d+[a-z]?                     # a clip range a downloader appended
      | \d{1,3}[a-z]?                          # a small counter, or `23s`
      | [a-z]{1,6}                              # a word, kept only if the vocabulary knows it
    )
    $"""
)


#: The shortest a name peeled out of an assembled filename may be: where a real username starts.
MIN_MACHINE_NAME = 3


#: And the shortest one may be when the last cut fell INSIDE a token rather than at a separator.
MIN_PEELED_NAME = 4


#: How many trailing parts one filename may have peeled off it.
_MAX_PEEL = 8


@dataclass(frozen=True, slots=True)
class _Peeled:
    """One trailing machine part taken off a filename, and what taking it off proved."""

    rest: str
    evidence: bool
    inside: bool


def _peel_once(rest: str) -> _Peeled | None:
    """One trailing machine part removed. None when nothing came off."""
    shorter = _STRONG_MACHINE.sub("", rest, count=1)
    if shorter != rest:
        return _Peeled(shorter.strip(" ._-"), True, _cut_inside(rest, shorter))
    shorter = _WEAK_MACHINE.sub("", rest, count=1)
    if shorter == rest:
        return None
    taken = rest[len(shorter) :].strip(" ._-").casefold()
    # A bare word only comes off if this file's own vocabulary says it describes the file.
    if taken.isalpha() and taken not in STOP_WORDS:
        return None
    return _Peeled(shorter.strip(" ._-"), False, _cut_inside(rest, shorter))


def _cut_inside(rest: str, shorter: str) -> bool:
    """Whether the part that came off was joined to what is left, with no separator between them."""
    return bool(shorter) and rest[len(shorter) :][:1] not in " ._-"


def username_in_filename(filename: str) -> str:
    """The name a machine-assembled filename opens with, or empty.

    **Peeled from the end rather than split from the front**

    **At least one part has to be an ID, a date or a hash.**
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    rest = clean_stored_text(stem).strip()
    if _ALL_ID.match(rest.strip()):
        return ""
    evidence = False
    guessed = False
    for _ in range(_MAX_PEEL):
        taken = _peel_once(rest)
        if taken is None or not taken.rest.strip(" ._-"):
            break
        rest = taken.rest
        evidence = evidence or taken.evidence
        guessed = guessed or taken.inside
    if not evidence:
        return ""
    # The brand the service stamped on the front, where there is one.
    parts = [part for part in re.split(r"[\s_]+", rest) if part]
    while parts and classify(parts[0]) is Segment.SITE:
        parts = parts[1:]
    candidate = " ".join(parts)
    if not candidate or _ALL_ID.match(candidate.replace(" ", "")):
        return ""
    # Long enough to be somebody.
    if len(candidate.replace(" ", "")) < MIN_MACHINE_NAME:
        return ""
    found = strip_noise(candidate) if reads_like_a_name(candidate) else ""
    # And a second floor, on the NAME rather than on the candidate, for a filename one of whose cuts
    # fell inside a token.
    if guessed and len(found.replace(" ", "")) < MIN_PEELED_NAME:
        return ""
    return found


#: A number that could name a username: six to fourteen digits. Below six is a counter or an index.
_USERNAME_NUMBER = re.compile(r"^\d{6,14}$")


#: The site's own number for ONE FILE: fifteen digits or more.
_MEDIA_NUMBER = re.compile(r"^\d{15,}$")


def username_and_number_in_filename(filename: str) -> tuple[str, str] | None:
    """The username AND the site's own number for it, when a filename carries both.

    **Position tells the username number from the timestamp, and WIDTH cannot:**

    **And it is only read where a MEDIA ID stands before it**
    """
    username = username_in_filename(filename)
    if not username:
        return None
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    fields = [part for part in re.split(r"[\s._\-]+", clean_stored_text(stem)) if part]
    seen_post = False
    numbers: list[str] = []
    for field in fields:
        if _MEDIA_NUMBER.match(field):
            seen_post = True
            continue
        if seen_post and _USERNAME_NUMBER.match(field):
            numbers.append(field)
    # **One candidate, or none.** Two candidate numbers after the media id is a shape this does not
    # recognise, and letting the last one win by nothing more than being last would be a coin toss.
    if len(set(numbers)) != 1:
        return None
    return (username, numbers[0])


# --- the shapes a tool's own naming gives away --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Username:
    """One username on one site, as a filename gives it away."""

    site: str
    name: str
    #: The site's own number for the username, which never changes when the name does.
    number: str


@dataclass(frozen=True, slots=True)
class Posted:
    """Where one file came from, read off the SHAPE of its own name."""

    #: The site the SHAPE names, never the username. See `SIGNATURES`.
    site: str
    #: The username, spelled the way the tool wrote it.
    username: str
    #: The site's own number for that username, which is the one thing about it that never changes.
    number: str
    #: The site's own number for THIS FILE, not for the post it was in.
    media: str
    #: The unix second the tool wrote the file, EMPTY where the stamp is not a moment.
    saved: str

    @property
    def where(self) -> Username:
        """The username half of this reading, which is what a filing is filed under."""
        return Username(site=self.site, name=self.username, number=self.number)


def one_post(posted: Posted) -> tuple[str, str]:
    """What two files must agree on to have been posted together: the username, and the second.

    **THE SECOND, BECAUSE THE NUMBER THAT LOOKS LIKE A POST ID IS NOT ONE.**

    **It is not exact and the inexactness is named rather than hidden.**

    **An empty second is not a post and the caller has to read it as one.**
    """
    return (posted.number, posted.saved)


def posts_among(readings: Iterable[tuple[str, Posted]]) -> dict[Username, dict[str, list[str]]]:
    """`{username: {post: [asset id, ...]}}` for files whose names were read. Pure work over strings.

    **A post is keyed by the SECOND, and never by anything read off the files in hand.**
    """
    found: dict[Username, dict[tuple[str, str], dict[str, int]]] = {}
    for asset_id, posted in readings:
        by_post = found.setdefault(posted.where, {})
        # Keyed by the asset so a file present at two paths is one file in its post.
        by_post.setdefault(one_post(posted), {})[asset_id] = int(posted.media)
    return {
        where: {
            post[1]: sorted(files, key=lambda one: files[one]) for post, files in by_post.items()
        }
        for where, by_post in found.items()
    }


#: THE SHAPE A TOOL WRITES, AND THE SITE THAT SHAPE BELONGS TO.


@dataclass(frozen=True, slots=True)
class Signature:
    """One tool's own naming, and what reading it yields."""

    #: The site this tool fetches from. From the SHAPE, never from anything inside the name.
    site: str
    #: The whole name, anchored at both ends.
    shape: re.Pattern[str]
    #: How the `saved` group is spelled, for `datetime.strptime`, or None where it is already a
    #: unix second. See `_saved_second` for why both are turned into one kind of value.
    clock: str | None = None

    @property
    def names_username(self) -> bool:
        """Whether this shape spells the username out as well as saying which one it is."""
        return "username" in self.shape.groupindex


SIGNATURES: tuple[Signature, ...] = (
    Signature(
        site="Instagram",
        shape=re.compile(
            r"^(?P<username>\S.*?)_(?P<saved>\d{10})_(?P<media>\d{19})_(?P<number>\d{6,14})$"
        ),
    ),
    Signature(
        site="Instagram",
        shape=re.compile(
            r"^(?P<saved>\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}) "
            r"(?P<media>\d{19})_(?P<number>\d{6,14})$"
        ),
        clock="%Y-%m-%d %H.%M.%S",
    ),
)


#: How near the start of the epoch a stamp may fall and still be taken for a moment.
EPOCH_WINDOW = 86_400


def _saved_second(signature: Signature, spelling: str) -> str:
    """The moment a shape's stamp names, as the digits of a unix second, or empty for no moment.

    **ONE KIND OF VALUE, because the post id column holds it.**

    **The wall clock is read as though it were UTC, and that is deliberate.**
    """
    if signature.clock is None:
        second = int(spelling)
    else:
        try:
            second = int(
                datetime.strptime(spelling, signature.clock).replace(tzinfo=UTC).timestamp()
            )
        except ValueError:
            # A shape can match a thirtieth of February; a calendar cannot.
            return ""
    return "" if abs(second) < EPOCH_WINDOW else str(second)


def posted_in_filename(filename: str) -> Posted | None:
    """The site, the username and the username number one filename's shape gives away, or None."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    cleaned = clean_stored_text(stem).strip()
    for signature in SIGNATURES:
        found = signature.shape.match(cleaned)
        if found is None:
            continue
        username = found.group("username") if signature.names_username else ""
        if signature.names_username and not reads_like_a_name(username):
            return None
        return Posted(
            site=signature.site,
            username=username,
            number=found.group("number"),
            media=found.group("media"),
            saved=_saved_second(signature, found.group("saved")),
        )
    return None


#: A folder named for one username on one site: `<username> (RedGifs)`.
_USERNAME_FOLDER = re.compile(r"^\s*(?P<username>[^()]+?)\s*\((?P<site>[^()]+)\)\s*$")


def mirror_in_folder(name: str, *, sites: Iterable[str] = ()) -> tuple[str, str] | None:
    """`(username, site)` where a folder's name says both, or None. The username is RAW (see
    `Posted`).

    **The site in the brackets is what the files are filed under, even where it only re-hosts.**
    """
    found = _USERNAME_FOLDER.match(clean_stored_text(name))
    if found is None:
        return None
    username, site = found.group("username"), found.group("site")
    if classify(site, sites=sites) is not Segment.SITE:
        return None
    return (username, site) if reads_like_a_name(username) else None


def person_in_filename(filename: str, *, after_prefix: bool) -> str:
    """The name one filename claims, or empty."""
    fields = _fields(filename)
    wanted = 1 if after_prefix else 0
    machine = username_in_filename(filename)
    # There has to be something AFTER the name for it to have been a name rather than the whole
    # title.
    if len(fields) <= wanted or (len(fields) < wanted + 2 and not after_prefix):
        # Nothing the ` - ` reading can use.
        return machine
    candidate = fields[wanted]
    found = strip_noise(candidate) if reads_like_a_name(candidate) else ""
    return found or machine
