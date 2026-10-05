# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may see which file, stored one row per user and file, and kept true by the database.

Deciding whether a user may see a file means walking the folders every copy of it sits in
(a restrict anywhere above a copy is absolute; otherwise the nearest share wins), checking every
tag, person, collection, site, photo set and song the file belongs to for a grant, reading the
item's own grant, and then asking whether anything on any of those axes is hidden for that user.
That is not a predicate an index can answer, so a statement that works it out on the way to a page
works it out for the whole library first, and every screen that lists files grows with the
library instead of with the page.

So the answer is stored. `viewer_assets` holds one row per (user, file) the user is permitted
to see, with the file's concealment for that user beside it. A page is then a walk of a sort
index and one primary-key probe per row; the check behind a thumbnail is one probe; an entity
wall's permitted set is one range of the table. No read statement carries a permission rule.

**The database keeps it true, not the application.** Every table the verdict reads carries
triggers that recompute the rows a change can reach, inside the writer's own transaction, so there
is no path (a route, a job, a migration, a test writing rows by hand) that can leave the stored
answer behind the facts. That is what makes this a fact rather than a cache.

`folder_ancestry` is what makes the folder walk expressible without recursion: every folder lists
itself and every folder above it, with the distance. Nearest-wins is then `ORDER BY depth LIMIT 1`,
"anything hidden above this" is an `EXISTS`, and "every copy under this folder" is a join on the
ancestor. A trigger body cannot recurse, and with this table nothing needs to.

`viewer_stats` counts the rows per user, so the total under an un-narrowed wall is one read.

The verdict is written ONCE, as `_VERDICT_ROWS`. Every trigger and the backfill are that statement
over a different set of pairs. A second copy of the rules is exactly the drift this module exists
to end, and the tests hold that there is none.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sift.kernel.access.sites import FILES_SITES_REACH, SITE_REACH
from sift.kernel.access.viewer import Viewer, reveals_existence
from sift.kernel.db import (
    Connection,
    add_schema_dependency,
    register_schema_initializer,
    register_schema_invariant,
)
from sift.kernel.log import get_logger
from sift.kernel.migrations import column_exists
from sift.kernel.sql_splice import splice

log = get_logger(__name__)


def _filled(template: str, **names: str) -> str:
    """One statement from a template and the pieces it names as `<<NAME>>`.

    `splice` is for the rule fragments, which every template must name exactly. This is for the
    pieces a template takes by position (the pairs, the places, the user), where the same
    text is filled several ways. Every piece is a module constant or a trigger's own row
    reference; nothing that arrives at run time is put through here.
    """
    text = template
    for name, value in names.items():
        text = text.replace("<<" + name + ">>", value)
    return text


COMPONENT = "visibility"
VERSION = 15

# --- the tables ----------------------------------------------------------------------------

_CREATE_FOLDER_ANCESTRY = """
CREATE TABLE IF NOT EXISTS folder_ancestry (
  folder_id   TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  ancestor_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  depth       INTEGER NOT NULL,
  PRIMARY KEY (folder_id, ancestor_id)
) WITHOUT ROWID
"""

# Every copy under a folder: seek by the ancestor, read the folder ids off the index.
_CREATE_ANCESTRY_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_folder_ancestry_ancestor"
    " ON folder_ancestry(ancestor_id, folder_id)"
)

_CREATE_VIEWER_ASSETS = """
CREATE TABLE IF NOT EXISTS viewer_assets (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  asset_id  TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  concealed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, asset_id)
) WITHOUT ROWID
"""

# The Hidden screen lists what is concealed, which is a small part of any library. Partial, so the
# screen reads exactly those rows rather than every row the user has.
_CREATE_CONCEALED_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_viewer_assets_concealed"
    " ON viewer_assets(user_id, asset_id) WHERE concealed = 1"
)

# `permitted_bytes` and `concealed_bytes` are the sizes of the same two sets of files, summed off
# each file's own size, so the size a screen draws beside a count is of exactly the files the
# count counts, for exactly this viewer, and the vault holds back its share of the bytes by the
# same rule it holds back its share of the files.
_CREATE_VIEWER_STATS = """
CREATE TABLE IF NOT EXISTS viewer_stats (
  user_id   TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  permitted INTEGER NOT NULL DEFAULT 0,
  concealed INTEGER NOT NULL DEFAULT 0,
  permitted_bytes INTEGER NOT NULL DEFAULT 0,
  concealed_bytes INTEGER NOT NULL DEFAULT 0
)
"""

# Scratch: the pairs a recompute is working on. Every recompute stages its pairs here first and
# reads them from here for each of its steps, so the pairs are worked out once rather than once
# per step, and the folder-move trigger can read which copies it reaches BEFORE the ancestry is
# rewritten and re-decide them AFTER. Emptied on the way out; Sift has one writer, so nothing else
# can see it in between.
_CREATE_PENDING = """
CREATE TABLE IF NOT EXISTS visibility_pending (
  user_id  TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  PRIMARY KEY (user_id, asset_id)
) WITHOUT ROWID
"""

# Scratch: what each place (a folder, or a root for a copy sitting directly in one) says for
# one user, for the places the staged pairs' copies sit in. The place rules walk the folder
# chain and read the grants at each step, and every copy in a folder gets the same answer, so a
# recompute works each place out once here and the verdict reads the answer per copy with one
# probe. Filled for exactly the places in question at the start of a recompute, and emptied at
# the end; the backfill fills it for every place there is.
_CREATE_PLACES = """
CREATE TABLE IF NOT EXISTS visibility_places (
  user_id    TEXT NOT NULL,
  root_id    TEXT NOT NULL,
  place      TEXT NOT NULL,
  restricted INTEGER NOT NULL,
  shared     INTEGER NOT NULL,
  vaulted    INTEGER NOT NULL,
  PRIMARY KEY (user_id, root_id, place)
) WITHOUT ROWID
"""

# How many files of each thing a user may see, and how many the vault holds back: one row per
# (user, kind, object) with at least one permitted file, so a wall reads one row per card instead
# of counting every membership under the page. Kept by every recompute, as sets: taken away before
# the staged rows go and given back from the rows that return, and a membership change is made to
# look the same way, so there is exactly one path by which a count moves.
#
# The byte and `_ms` columns are the size and running time of the same files, moved by the same
# statements. Summed only for a kind that counts files (`Counted.sized`); every other kind keeps
# nought there, so a count of faces is never read as a size.
_CREATE_ENTITY_COUNTS = """
CREATE TABLE IF NOT EXISTS viewer_entity_counts (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind      TEXT NOT NULL,
  object_id TEXT NOT NULL,
  permitted INTEGER NOT NULL DEFAULT 0,
  concealed INTEGER NOT NULL DEFAULT 0,
  permitted_bytes INTEGER NOT NULL DEFAULT 0,
  concealed_bytes INTEGER NOT NULL DEFAULT 0,
  permitted_ms INTEGER NOT NULL DEFAULT 0,
  concealed_ms INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, kind, object_id)
) WITHOUT ROWID
"""

# HOW MANY FILES TWO THINGS SHARE, per user: a person and a tag, a tag and a photo set, a
# person and a username. One row per (user, pair) with at least one permitted file carrying
# both, and how many of those the vault holds back. What an entity CARD on a wall draws beside its
# name ("12 photo sets, 40 tags") is how many of these rows are alive for it, so a wall of sixty
# cards reads sixty short index ranges instead of counting every membership of every card's files
# on the way to the page, which on a large library is the difference between a fraction of a
# second a page and a few milliseconds for the whole wall.
#
# Kept by the SAME recompute as `viewer_entity_counts` and in the same two halves: a file's pairs
# are taken away with its rows and given back from the rows that return, so a membership arriving
# or going, a grant, a hide and a folder move all move a pair by the one path a single count moves
# by. Each pair is stored ONCE, in the order `PAIRED` names it, and read in both directions: the
# second index is what makes the B side a range.
#
# A SITE IS NOT A SIDE OF ANY PAIR, AND THAT IS DELIBERATE: the USERNAME is. What a site reaches
# is a walk up `sites.parent_id` from each username's `site_id`, and both of those columns move
# without a file moving: the two triggers that watch them are AFTER only (see the note over
# `_KERNEL_COUNTED`), so a stored site pair would be taken from the new site and left counting on
# the old. A file's USERNAMES are memberships like any other, so the pair is stored against the
# username and the walk up to the sites is made when the card is read, over the handful of usernames
# a page reaches. Moving a username to another site then moves nothing stored at all.
_CREATE_PAIR_COUNTS = """
CREATE TABLE IF NOT EXISTS viewer_pair_counts (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind_a    TEXT NOT NULL,
  id_a      TEXT NOT NULL,
  kind_b    TEXT NOT NULL,
  id_b      TEXT NOT NULL,
  permitted INTEGER NOT NULL DEFAULT 0,
  concealed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, kind_a, id_a, kind_b, id_b)
) WITHOUT ROWID
"""

# The B side as a range, covering, so a card read from that side never probes the table.
_CREATE_PAIR_COUNTS_B_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_viewer_pair_counts_b"
    " ON viewer_pair_counts(user_id, kind_b, id_b, kind_a, id_a, permitted, concealed)"
)

# The rows a recompute has just emptied, and nothing else. Without it the sweep that drops them
# reads every pair the user has on every change to one file; partial, so it holds only the rows
# that are about to go and costs nothing the rest of the time.
_CREATE_PAIR_COUNTS_EMPTY_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_viewer_pair_counts_empty"
    " ON viewer_pair_counts(user_id) WHERE permitted <= 0"
)

# HOW MANY PARTNERS OF ONE KIND A THING HAS, per user: a tag's people, a person's photo sets.
# One row per (user, thing, partner kind) with at least one live pair, holding how many pairs
# are alive (`permitted`) and how many of those are alive with the vault shut (`shown`: a pair with
# a file the vault is not holding back). A card's cell is that number read off one row, where
# counting the pairs themselves reads one row PER PARTNER: a page of the largest tags reaching
# thousands of tag-person pairs costs many times what one row read this way does.
#
# Kept by TRIGGERS ON `viewer_pair_counts`, and only there: every path that moves a pair (the
# recompute, a user's rebuild, a user going) moves this through the pair row it writes, so
# there is no second place the rule lives. They fire only when a pair CROSSES nought on either
# measure (the WHEN below), so the ordinary take-and-give of a recompute that leaves a pair alive
# costs one comparison per pair row and nothing more. The whole rebuild drops them with every
# other trigger and fills this from the pairs in one GROUP BY instead.
#
# What it does NOT know, on purpose, so that nothing it stores can go stale without a pair moving:
# which partners THIS viewer has hidden (read at card time, over the viewer's hides, a handful
# of rows behind a partial index), and a site's anything (a site is no side of a pair; see above).
_CREATE_PARTNER_COUNTS = """
CREATE TABLE IF NOT EXISTS viewer_partner_counts (
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,
  object_id    TEXT NOT NULL,
  partner_kind TEXT NOT NULL,
  permitted    INTEGER NOT NULL DEFAULT 0,
  shown        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, kind, object_id, partner_kind)
) WITHOUT ROWID
"""

TABLES = (
    "folder_ancestry",
    "viewer_assets",
    "viewer_stats",
    "viewer_entity_counts",
    "viewer_pair_counts",
    "viewer_partner_counts",
    "visibility_pending",
    "visibility_places",
)

# The copies on disk under each folder, every folder above the copy's own included. Copies rather
# than files, which is what a folder's number has always meant here: a file with two copies under
# a folder occupies it twice. Only copies that are really there count: a missing one has nothing
# to move and nothing to show.
_COPIES_UNDER_FOLDERS = (
    "(SELECT l.asset_id, an.ancestor_id AS folder_id"
    "   FROM asset_locations l JOIN folder_ancestry an ON an.folder_id = l.folder_id"
    "  WHERE l.status = 'present')"
)


@dataclass(frozen=True)
class Counted:
    """One kind of thing whose files are counted per user in `viewer_entity_counts`.

    `source` is what a membership of the kind is: a table, or a SELECT of (asset_id, `column`)
    over one. It is joined per staged file on `asset_id`, so it must be a shape the planner can
    probe that way: a plain table, or a subquery with no DISTINCT and no aggregate, which the
    engine folds into the join. A DISTINCT subquery is materialised whole, once per statement,
    and a recompute of one file would then read every membership in the library. `table` is the
    table whose changes move the count, and `update` the UPDATE event that watches it.

    `keys` is every set of columns the table refuses two rows to share: its primary key and each
    UNIQUE constraint. The trigger that runs BEFORE a row lands takes the file's rows and counts
    away, and the one AFTER puts them back; the engine fires the first before it decides whether
    the row will land at all. An `INSERT OR IGNORE`, an `ON CONFLICT DO NOTHING` or an
    `UPDATE OR IGNORE` that collides fires BEFORE and never AFTER, so without a guard on the keys
    every idempotent re-filing of a membership would take the file's rows away for good. The BEFORE
    half therefore fires only when no row already holds what the change would write.
    """

    kind: str
    source: str
    column: str
    table: str
    update: str = "UPDATE"
    keys: tuple[tuple[str, ...], ...] = ()
    #: Count FILES rather than the rows a file joins to: a Site's source can reach one file twice
    #: (two usernames on one site). Still right when moved as sets, because every recompute takes
    #: and gives a staged file WHOLE.
    distinct: bool = False
    #: Sum the size and running time of the files counted too. Only for a kind counting FILES; one
    #: counting faces or marks keeps nought there and does not pay for reading each file's row.
    sized: bool = False


#: The file counts and concealed counts a kind's rows are summed into, as SQL over `v` (the
#: verdict row). The two forms of `Counted.distinct`.
_ROWS_COUNTED = ("COUNT(*)", "COALESCE(SUM(v.concealed), 0)")
_FILES_COUNTED = (
    "COUNT(DISTINCT v.asset_id)",
    "COUNT(DISTINCT CASE WHEN v.concealed = 1 THEN v.asset_id END)",
)

#: The sizes beside those counts, as SQL over `v` and `a` (the file's own row): every file's bytes,
#: and the vault's share. A file with no size or time recorded adds nothing.
_BYTES_SUMMED = (
    "COALESCE(SUM(a.size_bytes), 0)",
    "COALESCE(SUM(CASE WHEN v.concealed = 1 THEN a.size_bytes END), 0)",
)
_BYTES_NOT_SUMMED = ("0", "0")
_MS_SUMMED = (
    "COALESCE(SUM(a.duration_ms), 0)",
    "COALESCE(SUM(CASE WHEN v.concealed = 1 THEN a.duration_ms END), 0)",
)


def _members(one: Counted, rows: str, tail: str = "") -> str:
    """What one kind's counts are taken over: `rows` (verdict rows aliased `v`) joined to the kind's
    memberships, with `tail` (a WHERE on `v`, or nothing) after them.

    A sized kind also reads each file's own row, as `a`. A DISTINCT sized kind makes its rows
    distinct per (user, file, thing) FIRST, since no aggregate mends a SUM over a file reached
    twice; over the rows already joined to `rows`, never over the whole source (see `Counted`).
    """
    if not one.sized:
        template = _JOINED
    elif not one.distinct:
        template = _JOINED_SIZED
    else:
        template = _JOINED_DISTINCT_SIZED
    return _filled(template, ROWS=rows, TABLE=one.source, COLUMN=one.column, TAIL=tail)


