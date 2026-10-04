# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pieces a history line is made of, the counts it says, and the address each thing it names lives at."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from urllib.parse import quote

#: Sift itself, wherever a sentence has to name it.
SIFT = "Sift"

#: The viewer, as a line names them.
YOU = "You"

#: What a stash-box is called in a sentence when the record cannot say which box it was.
A_STASH_BOX = "A stash-box"

#: What another user is called to somebody who may not be told their name. See `history._Who`.
ANOTHER_USER = "Another user"

#: What a move with no folder in it lands in, said in words because there is no folder to name.
THE_TOP = "the top of the library"

# --- the pieces a line is made of ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Piece:
    """One run of a sentence: plain words (`kind` None), or a thing of `history.LINK_KINDS` at `id`.
    `href` overrides the page, `gone` is struck through, and `rest` makes the run a FOLD."""

    text: str
    kind: str | None = None
    id: str | None = None
    href: str | None = None
    gone: bool = False
    rest: tuple[Piece, ...] = ()
    #: The words a fold is read after while it is shut (" and "), which the opened rest replaces.
    lead: str = ""


#: One finished line: its pieces in order. Their texts joined are the sentence.
Line = tuple[Piece, ...]

#: What `said` takes: words, one piece, a whole line, or nothing at all.
Part = str | Piece | Sequence[Piece] | None


def said(*parts: Part) -> Line:
    """A line made of these parts in order, plain words run together and `None` or "" skipped."""
    out: list[Piece] = []
    for part in parts:
        if part is None or part == "":
            continue
        pieces: Sequence[Piece] = (
            (Piece(part),)
            if isinstance(part, str)
            else (part,)
            if isinstance(part, Piece)
            else part
        )
        for one in pieces:
            if not one.text and not one.rest:
                continue
            last = out[-1] if out else None
            if one.kind is None and not one.rest and last and last.kind is None and not last.rest:
                out[-1] = Piece(last.text + one.text)
            else:
                out.append(one)
    return tuple(out)


def text_of(line: Sequence[Piece]) -> str:
    """The sentence the pieces say, shut: every run's words in order, a fold's lead before it."""
    return "".join(one.lead + one.text for one in line)


def thing(
    kind: str, thing_id: str, name: str, *, href: str | None = None, gone: bool = False
) -> Piece:
    """A thing a line names: its kind, its id, the words it wears, and where it goes."""
    return Piece(text=name, kind=kind, id=thing_id, href=href, gone=gone)


def things_in(line: Sequence[Piece]) -> tuple[Piece, ...]:
    """Every thing a line names, including the ones behind a fold, in order."""
    found: list[Piece] = []
    for one in line:
        if one.kind is not None:
            found.append(one)
        found.extend(things_in(one.rest))
    return tuple(found)


def capitalized(line: Line) -> Line:
    """The line with its first plain letter a capital; a name is never changed."""
    if not line or line[0].kind is not None or not line[0].text:
        return line
    head = line[0]
    return (replace(head, text=head.text[:1].upper() + head.text[1:]), *line[1:])


#: HOW MANY THINGS A LINE NAMES BEFORE IT COUNTS THE REST, one press away (`Piece.rest`).
FEED_MOST = 5


def listed(items: Sequence[Line], most: int | None = FEED_MOST) -> Line:
    """Several things as a list ("A, B and C"), folded past `most` into a run that opens in place."""
    if not items:
        return ()
    if most is None or len(items) <= most + 1:
        return said(*_joined(items))
    shown, hidden = items[:most], items[most:]
    rest = said(*_joined(hidden, lead=", ", last=" and "))
    return said(
        *_joined(shown, last=", "),
        Piece(text=f"{many(len(hidden))} more", rest=rest, lead=" and "),
    )


def _joined(items: Sequence[Line], lead: str = "", last: str = " and ") -> list[Part]:
    """`items` with ", " between them and `last` before the final one; `lead` before the first."""
    parts: list[Part] = []
    for at, one in enumerate(items):
        if at == 0:
            parts.append(lead or None)
        else:
            parts.append(last if at == len(items) - 1 else ", ")
        parts.extend(one)
    return parts


def many(count: int) -> str:
    """A number as somebody reads it: `7,700`, never `7726`: the separator every screen uses."""
    return f"{count:,}"


def plural(count: int, one: str, more: str, *, number: Callable[[int], str] = many) -> str:
    """ "1 video" or "7,700 videos": the ONE plural rule; `number` words the count."""
    return f"1 {one}" if count == 1 else f"{number(count)} {more}"


def files(count: int) -> str:
    return plural(count, "file", "files")


def people(count: int) -> str:
    return plural(count, "person", "people")


