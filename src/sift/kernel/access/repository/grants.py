# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who a viewer is, what has been granted to them, and where a grant came from.

Three kinds of statement. The writes (`_INSERT_GRANT`, `_DELETE_GRANT`) are the only way a
permission changes. The marks say, for one object, what every user currently makes of it: the
same ladder as the file query, run for everybody at once instead of for one person. The sources
answer "why can this be seen", which is what the sharing panel shows.

`_USER_BY_ID` is here because the viewer is the subject every grant is resolved against: this is
where the identity a read is scoped to is loaded from the database, rather than taken from a caller.
"""

from __future__ import annotations

from sift.kernel.access.sites import SITE_REACH
from sift.kernel.access.viewer import ObjectType
from sift.kernel.access.visibility import (
    ITEM_BITS,
    LOGICAL_BITS,
    PLACE_BITS_OF_COPIES,
    PLACE_REACHED_JOIN,
    PLACE_RESTRICTED,
    PLACE_SHARED,
    ladder_admits,
    ladder_restricts,
)
from sift.kernel.db import point_read
from sift.kernel.sql_splice import splice

# The viewer is rebuilt from this on every single request, and it is one seek by primary key, so
# it is declared a point read and runs on the event loop where the machine allows it.
_USER_BY_ID = point_read(
    "access.user_by_id",
    "SELECT id, role, cache_stamp FROM users WHERE id = ? AND disabled = 0",
)

# ONE ADMIN, for a pass that runs as nobody. A sweep the fingerprint chain asks for carries no
# user and still has to read the library THROUGH one, because every read in this layer is scoped
# to a viewer, and an admin's scope is the whole library, so which admin makes no difference to
# what is read. The OLDEST enabled one by id (ids are ULIDs, so the one who set the instance up,
# while they are still here), so the answer does not wander between two admins from one run to the
# next. Not a point read, unlike `_USER_BY_ID` above it: that lane is for a statement that binds
# the one subject it asks about, and this one asks the table which row it has: a question a
# handful of users answers in one seek, off the loop, once per pass.
_AN_ADMIN = "SELECT id FROM users WHERE role = 'admin' AND disabled = 0 ORDER BY id LIMIT 1"

# Two conflict targets, because a global grant collides on a different constraint than the rest.
# A grant with an object collides on the full UNIQUE. A global grant has a NULL object_id, which
# the full UNIQUE lets slip (every NULL is distinct there), so it collides instead on the partial
# unique index over the non-null columns. Either way a re-grant updates in place and RETURNING
# hands back the row that was already there, which is the idempotency the sharing UI relies on.
_INSERT_GRANT = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(object_type, object_id, subject_user_id, effect) DO UPDATE SET id = id
ON CONFLICT(object_type, subject_user_id, effect) WHERE object_id IS NULL DO UPDATE SET id = id
RETURNING *
"""

_DELETE_GRANT = """
DELETE FROM acl_grants
 WHERE object_type = ? AND object_id IS ? AND subject_user_id = ? AND effect = ?
"""

_DELETE_OBJECT_GRANTS = "DELETE FROM acl_grants WHERE object_type = ? AND object_id IS ?"

_DELETE_ITEM_GRANTS = "DELETE FROM acl_grants WHERE object_type = 'item' AND object_id IN (?*)"

_GRANTS_OF_USER = "SELECT * FROM acl_grants WHERE subject_user_id = ? ORDER BY id"

#: Every grant made on one object, whoever it was made to. The other direction from the read
#: above, and it answers a different question: not "what has this person been given" but "who is
#: this shared with", which is what the panel beside a thing has to show before anybody can
#: sensibly revoke anything.
_GRANTS_ON_OBJECT = """
SELECT * FROM acl_grants
 WHERE object_type = ? AND object_id IS ?
 ORDER BY id
"""

#: Which of these objects carry a grant at all, and of which kind. One row per object rather than
#: one per grant: a grid marking its tiles needs to know that something was said about each of
#: them, not who it was said to. The panel is where the names are.
_GRANT_MARKS = """
SELECT object_id,
       MAX(effect = 'share')    AS shared,
       MAX(effect = 'restrict') AS restricted
  FROM acl_grants
 WHERE object_type = ? AND object_id IN (?*)
 GROUP BY object_id
"""

