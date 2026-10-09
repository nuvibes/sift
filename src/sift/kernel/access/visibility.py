# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may see which file, stored one row per user and file and kept true by triggers."""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sift.kernel.access import (
    visibility_panel,
    visibility_settled,
    visibility_triggers,
    visibility_walls,
)
from sift.kernel.access.viewer import Viewer, reveals_existence
from sift.kernel.access.visibility_kinds import (
    _LOOP_KIND,
    _SITE_KIND,
    PAIRED,
    _counted,
    _each_kind,
    _each_pair,
    _every_kind_joined,
)
from sift.kernel.access.visibility_kinds import WAITING_FACES_KIND as WAITING_FACES_KIND
from sift.kernel.access.visibility_kinds import Counted as Counted
from sift.kernel.access.visibility_kinds import counted as counted
from sift.kernel.access.visibility_settled import RUN as _RUN
from sift.kernel.access.visibility_settled import RUN_USER as _RUN_USER
from sift.kernel.access.visibility_tables import (
    _ADD_COLUMN,
    _ANCESTRY_CHECK,
    _ANCESTRY_DIFFERENCES,
    _CLEAR_ANCESTRY,
    _CLEAR_ENTITY_COUNTS,
    _CLEAR_PAIR_COUNTS,
    _CLEAR_PARTNER_COUNTS,
    _CLEAR_PENDING,
    _CLEAR_PLACES,
    _CLEAR_ROWS,
    _CLEAR_STATS,
    _COUNTS_EMPTIED,
    _COUNTS_GIVEN_ONE,
    _COUNTS_TAKEN_ONE,
    _CREATE_ANCESTRY_INDEX,
    _CREATE_CONCEALED_INDEX,
    _CREATE_ENTITY_COUNTS,
    _CREATE_FOLDER_ANCESTRY,
    _CREATE_PAIR_COUNTS,
    _CREATE_PAIR_COUNTS_B_INDEX,
    _CREATE_PAIR_COUNTS_EMPTY_INDEX,
    _CREATE_PARTNER_COUNTS,
    _CREATE_PENDING,
    _CREATE_PLACES,
    _CREATE_VIEWER_ASSETS,
    _CREATE_VIEWER_STATS,
    _DELETE_STAGED,
    _DIFFERENCES,
    _DROP_EXPECTED_ROWS,
    _DROP_KIND_COUNTS,
    _DROP_KIND_PAIRS,
    _DROP_USER_COUNTS,
    _DROP_USER_PAIRS,
    _DROP_USER_ROWS,
    _DROP_USER_STATS,
    _ENTITY_COUNT_DIFFERENCES,
    _ENTITY_COUNT_ROWS_ONE,
    _EVERY_USER_EVERY_FILE,
    _EVERY_USER_EVERY_PLACE,
    _FILL_ANCESTRY,
    _FILL_ENTITY_COUNTS,
    _FILL_PAIR_COUNTS,
    _FILL_PARTNER_COUNTS,
    _FILL_STATS,
    _INSERT_ROWS,
    _ONE_USER_EVERY_FILE,
    _ONE_USER_EVERY_PLACE,
    _PAIR_COUNT_DIFFERENCES,
    _PAIR_COUNT_ROWS_ONE,
    _PAIRS_EMPTIED,
    _PAIRS_GIVEN_ONE,
    _PAIRS_TAKEN_ONE,
    _PARTNER_COUNT_DIFFERENCES,
    _STAGE_PAIRS,
    _STAGED,
    _STAGED_PLACES,
    _STATS_DIFFERENCES,
    _STATS_GIVEN,
    _STATS_TAKEN,
    _TRIGGERS_PRESENT,
    _USER_SCOPE,
    _USER_STATS,
    _filled,
)
from sift.kernel.access.visibility_triggers import _changed as _changed
from sift.kernel.access.visibility_triggers import _not_already_held as _not_already_held
from sift.kernel.access.visibility_triggers import _trigger as _trigger
from sift.kernel.access.visibility_triggers import _update_columns as _update_columns
from sift.kernel.access.visibility_triggers import _watching as _watching
from sift.kernel.db import (
    Connection,
    add_schema_dependency,
    register_schema_initializer,
    register_schema_invariant,
)
from sift.kernel.log import get_logger
from sift.kernel.migrations import column_exists
from sift.kernel.sql_splice import splice

log = get_logger(__name__)


COMPONENT = "visibility"
VERSION = 20


def register_counted(new: Counted, *, component: str) -> None:
    """A slice declares a kind of thing to count, over a table its `component` owns."""
    if any(one.kind == new.kind for one in _counted):
        raise ValueError(f"a counted kind named {new.kind!r} is already known")
    if not new.keys:
        raise ValueError(f"a counted kind on {new.table!r} must declare the table's keys")
    # A second kind over a watched table shares its triggers (`_watching`), so the keys must agree.
    for one in _counted:
        if one.table == new.table and set(one.keys) != set(new.keys):
            raise ValueError(f"counted kinds on {new.table!r} disagree about the table's keys")
    _counted.append(new)
    add_schema_dependency(COMPONENT, component)
    _built.cache_clear()


