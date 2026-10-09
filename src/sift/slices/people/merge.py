# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two people who turn out to be one.

Every table that points at a person has its own literal pair: move what can move, then sweep what
collided. One entry point and one transaction, whether one person is folded in or six. A merge
cannot be taken back, so `weigh` counts what would move before it happens; the name that goes
becomes an alias, and the survivor's blanks are filled from the person going.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sift.kernel.access import ObjectType, Repository
from sift.kernel.access.default_covers import assign_if_empty
from sift.kernel.access.merged import follow
from sift.kernel.access.sentences import MERGE_NAMES_KEPT
from sift.kernel.changes import About, announce, who_may_see_a_file
from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.log import get_logger
from sift.kernel.records import Kind as RecordKind
from sift.kernel.records import Subject as RecordSubject
from sift.kernel.records import field as record_field
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import Subject

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Moves:
    """One table's share of a merge: what it holds, how to move it, and how to clear the rest.

    A judgement about the whole person is deleted for both; `sweep` is None where rows cannot
    collide.
    """

    what: str
    move: str
    sweep: str | None = None
    count: str = ""
    #: Run before the move, bound (keeping, losing): what the survivor's rows take from the others.
    before: str | None = None


#: Every table that points at a person; `test_merge.py` holds this list to the schema. Stores keyed
#: by an id and a kind word are in `kernel/access/merged.py`.
_TABLES: tuple[_Moves, ...] = (
    _Moves(
        what="files",
        move="UPDATE OR IGNORE asset_people SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM asset_people WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM asset_people WHERE person_id = ?",
        # A file both were on keeps the survivor's row; a person's placement wins over Sift's.
        before=(
            "UPDATE asset_people SET source = NULL WHERE person_id = ? AND source IS NOT NULL "
            "AND asset_id IN (SELECT asset_id FROM asset_people WHERE person_id = ? "
            "AND source IS NULL)"
        ),
    ),
    _Moves(
        what="usernames",
        move="UPDATE usernames SET person_id = ? WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM usernames WHERE person_id = ?",
    ),
    _Moves(
        what="aliases",
        move="UPDATE OR IGNORE people_aliases SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM people_aliases WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM people_aliases WHERE person_id = ?",
    ),
    _Moves(
        what="links",
        move="UPDATE OR IGNORE people_links SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM people_links WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM people_links WHERE person_id = ?",
    ),
    _Moves(
        what="tags",
        move="UPDATE OR IGNORE person_tags SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM person_tags WHERE person_id = ?",
    ),
    _Moves(
        what="faces",
        move="UPDATE face_tracks SET person_id = ? WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM face_tracks WHERE person_id = ?",
    ),
    _Moves(
        what="face references",
        move="UPDATE OR IGNORE face_references SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM face_references WHERE person_id = ?",
    ),
    _Moves(
        what="face confirmations",
        move="UPDATE face_confirmations SET person_id = ? WHERE person_id = ?",
    ),
    _Moves(
        what="face refusals",
        move="UPDATE OR IGNORE face_rejections SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM face_rejections WHERE person_id = ?",
    ),
    # The refusal memory, keyed by person, moves with the person.
    _Moves(
        what="remembered face refusals",
        move="UPDATE face_rejected SET person_id = ? WHERE person_id = ?",
    ),
    # A folder's proposal moves to the survivor; one already made for the survivor wins.
    _Moves(
        what="face group proposals",
        move="UPDATE OR IGNORE face_pile_proposals SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM face_pile_proposals WHERE person_id = ?",
    ),
    _Moves(
        what="faces on files",
        move="UPDATE OR IGNORE face_asset_people SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM face_asset_people WHERE person_id = ?",
    ),
    _Moves(
        what="refusals",
        move="UPDATE OR IGNORE asset_person_refusals SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM asset_person_refusals WHERE person_id = ?",
    ),
    _Moves(
        what="stash-box records",
        move="UPDATE OR IGNORE person_stash_box_links SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM person_stash_box_links WHERE person_id = ?",
    ),
    _Moves(
        what="folder answers",
        move="UPDATE OR IGNORE folder_people SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM folder_people WHERE person_id = ?",
    ),
    _Moves(
        what="folder claims",
        move="UPDATE folder_claims SET person_id = ? WHERE person_id = ?",
    ),
    # A folder taken back from the person going stays taken back from the survivor.
    _Moves(
        what="folders taken back",
        move="UPDATE OR IGNORE folder_refusals SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM folder_refusals WHERE person_id = ?",
    ),
    _Moves(
        what="claimed pack entries",
        move="UPDATE pack_entries SET claimed_person_id = ? WHERE claimed_person_id = ?",
    ),
    # Per user, so the survivor's own opinion wins where both have one.
    _Moves(
        what="opinions",
        move="UPDATE OR IGNORE person_user_state SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM person_user_state WHERE person_id = ?",
    ),
    # A shoot proposed under them; left out it would cascade away.
    _Moves(
        what="proposed shoots",
        move="UPDATE shoot_proposals SET person_id = ? WHERE person_id = ?",
    ),
    # The note that every stash-box picture was refused: the set of pictures changed, so both go and
    # the survivor is offered again.
    _Moves(
        what="refused starter pictures",
        move="DELETE FROM face_starter_refusals WHERE person_id IN (?, ?)",
    ),
    # A download keeps its person by id, so it follows them.
    _Moves(
        what="downloads",
        move="UPDATE downloads SET person_id = ? WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM downloads WHERE person_id = ?",
    ),
    # Stores naming a person by id and kind: `kernel/access/merged.py`, shared with Sites.
)

