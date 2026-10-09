# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two sites that turn out to be one: a sibling of `merge.py`, each table with its own literal pair.

A site can be a site's parent, so a self-parent is cleared; two sealed logins refuse the merge,
since it cannot be taken back; a colliding username has its files and blanks folded into its twin
before it goes. The going name becomes an alias.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sift.kernel.access import ObjectType, Repository
from sift.kernel.access.merged import follow
from sift.kernel.access.sites import site_address
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database, Params
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.log import get_logger
from sift.kernel.records import Subject as RecordSubject
from sift.kernel.records import field as record_field
from sift.kernel.sorting import sort_key
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import Subject
from sift.slices.people.merge import NAMED_AT_MOST, Filled, Named, fill_value

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Moves:
    """One table's share of a merge; `sweep` is None where its rows cannot collide."""

    what: str
    move: str
    sweep: str | None = None
    count: str = ""


#: Every table that points at a site, except usernames, logins and `sites.parent_id`, which are not
#: plain moves; `test_site_merge.py` holds this list to the schema.
_TABLES: tuple[_Moves, ...] = (
    _Moves(
        what="other names",
        move="UPDATE OR IGNORE site_aliases SET site_id = ? WHERE site_id = ?",
        sweep="DELETE FROM site_aliases WHERE site_id = ?",
        count="SELECT COUNT(*) AS n FROM site_aliases WHERE site_id = ?",
    ),
    _Moves(
        what="addresses",
        move="UPDATE OR IGNORE site_links SET site_id = ? WHERE site_id = ?",
        sweep="DELETE FROM site_links WHERE site_id = ?",
        count="SELECT COUNT(*) AS n FROM site_links WHERE site_id = ?",
    ),
    _Moves(
        what="tags",
        move="UPDATE OR IGNORE site_tags SET site_id = ? WHERE site_id = ?",
        sweep="DELETE FROM site_tags WHERE site_id = ?",
    ),
    _Moves(
        what="stash-box records",
        move="UPDATE OR IGNORE site_stash_box_links SET site_id = ? WHERE site_id = ?",
        sweep="DELETE FROM site_stash_box_links WHERE site_id = ?",
    ),
    # Links to a person found on this site; nothing can collide.
    _Moves(
        what="people's links",
        move="UPDATE people_links SET site_id = ? WHERE site_id = ?",
    ),
    # Per user, so the survivor's own opinion wins where both have one.
    _Moves(
        what="opinions",
        move="UPDATE OR IGNORE site_user_state SET site_id = ? WHERE site_id = ?",
        sweep="DELETE FROM site_user_state WHERE site_id = ?",
    ),
    # What is kept by id follows the Site; the site key above all, or the next download would make
    # the merged-away Site again.
    _Moves(
        what="downloads",
        move="UPDATE downloads SET site_id = ? WHERE site_id = ?",
        count="SELECT COUNT(*) AS n FROM downloads WHERE site_id = ?",
    ),
    _Moves(
        what="download site keys",
        move="UPDATE download_sites SET site_id = ? WHERE site_id = ?",
    ),
    _Moves(
        what="watermark readings",
        move="UPDATE watermark_reads SET site_id = ? WHERE site_id = ?",
    ),
)

# --- the usernames, which are the part that can lose something ----------------------------------

#: Every username on the losing site with a twin on the survivor, by name or by number, the twin
#: chosen by `MIN` so the fold is repeatable.
_TWINNED = """
SELECT gone.id AS gone_id, MIN(keep.id) AS keep_id
  FROM usernames gone
  JOIN usernames keep
    ON keep.site_id = :keeping
   AND (keep.name = gone.name
        OR (keep.number IS NOT NULL
            AND keep.number = gone.number))
 WHERE gone.site_id = :losing
 GROUP BY gone.id
"""

#: What the losing username knew that its twin does not, filling blanks only; the number travels
#: with how it was learned.
_FILL_TWIN = """
UPDATE usernames
   SET person_id    = COALESCE(person_id,    (SELECT gone.person_id    FROM usernames gone WHERE gone.id = :gone)),
       display_name = COALESCE(display_name, (SELECT gone.display_name FROM usernames gone WHERE gone.id = :gone)),
       url          = COALESCE(url,          (SELECT gone.url          FROM usernames gone WHERE gone.id = :gone)),
       number_via    = CASE WHEN number IS NULL
                            THEN (SELECT gone.number_via FROM usernames gone WHERE gone.id = :gone)
                            ELSE number_via END,
       number_agreed = CASE WHEN number IS NULL
                            THEN (SELECT gone.number_agreed FROM usernames gone WHERE gone.id = :gone)
                            ELSE number_agreed END,
       number        = COALESCE(number,     (SELECT gone.number     FROM usernames gone WHERE gone.id = :gone))
 WHERE id = :keep
"""