# --- the verdict -----------------------------------------------------------------------------

# --- one place, for one user: fragments over `p` (a `user_id`) and `l` (a `folder_id`, a
# `root_id`), spliced by the verdict, the folder tree and the badges so the three agree.

# A restrict anywhere on the folder chain is absolute: nothing shared nearer the file undoes it.
FOLDER_CHAIN_RESTRICT = """
EXISTS (SELECT 1 FROM folder_ancestry an
         JOIN acl_grants g ON g.object_type = 'folder' AND g.object_id = an.ancestor_id
                          AND g.subject_user_id = p.user_id AND g.effect = 'restrict'
        WHERE an.folder_id = l.folder_id)"""

# Read only where no restrict reaches, so the nearest share and any share agree.
NEAREST_FOLDER_SHARE = """
(SELECT MAX(g.effect = 'share')
   FROM folder_ancestry an
   JOIN acl_grants g ON g.object_type = 'folder' AND g.object_id = an.ancestor_id
                    AND g.subject_user_id = p.user_id
  WHERE an.folder_id = l.folder_id
  GROUP BY an.depth ORDER BY an.depth LIMIT 1)"""

# Two EXISTS rather than one OR, so each is a seek.
ROOT_RESTRICT = """
(EXISTS (SELECT 1 FROM acl_grants g
          WHERE g.subject_user_id = p.user_id AND g.object_type = 'root' AND g.object_id = l.root_id
            AND g.effect = 'restrict')
 OR EXISTS (SELECT 1 FROM acl_grants g
             WHERE g.subject_user_id = p.user_id AND g.object_type = 'global'
               AND g.object_id IS NULL AND g.effect = 'restrict'))"""


ROOT_SHARE = """
COALESCE((SELECT MAX(g.effect = 'share') FROM acl_grants g
           WHERE g.subject_user_id = p.user_id AND g.object_type = 'root' AND g.object_id = l.root_id),
         (SELECT MAX(g.effect = 'share') FROM acl_grants g
           WHERE g.subject_user_id = p.user_id AND g.object_type = 'global' AND g.object_id IS NULL),
         0)"""

ROOT_HIDDEN = """
EXISTS (SELECT 1 FROM root_user_state h
         WHERE h.root_id = l.root_id AND h.user_id = p.user_id AND h.hidden = 1)"""

FOLDER_CHAIN_HIDDEN = """
EXISTS (SELECT 1 FROM folder_ancestry an
         JOIN folder_user_state h ON h.folder_id = an.ancestor_id
                                 AND h.user_id = p.user_id AND h.hidden = 1
        WHERE an.folder_id = l.folder_id)"""

# A folder the walk never reached has no row for itself, so it reads as denied and concealed.
PLACE_REACHED_JOIN = "\n  LEFT JOIN folder_ancestry fa ON fa.folder_id = l.folder_id AND fa.ancestor_id = l.folder_id"

PLACE_RESTRICTED = splice(
    """CASE WHEN l.folder_id IS NULL THEN CASE WHEN {{ROOT_RESTRICT}} THEN 1 ELSE 0 END
     WHEN fa.folder_id IS NULL THEN 1
     WHEN {{FOLDER_CHAIN_RESTRICT}} OR {{ROOT_RESTRICT}} THEN 1
     ELSE 0 END""",
    ROOT_RESTRICT=ROOT_RESTRICT,
    FOLDER_CHAIN_RESTRICT=FOLDER_CHAIN_RESTRICT,
)

PLACE_SHARED = splice(
    """CASE WHEN l.folder_id IS NULL THEN {{ROOT_SHARE}}
     WHEN fa.folder_id IS NULL THEN 0
     ELSE COALESCE({{NEAREST_FOLDER_SHARE}}, {{ROOT_SHARE}}) END""",
    ROOT_SHARE=ROOT_SHARE,
    NEAREST_FOLDER_SHARE=NEAREST_FOLDER_SHARE,
)

PLACE_VAULTED = splice(
    """CASE WHEN l.folder_id IS NULL THEN CASE WHEN {{ROOT_HIDDEN}} THEN 1 ELSE 0 END
     WHEN fa.folder_id IS NULL THEN 1
     ELSE CASE WHEN {{ROOT_HIDDEN}} OR {{FOLDER_CHAIN_HIDDEN}} THEN 1 ELSE 0 END END""",
    ROOT_HIDDEN=ROOT_HIDDEN,
    FOLDER_CHAIN_HIDDEN=FOLDER_CHAIN_HIDDEN,
)

# --- one file, for one user: bit columns, so one subquery answers three questions. ph = denied * 4
# + allowed * 2 + vaulted (copies), lo = denied * 2 + allowed (memberships), it = restricted * 2 +
# shared (the item). These read `p.user_id` and `p.asset_id`.