#: Whether anything has ever been shared with anybody. The cheap answer for an install with no
#: guests on it, which is most of them (see `_asset_marks`).
_ANY_GRANT_AT_ALL = point_read("access.any_grant", "SELECT 1 FROM acl_grants LIMIT 1")

#: What each of these files' sharing actually comes to, counting everything above it.
#:
#: `_GRANT_MARKS` above answers "was a grant written on this row", which is the wrong question for a
#: tile: restrict a folder and every file in it is closed off, while every one of those tiles said
#: nothing at all. This answers the question somebody is really asking of a badge (can anybody
#: reach this, is anybody kept from it), and reports separately whether the decision was made on
#: the file itself, because that is the one you can undo where you are standing.
#:
#: It is the resolver, run for every user at once instead of for one.
#:
#: That is the whole of the difference and it is worth stating plainly: `grant_effect` groups by
#: SUBJECT as well as object rather than filtering to one viewer, every scope carries the subject
#: down with it, and the precedence ladder is applied per subject before the answers are collapsed.
#: Collapsing first would be wrong in a way that is easy to miss: one user restricted at the
#: folder and another shared at the file is not "an item grant beats a folder restrict", it is two
#: different people getting two different answers, and the badge has to say both.
#:
#: Hiding is deliberately absent. It is not a grant and says nothing about who may see a thing
#: (it takes a row off the screen of the one user who hid it), and a row concealed that way
#: never reaches a tile that could draw a badge in the first place.
#:
#: A location whose root has gone is dropped rather than counted as denied. The resolver reads a
#: missing chain as a no, because being wrong there is a leak; here the cost of being wrong is a
#: badge, and claiming somebody is restricted from a file because its library was removed would be
#: a mark nobody could explain or clear.
_EFFECTIVE_ASSET_MARKS = splice(
    """
WITH
wanted(asset_id) AS (
  SELECT value FROM json_each(:asset_ids)
),
subjects(subject) AS (
  SELECT DISTINCT subject_user_id FROM acl_grants
),
-- Every user with a grant, against every file asked about. The bits are the same three the
-- stored verdict is built from (where the copies sit, what the file belongs to, the item's own
-- grant), spliced from the one place they are written.
-- MATERIALIZED, so each bit is worked out once per user and file: flattened into the ladder below,
-- every mention of a bit ran its subquery again.
standing(asset_id, user_id, ph, lo, it) AS MATERIALIZED (
  SELECT p.asset_id, p.user_id,
         COALESCE({{PHYSICAL_BITS}}, 0),
         COALESCE({{LOGICAL_BITS}}, 0),
         COALESCE({{ITEM_BITS}}, 0)
    FROM (SELECT s.subject AS user_id, w.asset_id FROM subjects s CROSS JOIN wanted w) p
)
-- The ladder itself, spliced from the one place it is written (`visibility.LADDER_ADMITS`), and
-- whether a restrict is what decided it, never a copy that goes on drawing an old rule.
SELECT w.asset_id AS object_id,
       COALESCE(MAX({{LADDER_ADMITS}}), 0) AS shared,
       COALESCE(MAX({{LADDER_RESTRICTS}}), 0) AS restricted,
       COALESCE(MAX(st.it & 1), 0) AS shared_here,
       COALESCE(MAX((st.it & 2) / 2), 0) AS restricted_here
  FROM wanted w
  LEFT JOIN standing st ON st.asset_id = w.asset_id
 GROUP BY w.asset_id
""",
    PHYSICAL_BITS=PLACE_BITS_OF_COPIES,
    LOGICAL_BITS=LOGICAL_BITS,
    ITEM_BITS=ITEM_BITS,
    LADDER_ADMITS=ladder_admits("st"),
    LADDER_RESTRICTS=ladder_restricts("st"),
)