def tags(count: int) -> str:
    return "1 tag" if count == 1 else f"{many(count)} tags"


def usernames(count: int) -> str:
    return "1 username" if count == 1 else f"{many(count)} usernames"


def faces(count: int) -> str:
    """ "a face" or "3 faces", for the unnamed ones inside a list of names."""
    return "a face" if count == 1 else f"{many(count)} faces"


def may_be_faces(person: Part, person_id: str, count: int = 1) -> Line:
    """Faces Sift only suggests: "a face that may be Ada Lumen", two links kept apart by words."""
    question = thing(
        "faces", person_id, faces(count), href=faces_of_person(person_id, FACES_WAITING)
    )
    return said(question, " that may be ", person)


def counted_faces(count: int) -> str:
    """ "1 face" or "300 faces", where the phrase is the SUBJECT of its own line."""
    return "1 face" if count == 1 else f"{many(count)} faces"


def and_then(parts: Sequence[str]) -> str:
    """A list of words as somebody says it: "A", "A and B", "A, B and C"."""
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


#: A vantage word as the subject of a passive line, and the verb that agrees with it.
_SUBJECT_FORM: Mapping[str, str] = {"them": "They", "it": "It", "this file": "This file"}

_BE: Mapping[str, str] = {"them": "were", "it": "was", "this file": "was"}


def _were(count: int) -> str:
    """ "was" for one and "were" for more, so a passive line agrees with its count."""
    return "was" if count == 1 else "were"


def _value(text: str) -> str:
    """One value of one query parameter, safe to put in an address."""
    return quote(text, safe="")


def files_of_username(username_id: str) -> str:
    """Everything posted under one username, by its id: a username is not a query word."""
    return f"/browse?username={_value(username_id)}"


def username_opens(username_id: str, person_id: str | None) -> str:
    """Where pressing a username goes: its person's page, or the files under it where it has nobody."""
    if person_id:
        return f"/people/{_value(person_id)}"
    return files_of_username(username_id)


def files_in_folder(folder_id: str) -> str:
    """Everything in one folder, by its id, a library folder's own included (empty path)."""
    return f"/browse?in={_value(folder_id)}"


def folder_named(folder_id: str, words: str) -> Piece:
    """A folder a line names, linked by its id: a path is relative to its library folder, so two
    libraries' "Shoots" answer to one path, and `in:` reads a path as every folder under it."""
    return thing("folder", folder_id, words, href=files_in_folder(folder_id))


def download_row(download_id: str) -> str:
    """One row on the Downloads queue, picked out: a download that never landed has no other page."""
    return f"/downloads?row={_value(download_id)}"


#: Which wall of one person's decided faces an address opens, in the faces screen's `?show=` words.
FACES_CONFIRMED = "confirmed"

FACES_MATCHED = "matched"

FACES_WAITING = "suggested"


def faces_of_person(person_id: str, show: str) -> str:
    """One person's decided faces, on the wall that holds the ones this line counted."""
    return f"/organize/known-people/{_value(person_id)}?show={_value(show)}"


# --- the ledger's own events: ONE template per act, its slot for the page filled by the vantage --

#: Where a line is being read from. `None` is the feed, which names everything.
VANTAGE_FILE = "file"

VANTAGE_PERSON = "person"

VANTAGE_ENTITY = "entity"

#: What the thing whose page this is is CALLED in its own line; "them", since Sift knows only a name.
HERE: Mapping[str, str] = {
    VANTAGE_FILE: "this file",
    VANTAGE_PERSON: "them",
    VANTAGE_ENTITY: "it",
}

#: The possessive the page takes: "their cover", "its cover".
WHOSE: Mapping[str, str] = {VANTAGE_FILE: "its", VANTAGE_PERSON: "their", VANTAGE_ENTITY: "its"}


def _fill(template: str, slots: Mapping[str, Line]) -> Line:
    """A template with each `{slot}` replaced by its pieces."""
    parts: list[Part] = []
    for at, chunk in enumerate(re.split(r"\{(\w+)\}", template)):
        parts.extend(slots.get(chunk, ()) if at % 2 else (chunk,))
    return said(*parts)


def counted_line(count: int, counted: str, where: Part = None) -> Line:
    """What a task over many things was about: "1,200 files", or "1,200 files in <folder>"."""
    number = f"{many(count)} {counted.removesuffix('s') if count == 1 else counted}"
    return said(number, " in ", where) if where else said(number)


#: How a folded line of decisions that said the same thing says how many: "decisions, all the same".
_SAID_TIMES = ", {n} times"


def times(count: int) -> str:
    """ ", 78 times": what a fold of one decision's words says after them, wherever it is drawn."""
    return _SAID_TIMES.format(n=many(count))
