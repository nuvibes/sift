# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a query filtered to, in the only form the scoped read will accept.

The filter is applied INSIDE the statement that decides visibility, so totals describe the right set
and enforcement stays in one statement. A filter is a bounded tree (ALL, ANY, NOT) of fixed
templates whose values all bind, wrapped as one conjunct of a fixed AND chain, so it can only narrow
what the permission rule allowed. A group that resolved to nothing matches nothing: dropping it
would widen the search past a name the viewer may not be told about.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum

# Re-exported: the filter's callers import every filtering name from here.
from sift.kernel.access.entity_facets import ADMIN_ENTITY_FACETS as ADMIN_ENTITY_FACETS
from sift.kernel.access.entity_facets import DISAGREEING as DISAGREEING
from sift.kernel.access.entity_facets import DISAGREES as DISAGREES
from sift.kernel.access.entity_facets import ENTITY_FACETS as ENTITY_FACETS
from sift.kernel.access.entity_facets import NO_NARROWING as NO_NARROWING
from sift.kernel.access.entity_facets import EntityFacet as EntityFacet
from sift.kernel.access.entity_facets import EntityNarrowing as EntityNarrowing
from sift.kernel.access.entity_facets import asks_disagreements as asks_disagreements
from sift.kernel.access.entity_facets import is_refusal as is_refusal
from sift.kernel.access.filter_parts import AGE_WITHIN as AGE_WITHIN
from sift.kernel.access.filter_parts import AGE_YEARS as AGE_YEARS
from sift.kernel.access.filter_parts import FILE_MADE_BY as FILE_MADE_BY
from sift.kernel.access.filter_parts import HEIGHT_BAND as HEIGHT_BAND
from sift.kernel.access.filter_parts import KEPT_FROM_SWAPS_FILES as KEPT_FROM_SWAPS_FILES
from sift.kernel.access.filter_parts import KEPT_FROM_SWAPS_HERE as KEPT_FROM_SWAPS_HERE
from sift.kernel.access.filter_parts import KEPT_LOCAL_FILES as KEPT_LOCAL_FILES
from sift.kernel.access.filter_parts import KEPT_LOCAL_HERE as KEPT_LOCAL_HERE
from sift.kernel.access.filter_parts import ConstraintError as ConstraintError
from sift.kernel.access.sites import FILES_SITES_REACH
from sift.kernel.access.tag_tree import TAGS_UNDER
from sift.kernel.content.user_state import RESUMING
from sift.kernel.ids import is_id
from sift.kernel.text import printable

#: What `media_type` may be; anything else is refused.
MEDIA_TYPES = frozenset({"video", "image", "gif"})

#: The stored star range, ten whatever scale is drawn. Bounds outside it are clamped, not refused.
MIN_RATING = 0
MAX_RATING = 10

#: The trigram index holds runs of three characters, so a shorter term is not in it.
MIN_TEXT_TERM = 3


Group = tuple[str, ...]
Groups = tuple[Group, ...]


class FolderDepth(StrEnum):
    """How far below a named folder `in:` reaches: a parameter a control sends, because a typed
    `in:` means the whole subtree. `DIRECT` is narrower, so naming a depth can only ask for less."""

    #: The named folder and everything beneath it. What `in:` has always meant.
    SUBTREE = "subtree"
    #: The named folder itself, and nothing below it.
    DIRECT = "direct"


#: WHICH STASH-BOX MADE ONE FILING, the History's own expression (`history.BOX_OF_A_ROW`) written
#: out again because this module may not import it; `test_the_filing_box_is_the_history_box` holds
#: the copies equal.
_BOX_OF_THE_ROW = (
    "CASE WHEN link.source = 'stash_box' THEN ("
    " CASE WHEN link.box_id IS NOT NULL THEN ("
    " SELECT b.name FROM stash_boxes b WHERE b.id = link.box_id"
    " ) ELSE ("
    " SELECT CASE WHEN COUNT(*) = 1 THEN MIN(b.name) END"
    " FROM asset_stash_box_matches m"
    " JOIN stash_boxes b ON b.id = m.box_id"
    " WHERE m.asset_id = link.asset_id AND m.state = 'applied'"
    " ) END"
    " ) END"
)


