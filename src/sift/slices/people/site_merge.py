# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two sites that turn out to be one.

The same act a person merge is, on a different subject, and it is a SIBLING of `merge.py` rather
than a generalisation of it, deliberately. That file's own argument applies here twice over: each
table gets its own literal pair so the whole set reads as one thing, and the question a reader has
is "does this cover everything". A shared engine taking a table list as data would answer that
question by asking somebody to read two files together, and it would put the three decisions below
(the ones that exist here and nowhere else) behind a parameter.

## Three things a person merge never had to answer

**A site can be a site's PARENT.** `sites.parent_id` is a self-reference: a network owns
labels, and a label is still a site. So merging a network into one of its own labels can leave the
survivor pointing at itself, and every other label of the network pointing at a row that is gone.
Both are handled below, in that order, and a self-parent is cleared rather than refused: "these
two turned out to be the same site" is the ordinary case and refusing it would be refusing the
feature.

**A site can hold a SEALED LOGIN.** `site_connections` carries the credential for reaching a site,
and only one of two can survive. A merge cannot be taken back, so this is the one case that is
REFUSED outright rather than decided: the numbers on the confirm screen count files and usernames,
and a secret quietly destroyed is the one loss they could not honestly report. Somebody removes one
of the two logins first, which is a thing they can see themselves doing.

**A USERNAME belongs to exactly one site, and two sites can hold the same name.** A person's
tables move with `UPDATE OR IGNORE`, and what will not move is swept, which is safe there because
the swept row holds nothing. It is not safe here: a username is what carries which FILES came from
it, through `asset_usernames`, and that cascades when the username goes. So a colliding username has
its files moved onto its twin and its blanks poured into it before it is removed, and only then do
the rest change hands.

## Everything else is the shape `merge.py` already settled

Weigh before the fact, because a merge cannot be taken back. One transaction for the whole set, so
folding four is an act somebody either took or did not. The name that goes becomes an
also-known-as, so nothing that finds the site now stops finding it, and a site's aliases are in
the search index already, so that is true of search as well as of the wall.
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
    """One table's share of a merge: what it holds, how to move it, and how to clear the rest.

    `sweep` is None for a table whose rows cannot collide, and a statement for every table keyed by
    the site, where the survivor may already hold the row being moved.
    """

    what: str
    move: str
    sweep: str | None = None
    #: How many rows the site being merged away holds. What the confirm screen counts.
    count: str = ""


#: Every table that points at a site, and what a merge does to each, EXCEPT the three above.
#:
#: `usernames`, `site_connections` and `sites.parent_id` are not here because none of them is a
#: plain move: one has to fold twins before it moves, one refuses, and one is the table's own
#: self-reference. Putting them in this list as if they were ordinary is exactly how the dangerous
#: case gets read as the safe one.
#:
#: A table added later that names a site and is not here is a merge that silently leaves rows
#: behind, which is why `test_site_merge.py` reads the schema and holds this list to it.
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
    # Somebody's links to a person that Sift recognised as being ON this site. No uniqueness on the
    # site, so nothing can collide and there is nothing to sweep.
    _Moves(
        what="people's links",
        move="UPDATE people_links SET site_id = ? WHERE site_id = ?",
    ),
    # Hearted, rated, hidden, pinned: per user, so the survivor may already have their own.
    #
    # The survivor's own wins where both have one, which is the right way round and the same rule
    # `merge.py` states: it is this user's opinion of the site they are KEEPING, and replacing it
    # with an opinion about one they have just decided was the same site changes a rating nobody
    # asked to change.
    _Moves(
        what="opinions",
        move="UPDATE OR IGNORE site_user_state SET site_id = ? WHERE site_id = ?",
        sweep="DELETE FROM site_user_state WHERE site_id = ?",
    ),
    # What is kept by id follows the Site. None of the three can collide (a download and a reading
    # each name one Site; a site key is its own row), so each is a plain move.
    #
    # The site KEY matters most: it is how the downloader finds the Site a web site's files are
    # filed under, and its key cascades on delete. Left behind, the merge would delete the row and
    # the next download from that web site would make the merged-away Site again.
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

#: Every username on the losing site that has a twin on the survivor, with the twin's id beside it.
#:
#: Two constraints, not one, and both have to be asked about: `UNIQUE(site_id, name)` on the
#: table, and a partial unique index on `(site_id, number)` for the rows that carry a
#: number. A fold that checked only the name would move a username whose NUMBER already exists on
#: the survivor, and the whole transaction would fail on the constraint, a merge that refuses for
#: a reason nothing on the screen can explain.
#:
#: A JOIN rather than a subquery written out twice, and `MIN` rather than `LIMIT 1`, because one
#: username can match two of the survivor's: its name equal to one and its number equal to
#: another. Grouping picks exactly one of them and picks the SAME one every time, which is what
#: makes the fold below repeatable; the other survivor username is left alone, which is right,
#: because it was never in the way.
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

