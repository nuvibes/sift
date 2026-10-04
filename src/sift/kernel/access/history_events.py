# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the ledger, with the vault's answer applied to it.

`kernel/ledger.py` writes the events; this reads them back. The split is the same one every write
door in Sift makes (a permission rule written next to a write is a rule nobody reading the write
would think to look for), and here it carries a second argument as well, because what is read back
is not what was written.

## The rule, and why it cannot be the one History uses today

`history_of_asset` is UNSCOPED about its subject and says so: the route resolved the file through a
scoped read before calling it, so a viewer who may not see the file never reaches it. That is sound
for a pane hung off one file's screen and it does not survive contact with a ledger. An event names
things the route never resolved (a person, a folder, seven other files), and the whole-install
feed resolves nothing at all before it reads. "The route resolved the subject first" is not a
permission model that can be borrowed by a read whose subject is the library.

So every read here filters through the STORED VERDICT, exactly as every wall does: `viewer_assets`
holds one row per user and file the user may see, with that file's concealment beside it, and
it is kept true by triggers inside the writer's own transaction. An event is shown when no file it
names is one this viewer may not see. One rule, written once below, spliced into all three reads
(see `visibility.splice`, which exists because a rule with three copies has three chances to drift).

## The one place it is looser than a wall, and the reason

**A file that has been DELETED has no verdict row, for anybody.** Read strictly, that would hide
every event about every file that has ever been removed, which is precisely the year's worth of
"what I got rid of" the ledger was built to be able to answer, erased by the permission check
instead of by a cascade. So a subject that no longer exists is cleared for an ADMIN and for nobody
else: the user who can already see the whole library learns nothing new from it, and a guest is
told nothing about a file that is gone. The alternative (clearing it for everyone) would let a
guest learn that a file they were never shown had existed.

## What it does not do

It writes no sentence. An event carries a verb, an object and the names those things had at the
time; turning that into "Ilva Brennan was added to the library" is one table in one module on
the reading side, and these three functions are what it reads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from sift.kernel.access.history_names import names_now as names_now
from sift.kernel.access.history_names import objects_named
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row, in_clause
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import LEDGER_QUEUE, SubjectKind

#: How many events one read hands back unless the caller says otherwise.
#:
#: The same number `history.DEFAULT_LIMIT` uses, and for the same reason: a pane is a column of
#: lines somebody reads down, and a thousand of them is not a longer answer but an unusable one.
DEFAULT_LIMIT = 200

#: The most any read will hand back, whatever is asked for. A cap rather than a suggestion: this is
#: the one read in the application whose table grows with every press anybody makes.
MAX_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class Thing:
    """A row an event names, as the kind of thing it is, its id, and what it was CALLED then.

    The name is a snapshot and not a lookup, which is the whole point of it: an event outlives its
    subject, so resolving the id when the row is read would answer "nothing" for exactly the events
    somebody opened the record to find. None means the name was not written down, which is what an
    older row has.
    """

    kind: str
    id: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """One row of the ledger, as it was stored. No sentence, no resolution, no opinion.

    Deliberately close to the row. What a reader does with it (the sentence, the link, the fold)
    belongs to the reader, in one place; a shape that had already decided half of that here would be
    a second vocabulary for the three history screens to keep in step with.
    """

    id: str
    at: int
    verb: str | None
    #: `sift`, `user` or `box`: the same three words `created_by_kind` stores. See
    #: `kernel/ledger.Actor`, which is what wrote it.
    actor_kind: str | None
    #: A user id, the name of a pass, or a stash-box id, depending on the kind above.
    actor_id: str | None
    #: The user, as the foreign key still holds it. NULL once that user is deleted, which is
    #: exactly why `actor_id` is stored beside it.
    user_id: str | None
    #: What the act was done to or with, where there was one.
    object: Thing | None
    #: How many things one event stands for, where it stands for a pass rather than an act. None on
    #: an ordinary event, and None is not one: a pass over a single file writes the file.
    count: int | None
    #: The queue this was taken on, or `ledger` for an event that was not a judgement at all.
    queue: str
    #: WHAT THE WRITER WROTE DOWN ABOUT ITS OWN ACT, opaque to everything but the area that wrote
    #: it, and to the one reader that needs two of its words: an `edited` event says which fields a
    #: save moved and a `renamed` one says what the thing used to be called, and neither fact is
    #: anywhere else in the database. Read on the server to build a sentence and never sent
    #: anywhere: the feed's own view names its fields one by one, so this stays behind the wire: a
    #: queue's payload is its own business.
    payload: str
    title: str
    detail: str
    #: When it was taken back, or None. An event that was reversed keeps its place in the order: a
    #: history that quietly loses its reversals reads as though nothing had ever happened.
    reversed_at: int | None
    #: What the event was about. Filled by the reads that page over many events; the two that ask
    #: about one subject already know theirs.
    subjects: tuple[Thing, ...] = ()


