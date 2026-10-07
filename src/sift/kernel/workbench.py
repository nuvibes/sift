# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work waiting on a person, and the record of what they decided.

Sift works most things out on its own. What is left over is the judgements a rule cannot settle:
a folder that looks like somebody's name, a group of faces that might be one person, and those
have to go somewhere rather than being guessed at. This is the shape of that somewhere.

Two rules hold for everything registered here, and they are the whole design:

- **A queue is for judgements a threshold cannot settle.** Anything a rule could decide is decided
  by the rule and never queued. A queue that fills up with work a threshold could have done stops
  being opened, and then the real judgements rot inside it.
- **Every decision leaves a receipt, and every receipt can be reversed.** A bulk decision writes
  hundreds of rows in one go, and the wrong one is often noticed a day later rather than in ten
  seconds. A decision nobody can find again is a decision nobody can undo.
- **A queue says what KIND of pile it is, and the board sorts by nothing else.** See `Band`. The
  first rule above is not self-enforcing: piles that ask nothing at all accumulate here because
  there is nowhere else obvious to put them, and drawn as equals they bury the judgements. The band
  is how a log stays findable without pretending to be work.

Each area registers its own queue, because what is waiting is that feature's business and this
module has no way to know what a face group is. What it owns is the shape: a name, a sentence
saying what the decision is, a count, something to look at, and a way to take a decision back.