#: The files from the losing username, moved onto its twin; `OR IGNORE` for a file on both.
_MOVE_ATTRIBUTIONS = "UPDATE OR IGNORE asset_usernames SET username_id = ? WHERE username_id = ?"

_DROP_USERNAME = "DELETE FROM usernames WHERE id = ?"

#: What is left once every twin has been folded: usernames with no counterpart, which simply move.
_MOVE_USERNAMES = "UPDATE usernames SET site_id = ? WHERE site_id = ?"

#: How many usernames move; the row with no name (a filing's "poster unknown") is not one.
_COUNT_USERNAMES = "SELECT COUNT(*) AS n FROM usernames WHERE site_id = ? AND name <> ''"

#: How many files change hands, through their usernames.
_COUNT_FILES = """
SELECT COUNT(DISTINCT au.asset_id) AS n
  FROM asset_usernames au
  JOIN usernames u ON u.id = au.username_id
 WHERE u.site_id = ?
"""

# --- the site's own row -------------------------------------------------------------------------

#: What the site going knew that the survivor lacks; `parent_id` sets the order (`_one_into`).
_FILL_BLANKS_FROM = """
UPDATE sites
   SET kind      = COALESCE(kind,      (SELECT kind      FROM sites WHERE id = :losing)),
       notes     = COALESCE(notes,     (SELECT notes     FROM sites WHERE id = :losing)),
       parent_id = COALESCE(parent_id, (SELECT parent_id FROM sites WHERE id = :losing))
 WHERE id = :keeping
"""

#: The cover, its frame and `cover_by_default` moved as one set, and only onto a survivor with none.
_TAKE_COVER_FROM = """
UPDATE sites
   SET cover_asset_id  = (SELECT cover_asset_id  FROM sites WHERE id = :losing),
       cover_at_ms     = (SELECT cover_at_ms     FROM sites WHERE id = :losing),
       cover_upload_id = (SELECT cover_upload_id FROM sites WHERE id = :losing),
       cover_frame     = (SELECT cover_frame     FROM sites WHERE id = :losing),
       cover_by_default = (SELECT cover_by_default FROM sites WHERE id = :losing)
 WHERE id = :keeping
   AND cover_asset_id IS NULL
   AND cover_upload_id IS NULL
   AND ((SELECT cover_asset_id FROM sites WHERE id = :losing) IS NOT NULL
        OR (SELECT cover_upload_id FROM sites WHERE id = :losing) IS NOT NULL)
"""

#: The survivor's record columns the weigh names, in fill order, with the record's key for each.
#:
#: `kind` is filled and not named: no field shows it. The address is the first link, filled by the
#: links moving (`_LINKS_GO_AFTER`). Held to the statement by `test_site_merge.py`.
_RECORD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("site_url", "address"),
    ("notes", "details"),
    ("parent_id", "parent"),
)

_RECORD_ROW = splice(
    """
SELECT s.name AS name, {{SITE_ADDRESS}} AS site_url, s.notes AS notes, s.parent_id AS parent_id,
       p.name AS parent_name, s.cover_asset_id AS cover_asset_id,
       s.cover_upload_id AS cover_upload_id
  FROM sites s LEFT JOIN sites p ON p.id = s.parent_id
 WHERE s.id = ?
""",
    SITE_ADDRESS=site_address("s"),
)

#: The going site's addresses land after the survivor's, under fresh ids, so the survivor keeps its
#: address and only one without takes the going site's first.
_LINKS_OF_GOING = "SELECT id FROM site_links WHERE site_id = ? ORDER BY id"
_REID_LINK = "UPDATE site_links SET id = ? WHERE id = ?"

#: Named in stored-key order (`kernel.sorting`).
_USERNAMES_NAMED = """
SELECT name FROM usernames WHERE site_id = ? AND name <> ''
 ORDER BY COALESCE(name_sort, name), id LIMIT ?
"""
_ALIASES_NAMED = """
SELECT alias AS name FROM site_aliases WHERE site_id = ?
 ORDER BY COALESCE(alias_sort, alias), id LIMIT ?
"""
_LINKS_NAMED = """
SELECT url AS name FROM site_links WHERE site_id = ? ORDER BY url LIMIT ?
"""
#: The labels published under the site going, never the survivor itself, counted and named alike.
_CHILDREN_NAMED = """
SELECT name FROM sites WHERE parent_id = :losing AND id != :keeping
 ORDER BY COALESCE(name_sort, name), id LIMIT :most
"""
_COUNT_CHILDREN = "SELECT COUNT(*) AS n FROM sites WHERE parent_id = :losing AND id != :keeping"

