# SPDX-License-Identifier: AGPL-3.0-or-later
"""The receipt each side keeps, and the Undo that takes a landing back.

The Undo runs against a real library on a real disk: three hundred pictures landed through the one
import, as a swap lands them, then taken back through the delete feature's own remover. What stays
is asserted as hard as what goes: a file somebody else put in the swap's folder, a file the swap
found already here, and a person this library already had.
"""

from __future__ import annotations

import dataclasses
import io
import random
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, FolderRow, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.delete.service import Deleter
from sift.slices.library_roots.service import LibraryService
from sift.slices.swap import receipt
from sift.slices.swap.ingest import ArrivingPerson, LandingSession
from sift.slices.swap.models import ReceiptFolder, ReceiptUndo
from sift.slices.swap.store import SessionStore
from sift.slices.swap.tests.test_ingest import (  # noqa: F401 (the landing's own fixtures)
    PEER,
    Lander,
    _received,
    _staged,
    context,
    lander,
    library,
    received_folder,
    session,
)
from sift.slices.swap.tests.test_session import VIEWER, _database
from sift.testing.fixtures import create_user

#: The Undo's measure: three hundred files, as the brief asks it to be proven on.
LANDED = 300

#: The most files one removal may take, so no one write block holds the writer for the whole set.
FILES_PER_BLOCK = 50


def _picture(number: int) -> bytes:
    """A small JPEG of its own: noise seeded by the number, so each is a file of its own."""
    out = io.BytesIO()
    noise = random.Random(number).randbytes(16 * 16 * 3)
    Image.frombytes("RGB", (16, 16), noise).save(out, "JPEG", quality=90)
    return out.getvalue()


@pytest.fixture
async def admin(temp_db: Database, access: Repository) -> Viewer:
    """A real user (removals name theirs by foreign key), once the schema is there."""
    return await create_user(temp_db, Role.ADMIN)


@pytest.fixture
def remover(
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    access: Repository,
) -> Deleter:
    cache = SimpleNamespace(discard_asset=lambda _asset_id: 0)
    return Deleter(
        temp_db,
        content_store,
        library_store,
        access,
        cache,
        LibraryService(temp_db, library_store, access),
    )


async def _count(database: Database, statement: str, *params: object) -> int:
    row = await database.fetch_one(statement, params)
    assert row is not None
    return int(row["n"])


