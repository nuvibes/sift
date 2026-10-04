# SPDX-License-Identifier: AGPL-3.0-or-later
"""The walls of things a viewer may know about: their facets, their cards and who a word names.

Each wall resolves the visible files exactly as the file query does and counts what survived, since
a name listed straight off its table would name everybody to a guest shown nothing of them. The
walls themselves are one module each (`wall_people`, `wall_tags`, ...), re-exported here.
"""

from __future__ import annotations

import re

from sift.kernel.access.constraints import ENTITY_FACETS, EntityFacet
from sift.kernel.access.repository.wall_collections import _COLLECTIONS
from sift.kernel.access.repository.wall_collections import (
    _VISIBLE_COLLECTIONS as _VISIBLE_COLLECTIONS,
)
from sift.kernel.access.repository.wall_collections import (
    collections_position as collections_position,
)
from sift.kernel.access.repository.wall_collections import collections_query as collections_query
from sift.kernel.access.repository.wall_loops import loops_position as loops_position
from sift.kernel.access.repository.wall_loops import loops_query as loops_query
from sift.kernel.access.repository.wall_people import _A_USERNAME_SHOWS_ITS_PERSON, _PEOPLE
from sift.kernel.access.repository.wall_people import people_position as people_position
from sift.kernel.access.repository.wall_people import people_query as people_query
from sift.kernel.access.repository.wall_photo_sets import _PHOTO_SETS
from sift.kernel.access.repository.wall_photo_sets import photo_sets_position as photo_sets_position
from sift.kernel.access.repository.wall_photo_sets import photo_sets_query as photo_sets_query
from sift.kernel.access.repository.wall_sites import _SITES
from sift.kernel.access.repository.wall_sites import SITES_BY_ID as SITES_BY_ID
from sift.kernel.access.repository.wall_sites import sites_position as sites_position
from sift.kernel.access.repository.wall_sites import sites_query as sites_query
from sift.kernel.access.repository.wall_songs import _SONGS
from sift.kernel.access.repository.wall_songs import songs_position as songs_position
from sift.kernel.access.repository.wall_songs import songs_query as songs_query
from sift.kernel.access.repository.wall_tags import _TAGS
from sift.kernel.access.repository.wall_tags import TAGS_BY_ID as TAGS_BY_ID
from sift.kernel.access.repository.wall_tags import tags_position as tags_position
from sift.kernel.access.repository.wall_tags import tags_query as tags_query
from sift.kernel.access.repository.wall_usernames import _VISIBLE_USERNAMES as _VISIBLE_USERNAMES
from sift.kernel.access.repository.wall_usernames import usernames_position as usernames_position
from sift.kernel.access.repository.wall_usernames import usernames_query as usernames_query
from sift.kernel.access.repository.walls import (
    _FILTER_AT,
    _NOTHING,
    _narrowed,
    _row_narrowed,
    _Wall,
)
from sift.kernel.access.repository.walls import ENTITY_SORT_KEYS as ENTITY_SORT_KEYS
from sift.kernel.access.repository.walls import ENTITY_SORT_SEEN as ENTITY_SORT_SEEN
from sift.kernel.access.repository.walls import SONG_SORT_KEYS as SONG_SORT_KEYS
from sift.kernel.access.repository.walls import _entity_sort as _entity_sort
from sift.kernel.access.sites import SITE_CONCEALED, SITE_REACH
from sift.kernel.sql_splice import splice

# --- counting a wall of things along one dimension ----------------------------------------------
#
# A facet on a wall of THINGS is the wall's own statement with the projection swapped: the same
# CTEs, the same joins, the same file filter, the same row filter and the same permission rules,
# reading one dimension and counting the things instead of listing them. That is the whole point,
# and it is the rule `facet_query` in `assets.py` follows for files: a count computed any other way
# is a number that can disagree with the screen, and the direction it disagrees in is the one that
# publishes what somebody was not shown. A concealed row never reaches a group here because it
# never gets past the WHERE that the page uses.
#
# The counts are always the LIVE form, never the stored-count shortcut. A stored per-thing count
# answers "how many files are under this thing", which is a different question from "how many
# THINGS have this value", and the shortcut replaces the very CTE the file filter is spliced
# into, so a facet built on it could not be filtered by a filter at all.


