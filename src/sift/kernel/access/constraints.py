# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a query filtered to, in the only form the scoped read will accept.

Applied INSIDE the statement that decides visibility, so totals describe the right set. A filter is
a bounded tree (ALL, ANY, NOT) of fixed templates whose values all bind, so it can only narrow what
the permission rule allowed. A group that resolved to nothing
matches nothing: dropping it would widen the search past a name the viewer may not be told about.
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
from sift.kernel.access.entity_facets import read_pick as read_pick
from sift.kernel.access.filter_parts import AGE_WITHIN as AGE_WITHIN
from sift.kernel.access.filter_parts import AGE_YEARS as AGE_YEARS
from sift.kernel.access.filter_parts import FILE_MADE_BY as FILE_MADE_BY
from sift.kernel.access.filter_parts import HEIGHT_BAND as HEIGHT_BAND
from sift.kernel.access.filter_parts import KEPT_FROM_SWAPS_FILES as KEPT_FROM_SWAPS_FILES
from sift.kernel.access.filter_parts import KEPT_FROM_SWAPS_HERE as KEPT_FROM_SWAPS_HERE
from sift.kernel.access.filter_parts import KEPT_LOCAL_FILES as KEPT_LOCAL_FILES
from sift.kernel.access.filter_parts import KEPT_LOCAL_HERE as KEPT_LOCAL_HERE
from sift.kernel.access.filter_parts import ConstraintError as ConstraintError
from sift.kernel.access.sites import SITE_CONCEALED, SITE_REACH
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
    """How far below a named folder `in:` reaches; `DIRECT`, sent by a control, only asks less."""

    #: The named folder and everything beneath it.
    SUBTREE = "subtree"
    #: The named folder itself, and nothing below it.
    DIRECT = "direct"


#: `history.BOX_OF_A_ROW`, not importable here; `test_the_filing_box_is_the_history_box` holds it.
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
_DAY = " AND unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 IS {}"
_ON_DAY = " AND link.source IS {}" + _DAY
_BY_BOX = " AND link.source = 'stash_box'" + _DAY + " AND " + _BOX_OF_THE_ROW + " IS {}"
_FILED = "usernames ac ON ac.id = link.username_id"


# --- the leaves -------------------------------------------------------------------------------

#: Who may see every file, so the word index may answer them over the whole library.
SEES_EVERY_FILE = "u.id = :viewer AND u.role = 'admin'"

_WORD_COLUMNS = (
    "title",
    "filename",
    "path",
    "tags",
    "people",
    "usernames",
    "collections",
    "sites",
    "music",
)
_LIKE_TERM = "LIKE '%' || trm.value || '%' ESCAPE '\\'"


def _no_column(test: str) -> str:
    """No column of `f` passes `test` for `trm`; NULL reads as empty, or it would pass."""
    return " AND ".join(f"COALESCE(f.{column}, '') NOT {test}" for column in _WORD_COLUMNS)


def _words_in(admin_arm: str, asked: str, *tests: tuple[str, str]) -> str:
    """Files holding every term, read from the viewer's own rows unless they see every file."""
    each = "".join(
        f" AND NOT EXISTS (SELECT 1 FROM json_each({terms}) trm WHERE {_no_column(test)})"  # noqa: S608
        for terms, test in tests
    )
    return (
        "a.id IN (SELECT f.asset_id FROM users u CROSS JOIN assets_fts f"  # noqa: S608
        f" WHERE {SEES_EVERY_FILE} AND {admin_arm}"
        " UNION ALL SELECT sv.asset_id FROM users u CROSS JOIN viewer_assets sv"
        " JOIN assets_fts_rows r ON r.asset_id = sv.asset_id"
        " JOIN assets_fts f ON f.rowid = r.fts_rowid"
        f" WHERE u.id = :viewer AND NOT ({SEES_EVERY_FILE}) AND sv.user_id = :viewer"
        f" AND {asked} IS NOT NULL{each})"
    )