#: What the losing username knew that its twin does not, poured in before the row goes.
#:
#: The same "fill what is blank, never overwrite what is filled" rule the people merge applies to a
#: person's row, and for the same reason: the twin is about to become the only record of this
#: username, and `person_id` in particular is who somebody said it belongs to. Losing that
#: silently is losing an answer a human gave.
#:
#: The number travels WITH how it was learned: `number_via` and `number_agreed` are taken exactly
#: when `number` is, and never one without the others, or the twin would hold a number with no
#: record of where it came from, or keep its own number beside the loser's provenance. Every
#: `number IS NULL` below reads the row as it was before this UPDATE, so the three agree.
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

#: The files that came from the losing username, moved onto its twin.
#:
#: `OR IGNORE` because the same file may already be attributed to the twin (one row, not two),
#: and the leftover is taken by the cascade when the username below is removed.
_MOVE_ATTRIBUTIONS = "UPDATE OR IGNORE asset_usernames SET username_id = ? WHERE username_id = ?"

_DROP_USERNAME = "DELETE FROM usernames WHERE id = ?"

#: What is left once every twin has been folded: usernames with no counterpart, which simply move.
_MOVE_USERNAMES = "UPDATE usernames SET site_id = ? WHERE site_id = ?"

#: How many USERNAMES move: names somebody is known by on this site.
#:
#: THE ROW WITH NO NAME IS NOT ONE; counted, the sheet would say "Username on Discordapp moves too"
#: with nothing where the name goes. That row is the library's own way of writing "from this site,
#: poster unknown" (`sentences.filed_sentence`): a filing makes it for itself, nobody gave it, and
#: it has no name to say. What it carries is FILES, and those are counted (and said first) by
#: the files line. It still folds onto the survivor's own blank row like any other
#: (`_fold_usernames`); it is only not called a username on the sheet.
_COUNT_USERNAMES = "SELECT COUNT(*) AS n FROM usernames WHERE site_id = ? AND name <> ''"

#: How many FILES change hands. Counted through the usernames, because a site holds no file
#: directly: an asset points at a username and a username points at a site, which is the two-hop
#: shape the whole attribution model is built on.
_COUNT_FILES = """
SELECT COUNT(DISTINCT au.asset_id) AS n
  FROM asset_usernames au
  JOIN usernames u ON u.id = au.username_id
 WHERE u.site_id = ?
"""

# --- the site's own row -------------------------------------------------------------------------

#: What the site going KNEW that the survivor does not.
#:
#: One literal statement rather than SQL built from column names, the same as the people merge: the
#: delete at the end takes the row and everything on it, and a merge cannot be taken back.
#:
#: `parent_id` is in here and it is the reason the order below matters (see `_one_into`).
_FILL_BLANKS_FROM = """
UPDATE sites
   SET kind      = COALESCE(kind,      (SELECT kind      FROM sites WHERE id = :losing)),
       notes     = COALESCE(notes,     (SELECT notes     FROM sites WHERE id = :losing)),
       parent_id = COALESCE(parent_id, (SELECT parent_id FROM sites WHERE id = :losing))
 WHERE id = :keeping
"""

#: The cover, moved as a SET and only onto a survivor that has none.
#:
#: Three columns and one statement, for the reason a person's two are one: a cover is a picture, a
#: moment of that picture and possibly an uploaded file, and taking them independently could produce
#: a cover naming a moment of a file it is not a moment of. The frame is the fourth: the window
#: somebody chose goes with the picture it was chosen on, and it names that picture itself, so it
#: cannot land on any other (`kernel/cover_frame.py frame_of`). `cover_by_default` goes with it,
#: NULL on every Site because the default-cover rule does not reach Sites (catalog 76), so the
#: pair stays one statement's.
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

#: The survivor's record columns the weigh names, in the order `_FILL_BLANKS_FROM` fills them,
#: with the record's key for each: `notes` is kept under its old column name, and `parent_id`
#: is the record's "Part of", said as the parent's NAME rather than its id.
#:
#: !! `kind` IS FILLED AND NOT NAMED. The statement pours it across (the column is in `sites`),
#: but nothing reads it (the record has no field for it), so a line
#: saying a box nobody can see was filled in would be a line about nothing. `site_url` has no
#: field of its own either, and no column since catalog v66: it is the FIRST of the site's links
#: (`sites.SITE_ADDRESS`, read into `_RECORD_ROW` under that name), so it is named in the words the
#: site's record uses for where a site is. It is not filled by `_FILL_BLANKS_FROM` but by the links
#: moving (`_LINKS_GO_AFTER`): a survivor with none takes the going site's first as its address.
#:
#: Held to the statement by `test_site_merge.py`, which fills each of these on the site going and
#: requires the weigh to name exactly the columns the merge then changed.
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

