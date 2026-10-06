# SPDX-License-Identifier: AGPL-3.0-or-later
"""The facets under the filter bar: what each dimension joins and groups by, and how a value
whose own row is concealed is kept out of the counts."""

from __future__ import annotations

from sift.kernel.access.constraints import (
    AGE_YEARS,
    FILE_MADE_BY,
    HEIGHT_BAND,
    KEPT_LOCAL_HERE,
    only_shown,
)
from sift.kernel.access.sites import SITE_CONCEALED
from sift.kernel.content.identity import VerdictProduct
from sift.kernel.content.user_state import RESUMING
from sift.kernel.db import point_read

#: Dimensions only an admin may ask about. Not about access (every file counted is one the asker
#: may see): `sharing` describes the decisions of the person running Sift, not the media.
ADMIN_FACETS = frozenset({"sharing"})


#: What each facet column joins and groups by, chosen by a validated key; every value is the query
#: language's own, so a row's click finds exactly what it counted. Aliases start `fx`.
FACETS: dict[str, tuple[str, str]] = {
    "tags": (
        "\n  JOIN asset_tags fxa ON fxa.asset_id = a.id"
        "\n  JOIN tags fx ON fx.id = fxa.tag_id"
        # A vaulted tag comes off the list entirely: a name listed with a zero beside it still
        # names the thing being hidden.
        "\n  LEFT JOIN tag_user_state fxh ON fxh.tag_id = fx.id AND fxh.user_id = :viewer",
        "fx.name",
    ),
    "people": (
        "\n  JOIN asset_people fxa ON fxa.asset_id = a.id"
        "\n  JOIN people fx ON fx.id = fxa.person_id"
        "\n  LEFT JOIN person_user_state fxh ON fxh.person_id = fx.id AND fxh.user_id = :viewer",
        "fx.id",
    ),
    # No `fxh` join: a Site is concealed by anything above it as well, so `_CONCEALED_SITE` walks
    # the chain rather than reading one row.
    "sites": (
        "\n  JOIN asset_usernames fxa ON fxa.asset_id = a.id"
        "\n  JOIN usernames fxc ON fxc.id = fxa.username_id"
        "\n  JOIN sites fx ON fx.id = fxc.site_id",
        "fx.name",
    ),
    "collections": (
        "\n  JOIN collection_items fxa ON fxa.asset_id = a.id"
        "\n  JOIN collections fx ON fx.id = fxa.collection_id"
        "\n  LEFT JOIN collection_user_state fxh ON fxh.collection_id = fx.id"
        " AND fxh.user_id = :viewer",
        "fx.name",
    ),
    "photo_sets": (
        "\n  JOIN photo_set_items fxa ON fxa.asset_id = a.id"
        "\n  JOIN photo_sets fx ON fx.id = fxa.photo_set_id"
        "\n  LEFT JOIN photo_set_user_state fxh ON fxh.photo_set_id = fx.id"
        " AND fxh.user_id = :viewer",
        "fx.name",
    ),
    "songs": (
        "\n  JOIN song_files fxa ON fxa.asset_id = a.id\n  JOIN songs fx ON fx.id = fxa.song_id"
        "\n  LEFT JOIN song_user_state fxh ON fxh.song_id = fx.id AND fxh.user_id = :viewer",
        "fx.name",
    ),
    # By the folder's NAME, not its path, because the name is what `in:` reads back: two "2024"
    # folders are one row, as `in:2024` is one filter. A file in two places counts under each.
    "in": (
        "\n  JOIN asset_locations fxl ON fxl.asset_id = a.id"
        "\n  JOIN folders fx ON fx.id = fxl.folder_id"
        "\n  LEFT JOIN folder_user_state fxh ON fxh.folder_id = fx.id AND fxh.user_id = :viewer",
        "fx.name",
    ),
    # A column, or a band computed from one. No sound is the filter `acodec:none`, not a codec.
    "media": ("", "a.media_type"),
    "filetype": ("", "LOWER(a.container)"),
    "vcodec": ("", "LOWER(a.vcodec)"),
    "acodec": ("", "LOWER(a.acodec)"),
    # The track a file is set to, as a name: not lowered, and a locked tile counts under none, as
    # its track can name a hidden song.
    "music": ("", "CASE WHEN :reveal_named = 1 OR v.concealed = 0 THEN a.music END"),
    # What was decided on THIS FILE, the question `sharing:` asks of the same table. A file shared
    # with one user and withheld from another is under both rows; `COUNT(DISTINCT a.id)` counts it
    # once in each. Admin-only, at the route (`ADMIN_FACETS`).
    "sharing": (
        "\n  JOIN acl_grants fx ON fx.object_type = 'item' AND fx.object_id = a.id"
        " AND fx.effect IN ('share', 'restrict')",
        "CASE fx.effect WHEN 'share' THEN 'shared' ELSE 'restricted' END",
    ),
    # The O counter, per user, one row per count (`o_count:3` is the filter). A LEFT join: a file
    # this user never touched has no state row and counts under nought.
    "o_count": (
        "\n  LEFT JOIN asset_user_state fxo ON fxo.asset_id = a.id AND fxo.user_id = :viewer",
        "CAST(COALESCE(fxo.o_count, 0) AS TEXT)",
    ),
    # Stars are per user: the asking viewer's own row and nobody else's.
    "rating": (
        "\n  JOIN asset_user_state fx ON fx.asset_id = a.id AND fx.user_id = :viewer"
        " AND fx.rating IS NOT NULL",
        "CAST(fx.rating AS TEXT)",
    ),
    # Opened or not, per user, off `last_viewed_at` as `viewed:` reads it. A LEFT join, because
    # "not opened" is the absence of a state row. Three states and a file is in exactly one:
    # `none` never touched, `continue` part-way through (the player's own resume rule, from
    # `user_state`, first in the ladder), `done` opened with nowhere to go back to. `done` and not
    # `yes`, because `yes` is the wider word and the row would not return what it counted. With
    # resuming off, `:resume_min_ms` binds NULL and the column reads two values.
    "viewed": (
        "\n  LEFT JOIN asset_user_state fxv ON fxv.asset_id = a.id AND fxv.user_id = :viewer",
        "CASE WHEN "
        + RESUMING.format(
            duration="a.duration_ms", position="fxv.resume_ms", minimum=":resume_min_ms"
        )
        + " THEN 'continue'"
        " WHEN fxv.last_viewed_at IS NOT NULL THEN 'done' ELSE 'none' END",
    ),
    # Bands rather than numbers, where no two files share a value.
    "resolution": (
        "",
        "CASE WHEN MIN(a.width, a.height) IS NULL THEN NULL"
        " WHEN MIN(a.width, a.height) >= 4320 THEN '8k'"
        " WHEN MIN(a.width, a.height) >= 2160 THEN '4k'"
        " WHEN MIN(a.width, a.height) >= 1440 THEN '1440p'"
        " WHEN MIN(a.width, a.height) >= 1080 THEN '1080p'"
        " WHEN MIN(a.width, a.height) >= 720 THEN '720p'"
        " WHEN MIN(a.width, a.height) >= 480 THEN '480p'"
        " ELSE '360p' END",
    ),
    # RANGES, each naming exactly its band, and HALF-OPEN (`A..<B`) because the cuts are `<`: a
    # closed range would claim back the file sitting on a cut. The value stays the language's; what
    # a row is called is the client's table, held to this CASE by `test_one_name_per_filter.py`.
    # Any other range typed or kept still parses and returns exactly its files.
    "duration": (
        "",
        "CASE WHEN a.duration_ms IS NULL THEN NULL"
        " WHEN a.duration_ms < 60000 THEN '0s..<1m'"
        " WHEN a.duration_ms < 180000 THEN '1m..<3m'"
        " WHEN a.duration_ms < 300000 THEN '3m..<5m'"
        " WHEN a.duration_ms < 900000 THEN '5m..<15m'"
        " WHEN a.duration_ms < 1800000 THEN '15m..<30m'"
        " WHEN a.duration_ms < 3600000 THEN '30m..<60m'"
        " ELSE '60m+' END",
    ),
    # Six half-open bands, as `duration`'s. BINARY, as `size:` scales by 1024; full digits, because
    # a digit separator in SQL parses only on newer SQLite than Sift runs on.
    "size": (
        "",
        "CASE WHEN a.size_bytes IS NULL THEN NULL"
        " WHEN a.size_bytes < 1048576 THEN '0..<1mb'"
        " WHEN a.size_bytes < 10485760 THEN '1mb..<10mb'"
        " WHEN a.size_bytes < 104857600 THEN '10mb..<100mb'"
        " WHEN a.size_bytes < 1073741824 THEN '100mb..<1gb'"
        " WHEN a.size_bytes < 5368709120 THEN '1gb..<5gb'"
        " ELSE '5gb+' END",
    ),
    # The three words `orientation:` takes; NULL, left out of the column, for a file never measured.
    "orientation": (
        "",
        "CASE WHEN a.width IS NULL OR a.height IS NULL THEN NULL"
        " WHEN a.width > a.height THEN 'landscape'"
        " WHEN a.width < a.height THEN 'portrait'"
        " ELSE 'square' END",
    ),
    # How a file came to be enriched without a person doing it, one row per way and per box with a
    # word (a file recognised by two boxes is under both), each arm reading the tables its
    # `enriched:` predicate reads. The username arms are FILINGS, not namings. A box Sift has no
    # word for is under no row, since no filter could name it; `enriched:stash` still finds it. A
    # LEFT join, so a file nothing wrote to is under `none`.
    "enriched": (
        "\n  LEFT JOIN ("
        "SELECT sb.asset_id, b.slug AS via FROM asset_stash_box_matches sb"
        " JOIN stash_boxes b ON b.id = sb.box_id"
        " WHERE sb.state = 'applied' AND b.slug IS NOT NULL"
        " UNION SELECT ft.asset_id, 'faces' FROM face_tracks ft"
        " WHERE ft.person_id IS NOT NULL AND ft.attribution IN ('matched', 'confirmed')"
        " UNION SELECT ep2.asset_id, 'folder' FROM asset_people ep2 WHERE ep2.source = 'folder'"
        " UNION SELECT ea.asset_id, 'folder' FROM asset_usernames ea WHERE ea.source = 'folder'"
        " UNION SELECT ea2.asset_id, 'filename' FROM asset_usernames ea2"
        " WHERE ea2.source = 'filename'"
        " UNION SELECT ea3.asset_id, 'watermark' FROM asset_usernames ea3"
        " WHERE ea3.source = 'watermark'"
        " UNION SELECT ea4.asset_id, 'metadata' FROM asset_usernames ea4"
        " WHERE ea4.source = 'metadata'"
        " UNION SELECT es.asset_id, 'acoustid' FROM song_files es WHERE es.source = 'acoustid'"
        ") fx ON fx.asset_id = a.id",
        "COALESCE(fx.via, 'none')",
    ),
    # Who made the file, one answer each: the expression `created:` reads.
    "created": ("", FILE_MADE_BY),
    # Whether THIS user hearted the file (`fav:`); a LEFT join, as `viewed` takes one.
    "fav": (
        "\n  LEFT JOIN asset_user_state fxf ON fxf.asset_id = a.id AND fxf.user_id = :viewer",
        "CASE WHEN COALESCE(fxf.favorite, 0) = 1 THEN 'yes' ELSE 'no' END",
    ),
    # Whether a Loop is marked in the file: `loops:any` or `loops:none`.
    "loops": (
        "",
        "CASE WHEN EXISTS (SELECT 1 FROM loops fxl WHERE fxl.asset_id = a.id)"
        " THEN 'any' ELSE 'none' END",
    ),
    # Which of Sift's products gave up on the file (`left_out:`). A transient verdict is a retry
    # still to come, so it is not counted.
    "left_out": (
        "\n  JOIN file_verdicts fx ON fx.asset_id = a.id AND fx.transient = 0"
        # The probe and the identity are verdicts of their own, not products.
        " AND fx.product NOT IN ('"
        + VerdictProduct.PROBE
        + "', '"
        + VerdictProduct.IDENTITY
        + "')",
        "fx.product",
    ),
    # WHEN a file was last put to a box outside this machine, one row per file (`enriched` above is
    # WHO wrote to it). Kept local is a row of its own, first, so a file held back never reads as
    # one nobody got round to. `now` is read in the statement, which is built once; the bands are
    # days, so "this week" is the last seven. It reads `stash_box_scans`, one row per box asked
    # whatever came back, so "never" means never asked.
    "enrichment": (
        "\n  LEFT JOIN (SELECT asset_id, MAX(scanned_at) AS at FROM stash_box_scans"
        " GROUP BY asset_id) fx ON fx.asset_id = a.id",
        "CASE WHEN " + KEPT_LOCAL_HERE + " THEN 'local'"
        " WHEN fx.at IS NULL THEN 'never'"
        " WHEN fx.at >= CAST(strftime('%s', 'now') AS INTEGER) - 86400 THEN 'today'"
        " WHEN fx.at >= CAST(strftime('%s', 'now') AS INTEGER) - 604800 THEN 'week'"
        " WHEN fx.at >= CAST(strftime('%s', 'now') AS INTEGER) - 2592000 THEN 'month'"
        " ELSE 'older' END",
    ),
    # The release year. A file with no date is NULL and left out of the column.
    "released": ("", "substr(a.release_date, 1, 4)"),
    # The network a file was put out by: the root of its Site's `parent_id` chain, by ID because
    # two networks may share a name (`FACET_LABELS` names it). A root is a Site with no parent and
    # at least one Site within it; its own files count under it, as `network:<id>` selects them. A
    # standalone Site is no network. UNION, so a parent loop terminates and drops out. The root's
    # name is carried down the recursion: joined back to `sites`, the planner drives the whole
    # statement from that small table.
    "network": (
        "\n  JOIN asset_usernames fxa ON fxa.asset_id = a.id"
        "\n  JOIN usernames fxc ON fxc.id = fxa.username_id"
        "\n  JOIN (WITH RECURSIVE network_root(id, root_id, root_name) AS ("
        "SELECT id, id, name FROM sites WHERE parent_id IS NULL"
        " AND EXISTS (SELECT 1 FROM sites fxk WHERE fxk.parent_id = sites.id)"
        " UNION SELECT p.id, r.root_id, r.root_name FROM sites p"
        " JOIN network_root r ON p.parent_id = r.id)"
        " SELECT id, root_id, root_name FROM network_root) fx ON fx.id = fxc.site_id"
        "\n  LEFT JOIN site_user_state fxh ON fxh.site_id = fx.root_id"
        " AND fxh.user_id = :viewer",
        "fx.root_id",
    ),
    # What the people on a file are like: six words off a person's record and two bands. UPPER, as
    # the predicates fold, so two boxes' spellings are one row. A concealed person's attributes are
    # counted, unlike their name: the word names nobody, and `hair:` returns their files too.
    "gender": (
        "\n  JOIN asset_people fxa ON fxa.asset_id = a.id"
        "\n  JOIN people fxp ON fxp.id = fxa.person_id",
        "UPPER(fxp.gender)",
    ),
    "hair": (
        "\n  JOIN asset_people fxa ON fxa.asset_id = a.id"
        "\n  JOIN people fxp ON fxp.id = fxa.person_id",
        "UPPER(fxp.hair_color)",
    ),
    "eyes": (
        "\n  JOIN asset_people fxa ON fxa.asset_id = a.id"
        "\n  JOIN people fxp ON fxp.id = fxa.person_id",
        "UPPER(fxp.eye_color)",
    ),
    "ethnicity": (
        "\n  JOIN asset_people fxa ON fxa.asset_id = a.id"
        "\n  JOIN people fxp ON fxp.id = fxa.person_id",
        "UPPER(fxp.ethnicity)",
    ),
    "nationality": (
        "\n  JOIN asset_people fxa ON fxa.asset_id = a.id"
        "\n  JOIN people fxp ON fxp.id = fxa.person_id",
        "UPPER(fxp.country)",
    ),
    "breasts": (
        "\n  JOIN asset_people fxa ON fxa.asset_id = a.id"
        "\n  JOIN people fxp ON fxp.id = fxa.person_id",
        "UPPER(fxp.breast_type)",
    ),
    # The same expression the `height:` and `age:` predicates compare against, from `constraints`.
    "height": (
        "\n  JOIN asset_people fxa ON fxa.asset_id = a.id"
        "\n  JOIN people fxp ON fxp.id = fxa.person_id",
        HEIGHT_BAND.format(col="fxp.height_cm"),
    ),
    "age": (
        "\n  JOIN asset_people fxa ON fxa.asset_id = a.id"
        "\n  JOIN people fxp ON fxp.id = fxa.person_id",
        AGE_YEARS.format(col="fxp.birth_date"),
    ),
}