# Worked out in place, for the badge, where no recompute has filled `visibility_places`.
PLACE_BITS_OF_COPIES = splice(
    """
(SELECT MAX(s.r) * 4 + MAX(CASE WHEN s.r = 0 AND s.s = 1 THEN 1 ELSE 0 END) * 2 + MAX(s.v)
   FROM (SELECT CASE WHEN r.id IS NULL THEN 1 ELSE {{PLACE_RESTRICTED}} END AS r,
                CASE WHEN r.id IS NULL THEN 0 ELSE {{PLACE_SHARED}} END AS s,
                CASE WHEN r.id IS NULL THEN 1 ELSE {{PLACE_VAULTED}} END AS v
           FROM asset_locations l
           LEFT JOIN library_roots r ON r.id = l.root_id{{PLACE_REACHED_JOIN}}
          WHERE l.asset_id = p.asset_id) s)""",
    PLACE_RESTRICTED=PLACE_RESTRICTED,
    PLACE_SHARED=PLACE_SHARED,
    PLACE_VAULTED=PLACE_VAULTED,
    PLACE_REACHED_JOIN=PLACE_REACHED_JOIN,
)

PHYSICAL_BITS = """
(SELECT MAX(pl.restricted) * 4
      + MAX(CASE WHEN pl.restricted = 0 AND pl.shared = 1 THEN 1 ELSE 0 END) * 2
      + MAX(pl.vaulted)
   FROM asset_locations l
   JOIN visibility_places pl ON pl.user_id = p.user_id AND pl.root_id = l.root_id
                            AND pl.place = COALESCE(l.folder_id, '')
  WHERE l.asset_id = p.asset_id)"""

#: Where the places go: a SELECT of (user_id, root_id, folder_id).
PLACES = "<<PLACES>>"

# A place whose root is gone fails closed, like an unreached folder.
_PLACE_ROWS = splice(
    """
INSERT INTO visibility_places (user_id, root_id, place, restricted, shared, vaulted)
SELECT l.user_id, l.root_id, COALESCE(l.folder_id, ''),
       CASE WHEN r.id IS NULL THEN 1 ELSE {{PLACE_RESTRICTED}} END,
       CASE WHEN r.id IS NULL THEN 0 ELSE {{PLACE_SHARED}} END,
       CASE WHEN r.id IS NULL THEN 1 ELSE {{PLACE_VAULTED}} END
  FROM (<<PLACES>>) l
  JOIN (SELECT id AS user_id FROM users) p ON p.user_id = l.user_id
  LEFT JOIN library_roots r ON r.id = l.root_id{{PLACE_REACHED_JOIN}}""",
    PLACE_RESTRICTED=PLACE_RESTRICTED,
    PLACE_SHARED=PLACE_SHARED,
    PLACE_VAULTED=PLACE_VAULTED,
    PLACE_REACHED_JOIN=PLACE_REACHED_JOIN,
)

#: Whether no copy of the file is present, over `a.id`: shared by the media grid and the wall of
#: loops.
ANY_COPY_MISSING = """CASE WHEN NOT EXISTS (SELECT 1 FROM asset_locations al
                              WHERE al.asset_id = a.id AND al.status = 'present')
            THEN 1 ELSE 0 END"""

#: Whether the viewer may be shown the file of a membership row aliased `link`; binds `seen_by`.
FILE_SEEN_BY_VIEWER = """EXISTS (SELECT 1 FROM viewer_assets seen
                WHERE seen.asset_id = link.asset_id AND seen.user_id = :viewer
                  AND (:reveal = 1 OR seen.concealed = 0))"""


def seen_by(viewer: Viewer) -> dict[str, object]:
    """The two values `FILE_SEEN_BY_VIEWER` binds, from the session asking."""
    return {"viewer": viewer.id, "reveal": 1 if reveals_existence(viewer) else 0}


#: Whether the viewer hid this file themselves rather than something above it; reads `a.id` and
#: `:viewer`.
CONCEALED_BY_THIS_FILE = """CASE WHEN EXISTS (SELECT 1 FROM asset_user_state h
                          WHERE h.asset_id = a.id AND h.user_id = :viewer AND h.hidden = 1)
            THEN 1 ELSE 0 END"""

#: Every site one file reaches, walked up from the file rather than over every site.
_FILE_SITES = """WITH RECURSIVE up(site_id) AS (SELECT s0.id FROM asset_usernames aa
  JOIN usernames ac ON ac.id = aa.username_id JOIN sites s0 ON s0.id = ac.site_id
  WHERE aa.asset_id = p.asset_id
  UNION SELECT s.parent_id FROM up JOIN sites s ON s.id = up.site_id WHERE s.parent_id IS NOT NULL)
SELECT site_id FROM up"""

