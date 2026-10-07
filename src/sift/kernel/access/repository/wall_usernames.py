# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wall of usernames: the usernames a viewer may know about, with their counts."""

from __future__ import annotations

from sift.kernel.access.repository.walls import (
    _NOTHING,
    HIDDEN_SITES,
    _cut,
    _filtered,
    _narrowed,
    _position,
    _wall,
    _with_stored_counts,
    ordered,
    plain_total,
    shown,
)
from sift.kernel.access.sites import SITE_CONCEALED
from sift.kernel.sql_splice import splice

#: Sites this viewer may know about, each with its visible asset count. Another copy of the
#: resolver; see the note above `_VISIBLE_PEOPLE`.
#:
#: It exists for the same reason the other suggesters do rather than as a convenience: `sites:`
#: is a search token, and a token whose values were listed straight off the table would name every
#: site in the library to a guest who has been shown nothing from any of them. A site is a
#: grantable object (a share can name one), so which sites somebody may know about is a
#: permission question with an answer, and this is where that answer is worked out.
#: One username on one site, scoped exactly as every other wall is.
#:
#: Written out in full rather than sharing the prelude above it, and that is the same deliberate
#: duplication every statement in this file carries (see the note over `_VISIBLE_ASSETS_HEAD`).
#: These are the most security-critical statements in the project; a query assembled from fragments
#: is a query that can be assembled wrongly, and `VAULT_CASES` in test_access.py puts every way of
#: concealing a file to every one of them, so a copy that stops agreeing fails the build.
_VISIBLE_USERNAMES = splice(
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
-- One row per username, counted over the files this viewer may see.
--
-- COUNT(*) rather than COUNT(DISTINCT) here, and that is exact rather than approximate: the
-- grouping is by USERNAME and `asset_usernames` has `(asset_id, username_id)` as its primary key,
-- so one file can appear against one username exactly once. The site query below groups a level up
-- and has to say DISTINCT for precisely the reason this does not.
counted(username_id, asset_count, size_bytes, duration_ms) AS (
  SELECT aa.username_id, COUNT(*),
         COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.size_bytes END), 0),
         COALESCE(SUM(CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.duration_ms END), 0)
    FROM asset_usernames aa
    CROSS JOIN viewer_assets v ON v.asset_id = aa.asset_id AND v.user_id = :viewer
    JOIN assets a ON a.id = aa.asset_id
   WHERE (:reveal = 1 OR v.concealed = 0)
     AND 1 = 1
     -- The wall's own narrowing is spliced in here. See `_filtered`.
   GROUP BY aa.username_id
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
whole(username_id, asset_count, size_bytes, duration_ms) AS (
  SELECT c.object_id, c.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE c.concealed END,
         c.permitted_bytes - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_bytes END,
         c.permitted_ms - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_ms END
    FROM viewer_entity_counts c
   WHERE c.user_id = :viewer AND c.kind = 'username'
)
-- A username with nothing in it this viewer may see is not there for them, which is how a
-- container is concealed by the grants alone. The rule is the site's, applied a level down.
--
-- A username has no vault flag and no heart of its own, and that is deliberate rather than
-- unfinished. Concealing is done to the SITE or to the PERSON (the two things somebody thinks in),
-- and a third switch on the username between them would be a third place to look when something
-- is missing. So the site's vault reaches every username under it, through `logical_vault` above,
-- and there is nothing here to set.
SELECT ac.id, ac.name, ac.display_name, ac.url, ac.site_id,
       -- WHO THIS USERNAME BELONGS TO, and nothing at all while their concealment is in force.
       --
       -- The vault's rule is one sentence: locked, a concealed person is neither counted nor listed
       -- nor named; unlocked, everywhere. Without it here, the name would come through whenever the
       -- username still had a countable file.
       --
       -- The USERNAME stays either way. A username is a row
       -- on a Site and is not the person: it has no vault flag of its own, its files are its own,
       -- and taking the row away would hide a site's username because of somebody attached to it.
       -- What is concealed is the LINK and the name at the end of it.
       --
       -- `:reveal_named` and not `:reveal`, the same flag the People, tags, collections, Photo Sets
       -- and sites walls read: placeholder mode reveals a locked TILE, which has no content in
       -- it, and a name is nothing but content.
       CASE WHEN (:reveal_named = 1 OR COALESCE(pst.hidden, 0) = 0) AND {{PERSON_SHOWN}}
            THEN ac.person_id END AS person_id,
       pl.name AS site_name,
       CASE WHEN (:reveal_named = 1 OR COALESCE(pst.hidden, 0) = 0) AND {{PERSON_SHOWN}}
            THEN pe.name END AS person_name,
       COALESCE(w.asset_count, 0) AS asset_count,
       -- The size of exactly the files that number counts, off the same tally.
       COALESCE(w.size_bytes, 0) AS size_bytes,
       -- NO COVER, since v58: a username has no page and no picture of its own. See
       -- `_DROP_USERNAME_COVER` in the schema.
       -- HOW MANY PEOPLE ANSWER TO THIS SPELLING, which is the whole of what a waiting username is
       -- asking. Zero means nobody here goes by it; more than one means the rule that files a
       -- download deliberately refused to guess (`attribute_to_person`: exactly one match, or
       -- nothing happens). Those are different work and only the second is a judgement, and
       -- without this column the screen could not tell them apart.
       --
       -- Only an admin works that queue, and the count takes in people a guest may not be shown.
       -- `:name_candidates` is 0 for every other reader, so SQLite skips the subquery.
       --
       -- It is the SAME rule `people_named` applies, written the same way. Two spellings of "who
       -- does this word name" is two answers, and the one that drifts is the one nobody is looking
       -- at, so if that rule ever changes, this changes with it.
       CASE WHEN :name_candidates = 1 AND :is_admin = 1 THEN (
              SELECT COUNT(*) FROM (
                SELECT id AS person_id FROM people WHERE name = ac.name COLLATE NOCASE
                UNION
                SELECT person_id FROM people_aliases WHERE alias = ac.name COLLATE NOCASE
              )
            ) ELSE 0 END AS name_candidates,
       -- The scoped total, over the whole result rather than over the page. See the tag query.
       COUNT(*) OVER () AS total_count
  FROM usernames ac
  LEFT JOIN counted   c  ON c.username_id = ac.id
  -- ...and beside it, the same tally over the permitted set. The card reads THIS one; the wall's
  -- membership rule below still reads the narrowed one. See `whole` for why they are two numbers.
  LEFT JOIN whole     w  ON w.username_id = ac.id
  LEFT JOIN sites pl ON pl.id = ac.site_id
  LEFT JOIN people    pe ON pe.id = ac.person_id
  -- What this viewer has decided about the person at the end of the username. LEFT, because most
  -- people have no row here at all and a concealment nobody made is `COALESCE(..., 0)`.
  LEFT JOIN person_user_state pst ON pst.person_id = ac.person_id AND pst.user_id = :viewer
 WHERE (:username_id IS NULL OR ac.id = :username_id)
   AND (:site_id IS NULL OR ac.site_id = :site_id)
   AND (:person_id IS NULL OR ac.person_id = :person_id)
   -- Unattached only, for the queue of usernames nobody has said who they belong to. NULL is
   -- "either", so a wall that does not ask gets both.
   --
   -- A BLANK username is never waiting, and that is the rule rather than a tidy-up. A download that
   -- could not read who posted something files it under an empty username, so a library has
   -- one of those per site holding everything anonymous. Nobody
   -- can answer "who is ''", so a queue holding them is a queue with permanent rows in it, and a
   -- queue that can never be emptied stops being opened. They are still listed, still reachable and
   -- still have a page; they are simply not a question.
   AND (:unattached IS NULL
        OR ((ac.person_id IS NULL) = (:unattached = 1)
            AND (:unattached = 0 OR trim(ac.name) <> '')))
   -- AND A USERNAME WITH NO FILES UNDER IT IS NEVER WAITING, whoever is asking. The queue is "files
   -- posted under a username nobody is", and because a stash-box's links are usernames (a box
   -- lists a person's page on a site, and the page's name is a username with nothing under it) a
   -- username can exist with no files at all. Asked "who is this", such a row offers nothing to
   -- recognise and nothing that would move if it were answered: it would read "0 items on
   -- TikTok" on the wall.
   --
   -- It has to be said HERE and not left to `:list_empty`, because that rule lists empty rows for
   -- an admin on purpose (the ordinary wall shows an admin everything), and an admin is exactly who
   -- works this queue. `counted` rather than `whole`: it is the narrowed tally and never larger
   -- than the whole one, so a row this keeps can never draw a card that says 0. The board's count
   -- and the panel's wall both read this statement, so the two agree by construction.
   AND (:unattached IS NULL OR :unattached = 0 OR COALESCE(c.asset_count, 0) > 0)
   AND (:prefix = '' OR (CASE WHEN {{SHOWN}} THEN (ac.name LIKE :like ESCAPE '\\'
        OR ac.display_name LIKE :like ESCAPE '\\') ELSE 0 END))
   -- A site this viewer has hidden takes its usernames with it, the same way it takes its files,
   -- and so does a NETWORK above that site, so a username under a label under a hidden network
   -- does not stay on the wall with its name and its count on it.
   AND (:reveal_named = 1 OR NOT {{SITE_CONCEALED_USERNAME}})
   AND (:list_empty = 1 OR COALESCE(c.asset_count, 0) > 0)
 -- The wall's chosen order, then most-seen first, then the username. No heart and no stars here.
 ORDER BY CASE :entity_sort WHEN 'name_az' THEN COALESCE(ac.name_sort, ac.name) END ASC,
          CASE :entity_sort WHEN 'name_za' THEN COALESCE(ac.name_sort, ac.name) END DESC,
          CASE :entity_sort WHEN 'newest' THEN ac.id END DESC,
          CASE :entity_sort WHEN 'edited' THEN ac.edited_at END DESC NULLS LAST,
          CASE :entity_sort WHEN 'edited' THEN ac.id END DESC,
          CASE :entity_sort WHEN 'oldest' THEN ac.id END ASC,
          CASE :entity_sort WHEN 'largest' THEN COALESCE(w.asset_count, 0) END DESC,
          CASE :entity_sort WHEN 'smallest' THEN COALESCE(w.asset_count, 0) END ASC,
          CASE :entity_sort WHEN 'largest_total' THEN COALESCE(w.size_bytes, 0) END DESC,
          CASE :entity_sort WHEN 'smallest_total' THEN COALESCE(w.size_bytes, 0) END ASC,
          CASE :entity_sort WHEN 'longest_total' THEN NULLIF(w.duration_ms, 0) END DESC NULLS LAST,
          CASE :entity_sort WHEN 'shortest_total' THEN NULLIF(w.duration_ms, 0) END ASC NULLS LAST,
          COALESCE(c.asset_count, 0) DESC, COALESCE(ac.name_sort, ac.name) ASC, ac.id ASC
 LIMIT :limit OFFSET :offset
