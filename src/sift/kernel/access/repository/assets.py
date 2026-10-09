# SPDX-License-Identifier: AGPL-3.0-or-later
"""The statement that lists what a viewer may see, and the questions asked of it.

Who may see which file is a stored fact, one row per user and file in `viewer_assets` kept true
by the database (`sift.kernel.access.visibility`); every statement here joins it. There is ONE
statement: the grid, the one-file fetch, the count, the facets and a position are it cut at
checked seams, because an unauthorized read is served where two reads disagree about who may see
what. A cut that no longer puts the statement back together fails the import.
"""

from __future__ import annotations

from sift.kernel.access.constraints import SEES_EVERY_FILE
from sift.kernel.access.repository.asset_facets import ADMIN_FACETS as ADMIN_FACETS
from sift.kernel.access.repository.asset_facets import FACET_LABELS as FACET_LABELS
from sift.kernel.access.repository.asset_facets import FACETS as FACETS
from sift.kernel.access.repository.asset_facets import FACETS_RENAMED as FACETS_RENAMED
from sift.kernel.access.repository.asset_facets import concealed_value as concealed_value
from sift.kernel.access.repository.asset_orders import _ORDER_TAILS as _ORDER_TAILS
from sift.kernel.access.repository.asset_orders import (
    _SEEK_AFTER,
    _SEEK_CLOSE,
    _SEEK_OPEN,
)
from sift.kernel.access.repository.asset_orders import DEFAULT_SORT as DEFAULT_SORT
from sift.kernel.access.repository.asset_orders import RELEVANCE as RELEVANCE
from sift.kernel.access.repository.asset_orders import SEEKABLE_SORTS as SEEKABLE_SORTS
from sift.kernel.access.repository.asset_orders import SHUFFLE_MODULUS as SHUFFLE_MODULUS
from sift.kernel.access.repository.asset_orders import SIMILARITY as SIMILARITY
from sift.kernel.access.repository.asset_orders import SORT_KEYS as SORT_KEYS
from sift.kernel.access.repository.asset_orders import seek_anchor as seek_anchor
from sift.kernel.access.repository.asset_orders import shuffle_of as shuffle_of
from sift.kernel.access.visibility import (
    ANY_COPY_MISSING,
    CONCEALED_BY_THIS_FILE,
    WAITING_FACES_KIND,
)
from sift.kernel.db import point_read
from sift.kernel.sql_splice import splice

