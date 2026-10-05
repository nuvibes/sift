# SPDX-License-Identifier: AGPL-3.0-or-later
"""A stash-box's History lines: what a box recognized and filled in, an answer kept over a box's,
and where the number on a counted filing goes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlparse

from sift.kernel.access import sentences as say
from sift.kernel.access.constraints import BOX_SOURCE, Filing
from sift.kernel.access.history_line import Actor, Detail, Event, Link
from sift.kernel.access.sentences import A_THING, SIFT, FilledField, Line
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row, in_clause, point_read
from sift.kernel.records import Subject, fields_filled, fields_of, said_plainly, value_said
from sift.kernel.sql_splice import splice
from sift.kernel.where import folder_said

if TYPE_CHECKING:  # pragma: no cover
    from sift.kernel.access.history_events import LedgerEvent, Thing
    from sift.kernel.access.repository import Repository


def kept_line(row: Row, subject: str) -> Line:
    """A stash-box's answer refused for what was here, from one `stash_box_kept` row read with its
    `box`, `field`, `mine` and `theirs`: the field's own word and both values as the record draws
    them. The one reading every page's kept line goes through."""
    field_key = str(row["field"])
    return say.kept_mine(
        None,
        str(row["box"]),
        field_word(subject, field_key),
        value_said(subject, field_key, row["mine"]),
        value_said(subject, field_key, row["theirs"]),
    )


#: A STASH-BOX DISAGREED ABOUT A FIELD AND THE ANSWER WAS TO KEEP WHAT WAS HERE: the half of the
#: enrichment story that says somebody made a judgement. One statement for every kind of page, the
#: subject word bound. `PRIMARY KEY (subject, local_id, box_id, key)` is the seek.
_KEPT = point_read(
    "history.kept_mine",
    """
SELECT b.name AS box, k.key AS field, k.mine AS mine, k.theirs AS theirs, k.decided_at AS at,
       k.box_id AS box_id
  FROM stash_box_kept k
  JOIN stash_boxes b ON b.id = k.box_id
 WHERE k.subject = ? AND k.local_id = ?
 ORDER BY k.decided_at ASC, k.key ASC
""",
)


async def kept_events(database: Database, subject: str, local_id: str) -> list[Event]:
    """Which of a box's answers about this thing were refused, in favour of what was already here.

    The one read every page's thread draws these from: a file's, a person's, a Site's and a tag's.
    Asked only where `stash_box_kept` and `stash_boxes` are both installed.
    """
    return [
        Event(
            at=int(row["at"]),
            # SOMEBODY: the table records that the disagreement was answered and nothing at all
            # about which user answered it.
            actor=Actor.SOMEBODY,
            actor_name=None,
            kind="kept_mine",
            pieces=kept_line(row, subject),
            answer=(subject, local_id, str(row["box_id"]), str(row["field"])),
        )
        for row in await database.fetch_all(_KEPT, (subject, local_id))
    ]


def field_word(subject: str, key: str) -> str:
    """One field of a record, in the words its own row on the record wears, never its key.

    Not "StashDB disagreed about breast_type": the record calls that row "Breast type". The field
    registry is the one place a field's words live, and
    `said_plainly` is how a label reads mid-sentence; a key nothing declares keeps its own spelling
    with the underscores opened out, and a subject the registry has never heard of does the same.
    """
    try:
        whose = Subject(subject)
    except ValueError:
        return key.replace("_", " ")
    # `said_plainly` answers for every key it is handed, declared or not.
    return said_plainly(whose, (key,))[0]


# --- what a box filled in, NAMED --------------------------------------------------------------------
#
# A run records which fields a box filled (`enrichment_runs.applied`), never the values. The values
# are the box's answer kept on the link (`*_stash_box_links.payload`), and a list's are the rows
# added in the run's own moment that the answer lists, as many as the run counted.

#: How far from a run's moment the rows it wrote can carry their time. The run is written after
#: the rows, in the same press, a second or less after them.
RUN_SPAN = 60