#: What the person going knew that the survivor does not, moved before the row is deleted.
#:
#: Fill what is blank, never overwrite what is filled; the writer stores an emptied box as NULL, so
#: `COALESCE` is enough.
_FILL_BLANKS_FROM = """
UPDATE people
   SET disambiguation     = COALESCE(disambiguation,     (SELECT disambiguation     FROM people WHERE id = :losing)),
       gender             = COALESCE(gender,             (SELECT gender             FROM people WHERE id = :losing)),
       birth_date         = COALESCE(birth_date,         (SELECT birth_date         FROM people WHERE id = :losing)),
       country            = COALESCE(country,            (SELECT country            FROM people WHERE id = :losing)),
       ethnicity          = COALESCE(ethnicity,          (SELECT ethnicity          FROM people WHERE id = :losing)),
       eye_color          = COALESCE(eye_color,          (SELECT eye_color          FROM people WHERE id = :losing)),
       hair_color         = COALESCE(hair_color,         (SELECT hair_color         FROM people WHERE id = :losing)),
       height_cm          = COALESCE(height_cm,          (SELECT height_cm          FROM people WHERE id = :losing)),
       measurements       = COALESCE(measurements,       (SELECT measurements       FROM people WHERE id = :losing)),
       breast_type        = COALESCE(breast_type,        (SELECT breast_type        FROM people WHERE id = :losing)),
       career_start_year  = COALESCE(career_start_year,  (SELECT career_start_year  FROM people WHERE id = :losing)),
       career_end_year    = COALESCE(career_end_year,    (SELECT career_end_year    FROM people WHERE id = :losing)),
       tattoos            = COALESCE(tattoos,            (SELECT tattoos            FROM people WHERE id = :losing)),
       piercings          = COALESCE(piercings,          (SELECT piercings          FROM people WHERE id = :losing)),
       notes              = COALESCE(notes,              (SELECT notes              FROM people WHERE id = :losing))
 WHERE id = :keeping
"""

#: The cover, moved as a pair and only onto a survivor with none, and only a cover somebody chose;
#: the rule gives the survivor its own afterwards (`assign_if_empty`).
_TAKE_COVER_FROM = """
UPDATE people
   SET cover_asset_id = (SELECT cover_asset_id FROM people WHERE id = :losing),
       cover_track_id = (SELECT cover_track_id FROM people WHERE id = :losing),
       cover_at_ms = (SELECT cover_at_ms FROM people WHERE id = :losing),
       cover_frame = (SELECT cover_frame FROM people WHERE id = :losing)
 WHERE id = :keeping
   AND cover_asset_id IS NULL
   AND cover_upload_id IS NULL
   AND (SELECT cover_asset_id FROM people WHERE id = :losing) IS NOT NULL
   AND NOT EXISTS (
         SELECT 1 FROM people gone
          WHERE gone.id = :losing AND gone.cover_by_default IS NOT NULL
            AND gone.cover_asset_id = gone.cover_by_default)
"""