# --- the leaves -------------------------------------------------------------------------------
#
# Every condition a filter can express, written once; `{}` is the only substitution. Set-valued
# leaves bind one JSON array. Membership is `a.id IN (SELECT ...)` so the planner drives the read
# from the members. Every `noqa: S608` splices a constant, never a value.
PREDICATES: dict[str, str] = {
    # A TAG TAKES IN ITS BRANCH: the files carrying it or any tag filed under it (`tag_tree.py`).
    "tags": (
        "a.id IN (SELECT t.asset_id FROM asset_tags t"  # noqa: S608
        " WHERE t.tag_id IN (" + TAGS_UNDER + "))"
    ),
    "people": (
        "a.id IN (SELECT ap.asset_id FROM asset_people ap"
        " WHERE ap.person_id IN (SELECT value FROM json_each({})))"
    ),
    # One username by id, for the Files wall's `?username=`; never a query word.
    "usernames": (
        "a.id IN (SELECT aa.asset_id FROM asset_usernames aa"
        " WHERE aa.username_id IN (SELECT value FROM json_each({})))"
    ),
    # A network reaches its labels' files, by the fragment the two visibility rules read
    # (`sites.py`).
    "sites": "a.id IN ("  # noqa: S608
    + FILES_SITES_REACH.format(ancestors="IN (SELECT value FROM json_each({}))")
    + ")",
    "collections": (
        "a.id IN (SELECT ci.asset_id FROM collection_items ci"
        " WHERE ci.collection_id IN (SELECT value FROM json_each({})))"
    ),
    "photo_sets": (
        "a.id IN (SELECT psi.asset_id FROM photo_set_items psi"
        " WHERE psi.photo_set_id IN (SELECT value FROM json_each({})))"
    ),
    # The files that carry one song (`kernel/content/songs.py`).
    "songs": (
        "a.id IN (SELECT sf.asset_id FROM song_files sf"
        " WHERE sf.song_id IN (SELECT value FROM json_each({})))"
    ),
    # A set of ids a caller holds with no question to ask instead (a group of unnamed faces).
    # Filtering inside the resolve keeps the visibility rule in one place.
    "assets": "a.id IN (SELECT value FROM json_each({}))",
    # ONE FILING, as a History line counts it (`history_entity._SITE_FILES` and its siblings): the
    # subject by equality, the source and the local day with `IS` because NULL is a real value of
    # each. The `_box` arms add which box.
    "filed_under": (
        "a.id IN (SELECT link.asset_id FROM usernames ac"
        " JOIN asset_usernames link ON link.username_id = ac.id"
        " WHERE ac.site_id = {} AND link.source IS {}"
        " AND unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 IS {})"
    ),
    "filed_under_box": (
        "a.id IN (SELECT link.asset_id FROM usernames ac"  # noqa: S608
        " JOIN asset_usernames link ON link.username_id = ac.id"
        " WHERE ac.site_id = {} AND link.source = 'stash_box'"
        " AND unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 IS {}"
        " AND " + _BOX_OF_THE_ROW + " IS {})"
    ),
    "tagged_with": (
        "a.id IN (SELECT link.asset_id FROM asset_tags link"
        " WHERE link.tag_id = {} AND link.source IS {}"
        " AND unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 IS {})"
    ),
    "tagged_with_box": (
        "a.id IN (SELECT link.asset_id FROM asset_tags link"  # noqa: S608
        " WHERE link.tag_id = {} AND link.source = 'stash_box'"
        " AND unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 IS {}"
        " AND " + _BOX_OF_THE_ROW + " IS {})"
    ),
    "named_as": (
        "a.id IN (SELECT link.asset_id FROM asset_people link"
        " WHERE link.person_id = {} AND link.source IS {}"
        " AND unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 IS {})"
    ),
    "named_as_box": (
        "a.id IN (SELECT link.asset_id FROM asset_people link"  # noqa: S608
        " WHERE link.person_id = {} AND link.source = 'stash_box'"
        " AND unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 IS {}"
        " AND " + _BOX_OF_THE_ROW + " IS {})"
    ),
    # Matched against the expanded subtree (`in_scope`); any copy inside matches.
    "folder": (
        "a.id IN (SELECT l.asset_id FROM asset_locations l"
        " JOIN in_scope s ON s.folder_id = l.folder_id AND s.grp = {})"
    ),
    # Not a bare IN, which is NULL over a NULL column and stops applying under a NOT.
    "media_type": "EXISTS (SELECT 1 FROM json_each({}) kind WHERE kind.value = a.media_type)",
    # Per user. An unrated file is in no rating range.
    "rating_min": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.rating >= {})"
    ),
    "rating_max": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.rating <= {})"
    ),
    # Per user, as a number: never pressed is nought, and the COALESCE keeps a negation applying.
    "o_count_min": (
        "COALESCE((SELECT s.o_count FROM asset_user_state s"
        " WHERE s.asset_id = a.id AND s.user_id = :viewer), 0) >= {}"
    ),
    "o_count_max": (
        "COALESCE((SELECT s.o_count FROM asset_user_state s"
        " WHERE s.asset_id = a.id AND s.user_id = :viewer), 0) <= {}"
    ),
    # The column rather than the row, which a heart alone also writes.
    "rated": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.rating IS NOT NULL)"
    ),
    # Per user. The column rather than the row, which a heart alone also writes.
    "viewed": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.last_viewed_at IS NOT NULL)"
    ),
    # Viewed, or holding a place to go back to: a short sitting writes a place without a view
    # (`user_state.record_watch_time`). What `viewed:none` negates.
    "opened": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"  # noqa: S608
        " AND s.user_id = :viewer AND (s.last_viewed_at IS NOT NULL OR "
        + RESUMING.format(
            duration="a.duration_ms", position="s.resume_ms", minimum=":resume_min_ms"
        )
        + "))"
    ),
    # Seen through once. `completed_at`, because watch time counts a minute watched sixty times as
    # an hour.
    "finished": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.completed_at IS NOT NULL)"
    ),
    # Opened and not seen through.
    "started": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.last_viewed_at IS NOT NULL AND s.completed_at IS NULL)"
    ),
    # Part-way through, by `RESUMING`, the rule the player and the grid's bar use. `:resume_min_ms`
    # is NULL when resuming is off, which makes this false for every row.
    "resuming": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"  # noqa: S608
        " AND s.user_id = :viewer AND "
        + RESUMING.format(
            duration="a.duration_ms", position="s.resume_ms", minimum=":resume_min_ms"
        )
        + ")"
    ),
    "favorite": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.favorite = 1)"
    ),
    # A predicate as well as a sort key: the page reads pins on their own (`list_assets`).
    "pinned": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.pinned = 1)"
    ),
    # A decision made on THIS FILE, not one that reaches it: a restricted root would otherwise put
    # the whole library under `sharing:restricted`.
    "shared_here": (
        "EXISTS (SELECT 1 FROM acl_grants g WHERE g.object_type = 'item'"
        " AND g.object_id = a.id AND g.effect = 'share')"
    ),
    "restricted_here": (
        "EXISTS (SELECT 1 FROM acl_grants g WHERE g.object_type = 'item'"
        " AND g.object_id = a.id AND g.effect = 'restrict')"
    ),
    # Presence on a dimension. The negations are the curation queues.
    "has_tags": "EXISTS (SELECT 1 FROM asset_tags t WHERE t.asset_id = a.id)",
    "has_people": "EXISTS (SELECT 1 FROM asset_people ap WHERE ap.asset_id = a.id)",
    "has_sites": (
        "EXISTS (SELECT 1 FROM asset_usernames aa JOIN usernames ac ON ac.id = aa.username_id"
        " WHERE aa.asset_id = a.id)"
    ),
    "has_collections": "EXISTS (SELECT 1 FROM collection_items ci WHERE ci.asset_id = a.id)",
    "has_photo_sets": "EXISTS (SELECT 1 FROM photo_set_items psi WHERE psi.asset_id = a.id)",
    "has_songs": "EXISTS (SELECT 1 FROM song_files hs WHERE hs.asset_id = a.id)",
    # No `loops:` value filter: a loop is a piece of the file, not a thing it is in.
    "has_loops": "EXISTS (SELECT 1 FROM loops lp WHERE lp.asset_id = a.id)",
    # A face nobody named, outside a group somebody set aside: dismissed work must not come back.
    "has_unnamed_face": (
        "EXISTS (SELECT 1 FROM face_tracks ft"
        " JOIN face_piles fp ON fp.id = ft.pile_id AND fp.status = 'open'"
        " WHERE ft.asset_id = a.id AND ft.person_id IS NULL)"
    ),
    # Filed under one person from a folder, with a face in it nobody named yet: the folder's name
    # gave the file her name and the face waits in an unnamed group, so it never reaches her faces.
    # The same unnamed face `has_unnamed_face` means.
    "unnamed_face": (
        "a.id IN (SELECT ufp.asset_id FROM asset_people ufp"
        " WHERE ufp.person_id = {} AND ufp.source = 'folder')"
        " AND EXISTS (SELECT 1 FROM face_tracks ft"
        " JOIN face_piles fp ON fp.id = ft.pile_id AND fp.status = 'open'"
        " WHERE ft.asset_id = a.id AND ft.person_id IS NULL)"
    ),
    # What the Importing pane counts as given up on, standing verdicts only.
    "left_out": (
        "a.id IN (SELECT lv.asset_id FROM file_verdicts lv"
        " WHERE lv.product = {} AND lv.transient = 0)"
    ),
    "added_from": "a.added_at >= {}",
    "added_to": "a.added_at <= {}",
    "duration_min": "a.duration_ms >= {}",
    "duration_max": "a.duration_ms <= {}",
    # The shorter side, because "1080p" names the short side of a portrait clip too.
    "height_min": "MIN(a.width, a.height) >= {}",
    "height_max": "MIN(a.width, a.height) <= {}",
    "size_min": "a.size_bytes >= {}",
    "size_max": "a.size_bytes <= {}",
    # Folded; COALESCE keeps a never-probed NULL from dropping the row out of a NOT.
    "vcodec": "LOWER(COALESCE(a.vcodec, '')) = LOWER({})",
    "acodec": "LOWER(COALESCE(a.acodec, '')) = LOWER({})",
    "has_audio": "COALESCE(a.acodec, '') <> ''",
    # Square is its own answer; an unmeasured file answers neither way.
    "orientation": (
        "CASE WHEN a.width IS NULL OR a.height IS NULL THEN NULL"
        " WHEN a.width > a.height THEN 'landscape'"
        " WHEN a.width < a.height THEN 'portrait'"
        " ELSE 'square' END = {}"
    ),
    # HOW A FILE CAME TO BE ENRICHED: one predicate per writer, because a file can be under several.
    "enriched_stash": (
        "(EXISTS (SELECT 1 FROM asset_stash_box_matches sm WHERE sm.asset_id = a.id"
        " AND sm.state = 'applied')"
        " OR EXISTS (SELECT 1 FROM asset_people ep WHERE ep.asset_id = a.id"
        " AND ep.source = 'stash_box')"
        " OR EXISTS (SELECT 1 FROM asset_tags et WHERE et.asset_id = a.id"
        " AND et.source = 'stash_box'))"
    ),
    # Matched or agreed to; a face only proposed has enriched nothing.
    "enriched_faces": (
        "EXISTS (SELECT 1 FROM face_tracks ft WHERE ft.asset_id = a.id"
        " AND ft.person_id IS NOT NULL AND ft.attribution IN ('matched', 'confirmed'))"
    ),
    # WHICH stash-box, by slug. Applied matches only: a person or tag a box wrote carries no box id.
    "enriched_box": (
        "EXISTS (SELECT 1 FROM asset_stash_box_matches sbm"
        " JOIN stash_boxes sbb ON sbb.id = sbm.box_id"
        " WHERE sbm.asset_id = a.id AND sbm.state = 'applied' AND sbb.slug = {})"
    ),
    # A person or a filing a FOLDER NAME gave; a confirmed filing keeps the word. Bracketed because
    # `OR` binds looser than `AND`.
    "enriched_folder": (
        "(EXISTS (SELECT 1 FROM asset_people ep WHERE ep.asset_id = a.id AND ep.source = 'folder')"
        " OR EXISTS (SELECT 1 FROM asset_usernames ea WHERE ea.asset_id = a.id"
        " AND ea.source = 'folder'))"
    ),
    # The file's NAME gave a username: a filing, because one username can be several people.
    "enriched_filename": (
        "EXISTS (SELECT 1 FROM asset_usernames ea WHERE ea.asset_id = a.id"
        " AND ea.source = 'filename')"
    ),
    # A site's mark printed on the PICTURE.
    "enriched_watermark": (
        "EXISTS (SELECT 1 FROM asset_usernames ea WHERE ea.asset_id = a.id"
        " AND ea.source = 'watermark')"
    ),
    # The pictures' metadata named the username behind the number in the file's name.
    "enriched_metadata": (
        "EXISTS (SELECT 1 FROM asset_usernames ea WHERE ea.asset_id = a.id"
        " AND ea.source = 'metadata')"
    ),
    # AcoustID named the file's song.
    "enriched_acoustid": (
        "EXISTS (SELECT 1 FROM song_files ea WHERE ea.asset_id = a.id AND ea.source = 'acoustid')"
    ),
    # A FILE MARKED "DO NOT SWAP"; a swap's offer is the one reader.
    "kept_from_swaps": KEPT_FROM_SWAPS_HERE,
    # One CASE over one column, a file under exactly one value: the facet column's expression
    # (`repository/assets.py`), which reads every ask in `stash_box_scans`.
    "enrichment": (
        "(CASE WHEN " + KEPT_LOCAL_HERE + " THEN 'local'"  # noqa: S608
        " WHEN (SELECT MAX(sbs.scanned_at) FROM stash_box_scans sbs"
        " WHERE sbs.asset_id = a.id) IS NULL THEN 'never'"
        " WHEN (SELECT MAX(sbs.scanned_at) FROM stash_box_scans sbs"
        " WHERE sbs.asset_id = a.id)"
        " >= CAST(strftime('%s', 'now') AS INTEGER) - 86400 THEN 'today'"
        " WHEN (SELECT MAX(sbs.scanned_at) FROM stash_box_scans sbs"
        " WHERE sbs.asset_id = a.id)"
        " >= CAST(strftime('%s', 'now') AS INTEGER) - 604800 THEN 'week'"
        " WHEN (SELECT MAX(sbs.scanned_at) FROM stash_box_scans sbs"
        " WHERE sbs.asset_id = a.id)"
        " >= CAST(strftime('%s', 'now') AS INTEGER) - 2592000 THEN 'month'"
        " ELSE 'older' END) = {}"
    ),
    # WHO MADE THE FILE, the facet column's `FILE_MADE_BY`.
    "created": "(" + FILE_MADE_BY + ") = {}",
    # WHAT THE PEOPLE ON A FILE ARE LIKE, one entry per column because a column cannot be bound.
    # UPPER on both sides: the library holds `BLONDE` and somebody types `blonde`.
    "gender": (
        "a.id IN (SELECT ap.asset_id FROM asset_people ap"
        " JOIN people p ON p.id = ap.person_id WHERE UPPER(p.gender) = UPPER({}))"
    ),
    "hair": (
        "a.id IN (SELECT ap.asset_id FROM asset_people ap"
        " JOIN people p ON p.id = ap.person_id WHERE UPPER(p.hair_color) = UPPER({}))"
    ),
    "eyes": (
        "a.id IN (SELECT ap.asset_id FROM asset_people ap"
        " JOIN people p ON p.id = ap.person_id WHERE UPPER(p.eye_color) = UPPER({}))"
    ),
    "ethnicity": (
        "a.id IN (SELECT ap.asset_id FROM asset_people ap"
        " JOIN people p ON p.id = ap.person_id WHERE UPPER(p.ethnicity) = UPPER({}))"
    ),
    "nationality": (
        "a.id IN (SELECT ap.asset_id FROM asset_people ap"
        " JOIN people p ON p.id = ap.person_id WHERE UPPER(p.country) = UPPER({}))"
    ),
    "breasts": (
        "a.id IN (SELECT ap.asset_id FROM asset_people ap"
        " JOIN people p ON p.id = ap.person_id WHERE UPPER(p.breast_type) = UPPER({}))"
    ),
    # A flag, so it binds nothing and `pmv:no` is its negation.
    "pmv_creator": (
        "a.id IN (SELECT ap.asset_id FROM asset_people ap"
        " JOIN people p ON p.id = ap.person_id WHERE p.pmv_creator = 1)"
    ),
    # The text the facet column groups by, so a row and its filter describe one set.
    "height": (
        "a.id IN (SELECT ap.asset_id FROM asset_people ap"  # noqa: S608
        " JOIN people p ON p.id = ap.person_id WHERE "
        + HEIGHT_BAND.format(col="p.height_cm")
        + " = {})"
    ),
    # Two values: the ends of the span the parser read (`AGE_WITHIN`).
    "age": (
        "a.id IN (SELECT ap.asset_id FROM asset_people ap"  # noqa: S608
        " JOIN people p ON p.id = ap.person_id WHERE "
        + AGE_WITHIN.format(age=AGE_YEARS.format(col="p.birth_date"))
        + ")"
    ),
    # The year, because a facet over exact dates is a column of ones; no date compares false.
    "released": "substr(COALESCE(a.release_date, ''), 1, 4) = {}",
    "container": "LOWER(COALESCE(a.container, '')) = LOWER({})",
    # Either name, the one it arrived with or its current one. Escaped by `like_anywhere`.
    "filename": (
        "(LOWER(COALESCE(a.original_filename, '')) LIKE LOWER({}) ESCAPE '\\'"
        " OR EXISTS (SELECT 1 FROM asset_locations al WHERE al.asset_id = a.id"
        " AND LOWER(COALESCE(al.filename, '')) LIKE LOWER({}) ESCAPE '\\'))"
    ),
    # The name somebody gave it, not its name on disk.
    "title": "LOWER(COALESCE(a.title, '')) LIKE LOWER({}) ESCAPE '\\'",
    # Anywhere, since sites write artist and title in either order.
    "music": "LOWER(COALESCE(a.music, '')) LIKE LOWER({}) ESCAPE '\\'",
    # THE FILES SHARING A SONG WITH ONE FILE, one hop, the group the Same music strip draws. The
    # file bridged through must be visible to this viewer, or a hidden file would be described out
    # of visible ones.
    "same_music": (
        "a.id IN (WITH d(id) AS (SELECT b_id FROM music_pairs WHERE a_id = {}"
        " UNION SELECT a_id FROM music_pairs WHERE b_id = {}),"
        " bridge(id) AS (SELECT d.id FROM d WHERE EXISTS (SELECT 1 FROM viewer_assets va"
        " WHERE va.user_id = :viewer AND va.asset_id = d.id AND va.concealed = 0)),"
        " g(id) AS (SELECT id FROM d"
        " UNION SELECT p.b_id FROM music_pairs p JOIN bridge b ON p.a_id = b.id"
        " UNION SELECT p.a_id FROM music_pairs p JOIN bridge b ON p.b_id = b.id)"
        " SELECT id FROM g WHERE id <> {})"
    ),
    # Uncorrelated, so the index runs once. Named because the relevance ordering binds the same
    # text.
    "text_match": "a.id IN (SELECT asset_id FROM assets_fts WHERE assets_fts MATCH :text_match)",
    # Terms too short for the trigram index, scanned once. COALESCE, or a NULL column would satisfy
    # the term.
    "text_contains": (
        "a.id IN (SELECT f.asset_id FROM assets_fts f WHERE NOT EXISTS ("
        "SELECT 1 FROM json_each(:text_contains) trm"
        " WHERE COALESCE(f.title, '')       NOT LIKE '%' || trm.value || '%' ESCAPE '\\'"
        " AND COALESCE(f.filename, '')    NOT LIKE '%' || trm.value || '%' ESCAPE '\\'"
        " AND COALESCE(f.path, '')        NOT LIKE '%' || trm.value || '%' ESCAPE '\\'"
        " AND COALESCE(f.tags, '')        NOT LIKE '%' || trm.value || '%' ESCAPE '\\'"
        " AND COALESCE(f.people, '')      NOT LIKE '%' || trm.value || '%' ESCAPE '\\'"
        " AND COALESCE(f.usernames, '')    NOT LIKE '%' || trm.value || '%' ESCAPE '\\'"
        " AND COALESCE(f.collections, '') NOT LIKE '%' || trm.value || '%' ESCAPE '\\'"
        " AND COALESCE(f.sites, '')   NOT LIKE '%' || trm.value || '%' ESCAPE '\\'))"
    ),
}

