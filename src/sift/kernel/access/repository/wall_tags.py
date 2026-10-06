# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Tags wall: the tags a viewer may know about, each counted over the files they may see."""

from __future__ import annotations

from sift.kernel.access.repository.walls import (
    _NOTHING,
    _cut,
    _filtered,
    _narrowed,
    _one_seam,
    _position,
    _row_narrowed,
    _wall,
    _with_stored_counts,
    locked_tile,
    one_row,
    shown,
)
from sift.kernel.sql_splice import splice

#: Tags this viewer may know about, each with the number of assets they can actually see under
#: it. Ordered most-used first, which is the order a suggester wants and a stable one for a list.
# `CROSS JOIN` onto the verdict in every count, and it is not a style. SQLite treats it as an order
# to walk the left table first, and that is the order that costs the members rather than the
# library: left to itself the planner starts from the user's whole verdict range and probes
# the membership table once per file it may see, which costs the library for a handful of
# membership rows.
_VISIBLE_TAGS = splice(
    """
WITH RECURSIVE
-- The folders an `in:` filter names, expanded to their subtrees.
--
-- Present in every statement a filter can be spliced into, so an entity wall accepts the SAME
-- query language the file wall does rather than a smaller one somebody has to remember the shape
-- of. It costs nothing when no folder is named: the parameter is NULL, `json_each` over NULL
-- yields no rows, and the whole CTE is empty.
--
-- UNION rather than UNION ALL, so a hand-edited or restored database with a parent loop
-- terminates instead of recursing forever: a folder already in the set is not added twice.
--
-- `:folder_depth_direct` is how a CONTROL asks for the folder and not what is under it: a file
-- manager walking into a folder, where a typed `in:` means the whole subtree. Said as whether to
-- descend at all rather than as a number of levels, and that is the loop property again: a depth
-- column would make the same folder a NEW row at each depth, so UNION would stop deduplicating
-- and a parent loop would recurse forever. Nobody has asked for two levels.
in_scope(grp, folder_id) AS (
  SELECT g.key, v.value FROM json_each(:folder_ids_groups) g, json_each(g.value) v
  UNION
  SELECT s.grp, f.id FROM folders f JOIN in_scope s ON f.parent_id = s.folder_id
    WHERE :folder_depth_direct = 0
),
-- The same set again, narrowed to whatever THIS WALL was asked to show.
--
-- A second CTE rather than a condition inside the one above. What may be SEEN is a rule about the
-- viewer, and what belongs on THIS wall is a question about the wall: a cover gated on the
-- narrowed set would vanish from an album drawn on a person's page whenever the picture is not one
-- of her files. Covers are gated on `permitted`; everything counted stays on `visible`.
--
-- It is also strictly SAFER than a conjunct in the permission chain. The filter is ANDed over rows
-- that have already been through every rule and closed as their own CTE, so no arrangement of ORs
-- inside it can reach a permission rule at all.
--
counted(tag_id, asset_count, size_bytes, duration_ms) AS (
  SELECT t.tag_id, COUNT(*),
         COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.size_bytes END), 0),
         COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.duration_ms END), 0)
    FROM asset_tags t
    CROSS JOIN viewer_assets v ON v.asset_id = t.asset_id AND v.user_id = :viewer
    JOIN assets a ON a.id = t.asset_id
   WHERE (:reveal = 1 OR v.concealed = 0)
     AND 1 = 1
     -- The wall's own narrowing is spliced in here. See `_filtered`.
   GROUP BY t.tag_id
),
-- HOW BIG THE THING ITSELF IS, as opposed to how much of it is on THIS wall.
--
-- Two counts, because a card asks two different questions of one number once an entity wall can be
-- narrowed. `counted` above is the narrowed one and decides MEMBERSHIP: a row with nothing under it
-- on this wall does not belong on it. This one is what the card SAYS: an album of 106 pictures says
-- 106 on a person's tab, not 43, because 43 is a fact about the wall and the album is what the card
-- is about.
--
-- It reads `permitted`, so every permission rule still applies exactly as before: an entity
-- showing 42 to somebody who may see nine would still be reporting the size of the set they were
-- kept out of, which is the number this whole model exists to keep back. What it drops is only the
-- narrowing.
--
-- READ OFF THE STORED COUNT (`viewer_entity_counts`), which is that same number kept by the
-- triggers that keep the verdict (every file under the row this viewer may see, less the vault's
-- share unless `:reveal`), and which carries the SIZE of those files beside it. Summed live, the
-- size would walk every membership of every row on the wall. Read here it costs one range of the
-- stored rows, and the number and the size come off one row, so they cannot describe different files.
whole(tag_id, asset_count, size_bytes, duration_ms) AS (
  SELECT c.object_id, c.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE c.concealed END,
         c.permitted_bytes - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_bytes END,
         c.permitted_ms - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_ms END
    FROM viewer_entity_counts c
   WHERE c.user_id = :viewer AND c.kind = 'tag'
)
-- An admin gets every tag, including ones nothing carries yet, because the editor has to be able
-- to see and rename them. Everyone else gets only tags they can actually reach something through:
-- a name alone says what the library is organised around, and an empty one says it about rows
-- they were not shown.
--
-- `:reveal_named` takes a vaulted tag off the list ENTIRELY rather than dropping its count to
-- nothing. A tag left listed with a zero beside it is the one answer concealment must not give: it
-- names the thing being hidden and says there is something under it. See `reveals_named_rows`.
-- The heart and the stars are THIS viewer's, off the same row the hidden flag comes from. Two
-- users sharing an install hold their own opinions about one tag, which is the only reading of
-- an opinion that makes sense, and it is why these are joined per viewer rather than stored on
-- the tag.
-- A locked tile has no name; see `_LOCKED_TILE`. `locked` is what the card draws it by.
SELECT t.id, CASE WHEN {{LOCKED}} THEN '' ELSE t.name END AS name,
       {{LOCKED}} AS locked, COALESCE(h.hidden, 0) AS vault, t.created_at,
       -- Kept local: never sent outside the machine. Read on the wall because a MARK is drawn on
       -- every card without a press; see the note on the people query.
       t.keep_local AS keep_local,
       -- And "Don't swap", the refusal's other mark: swap mode draws both on the card.
       t.keep_from_swaps AS keep_from_swaps,
       COALESCE(h.favorite, 0) AS favorite, h.rating AS rating,
       -- Kept at the top of this wall by this user. Read beside the heart because it is
       -- the same kind of thing: an opinion held per user, sparse, absent meaning no.
       COALESCE(h.pinned, 0) AS pinned,
       -- WHICH OF THE TWO A CARD PRINTS IS THE CALLER'S TO SAY, and `:count_narrowed` says it.
       -- One expression rather than a second statement, so the wall, its membership rule and its
       -- count stay one question. Nought asks for the whole (every file under this row the
       -- viewer may see, which is what the plain wall and the row's own page mean by the number),
       -- and one asks for THIS wall's, which is what a card whose press carries the page it was
       -- pressed from has to say. Un-narrowed the two are the same number. See the people query
       -- above, where the rule is written out in full.
       COALESCE(CASE WHEN :count_narrowed = 1 THEN c.asset_count ELSE w.asset_count END, 0)
         AS asset_count,
       -- The size of exactly the files that number counts, off the same tally.
       COALESCE(CASE WHEN :count_narrowed = 1 THEN c.size_bytes ELSE w.size_bytes END, 0)
         AS size_bytes,
       -- The chosen picture, and only when this viewer may open the file it is a frame of. A cover
       -- is a still from one of the tag's own files, so handing back one they cannot see would
       -- publish it to everybody who can see the tag. `visible` has already applied the vault and
       -- the grants, so this is the same test the tile itself gets. The same shape People,
       -- Sites, collections and photo sets use, written out rather than shared for the reason
       -- the whole statement is.
       --
       -- `:reveal_named`, not `:reveal`: the looser flag is also set by PLACEHOLDER mode, where a
       -- card is not a grid tile: the grid can draw a lock and this cannot, so the card would get
       -- a picture address that answers 404, a broken thumbnail saying there is something here you
       -- are not being shown.
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = t.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN t.cover_asset_id END AS cover_asset_id,
       -- An UPLOADED cover, which carries no visibility question because it is not a file in the
       -- library: it is a picture somebody put on this row, and anybody who may be shown the row
       -- may see it. That is why it has no CASE above it while the line beside it does.
       CASE WHEN {{LOCKED}} THEN NULL ELSE t.cover_upload_id END AS cover_upload_id,
       -- HOW the picture sits in its frame, read raw: `views._frame` honours it only while
       -- it names the picture the gated columns here still name, so a withheld file takes its
       -- frame with it and no CASE is needed.
       t.cover_frame AS cover_frame,
       -- WHICH MOMENT of that file, withheld with the file. The cover's address names it, and an
       -- address that names its picture is the only one the server will let the browser keep
       -- (`names_its_cover`); without it a chosen frame would be re-checked on every visit.
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = t.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN t.cover_at_ms END AS cover_at_ms,
       -- How many this viewer may see in total, counted over the whole scoped result rather than
       -- over the page taken out of it. The same window People's listing uses, and for the same
       -- reason: a second query for the count is a second copy of every scoping rule above, and the
       -- two would be free to disagree about which tags are visible.
       -- WHICH spelling the term matched, when it was not the name on the row. The same column a
       -- person's listing carries, for the same reason: a row that comes back for a word the row
       -- does not contain reads as the box answering a different question, and this is the line
       -- that explains it.
       CASE WHEN :prefix = '' OR t.name LIKE :like ESCAPE '\\' THEN NULL ELSE
         (SELECT ta.alias FROM tag_aliases ta
           WHERE ta.tag_id = t.id AND ta.alias LIKE :like ESCAPE '\\'
           ORDER BY ta.alias COLLATE NOCASE LIMIT 1) END AS matched_alias,
       -- The tag this one is filed under, so a picker can group a branch under its parent. Held
       -- back with a locked tile's name, and for a parent this viewer has hidden or may not be shown.
       CASE WHEN {{LOCKED}} THEN NULL ELSE
         (SELECT pt.id FROM tags pt WHERE pt.id = t.parent_id AND {{PARENT_SHOWN}}) END AS parent_id,
       CASE WHEN {{LOCKED}} THEN NULL ELSE
         (SELECT pt.name FROM tags pt
           LEFT JOIN tag_user_state ph ON ph.tag_id = pt.id AND ph.user_id = :viewer
           WHERE pt.id = t.parent_id AND (:reveal_named = 1 OR COALESCE(ph.hidden, 0) = 0)
             AND {{PARENT_SHOWN}})
         END AS parent_name,
       COUNT(*) OVER () AS total_count
  FROM tags t
  LEFT JOIN counted c ON c.tag_id = t.id
  -- ...and beside it, the same tally over the permitted set. The card reads THIS one; the wall's
  -- membership rule below still reads the narrowed one. See `whole` for why they are two numbers.
  LEFT JOIN whole w ON w.tag_id = t.id
  LEFT JOIN tag_user_state h ON h.tag_id = t.id AND h.user_id = :viewer
 WHERE (:tag_id IS NULL OR t.id = :tag_id)
   -- The name OR one of its other names. A tag's other names are in the FILE index (searching
   -- one finds the files), and without them here the tag itself would be unreachable by any word
   -- but the one it happened to be filed under. The record's own help text promises "any of them
   -- finds it".
   AND (:prefix = '' OR (CASE WHEN {{SHOWN}} THEN (t.name LIKE :like ESCAPE '\\'
        OR EXISTS (SELECT 1 FROM tag_aliases ta
                    WHERE ta.tag_id = t.id AND ta.alias LIKE :like ESCAPE '\\')) ELSE 0 END))
   AND (:reveal_named = 1 OR COALESCE(h.hidden, 0) = 0)
   AND (:list_empty = 1 OR COALESCE(c.asset_count, 0) > 0)
   -- A LOCKED TILE matches no typed word: a box that finds a padlock has said the name. See
   -- `_LOCKED_TILE`, where the rule is written out.
   AND NOT ({{LOCKED}} AND :prefix <> '')
   AND 1 = 1
   -- The wall's own ROW narrowing is spliced in here. See `_row_narrowed`.
 -- The wall's chosen order, written out here rather than shared with the queries above.
 --
 -- These are the most security-critical statements in the project and they are written out on
 -- purpose; a shared fragment is a fragment that can be assembled wrongly. The fall-through is the
 -- same too: any key this does not know leaves the ordinary most-used-then-name order as it was.
 --
 -- The order is applied here and not in the BROWSER: the wall pages, so a comparison applied in
 -- the client would sort the fifty rows in hand and call it the order of a thousand: page two
 -- would open on names that belong on page one. Paging and client-side ordering are not two
 -- features; the first one takes the second.
 -- PINNED FIRST, ahead of everything below including the order somebody chose.
 --
 -- That is what a pin means: it is not one more way of sorting a wall, it is a statement that these
 -- few belong at the top of it whatever the rest is doing. An arm under `CASE :entity_sort` would
 -- be a pin that quietly stopped working the moment anybody changed the sort, which is worse than
 -- not having one.
 --
 -- Among themselves the pinned rows fall straight through to the order below, so pinning three
 -- people and asking for A-Z gives those three A-Z at the top. That is why nothing stores WHEN a
 -- pin was made: there is no order it would be read in.
 --
 -- This viewer's, joined on `:viewer` above with the heart and the stars: a pin is an opinion, and
 -- two users sharing an install pin their own walls.
 ORDER BY COALESCE(h.pinned, 0) DESC,
          CASE :entity_sort WHEN 'favorite' THEN COALESCE(h.favorite, 0) END DESC,
          CASE :entity_sort WHEN 'rating' THEN COALESCE(h.rating, 0) END DESC,
          CASE :entity_sort WHEN 'name_az' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(t.name_sort, t.name) END END ASC NULLS LAST,
          CASE :entity_sort WHEN 'name_za' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(t.name_sort, t.name) END END DESC,
          CASE :entity_sort WHEN 'newest' THEN t.id END DESC,
          CASE :entity_sort WHEN 'edited' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE t.edited_at END END DESC NULLS LAST,
          CASE :entity_sort WHEN 'edited' THEN t.id END DESC,
          CASE :entity_sort WHEN 'oldest' THEN t.id END ASC,
          -- The number and the size the card prints, so the cards read in the order asked.
          CASE :entity_sort WHEN 'largest' THEN
            COALESCE(CASE WHEN :count_narrowed = 1 THEN c.asset_count ELSE w.asset_count END, 0)
          END DESC,
          CASE :entity_sort WHEN 'smallest' THEN
            COALESCE(CASE WHEN :count_narrowed = 1 THEN c.asset_count ELSE w.asset_count END, 0)
          END ASC,
          CASE :entity_sort WHEN 'largest_total' THEN
            COALESCE(CASE WHEN :count_narrowed = 1 THEN c.size_bytes ELSE w.size_bytes END, 0)
          END DESC,
          CASE :entity_sort WHEN 'smallest_total' THEN
            COALESCE(CASE WHEN :count_narrowed = 1 THEN c.size_bytes ELSE w.size_bytes END, 0)
          END ASC,
          CASE :entity_sort WHEN 'longest_total' THEN
            NULLIF(CASE WHEN :count_narrowed = 1 THEN c.duration_ms ELSE w.duration_ms END, 0)
          END DESC NULLS LAST,
          CASE :entity_sort WHEN 'shortest_total' THEN
            NULLIF(CASE WHEN :count_narrowed = 1 THEN c.duration_ms ELSE w.duration_ms END, 0)
          END ASC NULLS LAST,
          COALESCE(c.asset_count, 0) DESC, CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(t.name_sort, t.name) END ASC NULLS LAST, t.id ASC
 LIMIT :limit OFFSET :offset
""",
    LOCKED=locked_tile("tag", "t"),
    SHOWN=shown("tag", "t"),
    PARENT_SHOWN=shown("tag", "pt"),
)


