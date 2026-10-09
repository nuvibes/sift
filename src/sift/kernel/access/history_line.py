# SPDX-License-Identifier: AGPL-3.0-or-later
"""One History line: what it says, who did it, what it names, and where it sits in a thread."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from sift.kernel.access import sentences as say
from sift.kernel.access.sentences import Line, Piece


class Actor(StrEnum):
    """Who did a thing, as far as the row that recorded it can say."""

    YOU = "you"
    ANOTHER_USER = "another_user"
    SOMEBODY = "somebody"
    SIFT = "sift"
    STASH_BOX = "stash_box"


@dataclass(frozen=True, slots=True)
class Undo:
    """What would take an event back: the kind of thing it is and its id."""

    kind: str
    id: str


#: What a sentence can name; closed, because a client draws a link from this word.
LINK_KINDS = (
    # A count names a set, not a row: `href` carries the filtered wall it stands for.
    "files",
    "faces",
    # A field of the record: drawn as a chip with nothing to press.
    "field",
    "person",
    "site",
    "tag",
    "collection",
    "photo_set",
    "song",
    "folder",
    "asset",
    "face_pile",
    # No page of its own, so not in `sentences.LINKED_KINDS`: its address depends on a person.
    "username",
    "download",
)


@dataclass(frozen=True, slots=True)
class Link:
    """One thing a line names, and where it lives."""

    kind: str
    id: str
    name: str
    #: Where this goes when the kind's own page is not the answer, such as a count's set.
    href: str | None = None
    #: The thing has gone: drawn and never linked, so no link lands on a missing page.
    gone: bool = False


@dataclass(frozen=True, slots=True)
class Detail:
    """One group of things a folded line stands for, and the phrase the sentence counted them in."""

    kind: str
    words: str
    links: tuple[Link, ...]


KeptAnswer = tuple[str, str, str, str]

FaceAnswer = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened to the subject, as a finished sentence with no full stop."""

    at: int | None
    actor: Actor
    #: None for another user when the viewer is not an admin.
    actor_name: str | None
    kind: str
    pieces: Line
    undo: Undo | None = None
    #: A reversed event keeps its place, so a history never hides its reversals.
    reversed: bool = False
    detail: tuple[Detail, ...] = ()
    #: Which pass did it, in the `enriched:` filter's words; the actor cannot tell them apart.
    via: str | None = None
    how: str | None = None
    #: Whether a person agreed to an enrichment; nothing else records it, so it is stored.
    by_hand: bool | None = None
    #: Fields a stash-box filled in that the file's own rows cannot account for. Not on the wire.
    wrote: tuple[str, ...] = ()
    grade: str | None = None
    away: tuple[str, str] | None = None
    routine: bool = False
    landed: bool = False
    #: The receipt this line belongs to, so one act's two lines are matched by id, not wording.
    receipt: str | None = None
    since: int | None = None
    box_id: str | None = None
    source: str | None = None
    kept: tuple[tuple[KeptAnswer, str], ...] = ()
    answer: KeptAnswer | None = None
    faces: tuple[FaceAnswer, ...] = ()
    stored_words: bool = False
    more: str = ""

    @property
    def what(self) -> str:
        """The line as words."""
        return say.text_of(self.pieces)

    @property
    def links(self) -> tuple[Link, ...]:
        """Every thing the line names, in order."""
        return tuple(link_of_piece(one) for one in say.things_in(self.pieces))


def link_of_piece(one: Piece) -> Link:
    """A named piece as a `Link`."""
    return Link(kind=one.kind or "", id=one.id or "", name=one.text, href=one.href, gone=one.gone)


def piece_of(link: Link) -> Piece:
    """A `Link` as a piece."""
    return say.thing(link.kind, link.id, link.name, href=link.href, gone=link.gone)


def by_of(actor: Actor, name: str | None) -> str | None:
    """Who did it, as the first word of a line."""
    return say.by_word(actor.value, name)


#: The four words the `enriched:` filter takes, so a row's mark and the filter cannot disagree.
VIAS = (
    "stash",
    "faces",
    "folder",
    "filename",
    "metadata",
    "watermark",
    "facial_fingerprints",
)


#: Every kind an event can be; a new kind is a deliberate act with a mark to choose.
KINDS = (
    "added",
    "moved",
    "renamed",
    "undone",
    "decided",
    "named",
    "tagged",
    "filed",
    "enriched",
    "asked",
    "face_run",
    "matched",
    "confirmed",
    "rejected",
    "copied_from",
    "copied_into",
    "shared",
    "watermark",
    "downloaded",
    "ready",
    "left_out",
    "face_off",
    "ruled_out",
    "hidden",
    "kept_mine",
    "taught",
    # Acts only the event ledger records, each its own kind so a mark never says something untrue.
    "removed",
    "revealed",
    "edited",
    "deleted",
    "merged",
    "kept_local",
    "allowed",
    "kept_from_swaps",
    "allowed_in_swaps",
    "scanned",
    "download_failed",
    "saved",
    "wall_sent",
    # Without these each would fall through to `decided` and claim it can be taken back.
    "paused",
    "resumed",
    "cookies_saved",
    "cookies_replaced",
    "cookies_forgotten",
    "canceled",
    "ran",
    "pressed",
    "song_named",
    "swap_started",
    "swap_ended",
    "restored",
    "adopted",
    "sharing_turned_on",
    "sharing_turned_off",
    "start_with_windows_on",
    "start_with_windows_off",
    "firewall_opened",
    "storage_moved",
    "update_started",
    "library_opened",
    "restarted",
)

#: Which of two acts in the same second came first: the cause before what it caused.
CAUSE_ORDER: tuple[tuple[str, ...], ...] = (
    ("downloaded", "download_failed", "paused", "resumed", "canceled", "swap_started"),
    ("added", "copied_from"),
    ("filed",),
    ("renamed", "moved"),
    (
        "named",
        "tagged",
        "song_named",
        "decided",
        "edited",
        "removed",
        "merged",
        "cookies_saved",
        "cookies_replaced",
        "cookies_forgotten",
    ),
    ("ready", "left_out", "watermark", "scanned", "ran", "pressed", "restored", "adopted"),
    (
        "sharing_turned_on",
        "sharing_turned_off",
        "start_with_windows_on",
        "start_with_windows_off",
        "firewall_opened",
        "storage_moved",
        "update_started",
        "library_opened",
        "restarted",
    ),
    ("asked", "enriched", "kept_mine"),
    ("face_run",),
    ("matched",),
    ("confirmed", "rejected", "face_off", "ruled_out", "taught"),
    (
        "shared",
        "copied_into",
        "saved",
        "wall_sent",
        "kept_local",
        "allowed",
        "kept_from_swaps",
        "allowed_in_swaps",
        "swap_ended",
    ),
    ("hidden", "revealed", "deleted"),
    ("undone",),
)
CAUSE_RANK: Mapping[str, int] = {
    kind: rank for rank, kinds in enumerate(CAUSE_ORDER) for kind in kinds
}


def ordered(events: Iterable[Event]) -> list[Event]:
    """A thread in the order its acts happened; every History thread is ordered here."""
    return sorted(
        events,
        key=lambda event: (
            event.at is not None,
            event.at or 0,
            CAUSE_RANK.get(event.kind, len(CAUSE_ORDER)),
        ),
    )


#: The cap keeps the newest: recent events are what somebody is looking for.
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