#: How many parameters each template binds, derived so the two cannot disagree.
_ARITY = {key: template.count("{}") for key, template in PREDICATES.items()}

#: The set-valued leaves, derived so a new one cannot bind a bare string into `json_each`.
_SET_VALUED = frozenset(key for key, template in PREDICATES.items() if "json_each({})" in template)


@dataclass(frozen=True, slots=True)
class Where:
    """One condition: which fixed predicate, and what it binds."""

    key: str
    values: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if self.key not in PREDICATES:
            raise ConstraintError(f"{self.key!r} is not a condition")
        expected = _ARITY[self.key]
        # A set-valued leaf binds ONE parameter (the array), however many ids are in it.
        given = 1 if self.key in _SET_VALUED else len(self.values)
        if given != expected:
            raise ConstraintError(f"{self.key!r} binds {expected} values, not {given}")
        if self.key in _SET_VALUED:
            # Deduplicated and ordered, so two ways of asking one question are equal filters.
            ordered = sorted(dict.fromkeys(self.values), key=str)
            object.__setattr__(self, "values", tuple(ordered))
        if self.key == "media_type":
            for value in self.values:
                if value not in MEDIA_TYPES:
                    raise ConstraintError(f"{value!r} is not a kind of media")
        if self.key == "same_music" and not all(
            isinstance(value, str) and is_id(value) for value in self.values
        ):
            raise ConstraintError("same_music takes the ID of one file")