def _seen(link: str, test: str, *joins: str) -> str:
    """Files whose `link` row passes `test`: any for an admin, else only the viewer's own, the test
    tied to each row so no list is built from it over hidden files."""
    alias = link.split()[-1]
    numbers = iter(range(test.count("{}")))
    test = re.sub(r"\{\}", lambda _: f"{{{next(numbers)}}}", test)
    plain = "".join(f" JOIN {join}" for join in joins)
    pinned = "".join(f" CROSS JOIN {join}" for join in joins)
    return (
        f"a.id IN (SELECT {alias}.asset_id FROM users u CROSS JOIN {link}{plain}"  # noqa: S608
        f" WHERE {SEES_EVERY_FILE} AND {test} UNION ALL SELECT sv.asset_id FROM users u"
        f" CROSS JOIN viewer_assets sv CROSS JOIN {link} ON {alias}.asset_id = sv.asset_id{pinned}"
        f" WHERE u.id = :viewer AND NOT ({SEES_EVERY_FILE}) AND sv.user_id = :viewer"
        f" AND CASE WHEN {alias}.asset_id = sv.asset_id THEN {test} END)"
    )


def _bridged(near: str, far: str) -> str:
    """A visible file paired with this row and the named one."""
    return (
        " OR EXISTS (SELECT 1 FROM music_pairs h CROSS JOIN viewer_assets vb"  # noqa: S608
        f" ON vb.user_id = :viewer AND vb.asset_id = h.{far} AND vb.concealed = 0"
        f" CROSS JOIN music_pairs hx ON hx.a_id = MIN(h.{far}, {{2}})"
        f" AND hx.b_id = MAX(h.{far}, {{2}}) WHERE h.{near} = sv.asset_id)"
    )


def _person(test: str) -> str:
    return _seen("asset_people ap", test, "people p ON p.id = ap.person_id")


#: Each presence dimension's link to a file, aliased `pl`, and the kind of thing it names.
PRESENCE_LINKS = {
    "tags": ("asset_tags pl", "tag"),
    "people": ("asset_people pl", "person"),
    "sites": ("asset_usernames pl JOIN usernames pu ON pu.id = pl.username_id", "site"),
    "collections": ("collection_items pl", "collection"),
    "photo_sets": ("photo_set_items pl", "photo_set"),
    "songs": ("song_files pl", "song"),
}


def only_shown(dimension: str) -> str:
    """Not a file whose every `dimension` this viewer hides; only a concealed file can be one."""
    link, kind = PRESENCE_LINKS[dimension]
    hidden = (
        SITE_CONCEALED.format(site="pu.site_id")
        if kind == "site"
        else f"EXISTS (SELECT 1 FROM {kind}_user_state h WHERE h.{kind}_id = pl.{kind}_id"  # noqa: S608
        " AND h.user_id = :viewer AND h.hidden = 1)"
    )
    return (
        "(:reveal_named = 1 OR :reveal = 0 OR a.id NOT IN (SELECT pc.asset_id FROM viewer_assets"  # noqa: S608
        " pc WHERE pc.user_id = :viewer AND pc.concealed = 1 AND NOT EXISTS (SELECT 1 FROM"
        f" {link} WHERE pl.asset_id = pc.asset_id AND NOT {hidden})))"
    )


