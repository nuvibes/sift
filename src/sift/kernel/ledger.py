# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one door everything that happens in the library is written through.

Tables of CURRENT STATE cannot explain a library: removing a person from a file would erase,
retroactively, the fact that they had ever been on it; a revoked share deletes its own row; a
record field edited, a person renamed, a merge, a file deleted leave nothing anywhere. So everything
leaves a row, and the row outlives what it is about. This is where that row is written.

## Why this is a door and not a table

There is no single write transaction in Sift: `telling()` wraps about a third of them and the
rest open the database directly, so nothing can catch every act from underneath. That makes this
a DISCIPLINE: each writer says what it did. A discipline needs one place to say it, in one
vocabulary, with the rules enforced at the moment of writing rather than checked afterwards over a
table nobody reads. Three rules, and each is here because the alternative has a name:

- **A closed list of verbs.** A free string lets two areas write `remove` and `removed` for the
  same act, and the only symptom is a history that is missing half of what happened: a silence,
  which is the failure the record exists to end.
- **One event per file.** A face scan that wrote a row per FACE would make this the largest table
  in the database within a week, and a whole-library pass that wrote a row per file would add a
  hundred thousand rows for one press. So the door refuses a call that names more subjects than an
  act plausibly has, and a pass hands a count instead. See `MOST_SUBJECTS`.
- **A snapshot, never a key.** The subject's kind, id AND the name it had at the time; the actor as
  a word rather than a foreign key. An event that cascaded away with its subject would erase
  exactly the year's worth of "what I removed" that somebody opens this record to read.

## What it writes to, and why not a new table

`workbench_decisions`, widened. See `slices/workbench/schema.py` for the argument. It is an event
log with a subjects link table in a readable vocabulary, written to through a kernel protocol that
needs no imports. A second table beside it would be a second mechanism to keep true and a second
answer to "what happened to this file".

## What it does NOT do

It writes no sentence. Receipts carry a title and a detail composed when the decision was taken,
and that is right for them: a sentence assembled later from the current state describes the
library as it is now, which is what somebody reading the past is trying to look behind. An event
carries the verb, the object and the names those things HAD, which is enough to assemble a true
past-tense sentence at any time, so the sentence belongs to the reader, in one place, rather than
to each of the fifty writers. `kernel/access/sentences.py` is that place; screens draw its pieces.

It reads one thing and one only: the NAME of something a caller handed without one. See
`record_event` and `NAME_NOW`. Concealment, and the reads that apply it, are
`kernel/access/history_events.py`: a permission rule written next to a write door would be a second
place to get it wrong.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from sift.kernel.client import current as current_client
from sift.kernel.db import Connection, in_clause
from sift.kernel.ids import new_id
from sift.kernel.vocabulary import (
    LEDGER_QUEUE,
    SIFT_ACTS_BY,
    Subject,
    SubjectKind,
)

