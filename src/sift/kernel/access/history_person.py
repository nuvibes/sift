# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happened to one person, in order.

`history.py` next door asks that question of a FILE, and says in its own header that a person has a
history of the same shape (the same `Event`, the same actors, the same ordering rule), read from
the other end of `asset_people`. This is that read.

Nothing new is recorded to serve it, for the reason given there: a history assembled from a second
log beside the real writes can be missing an event that happened or hold one that did not, and a
record that is only usually right is worse than none.

## What is different from a file's history, and why

**It counts rather than lists.** A file is named in by a handful of people; a person is named on
thousands of files, and a thread with four thousand identical lines on it is not a history anybody
can read. So every statement here GROUPS (by what decided it and by the day it was decided), and
one event says "named on 12 files" rather than twelve events saying nothing each. That is also what
keeps the read cheap: what crosses out of SQLite is one row per day, never one row per file.

**Every statement here is a seek.** EXPLAIN QUERY PLAN against an initialized schema:

    the person, and what they are called   SEARCH people USING INDEX sqlite_autoindex_people_1 named
    on files                         SEARCH link USING INDEX ix_asset_people_person a stash-box link
    SEARCH l USING INDEX (person_id=?), then the box faces agreed to be them                SEARCH c
    USING COVERING INDEX ix_face_confirmations_person faces refused as them                  SEARCH
    r USING COVERING INDEX ix_face_rejections_person

The two face indexes (v22 of the faces schema) carry `created_at` beside `person_id` rather than
the person alone: a statement here reads the person, the day and the newest moment and nothing else,
so the whole answer comes out of the index and the table is never opened. A one-column index would
seek the rows and then fetch each of them for its moment.

**Aliases are not here, and the table is why.** `people_aliases` records an id, a person and a word
and no moment at all, so "an alias was added" has no time to be drawn at. An event with a made-up
time on a screen somebody is reading to find out what really happened is the one thing a history
may not do, so the row is left out until the column exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

# The wording, and the addresses the numbers in it go to. One table for all three histories (see
# that module's header for why the words moved out of the reads that say them).
from sift.kernel.access import sentences as say
from sift.kernel.access.history import (
    DEFAULT_LIMIT,
    MADE_BY_BOX,
    MAX_LIMIT,
    Actor,
    Event,
    FaceAnswer,
    _decision_events,
    _names_of,
    _via_of_source,
    _Who,
    actor_of_source,
    by_of,
    files_of_decisions,
    files_of_filing,
    kept_events,
    ledger_events,
    maker_of,
    one_line_per_face_answer,
    one_line_per_kept,
    ordered,
    recognized_lines,
    runs_not_drawn,
    stash_box_tables_in,
)

