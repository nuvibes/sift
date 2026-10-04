# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Collections wall: the collections a viewer may know about, with their counts."""

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
)
from sift.kernel.sql_splice import splice

#: Collections this viewer may know about, each with the number of items they can actually see in
#: it. The resolver is written out here rather than shared, for the reason given in full above
#: `_VISIBLE_ASSETS_HEAD` in assets.py: a query assembled from fragments is a query that can be
#: assembled wrongly. `VAULT_CASES` in test_access.py puts every way of concealing a file to every
#: copy, so a copy that stops agreeing fails the build rather than quietly differing by a number.
_VISIBLE_COLLECTIONS = splice(
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
counted(collection_id, item_count, size_bytes) AS (
  SELECT ci.collection_id, COUNT(*),
         COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.size_bytes END), 0)
    FROM collection_items ci
    CROSS JOIN viewer_assets v ON v.asset_id = ci.asset_id AND v.user_id = :viewer
    JOIN assets a ON a.id = ci.asset_id
   WHERE (:reveal = 1 OR v.concealed = 0)
     AND 1 = 1
     -- The wall's own narrowing is spliced in here. See `_filtered`.
   GROUP BY ci.collection_id
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
whole(collection_id, item_count, size_bytes) AS (
  SELECT c.object_id, c.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE c.concealed END,
         c.permitted_bytes - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_bytes END
    FROM viewer_entity_counts c
   WHERE c.user_id = :viewer AND c.kind = 'collection'
)
-- Three scoped columns, and each of them is the same fact reached by a different route.
--
-- `item_count` is counted over `visible` rather than over the collection, so it is the size of
-- what this viewer is shown. The absolute size is never sent to anybody: the gap between the two
-- numbers is the size of the set they were kept out of, and a count is enough to publish it.
--
-- The cover is checked against the stricter set. It names one item, and a picture of a concealed
-- item is the item, so a cover the viewer may not open comes back as nothing and the screen
-- draws a blank tile, which is the same answer an empty collection gets.
--
-- `:reveal_named` is the same flag the people list reads, and is separate from `:reveal` for the
-- reason. `:reveal` decides whether concealed ITEMS are counted, and placeholder mode sets it. A
-- collection's own row is a name somebody chose, which is content and not a locked tile, so it
-- comes back only when the vault is genuinely unlocked.
--
-- A guest sees a collection only once they can see something in it, which is the rule the people
-- list already uses. Applied here it also settles the empty case: an admin sees a collection they
-- have made and not yet filled, and to everybody else it does not exist yet.
-- The heart and the stars are THIS viewer's, off the same row the hidden flag comes from. See the
-- tag query above for why an opinion is joined per viewer rather than stored on the thing.
-- A locked tile has no name; see `_LOCKED_TILE`. `locked` is what the card draws it by.
SELECT c.id, CASE WHEN {{LOCKED}} THEN '' ELSE c.name END AS name,
       {{LOCKED}} AS locked, COALESCE(h.hidden, 0) AS vault, c.owner_id, c.created_at,
       COALESCE(h.favorite, 0) AS favorite, h.rating AS rating,
       -- Kept at the top of this wall by this user. Read beside the heart because it is
       -- the same kind of thing: an opinion held per user, sparse, absent meaning no.
       COALESCE(h.pinned, 0) AS pinned,
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = c.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN c.cover_asset_id END AS cover_asset_id,
       -- An UPLOADED cover, which carries no visibility question because it is not a file in the
       -- library: it is a picture somebody put on this row, and anybody who may be shown the row
       -- may see it. That is why it has no CASE above it while the line beside it does.
       CASE WHEN {{LOCKED}} THEN NULL ELSE c.cover_upload_id END AS cover_upload_id,
       -- HOW the picture sits in its frame, read raw: `views._frame` honours it only while
       -- it names the picture the gated columns here still name, so a withheld file takes its
       -- frame with it and no CASE is needed.
       c.cover_frame AS cover_frame,
       -- WHICH MOMENT of that file, withheld with the file. The cover's address names it, and an
       -- address that names its picture is the only one the server will let the browser keep
       -- (`names_its_cover`); without it a chosen frame would be re-checked on every visit.
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = c.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN c.cover_at_ms END AS cover_at_ms,
       COALESCE(w.item_count, 0) AS item_count,
       -- The size of exactly the files that number counts, off the same tally.
       COALESCE(w.size_bytes, 0) AS size_bytes,
       -- The scoped total, over the whole result rather than over the page. See the tag query.
       COUNT(*) OVER () AS total_count
  FROM collections c
  LEFT JOIN counted n ON n.collection_id = c.id
  -- ...and beside it, the same tally over the permitted set. The card reads THIS one; the wall's
  -- membership rule below still reads the narrowed one. See `whole` for why they are two numbers.
  LEFT JOIN whole w ON w.collection_id = c.id
  LEFT JOIN collection_user_state h ON h.collection_id = c.id AND h.user_id = :viewer
 WHERE (:collection_id IS NULL OR c.id = :collection_id)
   -- The name, where a picker or a box is narrowing the list to what somebody is typing.
   -- Plain prefix matching on the name alone: these two carry no other names to match against,
   -- unlike a tag or a person, so there is nothing here to report having matched INSTEAD.
   AND (:prefix = '' OR c.name LIKE :like ESCAPE '\\')
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
          CASE :entity_sort WHEN 'name_az' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(c.name_sort, c.name) END END ASC NULLS LAST,
          CASE :entity_sort WHEN 'name_za' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(c.name_sort, c.name) END END DESC,
          CASE :entity_sort WHEN 'newest' THEN c.id END DESC,
          CASE :entity_sort WHEN 'edited' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE c.edited_at END END DESC NULLS LAST,
          CASE :entity_sort WHEN 'edited' THEN c.id END DESC,
          CASE :entity_sort WHEN 'oldest' THEN c.id END ASC,
          CASE :entity_sort WHEN 'largest' THEN COALESCE(w.item_count, 0) END DESC,
          CASE :entity_sort WHEN 'smallest' THEN COALESCE(w.item_count, 0) END ASC,
          CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(c.name_sort, c.name) END ASC NULLS LAST, c.id ASC
 LIMIT :limit OFFSET :offset
