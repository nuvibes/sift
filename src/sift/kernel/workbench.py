# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work waiting on a person, and the record of what they decided.

Only judgements a rule cannot settle are queued; every decision leaves a reversible receipt."""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from sift.kernel.access import Viewer
from sift.kernel.changes import About, mark_of
from sift.kernel.db import Connection
from sift.kernel.log import timing_hook
from sift.kernel.vocabulary import LEDGER_QUEUE, Subject

if TYPE_CHECKING:  # pragma: no cover (a name for the annotation, never imported at run time)
    # The ledger imports this module, so importing it back at run time would be a cycle.
    from sift.kernel.ledger import Object as LedgerObject


class Band(StrEnum):
    """What kind of thing a queue is: the only thing the board sorts by."""

    DECISION = "decision"

    #: Real work with no judgement in it, such as which copies to keep.
    CLEANUP = "cleanup"

    #: A report nothing is asked about, drawn quietly.
    LOG = "log"

    #: Already settled: reached through its group's tabs, never a card.
    RECORD = "record"


@dataclass(frozen=True, slots=True)
class Preview:
    """One thing to draw on a card; the kind is declared because an id does not say it."""

    kind: str
    id: str
    #: Where pressing it goes; only the registering area knows where its things live.
    href: str | None = None


@dataclass(frozen=True, slots=True)
class Aside:
    """A line under a queue's lede about work the pile waits on elsewhere, with its link."""

    said: str
    link: str
    href: str


ASSET = "asset"
FACE = "face"


#: Names no queue may claim: a fixed screen lives at that address and the router prefers it.
#: A queue named `LEDGER_QUEUE` would offer to undo every plain event in the library.
RESERVED_NAMES = frozenset({"decisions", LEDGER_QUEUE})


@dataclass(frozen=True, slots=True)
class Summary:
    """One queue as the board draws it, resolved for whoever is looking."""

    name: str
    title: str
    decision: str
    icon: str
    count: int
    #: The lowercase phrase after a count of more than one: "folders to name". A gate checks it.
    verb: str
    #: The phrase for exactly one, required beside `verb`: only the queue knows its own grammar.
    verb_one: str
    #: Stamped from the queue's declaration by `Workbench._surveyed`, never set by a survey.
    band: Band = Band.DECISION
    group: str | None = None
    group_title: str | None = None
    purpose: str | None = None
    #: How to work through this pile, when the order changes the work; usually None.
    advice: str | None = None
    aside: Aside | None = None
    preview: tuple[Preview, ...] = ()
    #: Where the card opens for a queue with no panel; None means `/organize/<name>`.
    opens: str | None = None

    @property
    def pending(self) -> bool:
        """Derived from the band, so no queue can set it to disagree."""
        return self.band is not Band.RECORD


@dataclass(frozen=True, slots=True)
class Receipt:
    """One decision made; `detail` is written at the time, since it describes that moment."""

    id: str
    queue: str
    title: str
    detail: str
    decided_at: int
    reversible: bool = True
    preview: tuple[Preview, ...] = ()