#: A site reaches what its labels released, so a file belongs to every network above its username.
LOGICAL_BITS = _filled(
    """
(SELECT MAX(g.effect = 'restrict') * 2 + MAX(g.effect = 'share')
   FROM (SELECT 'tag' AS kind, tag_id AS object FROM asset_tags WHERE asset_id = p.asset_id
         UNION ALL
         SELECT 'person', person_id FROM asset_people WHERE asset_id = p.asset_id
         UNION ALL
         SELECT 'collection', collection_id FROM collection_items WHERE asset_id = p.asset_id
         UNION ALL
         SELECT 'site', up.site_id FROM (<<FILE_SITES>>) up
         UNION ALL
         SELECT 'photo_set', photo_set_id FROM photo_set_items WHERE asset_id = p.asset_id
         UNION ALL
         SELECT 'song', song_id FROM song_files WHERE asset_id = p.asset_id) m
   JOIN acl_grants g ON g.subject_user_id = p.user_id
                    AND g.object_type = m.kind AND g.object_id = m.object)""",
    FILE_SITES=_FILE_SITES,
)

ITEM_BITS = """
(SELECT MAX(g.effect = 'restrict') * 2 + MAX(g.effect = 'share')
   FROM acl_grants g
  WHERE g.subject_user_id = p.user_id AND g.object_type = 'item' AND g.object_id = p.asset_id)"""

_HIDDEN_ITSELF = """
EXISTS (SELECT 1 FROM asset_user_state h
         WHERE h.asset_id = p.asset_id AND h.user_id = p.user_id AND h.hidden = 1)"""

# Hidden by something the file belongs to; a site arm walks up as `LOGICAL_BITS` does.
_HIDDEN_BY_MEMBERSHIP = _filled(
    """
EXISTS (SELECT 1 FROM asset_people ap
         JOIN person_user_state hp ON hp.person_id = ap.person_id
        WHERE ap.asset_id = p.asset_id AND hp.user_id = p.user_id AND hp.hidden = 1)
OR EXISTS (SELECT 1 FROM collection_items ci
            JOIN collection_user_state hc ON hc.collection_id = ci.collection_id
           WHERE ci.asset_id = p.asset_id AND hc.user_id = p.user_id AND hc.hidden = 1)
OR EXISTS (SELECT 1 FROM asset_tags vt
            JOIN tag_user_state ht ON ht.tag_id = vt.tag_id
           WHERE vt.asset_id = p.asset_id AND ht.user_id = p.user_id AND ht.hidden = 1)
OR (EXISTS (SELECT 1 FROM site_user_state hz WHERE hz.user_id = p.user_id AND hz.hidden = 1)
    AND EXISTS (SELECT 1 FROM (<<FILE_SITES>>) up JOIN site_user_state hl ON hl.site_id = up.site_id
                 WHERE hl.user_id = p.user_id AND hl.hidden = 1))
OR EXISTS (SELECT 1 FROM photo_set_items vp
            JOIN photo_set_user_state hs ON hs.photo_set_id = vp.photo_set_id
           WHERE vp.asset_id = p.asset_id AND hs.user_id = p.user_id AND hs.hidden = 1)
OR EXISTS (SELECT 1 FROM song_files vs
            JOIN song_user_state hg ON hg.song_id = vs.song_id
           WHERE vs.asset_id = p.asset_id AND hg.user_id = p.user_id AND hg.hidden = 1)""",
    FILE_SITES=_FILE_SITES,
)

#: The ladder, over the `lo`, `it` and `ph` bits of alias `<<S>>`: written once for the verdict and
#: the badge.
LADDER_ADMITS = """CASE WHEN (<<S>>.lo & 2) = 2 THEN 0
            WHEN (<<S>>.it & 2) = 2 THEN 0
            WHEN (<<S>>.ph & 4) = 4 THEN 0
            WHEN (<<S>>.it & 1) = 1 THEN 1
            WHEN (<<S>>.ph & 2) = 2 OR (<<S>>.lo & 1) = 1 THEN 1
            ELSE 0 END"""


LADDER_RESTRICTS = """CASE WHEN (<<S>>.lo & 2) = 2 OR (<<S>>.it & 2) = 2 OR (<<S>>.ph & 4) = 4
            THEN 1 ELSE 0 END"""


def ladder_admits(alias: str) -> str:
    """The ladder over the bit columns of `alias`, a module constant's alias only."""
    return _filled(LADDER_ADMITS, S=alias)


def ladder_restricts(alias: str) -> str:
    """`LADDER_RESTRICTS` over the bit columns of `alias`, a module constant's alias only."""
    return _filled(LADDER_RESTRICTS, S=alias)


#: Where the pairs go: a SELECT of (user_id, asset_id).
PAIRS = "<<PAIRS>>"