@dataclass(frozen=True, slots=True)
class AllOf:
    """Every one of these holds. Empty means "no constraint", which is the plain grid."""

    parts: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class AnyOf:
    """At least one of these holds. Empty means NOTHING holds (see the module docstring)."""

    parts: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class Not:
    """The opposite of what is inside it."""

    part: Node


Node = Where | AllOf | AnyOf | Not


# --- one filing, as an address ----------------------------------------------------------------
#
# What a History line's count writes into the Files wall's address; here because the History and the
# search may not import each other.

#: The three parameters and the leaves answering each, plain and with a box.
FILING_PARAMETERS: dict[str, tuple[str, str]] = {
    "filed": ("filed_under", "filed_under_box"),
    "tagged": ("tagged_with", "tagged_with_box"),
    "named": ("named_as", "named_as_box"),
}

#: The source a stash-box writes, and the only one a filing's box is asked of.
BOX_SOURCE = "stash_box"

_FILING_SEPARATOR = "~"
_FILING_SUBJECT = re.compile(r"^[^~\s]{1,200}$")
_FILING_SOURCE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_FILING_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EPOCH = date(1970, 1, 1)


@dataclass(frozen=True, slots=True)
class Filing:
    """One History line's group: `<id>~<source>~<day>`, with `~<box>` where a stash-box did it.

    An empty source, day or box is a real line with its own count. The day is the History's local
    day.
    """

    parameter: str
    subject: str
    source: str | None
    day: int | None
    box: str | None = None

    def __post_init__(self) -> None:
        if self.parameter not in FILING_PARAMETERS:
            raise ConstraintError(f"{self.parameter!r} is not a filing")
        if not _FILING_SUBJECT.match(self.subject):
            raise ConstraintError("a filing names one thing by its id")
        if self.source is not None and not _FILING_SOURCE.match(self.source):
            raise ConstraintError(f"{self.source!r} is not a source")
        if self.box is not None and (self.source != BOX_SOURCE or not self.box.strip()):
            raise ConstraintError("only a stash-box filing names a box")

    @property
    def value(self) -> str:
        """The spelling in an address. `read` of this is this filing again."""
        day = "" if self.day is None else (_EPOCH + timedelta(days=self.day)).isoformat()
        parts = [self.subject, self.source or "", day]
        if self.box is not None:
            parts.append(self.box)
        return _FILING_SEPARATOR.join(parts)

    @classmethod
    def read(cls, parameter: str, value: str) -> Filing | None:
        """The filing an address names, or None, which the caller answers with matching nothing."""
        parts = value.split(_FILING_SEPARATOR, 3)
        if len(parts) < 3:
            return None
        subject, source, day = parts[0], parts[1], parts[2]
        box = parts[3] if len(parts) == 4 else None
        number: int | None = None
        if day:
            if not _FILING_DATE.match(day):
                return None
            try:
                number = (date.fromisoformat(day) - _EPOCH).days
            except ValueError:
                return None
        try:
            return cls(parameter, subject, source or None, number, box)
        except ConstraintError:
            return None

    @property
    def where(self) -> Where:
        """The condition: exactly the rows this line counted, inside the viewer's own scope."""
        plain, boxed = FILING_PARAMETERS[self.parameter]
        if self.source == BOX_SOURCE:
            return Where(boxed, (self.subject, self.day, self.box))
        return Where(plain, (self.subject, self.source, self.day))