@pytest.mark.integration
async def test_the_undo_takes_back_every_file_the_swap_landed_and_nothing_else(
    lander: tuple[Lander, Any, Any],  # noqa: F811
    session: LandingSession,  # noqa: F811
    temp_db: Database,
    library: Root,  # noqa: F811
    received_folder: FolderRow,  # noqa: F811
    library_store: LibraryStore,
    content_store: ContentStore,
    access: Repository,
    remover: Deleter,
    admin: Viewer,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _told, _faces = lander
    base = Path(library.abs_path)
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO people (id, name, name_sort, created_at) VALUES ('p-ava', ?, ?, 1)",
            ("Ava Example", "ava example"),
        )
        # A Site this library made itself: the swap files under it and never removes it.
        await connection.execute(
            "INSERT INTO sites (id, name, name_sort, created_at) VALUES ('s-nl', ?, ?, 1)",
            ("Northlight Media", "northlight media"),
        )
    # A file already here, outside the swap's folder: the swap lands the same bytes beside it.
    mine = base / "Mine" / "kept.jpg"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(_picture(LANDED))
    already = await content_store.ingest(
        verify_ingress(mine, origin=Origin.SCAN, settings=settings),
        root_id=library.id,
        rel_path="Mine/kept.jpg",
    )

    keys = [f"k{n}" for n in range(LANDED)] + ["kdup"]
    landing = LandingSession(
        id=session.id,
        peer_device=PEER,
        dest_folder_id=session.dest_folder_id,
        wanted=frozenset(keys),
    )
    for number, key in enumerate(keys):
        data = _picture(LANDED if key == "kdup" else number)
        fields: dict[str, Any] = {"key": key, "name": f"{key}.jpg"}
        if number < 150:
            fields["people"] = (ArrivingPerson(name="Bryn Calloway", aliases=("bryncalloway",)),)
        elif number < 200:
            fields["people"] = (ArrivingPerson(name="Ava Example", local_id="p-ava"),)
        elif number < 270:
            fields["site"], fields["username"] = "Clipyard", f"clipyard{number % 4}"
        elif number < 280:
            # Under the Site alone: its nameless row, which goes with the Site.
            fields["site"] = "Clipyard"
        elif key == "kdup":
            # The file found already here keeps what the swap filed it under: that username
            # stays, and holds its Site.
            fields["site"], fields["username"] = "Northlight Media", "northlight"
        await run(_received(_staged(tmp_path, f"{key}.part", data), **fields), session=landing)
    # Somebody else's file in the swap's own folder: never the swap's to take back.
    top = f"{received_folder.rel_path}/Swap-{session.short_id}"
    theirs = base / top / "theirs.jpg"
    theirs.write_bytes(_picture(LANDED + 1))
    top_folder = await library_store.folder_at(library.id, top)
    assert top_folder is not None
    await content_store.ingest(
        verify_ingress(theirs, origin=Origin.SCAN, settings=settings),
        root_id=library.id,
        rel_path=f"{top}/theirs.jpg",
        folder_id=top_folder.id,
    )

    store = SessionStore(temp_db)
    assert await store.end(session.id, state="done", reason="done", now=int(time.time()))
    row = await store.get(session.id)
    assert row is not None and row.folder_id == top_folder.id
    before = await _count(temp_db, "SELECT COUNT(*) AS n FROM assets")
    assert before == LANDED + 2

    taken: list[int] = []
    real = remover.remove_many

    async def counted(asset_ids: Any, **kwargs: Any) -> Any:
        taken.append(len(asset_ids))
        return await real(asset_ids, **kwargs)

    monkeypatch.setattr(remover, "remove_many", counted)
    began = time.monotonic()
    undone = await receipt.undo(
        row, database=temp_db, library=library_store, access=access, remover=remover, actor=admin
    )
    took = time.monotonic() - began
    monkeypatch.undo()
    # The lane's figure: 301 files in 1.8 to 2.0 s, the longest write block 48 to 76 ms.
    assert took < 30

    assert (undone.files, undone.people, undone.sites, undone.left) == (LANDED + 1, 1, 1, 1)
    # Counted, not timed: a hosted runner's clock says how loaded it is, not how long a block is.
    assert max(taken) <= FILES_PER_BLOCK and len(taken) >= LANDED // FILES_PER_BLOCK
    # From the library: every file the swap brought, and the copy it laid beside the one here.
    assert await _count(temp_db, "SELECT COUNT(*) AS n FROM assets") == 2
    assert await content_store.get(already.asset.id) is not None
    assert len(await content_store.locations(already.asset.id)) == 1
    # From the disk: nothing the swap wrote is left; what was not its own is.
    left_on_disk = sorted(path.name for path in (base / top).rglob("*") if path.is_file())
    assert left_on_disk == ["theirs.jpg"]
    assert mine.exists()
    # What it made and nothing holds now: the new person, the Site and its usernames.
    assert (
        await _count(temp_db, "SELECT COUNT(*) AS n FROM people WHERE name = 'Bryn Calloway'") == 0
    )
    assert await _count(temp_db, "SELECT COUNT(*) AS n FROM people_aliases") == 0
    assert await _count(temp_db, "SELECT COUNT(*) AS n FROM people WHERE id = 'p-ava'") == 1
    assert await _count(temp_db, "SELECT COUNT(*) AS n FROM sites WHERE name = 'Clipyard'") == 0
    assert await _count(temp_db, "SELECT COUNT(*) AS n FROM sites WHERE id = 's-nl'") == 1
    kept_names = await temp_db.fetch_all("SELECT name FROM usernames ORDER BY name", ())
    assert [row["name"] for row in kept_names] == ["northlight"]
    # A row already gone is not counted twice.
    assert await receipt._let_go(temp_db, receipt._Made(people=("p-gone",))) == (0, 0)

    # Said on the receipt, once.
    when = int(time.time())
    said = ReceiptUndo(at=when, files=undone.files, from_disk=True, people=1, sites=1)
    assert await receipt.mark_undone(temp_db, session.id, said)
    assert not await receipt.mark_undone(temp_db, session.id, said)
    kept = await receipt.read(temp_db, session.id)
    assert kept is not None and kept.undo == said