#: The vault's answer, written once and spliced into all three reads, and into the bulk judgements
#: a person's and a tag's, Site's, collection's or Photo Set's own thread reads
#: (`history_person._DECIDED`, `history_entity._DECIDED`): a receipt's title carries a number
#: written when it was taken, and this is the rule under which that number is true for whoever reads
#: it.
#:
#: Read as: no file this event names is one the viewer may not see. The double negative is what
#: makes it an AND over the event's whole subject list without a GROUP BY: an event about seven
#: files is shown only when all seven are clear, and the first one that is not stops it.
#:
#: `:reveal` is the vault being open for this session, which is the same parameter every wall binds
#: and means the same thing. `:admin` decides only the deleted case above. The last two terms hold
#: back an event that only NAMES something Hidden (a person, tag, collection, Site or Photo Set the
#: viewer marked Hidden, or one whose every file is concealed, which is the only way a song is)
#: while the vault is shut.
NOTHING_HIDDEN = """
   AND NOT EXISTS (
         SELECT 1
           FROM workbench_decision_subjects hidden
          WHERE hidden.decision_id = d.id
            AND hidden.kind = 'asset'
            AND NOT EXISTS (SELECT 1 FROM viewer_assets v
                             WHERE v.user_id = :viewer AND v.asset_id = hidden.subject_id
                               AND (:reveal = 1 OR v.concealed = 0))
            AND NOT (:admin = 1
                     AND NOT EXISTS (SELECT 1 FROM assets gone WHERE gone.id = hidden.subject_id)))
   AND (d.object_kind IS NULL
        OR d.object_kind <> 'asset'
        OR EXISTS (SELECT 1 FROM viewer_assets vo
                    WHERE vo.user_id = :viewer AND vo.asset_id = d.object_id
                      AND (:reveal = 1 OR vo.concealed = 0))
        OR (:admin = 1
            AND NOT EXISTS (SELECT 1 FROM assets ogone WHERE ogone.id = d.object_id)))
   AND (:reveal = 1 OR NOT EXISTS (
         SELECT 1
           FROM workbench_decision_subjects named
          WHERE named.decision_id = d.id
            AND ((named.kind = 'person' AND (
                 EXISTS (SELECT 1 FROM person_user_state hs
                          WHERE hs.person_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'person' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed)))
              OR (named.kind = 'tag' AND (
                 EXISTS (SELECT 1 FROM tag_user_state hs
                          WHERE hs.tag_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'tag' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed)))
              OR (named.kind = 'collection' AND (
                 EXISTS (SELECT 1 FROM collection_user_state hs
                          WHERE hs.collection_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'collection' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed)))
              OR (named.kind = 'site' AND (
                 EXISTS (SELECT 1 FROM site_user_state hs
                          WHERE hs.site_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'site' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed)))
              OR (named.kind = 'photo_set' AND (
                 EXISTS (SELECT 1 FROM photo_set_user_state hs
                          WHERE hs.photo_set_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'photo_set' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed)))
              OR (named.kind = 'song' AND (
                 EXISTS (SELECT 1 FROM song_user_state hs
                          WHERE hs.song_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'song' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed))))))
   AND (:reveal = 1 OR d.object_kind IS NULL
        OR NOT ((d.object_kind = 'person' AND (
                 EXISTS (SELECT 1 FROM person_user_state hs
                          WHERE hs.person_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'person' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))
              OR (d.object_kind = 'tag' AND (
                 EXISTS (SELECT 1 FROM tag_user_state hs
                          WHERE hs.tag_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'tag' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))
              OR (d.object_kind = 'collection' AND (
                 EXISTS (SELECT 1 FROM collection_user_state hs
                          WHERE hs.collection_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'collection' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))
              OR (d.object_kind = 'site' AND (
                 EXISTS (SELECT 1 FROM site_user_state hs
                          WHERE hs.site_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'site' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))
              OR (d.object_kind = 'photo_set' AND (
                 EXISTS (SELECT 1 FROM photo_set_user_state hs
                          WHERE hs.photo_set_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'photo_set' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))
              OR (d.object_kind = 'song' AND (
                 EXISTS (SELECT 1 FROM song_user_state hs
                          WHERE hs.song_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'song' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))))
"""

#: The columns every read hands back, so the three cannot come to disagree about what an event is.
_EVENT_COLUMNS = """
SELECT d.id AS id, d.decided_at AS at, d.verb AS verb,
       d.actor_kind AS actor_kind, d.actor_id AS actor_id, d.user_id AS user_id,
       d.object_kind AS object_kind, d.object_id AS object_id, d.object_name AS object_name,
       d.touched AS touched, d.queue AS queue, d.payload AS payload, d.title AS title,
       d.detail AS detail, d.reversed_at AS reversed_at
"""