# --- the filter -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssetFilter:
    """Everything a query asked for, resolved to values and arranged as a tree."""

    #: The tree. `All(())` constrains nothing.
    where: Node = field(default_factory=AllOf)

    #: Also kept beside the tree for the relevance ordering, which binds the same `:text_match`.
    text: str | None = None

    #: Every folder group named in the tree, expanded once by the read.
    folder_scope: Groups = ()

    #: One depth for the filter: only a control asks for one, naming one folder.
    folder_depth: FolderDepth = FolderDepth.SUBTREE

    #: Nearest files to a search by meaning, closest first; ordering only, handed in as data.
    neighbours: tuple[tuple[str, float], ...] = ()

    #: The shortest video this user keeps a place in, or None. On the filter because the access
    #: layer must not read settings.
    resume_min_ms: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "folder_scope", _normalised(self.folder_scope))

        # Refused rather than defaulted: the default is the wider answer.
        try:
            object.__setattr__(self, "folder_depth", FolderDepth(self.folder_depth))
        except ValueError:
            raise ConstraintError(f"{self.folder_depth!r} is not a folder depth") from None

        text = None if self.text is None else " ".join(self.text.split())
        object.__setattr__(self, "text", text or None)
        if (
            self.text is not None
            and fts_match(self.text) is None
            and fts_contains(self.text) is None
        ):
            raise ConstraintError("that text contains nothing anything could be matched by")

    def also(self, extra: Node) -> AssetFilter:
        """This filter with one more condition every row has to meet. Everything else (the
        words, the folder scope, the neighbours) is carried as it was."""
        return replace(self, where=AllOf((self.where, extra)))

    def predicate(self) -> tuple[str, dict[str, object]]:
        """The filter as one SQL expression and its parameters, for one conjunct of a fixed AND
        chain."""
        emitter = _Emitter()
        sql = emitter.emit(self.where)
        bound: dict[str, object] = dict(emitter.bound)
        match = fts_match(self.text)
        bound["text_match"] = match
        bound["text_contains"] = fts_contains(self.text)
        bound["folder_ids_groups"] = _json(self.folder_scope)
        # Always bound: the recursive arm of `in_scope` reads it.
        bound["folder_depth_direct"] = 1 if self.folder_depth is FolderDepth.DIRECT else 0
        # Always bound: the `viewed` facet reads it too.
        bound["resume_min_ms"] = self.resume_min_ms
        # A list, so an empty one keeps a NULL away from FTS5.
        bound["text_rank"] = None if match is None else json.dumps([match])
        # NULL when there is nothing to order by, so every file ties.
        bound["semantic_rank"] = (
            json.dumps([[asset_id, distance] for asset_id, distance in self.neighbours])
            if self.neighbours
            else None
        )
        return sql, bound