#: Every wall, in its pieces, by the noun it is a wall of. The keys are `ENTITY_FACETS`'s, checked
#: below: a wall with no facets or a facet table naming no wall is a half-built dimension.
_WALLS: dict[str, _Wall] = {
    "person": _PEOPLE,
    "site": _SITES,
    "tag": _TAGS,
    "collection": _COLLECTIONS,
    "photo_set": _PHOTO_SETS,
    "song": _SONGS,
}

if set(_WALLS) != set(ENTITY_FACETS):  # pragma: no cover (a wall or a facet table added alone)
    raise RuntimeError("the walls and the facets they are counted along name different nouns")


def _facet_statement(wall: _Wall, facet: EntityFacet) -> str:
    """One wall, projected onto one dimension.

    `COUNT(DISTINCT <alias>.id)` because a dimension reached through a join multiplies rows (a
    person with three tags is three rows here), and the question is how many PEOPLE, not how many
    rows. The facet's own joins are written straight after the FROM, ahead of the wall's own, which
    is where they can only reference the thing itself.

    Nothing in the dimension is not a value of it, said in the WHERE chain: a HAVING over the
    output alias is an extension older SQLite refuses.
    """
    for text in (facet.value, facet.narrow, facet.joins):
        if text and re.search(r"\b" + re.escape(wall.alias) + r"\.", text):
            break
    else:  # pragma: no cover (reaching this means a wall was re-aliased under its own facets)
        raise RuntimeError(f"a facet of {wall.source} never mentions the alias {wall.alias!r}")
    label = "NULL" if facet.label is None else facet.label
    return (
        wall.ctes
        + "\nSELECT "
        + facet.value
        + " AS facet_value,"
        + "\n       "
        + label
        + " AS facet_label,"
        + "\n       COUNT(DISTINCT "
        + wall.alias
        + ".id) AS facet_count"
        + wall.from_at
        + facet.joins
        + wall.body
        + "\n   AND ("
        + facet.value
        + ") IS NOT NULL AND ("
        + facet.value
        + ") <> ''"
        + "\n GROUP BY facet_value, facet_label"
        + "\n ORDER BY facet_count DESC, facet_value ASC"
        + "\n LIMIT :limit\n"
    )


#: Every dimension of every wall, built once at import. The only part that varies per request is
#: the two seams, which are spliced below.
_FACET_STATEMENTS: dict[tuple[str, str], str] = {
    (subject, key): _facet_statement(_WALLS[subject], facet)
    for subject, facets in ENTITY_FACETS.items()
    for key, facet in facets.items()
}


def _filter_spliced(statement: str, where: str) -> str:
    """A whole statement with a file filter at its seam: what `_filtered` does to two halves."""
    if not _narrowed(where):
        return statement
    found = statement.count(_FILTER_AT)
    if found != 1:  # pragma: no cover (reaching this means a statement was edited across the cut)
        raise RuntimeError(f"a facet statement has {found} filter seams, expected exactly one")
    return statement.replace(_FILTER_AT, "\n     AND (" + where + ")\n", 1)


def entity_facet_query(subject: str, facet: str, where: str, rows: str = _NOTHING) -> str:
    """How many of the things this wall reaches carry each value of one dimension.

    `subject` and `facet` are validated keys, never text a caller wrote: the route refuses a
    dimension `ENTITY_FACETS` does not declare, exactly as the file facet route does, so nothing
    typed reaches the statement text. Both filters apply: the panel describes the wall as it
    is being looked at, not the library.
    """
    return _row_narrowed(_filter_spliced(_FACET_STATEMENTS[(subject, facet)], where), rows)