#: The same question as `_EFFECTIVE_ASSET_MARKS`, asked of folders.
#:
#: A folder has no item grant and belongs to no tag, so the ladder is shorter: whatever the chain
#: above it says, unless something was said about the folder itself. `_VISIBLE_FOLDERS` above is the
#: rule this mirrors (a folder is visible when it is shared and not restricted), and the two are
#: held to each other by a test that asks the resolver what the answer should be.
_EFFECTIVE_FOLDER_MARKS = splice(
    """
WITH
wanted(folder_id) AS (
  SELECT value FROM json_each(:folder_ids)
),
subjects(subject) AS (
  SELECT DISTINCT subject_user_id FROM acl_grants
),
standing(folder_id, restricted, shared) AS (
  SELECT l.folder_id, {{PLACE_RESTRICTED}}, {{PLACE_SHARED}}
    FROM (SELECT s.subject AS user_id FROM subjects s) p
   CROSS JOIN (SELECT f.id AS folder_id, f.root_id FROM folders f
                WHERE f.id IN (SELECT folder_id FROM wanted)) l{{PLACE_REACHED_JOIN}}
)
SELECT w.folder_id AS object_id,
       COALESCE(MAX(CASE WHEN st.restricted = 0 AND st.shared = 1 THEN 1 ELSE 0 END), 0) AS shared,
       COALESCE(MAX(st.restricted), 0)  AS restricted,
       COALESCE(MAX(own.effect = 'share'), 0)    AS shared_here,
       COALESCE(MAX(own.effect = 'restrict'), 0) AS restricted_here
  FROM wanted w
  LEFT JOIN standing st ON st.folder_id = w.folder_id
  LEFT JOIN acl_grants own
         ON own.object_type = 'folder' AND own.object_id = w.folder_id
 GROUP BY w.folder_id
""",
    PLACE_RESTRICTED=PLACE_RESTRICTED,
    PLACE_SHARED=PLACE_SHARED,
    PLACE_REACHED_JOIN=PLACE_REACHED_JOIN,
)

#: EVERYTHING A GRANT COULD BE MADE ON THAT REACHES A FILE, per file, over a set of files.
#:
#: The ladder `_GRANT_SOURCES_FOR_ASSET` is written with, lifted out because a second read needs the
#: same one: the reach report's entity half has to name WHICH files under a person a user can reach
#: and what let each of them through, and that is this ladder asked of a page of files and
#: collapsed. Written out twice it would be two definitions of "what reaches a file", and a
#: missing arm (`photo_set`, the network above a label) means a file handed over by that share
#: comes back to the panel with no line at all beside the user's name.
#:
#: It is the two CTEs and no more; both statements open `WITH RECURSIVE` and close it themselves.
#: The files are bound as a JSON array, so one file and a page of them are the same statement.
#:
#: DISTINCT on the root and site arms. A file with two locations under one library reaches it
#: twice and a file filed under two labels of one network reaches the network twice, and one reason
#: listed twice reads as two.
TOUCHING_ASSETS = splice(
    """
wanted(asset_id) AS (
  SELECT value FROM json_each(:asset_ids)
),
-- Every folder each file sits in, and every folder above those.
chain(asset_id, folder_id) AS (
  SELECT l.asset_id, l.folder_id FROM asset_locations l
    JOIN wanted w ON w.asset_id = l.asset_id
   WHERE l.folder_id IS NOT NULL
  UNION
  SELECT c.asset_id, f.parent_id FROM folders f JOIN chain c ON f.id = c.folder_id
   WHERE f.parent_id IS NOT NULL
),
touching(asset_id, object_type, object_id, name) AS (
  SELECT w.asset_id, 'item', w.asset_id, NULL FROM wanted w
  UNION ALL
  SELECT c.asset_id, 'folder', f.id, f.name FROM folders f JOIN chain c ON c.folder_id = f.id
  UNION ALL
  SELECT DISTINCT l.asset_id, 'root', r.id, r.name FROM asset_locations l
    JOIN wanted w ON w.asset_id = l.asset_id
    JOIN library_roots r ON r.id = l.root_id
  UNION ALL
  SELECT at2.asset_id, 'tag', t.id, t.name FROM tags t
    JOIN asset_tags at2 ON at2.tag_id = t.id
    JOIN wanted w ON w.asset_id = at2.asset_id
  UNION ALL
  SELECT ap.asset_id, 'person', p.id, p.name FROM people p
    JOIN asset_people ap ON ap.person_id = p.id
    JOIN wanted w ON w.asset_id = ap.asset_id
  UNION ALL
  SELECT ci.asset_id, 'collection', c.id, c.name FROM collections c
    JOIN collection_items ci ON ci.collection_id = c.id
    JOIN wanted w ON w.asset_id = ci.asset_id
  UNION ALL
  SELECT DISTINCT aa.asset_id, 'site', pl.id, pl.name
    FROM asset_usernames aa
    JOIN wanted w ON w.asset_id = aa.asset_id
    JOIN usernames ac ON ac.id = aa.username_id
    JOIN ({{SITE_REACH}}) reach ON reach.site_id = ac.site_id
    JOIN sites pl ON pl.id = reach.ancestor_id
  UNION ALL
  SELECT psi.asset_id, 'photo_set', ps.id, ps.name FROM photo_sets ps
    JOIN photo_set_items psi ON psi.photo_set_id = ps.id
    JOIN wanted w ON w.asset_id = psi.asset_id
  UNION ALL
  SELECT sf.asset_id, 'song', sg.id, sg.name FROM songs sg
    JOIN song_files sf ON sf.song_id = sg.id
    JOIN wanted w ON w.asset_id = sf.asset_id
  UNION ALL
  SELECT w.asset_id, 'global', NULL, NULL FROM wanted w
)
""",
    SITE_REACH=SITE_REACH,
)