class _Emitter:
    """Walks a tree and writes it out, naming each parameter it binds."""

    def __init__(self) -> None:
        self.bound: dict[str, object] = {}
        self._next = 0

    def _name(self, value: object) -> str:
        name = f"p{self._next}"
        self._next += 1
        self.bound[name] = value
        return f":{name}"

    def emit(self, node: Node) -> str:
        if isinstance(node, Where):
            template = PREDICATES[node.key]
            if node.key in _SET_VALUED:
                return template.format(self._name(json.dumps(list(node.values))))
            return template.format(*(self._name(value) for value in node.values))
        if isinstance(node, Not):
            return f"NOT {self.emit(node.part)}"
        # Literals, since empty parentheses are not SQL; ANY of nothing must never mean everything.
        joiner = " AND " if isinstance(node, AllOf) else " OR "
        if not node.parts:
            return "1" if isinstance(node, AllOf) else "0"
        return "(" + joiner.join(self.emit(part) for part in node.parts) + ")"


#: What an emitted filter may be made of, read by the test that proves it.
CONNECTORS = ("(", ")", " AND ", " OR ", "NOT ", "1", "0")

#: A generated parameter name, which is the only thing ever substituted into a template.
PARAMETER = re.compile(r":p\d+")


def _normalised(values: Iterable[Sequence[str]]) -> Groups:
    """Groups with each one's members deduplicated and ordered, for stable equality."""
    return tuple(tuple(sorted(dict.fromkeys(group))) for group in values)