#: The three shapes `_members` fills: module constants filled with module constants.
#: The CROSS JOIN keeps the planner from scanning every file to reach a handful of rows.
_JOINED = "<<ROWS>> JOIN <<TABLE>> m ON m.asset_id = v.asset_id<<TAIL>>"
_JOINED_SIZED = (
    "<<ROWS>> JOIN <<TABLE>> m ON m.asset_id = v.asset_id"
    " JOIN assets a ON a.id = v.asset_id<<TAIL>>"
)
_JOINED_DISTINCT_SIZED = (
    "(SELECT DISTINCT v.user_id, v.asset_id, v.concealed, m.<<COLUMN>>"
    " FROM <<ROWS>> JOIN <<TABLE>> m ON m.asset_id = v.asset_id<<TAIL>>)"
    " v CROSS JOIN assets a ON a.id = v.asset_id"
)


def _member_of(one: Counted) -> str:
    """The thing a membership row names, as the templates over `_members` read it."""
    return ("v." if one.sized and one.distinct else "m.") + one.column


#: What a Site reaches, as memberships: one row per (file, site) for every site at or above the site
#: of each username the file is filed under. `SITE_REACH` is the one walk every site rule reads (see
#: `kernel/access/sites.py`), so the stored count and the live one cannot disagree about what a
#: network holds. Joined per staged file on `asset_id` like every other source: the walk over
#: `sites` is materialised once per statement and probed, and the filings are read by the file's
#: own key.
#: `noqa: S608`: module constants only.
_SITE_REACHED = (
    "(SELECT aa.asset_id, reach.ancestor_id AS site_id FROM asset_usernames aa"  # noqa: S608
    " JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN (" + SITE_REACH + ") reach ON reach.site_id = ac.site_id)"
)


#: The kinds the kernel itself counts, Sites among them, as the last entry. A Site's count is a
#: DISTINCT over its usernames' files (`Counted.distinct`), because a per-username number cannot be
#: summed into it: one file filed under two usernames of one Site is still one file.
#:
#: Three things a Site needs that the other kinds do not. Its count is a DISTINCT over FILES (a
#: file filed under two usernames on one site, or under two labels of one network, joins twice),
#: which is `Counted.distinct`. Its table is `asset_usernames`, shared with the `username` kind,
#: which `_watching` allows (one set of triggers). And the triggers that move it
#: (`vis_usernames_site` and `vis_sites_parent`) are BEFORE/AFTER pairs, with a BEFORE half on the
#: DELETE of a username and of a site as well: a count that reads the column being changed has to
#: take while the old value is there, and a trigger fired by a foreign-key action cannot see the
#: parent row that is going (a child's BEFORE trigger under `ON DELETE SET NULL` reads the parent as
#: already gone). See the triggers themselves.
_KERNEL_COUNTED: tuple[Counted, ...] = (
    Counted(
        "tag", "asset_tags", "tag_id", "asset_tags", keys=(("asset_id", "tag_id"),), sized=True
    ),
    Counted(
        "person",
        "asset_people",
        "person_id",
        "asset_people",
        keys=(("asset_id", "person_id"),),
        sized=True,
    ),
    Counted(
        "collection",
        "collection_items",
        "collection_id",
        "collection_items",
        keys=(("collection_id", "asset_id"),),
        sized=True,
    ),
    Counted(
        "photo_set",
        "photo_set_items",
        "photo_set_id",
        "photo_set_items",
        keys=(("photo_set_id", "asset_id"),),
        sized=True,
    ),
    # A SONG: the files carrying one piece of music. A file carries at most one song, so the
    # file is the table's key. In the verdict as a Photo Set is: a grant can name a song and a
    # user can hide one, and either reaches the files that carry it (`LOGICAL_BITS`,
    # `_HIDDEN_BY_MEMBERSHIP`). A song is a thing of its own with a page, and a thing with a page
    # has Hidden and sharing like every other.
    Counted(
        "song",
        "song_files",
        "song_id",
        "song_files",
        keys=(("asset_id",),),
        sized=True,
    ),
    Counted(
        "username",
        "asset_usernames",
        "username_id",
        "asset_usernames",
        keys=(("asset_id", "username_id"),),
        sized=True,
    ),
    # A LOOP is a membership of exactly one file: the mark is a row of `loops` naming the file it
    # was cut from, so the file's rows carry it the way they carry a tag. Its count is therefore 1
    # or nothing, and what it buys is the PAIRS below: a person's card reads how many marks sit
    # on files it shares with them off the stored pairs rather than walking every one of the
    # person's files. The vault rule holds by construction: a mark on a file a user may not
    # see has no row to join to. Only a change of the file it points at moves anything; a rename
    # or a retime (the only updates `slices/loops/service.py` makes) moves no count.
    Counted("loop", "loops", "id", "loops", "UPDATE OF id, asset_id", keys=(("id",),)),
    Counted(
        "folder",
        _COPIES_UNDER_FOLDERS,
        "folder_id",
        "asset_locations",
        "UPDATE OF asset_id, root_id, folder_id, status",
        keys=(("id",), ("root_id", "rel_path")),
        sized=True,
    ),
    # A SITE: every file filed under a username on it or on any site below it, counted once. Over
    # `asset_usernames`, like the `username` kind, so a filing arriving or going moves both through
    # the one set of triggers on that table; a username moving site, a label moving network and
    # either of them being deleted are the four other ways a site's reach changes, and each has its
    # own trigger pair below.
    Counted(
        "site",
        _SITE_REACHED,
        "site_id",
        "asset_usernames",
        keys=(("asset_id", "username_id"),),
        distinct=True,
        sized=True,
    ),
)

#: The pairs `viewer_pair_counts` keeps, each stored once in the order written here and read from
#: either side. They are exactly the tabs an entity card draws (`tabsFor` in the client's
#: `related.svelte.ts`), with a site's cells reached through `username` for the reason written over
#: the table. Not here, and why:
#:
#: - a PHOTO SET and a COLLECTION: neither page has a tab for the other. Nor a SONG and a Photo
#:   Set: a song's page has every tab but Photo Sets, and a Photo Set's page has no Music tab.
#: - a PHOTO SET and a LOOP: a photo set's page has no Loops tab.
#: - a TAG a mark carries ITSELF (`loop_tags`): a tag's Loops tab lists those marks as well as the
#:   marks cut from its files, and only the second is a pair of two memberships of one file. The
#:   first is read when the card is read (`_CARD_COUNTS`), over the tag's own marks and each mark's
#:   stored count, so tagging a mark moves nothing stored, and there is no second path to keep.
#: - a thing and ITSELF (a person's Seen with): the card does not draw it.
PAIRED: tuple[tuple[str, str], ...] = (
    ("person", "photo_set"),
    ("person", "tag"),
    ("person", "collection"),
    ("person", "username"),
    ("tag", "photo_set"),
    ("tag", "collection"),
    ("tag", "username"),
    ("collection", "username"),
    ("photo_set", "username"),
    ("person", "song"),
    ("tag", "song"),
    ("song", "username"),
    # A song's page has Loops and Collections tabs, and a Collection's page a Music tab.
    ("collection", "song"),
    ("song", "loop"),
    ("person", "loop"),
    ("tag", "loop"),
    ("collection", "loop"),
    ("username", "loop"),
)

#: The counted kind the v6 step added, and the pairs it is a side of.
_LOOP_KIND = "loop"

#: The counted kind the v8 step added. A side of no pair; see the note over the pair table.
_SITE_KIND = "site"

#: The kind under which the faces waiting in each group are counted. The slice that owns the
#: faces registers the count; the wall that lists the groups is the kernel's, so the name is here.
WAITING_FACES_KIND = "pile"

_counted: list[Counted] = list(_KERNEL_COUNTED)


def counted() -> tuple[Counted, ...]:
    """Every counted kind: the kernel's own, and what the slices have registered."""
    return tuple(_counted)


def register_counted(new: Counted, *, component: str) -> None:
    """A slice declares a kind of thing to count, over a table of its own.

    `component` is the schema component that owns that table; this component is then brought up
    after it, so the triggers that keep the count are made on a table that exists. Registered at
    import, like the schema components themselves: everything built from the list is built on
    first use and forgotten here, so a registration after a database has been opened in this
    process would leave that database's triggers behind the list.
    """
    if any(one.kind == new.kind for one in _counted):
        raise ValueError(f"a counted kind named {new.kind!r} is already known")
    if not new.keys:
        raise ValueError(f"a counted kind on {new.table!r} must declare the table's keys")
    # A SECOND KIND OVER A TABLE ALREADY WATCHED is allowed, and is one set of triggers rather than
    # two: the trigger on a table re-decides the FILE, and the recompute moves every kind's count
    # for it, so two kinds over one table need one watcher that fires on either kind's columns
    # (`_watching`). What cannot be merged is two opinions about the table's keys (the guard on
    # the BEFORE half is written from them), so that is refused.
    for one in _counted:
        if one.table == new.table and set(one.keys) != set(new.keys):
            raise ValueError(f"counted kinds on {new.table!r} disagree about the table's keys")
    _counted.append(new)
    add_schema_dependency(COMPONENT, component)
    _built.cache_clear()


# --- the verdict -----------------------------------------------------------------------------

# --- one place, for one user -----------------------------------------------------------------
#
# The fragments below answer about a PLACE (a folder or a root) for one user. They assume
# two aliases and nothing else: `p` with a `user_id`, and `l` with a `folder_id` and a `root_id`
# (a copy of a file, or a folder standing in for one). Every statement that needs the scope of a
# place splices these rather than writing the rules again: the verdict below, the folder tree, the
# sharing badges. One definition, read from three places, is what keeps them agreeing.

# A RESTRICT ANYWHERE ON THE FOLDER CHAIN, the place's own folder included (its row at depth 0 is
# itself). A restrict is ABSOLUTE: nothing said nearer the file (a share on a folder inside it, a
# share on the file itself, a share on a tag the file carries) undoes it. That is the promise the
# sharing panel makes in so many words ("never this, whatever else gets shared later").
#
# Not the nearest grant: that would hand over a sub-folder shared inside a restricted folder whole,
# and one clip shared on its own inside it by the item rung. This is an EXISTS over every folder
# above, and the ladder asks it before the item's own share.
FOLDER_CHAIN_RESTRICT = """
EXISTS (SELECT 1 FROM folder_ancestry an
         JOIN acl_grants g ON g.object_type = 'folder' AND g.object_id = an.ancestor_id
                          AND g.subject_user_id = p.user_id AND g.effect = 'restrict'
        WHERE an.folder_id = l.folder_id)"""

# The nearest folder with a SHARE, walking up from the place's own folder. Only ever read where no
# restrict reaches the place (`PLACE_RESTRICTED` is asked first everywhere this is spliced), so
# "nearest" and "any" give the same answer; the walk stops at the first depth with a grant.
NEAREST_FOLDER_SHARE = """
(SELECT MAX(g.effect = 'share')
   FROM folder_ancestry an
   JOIN acl_grants g ON g.object_type = 'folder' AND g.object_id = an.ancestor_id
                    AND g.subject_user_id = p.user_id
  WHERE an.folder_id = l.folder_id
  GROUP BY an.depth ORDER BY an.depth LIMIT 1)"""

# A restrict on the root the place is in, or on everything. Absolute for the reason the folder
# chain's is: a root is a folder on every screen that names one, and "everything" restricted is the
# promise at its widest. Two EXISTS rather than one with an OR, so each is a seek.
ROOT_RESTRICT = """
(EXISTS (SELECT 1 FROM acl_grants g
          WHERE g.subject_user_id = p.user_id AND g.object_type = 'root' AND g.object_id = l.root_id
            AND g.effect = 'restrict')
 OR EXISTS (SELECT 1 FROM acl_grants g
             WHERE g.subject_user_id = p.user_id AND g.object_type = 'global'
               AND g.object_id IS NULL AND g.effect = 'restrict'))"""

# The root's own share, and behind it the global one. Read only where no restrict reaches the place.

ROOT_SHARE = """
COALESCE((SELECT MAX(g.effect = 'share') FROM acl_grants g
           WHERE g.subject_user_id = p.user_id AND g.object_type = 'root' AND g.object_id = l.root_id),
         (SELECT MAX(g.effect = 'share') FROM acl_grants g
           WHERE g.subject_user_id = p.user_id AND g.object_type = 'global' AND g.object_id IS NULL),
         0)"""

ROOT_HIDDEN = """
EXISTS (SELECT 1 FROM root_user_state h
         WHERE h.root_id = l.root_id AND h.user_id = p.user_id AND h.hidden = 1)"""

FOLDER_CHAIN_HIDDEN = """
EXISTS (SELECT 1 FROM folder_ancestry an
         JOIN folder_user_state h ON h.folder_id = an.ancestor_id
                                 AND h.user_id = p.user_id AND h.hidden = 1
        WHERE an.folder_id = l.folder_id)"""

# Whether the place's folder was reached by the walk from its root. A folder the walk never
# reached (an orphan, a loop in a restored database) has no row for itself, and reads as
# denied and concealed: the same fail-closed reading the tree gives a chain it cannot resolve.
# Statements splicing the three below join `folder_ancestry fa` on the place's own folder as
# both `folder_id` and `ancestor_id`, so that `fa.folder_id IS NULL` is "not reached".
PLACE_REACHED_JOIN = "\n  LEFT JOIN folder_ancestry fa ON fa.folder_id = l.folder_id AND fa.ancestor_id = l.folder_id"

# What the place says, for one user. RESTRICTED when a restrict sits on any folder above it, on its
# root or on everything, wherever it was made (see `FOLDER_CHAIN_RESTRICT`). SHARED (below) by the
# nearest share, which only counts where nothing restricts.
PLACE_RESTRICTED = splice(
    """CASE WHEN l.folder_id IS NULL THEN CASE WHEN {{ROOT_RESTRICT}} THEN 1 ELSE 0 END
     WHEN fa.folder_id IS NULL THEN 1
     WHEN {{FOLDER_CHAIN_RESTRICT}} OR {{ROOT_RESTRICT}} THEN 1
     ELSE 0 END""",
    ROOT_RESTRICT=ROOT_RESTRICT,
    FOLDER_CHAIN_RESTRICT=FOLDER_CHAIN_RESTRICT,
)

PLACE_SHARED = splice(
    """CASE WHEN l.folder_id IS NULL THEN {{ROOT_SHARE}}
     WHEN fa.folder_id IS NULL THEN 0
     ELSE COALESCE({{NEAREST_FOLDER_SHARE}}, {{ROOT_SHARE}}) END""",
    ROOT_SHARE=ROOT_SHARE,
    NEAREST_FOLDER_SHARE=NEAREST_FOLDER_SHARE,
)

PLACE_VAULTED = splice(
    """CASE WHEN l.folder_id IS NULL THEN CASE WHEN {{ROOT_HIDDEN}} THEN 1 ELSE 0 END
     WHEN fa.folder_id IS NULL THEN 1
     ELSE CASE WHEN {{ROOT_HIDDEN}} OR {{FOLDER_CHAIN_HIDDEN}} THEN 1 ELSE 0 END END""",
    ROOT_HIDDEN=ROOT_HIDDEN,
    FOLDER_CHAIN_HIDDEN=FOLDER_CHAIN_HIDDEN,
)