#: Every grant that reaches one file, and what each was made on.
#:
#: Every arm `LOGICAL_BITS` (the statement that actually decides what a guest may see) reads is
#: here, `photo_set` and a site AND every network above it included. A missing one would bring a
#: file handed over by that share back to the sharing panel with NO line beside the user's name: the
#: panel would say "Shared" and could not say by what. The vault's half of the same question is
#: `_VAULT_SOURCES_FOR_ASSET`.
#:
#: `DISTINCT` on the site arm alone, for the reason the vault's carries it: a file filed under
#: two labels of one network reaches that network twice, and one reason listed twice reads as two.
_GRANT_SOURCES_FOR_ASSET = splice(
    """
WITH RECURSIVE
{{TOUCHING}}
SELECT g.subject_user_id, u.username, g.effect,
       t.object_type AS source_type, t.object_id AS source_id, t.name AS source_name
  FROM acl_grants g
  JOIN touching t ON t.object_type = g.object_type AND t.object_id IS g.object_id
  JOIN users u ON u.id = g.subject_user_id
 ORDER BY u.username, g.effect, t.object_type, t.name
""",
    TOUCHING=TOUCHING_ASSETS,
)

#: The six kinds of thing whose reach is explained through the files under them.
#:
#: Exactly the arms of `_FILES_UNDER_ENTITY_FOR_USER` below, and the same six `_REACH_OF_OBJECT`
#: answers with an EXISTS over a membership table. The physical three (a file, a folder, a
#: library) are left out because each already has a chain of its own that `grant_sources` gives
#: whole, and the global grant is not a thing anybody opens a report on.
ENTITY_REACH_KINDS = frozenset(
    {
        ObjectType.TAG,
        ObjectType.PERSON,
        ObjectType.COLLECTION,
        ObjectType.PHOTO_SET,
        ObjectType.SITE,
        ObjectType.SONG,
    }
)

#: How many of an entity's files are looked at to work out WHY it is reachable.
#:
#: The bound on the read, and it is a bound on work rather than on the answer: the reasons are a
#: handful whatever the number is, because a library has a handful of shares in it, and every file
#: past the first few is another walk up a folder tree that says the same thing again. Two hundred
#: is enough that a person with a dozen files is answered completely (which is nearly all of them)
#: and small enough that a person with thousands costs the same as a page of a wall.
#:
#: The answer carries `complete`, so a report can say "of the 200 looked at" rather than implying
#: it counted everything. A ceiling that is invisible is a ceiling that gets read as a total.
ENTITY_REACH_CEILING = 200