# The statement: fixed text, the filter, then the concealment rules and the order. Only the
# filter varies.
_VISIBLE_ASSETS_HEAD = splice(
    """
WITH RECURSIVE
-- Each `in:` folder expanded to its subtree, one group per term. UNION, not UNION ALL, so a parent
-- loop runs out of new rows; a depth column would defeat that, hence a yes or no to descend.
in_scope(grp, folder_id) AS (
  SELECT g.key, v.value FROM json_each(:folder_ids_groups) g, json_each(g.value) v
  UNION
  SELECT s.grp, f.id FROM folders f JOIN in_scope s ON f.parent_id = s.folder_id
    WHERE :folder_depth_direct = 0
),
-- How well each file matches the words, for a viewer who sees every file: bm25 reads every match.
-- From `json_each`, empty when nothing was typed, as FTS5 refuses a NULL match.
relevance(asset_id, score) AS (
  SELECT assets_fts.asset_id, bm25(assets_fts)
    FROM users u CROSS JOIN json_each(:text_rank) rank
    JOIN assets_fts ON assets_fts MATCH rank.value
   WHERE {{SEES_EVERY_FILE}}
),
-- How near each file sat to what was typed, when a model was asked rather than the word index.
--
-- **This is a list of answers, not a question.** The nearest-neighbour lookup happens in the
-- feature that owns the vector index, before this statement runs, and its result arrives here as
-- pairs of an id and a distance. So nothing in the read that decides who may see what mentions
-- that index, the table type it lives in, or the syntax it is queried with, which is what makes
-- the thing underneath it replaceable without touching this file.
--
-- It narrows nothing. A search by meaning returns the same files the same query returns without
-- it, in a different order; every rule below still decides what may be seen, on the whole set.
-- Ordering by a model's opinion cannot reveal a file, only move one somebody could already see.
--
-- Driven from `json_each` for the same reason the ranking above is: over NULL it yields no rows,
-- so with nothing to order by every file scores NULL and ties, and the tie falls through.
semantic(asset_id, score) AS (
  SELECT json_extract(near.value, '$[0]'), json_extract(near.value, '$[1]')
    FROM json_each(:semantic_rank) near
)
SELECT a.*,
       -- Whether ANY copy of this file is where Sift last saw it.
       --
       -- Derivatives live beside the library rather than inside it, so a file whose bytes have
       -- gone keeps its thumbnail: it draws on the wall, opens, and reports a size and dimensions
       -- off this row. Without this, a deleted file would go on looking present until somebody
       -- opened it and got a torn-page glyph.
       --
       -- One subquery over the locations rather than a join, so an asset with four copies is still
       -- one row. `NOT EXISTS ... present` rather than counting: the question is whether there is
       -- one readable copy, and stopping at the first is the cheap way to ask it.
       {{ANY_COPY_MISSING}} AS unreachable,
       -- The vault's answer for this viewer, off the stored row: hidden itself, hidden by a folder
       -- or root above any copy, or hidden by something it belongs to.
       v.concealed AS concealed,
       -- Concealed BY THIS FILE, as against by something it is in or is of. The same split
       -- `shared_here` and `restricted_here` draw for the other axis, and it exists for the same
       -- reason: one is something you did and can undo where you are standing, the other is a
       -- consequence of something further up.
       --
       -- Without it a tile knows only THAT it is concealed, and the mark it draws would have to pick
       -- one fill for every case. Solid, in this app's own language, means "the switch is here", so
       -- every file concealed by a person or a folder would wear a badge asserting somebody had
       -- hidden that file, and pressing it would open a panel that immediately said otherwise.
       {{CONCEALED_BY_THIS_FILE}} AS concealed_here,
       -- Whether the still exists yet. The grid draws a tile the instant an asset is indexed, long
       -- before its thumbnail is built, so it needs to tell "the picture is on its way" from "there
       -- is no picture": one is a shimmer, the other is the empty-frame glyph. Without this the
       -- tile can only find out by requesting the address and watching it 404, which makes every
       -- not-yet-built thumbnail look like a broken one. The kind is filtered so a preview or sprite
       -- for an asset with no thumbnail does not read as one.
       EXISTS (SELECT 1 FROM derivatives d
                WHERE d.asset_id = a.id AND d.kind = 'thumb') AS has_thumb,
       -- Why there is no still, when the answer is "there never will be": the standing verdict
       -- the picture pass recorded. A tile with no still and no verdict is waiting; one with a
       -- verdict says the reason on hover rather than shimmering for ever.
       --
       -- Filed under 'thumbnails', the still's own product: each of the three pictures is a
       -- product with a verdict of its own. The code rides beside the reason because the code is
       -- what the words a person reads are chosen by (`failure_words.why_left_out`).
       (SELECT v.reason FROM file_verdicts v
         WHERE v.asset_id = a.id AND v.product = 'thumbnails' AND v.transient = 0)
         AS picture_verdict,
       (SELECT v.code FROM file_verdicts v
         WHERE v.asset_id = a.id AND v.product = 'thumbnails' AND v.transient = 0)
         AS picture_verdict_code,
       -- What this asset's generated pictures currently are, as one string. It is turned into the
       -- short token that goes on the end of every picture address for this asset, so a browser
       -- may keep them without asking (see `art_version`).
       --
       -- All of them together rather than one per kind, deliberately. Rebuilding a preview changes
       -- the address of the thumbnail beside it, which costs that one asset a re-fetch of pictures
       -- it already had; three separate tokens would cost three fields on every row of every grid,
       -- every wall and every search result, for an economy nobody could measure. A rebuild is rare
       -- and a grid row is not.
       --
       -- Ordered inside a subselect because `group_concat` has no ordering of its own: left to the
       -- order rows happen to come back in, the same three pictures could produce two different
       -- strings and an address would change for no reason.
       (SELECT group_concat(ordered.mark, '|') FROM (
          SELECT d.kind || ':' || COALESCE(d.content_hash, '') AS mark
            FROM derivatives d WHERE d.asset_id = a.id ORDER BY d.kind, d.params
        ) ordered) AS art_marks,
       -- Kept at the top of this wall by the person asking, and by nobody else. The sixth opinion
       -- on `asset_user_state`, and the file half of the pin the five named kinds already carry.
       -- COALESCE because most files have no row here at all: an absent opinion is "not pinned".
       COALESCE(mine.pinned, 0) AS pinned,
       COUNT(*) OVER () AS total_count
  FROM assets a
  -- The stored verdict. An inner join: a file this viewer may not see has no row, and a file that
  -- is nowhere has no row for anybody. Everything about permission is in whether the row exists.
  JOIN viewer_assets v ON v.asset_id = a.id AND v.user_id = :viewer
  LEFT JOIN relevance     rl ON rl.asset_id = a.id
  LEFT JOIN semantic      sm ON sm.asset_id = a.id
  -- THIS VIEWER'S own opinion row, for the pin. One index seek on the primary key per row, and a
  -- LEFT join because most files have no row here: an inner one would return only the files
  -- somebody has already formed an opinion about, which is a fraction of any library.
  LEFT JOIN asset_user_state mine ON mine.asset_id = a.id AND mine.user_id = :viewer
 WHERE (:asset_id IS NULL OR a.id = :asset_id)
   -- Filtering by tag rides inside the statement rather than in front of it, so the page, the
   -- total and the permission check all narrow together. Applied outside, the count would describe
   -- the tag and the rows would describe the tag intersected with what this viewer may see, and
   -- the two would disagree on exactly the assets the filter exists to hide.
   AND (:tag_id IS NULL
        OR EXISTS (SELECT 1 FROM asset_tags t WHERE t.asset_id = a.id AND t.tag_id = :tag_id))
   -- Narrowing to one collection rides inside for the same reason narrowing to a tag does, and
   -- the count is the reason it matters here more: a collection is a thing somebody curated, so
   -- the difference between its size and what a viewer is shown of it is a number somebody would
   -- notice.
   AND (:collection_id IS NULL
        OR EXISTS (SELECT 1 FROM collection_items ci
                    WHERE ci.asset_id = a.id AND ci.collection_id = :collection_id))
   -- What a search narrowed to, applied here for the reason the two predicates above are applied
   -- here: inside the statement, so the page and the total describe the same set. A search that
   -- filtered afterwards would report a count of everything the term matched and show only the
   -- part this viewer may see, and the difference between those two numbers is a way to ask
   -- whether a named file exists without ever being shown it.
   --
   -- THIS IS THE ONE PART OF THIS STATEMENT THAT IS NOT FIXED TEXT. A filter is a tree now
   -- (all of these, any of these, not that), so its shape is decided per request and written by
   -- `AssetFilter.predicate`, which builds it only from conditions spelled out in full there and
   -- binds every value it carries. Nothing a person typed reaches the text.
   --
   -- The seam is the point, and the parentheses are what make it hold. Whatever comes back is ONE
   -- conjunct of the fixed AND chain below, so the concealment rules that follow are applied to
   -- whatever it left, and no arrangement of ORs inside it can reach outside it. A filter asks a
   -- question about an asset; it never answers who may see one. The permission answer is the join
   -- above, and nothing that builds a filter can reach that either.
   --
   -- These parentheses are the SECOND of two: the writer already wraps every group in its own, so
   -- removing either one alone changes nothing. That is deliberate rather than an oversight worth
   -- tidying: this is the line between a filter and a permission rule, and it is worth holding
   -- twice. The grouping WITHIN a filter is what the writer's own parentheses carry, and that one
   -- is load-bearing on its own.
   AND (
""",
    ANY_COPY_MISSING=ANY_COPY_MISSING,
    CONCEALED_BY_THIS_FILE=CONCEALED_BY_THIS_FILE,
    SEES_EVERY_FILE=SEES_EVERY_FILE,
)

