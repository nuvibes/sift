# SPDX-License-Identifier: AGPL-3.0-or-later
"""The kernel's writes that a screen draws tell whoever is drawing it.

One test per writer that says so itself. Each subscribes a window that did not make the change,
makes it, and reads what that window was told: a Site, a Username or a person made, a file put
under one, a folder given or taken back, a library added or moved, a folder moved or forgotten, a
file's record written, a run closing, a tidying done, a tunnel measured for hosting. Where a write
changes nothing, the window hears nothing, because a re-read for a change that never happened is
the noise the change bus exists to avoid.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

# Imported for their side effect: registering the tables the stash-boxes keep, so a kernel
# database has them.
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.composition import LibraryFiling, LibraryNaming
from sift.kernel import changes, tidy
from sift.kernel.access import Repository, catalog
from sift.kernel.access.catalog import by_sift
from sift.kernel.changes import About, ChangeBus
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.content.library import LibraryStore
from sift.kernel.db import Database
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.ledger import Actor
from sift.kernel.secret_store import SecretStore
from sift.kernel.tunnels import store as tunnel_store_module
from sift.kernel.tunnels.store import TunnelStore
from sift.kernel.vocabulary import VIA_STASH_LIBRARY
from sift.slices.tags_ratings.service import TagService
from sift.testing.fixtures import World

pytestmark = pytest.mark.anyio

#: Any id at all. What these writes reach is every admin, settled at the moment of sending.
ANOTHER_WINDOW = "01HX00000000000000000WIN2"
BOX = "01HX0000000000000000000503"
AT = 1_700_000_000


@pytest.fixture
async def told(temp_db: Database) -> AsyncIterator[ChangeBus]:
    """A bus the writes below are heard on, put back afterwards so no later test collects them.

    The database is brought to its schema first, so a write reached without the world's own
    building has its tables."""
    await temp_db.initialize_schema()
    bus = ChangeBus()
    changes.listens(bus)
    yield bus
    changes.listens(None)


# --- the catalog -----------------------------------------------------------------------------


async def test_a_site_made_tells_every_admin_and_a_site_found_tells_nobody(
    temp_db: Database, told: ChangeBus
) -> None:
    window = told.subscribe(ANOTHER_WINDOW)
    await catalog.ensure_site(temp_db, "Moonquarry", made=by_sift("download"))
    made = window.take(as_admin=True).about
    await catalog.ensure_site(temp_db, "Moonquarry", made=by_sift("download"))
    again = window.take(as_admin=True).about

    assert made == (About.LIBRARY,), "a Site was made and the Sites wall was not told"
    assert again == (), "a Site that already existed was announced as made"


async def test_a_username_seeded_tells_every_admin(temp_db: Database, told: ChangeBus) -> None:
    window = told.subscribe(ANOTHER_WINDOW)
    await catalog.seed_site_username(
        temp_db, site="Moonquarry", name="quillfeather", made=by_sift("download")
    )

    assert window.take(as_admin=True).about == (About.LIBRARY,)


async def test_a_file_filed_under_a_username_tells_every_admin(
    temp_db: Database, world: World, told: ChangeBus
) -> None:
    _, username_id = await catalog.seed_site_username(
        temp_db, site="Moonquarry", name="quillfeather", made=by_sift("download")
    )
    window = told.subscribe(ANOTHER_WINDOW)
    await catalog.link_username_to_asset(
        temp_db, asset_id=world.solo, username_id=username_id, source="download"
    )

    assert window.take(as_admin=True).about == (About.LIBRARY,)


async def test_a_file_filed_under_a_site_tells_every_admin(
    temp_db: Database, world: World, told: ChangeBus
) -> None:
    window = told.subscribe(ANOTHER_WINDOW)
    await catalog.link_asset_to_site(
        temp_db, asset_id=world.solo, site="Moonquarry", made=by_sift("download")
    )

    assert window.take(as_admin=True).about == (About.LIBRARY,)


async def test_a_file_filed_under_a_named_username_tells_every_admin(
    temp_db: Database, world: World, told: ChangeBus
) -> None:
    window = told.subscribe(ANOTHER_WINDOW)
    await catalog.file_asset_under_username(
        temp_db,
        asset_id=world.solo,
        site="Moonquarry",
        name="quillfeather",
        made=by_sift("stash"),
        source="stash_box",
    )

    assert window.take(as_admin=True).about == (About.LIBRARY,)


async def test_a_person_made_from_a_download_tells_every_admin(
    temp_db: Database, world: World, told: ChangeBus
) -> None:
    window = told.subscribe(ANOTHER_WINDOW)
    person_id = await catalog.attribute_to_person(
        temp_db,
        asset_id=world.solo,
        name="Bryn Calloway",
        create_if_unknown=True,
        made=by_sift("download"),
    )

    assert person_id is not None
    assert window.take(as_admin=True).about == (About.LIBRARY,)


async def test_a_number_typed_onto_a_username_tells_every_admin(
    temp_db: Database, told: ChangeBus
) -> None:
    _, username_id = await catalog.seed_site_username(
        temp_db, site="Moonquarry", name="quillfeather", made=by_sift("download")
    )
    window = told.subscribe(ANOTHER_WINDOW)
    typed = await catalog.set_username_number(temp_db, username_id=username_id, number="4417")

    assert typed.outcome == "written"
    assert window.take(as_admin=True).about == (About.LIBRARY,)


async def test_the_marks_on_a_person_tell_every_admin(
    temp_db: Database, world: World, told: ChangeBus
) -> None:
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at, slug) VALUES (?, ?, ?, 0, ?)",
        (BOX, "Lanternbox", "https://lanternbox.example/graphql", "lanternbox"),
    )
    # A person a stash-box's answer made, which is the only kind a pass's word is written onto.
    await temp_db.execute(
        "UPDATE people SET created_by_kind = 'sift', created_by_via = 'stash' WHERE id = ?",
        (world.person,),
    )
    window = told.subscribe(ANOTHER_WINDOW)

    await catalog.mark_pmv_creator(temp_db, world.person)
    pmv = window.take(as_admin=True).about
    await catalog.mark_made_via(temp_db, "person", world.person, VIA_STASH_LIBRARY)
    via = window.take(as_admin=True).about
    await catalog.mark_created_by_box(temp_db, "person", world.person, BOX)
    by_box = window.take(as_admin=True).about
    await catalog.clear_created_by_box(temp_db, BOX)
    cleared = window.take(as_admin=True).about
    await catalog.record_enrichment(temp_db, "person", world.person, BOX, automatic=False, at=AT)
    enriched = window.take(as_admin=True).about

    assert pmv == (About.LIBRARY,), "the maker-of-edits mark moved and nobody was told"
    assert via == (About.LIBRARY,), "how a person was made moved and nobody was told"
    assert by_box == (About.LIBRARY,), "which box made a person moved and nobody was told"
    assert cleared == (About.LIBRARY,), "a forgotten box's marks went and nobody was told"
    assert enriched == (About.LIBRARY,), "a box's run on a person landed and nobody was told"


# --- the composition root --------------------------------------------------------------------


async def test_a_person_a_stash_box_names_is_made_and_told(
    temp_db: Database, access: Repository, told: ChangeBus
) -> None:
    naming = LibraryNaming(temp_db, TagService(temp_db, access))
    window = told.subscribe(ANOTHER_WINDOW)

    assert await naming.person_named("Bryn Calloway", creating=True) is not None
    assert window.take(as_admin=True).about == (About.LIBRARY,)


async def test_a_person_a_stash_box_puts_on_a_file_is_told(
    temp_db: Database, access: Repository, world: World, told: ChangeBus
) -> None:
    filing = LibraryFiling(temp_db, TagService(temp_db, access))
    window = told.subscribe(ANOTHER_WINDOW)
    await filing.attribute(world.twin, world.person, source="stash_box")

    assert window.take(as_admin=True).about == (About.LIBRARY,)


# --- the folders given to Sift, the libraries and their folders -------------------------------


async def test_a_folder_given_and_taken_back_tells_every_admin(
    library_store: LibraryStore, tmp_path: Path, told: ChangeBus
) -> None:
    folder = tmp_path / "given"
    folder.mkdir()
    window = told.subscribe(ANOTHER_WINDOW)

    grant = await library_store.grant(folder)
    given = window.take(as_admin=True).about
    await library_store.revoke_grant(grant.id)
    taken = window.take(as_admin=True).about

    assert given == (About.LIBRARY,), "a folder was given and the list of them was not told"
    assert taken == (About.LIBRARY,), "a folder was taken back and the list was not told"


async def test_a_library_added_moved_and_removed_is_told_each_time(
    library_store: LibraryStore, tmp_path: Path, told: ChangeBus
) -> None:
    first = tmp_path / "shelf"
    first.mkdir()
    second = tmp_path / "shelf-moved"
    second.mkdir()
    window = told.subscribe(ANOTHER_WINDOW)

    root = await library_store.create_root(name="shelf", abs_path=first)
    added = window.take(as_admin=True).about
    await library_store.repoint_root(root.id, second)
    moved = window.take(as_admin=True).about
    await library_store.delete_root(root.id, actor=Actor.sift("folder"))
    removed = window.take(as_admin=True).about

    assert added == (About.LIBRARY,)
    assert moved == (About.LIBRARY,)
    assert About.ARRIVALS in removed, "a library went and the walls holding its files were not told"


async def test_a_folder_moved_and_forgotten_is_told_as_files_moving(
    library_store: LibraryStore, tmp_path: Path, told: ChangeBus
) -> None:
    shelf = tmp_path / "shelf"
    shelf.mkdir()
    root = await library_store.create_root(name="shelf", abs_path=shelf)
    folder = await library_store.upsert_folder(root.id, "trips")
    window = told.subscribe(ANOTHER_WINDOW)

    await library_store.move_folder(folder, "journeys", actor=Actor.sift("folder"))
    moved = window.take(as_admin=True).about
    await library_store.remove_folder(folder.id)
    forgotten = window.take(as_admin=True).about

    assert moved == (About.ARRIVALS,), "a folder moved and the files under it were not told"
    assert forgotten == (About.ARRIVALS,), "a folder went and the files under it were not told"


# --- a file's record ---------------------------------------------------------------------------


async def test_a_files_record_written_tells_whoever_draws_it(
    content_store: ContentStore, world: World, told: ChangeBus
) -> None:
    window = told.subscribe(ANOTHER_WINDOW)

    await content_store.set_title(world.solo, "Harbour at dusk")
    titled = window.take(as_admin=True).about
    await content_store.set_links(world.solo, ["https://example.com/harbour"])
    linked = window.take(as_admin=True).about
    await content_store.seed_download_url(world.solo, "https://example.com/harbour.mp4")
    seeded = window.take(as_admin=True).about
    await content_store.seed_download_url(world.solo, "https://example.com/other.mp4")
    seeded_again = window.take(as_admin=True).about
    await content_store.seed_music(world.solo, "paper boats on the river")
    sung = window.take(as_admin=True).about

    assert titled == (About.LIBRARY,), "a title was written and the file page was not told"
    assert linked == (About.LIBRARY,), "the links were written and nobody was told"
    assert seeded == (About.LIBRARY,), "where the file came from was written and nobody was told"
    assert seeded_again == (), "a seed that met a filled column was announced"
    assert sung == (About.LIBRARY,), "a song was named on the file and nobody was told"


# --- runs, tidying, tunnels --------------------------------------------------------------------


async def test_a_run_closing_rings_the_jobs_bell(temp_db: Database, told: ChangeBus) -> None:
    await temp_db.initialize_schema()
    book = Ledger(
        temp_db,
        machine="a box",
        profile="abc123",
        version="0.1.0",
        families_of={"probe": Family.SCAN},
    )
    await book.start()
    book.started("probe")
    await book.settle({"probe": 1}, settings={})
    window = told.subscribe(ANOTHER_WINDOW)
    await book.settle({}, settings={})

    assert About.JOBS in window.take(as_admin=True).about


async def test_a_tidying_done_and_a_survey_kept_ring_the_jobs_bell(
    temp_db: Database, settings: Settings, told: ChangeBus
) -> None:
    resources = tidy.Resources(database=temp_db, settings=settings)
    await temp_db.execute(
        "INSERT INTO jobs (id, type, payload, state, created_at, updated_at)"
        " VALUES ('01HX00000000000000000JOB1', 'probe', '{}', 'failed', 0, 0)"
    )
    failures = tidy.SettledFailures(resources)
    window = told.subscribe(ANOTHER_WINDOW)

    await tidy.remember_survey(resources, failures)
    surveyed = window.take(as_admin=True).about
    assert await failures.run() == 1
    cleared = window.take(as_admin=True).about

    assert surveyed == (About.JOBS,), "a survey was kept and Maintenance was not told"
    assert cleared == (About.JOBS,), "failed jobs were cleared and Activity was not told"


async def test_a_tunnel_measured_for_hosting_tells_every_admin(
    temp_db: Database, told: ChangeBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def accepts(_config: str, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(tunnel_store_module, "validate_config", accepts)
    await temp_db.initialize_schema()
    store = TunnelStore(temp_db, SecretStore(temp_db))
    tunnel_id = await store.add(
        name="Fjord",
        config="[Interface]\nPrivateKey = x\n\n[Peer]\nPublicKey = y\n",
        master_key=bytes(32),
    )
    window = told.subscribe(ANOTHER_WINDOW)
    await store._set_can_host(tunnel_id, can=False)

    assert window.take(as_admin=True).about == (About.SETTINGS,)