The alternative (one module that knew about every queue) would have to import half the
application, and would be the place every future feature has to remember to edit.
"""

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
    # The ledger imports THIS module for its subject vocabulary, so importing it back would be a
    # cycle. Under `from __future__ import annotations` the annotation is a string either way, and
    # the type checker reads it, which is the whole of what the name is here for.
    from sift.kernel.ledger import Object as LedgerObject


class Band(StrEnum):
    """What KIND of thing a queue is, which is the only thing the board sorts by.

    With one axis (`pending: bool`) and a dozen queues to draw on it, a diagnostic log, a
    preference about copies, a third party's offer and a genuine judgement would all land as the
    same card at the same weight, and the screen would read as a sprawl: the eye would have no way
    to tell the cards that need a person from the ones that do not.

    Declared by each queue, exactly as its name and icon are, so the board draws bands from what
    registered rather than from a list of names living on the client. A queue added in a later
    version says one word and lands in the right band with nothing else edited, which is the
    property the whole registry exists for and the one a hard-coded list would destroy.
    """

    #: A judgement a threshold cannot settle. What this whole surface is FOR. See the module
    #: docstring's first rule. Anything here is work waiting on a person.
    DECISION = "decision"

    #: A choice with no judgement in it, or an offer to accept. Real work, and nobody else's
    #: decision, but nothing is being weighed up: which copies to keep, whether to take what a
    #: stash-box says. Drawn under the judgements because it can wait.
    CLEANUP = "cleanup"

    #: A log. Nothing was decided, nothing is asked, and the pile is a report about files Sift could
    #: not read. It stays on the board (somebody has to be able to find it), but quietly, because
    #: a card in the first band is a promise that answering it settles something.
    LOG = "log"

    #: Already settled. A count that never goes down, so it can never be a card on a screen whose
    #: whole promise is that it empties. Reached through its GROUP's tabs instead (see
    #: `Queue.group`), which puts what was settled beside the thing it was settled from.
    RECORD = "record"


@dataclass(frozen=True, slots=True)
class Preview:
    """One thing to draw on a card, so a pile is recognisable before it is opened.

    The KIND is here rather than worked out from the id, because the two queues that ship draw two
    different things (a still from a file, and a crop of a face) and an id says nothing about
    which. A client that guessed would be a second place that has to be edited every time a queue
    is added, which is exactly what this registry exists to avoid.
    """

    #: What sort of picture this is. The client knows how to address each kind.
    kind: str
    id: str
    #: Where pressing it goes, or None when it goes nowhere.
    #:
    #: From the queue for the same reason the kind is: only the area that registered it knows where
    #: its own things live, and a client working the address out from the kind would be a second
    #: place to edit every time a queue is added. A record of a decision is worth much more when the
    #: things in it can be opened: reading that 21 faces were set aside answers less than one
    #: press on the ones it happened to.
    href: str | None = None


@dataclass(frozen=True, slots=True)
class Aside:
    """A line of its own under a queue's lede, about work the pile is WAITING ON, with its link.

    Not part of `advice`: advice is how to work through the pile on screen, and this is a thing to
    go and do somewhere else, so it is drawn as its own line with the one place it is done. The
    words are the queue's, like every other sentence on a card.
    """

    #: The line itself: "6 IDs are waiting for a username. Add each ID ..."
    said: str
    #: The words of the link after it, saying what is there: "Open Sites".
    link: str
    #: A screen in the client, the same address space as `Preview.href`.
    href: str


#: The kinds that exist. A queue drawing something else declares its own and the client learns it.
ASSET = "asset"
FACE = "face"


#: `LEDGER_QUEUE`: what the `queue` column says on an event that was not taken on a queue at all.
#: Decided in `kernel/vocabulary.py`, with the argument for it, and named here because
#: `RESERVED_NAMES` below is what keeps a queue from claiming it.

#: Names no queue may claim, because a fixed screen already lives at that address.
#:
#: The client's Organize section is `/organize/<queue>` with a small number of fixed pages beside
#: it. A router prefers the fixed page, so a queue registering one of these words would draw a card
#: that opens the wrong screen and nothing would report it. Held here rather than in the client,
#: because the server is what hands out the names.
#:
#: `LEDGER_QUEUE` is here for a second reason, said above: a queue claiming it would offer to undo
#: every plain event in the library.
RESERVED_NAMES = frozenset({"decisions", LEDGER_QUEUE})


@dataclass(frozen=True, slots=True)
class Summary:
    """One queue as the board draws it, resolved for whoever is looking.

    `band` is what makes the board honest about itself. A queue of things somebody has already set
    aside is reachable and countable and is not work, so it is drawn apart from the ones that are:
    mixed in among them it would be a card that can never reach zero, sitting in a screen whose
    whole promise is that it empties. See `Band` for why one boolean could not say that.
    """

    name: str
    title: str
    #: What deciding one of these actually means, in a sentence. On screen, never in a tooltip: a
    #: bulk decision offered without saying what it does is a leap of faith.
    decision: str
    icon: str
    count: int
    #: The noun phrase that follows the count on the card, FOR MORE THAN ONE: "folders to name",
    #: "look-alike files". `verb_one` is the same phrase about a single item.
    #:
    #: **The card leads with the number, and a number leads nothing on its own.** *Folders 4*
    #: would make the title the headline and the figure a footnote; *4 folders to name* is the
    #: same two facts read as one sentence, and the verb is the half a title cannot carry: a pile
    #: called Duplicates counts FILES and a pile called Faces counts GROUPS, and the title says
    #: neither.
    #:
    #: Lowercase, because it continues the number rather than starting anything, and required
    #: rather than defaulted: a queue that forgot one would draw a bare figure with nothing saying
    #: what was counted. A gate checks the spelling.
    #: See `tests/gates/test_every_queue_says_what_it_holds.py`.
    verb: str
    #: The same phrase about ONE of them: "folder to name", "shoot to agree", "file Sift moved
    #: aside". Required beside `verb`, never derived from it.
    #:
    #: ## Why the singular is a second field and not the card's job
    #:
    #: A pile that has come down to its last item is the most-looked-at state the board has (it
    #: is one press from empty) and a card must not read *1 shoots to agree*, *1 groups that look
    #: alike*, *1 files Sift moved aside*. The count and the phrase are decided
    #: in two different places and neither can see the other: only the CARD knows the number (it is
    #: a sum over a grouped card's queues, not any one queue's count), and only the QUEUE knows the
    #: words, which is why the plural is declared here in the first place.
    #:
    #: Three shapes were possible and this is the one that keeps each half where its facts are.
    #: Stripping a trailing "s" in the client cannot work: "people Sift has named" becomes
    #: "person", "groups that look alike" needs its VERB to agree as well ("group that looks
    #: alike"), and "records a stash-box knows" would lose the wrong word. An English inflection
    #: library in the browser is a dependency that would be wrong about exactly the phrases this
    #: project writes. So each queue says both, in its own words, the way `kernel.reach` carries
    #: `OUT_OF_REACH` beside `OUT_OF_REACH_MANY` for the identical reason.
    #:
    #: The gate refuses a queue declaring one without the other, and refuses the two being equal:
    #: a copy-pasted pair would be the same fault with a field that says it is fixed.
    verb_one: str
    #: What kind of pile this is. See `Band`.
    #:
    #: **A survey never sets this, and the default is not a claim.** It is STAMPED from the
    #: queue's own declaration on the way out (`Workbench._surveyed`), because a queue that
    #: declared its band on the class and then returned a different one in its summary would be
    #: drawn in the wrong band of the screen, silently, on the one install that has that queue.
    #: A test that keeps two copies honest is worth much less than not having two copies: this is
    #: one declaration, and a second one cannot drift because it is not written anywhere.
    band: Band = Band.DECISION
    #: Which queues this one shares a page with, or None to stand alone. See `Queue.group`.
    #: Stamped from the queue too, for the same reason.
    group: str | None = None
    #: What the whole GROUP is called, on the one queue that leads it. See `Queue.group_title`.
    #: Stamped from the queue as well.
    group_title: str | None = None
    #: What the queue's card on the board is for, in one short sentence. See `Queue.purpose`.
    #: Stamped from the queue as well.
    purpose: str | None = None
    #: HOW to work through this pile, when the order somebody answers it in changes the work.
    #:
    #: One sentence, drawn under the heading of the queue's own page. Almost every pile has nothing
    #: to say here and leaves it None, which is the honest default: a line of advice on a screen
    #: that does not need one is a line people learn to read past.
    #:
    #: Declared beside the queue rather than written into the page, for the same reason the icon,
    #: the band and the verb are: only the area that registered a pile knows how its own work goes,
    #: and a sentence living in the client would be a second place to edit every time one changes.
    #: It also means the board and the page can say it in the same words rather than in two that
    #: happen to match today.
    advice: str | None = None
    #: A line of its own under the lede, about work waiting elsewhere, or None. See `Aside`.
    aside: Aside | None = None
    #: A few things to draw, so a pile is recognisable before it is opened. See `Preview`.
    preview: tuple[Preview, ...] = ()
    #: Where the card's way in goes, for a queue whose way in is NOT a panel of its own.
    #:
    #: **The card is pressable either way, and this is what makes that possible for a queue with no
    #: panel.** With `/organize/<name>` the only way in, a card whose whole point is a report over
    #: files the browse wall already draws would have a dead heading and a dead body: the client
    #: knows of no drawing for the queue, so it would disable the press. Building a
    #: panel to fix that would be a second, worse Files wall; hard-coding the address beside the
    #: client's panel registry would be the client learning a queue's name for something the queue
    #: already knows. So the queue says where it opens, exactly as it says its icon and its band.
    #:
    #: A screen in the client, the same address space as `Preview.href` and not the API's. None means the ordinary thing: the queue's own panel at `/organize/<name>`, and a
    #: client with no drawing for it cannot be opened at all, which is still the honest answer
    #: for a queue that really is a panel this build has never heard of.
    opens: str | None = None

    @property
    def pending(self) -> bool:
        """Whether this is work waiting on somebody.

        DERIVED, and that is the point of it. A field each queue set beside its band would be two
        answers to one question and free to disagree: a queue saying `pending=False` while sitting
        in the band of things that need a person, drawn in the wrong half of a screen. There is one
        answer and this reads it.

        A property on a `slots` dataclass is not a field, so it costs no slot and cannot be passed
        to the constructor. That is the guarantee rather than a detail: there is no way to set it.
        """
        return self.band is not Band.RECORD


@dataclass(frozen=True, slots=True)
class Receipt:
    """One decision that was made, and what it did.

    `detail` is written when the decision is taken rather than worked out when it is read. What a
    decision changed is a fact about the moment it happened, and a sentence assembled later from
    the current state describes the library as it is now, which is exactly what somebody reading
    a list of past decisions is trying to look behind.
    """

    id: str
    queue: str
    title: str
    detail: str
    decided_at: int
    #: Whether this can still be taken back. False once it has been.
    reversible: bool = True
    #: A few pictures of WHAT the decision was about. See `Queue.pictures_of`.
    #:
    #: A record that says only that something happened is a record nobody can check. "A group of 21
    #: faces set aside" answers how many and not which, so the one question somebody opens this list
    #: to ask (what did I just do that to) is the one it cannot answer.
    preview: tuple[Preview, ...] = ()


# `Subject`: one thing a decision was about. Decided in `kernel/vocabulary.py` and imported above,
# with `SubjectKind` beside it: `kernel/ledger.py` validates against both and this module reads
# `kernel/access`, so a door defined against a constant here would sit above two of the writers
# that have to call it. `Subject` is spelled from here by the callers that use it; `SubjectKind` is
# spelled from the leaf, because nothing here needs it.


class Recorder(Protocol):
    """Somewhere to write down what a decision did, on the decision's own connection.

    On the caller's connection, and that is the whole shape of it. A receipt written afterwards can
    be missing for a decision that happened, or present for one that did not, and a record that is
    only usually right is worse than none: it is the thing somebody reaches for once they already
    know something has gone wrong.

    Declared here rather than in the workbench feature so an area can write a receipt without
    importing the shell. A feature depends on the kernel and never on another feature.

    `verb` and `object` are the ledger's, and they default to `decided`, which is deliberately vague
    because WHICH judgement was taken lives in the queue's own opaque payload. A caller that knows
    what it did says so. A default rather than a required argument, because many areas write
    receipts and not all of them name a verb and an object.
    """

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
    """What taking back one decision of many acts did: how many went back, out of how many.

    A decision that is one act answers `reverse` with a yes or a no. One that is many (a batch of
    renames) can have some of its acts refused while the rest go back, and a yes would then read
    as all of them. `said` is the line a person reads when not every one went back: how many did,
    what they are, and why the rest stayed. The reverser writes it because only it knows the noun
    and the reason. None when every one went back.
    """

    put_back: int
    of: int
    said: str | None = None
    #: Other receipts this reversal took back with it, by id: decisions that rested on this one
    #: and were put back in the same press. The workbench marks each taken back, so its own line
    #: reads as undone rather than offering an Undo that has nothing left to do.
    along: tuple[str, ...] = ()


class Reverser(Protocol):
    """Somewhere a receipt of one kind can be read back and put back.

    Separate from `Queue` because the two responsibilities look like one and are not. **A queue is
    a card on a screen; a reverser is the ability to take back a decision that was already
    written.** Every queue is both, but an area can stop offering a card while its receipts are
    still on disk, and those receipts must still be undoable rather than answered with "nothing in
    this version knows how to take that decision back".

    A reverser that is not a queue is never surveyed and never drawn. It exists to answer two
    questions about a receipt already written, and it is registered with `register_reverser`.
    """

    @property
    def name(self) -> str: ...

    #: Whether ANY decision of this kind can be taken back. False where every decision is final.
    #:
    #: Declared here rather than worked out, because there is no way to find out by asking: calling
    #: `reverse` to see whether it would succeed IS the reversal. Without it the record would show
    #: an Undo button beside every decision ever taken, including the ones whose own sentence says
    #: they cannot be undone, and pressing it would answer "there was nothing left to put back",
    #: which reads as undo being broken rather than as the decision being final.
    #:
    #: Per KIND and not per decision, which is the honest limit of it. One with a mix (a verdict
    #: that can be taken back and another that deleted a file) stays True here and keeps refusing
    #: the individual ones in `reverse`, because this cannot express "sometimes".
    #: Read-only, like `name` above, and that is not a detail. A plain annotation in a Protocol
    #: demands a SETTABLE attribute, so an implementation that works this out (the test double
    #: returns `not self.final`) does not satisfy it, and every registration of one is a type
    #: error. Nothing ever assigns to a `reversible`: it is asked, never set.
    @property
    def reversible(self) -> bool: ...

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """A few pictures of what one decision of this kind was about.

        From the payload the decision wrote down, which is the only thing that still names what was
        acted on: the rows themselves have moved by then, which is the point of the decision.

        Scoped like everything else: a record of a decision about files this user may no longer
        see shows no pictures of them. It still shows the decision, because it is a record of what
        the user did themselves.

        Answering with nothing is allowed and means "there is nothing to show", not "this failed".
        Decisions that are not about anything picturable simply say so.
        """
        ...

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool | Reversal:
        """Put back what one decision did. True if anything was actually put back.

        A decision of many acts answers with a `Reversal` instead, so the screen can say how many
        went back rather than a yes that reads as every one of them.

        The payload is what the decision wrote down about itself at the time. Reversing from it
        rather than from the current state is what keeps an undo from touching rows the decision
        never touched: a person who already existed is not deleted, an attribution that predates
        the decision is not detached.
        """
        ...


# --- A DECISION WORDED FROM WHAT IT RECORDED, WHEN IT IS SHOWN -----------------------------------
#
# A receipt's title is composed at the press and stored, and a stored sentence keeps its words for
# good: "Kept your answer for Ada Byron" about a person who is called Esme Wrenfield, "set aside"
# after the word became Discarded, "breast_type: 'FAKE' became 'NATURAL'" with the field's key and
# the raw values in it. So the line a decision SHOWS is worded when it is shown, by the one party
# that can read what the decision wrote down (the area that wrote it, exactly as
# `Reverser.pictures_of` is asked) from the facts the row recorded, with every thing it names said
# as it is NOW.
#
# The stored title is kept, and it keeps two jobs: it is what the record folds on (a run of receipts
# saying the same thing is one row: see the workbench store), and it is the words shown for an old
# row that recorded nothing else. A gate counts the areas that still answer with it.


def payload_held(payload: str) -> dict[str, Any]:
    """What a decision wrote down, as a record, or an empty record when it cannot be read.

    A payload is text its queue wrote. One that is not JSON, or is JSON but not an object, holds
    nothing to read, and a reader answers from an empty record (the stored title, an Undo that says
    no) rather than an error on a page somebody opened to find out what happened.
    """
    try:
        held: Any = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


@dataclass(frozen=True, slots=True)
class Recorded:
    """Everything one decision wrote down, which is all its words may be made from.

    The ledger's own columns (the verb, who acted, the thing it was done with, the count and the
    subjects) beside the queue's payload, which only the area that wrote it can read. The stored
    title and detail are here too, and deliberately: a decision written by an older build recorded
    some of its facts ONLY in those words (the value a stash-box offered, before the payload carried
    it), in a format its writer fixed and no build writes any more. An area may read such a format
    back, strictly, as a record of those facts; it never shows the words themselves as the line.
    """

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
    #: HOW MANY RECEIPTS THE LINE STANDS FOR. One on an ordinary decision; more where the record
    #: folded a run of receipts saying the same thing into one row (a pass writes one per file).
    #: The line is the run's, so an area words a run as the run ("Sift filed 4,000 files under
    #: ...") rather than as the one file its newest receipt happens to name.
    run: int = 1
    #: THE PAGE IT IS SHOWN ON, `(kind, id)`, where a History page asks; None on the board's decision
    #: record and the feed, which are nobody's page. For a decision about MANY things, drawn on one of
    #: their pages: the area says the part about that one: "Sift added 4 starter pictures from
    #: FansDB for them" on her page, not the 300 pictures for 90 People the run added. The vantage
    #: WORD stays the reader's (`Named`); this only says whose page.
    page: tuple[str, str] | None = None

    def held(self) -> dict[str, Any]:
        """The queue's payload as a record, or an empty one where it cannot be read."""
        return payload_held(self.payload)