#: Everyone a typed term names. The one place a term becomes a person, shared by the People
#: screen and by search, because two implementations of this is the bug where a name is found in
#: one box and not the other with nothing to say which is right.
#:
#: Three sources, unioned: the name on the row, an explicit alias, and any username linked to that
#: person. The third is what makes linking a username quietly make it searchable: there is no
#: separate step and nothing to re-index, which is the point.
#:
#: The resolver is here, and there are no counts. This answers who a word names, not what they
#: may see, so the visible set is joined to rather than tallied and the caller takes these ids on
#: to a query that is scoped. The vault is honoured for the same reason: a vaulted person's name
#: is exactly what must not come back.
_ALIAS_TARGETS = """
WITH RECURSIVE
matched(person_id) AS (
  SELECT id FROM people WHERE name = :term COLLATE NOCASE
  UNION
  SELECT person_id FROM people_aliases WHERE alias = :term COLLATE NOCASE
  UNION
  SELECT person_id FROM usernames
   WHERE person_id IS NOT NULL AND name = :term COLLATE NOCASE
),

-- The stored verdict for this viewer (see `sift.kernel.access.visibility`): every file they may
-- see that the vault is not holding back, which is the set a count may describe.
counted(person_id, asset_count) AS (
  SELECT ap.person_id, COUNT(*)
    FROM asset_people ap
    CROSS JOIN viewer_assets v ON v.asset_id = ap.asset_id AND v.user_id = :viewer
   WHERE (:reveal = 1 OR v.concealed = 0)
   GROUP BY ap.person_id
)
-- Scoped exactly as the people list is, and that is why the resolver is here rather than being a
-- bare name lookup. A term is a probe: type one, and an answer tells the asker that somebody by
-- that name is in this library. Left unscoped, anybody who can sign in could work through a list
-- of names and learn which of them this install holds, while the people list, one route over,
-- refuses to show them a single one.
SELECT p.id
  FROM people p
  JOIN matched m ON m.person_id = p.id
  LEFT JOIN counted c ON c.person_id = p.id
 WHERE (:reveal_named = 1
        OR NOT EXISTS (SELECT 1 FROM person_user_state hp
                        WHERE hp.person_id = p.id AND hp.user_id = :viewer AND hp.hidden = 1))
   AND (:is_admin = 1 OR COALESCE(c.asset_count, 0) > 0)
 ORDER BY COALESCE(p.name_sort, p.name) ASC, p.id ASC
 LIMIT :limit
"""


# --- what an entity CARD draws beside its name -------------------------------------------------

#: The cells each kind of card carries, as the tab ids of the page the card opens (`tabsFor` in the
#: client's `related.svelte.ts`), in no particular order: the client orders them.
#:
#: Every tab of that page is here but two, each for its own reason: the files (the card says them
#: in words under the name) and a person's Seen with (deliberately not on the card). A site's
#: `sites_within` is here although it is not a count of files: it is how many Sites are part of
#: this one, read off `sites.parent_id`, and the card draws it as its network mark beside the name
#: rather than as a cell. A photo set's page has no Loops tab, so its card has no Loops cell. A tab
#: missing here is a cell the card leaves undrawn, never one it draws as nought.
CARD_TABS: dict[str, tuple[str, ...]] = {
    "person": ("photo_sets", "loops", "tags", "sites", "collections", "songs"),
    "tag": ("photo_sets", "loops", "people", "sites", "collections", "songs"),
    "site": ("photo_sets", "loops", "sites_within", "people", "tags", "collections", "songs"),
    "collection": ("loops", "people", "tags", "sites", "songs"),
    "photo_set": ("people", "tags", "sites"),
    # A song's page has every tab but Photo Sets: the Loops cut from the files that carry it, who
    # is in those files, their tags, the Sites they came from and the Collections holding them.
    "song": ("loops", "people", "tags", "sites", "collections"),
}

#: The tab each kind of partner is counted under.
CARD_TAB_OF_KIND: dict[str, str] = {
    "person": "people",
    "tag": "tags",
    "photo_set": "photo_sets",
    "collection": "collections",
    "site": "sites",
    "loop": "loops",
    "song": "songs",
    # Not a partner kind: the Sites that are part of a site card, counted by `within` below.
    "sites_within": "sites_within",
}

#: Whether the card `s.id` of kind `:kind` and one partner share an ALIVE pair for this viewer, by
#: the same vault test the cell reads. A pair is stored once in one order, so both are probed; each
#: is a primary-key or B-index point read and one of the two finds nothing. `{partner_kind}` and
#: `{partner}` are filled with a quoted kind and a column of the statement, nothing else.
_ALIVE_PAIR = (
    "(EXISTS (SELECT 1 FROM viewer_pair_counts ap"
    " WHERE ap.user_id = :viewer AND ap.kind_a = :kind AND ap.id_a = s.id"
    " AND ap.kind_b = {partner_kind} AND ap.id_b = {partner}"
    " AND CASE WHEN :reveal = 1 THEN ap.permitted ELSE ap.permitted - ap.concealed END > 0)"
    " OR EXISTS (SELECT 1 FROM viewer_pair_counts ap"
    " WHERE ap.user_id = :viewer AND ap.kind_a = {partner_kind} AND ap.id_a = {partner}"
    " AND ap.kind_b = :kind AND ap.id_b = s.id"
    " AND CASE WHEN :reveal = 1 THEN ap.permitted ELSE ap.permitted - ap.concealed END > 0))"
)

