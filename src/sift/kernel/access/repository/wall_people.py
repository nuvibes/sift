# SPDX-License-Identifier: AGPL-3.0-or-later
"""The People wall: the people a viewer may know about, each counted over the files they may see."""

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
from sift.kernel.access.sites import SITE_CONCEALED, SITE_REACH
from sift.kernel.sql_splice import splice

#: People this viewer may know about, each with the number of assets they can actually see of
#: them. The resolver is written out here rather than shared, for the reason given in full above
#: `_VISIBLE_ASSETS_HEAD` in assets.py: a query assembled from fragments is a query that can be
#: assembled wrongly. `VAULT_CASES` in test_access.py puts every way of concealing a file to every
#: copy, so a copy that stops agreeing fails the build rather than quietly differing by a number.
#: WHETHER ONE USERNAME PUTS ITS PERSON ON ITS SITE, FOR THIS VIEWER.
#:
#: Not only the people who share a FILE with a Site: a Site a stash-box filled from people's profile
#: links has usernames and no files at all, and its page would list none of them, while a username
#: has no page of its own to be found on instead. So a person with a username on a Site is one of
#: that Site's people too, on its People tab and in the People cell of its card; this is the
#: condition, in ONE place, and both statements splice it in with `{u}` filled by the username's
#: alias.
#:
#: It is the USERNAMES wall's own rule for listing that username, applied a step further to the
#: person behind it, so the Site page cannot name somebody through this that its own list of
#: usernames would not:
#:
#: - A Site this viewer concealed (itself or any network above it) takes its usernames with it,
#:   so it takes this with it. The same `SITE_CONCEALED` fragment that wall reads.
#: - An admin is shown every username on a Site, files or none, which is what that wall does for a
#:   Site's usernames. Anybody else only one carrying a file they may see (the stored per-username
#:   count, with the vault shut unless `:reveal`), AND only a person they are shown at all: a
#:   person with no file they may see is not on their People wall, and a card leading to a page that
#:   answers "not found" is a disclosure and a dead end at once.
#: - The PERSON's own concealment is not here because both readers already apply it to every row:
#:   the wall's `st.hidden` test and the card's `counted` CTE.
_A_USERNAME_SHOWS_ITS_PERSON = splice(
    "((:reveal_named = 1 OR NOT {{SITE_CONCEALED}})"
    " AND (:is_admin = 1 OR ("
    "EXISTS (SELECT 1 FROM viewer_entity_counts ue"
    " WHERE ue.user_id = :viewer AND ue.kind = 'username' AND ue.object_id = {u}.id"
    " AND ue.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE ue.concealed END > 0)"
    " AND EXISTS (SELECT 1 FROM viewer_entity_counts pe"
    " WHERE pe.user_id = :viewer AND pe.kind = 'person' AND pe.object_id = {u}.person_id"
    " AND pe.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE pe.concealed END > 0))))",
    SITE_CONCEALED=SITE_CONCEALED.format(site="{u}.site_id"),
)