#: The survivor's record columns in `_FILL_BLANKS_FROM` order, with the record's word for each;
#: `test_merge.py` holds the two spellings together.
_RECORD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("disambiguation", "disambiguation"),
    ("gender", "gender"),
    ("birth_date", "birth_date"),
    ("country", "country"),
    ("ethnicity", "ethnicity"),
    ("eye_color", "eye_color"),
    ("hair_color", "hair_color"),
    ("height_cm", "height_cm"),
    ("measurements", "measurements"),
    ("breast_type", "breast_type"),
    ("career_start_year", "career_start_year"),
    ("career_end_year", "career_end_year"),
    ("tattoos", "tattoos"),
    ("piercings", "piercings"),
    ("notes", "details"),
)

#: One person's record as the weigh compares it, with the cover as one thing.
_RECORD_ROW = """
SELECT name, disambiguation, gender, birth_date, country, ethnicity, eye_color, hair_color,
       height_cm, measurements, breast_type, career_start_year, career_end_year, tattoos,
       piercings, notes, cover_asset_id, cover_by_default
  FROM people WHERE id = ?
"""

#: How many of each list the weigh names; the count is always whole.
NAMED_AT_MOST = 50

#: The usernames that move, with their sites, in stored-key order (`kernel.sorting`).
_USERNAMES_NAMED = """
SELECT u.name AS name, s.name AS site
  FROM usernames u LEFT JOIN sites s ON s.id = u.site_id
 WHERE u.person_id = ?
 ORDER BY COALESCE(u.name_sort, u.name), COALESCE(s.name_sort, s.name), u.id
 LIMIT ?
"""

#: In the order the person's own page lists them (`service._ALIASES_OF_PERSON`).
_ALIASES_NAMED = """
SELECT alias AS name FROM people_aliases WHERE person_id = ?
 ORDER BY COALESCE(alias_sort, alias), id
 LIMIT ?
"""

#: A saved link by its Site where known, else its label or address, in id order.
_LINKS_NAMED = """
SELECT COALESCE(s.name, l.label, l.url) AS name, l.url AS url
  FROM people_links l LEFT JOIN sites s ON s.id = l.site_id
 WHERE l.person_id = ?
 ORDER BY l.id
 LIMIT ?
"""

#: The tags that come over: the ones only the person going carries.
_TAGS_BROUGHT = """
SELECT COUNT(*) AS n FROM person_tags
 WHERE person_id = ? AND tag_id NOT IN (SELECT tag_id FROM person_tags WHERE person_id = ?)
"""
_TAGS_BROUGHT_NAMED = """
SELECT t.name AS name FROM person_tags pt JOIN tags t ON t.id = pt.tag_id
 WHERE pt.person_id = ? AND pt.tag_id NOT IN (SELECT tag_id FROM person_tags WHERE person_id = ?)
 ORDER BY COALESCE(t.name_sort, t.name), t.id
 LIMIT ?
"""

#: A paragraph-long value is cut here; the whole text is on the record.
_VALUE_AT_MOST = 120


_NAME = "SELECT name FROM people WHERE id = ?"
#: The loser's name as an alias on the keeper, added at the moment of the merge.
_ADD_ALIAS = (
    "INSERT OR IGNORE INTO people_aliases (id, person_id, alias, alias_sort, added_at)"
    " VALUES (?, ?, ?, ?, ?)"
)
_ALIAS_HELD = "SELECT 1 FROM people_aliases WHERE person_id = ? AND alias = ? COLLATE NOCASE"
_REMOVE = "DELETE FROM people WHERE id = ? RETURNING id"