@pytest.mark.integration
async def test_an_emptied_swap_folder_goes_with_its_files(
    lander: tuple[Lander, Any, Any],  # noqa: F811
    session: LandingSession,  # noqa: F811
    temp_db: Database,
    library: Root,  # noqa: F811
    received_folder: FolderRow,  # noqa: F811
    library_store: LibraryStore,
    access: Repository,
    remover: Deleter,
    admin: Viewer,
    tmp_path: Path,
) -> None:
    run, _told, _faces = lander
    await run(_received(_staged(tmp_path, "k1.part", _picture(1)), name="k1.jpg"))
    store = SessionStore(temp_db)
    await store.end(session.id, state="done", reason="done", now=int(time.time()))
    row = await store.get(session.id)
    assert row is not None
    undone = await receipt.undo(
        row, database=temp_db, library=library_store, access=access, remover=remover, actor=admin
    )
    assert (undone.files, undone.left) == (1, 0)
    top = Path(library.abs_path) / received_folder.rel_path / f"Swap-{session.short_id}"
    assert not top.exists()
    assert await library_store.get_folder(str(row.folder_id)) is None
    # A second Undo finds nothing to take back.
    again = await receipt.undo(
        row, database=temp_db, library=library_store, access=access, remover=remover, actor=admin
    )
    assert again == receipt.Undone(files=0, people=0, sites=0, left=0)