# Everything that happened to one file, FROM BOTH SIDES.
#
# ## Why there are two arms and not one predicate
#
# An event has a subject list and one object, and matching the SUBJECTS only is half the record:
# "Ilva Brennan was merged into Orla Fennimore" names the person who went as its subject and the one
# who was KEPT as its object, so it would appear on the page of somebody who no longer exists and
# on no page at all for the person it actually happened to. The same shape for a cover: the
# person is the subject, the still is the object.
#
# A `UNION` of two seeks rather than one `OR`:
# one arm is sought on `ix_workbench_subject (kind, subject_id)` and the other on
# `ix_workbench_object (object_kind, object_id, decided_at DESC)`, and an `OR` across a correlated
# EXISTS and a column of the outer table gives SQLite nothing to seek: it walks every row of the
# table that grows with every press anybody makes and runs the EXISTS for each one. `UNION` also
# folds the row an event would produce twice, which is what an act naming one thing as both its
# subject and its object would do.
#
# Both halves are spliced rather than joined with `+`, and that is not a style choice: SQL built by
# a string operation is refused outright in this repository, because parameterisation is the whole
# of the injection defence and a rule that holds except where the pieces looked harmless is not a
# rule. `splice` takes module constants only, checks that every marker is filled, and fails at
# import rather than running as SQL with a marker left in it.
#
# The ORDER BY names the OUTPUT columns (`at`, `id`), because a compound select orders the result
# and not either arm: `d.decided_at` is not a name the combined query has.
_OF_ASSET = splice(
    """
{{COLUMNS}}
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id = :subject
{{NOTHING_HIDDEN}}
UNION
{{COLUMNS}}
  FROM workbench_decisions d
 WHERE d.object_kind = 'asset' AND d.object_id = :subject
{{NOTHING_HIDDEN}}
 ORDER BY at DESC, id DESC
 LIMIT :limit
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

# HOW ONE FILE ARRIVED, where the ledger says: the newest `added` Sift recorded about it, which
# names the task that brought it in (`actor_id`) and what that task wrote down about it. Only a swap
# writes one today ("by swap from device ABCD-EFGH-..."), and a file's arrival line is the one
# place that says it, because the arrival belongs to that line (`history._drawn_elsewhere`). Its own
# seek on `ix_workbench_subject` rather than a pick out of `_OF_ASSET`'s page: that page is capped
# at the newest events, and a file worked on for long enough would lose the oldest of them, which
# this is.
_ARRIVAL = splice(
    """
{{COLUMNS}}
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id = :subject
   AND d.verb = 'added' AND d.actor_kind = 'sift' AND d.queue = :ledger
{{NOTHING_HIDDEN}}
 ORDER BY d.id DESC
 LIMIT 1
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

# EVERY PRESS OF A PASS OVER ONE FILE (`kernel.presses`), newest first: who had Sift run which pass
# on it, and when. Its own seek on `ix_workbench_subject` rather than a pick out of `_OF_ASSET`'s
# page, because each pass line on the file's History is matched against every press of its pass,
# and a page capped at the newest events of every kind would lose the press an older line was.
_PRESSES_OF_ASSET = splice(
    """
{{COLUMNS}}
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id = :subject
   AND d.verb = 'pressed' AND d.actor_kind = 'user'
{{NOTHING_HIDDEN}}
 ORDER BY d.decided_at DESC, d.id DESC
 LIMIT :limit
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

# The same question about a person, a site, a tag, a collection, a Photo Set or a folder, from both
# sides (see `_OF_ASSET` above for why there are two arms).
#
# The kind is BOUND here where `history._DECIDED` writes `'asset'` into the statement, and the
# difference is deliberate rather than an inconsistency: that read is about a file and nothing else,
# and this one is the general link lookup: one statement for every entity page, because a copy of
# it per kind would be a copy per kind that can forget the vault.
_OF_ENTITY = splice(
    """
{{COLUMNS}}
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = :kind AND s.subject_id = :subject
{{NOTHING_HIDDEN}}
UNION
{{COLUMNS}}
  FROM workbench_decisions d
 WHERE d.object_kind = :kind AND d.object_id = :subject
{{NOTHING_HIDDEN}}
 ORDER BY at DESC, id DESC
 LIMIT :limit
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

#: What the feed was filtered to, where it was filtered at all.
#:
#: Both halves are written as "the parameter was not given OR it matches", so ONE statement answers
#: the filtered question and the unfiltered one. The alternative is a statement built per request,
#: which is refused outright here, and it would put the vault's rule into text assembled at run
#: time, which is the one place it must never be.
#:
#: A KIND is a subject's kind and not the event's, because that is the question somebody asks of a
#: feed: "what happened to my people". An event about several things is shown when ANY of them is
#: of that kind, which is what `EXISTS` says: an event that named a person and four files belongs
#: on both lists, and a rule that demanded every subject match would drop it from both.
#:
#: DECISIONS are the acts somebody can take back from a queue's own Undo: every row taken on a queue
#: rather than written straight to the ledger, which is exactly the record Organize's answers and
#: Sift's own filings make. A narrowing of the one feed and not a list beside it, so a decision is
#: read in one place, with the same line and the same Undo whichever way the feed is narrowed.
_FILTERED = """
   AND (:verb IS NULL OR d.verb = :verb)
   AND (:decisions = 0 OR d.queue <> :ledger)
   AND (:kind IS NULL
        OR EXISTS (SELECT 1 FROM workbench_decision_subjects narrowed
                    WHERE narrowed.decision_id = d.id AND narrowed.kind = :kind))
"""

# The whole install, newest first: the feed that lives in Settings beside Activity and Logs.
#
# It joins no subject at all, which is what makes the filter above load-bearing rather than
# decorative: there is no route that resolved anything before this ran.
_RECENT = splice(
    """
{{COLUMNS}}
  FROM workbench_decisions d
 WHERE 1 = 1
{{NOTHING_HIDDEN}}
{{FILTERED}}
 ORDER BY d.decided_at DESC, d.id DESC
 LIMIT :limit OFFSET :offset
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
    FILTERED=_FILTERED,
)

#: What a page of events was about, asked once for the whole page rather than once per row.
#:
#: Bounded by the page above it, so this is `LIMIT`-sized however large the table is.
_SUBJECTS_OF = (
    "SELECT decision_id, kind, subject_id, name FROM workbench_decision_subjects"
    " WHERE decision_id IN (?*)"
)


def verdict_of(viewer: Viewer) -> dict[str, object]:
    """The three values the vault's answer binds, from the session asking.

    One place, because a read that bound two of the three would be a read that quietly disagreed
    with the other two about what concealment means.
    """
    return {
        "viewer": viewer.id,
        "reveal": 1 if viewer.show_hidden else 0,
        "admin": 1 if viewer.is_admin else 0,
    }


def _event(row: Row) -> LedgerEvent:
    mapping = dict(row)
    object_kind = mapping["object_kind"]
    return LedgerEvent(
        id=str(mapping["id"]),
        at=int(mapping["at"]),
        verb=None if mapping["verb"] is None else str(mapping["verb"]),
        actor_kind=None if mapping["actor_kind"] is None else str(mapping["actor_kind"]),
        actor_id=None if mapping["actor_id"] is None else str(mapping["actor_id"]),
        user_id=None if mapping["user_id"] is None else str(mapping["user_id"]),
        object=None
        if object_kind is None
        else Thing(
            kind=str(object_kind),
            id=str(mapping["object_id"]),
            name=None if mapping["object_name"] is None else str(mapping["object_name"]),
        ),
        count=None if mapping["touched"] is None else int(mapping["touched"]),
        queue=str(mapping["queue"]),
        payload=str(mapping["payload"] or ""),
        title=str(mapping["title"]),
        detail=str(mapping["detail"]),
        reversed_at=None if mapping["reversed_at"] is None else int(mapping["reversed_at"]),
    )


def _kept(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


async def events_of_asset(
    database: Database, viewer: Viewer, asset_id: str, *, limit: int = DEFAULT_LIMIT
) -> list[LedgerEvent]:
    """Every event that named one file, newest first, as this user may be told about it.

    NAMED it either way round: as one of the things the act was about, or as the thing it was done
    WITH. See `_OF_ASSET`: a file chosen as somebody's cover is the object of that act.

    Scoped although the caller has already resolved the file, and that is not belt and braces: an
    event about this file can name OTHERS (a merge, a copy, a pass over a folder), and the second
    file is the one thing a history can reveal that somebody may not be entitled to know exists.
    """
    rows = await database.fetch_all(
        _OF_ASSET, {**verdict_of(viewer), "subject": asset_id, "limit": _kept(limit)}
    )
    return await objects_named(database, [_event(row) for row in rows])


async def arrival_of(database: Database, viewer: Viewer, asset_id: str) -> LedgerEvent | None:
    """How one file arrived, where Sift recorded it (`_ARRIVAL`), as this user may be told; else None.

    Scoped for the reason `events_of_asset` is, though the one event this answers names only the
    file: the rule is written once and spliced into every read of the record, never argued per read.
    """
    row = await database.fetch_one(
        _ARRIVAL, {**verdict_of(viewer), "subject": asset_id, "ledger": LEDGER_QUEUE}
    )
    return None if row is None else _event(row)


async def presses_of_asset(
    database: Database, viewer: Viewer, asset_id: str, *, limit: int = MAX_LIMIT
) -> list[LedgerEvent]:
    """Every press of a pass over one file (`_PRESSES_OF_ASSET`), newest first, as this user may be
    told about it. Scoped for the reason `arrival_of` is."""
    rows = await database.fetch_all(
        _PRESSES_OF_ASSET, {**verdict_of(viewer), "subject": asset_id, "limit": _kept(limit)}
    )
    return [_event(row) for row in rows]


async def events_of_entity(
    database: Database,
    viewer: Viewer,
    kind: SubjectKind,
    entity_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[LedgerEvent]:
    """Every event that named one person, site, tag, collection, Photo Set or folder.

    From both sides. A merge names the person who WENT as its subject and the one who was kept as
    its object, so "Ilva Brennan was merged into them" belongs on the keeper's page. See
    `_OF_ASSET`.
    """
    rows = await database.fetch_all(
        _OF_ENTITY,
        {**verdict_of(viewer), "kind": kind, "subject": entity_id, "limit": _kept(limit)},
    )
    return await objects_named(database, [_event(row) for row in rows])


async def events_recent(
    database: Database,
    viewer: Viewer,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    kind: str | None = None,
    verb: str | None = None,
    decisions: bool = False,
) -> list[LedgerEvent]:
    """What this installation has been doing, newest first, with what each event was about.

    The subjects come back on these because nothing else here knows them: a per-file pane already
    has its file and an entity pane already has its entity, and a feed over the whole install has
    only what the rows say. They are read in one statement for the whole page, so the cost is the
    page's rather than a read per row.

    `kind` filters to the events that named a thing of that kind and `verb` to one act. Both are
    None for the whole feed, and a word neither vocabulary knows simply matches nothing. Refusing
    it here would be a second copy of two lists the ledger already owns. `decisions` keeps only
    the acts a queue can take back. See `_FILTERED`.
    """
    rows = await database.fetch_all(
        _RECENT,
        {
            **verdict_of(viewer),
            "limit": _kept(limit),
            "offset": max(0, offset),
            "kind": kind,
            "verb": verb,
            "decisions": int(decisions),
            "ledger": LEDGER_QUEUE,
        },
    )
    events = await objects_named(database, [_event(row) for row in rows])
    named = await subjects_of(database, [one.id for one in events]) if events else {}
    return [replace(one, subjects=tuple(named.get(one.id, ()))) for one in events]


async def subjects_of(database: Database, event_ids: Sequence[str]) -> dict[str, list[Thing]]:
    """What each of these events was about, asked once for the whole page.

    Its own function because two readers need it and they need it for opposite reasons: the feed
    has nothing else on the screen to say what a line was about, and a history standing on one
    thing needs the OTHER things an act named: the file a delete ended, which is the one name a
    person's page cannot get from anywhere else. A second copy of this read would be a second
    place to forget that a name is a snapshot.

    It applies no permission rule and needs none: the caller has already read the events through
    one of the three statements above, and an event this user may not be told about never
    reaches here to have its subjects listed.
    """
    if not event_ids:
        return {}
    statement, values = in_clause(_SUBJECTS_OF, sorted(set(event_ids)))
    rows = [dict(row) for row in await database.fetch_all(statement, values)]
    # A SUBJECT WITH NO SNAPSHOT IS NAMED NOW, where it is still there (`names_now`), here and not
    # in each reader, so one act never reads "You added beach.gif to d" in the feed and "You added a
    # file to it" on the Collection's own page. One read per kind with a nameless subject, bounded
    # by the page; a page of rows the door already named costs none.
    nameless: dict[str, list[str]] = {}
    for mapping in rows:
        if mapping["name"] is None:
            nameless.setdefault(str(mapping["kind"]), []).append(str(mapping["subject_id"]))
    now = await names_now(database, nameless) if nameless else {}
    named: dict[str, list[Thing]] = {}
    for mapping in rows:
        kind, subject_id = str(mapping["kind"]), str(mapping["subject_id"])
        named.setdefault(str(mapping["decision_id"]), []).append(
            Thing(
                kind=kind,
                id=subject_id,
                name=(
                    str(mapping["name"])
                    if mapping["name"] is not None
                    else now.get((kind, subject_id))
                ),
            )
        )
    return named


#: Every stash-box's `enriched` events about one thing, oldest first. See `first_presses`.
#:
#: By the moment and then by id. The id alone is not enough: the ledger's backfill of the runs
#: from before the run's writer recorded an event minted its ids on the night it ran, so those
#: older presses carry NEWER ids than every press since: their `decided_at` is the run's own
#: moment. The id breaks a tie inside one second, which is where the wall clock's backward steps
#: on this kind of machine would otherwise decide.
_PRESSES_OLDEST_FIRST = """
SELECT d.object_id AS box_id, d.id AS id
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = ? AND s.subject_id = ?
   AND d.verb = 'enriched' AND d.object_kind = 'box'
 ORDER BY d.decided_at, d.id
"""


async def first_presses(database: Database, kind: str, subject_id: str) -> dict[str, str]:
    """Each box's first `enriched` event about this thing: box id -> event id.

    What tells the press that LINKED a box from every later re-ask of it (`sentences.box_line`'s
    `again`). Read here rather than off the thread's page because the page is capped at the newest
    events, and its oldest visible press of a box is not that box's first. Every run has its event
    (the ledger's backfill wrote one for each run from before the writer did), so the first event
    is the first run.

    No permission rule, for the reason `subjects_of` needs none: it answers ids of events about a
    thing the caller already resolved for this viewer, and a box is not a thing a vault hides.
    """
    first: dict[str, str] = {}
    for row in await database.fetch_all(_PRESSES_OLDEST_FIRST, (kind, subject_id)):
        if row["box_id"] is not None:
            first.setdefault(str(row["box_id"]), str(row["id"]))
    return first


# --- the feed's fold: one line per press ----------------------------------------------------------
#
# One file-name task can write thousands of feed lines, a line per file; one floor task deleting
# Photo Sets hundreds; a volume slider dragged across a sitting two dozen. Each act
# is right to be its own row (the unit of undo is the file, and a history of one file needs its
# own line), and the feed is the one reader that wants the PRESS, so the fold is a reading of the
# rows and never a change to them.
#
# ## Which acts are one press
#
# Nothing records a press: a task writes its acts as it goes, and the record names the task but
# has no run id. So a press is read as a run: the same KEY,
# with no gap longer than `FEED_FOLD_GAP` between one act and the next of that key. The key is what
# makes two acts the same act of the same doer:
#
# - the verb, the queue, who acted and which of their tasks, and the KIND of thing it was done
#   with. A task's press folds across the things it acted on (one file-name task files under 23
#   usernames, and that is one press), which is why a TASK's key leaves the object's id out.
# - a PERSON's key keeps the object's id: somebody adding files to one Collection is one line, and
#   adding a tag and then a different tag is two acts that happen to share a verb.
# - a setting is keyed by WHICH setting, and folds over a sitting (`FEED_SITTING_GAP`) rather than
#   a minute: a slider moved twenty times is one change from where it was to where it was left.
# - a decision is keyed by its words, so only receipts saying the same thing fold: a decision's
#   payload is its area's to read, never this reader's.
# - a delete is keyed by where it took the files (the disk, or Sift only): two different acts.
# - a SONG Sift named is keyed by the song AND the thing it was named from: the Site whose page
#   said it, the file with the same music it came from, or nothing for AcoustID. A task's key
#   leaves the object out, and for this act that would be a lie: one settle names eleven files
#   after ONE file and folds to "Sift named the song X on 11 files, from the same music as
#   <file>", but
#   the next file's settle a second later is a different song from a different file, and a Site's
#   downloads in one minute are as many songs as downloads. Folded without the two, the line would
#   name one song and one file over acts that said others.
# - the usernames the workbench backfilled (`{"backfilled": true}`) fold as ONE line whoever is
#   credited, because the backfill is one press of this application's own, not of those tasks.
# - the files a SWAP imported are keyed by the session (the payload's short id): a task's key
#   leaves the object out and names no session, so two swaps landing in the same minute (two
#   devices, or one device twice) would fold into one line naming the newer device for both.
# - NEVER folded: a finished task (`ran`, each is a press and opens its own report), a merge,
#   a rename and a forget, each of which is one deliberate act about one thing; each end of a swap
#   (`swap_started`, `swap_ended`), which is one session's and says its own device and counts; each
#   act on the computer running Sift (`sentences.MACHINE_ACTS`), one deliberate press apiece; and
#   an edit that is not a setting, whose fields differ act by act.
#
# A run breaks on a gap between ADJACENT acts of the same key, not on another act landing between
# them: a download finishing while the file-name task runs is not the end of that task.
#
# ## Its cost
#
# A window over the whole filtered record, per page (`history_feed`): the walk the exact total
# beside it takes. ONE walk and one sort: every act is marked against its neighbours of the same key
# (does a press open here, does it close here), a press is the stretch from an opening to the next
# closing, and the page's lines are the newest closings. A second window over runs, or a grouping
# of every press, would each sort the whole record again to answer for fifty lines. What a folded
# line was done with and about is then a SEEK on the stretch of time its press spans
# (`ix_workbench_decided`): a press is every act of its key between its oldest and its newest.
# The day the walk stops being cheap the answer is a run id written at the door; until then there
# is nothing to write it from, and every row already in the table would still need this reading.

#: The longest gap, in seconds, between two acts of one key that are still one press. A task writes
#: as fast as it can (four thousand filings in four seconds), so any window folds them; what the
#: number has to do is stay well under the rate at which a person repeats themselves, minutes apart,
#: an order of magnitude under a sitting. A minute rather than seconds because a pass is not always
#: fast: a filing waiting on a slow disk leaves seconds between writes, and a window sized on a fast
#: run would split one pass into a dozen lines the first time the machine was busy.
FEED_FOLD_GAP: Final = 60

#: The gap for a SETTING: a sitting, ten minutes, long enough to look at a thing properly and short
#: enough that a break is not counted. A slider dragged in small steps is one change.
FEED_SITTING_GAP: Final = 600

#: The most subjects a folded line lists under its "Show each", per kind; the count says the rest.
FEED_FOLD_SHOWN: Final = 100

# Which setting an edit changed, where it was a setting's: its payload's key. NULL for any other
# edit, and for a payload that is not JSON: `json_valid` first, because `json_extract` on text
# that is not JSON is an error, and a feed that fails on one row is no feed.
_SETTING_KEY = (
    "(CASE WHEN d.verb = 'edited' AND d.object_kind IS NULL AND json_valid(d.payload)"
    " THEN json_extract(d.payload, '$.key') END)"
)
_DELETED_FROM = (
    "(CASE WHEN d.verb = 'deleted' AND json_valid(d.payload)"
    " THEN json_extract(d.payload, '$.from') END)"
)
_BACKFILLED = (
    "(CASE WHEN d.verb = 'added' AND json_valid(d.payload)"
    " THEN json_extract(d.payload, '$.backfilled') END)"
)
#: The song a `song_named` act wrote (`identity.seed_music_on`'s payload); NULL for any other.
_SONG = (
    "(CASE WHEN d.verb = 'song_named' AND json_valid(d.payload)"
    " THEN json_extract(d.payload, '$.song') END)"
)

#: The session a file that arrived BY SWAP came in (`vocabulary.VIA_SWAP`, the payload's `session`,
#: its short id); NULL for any other act.
_SWAP_SESSION = (
    "(CASE WHEN d.verb = 'added' AND d.actor_kind = 'sift' AND d.actor_id = 'swap'"
    " AND json_valid(d.payload) THEN json_extract(d.payload, '$.session') END)"
)

#: The passes a press of a pass over a file ran (`kernel.presses`), as their stored JSON list; NULL
#: for any other act. Presses of different passes are different acts, so they never fold together.
_PRESSED_PASSES = (
    "(CASE WHEN d.verb = 'pressed' AND json_valid(d.payload)"
    " THEN json_extract(d.payload, '$.passes') END)"
)

#: The key one press shares. See the section's head for each branch.
_FOLD_KEY = splice(
    """
CASE
  WHEN d.verb IN ('ran', 'merged', 'renamed', 'forgot', 'swap_started', 'swap_ended') THEN d.id
  WHEN d.verb IN ('sharing_turned_on', 'sharing_turned_off', 'start_with_windows_on',
                  'start_with_windows_off', 'firewall_opened', 'storage_moved',
                  'update_started', 'library_opened', 'restarted') THEN d.id
  WHEN {{BACKFILLED}} THEN 'added|backfilled|' || COALESCE(d.object_kind, '')
  ELSE d.verb || '|' || d.queue || '|' || COALESCE(d.actor_kind, '') || '|'
       || COALESCE(d.actor_id, '') || '|' || COALESCE(d.object_kind, '') || '|'
       || CASE
            WHEN d.verb = 'edited' THEN COALESCE({{SETTING_KEY}}, d.id)
            WHEN d.verb = 'decided' THEN d.title
            WHEN d.verb = 'song_named' THEN COALESCE(d.object_id, '') || '|' || COALESCE({{SONG}}, '')
            WHEN {{SWAP_SESSION}} IS NOT NULL THEN {{SWAP_SESSION}}
            WHEN {{PRESSED_PASSES}} IS NOT NULL THEN {{PRESSED_PASSES}}
            WHEN d.actor_kind IS NULL OR d.actor_kind = 'user' THEN COALESCE(d.object_id, '')
            ELSE ''
          END
       || '|' || COALESCE({{DELETED_FROM}}, '')
END""",
    BACKFILLED=_BACKFILLED,
    SETTING_KEY=_SETTING_KEY,
    DELETED_FROM=_DELETED_FROM,
    SONG=_SONG,
    SWAP_SESSION=_SWAP_SESSION,
    PRESSED_PASSES=_PRESSED_PASSES,
)

#: Where each kind of subject lives, and what it is addressed BY. Read by `subjects_present`.
#:
#: **Only the kinds that have a screen.** A subject with nowhere to go needs no probe: the answer
#: would be discarded, and asking would be a read of a table for a link that cannot exist. A shoot,
#: a stash-box, a grant and a setting are all in that position today, and adding one here is what
#: giving one of them a page would look like.
#:
#: A folder is addressed by its ID, like every other kind, not its PATH. `in:` takes a folder's id,
#: and it is the one form that names exactly one folder (`search/filters.py`, `_folders`): a path is
#: relative to its library folder, so two of them each holding a "2024" both answer `in:2024`, and a
#: library folder's OWN folder has the empty path, and `/browse?in=` names nothing.
#: The folder browser sends the id for that reason (`FolderExplorer.svelte`, `nameFor`), and so do
#: the client's own history links (`components/common/history.ts`). The probe still proves it is
#: there.
#:
#: The three faces tables the histories next door already read are the precedent for `pile` being
#: here: the kernel reads what it needs to explain a library, and a pile is a thing a screen shows.
_STILL_THERE: Final[Mapping[str, str]] = {
    "asset": "SELECT id AS id, id AS address FROM assets WHERE id IN (?*)",
    "person": "SELECT id AS id, id AS address FROM people WHERE id IN (?*)",
    "site": "SELECT id AS id, id AS address FROM sites WHERE id IN (?*)",
    "tag": "SELECT id AS id, id AS address FROM tags WHERE id IN (?*)",
    "collection": "SELECT id AS id, id AS address FROM collections WHERE id IN (?*)",
    "photo_set": "SELECT id AS id, id AS address FROM photo_sets WHERE id IN (?*)",
    "song": "SELECT id AS id, id AS address FROM songs WHERE id IN (?*)",
    # The table keeps its old name until the storage rename; the KIND is the username's.
    "username": "SELECT id AS id, id AS address FROM usernames WHERE id IN (?*)",
    # A SIGN-IN USER, probed in its own table, never against the usernames above. It has no page
    # (no `_ADDRESS` in the ledger's router), so what this answers is only whether it is still
    # there, which is what decides between its snapshot name and "a user who is gone".
    "login": "SELECT id AS id, id AS address FROM users WHERE id IN (?*)",
    "pile": "SELECT id AS id, id AS address FROM face_piles WHERE id IN (?*)",
    "folder": "SELECT id AS id, id AS address FROM folders WHERE id IN (?*)",
    # A ROW ON THE DOWNLOADS QUEUE, and the one probe here that reads a table a
    # FEATURE owns rather than the catalog's. That is safe by construction rather than by luck: a
    # process that never registered the download slice has no `downloads` table AND no download
    # events, so `wanted` never carries this kind and the statement is never run (see the loop
    # below, which skips a kind with no ids).
    #
    # A HIDDEN ROW IS TREATED AS GONE, deliberately. "Remove from the list" keeps the row (it is
    # what stops a re-pasted link being fetched twice), but the queue has no way to show it again,
    # so a link to it would land on a page that does not draw it. Absent here is what makes the
    # name plain words instead, which is the honest drawing for something with nowhere to go.
    "download": "SELECT id AS id, id AS address FROM downloads WHERE id IN (?*) AND hidden_at IS NULL",
}


async def subjects_present(
    database: Database, wanted: Mapping[str, Sequence[str]]
) -> dict[tuple[str, str], str]:
    """Which of these things still exist, keyed by kind and id, and what each is addressed by.

    **The one question a reader of the ledger cannot answer from the row.** An event outlives its
    subject on purpose, so a page of events names things that have since been deleted, and a link
    to a deleted person is a link that lands on "no such person", which reads as a broken screen
    rather than as a library that has moved on. Absent here means there is nowhere to go, and the
    name is drawn as the plain words it already is.

    One statement per kind PRESENT on the page, each bounded by the page, rather than a read per
    subject: fifty events naming four kinds is four probes and not two hundred. A kind with no
    screen is not asked about at all (see `_STILL_THERE`).

    It takes no viewer, and that is a statement rather than an omission: whether a row EXISTS is not
    a thing the vault has an opinion about, and the permission rule has already been applied by the
    read that produced these events. An event naming a file this user may not see never reaches
    here to be asked about.
    """
    found: dict[tuple[str, str], str] = {}
    for kind, ids in wanted.items():
        statement = _STILL_THERE.get(kind)
        if statement is None or not ids:
            continue
        asked, values = in_clause(statement, sorted(set(ids)))
        for row in await database.fetch_all(asked, values):
            mapping = dict(row)
            found[(kind, str(mapping["id"]))] = str(mapping["address"])
    return found


def can_be_found(kind: str) -> bool:
    """Whether Sift has a way of asking at all whether a thing of this kind is still there.

    Read by the feed to tell the two silences apart: a nameless subject of a kind with no probe is
    simply a kind nothing looks up, and a nameless subject of a kind that IS probed and was not
    found is a thing that has gone, which is a different sentence and the truer one.
    """
    return kind in _STILL_THERE


#: What an actor is CALLED, by the kind of actor it is. Read by `actor_names`.
#:
#: Two kinds and not three: Sift needs no lookup (it is Sift, and `actor_id` is the pass rather
#: than a row anywhere), and the other two are names that can change and can go. The name is read
#: at the moment the feed is drawn rather than snapshotted with the event, and that is the opposite
#: of the rule for a subject: a user renamed yesterday is the same user, and a feed that
#: called it by last year's name would be answering "who did this" with an answer nobody recognises.
_CALLED: Final[Mapping[str, str]] = {
    "user": "SELECT id AS id, username AS name FROM users WHERE id IN (?*)",
    "box": "SELECT id AS id, name AS name FROM stash_boxes WHERE id IN (?*)",
}


async def actor_names(
    database: Database, wanted: Mapping[str, Sequence[str]]
) -> dict[tuple[str, str], str]:
    """What each actor on a page of events is called, keyed by kind and id.

    Absent for a user who has been deleted, which is the case `Actor` was written for: the
    event still says a user did it and still says which, and the feed says so in words rather
    than printing an id nobody can read.
    """
    named: dict[tuple[str, str], str] = {}
    for kind, ids in wanted.items():
        statement = _CALLED.get(kind)
        if statement is None or not ids:
            continue
        asked, values = in_clause(statement, sorted(set(ids)))
        for row in await database.fetch_all(asked, values):
            mapping = dict(row)
            named[(kind, str(mapping["id"]))] = str(mapping["name"])
    return named