@dataclass(frozen=True, slots=True)
class Named:
    """One thing a merge moves, by name, and whose it was."""

    name: str
    #: Where it is, where that is a second word it is known by: a username's site.
    where: str | None
    #: The one going it belonged to, by name.
    whose: str


@dataclass(frozen=True, slots=True)
class Counted:
    """How many of something one of the people going holds: their confirmed faces."""

    whose: str
    count: int


@dataclass(frozen=True, slots=True)
class Filled:
    """One box on the survivor's record that the merge fills; `value` is empty for the cover."""

    key: str
    label: str
    value: str
    whose: str


def fill_value(kind: RecordKind | None, value: object) -> str:
    """A stored value as the sheet says it, by the kind the registry declares; shared with the site
    merge.
    """
    said = str(value)
    if kind is RecordKind.NAMES:
        try:
            listed = json.loads(said)
        except ValueError:
            listed = None
        if isinstance(listed, list):
            said = ", ".join(str(one) for one in listed)
    elif kind is RecordKind.LENGTH:
        said = f"{value} cm"
    if len(said) > _VALUE_AT_MOST:
        return said[: _VALUE_AT_MOST - 1].rstrip() + "\u2026"
    return said


@dataclass(frozen=True, slots=True)
class Weighed:
    """What a merge would move, counted before anything moves: the whole of the guard."""

    from_name: str
    into_name: str
    files: int
    usernames: int
    aliases: int
    links: int
    faces: int
    #: How many facts the person going knows that the survivor lacks: the row would take them.
    facts: int = 0
    #: The same moves BY NAME, each list bounded by `NAMED_AT_MOST` while its count is whole.
    usernames_named: tuple[Named, ...] = ()
    aliases_named: tuple[Named, ...] = ()
    links_named: tuple[Named, ...] = ()
    #: Whose confirmed faces move, and how many of each person's.
    faces_from: tuple[Counted, ...] = ()
    #: Every blank on the survivor the merge fills, with the value and whose it was.
    filled: tuple[Filled, ...] = ()


async def _named(
    database: Database, statement: str, one: str, whose: str, *, where: str | None = None
) -> tuple[Named, ...]:
    rows = await database.fetch_all(statement, (one, NAMED_AT_MOST))
    return tuple(
        Named(
            name=str(row["name"]),
            where=None if where is None or row[where] is None else str(row[where]),
            whose=whose,
        )
        for row in rows
    )


async def _filled(
    database: Database, *, going: Sequence[str], keeping: str
) -> tuple[Filled, ...] | None:
    """Every blank on the survivor that folding `going` in, in that order, would fill.

    The first one named fills a box both know; None if any id names nobody.
    """
    kept = await database.fetch_one(_RECORD_ROW, (keeping,))
    if kept is None:
        return None
    rows = []
    for one in going:
        row = await database.fetch_one(_RECORD_ROW, (one,))
        if row is None:
            return None
        rows.append(row)
    filled: list[Filled] = []
    for column, key in _RECORD_COLUMNS:
        if kept[column] is not None:
            continue
        giver = next((row for row in rows if row[column] is not None), None)
        if giver is None:
            continue
        declared = record_field(RecordSubject.PERSON, key)
        filled.append(
            Filled(
                key=key,
                label=declared.label if declared is not None else key.replace("_", " "),
                value=fill_value(declared.kind if declared is not None else None, giver[column]),
                whose=str(giver["name"]),
            )
        )
    # The cover as one thing, from the first of them that chose one, as `_TAKE_COVER_FROM` does.
    if kept["cover_asset_id"] is None:
        giver = next(
            (
                row
                for row in rows
                if row["cover_asset_id"] is not None
                and row["cover_asset_id"] != row["cover_by_default"]
            ),
            None,
        )
        if giver is not None:
            filled.append(
                Filled(key="cover", label="Cover picture", value="", whose=str(giver["name"]))
            )
    return tuple(filled)