# Every condition a filter can express, written once; `{}` (or `{0}`, read twice) is the only
# substitution. Membership is `a.id IN (SELECT ...)` so the planner drives the read from the
# members. Every `noqa: S608` splices a constant, never a value.
PREDICATES: dict[str, str] = {
    # A TAG TAKES IN ITS BRANCH: the files carrying it or any tag filed under it (`tag_tree.py`).
    "tags": _seen("asset_tags t", "t.tag_id IN (" + TAGS_UNDER + ")"),
    "people": _seen("asset_people ap", "ap.person_id IN (SELECT value FROM json_each({}))"),
    # One username by id, for the Files wall's `?username=`; never a query word.
    "usernames": _seen("asset_usernames aa", "aa.username_id IN (SELECT value FROM json_each({}))"),
    # A network reaches its labels' files, by the visibility rules' fragment (`sites.py`).
    "sites": _seen(
        "asset_usernames aa",
        "reach.ancestor_id IN (SELECT value FROM json_each({}))",
        "usernames ac ON ac.id = aa.username_id",
        "(" + SITE_REACH + ") reach ON reach.site_id = ac.site_id",
    ),
    "collections": _seen(
        "collection_items ci", "ci.collection_id IN (SELECT value FROM json_each({}))"
    ),
    "photo_sets": _seen(
        "photo_set_items psi", "psi.photo_set_id IN (SELECT value FROM json_each({}))"
    ),
    "songs": _seen("song_files sf", "sf.song_id IN (SELECT value FROM json_each({}))"),
    # Ids a caller holds with no question to ask instead, filtered inside the resolve.
    "assets": "a.id IN (SELECT value FROM json_each({}))",
    # ONE FILING, as a History line counts it (`history_entity._SITE_FILES`): the source and the
    # local day by `IS`, as NULL is a real value of each. The `_box` arms add which box.
    "filed_under": _seen("asset_usernames link", "ac.site_id = {}" + _ON_DAY, _FILED),
    "filed_under_box": _seen("asset_usernames link", "ac.site_id = {}" + _BY_BOX, _FILED),
    "tagged_with": _seen("asset_tags link", "link.tag_id = {}" + _ON_DAY),
    "tagged_with_box": _seen("asset_tags link", "link.tag_id = {}" + _BY_BOX),
    "named_as": _seen("asset_people link", "link.person_id = {}" + _ON_DAY),
    "named_as_box": _seen("asset_people link", "link.person_id = {}" + _BY_BOX),
    # Matched against the expanded subtree (`in_scope`); any copy inside matches.
    "folder": _seen("asset_locations l", "s.grp = {}", "in_scope s ON s.folder_id = l.folder_id"),
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
    "viewed": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.last_viewed_at IS NOT NULL)"
    ),
    # Viewed, or holding a place, which a short sitting writes alone. What `viewed:none` negates.
    "opened": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"  # noqa: S608
        " AND s.user_id = :viewer AND (s.last_viewed_at IS NOT NULL OR "
        + RESUMING.format(
            duration="a.duration_ms", position="s.resume_ms", minimum=":resume_min_ms"
        )
        + "))"
    ),
    # Seen through once: watch time counts a minute watched sixty times as an hour.
    "finished": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.completed_at IS NOT NULL)"
    ),
    "started": (
        "EXISTS (SELECT 1 FROM asset_user_state s WHERE s.asset_id = a.id"
        " AND s.user_id = :viewer AND s.last_viewed_at IS NOT NULL AND s.completed_at IS NULL)"
    ),
    # Part-way through, by the player's `RESUMING`; false for every row while resuming is off.
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
    # Made on THIS FILE: a restricted root would put the whole library under `sharing:restricted`.
    "shared_here": (
        "EXISTS (SELECT 1 FROM acl_grants g WHERE g.object_type = 'item'"
        " AND g.object_id = a.id AND g.effect = 'share')"
    ),
    "restricted_here": (
        "EXISTS (SELECT 1 FROM acl_grants g WHERE g.object_type = 'item'"
        " AND g.object_id = a.id AND g.effect = 'restrict')"
    ),
    # Presence of a thing this viewer is shown. The negations are the curation queues.
    **{
        f"has_{name}": f"(EXISTS (SELECT 1 FROM {link} WHERE pl.asset_id = a.id)"  # noqa: S608
        f" AND {only_shown(name)})"
        for name, (link, _) in PRESENCE_LINKS.items()
    },
    # No `loops:` value filter: a loop is a piece of the file, not a thing it is in.
    "has_loops": "EXISTS (SELECT 1 FROM loops lp WHERE lp.asset_id = a.id)",
    # A face nobody named, outside a group somebody set aside: dismissed work must not come back.
    "has_unnamed_face": (
        "EXISTS (SELECT 1 FROM face_tracks ft"
        " JOIN face_piles fp ON fp.id = ft.pile_id AND fp.status = 'open'"
        " WHERE ft.asset_id = a.id AND ft.person_id IS NULL)"
    ),
    # A folder's name filed it under one person, with a face nobody named (`has_unnamed_face`).
    "unnamed_face": _seen("asset_people ufp", "ufp.person_id = {} AND ufp.source = 'folder'")  # noqa: S608
    + (
        " AND EXISTS (SELECT 1 FROM face_tracks ft"
        " JOIN face_piles fp ON fp.id = ft.pile_id AND fp.status = 'open'"
        " WHERE ft.asset_id = a.id AND ft.person_id IS NULL)"
    ),
    # What the Importing pane counts as given up on, standing verdicts only.
    "left_out": _seen("file_verdicts lv", "lv.product = {} AND lv.transient = 0"),
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
    # A person or a filing a FOLDER NAME gave, confirmed or not; bracketed, as `OR` binds loosely.
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
    "enriched_acoustid": (
        "EXISTS (SELECT 1 FROM song_files ea WHERE ea.asset_id = a.id AND ea.source = 'acoustid')"
    ),
    # A FILE MARKED "DO NOT SWAP"; a swap's offer is the one reader.
    "kept_from_swaps": KEPT_FROM_SWAPS_HERE,
    # The facet column's CASE, a file under exactly one value, over every ask in `stash_box_scans`.
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
    "created": "(" + FILE_MADE_BY + ") = {}",
    # WHAT THE PEOPLE ON A FILE ARE LIKE: an entry per column, as none binds; UPPER on both sides.
    "gender": _person("UPPER(p.gender) = UPPER({})"),
    "hair": _person("UPPER(p.hair_color) = UPPER({})"),
    "eyes": _person("UPPER(p.eye_color) = UPPER({})"),
    "ethnicity": _person("UPPER(p.ethnicity) = UPPER({})"),
    "nationality": _person("UPPER(p.country) = UPPER({})"),
    "breasts": _person("UPPER(p.breast_type) = UPPER({})"),
    # A flag, so it binds nothing and `pmv:no` is its negation.
    "pmv_creator": _person("p.pmv_creator = 1"),
    # The text the facet column groups by, so a row and its filter describe one set.
    "height": _person(HEIGHT_BAND.format(col="p.height_cm") + " = {}"),
    # Two values: the ends of the span the parser read (`AGE_WITHIN`).
    "age": _person(AGE_WITHIN.format(age=AGE_YEARS.format(col="p.birth_date"))),
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
    # THE FILES SHARING A SONG WITH ONE FILE, one hop, as the Same music strip draws them; the file
    # bridged through must be visible, or a hidden file would be described out of visible ones.
    "same_music": (
        "a.id IN (WITH d(id) AS (SELECT b_id FROM music_pairs WHERE a_id = {0}"  # noqa: S608
        " UNION SELECT a_id FROM music_pairs WHERE b_id = {1}),"
        " bridge(id) AS (SELECT d.id FROM d WHERE EXISTS (SELECT 1 FROM viewer_assets va"
        " WHERE va.user_id = :viewer AND va.asset_id = d.id AND va.concealed = 0)),"
        " g(id) AS (SELECT id FROM d"
        " UNION SELECT p.b_id FROM music_pairs p JOIN bridge b ON p.a_id = b.id"
        " UNION SELECT p.a_id FROM music_pairs p JOIN bridge b ON p.b_id = b.id)"
        f" SELECT g.id FROM users u CROSS JOIN g WHERE {SEES_EVERY_FILE} AND g.id <> {{2}}"
        " UNION ALL SELECT sv.asset_id FROM users u CROSS JOIN viewer_assets sv WHERE u.id = :viewer"
        f" AND NOT ({SEES_EVERY_FILE}) AND sv.user_id = :viewer AND sv.asset_id <> {{2}}"
        " AND (EXISTS (SELECT 1 FROM music_pairs mx WHERE mx.a_id = MIN(sv.asset_id, {2})"
        " AND mx.b_id = MAX(sv.asset_id, {2}))"
        + _bridged("a_id", "b_id")
        + _bridged("b_id", "a_id")
        + "))"
    ),
    # Named because the relevance ordering binds the same text.
    "text_match": _words_in(
        "f.assets_fts MATCH :text_match",
        ":text_match",
        (":text_likes", _LIKE_TERM),
        (":text_globs", "GLOB trm.value"),
    ),
    # Terms too short for the trigram index, scanned.
    "text_contains": _words_in(
        f"NOT EXISTS (SELECT 1 FROM json_each(:text_contains) trm WHERE {_no_column(_LIKE_TERM)})",  # noqa: S608
        ":text_contains",
        (":text_contains", _LIKE_TERM),
    ),
}

