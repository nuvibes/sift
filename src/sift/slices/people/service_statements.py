# SPDX-License-Identifier: AGPL-3.0-or-later
"""The statements the people service runs, by the table they touch."""

from __future__ import annotations

from sift.kernel.access.repository.walls import shown
from sift.kernel.access.sites import site_address
from sift.kernel.sql_splice import splice

#: The three number columns and the site's address and name, for a page of usernames. The address
#: is the Site's first link (`sites.SITE_ADDRESS`), the one every other logo lookup reads.
_USERNAME_FACTS = splice(
    "SELECT u.id AS id, u.number AS number, u.number_via AS via,"
    " u.number_agreed AS agreed, {{SITE_ADDRESS}} AS site_url, s.name AS site_name"
    " FROM usernames u LEFT JOIN sites s ON s.id = u.site_id WHERE u.id IN (?*)",
    SITE_ADDRESS=site_address("s"),
)


# No uniqueness on a person's name, deliberately. Two people can be called the same thing, and
# refusing the second would be asserting something about the world that is not true.
_INSERT_PERSON = """
INSERT INTO people (id, name, name_sort, notes, created_at, created_by_kind, created_by_user_id)
VALUES (?, ?, ?, ?, ?, 'user', ?)
RETURNING *
"""


_UPDATE_PERSON = "UPDATE people SET name = ?, name_sort = ?, notes = ? WHERE id = ? RETURNING *"


#: The record, written whole.
_UPDATE_PERSON_RECORD = """
UPDATE people
   SET disambiguation = ?, gender = ?, birth_date = ?, country = ?,
       ethnicity = ?, eye_color = ?, hair_color = ?, height_cm = ?, measurements = ?,
       breast_type = ?, career_start_year = ?, career_end_year = ?, tattoos = ?, piercings = ?,
       pmv_creator = ?
 WHERE id = ?
RETURNING *
"""


_DELETE_PERSON = "DELETE FROM people WHERE id = ? RETURNING id"


# `vault` rides along on every read of a person because it is what a screen draws the mark from, and
# it is THIS user's, joined on the viewer.
_PERSON_BY_ID = """
SELECT p.*, COALESCE(h.hidden, 0) AS vault
  FROM people p
  LEFT JOIN person_user_state h ON h.person_id = p.id AND h.user_id = ?
 WHERE p.id = ?
"""


#: Of the people on one file, the ones a pass put there rather than a person, and WHICH pass.
_AUTOMATIC_ON_ASSET = """
SELECT ap.person_id AS person_id,
       ap.source    AS source,
       (SELECT u.name
          FROM asset_usernames au
          JOIN usernames u ON u.id = au.username_id
         WHERE au.asset_id = ap.asset_id
           AND u.person_id = ap.person_id
           AND u.name <> ''
         ORDER BY COALESCE(u.name_sort, u.name), u.id
         LIMIT 1) AS username
  FROM asset_people ap
 WHERE ap.asset_id = ? AND ap.source IS NOT NULL
"""


_PEOPLE_OF_ASSET = """
SELECT p.*, COALESCE(h.hidden, 0) AS vault
  FROM people p
  JOIN asset_people ap ON ap.person_id = p.id
  LEFT JOIN person_user_state h ON h.person_id = p.id AND h.user_id = ?
 WHERE ap.asset_id = ?
 ORDER BY COALESCE(p.name_sort, p.name) ASC, p.id ASC
"""


# Hiding somebody, for one user. The same upsert shape the heart and the stars use, because it is
# the same kind of fact: something this viewer decided about a row, held beside the row and not on
# it. `hidden_at` is cleared on the way out so it always means "since when".
_SET_PERSON_VAULT = """
INSERT INTO person_user_state (person_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(person_id, user_id) DO UPDATE SET
    hidden     = excluded.hidden,
    hidden_at  = excluded.hidden_at,
    updated_at = excluded.updated_at
"""


# `OR IGNORE` because assigning a person to something they are already on is not an error. Drag
# the same clip onto the same face twice and the second is a no-op, which is what doing it means.
_ASSIGN_PERSON = "INSERT OR IGNORE INTO asset_people (asset_id, person_id) VALUES (?, ?)"


_UNASSIGN_PERSON = "DELETE FROM asset_people WHERE asset_id = ? AND person_id = ?"


#: `added_at` is named and bound like every other column here. The column is nullable, so leaving it
#: out would write an alias that arrived at no moment, which is what a row from before the column
#: existed looks like, and the two must not be confusable.
_INSERT_ALIAS = """
INSERT INTO people_aliases (id, person_id, alias, alias_sort, added_at) VALUES (?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
RETURNING *
"""