#: The box's kept answer about one subject, per kind: a fixed statement each, never a table name
#: spliced into text (`sift-no-string-built-sql`).
_KEPT_ANSWER: Mapping[str, str] = {
    "person": "SELECT payload FROM person_stash_box_links WHERE person_id = ? AND box_id = ?",
    "site": "SELECT payload FROM site_stash_box_links WHERE site_id = ? AND box_id = ?",
    "tag": "SELECT payload FROM tag_stash_box_links WHERE tag_id = ? AND box_id = ?",
}
_RECORD_ROW: Mapping[str, str] = {
    "person": "SELECT * FROM people WHERE id = ?",
    "site": "SELECT * FROM sites WHERE id = ?",
    "tag": "SELECT * FROM tags WHERE id = ?",
}
_ALIASES_HELD: Mapping[str, str] = {
    "person": "SELECT alias AS name, added_at AS at FROM people_aliases WHERE person_id = ?",
    "site": "SELECT alias AS name, added_at AS at FROM site_aliases WHERE site_id = ?",
    "tag": "SELECT alias AS name, added_at AS at FROM tag_aliases WHERE tag_id = ?",
}
_LINKS_HELD: Mapping[str, str] = {
    "person": "SELECT url AS name, created_at AS at FROM people_links WHERE person_id = ?",
    "site": "SELECT url AS name, created_at AS at FROM site_links WHERE site_id = ?",
}
_TAGS_HELD: Mapping[str, str] = {
    "person": "SELECT t.id AS id, t.name AS name, j.added_at AS at"
    " FROM person_tags j JOIN tags t ON t.id = j.tag_id WHERE j.person_id = ?",
    "site": "SELECT t.id AS id, t.name AS name, j.added_at AS at"
    " FROM site_tags j JOIN tags t ON t.id = j.tag_id WHERE j.site_id = ?",
}
#: The usernames a box's run joined to a person: the ledger's `linked` events with the person as
#: the object, written in the same press (`PersonWriter._attach`), and the Site each is on.
_USERNAMES_JOINED = """
SELECT s.subject_id AS id, COALESCE(u.name, s.name) AS name, u.person_id AS person_id,
       u.id IS NULL AS gone, st.id AS site_id, st.name AS site
  FROM workbench_decisions d
  JOIN workbench_decision_subjects s ON s.decision_id = d.id AND s.kind = 'username'
  LEFT JOIN usernames u ON u.id = s.subject_id
  LEFT JOIN sites st ON st.id = u.site_id
 WHERE d.verb = 'linked' AND d.object_kind = 'person' AND d.object_id = ?
   AND d.decided_at BETWEEN ? AND ?
   AND (d.actor_kind = 'user' OR d.actor_id = ?)
 ORDER BY d.decided_at, s.name
"""
_SITE_NAMED = "SELECT id, name FROM sites WHERE name = ? COLLATE NOCASE"
_SHOWN_OF = (
    "SELECT object_id FROM viewer_entity_counts"
    " WHERE user_id = ? AND kind = ? AND permitted > 0 AND object_id IN (?*)"
)


async def shown_of(
    database: Database, viewer: Viewer | None, kind: str, ids: Sequence[str]
) -> set[str]:
    """Which of these things the viewer may be shown: every one for an admin, or with no viewer."""
    wanted = sorted(set(ids))
    if viewer is None or viewer.is_admin or not wanted:
        return set(wanted)
    statement, values = in_clause(_SHOWN_OF, wanted)
    rows = await database.fetch_all(statement, [viewer.id, kind, *values])
    return {str(row["object_id"]) for row in rows}


#: The record's column for a field key where the two differ; every other key is its own column.
_COLUMN: Mapping[str, str] = {"details": "notes", "parent": "parent_id"}

#: Fields whose value is a paragraph: said by their word alone, never read out into a line.
_WORD_ONLY = frozenset({"details", "description"})


def _kept_fields(payload: object) -> Mapping[str, object]:
    """The fields of the box's kept answer, or nothing where it will not read."""
    try:
        records = json.loads(str(payload))
    except ValueError:
        return {}
    first = records[0] if isinstance(records, list) and records else None
    fields = first.get("fields") if isinstance(first, dict) else None
    return fields if isinstance(fields, dict) else {}


def _folded(value: object) -> set[str]:
    """A value as the set of words it holds, for comparing a box's answer with the record's."""
    if isinstance(value, str):
        with suppress(ValueError):
            value = json.loads(value) if value[:1] in "[{" else value
    items = value if isinstance(value, list | tuple) else [value]
    return {str(one).strip().casefold() for one in items if one is not None and str(one).strip()}


def _host(url: str) -> str:
    """A link as its site's address, which is what tells two of them apart in a sentence."""
    host = urlparse(url).netloc or url
    return host.removeprefix("www.")


_COUNTED_KINDS = ("person", "tag", "site", "username", "collection", "photo_set", "song")


async def unshown_unnamed(
    database: Database, viewer: Viewer, drawn: list[LedgerEvent]
) -> tuple[list[LedgerEvent], frozenset[tuple[str, str]]]:
    """The events with each thing this viewer may not be shown left nameless, and those things."""
    wanted: dict[str, set[str]] = {}
    for one in drawn if not viewer.is_admin else ():
        for thing in (one.object, *one.subjects):
            if thing is not None and thing.kind in _COUNTED_KINDS and thing.id:
                wanted.setdefault(thing.kind, set()).add(thing.id)
    unshown: set[tuple[str, str]] = set()
    for kind, ids in wanted.items():
        seen = await shown_of(database, viewer, kind, sorted(ids))
        unshown |= {(kind, one) for one in ids - seen}
    if not unshown:
        return drawn, frozenset()

    def nameless(thing: Thing) -> Thing:
        return replace(thing, name=None) if (thing.kind, thing.id) in unshown else thing

    return [
        replace(
            one,
            object=None if one.object is None else nameless(one.object),
            subjects=tuple(map(nameless, one.subjects)),
        )
        for one in drawn
    ], frozenset(unshown)