#: The named-thing columns that also count `any` and `none`: the link `tags:any` reads, joined bare,
#: and the same test of what it may count, so a row counts the wall it opens.
FACET_PRESENCE: dict[str, tuple[str, str]] = {
    name: (join, "\n   AND " + only_shown(name))
    for name, join in {
        "tags": "\n  JOIN asset_tags fxa ON fxa.asset_id = a.id",
        "people": "\n  JOIN asset_people fxa ON fxa.asset_id = a.id",
        "sites": (
            "\n  JOIN asset_usernames fxa ON fxa.asset_id = a.id"
            "\n  JOIN usernames fxc ON fxc.id = fxa.username_id"
        ),
        "collections": "\n  JOIN collection_items fxa ON fxa.asset_id = a.id",
        "photo_sets": "\n  JOIN photo_set_items fxa ON fxa.asset_id = a.id",
        "songs": "\n  JOIN song_files fxa ON fxa.asset_id = a.id",
    }.items()
}

#: A name spelled as a presence word cannot be filtered to by name (`tags:none` asks presence).
PRESENCE_WORDS = frozenset({"any", "none"})

#: The other spellings a dimension answers to: whatever its filter answers to. The kernel may not
#: import the search slice's own table, so `test_a_renamed_facet_agrees_with_the_renamed_token`
#: holds the two together.
FACETS_RENAMED: dict[str, str] = {
    "folder": "in",
    "file_type": "filetype",
    "video_codec": "vcodec",
    "audio_codec": "acodec",
}