#: The alias comes back with the id, so the event's payload can say WHICH name went. Read out of
#: the delete itself rather than by a second statement: a name looked up afterwards is nothing at
#: all, and one looked up before is a second read of a row this one already has in hand.
_DELETE_ALIAS = "DELETE FROM people_aliases WHERE id = ? AND person_id = ? RETURNING id, alias"


_ALIASES_OF_PERSON = """
SELECT * FROM people_aliases WHERE person_id = ? ORDER BY COALESCE(alias_sort, alias) ASC, id ASC
"""


# Where somebody can be found.
_INSERT_LINK = """
INSERT INTO people_links (id, person_id, url, site_id, label, created_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(person_id, url) DO NOTHING
RETURNING *
"""


#: The address comes back with the id, for the reason the alias does above.
_DELETE_LINK = "DELETE FROM people_links WHERE id = ? AND person_id = ? RETURNING id, url"


# The same three, for a SITE. Written out rather than shared with the person's, because the two
# tables have different columns (a person's link records which site it is on and a site's cannot,
# being one already), and a helper taking a table name would be building SQL from a string.
_INSERT_SITE_LINK = """
INSERT INTO site_links (id, site_id, url, label, created_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(site_id, url) DO NOTHING
RETURNING *
"""


_DELETE_SITE_LINK = "DELETE FROM site_links WHERE id = ? AND site_id = ? RETURNING id"


#: In the order they were written, which is the ID's order (ULIDs, minted by a clock that never goes
#: back: `kernel.ids.new_id`), never `created_at`: that is a wall-clock second, on machines whose
#: clock steps backwards, and the FIRST of these is the Site's address (`sites.SITE_ADDRESS`).
_LINKS_OF_SITE = """
SELECT * FROM site_links WHERE site_id = ? ORDER BY id ASC
"""


#: Replace a site's whole list in one go, which is what the record form sends.
_CLEAR_SITE_LINKS = "DELETE FROM site_links WHERE site_id = ?"


# The row and this user's vault mark, and nothing counted.
_SITE_BY_ID = splice(
    """
SELECT p.*, COALESCE(h.hidden, 0) AS vault, {{SITE_ADDRESS}} AS site_url
  FROM sites p
  LEFT JOIN site_user_state h ON h.site_id = p.id AND h.user_id = :viewer
 WHERE p.id = :site_id
""",
    SITE_ADDRESS=site_address("p"),
)


#: A site's name and nothing else, for the snapshot an event carries.
_SITE_NAME = "SELECT name FROM sites WHERE id = ?"


#: The ordering key is written WITH the name, or a renamed site sorts under the key of a name it no
#: longer has, on the wall and in every picker alike.
_UPDATE_SITE = "UPDATE OR IGNORE sites SET name = ?, name_sort = ? WHERE id = ? RETURNING *"


_SET_SITE_VAULT = """
INSERT INTO site_user_state (site_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(site_id, user_id) DO UPDATE SET
    hidden     = excluded.hidden,
    hidden_at  = excluded.hidden_at,
    updated_at = excluded.updated_at
"""


_DELETE_SITE = "DELETE FROM sites WHERE id = ? RETURNING id"


_DELETE_SITE_USERNAMES = "DELETE FROM usernames WHERE site_id = ?"


# Every username, including one belonging to somebody in the vault, unlike the two counts above,
# which leave those out.
_COUNT_SITE_USERNAMES = "SELECT COUNT(*) AS n FROM usernames WHERE site_id = ?"


# --- the heart, the stars and the tags a person or a site carries -----------------------

_SET_PERSON_FAVORITE = """
INSERT INTO person_user_state (person_id, user_id, favorite, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(person_id, user_id) DO UPDATE SET
    favorite   = excluded.favorite,
    updated_at = excluded.updated_at
RETURNING favorite, rating
"""


_SET_PERSON_RATING = """
INSERT INTO person_user_state (person_id, user_id, rating, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(person_id, user_id) DO UPDATE SET
    rating     = excluded.rating,
    updated_at = excluded.updated_at
RETURNING favorite, rating
"""


_SET_SITE_FAVORITE = """
INSERT INTO site_user_state (site_id, user_id, favorite, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(site_id, user_id) DO UPDATE SET
    favorite   = excluded.favorite,
    updated_at = excluded.updated_at
RETURNING favorite, rating
"""