# `LIMIT -1` keeps `x` whole, so each pair's bits are worked out once.
_VERDICT_ROWS = splice(
    """
SELECT x.user_id, x.asset_id,
       CASE WHEN x.hidden = 1 OR (x.ph & 1) = 1 OR x.lv = 1 THEN 1 ELSE 0 END AS concealed
  FROM (SELECT p.user_id, p.asset_id, u.role,
               COALESCE({{PHYSICAL_BITS}}, 0) AS ph,
               COALESCE({{LOGICAL_BITS}}, 0) AS lo,
               COALESCE({{ITEM_BITS}}, 0) AS it,
               CASE WHEN {{HIDDEN_ITSELF}} THEN 1 ELSE 0 END AS hidden,
               CASE WHEN {{HIDDEN_BY_MEMBERSHIP}} THEN 1 ELSE 0 END AS lv
          FROM (<<PAIRS>>) p
          JOIN users u ON u.id = p.user_id
         WHERE EXISTS (SELECT 1 FROM asset_locations al WHERE al.asset_id = p.asset_id)
         LIMIT -1) x
 WHERE x.role = 'admin'
    OR {{LADDER}} = 1""",
    LADDER=ladder_admits("x"),
    PHYSICAL_BITS=PHYSICAL_BITS,
    LOGICAL_BITS=LOGICAL_BITS,
    ITEM_BITS=ITEM_BITS,
    HIDDEN_ITSELF=_HIDDEN_ITSELF,
    HIDDEN_BY_MEMBERSHIP=_HIDDEN_BY_MEMBERSHIP,
)

if _VERDICT_ROWS.count(PAIRS) != 1:  # pragma: no cover (an edit that broke the seam)
    raise RuntimeError("the verdict statement must take its pairs in exactly one place")

_FILL_STAGED_PLACES = _filled(_PLACE_ROWS, PLACES=_STAGED_PLACES)

_INSERT_STAGED = _filled(_INSERT_ROWS, ROWS=_filled(_VERDICT_ROWS, PAIRS=_STAGED))


@dataclass(frozen=True)
class _Recompute:
    """The two halves of a recompute around a change, which every trigger calls rather than
    carries."""

    taken: tuple[str, ...]
    given: tuple[str, ...]
    fill_entity_counts: str
    fill_pair_counts: str
    moving: tuple[tuple[str, tuple[str, ...]], ...] = ()
    version_13: bool = False

    def take(self) -> list[str]:
        """Record the staged pairs and what their files hold, before a change lands."""
        return list(self.taken) if self.version_13 else [visibility_settled.TOUCH_CALL]

    def give(self) -> list[str]:
        """Re-decide the staged pairs' rows; their counts move when the write ends."""
        return list(self.given) if self.version_13 else [visibility_settled.ROWS_CALL]

    def split(self, pairs: str, *, table: str = "") -> tuple[list[str], list[str]]:
        """Stage `pairs` (a module constant) around a change; a table the verdict does not read
        moves counts only."""
        stage = [_CLEAR_PENDING, _filled(_STAGE_PAIRS, PAIRS=pairs)]
        if self.version_13 or table in visibility_settled.decided_by() or not table:
            return [*stage, *self.take()], self.give()
        touch = _filled(_RUN, STEP=visibility_settled.TOUCH + "_" + table)
        return [*stage, touch, _CLEAR_PENDING], [_CLEAR_PENDING]

    def whole(self, pairs: str) -> list[str]:
        """Stage, decide and settle the rows for a change that moves no membership."""
        before, after = self.split(pairs)
        if self.version_13:
            return before + after
        steps = (visibility_settled.SETTLE, visibility_settled.ANSWERS, visibility_settled.SETTLED)
        return [*before[:2], *(_filled(_RUN, STEP=step) for step in steps)]

    def owing(self, pairs: str, *, row: str = "", widening: str | None = None) -> list[str]:
        return visibility_settled.owing(self, pairs, row=row, widening=widening)

    def going(self, pairs: str) -> list[str]:
        return visibility_settled.going(self, pairs)

    def user(self, user: str) -> list[str]:
        """Every file for one user re-decided from nothing (see `rebuilt`), by a call."""
        return self.rebuilt(user) if self.version_13 else [_filled(_RUN_USER, USER=user)]

    def rebuilt(self, user: str) -> list[str]:
        """Every file for one user from nothing: rows, places and counts dropped and rebuilt."""
        return [
            _filled(_DROP_USER_STATS, USER=user),
            _filled(_DROP_USER_COUNTS, USER=user),
            _filled(_DROP_USER_PAIRS, USER=user),
            _filled(_DROP_USER_ROWS, USER=user),
            _CLEAR_PLACES,
            _filled(_filled(_PLACE_ROWS, PLACES=_ONE_USER_EVERY_PLACE), USER=user),
            _filled(_INSERT_USER_ROWS, USER=user),
            _CLEAR_PLACES,
            _filled(_USER_STATS, USER=user),
            _filled(_filled(self.fill_entity_counts, SCOPE=_USER_SCOPE), USER=user),
            _filled(_filled(self.fill_pair_counts, SCOPE=_USER_SCOPE), USER=user),
        ]


