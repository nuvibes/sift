# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real library, real files, and a real filesystem underneath both: what is tested is where the
bytes are afterwards."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import library_write
from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.content.library import NotWritable, check_folder_writable
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.organize.service import Organizer
from sift.testing.fixtures import FakeClock, create_user

#: A file the ingress gate accepts.
CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@dataclass(frozen=True, slots=True)
class Library:
    """A root, and the directory behind it."""

    root: Root
    path: Path


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(1_700_000_000)


@pytest.fixture
async def managed(library_store: LibraryStore, tmp_path: Path) -> Library:
    """A root made through the store, which checks the real directory."""
    directory = tmp_path / "library"
    directory.mkdir()
    root = await library_store.create_root(name="Videos", abs_path=directory)
    return Library(root=root, path=directory)


@pytest.fixture
async def second_managed(library_store: LibraryStore, tmp_path: Path) -> Library:
    """A second root on the same filesystem, for moves between library folders."""
    directory = tmp_path / "second"
    directory.mkdir()
    root = await library_store.create_root(name="Photos", abs_path=directory)
    return Library(root=root, path=directory)


@pytest.fixture
async def read_only(
    library_store: LibraryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Library:
    """A root whose folder the filesystem will not let Sift write in. The one blocking predicate is
    made to fail, since `chmod` is ignored on Windows; everything above it stays real."""
    directory = tmp_path / "archive"
    directory.mkdir()

    # Read from the defining module, patched on `library_write`, whose globals `move_into` uses.
    real = check_folder_writable

    def refuse_under_the_archive(candidate: Path) -> None:
        if candidate == directory or directory in candidate.parents:
            raise NotWritable(
                "This folder is read-only, so nothing can change anything in it \u2014 not Sift, and "
                "not any other program."
            )
        real(candidate)

    monkeypatch.setattr(library_write, "check_folder_writable", refuse_under_the_archive)
    root = await library_store.create_root(name="Archive", abs_path=directory)
    return Library(root=root, path=directory)


@pytest.fixture
def organizer(
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    access: Repository,
    clock: FakeClock,
) -> Organizer:
    return Organizer(temp_db, content_store, library_store, access, clock=clock.now)


@pytest.fixture
def add_file(
    content_store: ContentStore, library_store: LibraryStore, settings: Settings
) -> Callable[..., Any]:
    """Put a real, gate-passing file in a library and index it, folder rows included, as a scan
    would. Returns what was indexed."""

    async def add(library: Library, rel_path: str, source: str = "accepted.mp4") -> Ingested:
        target = library.path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((CORPUS / source).read_bytes())

        folder_id = None
        parent = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
        if parent:
            folder_id = (await library_store.upsert_folder(library.root.id, parent)).id
        else:
            root_folder = await library_store.root_folder(library.root.id)
            folder_id = None if root_folder is None else root_folder.id

        checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
        return await content_store.ingest(
            checked, root_id=library.root.id, rel_path=rel_path, folder_id=folder_id
        )

    return add


@pytest.fixture
async def admin(temp_db: Database) -> Viewer:
    """A real user: a recorded move's foreign key into `users` refuses a made-up id."""
    return await create_user(temp_db, Role.ADMIN)


@pytest.fixture
async def guest(temp_db: Database) -> Viewer:
    return await create_user(temp_db, Role.GUEST)