_SET_SITE_RATING = """
INSERT INTO site_user_state (site_id, user_id, rating, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(site_id, user_id) DO UPDATE SET
    rating     = excluded.rating,
    updated_at = excluded.updated_at
RETURNING favorite, rating
"""


# `OR IGNORE`, because putting a tag on something that already has it is not an error: it is what
# doing it twice means. The same choice `_ASSIGN_PERSON` makes, for the same reason.
_TAG_PERSON = "INSERT OR IGNORE INTO person_tags (person_id, tag_id, added_at) VALUES (?, ?, ?)"


_UNTAG_PERSON = "DELETE FROM person_tags WHERE person_id = ? AND tag_id = ?"


_TAG_SITE = "INSERT OR IGNORE INTO site_tags (site_id, tag_id, added_at) VALUES (?, ?, ?)"


_UNTAG_SITE = "DELETE FROM site_tags WHERE site_id = ? AND tag_id = ?"


_TAGS_OF_PERSON = splice(
    """
SELECT t.* FROM tags t
  JOIN person_tags pt ON pt.tag_id = t.id
 WHERE pt.person_id = :subject AND {{SHOWN}}
 ORDER BY COALESCE(t.name_sort, t.name) ASC, t.id ASC
""",
    SHOWN=shown("tag", "t"),
)


_TAGS_OF_SITE = splice(
    """
SELECT t.* FROM tags t
  JOIN site_tags pt ON pt.tag_id = t.id
 WHERE pt.site_id = :subject AND {{SHOWN}}
 ORDER BY COALESCE(t.name_sort, t.name) ASC, t.id ASC
""",
    SHOWN=shown("tag", "t"),
)


# The face goes with the file, and that is why `cover_track_id` is cleared here rather than left.
_SET_PERSON_COVER = (
    "UPDATE people SET cover_asset_id = ?, cover_at_ms = ?, cover_upload_id = ?, cover_frame = ?,"
    " cover_cleared_at = ?, cover_by_default = NULL, cover_track_id = NULL"
    " WHERE id = ? RETURNING id, name"
)


_SET_SITE_COVER = (
    "UPDATE sites SET cover_asset_id = ?, cover_at_ms = ?, cover_upload_id = ?, cover_frame = ?,"
    " cover_cleared_at = ?, cover_by_default = NULL WHERE id = ? RETURNING id, name"
)


#: What somebody wrote about a site. Its address is not here: that is the first of its links, which
#: `set_site_links` writes (the `site_url` column this also wrote went in catalog v66).
_UPDATE_SITE_DETAILS = """
UPDATE sites SET notes = ? WHERE id = ? RETURNING *
"""


# --- A site's record -------------------------------------------------------------------------
_INSERT_SITE_ALIAS = (
    "INSERT OR IGNORE INTO site_aliases (id, site_id, alias, alias_sort, added_at)"
    " VALUES (?, ?, ?, ?, ?)"
)


#: What this site's aliases arrived at, before the record form rewrites the list.
_SITE_ALIAS_MOMENTS = "SELECT alias, added_at FROM site_aliases WHERE site_id = ?"


_CLEAR_SITE_ALIASES = "DELETE FROM site_aliases WHERE site_id = ?"


_SITE_ALIASES = (
    "SELECT alias FROM site_aliases WHERE site_id = ? ORDER BY COALESCE(alias_sort, alias), id"
)


# The parent as a REFERENCE, read back as the parent's own name AND its id.
_SET_SITE_PARENT = "UPDATE sites SET parent_id = ? WHERE id = ?"


#: Whether the first Site is part of the second, at any depth, or is it. The one walk up a Site's
#: parents, so this answers the question every reader of the tree asks, the same way.
_SITE_PARENT = """
SELECT parent.id AS id, parent.name AS name
  FROM sites child JOIN sites parent ON parent.id = child.parent_id
 WHERE child.id = ?
"""


# --- usernames --------------------------------------------------------------------------------

_UPDATE_USERNAME_DISPLAY_NAME = "UPDATE usernames SET display_name = ? WHERE id = ?"


_UPDATE_USERNAME_URL = "UPDATE usernames SET url = ? WHERE id = ?"


#: Point a username at somebody, or take the pointer off. Unconditional, unlike the stash-box's
#: `AND person_id IS NULL` version: this is a person deciding, and deciding again is allowed.
_SET_USERNAME_PERSON = "UPDATE usernames SET person_id = ? WHERE id = ? RETURNING person_id"


#: The file a cover names and WHICH MOMENT of it, read straight off the row.
_PERSON_COVER = (
    "SELECT cover_asset_id, cover_at_ms, cover_upload_id, cover_frame FROM people WHERE id = ?"
)