# Everything from the concealment rules down: what decides which rows a viewer is SHOWN is fixed
# text with nothing spliced into it.
_VISIBLE_ASSETS_ACCESS = """)
   AND (:reveal = 1 OR v.concealed = 0)
   -- The Hidden screen: the same set, turned inside out.
   --
   -- Exactly the negation of the line above, which is what makes it safe to have at all. It is an
   -- AND, so it can only ever NARROW what the join already permitted: there is no arrangement of
   -- it that hands back a row the join refused. With the vault locked, `:reveal` is 0 and that line
   -- already excludes every concealed row, so asking for hidden-only returns nothing at all: the
   -- screen is empty until the PIN is entered, without needing a rule of its own to say so.
   AND (:hidden_only = 0 OR v.concealed = 1)
 -- What stands in front of the sort: the pin, then a photo set's own order. Neither asked, every
 -- row sorts equal here and the sort falls through unchanged.
 ORDER BY
          -- PINNED FIRST, on the walls that HONOUR the pin, and nowhere else.
          --
          -- Which walls those are is the caller's to say, and it is the same fact that decides
          -- whether the Pin verb appears at all: a wall somebody curates orders by the pin and
          -- offers it; the whole library and the log of what has been watched do neither. One fact,
          -- one parameter, so a wall cannot come to offer a verb that does not affect it or float
          -- rows for a reason it never mentions.
          --
          -- It sits above `_ORDER_TAILS` because a pin buried under the chosen sort stops working
          -- the moment anybody changes the sort.
          CASE WHEN :pinned_first = 1 THEN COALESCE(mine.pinned, 0) ELSE 0 END DESC,
          -- A photo set is a sequence for the same reason and is ordered the same way. A shoot was
          -- numbered, and showing it newest-first is showing something else.
          --
          -- Guarded by the same CASE, which is what keeps it free rather than merely correct: with
          -- no set named the planner skips the subquery entirely and the plain grid costs what it
          -- always cost. NULLS LAST for the same reason too.
          CASE WHEN :photo_set_id IS NULL THEN NULL ELSE
            (SELECT psi.position FROM photo_set_items psi
              WHERE psi.photo_set_id = :photo_set_id AND psi.asset_id = a.id) END ASC NULLS LAST,
"""