# `_via_of_source` is the file history's own, reached past the underscore deliberately: which of the
# three ways an attribution arrived is a fact about `source`, which is one column meaning one thing
# in three tables. A second copy here would be a second mapping to keep in step with the `enriched:`
# filter, and the symptom of it drifting would be a mark, which nothing fails on.
#
# `_decision_events`, `_Who` and `_names_of` are reached for the same way and for a stronger version
# of the same reason. A bulk judgement is ONE act, and a person's thread and a file's thread are two
# views of it: the same title, the same Undo, the same reversal line underneath, the same rule
# about which user may be named. Written again here they would be two accounts of one thing, and
# the way that fails is silent: a person's page would go on offering an Undo the workbench had
# already learnt to refuse, or stop naming a user the file's page still names.
from sift.kernel.access.history_boxes import linked_line, unshown_said
from sift.kernel.access.history_entity import NAMES_A_FILE
from sift.kernel.access.history_events import NOTHING_HIDDEN, verdict_of
from sift.kernel.access.history_folds import (
    NO_RECEIPT,
    RECEIPT_OF_A_NAMING,
    counted_apart_from_receipts,
    drawn_receipts,
)
from sift.kernel.access.history_sources import _folders_seen
from sift.kernel.access.repository import Repository
from sift.kernel.access.sentences import (
    FACES_CONFIRMED,
    SIFT,
    VANTAGE_PERSON,
    Line,
    counted_faces,
    faces_of_person,
    files,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.access.visibility import FILE_SEEN_BY_VIEWER, seen_by
from sift.kernel.access.worded import decided_said, lines_under
from sift.kernel.db import Database, Row
from sift.kernel.records import Subject
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import FACE_SAID_NO, FACE_SAID_YES
from sift.kernel.where import folder_said

if TYPE_CHECKING:  # pragma: no cover (the registry type, for the annotation only)
    from sift.kernel.workbench import Workbench

#: The tables a FEATURE owns that this read touches, and which may therefore not be there.
#:
#: Same guard and same reason as the file's history: a process that never imported the faces slice
#: has never registered its schema, so `face_confirmations` is genuinely absent and a statement
#: naming it is a hard error rather than an empty answer.
_FEATURE_TABLES = (
    "face_confirmations",
    # Which folders were answered as this person, and what Sift has learnt to recognize them by.
    "folder_people",
    "face_references",
    "stash_box_kept",
    "face_rejections",
    "person_stash_box_links",
    "stash_boxes",
    "workbench_decisions",
    "workbench_decision_subjects",
)

_TABLES = "SELECT name FROM sqlite_master WHERE type = 'table'"

#: The maker columns ride along with the name and the moment: the arrival event needs all three,
#: and a second read for them would be a second point read on every person's page.
_PERSON = (
    "SELECT name, created_at, created_by_kind, created_by_via, created_by_user_id,"
    " created_by_box_id"
    " FROM people WHERE id = ?"
)

#: A day-group is the machine's local day (`kernel/when.py`, `LOCAL_DAY_SQL`), worked out in SQL
#: because the grouping is. The face tables keep MILLISECONDS (see `history_reads.py`, which is where
#: that difference is explained), so their moment is divided down to seconds first.

#: Every file this person was put on, gathered by WHAT decided it and by the DAY it was decided.
#:
#: The day is null for a row written before `decided_at` existed, so those rows fall
#: into a group of their own and come back with no time, which is exactly what they know. See
#: `Event.at`.
#:
#: And by WHICH BOX, where the filing or the file's one applied match can say it: `{box}` is
#: `history.BOX_OF_A_ROW`, or NULL on a database without the stash-box tables.
#:
#: ONLY THE FILES THIS VIEWER MAY BE SHOWN. Counting every row would tell a guest "Named on 4,000
#: files" about somebody they were shown twelve of, and an admin with the vault shut how much of
#: her is in it: the size of the set being kept back, written as a number. The number is not
#: stored anywhere: it is counted here, as it is read, through the stored
#: verdict (`visibility.FILE_SEEN_BY_VIEWER`), so it is the number the People card says and the
#: number of tiles the link opens. A group with nothing this viewer may see does not come back.
# A NAMING THAT IS A DECISION'S OWN ACT. A decision taken on a queue (a folder answered as
# somebody, "Ada Lumen, 20 files") writes its receipt and the namings it made in one transaction,
# so this page would draw the decision's card AND "20 files were named as them", one act twice. The
# card names the files under "Show each" and carries the Undo, so it is the survivor. Which naming
# is which receipt's is the one rule every thread folds by (`history_folds.RECEIPT_OF_A_NAMING`,
# the file tab's rule): the receipt on that file whose object is this person, by id. Each group
# carries it, and `counted_apart_from_receipts` drops the ones a card on this page accounts for.
_NAMED_SQL = """
SELECT link.source AS source,
       CASE WHEN link.source = 'stash_box' THEN (
         CASE WHEN link.box_id IS NOT NULL THEN (
           SELECT b.name FROM stash_boxes b WHERE b.id = link.box_id
         ) ELSE (
           SELECT CASE WHEN COUNT(*) = 1 THEN MIN(b.name) END
             FROM asset_stash_box_matches m
             JOIN stash_boxes b ON b.id = m.box_id
            WHERE m.asset_id = link.asset_id AND m.state = 'applied'
         ) END
       ) END AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM asset_people link
 WHERE link.person_id = :person AND {{SEEN}}
 GROUP BY source, box, day, receipt
"""
_NAMED = splice(_NAMED_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=RECEIPT_OF_A_NAMING)
_NAMED_NO_LEDGER = splice(_NAMED_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=NO_RECEIPT)

#: The same, on a database without the stash-box tables (a process that never imported that
#: slice): NULL where the box would be. Two fixed statements rather than one with a switch in it,
#: which is the rule query text lives under here; the verdict is spliced into both.
_NAMED_NO_BOX_SQL = """
SELECT link.source AS source,
       NULL AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM asset_people link
 WHERE link.person_id = :person AND {{SEEN}}
 GROUP BY source, box, day, receipt
"""
_NAMED_NO_BOX = splice(_NAMED_NO_BOX_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=RECEIPT_OF_A_NAMING)
_NAMED_NO_BOX_NO_LEDGER = splice(_NAMED_NO_BOX_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=NO_RECEIPT)

#: Faces agreed to be them, on files this viewer may be shown: a face is a piece of its file, so a
#: count of faces on files kept from somebody is a count of those files. Same rule, same fragment.
_CONFIRMED = splice(
    """
SELECT unixepoch(link.created_at / 1000, 'unixepoch', 'localtime') / 86400 AS day,
       COUNT(*) AS faces,
       MAX(link.created_at) / 1000 AS at,
       GROUP_CONCAT(link.asset_id) AS assets
  FROM face_confirmations link
 WHERE link.person_id = :person AND {{SEEN}}
 GROUP BY day
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

#: Faces refused as them. A refusal is kept against the face, so the file is the face's own.
_REJECTED = splice(
    """
SELECT unixepoch(r.created_at / 1000, 'unixepoch', 'localtime') / 86400 AS day,
       COUNT(*) AS faces,
       MAX(r.created_at) / 1000 AS at,
       GROUP_CONCAT(link.asset_id) AS assets
  FROM face_rejections r
  JOIN face_tracks link ON link.id = r.track_id
 WHERE r.person_id = :person AND {{SEEN}}
 GROUP BY day
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

#: A stash-box that knows this person, and what its last ask filled in.
#:
#: `enrichment_runs.applied` carries the half the link row cannot: which of this person's fields
#: were empty and got written, as against what the box merely OFFERED, which is what the link's own
#: payload holds. Version 52 of the catalog; see `_ADD_ENRICHMENT_APPLIED` for why they are not the
#: same fact and why a sentence is not built from the offer.
#:
#: A SUBQUERY and not a join: every ask is kept (version 51), so a person enriched against one
#: box three times would join three rows and the thread would draw the same line three times. What
#: is wanted is the LAST of them, which is one row off `ix_enrichment_runs_subject`: the index
#: carries `at` behind the three, so this reads one entry and never opens the table.
#:
#: It reads `automatic`, through the same `_by_hand` the site and tag threads read, so the three
#: threads answer "did I do this" the same way about the identical act.
_LINKED = """
SELECT b.name AS box, l.box_id AS box_id, l.fetched_at AS at,
       (SELECT r.automatic FROM enrichment_runs r
         WHERE r.subject = 'person' AND r.local_id = l.person_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS automatic,
       (SELECT r.at FROM enrichment_runs r
         WHERE r.subject = 'person' AND r.local_id = l.person_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS run_at,
       (SELECT r.applied FROM enrichment_runs r
         WHERE r.subject = 'person' AND r.local_id = l.person_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS applied
  FROM person_stash_box_links l
  JOIN stash_boxes b ON b.id = l.box_id
 WHERE l.person_id = ?
"""

#: EVERY BULK JUDGEMENT THAT NAMED THIS PERSON, newest last, exactly as a file's history reads them.
#:
#: THE LINE THIS WAS BUILT FOR is the one a re-match writes. A pass that compares every unattached
#: appearance against everybody's references attaches faces without anybody being asked, and it
#: writes one receipt per person saying so ("Sift matched 300 more faces to <name>, between 78%
#: and 96% sure"), with every file it touched and the person themselves named as its subjects. That
#: receipt belongs in each of those files' histories and on the page of the person it is ABOUT,
#: which is the one page somebody stands on when they wonder why a name arrived on three hundred
#: files overnight.
#:
#: NOT FILTERED TO THAT ONE QUEUE, and that is deliberate rather than a wider net cast by accident.
#: The queue names belong to the slices that register them; a kernel read naming one of them in its
#: text would be the kernel holding a copy of a slice's vocabulary, and the day the word is changed
#: on one side this statement silently answers with nothing. What this asks is the question the
#: subject table exists to answer (which decisions were about this person), and every queue that
#: names a person as a subject is one whose decision belongs on their thread for the same reason.
#:
#: The SEARCH is on `ix_workbench_subject (kind, subject_id)`, the same index the file's own read
#: uses; without it this is a walk of every link row in the library to draw one person's pane.
#:
#: The kind is written into the statement rather than bound, for the reason the file's is: this read
#: is about a PERSON and nothing else, and a bound kind would make it a general link lookup that
#: happens to be called with `person`.
#: AND IT MUST NOT DRAW AN EVENT. The event ledger is this very table, so every act a writer records
#: lands here, and read straight out, an event would draw as a bulk judgement with an Undo on it:
#: a person's own arrival a second time on their thread, settled at a workbench nobody went to,
#: offering a button the workbench refuses because an event under `LEDGER_QUEUE` belongs to no queue
#: and has no reverser. The word is reserved so that no queue may claim it: the exclusion is exact. An
#: event that carries a RECEIPT is written under that receipt's own queue and still draws, which is
#: right: it is a decision and it can be taken back.
#:
#: AND ONLY A DECISION WHOSE EVERY FILE THIS VIEWER MAY BE SHOWN. A receipt's
#: title is written when the decision is taken ("Sift matched 300 more faces to them"), so its
#: number is STORED and cannot be scoped when it is read. The honest answers were two: count again
#: at read time and word the line fresh, or show the stored line only where its number is true for
#: the viewer. The second is the ledger's own rule (`history_events.NOTHING_HIDDEN`, spliced here
#: rather than written again), and it is the one that needs no new wording: a receipt naming any
#: file this viewer may not see is not drawn, so no stored number counts one.
_DECIDED = splice(
    """
SELECT d.id AS id, d.title AS title, d.queue AS queue, d.user_id AS user_id,
       d.actor_kind AS actor_kind, d.actor_id AS actor_id,
       d.decided_at AS decided_at, d.reversed_at AS reversed_at, d.verb AS verb,
       d.object_kind AS object_kind, d.object_id AS object_id, d.payload AS payload,
       d.detail AS detail
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'person' AND s.subject_id = :subject AND d.queue <> :ledger
{{NAMES_A_FILE}}
{{NOTHING_HIDDEN}}
 ORDER BY d.decided_at ASC, d.id ASC
""",
    NAMES_A_FILE=NAMES_A_FILE,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)


#: WHICH FOLDERS WERE ANSWERED AS THIS PERSON, so the folder-read line can say which.
#:
#: Not "Sift read a folder name and named them on 4000 files" with no folder named. The folder read
#: keeps a standing rule per folder, which is the whole reason `folder_people` exists, and this
#: reads it from the person's end.
#:
#: `ix_folder_people_person` is the seek. The path rather than the name, because a path is what
#: `in:` resolves and therefore what the link on the words has to carry.
_FOLDERS_NAMED = """
SELECT f.id AS id, f.root_id AS root_id, f.rel_path AS path, f.name AS name
  FROM folder_people fp
  JOIN folders f ON f.id = fp.folder_id
 WHERE fp.person_id = ?
 ORDER BY f.rel_path
"""

#: What Sift has learnt to recognize this person BY, gathered by the day it learnt.
#:
#: `UNIQUE(person_id, crop_digest)` is what this seeks on. Grouped by day like everything else here:
#: somebody who confirms a pile of forty adds forty of these in one press.
#: IN MILLISECONDS, like the two face tables above (`faces.store.now_ms` stamps all three): read as
#: seconds, a person's "Sift learnt them from 12 faces" would be dated in the year 58647. The
#: statements are written out rather than built from one template because a formatted string
#: reaching `fetch_all` is what the SQL-injection rule refuses; `test_history_person` holds all
#: three to the unit with a real millisecond stamp instead.
#:
#: A picture added by hand or from a pack is no file in the library (`asset_id` is NULL), so it
#: counts for everybody; one cut from a file counts where that file may be shown.
_TAUGHT = splice(
    """
SELECT unixepoch(link.created_at / 1000, 'unixepoch', 'localtime') / 86400 AS day,
       COUNT(*) AS faces,
       MAX(link.created_at) / 1000 AS at
  FROM face_references link
 WHERE link.person_id = :person AND link.origin != 'seed' AND (link.asset_id IS NULL OR {{SEEN}})
 GROUP BY day
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

#: The files somebody said this person is NOT in. The opposite of a naming, and the only record that
#: a pass was told to stop proposing them somewhere.
#:
#: `ix_asset_person_refusals_person` is the seek.
_RULED_OUT = splice(
    """
SELECT unixepoch(link.refused_at, 'unixepoch', 'localtime') / 86400 AS day,
       COUNT(*) AS files,
       MAX(link.refused_at) AS at
  FROM asset_person_refusals link
 WHERE link.person_id = :person AND {{SEEN}}
 GROUP BY day
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)


async def history_of_person(
    database: Database,
    viewer: Viewer,
    person_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
    access: Repository | None = None,
) -> list[Event]:
    """Everything that happened to one person, oldest first, the newest `limit` kept.

    `access` answers which folders this reader may see, so the folder a name was read from is said
    only to somebody who may be told it; without it no folder is named. Unscoped about the person:
    the route has already resolved them through the scoped lookup. `final_queues` is passed in for
    the reason the file's history takes it (`history.history_of_asset`).
    """
    # The ledger's own word, imported here: `kernel.workbench` imports `kernel.access`, so naming it
    # at the top is a cycle whose failure depends on which module a process imports first.
    from sift.kernel.vocabulary import LEDGER_QUEUE

    kept = max(1, min(limit, MAX_LIMIT))
    present = {str(row["name"]) for row in await database.fetch_all(_TABLES)}
    here = {name for name in _FEATURE_TABLES if name in present}

    person = await database.fetch_one(_PERSON, (person_id,))
    if person is None:
        return []

    # Both tables: the statement names both, and a query naming a missing table is a hard error.
    decided = (
        list(
            await database.fetch_all(
                _DECIDED, {**verdict_of(viewer), "subject": person_id, "ledger": LEDGER_QUEUE}
            )
        )
        if {"workbench_decisions", "workbench_decision_subjects"} <= here
        else []
    )
    # Before the actors are resolved, so a decision's user is in the single lookup of names.
    who = _Who(
        viewer=viewer,
        names=await _names_of(
            database, [str(row["user_id"]) for row in decided if row["user_id"] is not None]
        ),
    )
    events = [await _person_added(database, viewer, person_id, person, here)]
    cards = await _person_decisions(
        database, viewer, person_id, decided, who, final_queues=final_queues, bench=bench
    )
    events.extend(cards)
    seen = {**seen_by(viewer), "person": person_id}
    events.extend(
        await _person_named_events(database, viewer, access, person_id, present, seen, cards)
    )
    events.extend(await _person_face_answers(database, person_id, here, seen))
    # Held until the ledger is read: a box it draws press by press loses its latest-run line.
    linked: list[Event] = []
    if {"person_stash_box_links", "stash_boxes"} <= here:
        for row in await database.fetch_all(_LINKED, (person_id,)):
            # "their": the one word this sentence differs by between a person's thread and an
            # entity's (see `history_boxes.thing_linked`).
            linked.append(
                await linked_line(database, Subject.PERSON, person_id, row, "their", viewer)
            )
    events.extend(await _person_runs(database, person_id, here, seen))
    ledger = await _person_ledger(
        database, viewer, person_id, person, kept=kept, box_said=bool(linked)
    )
    events.extend(runs_not_drawn(linked, ledger))
    events.extend(ledger)
    # A stash-box answer kept and the receipt of that press are one line (`one_line_per_kept`).
    events = one_line_per_kept(events)
    # A Yes or a No and its receipt are one line; a day's line the receipts only partly account
    # for is said again for the rest (`one_line_per_face_answer`).
    events = one_line_per_face_answer(events, reword=_faces_left)

    return await unshown_said(database, access, viewer, ordered(events)[-kept:])


async def _person_added(
    database: Database, viewer: Viewer, person_id: str, person: Row, here: set[str]
) -> Event:
    """The line the person arrived with: who made them, where the row records it."""
    # The catalog records which of the three makers made the row; SOMEBODY is the answer for a row
    # older than that record, and it means "the row does not say".
    box = person["created_by_box_id"]
    box_name = None
    if box is not None and "stash_boxes" in here:
        found = await database.fetch_one(MADE_BY_BOX, (str(box),))
        box_name = None if found is None else str(found["name"])
    made_by, made_by_name = maker_of(person, viewer, box_name)
    return Event(
        at=int(person["created_at"]),
        actor=made_by,
        actor_name=made_by_name,
        kind="added",
        # The subject of its own history, linked all the same: a sentence that names somebody
        # carries the way to them wherever it is read. Which task added them is said at the back.
        pieces=say.person_added(
            by_of(made_by, made_by_name),
            say.thing("person", person_id, str(person["name"])),
            person["created_by_via"],
        ),
    )


async def _person_decisions(
    database: Database,
    viewer: Viewer,
    person_id: str,
    decided: list[Row],
    who: _Who,
    *,
    final_queues: Sequence[str],
    bench: Workbench | None,
) -> list[Event]:
    """Every decision naming the person, worded the one way the feed words it."""
    # "here" in a saved title is the FILE, and this page is not it (see `files_of_decisions`). A
    # face match is said as the one face-match sentence with this page's word (`recognized_lines`);
    # every other decision is worded as the feed words it, this person said as "them".
    decided_files = await files_of_decisions(database, decided)
    said = await decided_said(
        database, bench, viewer, decided, here=("person", person_id, say.HERE[say.VANTAGE_PERSON])
    )
    worded = {key: line.pieces for key, line in said.items()}
    recognized = await recognized_lines(
        database, decided, decided_files, page=("person", person_id)
    )
    cards = _decision_events(
        decided,
        who,
        final=frozenset(final_queues),
        files=decided_files,
        lines={**worded, **recognized},
        under=lines_under(said),
    )
    return cards


async def _person_named_events(
    database: Database,
    viewer: Viewer,
    access: Repository | None,
    person_id: str,
    present: set[str],
    seen: dict[str, object],
    cards: list[Event],
) -> list[Event]:
    """A line per source that named the person on files, each count a link to those files."""
    from sift.kernel.vocabulary import LEDGER_QUEUE

    events: list[Event] = []
    # WHICH FOLDER, where exactly one was answered as them: one phrase can link to one place, so
    # with several the line says "from their folders". A library's own top folder is said by its
    # name (`rel_path or name`); a folder that is gone takes its `folder_people` rows with it.
    answered = (
        await database.fetch_all(_FOLDERS_NAMED, (person_id,))
        if {"folder_people", "folders"} <= present
        else []
    )
    folders = [str(row["path"]) or str(row["name"]) for row in answered]
    only_folder = await _the_one_folder(access, viewer, answered)
    # Which of the four: the box's name where its tables are here, and each naming's receipt where
    # the record is (`RECEIPT_OF_A_NAMING`), so a card on this page absorbs its own namings.
    receipts_here = {"workbench_decisions", "workbench_decision_subjects"} <= present
    if stash_box_tables_in(present):
        naming = _NAMED if receipts_here else _NAMED_NO_LEDGER
    else:
        naming = _NAMED_NO_BOX if receipts_here else _NAMED_NO_BOX_NO_LEDGER
    for group in counted_apart_from_receipts(
        await database.fetch_all(naming, {**seen, "ledger": LEDGER_QUEUE}),
        drawn_receipts(cards),
    ):
        source = None if group["source"] is None else str(group["source"])
        folder = only_folder if source == "folder" else None
        count = int(group["files"])
        box = None if group["box"] is None else str(group["box"])
        actor, actor_name = actor_of_source(source, box)
        # THE NUMBER IS THE WAY TO WHAT IT COUNTS: a link to exactly those files, placed where it
        # sits in the line, as is the folder.
        counted = say.thing(
            "files", person_id, files(count), href=files_of_filing("named", person_id, group)
        )
        line: Line = say.named_on(
            say.by_word(actor.value, actor_name),
            source,
            counted,
            None if folder is None else say.folder_named(*folder),
            len(folders),
        )
        events.append(
            Event(
                at=None if group["at"] is None else int(group["at"]),
                actor=actor,
                actor_name=actor_name,
                kind="named",
                pieces=line,
                via=_via_of_source(source),
            )
        )
    return events


async def _person_face_answers(
    database: Database, person_id: str, here: set[str], seen: dict[str, object]
) -> list[Event]:
    """Each day's Yes and No on the person's faces."""
    events: list[Event] = []
    if "face_confirmations" in here:
        for row in await database.fetch_all(_CONFIRMED, seen):
            events.append(
                Event(
                    # Never None: `created_at` is NOT NULL on both face tables.
                    at=int(row["at"]),
                    actor=Actor.SOMEBODY,
                    actor_name=None,
                    kind="confirmed",
                    # The way to exactly the faces this line counted, through the faces screen's
                    # own `?show=`, so a line here and that screen's tabs cannot disagree.
                    pieces=_faces_agreed(person_id, int(row["faces"])),
                    faces=_faces_of(person_id, row["assets"], FACE_SAID_YES),
                )
            )
    if "face_rejections" in here:
        for row in await database.fetch_all(_REJECTED, seen):
            events.append(
                Event(
                    at=int(row["at"]),
                    actor=Actor.SOMEBODY,
                    actor_name=None,
                    kind="rejected",
                    # NO LINK: a refusal takes the name off the face, so there is no wall of faces
                    # refused as them to go to.
                    pieces=say.faces_refused(int(row["faces"])),
                    faces=_faces_of(person_id, row["assets"], FACE_SAID_NO),
                )
            )
    return events


async def _person_runs(
    database: Database, person_id: str, here: set[str], seen: dict[str, object]
) -> list[Event]:
    """What a stash-box kept, the faces Sift was taught from, and the files ruled out."""
    events: list[Event] = []
    if {"stash_box_kept", "stash_boxes"} <= here:
        events.extend(await kept_events(database, "person", person_id))
    if "face_references" in here:
        for row in await database.fetch_all(_TAUGHT, seen):
            events.append(
                Event(
                    at=int(row["at"]),
                    actor=Actor.SIFT,
                    actor_name=SIFT,
                    kind="taught",
                    pieces=say.taught_by(int(row["faces"])),
                    via="faces",
                )
            )
    for row in await database.fetch_all(_RULED_OUT, seen):
        events.append(
            Event(
                at=int(row["at"]),
                actor=Actor.SOMEBODY,
                actor_name=None,
                kind="ruled_out",
                pieces=say.ruled_out_of(int(row["files"])),
            )
        )
    return events


async def _person_ledger(
    database: Database, viewer: Viewer, person_id: str, person: Row, *, kept: int, box_said: bool
) -> list[Event]:
    """The ledger's acts on the person: a rename, an edit, a share taken back."""
    return await ledger_events(
        database,
        viewer,
        here=VANTAGE_PERSON,
        kind="person",
        subject_id=person_id,
        # NO GRANTS WORD: a person's page has no grants source of its own, so a share made on
        # somebody is drawn HERE or nowhere, and the event is the only thing that says it was
        # taken back.
        grants_as=None,
        limit=kept,
        box_said=box_said,
        # So a line about somebody merged into them says whose act it was. See `_taken_as`.
        name_now=str(person["name"]),
    )


async def _the_one_folder(
    access: Repository | None, viewer: Viewer, answered: Sequence[Row]
) -> tuple[str, str] | None:
    """The one folder answered as this person, where there is one and this reader may be told it.

    A folder inside one the reader may not see (hidden from them, or never shared with them) is a
    place nothing else tells them (`kernel.where`), and a person can be shown to somebody who may
    see one of their files and none of their folders. So the line keeps its words without the
    folder, exactly as a file's own history does (`naming_folders`). A library's own top folder is
    said by its name, as it is there. With nothing to ask what the reader may see, none is named.
    The answer is the folder's id, which its link goes by, and the words it is said in.
    """
    if len(answered) != 1:
        return None
    folder_id = str(answered[0]["id"])
    path, name = str(answered[0]["path"]), str(answered[0]["name"])
    if access is None:
        return None
    seen = await _folders_seen(access, viewer)
    if folder_said(path, seen=seen.get(str(answered[0]["root_id"]), frozenset())) != path:
        return None
    return folder_id, path or name


def _faces_agreed(person_id: str, faces: int) -> Line:
    """The confirmed line's words for a count: the way to exactly the faces it counts."""
    return say.faces_agreed(
        say.thing(
            "faces",
            person_id,
            counted_faces(faces),
            href=faces_of_person(person_id, FACES_CONFIRMED),
        ),
        faces,
    )


def _faces_of(person_id: str, assets: object, how: str) -> tuple[FaceAnswer, ...]:
    """The faces a day's line counts, one `FaceAnswer` per face, off the files the SQL listed."""
    listed = str(assets).split(",") if assets else []
    return tuple((person_id, asset_id, how) for asset_id in listed if asset_id)


def _faces_left(event: Event, left: int) -> Event:
    """A day's face line said again for the faces no receipt on the page accounts for."""
    person_id = event.faces[0][0]
    pieces = (
        _faces_agreed(person_id, left) if event.kind == "confirmed" else say.faces_refused(left)
    )
    return replace(event, pieces=pieces)


async def history_count_of_person(
    database: Database, viewer: Viewer, person_id: str, *, limit: int = DEFAULT_LIMIT
) -> int:
    """How many lines one person's thread has, for the number beside the word History.

    ## Why this is the history itself and not a COUNT statement

    Because a count beside a wall must come from the very read the wall draws. That is the rule the
    related-counts route is written around in as many words: a number from one statement beside a
    list from another is two populations on one screen, and the day either moves they disagree with
    nothing on screen to say which is lying. Here it would be worse than usual: a history is not a
    table, it is four statements GROUPED by day and by what decided them, capped, and sorted. There
    is no SQL that counts what that comes to; anything simpler would count rows and the thread
    counts DAYS.

    So it asks the same question and measures the answer, at the same cap the pane opens with, and
    the two cannot differ by construction. What it costs is one more pass of the reads the module
    header measures (every one of them a seek, one row per day out of SQLite), paid once when an
    entity page opens rather than when the tab is pressed.

    UNSCOPED for the reason `history_of_person` above is: the caller resolves the person first. Its
    viewer is passed straight through, so a rule written there is the rule here.
    """
    return len(await history_of_person(database, viewer, person_id, limit=limit))