#: Every word an event's verb may be. One list, closed, and extended here when a writer needs a
#: word.
#:
#: Closed for the reason `SubjectKind` is closed: this is what a reader groups and filters on, and a
#: free string would let two areas spell one act two ways. Extended rather than argued over: a
#: writer that has no word for what it did should add one here, in the same commit, rather than
#: reach for the nearest wrong one.
#:
#: Past tense throughout, because every row in this table is a thing that has already happened. The
#: two that are not obviously verbs earn their place: `kept_local` is the refusal to send a file or
#: an entity outside the LAN, which is an act somebody takes and takes back, and `forgot` is a
#: feature's own data deleted on purpose (the faces feature's Delete face data). No act removes a
#: row from here.
VERBS: Final = frozenset(
    {
        "added",
        "removed",
        "renamed",
        "named",
        "filed",
        "moved",
        "linked",
        "unlinked",
        "hidden",
        "revealed",
        "shared",
        "unshared",
        "kept_local",
        "allowed",
        # "Do not swap" put on and taken off: the refusal's other purpose (see `catalog.Purpose`).
        "kept_from_swaps",
        "allowed_in_swaps",
        "merged",
        "edited",
        "enriched",
        "asked",
        "scanned",
        "produced",
        "deleted",
        "forgot",
        # THE TWO ENDINGS OF A DOWNLOAD. Two words rather than one carrying an
        # outcome in its payload, for the reason every other pair here is two: a reader groups and
        # filters on the verb, and "show me what would not download" is the question a queue of
        # failures exists to answer. `download_failed` is the one verb in this list that is not a
        # single word, and it stays that way because "failed" alone would be claimed by the next
        # area that has a failure (a scan, an import, a stash-box) and one word for two kinds
        # of failure is the drift `VERBS` is closed to prevent.
        "downloaded",
        "download_failed",
        # The two halves of stopping a piece of work and starting it again: a download today, and
        # nothing else yet. They are a pair and both are here because a record that says a thing was
        # paused and never says it came back is a record of an outage.
        "paused",
        "resumed",
        # What a judgement taken on a queue says, and the word every row written before the ledger
        # carries. It is deliberately vague: WHICH judgement lives in the queue's own payload, and
        # the payload is opaque to everything but the area that wrote it.
        "decided",
        # COOKIES AND A CANCEL. Cookies are the one credential this record
        # speaks about, so the three things somebody does with them are three words rather than
        # one `edited` with the act hidden in a payload nothing filters on. `canceled` is a
        # download stopped for good by a person: not `removed` (the row stays) and not
        # `download_failed` (nothing failed).
        "cookies_saved",
        "cookies_replaced",
        "cookies_forgotten",
        "canceled",
        # A COPY TAKEN AWAY AND A PASS THAT RAN. `saved` is a file saved to somebody's own device:
        # the save log's act, which the log keeps for an admin and this puts on the file's own
        # record. `ran` is one of the long passes over the library
        # finishing (`kernel/jobs/ledger.py`), with its counts in the payload.
        "saved",
        "ran",
        # A PASS SOMEBODY PRESSED OVER ONE FILE (`kernel/presses.py`): the person is the actor, the
        # file the subject, and the passes and the moment the work began are in the payload. One
        # per press and kept for ever, so a second look at a file never takes the first one's
        # place on its History. Written by the worker pool as the job is marked done.
        "pressed",
        "face_run",
        # A song named on a file by Sift (the download task): the object is the Site whose page
        # said it (later, the file it was shared from) and the song is in the payload.
        "song_named",
        # A SWAP WITH ANOTHER SIFT, its two ends (`slices/swap`). Two words rather than one carrying
        # an outcome, for the reason the download's two endings are two: a reader filters on the
        # verb. The subject is the session (`SubjectKind` "swap"); the payload holds the side this
        # device took, the other device's id, why it ended and how many files moved, and never
        # an address, a token or the code.
        "swap_started",
        "swap_ended",
        # A THEATER WALL SENT from one of a person's devices to another (older lines; the sends
        # are retired and the verb stays so those lines still read):
        # the files that were in its cells are the subjects, and the payload says which kind of
        # device it went to and what the sending one calls itself. Not `shared`: nothing changed
        # who may see anything.
        "wall_sent",
        # A BACKUP RESTORED as this library (`slices/backup`): its own word, because nothing else
        # here says the whole library was replaced by an earlier copy of itself. The subject is the
        # backup file (`SubjectKind` "backup"). Written into the restored database once it is
        # open, so the library's own History says when it went back and to which day.
        "restored",
        # A LIBRARY MADE FROM A SIFT DATABASE FILE on the Database Switcher (`slices/backup`):
        # `adopt_database`'s own word for the act. Not `restored`, because the file need not be a
        # backup of this library or of any; not `produced`, which is a file made from a file. The
        # subject is the file (`SubjectKind` "database_file"), and it is the first line the new
        # library writes, once it is open.
        "adopted",
        # AN ACT ON THE COMPUTER RUNNING SIFT, asked from a window (`kernel/machine_acts.py`): each
        # changes that machine rather than the library, so none of the words above says it. One
        # word per act and per end of a switch, for the reason the download's two endings are two:
        # a reader filters on the verb. The subject is the computer (`SubjectKind` "computer"); the
        # payload holds the device the window runs on, and the version or library the act names.
        "sharing_turned_on",
        "sharing_turned_off",
        "start_with_windows_on",
        "start_with_windows_off",
        "firewall_opened",
        "storage_moved",
        "update_started",
        "library_opened",
        "restarted",
    }
)