#: The dimensions whose value is an ID, since two networks or two people may share a name, and the
#: name on the row whose primary key IS the value, so the grouping never has two answers.
FACET_LABELS: dict[str, str] = {"network": "fx.root_name", "people": "fx.name"}

#: Which facets hide a value whose own row this viewer may not be told about. A scalar has no row.
_NAMED_FACETS = frozenset(
    {"tags", "people", "sites", "collections", "photo_sets", "songs", "in", "network"}
)

#: Leaves a concealed name out of the grouping, off the per-user state each joins as `fxh`.
_CONCEALED_VALUE = "\n   AND (:reveal_named = 1 OR COALESCE(fxh.hidden, 0) = 0)"

#: ...except a Site, concealed by anything above it too. `network` groups by the root, which has
#: nothing above it, so it reads its own row.
_CONCEALED_SITE = "\n   AND (:reveal_named = 1 OR NOT " + SITE_CONCEALED.format(site="fx.id") + ")"

_CONCEALED_BY_FACET: dict[str, str] = {
    name: (_CONCEALED_SITE if name == "sites" else _CONCEALED_VALUE) for name in _NAMED_FACETS
}


def concealed_value(facet: str) -> str:
    """The clause that keeps a name this viewer may not be told about out of the grouping, or the
    empty string for a dimension nothing conceals."""
    return _CONCEALED_BY_FACET.get(facet, "")