_PAGE_TAIL = "\n LIMIT :limit OFFSET :offset\n"

#: The running total, which the page statement cuts out (`assets_count_query`): a window function
#: sees every row, so on the page it would evaluate every column for every permitted file.
_TOTAL_COLUMN = ",\n       COUNT(*) OVER () AS total_count"

if _VISIBLE_ASSETS_HEAD.count(_TOTAL_COLUMN) != 1:  # pragma: no cover (an edit across the seam)
    raise RuntimeError("the visible-assets statement no longer carries one window count")

# The statement taken apart at four seams, so a different question is asked of the same rows. A
# count is a disclosure in its own right, so it may never come from a second copy of the rule.
_COLUMNS_AT = "\nSELECT a.*,"
_FROM_AT = "\n  FROM assets a"
_WHERE_AT = "\n WHERE (:asset_id IS NULL OR a.id = :asset_id)"
_ORDER_AT = "\n -- What stands in front of the sort"

_CTES, _after_ctes = _VISIBLE_ASSETS_HEAD.split(_COLUMNS_AT, 1)
_COLUMNS, _after_columns = _after_ctes.split(_FROM_AT, 1)
_JOINS, _WHERE_OPEN = _after_columns.split(_WHERE_AT, 1)
_CONDITIONS, _ARRANGED_ORDER = _VISIBLE_ASSETS_ACCESS.split(_ORDER_AT, 1)

