# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the workbench routes hand back."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import HistoryDetail, HistoryPiece, Wire


class PreviewView(Wire):
    """One thing to draw on a card. `kind` says how the client addresses its picture."""

    kind: str
    id: str
    #: Where pressing it goes, when there is somewhere. Absent means it is a picture and not a link.
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
    #: A screen in the client, never an API path.
    href: str


class QueueView(Wire):
    """One pile on the board."""

    name: str
    title: str
    decision: str
    icon: str
    count: int
    #: The noun phrase that follows the count when there is more than one: "folders to name".
    #: Declared by the queue, lowercase, and always present. See `kernel.workbench.Summary.verb`.
    verb: str = ""
    #: The same phrase about ONE of them: "folder to name". Sent beside `verb`, never instead of
    #: it, and the card picks by the number it is about to draw.
    #:
    #: Both wordings come from the server because only the queue knows its own words, and only the
    #: card knows the number: on a grouped card that number is every queue on it added up, so it
    #: is not a figure any one queue could have chosen a wording for. A client stripping a trailing
    #: "s" would make "people Sift has named" into "people Sift has name", would miss the verb
    #: agreement in "group that looks alike", and would cut the wrong word out of "records a
    #: stash-box knows". See `kernel.workbench.Summary.verb_one`.
    verb_one: str = ""
    #: What kind of pile this is: `decision`, `cleanup`, `log` or `record`. What the board sorts by
    #: and the only thing it sorts by. See `kernel.workbench.Band`.
    #:
    #: A string on the wire rather than an enum with a fixed set, deliberately. A client meeting a
    #: band it has never heard of is the same situation as one meeting a queue it cannot draw, and
    #: the answer is the same: it falls back rather than throwing. A closed set here would make a
    #: NEW band a breaking change for every older client, which is exactly what this registry is
    #: built to avoid.
    band: str
    #: Which queues share a page with this one, or absent when it stands alone. Drives the tabs.
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
    #: Whether this is work waiting on somebody, which is `band` read one way.
    #:
    #: Sent even though a client could work it out, because working it out means a SECOND copy of
    #: "which bands are work" living in the browser, and the day a band is added, an older client
    #: would answer that question differently from the server that told it about the band. Derived
    #: once, where the bands are defined (`Summary.pending`), and read everywhere else.
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
    """Whether anything was put back, and for a decision of many acts, how many of them.

    `undone` is true when any went back. The counts say whether that was all of them: a batch of
    renames where some files were renamed again since goes back only in part, and a screen that
    read the yes alone would tell somebody every name was back.
    """

    undone: bool = False
    #: How many of the decision's acts went back, out of how many it had.
    put_back: int = 0
    of: int = 0
    #: The line a person reads when not every act went back (how many did, and why the rest
    #: stayed); null when every one did.
    said: str | None = None


class UndoneFoldView(Wire):
    """What taking back a whole folded row did: how many were put back, out of how many there were.

    Two numbers rather than one, because the difference is a real state and a common one: a run
    where somebody has already undone a few by hand comes back with fewer put back than there were,
    and that is not a failure. A single count could not tell it from one.
    """

    undone: int = 0
    of: int = 0


# --- the ledger feed -----------------------------------------------------------------------------
#
# What the whole-install record hands back. Its own shapes rather than the board's above, and
# the difference is the whole point of the ledger: a decision is something somebody took on a queue
# and can take back, and an event is something that HAPPENED: most of them to nobody's queue and
# with nothing to undo. Drawn as a receipt only where there is one.


class LedgerActorView(Wire):
    """Who took an act, as the kind of doer it was and what it is called.

    `kind` is `sift`, `user` or `box`. `id` means a different thing in each (the name of the
    pass, a user id, a stash-box id), which is why the kind is beside it rather than inferred.

    `name` is read when the feed is drawn and not snapshotted with the event, and that is the
    OPPOSITE of the rule for the things an act was about. A user renamed last week is the same
    user, and a feed calling them by an old name would answer "who did this" with a name nobody
    recognises. Absent once that user is deleted: the event still says a user did it and
    still says which, and the line says so in words rather than printing an id.
    """

    kind: str
    id: str | None = None
    name: str | None = None