#: The label a site's record box is drawn under, where the record declares one.
_LABEL_INSTEAD = {"address": "Web address"}


#: The labels of the site going, moved to the survivor before its own parent is looked at.
_REPARENT_CHILDREN = "UPDATE sites SET parent_id = ? WHERE parent_id = ?"

#: A site cannot be its own parent: cleared, since either direction of merge can make one.
_CLEAR_SELF_PARENT = "UPDATE sites SET parent_id = NULL WHERE id = ? AND parent_id = ?"

#: A folder suggestion names a site by text, so it is rewritten rather than moved by a key.
_RENAME_CLAIMS = "UPDATE folder_claims SET site = ? WHERE site = ? AND kind = 'site'"

_NAME = "SELECT name FROM sites WHERE id = ?"
_HAS_CONNECTION = "SELECT 1 FROM site_connections WHERE site_id = ? LIMIT 1"
_MOVE_CONNECTION = "UPDATE site_connections SET site_id = ? WHERE site_id = ?"
#: The loser's name as an alias on the keeper, added at the moment of the merge.
_ADD_ALIAS = (
    "INSERT OR IGNORE INTO site_aliases (id, site_id, alias, alias_sort, added_at)"
    " VALUES (?, ?, ?, ?, ?)"
)
_ALIAS_HELD = "SELECT 1 FROM site_aliases WHERE site_id = ? AND alias = ? COLLATE NOCASE"
_REMOVE = "DELETE FROM sites WHERE id = ? RETURNING id"


class BothHaveALogin(Exception):
    """Two sites being merged both hold a sealed login, and only one can survive."""

    def __init__(self, going: str, keeping: str) -> None:
        super().__init__(f"{going} and {keeping} both hold a login")
        self.going = going
        self.keeping = keeping


@dataclass(frozen=True, slots=True)
class Weighed:
    """What a merge would move, counted before anything moves; `usernames` carry the files."""

    from_name: str
    into_name: str
    files: int
    usernames: int
    aliases: int
    links: int
    #: Labels published under the site going, which change networks rather than disappear.
    children: int = 0
    facts: int = 0
    #: The same moves by name, bounded by `NAMED_AT_MOST` while each count stays whole.
    usernames_named: tuple[Named, ...] = ()
    aliases_named: tuple[Named, ...] = ()
    links_named: tuple[Named, ...] = ()
    children_named: tuple[Named, ...] = ()
    filled: tuple[Filled, ...] = ()


async def _count(database: Database, statement: str, site_id: str) -> int:
    # Unpacked: every one is a bare COUNT.
    (row,) = await database.fetch_all(statement, (site_id,))
    return int(row["n"])


async def _named(
    database: Database, statement: str, params: Params, whose: str, *, where: str | None = None
) -> tuple[Named, ...]:
    rows = await database.fetch_all(statement, params)
    return tuple(Named(name=str(row["name"]), where=where, whose=whose) for row in rows)