# --- the columns read off the stored counts, for a question that narrows nothing ----------------

#: One stored count row (`c`) by the page's two vault rules: what a column counts of its files.
_SHOWN = (
    "CASE WHEN :hidden_only = 1 THEN CASE WHEN :reveal = 1 THEN c.concealed ELSE 0 END"
    " ELSE c.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE c.concealed END END"
)

#: Per value: the kind its count is kept under, the table naming it, the hiding per user, the
#: column both key it by, and the memberships two values sharing one name are counted from.
_STORED_NAMED: dict[str, tuple[str, str, str, str, str]] = {
    "tags": ("tag", "tags", "tag_user_state", "tag_id", "asset_tags"),
    "collections": (
        "collection",
        "collections",
        "collection_user_state",
        "collection_id",
        "collection_items",
    ),
    "photo_sets": (
        "photo_set",
        "photo_sets",
        "photo_set_user_state",
        "photo_set_id",
        "photo_set_items",
    ),
    "songs": ("song", "songs", "song_user_state", "song_id", "song_files"),
}

# A column grouped by NAME: each name one count row answers is read off it; a name two rows share
# (two photo sets of one name) counts its files once, from those rows' memberships alone.
_NAMED_COLUMN = """
WITH named(facet_value, object_id, files) AS (
  SELECT fx.name, c.object_id, <<SHOWN>>
    FROM viewer_entity_counts c
    JOIN <<TABLE>> fx ON fx.id = c.object_id
    LEFT JOIN <<HIDING>> fxh ON fxh.<<KEY>> = fx.id AND fxh.user_id = :viewer
   WHERE c.user_id = :viewer AND c.kind = '<<KIND>>'<<CONCEALED>>
),
counted(facet_value, object_id, files) AS (
  SELECT facet_value, object_id, files FROM named
   WHERE files > 0 AND facet_value IS NOT NULL AND facet_value <> ''
),
shared(facet_value) AS (
  SELECT facet_value FROM counted GROUP BY facet_value HAVING COUNT(*) > 1
)
SELECT facet_value, files FROM counted
 WHERE facet_value NOT IN (SELECT facet_value FROM shared)
UNION ALL
SELECT s.facet_value, COUNT(DISTINCT m.asset_id)
  FROM shared h CROSS JOIN counted s ON s.facet_value = h.facet_value
  CROSS JOIN <<MEMBERS>> m ON m.<<KEY>> = s.object_id
  CROSS JOIN viewer_assets v ON v.user_id = :viewer AND v.asset_id = m.asset_id
 WHERE (:reveal = 1 OR v.concealed = 0) AND (:hidden_only = 0 OR v.concealed = 1)
 GROUP BY s.facet_value
 ORDER BY files DESC, facet_value ASC
 LIMIT :limit
"""