# --- one file, for one user ----------------------------------------------------------------------
#
# Bits, so that one subquery answers three questions about the same rows instead of three
# subqueries walking them three times:
#
#   ph = denied * 4 + allowed * 2 + vaulted     over every copy of the file
#   lo = denied * 2 + allowed                   over every membership with a grant on it
#   it = restricted * 2 + shared                the item's own grant
#
# A grant's two flags come from the same object: `MAX(effect = 'restrict')` over an object's rows is
# 1 if any of them restricts, 0 if rows exist and none does, NULL if there are none, and NULL is
# what lets COALESCE fall through to the level above.
#
# These assume `p.user_id` and `p.asset_id`, and nothing else.

# One row per copy: what the place it sits in says, worked out in place. This is the form for a
# reader asking about one file on its own (the sharing badge), where there is no recompute
# to have filled `visibility_places` first. The verdict itself reads the filled table below.
PLACE_BITS_OF_COPIES = splice(
    """
(SELECT MAX(s.r) * 4 + MAX(CASE WHEN s.r = 0 AND s.s = 1 THEN 1 ELSE 0 END) * 2 + MAX(s.v)
   FROM (SELECT CASE WHEN r.id IS NULL THEN 1 ELSE {{PLACE_RESTRICTED}} END AS r,
                CASE WHEN r.id IS NULL THEN 0 ELSE {{PLACE_SHARED}} END AS s,
                CASE WHEN r.id IS NULL THEN 1 ELSE {{PLACE_VAULTED}} END AS v
           FROM asset_locations l
           LEFT JOIN library_roots r ON r.id = l.root_id{{PLACE_REACHED_JOIN}}
          WHERE l.asset_id = p.asset_id) s)""",
    PLACE_RESTRICTED=PLACE_RESTRICTED,
    PLACE_SHARED=PLACE_SHARED,
    PLACE_VAULTED=PLACE_VAULTED,
    PLACE_REACHED_JOIN=PLACE_REACHED_JOIN,
)

# The same bits read from `visibility_places`, which the recompute filled a moment ago for every
# place these copies sit in (see `_PLACE_ROWS`): one probe per copy instead of the walk above.
PHYSICAL_BITS = """
(SELECT MAX(pl.restricted) * 4
      + MAX(CASE WHEN pl.restricted = 0 AND pl.shared = 1 THEN 1 ELSE 0 END) * 2
      + MAX(pl.vaulted)
   FROM asset_locations l
   JOIN visibility_places pl ON pl.user_id = p.user_id AND pl.root_id = l.root_id
                            AND pl.place = COALESCE(l.folder_id, '')
  WHERE l.asset_id = p.asset_id)"""

#: Where the places go, in the statement that fills `visibility_places`: a SELECT of
#: (user_id, root_id, folder_id), the folder NULL for a copy sitting directly in its root.
PLACES = "<<PLACES>>"

# What each place says for one user, worked out once per place. A place whose root is gone is
# denied and concealed, for the reason an unreached folder is. The root cannot in fact be gone
# (a copy references its root and goes with it), but a rule that fails closed costs one seek. The
# users are joined as `p` because that is the alias the place rules were written against.
_PLACE_ROWS = splice(
    """
INSERT INTO visibility_places (user_id, root_id, place, restricted, shared, vaulted)
SELECT l.user_id, l.root_id, COALESCE(l.folder_id, ''),
       CASE WHEN r.id IS NULL THEN 1 ELSE {{PLACE_RESTRICTED}} END,
       CASE WHEN r.id IS NULL THEN 0 ELSE {{PLACE_SHARED}} END,
       CASE WHEN r.id IS NULL THEN 1 ELSE {{PLACE_VAULTED}} END
  FROM (<<PLACES>>) l
  JOIN (SELECT id AS user_id FROM users) p ON p.user_id = l.user_id
  LEFT JOIN library_roots r ON r.id = l.root_id{{PLACE_REACHED_JOIN}}""",
    PLACE_RESTRICTED=PLACE_RESTRICTED,
    PLACE_SHARED=PLACE_SHARED,
    PLACE_VAULTED=PLACE_VAULTED,
    PLACE_REACHED_JOIN=PLACE_REACHED_JOIN,
)

#: Whether NO copy of this file is where Sift last saw it, as a column expression over `a.id`.
#:
#: A fragment rather than two copies, because two statements answer it: the media grid, and the wall
#: of loops. A copy in one and nothing in the other would draw a file whose bytes had gone as a
#: torn page on one screen and as present on the other, off the same row in the same database.
#:
#: `NOT EXISTS ... present` rather than counting: the question is whether there is ONE readable
#: copy, and stopping at the first is the cheap way to ask it. A subquery rather than a join, so an
#: asset with four copies is still one row.
ANY_COPY_MISSING = """CASE WHEN NOT EXISTS (SELECT 1 FROM asset_locations al
                              WHERE al.asset_id = a.id AND al.status = 'present')
            THEN 1 ELSE 0 END"""

#: WHETHER THIS VIEWER MAY BE SHOWN THE FILE OF A MEMBERSHIP ROW, as a condition over
#: `link.asset_id`: one probe of the stored verdict. Every count of files a screen draws joins the
#: verdict for the viewer asking: "card says 6, page shows 2" is the fault when one does not, and
#: the difference is the size of the set that user was kept from. The walls' counts read the stored
#: per-thing counts; a statement that counts rows of its own (a History line's "on 12 files", the
#: filed-from-filenames card) splices this rather than writing the join again.
#:
#: The statement aliases its membership table `link`, and binds `:viewer` and `:reveal` from
#: `seen_by` below. `:reveal` is `viewer.reveals_existence`: a locked tile is counted where the
#: grid draws one, and nothing concealed is counted where it does not.
FILE_SEEN_BY_VIEWER = """EXISTS (SELECT 1 FROM viewer_assets seen
                WHERE seen.asset_id = link.asset_id AND seen.user_id = :viewer
                  AND (:reveal = 1 OR seen.concealed = 0))"""


def seen_by(viewer: Viewer) -> dict[str, object]:
    """The two values `FILE_SEEN_BY_VIEWER` binds, from the session asking. One place, so no
    statement splicing it can bind the vault's flag a different way from the next."""
    return {"viewer": viewer.id, "reveal": 1 if reveals_existence(viewer) else 0}


#: Whether the VIEWER hid this file themselves, as against it being hidden by something above it.
#:
#: The other half of the same pair, and a fragment for the same reason. It reads `a.id` and the
#: `:viewer` parameter, so any statement splicing it must bind both.
#:
#: The distinction is what a mark can honestly draw: one is something you did and can undo where you
#: are standing, the other is a consequence of something further up. Without it a tile knows only
#: THAT a file is concealed and has to pick a fill and hold it.
CONCEALED_BY_THIS_FILE = """CASE WHEN EXISTS (SELECT 1 FROM asset_user_state h
                          WHERE h.asset_id = a.id AND h.user_id = :viewer AND h.hidden = 1)
            THEN 1 ELSE 0 END"""

#: What a file belongs to, as the objects a grant can name. The site arm is the one that is not
#: a single column: A SITE REACHES WHAT ITS LABELS RELEASED, so a file filed under a label belongs
#: to that label AND to every network above it: a network has no usernames of its own, so asking
#: only for the username's own site would make sharing a network share nothing. It reads the same
#: fragment the search leaf, the wall count and the
#: concealment rule read; see `kernel/access/sites.py`.
LOGICAL_BITS = _filled(
    """
(SELECT MAX(g.effect = 'restrict') * 2 + MAX(g.effect = 'share')
   FROM (SELECT 'tag' AS kind, tag_id AS object FROM asset_tags WHERE asset_id = p.asset_id
         UNION ALL
         SELECT 'person', person_id FROM asset_people WHERE asset_id = p.asset_id
         UNION ALL
         SELECT 'collection', collection_id FROM collection_items WHERE asset_id = p.asset_id
         UNION ALL
         SELECT 'site', reach.ancestor_id
           FROM asset_usernames aa JOIN usernames ac ON ac.id = aa.username_id
           JOIN (<<SITE_REACH>>) reach ON reach.site_id = ac.site_id
          WHERE aa.asset_id = p.asset_id
         UNION ALL
         SELECT 'photo_set', photo_set_id FROM photo_set_items WHERE asset_id = p.asset_id
         UNION ALL
         SELECT 'song', song_id FROM song_files WHERE asset_id = p.asset_id) m
   JOIN acl_grants g ON g.subject_user_id = p.user_id
                    AND g.object_type = m.kind AND g.object_id = m.object)""",
    SITE_REACH=SITE_REACH,
)

ITEM_BITS = """
(SELECT MAX(g.effect = 'restrict') * 2 + MAX(g.effect = 'share')
   FROM acl_grants g
  WHERE g.subject_user_id = p.user_id AND g.object_type = 'item' AND g.object_id = p.asset_id)"""

_HIDDEN_ITSELF = """
EXISTS (SELECT 1 FROM asset_user_state h
         WHERE h.asset_id = p.asset_id AND h.user_id = p.user_id AND h.hidden = 1)"""

# Hidden by something the file belongs to. Not a grant: it takes the row off one user's screen
# and no share overrides it.
#
# A SITE HIDES WHAT ITS LABELS RELEASED, the one arm here that is not a single join. A network owns
# labels and a label publishes the files, so asking only about the file's own site (the label) would
# make hiding a network hide nothing. The reach fragment is the same one the search leaf and the
# wall count read (see `kernel/access/sites.py`), so what a site CONCEALS and what a site SHOWS
# cannot come apart, which is the whole reason it is a fragment and not a fourth copy.
_HIDDEN_BY_MEMBERSHIP = _filled(
    """
EXISTS (SELECT 1 FROM asset_people ap
         JOIN person_user_state hp ON hp.person_id = ap.person_id
        WHERE ap.asset_id = p.asset_id AND hp.user_id = p.user_id AND hp.hidden = 1)
OR EXISTS (SELECT 1 FROM collection_items ci
            JOIN collection_user_state hc ON hc.collection_id = ci.collection_id
           WHERE ci.asset_id = p.asset_id AND hc.user_id = p.user_id AND hc.hidden = 1)
OR EXISTS (SELECT 1 FROM asset_tags vt
            JOIN tag_user_state ht ON ht.tag_id = vt.tag_id
           WHERE vt.asset_id = p.asset_id AND ht.user_id = p.user_id AND ht.hidden = 1)
OR EXISTS (SELECT 1 FROM asset_usernames va
            JOIN usernames vac ON vac.id = va.username_id
            JOIN (<<SITE_REACH>>) reach ON reach.site_id = vac.site_id
            JOIN site_user_state hl ON hl.site_id = reach.ancestor_id
           WHERE va.asset_id = p.asset_id AND hl.user_id = p.user_id AND hl.hidden = 1)
OR EXISTS (SELECT 1 FROM photo_set_items vp
            JOIN photo_set_user_state hs ON hs.photo_set_id = vp.photo_set_id
           WHERE vp.asset_id = p.asset_id AND hs.user_id = p.user_id AND hs.hidden = 1)
OR EXISTS (SELECT 1 FROM song_files vs
            JOIN song_user_state hg ON hg.song_id = vs.song_id
           WHERE vs.asset_id = p.asset_id AND hg.user_id = p.user_id AND hg.hidden = 1)""",
    SITE_REACH=SITE_REACH,
)

#: THE LADDER, as one expression over three bit columns of one alias: whether a user may see a file.
#:
#: Named once and read by every statement that answers it: the stored verdict below and the
#: sharing badge (`repository/grants.py`), which asks the same question of every user at once, so a
#: rule change cannot be made in one of them and not the other. `<<S>>` is the alias carrying `lo`,
#: `it` and `ph` (see the bits above).
#:
#: In order: a restrict on anything the file belongs to, the file's own restrict, a restrict on any
#: place a copy sits in (ABSOLUTE: above the item's own share, so one clip shared inside a
#: restricted folder is not handed over), then the item's own share, then a place or a membership
#: that shares it.
LADDER_ADMITS = """CASE WHEN (<<S>>.lo & 2) = 2 THEN 0
            WHEN (<<S>>.it & 2) = 2 THEN 0
            WHEN (<<S>>.ph & 4) = 4 THEN 0
            WHEN (<<S>>.it & 1) = 1 THEN 1
            WHEN (<<S>>.ph & 2) = 2 OR (<<S>>.lo & 1) = 1 THEN 1
            ELSE 0 END"""


#: Whether a RESTRICT is what decides, over the same bits: the other half of the badge's question.
#: Every restrict is absolute, so this is simply whether one reaches the file at all.
LADDER_RESTRICTS = """CASE WHEN (<<S>>.lo & 2) = 2 OR (<<S>>.it & 2) = 2 OR (<<S>>.ph & 4) = 4
            THEN 1 ELSE 0 END"""


def ladder_admits(alias: str) -> str:
    """The ladder over the bit columns of `alias`. A module constant's alias only; see `_filled`."""
    return _filled(LADDER_ADMITS, S=alias)


def ladder_restricts(alias: str) -> str:
    """`LADDER_RESTRICTS` over the bit columns of `alias`. A module constant's alias only."""
    return _filled(LADDER_RESTRICTS, S=alias)


#: Where the pairs go. Every statement below that computes rows replaces this with a SELECT of
#: (user_id, asset_id). The marker is chosen so it cannot occur in SQL by accident.
PAIRS = "<<PAIRS>>"

# The rows for a set of pairs. A pair is kept when the file is somewhere and the ladder admits the
# user; `concealed` is the vault's answer for that user. The ladder: a restrict on anything the file
# belongs to wins, then the item's own grant, then where the copies sit, then whether any membership
# shares it. See `LADDER_ADMITS`.
_VERDICT_ROWS = splice(
    """
SELECT x.user_id, x.asset_id,
       CASE WHEN x.hidden = 1 OR (x.ph & 1) = 1 OR x.lv = 1 THEN 1 ELSE 0 END AS concealed
  FROM (SELECT p.user_id, p.asset_id, u.role,
               COALESCE({{PHYSICAL_BITS}}, 0) AS ph,
               COALESCE({{LOGICAL_BITS}}, 0) AS lo,
               COALESCE({{ITEM_BITS}}, 0) AS it,
               CASE WHEN {{HIDDEN_ITSELF}} THEN 1 ELSE 0 END AS hidden,
               CASE WHEN {{HIDDEN_BY_MEMBERSHIP}} THEN 1 ELSE 0 END AS lv
          FROM (<<PAIRS>>) p
          JOIN users u ON u.id = p.user_id
         WHERE EXISTS (SELECT 1 FROM asset_locations al WHERE al.asset_id = p.asset_id)) x
 WHERE x.role = 'admin'
    OR {{LADDER}} = 1""",
    LADDER=ladder_admits("x"),
    PHYSICAL_BITS=PHYSICAL_BITS,
    LOGICAL_BITS=LOGICAL_BITS,
    ITEM_BITS=ITEM_BITS,
    HIDDEN_ITSELF=_HIDDEN_ITSELF,
    HIDDEN_BY_MEMBERSHIP=_HIDDEN_BY_MEMBERSHIP,
)