async def _filled(
    database: Database, *, going: Sequence[str], keeping: str
) -> tuple[Filled, ...] | None:
    """Every blank on the survivor that folding `going` in, in that order, would fill; None for an
    unknown id.
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
        declared = record_field(RecordSubject.SITE, key)
        label = _LABEL_INSTEAD.get(key) or (declared.label if declared is not None else key)
        value = giver["parent_name"] if column == "parent_id" else giver[column]
        filled.append(
            Filled(
                key=key,
                label=label,
                value=fill_value(
                    declared.kind if declared is not None else None,
                    value if value is not None else "",
                ),
                whose=str(giver["name"]),
            )
        )
    if kept["cover_asset_id"] is None and kept["cover_upload_id"] is None:
        giver = next(
            (
                row
                for row in rows
                if row["cover_asset_id"] is not None or row["cover_upload_id"] is not None
            ),
            None,
        )
        if giver is not None:
            filled.append(
                Filled(key="cover", label="Cover picture", value="", whose=str(giver["name"]))
            )
    return tuple(filled)


async def weigh(database: Database, *, losing: str, keeping: str) -> Weighed | None:
    """Count what merging one site into another would move; None when either id names nothing."""
    names = {}
    for site_id in (losing, keeping):
        row = await database.fetch_one(_NAME, (site_id,))
        if row is None:
            return None
        names[site_id] = str(row["name"])

    counted: dict[str, int] = {}
    for table in _TABLES:
        if table.count:
            counted[table.what] = await _count(database, table.count, losing)
    whose = names[losing]
    pair = {"losing": losing, "keeping": keeping}
    (children,) = await database.fetch_all(_COUNT_CHILDREN, pair)
    filled = await _filled(database, going=[losing], keeping=keeping) or ()
    return Weighed(
        from_name=whose,
        into_name=names[keeping],
        files=await _count(database, _COUNT_FILES, losing),
        usernames=await _count(database, _COUNT_USERNAMES, losing),
        aliases=counted.get("other names", 0),
        links=counted.get("addresses", 0),
        children=int(children["n"]),
        facts=len(filled),
        # A username is named by the site it is on now, the one going.
        usernames_named=await _named(
            database, _USERNAMES_NAMED, (losing, NAMED_AT_MOST), whose, where=whose
        ),
        aliases_named=await _named(database, _ALIASES_NAMED, (losing, NAMED_AT_MOST), whose),
        links_named=await _named(database, _LINKS_NAMED, (losing, NAMED_AT_MOST), whose),
        children_named=await _named(
            database, _CHILDREN_NAMED, {**pair, "most": NAMED_AT_MOST}, whose
        ),
        filled=filled,
    )


async def weigh_many(database: Database, *, losing: Sequence[str], keeping: str) -> Weighed | None:
    """What folding several sites into one would move, added up."""
    going = [one for one in dict.fromkeys(losing) if one != keeping]
    if not going:
        return None
    into = await database.fetch_one(_NAME, (keeping,))
    if into is None:
        return None
    total = Weighed(
        from_name="", into_name=str(into["name"]), files=0, usernames=0, aliases=0, links=0
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
            children=total.children + weighed.children,
            usernames_named=(total.usernames_named + weighed.usernames_named)[:NAMED_AT_MOST],
            aliases_named=(total.aliases_named + weighed.aliases_named)[:NAMED_AT_MOST],
            links_named=(total.links_named + weighed.links_named)[:NAMED_AT_MOST],
            children_named=(total.children_named + weighed.children_named)[:NAMED_AT_MOST],
        )
    filled = await _filled(database, going=going, keeping=keeping)
    if filled is None:  # pragma: no cover (every id was resolved by `weigh` a moment ago)
        return None
    return replace(total, facts=len(filled), filled=filled)


async def _fold_usernames(connection: Connection, *, losing: str, keeping: str) -> None:
    """Fold the losing site's usernames onto the survivor: files first, then the row's blanks, then the
    row, since `asset_usernames` cascades.
    """
    twins = list(
        await connection.execute_fetchall(_TWINNED, {"losing": losing, "keeping": keeping})
    )
    for row in twins:
        gone, keep = str(row["gone_id"]), str(row["keep_id"])
        await connection.execute(_MOVE_ATTRIBUTIONS, (keep, gone))
        await connection.execute(_FILL_TWIN, {"keep": keep, "gone": gone})
        await connection.execute(_DROP_USERNAME, (gone,))
    await connection.execute(_MOVE_USERNAMES, (keeping, losing))


async def _links_go_after(connection: Connection, *, losing: str) -> None:
    """Re-mint the going site's link ids so the move appends them. See `_LINKS_OF_GOING`."""
    for row in list(await connection.execute_fetchall(_LINKS_OF_GOING, (losing,))):
        await connection.execute(_REID_LINK, (new_id(), str(row["id"])))


async def _one_into(
    connection: Connection, *, losing: str, keeping: str, name: str
) -> list[object]:
    """One site folded into another, inside a transaction somebody else opened."""
    where = {"keeping": keeping, "losing": losing}
    # The cover first; the default-cover rule does not reach a Site.
    await connection.execute(_TAKE_COVER_FROM, where)
    await _fold_usernames(connection, losing=losing, keeping=keeping)
    await _links_go_after(connection, losing=losing)
    for table in _TABLES:
        await connection.execute(table.move, (keeping, losing))
        if table.sweep is not None:
            await connection.execute(table.sweep, (losing,))
    await connection.execute(_MOVE_CONNECTION, (keeping, losing))
    # Labels, then the survivor's row, then any self-parent those made.
    await connection.execute(_REPARENT_CHILDREN, (keeping, losing))
    await connection.execute(_FILL_BLANKS_FROM, where)
    await connection.execute(_CLEAR_SELF_PARENT, (keeping, keeping))
    # Stores naming a Site by id and kind: the person merge's list (`kernel/access/merged.py`).
    await follow(connection, kind="site", losing=losing, keeping=keeping, name=name)
    await _keep_the_name(connection, name=name, on=keeping)
    # Folder suggestions are rewritten to the survivor's name.
    into = await (await connection.execute(_NAME, (keeping,))).fetchone()
    if into is not None:  # pragma: no branch (the survivor was resolved before the transaction)
        await connection.execute(_RENAME_CLAIMS, (str(into["name"]), name))
    return list(await connection.execute_fetchall(_REMOVE, (losing,)))


