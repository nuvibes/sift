# SPDX-License-Identifier: AGPL-3.0-or-later
"""The base every model that crosses the wire is built on.

Every key is always sent, so the serialisation schema marks defaulted fields required."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:  # pragma: no cover (an annotation, never imported at run time)
    from sift.kernel.access.catalog import EntityMaker
    from sift.kernel.access.history import Event
    from sift.kernel.access.history import Link as HistoryLinkSource
    from sift.kernel.access.sentences import Piece


class Wire(BaseModel):
    """A shape that crosses the wire in either direction; a gate refuses a bare `BaseModel`."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class Refused(ValueError):
    """A value refused in a sentence for the person, shown beside the field it names."""


class FacetValue(Wire):
    """One value a dimension takes, and how many of the rows on screen carry it."""

    value: str
    count: int
    label: str | None = None


class FacetCounts(Wire):
    """A wall's rows along one dimension; a value the viewer may not know of is absent."""

    facet: str
    values: list[FacetValue]


class MadeBy(Wire):
    """Who invented a row, as against who has since described it."""

    #: The history's five `actor` words; never a name for another user.
    kind: str
    via: str | None = None
    act: str | None = None
    #: Null for a forgotten box too, which the screen must not tell apart.
    box_id: str | None = None
    box_name: str | None = None
    box_slug: str | None = None


def made_by_wire(made: EntityMaker) -> MadeBy:
    """One kernel maker as the shape that crosses the wire."""
    return MadeBy(
        kind=made.actor.value,
        via=made.via,
        act=made.act,
        box_id=made.box_id,
        box_name=made.box_name,
        box_slug=made.box_slug,
    )


class UndoPoint(Wire):
    """What would take one event back; the kind names which of two doors."""

    kind: str
    id: str


class HistoryLink(Wire):
    """One thing a history sentence names, and where that thing lives."""

    #: An unknown kind is drawn as plain text: an older client is the ordinary case.
    kind: str
    id: str
    name: str
    #: A client screen when not the kind's own page; null means that page.
    href: str | None = None
    #: Gone: drawn struck through, never linked.
    gone: bool = False


class HistoryPiece(Wire):
    """One run of a history line: plain words, or words standing for a thing."""

    text: str
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
    """One group of things a folded line stands for, headed by the sentence's own words."""

    kind: str
    words: str
    entries: list[HistoryLink] = []


class HistoryEvent(Wire):
    """One thing that happened to a history's subject, ready to draw."""

    at: int | None = None
    actor: str
    actor_name: str | None = None
    #: An unknown kind is drawn as "something happened".
    kind: str
    pieces: list[HistoryPiece] = []
    what: str
    #: The tooltip for the row's mark: what kind of act the line is.
    means: str = ""
    undo: UndoPoint | None = None
    #: Kept in place when taken back, or the history reads as if nothing happened.
    reversed: bool = False
    detail: list[HistoryDetail] = []
    #: In the `enriched:` filter's own words, so the mark and the filter agree.
    via: str | None = None
    how: str | None = None
    #: The receipt this line belongs to; a client groups by it, never by sentence.
    receipt: str | None = None
    #: When a line standing for a run began, with `at` its last.
    since: int | None = None
    away: HistoryAway | None = None
    more: str = ""


def link_of(one: HistoryLinkSource) -> HistoryLink:
    """One kernel link as the shape that crosses the wire."""
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
    """One kernel event on the wire; `sentences` is imported late, as this base loads first."""
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