_FOLDER_PLACES = "SELECT id, root_id, rel_path FROM folders WHERE id IN (?*)"
#: Kinds whose page is an admin's screen (the Downloads queue, Organize); a count keeps its words.
_AN_ADMINS_PAGE = ("download", "face_pile", "faces")


async def _unshown_of(
    database: Database, access: Repository | None, viewer: Viewer, wanted: Mapping[str, set[str]]
) -> set[tuple[str, str]]:
    """Which of these things the viewer may not be shown; a folder by `kernel.where`'s rule."""
    from sift.kernel.access.history_sources import _folders_seen  # it imports this module

    unshown: set[tuple[str, str]] = set()
    for kind, ids in wanted.items():
        if kind in _AN_ADMINS_PAGE:
            unshown |= {(kind, one) for one in ids}
            continue
        if kind != "folder":
            shown = await shown_of(database, viewer, kind, list(ids))
            unshown |= {(kind, one) for one in ids - shown}
            continue
        statement, values = in_clause(_FOLDER_PLACES, sorted(ids))
        places = {str(row["id"]): row for row in await database.fetch_all(statement, values)}
        seen = {} if access is None else await _folders_seen(access, viewer)
        for one in ids:
            row = places.get(one)
            path = "" if row is None else str(row["rel_path"])
            if row is None or folder_said(path, seen=seen.get(str(row["root_id"]), ())) != path:
                unshown.add((kind, one))
    return unshown


def _nameless(line: Line, unshown: set[tuple[str, str]]) -> Line:
    """The line with each unshown thing in a stranger's words, its kind said once."""
    out: list[say.Piece] = []
    for one in line:
        if one.rest:
            out.append(replace(one, rest=_nameless(one.rest, unshown)))
        elif one.kind is not None and (one.kind, one.id or "") in unshown:
            words = A_THING.get(one.kind, one.text)
            before = f"the {words.split(' ', 1)[-1]} ".casefold()
            if (
                one.kind in A_THING
                and out
                and out[-1].kind is None
                and out[-1].text.casefold().endswith(before)
            ):
                out[-1] = say.Piece(out[-1].text[: -len(before)])
            out.append(say.Piece(words))
        else:
            out.append(one)
    return say.said(*out)


async def unshown_said(
    database: Database, access: Repository | None, viewer: Viewer, events: list[Event]
) -> list[Event]:
    """A thread's lines as they leave, each thing the viewer may not be shown said nameless."""
    wanted: dict[str, set[str]] = {}
    for event in events if not viewer.is_admin else ():
        named = [(one.kind or "", one.id or "") for one in say.things_in(event.pieces)]
        named += [(link.kind, link.id) for group in event.detail for link in group.links]
        for kind, one in named:
            if kind in (*_COUNTED_KINDS, "folder", *_AN_ADMINS_PAGE) and one:
                wanted.setdefault(kind, set()).add(one)
    unshown = await _unshown_of(database, access, viewer, wanted) if wanted else set()
    if not unshown:
        return events
    return [
        replace(
            event,
            pieces=_nameless(event.pieces, unshown),
            detail=tuple(
                replace(
                    group,
                    links=tuple(
                        Link(kind="", id="", name=A_THING.get(one.kind, one.name))
                        if (one.kind, one.id) in unshown
                        else one
                        for one in group.links
                    ),
                )
                for group in event.detail
            ),
        )
        for event in events
    ]


class _Held:
    """What one record holds now, read once for every field a line names."""

    def __init__(
        self, database: Database, subject: str, local_id: str, viewer: Viewer | None = None
    ) -> None:
        self.database = database
        self.viewer = viewer
        self.subject = subject
        self.local_id = local_id
        self.row: Row | None = None
        self.lists: dict[str, list[Row]] = {}

    async def load(self) -> None:
        rows = await self.database.fetch_all(_RECORD_ROW[self.subject], (self.local_id,))
        self.row = rows[0] if rows else None
        for key, reads in (
            ("aliases", _ALIASES_HELD),
            ("links", _LINKS_HELD),
            ("tags", _TAGS_HELD),
        ):
            statement = reads.get(self.subject)
            if statement is not None:
                self.lists[key] = list(await self.database.fetch_all(statement, (self.local_id,)))

    def value(self, key: str) -> object:
        if self.row is None:
            return None
        column = _COLUMN.get(key, key)
        columns = list(self.row.keys())
        return self.row[column] if column in columns else None