#: THE FILES UNDER ONE ENTITY THAT ONE USER CAN REACH, a page of them.
#:
#: The first half of the entity reach chain. `_REACH_OF_OBJECT` below answers whether there is ONE
#: such file, which is what puts a person on a guest's wall; this names them, so the read after it
#: can say what let each one through.
#:
#: THE MEMBERSHIP IS THE DRIVING SIDE and the verdict is the probe, which is the same shape the
#: EXISTS arms below have: a person's files are a handful and a user who sees everything has a
#: verdict row per file in the library, so driving from the verdict would walk the library to find
#: the three rows wanted. Only one arm can match (the type is compared, never interpolated), and
#: the rest cost a constant-false guard.
#:
#: ONLY THE SIX ENTITY KINDS. A file, a folder and a library each have a chain of their own that
#: `grant_sources` answers directly, so there is nothing for this to add to them.
#:
#: Ordered by the file's id rather than by anything on it. A ULID never goes backwards on a machine
#: whose clock does, so the page is the same page twice.
_FILES_UNDER_ENTITY_FOR_USER = splice(
    """
SELECT m.asset_id FROM asset_tags m
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'tag' AND m.tag_id = :object_id
UNION
SELECT m.asset_id FROM asset_people m
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'person' AND m.person_id = :object_id
UNION
SELECT m.asset_id FROM collection_items m
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'collection' AND m.collection_id = :object_id
UNION
SELECT m.asset_id FROM photo_set_items m
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'photo_set' AND m.photo_set_id = :object_id
UNION
SELECT m.asset_id FROM song_files m
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'song' AND m.song_id = :object_id
UNION
SELECT m.asset_id FROM asset_usernames m
  JOIN usernames ac ON ac.id = m.username_id
  JOIN ({{SITE_REACH}}) reach ON reach.site_id = ac.site_id
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'site' AND reach.ancestor_id = :object_id
 ORDER BY 1
 LIMIT :ceiling
""",
    SITE_REACH=SITE_REACH,
)

#: WHY THOSE FILES ARE REACHABLE, collapsed to one row per reason.
#:
#: The second half. The same ladder the sharing panel explains a single file with, asked of the
#: page above and grouped, so a report says "through the folder Holiday, which holds 3 of them"
#: rather than listing a file at a time, which is what somebody actually wants to know before
#: handing a library to a second person.
#:
#: SHARES ONLY, and that is read off the verdict rather than resolved here. Every file in the page
#: is one this user can see, so whatever restrict touches it lost: naming it would be offering
#: a reason that is not one. The same reading `/sharing/sources` makes about `decides`.
#:
#: The count is of the files in the PAGE and the caller says so. A reason under a ceiling is still
#: a true reason; a count presented as the whole would be a number that quietly means something
#: else on a big person.
_REACH_THROUGH_FILES = splice(
    """
WITH RECURSIVE
{{TOUCHING}}
SELECT t.object_type AS source_type, t.object_id AS source_id, t.name AS source_name,
       COUNT(DISTINCT t.asset_id) AS files
  FROM acl_grants g
  JOIN touching t ON t.object_type = g.object_type AND t.object_id IS g.object_id
 WHERE g.subject_user_id = :subject AND g.effect = 'share'
 GROUP BY t.object_type, t.object_id, t.name
 ORDER BY files DESC, t.object_type, t.name
""",
    TOUCHING=TOUCHING_ASSETS,
)

#: The same question for everything that is not a file.
#:
#: A tag, a person and a collection have nothing above them, so the only grants that reach one are
#: its own and the global one. A folder does have a chain, and it gets it below. A SITE DOES TOO, as
#: in the vault's half of this: a label under a shared network is reachable through that network,
#: so the network's grant is one of the grants that reach it, and asking only about the label's own
#: row would leave the panel on a label with nothing to say about why it was shared.
#:
#: The generic arm therefore excludes sites rather than standing beside a site arm that
#: would repeat the label's own row. One statement, and the type is compared rather than
#: interpolated: there is no ORM here, so a query built by formatting is the injection control
#: gone. Two of the three arms can never match at once, so the UNION costs a lookup that finds
#: nothing.
_GRANT_SOURCES_FOR_OBJECT = splice(
    """
SELECT g.subject_user_id, u.username, g.effect,
       g.object_type AS source_type, g.object_id AS source_id, NULL AS source_name
  FROM acl_grants g
  JOIN users u ON u.id = g.subject_user_id
 WHERE :object_type <> 'site'
   AND ((g.object_type = :object_type AND g.object_id IS :object_id)
     OR (g.object_type = 'global' AND g.object_id IS NULL))
UNION ALL
SELECT g.subject_user_id, u.username, g.effect,
       g.object_type, g.object_id, pl.name
  FROM ({{SITE_REACH}}) reach
  JOIN sites pl ON pl.id = reach.ancestor_id
  JOIN acl_grants g ON g.object_type = 'site' AND g.object_id = pl.id
  JOIN users u ON u.id = g.subject_user_id
 WHERE :object_type = 'site' AND reach.site_id = :object_id
UNION ALL
SELECT g.subject_user_id, u.username, g.effect, g.object_type, g.object_id, NULL
  FROM acl_grants g
  JOIN users u ON u.id = g.subject_user_id
 WHERE :object_type = 'site'
   AND g.object_type = 'global' AND g.object_id IS NULL
 ORDER BY username, effect, source_type, source_name
""",
    SITE_REACH=SITE_REACH,
)

