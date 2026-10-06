# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Sites wall: the Sites a viewer may know about, each counted over every file it reaches."""

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
    shown,
)
from sift.kernel.access.sites import SITE_CONCEALED, SITE_REACH, site_address
from sift.kernel.sql_splice import splice

_VISIBLE_SITES = splice(
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
-- COUNT(DISTINCT), because grouping one level up fans out. An asset attributed to two usernames
-- on the same site (a repost, or a username that was renamed) joins twice and would be counted
-- twice. The usernames query above groups by username, where the join table's primary key makes
-- COUNT(*) exact; that stops being true the moment the grouping moves to the site. (It is the
-- paragraph directly above `_VISIBLE_USERNAMES`'s own `counted`.)
--
-- EVERY SITE EACH SITE REACHES: itself, and everything whose parent chain arrives at it.
--
-- A site can be part of another site, so a network owns labels and a label publishes the files.
-- Counted by the username's own site alone, a network would be a card reading nought beside a
-- Files tab holding thousands: the tab asks the filter, which expands the same way. One
-- expansion, read by both counts below, so the card and the tab cannot come apart.
--
-- The walk itself is NOT written here. It is one fragment, `kernel/access/sites.py`, spliced in as
-- SITE_REACH, so this card, the search leaf and the two rules that decide who may see a file
-- cannot disagree about what a network reaches. That is also where the reasons live (UP rather
-- than down, UNION so a parent loop terminates, and why a library that nests nothing pays nothing
-- for it).
lineage(site_id, ancestor_id) AS ({{SITE_REACH}}),
-- A file reaches a Site once however many of its usernames it is filed under, so the count is of
-- DISTINCT files, and the size is summed over the same files made distinct first: no aggregate
-- mends a SUM the way `COUNT(DISTINCT ...)` mends a count.
counted(site_id, asset_count, size_bytes, duration_ms) AS (
  SELECT r.ancestor_id, COUNT(*), COALESCE(SUM(r.size_bytes), 0), COALESCE(SUM(r.duration_ms), 0)
    FROM (
  SELECT DISTINCT li.ancestor_id, aa.asset_id,
         CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.size_bytes END AS size_bytes,
         CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.duration_ms END AS duration_ms
    FROM asset_usernames aa
    JOIN usernames ac ON ac.id = aa.username_id
    CROSS JOIN viewer_assets v ON v.asset_id = aa.asset_id AND v.user_id = :viewer
    JOIN assets a ON a.id = aa.asset_id
    JOIN lineage li ON li.site_id = ac.site_id
   WHERE (:reveal = 1 OR v.concealed = 0)
     AND ac.site_id IS NOT NULL
     AND 1 = 1
     -- The wall's own narrowing is spliced in here. See `_filtered`.
    ) r
   GROUP BY r.ancestor_id
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
whole(site_id, asset_count, size_bytes, duration_ms) AS (
  SELECT c.object_id, c.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE c.concealed END,
         c.permitted_bytes - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_bytes END,
         c.permitted_ms - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_ms END
    FROM viewer_entity_counts c
   WHERE c.user_id = :viewer AND c.kind = 'site'
)
-- HOW MANY PEOPLE EACH SITE HAS MEDIA OF is NOT asked here. The card reads the People cell of its
-- own card counts (`_CARD_COUNTS`, off the stored pairs), which is the same question (a person is
-- on a site's card exactly when a file this viewer may be shown carries both, with the person
-- passing `:reveal_named`), asked of the page's rows rather than of every site. The router copies
-- that cell into `people_count`. A site with nothing
-- in it that this viewer may see is not there for them, which is how a container is concealed by
-- the grants alone. `:reveal_named` is the other way, and it is a deliberate act rather than a
-- consequence: a site this viewer hid leaves their list ENTIRELY until they open Hidden. Left
-- listed with a zero beside it, the name and the zero together are the disclosure. See
-- `reveals_named_rows`.
--
-- The count is scoped for the reason every count here is scoped: a site reporting "812" to
-- somebody who may see four of them has published the size of the set they were kept out of.
-- The cover is checked against what this viewer may actually open, exactly as a person's and a
-- collection's are. A site has no vault flag of its own, but the asset it wears certainly can,
-- and a cover nobody may open comes back as nothing rather than as a broken picture.
--
-- The heart and the stars are this viewer's own; see the note on the people query above.
-- The Site's address is its FIRST LINK by id (`sites.SITE_ADDRESS`); the wire still calls it
-- `site_url`, which is what it has always been to a screen.
-- A locked tile has no name; see `_LOCKED_TILE`. `locked` is what the card draws it by.
SELECT pl.id, CASE WHEN {{LOCKED}} THEN '' ELSE pl.name END AS name,
       {{LOCKED}} AS locked, CASE WHEN {{LOCKED}} THEN NULL ELSE {{SITE_ADDRESS}} END AS site_url,
       CASE WHEN {{LOCKED}} THEN NULL ELSE pl.notes END AS notes, COALESCE(st.hidden, 0) AS vault,
       -- Kept local: never sent outside the machine. Read on the wall because a MARK is drawn on
       -- every card without a press; see the note on the people query.
       pl.keep_local AS keep_local,
       -- And "Don't swap", the refusal's other mark: swap mode draws both on the card.
       pl.keep_from_swaps AS keep_from_swaps,
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
       -- The chosen picture, against the stricter flag, for the reason the tags wall gives: with
       -- the vault shut and placeholder mode on, a site's card would name a file in the vault as
       -- its cover and the picture behind that address would answer 404.
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = pl.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN pl.cover_asset_id END AS cover_asset_id,
       -- An UPLOADED cover, which carries no visibility question because it is not a file in the
       -- library: it is a picture somebody put on this row, and anybody who may be shown the row
       -- may see it. That is why it has no CASE above it while the line beside it does.
       CASE WHEN {{LOCKED}} THEN NULL ELSE pl.cover_upload_id END AS cover_upload_id,
       -- HOW the picture sits in its frame, read raw: `views._frame` honours it only while
       -- it names the picture the gated columns here still name, so a withheld file takes its
       -- frame with it and no CASE is needed.
       pl.cover_frame AS cover_frame,
       -- WHICH MOMENT of that file, withheld with the file. The cover's address names it, and an
       -- address that names its picture is the only one the server will let the browser keep
       -- (`names_its_cover`); without it a chosen frame would be re-checked on every visit.
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = pl.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN pl.cover_at_ms END AS cover_at_ms,
       COALESCE(st.favorite, 0) AS favorite,
       -- Kept at the top of this wall by this user. Read beside the heart because it is
       -- the same kind of thing: an opinion held per user, sparse, absent meaning no.
       COALESCE(st.pinned, 0) AS pinned,
       st.rating AS rating,
       -- WHICH spelling the term matched, when it was not the name on the row. See the person and
       -- tag queries; a site linked to a stash-box usually carries three or four other names.
       CASE WHEN :prefix = '' OR pl.name LIKE :like ESCAPE '\\' THEN NULL ELSE
         (SELECT pa.alias FROM site_aliases pa
           WHERE pa.site_id = pl.id AND pa.alias LIKE :like ESCAPE '\\'
           ORDER BY pa.alias COLLATE NOCASE LIMIT 1) END AS matched_alias,
       -- The scoped total, over the whole result rather than over the page. See the tag query.
       COUNT(*) OVER () AS total_count
  FROM sites pl
  LEFT JOIN counted c ON c.site_id = pl.id
  -- ...and beside it, the same tally over the permitted set. The card reads THIS one; the wall's
  -- membership rule below still reads the narrowed one. See `whole` for why they are two numbers.
  LEFT JOIN whole w ON w.site_id = pl.id
  LEFT JOIN site_user_state st ON st.site_id = pl.id AND st.user_id = :viewer
 WHERE (:site_id IS NULL OR pl.id = :site_id)
   -- THE SITES DIRECTLY UNDER ONE NETWORK, which is what a network's Sites tab is a page of.
   --
   -- One step and not the whole subtree, deliberately. This answers "what is part of this", which
   -- is what the child's own record says of itself, and a tab that listed grandchildren beside
   -- children would say a label belongs to two networks at once. The counts on those cards already
   -- roll the whole subtree up, so nothing is hidden by listing one level: a network two deep shows
   -- as a card whose number includes what is under IT.
   --
   -- Narrowing rows and not files: it never touches the filter seam, so it composes with a wall
   -- that is also narrowed by a person or a tag rather than competing with one.
   AND (:parent_id IS NULL OR pl.parent_id = :parent_id)
   -- The name OR one of its other names, exactly as a person and a tag are matched: a site filed
   -- as one spelling is found under another, as its FILES are, which is what the site's own record
   -- promises ("any of them finds it").
   AND (:prefix = '' OR (CASE WHEN {{SHOWN}} THEN (pl.name LIKE :like ESCAPE '\\'
        OR EXISTS (SELECT 1 FROM site_aliases pa
                    WHERE pa.site_id = pl.id AND pa.alias LIKE :like ESCAPE '\\')) ELSE 0 END))
   -- CONCEALED BY ITSELF **OR BY ANY SITE ABOVE IT**. Stopping at the row's own flag would take a
   -- hidden network's labels' FILES off every screen and leave the labels themselves on this wall:
   -- named, counted, and with pages that still answer 200: the disclosure concealment exists to
   -- prevent.
   --
   -- `SITE_CONCEALED` and not a join to `lineage` above, though that CTE is right here and would
   -- save a walk: the same rule has to hold in four other statements that have no such CTE, and
   -- one question written out more than once is how two copies come to disagree. The walk is
   -- over a table holding sites in the hundreds and the planner evaluates the
   -- recursive arm once per statement (see `kernel/access/sites.py`).
   AND (:reveal_named = 1 OR NOT {{SITE_CONCEALED_SITE}})
   AND (:list_empty = 1 OR COALESCE(c.asset_count, 0) > 0)
   -- A LOCKED TILE matches no typed word: a box that finds a padlock has said the name. See
   -- `_LOCKED_TILE`, where the rule is written out.
   AND NOT ({{LOCKED}} AND :prefix <> '')
   AND 1 = 1
   -- The wall's own ROW narrowing is spliced in here. See `_row_narrowed`.
 -- The wall's chosen order, written out here rather than shared with the people query above.
 --
 -- These are the most security-critical statements in the project and they are written out on
 -- purpose; a shared fragment is a fragment that can be assembled wrongly, and the test that puts
 -- every way of concealing a file to all of them is what catches a copy that stops agreeing. The
 -- reasoning for the CASE expressions is the same as it is there, and so is the fall-through: any
 -- key this does not know leaves the ordinary most-seen-then-name order exactly as it was.
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
 ORDER BY COALESCE(st.pinned, 0) DESC,
          CASE :entity_sort WHEN 'favorite' THEN COALESCE(st.favorite, 0) END DESC,
          CASE :entity_sort WHEN 'rating' THEN COALESCE(st.rating, 0) END DESC,
          CASE :entity_sort WHEN 'name_az' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(pl.name_sort, pl.name) END END ASC NULLS LAST,
          CASE :entity_sort WHEN 'name_za' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(pl.name_sort, pl.name) END END DESC,
          CASE :entity_sort WHEN 'newest' THEN pl.id END DESC,
          CASE :entity_sort WHEN 'edited' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE pl.edited_at END END DESC NULLS LAST,
          CASE :entity_sort WHEN 'edited' THEN pl.id END DESC,
          CASE :entity_sort WHEN 'oldest' THEN pl.id END ASC,
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
          COALESCE(c.asset_count, 0) DESC, CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(pl.name_sort, pl.name) END ASC NULLS LAST, pl.id ASC
 LIMIT :limit OFFSET :offset
""",
    SITE_REACH=SITE_REACH,
    SITE_CONCEALED_SITE=SITE_CONCEALED.format(site="pl.id"),
    SITE_ADDRESS=site_address("pl"),
    LOCKED=locked_tile("site", "pl"),
    SHOWN=shown("site", "pl"),
)
_SITES_HEAD, _SITES_ACCESS = _cut(_VISIBLE_SITES, "_VISIBLE_SITES")
#: The sites wall in pieces.
_SITES = _wall(_VISIBLE_SITES, "_VISIBLE_SITES")
SITES_POSITION = _position(_SITES)
_SITES_STORED = _with_stored_counts(_VISIBLE_SITES, "_VISIBLE_SITES")

#: A handful of Sites by id: the sites wall unfiltered. A Site's files are counted in
#: `viewer_entity_counts`, so a by-id read is one range per Site asked about, exactly as
#: `TAGS_BY_ID` is, rather than counting every Site live before the by-id condition is reached,
#: which a screen naming twenty-five Sites would pay twenty-five times.
SITES_BY_ID = _one_seam(
    _SITES_STORED,
    "_SITES_STORED",
    "WHERE (:site_id IS NULL OR pl.id = :site_id)",
    "WHERE pl.id IN (SELECT value FROM json_each(:ids))",
)


def sites_query(where: str, rows: str = _NOTHING) -> str:
    """Sites this viewer may know about, counted over only the files a filter reaches.

    `rows` filters the sites themselves: the network they are part of, their own tags. Unfiltered
    by a file filter, the counts are read off the stored per-Site figures (see `_COUNTED_KIND`);
    filtered, they are counted live, because no stored number can know a filter on files.
    """
    if not _narrowed(where):
        return _row_narrowed(_SITES_STORED, rows)
    return _row_narrowed(_filtered(_SITES_HEAD, _SITES_ACCESS, where), rows)


def sites_position(where: str = _NOTHING, rows: str = _NOTHING) -> str:
    """Where one site sits in that same wall, filtered the same two ways.

    Spliced unconditionally, and still the live form unfiltered although `sites_query` reads the
    stored counts then: the two forms answer the same number when nothing filters the wall, so the
    ordering (which is all a position reads) is the same, and `tags_position` makes the same
    choice for the same reason. See it for the rest.
    """
    head, rest = _cut(SITES_POSITION, "SITES_POSITION")
    return _row_narrowed(_filtered(head, rest, where), rows)