""",
    LOCKED=locked_tile("collection", "c"),
)
_COLLECTIONS_HEAD, _COLLECTIONS_ACCESS = _cut(_VISIBLE_COLLECTIONS, "_VISIBLE_COLLECTIONS")
#: The collections wall in pieces.
_COLLECTIONS = _wall(_VISIBLE_COLLECTIONS, "_VISIBLE_COLLECTIONS")
COLLECTIONS_POSITION = _position(_COLLECTIONS)
_COLLECTIONS_STORED = _with_stored_counts(_VISIBLE_COLLECTIONS, "_VISIBLE_COLLECTIONS")


def collections_query(where: str, rows: str = _NOTHING) -> str:
    """Collections this viewer may know about, counted over only the files a filter reaches.

    `rows` filters the collections themselves: whose they are, what has been shared.
    """
    if not _narrowed(where):
        return _row_narrowed(_COLLECTIONS_STORED, rows)
    return _row_narrowed(_filtered(_COLLECTIONS_HEAD, _COLLECTIONS_ACCESS, where), rows)


def collections_position(where: str = _NOTHING, rows: str = _NOTHING) -> str:
    """Where one collection sits in that same wall, filtered the same two ways. See
    `tags_position`, which is the same three lines on the wall next door."""
    statement = COLLECTIONS_POSITION
    if _narrowed(where):
        head, rest = _cut(statement, "COLLECTIONS_POSITION")
        statement = _filtered(head, rest, where)
    return _row_narrowed(statement, rows)