_SITE_COVER = (
    "SELECT cover_asset_id, cover_at_ms, cover_upload_id, cover_frame FROM sites WHERE id = ?"
)


_USERNAME_NAME = "SELECT name, person_id FROM usernames WHERE id = ?"


#: Every username one person holds, with the site each sits on. Ordered so a record reads the same
#: way twice: by the site's name, then the spelling.

# The one-person reads above, for many people at once (`enrichment_view_of_people`): the same
# joins and the same order within each person, the ids bound as one JSON array.
_PEOPLE_AMONG = "SELECT * FROM people WHERE id IN (SELECT value FROM json_each(?))"


_ALIASES_OF_PEOPLE = """
SELECT person_id, alias FROM people_aliases
 WHERE person_id IN (SELECT value FROM json_each(?))
 ORDER BY person_id, COALESCE(alias_sort, alias) ASC, id ASC
"""


#: One statement for one person and for many: `links_of` binds one id, the batch read binds
#: every id it was asked about, and both list a person's links in the one order.
_LINKS_OF_PEOPLE = """
SELECT l.*, p.name AS site_name
  FROM people_links l
  LEFT JOIN sites p ON p.id = l.site_id
 WHERE l.person_id IN (SELECT value FROM json_each(?))
 -- ordered by the clock: not every link has an id Sift minted in order; the carry-over from
 -- usernames wrote random hex ones (access schema `_LINKS_FROM_ACCOUNTS`)
 ORDER BY l.person_id, l.created_at ASC, l.id ASC
"""


_TAGS_OF_PEOPLE = """
SELECT pt.person_id, t.name FROM tags t
  JOIN person_tags pt ON pt.tag_id = t.id
 WHERE pt.person_id IN (SELECT value FROM json_each(?))
 ORDER BY pt.person_id, COALESCE(t.name_sort, t.name) ASC, t.id ASC
"""


_USERNAMES_OF_PEOPLE = """
SELECT u.person_id, u.name, u.url, p.name AS site_name
  FROM usernames u
  LEFT JOIN sites p ON p.id = u.site_id
 WHERE u.person_id IN (SELECT value FROM json_each(?))
 ORDER BY u.person_id, COALESCE(p.name_sort, p.name), COALESCE(u.name_sort, u.name), u.id
"""


#: File everything posted under a username under the person it turned out to be.
_FILE_UNDER_PERSON = """
INSERT INTO asset_people (asset_id, person_id, source, decided_at)
SELECT au.asset_id, ?, 'username', ?
  FROM asset_usernames au
 WHERE au.username_id = ?
ON CONFLICT(asset_id, person_id) DO NOTHING
"""


#: And take back exactly what that wrote. Only rows this decision made, and only for files still
#: under this username. A file moved to another username since is not this undo's business.
_UNFILE_FROM_PERSON = """
DELETE FROM asset_people
 WHERE person_id = ?
   AND source = 'username'
   AND asset_id IN (SELECT asset_id FROM asset_usernames WHERE username_id = ?)
"""


#: Every filing one file holds: which username it is under, and on which site.
_FILINGS_OF_ASSET = """
SELECT au.username_id AS username_id,
       au.source      AS source,
       u.name         AS username,
       u.person_id    AS person_id,
       u.site_id      AS site_id,
       p.name         AS site_name
  FROM asset_usernames au
  JOIN usernames u ON u.id = au.username_id
  LEFT JOIN sites p ON p.id = u.site_id
 WHERE au.asset_id = ?
 ORDER BY COALESCE(p.name_sort, p.name), COALESCE(u.name_sort, u.name), u.id
"""


#: Take one file off one username.
_UNFILE_ASSET_FROM_USERNAME = "DELETE FROM asset_usernames WHERE asset_id = ? AND username_id = ?"


#: Take a file off a SITE, however many of its usernames it was filed under.
_UNFILE_ASSET_FROM_SITE = (
    "DELETE FROM asset_usernames WHERE asset_id = ?"
    " AND username_id IN (SELECT id FROM usernames WHERE site_id = ?)"
)


#: Which files a name change on this slice's entities actually reaches.
_ASSETS_OF_PERSON = "SELECT asset_id FROM asset_people WHERE person_id = ?"


_ASSETS_OF_USERNAME = "SELECT asset_id FROM asset_usernames WHERE username_id = ?"


_ASSETS_OF_SITE = (
    "SELECT DISTINCT au.asset_id FROM asset_usernames au"
    " JOIN usernames u ON u.id = au.username_id"
    " WHERE u.site_id = ?"
)