#: How many of each kind of thing every card on one page of a wall reaches, read off the stored
#: pairs (`viewer_pair_counts`) and the partner totals they keep (`viewer_partner_counts`) rather
#: than counted from the memberships.
#:
#: THE SAME QUESTION THE ENTITY PAGE'S OWN TAB STRIP ASKS, AND IT MUST GIVE THE SAME ANSWER. The
#: strip counts the rows of the related wall (`list_tags(asset_filter=person)` and the rest), and
#: a row is on that wall when at least one file this viewer may see carries both things (with the
#: vault shut, one the vault is not holding back) and the row itself is not hidden from this viewer.
#: So a pair is ALIVE here exactly then: its permitted count, less the concealed ones unless
#: `:reveal`, is above nought, and the partner passes the same `:reveal_named` test the wall puts
#: on its own rows. `test_every_count_hides_what_the_vault_hides` holds the two side by side.
#:
#: TWO WAYS OF READING A CELL, and which one is the kind's:
#:
#: - A cell whose partners are a kind the pairs name, on a card that is itself a side of a pair (a
#:   person's photo sets, a tag's people): ONE ROW of `viewer_partner_counts`, which holds how many
#:   of the card's pairs of that kind are alive, with the vault open and with it shut. What that
#:   row cannot know is which partners THIS viewer has hidden, so with `:reveal_named` off the ones
#:   they hid are taken back off: a probe per hidden partner, over the viewer's hides of that kind
#:   (a partial index each), which is a handful where the pairs are thousands: a page of the
#:   largest tags costs a tenth as much this way as counted pair by pair.
#: - A SITE, either way round, counted pair by pair (see the visibility module for why no
#:   pair names a site). A card of a person counts the sites above every username it shares a file
#:   with, reading only its `'username'` pairs; a card of a site reads the pairs of every username
#:   filed under it or under a label beneath it, because a partner two of its usernames share is one
#:   partner and a stored per-username total cannot be summed into that.
#:
#: A tag's Loops cell is both: the Loops cut from its files are pairs, and the Loops it carries
#: ITSELF (`loop_tags`) are read here and added when no alive pair already reaches them.
#:
#: Bounded by the PAGE rather than the wall: `:ids` is the page's ids, so the cost is sixty cards
#: however large the library grows, and nothing in the wall's own statement (the most
#: security-critical text in the project) is touched to carry it. It is a read of a stored answer
#: and not a second copy of the permission rules: the rows are the verdict's own.
_CARD_COUNTS = splice(
    """
WITH RECURSIVE
lineage(site_id, ancestor_id) AS ({{SITE_REACH}}),
page(id) AS (SELECT value FROM json_each(:ids)),
-- A SITE card as the sides of stored pairs: every username under it or under a label beneath it.
sides(id, side) AS (
  SELECT page.id, ac.id
    FROM page
    JOIN lineage li ON li.ancestor_id = page.id
    JOIN usernames ac ON ac.site_id = li.site_id
   WHERE :kind = 'site'
),
-- The partners counted PAIR BY PAIR: every partner of a site card, and the usernames of any other
-- card (which stand for the sites above them).
--
-- CROSS JOIN, and each side's kind written as a constant rather than read off a column: left to
-- itself the planner cannot use the kind as a key and walks every pair the user has, where this
-- way is a range read per card.
reached(id, kind, partner) AS (
  SELECT s.id, p.kind_b, p.id_b
    FROM sides s
    CROSS JOIN viewer_pair_counts p
      ON p.user_id = :viewer AND p.kind_a = 'username' AND p.id_a = s.side
   WHERE CASE WHEN :reveal = 1 THEN p.permitted ELSE p.permitted - p.concealed END > 0
  UNION ALL
  SELECT s.id, p.kind_a, p.id_a
    FROM sides s
    CROSS JOIN viewer_pair_counts p
      ON p.user_id = :viewer AND p.kind_b = 'username' AND p.id_b = s.side
   WHERE CASE WHEN :reveal = 1 THEN p.permitted ELSE p.permitted - p.concealed END > 0
  UNION ALL
  SELECT s.id, 'username', p.id_b
    FROM page s
    CROSS JOIN viewer_pair_counts p
      ON p.user_id = :viewer AND p.kind_a = :kind AND p.id_a = s.id AND p.kind_b = 'username'
   WHERE :kind != 'site'
     AND CASE WHEN :reveal = 1 THEN p.permitted ELSE p.permitted - p.concealed END > 0
  UNION ALL
  SELECT s.id, 'username', p.id_a
    FROM page s
    CROSS JOIN viewer_pair_counts p
      ON p.user_id = :viewer AND p.kind_b = :kind AND p.id_b = s.id AND p.kind_a = 'username'
   WHERE :kind != 'site'
     AND CASE WHEN :reveal = 1 THEN p.permitted ELSE p.permitted - p.concealed END > 0
),
-- A username partner stands for every site above it, which is what the Sites tab lists.
--
-- CROSS JOIN onto the usernames, from the handful reached: left to itself the planner scans every
-- username in the library and probes `reached` for each, at more than twice the cost.
resolved(id, kind, partner) AS (
  SELECT id, kind, partner FROM reached WHERE kind != 'username'
  UNION ALL
  SELECT r.id, 'site', li.ancestor_id
    FROM reached r
    CROSS JOIN usernames ac ON ac.id = r.partner
    JOIN lineage li ON li.site_id = ac.site_id
   WHERE r.kind = 'username'
  UNION ALL
  -- A SITE's people include the people with a USERNAME on it, files or none: the rule and the
  -- reason are `_A_USERNAME_SHOWS_ITS_PERSON`, which the People wall narrowed to this Site reads
  -- too, so the cell and the tab its press opens are one population. COUNT(DISTINCT) below makes a
  -- person reached both ways one person.
  SELECT s.id, 'person', ac.person_id
    FROM sides s
    CROSS JOIN usernames ac ON ac.id = s.side
   WHERE ac.person_id IS NOT NULL AND {{A_USERNAME_SHOWS_ITS_PERSON}}
),
counted(id, kind, n) AS (
  SELECT r.id, r.kind, COUNT(DISTINCT r.partner)
    FROM resolved r
   WHERE :reveal_named = 1 OR NOT (CASE r.kind
     WHEN 'person' THEN EXISTS (SELECT 1 FROM person_user_state h
                                 WHERE h.person_id = r.partner AND h.user_id = :viewer
                                   AND h.hidden = 1)
     WHEN 'tag' THEN EXISTS (SELECT 1 FROM tag_user_state h
                              WHERE h.tag_id = r.partner AND h.user_id = :viewer AND h.hidden = 1)
     WHEN 'collection' THEN EXISTS (SELECT 1 FROM collection_user_state h
                                     WHERE h.collection_id = r.partner AND h.user_id = :viewer
                                       AND h.hidden = 1)
     WHEN 'photo_set' THEN EXISTS (SELECT 1 FROM photo_set_user_state h
                                    WHERE h.photo_set_id = r.partner AND h.user_id = :viewer
                                      AND h.hidden = 1)
     WHEN 'song' THEN EXISTS (SELECT 1 FROM song_user_state h
                               WHERE h.song_id = r.partner AND h.user_id = :viewer
                                 AND h.hidden = 1)
     WHEN 'site' THEN {{SITE_CONCEALED_PARTNER}}
     -- A Loop has no hide of its own: its file's verdict, already applied above, is the whole rule.
     WHEN 'loop' THEN 0
     ELSE 1 END)
   GROUP BY r.id, r.kind
),
-- The partners READ OFF ONE ROW: every other cell of every card but a site's.
stored(id, kind, n) AS (
  SELECT s.id, x.partner_kind,
         CASE WHEN :reveal = 1 THEN x.permitted ELSE x.shown END
         - CASE WHEN :reveal_named = 1 THEN 0 ELSE (CASE x.partner_kind
             WHEN 'person' THEN (SELECT COUNT(*) FROM person_user_state h
                                  WHERE h.user_id = :viewer AND h.hidden = 1
                                    AND {{ALIVE_PERSON}})
             WHEN 'tag' THEN (SELECT COUNT(*) FROM tag_user_state h
                               WHERE h.user_id = :viewer AND h.hidden = 1 AND {{ALIVE_TAG}})
             WHEN 'collection' THEN (SELECT COUNT(*) FROM collection_user_state h
                                      WHERE h.user_id = :viewer AND h.hidden = 1
                                        AND {{ALIVE_COLLECTION}})
             WHEN 'photo_set' THEN (SELECT COUNT(*) FROM photo_set_user_state h
                                     WHERE h.user_id = :viewer AND h.hidden = 1
                                       AND {{ALIVE_PHOTO_SET}})
             WHEN 'song' THEN (SELECT COUNT(*) FROM song_user_state h
                                WHERE h.user_id = :viewer AND h.hidden = 1 AND {{ALIVE_SONG}})
             ELSE 0 END) END
    FROM page s
    CROSS JOIN viewer_partner_counts x
      ON x.user_id = :viewer AND x.kind = :kind AND x.object_id = s.id
   WHERE :kind != 'site' AND x.partner_kind NOT IN ('username', 'site')
  UNION ALL
  -- The Loops a TAG carries itself, which the tag's Loops wall lists beside the Loops cut from its
  -- files (`loop_tag` in the loops statement). Not a pair (no file carries the tag), so each is
  -- read here against the Loop's own stored count, which is alive exactly when the Loop's file is
  -- one this viewer may be shown, by the same vault test as the pairs; and counted only when no
  -- alive pair already reached it, because a Loop reached both ways is one Loop.
  SELECT s.id, 'loop', COUNT(*)
    FROM page s
    CROSS JOIN loop_tags lt ON lt.tag_id = s.id
    JOIN viewer_entity_counts ec
      ON ec.user_id = :viewer AND ec.kind = 'loop' AND ec.object_id = lt.loop_id
   WHERE :kind = 'tag'
     AND CASE WHEN :reveal = 1 THEN ec.permitted ELSE ec.permitted - ec.concealed END > 0
     AND NOT {{ALIVE_TAG_LOOP}}
   GROUP BY s.id
),
-- THE SITES THAT ARE PART OF A SITE CARD: its network mark, and the number its Sites tab carries.
--
-- The tab counts the Sites wall asked for one parent (`list_sites(parent=...)`), so this is that
-- wall's own membership rule over the card's direct children and nothing else: one step down, the
-- child not concealed by itself or by anything above it unless `:reveal_named`, and a child with
-- nothing this viewer may be shown kept only for an admin, whose unfiltered wall lists every row.
-- The files test is the stored count the unfiltered wall reads. `ix_sites_parent` answers the join.
within(id, kind, n) AS (
  SELECT s.id, 'sites_within', COUNT(*)
    FROM page s
    CROSS JOIN sites ch ON ch.parent_id = s.id
   WHERE :kind = 'site'
     AND (:reveal_named = 1 OR NOT {{SITE_CONCEALED_CHILD}})
     AND (:is_admin = 1 OR EXISTS (
           SELECT 1 FROM viewer_entity_counts ec
            WHERE ec.user_id = :viewer AND ec.kind = 'site' AND ec.object_id = ch.id
              AND ec.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE ec.concealed END > 0))
   GROUP BY s.id
)
SELECT id, kind, SUM(n) AS n
  FROM (SELECT id, kind, n FROM counted UNION ALL SELECT id, kind, n FROM stored
        UNION ALL SELECT id, kind, n FROM within)
 GROUP BY id, kind
""",
    SITE_REACH=SITE_REACH,
    A_USERNAME_SHOWS_ITS_PERSON=_A_USERNAME_SHOWS_ITS_PERSON.format(u="ac"),
    SITE_CONCEALED_PARTNER=SITE_CONCEALED.format(site="r.partner"),
    SITE_CONCEALED_CHILD=SITE_CONCEALED.format(site="ch.id"),
    ALIVE_PERSON=_ALIVE_PAIR.format(partner_kind="'person'", partner="h.person_id"),
    ALIVE_TAG=_ALIVE_PAIR.format(partner_kind="'tag'", partner="h.tag_id"),
    ALIVE_COLLECTION=_ALIVE_PAIR.format(partner_kind="'collection'", partner="h.collection_id"),
    ALIVE_PHOTO_SET=_ALIVE_PAIR.format(partner_kind="'photo_set'", partner="h.photo_set_id"),
    ALIVE_SONG=_ALIVE_PAIR.format(partner_kind="'song'", partner="h.song_id"),
    ALIVE_TAG_LOOP=_ALIVE_PAIR.format(partner_kind="'loop'", partner="lt.loop_id"),
)


def card_counts_query() -> str:
    """The statement above. A function so the reader names what it asks rather than a constant."""
    return _CARD_COUNTS
