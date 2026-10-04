# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Songs wall: the songs a viewer may know about, with their counts."""

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

#: SONGS this viewer may know about: one piece of music each, with how many of the files carrying
#: it they may see. The Photo Sets wall above, over `song_files`, written out in full for the reason
#: every wall here is, the hidden flag and all.
_VISIBLE_SONGS = splice(
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
counted(song_id, item_count, size_bytes) AS (
  SELECT sf.song_id, COUNT(*),
         COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.size_bytes END), 0)
    FROM song_files sf
    CROSS JOIN viewer_assets v ON v.asset_id = sf.asset_id AND v.user_id = :viewer
    JOIN assets a ON a.id = sf.asset_id
   WHERE (:reveal = 1 OR v.concealed = 0)
     AND 1 = 1
     -- The wall's own narrowing is spliced in here. See `_filtered`.
   GROUP BY sf.song_id
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
whole(song_id, item_count, size_bytes) AS (
  SELECT c.object_id, c.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE c.concealed END,
         c.permitted_bytes - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_bytes END
    FROM viewer_entity_counts c
   WHERE c.user_id = :viewer AND c.kind = 'song'
)
-- The same three scoped columns a Photo Set's wall carries, for the same reasons: `item_count` is
-- counted over what this viewer is shown, never the song's absolute size; the cover is checked
-- against the stricter set; and the name comes back only where a file this viewer may see carries
-- the song, the rule every wall of things uses.
--
-- A song this viewer hid is a name, and comes back only with the vault genuinely unlocked
-- (`:reveal_named`), as a hidden Photo Set's does; hiding it also conceals the files that carry it
-- (the verdict's own arm). A song whose every file the vault is holding back is a locked tile
-- exactly as a Photo Set's is (`_LOCKED_TILE`, off the stored count).
--
-- A guest sees a song only once they can see a file that carries it; an admin sees one nothing
-- carries any more, so it can be renamed, merged or deleted.
-- The heart and the stars are THIS viewer's. See the tag query above.
-- A locked tile has no name; see `_LOCKED_TILE`. `locked` is what the card draws it by.
SELECT sg.id, CASE WHEN {{LOCKED}} THEN '' ELSE sg.name END AS name,
       {{LOCKED}} AS locked, COALESCE(h.hidden, 0) AS vault, sg.created_at,
       -- Which AcoustID recording it is, withheld with the name: it identifies the music.
       CASE WHEN {{LOCKED}} THEN NULL ELSE sg.recording_id END AS recording_id,
       CASE WHEN {{LOCKED}} THEN NULL ELSE sg.notes END AS notes,
       -- The artists it credits, in order, packed as `[[id, name], ...]`: a handful per song, read
       -- by the song's primary key. Withheld with the name on a locked tile.
       CASE WHEN {{LOCKED}} THEN NULL ELSE (
         SELECT json_group_array(json_array(cr.id, cr.name))
           FROM (SELECT ar.id, ar.name FROM song_artists sa JOIN artists ar ON ar.id = sa.artist_id
                  WHERE sa.song_id = sg.id ORDER BY sa.position, ar.id) cr
       ) END AS artists,
       COALESCE(h.favorite, 0) AS favorite, h.rating AS rating,
       -- Kept at the top of this wall by this user. Read beside the heart because it is
       -- the same kind of thing: an opinion held per user, sparse, absent meaning no.
       COALESCE(h.pinned, 0) AS pinned,
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = sg.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN sg.cover_asset_id END AS cover_asset_id,
       -- An UPLOADED cover, which carries no visibility question because it is not a file in the
       -- library: it is a picture somebody put on this row, and anybody who may be shown the row
       -- may see it. That is why it has no CASE above it while the line beside it does.
       CASE WHEN {{LOCKED}} THEN NULL ELSE sg.cover_upload_id END AS cover_upload_id,
       -- HOW the picture sits in its frame, read raw: `views._frame` honours it only while
       -- it names the picture the gated columns here still name, so a withheld file takes its
       -- frame with it and no CASE is needed.
       sg.cover_frame AS cover_frame,
       -- WHICH MOMENT of that file, withheld with the file. The cover's address names it, and an
       -- address that names its picture is the only one the server will let the browser keep
       -- (`names_its_cover`); without it a chosen frame would be re-checked on every visit.
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = sg.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN sg.cover_at_ms END AS cover_at_ms,
       -- Which of the two tallies a card prints is the caller's to say (`:count_narrowed`): the
       -- song's every file this viewer may see, or how many are on THIS wall. See the people
       -- query, where the rule is written out.
       COALESCE(CASE WHEN :count_narrowed = 1 THEN n.item_count ELSE w.item_count END, 0)
         AS item_count,
       -- The size of exactly the files that number counts, off the same tally.
       COALESCE(CASE WHEN :count_narrowed = 1 THEN n.size_bytes ELSE w.size_bytes END, 0)
         AS size_bytes,
       -- The scoped total, over the whole result rather than over the page. See the tag query.
       COUNT(*) OVER () AS total_count
  FROM songs sg
  LEFT JOIN counted n ON n.song_id = sg.id
  -- ...and beside it, the same tally over the permitted set. The card reads THIS one; the wall's
  -- membership rule below still reads the narrowed one. See `whole` for why they are two numbers.
  LEFT JOIN whole w ON w.song_id = sg.id
  LEFT JOIN song_user_state h ON h.song_id = sg.id AND h.user_id = :viewer
 WHERE (:song_id IS NULL OR sg.id = :song_id)
   -- The name, where a picker or a box is narrowing the list to what somebody is typing, or the
   -- name of an artist it credits: a song is found by who sings it as well as by what it is
   -- called (the search box's dropdown reads this wall, and the Music page's box is this wall).
   AND (:prefix = '' OR sg.name LIKE :like ESCAPE '\\'
        OR EXISTS (SELECT 1 FROM song_artists pa JOIN artists par ON par.id = pa.artist_id
                    WHERE pa.song_id = sg.id AND par.name LIKE :like ESCAPE '\\'))
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
          CASE :entity_sort WHEN 'name_az' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(sg.name_sort, sg.name) END END ASC NULLS LAST,
          CASE :entity_sort WHEN 'name_za' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(sg.name_sort, sg.name) END END DESC,
          CASE :entity_sort WHEN 'newest' THEN sg.id END DESC,
          CASE :entity_sort WHEN 'edited' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE sg.edited_at END END DESC NULLS LAST,
          CASE :entity_sort WHEN 'edited' THEN sg.id END DESC,
          CASE :entity_sort WHEN 'oldest' THEN sg.id END ASC,
          -- By the first artist it credits, withheld on a locked tile as the name is.
          CASE :entity_sort WHEN 'artist' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE (
            SELECT COALESCE(ar.name_sort, ar.name) FROM song_artists sa
              JOIN artists ar ON ar.id = sa.artist_id
             WHERE sa.song_id = sg.id ORDER BY sa.position, ar.id LIMIT 1) END END ASC NULLS LAST,
          -- The SAME expression the card prints, `:count_narrowed` and all. A wall ordered by
          -- size that orders on the other tally puts its cards out of the order their own numbers
          -- read in, which looks like a broken sort and is a second answer to one question.
          CASE :entity_sort WHEN 'largest' THEN
            COALESCE(CASE WHEN :count_narrowed = 1 THEN n.item_count ELSE w.item_count END, 0)
          END DESC,
          CASE :entity_sort WHEN 'smallest' THEN
            COALESCE(CASE WHEN :count_narrowed = 1 THEN n.item_count ELSE w.item_count END, 0)
          END ASC,
          CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(sg.name_sort, sg.name) END ASC NULLS LAST, sg.id ASC
 LIMIT :limit OFFSET :offset
""",
    LOCKED=locked_tile("song", "sg"),
)

#: The songs wall in pieces, cut once. The facet counts read it and so does the position lookup.
_SONGS = _wall(_VISIBLE_SONGS, "_VISIBLE_SONGS")

#: Where one song sits in the scoped, filtered, ordered wall. See `PHOTO_SETS_POSITION`.
SONGS_POSITION = _position(_SONGS)
_SONGS_HEAD, _SONGS_ACCESS = _cut(_VISIBLE_SONGS, "_VISIBLE_SONGS")
_SONGS_STORED = _with_stored_counts(_VISIBLE_SONGS, "_VISIBLE_SONGS")


def songs_query(where: str, rows: str = _NOTHING) -> str:
    """Songs this viewer may know about, counted over only the files a filter reaches. The Photo
    Sets wall's two seams, the same way round: `rows` filters the songs themselves."""
    if not _narrowed(where):
        return _row_narrowed(_SONGS_STORED, rows)
    return _row_narrowed(_filtered(_SONGS_HEAD, _SONGS_ACCESS, where), rows)


def songs_position(where: str = _NOTHING, rows: str = _NOTHING) -> str:
    """Where one song sits in that same wall, filtered the same two ways. See
    `photo_sets_position`, which carries the reasoning for both halves."""
    statement = SONGS_POSITION
    if _narrowed(where):
        head, rest = _cut(statement, "SONGS_POSITION")
        statement = _filtered(head, rest, where)
    return _row_narrowed(statement, rows)