if (
    _CTES + _COLUMNS_AT + _COLUMNS + _FROM_AT + _JOINS + _WHERE_AT + _WHERE_OPEN
    != _VISIBLE_ASSETS_HEAD
    or _CONDITIONS + _ORDER_AT + _ARRANGED_ORDER != _VISIBLE_ASSETS_ACCESS
):  # pragma: no cover (reaching this means the statement was edited across a seam)
    raise RuntimeError("the visible-assets statement no longer splits where the facet read cuts it")


#: The point form's row filter: the id is never absent, so the planner seeks the primary key
#: rather than meeting the set form's guard.
_POINT_WHERE_AT = "\n WHERE a.id = :asset_id"

#: The one join every read makes. Which side is walked first is the caller's choice
#: (`drive_for`): the planner picks the verdict side whatever the user may see, and statistics do
#: not move it; a CROSS JOIN does, because SQLite honours it as an ordering.
_VERDICT_JOIN = "\n  JOIN viewer_assets v ON v.asset_id = a.id AND v.user_id = :viewer"

if _JOINS.count(_VERDICT_JOIN) != 1:  # pragma: no cover (an edit across the seam)
    raise RuntimeError(
        "the visible-assets statement no longer joins the verdict where the page expects"
    )

#: The two rankings, read only by the orders that rank: an ids-first walk under any other order
#: leaves them out, as a ranking joined per row costs every row of the walk.
_RANKED_JOINS = (
    "\n  LEFT JOIN relevance     rl ON rl.asset_id = a.id"
    "\n  LEFT JOIN semantic      sm ON sm.asset_id = a.id"
)

if _JOINS.count(_RANKED_JOINS) != 1:  # pragma: no cover (an edit across the seam)
    raise RuntimeError("the visible-assets statement no longer joins the rankings in one place")

#: Walk the sort index, probe the verdict. For a user who may see most of the library.
DRIVE_LIBRARY = "library"
#: Walk the user's verdict rows, then sort them. For a user who may see little of it.
DRIVE_VERDICT = "verdict"

_DRIVES = {
    DRIVE_LIBRARY: (
        _FROM_AT,
        _JOINS.replace(
            _VERDICT_JOIN,
            "\n  CROSS JOIN viewer_assets v ON v.asset_id = a.id AND v.user_id = :viewer",
        ),
    ),
    DRIVE_VERDICT: (
        "\n  FROM viewer_assets v",
        _JOINS.replace(
            _VERDICT_JOIN,
            "\n  CROSS JOIN assets a ON a.id = v.asset_id AND v.user_id = :viewer",
        ),
    ),
}


def drive_for(permitted: int, library: int) -> str:
    """Which side a page should walk first, from how much of the library the user may see.

    The sort index costs about `50 * library / permitted` probes for a page of fifty; the verdict
    about `2 * permitted`. So the verdict only while `permitted * permitted < 25 * library`.
    `library` is an upper bound, since an exact count is a pass over the table.
    """
    return DRIVE_VERDICT if permitted * permitted < 25 * max(library, 1) else DRIVE_LIBRARY


# The arranged order that comes first whatever sort was named, read by the page and its position.
_ORDERING = _ORDER_AT + _ARRANGED_ORDER


def _ordered_by(sort: str, *, arranged: bool = True) -> str:
    """The complete ORDER BY for one sort, the one every ordering statement reads, so a page and a
    position on it cannot disagree. An unknown key is the default: a sort is a display preference.

    `arranged` LEAVES OUT the two arms in front of the sort (the pin, a photo set's position) for
    a read that named neither: they sort every row equal, but an expression cannot
    be answered from an index, so their presence alone makes every page a full sort.
    """
    tail = _ORDER_TAILS.get(sort, _ORDER_TAILS[DEFAULT_SORT])
    # The `ORDER BY` keyword lives inside the arranged block, so leaving that out puts it back.
    return (_ORDERING + tail) if arranged else "\n ORDER BY\n" + tail