class Recorder(Protocol):
    """Writes a decision's receipt on the decision's own connection, so the two never disagree."""

    async def record_on(
        self,
        connection: Connection,
        *,
        queue: str,
        user_id: str | None,
        title: str,
        detail: str,
        payload: str,
        subjects: Sequence[Subject] = (),
        verb: str = "decided",
        object: LedgerObject | None = None,
        via: str | None = None,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class Reversal:
    """How many acts of one decision went back, and `said` for a person when not all did."""

    put_back: int
    of: int
    said: str | None = None
    #: Receipts of decisions resting on this one, put back in the same press.
    along: tuple[str, ...] = ()


class Reverser(Protocol):
    """Reads back and puts back one kind of receipt, outliving any card that wrote it."""

    @property
    def name(self) -> str: ...

    #: Whether any decision of this kind can be taken back; calling `reverse` would be the act.
    @property
    def reversible(self) -> bool: ...

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """A few pictures of what one decision was about, from its payload; may be empty."""
        ...

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool | Reversal:
        """Put back what one decision did, from its payload, never from the current state."""
        ...


# A decision's shown line is worded when shown, from what it recorded, so names stay current.
# The stored title is still what the record folds on, and the fallback for an old row.


def payload_held(payload: str) -> dict[str, Any]:
    """What a decision wrote down as a record, or an empty one when it cannot be read."""
    try:
        held: Any = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


@dataclass(frozen=True, slots=True)
class Recorded:
    """Everything one decision wrote down, which is all its words may be made from."""

    id: str
    queue: str
    payload: str
    title: str
    detail: str
    decided_at: int
    user_id: str | None = None
    verb: str | None = None
    actor_kind: str | None = None
    actor_id: str | None = None
    object_kind: str | None = None
    object_id: str | None = None
    object_name: str | None = None
    count: int | None = None
    subjects: tuple[Subject, ...] = ()
    #: How many receipts the line stands for, where the record folded a run into one row.
    run: int = 1
    #: The History page it is shown on, so the area says only that page's part; else None.
    page: tuple[str, str] | None = None

    def held(self) -> dict[str, Any]:
        """The queue's payload as a record, or an empty one where it cannot be read."""
        return payload_held(self.payload)


@dataclass(frozen=True, slots=True)
class Named:
    """One thing a worded decision names, resolved by the reader to how it is when shown."""

    kind: str
    id: str
    recorded: str | None = None
    #: Said by the recorded name where that name is the fact, with its current name after it.
    as_recorded: bool = False
    #: Said with its kind before a name, where a name alone can mislead.
    kind_said: bool = False


@dataclass(frozen=True, slots=True)
class Doer:
    """Who took the decision, said by the reader, who alone knows who is reading."""


DOER: Final = Doer()

Piece = str | Named | Doer


@dataclass(frozen=True, slots=True)
class Worded:
    """A decision's line as pieces, and the one plain line that may sit under it."""

    said: tuple[Piece, ...]
    more: tuple[Piece, ...] = ()


@runtime_checkable
class Words(Protocol):
    """An area that words its own decisions from what they recorded; None keeps the title."""

    def worded(self, recorded: Recorded) -> Worded | None: ...


TAKEN_BACK: Final = "Taken back."


@runtime_checkable
class TakenBack(Protocol):
    """What taking one decision back left true, shown instead of the stored sentence."""

    @property
    def taken_back(self) -> str: ...


@runtime_checkable
class Grouped(Protocol):
    """A reverser with no card of its own whose decisions are taken on a group's card."""

    @property
    def group(self) -> str | None: ...


#: The announcements that can change a pile; what a viewer may see is in the `Viewer` key.
MOVED_BY: Final = frozenset({About.LIBRARY, About.ARRIVALS, About.SETTINGS, About.MINE})

KEPT_SUMMARIES: Final = 512


@runtime_checkable
class Moves(Protocol):
    """A queue naming the announcements that change it; None means surveyed on every read."""

    @property
    def moved_by(self) -> frozenset[About] | None: ...


@runtime_checkable
class Stills(Protocol):
    """A queue whose cards draw only files with a still, so a new picture moves it."""

    @property
    def draws_stills(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class Card:
    """The card a decision was taken on: the queue whose page it opens, and its heading."""

    name: str
    title: str


class Queue(Reverser, Protocol):
    """One kind of waiting work drawn on the board; see `Reverser` for why the two separate."""

    @property
    def title(self) -> str: ...

    @property
    def band(self) -> Band: ...

    #: Which queues share a page as tabs; unlike `band`, it says which are alternatives.
    @property
    def group(self) -> str | None: ...

    #: The group's one card title, declared only by the group's lead.
    @property
    def group_title(self) -> str | None: ...

    #: What the card is for in one plain sentence, never a question; None on a record.
    @property
    def purpose(self) -> str | None: ...

    async def available(self) -> bool:
        """Whether anything is behind this queue here; an absent card beats an empty panel."""
        ...

    async def survey(self, viewer: Viewer) -> Summary:
        """What is waiting, as this user may be told about it."""
        ...


@dataclass
class Workbench:
    """Every registered queue, held per application because queues are built at boot."""

    queues: list[Queue] = field(default_factory=list)

    #: Kept apart from `queues`: a receipt outlives the card that wrote it.
    reversers: list[Reverser] = field(default_factory=list)

    #: Each queue's last summary per viewer, with the mark it was surveyed at. See `board`.
    _kept: OrderedDict[tuple[str, Viewer], tuple[str, Summary]] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    #: A survey in flight per queue, viewer and mark, which every reader asking meanwhile shares.
    _asking: dict[tuple[str, Viewer, str], asyncio.Future[Summary]] = field(
        default_factory=dict, init=False, repr=False
    )

    def register(self, queue: Queue) -> None:
        """Claim a name; a duplicate or reserved name fails the start rather than misroutes."""
        if any(existing.name == queue.name for existing in self.queues):
            raise ValueError(f"a queue named {queue.name!r} is already registered")
        if queue.name in RESERVED_NAMES:
            raise ValueError(
                f"{queue.name!r} is reserved: a screen already lives at /organize/{queue.name}"
            )
        self.queues.append(queue)
        self.register_reverser(queue)

    def register_reverser(self, reverser: Reverser) -> None:
        """Claim a name for taking receipts back, without offering a card."""
        if any(existing.name == reverser.name for existing in self.reversers):
            raise ValueError(f"{reverser.name!r} already knows how to take its decisions back")
        self.reversers.append(reverser)

    def reverser(self, name: str) -> Reverser | None:
        """What reads and puts back one kind of receipt, retired cards included, or None."""
        return next((one for one in self.reversers if one.name == name), None)

    def recorded_under(self, group: str | None) -> list[str]:
        """Every name a decision on one group's card may be recorded under."""
        if group is None:
            return []
        return [
            one.name for one in self.reversers if isinstance(one, Grouped) and one.group == group
        ]

    def card_of(self, name: str) -> Card | None:
        """The card a decision under `name` was taken on, joined as the client's `bandsOf`."""
        found: object = self.reverser(name)
        group = found.group if isinstance(found, Grouped) else None
        if group is None:
            queue = next((one for one in self.queues if one.name == name), None)
            return None if queue is None else Card(queue.name, queue.title)
        members = [one for one in self.queues if one.group == group]
        lead = next((one for one in members if one.band is not Band.RECORD), None)
        if lead is None:
            return None
        title = lead.group_title if len(self.recorded_under(group)) > 1 else None
        return Card(lead.name, title or lead.title)

    async def available(self, only: Collection[str] | None = None) -> list[Queue]:
        """The queues with something behind them, limited to `only` when given."""
        asked = [one for one in self.queues if only is None or one.name in only]
        behind = await asyncio.gather(*(one.available() for one in asked))
        return [one for one, has in zip(asked, behind, strict=True) if has]

    async def board(self, viewer: Viewer, only: Collection[str] | None = None) -> list[Summary]:
        """Every available queue as this user may see it, surveyed once per announcement."""
        return list(
            await asyncio.gather(
                *(self._summary(one, viewer) for one in await self.available(only))
            )
        )

    async def _summary(self, queue: Queue, viewer: Viewer) -> Summary:
        """One queue's summary: kept until an announcement moves it, one survey shared."""
        moved_by = queue.moved_by if isinstance(queue, Moves) else MOVED_BY
        pictures = isinstance(queue, Stills) and queue.draws_stills
        mark = None if moved_by is None else mark_of(moved_by, pictures=pictures)
        if mark is None:
            return await self._surveyed(queue, viewer)
        key = (queue.name, viewer)
        held = self._kept.get(key)
        if held is not None and held[0] == mark:
            self._kept.move_to_end(key)
            return held[1]
        asking = self._asking.get((queue.name, viewer, mark))
        if asking is None:
            asking = asyncio.ensure_future(self._surveyed(queue, viewer))
            self._asking[(queue.name, viewer, mark)] = asking
            asking.add_done_callback(lambda done: self._landed(key, mark, done))
        # Shielded, so a reader that goes away does not cancel the survey others wait on.
        return await asyncio.shield(asking)

    def _landed(self, key: tuple[str, Viewer], mark: str, done: asyncio.Future[Summary]) -> None:
        """Keep a finished survey under the mark it started at; a failed one keeps nothing."""
        self._asking.pop((key[0], key[1], mark), None)
        if done.cancelled() or done.exception() is not None:
            return
        self._kept[key] = (mark, done.result())
        self._kept.move_to_end(key)
        while len(self._kept) > KEPT_SUMMARIES:
            self._kept.popitem(last=False)

    @staticmethod
    async def _surveyed(queue: Queue, viewer: Viewer) -> Summary:
        """One timed survey, stamped with the queue's declared band, group and purpose."""
        with timing_hook("workbench.survey", queue=queue.name, level="debug", slow_ms=250.0):
            surveyed = await queue.survey(viewer)
        return replace(
            surveyed,
            title=queue.title,
            band=queue.band,
            group=queue.group,
            group_title=queue.group_title,
            purpose=queue.purpose,
        )