@dataclass(frozen=True, slots=True)
class Named:
    """One thing a worded decision names, said as it is when the line is SHOWN.

    Resolved by the reader, never by the area: its name now while it exists (and linked), "since
    merged into" the one that absorbed it where it was merged, and the name it had (plain words,
    never a link) where it has gone. A name an area recorded is the fallback and never the answer
    while the thing is there: a person renamed since is called by the name they have.

    `id` may be empty where an old row recorded only the name (a stash-box named in a title before
    the payload carried its id); the thing is then said by that name and linked nowhere.
    """

    kind: str
    id: str
    #: What it was called when the decision was taken, where the decision recorded that.
    recorded: str | None = None
    #: SAID BY THE RECORDED NAME, where that name IS the fact the line is about: "You kept the
    #: name Esme Wrenfield over FansDB's Ada Byron". Renamed since, the line would otherwise put the
    #: new name where the kept one was and say something false; so the recorded name is said, and
    #: the name it has now follows it ("Esme Wrenfield (now Ada Lumen)"), linked.
    as_recorded: bool = False
    #: SAID WITH ITS KIND before its name ("the Photo Set Cassia Lynn") where a name alone can
    #: lie: a Photo Set is often named after the person in it. The READER says the kind, and only
    #: before a name: a thing with none is already said by its kind ("a Photo Set that is gone"),
    #: and an area writing "the Photo Set " itself would draw "the Photo Set a Photo Set that is
    #: gone" on every card whose set has since been deleted.
    kind_said: bool = False