def _recompute_for(
    kinds: Sequence[Counted],
    pairs: Sequence[tuple[Counted, Counted]],
    fill_entity_counts: str,
    fill_pair_counts: str,
) -> _Recompute:
    return _Recompute(
        taken=(
            _STATS_TAKEN,
            *_each_kind(_COUNTS_TAKEN_ONE, kinds),
            *_each_pair(_PAIRS_TAKEN_ONE, pairs),
            _DELETE_STAGED,
        ),
        given=(
            _CLEAR_PLACES,
            _FILL_STAGED_PLACES,
            _INSERT_STAGED,
            _STATS_GIVEN,
            *_each_kind(_COUNTS_GIVEN_ONE, kinds),
            _COUNTS_EMPTIED,
            *_each_pair(_PAIRS_GIVEN_ONE, pairs),
            _PAIRS_EMPTIED,
            _CLEAR_PLACES,
            _CLEAR_PENDING,
        ),
        fill_entity_counts=fill_entity_counts,
        fill_pair_counts=fill_pair_counts,
        moving=visibility_settled.moving(kinds, pairs),
    )


_INSERT_USER_ROWS = _filled(_INSERT_ROWS, ROWS=_filled(_VERDICT_ROWS, PAIRS=_ONE_USER_EVERY_FILE))


@dataclass(frozen=True)
class _Built:
    """Everything that depends on the list of counted kinds, built once from it."""

    recompute: _Recompute
    fill_every_entity_count: str
    entity_count_differences: str
    fill_every_pair_count: str
    pair_count_differences: str
    fill_loop_counts: tuple[str, ...]
    fill_site_counts: tuple[str, ...]
    triggers: tuple[tuple[str, str, str], ...]
    version_13_triggers: tuple[tuple[str, str, str], ...]
    drop_triggers: tuple[str, ...]


@functools.cache
def _built() -> _Built:
    kinds = counted()
    rows = _every_kind_joined(_ENTITY_COUNT_ROWS_ONE, kinds)
    fill = _filled(_FILL_ENTITY_COUNTS, ROWS=rows)
    # The pairs name kernel kinds only, so a slice cannot remove one from under them.
    by_kind = {one.kind: one for one in kinds}
    pairs = tuple((by_kind[a], by_kind[b]) for a, b in PAIRED)
    pair_rows = " UNION ALL ".join(_each_pair(_PAIR_COUNT_ROWS_ONE, pairs))
    fill_pairs = _filled(_FILL_PAIR_COUNTS, ROWS=pair_rows)
    recompute = _recompute_for(kinds, pairs, fill, fill_pairs)
    triggers = tuple(visibility_triggers.build(recompute, kinds))
    version_13_triggers = tuple(
        visibility_triggers.build(replace(recompute, version_13=True), kinds)
    )
    # The v6 and v8 steps' fills, from the same row templates as the whole fill.
    loop = by_kind[_LOOP_KIND]
    loop_pairs = tuple(pair for pair in pairs if loop in pair)
    fill_loop_counts = (
        _filled(_DROP_KIND_COUNTS, KIND=loop.kind),
        _filled(_DROP_KIND_PAIRS, KIND=loop.kind),
        _filled(
            _filled(_FILL_ENTITY_COUNTS, ROWS=_every_kind_joined(_ENTITY_COUNT_ROWS_ONE, [loop])),
            SCOPE="",
        ),
        _filled(
            _filled(
                _FILL_PAIR_COUNTS,
                ROWS=" UNION ALL ".join(_each_pair(_PAIR_COUNT_ROWS_ONE, loop_pairs)),
            ),
            SCOPE="",
        ),
    )
    site = by_kind[_SITE_KIND]
    fill_site_counts = (
        _filled(_DROP_KIND_COUNTS, KIND=site.kind),
        _filled(
            _filled(_FILL_ENTITY_COUNTS, ROWS=_every_kind_joined(_ENTITY_COUNT_ROWS_ONE, [site])),
            SCOPE="",
        ),
    )
    return _Built(
        recompute=recompute,
        fill_every_entity_count=_filled(fill, SCOPE=""),
        entity_count_differences=_filled(_ENTITY_COUNT_DIFFERENCES, ROWS=_filled(rows, SCOPE="")),
        fill_every_pair_count=_filled(fill_pairs, SCOPE=""),
        pair_count_differences=_filled(_PAIR_COUNT_DIFFERENCES, ROWS=_filled(pair_rows, SCOPE="")),
        fill_loop_counts=fill_loop_counts,
        fill_site_counts=fill_site_counts,
        triggers=triggers,
        version_13_triggers=version_13_triggers,
        drop_triggers=tuple(
            _filled("DROP TRIGGER IF EXISTS <<NAME>>", NAME=name)
            for name in (*(name for name, _table, _ddl in triggers), *RETIRED_TRIGGERS)
        ),
    )


def triggers() -> tuple[tuple[str, str, str], ...]:
    """Every trigger this build keeps, as (name, table, DDL)."""
    return _built().triggers