async def _single(
    held: _Held, subject: Subject, key: str, theirs: object, *, agreeing: bool
) -> FilledField | None:
    """One single-value field with the box's value, and whether the record still holds it."""
    label = said_plainly(subject, (key,))[0]
    if key in _WORD_ONLY:
        return None if agreeing else FilledField(label)
    if key == "parent":
        found = await held.database.fetch_all(_SITE_NAMED, (str(theirs),))
        shown = await shown_of(
            held.database, held.viewer, "site", [str(one["id"]) for one in found]
        )
        site = next((one for one in found if str(one["id"]) in shown), None)
        same = site is not None and held.value("parent") == site["id"]
        value: Line = (
            say.said(say.thing("site", str(site["id"]), str(site["name"])))
            if site is not None
            else say.said(str(theirs))
        )
    elif isinstance(theirs, list | tuple):
        same = _folded(theirs) == _folded(held.value(key))
        items = tuple(say.said(str(one)) for one in theirs if str(one).strip())
        if agreeing and not same:
            return None
        return FilledField(label, count=max(len(items), 1), values=items, changed=not same)
    else:
        said_value = value_said(subject, key, theirs)
        if said_value is None:
            return None if agreeing else FilledField(label)
        same = _folded(theirs) == _folded(held.value(key))
        value = say.said(said_value)
    if agreeing and not same:
        return None
    return FilledField(label, values=(value,), changed=not same)


def _added_rows(rows: Sequence[Row], offered: set[str], at: int | None) -> list[Row]:
    """The rows of one list the box's answer lists, added in the run's own moment where there is
    one (`at`), or every such row the record holds where there is not."""
    return [
        one
        for one in rows
        if str(one["name"]).strip().casefold() in offered
        and (
            at is None
            or (one["at"] is not None and at - RUN_SPAN <= int(one["at"]) <= at + RUN_SPAN)
        )
    ]


async def _joined_usernames(held: _Held, at: int, box_id: str) -> tuple[list[Line], int]:
    """The usernames a box's run joined to a person, and how many it joined."""
    rows = await held.database.fetch_all(
        _USERNAMES_JOINED, (held.local_id, at - RUN_SPAN, at + RUN_SPAN, box_id)
    )
    seen = await shown_of(held.database, held.viewer, "username", [str(one["id"]) for one in rows])
    values: list[Line] = []
    for one in rows:
        # A username removed since has no Site to say it on: counted, and said as "N since
        # removed" (`filled_field`), as every list says a removed row.
        if one["gone"]:
            continue
        if str(one["id"]) not in seen:
            values.append(say.said(A_THING["username"]))
            continue
        owner = None if one["person_id"] is None else str(one["person_id"])
        name = say.thing(
            "username",
            str(one["id"]),
            str(one["name"]),
            href=say.username_opens(str(one["id"]), owner),
        )
        where = (
            say.said(" on ", say.thing("site", str(one["site_id"]), str(one["site"])))
            if one["site_id"] is not None
            else None
        )
        values.append(say.said(name, where))
    return values, len(rows)


async def _tags_said(held: _Held, added: Sequence[Row]) -> list[Line]:
    """A record's tags a box's run added, each by name only where the viewer may be shown it."""
    seen = await shown_of(held.database, held.viewer, "tag", [str(one["id"]) for one in added])
    return [
        say.said(say.thing("tag", str(one["id"]), str(one["name"])))
        if str(one["id"]) in seen
        else say.said(A_THING["tag"])
        for one in added
    ]


async def _listed_field(
    held: _Held,
    subject: Subject,
    key: str,
    theirs: Mapping[str, object],
    count: int | None,
    at: int | None,
    box_id: str,
) -> FilledField | None:
    """One list field (aliases, links, tags, usernames) with the rows the box added to it."""
    label = said_plainly(subject, (key,))[0]
    values: list[Line] = []
    joined = 0
    if key == "accounts" and subject is Subject.PERSON and at is not None:
        values, joined = await _joined_usernames(held, at, box_id)
    elif key == "links":
        accounts = theirs.get("accounts")
        offered = _folded(theirs.get("links")) | {
            str(entry.get("url") or "").strip().casefold()
            for entry in (accounts if isinstance(accounts, list) else [])
            if isinstance(entry, dict)
        }
        values = [
            say.said(_host(str(one["name"])))
            for one in _added_rows(held.lists.get("links", []), offered, at)
        ]
    elif key in ("aliases", "tags"):
        offered = _folded(theirs.get(key))
        # A tag's aliases are written whole, so a tag's run has no moment of its own to read by.
        moment = None if subject is Subject.TAG else at
        added = _added_rows(held.lists.get(key, []), offered, moment)
        values = (
            await _tags_said(held, added)
            if key == "tags"
            else [say.said(str(one["name"])) for one in added]
        )
        if subject is Subject.TAG:
            count = len(values)
    else:
        return None
    if count is None:
        if not values and not joined:
            return None
        count = max(len(values), joined)
    return FilledField(label, count=max(count, len(values)), values=tuple(values))


#: The list fields, read from their own tables rather than the record's row.
_LISTS = frozenset({"aliases", "links", "tags", "accounts"})