if _VERDICT_ROWS.count(PAIRS) != 1:  # pragma: no cover (an edit that broke the seam)
    raise RuntimeError("the verdict statement must take its pairs in exactly one place")


#: The staged pairs, which is where every step of a recompute reads them from.
_STAGED = "SELECT user_id, asset_id FROM visibility_pending"

_CLEAR_PENDING = "DELETE FROM visibility_pending"

_STAGE_PAIRS = (
    "INSERT INTO visibility_pending (user_id, asset_id)"
    " SELECT DISTINCT user_id, asset_id FROM (<<PAIRS>>)"
)

_CLEAR_PLACES = "DELETE FROM visibility_places"

# The places the staged pairs' copies sit in, and the fill for exactly those.
_STAGED_PLACES = (
    "SELECT DISTINCT s.user_id, l.root_id, l.folder_id"
    "  FROM visibility_pending s JOIN asset_locations l ON l.asset_id = s.asset_id"
)

_FILL_STAGED_PLACES = _filled(_PLACE_ROWS, PLACES=_STAGED_PLACES)

_DELETE_STAGED = _filled(
    "DELETE FROM viewer_assets WHERE (user_id, asset_id) IN (<<STAGED>>)", STAGED=_STAGED
)

_INSERT_ROWS = "INSERT INTO viewer_assets (user_id, asset_id, concealed)<<ROWS>>"

_INSERT_STAGED = _filled(_INSERT_ROWS, ROWS=_filled(_VERDICT_ROWS, PAIRS=_STAGED))

# The stored rows of the staged pairs. CROSS JOIN, so the walk starts from the staged pairs and
# probes each one's row: left to the planner it would walk every row the user has instead.
_STAGED_ROWS = (
    "visibility_pending s CROSS JOIN viewer_assets v"
    " ON v.user_id = s.user_id AND v.asset_id = s.asset_id"
)

# The counts, moved as sets. Taken away from the rows about to go, given back from the rows that
# came back; an upsert makes a count row the first time a thing has a permitted file under it,
# and the last statement drops the rows nothing permitted is left under. `WHERE 1 = 1` before the
# GROUP BY is what lets the parser tell the upsert's ON CONFLICT from a join's ON.
# The library's own total is always sized: it is what Browse says beside its count of files.
_STAGED_ROWS_SIZED = _STAGED_ROWS + " JOIN assets a ON a.id = v.asset_id"

_STATS_TAKEN = _filled(
    "UPDATE viewer_stats SET permitted = permitted - d.n, concealed = concealed - d.c,"
    " permitted_bytes = permitted_bytes - d.b, concealed_bytes = concealed_bytes - d.cb"
    " FROM (SELECT v.user_id, COUNT(*) AS n, SUM(v.concealed) AS c,"
    "              <<BYTES>> AS b, <<CONCEALED_BYTES>> AS cb"
    "         FROM <<STAGED_ROWS>> GROUP BY v.user_id) d"
    " WHERE viewer_stats.user_id = d.user_id",
    STAGED_ROWS=_STAGED_ROWS_SIZED,
    BYTES=_BYTES_SUMMED[0],
    CONCEALED_BYTES=_BYTES_SUMMED[1],
)

_STATS_GIVEN = _filled(
    "INSERT INTO viewer_stats (user_id, permitted, concealed, permitted_bytes, concealed_bytes)"
    " SELECT v.user_id, COUNT(*), SUM(v.concealed), <<BYTES>>, <<CONCEALED_BYTES>>"
    "   FROM <<STAGED_ROWS>> WHERE 1 = 1"
    " GROUP BY v.user_id"
    " ON CONFLICT (user_id) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed,"
    " permitted_bytes = permitted_bytes + excluded.permitted_bytes,"
    " concealed_bytes = concealed_bytes + excluded.concealed_bytes",
    STAGED_ROWS=_STAGED_ROWS_SIZED,
    BYTES=_BYTES_SUMMED[0],
    CONCEALED_BYTES=_BYTES_SUMMED[1],
)

# `<<MEMBERS>>` is `_members` over the staged rows, and `<<OBJECT>>` is `_member_of`: see
# `_each_kind`, which fills both per kind.
_COUNTS_TAKEN_ONE = (
    "UPDATE viewer_entity_counts SET permitted = permitted - d.n, concealed = concealed - d.c,"
    " permitted_bytes = permitted_bytes - d.b, concealed_bytes = concealed_bytes - d.cb,"
    " permitted_ms = permitted_ms - d.ms, concealed_ms = concealed_ms - d.cms"
    " FROM (SELECT v.user_id, <<OBJECT>> AS object_id, <<FILES>> AS n, <<CONCEALED>> AS c,"
    "              <<BYTES>> AS b, <<CONCEALED_BYTES>> AS cb,"
    "              <<MS>> AS ms, <<CONCEALED_MS>> AS cms"
    "         FROM <<MEMBERS>>"
    "        GROUP BY v.user_id, <<OBJECT>>) d"
    " WHERE viewer_entity_counts.user_id = d.user_id AND viewer_entity_counts.kind = '<<KIND>>'"
    "   AND viewer_entity_counts.object_id = d.object_id"
)

_COUNTS_GIVEN_ONE = (
    "INSERT INTO viewer_entity_counts (user_id, kind, object_id, permitted, concealed,"
    " permitted_bytes, concealed_bytes, permitted_ms, concealed_ms)"
    " SELECT v.user_id, '<<KIND>>', <<OBJECT>>, <<FILES>>, <<CONCEALED>>,"
    "        <<BYTES>>, <<CONCEALED_BYTES>>, <<MS>>, <<CONCEALED_MS>>"
    "   FROM <<MEMBERS>> WHERE 1 = 1"
    "  GROUP BY v.user_id, <<OBJECT>>"
    " ON CONFLICT (user_id, kind, object_id) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed,"
    " permitted_bytes = permitted_bytes + excluded.permitted_bytes,"
    " concealed_bytes = concealed_bytes + excluded.concealed_bytes,"
    " permitted_ms = permitted_ms + excluded.permitted_ms,"
    " concealed_ms = concealed_ms + excluded.concealed_ms"
)

_COUNTS_EMPTIED = (
    "DELETE FROM viewer_entity_counts WHERE permitted <= 0"
    " AND user_id IN (SELECT DISTINCT user_id FROM visibility_pending)"
)

# The pairs, moved exactly as the counts above are: one statement per pair kind, over the staged
# rows joined to both memberships. A file carries each person, tag, set, collection and username at
# most once (the tables' keys say so), so COUNT(*) over the join is a count of FILES.
_PAIR_JOIN = (
    "<<STAGED_ROWS>> JOIN <<TABLE_A>> ma ON ma.asset_id = v.asset_id"
    " JOIN <<TABLE_B>> mb ON mb.asset_id = v.asset_id"
)

_PAIRS_TAKEN_ONE = _filled(
    "UPDATE viewer_pair_counts SET permitted = permitted - d.n, concealed = concealed - d.c"
    " FROM (SELECT v.user_id, ma.<<COLUMN_A>> AS id_a, mb.<<COLUMN_B>> AS id_b,"
    "              COUNT(*) AS n, SUM(v.concealed) AS c"
    "         FROM <<PAIR_JOIN>>"
    "        GROUP BY v.user_id, ma.<<COLUMN_A>>, mb.<<COLUMN_B>>) d"
    " WHERE viewer_pair_counts.user_id = d.user_id"
    "   AND viewer_pair_counts.kind_a = '<<KIND_A>>' AND viewer_pair_counts.id_a = d.id_a"
    "   AND viewer_pair_counts.kind_b = '<<KIND_B>>' AND viewer_pair_counts.id_b = d.id_b",
    PAIR_JOIN=_PAIR_JOIN,
    STAGED_ROWS=_STAGED_ROWS,
)

_PAIRS_GIVEN_ONE = _filled(
    "INSERT INTO viewer_pair_counts (user_id, kind_a, id_a, kind_b, id_b, permitted, concealed)"
    " SELECT v.user_id, '<<KIND_A>>', ma.<<COLUMN_A>>, '<<KIND_B>>', mb.<<COLUMN_B>>,"
    "        COUNT(*), SUM(v.concealed)"
    "   FROM <<PAIR_JOIN>> WHERE 1 = 1"
    "  GROUP BY v.user_id, ma.<<COLUMN_A>>, mb.<<COLUMN_B>>"
    " ON CONFLICT (user_id, kind_a, id_a, kind_b, id_b) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed",
    PAIR_JOIN=_PAIR_JOIN,
    STAGED_ROWS=_STAGED_ROWS,
)

_PAIRS_EMPTIED = (
    "DELETE FROM viewer_pair_counts WHERE permitted <= 0"
    " AND user_id IN (SELECT DISTINCT user_id FROM visibility_pending)"
)


# The partner totals, moved by one pair row crossing nought. `<<ROW>>` is the trigger's NEW or
# OLD (whichever holds the pair's keys), `<<DP>>` and `<<DS>>` how far the pair's two liveness bits
# moved (-1, 0 or 1), and each pair moves both of its sides: A's count of B partners and B's of A.
#
# The row is made only when something is being ADDED to it, and a take only ever updates: a user
# going deletes their pairs and their totals by cascade in no fixed order, and a take that made a
# row would make one for a user who no longer exists. A total that reaches nought goes.
#
# NOT EXISTS rather than `INSERT OR IGNORE`, and that is not style: a conflict clause inside a
# trigger is REPLACED by the one on the statement that fired it, and the pairs are written by an
# upsert: an OR IGNORE here runs as ABORT, and every second tag on a file would fail with a UNIQUE
# error on this table.
_PARTNER_ENSURED = (
    "INSERT INTO viewer_partner_counts (user_id, kind, object_id, partner_kind)"
    " SELECT <<ROW>>.user_id, <<ROW>>.kind_<<THIS>>, <<ROW>>.id_<<THIS>>, <<ROW>>.kind_<<THAT>>"
    " WHERE (<<DP>> > 0 OR <<DS>> > 0) AND NOT EXISTS (SELECT 1 FROM viewer_partner_counts"
    " WHERE user_id = <<ROW>>.user_id AND kind = <<ROW>>.kind_<<THIS>>"
    "   AND object_id = <<ROW>>.id_<<THIS>> AND partner_kind = <<ROW>>.kind_<<THAT>>)"
)

_PARTNER_MOVED = (
    "UPDATE viewer_partner_counts SET permitted = permitted + <<DP>>, shown = shown + <<DS>>"
    " WHERE user_id = <<ROW>>.user_id AND kind = <<ROW>>.kind_<<THIS>>"
    "   AND object_id = <<ROW>>.id_<<THIS>> AND partner_kind = <<ROW>>.kind_<<THAT>>"
)

_PARTNER_EMPTIED = (
    "DELETE FROM viewer_partner_counts"
    " WHERE user_id = <<ROW>>.user_id AND kind = <<ROW>>.kind_<<THIS>>"
    "   AND object_id = <<ROW>>.id_<<THIS>> AND partner_kind = <<ROW>>.kind_<<THAT>>"
    "   AND permitted <= 0"
)

# A pair is alive at all, and alive with the vault shut: the two tests a card reads a pair by.
_PAIR_ALIVE = "(<<ROW>>.permitted > 0)"
_PAIR_SHOWN = "(<<ROW>>.permitted - <<ROW>>.concealed > 0)"


def _partner_trigger(event: str, dp: str, ds: str, row: str) -> tuple[str, str]:
    """One trigger on the pair table: its name and its DDL. Fires only on a crossing."""
    name = "vis_pair_counts_" + event.split()[0].lower()
    body = [
        _filled(template, ROW=row, THIS=this, THAT=that, DP=dp, DS=ds)
        for this, that in (("a", "b"), ("b", "a"))
        for template in (_PARTNER_ENSURED, _PARTNER_MOVED, _PARTNER_EMPTIED)
    ]
    return name, _trigger(
        name, event, "viewer_pair_counts", body, when=dp + " != 0 OR " + ds + " != 0"
    )


def _partner_triggers() -> list[tuple[str, str]]:
    alive_new, shown_new = _filled(_PAIR_ALIVE, ROW="NEW"), _filled(_PAIR_SHOWN, ROW="NEW")
    alive_old, shown_old = _filled(_PAIR_ALIVE, ROW="OLD"), _filled(_PAIR_SHOWN, ROW="OLD")
    return [
        _partner_trigger("INSERT", alive_new, shown_new, "NEW"),
        _partner_trigger("DELETE", "-" + alive_old, "-" + shown_old, "OLD"),
        _partner_trigger(
            "UPDATE OF permitted, concealed",
            "(" + alive_new + " - " + alive_old + ")",
            "(" + shown_new + " - " + shown_old + ")",
            "NEW",
        ),
    ]


# Every total from the pairs, once: each pair read from both of its sides.
_PARTNER_COUNT_ROWS = (
    "SELECT user_id, kind, object_id, partner_kind,"
    " SUM(permitted > 0) AS permitted, SUM(permitted - concealed > 0) AS shown"
    " FROM (SELECT user_id, kind_a AS kind, id_a AS object_id, kind_b AS partner_kind,"
    "              permitted, concealed FROM viewer_pair_counts"
    "       UNION ALL"
    "       SELECT user_id, kind_b, id_b, kind_a, permitted, concealed FROM viewer_pair_counts)"
    " WHERE permitted > 0"
    " GROUP BY user_id, kind, object_id, partner_kind"
)

_CLEAR_PARTNER_COUNTS = "DELETE FROM viewer_partner_counts"

_FILL_PARTNER_COUNTS = _filled(
    "INSERT INTO viewer_partner_counts (user_id, kind, object_id, partner_kind, permitted, shown)"
    " <<ROWS>>",
    ROWS=_PARTNER_COUNT_ROWS,
)

# The stored totals, compared with what the stored pairs say. The pairs are compared with the
# facts by the statement above this one's caller, so the two together hold the totals to the facts.
_PARTNER_COUNT_DIFFERENCES = _filled(
    "SELECT 'partners missing' AS what,"
    " user_id || '/' || kind || '/' || object_id || '/' || partner_kind,"
    " permitted || '/' || shown, permitted FROM ("
    "SELECT user_id, kind, object_id, partner_kind, permitted, shown FROM (<<ROWS>>)"
    " EXCEPT SELECT user_id, kind, object_id, partner_kind, permitted, shown"
    " FROM viewer_partner_counts)"
    " UNION ALL "
    "SELECT 'partners extra',"
    " user_id || '/' || kind || '/' || object_id || '/' || partner_kind,"
    " permitted || '/' || shown, permitted FROM ("
    "SELECT user_id, kind, object_id, partner_kind, permitted, shown FROM viewer_partner_counts"
    " EXCEPT SELECT user_id, kind, object_id, partner_kind, permitted, shown FROM (<<ROWS>>))",
    ROWS=_PARTNER_COUNT_ROWS,
)


def _each_pair(template: str, pairs: Sequence[tuple[Counted, Counted]]) -> list[str]:
    return [
        _filled(
            template,
            KIND_A=a.kind,
            COLUMN_A=a.column,
            TABLE_A=a.source,
            KIND_B=b.kind,
            COLUMN_B=b.column,
            TABLE_B=b.source,
        )
        for a, b in pairs
    ]