@dataclass(frozen=True, slots=True)
class Doer:
    """Who took the decision, said when shown: "You" to the person who did, their name to anybody
    else, "Sift", or the stash-box's name. A piece rather than a word the area writes, because only
    the reader knows who is reading."""


#: The one `Doer`. A line starts with it: actor first, active voice.
DOER: Final = Doer()

#: One run of a worded line: plain words, a thing it names, or who did it.
Piece = str | Named | Doer


@dataclass(frozen=True, slots=True)
class Worded:
    """A decision's line as pieces, and the one plain line that may sit under it.

    `said` is the act, actor first. `more` is what the act left behind that the line does not say
    ("FansDB's answer was discarded too") and is empty far more often than not: a detail that
    repeats the line in more words is the habit of a stored text.
    """

    said: tuple[Piece, ...]
    more: tuple[Piece, ...] = ()


@runtime_checkable
class Words(Protocol):
    """An area that can word its own decisions from what they recorded. See `Recorded`.

    Optional, and asked the way `pictures_of` is: a reverser that does not have it keeps showing
    the stored title, and so does one that answers None, which means "this row recorded nothing
    I can word", the honest answer for an old row whose facts were never written down. Pure and
    synchronous: everything it may use is in `Recorded`, and naming a thing is the reader's job.
    """

    def worded(self, recorded: Recorded) -> Worded | None: ...