async def filled_named(
    database: Database,
    subject: str,
    local_id: str,
    box_id: str,
    *,
    at: int | None,
    stored: object,
    viewer: Viewer | None = None,
) -> tuple[FilledField, ...] | None:
    """What one box's run filled in on this record, every field with the values it put there.

    `stored` is the run's `applied` (or its event's payload): which fields, and how many rows each
    list gained. None where it says nothing (a link made before runs were recorded): the caller
    says that, with `agreeing_with_box` for what the record holds today. In the record's own order,
    one `FilledField` per field; a field this cannot read a value for is its word alone.
    """
    kind = next((one for one in Subject if one.value == subject), None)
    if kind is None or subject not in _KEPT_ANSWER or not isinstance(stored, str) or not stored:
        return None
    try:
        applied = json.loads(stored)
    except ValueError:
        return None
    if isinstance(applied, list):
        counts: dict[str, int | None] = {str(one): None for one in applied}
    elif isinstance(applied, dict):
        counts = {
            str(key): int(value)
            for key, value in applied.items()
            if isinstance(value, int) and value > 0
        }
    else:
        return None
    kept = await database.fetch_all(_KEPT_ANSWER[subject], (local_id, box_id))
    theirs = _kept_fields(kept[0]["payload"]) if kept else {}
    held = _Held(database, subject, local_id, viewer)
    await held.load()
    out: list[FilledField] = []
    for key in _in_record_order(kind, counts):
        label = said_plainly(kind, (key,))[0]
        one: FilledField | None
        if key in _LISTS:
            one = await _listed_field(held, kind, key, theirs, counts[key], at, box_id)
        elif key in theirs:
            one = await _single(held, kind, key, theirs[key], agreeing=False)
        else:
            one = None
        out.append(one if one is not None else FilledField(label, count=counts[key] or 1))
    return tuple(out)


async def agreeing_with_box(
    database: Database, subject: str, local_id: str, box_id: str, viewer: Viewer | None = None
) -> tuple[FilledField, ...]:
    """What the record holds today that is the box's own answer: for a link made before Sift
    recorded what a box fills in, the most that can truthfully be said about what it gave.

    Agreement, not authorship: a value typed by hand that happens to match is listed too, and the
    line says "agrees with it" for exactly that reason (`sentences.linked_before_recorded`).
    """
    kind = next((one for one in Subject if one.value == subject), None)
    if kind is None or subject not in _KEPT_ANSWER:
        return ()
    kept = await database.fetch_all(_KEPT_ANSWER[subject], (local_id, box_id))
    theirs = _kept_fields(kept[0]["payload"]) if kept else {}
    if not theirs:
        return ()
    held = _Held(database, subject, local_id, viewer)
    await held.load()
    out: list[FilledField] = []
    for key in _in_record_order(kind, dict.fromkeys(theirs)):
        if key == "name":
            continue
        if key in _LISTS:
            one = (
                await _listed_field(held, kind, key, theirs, None, None, box_id)
                if key != "accounts"
                else None
            )
        else:
            one = await _single(held, kind, key, theirs[key], agreeing=True)
        if one is not None and one.values:
            out.append(one)
    return tuple(out)


def _in_record_order(kind: Subject, keys: Mapping[str, object]) -> list[str]:
    """These keys in the order the record draws them, any it does not declare after them."""
    declared = [one.key for one in fields_of(kind) if one.key in keys]
    return declared + [one for one in keys if one not in declared]


def box_filled_in(
    *,
    at: int,
    box: str,
    box_id: str | None,
    pressed: bool | None,
    filled: Sequence[str] | None,
    whose: str,
    again: bool = False,
    named: Sequence[FilledField] | None = None,
) -> Event:
    """A stash-box that knows this thing, and what its last ask filled in, as ONE line that opens.

    The one builder of that line for every thread that draws it (a person's, a site's and a
    tag's), so the three cannot come to say one press three ways.

    WHAT IT FILLED IN IS NAMED, ALL OF IT. Up to three fields the sentence names them; past that it
    counts ("FansDB filled in 10 details") and the fields are listed under it, in the record's own
    words and order, in the row's "Show each" fold: the mechanism the file's line already uses for
    the people and tags a box wrote. The heading over the list is `filled_folded`'s phrase, the same
    string the sentence carries.

    THIS LINE IS THE PRESS, and the ledger's `enriched` event written by the same write is not
    drawn beside it (see `_drawn_elsewhere`). The event knows neither the box nor the fields; this
    line knows both.

    `box_id` is the box's row, carried so `runs_not_drawn` can match a table line to the ledger's
    presses by WHICH box rather than by what it is called. `again` is whether this box had been
    asked about this thing before this press; see `sentences.box_line`.

    `named` is every field WITH WHAT IT HOLDS (`filled_named`): the line names the values where it
    names the fields, and the fold under a counted line lists each field with its values.
    """
    folded = say.filled_folded(filled)
    who, line = say.box_line(
        box, pressed, filled, whose, again, whom="them" if whose == "their" else "it", named=named
    )
    shown = (
        [(one.label, say.text_of(say.filled_field(one, most=None))) for one in named]
        if named and filled is not None and len(named) == len(filled)
        else [(one, one) for one in filled or ()]
    )
    actor = {say.YOU: Actor.YOU, SIFT: Actor.SIFT, "": Actor.SOMEBODY}.get(who, Actor.STASH_BOX)
    detail = (
        (
            Detail(
                kind="field",
                words=folded,
                links=tuple(Link(kind="field", id=key, name=words) for key, words in shown),
            ),
        )
        if folded is not None
        else ()
    )
    return Event(
        at=at,
        actor=actor,
        actor_name=box if actor is Actor.STASH_BOX else None,
        kind="enriched",
        pieces=line,
        detail=detail,
        by_hand=pressed,
        box_id=box_id,
        # A stash-box has no page, so it is named in words; `via` draws its mark on the row.
        via="stash",
    )