def _each_kind(
    template: str, kinds: Sequence[Counted], rows: str = _STAGED_ROWS, tail: str = ""
) -> list[str]:
    """The template once per kind, its memberships taken over `rows` with `tail` after them."""
    return [
        _filled(
            template,
            MEMBERS=_members(one, rows, tail),
            OBJECT=_member_of(one),
            KIND=one.kind,
            COLUMN=one.column,
            TABLE=one.source,
            FILES=(_FILES_COUNTED if one.distinct else _ROWS_COUNTED)[0],
            CONCEALED=(_FILES_COUNTED if one.distinct else _ROWS_COUNTED)[1],
            BYTES=(_BYTES_SUMMED if one.sized else _BYTES_NOT_SUMMED)[0],
            CONCEALED_BYTES=(_BYTES_SUMMED if one.sized else _BYTES_NOT_SUMMED)[1],
            MS=(_MS_SUMMED if one.sized else _BYTES_NOT_SUMMED)[0],
            CONCEALED_MS=(_MS_SUMMED if one.sized else _BYTES_NOT_SUMMED)[1],
        )
        for one in kinds
    ]


#: Where a trigger hands a recompute over: a view no row ever sits in, with one INSTEAD OF trigger
#: per step. A trigger body cannot call a procedure, but it can write to a view, and the view's
#: trigger runs in the same transaction with the same conflict handling, so writing one row here
#: runs the step's statements exactly as if they were written in place.
#:
#: That is what keeps the schema small. Every half reads only the staged pairs, never the
#: trigger's own row, so written in place they were the same text in every trigger that ran them,
#: and every connection parses the whole schema before its first statement and again after any
#: change to it. One copy of each step, called from each trigger, is the same rules parsed once.
RECOMPUTE = "visibility_recompute"

_CREATE_RECOMPUTE = (
    "CREATE VIEW IF NOT EXISTS visibility_recompute (step, user_id) AS SELECT NULL, NULL WHERE 0"
)

#: A call of one step. `take` and `give` work on the staged pairs; `user` re-decides one user.
_RUN = "INSERT INTO visibility_recompute (step) VALUES ('<<STEP>>')"
_RUN_USER = "INSERT INTO visibility_recompute (step, user_id) VALUES ('user', <<USER>>)"


@dataclass(frozen=True)
class _Recompute:
    """The two halves of a recompute around the rows themselves, for one list of counted kinds:
    everything up to the rows going, and everything from the rows coming back. Around a
    membership change they run either side of the row landing, so the counts see the file leave
    with its old memberships and return with its new ones. Only these three ways of running the
    halves exist.

    A trigger CALLS a half rather than carrying it (see `RECOMPUTE`): the halves read nothing
    but the staged pairs, so one copy of each serves every trigger. `version_13` builds the
    triggers exactly as version 13 wrote them (every half in place, and no guard against a write
    that changes nothing), kept only so the step that replaces them can recognise them."""

    taken: tuple[str, ...]
    given: tuple[str, ...]
    fill_entity_counts: str
    fill_pair_counts: str
    version_13: bool = False

    def take(self) -> list[str]:
        """Take the staged pairs' counts away and drop their rows."""
        return list(self.taken) if self.version_13 else [_filled(_RUN, STEP="take")]

    def give(self) -> list[str]:
        """Re-decide the staged pairs and give their counts back."""
        return list(self.given) if self.version_13 else [_filled(_RUN, STEP="give")]

    def split(self, pairs: str) -> tuple[list[str], list[str]]:
        """`pairs` is a SELECT of (user_id, asset_id). It is one of the constants in this module
        and nothing else: the text reaches SQLite as DDL inside a trigger, or as a statement the
        backfill runs, and in both cases every value in it is a column reference or a trigger's
        row."""
        return [_CLEAR_PENDING, _filled(_STAGE_PAIRS, PAIRS=pairs), *self.take()], self.give()

    def whole(self, pairs: str) -> list[str]:
        """The statements that bring the rows for these pairs up to date: stage them, take their
        counts away, drop them, work out their places, re-decide them, give the counts back."""
        before, after = self.split(pairs)
        return before + after

    def user(self, user: str) -> list[str]:
        """Every file for one user re-decided from nothing (see `rebuilt`), by a call."""
        return self.rebuilt(user) if self.version_13 else [_filled(_RUN_USER, USER=user)]

    def rebuilt(self, user: str) -> list[str]:
        """Every file for one user, from nothing: its counts and rows dropped by user, its
        places worked out for every place there is, every file re-decided, the counts rebuilt."""
        return [
            _filled(_DROP_USER_STATS, USER=user),
            _filled(_DROP_USER_COUNTS, USER=user),
            _filled(_DROP_USER_PAIRS, USER=user),
            _filled(_DROP_USER_ROWS, USER=user),
            _CLEAR_PLACES,
            _filled(_filled(_PLACE_ROWS, PLACES=_ONE_USER_EVERY_PLACE), USER=user),
            _filled(_INSERT_USER_ROWS, USER=user),
            _CLEAR_PLACES,
            _filled(_USER_STATS, USER=user),
            _filled(_filled(self.fill_entity_counts, SCOPE=_USER_SCOPE), USER=user),
            _filled(_filled(self.fill_pair_counts, SCOPE=_USER_SCOPE), USER=user),
        ]


def _recompute_for(
    kinds: Sequence[Counted],
    pairs: Sequence[tuple[Counted, Counted]],
    fill_entity_counts: str,
    fill_pair_counts: str,
) -> _Recompute:
    return _Recompute(
        taken=(
            _STATS_TAKEN,
            *_each_kind(_COUNTS_TAKEN_ONE, kinds),
            *_each_pair(_PAIRS_TAKEN_ONE, pairs),
            _DELETE_STAGED,
        ),
        given=(
            _CLEAR_PLACES,
            _FILL_STAGED_PLACES,
            _INSERT_STAGED,
            _STATS_GIVEN,
            *_each_kind(_COUNTS_GIVEN_ONE, kinds),
            _COUNTS_EMPTIED,
            *_each_pair(_PAIRS_GIVEN_ONE, pairs),
            _PAIRS_EMPTIED,
            _CLEAR_PLACES,
            _CLEAR_PENDING,
        ),
        fill_entity_counts=fill_entity_counts,
        fill_pair_counts=fill_pair_counts,
    )


#: Where a user goes, in the statements that re-decide everything for one user.
USER = "<<USER>>"

_ONE_USER_EVERY_FILE = "SELECT <<USER>> AS user_id, a.id AS asset_id FROM assets a"

# Every place a copy sits in, from the copies themselves rather than from the folder table, so a
# copy whose root and folder disagree is decided on what its own row says.
_EVERY_PLACE = "SELECT DISTINCT root_id, folder_id FROM asset_locations"

_ONE_USER_EVERY_PLACE = _filled(
    "SELECT <<USER>> AS user_id, x.root_id, x.folder_id FROM (<<EVERY_PLACE>>) x",
    EVERY_PLACE=_EVERY_PLACE,
)

_DROP_USER_STATS = "DELETE FROM viewer_stats WHERE user_id = <<USER>>"
_DROP_USER_COUNTS = "DELETE FROM viewer_entity_counts WHERE user_id = <<USER>>"
_DROP_USER_PAIRS = "DELETE FROM viewer_pair_counts WHERE user_id = <<USER>>"
_DROP_USER_ROWS = "DELETE FROM viewer_assets WHERE user_id = <<USER>>"
_INSERT_USER_ROWS = _filled(_INSERT_ROWS, ROWS=_filled(_VERDICT_ROWS, PAIRS=_ONE_USER_EVERY_FILE))

#: Where a filter goes in the count rows: nothing for every user, a WHERE for one.
SCOPE = "<<SCOPE>>"

# Every count from the rows, once. The same five joins a recompute moves as sets. The rows are
# every verdict row, and the scope marker is where a user's rebuild narrows them to that user.
_ENTITY_COUNT_ROWS_ONE = (
    "SELECT v.user_id AS user_id, '<<KIND>>' AS kind, <<OBJECT>> AS object_id,"
    " <<FILES>> AS permitted, <<CONCEALED>> AS concealed,"
    " <<BYTES>> AS permitted_bytes, <<CONCEALED_BYTES>> AS concealed_bytes,"
    " <<MS>> AS permitted_ms, <<CONCEALED_MS>> AS concealed_ms"
    " FROM <<MEMBERS>>"
    " GROUP BY v.user_id, <<OBJECT>>"
)

#: What the rows above read: every verdict row, with the scope marker after the joins.
_EVERY_ROW = "viewer_assets v"


# Every pair from the rows, once. The same joins a recompute moves as sets.
_PAIR_COUNT_ROWS_ONE = (
    "SELECT v.user_id AS user_id, '<<KIND_A>>' AS kind_a, ma.<<COLUMN_A>> AS id_a,"
    " '<<KIND_B>>' AS kind_b, mb.<<COLUMN_B>> AS id_b,"
    " COUNT(*) AS permitted, COALESCE(SUM(v.concealed), 0) AS concealed"
    " FROM viewer_assets v JOIN <<TABLE_A>> ma ON ma.asset_id = v.asset_id"
    " JOIN <<TABLE_B>> mb ON mb.asset_id = v.asset_id<<SCOPE>>"
    " GROUP BY v.user_id, ma.<<COLUMN_A>>, mb.<<COLUMN_B>>"
)

_FILL_PAIR_COUNTS = (
    "INSERT INTO viewer_pair_counts (user_id, kind_a, id_a, kind_b, id_b, permitted, concealed)"
    " <<ROWS>>"
)


def _every_kind_joined(template: str, kinds: Sequence[Counted]) -> str:
    """The template for each counted kind over every verdict row, as one compound SELECT."""
    return " UNION ALL ".join(_each_kind(template, kinds, _EVERY_ROW, SCOPE))


# Keeps the scope marker: the backfill fills it with nothing, a user's rebuild with itself.
_FILL_ENTITY_COUNTS = (
    "INSERT INTO viewer_entity_counts (user_id, kind, object_id, permitted, concealed,"
    " permitted_bytes, concealed_bytes, permitted_ms, concealed_ms) <<ROWS>>"
)

#: The count rows filtered to one user.
_USER_SCOPE = " WHERE v.user_id = <<USER>>"

_USER_STATS = _filled(
    "INSERT INTO viewer_stats (user_id, permitted, concealed, permitted_bytes, concealed_bytes)"
    " SELECT <<USER>>, COUNT(*), COALESCE(SUM(v.concealed), 0), <<BYTES>>, <<CONCEALED_BYTES>>"
    "   FROM viewer_assets v JOIN assets a ON a.id = v.asset_id WHERE v.user_id = <<USER>>",
    BYTES=_BYTES_SUMMED[0],
    CONCEALED_BYTES=_BYTES_SUMMED[1],
)


# --- the pairs a change can reach ------------------------------------------------------------

_EVERY_USER_ONE_FILE = "SELECT u.id AS user_id, {asset} AS asset_id FROM users u"

_EVERY_USER_FILES_OF_USERNAME = (
    "SELECT DISTINCT u.id AS user_id, aa.asset_id"
    "  FROM users u, asset_usernames aa WHERE aa.username_id = {username}"
)

_EVERY_USER_UNDER_FOLDER = (
    "SELECT DISTINCT u.id AS user_id, l.asset_id"
    "  FROM users u, folder_ancestry an"
    "  JOIN asset_locations l ON l.folder_id = an.folder_id"
    " WHERE an.ancestor_id = {folder}"
)

# Every file a site reaches, for every user: the pairs a change to `sites.parent_id` can
# move. Bounded to the subtree of the site whose parent changed, which is the whole of what such a
# change can reach: a label joining a network changes who may see the label's files and the files
# of the labels under IT, and nothing else in the library.
# `noqa: S608`, as everywhere in this module: the only text put through here is module
# constants and a trigger's own `NEW`, never a value read at run time.
_EVERY_USER_UNDER_SITE = (
    "SELECT DISTINCT u.id AS user_id, reached.asset_id"  # noqa: S608
    "  FROM users u, (" + FILES_SITES_REACH.format(ancestors="= {site}") + ") reached"
)

_ONE_USER_ONE_FILE = "SELECT {user} AS user_id, {asset} AS asset_id"

_ONE_USER_IN_ROOT = (
    "SELECT DISTINCT {user} AS user_id, l.asset_id FROM asset_locations l WHERE l.root_id = {root}"
)

_ONE_USER_UNDER_FOLDER = (
    "SELECT DISTINCT {user} AS user_id, l.asset_id"
    "  FROM folder_ancestry an JOIN asset_locations l ON l.folder_id = an.folder_id"
    " WHERE an.ancestor_id = {folder}"
)

# What each kind of logical object reaches. The site reaches its files through its usernames,
# and through the usernames of every label under it, the same reach the search leaf and the wall
# count read. A share on a network that stopped at the network's own usernames would open up the
# one thing a network never has: a file filed directly under it. See `kernel/access/sites.py`.
_MEMBERS_OF = {
    "tag": "SELECT DISTINCT {user} AS user_id, asset_id FROM asset_tags WHERE tag_id = {object}",
    "person": (
        "SELECT DISTINCT {user} AS user_id, asset_id FROM asset_people WHERE person_id = {object}"
    ),
    "collection": (
        "SELECT DISTINCT {user} AS user_id, asset_id FROM collection_items"
        " WHERE collection_id = {object}"
    ),
    # `noqa: S608`: module constants only, filled with a trigger's own row references.
    "site": (
        "SELECT DISTINCT {user} AS user_id, reached.asset_id"  # noqa: S608
        "  FROM (" + FILES_SITES_REACH.format(ancestors="= {object}") + ") reached"
    ),
    "photo_set": (
        "SELECT DISTINCT {user} AS user_id, asset_id FROM photo_set_items"
        " WHERE photo_set_id = {object}"
    ),
    # A file carries at most one song, so no DISTINCT is needed; written as its neighbours are.
    "song": "SELECT DISTINCT {user} AS user_id, asset_id FROM song_files WHERE song_id = {object}",
}

#: The per-user tables that hide a logical object, keyed by the object kind they hide.
_HIDING_TABLES = {
    "tag": ("tag_user_state", "tag_id"),
    "person": ("person_user_state", "person_id"),
    "collection": ("collection_user_state", "collection_id"),
    "site": ("site_user_state", "site_id"),
    "photo_set": ("photo_set_user_state", "photo_set_id"),
    "song": ("song_user_state", "song_id"),
}

#: The keys of the two tables whose own columns move a Site's reach, for the guard on their BEFORE
#: halves (see `_not_already_held`). A username is one name per site, and one site user id per
#: site where the site said one; a site is one name, case folded (the guard compares under the
#: column's own collation, which is the left operand's).
_USERNAME_KEYS: tuple[tuple[str, ...], ...] = (
    ("id",),
    ("site_id", "name"),
    ("site_id", "number"),
)
_SITE_KEYS: tuple[tuple[str, ...], ...] = (("id",), ("name",))

#: The membership tables, and the column naming the file in each.
_MEMBERSHIP_TABLES = (
    "asset_tags",
    "asset_people",
    "collection_items",
    "asset_usernames",
    "photo_set_items",
    "song_files",
)


