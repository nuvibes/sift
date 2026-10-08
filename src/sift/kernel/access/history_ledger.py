# SPDX-License-Identifier: AGPL-3.0-or-later
"""The event ledger's acts as History lines, each drawn once."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from sift.kernel.access import sentences as say
from sift.kernel.access.history_actors import _actor_of_act, _names_of, _Who
from sift.kernel.access.history_boxes import enriched_by_box, named_of_event, unshown_unnamed
from sift.kernel.access.history_line import (
    DEFAULT_LIMIT,
    VIAS,
    Actor,
    Detail,
    Event,
    Link,
    by_of,
    link_of_piece,
)
from sift.kernel.access.history_reads import _text_or_none
from sift.kernel.access.history_removals import removals_named
from sift.kernel.access.sentences import LINKED_KINDS as _LINKED_KINDS_OF
from sift.kernel.access.sentences import SIFT, Line, Piece, Said
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row, point_read

# The ledger's own word for one thing an event named, apart from the record editor's `Subject`.
# At module level, unlike `LEDGER_QUEUE` inside `ledger_events`: the vocabulary imports nothing
# from Sift.
from sift.kernel.vocabulary import Subject as LedgerSubject
from sift.kernel.when import day_number

if TYPE_CHECKING:  # pragma: no cover
    from sift.kernel.access.history_events import LedgerEvent
    from sift.kernel.vocabulary import SubjectKind


#: WHO MADE EACH SHARE, as the ledger wrote it: every `shared` event about this thing, oldest first.
#:
#: `acl_grants` records who a grant is FOR and never who made it, so a share line drawn from it
#: alone would put the GUEST in the "by" position. The sharing feature writes the maker beside every
#: grant in the ledger; a grant older than that has no maker anywhere and says so.
_SHARES_MADE = """
SELECT d.object_id AS user_id, d.actor_kind AS actor_kind, d.actor_id AS actor_id,
       d.user_id AS by_user, d.decided_at AS at
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = ? AND s.subject_id = ? AND d.verb = 'shared' AND d.object_kind = 'login'
 ORDER BY d.decided_at ASC, d.id ASC