#: How many parameters each template binds, derived so the two cannot disagree.
_ARITY = {
    key: len(set(re.findall(r"\{(\d+)\}", t))) or t.count("{}") for key, t in PREDICATES.items()
}

#: The set-valued leaves, derived so a new one cannot bind a bare string into `json_each`.
_SET_VALUED = frozenset(
    key for key, t in PREDICATES.items() if re.search(r"json_each\(\{0?\}\)", t)
)


@dataclass(frozen=True, slots=True)
class Where:
    """One condition: which fixed predicate, and what it binds."""

    key: str
    values: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if self.key not in PREDICATES:
            raise ConstraintError(f"{self.key!r} is not a condition")
        expected = _ARITY[self.key]
        # A set-valued leaf binds ONE parameter, the array.
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
    """Every one of these holds; empty constrains nothing."""

    parts: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class AnyOf:
    """At least one of these holds; empty, NOTHING does (see the module docstring)."""

    parts: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class Not:
    """The opposite of what is inside it."""

    part: Node


Node = Where | AllOf | AnyOf | Not


# --- one filing, a History line's count, as an address (History and search import neither) ---

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
    An empty source, day or box is a real line with its own count; the day is the local one."""

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

    #: The shortest video this user keeps a place in, or None; the access layer reads none.
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
        """This filter with one more condition every row has to meet."""
        return replace(self, where=AllOf((self.where, extra)))

    def predicate(self) -> tuple[str, dict[str, object]]:
        """The filter as one SQL expression and its parameters, one conjunct of a fixed AND chain."""
        emitter = _Emitter()
        sql = emitter.emit(self.where)
        bound: dict[str, object] = dict(emitter.bound)
        match = fts_match(self.text)
        bound["text_match"] = match
        bound["text_contains"] = fts_contains(self.text)
        bound["text_likes"], bound["text_globs"] = fts_patterns(self.text)
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


NO_FILTER = AssetFilter()

#: The filter nothing satisfies: a query that could not mean anything must never widen.
MATCHES_NOTHING = AssetFilter(where=AnyOf())


def fts_match(text: str | None) -> str | None:
    """Free text as an FTS5 match of quoted literals; short terms go to `fts_contains`, as dropping
    one would widen the search, and control characters go, as a NUL ends FTS5's string."""
    if text is None:
        return None
    terms = [term for term in _terms(text) if len(term) >= MIN_TEXT_TERM]
    if not terms:
        return None
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)


