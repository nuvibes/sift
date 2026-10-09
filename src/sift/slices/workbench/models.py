# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the workbench routes hand back."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import HistoryDetail, HistoryPiece, Wire


class PreviewView(Wire):
    """One thing to draw on a card. `kind` says how the client addresses its picture."""

    kind: str
    id: str
    href: str | None = None
    art: str | None = Field(
        default=None,
        description=(
            "The token the picture's address carries, which is what lets a browser keep it for a "
            "week instead of asking about every card on every visit. Null where there is nothing "
            "to say about the picture yet (a still built before Sift recorded what it had made, "
            "or a kind of picture this version has no token for) and the address is then left "
            "bare and re-checked on every use."
        ),
    )


class AsideView(Wire):
    """A line of its own under a queue's lede, and its link. See `kernel.workbench.Aside`."""

    said: str
    link: str
    href: str


class QueueView(Wire):
    """One pile on the board."""

    name: str
    title: str
    decision: str
    icon: str
    count: int
    verb: str = ""
    #: The same phrase about one, sent beside `verb`: only the card knows the number it draws.
    verb_one: str = ""
    #: A string, not an enum: a client meeting a new band falls back rather than throwing.
    band: str
    group: str | None = None
    group_title: str | None = Field(
        default=None,
        description=(
            "What the whole GROUP is called, on the one queue that leads it. The board draws a "
            "group of two or more cards as ONE card wearing this name: a group is one page, and "
            "two cards pointing at two tabs of it is two ways in to one job. Null on every other "
            "member, and on a queue that stands alone."
        ),
    )
    purpose: str | None = Field(
        default=None,
        description=(
            "What this pile's card on the board is for, in one short sentence, the same for "
            "everybody: never a question, a count or a name. On the queue that leads a group it "
            "says what the group's page is for. Null on a record, which is never a card. See "
            "`kernel.workbench.Queue.purpose`."
        ),
    )
    advice: str | None = Field(
        default=None,
        description=(
            "How to work through this pile, in one sentence, where the ORDER somebody answers it "
            "in changes the work. Drawn under the heading of the queue's own page. Null on almost "
            "every queue. See `kernel.workbench.Summary.advice`."
        ),
    )
    aside: AsideView | None = Field(
        default=None,
        description=(
            "A line of its own under the queue's lede, about work the pile is waiting on elsewhere, "
            "with its link. Null on almost every queue. See `kernel.workbench.Aside`."
        ),
    )
    #: Sent so the browser holds no second copy of which bands are work.
    pending: bool
    opens: str | None = Field(
        default=None,
        description=(
            "Where this pile's card opens, for a pile whose way in is not a panel of its own: a "
            "screen in the client, never an API path. Null means the ordinary thing: the pile's "
            "own panel, which a client that has no drawing for it cannot open at all."
        ),
    )
    preview: list[PreviewView] = Field(default=[])


class BoardView(Wire):
    """The whole board: what is waiting. What was decided is the feed's (History, Decisions)."""

    queues: list[QueueView] = Field(default=[])


class UndoneView(Wire):
    """Whether anything was put back, and for a decision of many acts, how many of them."""

    undone: bool = False
    put_back: int = 0
    of: int = 0
    said: str | None = None


class UndoneFoldView(Wire):
    """What taking back a whole folded row did: how many were put back, out of how many."""

    undone: int = 0
    of: int = 0


# The ledger feed: events, most with nothing to undo, a receipt only where there is one.


class LedgerActorView(Wire):
    """Who took an act, as the kind of doer and its name, read live so a rename shows."""

    kind: str
    id: str | None = None
    name: str | None = None


class LedgerThingView(Wire):
    """One thing an event named, by its name at the time, and the screen it lives on."""

    kind: str
    id: str
    name: str | None = None
    href: str | None = None
    #: Nothing is left of this one, which only the server can tell from a nameless kind.
    gone: bool = False


class LedgerReceiptView(Wire):
    """The queue an event was decided on, where it was a decision at all."""

    queue: str
    title: str
    detail: str
    reversed_at: int | None = None
    #: Asked of the registry when read, so a queue that becomes reversible says so about old rows.
    final: bool = False
    taken_back: str | None = None


class LedgerEventView(Wire):
    """One thing that happened, as the feed draws it."""

    id: str
    #: The order is the id's (a ULID), since a wall clock is not guaranteed to move forwards.
    at: int
    actor: LedgerActorView
    verb: str | None = None
    subjects: list[LedgerThingView] = Field(default=[])
    object: LedgerThingView | None = None
    count: int | None = Field(
        default=None,
        description=(
            "How many things one event stands for, where it stands for a pass rather than an act: "
            "'Scanned 1,203 files'. Absent on an ordinary event, and absent is not one: a pass "
            "over a single file writes the file."
        ),
    )
    receipt: LedgerReceiptView | None = None
    #: Built on the server by `sentences.feed_line`: a second copy of these words would drift.
    pieces: list[HistoryPiece] = Field(default=[])
    detail: list[HistoryDetail] = Field(default=[])
    more: str = Field(
        default="",
        description=(
            "What else a decision wrote that its line does not say, drawn under the line: 'A new "
            "person added, 1 other folder with that name answered.' Empty on most lines, and on a "
            "folded line, whose acts each wrote their own."
        ),
    )
    #: How many acts this line stands for; its id, moment and receipt are the newest act's.
    folded: int = 1
    #: The press's oldest act, which a client recognises the line by as it grows.
    first: str = ""
    standing: int = 0
    report: str | None = Field(
        default=None,
        description=(
            "The run whose report this line opens, or null where there is none. A pass over the "
            "library keeps the record a report is made from; a task's own run line (a backup, a "
            "clean-up) names the task and has none."
        ),
    )
    still: PreviewView | None = Field(
        default=None,
        description=(
            "On the Decisions narrowing only: the picture of what a decision was about, the one "
            "its area draws for it (the file's own still, or a face's crop), addressed with its "
            "token like a board card's. The first of a folded line's newest receipt. Null for a "
            "line that names no picture, a picture this viewer may not see or one not made yet."
        ),
    )


class LedgerPage(Wire):
    """A page of the record, newest first."""

    items: list[LedgerEventView] = Field(default=[])
    total: int = 0
    #: Echoed back so a reply arriving out of order is never drawn as the current page.
    offset: int = 0
