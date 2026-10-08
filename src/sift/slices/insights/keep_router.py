# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep as Collections: a year's recap turned into Collections that can be browsed and played.

Only on a press, and never twice. This route answers the four lists the closing card offers, each
with the files it would hold for the reader now and the Collection already kept under its name, if
there is one; the client creates the rest through the Collections routes themselves, so each
Collection, and each file put in it, is the reader's own act in History, and a hidden file is
refused there for a locked reader as it is everywhere else. The files are chosen here over the
reader's vault as it stands: a file the reader may not act on is not offered.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer, arrivals
from sift.kernel.db import Database, in_clause
from sift.kernel.wire import Wire
from sift.slices.auth import require_admin
from sift.slices.insights import store
from sift.slices.insights.metrics import split_file_key
from sift.slices.insights.recaps_periods import PeriodKind, period_from_key
from sift.slices.insights.recaps_recipes import _ranked, _totals

router = APIRouter(tags=["insights"])

#: The most files a kept list holds.
MOST = 100

#: The people whose most viewed file goes into "People of".
PEOPLE = 20

#: Each list, in the order the sheet offers it: its key, its name, the figure it ranks files by,
#: and whether it is ticked when the sheet opens.
LISTS: tuple[tuple[str, str, str, bool], ...] = (
    ("most_viewed", "Your {year}, most viewed", "sittings:file", True),
    ("people", "People of {year}", "viewed_ms:person", True),
    ("rediscovered", "Rediscoveries of {year}", "rediscovered:file", True),
    ("new_favourites", "New favorites of {year}", "new_favourites:file", False),
)

_IN_FILES = "SELECT person_id, asset_id FROM asset_people WHERE person_id IN (?*)"


class KeptList(Wire):
    """One Collection the press can keep: its name, its files, and the one kept, if any."""

    key: str
    name: str
    asset_ids: list[str]
    ticked: bool
    #: The Collection already kept under this name, so a second press links to it.
    kept: str | None = None


class KeepSheet(Wire):
    lists: list[KeptList]


async def _people_files(database: Database, people: list[str], files: list[str]) -> list[str]:
    """Each person's most viewed file, one each, a file once, in the people's order."""
    if not people:
        return []
    sql, params = in_clause(_IN_FILES, people)
    holds: dict[str, set[str]] = {}
    for row in await database.fetch_all(sql, params):
        holds.setdefault(str(row["person_id"]), set()).add(str(row["asset_id"]))
    chosen: list[str] = []
    for person in people:
        found = next(
            (one for one in files if one in holds.get(person, ()) and one not in chosen), None
        )
        if found is not None:
            chosen.append(found)
    return chosen


async def _kept(access: Repository, viewer: Viewer, name: str) -> str | None:
    page = await access.list_collections(viewer, name, limit=50)
    return next((one.id for one in page.items if one.name == name), None)


def _files(ranked: Mapping[str, list[str]], metric: str) -> list[str]:
    return [split_file_key(key)[1] for key in ranked[metric]]


@router.get("/insights/recaps/{recap_id}/keep")
async def keep_sheet(
    recap_id: str,
    database: Annotated[Database, Depends(wiring.database)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> KeepSheet:
    """The Collections a year's recap can be kept as, each with the files the reader may put in
    it now, and the one already kept. Creating them is the Collections routes'."""
    found = await store.recap(database, viewer.id, recap_id)
    period = None if found is None else period_from_key(found.period)
    if period is None or period.kind is not PeriodKind.YEAR:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    metrics = {metric for _, _, metric, _ in LISTS}
    totals = (await _totals(database, viewer.id, period.first, period.last, metrics)).of(
        period.first, period.last
    )
    ranked = {metric: _ranked(totals, metric) for metric in metrics}
    most = _files(ranked, "sittings:file")
    people = await _people_files(database, ranked["viewed_ms:person"][:PEOPLE], most)
    chosen = {
        "most_viewed": most,
        "people": people,
        "rediscovered": _files(ranked, "rediscovered:file"),
        "new_favourites": _files(ranked, "new_favourites:file"),
    }
    # Files since gone from the library are not offered, nor asked about.
    known = {
        str(row["id"])
        for row in await arrivals.file_names(
            database.fetch_all, sorted({one for files in chosen.values() for one in files})
        )
    }
    lists: list[KeptList] = []
    for key, name, _, ticked in LISTS:
        there = [one for one in chosen[key] if one in known][: MOST * 2]
        allowed = (await access.actionable_of(viewer, there)).allowed if there else ()
        if not allowed:
            continue
        called = name.format(year=period.first.year)
        lists.append(
            KeptList(
                key=key,
                name=called,
                asset_ids=list(allowed[:MOST]),
                ticked=ticked,
                kept=await _kept(access, viewer, called),
            )
        )
    return KeepSheet(lists=lists)