#: And for a folder, which does have things above it.
_GRANT_SOURCES_FOR_FOLDER = """
WITH RECURSIVE
chain(folder_id) AS (
  SELECT :folder_id
  UNION
  SELECT f.parent_id FROM folders f JOIN chain c ON f.id = c.folder_id
   WHERE f.parent_id IS NOT NULL
),
touching(object_type, object_id, name) AS (
  SELECT 'folder', f.id, f.name FROM folders f JOIN chain c ON c.folder_id = f.id
  UNION ALL
  SELECT 'root', r.id, r.name FROM library_roots r
   WHERE r.id IN (SELECT root_id FROM folders WHERE id = :folder_id)
  UNION ALL
  SELECT 'global', NULL, NULL
)
SELECT g.subject_user_id, u.username, g.effect,
       t.object_type AS source_type, t.object_id AS source_id, t.name AS source_name
  FROM acl_grants g
  JOIN touching t ON t.object_type = g.object_type AND t.object_id IS g.object_id
  JOIN users u ON u.id = g.subject_user_id
 ORDER BY u.username, g.effect, t.object_type, t.name
"""

#: Everything whose vault flag is concealing one file, named.
#:
#: The same `chain` and the same shapes as the grant sources above, reading the other column. It is
#: an EXPLANATION and not the rule: what conceals a file is decided by the resolver, in the query
#: that answers whether the row comes back at all. This says which flags are set among the things
#: that reach the file, so a panel can say "the folder Holiday" instead of leaving somebody to
#: open folders until they find it.
#:
#: Every location's chain, not the one in front of you: a second copy of the same file in a vaulted
#: folder somewhere else conceals it everywhere, and that is precisely the case nobody can work out
#: by looking.
_VAULT_SOURCES_FOR_ASSET = splice(
    """
WITH RECURSIVE
chain(folder_id) AS (
  SELECT l.folder_id FROM asset_locations l
   WHERE l.asset_id = :asset_id AND l.folder_id IS NOT NULL
  UNION
  SELECT f.parent_id FROM folders f JOIN chain c ON f.id = c.folder_id
   WHERE f.parent_id IS NOT NULL
)
SELECT 'item' AS source_type, a.id AS source_id, NULL AS source_name, 1 AS here
  FROM assets a
  JOIN asset_user_state h ON h.asset_id = a.id
 WHERE a.id = :asset_id AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'folder', f.id, f.name, 0
  FROM folders f
  JOIN chain c ON c.folder_id = f.id
  JOIN folder_user_state h ON h.folder_id = f.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'root', r.id, r.name, 0
  FROM library_roots r
  JOIN root_user_state h ON h.root_id = r.id
 WHERE h.user_id = :viewer AND h.hidden = 1
   AND r.id IN (SELECT root_id FROM asset_locations WHERE asset_id = :asset_id)
UNION ALL
SELECT 'person', p.id, p.name, 0
  FROM people p
  JOIN asset_people ap ON ap.person_id = p.id AND ap.asset_id = :asset_id
  JOIN person_user_state h ON h.person_id = p.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'collection', c.id, c.name, 0
  FROM collections c
  JOIN collection_items ci ON ci.collection_id = c.id AND ci.asset_id = :asset_id
  JOIN collection_user_state h ON h.collection_id = c.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'tag', t.id, t.name, 0
  FROM tags t
  JOIN asset_tags at2 ON at2.tag_id = t.id AND at2.asset_id = :asset_id
  JOIN tag_user_state h ON h.tag_id = t.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
-- THE SITE, or any site ABOVE it: a network hides what its labels released, so the site whose
-- flag is set is often not the one the file is filed under. Asking only about the file's own site
-- would bring a file concealed by a hidden network back with no reason beside it, on the panel
-- that exists to say "why can I not see this". The walk is the same fragment the rule itself
-- reads.
--
-- DISTINCT on this arm alone: a file filed under two labels of one hidden network reaches that
-- network twice (as two usernames on one site would), and a panel listing the same reason twice
-- reads as two separate reasons.
SELECT DISTINCT 'site', pl.id, pl.name, 0
  FROM asset_usernames aa
  JOIN usernames ac ON ac.id = aa.username_id
  JOIN ({{SITE_REACH}}) reach ON reach.site_id = ac.site_id
  JOIN sites pl ON pl.id = reach.ancestor_id
  JOIN site_user_state h ON h.site_id = pl.id
 WHERE aa.asset_id = :asset_id AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'photo_set', ps.id, ps.name, 0
  FROM photo_sets ps
  JOIN photo_set_items psi ON psi.photo_set_id = ps.id AND psi.asset_id = :asset_id
  JOIN photo_set_user_state h ON h.photo_set_id = ps.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'song', sg.id, sg.name, 0
  FROM songs sg
  JOIN song_files sf ON sf.song_id = sg.id AND sf.asset_id = :asset_id
  JOIN song_user_state h ON h.song_id = sg.id
 WHERE h.user_id = :viewer AND h.hidden = 1
 ORDER BY here DESC, source_type, source_name
""",
    SITE_REACH=SITE_REACH,
)