#: THE GOING SITE'S ADDRESSES LAND AFTER THE SURVIVOR'S, never among them.
#:
#: A Site's address is its first link BY ID (`sites.SITE_ADDRESS`), and the move below keeps each
#: link's id, so a going site whose links are older would hand the survivor ITS address, which
#: is the opposite of what a merge promises (the survivor keeps what it has; only a blank is
#: filled). Given fresh ids first, in their own order, they sort after everything the survivor
#: holds: a survivor with an address keeps it, one without takes the going site's first.
_LINKS_OF_GOING = "SELECT id FROM site_links WHERE site_id = ? ORDER BY id"
_REID_LINK = "UPDATE site_links SET id = ? WHERE id = ?"

#: Named in the order of their stored keys (`kernel.sorting`), so an accented name is named where
#: it is filed.
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
#: The labels published under the site going, never the SURVIVOR itself, which may be one of them
#: (a network merged into its own label) and is then not "published under the survivor instead":
#: its self-parent is cleared. Counted and named by the same predicate so the two agree.
_CHILDREN_NAMED = """
SELECT name FROM sites WHERE parent_id = :losing AND id != :keeping
 ORDER BY COALESCE(name_sort, name), id LIMIT :most
"""
_COUNT_CHILDREN = "SELECT COUNT(*) AS n FROM sites WHERE parent_id = :losing AND id != :keeping"

#: The label a site's record box is drawn under, where the record declares one.
_LABEL_INSTEAD = {"address": "Web address"}


#: The labels published under the site going, moved to the survivor.
#:
#: Before the survivor's own parent is looked at, and that order is the whole of it: a label whose
#: network has gone is a label, and one pointing at a deleted row is a broken record.
_REPARENT_CHILDREN = "UPDATE sites SET parent_id = ? WHERE parent_id = ?"

#: A site cannot be its own parent.
#:
#: Reachable two ways and both are ordinary: merging a network into one of its own labels makes the
#: label its own parent through the line above, and merging a label into its network does it through
#: `_FILL_BLANKS_FROM`. Cleared rather than refused: "these two are the same site" is exactly what
#: somebody is saying, and there is no parent left to name.
_CLEAR_SELF_PARENT = "UPDATE sites SET parent_id = NULL WHERE id = ? AND parent_id = ?"

#: A folder suggestion naming this site by NAME rather than by id.
#:
#: `folder_claims.site` is text (the word every filename in a folder opens with), so nothing
#: about it cascades and nothing about it moves on a foreign key. A merge that left it alone would
#: leave the Organize screen proposing a site that no longer exists.
_RENAME_CLAIMS = "UPDATE folder_claims SET site = ? WHERE site = ? AND kind = 'site'"

_NAME = "SELECT name FROM sites WHERE id = ?"
_HAS_CONNECTION = "SELECT 1 FROM site_connections WHERE site_id = ? LIMIT 1"
_MOVE_CONNECTION = "UPDATE site_connections SET site_id = ? WHERE site_id = ?"
#: The loser's name, kept as an alias on the keeper. `added_at` is the moment of the MERGE, for the
#: reason the same statement gives on a person: the keeper did not go by this name until now.
_ADD_ALIAS = (
    "INSERT OR IGNORE INTO site_aliases (id, site_id, alias, alias_sort, added_at)"
    " VALUES (?, ?, ?, ?, ?)"
)
_ALIAS_HELD = "SELECT 1 FROM site_aliases WHERE site_id = ? AND alias = ? COLLATE NOCASE"
_REMOVE = "DELETE FROM sites WHERE id = ? RETURNING id"


class BothHaveALogin(Exception):
    """Two sites being merged both hold a sealed login, and only one can survive.

    Its own exception rather than a None, because it is the one refusal here that a person can act
    on: every other way a merge fails to happen is "one of these is not a site I can show you",
    which is a 404 with nothing to do about it. This one has an instruction.
    """

    def __init__(self, going: str, keeping: str) -> None:
        super().__init__(f"{going} and {keeping} both hold a login")
        self.going = going
        self.keeping = keeping