# People by id, named beside it.
_PEOPLE_COLUMN = """
SELECT c.object_id AS facet_value, <<SHOWN>> AS files, fx.name AS facet_label
  FROM viewer_entity_counts c
  JOIN people fx ON fx.id = c.object_id
  LEFT JOIN person_user_state fxh ON fxh.person_id = fx.id AND fxh.user_id = :viewer
 WHERE c.user_id = :viewer AND c.kind = 'person'<<CONCEALED>> AND <<SHOWN>> > 0
 ORDER BY files DESC, facet_value ASC
 LIMIT :limit
"""

# A column whose value is the count row's own key: the kind of file, a folder's name.
_KEYED_COLUMN = """
SELECT c.object_id AS facet_value, <<SHOWN>> AS files
  FROM viewer_entity_counts c
 WHERE c.user_id = :viewer AND c.kind = '<<KIND>>' AND c.object_id <> '' AND <<SHOWN>> > 0
 ORDER BY files DESC, facet_value ASC
 LIMIT :limit
"""

# How many files carry any of one dimension (`has_<facet>`), for its Has and No rows.
_PRESENCE = """
SELECT <<SHOWN>> AS files
  FROM viewer_entity_counts c
 WHERE c.user_id = :viewer AND c.kind = :presence AND c.object_id = 'any'
"""