async def weigh(database: Database, *, losing: str, keeping: str) -> Weighed | None:
    """Count what merging one person into another would move; None when either id names nobody."""
    names = {}
    for person_id in (losing, keeping):
        row = await database.fetch_one(_NAME, (person_id,))
        if row is None:
            return None
        names[person_id] = str(row["name"])

    counted: dict[str, int] = {}
    for table in _TABLES:
        if table.count:
            # Unpacked: every one is a bare COUNT.
            (row,) = await database.fetch_all(table.count, (losing,))
            counted[table.what] = int(row["n"])
    whose = names[losing]
    filled = await _filled(database, going=[losing], keeping=keeping) or ()
    faces = counted.get("faces", 0)
    return Weighed(
        from_name=whose,
        into_name=names[keeping],
        files=counted.get("files", 0),
        usernames=counted.get("usernames", 0),
        aliases=counted.get("aliases", 0),
        links=counted.get("links", 0),
        faces=faces,
        facts=len(filled),
        usernames_named=await _named(database, _USERNAMES_NAMED, losing, whose, where="site"),
        aliases_named=await _named(database, _ALIASES_NAMED, losing, whose),
        links_named=await _named(database, _LINKS_NAMED, losing, whose),
        faces_from=(Counted(whose=whose, count=faces),) if faces else (),
        filled=filled,
    )


async def _one_into(
    connection: Connection, *, losing: str, keeping: str, name: str
) -> list[object]:
    """One person folded into another, inside a transaction somebody else opened."""
    for table in _TABLES:
        if table.before is not None:
            await connection.execute(table.before, (keeping, losing))
        await connection.execute(table.move, (keeping, losing))
        if table.sweep is not None:
            await connection.execute(table.sweep, (losing,))
    # Before the delete, in the same transaction: no table above moves the row's own facts.
    where = {"keeping": keeping, "losing": losing}
    await connection.execute(_FILL_BLANKS_FROM, where)
    await connection.execute(_TAKE_COVER_FROM, where)
    # A survivor still without a cover gets its first file's, after the carry.
    await assign_if_empty(connection, "person", keeping)
    await follow(connection, kind="person", losing=losing, keeping=keeping, name=name)
    await _keep_the_name(connection, name=name, on=keeping)
    return list(await connection.execute_fetchall(_REMOVE, (losing,)))


async def weigh_many(database: Database, *, losing: Sequence[str], keeping: str) -> Weighed | None:
    """What folding several people into one would move, added up.

    None if any id names nobody, or if the survivor is among the ones going.
    """
    going = [one for one in dict.fromkeys(losing) if one != keeping]
    if not going:
        return None
    into = await database.fetch_one(_NAME, (keeping,))
    if into is None:
        return None
    total = Weighed(
        from_name="", into_name=str(into["name"]), files=0, usernames=0, aliases=0, links=0, faces=0
    )
    for one in going:
        weighed = await weigh(database, losing=one, keeping=keeping)
        if weighed is None:
            return None
        total = Weighed(
            from_name=weighed.from_name if not total.from_name else total.from_name,
            into_name=total.into_name,
            files=total.files + weighed.files,
            usernames=total.usernames + weighed.usernames,
            aliases=total.aliases + weighed.aliases,
            links=total.links + weighed.links,
            faces=total.faces + weighed.faces,
            usernames_named=(total.usernames_named + weighed.usernames_named)[:NAMED_AT_MOST],
            aliases_named=(total.aliases_named + weighed.aliases_named)[:NAMED_AT_MOST],
            links_named=(total.links_named + weighed.links_named)[:NAMED_AT_MOST],
            faces_from=total.faces_from + weighed.faces_from,
        )
    # The fills over the whole set; see `_filled`.
    filled = await _filled(database, going=going, keeping=keeping)
    if filled is None:  # pragma: no cover (every id was resolved by `weigh` a moment ago)
        return None
    return replace(total, facts=len(filled), filled=filled)