#: What a row of the record says under its line once every decision it stands for has been taken
#: back, where the area that wrote it says nothing more particular. See `TakenBack`.
TAKEN_BACK: Final = "Taken back."


@runtime_checkable
class TakenBack(Protocol):
    """An area that can say, in one sentence, what taking one of its decisions back left true.

    The sentence a decision wrote is about the moment it was taken ("All 2 files were kept, and
    these will not be raised again"), and it stops being true the moment the decision is undone:
    a row reading "Undone" beside "these will not be raised again" says two opposite things. So a
    row whose every decision is back shows this instead of the stored sentence, and the stored
    sentence stays on the receipt untouched: it is still what the decision said.

    Optional and asked the way `Words` is. A reverser without it gets `TAKEN_BACK`, which is true
    of every undone decision and claims nothing about what the area does next.
    """

    @property
    def taken_back(self) -> str: ...


@runtime_checkable
class Grouped(Protocol):
    """A reverser with no card of its own whose decisions are taken on a group's card.

    Optional, and asked the way `Words` is. Every queue is one; a reverser that is not a queue
    declares it to say which card its receipts were written from. See `Workbench.card_of`.
    """

    @property
    def group(self) -> str | None: ...


#: The announcements that can change what a pile of library things holds: a decision or a pass
#: writing to it (`LIBRARY`), a file arriving or leaving (`ARRIVALS`), a dial (`SETTINGS`), and the
#: viewer's own lists (`MINE`). What the visibility rules let a viewer see is in the `Viewer` itself
#: (`cache_stamp`), so a summary is kept per viewer and never needs a subject for that.
MOVED_BY: Final = frozenset({About.LIBRARY, About.ARRIVALS, About.SETTINGS, About.MINE})