"""

#: How far a grant's own moment may sit AFTER the event that recorded it: the two are written in one
#: transaction from two reads of the clock. The same tolerance the workbench's run-matching step
#: allows for the same reason.
_SHARE_CLOCK_SLACK = 2


async def share_makers(
    database: Database, viewer: Viewer, kind: str, subject_id: str, grants: Sequence[Row]
) -> list[tuple[Actor, str | None]]:
    """Who made each of these grants, in their order: the actor and the name a history gives it.

    The maker of a grant is the LATEST `shared` event for that user on this thing at or before the
    grant's moment: a revoke and a share again make a new grant row, and pressing Share on one
    that stands records an event and makes no row. A grant with no such event predates the record
    and is SOMEBODY, with no name: the recipient is never the answer. `kind` is the ledger's word
    for the thing (`asset`, not the grants table's `item`).
    """
    unknown: tuple[Actor, str | None] = (Actor.SOMEBODY, None)
    if not grants:
        return []
    # A process that never registered the workbench has no record, and every grant is unknown.
    recorded = len(list(await database.fetch_all(_LEDGER_TABLES))) == 2
    made = list(await database.fetch_all(_SHARES_MADE, (kind, subject_id))) if recorded else []
    who = _Who(
        viewer=viewer,
        names=await _names_of(
            database, [str(one["by_user"]) for one in made if one["by_user"] is not None]
        ),
    )
    answers: list[tuple[Actor, str | None]] = []
    for grant in grants:
        at = int(grant["created_at"]) + _SHARE_CLOCK_SLACK
        found = [
            one
            for one in made
            if str(one["user_id"]) == str(grant["user_id"]) and int(one["at"]) <= at
        ]
        if not found:
            answers.append(unknown)
            continue
        last = found[-1]
        answers.append(
            _actor_of_act(
                _text_or_none(last["actor_kind"]),
                _text_or_none(last["actor_id"]),
                _text_or_none(last["by_user"]),
                who,
            )
        )
    return answers


# --- THE LEDGER'S OWN EVENTS --------------------------------------------------------------------
#
# The sources in `history_sources` read what is TRUE NOW and go silent when a row is removed; the
# ledger keeps the acts. ONE LINE PER ACT: an event is drawn only where no other source draws it,
# checked by the LINK ROW itself. While the row stands the link table draws it and the event is
# dropped; once it is gone both the making and the taking back are drawn (`_links_that_stand`).


#: THE VERBS A LINK TABLE ALREADY DRAWS. An event carrying one of these is dropped while the row it
#: describes is still there.
#:
#: `shared` is one of them and that is the same rule rather than an extra: a grant IS a link row,
#: `_GRANTS` draws it while it stands, and a revoked grant is the case the ledger was written for:
#: the row deletes itself and takes its own history line with it.
LINK_VERBS = frozenset({"linked", "filed", "named", "shared"})

#: THE PAGES THAT COUNT THEIR LINKS ("named on 12 files" a day), the only ones a link reached from
#: the object side belongs to something else on. A shelf draws its additions from the record
#: (`history_entity._SHELF_ADDITIONS`), so dropping them would show only the files taken out.
COUNTED_ON_ITS_PAGE = frozenset({"person", "tag", "site"})

#: THE ACTS WHOSE LINE NAMES WHAT THE EVENT WAS ABOUT rather than what it was done with.
#:
#: Each verb here costs a thread one more seek; a delete earns it, because the name of the file it
#: ended exists nowhere else once the file has gone.
_SAID_FROM_SUBJECTS = frozenset({"deleted"})

#: WHICH OF A FILE'S LINKS ARE STILL THERE, as the kind of thing each was made to and its id.
#:
#: One statement over the three tables that draw themselves in a file's thread. A site is two hops:
#: an asset has a username, and a username belongs to a site.
_STILL_LINKED = point_read(
    "history.still_linked",
    """
SELECT 'person' AS kind, person_id AS id FROM asset_people WHERE asset_id = :subject
 UNION ALL
SELECT 'tag' AS kind, tag_id AS id FROM asset_tags WHERE asset_id = :subject
 UNION ALL
SELECT 'site' AS kind, ac.site_id AS id
  FROM asset_usernames link
  JOIN usernames ac ON ac.id = link.username_id
 WHERE link.asset_id = :subject AND ac.site_id IS NOT NULL
""",
)

#: WHO THIS THING IS STILL SHARED WITH, by the word `acl_grants` files its kind under.
#:
#: Apart from the statement above rather than a fourth arm of it, because it answers for a person, a
#: tag, a site, a shelf and a Photo Set as well as for a file, and `_GRANTS` next door is drawn on
#: every one of those pages. One statement, one object type bound, the same rule everywhere.
_STILL_GRANTED = point_read(
    "history.still_granted",
    "SELECT subject_user_id AS id FROM acl_grants WHERE object_type = :type AND object_id = :subject",
)

_EVENT_KINDS: Mapping[str, str] = {
    "added": "added",
    "removed": "removed",
    "renamed": "renamed",
    "named": "named",
    "filed": "filed",
    "moved": "moved",
    "unlinked": "removed",
    "hidden": "hidden",
    "revealed": "revealed",
    "shared": "shared",
    "unshared": "shared",
    "kept_local": "kept_local",
    "kept_from_swaps": "kept_from_swaps",
    "allowed_in_swaps": "allowed_in_swaps",
    "allowed": "allowed",
    "merged": "merged",
    "edited": "edited",
    "enriched": "enriched",
    "asked": "asked",
    "scanned": "scanned",
    "face_run": "face_run",
    "produced": "copied_from",
    "deleted": "deleted",
    "forgot": "deleted",
    "decided": "decided",
    "downloaded": "downloaded",
    "download_failed": "download_failed",
    "saved": "saved",
    "wall_sent": "wall_sent",
    "paused": "paused",
    "resumed": "resumed",
    "cookies_saved": "cookies_saved",
    "cookies_replaced": "cookies_replaced",
    "cookies_forgotten": "cookies_forgotten",
    "canceled": "canceled",
    "ran": "ran",
    "pressed": "pressed",
    "song_named": "song_named",
    "swap_started": "swap_started",
    "swap_ended": "swap_ended",
    "restored": "restored",
    "adopted": "adopted",
    "sharing_turned_on": "sharing_turned_on",
    "sharing_turned_off": "sharing_turned_off",
    "start_with_windows_on": "start_with_windows_on",
    "start_with_windows_off": "start_with_windows_off",
    "firewall_opened": "firewall_opened",
    "storage_moved": "storage_moved",
    "update_started": "update_started",
    "library_opened": "library_opened",
    "restarted": "restarted",
    # Sift's own task over the User's figures, drawn as the finished task it is.
    "recounted": "ran",
}

#: WHAT A LINK MADE TO ONE KIND OF THING IS, in the kinds a history already had. A link to a person
#: is a naming and wears the naming's mark, which is the mark that act has worn since before the
#: ledger existed. A kind with no word of its own is an addition, which is what every link is.
_LINKED_KINDS: Mapping[str, str] = {
    "person": "named",
    "tag": "tagged",
    "site": "filed",
}


#: WHETHER THE RECORD IS EVEN HERE. Both tables, because a link with no event behind it is nothing.
#:
#: Asked by the source itself rather than handed in: a guard a caller has to remember to pass is
#: missing on the day another history is written.
_LEDGER_TABLES = (
    "SELECT name FROM sqlite_master WHERE type = 'table'"
    " AND name IN ('workbench_decisions', 'workbench_decision_subjects')"
)


#: The copy rows that name this file, from either end: it IS the copy, or it is the original.
_STILL_A_COPY = "SELECT asset_id AS id FROM produced_files WHERE asset_id = :subject"
_STILL_COPIED = "SELECT asset_id AS id FROM produced_files WHERE source_asset_id = :subject"

_ONE_TABLE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"


async def _has_table(database: Database, name: str) -> bool:
    """Whether a feature's table is registered in this database."""
    return await database.fetch_one(_ONE_TABLE, (name,)) is not None


async def _links_that_stand(
    database: Database, *, kind: str, subject_id: str, grants_as: str | None
) -> set[tuple[str, str]]:
    """Every link this thing still has, as (what kind of thing it was made to, its id).

    The one existence check the whole dedupe rule rests on. It is asked once per thread rather than
    once per event: a file with forty events has one read of its links, not forty.

    Unguarded on the tables, unlike every feature-owned read in `history_sources`: these four belong
    to the catalog and to the access layer, which every install has: they are the same tables the
    naming, tagging, filing and sharing sources read without asking either.

    A file asks about its people, its tags and its sites; everything else asks only about its
    shares. That is not a shortcut: those three tables are keyed by an ASSET, and no other kind of
    thing has a link table that a history draws from a timestamp. A shelf's and a Photo Set's event
    is always drawn: their membership rows draw only the additions the record never said
    (`history_entity._SHELF_ADDITIONS`), so the two cannot say one addition twice.
    """
    standing: set[tuple[str, str]] = set()
    if kind == "asset":
        for row in await database.fetch_all(_STILL_LINKED, {"subject": subject_id}):
            standing.add((str(row["kind"]), str(row["id"])))
        # A COPY WHOSE ROW STILL STANDS, as ('produced', the copy's id): the copy line and the made-
        # into line are drawn from `produced_files` while it stands, so its `produced` event is the
        # same act a second time. Guarded: the editing feature's
        # table is not registered in every process.
        if await _has_table(database, "produced_files"):
            for statement in (_STILL_A_COPY, _STILL_COPIED):
                for row in await database.fetch_all(statement, {"subject": subject_id}):
                    standing.add(("produced", str(row["id"])))
    if grants_as is not None:
        for row in await database.fetch_all(
            _STILL_GRANTED, {"type": grants_as, "subject": subject_id}
        ):
            # `login`: a grant is held by a user's sign-in, the word the sharing writer uses.
            standing.add(("login", str(row["id"])))
    return standing


def _drawn_elsewhere(
    event: LedgerEvent,
    *,
    viewer: Viewer,
    standing: set[tuple[str, str]],
    ledger_queue: str,
    about: tuple[str, str] | None = None,
    box_said: bool = False,
    from_object: bool = False,
) -> bool:
    """Whether some other source already says this, in which case the event is not drawn.

    Seven rules, and each of them names the source that owns the act:

    - **A receipt belongs to the decisions source.** An event written under a queue is a judgement
      somebody took, drawn with the Undo the workbench can honour, and drawing it here as well
      would put the same decision on the thread twice, the second time with no way to take it back.
      EXCEPT on the page of the FILE it was done with (`from_object`, `about` None): a file's
      decisions source reads its receipts by their subjects, so the file a song was shared from
      is named by no other reader. It is drawn here without an Undo, which stays on each file that
      received the name, and only while it stands.
    - **An arrival belongs to the arrival line.** Every one of the three readers opens with one,
      read off the row's own `created_at`, and it is the same act.
    - **A link that still stands belongs to its link table**, which draws it with the moment the
      row carries. See `LINK_VERBS`.
    - **A concealment is the user's own.** Hiding is per-user and invisible to everybody else
      by design, so an event about somebody else's Hidden is not drawn on a page they can see:
      that would be the one fact the vault exists to withhold, told by the history pane.
    - **A share is an admin's to read.** Who else signs in to this install is not something a
      file a guest may see should disclose, which is the rule `history_of_asset` states about the
      grants source; an event saying the same thing has to keep it.
    - **A LINK REACHED FROM THE OBJECT SIDE belongs to the entity's counted source.** `about` is the
      entity whose page is being drawn, and it is None for a file. The ledger's entity read matches
      an event either way round, which is what puts "X was merged into them" on the keeper's page,
      but an entity thread COUNTS its links rather than listing them ("named on 12 files", one row
      per day) precisely because a person is on thousands of files. Drawn from both sides, one
      re-match could put hundreds of lines on one page and push the rest of the
      thread off the end of the cap. So the link verbs stay the counted source's, and the acts that
      have no counted source (a merge, a cover, an edit) are what the object side is for.
    - **An enrichment belongs to the stash-box's own line**, where the thread draws one
      (`box_said`). The person and site writers record `enriched` with no box and no fields (they
      are handed neither), so on its own it would read "Enriched from something", one line below
      "FansDB filled in 10 details" for the same press. The box's line is read off the run, which
      knows the box, whether somebody pressed it and every field that landed; see `box_filled_in`.
      Only where that line IS drawn: a thread whose box link has been forgotten keeps the event,
      because then it is the only thing left that says an enrichment happened. ONLY THE NAMELESS
      EVENT: the run's own writer records `enriched` with the box as its object and what landed as
      its payload, one per press, and that event IS the box's line (see `enriched_by_box`). Those
      are never absorbed; it is the link table's latest-run line that gives way to them
      (`runs_not_drawn`).
    """
    if about is not None and box_said and event.verb == "enriched" and event.object is None:
        return True
    if (
        about is not None
        and about[0] in COUNTED_ON_ITS_PAGE
        and event.verb in LINK_VERBS
        and event.object is not None
        and (event.object.kind, event.object.id) == about
    ):
        return True
    if event.queue != ledger_queue:
        return not (about is None and from_object and event.reversed_at is None)
    if event.verb == "added":
        return True
    # A DOWNLOAD BELONGS TO THE DOWNLOADS SOURCE ON THE FILE'S OWN PAGE, and only there.
    #
    # `about` is None for a file only. The downloads row says it there, naming the username too, and
    # lives as long as the file does. The event is kept for the SITE's page, where nothing else
    # says it, and a failed download has no other source anywhere.
    if event.verb == "downloaded" and about is None:
        return True
    # A PRESS BELONGS TO THE PASS LINES ON THE FILE'S OWN PAGE (`history_sources.press_lines`),
    # and a look for faces to the face line, which says what each look found (`face_run_events`):
    # both are said there from their own reads, so a page capped at the newest acts loses neither.
    if event.verb in ("pressed", "face_run") and about is None:
        return True
    if (
        event.verb in LINK_VERBS
        and event.object is not None
        and (event.object.kind, event.object.id) in standing
    ):
        return True
    if event.verb in ("hidden", "revealed") and event.user_id != viewer.id:
        return True
    # A SAVE IS THE USER'S OWN, and an admin's to read, exactly as the save log it mirrors is
    # (`GET /save-log` is admin-only). What another user took home is not something a file a
    # guest may see should disclose.
    # A WALL SENT between one person's own devices is theirs for the same reason.
    if event.verb in ("saved", "wall_sent") and event.user_id != viewer.id and not viewer.is_admin:
        return True
    return event.verb in ("shared", "unshared") and not viewer.is_admin


def _given_key(event: LedgerEvent) -> tuple[object, ...]:
    """What makes two given receipts one act: the verb, the queue, who, and the song it gave."""
    return (
        event.verb,
        event.queue,
        event.actor_kind,
        event.actor_id,
        say.payload_of(event.payload).get("song"),
    )


def _receipts_given_together(
    events: list[LedgerEvent],
    *,
    is_given: Callable[[LedgerEvent], bool],
    gap: int,
) -> list[LedgerEvent]:
    """Receipts this page GAVE (it is their object), one line per act rather than one per file.

    A song shared from one file to its group writes a receipt per file that took the name, within
    moments of each other. Read from the file it came from they are one act, said once with every
    file it reached: the newest receipt carries the subjects of the ones folded into it. The key is
    the verb, the queue, who did it and what the payload says was given, and the run breaks at a
    gap longer than the feed's own fold. `events` is newest first, as the ledger reads it.
    """
    folded: list[LedgerEvent] = []
    for one in events:
        last = folded[-1] if folded else None
        if (
            last is not None
            and is_given(one)
            and is_given(last)
            and _given_key(one) == _given_key(last)
            and last.at - one.at <= gap
        ):
            seen = {(thing.kind, thing.id) for thing in last.subjects}
            extra = tuple(thing for thing in one.subjects if (thing.kind, thing.id) not in seen)
            folded[-1] = replace(last, subjects=(*last.subjects, *extra))
            continue
        folded.append(one)
    return folded


def _said_about(
    event: LedgerEvent,
    here: str,
    who: _Who,
    about_kind: str | None = None,
    *,
    from_object: bool = False,
) -> Said:
    """The line for one event, with what it was written down about read out of its payload.

    The payload is the writer's own shape and is read for the few words a line says: what a thing
    used to be called, which fields a save moved, how many things a delete could not name, where a
    delete took the file, what a merge brought and a song's name. Anything else in it stays where it
    is. A payload this reader cannot make sense of is not an error: the
    sentence says less and the line still draws, which is what every other unknown here does.

    `about_kind` is WHOSE page this is, and it is the vantage said exactly rather than in threes: a
    delete reads differently on a person's page and on a tag's, and both of those are the entity
    vantage. None is the file's own page, which is where a delete lists what the file was on.

    `from_object` is whether this page is the event's OBJECT rather than one of its subjects
    (see `sentences.event_said`'s `from_object`, which turns the line round).
    """
    actor, name = _ledger_actor(event, who)
    return say.event_said(
        event.verb or "",
        by=by_of(actor, name) or SIFT,
        here=here,
        task=_ledger_task(event),
        subjects=[
            LedgerSubject(kind=cast("SubjectKind", one.kind), id=one.id, name=one.name)
            for one in event.subjects
        ],
        about_kind=about_kind,
        object_kind=None if event.object is None else event.object.kind,
        object_id=None if event.object is None else event.object.id,
        object_name=None if event.object is None else event.object.name,
        title=event.title,
        from_object=from_object,
        payload=say.payload_of(event.payload),
    )


def _ledger_task(event: LedgerEvent) -> str | None:
    """Which task took an act Sift recorded, in the task's own word, or None."""
    if event.actor_kind not in (None, "sift"):
        return None
    return event.actor_id


@dataclass(frozen=True, slots=True)
class Lent:
    """What a ledger event dropped for a link that still stands knew and the link row may not: who
    did it, as a history names them, and when. See `ledger_events`'s `lent`."""

    actor: Actor
    name: str | None
    at: int


def _ledger_actor(event: LedgerEvent, who: _Who) -> tuple[Actor, str | None]:
    """Who took an act, in the words a history says it in. A box is named in words, not looked up."""
    return _actor_of_act(event.actor_kind, event.actor_id, event.user_id, who)


#: A link's kind back to the ledger's word for the same thing (`sentences.LINKED_KINDS` turned
#: round), so a line's names can be probed in `subjects_present`, which is keyed by the ledger's.
_LEDGER_KIND_OF_LINK: Mapping[str, str] = {link: kind for kind, link in _LINKED_KINDS_OF.items()}


def _resolved(
    line: Line,
    present: Mapping[tuple[str, str], str],
    unshown: frozenset[tuple[str, str]] = frozenset(),
) -> Line:
    """A line's things as ways to them, and as plain words where there is nowhere to go: a thing
    since deleted, or one this viewer may not be shown (`unshown`). Each thing by its own probe;
    a folder's address is its id, since `in:` takes one."""
    out: list[Piece] = []
    for one in line:
        if one.rest:
            out.append(replace(one, rest=_resolved(one.rest, present, unshown)))
            continue
        if one.kind is None or one.gone:
            out.append(one)
            continue
        ledger_kind = _LEDGER_KIND_OF_LINK.get(one.kind, one.kind)
        if (ledger_kind, one.id or "") in unshown:
            out.append(Piece(one.text))
            continue
        address = present.get((ledger_kind, one.id or ""))
        if address is None:
            out.append(Piece(one.text))
        elif ledger_kind == "folder":
            out.append(replace(one, href=say.files_in_folder(address)))
        elif ledger_kind == "download":
            out.append(replace(one, href=say.download_row(one.id or "")))
        else:
            out.append(one)
    return say.said(*out)


async def ledger_events(
    database: Database,
    viewer: Viewer,
    *,
    here: str,
    kind: str,
    subject_id: str,
    grants_as: str | None = None,
    limit: int = DEFAULT_LIMIT,
    box_said: bool = False,
    name_now: str | None = None,
    lent: dict[tuple[str, str], Lent] | None = None,
) -> list[Event]:
    """Everything the ledger recorded about one thing, said in this history's own voice.

    One source shared by every history: what differs between threads is a word and a vantage, and
    the rule that decides WHICH events are drawn is the same. Read through `history_events`, where
    the vault's answer is applied to the record, never with a statement of its own.

    `grants_as` is the word `acl_grants` files this kind of thing under, given only where the page
    already draws its grants; None means a share is drawn from the event or from nowhere. `here` is
    the page drawing it (`sentences.HERE`). `box_said` is whether the thread already draws a
    stash-box's line (`_drawn_elsewhere`). `name_now` is what the thing is called today, on the two
    pages a merge folds into (`_taken_as`). `lent`, where given, is filled with who did each act
    dropped for a link that still stands, and when (`Lent`).
    """
    from sift.kernel.access.history_events import events_of_asset, events_of_entity
    from sift.kernel.vocabulary import LEDGER_QUEUE

    # A process without the workbench slice has neither table, and naming one is a hard error.
    if len(list(await database.fetch_all(_LEDGER_TABLES))) != 2:
        return []
    found = (
        await events_of_asset(database, viewer, subject_id, limit=limit)
        if kind == "asset"
        else await events_of_entity(
            database, viewer, cast("SubjectKind", kind), subject_id, limit=limit
        )
    )
    if not found:
        return []
    standing = await _links_that_stand(
        database, kind=kind, subject_id=subject_id, grants_as=grants_as
    )
    on_object = _on_object_of(kind, subject_id)

    drawn = [
        one
        for one in found
        if not _drawn_elsewhere(
            one,
            viewer=viewer,
            standing=standing,
            ledger_queue=LEDGER_QUEUE,
            about=None if kind == "asset" else (kind, subject_id),
            box_said=box_said,
            from_object=on_object(one),
        )
    ]
    drawn = await _with_subjects(database, drawn, kind, subject_id, on_object)
    drawn = await removals_named(database, drawn, subject_id if kind == "asset" else None)
    drawn = _copies_not_standing(drawn, standing, subject_id, on_object)
    drawn, unshown = await unshown_unnamed(database, viewer, drawn)
    present = await _present_of(database, drawn, on_object)
    who = await _ledger_who(database, viewer, found, drawn, standing, lent)

    return await _said_oldest_first(
        database,
        drawn,
        kind=kind,
        subject_id=subject_id,
        here=here,
        who=who,
        present=present,
        name_now=name_now,
        on_object=on_object,
        viewer=viewer,
        unshown=unshown,
    )


def _on_object_of(kind: str, subject_id: str) -> Callable[[LedgerEvent], bool]:
    """Whether an event was done WITH this page's thing, so its line is read from the object's side."""

    def on_object(one: LedgerEvent) -> bool:
        return one.object is not None and (one.object.kind, one.object.id) == (kind, subject_id)

    return on_object


async def _with_subjects(
    database: Database,
    drawn: list[LedgerEvent],
    kind: str,
    subject_id: str,
    on_object: Callable[[LedgerEvent], bool],
) -> list[LedgerEvent]:
    """The events whose line names what they named, with those subjects read in.

    A delete names the file it ended on the page of everything that file was on, and the event is
    the only place that name still exists; an event read from its object's side names its subject.
    Bounded by the page, so it is one seek for the thread.
    """
    from sift.kernel.access.history_events import FEED_FOLD_GAP, subjects_of
    from sift.kernel.vocabulary import LEDGER_QUEUE

    asked = [one.id for one in drawn if one.verb in _SAID_FROM_SUBJECTS or on_object(one)]
    if asked:
        named = await subjects_of(database, asked)
        # The page itself is left out of an object-side line's subjects: it is `{here}` there.
        drawn = [
            replace(
                one,
                subjects=tuple(
                    thing
                    for thing in named.get(one.id, ())
                    if not on_object(one) or (thing.kind, thing.id) != (kind, subject_id)
                ),
            )
            if one.verb in _SAID_FROM_SUBJECTS or on_object(one)
            else one
            for one in drawn
        ]
        drawn = _receipts_given_together(
            drawn,
            is_given=lambda one: on_object(one) and one.queue != LEDGER_QUEUE,
            gap=FEED_FOLD_GAP,
        )
    return drawn


def _copies_not_standing(
    drawn: list[LedgerEvent],
    standing: set[tuple[str, str]],
    subject_id: str,
    on_object: Callable[[LedgerEvent], bool],
) -> list[LedgerEvent]:
    """Without a copy whose row still stands: the row draws it (see `_links_that_stand`)."""
    drawn = [
        one
        for one in drawn
        if one.verb != "produced"
        or not (
            ("produced", subject_id) in standing
            if not on_object(one)
            else any(("produced", thing.id) in standing for thing in one.subjects)
        )
    ]
    return drawn


async def _present_of(
    database: Database, drawn: list[LedgerEvent], on_object: Callable[[LedgerEvent], bool]
) -> dict[tuple[str, str], str]:
    """Which of the things the lines name are still there to link to."""
    from sift.kernel.access.history_events import subjects_present

    wanted: dict[str, list[str]] = {}
    for one in drawn:
        if one.object is not None:
            wanted.setdefault(one.object.kind, []).append(one.object.id)
        if on_object(one):
            # The subject is what an object-side line names and links, so it is probed as well.
            for thing in one.subjects:
                wanted.setdefault(thing.kind, []).append(thing.id)
    present = await subjects_present(database, wanted) if wanted else {}
    return present


async def _ledger_who(
    database: Database,
    viewer: Viewer,
    found: list[LedgerEvent],
    drawn: list[LedgerEvent],
    standing: set[tuple[str, str]],
    lent: dict[tuple[str, str], Lent] | None,
) -> _Who:
    """The users who took the drawn acts, and what the dropped acts lend their link rows."""
    from sift.kernel.vocabulary import LEDGER_QUEUE

    # The acts dropped because their link still stands, newest first: what they lend the link rows,
    # each beside the thing it was done with.
    standing_acts = (
        [
            (one.object, one)
            for one in found
            if one.queue == LEDGER_QUEUE
            and one.verb in LINK_VERBS
            and one.object is not None
            and (one.object.kind, one.object.id) in standing
        ]
        if lent is not None
        else []
    )
    who = _Who(
        viewer=viewer,
        names=await _names_of(
            database,
            [
                one.actor_id or ""
                for one in (*drawn, *(act for _thing, act in standing_acts))
                if one.actor_kind == "user" and one.actor_id
            ],
        ),
    )
    if lent is not None:
        for thing, one in standing_acts:
            actor, actor_name = _ledger_actor(one, who)
            lent.setdefault((thing.kind, thing.id), Lent(actor, actor_name, one.at))
    return who


async def _said_oldest_first(
    database: Database,
    drawn: list[LedgerEvent],
    *,
    kind: str,
    subject_id: str,
    here: str,
    who: _Who,
    present: Mapping[tuple[str, str], str],
    name_now: str | None,
    on_object: Callable[[LedgerEvent], bool],
    viewer: Viewer | None = None,
    unshown: frozenset[tuple[str, str]] = frozenset(),
) -> list[Event]:
    """Each drawn event as a line, OLDEST FIRST.

    The ledger's reads answer newest first, and the caller's sort is stable and by time alone, so
    reversed here two events written in the same second keep their order: a hide and the showing
    again that followed it never read the other way round.
    """
    first, taken = await _firsts_and_merged(database, drawn, kind, subject_id, name_now, on_object)
    events: list[Event] = []
    # A DAY OF DOWNLOADS IS ONE LINE on every page but the file's own (`_downloads_by_day`), by day
    # AND by who asked, so a day with your pastes and Sift's own downloads is two lines.
    fetched: dict[tuple[int, str, str | None], list[tuple[LedgerEvent, Actor, str | None]]] = {}
    for one in reversed(drawn):
        if one.verb == "downloaded" and kind != "asset":
            actor, actor_name = _ledger_actor(one, who)
            fetched.setdefault((day_number(one.at), actor.value, actor_name), []).append(
                (one, actor, actor_name)
            )
            continue
        if one.verb == "enriched" and one.object is not None and one.object.kind == "box":
            # ONE PRESS, ONE LINE, drawn by the one helper every box line goes through, so an
            # event and a link table's line cannot come to say one press two ways.
            pressed_first = first.get(one.object.id)
            events.append(
                enriched_by_box(
                    one,
                    kind,
                    again=pressed_first is not None and pressed_first != one.id,
                    named=await named_of_event(database, one, kind, subject_id, viewer),
                )
            )
            continue
        actor, actor_name = _ledger_actor(one, who)
        said = _said_about(
            one, here, who, None if kind == "asset" else kind, from_object=on_object(one)
        )
        line = _resolved(said.pieces, present, unshown)
        events.append(
            Event(
                at=one.at,
                actor=actor,
                actor_name=actor_name,
                kind=_event_kind(one),
                pieces=line if one.id not in taken else say.as_then(line, taken[one.id], here),
                # A line that counted what it stands for lists it under itself: "Edited 5
                # details" opens to the five, the way a box's "filled in 10 details" does.
                detail=_folded_detail(said.folded) + details_of(said.groups),
                via=_ledger_via(one),
            )
        )
    events.extend(_downloads_by_day(fetched, here, present, who))
    return events


async def _firsts_and_merged(
    database: Database,
    drawn: list[LedgerEvent],
    kind: str,
    subject_id: str,
    name_now: str | None,
    on_object: Callable[[LedgerEvent], bool],
) -> tuple[Mapping[str, str], Mapping[str, str]]:
    """Each box's first press about this thing, and whose act each was where it was merged in."""
    from sift.kernel.access.history_events import first_presses

    # Each box's first press about this thing, so a re-ask reads as one (see `first_presses`).
    first = (
        await first_presses(database, kind, subject_id)
        if any(one.verb == "enriched" for one in drawn)
        else {}
    )
    # WHOSE ACT IT WAS, where it was somebody merged into this page. See `_taken_as`.
    taken = (
        await _taken_as(
            database,
            drawn,
            kind=kind,
            subject_id=subject_id,
            name_now=name_now,
            on_object=on_object,
        )
        if kind != "asset"
        else {}
    )
    return first, taken


async def _taken_as(
    database: Database,
    drawn: Sequence[LedgerEvent],
    *,
    kind: str,
    subject_id: str,
    name_now: str | None,
    on_object: Callable[[LedgerEvent], bool],
) -> dict[str, str]:
    """Which lines were acts on somebody MERGED INTO this page, and the name each was taken under.

    A merge re-points every event about the one going at the survivor and keeps the name it was
    written with (`kernel/access/merged.py`), so on the survivor's page "Cover set to a group of
    faces" would draw as the survivor's own act. The line says whose it was then
    (`sentences.as_then`).

    ONLY A NAME SOMEBODY MERGED IN, never any name that differs from today's. A rename is the same
    person under a new word, and every line older than it would have gained "as <old name>":
    the whole thread, on every renamed person, saying a thing the rename line already says once.
    What needs saying is the other identity, and the page already holds the list of those:
    its own `merged` lines name everyone folded in. Read only where there is at least one, so a
    page nobody was merged into pays nothing.

    !! A NAME THE ONE GOING HAD BEFORE A RENAME OF THEIR OWN is not in that list and is not said.
    Unverified how often that happens; the merged line itself still says the merge happened.
    """
    from sift.kernel.access.history_events import subjects_of

    merged_in = {
        thing.name.casefold()
        for one in drawn
        if one.verb == "merged" and on_object(one)
        for thing in one.subjects
        if thing.name
    }
    if name_now is not None:
        merged_in.discard(name_now.casefold())
    if not merged_in:
        return {}
    named = await subjects_of(database, [one.id for one in drawn if not on_object(one)])
    taken: dict[str, str] = {}
    for one in drawn:
        if one.verb in _NOT_TAKEN_AS:
            continue
        if on_object(one):
            then = None if one.object is None else one.object.name
        else:
            then = next(
                (
                    thing.name
                    for thing in named.get(one.id, ())
                    if (thing.kind, thing.id) == (kind, subject_id)
                ),
                None,
            )
        if then and then.casefold() in merged_in:
            taken[one.id] = then
    return taken


#: The lines that never say "as <name>": a merge's own line names who went, and a rename's names
#: both of its words already.
_NOT_TAKEN_AS = frozenset({"merged", "renamed"})


def _downloads_by_day(
    days: Mapping[tuple[int, str, str | None], Sequence[tuple[LedgerEvent, Actor, str | None]]],
    here: str,
    present: Mapping[tuple[str, str], str],
    who: _Who,
) -> list[Event]:
    """One line per day of downloads, counting them and opening to the files. Each day oldest first.

    Not one line per download: on a Site that is "A file was downloaded from it" nine times in a
    row, saying nothing the first did not and taking nine of the thread's places under the cap. A
    day is the unit because it is the unit every other counted line on these threads uses
    (filings, taggings, usernames), so one screen does not fold by two clocks; it is the machine's
    local day (`kernel/when.py`), the one those lines count in SQL.

    THE FILES ARE THE EVENTS' OWN SUBJECTS, and that is why this needs no read of its own: an event
    read from its object's side has its subjects filled in by `ledger_events` (the file is what a
    download is ABOUT, the Site what it was done WITH), and every one of those events has already
    passed the vault's answer in `history_events`: a download of a file this viewer may not see
    is not here to be counted. A file that has since been deleted is listed struck through, the
    rule every name on these threads keeps (`Link.gone`), because the download still happened.

    `since` is the first download of the day and `at` the last, so the row says the span rather
    than a moment that was only the end of it.
    """
    events: list[Event] = []
    for runs in days.values():
        first, actor, actor_name = runs[0]
        files = tuple(
            Link(
                kind="asset",
                id=thing.id,
                name=say.name_of("asset", thing.name),
                gone=("asset", thing.id) not in present,
            )
            for one, _, _ in runs
            for thing in one.subjects
            if thing.kind == "asset"
        )
        line, words = say.downloads_folded(by_of(actor, actor_name) or SIFT, len(runs), here)
        last = runs[-1][0]
        events.append(
            Event(
                at=last.at,
                actor=actor,
                actor_name=actor_name,
                kind=_event_kind(first),
                pieces=line,
                detail=(Detail(kind="asset", words=words, links=files),) if files else (),
                via=_ledger_via(first),
                since=None if len(runs) == 1 else first.at,
            )
        )
    return events


def details_of(groups: Sequence[say.Group]) -> tuple[Detail, ...]:
    """What a line is about and does not say, as the groups its "Show each" opens to."""
    return tuple(
        Detail(kind=one.kind, words=one.words, links=tuple(link_of_piece(t) for t in one.things))
        for one in groups
    )


def _folded_detail(folded: tuple[str, tuple[str, ...]] | None) -> tuple[Detail, ...]:
    """A counted line's members as the one group its "Show each" opens to. See `Said.folded`."""
    if folded is None:
        return ()
    words, members = folded
    return (
        Detail(
            kind="field",
            words=words,
            links=tuple(Link(kind="field", id=one, name=one) for one in members),
        ),
    )


def _event_kind(event: LedgerEvent) -> str:
    """Which mark one event wears. A link says what it was made TO; see `_LINKED_KINDS`."""
    if event.verb == "linked":
        return _LINKED_KINDS.get("" if event.object is None else event.object.kind, "added")
    return _EVENT_KINDS.get(event.verb or "", "decided")


def _ledger_via(event: LedgerEvent) -> str | None:
    """Which pass did it, in the `enriched:` filter's own words, or None where it was not a pass.

    The actor's id is the pass word for Sift (one of `MADE_VIAS`), and only six of those are
    words this filter draws a mark for. A pass outside the six is Sift with no mark rather than a
    mark drawn from a word nothing else on the screen uses.
    """
    if event.actor_kind not in (None, "sift"):
        return None
    return event.actor_id if event.actor_id in VIAS else None