class _Refusing:
    """A remover whose every removal is refused, as a read-only folder refuses it."""

    async def remove(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("read-only")

    async def remove_many(self, asset_ids: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(removed=0)

    async def remove_folder(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("read-only")


@pytest.mark.integration
async def test_a_refused_removal_is_counted_out_and_the_rest_goes_on(
    lander: tuple[Lander, Any, Any],  # noqa: F811
    session: LandingSession,  # noqa: F811
    temp_db: Database,
    library: Root,  # noqa: F811
    library_store: LibraryStore,
    content_store: ContentStore,
    access: Repository,
    admin: Viewer,
    settings: Settings,
    tmp_path: Path,
) -> None:
    run, _told, _faces = lander
    base = Path(library.abs_path)
    (base / "Mine").mkdir()
    (base / "Mine" / "kept.jpg").write_bytes(_picture(5))
    await content_store.ingest(
        verify_ingress(base / "Mine" / "kept.jpg", origin=Origin.SCAN, settings=settings),
        root_id=library.id,
        rel_path="Mine/kept.jpg",
    )
    await run(_received(_staged(tmp_path, "k1.part", _picture(5)), name="k1.jpg"))
    store = SessionStore(temp_db)
    await store.end(session.id, state="done", reason="done", now=int(time.time()))
    row = await store.get(session.id)
    assert row is not None
    undone = await receipt.undo(
        row,
        database=temp_db,
        library=library_store,
        access=access,
        remover=_Refusing(),
        actor=admin,
    )
    # Refused as a file another program holds open: counted, for a later Undo.
    assert (undone.files, undone.left, undone.in_use) == (0, 1, 1)

    class _Failing(_Refusing):
        async def remove(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("gone")

    failed = await receipt.undo(
        row,
        database=temp_db,
        library=library_store,
        access=access,
        remover=_Failing(),
        actor=admin,
    )
    assert (failed.files, failed.in_use) == (0, 0)


async def test_a_swap_with_no_folder_has_nothing_to_undo(temp_db: Database, admin: Viewer) -> None:
    store = SessionStore(temp_db)
    await store.create("01HUNDOWITHOUTAFOLDER0001", role="guest", started_at=1, started_by=admin.id)
    row = await store.get("01HUNDOWITHOUTAFOLDER0001")
    assert row is not None
    nothing = receipt.Undone(files=0, people=0, sites=0, left=0)
    kwargs: dict[str, Any] = {
        "database": temp_db,
        "access": None,
        "remover": _Refusing(),
        "actor": admin,
    }

    class _Gone:
        async def get_folder(self, folder_id: str) -> None:
            return None

    assert await receipt.undo(row, library=_Gone(), **kwargs) == nothing  # type: ignore[arg-type]
    gone = dataclasses.replace(row, folder_id="f-gone")
    assert await receipt.undo(gone, library=_Gone(), **kwargs) == nothing  # type: ignore[arg-type]


# --- the receipt, kept and read back -------------------------------------------------------------


@pytest.mark.integration
async def test_a_receipt_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "swap.sqlite3"
    database = await _database(path)
    session_id = "01HRECEIPT" + "SURVIVES000001"
    try:
        store = SessionStore(database)
        await store.create(
            session_id, role="guest", started_at=100, started_by=VIEWER, peer_device=PEER
        )
        await store.add_sent(session_id, files=2, sent_bytes=4000)
        await store.end(session_id, state="done", reason="done", now=160)
        body = {
            "groups": [
                {
                    "way": "landed",
                    "kind": "person",
                    "id": "p1",
                    "name": "Bryn Calloway",
                    "files": 2,
                    "bytes": 4000,
                    "filed": "created",
                    "faces": 3,
                    "usernames": 0,
                }
            ],
            "skipped": [{"kind": "site", "name": "Northlight Media", "files": 4}],
            "refused": 1,
            "held": 5,
            "cut_offs": 1,
            "moving_since": 120,
        }
        await receipt.keep(database, session_id, body, 160)
    finally:
        await database.close()

    # The restart: the same file opened afresh, its schema brought up as a boot does.
    database = Database(path)
    await database.connect()
    try:
        await database.initialize_schema()
        row = await SessionStore(database).get(session_id)
        kept = await receipt.read(database, session_id)
        assert row is not None and kept is not None and kept.undo is None
        drawn = receipt.view(
            row,
            kept,
            folder=ReceiptFolder(id="f1", path="Received/Swap-00000001"),
            tunnel="Tunnel one",
        )
        assert (drawn.files, drawn.bytes, drawn.seconds, drawn.rate) == (2, 4000, 60, 100.0)
        assert (drawn.cut_offs, drawn.refused, drawn.held) == (1, 1, 5)
        assert drawn.groups[0].filed == "created" and drawn.groups[0].faces == 3
        assert drawn.skipped[0].name == "Northlight Media"
        assert drawn.device == PEER and drawn.tunnel == "Tunnel one"
        # No folder kept on the row: nothing on disk to take back.
        assert not drawn.can_undo
    finally:
        await database.close()


@pytest.mark.integration
async def test_a_receipt_read_from_the_row_alone(tmp_path: Path) -> None:
    database = await _database(tmp_path / "swap.sqlite3")
    try:
        store = SessionStore(database)
        await store.create(
            "01HROWALONE0000000000001", role="host", started_at=10, started_by=VIEWER
        )
        await store.set_rate("01HROWALONE0000000000001", 800)
        live = await store.get("01HROWALONE0000000000001")
        assert live is not None
        # Still running: no length yet, and nothing to undo.
        drawn = receipt.view(live, None, folder=None, tunnel=None)
        assert drawn.seconds is None and drawn.rate == 100.0 and not drawn.can_undo
        await store.end("01HROWALONE0000000000001", state="ended", reason="ended_by_you", now=20)
        ended = await store.get("01HROWALONE0000000000001")
        assert ended is not None
        drawn = receipt.view(ended, receipt.Kept(body={}), folder=None, tunnel=None)
        assert (drawn.role, drawn.seconds, drawn.groups, drawn.reason) == (
            "host",
            10,
            [],
            "ended_by_you",
        )
        await database.execute(
            "UPDATE swap_sessions SET rate_bps = NULL WHERE id = ?", ("01HROWALONE0000000000001",)
        )
        bare = await store.get("01HROWALONE0000000000001")
        assert bare is not None and receipt.view(bare, None, folder=None, tunnel=None).rate is None
        assert await receipt.read(database, "01HROWALONE0000000000001") is None
        # A body that is not one reads as empty.
        await database.execute(
            "INSERT INTO swap_receipts (session_id, body, written_at) VALUES (?, ?, 1)",
            ("01HROWALONE0000000000001", "[1]"),
        )
        kept = await receipt.read(database, "01HROWALONE0000000000001")
        assert kept is not None and kept.body == {}
        await database.execute(
            "UPDATE swap_receipts SET body = 'not json' WHERE session_id = ?",
            ("01HROWALONE0000000000001",),
        )
        kept = await receipt.read(database, "01HROWALONE0000000000001")
        assert kept is not None and kept.body == {}
    finally:
        await database.close()


@pytest.mark.integration
async def test_a_library_from_before_gains_the_receipts_and_keeps_its_swaps(
    tmp_path: Path,
) -> None:
    """Version five, as somebody who upgrades has it: the receipts' table and the two indexes are
    added, a swap from before keeps its row, and its receipt reads from the row alone."""
    from sift.slices.swap import schema

    database = await _database(tmp_path / "old.sqlite3")
    try:
        async with database.write() as connection:
            await connection.execute("DROP TABLE swap_receipts")
            await connection.execute("DROP INDEX ix_swap_sessions_peer")
            await connection.execute(
                "INSERT INTO swap_sessions (id, role, state, started_at, ended_at, sent_files)"
                " VALUES ('01KZOLDSESSION00000000005', 'guest', 'done', 1, 9, 4)"
            )
            await schema.initialize(connection, on_disk=5)
        row = await SessionStore(database).get("01KZOLDSESSION00000000005")
        assert row is not None and row.folder_id is None
        assert await receipt.read(database, row.id) is None
        drawn = receipt.view(row, None, folder=None, tunnel=None)
        assert (drawn.files, drawn.seconds, drawn.can_undo) == (4, 8, False)
        index = await database.fetch_one(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'ix_swap_sessions_peer'"
        )
        assert index is not None
    finally:
        await database.close()