def enriched_by_box(
    event: LedgerEvent,
    kind: str,
    *,
    again: bool = False,
    named: Sequence[FilledField] | None = None,
) -> Event:
    """One `enriched` event that names its box, as that box's line for this one press.

    Written by `StashBoxService.record_enrichment` in the same transaction as the run, with the box
    as the OBJECT and what landed as the PAYLOAD: the run column's own shape, so `fields_filled`
    reads it exactly as it reads the column: nothing for a bare link, `{}` for a plan that filled
    nothing, else each field against how many rows it gained. By hand where a USER is the
    actor, which is how the writer records a press; the box is the actor of a run nobody pressed.

    `kind` is whose thread this is, for the field words and the possessive: "their" on a person,
    "its" on a site or a tag: the same one-word difference `box_filled_in`'s callers hand in.
    """
    box = event.object
    subject = next((one for one in Subject if one.value == kind), None)
    return box_filled_in(
        at=event.at,
        box=(box.name if box is not None and box.name else None) or "A stash-box",
        box_id=None if box is None else box.id,
        pressed=event.actor_kind == "user",
        filled=None if subject is None else fields_filled(subject, event.payload or None),
        whose="their" if kind == "person" else "its",
        again=again,
        named=named,
    )


async def named_of_event(
    database: Database,
    event: LedgerEvent,
    kind: str,
    subject_id: str,
    viewer: Viewer | None = None,
) -> tuple[FilledField, ...] | None:
    """What one `enriched` event's press filled in, each field with its values (`filled_named`)."""
    box = event.object
    if box is None or not box.id:
        return None
    return await filled_named(
        database, kind, subject_id, box.id, at=event.at, stored=event.payload or None, viewer=viewer
    )


async def named_of_events(
    database: Database, events: Sequence[LedgerEvent]
) -> dict[str, tuple[FilledField, ...]]:
    """What each `enriched` event on a page filled in, named (`filled_named`), by the event's id:
    the feed's reading of the same fact a thing's own History says. Only events that name a box
    and one person, Site or tag; every other event is left out, and costs nothing."""
    out: dict[str, tuple[FilledField, ...]] = {}
    for event in events:
        if event.verb != "enriched" or event.object is None or event.object.kind != "box":
            continue
        about = next((one for one in event.subjects if one.kind in _KEPT_ANSWER), None)
        if about is None:
            continue
        named = await named_of_event(database, event, about.kind, about.id)
        if named:
            out[event.id] = named
    return out


async def linked_line(
    database: Database,
    subject: Subject,
    local_id: str,
    row: Row,
    whose: str,
    viewer: Viewer | None = None,
) -> Event:
    """A link table's line for one box: its latest run, every field named with its values.

    `row` is one of the threads' link reads (`box`, `box_id`, `at`, `automatic`, `applied` and
    `run_at`, the latest run's own moment). A link with NO run at all was made before Sift recorded
    what a box fills in, and says exactly that, with what the record agrees with it on today
    (`sentences.linked_before_recorded`); never that Sift has no record of it.
    """
    box_id = str(row["box_id"])
    box = str(row["box"])
    run_at = row["run_at"]
    event = box_filled_in(
        at=int(row["at"]),
        box=box,
        box_id=box_id,
        pressed=_by_hand(row),
        filled=fields_filled(subject, row["applied"]),
        whose=whose,
        named=None
        if run_at is None
        else await filled_named(
            database,
            subject.value,
            local_id,
            box_id,
            at=int(run_at),
            stored=row["applied"],
            viewer=viewer,
        ),
    )
    if run_at is not None:
        return event
    matching = await agreeing_with_box(database, subject.value, local_id, box_id, viewer)
    return replace(event, pieces=say.linked_before_recorded(box, matching))


#: `enrichment_runs` carries the two halves the link row cannot: who set it going, and which fields
#: the ask actually filled in (version 52 of the catalog; see `_ADD_ENRICHMENT_APPLIED`). A link
#: written by Auto-enrich and one somebody made in the chooser leave identical rows, so the answer
#: is stored beside them. NULL for every link made before that was written down.
#:
#: !! BOTH WERE A LEFT JOIN until v51 of the catalog, and neither can be one now: every ask is kept,
#: so a tag enriched against one box three times would join three rows and the thread would draw the
#: same line three times. The LAST run is one row off `ix_enrichment_runs_subject`.
_TAG_LINKED = """
SELECT b.name AS box, l.box_id AS box_id, l.fetched_at AS at,
       (SELECT r.automatic FROM enrichment_runs r
         WHERE r.subject = 'tag' AND r.local_id = l.tag_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS automatic,
       (SELECT r.at FROM enrichment_runs r
         WHERE r.subject = 'tag' AND r.local_id = l.tag_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS run_at,
       (SELECT r.applied FROM enrichment_runs r
         WHERE r.subject = 'tag' AND r.local_id = l.tag_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS applied
  FROM tag_stash_box_links l
  JOIN stash_boxes b ON b.id = l.box_id
 WHERE l.tag_id = ?
"""