# --- the triggers ------------------------------------------------------------------------------


def _trigger(
    name: str,
    event: str,
    table: str,
    body: Sequence[str],
    *,
    when: str = "",
    timing: str = "AFTER",
) -> str:
    """One trigger. `body` is statements; `when` filters which rows fire it; `timing` is AFTER
    unless a change has to be seen before it lands."""
    guard = (" WHEN " + when) if when else ""
    return (
        "CREATE TRIGGER IF NOT EXISTS "
        + name
        + " "
        + timing
        + " "
        + event
        + " ON "
        + table
        + guard
        + " BEGIN\n"
        + ";\n".join(body)
        + ";\nEND"
    )


def _not_already_held(table: str, keys: Sequence[Sequence[str]], *, updating: bool) -> str:
    """The guard on a BEFORE trigger: no row already holds what this change would write.

    For an insert, no row has the new row's values in any of the table's keys. For an update, no
    OTHER row has them: the row being updated holds its own key and must not count against
    itself. What the engine will refuse or ignore, the guard keeps the BEFORE half out of; what
    will land, it lets through. The text is column names and the trigger's own NEW and OLD, and
    nothing read at run time.
    """
    if not keys:
        raise ValueError(f"{table!r} declares no keys to guard its triggers on")
    clauses: list[str] = []
    for key in keys:
        same = " AND ".join(column + " = NEW." + column for column in key)
        if updating:
            itself = " AND ".join(column + " = OLD." + column for column in key)
            same = same + " AND NOT (" + itself + ")"
        clauses.append(_filled(_NOT_HELD, TABLE=table, SAME=same))
    return " AND ".join(clauses)


#: A row already holding what a change would write. Filled per watched table and key.
_NOT_HELD = "NOT EXISTS (SELECT 1 FROM <<TABLE>> WHERE <<SAME>>)"

#: The columns of a grant the verdict reads.
_GRANT_READ = ("object_type", "object_id", "subject_user_id", "effect")


def _changed(columns: Sequence[str]) -> str:
    """The guard on an UPDATE trigger: any of these columns moved. Column names only."""
    return " OR ".join("OLD." + column + " IS NOT NEW." + column for column in columns)


def _update_columns(event: str) -> list[str] | None:
    """The columns an UPDATE event names, in order, or None for a bare UPDATE (every column)."""
    head = "UPDATE OF "
    if not event.startswith(head):
        return None
    return [column.strip() for column in event[len(head) :].split(",")]


def _watching(kinds: Sequence[Counted]) -> dict[str, Counted]:
    """One watcher per table, whatever number of kinds are counted over it.

    The triggers on a table re-decide the whole file, which moves EVERY kind's count for it, so
    two kinds over one table want one set of triggers that fires on either kind's columns: two
    sets would recompute each file twice and, by name, could not both exist. The UPDATE event is
    the union of the kinds' columns in the order first named (a bare UPDATE, which watches every
    column, wins over any list); the keys are the table's, which `register_counted` holds the kinds
    to agreeing about.
    """
    merged: dict[str, Counted] = {}
    for one in kinds:
        held = merged.get(one.table)
        if held is None:
            merged[one.table] = one
            continue
        mine, theirs = _update_columns(held.update), _update_columns(one.update)
        if mine is None or theirs is None:
            event = "UPDATE"
        else:
            event = "UPDATE OF " + ", ".join(dict.fromkeys([*mine, *theirs]))
        merged[one.table] = Counted(
            held.kind, held.source, held.column, held.table, event, held.keys
        )
    return merged


def _triggers(halves: _Recompute, watched: Sequence[Counted]) -> list[tuple[str, str, str]]:
    """Every trigger this component keeps, as (name, DDL).

    Built from the tables above rather than written out one by one, so a membership table cannot be
    left without its triggers by being forgotten: the list of membership tables is the list of
    triggers. The gate that proves every table the verdict reads is here reads the verdict's own
    text.
    """
    made: list[tuple[str, str, str]] = []

    def add(name: str, ddl: str) -> None:
        # The table is the word after ON in the header this module wrote a moment ago.
        made.append((name, ddl.split(" ON ", 1)[1].split()[0], ddl))

    def add_pair(
        stem: str,
        event: str,
        table: str,
        before: Sequence[str],
        after: Sequence[str],
        *,
        when: str = "",
    ) -> None:
        # The guard is the BEFORE half's alone: AFTER fires only for a change that landed.
        add(
            stem + "_before",
            _trigger(stem + "_before", event, table, before, when=when, timing="BEFORE"),
        )
        add(stem, _trigger(stem, event, table, after))

    # The folder tree: ancestry rows, and the copies under a moved folder.
    add(
        "vis_folders_insert",
        _trigger(
            "vis_folders_insert",
            "INSERT",
            "folders",
            [
                # Itself, when it is reachable: no parent, or a parent the walk reached in the
                # same root. A folder whose parent is unreachable gets no rows and reads as
                # denied everywhere, which is the fail-closed answer the tree always gave.
                "INSERT INTO folder_ancestry (folder_id, ancestor_id, depth)"
                " SELECT NEW.id, NEW.id, 0"
                "  WHERE NEW.parent_id IS NULL"
                "     OR EXISTS (SELECT 1 FROM folders p"
                "                  JOIN folder_ancestry pa ON pa.folder_id = p.id"
                "                                        AND pa.ancestor_id = p.id"
                "                 WHERE p.id = NEW.parent_id AND p.root_id = NEW.root_id)",
                "INSERT INTO folder_ancestry (folder_id, ancestor_id, depth)"
                " SELECT NEW.id, pa.ancestor_id, pa.depth + 1"
                "   FROM folder_ancestry pa JOIN folders p ON p.id = pa.folder_id"
                "  WHERE p.id = NEW.parent_id AND p.root_id = NEW.root_id",
            ],
        ),
    )
    # A folder going. The rows and counts of everything under it are taken away BEFORE the row
    # goes, while the ancestry still says what was under it. The keys then cascade: the child
    # folders go the same way, the copies that sat in it are set to no folder, and each copy's
    # own trigger finds nothing left to take and puts the file's rows and counts back as they
    # now are. Left to the cascade alone, the copy's trigger might run after the ancestry rows
    # had gone, and the folders above would go on counting a copy that is no longer under them.
    add(
        "vis_folders_delete_before",
        _trigger(
            "vis_folders_delete_before",
            "DELETE",
            "folders",
            [
                _CLEAR_PENDING,
                _filled(_STAGE_PAIRS, PAIRS=_EVERY_USER_UNDER_FOLDER.format(folder="OLD.id")),
                *halves.take(),
                _CLEAR_PENDING,
            ],
            timing="BEFORE",
        ),
    )
    add(
        "vis_folders_move",
        _trigger(
            "vis_folders_move",
            "UPDATE OF parent_id, root_id",
            "folders",
            [
                # Which copies this reaches, read while the ancestry still says what is under it,
                # and their counts taken away against the folders it USED to sit under.
                _CLEAR_PENDING,
                _filled(_STAGE_PAIRS, PAIRS=_EVERY_USER_UNDER_FOLDER.format(folder="NEW.id")),
                *halves.take(),
                # A folder moved somewhere the walk from its root cannot arrive at (under one of
                # its own descendants, under a folder in another root, or under a folder the walk
                # never reached) takes its whole subtree out of reach: every row of it goes, and
                # what is under it reads as denied and concealed. Nothing here refuses the write;
                # a restored database can already hold such a chain, and the answer is the same.
                # Moving such a subtree back somewhere reachable is the one change this cannot
                # follow, because putting a whole subtree's rows back needs recursion; the boot
                # pass (`keep_true`) notices and rebuilds.
                "DELETE FROM folder_ancestry"
                " WHERE folder_id IN (SELECT folder_id FROM folder_ancestry"
                "                      WHERE ancestor_id = NEW.id)"
                "   AND NOT (NEW.parent_id IS NULL"
                "            OR (EXISTS (SELECT 1 FROM folders p"
                "                          JOIN folder_ancestry pa ON pa.folder_id = p.id"
                "                                                AND pa.ancestor_id = p.id"
                "                         WHERE p.id = NEW.parent_id AND p.root_id = NEW.root_id)"
                "                AND NOT EXISTS (SELECT 1 FROM folder_ancestry s"
                "                                 WHERE s.ancestor_id = NEW.id"
                "                                   AND s.folder_id = NEW.parent_id)))",
                # Otherwise: everything the subtree pointed at above the moved folder goes; what it
                # points at within itself stays. Then the whole subtree is joined to the new
                # parent's chain, each row's distance being its distance below the moved folder
                # plus one plus the parent's distance from its own ancestor.
                "DELETE FROM folder_ancestry"
                " WHERE folder_id IN (SELECT folder_id FROM folder_ancestry"
                "                      WHERE ancestor_id = NEW.id)"
                "   AND ancestor_id NOT IN (SELECT folder_id FROM folder_ancestry"
                "                            WHERE ancestor_id = NEW.id)",
                "INSERT INTO folder_ancestry (folder_id, ancestor_id, depth)"
                " SELECT s.folder_id, pa.ancestor_id, s.depth + 1 + pa.depth"
                "   FROM folder_ancestry s, folder_ancestry pa"
                "  WHERE s.ancestor_id = NEW.id"
                "    AND pa.folder_id = NEW.parent_id"
                "    AND EXISTS (SELECT 1 FROM folders p"
                "                 WHERE p.id = NEW.parent_id AND p.root_id = NEW.root_id)",
                *halves.give(),
            ],
        ),
    )

    # A file going. Its rows and counts are taken away BEFORE the row goes, while its copies
    # and memberships are still there to say what the counts were. The foreign keys then cascade
    # through its copies and memberships, and each of those triggers finds nothing left to take
    # and nothing to put back; left to the cascade on `viewer_assets` alone, the rows would go
    # without their counts, and in an order the engine decides.
    add(
        "vis_assets_delete_before",
        _trigger(
            "vis_assets_delete_before",
            "DELETE",
            "assets",
            halves.split(_EVERY_USER_ONE_FILE.format(asset="OLD.id"))[0],
            timing="BEFORE",
        ),
    )

    # A file's SIZE or running time changing (the probe writes the second after the file is
    # counted): the file taken away with its old values and given back with its new ones.
    add_pair(
        "vis_assets_size",
        "UPDATE OF size_bytes, duration_ms",
        "assets",
        *halves.split(_EVERY_USER_ONE_FILE.format(asset="NEW.id")),
        when="OLD.size_bytes IS NOT NEW.size_bytes OR OLD.duration_ms IS NOT NEW.duration_ms",
    )

    # A copy of a file, or a membership of one, appearing, changing or going.
    #
    # In two halves around the change rather than one after it. The verdict rows of the file are
    # taken away BEFORE the row lands and put back AFTER, so the counts see the file leave with
    # its old copies and memberships and return with its new ones. One trigger after the fact
    # would subtract a membership it had never added. The tables are the verdict's own membership
    # tables and every table a counted kind reads, which overlap; each is watched once.
    #
    # The BEFORE half of an insert or an update is guarded on the table's keys (`Counted.keys`):
    # the engine fires it before deciding whether the row will land, and a write it then ignores
    # as a duplicate would leave the taken half standing with nothing to put it back. A delete
    # needs no guard: a row that is not there fires nothing.
    by_table = _watching(watched)
    unwatched = sorted(set(_MEMBERSHIP_TABLES) - set(by_table))
    if unwatched:  # pragma: no cover (an edit that took a membership table out of the kinds)
        raise RuntimeError(f"membership tables {unwatched} are not counted, so carry no keys")
    for table, one in by_table.items():
        pairs = _EVERY_USER_ONE_FILE.format(asset="NEW.asset_id")
        add_pair(
            "vis_" + table + "_insert",
            "INSERT",
            table,
            *halves.split(pairs),
            when=_not_already_held(table, one.keys, updating=False),
        )
        pairs = _EVERY_USER_ONE_FILE.format(asset="OLD.asset_id")
        add_pair("vis_" + table + "_delete", "DELETE", table, *halves.split(pairs))
        both = (
            _EVERY_USER_ONE_FILE.format(asset="OLD.asset_id")
            + " UNION ALL "
            + _EVERY_USER_ONE_FILE.format(asset="NEW.asset_id")
        )
        add_pair(
            "vis_" + table + "_update",
            one.update,
            table,
            *halves.split(both),
            when=_not_already_held(table, one.keys, updating=True),
        )
    # A username changing site moves every file it holds to another site's grants and, since
    # a Site's files are counted (`_SITE_REACHED`), from one site's count to another's.
    #
    # A PAIR, not one AFTER trigger. The verdict rows are the same either way, because the
    # recompute reads the new site fresh; a stored SITE count is not, because the take half reads
    # the column being changed: run after the change it would subtract from the new site and leave
    # the old one counting for ever. So the files are taken while the username
    # still names the old site and given back once it names the new one. Guarded on the table's
    # keys like every BEFORE half (see `Counted.keys`): an `UPDATE OR IGNORE` that collides with
    # another username on the new site fires BEFORE and never AFTER.
    add_pair(
        "vis_usernames_site",
        "UPDATE OF site_id",
        "usernames",
        *halves.split(_EVERY_USER_FILES_OF_USERNAME.format(username="NEW.id")),
        when=_not_already_held("usernames", _USERNAME_KEYS, updating=True),
    )
    # A username going. Its files are taken away BEFORE the row goes, while it still names its
    # site. The filings then cascade, and a trigger fired by that cascade cannot see the username (a
    # child's trigger under a foreign-key action reads the parent row as already gone), so the take
    # there finds nothing to subtract from the sites, which would
    # leave every site above the username counting the files for ever. Each filing's own trigger
    # then puts its file back as it now is; the same shape as a folder going, above.
    add(
        "vis_usernames_delete_before",
        _trigger(
            "vis_usernames_delete_before",
            "DELETE",
            "usernames",
            [
                _CLEAR_PENDING,
                _filled(
                    _STAGE_PAIRS,
                    PAIRS=_EVERY_USER_FILES_OF_USERNAME.format(username="OLD.id"),
                ),
                *halves.take(),
                _CLEAR_PENDING,
            ],
            timing="BEFORE",
        ),
    )
    # A label joining a network, leaving one, or moving between two.
    #
    # The same kind of change as the one above: a site reaches what its labels released, so a share
    # or a hide on a network decides files that are filed under the labels. Setting
    # `sites.parent_id` therefore changes who may see those files, and without this the stored
    # verdict would stay as it was when the grant was made.
    #
    # A PAIR, for the reason given over `vis_usernames_site`: the verdict rows do not care which
    # side of the change the take runs, and a stored Site count does: the network a label LEFT has
    # to lose the label's files, which only a take made before the change can see. The subtree
    # BELOW the moved site does not move with it, so the one staged set serves both halves; only
    # what is above it changes, and that is read fresh by the recompute. Guarded on the table's
    # keys, as every BEFORE half is.
    #
    # DELETING a network is covered by this and needs no trigger of its own: the column is
    # `ON DELETE SET NULL`, and a foreign-key action fires user triggers, so each orphaned label
    # arrives here as an ordinary update of its parent. Verified on 3.45.1 and 3.50.4.
    #
    # An INSERT needs none either. A site row must exist before a username can reference it, so
    # a site that has just been made reaches no files at all.
    add_pair(
        "vis_sites_parent",
        "UPDATE OF parent_id",
        "sites",
        *halves.split(_EVERY_USER_UNDER_SITE.format(site="NEW.id")),
        when=_not_already_held("sites", _SITE_KEYS, updating=True),
    )
    # A site going. What it reaches is taken away BEFORE the row goes, while the walk up from
    # its labels still passes through it, so the networks above it lose its files. The foreign
    # keys then set its usernames' site and its labels' parent to nothing, and each of those
    # arrives at the pair above or at `vis_usernames_site` and puts its files back as they now are.
    # Without this the take in those would run with the site already gone (see the username's
    # delete, above) and every network above it would go on counting what it had.
    add(
        "vis_sites_delete_before",
        _trigger(
            "vis_sites_delete_before",
            "DELETE",
            "sites",
            [
                _CLEAR_PENDING,
                _filled(_STAGE_PAIRS, PAIRS=_EVERY_USER_UNDER_SITE.format(site="OLD.id")),
                *halves.take(),
                _CLEAR_PENDING,
            ],
            timing="BEFORE",
        ),
    )

    # A grant. One trigger per kind of object, so each reaches exactly the files that object
    # decides for. The only update the write door makes is the re-grant's upsert, which changes
    # nothing, so an update fires only when it moves something the verdict reads, and then both
    # what the row named before (`_was`) and what it names now are decided again.
    grant_events = [("INSERT", "NEW", ""), ("DELETE", "OLD", ""), ("UPDATE", "NEW", "")]
    if not halves.version_13:
        grant_events.append(("UPDATE", "OLD", "_was"))
    for event, row, tail in grant_events:
        user = row + ".subject_user_id"
        obj = row + ".object_id"
        kinds: dict[str, Sequence[str]] = {
            "global": halves.user(user),
            "root": halves.whole(_ONE_USER_IN_ROOT.format(user=user, root=obj)),
            "folder": halves.whole(_ONE_USER_UNDER_FOLDER.format(user=user, folder=obj)),
            "item": halves.whole(_ONE_USER_ONE_FILE.format(user=user, asset=obj)),
        }
        for kind, members in _MEMBERS_OF.items():
            kinds[kind] = halves.whole(members.format(user=user, object=obj))
        for kind, body in kinds.items():
            # THE KIND, in the trigger's name AND in what it watches for: the STORED spelling of
            # `object_type`. A trigger watching for a value no row carries never fires, and
            # nothing fails: a share simply stops changing what anybody can see. The access
            # component's version 4 step rewrote the older word for a Site in the rows and the
            # CHECK, and the three triggers that carried the old name are retired below.
            name = "vis_acl_grants_" + event.lower() + "_" + kind + tail
            when = row + ".object_type = '" + kind + "'"
            if event == "UPDATE" and not halves.version_13:
                when = when + " AND (" + _changed(_GRANT_READ) + ")"
            add(name, _trigger(name, event, "acl_grants", body, when=when))

    # Something hidden or shown again, for one user. An update fires only when it moves the flag
    # or the row (a merge moves a hide from the thing going to the one kept, and only the row's
    # key changes), and then what the row named before and what it names now are both decided
    # again. A write that leaves all three as they were (a hide made twice, a rating carried
    # beside the flag) decides nothing.
    hiding: list[tuple[str, str, str]] = [
        ("asset_user_state", "asset_id", _ONE_USER_ONE_FILE.replace("{asset}", "{object}")),
        ("root_user_state", "root_id", _ONE_USER_IN_ROOT.replace("{root}", "{object}")),
        ("folder_user_state", "folder_id", _ONE_USER_UNDER_FOLDER.replace("{folder}", "{object}")),
    ]
    hiding += [
        (table, column, _MEMBERS_OF[kind]) for kind, (table, column) in _HIDING_TABLES.items()
    ]
    for table, column, members in hiding:
        for event, row in (("INSERT", "NEW"), ("DELETE", "OLD")):
            name = "vis_" + table + "_" + event.lower()
            body = halves.whole(members.format(user=row + ".user_id", object=row + "." + column))
            add(name, _trigger(name, event, table, body))
        name = "vis_" + table + "_update"
        reached = members.format(user="NEW.user_id", object="NEW." + column)
        if halves.version_13:
            add(name, _trigger(name, "UPDATE OF hidden", table, halves.whole(reached)))
            continue
        read = ("hidden", "user_id", column)
        was = members.format(user="OLD.user_id", object="OLD." + column)
        add(
            name,
            _trigger(
                name,
                "UPDATE OF " + ", ".join(read),
                table,
                halves.whole(was + " UNION ALL " + reached),
                when=_changed(read),
            ),
        )

    # A user arriving, or changing what they are.
    add(
        "vis_users_insert",
        _trigger("vis_users_insert", "INSERT", "users", halves.user("NEW.id")),
    )
    add(
        "vis_users_role",
        _trigger("vis_users_role", "UPDATE OF role", "users", halves.user("NEW.id")),
    )

    # A pair crossing nought, which moves the partner totals of both its sides.
    for name, ddl in _partner_triggers():
        add(name, ddl)

    # The steps every trigger above calls, each written once (see `RECOMPUTE`).
    if not halves.version_13:
        for step, body in (
            ("take", halves.taken),
            ("give", halves.given),
            ("user", halves.rebuilt("NEW.user_id")),
        ):
            name = "vis_recompute_" + step
            add(
                name,
                _trigger(
                    name,
                    "INSERT",
                    RECOMPUTE,
                    body,
                    when="NEW.step = '" + step + "'",
                    timing="INSTEAD OF",
                ),
            )

    return made


