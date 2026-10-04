# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folders a swap made, read back from the swap's record for the folder pass.

The pass that reads folder names for people must never give the swap's folder, its `People` or its
`Sites` to one person, and must read each person's folder under `People` by its name. It knows
those folders only through this read, so the read is held to the folders the landing really makes
(`ingest.folder_name`) and to nothing else, a folder somebody named `Swap-...` by hand included.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sift.slices.swap.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Role
from sift.kernel.content import LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.swap.ingest import (
    ArrivingPerson,
    LandingSession,
    Received,
    folder_name,
    record_folder,
    swap_folder,
)
from sift.slices.swap.landed_folders import folders_made
from sift.slices.swap.store import SessionStore
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

SESSION = "01KZTESTSESSION7K3QM2RD"
OTHER_SESSION = "01KZTESTSESSION0NOTHING"


@pytest.fixture
async def library(temp_db: Database, library_store: LibraryStore, tmp_path: Path) -> Root:
    directory = tmp_path / "library"
    directory.mkdir()
    return await library_store.create_root(name="Library", abs_path=directory)


def _where(person: str | None, site: str | None, dest: str) -> str:
    """Where the landing puts a file of this person, or of this Site, or of neither."""
    here = LandingSession(id=SESSION, peer_device="peer", dest_folder_id=dest, wanted=frozenset())
    taken = [ArrivingPerson(name=person)] if person else []
    received = Received(key="k", staged=Path("x"), digest="", title="", site=site)
    return folder_name(received, taken, session=here)


async def test_the_swaps_folder_its_people_and_its_sites_are_containers_and_each_person_is_by_name(
    temp_db: Database, library_store: LibraryStore, library: Root
) -> None:
    received = await library_store.upsert_folder(library.id, "Received")
    admin = await create_user(temp_db, Role.ADMIN)
    sessions = SessionStore(temp_db)
    await sessions.create(
        SESSION, role="guest", started_at=0, started_by=admin.id, dest_folder_id=received.id
    )
    # A swap that landed nothing made no folder, and a session that only sent chose none.
    await sessions.create(
        OTHER_SESSION, role="guest", started_at=0, started_by=admin.id, dest_folder_id=received.id
    )
    await sessions.create(
        "01KZTESTSESSIONSENDONLY1", role="host", started_at=0, started_by=admin.id
    )
    folders = {}
    for person, site in (("Nadia Vance", None), ("Priya Sandoval", None), (None, "Northlight")):
        path = f"Received/{_where(person, site, received.id)}"
        folders[person or site] = (await library_store.upsert_folder(library.id, path)).id
    top = await library_store.upsert_folder(
        library.id, f"Received/{_where(None, None, received.id)}"
    )
    # What the landing records as it makes the swap's folder.
    here = LandingSession(
        id=SESSION, peer_device="peer", dest_folder_id=received.id, wanted=frozenset()
    )
    await record_folder(temp_db, here, top.id)
    people = await library_store.upsert_folder(library.id, f"{top.rel_path}/People")
    sites = await library_store.upsert_folder(library.id, f"{top.rel_path}/Sites")
    # Folders somebody made: one called like a swap's anywhere else, and one beside the swap's.
    await library_store.upsert_folder(library.id, f"Elsewhere/{top.name}")
    await library_store.upsert_folder(library.id, "Received/Swap-NOTASWAP")
    # And one a person made inside a person's folder, which the landing never makes.
    await library_store.upsert_folder(library.id, f"{top.rel_path}/People/Nadia Vance/Extras")

    made = await folders_made(temp_db)

    assert made.containers == {top.id, people.id, sites.id, folders["Northlight"]}
    assert made.people == {folders["Nadia Vance"], folders["Priya Sandoval"]}


async def test_a_library_with_no_swap_has_no_swap_folders(
    temp_db: Database, library_store: LibraryStore, library: Root
) -> None:
    await library_store.upsert_folder(library.id, "Received/Swap-7K3QM2RD/People/Nadia Vance")

    made = await folders_made(temp_db)

    assert made.containers == frozenset()
    assert made.people == frozenset()


async def test_a_swap_s_folder_renamed_is_still_the_swap_s(
    temp_db: Database, library_store: LibraryStore, library: Root
) -> None:
    """Known by the id the landing kept, so a person renaming it takes nothing away; and a second
    landing never moves what the first one recorded."""
    received = await library_store.upsert_folder(library.id, "Received")
    admin = await create_user(temp_db, Role.ADMIN)
    await SessionStore(temp_db).create(
        SESSION, role="guest", started_at=0, started_by=admin.id, dest_folder_id=received.id
    )
    here = LandingSession(
        id=SESSION, peer_device="peer", dest_folder_id=received.id, wanted=frozenset()
    )
    nadia = await library_store.upsert_folder(
        library.id, f"Received/{_where('Nadia Vance', None, received.id)}"
    )
    top = await library_store.upsert_folder(
        library.id, f"Received/{_where(None, None, received.id)}"
    )
    await record_folder(temp_db, here, top.id)
    await record_folder(temp_db, here, received.id)

    renamed = await library_store.move_folder(
        top, "Received/Holiday swap", actor=Actor.user(admin.id)
    )
    assert renamed is not None and renamed.id == top.id

    made = await folders_made(temp_db)

    assert top.id in made.containers
    assert made.people == {nadia.id}


async def test_a_session_landed_before_the_record_is_known_by_its_folder_s_name_once(
    temp_db: Database, library_store: LibraryStore, library: Root
) -> None:
    """Swap 4: a session that landed files before the column keeps its folder, found once by the
    name the landing gave it; a session whose folder was renamed before then stays unknown."""
    from sift.slices.swap.schema import initialize

    received = await library_store.upsert_folder(library.id, "Received")
    admin = await create_user(temp_db, Role.ADMIN)
    sessions = SessionStore(temp_db)
    for one in (SESSION, OTHER_SESSION):
        await sessions.create(
            one, role="guest", started_at=0, started_by=admin.id, dest_folder_id=received.id
        )
    top = await library_store.upsert_folder(library.id, f"Received/Swap-{SESSION[-8:]}")
    await library_store.upsert_folder(library.id, "Received/Renamed by hand")

    async with temp_db.write() as connection:
        await initialize(connection, 3)
        await initialize(connection, 3)

    made = await folders_made(temp_db)
    assert made.containers == {top.id}


async def test_a_swap_s_folder_deleted_since_is_gone_from_the_answer(
    temp_db: Database, library_store: LibraryStore, library: Root
) -> None:
    """The record keeps the id of a folder somebody has deleted since: nothing is made of it, and
    another swap's folder is still read."""
    received = await library_store.upsert_folder(library.id, "Received")
    admin = await create_user(temp_db, Role.ADMIN)
    sessions = SessionStore(temp_db)
    folders = {}
    for one in (SESSION, OTHER_SESSION):
        await sessions.create(
            one, role="guest", started_at=0, started_by=admin.id, dest_folder_id=received.id
        )
        here = LandingSession(
            id=one, peer_device="peer", dest_folder_id=received.id, wanted=frozenset()
        )
        folders[one] = await library_store.upsert_folder(
            library.id, f"Received/{swap_folder(here)}"
        )
        await record_folder(temp_db, here, folders[one].id)
    await temp_db.execute("DELETE FROM folders WHERE id = ?", (folders[SESSION].id,))

    made = await folders_made(temp_db)

    assert made.containers == {folders[OTHER_SESSION].id}
