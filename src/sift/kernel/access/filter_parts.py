# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQL a filter leaf and a facet count both read, written once so a row selects what it counted."""

from __future__ import annotations

from sift.kernel.access.sites import SITE_REACH


class ConstraintError(ValueError):
    """A constraint that could never describe an asset."""


#: Every file under a Site, person, tag or folder kept local, uncorrelated so SQLite builds it
#: once. The folder arm starts from the marked folders (a partial index), reaches every folder
#: inside each through `folder_ancestry`, and takes the copies there: any copy of a file inside a
#: marked folder keeps the file, since the bytes are the same wherever they sit.
KEPT_LOCAL_FILES = (
    "SELECT ap.asset_id FROM asset_people ap"  # noqa: S608
    " JOIN people p ON p.id = ap.person_id AND p.keep_local = 1"
    " UNION SELECT atg.asset_id FROM asset_tags atg"
    " JOIN tags t ON t.id = atg.tag_id AND t.keep_local = 1"
    " UNION SELECT aa.asset_id FROM asset_usernames aa"
    " JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN (" + SITE_REACH + ") reach ON reach.site_id = ac.site_id"
    " JOIN sites pl ON pl.id = reach.ancestor_id AND pl.keep_local = 1"
    " UNION SELECT fl.asset_id FROM folders kf"
    " CROSS JOIN folder_ancestry fan ON fan.ancestor_id = kf.id"
    " CROSS JOIN asset_locations fl ON fl.folder_id = fan.folder_id"
    " WHERE kf.keep_local = 1"
)

#: Kept local by its own switch or by anything it is filed under.
KEPT_LOCAL_HERE = "(a.keep_local = 1 OR a.id IN (" + KEPT_LOCAL_FILES + "))"

#: Who made one file. A download counts only if it began no later than a minute after the file
#: arrived; the minute is slack for a clock that steps backwards.
FILE_MADE_BY = (
    "CASE WHEN EXISTS (SELECT 1 FROM produced_files mb WHERE mb.asset_id = a.id"
    " AND mb.operation = 'compress') THEN 'compress'"
    " WHEN EXISTS (SELECT 1 FROM produced_files mb WHERE mb.asset_id = a.id) THEN 'edit'"
    " WHEN EXISTS (SELECT 1 FROM workbench_decision_subjects ms"
    " JOIN workbench_decisions md ON md.id = ms.decision_id"
    " WHERE ms.kind = 'asset' AND ms.subject_id = a.id AND md.verb = 'added'"
    " AND md.actor_kind = 'sift' AND md.actor_id = 'swap') THEN 'swap'"
    " WHEN EXISTS (SELECT 1 FROM downloads mdl WHERE mdl.asset_id = a.id"
    " AND mdl.state = 'done' AND mdl.created_at <= a.added_at + 60) THEN 'download'"
    " ELSE 'library' END"
)

#: `KEPT_LOCAL_FILES` over `keep_from_swaps`.
KEPT_FROM_SWAPS_FILES = (
    "SELECT ap.asset_id FROM asset_people ap"  # noqa: S608
    " JOIN people p ON p.id = ap.person_id AND p.keep_from_swaps = 1"
    " UNION SELECT atg.asset_id FROM asset_tags atg"
    " JOIN tags t ON t.id = atg.tag_id AND t.keep_from_swaps = 1"
    " UNION SELECT aa.asset_id FROM asset_usernames aa"
    " JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN (" + SITE_REACH + ") reach ON reach.site_id = ac.site_id"
    " JOIN sites pl ON pl.id = reach.ancestor_id AND pl.keep_from_swaps = 1"
    " UNION SELECT fl.asset_id FROM folders kf"
    " CROSS JOIN folder_ancestry fan ON fan.ancestor_id = kf.id"
    " CROSS JOIN asset_locations fl ON fl.folder_id = fan.folder_id"
    " WHERE kf.keep_from_swaps = 1"
)

#: Kept out of swaps by its own mark or by anything it is filed under.
KEPT_FROM_SWAPS_HERE = "(a.keep_from_swaps = 1 OR a.id IN (" + KEPT_FROM_SWAPS_FILES + "))"


# Read on the People wall and the Files wall, so one row means one set on both.

#: Whole years, as `_age_from` counts them; under 18 or over 120 is a wrong birthdate.
AGE_YEARS = (
    "CASE WHEN date({col}) IS NULL THEN NULL"
    " WHEN date({col}) > date('now', '-18 years') THEN NULL"
    " WHEN date({col}) < date('now', '-120 years') THEN NULL"
    " ELSE CAST(CAST(strftime('%Y', 'now') AS INTEGER)"
    " - CAST(strftime('%Y', date({col})) AS INTEGER)"
    " - (strftime('%m-%d', 'now') < strftime('%m-%d', date({col}))) AS TEXT) END"
)

#: Whether `{age}` falls between two whole numbers; one age is a span of one.
AGE_WITHIN = "CAST({age} AS INTEGER) BETWEEN {{}} AND {{}}"

#: Ten-centimetre bands, the step heights are read in, with the sparse ends named.
HEIGHT_BAND = (
    "CASE WHEN {col} IS NULL OR CAST({col} AS INTEGER) <= 0 THEN NULL"
    " WHEN CAST({col} AS INTEGER) < 150 THEN '<150'"
    " WHEN CAST({col} AS INTEGER) >= 200 THEN '200+'"
    " ELSE CAST((CAST({col} AS INTEGER) / 10) * 10 AS TEXT) || '-'"
    " || CAST((CAST({col} AS INTEGER) / 10) * 10 + 9 AS TEXT) END"
)