""",
    SITE_CONCEALED_USERNAME=SITE_CONCEALED.format(site="ac.site_id"),
    SHOWN=shown("username", "ac"),
    PERSON_SHOWN=shown("person", "pe"),
)
_USERNAMES_HEAD, _USERNAMES_ACCESS = _cut(_VISIBLE_USERNAMES, "_VISIBLE_USERNAMES")
#: The usernames wall in pieces: the list Organize's Usernames Waiting pages through, which is the
#: same statement with `:unattached` bound. No ROW filter seam, like the loops wall: a username
#: has no facets of its own, so `usernames_position` splices at the file filter alone.
_USERNAMES = _wall(_VISIBLE_USERNAMES, "_VISIBLE_USERNAMES")
USERNAMES_POSITION = _position(_USERNAMES)
_USERNAMES_STORED = _with_stored_counts(_VISIBLE_USERNAMES, "_VISIBLE_USERNAMES")
#: The plain wall: its total off the stored totals (`plain_total`).
_USERNAMES_PLAIN = ordered(
    plain_total(
        _USERNAMES_STORED,
        "_USERNAMES_STORED",
        "username",
        "usernames",
        "SELECT un.id FROM usernames un WHERE un.site_id IN (" + HIDDEN_SITES + ")",  # noqa: S608
        "site_user_state",
    ),
    "_USERNAMES_PLAIN",
)


def usernames_query(where: str, *, plain: str | None = None) -> str:
    """Usernames this viewer may know about, counted over only the files a filter reaches."""
    if not _narrowed(where):
        return _USERNAMES_PLAIN[plain] if plain else _USERNAMES_STORED
    return _filtered(_USERNAMES_HEAD, _USERNAMES_ACCESS, where)


def usernames_position(where: str = _NOTHING) -> str:
    """Where one username sits in that same wall, filtered the same way.

    Cut out of the listing by `_position`, never written again: a position taken in one order and a
    page taken in another opens somebody on a page that does not hold the row they left at. The live
    count form, as `sites_position` explains: unfiltered it orders exactly as the stored form
    does. The queue of waiting usernames needs it: its address names the first row
    of the page it was left on (`from`), and a join takes exactly that row off it.
    """
    statement = USERNAMES_POSITION
    if _narrowed(where):
        head, rest = _cut(statement, "USERNAMES_POSITION")
        statement = _filtered(head, rest, where)
    return statement
