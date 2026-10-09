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

# At module level, unlike `LEDGER_QUEUE` in `ledger_events`: the vocabulary imports nothing.
from sift.kernel.vocabulary import Subject as LedgerSubject
from sift.kernel.when import day_number

if TYPE_CHECKING:  # pragma: no cover
    from sift.kernel.access.history_events import LedgerEvent
    from sift.kernel.vocabulary import SubjectKind


#: Who made each share: `acl_grants` records only who a grant is for, never its maker.
_SHARES_MADE = """
SELECT d.object_id AS user_id, d.actor_kind AS actor_kind, d.actor_id AS actor_id,
       d.user_id AS by_user, d.decided_at AS at
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = ? AND s.subject_id = ? AND d.verb = 'shared' AND d.object_kind = 'login'
 ORDER BY d.decided_at ASC, d.id ASC
"""

#: A grant and its event are written in one transaction from two reads of the clock.
_SHARE_CLOCK_SLACK = 2


async def share_makers(
    database: Database, viewer: Viewer, kind: str, subject_id: str, grants: Sequence[Row]
) -> list[tuple[Actor, str | None]]:
    """Who made each of these grants, in their order: the actor and the name a history gives it."""
    unknown: tuple[Actor, str | None] = (Actor.SOMEBODY, None)
    if not grants:
        return []
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


# --- The ledger's own events: one line per act, drawn only where no other source draws it.


#: The verbs a link table already draws; an event with one is dropped while its row stands.
LINK_VERBS = frozenset({"linked", "filed", "named", "shared"})

#: The pages that count their links, where an object-side link belongs to the counted source.
COUNTED_ON_ITS_PAGE = frozenset({"person", "tag", "site"})

#: Acts whose line names what the event was about; a delete's file name exists nowhere else.
_SAID_FROM_SUBJECTS = frozenset({"deleted"})

#: Which of a file's links are still there; a site is two hops, through the username.
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

#: Apart from the statement above because it serves every kind of page, not only a file.
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
    "recounted": "ran",
}

_LINKED_KINDS: Mapping[str, str] = {
    "person": "named",
    "tag": "tagged",
    "site": "filed",
}


#: Asked by the source itself: a guard a caller must remember is missing on the next history.
_LEDGER_TABLES = (
    "SELECT name FROM sqlite_master WHERE type = 'table'"
    " AND name IN ('workbench_decisions', 'workbench_decision_subjects')"
)


_STILL_A_COPY = "SELECT asset_id AS id FROM produced_files WHERE asset_id = :subject"
_STILL_COPIED = "SELECT asset_id AS id FROM produced_files WHERE source_asset_id = :subject"

_ONE_TABLE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"


async def _has_table(database: Database, name: str) -> bool:
    """Whether a feature's table is registered in this database."""
    return await database.fetch_one(_ONE_TABLE, (name,)) is not None


async def _links_that_stand(
    database: Database, *, kind: str, subject_id: str, grants_as: str | None
) -> set[tuple[str, str]]:
    """Every link this thing still has, as (what kind of thing it was made to, its id)."""
    standing: set[tuple[str, str]] = set()
    if kind == "asset":
        for row in await database.fetch_all(_STILL_LINKED, {"subject": subject_id}):
            standing.add((str(row["kind"]), str(row["id"])))
        # A copy whose row stands is drawn from `produced_files`; guarded, as that table is a
        # feature's.
        if await _has_table(database, "produced_files"):
            for statement in (_STILL_A_COPY, _STILL_COPIED):
                for row in await database.fetch_all(statement, {"subject": subject_id}):
                    standing.add(("produced", str(row["id"])))
    if grants_as is not None:
        for row in await database.fetch_all(
            _STILL_GRANTED, {"type": grants_as, "subject": subject_id}
        ):
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
    """Whether some other source already says this, in which case the event is not drawn."""
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
    # A download is said by the downloads source on the file's own page; kept for the Site's page.
    if event.verb == "downloaded" and about is None:
        return True
    # A press and a look for faces are said by the file's own pass and face lines.
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
    # A save and a wall sent are the user's own and an admin's to read, as the save log is.
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
    """Receipts this page gave (it is their object), one line per act rather than one per file."""
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
    """The line for one event, with what it was written down about read out of its payload."""
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
    """Who did a dropped ledger event, and when, for the link row that still stands."""

    actor: Actor
    name: str | None
    at: int


def _ledger_actor(event: LedgerEvent, who: _Who) -> tuple[Actor, str | None]:
    """Who took an act, in the words a history says it in."""
    return _actor_of_act(event.actor_kind, event.actor_id, event.user_id, who)


_LEDGER_KIND_OF_LINK: Mapping[str, str] = {link: kind for kind, link in _LINKED_KINDS_OF.items()}


def _resolved(
    line: Line,
    present: Mapping[tuple[str, str], str],
    unshown: frozenset[tuple[str, str]] = frozenset(),
) -> Line:
    """A line's things as ways to them, or plain words where there is nowhere to go."""
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
    """Everything the ledger recorded about one thing, said in this history's own voice."""
    from sift.kernel.access.history_events import events_of_asset, events_of_entity
    from sift.kernel.vocabulary import LEDGER_QUEUE

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
    """Whether an event was done WITH this page's thing, so its line reads from the object side."""

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
    """The events whose line names what they named, with those subjects read in."""
    from sift.kernel.access.history_events import FEED_FOLD_GAP, subjects_of
    from sift.kernel.vocabulary import LEDGER_QUEUE

    asked = [one.id for one in drawn if one.verb in _SAID_FROM_SUBJECTS or on_object(one)]
    if asked:
        named = await subjects_of(database, asked)
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
    """Without a copy whose row still stands: the row draws it."""
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
    """Each drawn event as a line, oldest first, so same-second events keep their order."""
    first, taken = await _firsts_and_merged(database, drawn, kind, subject_id, name_now, on_object)
    events: list[Event] = []
    fetched: dict[tuple[int, str, str | None], list[tuple[LedgerEvent, Actor, str | None]]] = {}
    for one in reversed(drawn):
        if one.verb == "downloaded" and kind != "asset":
            actor, actor_name = _ledger_actor(one, who)
            fetched.setdefault((day_number(one.at), actor.value, actor_name), []).append(
                (one, actor, actor_name)
            )
            continue
        if one.verb == "enriched" and one.object is not None and one.object.kind == "box":
            # One helper draws every box line, so an event and a link table cannot say one press two
            # ways.
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

    first = (
        await first_presses(database, kind, subject_id)
        if any(one.verb == "enriched" for one in drawn)
        else {}
    )
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
    """Which lines were acts on somebody merged into this page, and the name each was taken as."""
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


_NOT_TAKEN_AS = frozenset({"merged", "renamed"})


def _downloads_by_day(
    days: Mapping[tuple[int, str, str | None], Sequence[tuple[LedgerEvent, Actor, str | None]]],
    here: str,
    present: Mapping[tuple[str, str], str],
    who: _Who,
) -> list[Event]:
    """One line per day of downloads, counting them and opening to the files; oldest first."""
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
    """A counted line's members as the one group its "Show each" opens to."""
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
    """Which mark one event wears. A link says what it was made TO."""
    if event.verb == "linked":
        return _LINKED_KINDS.get("" if event.object is None else event.object.kind, "added")
    return _EVENT_KINDS.get(event.verb or "", "decided")


def _ledger_via(event: LedgerEvent) -> str | None:
    """Which pass did it, in the `enriched:` filter's own words, or None where it was not a pass."""
    if event.actor_kind not in (None, "sift"):
        return None
    return event.actor_id if event.actor_id in VIAS else None