@dataclass(frozen=True)
class _Built:
    """Everything that depends on the list of counted kinds, built once from it."""

    recompute: _Recompute
    fill_every_entity_count: str
    entity_count_differences: str
    fill_every_pair_count: str
    pair_count_differences: str
    fill_loop_counts: tuple[str, ...]
    fill_site_counts: tuple[str, ...]
    triggers: tuple[tuple[str, str, str], ...]
    #: The triggers as version 13 wrote them, for the step that replaces them to recognise.
    version_13_triggers: tuple[tuple[str, str, str], ...]
    drop_triggers: tuple[str, ...]


@functools.cache
def _built() -> _Built:
    kinds = counted()
    rows = _every_kind_joined(_ENTITY_COUNT_ROWS_ONE, kinds)
    fill = _filled(_FILL_ENTITY_COUNTS, ROWS=rows)
    # The pairs name kernel kinds only, so a slice's registration cannot remove one from under them.
    by_kind = {one.kind: one for one in kinds}
    pairs = tuple((by_kind[a], by_kind[b]) for a, b in PAIRED)
    pair_rows = " UNION ALL ".join(_each_pair(_PAIR_COUNT_ROWS_ONE, pairs))
    fill_pairs = _filled(_FILL_PAIR_COUNTS, ROWS=pair_rows)
    recompute = _recompute_for(kinds, pairs, fill, fill_pairs)
    triggers = tuple(_triggers(recompute, kinds))
    version_13_triggers = tuple(_triggers(replace(recompute, version_13=True), kinds))
    # The v6 step's fill: the marks' own counts and every pair a mark is a side of, from the stored
    # rows, after whatever an interrupted boot left of them is dropped. The same row templates the
    # whole fill is made of, so the two cannot disagree about what a mark's count is.
    loop = by_kind[_LOOP_KIND]
    loop_pairs = tuple(pair for pair in pairs if loop in pair)
    fill_loop_counts = (
        _filled(_DROP_KIND_COUNTS, KIND=loop.kind),
        _filled(_DROP_KIND_PAIRS, KIND=loop.kind),
        _filled(
            _filled(_FILL_ENTITY_COUNTS, ROWS=_every_kind_joined(_ENTITY_COUNT_ROWS_ONE, [loop])),
            SCOPE="",
        ),
        _filled(
            _filled(
                _FILL_PAIR_COUNTS,
                ROWS=" UNION ALL ".join(_each_pair(_PAIR_COUNT_ROWS_ONE, loop_pairs)),
            ),
            SCOPE="",
        ),
    )
    # The v8 step's fill: the Sites' own counts from the stored rows, after whatever an interrupted
    # boot left of them is dropped. The same row template the whole fill is made of.
    site = by_kind[_SITE_KIND]
    fill_site_counts = (
        _filled(_DROP_KIND_COUNTS, KIND=site.kind),
        _filled(
            _filled(_FILL_ENTITY_COUNTS, ROWS=_every_kind_joined(_ENTITY_COUNT_ROWS_ONE, [site])),
            SCOPE="",
        ),
    )
    return _Built(
        recompute=recompute,
        fill_every_entity_count=_filled(fill, SCOPE=""),
        entity_count_differences=_filled(_ENTITY_COUNT_DIFFERENCES, ROWS=_filled(rows, SCOPE="")),
        fill_every_pair_count=_filled(fill_pairs, SCOPE=""),
        pair_count_differences=_filled(_PAIR_COUNT_DIFFERENCES, ROWS=_filled(pair_rows, SCOPE="")),
        fill_loop_counts=fill_loop_counts,
        fill_site_counts=fill_site_counts,
        triggers=triggers,
        version_13_triggers=version_13_triggers,
        drop_triggers=tuple(
            _filled("DROP TRIGGER IF EXISTS <<NAME>>", NAME=name)
            for name in (*(name for name, _table, _ddl in triggers), *RETIRED_TRIGGERS)
        ),
    )


def triggers() -> tuple[tuple[str, str, str], ...]:
    """Every trigger this build keeps, as (name, table, DDL)."""
    return _built().triggers


def triggered_tables() -> frozenset[str]:
    """The tables that carry triggers. The gate that proves this is complete derives the tables
    the verdict READS from the verdict's own text and checks each is here."""
    return frozenset(table for _name, table, _ddl in triggers())


#: Triggers earlier builds kept under these names and this one does not. A database carrying one
#: would keep running it, so they are dropped wherever this build's are made. Add to this list
#: when a trigger is renamed or removed: a name is the only handle a trigger can be dropped by,
#: and this module never builds a statement from a name it read at run time, so one it does not
#: know is reported at boot (`keep_true`) and not dropped.
RETIRED_TRIGGERS = (
    "vis_stats_insert",
    "vis_stats_delete",
    "vis_stats_update",
    # The eight the storage rename moved. A database upgraded across catalog version 50 has them
    # under these names, on the renamed tables, with bodies SQLite rewrote as it renamed, so they
    # are live triggers this build does not know, and `keep_true` would report one every boot and
    # rebuild every stored answer for ever. Named here, they are dropped instead.
    "vis_accounts_platform",
    "vis_platforms_parent",
    "vis_acl_grants_insert_platform",
    "vis_acl_grants_delete_platform",
    "vis_acl_grants_update_platform",
    "vis_platform_user_state_insert",
    "vis_platform_user_state_delete",
    "vis_platform_user_state_update",
    # The nine the username rename moved (catalog version 59), for the same reason.
    "vis_accounts_site",
    "vis_accounts_site_before",
    "vis_accounts_delete_before",
    "vis_asset_accounts_insert",
    "vis_asset_accounts_insert_before",
    "vis_asset_accounts_delete",
    "vis_asset_accounts_delete_before",
    "vis_asset_accounts_update",
    "vis_asset_accounts_update_before",
)


# --- the backfill --------------------------------------------------------------------------------

_CLEAR_ANCESTRY = "DELETE FROM folder_ancestry"

# Every folder reachable from a root, with its whole chain. Recursive here and nowhere else: this
# runs from Python, once, and the triggers keep the table from then on. `reach` is the set of
# folders a walk down from the roots arrives at (UNION, so a loop in a restored database ends the
# walk instead of hanging it); `chain` walks up from each of those to the root. A folder whose
# parent sits in another root, or that is part of a loop, is never reached and gets no rows.
_FILL_ANCESTRY = """
WITH RECURSIVE reach(folder_id, root_id) AS (
  SELECT f.id, f.root_id FROM folders f WHERE f.parent_id IS NULL
  UNION
  SELECT f.id, f.root_id
    FROM folders f JOIN reach r ON f.parent_id = r.folder_id AND f.root_id = r.root_id
),
chain(folder_id, ancestor_id, depth) AS (
  SELECT folder_id, folder_id, 0 FROM reach
  UNION ALL
  SELECT c.folder_id, f.parent_id, c.depth + 1
    FROM chain c JOIN folders f ON f.id = c.ancestor_id
   WHERE f.parent_id IS NOT NULL
)
INSERT INTO folder_ancestry (folder_id, ancestor_id, depth)
SELECT folder_id, ancestor_id, depth FROM chain
"""

_CLEAR_ROWS = "DELETE FROM viewer_assets"

_EVERY_USER_EVERY_FILE = "SELECT u.id AS user_id, a.id AS asset_id FROM users u, assets a"

_EVERY_USER_EVERY_PLACE = _filled(
    "SELECT u.id AS user_id, x.root_id, x.folder_id FROM users u, (<<EVERY_PLACE>>) x",
    EVERY_PLACE=_EVERY_PLACE,
)

_FILL_EVERY_PLACE = _filled(_PLACE_ROWS, PLACES=_EVERY_USER_EVERY_PLACE)

_FILL_ROWS = _filled(_INSERT_ROWS, ROWS=_filled(_VERDICT_ROWS, PAIRS=_EVERY_USER_EVERY_FILE))

_CLEAR_ENTITY_COUNTS = "DELETE FROM viewer_entity_counts"

_CLEAR_PAIR_COUNTS = "DELETE FROM viewer_pair_counts"

# One kind's counts, and every pair it is a side of. Filled with a kind this module names.
_DROP_KIND_COUNTS = "DELETE FROM viewer_entity_counts WHERE kind = '<<KIND>>'"
_DROP_KIND_PAIRS = "DELETE FROM viewer_pair_counts WHERE kind_a = '<<KIND>>' OR kind_b = '<<KIND>>'"


_CLEAR_STATS = "DELETE FROM viewer_stats"

_FILL_STATS = """
INSERT INTO viewer_stats (user_id, permitted, concealed, permitted_bytes, concealed_bytes)
SELECT u.id, COUNT(v.asset_id), COALESCE(SUM(v.concealed), 0),
       COALESCE(SUM(a.size_bytes), 0),
       COALESCE(SUM(CASE WHEN v.concealed = 1 THEN a.size_bytes END), 0)
  FROM users u LEFT JOIN viewer_assets v ON v.user_id = u.id
  LEFT JOIN assets a ON a.id = v.asset_id
 GROUP BY u.id
"""

_TRIGGERS_PRESENT = (
    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'vis_%'"
)


def _body_of(ddl: str) -> str:
    """A trigger's text from its timing word onward, which is the part that can silently change.

    The engine keeps a trigger's CREATE statement as written, apart from the IF NOT EXISTS the
    module writes and the engine drops. Comparing from the timing word onward sidesteps that
    difference and compares what matters: the event, the table, the guard and the body.
    """
    return " ".join(re.split(r"\s(?:BEFORE|AFTER|INSTEAD OF)\s", ddl, maxsplit=1)[1].split())