def facet_query(where: str, *, joins: str, value: str, label: str = "", extra: str = "") -> str:
    """How many of the files this query reaches carry each value of one dimension.

    The page's own CTEs, joins, filter and permission rules with another projection, so a count
    describes exactly what is on screen. `joins` and `value` come from `FACETS` by a validated key.
    `extra` keeps a concealed name out before the grouping. `label` names an ID value and is grouped
    by too, since a bare column under an aggregate picks an arbitrary row. `COUNT(DISTINCT a.id)`,
    since a dimension join multiplies rows.
    """
    return (
        _CTES
        + "\nSELECT "
        + value
        + " AS facet_value, COUNT(DISTINCT a.id) AS files"
        + ((", " + label + " AS facet_label") if label else "")
        + _FROM_AT
        + _JOINS
        + joins
        + _WHERE_AT
        + _WHERE_OPEN
        + where
        + _CONDITIONS
        + extra
        # Nothing in the dimension is not a value of it. In the WHERE chain with the expression
        # repeated, not a HAVING over the alias: that is an extension older SQLite refuses.
        + "\n   AND ("
        + value
        + ") IS NOT NULL AND ("
        + value
        + ") <> ''"
        + "\n GROUP BY facet_value"
        + (", facet_label" if label else "")
        + "\n ORDER BY files DESC, facet_value ASC"
        + "\n LIMIT :limit"
    )


#: The orders an index walks, so a page stops at its last row; no index holds the shuffle's key.
_INDEX_ORDERED = SEEKABLE_SORTS - {"random"}

#: Where a page that sorts every row reads its columns: the ids it kept, and no others.
_PAGED_IDS = "a.id IN (SELECT paged_id FROM paged)"


def _sorts_ids_first(sort: str, *, arranged: bool) -> bool:
    """Whether a page sorts every row, so it sorts their ids and reads its own rows after."""
    return arranged or sort not in _INDEX_ORDERED


def assets_query(
    sort: str,
    where: str,
    *,
    arranged: bool = True,
    counted: bool = True,
    drive: str = DRIVE_LIBRARY,
    continued: bool = False,
    outer: str | None = None,
    narrowed: bool = False,
) -> str:
    """The visible-assets statement, filtered by `where` and ordered by `sort`.

    The resolve, the filter in its parentheses, then the permission rules and the window, so a
    filter can only narrow. `drive` names the side walked first (`drive_for`). `continued` starts
    after `:after_key` and `:after_id`, here so the count, which takes `where` alone, never does.
    An order no index walks, or a `narrowed` page the planner may read from its members and sort,
    sorts the ids first, then reads those rows alone under `outer` (by default `where`), as the
    first step applied all of it.
    """
    columns = _COLUMNS if counted else _COLUMNS.replace(_TOTAL_COLUMN, "")
    from_at, joins = _DRIVES[drive]
    narrowing = where + _SEEK_OPEN + _SEEK_AFTER[sort] + _SEEK_CLOSE if continued else where
    ordered = _ordered_by(sort, arranged=arranged)
    tail = _WHERE_AT + _WHERE_OPEN + narrowing + _CONDITIONS + ordered + _PAGE_TAIL
    if counted or not (narrowed or _sorts_ids_first(sort, arranged=arranged)):
        return _CTES + _COLUMNS_AT + columns + from_at + joins + tail
    walked = from_at + (
        joins if sort in (RELEVANCE, SIMILARITY) else joins.replace(_RANKED_JOINS, "")
    )
    walked += tail
    # The second read keeps every rule and the order, so it can only drop a row, never add one. A
    # narrowed page's ids already passed its filter, which would only build its members again.
    again = "1" if narrowed else where if outer is None else outer
    return (
        _CTES
        + ",\npaged(paged_id) AS (\nSELECT a.id"
        + walked
        + ")"
        + _COLUMNS_AT
        + columns
        + _FROM_AT
        + _JOINS
        + _WHERE_AT
        + _WHERE_OPEN
        + again
        + _SEEK_OPEN
        + _PAGED_IDS
        + _SEEK_CLOSE
        + _CONDITIONS
        + ordered
    )


