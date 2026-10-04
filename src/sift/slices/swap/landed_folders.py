# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folders a swap made to hold what it brought, read back from what the swap recorded.

Everything one swap lands goes under one folder, `Swap-<short id>`, in the folder the receiver
chose, and inside it under `People/<person>`, `Sites/<Site>` or the swap's folder itself (see
`ingest.folder_name`). The pass that reads folder names for people has to know those folders for
what they are: the swap's folder, its `People` and its `Sites` are CONTAINERS of what arrived and
never one person's, and a person's folder under `People` is hers by its name. The folder pass has
no other way to tell, because its faces say whoever has the most files in it.

**Known from the record, never from the name.** A session keeps the id of the folder its landing
made (`swap_sessions.folder_id`), so the swap's folder is that folder wherever it is and whatever
it is called now: a person renaming it, or a folder inside it, does not stop it being the swap's.
A folder somebody named `Swap-...` by hand is not one, however it is spelled.

Read-only, and only these rows: the sessions, then a seek per session by the folder's id, then
the range of paths under each swap's folder. Nothing here grows with the library except
through a folder a swap made. The folders are read through the kernel (`TreeReads`), whose
unscoped reads are the ones a pass that runs for nobody is given.
"""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.content import TreeReads
from sift.kernel.db import Database
from sift.slices.swap.ingest import PEOPLE_FOLDER, SITES_FOLDER

#: Every session whose landing made a folder. A session that sent only, or landed nothing, has none.
_LANDED = "SELECT folder_id FROM swap_sessions WHERE folder_id IS NOT NULL"


@dataclass(frozen=True, slots=True)
class MadeFolders:
    """The folders swaps made, by what each one is."""

    #: The swap's folder, its `People` and `Sites`, and every folder under `Sites`.
    containers: frozenset[str] = frozenset()
    #: Each person's own folder, directly under `People`.
    people: frozenset[str] = frozenset()


async def folders_made(database: Database) -> MadeFolders:
    """Every folder a swap made that is still in the library, by what it is.

    A swap that landed nothing made no folder, and a folder somebody has deleted since is gone
    from the answer with it.
    """
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
        # Never the library's own folder (it sits inside the one the swap chose), so its path is
        # never empty and the folders inside it are the ones under the swap's folder alone.
        inside = f"{top_path}/"
        for folder_id, rel_path in await tree.folders_inside(root_id, top_path):
            parts = rel_path[len(inside) :].split("/")
            if parts[0] == SITES_FOLDER or parts == [PEOPLE_FOLDER]:
                containers.add(folder_id)
            elif parts[0] == PEOPLE_FOLDER and len(parts) == 2:
                people.add(folder_id)
    return MadeFolders(containers=frozenset(containers), people=frozenset(people))