def _json(groups: Groups) -> str | None:
    """The one folder parameter, or None when no folder was named."""
    if not groups:
        return None
    return json.dumps([list(group) for group in groups])


#: The empty filter, one object everywhere.
NO_FILTER = AssetFilter()

#: The filter nothing satisfies: a query that could not mean anything must never widen.
MATCHES_NOTHING = AssetFilter(where=AnyOf())


def fts_match(text: str | None) -> str | None:
    """Free text as an FTS5 match expression with nothing FTS5 reads as syntax.

    Each term is a quoted literal; short terms go to `fts_contains`, since dropping one would widen
    the search. Control characters go first: FTS5 reads a C string, so a NUL ends it whatever the
    quoting.
    """
    if text is None:
        return None
    terms = [term for term in _terms(text) if len(term) >= MIN_TEXT_TERM]
    if not terms:
        return None
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)


def fts_contains(text: str | None) -> str | None:
    """The terms too short for the index, as the array the LIKE scan binds, with `%` and `_`
    escaped."""
    if text is None:
        return None
    terms = [_like(term) for term in _terms(text) if len(term) < MIN_TEXT_TERM]
    if not terms:
        return None
    return json.dumps(terms)


def _terms(text: str) -> list[str]:
    """The words of a query, with everything that is not text taken out of them."""
    return [cleaned for cleaned in (_printable(term) for term in text.split()) if cleaned]


def _like(term: str) -> str:
    """A term as a LIKE pattern body, with the wildcards made literal."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


#: The read side of `kernel/text.py`'s rule, so nothing stored is stripped from a query.
_printable = printable


def title_filter(word: str) -> AssetFilter:
    """Files whose title contains a word."""
    return AssetFilter(where=Where("title", (like_anywhere(word.strip()),)))


def music_filter(word: str) -> AssetFilter:
    """Files whose music field, their song's name, contains a word."""
    return AssetFilter(where=Where("music", (like_anywhere(word.strip()),)))


def same_music_where(asset_id: str) -> Where:
    """The files sharing a song with this one (`same_music:<id>`), the id bound once per mention."""
    return Where("same_music", (asset_id.strip(),) * _ARITY["same_music"])


def like_anywhere(term: str) -> str:
    """A term as a LIKE pattern matching anywhere, with its wildcards escaped."""
    return f"%{_like(term)}%"


def clamp_rating(value: int) -> int:
    """A star count that the schema can hold. `rating:9+` means the top ones, not an error."""
    return max(MIN_RATING, min(MAX_RATING, value))