#: THE ACTS SIFT MAY RECORD WITHOUT SAYING WHICH TASK TOOK THEM, by verb, each with its reason.
#:
#: Short on purpose, and every entry has to say why the task is already known some other way:
#: that is the only honest reason for an actor with no task. Anything else that reaches the door as
#: "Sift" and nothing more is refused, because it is a writer that knows which of its tasks acted
#: and did not say.
SIFT_WITHOUT_A_TASK: Final[Mapping[str, str]] = {
    # `kernel/jobs/ledger._finish` and `kernel/jobs/queue._record_runs`: the event's SUBJECT is the
    # run, named with the task's own word ("Scan", or a scheduled task's title). The task is the
    # thing the line is about, so saying it again as the actor would be the same fact twice.
    "ran": "the subject is the run itself, named with the task's own word",
}

#: The kinds of thing that always HAVE a name, and so may never be recorded without one.
#:
#: Refused nameless rather than drawn as "a file", "a person": an event outlives its subject on
#: purpose, and a thing nobody named at the time can never be named afterwards: the page it
#: appears on says "Cover set to a file" for good. The door fills the name itself from `NAME_NOW`
#: where the caller did not hand one and the row is still there; what is refused is the case
#: nothing can fill, which is a writer that recorded AFTER the row went and did not keep the name.
#:
#: Seven and not every kind: a username can be the blank "poster unknown" row, a folder, a pile, a
#: run and a setting are named by their writers or have no name at all, and a download names
#: itself by the address somebody pasted.
NAMED_KINDS: Final = frozenset(
    {"asset", "person", "tag", "site", "collection", "photo_set", "song"}
)

#: WHAT EACH KIND OF THING IS CALLED NOW, one statement per kind, keyed `id` and `name`.
#:
#: The one definition of a thing's name in the record, read in two places for two reasons: here,
#: at the moment of writing, to fill a name a caller did not hand (`record_event`); and by the
#: history reader, for rows written before names were recorded at all
#: (`history_names.names_now`). Two tables would be two answers to "what is this file called",
#: and a name could be on the feed and missing from the file's own page for the same act.
#:
#: An asset answers in the file page's order: its title where a person typed one, then the name it
#: has on disk now (its first copy still present), and the name it arrived under only when no copy
#: is anywhere Sift can see. The imported name first would keep calling a file renamed on disk by
#: a name the person no longer sees anywhere. A download answers the file it
#: produced or the address somebody pasted, and a hidden one answers nothing.
#: Two of these read permission-carrying tables from outside `kernel.access`, and are allowed to
#: (the semgrep rule is answered on each line): a name is looked up for a row the writer has just
#: acted on, inside that writer's own transaction, and written into the ledger, which the access
#: layer then scopes to whoever reads it. No row reaches a caller from here.
NAME_NOW: Final[Mapping[str, str]] = {
    "asset": (
        "SELECT a.id AS id, COALESCE(NULLIF(a.title, ''),"
        " (SELECT l.filename FROM asset_locations l"  # nosemgrep: sift-no-asset-sql-outside-kernel
        "  WHERE l.asset_id = a.id AND l.status = 'present' ORDER BY l.first_seen_at LIMIT 1),"
        " a.original_filename) AS name"
        " FROM assets a WHERE a.id IN (?*)"  # nosemgrep: sift-no-asset-sql-outside-kernel
    ),
    "person": "SELECT id AS id, name AS name FROM people WHERE id IN (?*)",
    "site": "SELECT id AS id, name AS name FROM sites WHERE id IN (?*)",
    "tag": "SELECT id AS id, name AS name FROM tags WHERE id IN (?*)",
    "collection": "SELECT id AS id, name AS name FROM collections WHERE id IN (?*)",
    "photo_set": "SELECT id AS id, name AS name FROM photo_sets WHERE id IN (?*)",
    "song": "SELECT id AS id, name AS name FROM songs WHERE id IN (?*)",
    "username": "SELECT id AS id, name AS name FROM usernames WHERE id IN (?*)",
    "login": "SELECT id AS id, username AS name FROM users WHERE id IN (?*)",
    "folder": "SELECT id AS id, name AS name FROM folders WHERE id IN (?*)",  # nosemgrep: sift-no-asset-sql-outside-kernel
    "download": (
        "SELECT id AS id, COALESCE(NULLIF(filename, ''), url) AS name"
        " FROM downloads WHERE id IN (?*) AND hidden_at IS NULL"
    ),
}

#: How many ids one name lookup binds. Well under SQLite's variable limit on every build Sift runs
#: on, and a receipt naming more files than this is rare enough that a second statement is nothing.
_NAMES_PER_READ: Final = 500