def triggered_tables() -> frozenset[str]:
    """The tables that carry triggers."""
    return frozenset(table for _name, table, _ddl in triggers())


#: Names earlier builds used, dropped wherever this build's are made: a name is the only handle a
#: trigger has.
RETIRED_TRIGGERS = (
    "vis_recompute_take",
    "vis_recompute_give",
    "vis_stats_insert",
    "vis_stats_delete",
    "vis_stats_update",
    "vis_accounts_platform",
    "vis_platforms_parent",
    "vis_acl_grants_insert_platform",
    "vis_acl_grants_delete_platform",
    "vis_acl_grants_update_platform",
    "vis_platform_user_state_insert",
    "vis_platform_user_state_delete",
    "vis_platform_user_state_update",
    "vis_accounts_site",
    "vis_accounts_site_before",
    "vis_accounts_delete_before",
    "vis_asset_accounts_insert",
    "vis_asset_accounts_insert_before",
    "vis_asset_accounts_delete",
    "vis_asset_accounts_delete_before",
    "vis_asset_accounts_update",
    "vis_asset_accounts_update_before",
)

_FILL_EVERY_PLACE = _filled(_PLACE_ROWS, PLACES=_EVERY_USER_EVERY_PLACE)

_FILL_ROWS = _filled(_INSERT_ROWS, ROWS=_filled(_VERDICT_ROWS, PAIRS=_EVERY_USER_EVERY_FILE))


def _body_of(ddl: str) -> str:
    """A trigger's text from its timing word on, past the IF NOT EXISTS the engine drops."""
    return " ".join(re.split(r"\s(?:BEFORE|AFTER|INSTEAD OF)\s", ddl, maxsplit=1)[1].split())


async def _drop_triggers(connection: Connection) -> None:
    for statement in _built().drop_triggers:
        await connection.execute(statement)


async def _create_triggers(connection: Connection) -> None:
    await visibility_settled.create_step_views(connection, _built().triggers)
    for _name, _table, ddl in _built().triggers:
        await connection.execute(ddl)


async def refresh_everything(connection: Connection) -> None:
    """Rebuild every stored answer from the facts, without the triggers in the way."""
    await _drop_triggers(connection)
    await connection.execute(visibility_walls.CREATE)
    await visibility_settled.start_empty(connection)
    await connection.execute(_CLEAR_ANCESTRY)
    await connection.execute(_FILL_ANCESTRY)
    await connection.execute(_CLEAR_PENDING)
    await connection.execute(_CLEAR_ROWS)
    await connection.execute(_CLEAR_PLACES)
    await connection.execute(_FILL_EVERY_PLACE)
    await connection.execute(_FILL_ROWS)
    await connection.execute(_CLEAR_PLACES)
    await connection.execute(_CLEAR_STATS)
    await connection.execute(_FILL_STATS)
    await connection.execute(_CLEAR_ENTITY_COUNTS)
    await connection.execute(_built().fill_every_entity_count)
    await visibility_walls.fill(connection)
    await connection.execute(_CLEAR_PAIR_COUNTS)
    await connection.execute(_built().fill_every_pair_count)
    await connection.execute(_CLEAR_PARTNER_COUNTS)
    await connection.execute(_FILL_PARTNER_COUNTS)
    await _create_triggers(connection)


# The expected rows go into a temporary table: as a CTE read twice it is re-run per row.
_EXPECTED_ROWS = _filled(
    "CREATE TEMP TABLE visibility_expected AS <<ROWS>>",
    ROWS=_filled(_VERDICT_ROWS, PAIRS=_EVERY_USER_EVERY_FILE),
)


async def differences(connection: Connection) -> list[tuple[str, str, str | None, int | None]]:
    """Every way the stored answers disagree with the facts; a whole-library check."""
    found: list[tuple[str, str, str | None, int | None]] = []
    await connection.execute(_DROP_EXPECTED_ROWS)
    await connection.execute(_CLEAR_PLACES)
    await connection.execute(_FILL_EVERY_PLACE)
    try:
        await connection.execute(_EXPECTED_ROWS)
    finally:
        await connection.execute(_CLEAR_PLACES)
    try:
        for statement in (
            _ANCESTRY_DIFFERENCES,
            _DIFFERENCES,
            _STATS_DIFFERENCES,
            _built().entity_count_differences,
            _built().pair_count_differences,
            _PARTNER_COUNT_DIFFERENCES,
            visibility_walls.DIFFERENCES,
        ):
            rows = await connection.execute_fetchall(statement)
            found.extend(
                (str(row[0]), str(row[1]), None if row[2] is None else str(row[2]), row[3])
                for row in rows
            )
    finally:
        await connection.execute(_DROP_EXPECTED_ROWS)
    return found