class LedgerThingView(Wire):
    """One thing an event named, and the way to it.

    `name` is what it was CALLED at the time, straight off the row: an event outlives its subject,
    so a name looked up now would be empty for exactly the events somebody opened the record to
    find. Absent only where the row wrote none down AND nothing could be read now: a row from
    before names were snapshotted has none, and one naming something still in the library is named
    live rather than drawn as the word for its category.

    `href` is a screen in the client, built here because the server is the one place that knows
    which screen a kind of thing belongs on. Absent where there is nowhere to go: a kind with no
    page, and a thing that has since been deleted. A name with no address is drawn as plain words,
    which is honest: the alternative is a link that lands on "no such person" and reads as a
    broken screen rather than as a library that has moved on.
    """

    kind: str
    id: str
    name: str | None = None
    href: str | None = None
    #: NOTHING IS LEFT OF THIS ONE: no name was written down, and it is of a kind Sift can look up
    #: and is not there. A different sentence from a nameless thing of a kind nothing looks up
    #: ("a file that is gone" rather than "a file") and the client cannot tell the two apart on
    #: its own, because which kinds can be looked up is the server's list.
    gone: bool = False


class LedgerReceiptView(Wire):
    """The queue an event was decided on, where it was a decision at all.

    Absent on every event that was not a judgement, which is most of them. Its presence is what says
    a row can be taken back and where the undo lives; `reversed_at` says it already has been.

    The sentence is the one written at the moment the decision was taken, because that is what a
    receipt is. Every other line in this feed is assembled by the reader from the verb and the
    names, which is what lets those lines stay true as the library moves.
    """

    queue: str
    title: str
    detail: str
    reversed_at: int | None = None
    #: THE QUEUE THAT WROTE IT CANNOT TAKE ANY OF ITS DECISIONS BACK, so no undo is offered. Asked
    #: of the registry when the page is read rather than stored on the row (see `_receipt`), so
    #: a queue that becomes reversible in a later version says so about what it already wrote.
    final: bool = False
    #: What is true once it has been taken back, said under the line in place of the promise the
    #: decision made about itself: its area's own sentence ("Taken back, so these can come up
    #: again.") or the one true of every undo. Null while it stands.
    taken_back: str | None = None


class LedgerEventView(Wire):
    """One thing that happened, as the feed draws it."""

    id: str
    #: Whole seconds, from the row. The ORDER is the id's, which is a ULID and therefore
    #: chronological: a machine's wall clock is not guaranteed to move forwards.
    at: int
    actor: LedgerActorView
    #: One of the words `kernel/ledger.VERBS` holds, or absent on a row written before the ledger
    #: existed. A client that meets a verb it has never heard of says less rather than nothing.
    verb: str | None = None
    #: What it was about: the things whose own history this event appears in.
    subjects: list[LedgerThingView] = Field(default=[])
    #: What it was done to or with, where there was one. A person named on seven files is seven
    #: subjects and one object.
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
    #: THE LINE, built on the server by the one builder every History screen uses
    #: (`sentences.feed_line`), as pieces the client draws and does not assemble. The browser keeps
    #: no word table of its own: a second copy of these words would drift.
    pieces: list[HistoryPiece] = Field(default=[])
    #: What the line stands for and does not name (a decision's files, an edit's fields) in
    #: groups its "Show each" opens to.
    detail: list[HistoryDetail] = Field(default=[])
    more: str = Field(
        default="",
        description=(
            "What else a decision wrote that its line does not say, drawn under the line: 'A new "
            "person added, 1 other folder with that name answered.' Empty on most lines, and on a "
            "folded line, whose acts each wrote their own."
        ),
    )
    #: HOW MANY ACTS THIS LINE STANDS FOR: one press of a task, a sitting at one setting
    #: (`history_feed.presses_recent`). One on a line that is one act. The line's `id`, moment and
    #: receipt are its NEWEST act's.
    folded: int = 1
    #: The press's OLDEST act, which does not change as the press grows newer: what a client that
    #: already holds this line recognises it by when a later read hands it back with a newer `id`.
    #: The line's own `id` where it is one act.
    first: str = ""
    #: How many of the press's decisions are still standing, which is what its Undo all has left to
    #: put back. Nought on a line whose acts were not decisions.
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
    #: How many there are in all for the question asked, so the pane knows whether there is more.
    total: int = 0
    #: Where this page started, echoed back so a reply arriving out of order cannot be drawn as the
    #: page somebody is looking at.
    offset: int = 0
