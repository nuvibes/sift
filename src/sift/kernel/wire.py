# SPDX-License-Identifier: AGPL-3.0-or-later
"""The base every model that crosses the wire is built on.

Nothing in Sift serialises with `exclude_unset`, `exclude_none` or `exclude_defaults`, so every
declared key is in every reply. Pydantic leaves a defaulted field out of `required`, which a
generated client reads as "may be absent"; `json_schema_serialization_defaults_required` makes the
serialisation schema say what is true. It leaves the validation schema alone, so one base serves
requests too; a model read one way and written the other is published as `-Input` and `-Output`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:  # pragma: no cover (an annotation, never imported at run time)
    from sift.kernel.access.catalog import EntityMaker
    from sift.kernel.access.history import Event
    from sift.kernel.access.history import Link as HistoryLinkSource
    from sift.kernel.access.sentences import Piece


class Wire(BaseModel):
    """A shape that crosses the wire, in either direction.

    Every request body and reply inherits this, and a gate refuses a bare `BaseModel` so no model
    opts out of saying what it sends. One base for both directions, since the setting is inert on
    validation. A subclass's `model_config` is merged over this one, not replacing it.
    """

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class Refused(ValueError):
    """A value refused in a sentence written for the person who typed it.

    The reply carries the sentence and names its field, so a form says it beside that field; a
    plain ValueError stays a validation report, which reads as a fault.
    """


class FacetValue(Wire):
    """One value a dimension takes, and how many of the rows on screen carry it.

    `count` counts the set the wall shows, from the same statement, in the wall's own noun (files,
    people, sites); never the library's number. `value` is written as the wall reads it back, so a
    row builds a filter finding exactly what it counted. `label` is filled only where the value is
    an id (a network, a tag), with the row's name, so the client never looks names up through a
    second read with its own permission rule.
    """

    value: str
    count: int
    label: str | None = None


class FacetCounts(Wire):
    """What the things a wall reaches are made of, along one dimension.

    In the kernel because walls in several slices answer with it and a slice may not import
    another's models. A value this viewer may not be told about is absent, never listed with a
    zero, which would say the thing exists.
    """

    facet: str
    values: list[FacetValue]


# --- a history ---------------------------------------------------------------------------------
#
# In the kernel because three slices answer a history (a file, a person, a queue) with one shape;
# three copies would be three types on the wire for one thing.


class MadeBy(Wire):
    """WHO INVENTED a row, as against who has since described it.

    Anything Sift makes says so, a box being one answer among several. In the kernel because
    several kinds of row in several slices carry it, two with no stash-box at all.
    """

    #: `stash_box`, `sift`, `you`, `another_user` or `somebody`: the same five words the
    #: history's `actor` uses, decided by the same function. Never a name for another user.
    kind: str
    #: Which pass, where the maker was Sift or a box (`folder`, `filename`, `download`, `faces`,
    #: `stash`, `watermark`, `mirror`, `produced`, `archive`). Null where the row does not say, and
    #: for every maker that is a person.
    via: str | None = None
    #: Which act, where the pass was `produced`: `compress` or `edit` (`vocabulary.MADE_ACTS`), so a
    #: screen draws that verb's glyph. Null otherwise.
    act: str | None = None
    #: The box that made it. Null for every other maker AND for a forgotten box, which the screen
    #: must not tell apart (see `made_by`).
    box_id: str | None = None
    box_name: str | None = None
    box_slug: str | None = None


def made_by_wire(made: EntityMaker) -> MadeBy:
    """One kernel maker as the shape that crosses the wire.

    One translation for every slice that answers with it, so a new field cannot be missed in a copy.
    """
    return MadeBy(
        kind=made.actor.value,
        via=made.via,
        act=made.act,
        box_id=made.box_id,
        box_name=made.box_name,
        box_slug=made.box_slug,
    )


class UndoPoint(Wire):
    """What would take one event back.

    The kind travels with the id because there are two doors (a move through the organizer, a
    decision through the workbench), and a client must not keep its own copy of that mapping.
    """

    #: `move` or `decision`. Both doors are live. See the kernel's history.
    kind: str
    id: str


class HistoryLink(Wire):
    """One thing a history sentence names, and where that thing lives.

    The name travels with the id because the sentence is sent as words: a client links it by
    finding that run of text, and one that draws no links reads the same sentence.
    """

    #: `person`, `site`, `tag`, `collection`, `photo_set`, `folder`, `asset` or `face_pile`. A kind
    #: the client does not know is drawn as plain text: an older client meeting a newer server is
    #: the ordinary case.
    kind: str
    #: Whatever that kind's page is addressed by. A folder has no id and is its path.
    id: str
    #: Exactly as it appears in `what`.
    name: str
    #: Where it goes when not the kind's own page (a wall filtered to exactly what a line counted);
    #: null means that page. A client screen, not an API address.
    href: str | None = None
    #: The thing is gone, so the words are drawn and never linked: struck-through text says what
    #: happened, where a link would land on "no such file".
    gone: bool = False


class HistoryPiece(Wire):
    """ONE RUN OF A HISTORY LINE: plain words, or words standing for a thing, each where it sits.

    A line is a list of these, built on the server one builder per act; the client draws and builds
    nothing. A thing carries its kind, id, address and whether it has gone; plain words carry none.
    Nothing searches a sentence for a name, which would link every "d" in "Added to d". `rest`
    makes a run a fold: shut it reads " and 14 more", opened `rest` replaces that.
    """

    text: str
    #: Null for plain words; otherwise one of the history link kinds, or `place` for an address.
    kind: str | None = None
    id: str | None = None
    href: str | None = None
    gone: bool = False
    rest: list[HistoryPiece] = []
    #: What a shut fold is read after (" and "); the opened rest replaces it with the run.
    lead: str = ""


class HistoryAway(Wire):
    """A way OUT of Sift from a History line: its words and the address, opened in a new tab."""

    label: str
    href: str


class HistoryDetail(Wire):
    """One group of things a folded line stands for, with the words its sentence counted them in.

    A line for a whole press counts its things ("19 people, the site and 7 tags") and opens to
    them, grouped as the sentence grouped them. `words` is the phrase from the sentence itself, so
    the heading and the count are one string and the client has no second vocabulary.
    """

    #: The link kind every entry wears, so one glyph is drawn for the group.
    kind: str
    #: Exactly as it appears in `what`: "19 people", "the site", "7 tags".
    words: str
    #: The things themselves. Never empty: an act with nothing to open is only counted.
    entries: list[HistoryLink] = []


class HistoryEvent(Wire):
    """One thing that happened to the subject of a history, ready to draw.

    `what` is a finished sentence in the app's own voice, so the words match the rest of Sift.
    `at` is epoch seconds, null for a row from before moments were recorded (drawn as such, sorted
    oldest). `actor_name` is null when there is nothing to name, or the viewer is not an admin and
    the actor is another user.
    """

    at: int | None = None
    #: `you`, `another_user`, `somebody`, `sift` or `stash_box`.
    actor: str
    actor_name: str | None = None
    #: One of the kernel's closed kinds. An unknown one is drawn as "something happened", since an
    #: older client meeting a newer server is ordinary for a self-hosted app.
    kind: str
    #: THE LINE, as pieces. See `HistoryPiece`. Its texts joined are `what`.
    pieces: list[HistoryPiece] = []
    #: The line as plain words, read off `pieces`: for a screen reader's name, a search, a test.
    what: str
    #: What the row's mark means, as its tooltip: what KIND of act the line is. The server's words,
    #: beside the sentences they describe (`sentences.means`).
    means: str = ""
    undo: UndoPoint | None = None
    #: Already taken back, keeping its place: a history that loses its reversals reads as if nothing
    #: happened.
    reversed: bool = False
    #: What this line stands for when it is several acts (the people one press named), one group per
    #: phrase the sentence counted, in its order (see `HistoryDetail`); empty otherwise. A list the
    #: row opens, never searched for in the sentence.
    detail: list[HistoryDetail] = []
    #: How a row arrived (`stash`, `faces`, `folder`, `filename`, `watermark`), in the `enriched:`
    #: filter's own words so the mark and the filter mean the same; null where it cannot say.
    via: str | None = None
    #: Which verb made a copy (`trim`, `compress`, `gif`, ...), so the editor's mark is drawn
    #: without reading the sentence; null for other kinds.
    how: str | None = None
    #: The receipt this line belongs to: the server folds a press and its receipt into one line on
    #: this id, and a client groups by it rather than comparing sentences.
    receipt: str | None = None
    #: When a line that stands for a run began (a day's downloads), with `at` its last.
    #: Null on every line that is one act.
    since: int | None = None
    #: Where the line's subject lives OUTSIDE Sift ("Open on" a box's page); null when nowhere.
    away: HistoryAway | None = None
    #: What else a decision wrote that its line does not say, drawn under the line. Empty on every
    #: other line.
    more: str = ""


def link_of(one: HistoryLinkSource) -> HistoryLink:
    """One kernel link as the shape that crosses the wire, translated in one place so every
    route carries every field."""
    return HistoryLink(kind=one.kind, id=one.id, name=one.name, href=one.href, gone=one.gone)


def piece_of(one: Piece) -> HistoryPiece:
    """One kernel piece as the shape that crosses the wire, its fold included."""
    return HistoryPiece(
        text=one.text,
        kind=one.kind,
        id=one.id,
        href=one.href,
        gone=one.gone,
        rest=[piece_of(inner) for inner in one.rest],
        lead=one.lead,
    )


def pieces_of(line: tuple[Piece, ...]) -> list[HistoryPiece]:
    """A kernel line as the pieces a client draws."""
    return [piece_of(one) for one in line]


def history_event(event: Event) -> HistoryEvent:
    """One kernel event as the shape that crosses the wire.

    One translation for every route that answers with it. The import is local because this base is
    imported by nearly everything, and reaching for `kernel.access` at import would drag it in.
    """
    from sift.kernel.access.sentences import means

    return HistoryEvent(
        at=event.at,
        actor=event.actor.value,
        actor_name=event.actor_name,
        kind=event.kind,
        pieces=pieces_of(event.pieces),
        what=event.what,
        means=means(event.kind, event.how, event.actor.value),
        undo=None if event.undo is None else UndoPoint(kind=event.undo.kind, id=event.undo.id),
        reversed=event.reversed,
        detail=[
            HistoryDetail(
                kind=group.kind,
                words=group.words,
                entries=[link_of(one) for one in group.links],
            )
            for group in event.detail
        ],
        via=event.via,
        how=event.how,
        receipt=event.receipt,
        since=event.since,
        away=None if event.away is None else HistoryAway(label=event.away[0], href=event.away[1]),
        more=event.more,
    )