#: The most subjects one event may name, unless it is a receipt. See `record_event`.
#:
#: **The number is a shape rather than a measurement, and it is worth saying which.** There is no
#: cost that appears at nine subjects and not at eight. What the cap enforces is that a call naming
#: a long list has to stop and say what it is doing: either one event per file, or one event with
#: a count, and eight is chosen because it is comfortably above every act that genuinely concerns
#: several things together (a merge names two people; a file gains three tags; a photo set is made
#: of a handful of files' worth of decision) and far below the size at which a list is really a
#: pass. A writer that finds eight too few is nearly always a writer that should be handing a count.
MOST_SUBJECTS: Final = 8

#: The three words `created_by_kind` already stores, reused rather than re-spelled.
#:
#: See `kernel/access/catalog.Made`, which answers "who made this ROW". This answers "who took this
#: ACT", which is a different question about the same three kinds of doer, so the words are
#: imported in spirit and the two types stay apart: `Made` carries a pass only for Sift and has no
#: room for a box id, because a box is stamped onto a row later by a separate pass.
ACTOR_SIFT: Final = "sift"
ACTOR_USER: Final = "user"
ACTOR_BOX: Final = "box"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who took an act: one of three kinds, and which one.

    A snapshot and never a foreign key. `workbench_decisions.user_id` is a key with `ON DELETE SET
    NULL` on it, so deleting a user quietly un-authors every act it ever took, which is
    precisely the retroactive erasure the ledger exists to end. This is written beside it and
    survives.

    `id` means a different thing per kind, and that is why the kind is stored beside it rather than
    inferred: a user id, the name of the PASS or TASK for Sift (one of `SIFT_ACTS_BY`: the
    `MADE_VIAS` a row's provenance carries, so a row's provenance and the `enriched:` filter that
    finds the files it was read out of cannot come to mean different things, and the `TASK_VIAS`
    that act without making a row), and a stash-box's id for a box.

    NONE FOR SIFT IS REFUSED, outside `SIFT_WITHOUT_A_TASK`: allowed, nearly every act Sift records
    would say nothing about which task took it, and a person Sift made from a folder would read
    "Sift" and nothing else. Which task acted is always known where the act happens and never
    afterwards, so the door asks for it.
    """

    kind: str
    id: str | None = None

    @staticmethod
    def sift(via: str | None = None) -> Actor:
        """Sift itself, and which pass or task (`SIFT_ACTS_BY`).

        The word is optional in the signature and not at the door: only the acts in
        `SIFT_WITHOUT_A_TASK` may be recorded without one.
        """
        return Actor(ACTOR_SIFT, via)

    @staticmethod
    def user(user_id: str) -> Actor:
        """Somebody signed in, by their user id."""
        return Actor(ACTOR_USER, user_id)

    @staticmethod
    def box(box_id: str) -> Actor:
        """A stash-box answered, by its id."""
        return Actor(ACTOR_BOX, box_id)


@dataclass(frozen=True, slots=True)
class Object:
    """What an act was done TO or WITH, beside what it was done to.

    The difference between subject and object is the difference between "which files does this
    appear in the history of" and "what was it done with": naming a person on seven files has seven
    subjects and one object, and a reader looking at any of those files wants the person's name in
    the sentence.

    `name` is what it was called at the time, for the reason `Subject.name` is: a name looked up
    when the row is READ is the name it has now, which is what the record is being consulted to see
    behind.
    """

    kind: SubjectKind
    id: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Reversal:
    """What makes an event a receipt: the queue that can take it back, and the sentence it shows.

    Present only on an event somebody can undo, which is what `record_event`'s `receipt` argument
    means. Everything else in the library is an act that has happened: a file was deleted, a share
    was revoked, a title was edited, and none of those is a card on the Organize board.

    The queue's name is what `Workbench.reverser` is asked for, so a receipt whose area has since
    been retired still offers undo and is refused with the honest sentence rather than with a
    greyed-out button. An event with no reversal is written under `LEDGER_QUEUE`, which no queue may
    claim, so the same lookup finds nothing for it and the same sentence is what it gets.
    """

    queue: str
    title: str
    detail: str


_RECORD = (
    "INSERT INTO workbench_decisions"
    " (id, queue, user_id, title, detail, payload, decided_at, reversed_at,"
    " verb, actor_kind, actor_id, object_kind, object_id, object_name, touched,"
    " client_kind, device_id)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

# `OR IGNORE` for the reason the store's own subject insert has it: the key is the whole row bar the
# name, so a caller handing the same file twice (a group whose list repeats, an area passing what
# it knows from two directions) is saying the same true thing twice, and refusing it would fail a
# real write over a bookkeeping duplicate.
_RECORD_SUBJECT = (
    "INSERT OR IGNORE INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
    " VALUES (?, ?, ?, ?)"
)


class LedgerError(ValueError):
    """A call the door refuses, with a sentence saying which rule it broke."""


async def record_event(
    connection: Connection,
    *,
    actor: Actor,
    verb: str,
    subject: Subject | Sequence[Subject],
    object: Object | None = None,
    count: int | None = None,
    payload: str | None = None,
    receipt: Reversal | None = None,
) -> str:
    """Write down one thing that happened. Returns the event's id, which is also its receipt id.

    **On the caller's own connection, always.** An event written afterwards can be missing for an
    act that happened or present for one that did not, and a record that is only usually right is
    worse than none: it is the thing somebody reaches for once they already know something has
    gone wrong. So it lands in the same transaction as the rows it describes, which is the same
    argument `Recorder.record_on` has made since the record existed.

    `subject` is what the event is ABOUT: the things whose history it appears in. One, or a few, and
    the cap is the rule:

    - **At most `MOST_SUBJECTS` of them**, unless the event is a receipt. A receipt's subjects are
      the rows its undo has to put back, and capping THAT would make undo wrong, so the rule
      applies to provenance, which is what it is for, and not to the reversal footprint, which it
      was never about.
    - **A pass hands a `count` and at most one subject.** One event saying "7,700 files were named
      from a folder" with the folder as its subject, never 7,700 rows. A pass that could afford to
      name each file would not need the count, which is what makes the two mutually exclusive
      rather than merely discouraged.
    - **None at all is refused, unless the event is a receipt.** An act about nothing is a row
      nobody can ever reach. A receipt is the exception for the same reason it is the exception
      above: two of the decisions this application writes are about a file that was never imported,
      so there is no row anywhere for them to name, and the decision is still one somebody took
      and can take back.

    The moment is read here rather than taken from the caller, for the same reason the record's
    own store reads it: whole seconds off the wall clock, once, in the one place that writes the
    row. The ORDER of two events written in the same second is the id's, which is a ULID and
    therefore chronological, and that is the ordering every read here uses, because a wall clock
    is not guaranteed to move forwards.

    The parameter is called `object` although that is a builtin. It is the record's own word for
    the thing an act was done with, it is the column's name, and a synonym here would be one more
    word to translate at every call site and in every read.
    """
    if verb not in VERBS:
        raise LedgerError(
            f"{verb!r} is not a verb the ledger knows. Add it to VERBS in kernel/ledger.py, once,"
            " rather than reaching for the nearest word that is already there"
        )
    if actor.kind not in (ACTOR_SIFT, ACTOR_USER, ACTOR_BOX):
        raise LedgerError(f"{actor.kind!r} is not one of sift, user or box")
    if actor.kind == ACTOR_SIFT and actor.id is not None and actor.id not in SIFT_ACTS_BY:
        raise LedgerError(
            f"{actor.id!r} is not a pass this build has a word for. See MADE_VIAS and TASK_VIAS"
            " in kernel/vocabulary.py, the first of which is the vocabulary a row's provenance uses"
        )
    if actor.kind == ACTOR_SIFT and actor.id is None and verb not in SIFT_WITHOUT_A_TASK:
        raise LedgerError(
            f"Sift {verb} something and did not say which task did it. Name the pass or task"
            " (Actor.sift(VIA_...)). Only the acts in SIFT_WITHOUT_A_TASK may leave it out"
        )
    if actor.kind != ACTOR_SIFT and not actor.id:
        raise LedgerError(f"an actor of kind {actor.kind!r} has to say which one")

    subjects = [subject] if isinstance(subject, Subject) else list(subject)
    if not subjects and receipt is None:
        raise LedgerError("an event with no subject is an event about nothing")
    if count is not None and len(subjects) > 1:
        raise LedgerError(
            "an event carrying a count names at most one subject: the thing the pass ran over."
            " A pass that can afford to name each file does not need the count"
        )
    if count is not None and count < 1:
        raise LedgerError("a count of nothing is not a pass that ran")
    if receipt is None and len(subjects) > MOST_SUBJECTS:
        raise LedgerError(
            f"{len(subjects)} subjects for one event, and the ceiling is {MOST_SUBJECTS}."
            " Write one event per file, or one event with a count. See MOST_SUBJECTS"
        )

    subjects, object = await _named(connection, subjects, object)

    # WHICH WINDOW AND WHICH DEVICE, for an act somebody took: the client of the request being
    # served (`kernel/client.py`), read here so no writer has to pass it and none can forget it.
    # Only on a User's act: Sift's own and a stash-box's happen on the computer running Sift, and
    # a request that happened to be open while one ran says nothing about them.
    stamped = current_client() if actor.kind == ACTOR_USER else None

    event_id = new_id()
    happened_at = int(time.time())
    await connection.execute(
        _RECORD,
        (
            event_id,
            LEDGER_QUEUE if receipt is None else receipt.queue,
            # The key as well as the snapshot, where there is a user. It is what the record
            # screen and the pace measurement already read, and dropping it would be a second
            # reading of "who" for them to disagree about; the snapshot beside it is what survives
            # the user being deleted. See `Actor`.
            actor.id if actor.kind == ACTOR_USER else None,
            "" if receipt is None else receipt.title,
            "" if receipt is None else receipt.detail,
            payload or "",
            happened_at,
            verb,
            actor.kind,
            actor.id,
            None if object is None else object.kind,
            None if object is None else object.id,
            None if object is None else object.name,
            count,
            None if stamped is None else stamped.kind,
            None if stamped is None else stamped.device,
        ),
    )
    await connection.executemany(
        _RECORD_SUBJECT,
        [(event_id, one.kind, one.id, one.name) for one in subjects],
    )
    return event_id


async def _named(
    connection: Connection, subjects: list[Subject], object: Object | None
) -> tuple[list[Subject], Object | None]:
    """The same things, each carrying its name, or a refusal naming the one that cannot have one.

    **A name the caller handed is kept exactly as handed**, and nothing is read for it: the caller
    may be recording what something WAS called (a rename's old name, a delete's last name), and a
    lookup would overwrite that with what it is called now. Only a missing name is filled, from the
    row as it stands inside this same transaction, which is the name it has at the moment of the
    act, the one the event exists to keep.

    One statement per kind with anything missing, bounded by the event: a receipt over two hundred
    files is one read, and an event whose writer named everything is none.
    """
    missing: dict[str, set[str]] = {}
    for one in subjects:
        if not one.name and one.kind in NAME_NOW:
            missing.setdefault(one.kind, set()).add(one.id)
    if object is not None and not object.name and object.kind in NAME_NOW:
        missing.setdefault(object.kind, set()).add(object.id)

    found: dict[tuple[str, str], str] = {}
    there: set[tuple[str, str]] = set()
    for kind, ids in missing.items():
        ordered = sorted(ids)
        for start in range(0, len(ordered), _NAMES_PER_READ):
            asked, values = in_clause(NAME_NOW[kind], ordered[start : start + _NAMES_PER_READ])
            for row in await connection.execute_fetchall(asked, values):
                there.add((kind, str(row["id"])))
                if row["name"]:
                    found[(kind, str(row["id"]))] = str(row["name"])

    named = [
        one if one.name else replace(one, name=found.get((one.kind, one.id), one.name))
        for one in subjects
    ]
    if object is not None and not object.name:
        object = replace(object, name=found.get((object.kind, object.id), object.name))

    # REFUSED ONLY WHERE THE ROW IS GONE. A row that is there and carries no name (a file with
    # neither a title nor the name it arrived under, which the schema allows and an ordinary library
    # does not hold) is recorded as it is: the door has asked the one place a name could come from, and
    # failing somebody's act over a column the row itself left empty would be the record breaking
    # the library rather than describing it.
    for kind, thing_id, name in [
        *((one.kind, one.id, one.name) for one in named),
        *(() if object is None else ((object.kind, object.id, object.name),)),
    ]:
        if kind in NAMED_KINDS and not name and (kind, thing_id) not in there:
            raise LedgerError(
                f"the {kind} {thing_id} has no name to record, and a {kind} always has one."
                " It is not there to look up either: the writer recorded after it went. Read the"
                " name before the row goes and hand it in (see NAMED_KINDS)"
            )
    return named, object