# --- the component -------------------------------------------------------------------------------


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_FOLDER_ANCESTRY)
        await connection.execute(_CREATE_ANCESTRY_INDEX)
        await connection.execute(_CREATE_VIEWER_ASSETS)
        await connection.execute(_CREATE_CONCEALED_INDEX)
        await connection.execute(_CREATE_VIEWER_STATS)
        await connection.execute(_CREATE_ENTITY_COUNTS)
        await _create_pair_counts(connection)
        await connection.execute(_CREATE_PENDING)
        await connection.execute(_CREATE_PLACES)
        await refresh_everything(connection)
        log.info("visibility.backfilled")
    if 0 < on_disk < 15:
        await _add_columns(connection, "viewer_entity_counts", ("permitted_ms", "concealed_ms"))
    if 0 < on_disk < 11:
        await _size_the_counts(connection)
    if on_disk == 11:
        await refresh_everything(connection)
        log.info("visibility.songs_counted")
    if on_disk == 12:
        await refresh_everything(connection)
        log.info("visibility.songs_hidden_and_shared")
    if on_disk == 13:
        await share_the_steps(connection)
    if 13 <= on_disk < 15:
        await time_the_counts(connection)
    if on_disk == 15:
        await visibility_panel.count_the_panel(connection)
    if 0 < on_disk < 20:
        await visibility_settled.later_steps(connection, on_disk)


async def _size_the_counts(connection: Connection) -> None:
    for table in ("viewer_stats", "viewer_entity_counts"):
        await _add_columns(connection, table, ("permitted_bytes", "concealed_bytes"))
    await refresh_everything(connection)
    log.info("visibility.sized")


async def _add_columns(connection: Connection, table: str, columns: Sequence[str]) -> None:
    """Each count column where it is missing, so a step stopped half way can run again."""
    for column in columns:
        if not await column_exists(connection, table, column):
            # Module constants filled with module constants: nothing from run time.
            # nosemgrep: sift-no-string-built-sql
            await connection.execute(_ADD_COLUMN.format(table=table, column=column))


async def time_the_counts(connection: Connection) -> None:
    """The version 15 step, safe to run again."""
    await _drop_triggers(connection)
    await connection.execute(_CLEAR_ENTITY_COUNTS)
    await connection.execute(_built().fill_every_entity_count)
    await _create_triggers(connection)
    log.info("visibility.timed")


async def share_the_steps(connection: Connection) -> None:
    """The version 14 step: rewrite in place only where every trigger is version 13's, else
    rebuild."""
    rows = await connection.execute_fetchall(_TRIGGERS_PRESENT)
    present = {str(row[0]): _body_of(str(row[1])) for row in rows}
    shared = {name: _body_of(ddl) for name, _table, ddl in _built().triggers}
    if present == shared:
        log.info("visibility.steps_shared", triggers=len(shared), rewritten=0)
        return
    written = {name: _body_of(ddl) for name, _table, ddl in _built().version_13_triggers}
    if present != written:
        await refresh_everything(connection)
        log.warning("visibility.steps_shared_by_a_rebuild", triggers=len(shared))
        return
    await _drop_triggers(connection)
    await _create_triggers(connection)
    log.info("visibility.steps_shared", triggers=len(shared), rewritten=len(written))


async def _create_pair_counts(connection: Connection) -> None:
    """The pair table, its indexes and the partner totals; idempotent."""
    await connection.execute(_CREATE_PAIR_COUNTS)
    await connection.execute(_CREATE_PAIR_COUNTS_B_INDEX)
    await connection.execute(_CREATE_PAIR_COUNTS_EMPTY_INDEX)
    await connection.execute(_CREATE_PARTNER_COUNTS)


async def keep_true(connection: Connection) -> None:
    """Every boot: the triggers and the ancestry match this build, or every answer is rebuilt."""
    rows = await connection.execute_fetchall(_TRIGGERS_PRESENT)
    present = {str(row[0]): _body_of(str(row[1])) for row in rows}
    wanted = {name: _body_of(ddl) for name, _table, ddl in _built().triggers}
    missing = [name for name, body in wanted.items() if present.get(name) != body]
    # A trigger no build named is reported, and rebuilds every boot until it is retired.
    stale = sorted(name for name in present if name not in wanted)
    unknown = [name for name in stale if name not in RETIRED_TRIGGERS]
    if missing or stale:
        log.warning("visibility.triggers_repaired", repaired=len(missing), stale=stale)
        if unknown:
            log.error("visibility.unknown_triggers", names=unknown)
        await refresh_everything(connection)
        return
    out_of_step = await connection.execute_fetchall(_ANCESTRY_CHECK)
    if out_of_step:
        log.warning("visibility.ancestry_repaired")
        await refresh_everything(connection)
        return
    await visibility_settled.fold_what_is_owed(connection)


register_schema_initializer(
    COMPONENT,
    VERSION,
    initialize,
    depends_on=["identity", "library", "content", "user_state", "catalog", "access"],
    baseline=10,
)
register_schema_invariant(COMPONENT, keep_true)