def fts_contains(text: str | None) -> str | None:
    """The terms too short for the index, as LIKE bodies with their wildcards escaped."""
    if text is None:
        return None
    terms = [_like(term) for term in _terms(text) if len(term) < MIN_TEXT_TERM]
    if not terms:
        return None
    return json.dumps(terms)


def fts_patterns(text: str | None) -> tuple[str | None, str | None]:
    """`fts_match`'s terms folded as the index folds: LIKE where every case is ASCII, else GLOB."""
    terms = [term for term in _terms(text or "") if len(term) >= MIN_TEXT_TERM]
    likes = [_like(term) for term in terms if all(_cases(one).isascii() for one in term)]
    globs = [
        "*" + "".join(_glob_char(one) for one in term) + "*"
        for term in terms
        if not all(_cases(one).isascii() for one in term)
    ]
    return (json.dumps(likes) if likes else None, json.dumps(globs) if globs else None)


#: Capitals whose lowercase is a common letter but which `upper()` never gives back.
_OTHER_CAPITALS = {
    "k": "\u212a",
    "\u00e5": "\u212b",
    "\u03c9": "\u2126",
    "\u03b8": "\u03f4",
    "\u00df": "\u1e9e",
}


def _cases(char: str) -> str:
    """Every case of a character."""
    lower = char.lower() if len(char.lower()) == 1 else char
    cases = {lower, lower.upper(), lower.title(), _OTHER_CAPITALS.get(lower, lower)}
    return "".join(sorted(one for one in cases if len(one) == 1 and one.lower() == lower)) or char


def _glob_char(char: str) -> str:
    """A character as GLOB reads it, every case of it allowed."""
    cases = _cases(char)
    return f"[{cases}]" if len(cases) > 1 or char in "*?[" else char


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
    """The files sharing a song with this one (`same_music:<id>`)."""
    return Where("same_music", (asset_id.strip(),) * _ARITY["same_music"])


def like_anywhere(term: str) -> str:
    """A term as a LIKE pattern matching anywhere, with its wildcards escaped."""
    return f"%{_like(term)}%"


def clamp_rating(value: int) -> int:
    """A star count that the schema can hold. `rating:9+` means the top ones, not an error."""
    return max(MIN_RATING, min(MAX_RATING, value))