_TAGS_HEAD, _TAGS_ACCESS = _cut(_VISIBLE_TAGS, "_VISIBLE_TAGS")


# --- Where a row sits on the rest of the walls ---------------------------------------------------
#
# The People wall and the Photo Sets wall carry the row somebody was looking at in the address, so a
# link opens where the sender was rather than at the top; every wall here does, because a wall that
# loses its place on the way back from a row is the same complaint whichever noun it is a wall of.
#
# Each is CUT OUT of the listing by the one helper, never written again. What must never happen is
# two orderings: a position computed one way and a page taken another puts somebody at an offset
# holding a different row than the one they asked to be taken to, and it shows up as a link landing
# NEAR the right place, which reads as imprecision rather than as a bug.
#: The tags wall in pieces. The facet counts read the same cut; see `_position`.
_TAGS = _wall(_VISIBLE_TAGS, "_VISIBLE_TAGS")

#: Where one row sits in each of those scoped, filtered, ordered walls, counting from one, or no
#: row at all when this viewer may not be shown it. The unfiltered forms; the functions below
#: splice a file filter and a row filter into them exactly as the listings do, because a position
#: only means anything in the list it was taken from.
TAGS_POSITION = _position(_TAGS)


# The unfiltered form of each counted wall: the same statement with its counts stored. Whole
# statements rather than halves, because the filter seam sits inside the live count block and an
# unfiltered wall has nothing to splice there.
_TAGS_STORED = _with_stored_counts(_VISIBLE_TAGS, "_VISIBLE_TAGS")

