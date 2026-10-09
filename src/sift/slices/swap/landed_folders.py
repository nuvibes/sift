# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folders a swap made to hold what it brought, known from its record and never by name."""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.content import TreeReads
from sift.kernel.db import Database
from sift.slices.swap.ingest import PEOPLE_FOLDER, SITES_FOLDER

_LANDED = "SELECT folder_id FROM swap_sessions WHERE folder_id IS NOT NULL"


@dataclass(frozen=True, slots=True)
class MadeFolders:
    """The folders swaps made, by what each one is."""

    containers: frozenset[str] = frozenset()
    people: frozenset[str] = frozenset()


async def folders_made(database: Database) -> MadeFolders:
    """Every folder a swap made that is still in the library, by what it is."""
    tree = TreeReads(database)
    containers: set[str] = set()
    people: set[str] = set()
    for session in await database.fetch_all(_LANDED):
        top_id = str(session["folder_id"])
        place = await tree.place_of(top_id)
        if place is None:
            continue
        root_id, top_path = place
        containers.add(top_id)
        # Never the library's own folder, so the path is never empty.
        inside = f"{top_path}/"
        for folder_id, rel_path in await tree.folders_inside(root_id, top_path):
            parts = rel_path[len(inside) :].split("/")
            if parts[0] == SITES_FOLDER or parts == [PEOPLE_FOLDER]:
                containers.add(folder_id)
            elif parts[0] == PEOPLE_FOLDER and len(parts) == 2:
                people.add(folder_id)
    return MadeFolders(containers=frozenset(containers), people=frozenset(people))