_SITE_LINKED = """
SELECT b.name AS box, l.box_id AS box_id, l.fetched_at AS at,
       (SELECT r.automatic FROM enrichment_runs r
         WHERE r.subject = 'site' AND r.local_id = l.site_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS automatic,
       (SELECT r.at FROM enrichment_runs r
         WHERE r.subject = 'site' AND r.local_id = l.site_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS run_at,
       (SELECT r.applied FROM enrichment_runs r
         WHERE r.subject = 'site' AND r.local_id = l.site_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS applied
  FROM site_stash_box_links l
  JOIN stash_boxes b ON b.id = l.box_id
 WHERE l.site_id = ?
"""

#: The link read of each kind a thing's thread names its boxes from.
_LINKED: Mapping[Subject, str] = {Subject.TAG: _TAG_LINKED, Subject.SITE: _SITE_LINKED}


async def thing_linked(
    database: Database, subject: Subject, local_id: str, viewer: Viewer | None = None
) -> list[Event]:
    """A stash-box that knows this tag or Site, and what it filled in. Named in words: a box has
    no page.

    The subject is handed in because the FIELD NAMES are the record registry's, and the same key
    means a different label on a tag and on a site. Reading them off the wrong subject would put a
    person's word for something on a site's thread.

    "its" and not "their": both kinds this draws are things rather than people. The person reader
    says "their" for the same sentence, which is the one word the two threads differ by.
    """
    rows = await database.fetch_all(_LINKED[subject], (local_id,))
    return [await linked_line(database, subject, local_id, row, "its", viewer) for row in rows]


def runs_not_drawn(linked: Sequence[Event], drawn: Sequence[Event]) -> list[Event]:
    """The link table's box lines that no event of the thread already says, and only those.

    A link table's line is the box's LATEST run, read off the run table, and it stands for every
    run written before the run's writer recorded an event. Where the ledger
    has drawn a press of that box, the latest run IS one of those presses, so the table's line
    would be the same press twice.

    MATCHED ON THE BOX'S ROW, NOT ITS NAME: two configured boxes of one name would hide each other's
    line. Both builders carry `box_id` (`box_filled_in`), and a line with
    no id (an event whose object was never written) matches nothing and hides nothing.
    """
    said = {one.box_id for one in drawn if one.kind == "enriched" and one.box_id}
    return [one for one in linked if one.box_id is None or one.box_id not in said]


def _by_hand(row: Row) -> bool | None:
    """Whether somebody pressed this link, or None where the row does not say.

    Read off `enrichment_runs.automatic` and turned round, because the question a person asks of
    their own library is "did I do this", not "was this automatic", and a column stored one way
    round and read the other is one negation, here, rather than one in every reader.

    Here rather than in either reader, because BOTH of them read it (a person's thread and a
    site's or tag's), and a second copy of one negation is exactly the shape that drifts.
    """
    automatic = row["automatic"]
    return None if automatic is None else not bool(automatic)


#: THE THREE A FILE'S OWN ROWS CAN ACCOUNT FOR, which is why they are left out of the stored list.
#:
#: The fold counts these off `asset_people`, `asset_tags` and `asset_usernames`, where each row
#: wears the word for the pass that wrote it, so the count is taken at the moment somebody reads,
#: and a person taken off the file afterwards is not still claimed. The stored list says what was
#: written at the time and cannot know that. Both are true and they answer different questions;
#: counting the same act twice, from two sources free to disagree, is the one thing that would be
#: wrong.
_ROW_FIELDS = frozenset({"people", "tags", "site", "creator"})


def _filled_said(stored: object) -> list[str] | None:
    """What a box's run recorded filling on the file, each with its article. None where the run
    recorded nothing, which the line says in words rather than guessing."""
    filled = fields_filled(Subject.ASSET, stored)
    return None if filled is None else [f"the {one}" for one in filled]


def _wrote(stored: object) -> tuple[str, ...]:
    """What a box filled in that the file's own rows cannot say, in the words somebody reads.

    Empty where the run recorded no list (every match applied before version 52 of the catalog),
    which keeps those lines exactly as they were. There is no third answer to distinguish here the
    way there is on an entity: a file's line always has the rows to fall back on, so "a plan ran and
    filled nothing" and "nobody wrote it down" both leave the sentence to be made of rows alone.
    """
    # "the title", to match "the site" beside it in `_BOX_WROTE`. This line is a list of THINGS the
    # box wrote and every other item in it carries its article; a bare "title" among them reads as a
    # column name rather than as the thing on the file. The entity threads say the same labels with
    # no article at all, because there the possessive is doing that work: "their birthdate".
    return tuple(f"the {one}" for one in fields_filled(Subject.ASSET, stored, _ROW_FIELDS) or ())