async def merge_many(
    database: Database,
    access: Repository,
    *,
    losing: Sequence[str],
    keeping: str,
    actor: Actor,
) -> Weighed | None:
    """Fold several people into one, in one transaction, so the set is taken whole or not at all.

    Folded in the order given, so the first one named wins a blank. The counts come back added up.
    """
    going = [one for one in dict.fromkeys(losing) if one != keeping]
    if not going:
        return None
    weighed = await weigh_many(database, losing=going, keeping=keeping)
    if weighed is None:
        return None

    # Before the transaction: forgetting a grant only removes access.
    for one in going:
        await access.forget_object(ObjectType.PERSON, one)

    names = {}
    for one in going:
        row = await database.fetch_one(_NAME, (one,))
        if row is None:  # pragma: no cover (weighed above resolved every one of them)
            return None
        names[one] = str(row["name"])
    brought = await _what_each_brings(database, going=going, keeping=keeping)
    if brought is None:  # pragma: no cover (weighed above resolved every one of them)
        return None

    async with database.write() as connection:
        for one in going:
            if not await _one_into(connection, losing=one, keeping=keeping, name=names[one]):
                # Raising rolls the whole set back; returning would commit part of it.
                raise _PartialMerge(one)
            # One event per person folded in, the loser as subject, since nobody can look them up
            # afterwards. What came over rides in the payload.
            await record_event(
                connection,
                actor=actor,
                verb="merged",
                subject=Subject(kind="person", id=one, name=names[one]),
                object=Object(kind="person", id=keeping, name=weighed.into_name),
                payload=brought[one],
            )
        # Told on the commit, to everyone a change to a file could concern (`who_may_see_a_file`): a
        # merge moves files between people.
        announce(await who_may_see_a_file(connection), About.LIBRARY)

    log.info("people.merged_many", people=len(going), files=weighed.files)
    return weighed


async def _what_each_brings(
    database: Database, *, going: Sequence[str], keeping: str
) -> dict[str, str] | None:
    """What each person going brings to the survivor, as their `merged` event's payload.

    Credited in fold order, and named as well as counted (`MERGE_NAMES_KEPT`). None for an unknown
    id.
    """
    brought: dict[str, str] = {}
    filled_before: set[str] = set()
    for at, one in enumerate(going):
        each = await weigh(database, losing=one, keeping=keeping)
        upto = await _filled(database, going=going[: at + 1], keeping=keeping)
        if each is None or upto is None:
            return None
        filled = [one_fill.key for one_fill in upto if one_fill.key not in filled_before]
        filled_before.update(filled)
        (tagged,) = await database.fetch_all(_TAGS_BROUGHT, (one, keeping))
        tags = await database.fetch_all(_TAGS_BROUGHT_NAMED, (one, keeping, MERGE_NAMES_KEPT))

        def kept(named: Sequence[Named]) -> list[dict[str, str | None]]:
            return [{"name": n.name, "where": n.where} for n in named[:MERGE_NAMES_KEPT]]

        brought[one] = json.dumps(
            {
                "files": each.files,
                "usernames": each.usernames,
                "aliases": each.aliases,
                "links": each.links,
                "tags": int(tagged["n"]),
                "faces": each.faces,
                "filled": filled,
                "named": {
                    "usernames": kept(each.usernames_named),
                    "aliases": kept(each.aliases_named),
                    "links": kept(each.links_named),
                    "tags": [{"name": str(row["name"]), "where": None} for row in tags],
                },
            },
            sort_keys=True,
        )
    return brought


class _PartialMerge(RuntimeError):
    """One of a set could not be removed, so none of them is; the ids were resolved a moment earlier."""


async def _keep_the_name(connection: Connection, *, name: str, on: str) -> None:
    """Write the name that is going onto the survivor, unless they already answer to it.

    The survivor's own name is not in the alias table, so it is checked here.
    """
    row = await (await connection.execute(_NAME, (on,))).fetchone()
    if row is not None and str(row["name"]).casefold() == name.casefold():
        return
    held = await (await connection.execute(_ALIAS_HELD, (on, name))).fetchone()
    if held is not None:
        return
    await connection.execute(_ADD_ALIAS, (new_id(), on, name, sort_key(name), int(time.time())))


def tables_that_move() -> frozenset[str]:
    """Every table a merge touches, read out of the statements, for the check against the schema."""
    return frozenset(
        one.move.split("UPDATE OR IGNORE ")[-1]
        .split("UPDATE ")[-1]
        .split("DELETE FROM ")[-1]
        .split()[0]
        for one in _TABLES
    )
