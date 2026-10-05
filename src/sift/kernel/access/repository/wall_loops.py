# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Loops wall: the loops a viewer may know about, on the files they may see."""

from __future__ import annotations

from sift.kernel.access.repository.walls import _cut, _filtered, _position, _wall
from sift.kernel.access.visibility import (
    ANY_COPY_MISSING,
    CONCEALED_BY_THIS_FILE,
)
from sift.kernel.sql_splice import splice

#: Whether the tag asked about is on the Loop itself or on its video.
_TAGGED = (
    "(EXISTS (SELECT 1 FROM loop_tags g WHERE g.loop_id = l.id AND g.tag_id = :loop_tag)"
    " OR EXISTS (SELECT 1 FROM asset_tags t WHERE t.asset_id = l.asset_id AND t.tag_id = :loop_tag))"
)

#: Loops this viewer may see: a stretch of a video, and the video's own shape beside it.
#:
#: The resolver is written out here rather than shared, for the reason given in full above
#: `_VISIBLE_ASSETS_HEAD` in assets.py: a query assembled from fragments is a query that can be
#: assembled wrongly. `VAULT_CASES` in test_access.py puts every way of concealing a file to every
#: copy, so a copy that stops agreeing fails the build rather than quietly differing by a number.
#:
#: This is the one wall with no `:list_empty` gate, and its absence is not an oversight. Every other
#: wall lists NAMES that may have nothing under them, so somebody has to decide whether an empty one
#: belongs on the screen. A loop cannot be empty: it is one stretch of one file, and if that file is
#: not visible there is no row at all.
_VISIBLE_LOOPS = splice(
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
)
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
-- A loop is a pointer into a file, so this is an INNER join to the visible set and there is
-- nothing else to decide. A loop of a file this viewer may not see produces no row (not a row
-- with a zero, not a locked placeholder, no row), which is the same answer an id that was never
-- minted gets, and it is what makes "you cannot be shown a loop of something you cannot be shown"
-- a property of the join rather than of a second rule kept in step with the first.
--
-- The source file's own shape rides along, because a loop is drawn as a still from that file at
-- that moment and the client needs the proportions to lay the wall out before the picture arrives.
--
-- The heart and the stars are THIS viewer's opinion OF THE VIDEO, not of the Loop.
--
-- A pair of the Loop's own would be two hearts for one picture:
-- the tile a Loop is drawn as is a frame of the video, so it carries the video's heart the way every
-- other tile of that video does, and a second opinion sitting behind the same glyph is a
-- disagreement waiting for somebody to press one of them.
--
-- The rest of what a tile needs rides along so it can draw itself from this row alone: whether the
-- still exists yet (a shimmer, not a broken picture), the token that lets a browser keep the
-- pictures for a week, and the name a saved copy would take. Written the same way the asset listing
-- writes them, because they are the same facts about the same file.
SELECT l.id, l.asset_id, l.name, l.start_ms, l.end_ms, l.created_at, l.created_by,
       COALESCE(h.favorite, 0) AS favorite, h.rating AS rating,
       -- The VIDEO's pin, exactly as the heart and the stars beside it are the video's.
       COALESCE(h.pinned, 0) AS pinned,
       -- How many times this user has opened the file, which the tile draws as a tally.
       --
       -- Off the join that is ALREADY here for the three opinions above, so it costs nothing. This
       -- wall's rows do not come from `/assets`, so without it its tiles would show no view count
       -- while the file's own detail does.
       COALESCE(h.view_count, 0) AS view_count,
       -- And the tally beside it, off the same join and for the same reason: `Tile.svelte`
       -- reads one set of names off whatever row it is handed, so a wall that omits this
       -- one draws no number rather than a wrong one. It is NOT NULL on the table and reads
       -- zero for a file nobody has pressed it on; the COALESCE is for the missing row a
       -- LEFT JOIN leaves behind, the same as the three above it.
       COALESCE(h.o_count, 0) AS o_count,
       -- The three remaining facts the SHARED TILE reads off a row, which this wall did not send.
       --
       -- `Tile.svelte` is one component drawing every wall in Sift, and it decides three of its
       -- marks from these: the vault eye, whether that eye is filled (hidden HERE, as against by
       -- something above), and the torn page for a file whose bytes have gone. A row that omits
       -- them does not draw a wrong mark: it draws none, silently, and nothing says so.
       --
       -- Two of them are the media grid's own rules, spliced from `visibility` rather than
       -- restated here: two copies of "is any copy still there" is how one wall comes to disagree
       -- with another about the same file. (Their names are not written with the marker braces:
       -- see `splice`, which replaces a marker wherever it appears, comment included.)
       v.concealed AS vault_hidden,
       {{CONCEALED_BY_THIS_FILE}} AS concealed_here,
       {{ANY_COPY_MISSING}} AS unreachable,
       a.media_type, a.width, a.height, a.duration_ms, a.original_filename,
       EXISTS (SELECT 1 FROM derivatives d
                WHERE d.asset_id = a.id AND d.kind = 'thumb') AS has_thumb,
       -- Whether the LOOP's own picture exists, which is a different question from the one above.
       -- A still is filed under the moment it was cut at, so this asks about `(this video, this
       -- millisecond)` rather than about the video. False is not an error and not a shimmer: the
       -- tile falls back to the video's own still, so a library part-way through building them
       -- looks whole.
       --
       -- `json_extract` rather than comparing against a hand-built `'{"at_ms":' || start_ms || '}'`:
       -- the stored form is canonical JSON written by one function, and rebuilding that shape here
       -- would be a second implementation of it free to disagree the day either changes.
       EXISTS (SELECT 1 FROM derivatives d
                WHERE d.asset_id = a.id AND d.kind = 'thumb'
                  AND json_extract(d.params, '$.at_ms') = l.start_ms) AS has_still,
       (SELECT group_concat(ordered.mark, '|') FROM (
          SELECT d.kind || ':' || COALESCE(d.content_hash, '') AS mark
            FROM derivatives d WHERE d.asset_id = a.id ORDER BY d.kind, d.params
        ) ordered) AS art_marks,
       -- The LOOP's own tags, as one string. A Loop can be tagged in its own right, and a write
       -- with no read is indistinguishable from a write that did not happen.
       --
       -- Ordered inside a subselect because `group_concat` has no ordering of its own, exactly as
       -- `art_marks` above is. Otherwise the same tags come back in different orders and a tile
       -- reshuffles its own chips between pages.
       (SELECT group_concat(ordered.mark, char(31)) FROM (
          SELECT t.id || char(30) || t.name AS mark
            FROM tags t JOIN loop_tags lt ON lt.tag_id = t.id
           WHERE lt.loop_id = l.id ORDER BY t.name
        ) ordered) AS own_tags,
       CASE WHEN EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = l.asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0)) THEN 1 ELSE 0 END AS showable,
       -- The scoped total, over the whole result rather than over the page. See the tag query.
       COUNT(*) OVER () AS total_count
  FROM loops l
  JOIN viewer_assets v ON v.asset_id = l.asset_id AND v.user_id = :viewer
  JOIN assets a ON a.id = l.asset_id
  LEFT JOIN asset_user_state h ON h.asset_id = l.asset_id AND h.user_id = :viewer
 WHERE (:loop_id IS NULL OR l.id = :loop_id)
   AND (:loop_asset_id IS NULL OR l.asset_id = :loop_asset_id)
   -- The search box asks after what the tile is CALLED: the Loop's own name, or a name of its file
   -- (a Loop with no name is drawn under its file's). Read through `v`, so a Loop of a file the
   -- viewer may not see is never asked, whatever the join order.
   AND (:loop_called IS NULL
        OR v.asset_id = l.asset_id AND (
           l.name LIKE :loop_called ESCAPE '\\'
        OR a.title LIKE :loop_called ESCAPE '\\'
        OR a.original_filename LIKE :loop_called ESCAPE '\\'
        OR EXISTS (SELECT 1 FROM asset_locations fl
                    WHERE fl.asset_id = l.asset_id
                      AND fl.filename LIKE :loop_called ESCAPE '\\')))
   AND (:reveal = 1 OR v.concealed = 0)
   AND 1 = 1
     -- The wall's own narrowing is spliced in here. See `_filtered`.
   -- A tag reaches a Loop two ways, answered per Loop and kept out of the file filter (see
   -- `loops_query`). Through `v` as the name is; an admin's own term still narrows before `v`.
   AND (:loop_tag IS NULL OR :is_admin = 0 OR {{TAGGED}})
   AND (:loop_tag IS NULL OR :is_admin = 1 OR v.asset_id = l.asset_id AND {{TAGGED}})
 -- The wall's chosen order: the same words every other wall is ordered by, so somebody who has
 -- learned `Newest first` on one screen has learned it everywhere. `largest` and `smallest` read
 -- the loop's LENGTH, which is this wall's size in the way an item count is another wall's.
 --
 -- The opening words are the seam `_wall` cuts at: the position read is cut out of this
 -- statement, so the two cannot come to disagree about the order they count in.
 ORDER BY
          -- PINNED FIRST, above every sort, exactly as on a wall of files.
          --
          -- `h` is the asset's own opinion row, and that is the right subject here rather than a
          -- compromise: this wall already sorts by `h.favorite` and `h.rating`, so the heart and
          -- the stars on a Loop are the VIDEO's. A pin that meant something
          -- else would be the one opinion on this screen with a different subject from the two
          -- beside it. The consequence is worth saying out loud: two Loops cut from the same video
          -- are both pinned by one press, because there is one thing pinned and it is the video.
          COALESCE(h.pinned, 0) DESC,
          CASE :entity_sort WHEN 'favorite' THEN COALESCE(h.favorite, 0) END DESC,
          CASE :entity_sort WHEN 'rating' THEN COALESCE(h.rating, 0) END DESC,
          CASE :entity_sort WHEN 'name_az' THEN COALESCE(l.name, '') END COLLATE NOCASE ASC,
          CASE :entity_sort WHEN 'name_za' THEN COALESCE(l.name, '') END COLLATE NOCASE DESC,
          CASE :entity_sort WHEN 'newest' THEN l.id END DESC,
          CASE :entity_sort WHEN 'edited' THEN l.edited_at END DESC NULLS LAST,
          CASE :entity_sort WHEN 'edited' THEN l.id END DESC,
          CASE :entity_sort WHEN 'oldest' THEN l.id END ASC,
          CASE :entity_sort WHEN 'largest' THEN l.end_ms - l.start_ms END DESC,
          CASE :entity_sort WHEN 'smallest' THEN l.end_ms - l.start_ms END ASC,
          l.id DESC
 LIMIT :limit OFFSET :offset
""",
    ANY_COPY_MISSING=ANY_COPY_MISSING,
    CONCEALED_BY_THIS_FILE=CONCEALED_BY_THIS_FILE,
    TAGGED=_TAGGED,
)
_LOOPS_HEAD, _LOOPS_ACCESS = _cut(_VISIBLE_LOOPS, "_VISIBLE_LOOPS")
#: The loops wall in pieces. The one of the five with no ROW filter seam (a Loop has no facets
#: of its own), so `loops_position` splices at the file filter alone, exactly as `loops_query`
#: does.
_LOOPS = _wall(_VISIBLE_LOOPS, "_VISIBLE_LOOPS")
LOOPS_POSITION = _position(_LOOPS)


def loops_query(where: str) -> str:
    """Loops this viewer may see, filtered to what a filter reaches.

    The filter filters which FILES the loops are cut from, which is what makes "the loops on
    this person's videos" the same question as every other related list, and what lets the
    viewer's own filter from the bar filter the wall by its files' facets.

    **A tag is NOT part of this filter on this wall.** A tag reaches a Loop two ways (the Loop
    carries it, or the video does), and the statement's own final WHERE answers both, per Loop,
    from `:loop_tag`. So the caller hands the tag over as `tag` and leaves it out of the filter.

    Not by widening the seam to `(<filter>) OR <a Loop of this file carries the tag>`: the OR would
    let a video with a tagged Loop past EVERY other condition in the filter, so on a tag's Loops tab
    with the bar set to `media:image`, or on `?person=P&tag=T`, the Loops of videos the filter had
    ruled out would come back. The final WHERE already says everything such a widening would.
    """
    return _filtered(_LOOPS_HEAD, _LOOPS_ACCESS, where)


def loops_position(where: str) -> str:
    """Where one Loop sits in that same wall.

    The same seam the listing splices, with the same filter: a wall ranked any other way would put a
    Loop at a position in a list the page does not take, and the link would open near the Loop
    rather than at it. The tag is the statement's own final WHERE here too, cut out of the same
    text. One argument rather than two, because this wall has no row filtering to splice: a Loop
    has no facets of its own.
    """
    head, rest = _cut(LOOPS_POSITION, "LOOPS_POSITION")
    return _filtered(head, rest, where)