async def merge_many(
    database: Database,
    access: Repository,
    *,
    losing: Sequence[str],
    keeping: str,
    actor: Actor,
) -> Weighed | None:
    """Fold several sites into one, in one transaction; `BothHaveALogin` is checked for the whole set
    first.
    """
    going = [one for one in dict.fromkeys(losing) if one != keeping]
    if not going:
        return None
    weighed = await weigh_many(database, losing=going, keeping=keeping)
    if weighed is None:
        return None

    # The refusal for every one of them, before the transaction opens.
    survivor_has_a_login = await database.fetch_one(_HAS_CONNECTION, (keeping,)) is not None
    for one in going:
        if survivor_has_a_login and await database.fetch_one(_HAS_CONNECTION, (one,)) is not None:
            raise BothHaveALogin(one, keeping)

    # Before the transaction: forgetting a grant only removes access.
    for one in going:
        await access.forget_object(ObjectType.SITE, one)

    names = await _names_of(database, going)
    if names is None:  # pragma: no cover (weighed above resolved every one of them)
        return None
    brought = await _what_each_brings(database, going=going, keeping=keeping)
    if brought is None:  # pragma: no cover (weighed above resolved every one of them)
        return None

    async with telling(database, EVERY_ADMIN, About.LIBRARY) as connection:
        for one in going:
            if not await _one_into(connection, losing=one, keeping=keeping, name=names[one]):
                # Raising rolls the whole set back; returning would commit part of it.
                raise _PartialMerge(one)
            # The site that went is the subject; what came over rides in the payload.
            await record_event(
                connection,
                actor=actor,
                verb="merged",
                subject=Subject(kind="site", id=one, name=names[one]),
                object=Object(kind="site", id=keeping, name=weighed.into_name),
                payload=brought[one],
            )

    log.info("sites.merged_many", sites=len(going), files=weighed.files)
    return weighed


async def _names_of(database: Database, going: Sequence[str]) -> dict[str, str] | None:
    """Each going site's name, by id; None where one names nothing."""
    names = {}
    for one in going:
        row = await database.fetch_one(_NAME, (one,))
        if row is None:  # pragma: no cover (weighed above resolved every one of them)
            return None
        names[one] = str(row["name"])
    return names


async def _what_each_brings(
    database: Database, *, going: Sequence[str], keeping: str
) -> dict[str, str] | None:
    """What each site going brings to the survivor, as its `merged` event's payload; None for an
    unknown id.
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
        brought[one] = json.dumps(
            {
                "files": each.files,
                "usernames": each.usernames,
                "aliases": each.aliases,
                "links": each.links,
                "children": each.children,
                "filled": filled,
            },
            sort_keys=True,
        )
    return brought


class _PartialMerge(RuntimeError):
    """One of a set could not be removed, so none of them is; the ids were resolved a moment earlier."""


async def _keep_the_name(connection: Connection, *, name: str, on: str) -> None:
    """Write the name that is going onto the survivor, unless it already answers to it.

    Checked as a person's is: the survivor's own name is not in the alias table.
    """
    row = await (await connection.execute(_NAME, (on,))).fetchone()
    # pragma: no cover on the arm below (see the note above): `name` and the survivor's name cannot
    # be equal while the unique constraint on `sites.name` folds case.
    if row is not None and str(row["name"]).casefold() == name.casefold():  # pragma: no cover
        return
    held = await (await connection.execute(_ALIAS_HELD, (on, name))).fetchone()
    if held is not None:
        return
    await connection.execute(_ADD_ALIAS, (new_id(), on, name, sort_key(name), int(time.time())))


def tables_that_move() -> frozenset[str]:
    """Every table this merge touches, read out of the statements, for the check against the schema."""
    moved = {
        one.move.split("UPDATE OR IGNORE ")[-1].split("UPDATE ")[-1].split()[0] for one in _TABLES
    }
    return frozenset(moved | {"usernames", "site_connections", "sites"})
