# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Photo Sets wall: the photo sets a viewer may know about, with their counts."""

from __future__ import annotations

from sift.kernel.access.repository.walls import (
    _NOTHING,
    _cut,
    _filtered,
    _narrowed,
    _position,
    _row_narrowed,
    _wall,
    _with_stored_counts,
    locked_tile,
    shown,
)
from sift.kernel.sql_splice import splice

#: Photo sets this viewer may know about, each with the number of pictures they can actually see in
#: it. A set is a shoot: the pictures that arrived together, from one gallery or one folder.
#:
#: The resolver is written out here rather than shared, for the reason given in full above
#: `_VISIBLE_ASSETS_HEAD` in assets.py: a query assembled from fragments is a query that can be
#: assembled wrongly. `VAULT_CASES` in test_access.py puts every way of concealing a file to every
#: copy, so a copy that stops agreeing fails the build rather than quietly differing by a number.
#:
#: IMPORTANT: a photo set IS a grant object. A grant can name a set, `ObjectType` has a member for
#: it, the `logical` CTE carries the arm, and `acl_grants` accepts the value (the check constraint
#: was rebuilt for it; see `schema.py`).
#:
#: Hiding one conceals its PICTURES as well as its row (a set taken off somebody's screen whose
#: pictures stayed on it is not hidden), so `logical_vault` carries the arm too.
_VISIBLE_PHOTO_SETS = splice(
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
counted(photo_set_id, item_count, size_bytes, duration_ms) AS (
  SELECT psi.photo_set_id, COUNT(*),
         COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.size_bytes END), 0),
         COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.duration_ms END), 0)
    FROM photo_set_items psi
    CROSS JOIN viewer_assets v ON v.asset_id = psi.asset_id AND v.user_id = :viewer
    JOIN assets a ON a.id = psi.asset_id
   WHERE (:reveal = 1 OR v.concealed = 0)
     AND 1 = 1
     -- The wall's own narrowing is spliced in here. See `_filtered`.
   GROUP BY psi.photo_set_id
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
whole(photo_set_id, item_count, size_bytes, duration_ms) AS (
  SELECT c.object_id, c.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE c.concealed END,
         c.permitted_bytes - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_bytes END,
         c.permitted_ms - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_ms END
    FROM viewer_entity_counts c
   WHERE c.user_id = :viewer AND c.kind = 'photo_set'
)
-- Three scoped columns, and each of them is the same fact reached by a different route.
--
-- `item_count` is counted over `visible` rather than over the set, so it is the size of
-- what this viewer is shown. The absolute size is never sent to anybody: the gap between the two
-- numbers is the size of the set they were kept out of, and a count is enough to publish it.
--
-- The cover is checked against the stricter set. It names one item, and a picture of a concealed
-- item is the item, so a cover the viewer may not open comes back as nothing and the screen
-- draws a blank tile, which is the same answer an empty set gets.
--
-- `:reveal_named` is the same flag the people list reads, and is separate from `:reveal` for the
-- reason. `:reveal` decides whether concealed ITEMS are counted, and placeholder mode sets it. A
-- set's own row is a name somebody chose, which is content and not a locked tile, so it
-- comes back only when the vault is genuinely unlocked.
--
-- A guest sees a photo set only once they can see something in it, which is the rule the people
-- list already uses. Applied here it also settles the empty case: an admin sees a set they
-- have made and not yet filled, and to everybody else it does not exist yet.
-- The heart and the stars are THIS viewer's, off the same row the hidden flag comes from. See the
-- tag query above for why an opinion is joined per viewer rather than stored on the thing.
-- A locked tile has no name; see `_LOCKED_TILE`. `locked` is what the card draws it by.
SELECT ps.id, CASE WHEN {{LOCKED}} THEN '' ELSE ps.name END AS name,
       {{LOCKED}} AS locked, COALESCE(h.hidden, 0) AS vault, ps.created_at,
       ps.origin, ps.folder_id,
       CASE WHEN {{LOCKED}} THEN NULL ELSE ps.origin_url END AS origin_url,
       CASE WHEN {{LOCKED}} THEN NULL ELSE ps.notes END AS notes,
       COALESCE(h.favorite, 0) AS favorite, h.rating AS rating,
       -- Kept at the top of this wall by this user. Read beside the heart because it is
       -- the same kind of thing: an opinion held per user, sparse, absent meaning no.
       COALESCE(h.pinned, 0) AS pinned,
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = ps.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN ps.cover_asset_id END AS cover_asset_id,
       -- An UPLOADED cover, which carries no visibility question because it is not a file in the
       -- library: it is a picture somebody put on this row, and anybody who may be shown the row
       -- may see it. That is why it has no CASE above it while the line beside it does.
       CASE WHEN {{LOCKED}} THEN NULL ELSE ps.cover_upload_id END AS cover_upload_id,
       -- HOW the picture sits in its frame, read raw: `views._frame` honours it only while
       -- it names the picture the gated columns here still name, so a withheld file takes its
       -- frame with it and no CASE is needed.
       ps.cover_frame AS cover_frame,
       -- WHICH MOMENT of that file, withheld with the file. The cover's address names it, and an
       -- address that names its picture is the only one the server will let the browser keep
       -- (`names_its_cover`); without it a chosen frame would be re-checked on every visit.
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = ps.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN ps.cover_at_ms END AS cover_at_ms,
       -- WHICH OF THE TWO A CARD PRINTS IS THE CALLER'S TO SAY, and `:count_narrowed` says it.
       -- One expression rather than a second statement, so the wall, its membership rule and its
       -- count stay one question. Nought asks for the whole (every item in this set the viewer
       -- may see, which is what the plain wall and the set's own page mean by the number), and
       -- one asks for THIS wall's, which is what a card whose press carries the page it was
       -- pressed from has to say. Un-narrowed the two are the same number. See the people query
       -- above, where the rule is written out in full.
       COALESCE(CASE WHEN :count_narrowed = 1 THEN n.item_count ELSE w.item_count END, 0)
         AS item_count,
       -- The size of exactly the files that number counts, off the same tally.
       COALESCE(CASE WHEN :count_narrowed = 1 THEN n.size_bytes ELSE w.size_bytes END, 0)
         AS size_bytes,
       -- The scoped total, over the whole result rather than over the page. See the tag query.
       COUNT(*) OVER () AS total_count
  FROM photo_sets ps
  LEFT JOIN counted n ON n.photo_set_id = ps.id
  -- ...and beside it, the same tally over the permitted set. The card reads THIS one; the wall's
  -- membership rule below still reads the narrowed one. See `whole` for why they are two numbers.
  LEFT JOIN whole w ON w.photo_set_id = ps.id
  LEFT JOIN photo_set_user_state h ON h.photo_set_id = ps.id AND h.user_id = :viewer
 WHERE (:photo_set_id IS NULL OR ps.id = :photo_set_id)
   -- The name, where a picker or a box is narrowing the list to what somebody is typing.
   -- Plain prefix matching on the name alone: these two carry no other names to match against,
   -- unlike a tag or a person, so there is nothing here to report having matched INSTEAD.
   AND (:prefix = '' OR ({{SHOWN}} AND ps.name LIKE :like ESCAPE '\\'))
   AND (:reveal_named = 1 OR COALESCE(h.hidden, 0) = 0)
   AND (:list_empty = 1 OR COALESCE(n.item_count, 0) > 0)
   -- A LOCKED TILE matches no typed word: a box that finds a padlock has said the name. See
   -- `_LOCKED_TILE`, where the rule is written out.
   AND NOT ({{LOCKED}} AND :prefix <> '')
   AND 1 = 1
   -- The wall's own ROW narrowing is spliced in here. See `_row_narrowed`.
 -- The wall's chosen order. Written out rather than shared: see the tag query above, which also
 -- says why a wall that pages cannot order itself in the browser. `n.item_count` is this wall's
 -- size, standing where the others read an asset count.
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
          CASE :entity_sort WHEN 'name_az' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(ps.name_sort, ps.name) END END ASC NULLS LAST,
          CASE :entity_sort WHEN 'name_za' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(ps.name_sort, ps.name) END END DESC,
          CASE :entity_sort WHEN 'newest' THEN ps.id END DESC,
          CASE :entity_sort WHEN 'edited' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE ps.edited_at END END DESC NULLS LAST,
          CASE :entity_sort WHEN 'edited' THEN ps.id END DESC,
          CASE :entity_sort WHEN 'oldest' THEN ps.id END ASC,
          -- The number and the size the card prints, so the cards read in the order asked.
          CASE :entity_sort WHEN 'largest' THEN
            COALESCE(CASE WHEN :count_narrowed = 1 THEN n.item_count ELSE w.item_count END, 0)
          END DESC,
          CASE :entity_sort WHEN 'smallest' THEN
            COALESCE(CASE WHEN :count_narrowed = 1 THEN n.item_count ELSE w.item_count END, 0)
          END ASC,
          CASE :entity_sort WHEN 'largest_total' THEN
            COALESCE(CASE WHEN :count_narrowed = 1 THEN n.size_bytes ELSE w.size_bytes END, 0)
          END DESC,
          CASE :entity_sort WHEN 'smallest_total' THEN
            COALESCE(CASE WHEN :count_narrowed = 1 THEN n.size_bytes ELSE w.size_bytes END, 0)
          END ASC,
          CASE :entity_sort WHEN 'longest_total' THEN
            NULLIF(CASE WHEN :count_narrowed = 1 THEN n.duration_ms ELSE w.duration_ms END, 0)
          END DESC NULLS LAST,
          CASE :entity_sort WHEN 'shortest_total' THEN
            NULLIF(CASE WHEN :count_narrowed = 1 THEN n.duration_ms ELSE w.duration_ms END, 0)
          END ASC NULLS LAST,
          CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(ps.name_sort, ps.name) END ASC NULLS LAST, ps.id ASC
 LIMIT :limit OFFSET :offset
""",
    LOCKED=locked_tile("photo_set", "ps"),
    SHOWN=shown("photo_set", "ps"),
)

#: The photo-sets wall in pieces, cut once. The facet counts read it and so does the position
#: lookup below: a second cut of the same statement would be a second chance to cut it wrongly.
_PHOTO_SETS = _wall(_VISIBLE_PHOTO_SETS, "_VISIBLE_PHOTO_SETS")

#: Where one photo set sits in the scoped, filtered, ordered wall, counting from one, or no row
#: at all when this viewer may not be shown it. The People wall's, one screen over; see `_position`.
#:
#: It is the unfiltered form. `photo_sets_position` splices a file filter and a row filter into
#: it exactly as `photo_sets_query` does into the listing, because a position only means anything
#: in the list it was taken from: set forty of everything is a different set from set forty of a
#: person's, and handing one back where the caller meant the other lands somebody a long way from
#: where they asked to be.
PHOTO_SETS_POSITION = _position(_PHOTO_SETS)
_PHOTO_SETS_HEAD, _PHOTO_SETS_ACCESS = _cut(_VISIBLE_PHOTO_SETS, "_VISIBLE_PHOTO_SETS")
_PHOTO_SETS_STORED = _with_stored_counts(_VISIBLE_PHOTO_SETS, "_VISIBLE_PHOTO_SETS")


def photo_sets_query(where: str, rows: str = _NOTHING) -> str:
    """Photo sets this viewer may know about, counted over only the files a filter reaches.

    `rows` filters the sets themselves; a set has one facet and it is admin-only.
    """
    if not _narrowed(where):
        return _row_narrowed(_PHOTO_SETS_STORED, rows)
    return _row_narrowed(_filtered(_PHOTO_SETS_HEAD, _PHOTO_SETS_ACCESS, where), rows)


def photo_sets_position(where: str = _NOTHING, rows: str = _NOTHING) -> str:
    """Where one photo set sits in that same wall, filtered the same two ways.

    The splices are made into the POSITION statement rather than the position being cut out of an
    already-spliced listing, and the order matters. `_wall` cuts on four markers and requires
    exactly one of each; a filter is arbitrary generated SQL and may carry a `SELECT` of its own at
    the start of a line, so re-cutting a filtered statement would be a build that works until the
    day somebody filters by something with a subquery in it. Both seams this splices at are inside
    the pieces `_position` keeps (the file filter in the count CTE, the row narrowing in the final
    WHERE), so splicing afterwards reaches exactly what the listing reaches.

    The live count form, never the stored one: the filtered listing uses the live form and this has
    to rank in the same order the page is taken in. Unfiltered the two count the same number, so
    the only cost is the count itself, paid once for a single lookup rather than per page.
    """
    statement = PHOTO_SETS_POSITION
    if _narrowed(where):
        head, rest = _cut(statement, "PHOTO_SETS_POSITION")
        statement = _filtered(head, rest, where)
    return _row_narrowed(statement, rows)