#: How many summaries are kept: one per queue and viewer, and a viewer is one user in one vault
#: state, so this is the queues times the few people who look.
KEPT_SUMMARIES: Final = 512


@runtime_checkable
class Moves(Protocol):
    """A queue that says which announcements can change what it holds. See `Workbench.board`.

    Optional, and asked the way `Words` is: a queue without it is kept under `MOVED_BY`. None means
    the pile is not wholly in the database (a folder on disk) or moves with work nobody announces
    as such, so it is surveyed on every read; that is only for a survey that costs nothing.
    """

    @property
    def moved_by(self) -> frozenset[About] | None: ...


@runtime_checkable
class Stills(Protocol):
    """A queue whose cards draw only the files whose still has been made, so a picture made moves
    it. Every other queue is kept through a Generate's pictures (`changes.mark_of`)."""

    @property
    def draws_stills(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class Card:
    """The card on the board a decision was taken on: the queue whose page it opens, and the
    words the card is headed with."""

    name: str
    title: str


class Queue(Reverser, Protocol):
    """One kind of waiting work: how to count it, and how to take a decision back.

    A `Reverser` that also appears on the board. See that protocol for why the two are separable.
    """

    #: What the pile is called, declared on the class like the band, so it can be named without a
    #: survey (which counts). Stamped onto the summary by `Workbench._surveyed`.
    @property
    def title(self) -> str: ...

    #: What KIND of pile this is, matching the `band` of the summary it returns. What decides how
    #: the board draws it. See `Band`. The two are checked against each other in
    #: `test_workbench.py`: a queue that says one thing here and another in its summary is a
    #: hand-written list beside the thing it mirrors, and it fails the build rather than quietly
    #: drawing itself in the wrong band of the screen.
    #: Read-only for the same reason as `reversible` below: an implementation that computes it
    #: would otherwise be refused, and nothing here ever writes to it.
    @property
    def band(self) -> Band: ...

    #: Which queues this one shares a PAGE with, or None to stand alone.
    #:
    #: A second one-word declaration rather than a reuse of the band, and the difference is the
    #: whole reason both exist. The Faces page's tabs are Awaiting review, Ignored and Identified:
    #: one judgement and two records, three bands' worth of meaning on one page, while Usernames
    #: Waiting shares the judgement band with Awaiting review and has no business on its page. So
    #: the band says how the BOARD sorts a queue and the group says which queues are ALTERNATIVES to
    #: each other, and neither can be worked out from the other.
    #:
    #: A group of one is not a group: a queue standing alone answers None and its page draws no
    #: tabs at all, rather than a row with a single word in it.
    #:
    #: Read-only, like the two above, and for the same reason.
    @property
    def group(self) -> str | None: ...

    #: What the GROUP is called, declared by the one queue that leads it.
    #:
    #: The board draws a group as ONE card: a group is one page, and two cards pointing at two
    #: tabs of it would be two ways in to one job, in two different bands. The card needs a name,
    #: and that name cannot be either member's title: *Near Duplicates* over a card holding exact
    #: copies as well is a card that lies about half of what it opens.
    #:
    #: Declared on the LEAD rather than agreed between the members, so there is one answer and no
    #: two of them to disagree. Every other member answers None, and a group whose lead declares
    #: nothing keeps its members' own titles and is drawn as separate cards, which is the honest
    #: fallback for a group that never meant to be one card.
    @property
    def group_title(self) -> str | None: ...

    #: WHAT THE QUEUE'S CARD ON THE BOARD IS FOR, in one short plain sentence, or None on a
    #: record, which is never a card.
    #:
    #: A card on the board names a pile, says what it is for, counts it, shows a few pictures of
    #: it and opens its page; nothing on the card decides anything. So this sentence is a
    #: description and never a question: the same words for everybody, declared on the class
    #: like the band, never built from a count, a name or a file (a card that read "Do these 1,900
    #: faces look like Wren Halloway?" would ask about one item and describe none of the pile). The
    #: queue that leads a group says what the GROUP's page is for, because the card it leads is
    #: that page; every other member says what its own tab is for.
    #:
    #: Not `decision`: that is the queue page's lede, which says what deciding one item does and
    #: can run to three sentences. A gate holds this one to a sentence a card can carry
    #: (`tests/gates/test_every_queue_says_what_it_holds.py`).
    @property
    def purpose(self) -> str | None: ...

    async def available(self) -> bool:
        """Whether this queue has anything behind it on this install.

        Asked separately from the count, and the distinction is the whole reason it exists. A queue
        with nothing in it is finished, and says so. A queue whose feature is switched off, or whose
        source of work has never run, has no business being on the board at all: an empty panel is
        worse than an absent one, because it reads as a feature that does not work rather than as
        one that is not turned on.
        """
        ...

    async def survey(self, viewer: Viewer) -> Summary:
        """What is waiting, as this user may be told about it."""
        ...


@dataclass
class Workbench:
    """Every queue that has registered, in the order they registered.

    Held on the application rather than in a module-level dictionary, unlike the other registries
    here. A queue is built from a feature's own service, which exists only once the application has
    been assembled, so registration happens at boot, and a process-global would then be shared
    between every application a test builds.
    """

    queues: list[Queue] = field(default_factory=list)

    #: Everything that can take one of its own receipts back, which is every queue and then some.
    #:
    #: Held apart from `queues` because the two answer different questions and one of them outlives
    #: the other: a receipt is on disk for as long as the library exists, while the card that
    #: produced it may be retired the next release. See `Reverser`.
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
        """Claim a name. Registering the same one twice is a bug, not an override.

        A RESERVED name is refused for the same reason and with the same force. A queue lives at
        `/organize/<name>`, and the client has fixed screens beside that address: the router
        prefers the fixed one, so a queue claiming `decisions` would be a card on the board that
        opens somebody else's page, with nothing anywhere saying why. Refused here, at boot, so it
        is a failed start rather than a screen that quietly goes to the wrong place on the one
        install that has that queue.
        """
        if any(existing.name == queue.name for existing in self.queues):
            raise ValueError(f"a queue named {queue.name!r} is already registered")
        if queue.name in RESERVED_NAMES:
            raise ValueError(
                f"{queue.name!r} is reserved: a screen already lives at /organize/{queue.name}"
            )
        self.queues.append(queue)
        self.register_reverser(queue)

    def register_reverser(self, reverser: Reverser) -> None:
        """Claim a name for taking receipts back, without offering a card.

        For an area whose pile has been retired but whose decisions are still in the table. See
        `Reverser` for the case that produced it. Registering a queue does this too, so nothing
        that draws a card has to remember.
        """
        if any(existing.name == reverser.name for existing in self.reversers):
            raise ValueError(f"{reverser.name!r} already knows how to take its decisions back")
        self.reversers.append(reverser)

    def reverser(self, name: str) -> Reverser | None:
        """What knows how to read and put back one kind of receipt, or nothing.

        Asked of the reversers rather than of the queues, and that is the whole point of the split:
        a receipt written by a pile that has since been retired is still a receipt somebody can
        take back, and looking it up among the CARDS would answer "this version does not know how"
        about a version that knows perfectly well.
        """
        return next((one for one in self.reversers if one.name == name), None)

    def recorded_under(self, group: str | None) -> list[str]:
        """Every name a decision on one group's card may be recorded under."""
        if group is None:
            return []
        return [
            one.name for one in self.reversers if isinstance(one, Grouped) and one.group == group
        ]

    def card_of(self, name: str) -> Card | None:
        """The card a decision recorded under `name` was taken on, or None for a name no card
        answers (a retired pile, or a record Sift writes of its own work).

        Joined as the board joins them (`frontend/src/lib/organize/bands.ts` `bandsOf`): a group is
        one card, led by its first pending queue, and headed by that queue's `group_title`.
        """
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
        """The queues with something behind them, of those named in `only` when it is given. See
        `Queue.available`."""
        asked = [one for one in self.queues if only is None or one.name in only]
        behind = await asyncio.gather(*(one.available() for one in asked))
        return [one for one, has in zip(asked, behind, strict=True) if has]

    async def board(self, viewer: Viewer, only: Collection[str] | None = None) -> list[Summary]:
        """Every available queue, or those named in `only`, as this user may be told about it.

        `only` is for a screen that draws a few queues and is asking again because the library
        moved.

        **A summary is surveyed once per announcement that can change it, never per read.** Each
        is kept per queue and viewer under the mark of the subjects that can move it (`Moves`,
        `MOVED_BY`), so a board asked again by every open screen, or after work that cannot touch
        a pile, reads what is kept. The `Viewer` is part of the key, so a change to what somebody
        may see (their `cache_stamp`) or an unlocked vault is a different summary. Readers that
        arrive while a survey is running share it. With nothing announcing (a console tool, most
        tests) nothing could say a summary went stale, so every read surveys.

        The surveys that do run, run together: a slow queue does not hold the others up, and the
        order the board is drawn in is the order queues registered in, which `gather` keeps.
        """
        return list(
            await asyncio.gather(
                *(self._summary(one, viewer) for one in await self.available(only))
            )
        )

    async def _summary(self, queue: Queue, viewer: Viewer) -> Summary:
        """One queue's summary for this viewer: the kept one while nothing that can move it has
        been announced, or one survey shared by every reader asking meanwhile. See `board`."""
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
        # Shielded, so a reader that goes away (a closed tab) does not cancel the survey the
        # others are waiting on.
        return await asyncio.shield(asking)

    def _landed(self, key: tuple[str, Viewer], mark: str, done: asyncio.Future[Summary]) -> None:
        """Keep a finished survey under the mark it started at. One that failed keeps nothing,
        and its error reaches the readers that shared it."""
        self._asking.pop((key[0], key[1], mark), None)
        if done.cancelled() or done.exception() is not None:
            return
        self._kept[key] = (mark, done.result())
        self._kept.move_to_end(key)
        while len(self._kept) > KEPT_SUMMARIES:
            self._kept.popitem(last=False)

    @staticmethod
    async def _surveyed(queue: Queue, viewer: Viewer) -> Summary:
        """One survey, timed under the queue's own name, wearing the queue's own band. See `board`.

        The band, the group and the card's purpose are put on HERE rather than by each survey, and
        that is the whole reason a queue cannot be drawn in the wrong half of the screen: they are
        declared once, on the class, and this is the only thing that reads them onto a summary. A
        survey that set them would be a second copy free to disagree with the first, kept in step
        by a test rather than by construction.
        """
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