@dataclass(frozen=True, slots=True)
class Weighed:
    """What a merge would move, counted before anything moves.

    The same five-and-facts shape a person merge reports, named for what a site holds.
    `usernames` is the number that matters most here: it is the thing that carries the files.
    """

    from_name: str
    into_name: str
    files: int
    usernames: int
    aliases: int
    links: int
    #: Labels published under the site going, which change networks rather than disappear.
    children: int = 0
    facts: int = 0
    #: The same moves BY NAME (see the person merge's `Weighed` for why). Bounded by
    #: `NAMED_AT_MOST` while each count stays whole.
    usernames_named: tuple[Named, ...] = ()
    aliases_named: tuple[Named, ...] = ()
    links_named: tuple[Named, ...] = ()
    children_named: tuple[Named, ...] = ()
    filled: tuple[Filled, ...] = ()


async def _count(database: Database, statement: str, site_id: str) -> int:
    # Unpacked rather than guarded: every one of these is a bare COUNT, which answers with exactly
    # one row whether or not the site has anything of that kind.
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
    """Every blank on the survivor that folding `going` in, in that order, would fill.

    Left to right, the way the fill decides (see the person merge's `_filled`). None if any id
    names nothing.
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
    """Count what merging one site into another would move. Nothing is written.

    None when either id names nothing, which is the answer a screen wants: a merge whose halves
    cannot both be found is not one that failed, it is one that was never possible.
    """
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
        # A username on a site merged away is on the SURVIVOR afterwards; the site it is on now is
        # the one going, which is what somebody recognises it by on this sheet.
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
    """What folding several sites into one would move, added up.

    One set of numbers rather than one per pair: somebody who has picked four rows wants to know
    what happens when they press the button, not four answers to add up themselves.
    """
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
    # The fills over the WHOLE set, left to right, rather than added up per pair.
    filled = await _filled(database, going=going, keeping=keeping)
    if filled is None:  # pragma: no cover (every id was resolved by `weigh` a moment ago)
        return None
    return replace(total, facts=len(filled), filled=filled)


async def _fold_usernames(connection: Connection, *, losing: str, keeping: str) -> None:
    """Fold the losing site's usernames onto the survivor, keeping every file attribution.

    The order is the whole of this function and none of it is interchangeable. A username is what
    carries which files came from it, and `asset_usernames` cascades when the username goes, so the
    files move first, then what the row knew is poured into its twin, and only then is it removed.
    A fold that deleted first would be a site's whole history of attributions gone, silently, on an
    operation with no undo.
    """
    twins = list(
        await connection.execute_fetchall(_TWINNED, {"losing": losing, "keeping": keeping})
    )
    for row in twins:
        gone, keep = str(row["gone_id"]), str(row["keep_id"])
        await connection.execute(_MOVE_ATTRIBUTIONS, (keep, gone))
        await connection.execute(_FILL_TWIN, {"keep": keep, "gone": gone})
        await connection.execute(_DROP_USERNAME, (gone,))
    # Whatever is left has no counterpart, so it simply changes sites.
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
    # The cover first. The default-cover rule does not reach a Site
    # (`kernel/access/default_covers`), so nothing below fills the survivor's gap with a frame of a file: a Site with no picture of
    # its own keeps the going Site's, or its letter or its icon.
    await connection.execute(_TAKE_COVER_FROM, where)
    await _fold_usernames(connection, losing=losing, keeping=keeping)
    await _links_go_after(connection, losing=losing)
    for table in _TABLES:
        await connection.execute(table.move, (keeping, losing))
        if table.sweep is not None:
            await connection.execute(table.sweep, (losing,))
    await connection.execute(_MOVE_CONNECTION, (keeping, losing))
    # The labels first, then the survivor's own row, then the self-parent that either of those two
    # may have just created. Any other order leaves a label pointing at a row that has gone.
    await connection.execute(_REPARENT_CHILDREN, (keeping, losing))
    await connection.execute(_FILL_BLANKS_FROM, where)
    await connection.execute(_CLEAR_SELF_PARENT, (keeping, keeping))
    # What happened to it, what a stash-box was asked about it and what somebody thought of it:
    # the stores that name a Site by an id with a kind word beside it, which no key reaches and
    # nothing above moves. The SAME list the person merge runs (`kernel/access/merged.py`): without
    # it, folding Twitter into X would leave every stash-box run, every kept answer and every event
    # about Twitter under an id nothing answers to.
    await follow(connection, kind="site", losing=losing, keeping=keeping, name=name)
    await _keep_the_name(connection, name=name, on=keeping)
    # The folder suggestions name a site by the word its filenames open with, so they are rewritten
    # to the survivor's name rather than moved by a key.
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
    """Fold several sites into one, in ONE transaction.

    Raises `BothHaveALogin` when a site being merged away and the survivor both hold a sealed
    login. Checked for the WHOLE set before anything is written, so a set of four is refused before
    it starts rather than halfway through: there is no undo, and stopping in the middle of folding
    four sites is a library where two are gone and nothing says which.
    """
    going = [one for one in dict.fromkeys(losing) if one != keeping]
    if not going:
        return None
    weighed = await weigh_many(database, losing=going, keeping=keeping)
    if weighed is None:
        return None

    # The refusal, for every one of them, before the transaction opens. A login is a secret, and a
    # merge cannot report having destroyed one.
    survivor_has_a_login = await database.fetch_one(_HAS_CONNECTION, (keeping,)) is not None
    for one in going:
        if survivor_has_a_login and await database.fetch_one(_HAS_CONNECTION, (one,)) is not None:
            raise BothHaveALogin(one, keeping)

    # Before the transaction, for the reason a person merge gives: a grant is a decision somebody
    # took about one specific site, and forgetting only ever removes access, the safe direction to
    # be wrong in.
    for one in going:
        await access.forget_object(ObjectType.SITE, one)

    names = {}
    for one in going:
        row = await database.fetch_one(_NAME, (one,))
        if row is None:  # pragma: no cover (weighed above resolved every one of them)
            return None
        names[one] = str(row["name"])
    brought = await _what_each_brings(database, going=going, keeping=keeping)
    if brought is None:  # pragma: no cover (weighed above resolved every one of them)
        return None

    async with telling(database, EVERY_ADMIN, About.LIBRARY) as connection:
        for one in going:
            if not await _one_into(connection, losing=one, keeping=keeping, name=names[one]):
                # Refuse the whole set rather than commit part of it. Leaving the block by raising
                # is what rolls the transaction back; returning would commit what had been done.
                raise _PartialMerge(one)
            # The site that went is the subject, for the reason a person merge gives: it is the one
            # nobody can look up afterwards, so its name has to be in the row. What came over rides
            # in the payload, for the reason the person merge's does: the confirm sheet counted it
            # and was gone once the button was pressed.
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


async def _what_each_brings(
    database: Database, *, going: Sequence[str], keeping: str
) -> dict[str, str] | None:
    """What each site going brings to the survivor, as the payload of its `merged` event.

    The person merge's `_what_each_brings`, on a Site's counts: the same keys where the two hold the
    same thing (files, usernames, other names, addresses as `links`), `children` for the Sites
    published under it, and the blanks it fills credited left to right as the fold runs, so the
    one History reader (`sentences.brought_over`) says both merges in one vocabulary. A sibling and
    not a shared engine, for the reason this module's header gives.

    None if any id names nothing.
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
    """One of a set could not be removed, so none of them is. Never reaches a person: the ids were
    all resolved a moment earlier, so this is the impossible case being made impossible rather than
    a failure with a message."""