#: And for a folder, which is concealed by itself, by a folder above it, or by its library.
_VAULT_SOURCES_FOR_FOLDER = """
WITH RECURSIVE
chain(folder_id) AS (
  SELECT :folder_id
  UNION
  SELECT f.parent_id FROM folders f JOIN chain c ON f.id = c.folder_id
   WHERE f.parent_id IS NOT NULL
)
SELECT 'folder' AS source_type, f.id AS source_id, f.name AS source_name,
       CASE WHEN f.id = :folder_id THEN 1 ELSE 0 END AS here
  FROM folders f
  JOIN chain c ON c.folder_id = f.id
  JOIN folder_user_state h ON h.folder_id = f.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'root', r.id, r.name, 0
  FROM library_roots r
  JOIN root_user_state h ON h.root_id = r.id
 WHERE h.user_id = :viewer AND h.hidden = 1
   AND r.id IN (SELECT root_id FROM folders WHERE id = :folder_id)
 ORDER BY here DESC, source_type, source_name
"""

#: A person, a collection or a library carries its own flag and nothing above it. A SITE DOES NOT:
#: a label is concealed by its own flag or by any network above it, so the panel on a label under a
#: hidden network can say why it is gone.
#:
#: One statement rather than three, and the type is compared rather than interpolated: there is no
#: ORM here, so a query built by formatting is the injection control gone. Two of the three rows can
#: never match at once, so the UNION costs an index lookup that finds nothing.
_VAULT_SOURCES_FOR_OBJECT = splice(
    """
SELECT 'person' AS source_type, p.id AS source_id, p.name AS source_name, 1 AS here
  FROM people p JOIN person_user_state h ON h.person_id = p.id
 WHERE :object_type = 'person' AND p.id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'collection', c.id, c.name, 1
  FROM collections c JOIN collection_user_state h ON h.collection_id = c.id
 WHERE :object_type = 'collection' AND c.id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'root', r.id, r.name, 1
  FROM library_roots r JOIN root_user_state h ON h.root_id = r.id
 WHERE :object_type = 'root' AND r.id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'tag', t.id, t.name, 1
  FROM tags t JOIN tag_user_state h ON h.tag_id = t.id
 WHERE :object_type = 'tag' AND t.id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'site', pl.id, pl.name, CASE WHEN pl.id = :object_id THEN 1 ELSE 0 END
  FROM ({{SITE_REACH}}) reach
  JOIN sites pl ON pl.id = reach.ancestor_id
  JOIN site_user_state h ON h.site_id = pl.id
 WHERE :object_type = 'site' AND reach.site_id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'song', sg.id, sg.name, 1
  FROM songs sg JOIN song_user_state h ON h.song_id = sg.id
 WHERE :object_type = 'song' AND sg.id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
""",
    SITE_REACH=SITE_REACH,
)