def assets_count_query(where: str) -> str:
    """How many files this viewer may see through the same filter, and their size: the page's
    other half, from the same seams, so the two cannot describe different files. Not a window on
    the page, which would make every page cost the whole library (`_TOTAL_COLUMN`).
    """
    return (
        _CTES
        # A file the vault holds back adds no bytes while it is shut, even as a locked tile.
        + "\nSELECT COUNT(*) AS total_count,"
        + "\n       COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0"
        + "\n                         THEN a.size_bytes END), 0) AS total_bytes"
        + _FROM_AT
        + _JOINS
        + _WHERE_AT
        + _WHERE_OPEN
        + where
        + _CONDITIONS
    )


def point_query(where: str) -> str:
    """The same statement as `assets_query`, asking about ONE file by primary key: one probe of the
    stored verdict, and no sort or window."""
    return (
        _CTES
        + _COLUMNS_AT
        + _COLUMNS
        + _FROM_AT
        + _JOINS
        + _POINT_WHERE_AT
        + _WHERE_OPEN
        + where
        + _CONDITIONS
    )


#: Which username each row is filed under, and when, joined only to the usernames asked about.
_NEWEST_FILED_JOIN = (
    "\n  JOIN asset_usernames filed ON filed.asset_id = a.id"
    "\n   AND filed.username_id IN (SELECT value FROM json_each(:newest_of))"
)


def newest_filed_query(where: str) -> str:
    """The newest file this viewer may see under each username in `:newest_of`, one each.

    The page's own seams with another projection, so the answer is a file the username's wall would
    draw. Newest by when the filing was made, the id breaking a tie, whatever the wall's sort.
    """
    return (
        _CTES
        + "\nSELECT username_id, asset_id FROM ("
        + "\nSELECT filed.username_id AS username_id, a.id AS asset_id,"
        + "\n       ROW_NUMBER() OVER (PARTITION BY filed.username_id"
        + "\n                          ORDER BY filed.decided_at DESC, a.id DESC) AS nth"
        + _FROM_AT
        + _JOINS
        + _NEWEST_FILED_JOIN
        + _WHERE_AT
        + _WHERE_OPEN
        + where
        + _CONDITIONS
        + "\n) WHERE nth = 1"
    )


# How many of a group's waiting faces this viewer may see: the stored count, less the ones their
# vault holds back unless it is open. The same reading the grid's total makes of `viewer_stats`.
_WAITING_FACES = "c.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE c.concealed END"

#: The groups this viewer may see faces of, of one status, with the count of those faces; the
#: page and the position read it so they agree about what "waiting" means. `:pile_floor` and
#: `:pile_ceiling` (zero is none) bound a group by the faces THIS viewer may see, and bind into the
#: statement so the page, the order and the count are decided together.
_WAITING_PILES = splice(
    """
  FROM viewer_entity_counts c
  JOIN face_piles fp ON fp.id = c.object_id AND fp.status = :pile_status
 WHERE c.user_id = :viewer AND c.kind = '{{KIND}}'
   AND {{FACES}} >= :pile_floor
   AND (:pile_ceiling = 0 OR {{FACES}} <= :pile_ceiling)""",
    KIND=WAITING_FACES_KIND,
    FACES=_WAITING_FACES,
)

_WAITING_PILE_PAGE = splice(
    """
SELECT c.object_id AS pile_id,
       {{FACES}} AS visible,
       COUNT(*) OVER () AS total_count{{PILES}}
 ORDER BY visible DESC, pile_id
 LIMIT :limit OFFSET :offset
""",
    FACES=_WAITING_FACES,
    PILES=_WAITING_PILES,
)

_WAITING_PILE_POSITION = splice(
    """
SELECT position FROM (
SELECT c.object_id AS ranked_id,
       ROW_NUMBER() OVER (ORDER BY {{FACES}} DESC, c.object_id) AS position{{PILES}}
)
 WHERE ranked_id = :pile_id
""",
    FACES=_WAITING_FACES,
    PILES=_WAITING_PILES,
)


def waiting_pile_page_query() -> str:
    """One page of the groups waiting, each with how many of its faces this viewer may see.

    One row per group off the stored per-user counts (`viewer_entity_counts`), never a row per
    face. Ordered by the faces this viewer MAY SEE, so a group's place cannot measure what is kept
    back; the id breaks ties. `COUNT(*) OVER ()` counts the groups, the total the pager needs.
    """
    return _WAITING_PILE_PAGE