def _column(template: str, **parts: str) -> str:
    """A stored column's statement: module constants filled with module constants."""
    for name, value in parts.items():
        template = template.replace("<<" + name + ">>", value)
    return template


#: The Has row of a stored column, a seek on one count row.
STORED_PRESENCE = point_read("access.facet_presence", _column(_PRESENCE, SHOWN=_SHOWN))

#: Every column a stored count answers, by facet: the rest are counted from the page's statement.
STORED_COLUMNS: dict[str, str] = {
    facet: _column(
        _NAMED_COLUMN,
        SHOWN=_SHOWN,
        KIND=kind,
        TABLE=table,
        HIDING=hiding,
        KEY=key,
        MEMBERS=members,
        CONCEALED=concealed_value(facet),
    )
    for facet, (kind, table, hiding, key, members) in _STORED_NAMED.items()
} | {
    "people": _column(_PEOPLE_COLUMN, SHOWN=_SHOWN, CONCEALED=concealed_value("people")),
    "media": _column(_KEYED_COLUMN, SHOWN=_SHOWN, KIND="media"),
    # By name, as `in:` reads it. A shut vault leaves out a hidden folder's own name, so where
    # placeholders show what it holds the column is counted from the page's statement instead.
    "in": _column(_KEYED_COLUMN, SHOWN=_SHOWN, KIND="place"),
}