#: One tag by id, as the unfiltered wall reads it: what a cover or a page asks before it
#: answers, at one row's cost (`one_row`).
TAG_BY_ID = one_row(_TAGS_STORED, "_TAGS_STORED", "tag", "t", "tag_id")


#: A handful of tags by id: the tags wall unfiltered, counted off the stored numbers, with the
#: one-tag condition widened to a list. The ids bind as ONE JSON array under `:ids`, so an id that
#: is not an id matches nothing rather than reading as "no condition".
#:
#: Not the LIVE-count form (`_VISIBLE_TAGS`), which resolves every tag's files through the verdict
#: before the by-id condition is reached: several times the cost of the stored form, for the same
#: rows. Unfiltered the two forms answer the same number (see `_STORED_COUNTS`), so this is the
#: wall's own answer read the way the wall reads it, as `PERSON_BY_ID` is for people.
TAGS_BY_ID = _one_seam(
    _TAGS_STORED,
    "_TAGS_STORED",
    "WHERE (:tag_id IS NULL OR t.id = :tag_id)",
    "WHERE t.id IN (SELECT value FROM json_each(:ids))",
)


def tags_query(where: str, rows: str = _NOTHING) -> str:
    """Tags this viewer may know about, counted over only the files a filter reaches.

    `rows` filters the TAGS themselves (see `_row_narrowed`). The two compose: a wall can be
    "tags in a category, on beach photographs", and neither seam knows about the other.
    """
    if not _narrowed(where):
        return _row_narrowed(_TAGS_STORED, rows)
    return _row_narrowed(_filtered(_TAGS_HEAD, _TAGS_ACCESS, where), rows)


def tags_position(where: str = _NOTHING, rows: str = _NOTHING) -> str:
    """Where one tag sits in that same wall, filtered the same two ways.

    The live count form, never the stored one, and the splices are made into the POSITION statement
    rather than the position being cut out of an already-spliced listing. `photo_sets_position`
    below carries the whole of the reasoning for both halves; this is the same shape on the tags
    wall, and it is the same shape because they are the same question.
    """
    statement = TAGS_POSITION
    if _narrowed(where):
        head, rest = _cut(statement, "TAGS_POSITION")
        statement = _filtered(head, rest, where)
    return _row_narrowed(statement, rows)