#: How many groups the wall holds, without ranking them: the line under the floor wants the
#: number alone, and the page's window and sort cost twice the steps for it.
_WAITING_PILE_COUNT = "SELECT COUNT(*) AS total_count" + _WAITING_PILES


def waiting_pile_count_query() -> str:
    """How many groups `waiting_pile_page_query` would page through, as one row."""
    return _WAITING_PILE_COUNT


def waiting_pile_position_query() -> str:
    """How far down that wall one group sits, counting from one, or no row at all: a group this
    viewer may see nothing of is not ranked, the same answer a group that does not exist gets."""
    return _WAITING_PILE_POSITION


def position_query(sort: str, where: str, *, arranged: bool = True) -> str:
    """Where one file sits in the order this query puts its rows in. One-based, or no row at all.

    The address carries the FILE somebody was looking at, and this turns it back into a place. The
    page's own statement with a ROW_NUMBER projection, so a file this viewer may not see has no
    position, the answer a file that does not exist gets. Wrapped in a subquery, since a window
    cannot appear in a WHERE.
    """
    return (
        _CTES
        + "\nSELECT position FROM ("
        + "\nSELECT a.id AS ranked_id,"
        + "\n       ROW_NUMBER() OVER ("
        + _ordered_by(sort, arranged=arranged)
        + "\n       ) AS position"
        + _FROM_AT
        + _JOINS
        + _WHERE_AT
        + _WHERE_OPEN
        + where
        + _CONDITIONS
        + "\n)\n WHERE ranked_id = :position_of\n"
    )


_LOCATIONS_OF_ASSET = "SELECT * FROM asset_locations WHERE asset_id = ? ORDER BY first_seen_at, id"

# The present copies of a list of files in the order `locate` reads them, so the first per file
# is the one that opens. One statement for the list, which a page asks for fifty at a time.
_PRESENT_LOCATIONS_OF_ASSETS = """
SELECT asset_id, rel_path FROM asset_locations
 WHERE asset_id IN (SELECT value FROM json_each(?)) AND status = 'present'
 ORDER BY first_seen_at, id
"""

# The newest build wins, by id as `_BEST_DERIVATIVE_OF_ASSET` says. A point read: every picture
# runs it, and it is a seek on the derivatives key.
_DERIVATIVE_OF_ASSET = point_read(
    "access.derivative_of_asset",
    """
SELECT * FROM derivatives
 WHERE asset_id = ? AND kind = ? AND params = ?
 ORDER BY id DESC
 LIMIT 1
""",
)

# The NEWEST picture of this kind, whatever recipe built it: a recipe that changes (a hover clip's
# length) would otherwise leave every file built the old way with none until re-encoded. It needs
# no knowledge of the recipe, and a rebuild writes after the old row, so newest is built as now.
# By `id`, a ULID that never goes down, not `created_at`, a wall clock that can step backwards.
_BEST_DERIVATIVE_OF_ASSET = point_read(
    "access.best_derivative_of_asset",
    """
SELECT * FROM derivatives
 WHERE asset_id = ? AND kind = ?
 ORDER BY id DESC
 LIMIT 1
""",
)

# Hiding itself, on rows the caller has already resolved. An upsert, because most files have no
# state row until somebody hides one. `hidden_at` is cleared on the way out so it means "since when".
_SET_ASSET_VAULT = """
INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)
VALUES (:asset_id, :viewer, :hidden, :hidden_at, :now)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

# The same write over a LIST of files, one statement in one `_write_for`: one turn of the write
# lock and one cache stamp for them all. The ids bind as one JSON array, so the text is fixed.
# `WHERE true` lets SQLite tell the upsert clause from a join hint; without it nothing parses.
_SET_ASSET_VAULT_MANY = """
INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)
SELECT value, :viewer, :hidden, :hidden_at, :now FROM json_each(:asset_ids)
WHERE true
ON CONFLICT(asset_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""