#: WHICH BOX WROTE A ROW, where a stored column can say it, as a SQL expression over a link row
#: aliased `link` (asset_people, asset_tags or asset_usernames).
#:
#: The row's own `box_id` names the box whose answer made it (the note over `asset_people`), and a
#: box removed since names nobody. A row that names no box falls back to the file's APPLIED MATCH in
#: `asset_stash_box_matches`, and only where the file has exactly ONE, which is the same rule the
#: file thread's fold keeps (see `history_folds`) and the rule the column was first filled by: with
#: two applied boxes and nothing on the row, a name picked between them would be a guess dressed
#: as a fact. A NULL is drawn as "A stash-box". One seek on a primary key per row.
BOX_OF_A_ROW = """
CASE WHEN link.source = 'stash_box' THEN (
  CASE WHEN link.box_id IS NOT NULL THEN (
    SELECT b.name FROM stash_boxes b WHERE b.id = link.box_id
  ) ELSE (
    SELECT CASE WHEN COUNT(*) = 1 THEN MIN(b.name) END
      FROM asset_stash_box_matches m
      JOIN stash_boxes b ON b.id = m.box_id
     WHERE m.asset_id = link.asset_id AND m.state = 'applied'
  ) END
) END"""


def files_of_filing(parameter: str, subject: str, row: Mapping[str, Any]) -> str:
    """Where a counted line's number goes: exactly the files that line counted.

    `row` is one group of a counting read (`source`, `box` and `day`, the three columns every one
    of them groups by: `_SITE_FILES`, `_TAG_FILES`, the person's `_NAMED`), and the address is the
    Files wall filtered to that group and nothing wider (`constraints.Filing`). One builder for the
    three threads, so a line on a Site, a tag and a person cannot come to spell the same group three
    ways.

    NOT THE WHOLE SITE, TAG OR PERSON: "Sift filed 4 files under it" opening every file on a site
    would be a number that does not open what it counts. The Files wall can say "by this source, on
    this day", so the number opens its own.
    """
    source = None if row["source"] is None else str(row["source"])
    box = None if row["box"] is None else str(row["box"])
    filing = Filing(
        parameter=parameter,
        subject=subject,
        source=source,
        day=None if row["day"] is None else int(row["day"]),
        box=box if source == BOX_SOURCE else None,
    )
    return f"/browse?{parameter}={quote(filing.value, safe='')}"


#: Which boxes put one person on these files, by the box each filing names, the most files first.
_BOXES_THAT_NAMED = splice(
    """
SELECT {{BOX}} AS box, COUNT(*) AS files
  FROM asset_people link
 WHERE link.asset_id IN (?*) AND link.person_id = ? AND link.source = 'stash_box'
 GROUP BY box
""",
    BOX=BOX_OF_A_ROW,
)
_BOX_TABLES = (
    "SELECT name FROM sqlite_master WHERE type = 'table'"
    " AND name IN ('asset_stash_box_matches', 'stash_boxes')"
)
#: How many files one read of `_BOXES_THAT_NAMED` asks about, well under any bound on parameters.
_FILES_PER_READ = 500


async def boxes_that_named(
    database: Database, person_id: str, asset_ids: Sequence[str]
) -> list[str]:
    """The stash-boxes that put this person on these files, by name, the most files first.

    A filing no box can be named for (the box removed since, or two boxes on a file and nothing on
    the row) is left out, so an empty answer is "a stash-box" said honestly rather than a guess.
    Unscoped: the caller has already chosen the files.
    """
    present = {str(row["name"]) for row in await database.fetch_all(_BOX_TABLES)}
    if not stash_box_tables_in(present):
        return []
    counted: dict[str, int] = {}
    wanted = list(dict.fromkeys(asset_ids))
    for begin in range(0, len(wanted), _FILES_PER_READ):
        query, params = in_clause(_BOXES_THAT_NAMED, wanted[begin : begin + _FILES_PER_READ])
        for row in await database.fetch_all(query, [*params, person_id]):
            if row["box"] is not None:
                name = str(row["box"])
                counted[name] = counted.get(name, 0) + int(row["files"])
    return sorted(counted, key=lambda name: (-counted[name], name.casefold()))


def stash_box_tables_in(present: set[str]) -> bool:
    """Whether the tables `BOX_OF_A_ROW` reads are in this database.

    The threads keep two fixed statements each, one with that expression and one with NULL in its
    place, and pick by this, never a statement built from the fragment, which is the rule query
    text lives under here (semgrep's `sift-no-string-built-sql`). `BOX_OF_A_ROW` above is the one
    copy the four statements are checked against by `tests/gates/test_one_ordering`'s sibling,
    `test_the_box_expression_is_the_same_in_every_thread` (kernel/tests/test_history_ledger).
    """
    return {"asset_stash_box_matches", "stash_boxes"} <= present