#: WHO CAN SEE THIS, one row per user on the instance, read off the STORED verdict.
#:
#: The reach report's one question, and the whole reason it is asked here rather than assembled
#: from the grants above: `viewer_assets` IS the answer, kept true by the triggers inside every
#: writer's own transaction (see `kernel/access/visibility`), so a statement that reads it cannot
#: disagree with what the guest's next request will be told. The grants are then the EXPLANATION of
#: each yes, never a second computation of it.
#:
#: That distinction is not theoretical. `/sharing/sources` works its own `decides` out by asking
#: `can_view` for a file and, for everything else, a hand-written ladder over `grants_of`, which
#: is a rule about grants and not the rule the entity walls apply, where a tag is reachable because
#: some FILE under it is. This reads the one table both of those are downstream of.
#:
#: ONE STATEMENT AND THE TYPE IS COMPARED, NOT INTERPOLATED, for the reason
#: `_VAULT_SOURCES_FOR_OBJECT` gives: there is no ORM here, so a query built by formatting is the
#: injection control gone. At most one arm can match, and the rest cost a constant-false guard.
#:
#: EXISTS per arm rather than an `IN` over the members, and the difference is the shape of the
#: work: the question is whether there is ONE file this user can reach through the thing, so the
#: engine stops at the first row instead of collecting every file a tag carries once per user.
#:
#: A DISABLED USER CANNOT SEE ANYTHING, whatever rows it has. `load_viewer` answers None for one,
#: so it cannot make a request at all. But the triggers do not know that and its verdict rows sit
#: there exactly as before, which would have had the report saying "Can see" about a user who
#: has been switched off. The flag rides along so the report can say WHICH of the two it is.
#:
#: Ordered by id, matching the user list the sharing panel draws: a wall-clock second can go
#: backwards on some machines and a ULID cannot, and the two lists name the same users.
_REACH_OF_OBJECT = splice(
    """
SELECT u.id AS user_id, u.username, u.role, u.disabled,
       CASE WHEN u.disabled = 1 THEN 0 WHEN
         EXISTS (SELECT 1 FROM viewer_assets v
                  WHERE :object_type = 'item'
                    AND v.user_id = u.id AND v.asset_id = :object_id)
      OR EXISTS (SELECT 1 FROM asset_tags m
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'tag' AND m.tag_id = :object_id)
      OR EXISTS (SELECT 1 FROM asset_people m
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'person' AND m.person_id = :object_id)
      OR EXISTS (SELECT 1 FROM collection_items m
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'collection' AND m.collection_id = :object_id)
      OR EXISTS (SELECT 1 FROM photo_set_items m
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'photo_set' AND m.photo_set_id = :object_id)
      OR EXISTS (SELECT 1 FROM song_files m
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'song' AND m.song_id = :object_id)
      OR EXISTS (SELECT 1 FROM asset_usernames m
                  JOIN usernames ac ON ac.id = m.username_id
                  JOIN ({{SITE_REACH}}) reach ON reach.site_id = ac.site_id
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'site' AND reach.ancestor_id = :object_id)
      OR EXISTS (SELECT 1 FROM asset_locations l
                  JOIN folder_ancestry fa ON fa.folder_id = l.folder_id
                                         AND fa.ancestor_id = :object_id
                  JOIN viewer_assets v ON v.asset_id = l.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'folder')
      OR EXISTS (SELECT 1 FROM asset_locations l
                  JOIN viewer_assets v ON v.asset_id = l.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'root' AND l.root_id = :object_id)
      OR EXISTS (SELECT 1 FROM viewer_assets v
                  WHERE :object_type = 'global' AND v.user_id = u.id)
       THEN 1 ELSE 0 END AS sees
  FROM users u
 ORDER BY u.id
""",
    SITE_REACH=SITE_REACH,
)
