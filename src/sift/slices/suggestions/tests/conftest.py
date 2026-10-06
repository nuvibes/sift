# SPDX-License-Identifier: AGPL-3.0-or-later
"""A library on a real disk, and a stand-in for the faces behind the seam that matches the
protocol structurally."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.attribution import FolderFaces, FolderStamp
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Connection, Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.suggestions.service import SuggestionService
from sift.slices.suggestions.store import Store
from sift.testing.fixtures import create_user

#: Files the ingress gate accepts. A file has to pass the gate to become an asset at all, and none
#: of these tests are about the gate.
CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


#: Ids for the people a test seeds, shaped like Sift's: a scoped read refuses a malformed id.
SOMEBODY_KNOWN = "01HX0000000000000000000K01"
NADIA = "01HX0000000000000000000K02"
ONE_JANE = "01HX0000000000000000000J01"
ANOTHER_JANE = "01HX0000000000000000000J02"


@dataclass(frozen=True, slots=True)
class Library:
    root: Root
    path: Path


@dataclass
class FakeFaces:
    """The face feature as this one asks about it, answered from values a test set.

    `named_here` and `piles_here` are keyed by the folder's own name rather than by its id, because
    a test writes the tree and does not know the ids, and reading a fixture that says
    `piles_here={"Nadia Vance": {"pile-1": 5}}` is the point.
    """

    on: bool = True
    looked: dict[str, tuple[int, int]] = field(default_factory=dict)
    piles_here: dict[str, dict[str, int]] = field(default_factory=dict)
    named_here: dict[str, dict[str, int]] = field(default_factory=dict)
    dissenting: dict[str, tuple[str, ...]] = field(default_factory=dict)
    stamp: str = "0"
    named_groups: list[tuple[str, str]] = field(default_factory=list)
    #: Appearances an undo put back to unnamed.
    unnamed: list[str] = field(default_factory=list)
    names_by: Callable[[str], str] | None = None
    #: Files whose faces are somebody else's, per person id. What the silent path is vetoed by.
    contradicts: dict[str, set[str]] = field(default_factory=dict)
    #: What a folder answer taught from and took back: (faces, person) per call.
    taught: list[tuple[tuple[str, ...], str]] = field(default_factory=list)
    untaught: list[tuple[tuple[str, ...], str]] = field(default_factory=list)
    #: Groups proposed as somebody: (group, person, folder, files, of) per call.
    proposed: list[tuple[str, str, str, int, int]] = field(default_factory=list)
    #: (folder, person) per withdrawal.
    withdrawn: list[tuple[str, str]] = field(default_factory=list)

    async def looking(self) -> bool:
        return self.on

    async def stamps(self) -> dict[str, FolderStamp]:
        # One value for every folder, moved by a test that wants the pass to look again.
        return {}

    async def faces_in(self, folder_id: str) -> FolderFaces:
        name = self.names_by(folder_id) if self.names_by else folder_id
        looked_at, with_faces = self.looked.get(name, (0, 0))
        return FolderFaces(
            looked_at=looked_at,
            with_faces=with_faces,
            piles=dict(self.piles_here.get(name, {})),
            named=dict(self.named_here.get(name, {})),
            portraits={pile: f"face-of-{pile}" for pile in self.piles_here.get(name, {})},
            dissenting=self.dissenting.get(name, ()),
        )

    async def faces_in_many(self, folder_ids: Sequence[str]) -> dict[str, FolderFaces]:
        return {one: await self.faces_in(one) for one in folder_ids}

    async def name_group(self, connection: Connection, group_id: str, person_id: str) -> int:
        return len(await self.name_group_recording(connection, group_id, person_id))

    async def name_group_recording(
        self, connection: Connection, group_id: str, person_id: str
    ) -> list[str]:
        self.named_groups.append((group_id, person_id))
        return [f"face-in-{group_id}"]

    async def unname_faces(self, connection: Connection, track_ids: Sequence[str]) -> int:
        self.unnamed.extend(track_ids)
        return len(track_ids)

    async def contradicting(self, person_id: str, asset_ids: Sequence[str]) -> set[str]:
        held = self.contradicts.get(person_id, set())
        return {asset for asset in asset_ids if asset in held}

    async def teach(self, track_ids: Sequence[str], person_id: str) -> int:
        self.taught.append((tuple(track_ids), person_id))
        return len(track_ids)

    async def unteach(self, track_ids: Sequence[str], person_id: str) -> int:
        self.untaught.append((tuple(track_ids), person_id))
        return len(track_ids)

    async def propose_group(
        self, group_id: str, person_id: str, *, folder_id: str, files: int, of: int
    ) -> bool:
        self.proposed.append((group_id, person_id, folder_id, files, of))
        return True

    async def withdraw_proposals(self, folder_id: str, person_id: str) -> int:
        self.withdrawn.append((folder_id, person_id))
        return 0


@pytest.fixture
async def library(library_store: LibraryStore, tmp_path: Path) -> Library:
    directory = tmp_path / "library"
    directory.mkdir()
    root = await library_store.create_root(name="Media", abs_path=directory)
    return Library(root=root, path=directory)


@pytest.fixture
def add_file(
    content_store: ContentStore, library_store: LibraryStore, settings: Settings
) -> Callable[..., Any]:
    """Put a real, gate-passing file into a library at a path, and index it as a scan would.

    The folder row is upserted here, exactly as the scan job does it, because indexing a file does
    not make its folder: the walk does, one directory at a time, and a test that skipped it would
    leave every file sitting in the library's root and a folder reader with no folders to read.
    """

    async def add(library: Library, rel_path: str, source: str = "accepted.mp4") -> Ingested:
        parent = str(PurePosixPath(rel_path).parent)
        folder_id = None
        if parent != ".":
            folder_id = (await library_store.upsert_folder(library.root.id, parent)).id
        else:
            top = await library_store.root_folder(library.root.id)
            folder_id = None if top is None else top.id
        target = library.path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        # A distinct tail per path, so each file is its own asset. Sift identifies files by their
        # content, so writing the same fixture bytes five times produces ONE asset with five
        # locations, which is correct behaviour and the opposite of what a folder of five
        # different files needs to look like. The tail rides after the media, where nothing reads
        # it, so the file still passes the ingress gate as what it says it is.
        body = (CORPUS / source).read_bytes() + rel_path.encode("utf-8")
        target.write_bytes(body)
        checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
        return await content_store.ingest(
            checked, root_id=library.root.id, rel_path=rel_path, folder_id=folder_id
        )

    return add


@pytest.fixture
def faces() -> FakeFaces:
    return FakeFaces()


@pytest.fixture
async def store(temp_db: Database) -> Store:
    await temp_db.initialize_schema()
    return Store(temp_db)


class FakePreferences:
    """The settings hub, as much of it as this pass reads.

    A dictionary with a default of ON, because every switch over this pass is on out of the box and
    a stand-in that answered "off" would quietly turn half of every test off. A test that cares
    about a switch names it.
    """

    def __init__(self, **values: bool) -> None:
        self.values = values

    async def get_app(self, key: str) -> Any:
        return self.values.get(key, True)

    async def get_user(self, user_id: str, key: str) -> Any:  # pragma: no cover - never asked here
        raise AssertionError("this pass runs for nobody and has no account to read for")


@pytest.fixture
def preferences() -> FakePreferences:
    return FakePreferences()


@pytest.fixture
async def service(
    store: Store, access: Repository, faces: FakeFaces, preferences: FakePreferences
) -> SuggestionService:
    return SuggestionService(store=store, access=access, faces=faces, preferences=preferences)


@pytest.fixture
async def admin(temp_db: Database) -> Viewer:
    return await create_user(temp_db, Role.ADMIN)


@pytest.fixture
async def guest(temp_db: Database) -> Viewer:
    return await create_user(temp_db, Role.GUEST)


@pytest.fixture
async def name_folders(temp_db: Database, faces: FakeFaces) -> Callable[[], Any]:
    """Teach the stand-in which folder id is which name, once the tree exists."""

    async def wire() -> None:
        rows = await temp_db.fetch_all("SELECT id, name FROM folders")
        by_id = {str(row["id"]): str(row["name"]) for row in rows}
        faces.names_by = lambda folder_id: by_id.get(folder_id, folder_id)

    return wire