async def _keep_the_name(connection: Connection, *, name: str, on: str) -> None:
    """Write the name that is going onto the survivor, unless it already answers to it.

    Checked rather than left to `INSERT OR IGNORE` alone, exactly as a person's is: the unique
    constraint on the ALIAS table folds case, so the insert would be quietly correct, but the
    survivor's own NAME is not in the alias table, and a site merged into one already called the
    same thing would otherwise write an alias identical to the name it sits under.

    The obvious example (*"merging `PMVHaven` into `pmvhaven`"*) cannot happen here.
    `sites.name` is `UNIQUE COLLATE NOCASE`, so those two rows cannot both exist, and nothing
    inside the transaction changes the survivor's name (`_FILL_BLANKS_FROM` fills `kind`, `notes`
    and
    `parent_id`, and no statement here touches `name`). The check is kept anyway, for the reason
    the impossible branch above it is: it is two lines, it holds whichever way the constraint goes,
    and this helper stands beside the PERSON merge's, where names really are not unique and the
    branch is live.
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
    """Every table this merge touches, for the check that holds the list to the schema.

    Read out of the statements themselves rather than written beside them, the same as the people
    merge: a list of names kept next to the statements that name them is a list that drifts the
    first time somebody adds a table and edits only one of the two.

    The three handled specially are added by hand and named as such: they are not in `_TABLES`
    precisely because they are not plain moves, and the schema check still has to see them.
    """
    moved = {
        one.move.split("UPDATE OR IGNORE ")[-1].split("UPDATE ")[-1].split()[0] for one in _TABLES
    }
    return frozenset(moved | {"usernames", "site_connections", "sites"})