async def _drop_triggers(connection: Connection) -> None:
    for statement in _built().drop_triggers:
        await connection.execute(statement)


async def _create_triggers(connection: Connection) -> None:
    await connection.execute(_CREATE_RECOMPUTE)
    for _name, _table, ddl in _built().triggers:
        await connection.execute(ddl)


async def refresh_everything(connection: Connection) -> None:
    """Rebuild every stored answer from the facts. The backfill, and the repair.

    Without the triggers in the way: a bulk rebuild of two million rows must not ring the counters
    two million times when one GROUP BY says the same thing afterwards.
    """
    await _drop_triggers(connection)
    await connection.execute(_CLEAR_ANCESTRY)
    await connection.execute(_FILL_ANCESTRY)
    await connection.execute(_CLEAR_PENDING)
    await connection.execute(_CLEAR_ROWS)
    await connection.execute(_CLEAR_PLACES)
    await connection.execute(_FILL_EVERY_PLACE)
    await connection.execute(_FILL_ROWS)
    await connection.execute(_CLEAR_PLACES)
    await connection.execute(_CLEAR_STATS)
    await connection.execute(_FILL_STATS)
    await connection.execute(_CLEAR_ENTITY_COUNTS)
    await connection.execute(_built().fill_every_entity_count)
    await connection.execute(_CLEAR_PAIR_COUNTS)
    await connection.execute(_built().fill_every_pair_count)
    await connection.execute(_CLEAR_PARTNER_COUNTS)
    await connection.execute(_FILL_PARTNER_COUNTS)
    await _create_triggers(connection)


# What is stored, compared with what the facts say. Empty when the two agree. Read by the gate
# that proves the triggers, and by a check over a whole library.
#
# The expected rows go into a temporary table first. Written as one statement with the recompute
# as a common table expression referenced twice, the engine re-runs it per row of the second
# comparison, which turns a check of seconds into one of minutes and a gigabyte.
_EXPECTED_ROWS = _filled(
    "CREATE TEMP TABLE visibility_expected AS <<ROWS>>",
    ROWS=_filled(_VERDICT_ROWS, PAIRS=_EVERY_USER_EVERY_FILE),
)

_DROP_EXPECTED_ROWS = "DROP TABLE IF EXISTS visibility_expected"

_DIFFERENCES = """
SELECT 'missing' AS what, user_id, asset_id, concealed FROM (
  SELECT user_id, asset_id, concealed FROM visibility_expected
  EXCEPT
  SELECT user_id, asset_id, concealed FROM viewer_assets
)
UNION ALL
SELECT 'extra' AS what, user_id, asset_id, concealed FROM (
  SELECT user_id, asset_id, concealed FROM viewer_assets
  EXCEPT
  SELECT user_id, asset_id, concealed FROM visibility_expected
)
"""

# The expected rows sit inside a subquery on both sides of each EXCEPT: compound operators bind
# left to right, so `stored EXCEPT tag UNION ALL person ...` would add every other kind back.
_ENTITY_COUNT_DIFFERENCES = _filled(
    "SELECT 'count missing' AS what, user_id || '/' || kind || '/' || object_id,"
    " <<SHOWN>>, permitted FROM ("
    "SELECT <<COLUMNS>> FROM (<<ROWS>>)"
    " EXCEPT SELECT <<COLUMNS>> FROM viewer_entity_counts)"
    " UNION ALL "
    "SELECT 'count extra', user_id || '/' || kind || '/' || object_id,"
    " <<SHOWN>>, permitted FROM ("
    "SELECT <<COLUMNS>> FROM viewer_entity_counts EXCEPT SELECT <<COLUMNS>> FROM (<<ROWS>>))",
    COLUMNS="user_id, kind, object_id, permitted, concealed, permitted_bytes, concealed_bytes,"
    " permitted_ms, concealed_ms",
    SHOWN="permitted || '/' || concealed || '/' || permitted_bytes || '/' || concealed_bytes"
    " || '/' || permitted_ms || '/' || concealed_ms",
)

# The same comparison for the pairs, and the same subqueries for the same reason.
_PAIR_COUNT_DIFFERENCES = (
    "SELECT 'pair missing' AS what,"
    " user_id || '/' || kind_a || '/' || id_a || '/' || kind_b || '/' || id_b,"
    " permitted || '/' || concealed, permitted FROM ("
    "SELECT user_id, kind_a, id_a, kind_b, id_b, permitted, concealed FROM (<<ROWS>>)"
    " EXCEPT SELECT user_id, kind_a, id_a, kind_b, id_b, permitted, concealed"
    " FROM viewer_pair_counts)"
    " UNION ALL "
    "SELECT 'pair extra',"
    " user_id || '/' || kind_a || '/' || id_a || '/' || kind_b || '/' || id_b,"
    " permitted || '/' || concealed, permitted FROM ("
    "SELECT user_id, kind_a, id_a, kind_b, id_b, permitted, concealed FROM viewer_pair_counts"
    " EXCEPT SELECT user_id, kind_a, id_a, kind_b, id_b, permitted, concealed FROM (<<ROWS>>))"
)

_STATS_DIFFERENCES = """
SELECT 'stats' AS what, u.id AS user_id, NULL AS asset_id, NULL AS concealed
  FROM users u
  LEFT JOIN viewer_stats s ON s.user_id = u.id
 WHERE COALESCE(s.permitted, 0) != (SELECT COUNT(*) FROM viewer_assets v WHERE v.user_id = u.id)
    OR COALESCE(s.concealed, 0) != (SELECT COALESCE(SUM(concealed), 0) FROM viewer_assets v
                                     WHERE v.user_id = u.id)
    OR COALESCE(s.permitted_bytes, 0) != (SELECT COALESCE(SUM(a.size_bytes), 0)
                                            FROM viewer_assets v JOIN assets a ON a.id = v.asset_id
                                           WHERE v.user_id = u.id)
    OR COALESCE(s.concealed_bytes, 0) != (SELECT COALESCE(SUM(a.size_bytes), 0)
                                            FROM viewer_assets v JOIN assets a ON a.id = v.asset_id
                                           WHERE v.user_id = u.id AND v.concealed = 1)
"""

_ANCESTRY_DIFFERENCES = """
WITH RECURSIVE reach(folder_id, root_id) AS (
  SELECT f.id, f.root_id FROM folders f WHERE f.parent_id IS NULL
  UNION
  SELECT f.id, f.root_id
    FROM folders f JOIN reach r ON f.parent_id = r.folder_id AND f.root_id = r.root_id
),
chain(folder_id, ancestor_id, depth) AS (
  SELECT folder_id, folder_id, 0 FROM reach
  UNION ALL
  SELECT c.folder_id, f.parent_id, c.depth + 1
    FROM chain c JOIN folders f ON f.id = c.ancestor_id
   WHERE f.parent_id IS NOT NULL
),
expected(folder_id, ancestor_id, depth) AS (SELECT folder_id, ancestor_id, depth FROM chain),
stored(folder_id, ancestor_id, depth) AS (SELECT folder_id, ancestor_id, depth FROM folder_ancestry)
SELECT 'ancestry missing' AS what, folder_id AS user_id, ancestor_id AS asset_id, depth AS concealed
  FROM (SELECT * FROM expected EXCEPT SELECT * FROM stored)
UNION ALL
SELECT 'ancestry extra', folder_id, ancestor_id, depth
  FROM (SELECT * FROM stored EXCEPT SELECT * FROM expected)
"""


async def differences(connection: Connection) -> list[tuple[str, str, str | None, int | None]]:
    """Every way the stored answers disagree with the facts. Empty is the only acceptable answer.

    Recomputes the whole library, so it is a check and not something a request runs.
    """
    found: list[tuple[str, str, str | None, int | None]] = []
    await connection.execute(_DROP_EXPECTED_ROWS)
    await connection.execute(_CLEAR_PLACES)
    await connection.execute(_FILL_EVERY_PLACE)
    try:
        await connection.execute(_EXPECTED_ROWS)
    finally:
        await connection.execute(_CLEAR_PLACES)
    try:
        for statement in (
            _ANCESTRY_DIFFERENCES,
            _DIFFERENCES,
            _STATS_DIFFERENCES,
            _built().entity_count_differences,
            _built().pair_count_differences,
            _PARTNER_COUNT_DIFFERENCES,
        ):
            rows = await connection.execute_fetchall(statement)
            found.extend(
                (str(row[0]), str(row[1]), None if row[2] is None else str(row[2]), row[3])
                for row in rows
            )
    finally:
        await connection.execute(_DROP_EXPECTED_ROWS)
    return found


# --- the component -------------------------------------------------------------------------------


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_FOLDER_ANCESTRY)
        await connection.execute(_CREATE_ANCESTRY_INDEX)
        await connection.execute(_CREATE_VIEWER_ASSETS)
        await connection.execute(_CREATE_CONCEALED_INDEX)
        await connection.execute(_CREATE_VIEWER_STATS)
        await connection.execute(_CREATE_ENTITY_COUNTS)
        await _create_pair_counts(connection)
        await connection.execute(_CREATE_PENDING)
        await connection.execute(_CREATE_PLACES)
        await refresh_everything(connection)
        log.info("visibility.backfilled")
    # Version 15's columns come first, since every rebuild below writes them.
    if 0 < on_disk < 15:
        await _add_columns(connection, "viewer_entity_counts", ("permitted_ms", "concealed_ms"))
    # Version 11: the SIZE beside each stored count, then every stored answer rebuilt to fill it.
    if 0 < on_disk < 11:
        for table in ("viewer_stats", "viewer_entity_counts"):
            await _add_columns(connection, table, ("permitted_bytes", "concealed_bytes"))
        await refresh_everything(connection)
        log.info("visibility.sized")
    # Versions 12 and 13 made a SONG a counted, hidden and shared kind: every stored answer rebuilt
    # (a library below 11 has just been rebuilt by the step above, songs among the kinds).
    if on_disk == 11:
        await refresh_everything(connection)
        log.info("visibility.songs_counted")
    if on_disk == 12:
        await refresh_everything(connection)
        log.info("visibility.songs_hidden_and_shared")
    # Version 14: every trigger CALLS the steps of a recompute (see `RECOMPUTE`); the answers stay.
    if on_disk == 13:
        await share_the_steps(connection)
    # Version 15: the running time beside each size, counted from the stored rows.
    if 13 <= on_disk < 15:
        await time_the_counts(connection)


async def _add_columns(connection: Connection, table: str, columns: Sequence[str]) -> None:
    """Each count column where it is missing, so a step stopped half way can run again."""
    for column in columns:
        if not await column_exists(connection, table, column):
            # Module constants filled with module constants: nothing from run time.
            # nosemgrep: sift-no-string-built-sql
            await connection.execute(_ADD_COLUMN.format(table=table, column=column))


async def time_the_counts(connection: Connection) -> None:
    """The version 15 step, safe to run again: every count rebuilt from the stored rows, and the
    triggers rewritten to keep the running time as well."""
    await _drop_triggers(connection)
    await connection.execute(_CLEAR_ENTITY_COUNTS)
    await connection.execute(_built().fill_every_entity_count)
    await _create_triggers(connection)
    log.info("visibility.timed")


async def share_the_steps(connection: Connection) -> None:
    """The version 14 step, safe to run again: the triggers rewritten to call the shared steps.

    Rewritten in place only where every trigger on disk is exactly what version 13 wrote: then
    the stored answers were kept by these rules, and nothing about them moves. A trigger missing,
    changed or unknown means a change may have gone unseen, so every stored answer is rebuilt
    from the facts instead, which is what the boot check would have done. Triggers already in
    this build's shape are left alone.
    """
    rows = await connection.execute_fetchall(_TRIGGERS_PRESENT)
    present = {str(row[0]): _body_of(str(row[1])) for row in rows}
    shared = {name: _body_of(ddl) for name, _table, ddl in _built().triggers}
    if present == shared:
        log.info("visibility.steps_shared", triggers=len(shared), rewritten=0)
        return
    written = {name: _body_of(ddl) for name, _table, ddl in _built().version_13_triggers}
    if present != written:
        await refresh_everything(connection)
        log.warning("visibility.steps_shared_by_a_rebuild", triggers=len(shared))
        return
    await _drop_triggers(connection)
    await _create_triggers(connection)
    log.info("visibility.steps_shared", triggers=len(shared), rewritten=len(written))


async def _create_pair_counts(connection: Connection) -> None:
    """The pair table, its two indexes and the partner totals the pairs keep. Idempotent, so every
    step that needs them can say so, and together, because the triggers any step makes write
    the totals whenever a pair moves."""
    await connection.execute(_CREATE_PAIR_COUNTS)
    await connection.execute(_CREATE_PAIR_COUNTS_B_INDEX)
    await connection.execute(_CREATE_PAIR_COUNTS_EMPTY_INDEX)
    await connection.execute(_CREATE_PARTNER_COUNTS)


#: One column on one stored count table, for the version 11 and 15 steps.
_ADD_COLUMN = "ALTER TABLE {table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"

_ANCESTRY_CHECK = _filled("SELECT 1 FROM (<<DIFF>>) LIMIT 1", DIFF=_ANCESTRY_DIFFERENCES)


async def keep_true(connection: Connection) -> None:
    """Every boot: the triggers say what this build says and the ancestry says what the tree
    says, or the answers are rebuilt from the facts.

    Three things can make the stored answers wrong, and none of them moves this component's
    version. A migration in another component that rebuilds a table takes that table's triggers
    with it, silently. A build can change what a trigger says under the same name (a change to
    the rules, which a database carrying the old text has been keeping the old answers under). And
    a folder chain edited by hand into a loop, or out of one again, is a change the move trigger
    cannot follow all the way: it takes an unreachable subtree's rows away and cannot put a whole
    subtree's rows back without recursion, which a trigger body is not allowed. So this compares
    the trigger text, and compares the ancestry with the tree walked from its roots, and rebuilds
    everything when either disagrees. Cheap when nothing is wrong (one read of the schema and
    one walk of the folders) and idempotent when something is.
    """
    rows = await connection.execute_fetchall(_TRIGGERS_PRESENT)
    present = {str(row[0]): _body_of(str(row[1])) for row in rows}
    wanted = {name: _body_of(ddl) for name, _table, ddl in _built().triggers}
    missing = [name for name, body in wanted.items() if present.get(name) != body]
    # A trigger this build does not know is one an earlier build kept, still running. The rebuild
    # drops the ones this build retired by name; one it has never known is named here instead,
    # and the answers are rebuilt again on every boot until somebody retires it.
    stale = sorted(name for name in present if name not in wanted)
    unknown = [name for name in stale if name not in RETIRED_TRIGGERS]
    if missing or stale:
        log.warning("visibility.triggers_repaired", repaired=len(missing), stale=stale)
        if unknown:
            log.error("visibility.unknown_triggers", names=unknown)
        await refresh_everything(connection)
        return
    out_of_step = await connection.execute_fetchall(_ANCESTRY_CHECK)
    if out_of_step:
        log.warning("visibility.ancestry_repaired")
        await refresh_everything(connection)


register_schema_initializer(
    COMPONENT,
    VERSION,
    initialize,
    depends_on=["identity", "library", "content", "user_state", "catalog", "access"],
    baseline=10,
)
register_schema_invariant(COMPONENT, keep_true)