_VISIBLE_PEOPLE = splice(
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
counted(person_id, asset_count, size_bytes, duration_ms) AS (
  SELECT ap.person_id, COUNT(*),
         COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.size_bytes END), 0),
         COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.duration_ms END), 0)
    FROM asset_people ap
    CROSS JOIN viewer_assets v ON v.asset_id = ap.asset_id AND v.user_id = :viewer
    JOIN assets a ON a.id = ap.asset_id
   WHERE (:reveal = 1 OR v.concealed = 0)
     AND 1 = 1
     -- The wall's own narrowing is spliced in here. See `_filtered`.
   GROUP BY ap.person_id
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
--
-- !! WHICH OF THE TWO A CARD PRINTS IS THE CALLER'S TO SAY, and it is `:count_narrowed` that says
-- it. The paragraph above is the right default, but a press on a card carries the page it was
-- pressed from: reached through somebody else, a card opens the two of them together, so the
-- number on it has to be the size of THAT wall or it describes a set the press cannot reach. The rule is one sentence (a card's number is the
-- count of the wall its press opens), and only the caller knows what the press carries, which is
-- why it is bound rather than decided here. See the projection below.
whole(person_id, asset_count, size_bytes, duration_ms) AS (
  SELECT c.object_id, c.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE c.concealed END,
         c.permitted_bytes - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_bytes END,
         c.permitted_ms - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_ms END
    FROM viewer_entity_counts c
   WHERE c.user_id = :viewer AND c.kind = 'person'
)
-- `:reveal_named` is a separate flag from `:reveal`, and the difference is the whole point of it.
-- `:reveal` decides whether concealed ASSETS are counted, and placeholder mode sets it: a locked
-- tile in a grid shows a lock and no content. A row here is nothing BUT content (it is a name,
-- which is the most identifying column in the database), so the person's own row comes back only
-- when the vault is genuinely unlocked. Placeholder mode gets a gap, not a blurred name.
-- The heart and the stars are THIS viewer's, joined on `:viewer`: a rating is somebody's opinion
-- and not a property of the person rated, so two users sharing an install see their own. No row
-- is the ordinary case and means unhearted and unrated, which is why it is a LEFT JOIN and a
-- COALESCE rather than a row written when somebody is created.
-- The card's number, and which of the two tallies it is comes off `:count_narrowed`.
-- One expression rather than a second statement: the wall, its membership rule and its count stay
-- one question, and the two cannot come to describe different populations. Nought asks for the
-- whole (every file of this person the viewer may see, which is what the People wall and this
-- person's own page mean by the number), and one asks for this wall's, which is what a card whose
-- press opens the narrowed wall means by it. Un-narrowed the two are the same number, so a caller
-- that asks for the narrowed count of an unfiltered wall gets the honest answer either way.
-- A locked tile has no name; see `_LOCKED_TILE`. `locked` is what the card draws it by.
SELECT p.id, CASE WHEN {{LOCKED}} THEN '' ELSE p.name END AS name,
       {{LOCKED}} AS locked, COALESCE(st.hidden, 0) AS vault,
       -- WHETHER THIS ROW MUST NEVER BE SENT OUTSIDE THE MACHINE.
       --
       -- On the WALL and not only on the one route that answers about a single row, and that is the
       -- whole reason it is here. A menu asks `EnrichmentState` as it opens, which is one request
       -- for the one row somebody right-clicked; a MARK is drawn on every card of sixty without
       -- anybody pressing anything, and sixty requests to draw sixty marks is not a surface that
       -- can exist. So the menu keeps asking and the mark reads this.
       --
       -- The row's own flag, with no COALESCE and no CASE: it is NOT NULL on the table, and it says
       -- nothing about the row that would have to be withheld: a refusal to send something out is
       -- a decision about this install, not about the thing.
       p.keep_local AS keep_local,
       -- And "Don't swap", the refusal's other mark: swap mode draws both on the card.
       p.keep_from_swaps AS keep_from_swaps,
       COALESCE(CASE WHEN :count_narrowed = 1 THEN c.asset_count ELSE w.asset_count END, 0)
         AS asset_count,
       -- The size of exactly the files that number counts, off the same tally.
       COALESCE(CASE WHEN :count_narrowed = 1 THEN c.size_bytes ELSE w.size_bytes END, 0)
         AS size_bytes,
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = p.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN p.cover_asset_id END AS cover_asset_id,
       -- An UPLOADED cover, which carries no visibility question because it is not a file in the
       -- library: it is a picture somebody put on this row, and anybody who may be shown the row
       -- may see it. That is why it has no CASE above it while the line beside it does.
       CASE WHEN {{LOCKED}} THEN NULL ELSE p.cover_upload_id END AS cover_upload_id,
       -- HOW the picture sits in its frame, read raw: `views._frame` honours it only while
       -- it names the picture the gated columns here still name, so a withheld file takes its
       -- frame with it and no CASE is needed.
       p.cover_frame AS cover_frame,
       -- WHICH MOMENT of that file, withheld with the file. The cover's address names it, and an
       -- address that names its picture is the only one the server will let the browser keep
       -- (`names_its_cover`); without it a chosen frame would be re-checked on every visit.
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = p.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN p.cover_at_ms END AS cover_at_ms,
       -- The face, gated on the FILE it was found in rather than on itself. A face is a fragment
       -- of a frame, so somebody who may not open the file may not see a piece of one, and that
       -- rule is already written, directly above, against the asset. Testing the same column twice
       -- is what makes the two answers incapable of disagreeing.
       CASE WHEN NOT {{LOCKED}} AND EXISTS (SELECT 1 FROM viewer_assets cv WHERE cv.asset_id = p.cover_asset_id AND cv.user_id = :viewer AND (:reveal_named = 1 OR cv.concealed = 0))
            THEN p.cover_track_id END AS cover_track_id,
       -- Who MAKES the edits, as against who appears in them. Read on the WALL and not only on the
       -- page, because the mark is drawn on the card: a card that had to ask a second route for it
       -- would be sixty more requests a page, or a mark that appears a moment late.
       --
       -- No COALESCE and no CASE, unlike the columns around it. It is the row's own flag, which
       -- refuses NULL, and it says nothing about anybody that the name beside it does not already.
       p.pmv_creator AS pmv_creator,
       COALESCE(st.favorite, 0) AS favorite,
       -- Kept at the top of this wall by this user. Read beside the heart because it is
       -- the same kind of thing: an opinion held per user, sparse, absent meaning no.
       COALESCE(st.pinned, 0) AS pinned,
       st.rating AS rating,
       -- WHICH spelling the term matched, when it was not the name on the row.
       --
       -- Somebody typing a username and being shown a name they did not type has no way to tell why
       -- that row is there: it reads as the box answering a different question. This is what the
       -- row says beside the name, so "esmewrenfield" offers "Neve Arbor, esmewrenfield" and the answer
       -- explains itself.
       --
       -- Skipped entirely when nothing was typed, and when the NAME itself matched. The first
       -- keeps a plain wall of hundreds of people from running two correlated subqueries per row
       -- to produce NULL every time; the second is the rule that the name wins, written once here
       -- rather than re-derived by whoever reads the row.
       CASE WHEN :prefix = '' OR p.name LIKE :like ESCAPE '\\' THEN NULL ELSE
         (SELECT al.alias FROM people_aliases al
           WHERE al.person_id = p.id AND al.alias LIKE :like ESCAPE '\\'
           ORDER BY al.alias COLLATE NOCASE LIMIT 1) END AS matched_alias,
       CASE WHEN :prefix = '' OR p.name LIKE :like ESCAPE '\\' THEN NULL ELSE
         (SELECT ac.name FROM usernames ac
           WHERE ac.person_id = p.id AND ac.name LIKE :like ESCAPE '\\'
           ORDER BY ac.name COLLATE NOCASE LIMIT 1) END AS matched_username,
       -- How many this viewer may see in total, counted over the whole scoped result rather than
       -- over the page taken out of it. The same window the asset listing uses, and for the same
       -- reason: a second query for the count is a second copy of every scoping rule above, and
       -- the two would be free to disagree about who is visible.
       COUNT(*) OVER () AS total_count
  FROM people p
  LEFT JOIN counted c ON c.person_id = p.id
  -- ...and beside it, the same tally over the permitted set. The wall's membership rule below reads
  -- the narrowed one always; the card reads whichever `:count_narrowed` names. Both are joined on
  -- every wall because the choice is made per request. See `whole` for why they are two numbers.
  LEFT JOIN whole w ON w.person_id = p.id
  LEFT JOIN person_user_state st ON st.person_id = p.id AND st.user_id = :viewer
 -- Name, alias, or a username pointed at them. All three, because a person IS
 -- findable by all three, as `resolve_alias_targets` says. Matching only the first would leave a
 -- search box offering nothing for a username somebody typed off one of Sift's own screens: the
 -- files findable, the person not.
 WHERE (:prefix = '' OR ({{SHOWN}} AND (p.name LIKE :like ESCAPE '\\'
        OR EXISTS (SELECT 1 FROM people_aliases al
                    WHERE al.person_id = p.id AND al.alias LIKE :like ESCAPE '\\')
        OR EXISTS (SELECT 1 FROM usernames ac
                    WHERE ac.person_id = p.id AND ac.name LIKE :like ESCAPE '\\'))))
   AND (:person_id IS NULL OR p.id = :person_id)
   -- A SET of people, for a caller holding a list of ids that needs a name for each of them.
   --
   -- Beside the single form rather than replacing it, and both read the same way: NULL means no
   -- narrowing. It exists because narrowing here saves nothing: this lands after the counts, so
   -- the statement costs the same whether it answers for one person or for all of them. A
   -- screen drawing faces belonging to fourteen people asks one question
   -- rather than fourteen.
   --
   -- `json_each` over a bound array, the same way `in_scope` reads the folders a filter names: the
   -- statement stays one constant string, and the caller's own list is what bounds it.
   AND (:person_ids IS NULL OR p.id IN (SELECT value FROM json_each(:person_ids)))
   -- Narrowed to one site, for the site's own page. Reached through what this viewer may SEE
   -- rather than over the whole table: a person whose only file from this site is one they were
   -- never shown is not on this site as far as they are concerned, and listing them would say a
   -- file exists that every other route refuses to admit to.
   AND (:site_id IS NULL OR EXISTS (
         SELECT 1 FROM usernames ac2
           JOIN asset_usernames aa ON aa.username_id = ac2.id
           JOIN asset_people ap ON ap.asset_id = aa.asset_id AND ap.person_id = p.id
           CROSS JOIN viewer_assets v ON v.asset_id = ap.asset_id AND v.user_id = :viewer
          WHERE ac2.site_id = :site_id AND (:reveal = 1 OR v.concealed = 0)
       ))
   AND (:reveal_named = 1 OR COALESCE(st.hidden, 0) = 0)
   -- ...or, on a wall narrowed to one Site and nothing else, a USERNAME there. `:username_sites`
   -- is that Site as a one-element array, bound by the repository off the file filter itself (see
   -- `username_sites_of`), and NULL on every other wall, where this adds nobody. The Site reaches
   -- the labels under it as its files do, and each username must pass the rule the usernames wall
   -- lists it by: see `_A_USERNAME_SHOWS_ITS_PERSON`. An OR on the membership test and nowhere
   -- else: every other conjunct here still holds, so a concealed person stays off this wall.
   AND (:list_empty = 1 OR COALESCE(c.asset_count, 0) > 0
        OR (:username_sites IS NOT NULL AND EXISTS (
              SELECT 1 FROM usernames un
               WHERE un.person_id = p.id
                 AND un.site_id IN (
                       SELECT reach.site_id FROM ({{SITE_REACH}}) reach
                        WHERE reach.ancestor_id IN (SELECT value FROM json_each(:username_sites)))
                 AND {{A_USERNAME_SHOWS_ITS_PERSON}})))
   -- A LOCKED TILE matches no typed word: a box that finds a padlock has said the name. See
   -- `_LOCKED_TILE`, where the rule is written out.
   AND NOT ({{LOCKED}} AND :prefix <> '')
   AND 1 = 1
   -- The wall's own ROW narrowing is spliced in here. See `_row_narrowed`.
 -- The wall's chosen order, in front of the ordinary one rather than in place of it.
 --
 -- `:entity_sort` is a validated key, never text a caller wrote: `ENTITY_SORT_KEYS` is what the
 -- route checks against. Each CASE yields NULL for every row under any other key, so those rows
 -- all tie and the sort falls straight through to most-seen-then-name, which is why asking for
 -- an order this query does not know shows the library the ordinary way rather than an arbitrary
 -- one, and why the suggester, the assign box and search are untouched by it.
 --
 -- The heart and the stars being ordered here are THIS viewer's, joined on `:viewer` above. Two
 -- users sharing an install order their own walls, which is the only reading that makes sense
 -- for an opinion.
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
          CASE :entity_sort WHEN 'name_az' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(p.name_sort, p.name) END END ASC NULLS LAST,
          CASE :entity_sort WHEN 'name_za' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(p.name_sort, p.name) END END DESC,
          CASE :entity_sort WHEN 'newest' THEN p.id END DESC,
          -- WHEN IT WAS LAST EDITED, most recent first (`kernel.access.edited`), and a row never
          -- edited behind every row that was, newest made first: the id is creation order.
          CASE :entity_sort WHEN 'edited' THEN CASE WHEN {{LOCKED}} THEN NULL ELSE p.edited_at END END DESC NULLS LAST,
          CASE :entity_sort WHEN 'edited' THEN p.id END DESC,
          CASE :entity_sort WHEN 'oldest' THEN p.id END ASC,
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
          COALESCE(c.asset_count, 0) DESC, CASE WHEN {{LOCKED}} THEN NULL ELSE COALESCE(p.name_sort, p.name) END ASC NULLS LAST, p.id ASC
 LIMIT :limit OFFSET :offset
""",
    SITE_REACH=SITE_REACH,
    A_USERNAME_SHOWS_ITS_PERSON=_A_USERNAME_SHOWS_ITS_PERSON.format(u="un"),
    LOCKED=locked_tile("person", "p"),
    SHOWN=shown("person", "p"),
)


# --- Where somebody sits in that list ------------------------------------------------------------
#
# The People wall carries its position in the address, the same way the media grid does, so a link
# opens where the sender was rather than at the top. Answering that means asking "how far down the
# scoped, ordered, filtered list is this person", which is the SAME question the statement above
# answers, with the projection changed and the page window taken off.
#
# So it is CUT OUT of that statement rather than rewritten. Every seam is checked and the pieces are
# compared with what they came from, so a reworded comment inside the statement fails at import
# rather than quietly producing a position read with a permission rule missing from it. The asset
# listing does the same thing for the same reason; this is the second user of the pattern.
#
# What must never happen is two orderings. A position computed one way and a page taken another puts
# somebody at an offset holding a different person than the one they asked to be taken to, and it
# shows up as a link landing NEAR the right place, which reads as imprecision rather than as a bug.
#: The people wall in pieces. Cut by the one helper every wall here is cut by, so the seams are not
#: written out again for this statement and a second reader (a facet count) shares them.
_PEOPLE = _wall(_VISIBLE_PEOPLE, "_VISIBLE_PEOPLE")

#: Where one person sits in the scoped, filtered, ordered list, counting from one, or no row at
#: all when this viewer may not be shown them. The ordering is the listing's own, which is the
#: whole point of building this out of the wall's seams; see `_position`.
PEOPLE_POSITION = _position(_PEOPLE)
_PEOPLE_HEAD, _PEOPLE_ACCESS = _cut(_VISIBLE_PEOPLE, "_VISIBLE_PEOPLE")
_PEOPLE_STORED = _with_stored_counts(_VISIBLE_PEOPLE, "_VISIBLE_PEOPLE")


def people_query(where: str, rows: str = _NOTHING) -> str:
    """People this viewer may know about, counted over only the files a filter reaches.

    `rows` filters the PEOPLE themselves (red-haired, born in the eighties) and composes with
    the file filter rather than competing with it. See `_row_narrowed`.
    """
    if not _narrowed(where):
        return _row_narrowed(_PEOPLE_STORED, rows)
    return _row_narrowed(_filtered(_PEOPLE_HEAD, _PEOPLE_ACCESS, where), rows)


def people_position(where: str = _NOTHING, rows: str = _NOTHING) -> str:
    """Where one person sits in that same wall, filtered the same two ways.

    `photo_sets_position` carries the whole of the reasoning: the splices are made into the POSITION
    statement rather than the position being cut out of an already-spliced listing, and it is the
    live count form because the filtered listing is. Resolved against the unfiltered wall
    whatever the page was filtered by, the People wall's `from=` link into a Site's People tab
    or a filtered wall would open at the wrong row.
    """
    statement = PEOPLE_POSITION
    if _narrowed(where):
        head, rest = _cut(statement, "PEOPLE_POSITION")
        statement = _filtered(head, rest, where)
    return _row_narrowed(statement, rows)
