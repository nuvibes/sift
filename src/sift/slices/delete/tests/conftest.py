# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real library and real files: what is tested is whether bytes are still on disk."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.delete.service import Deleter
from sift.slices.library_roots.service import LibraryService
from sift.testing.fixtures import create_user

#: A file the ingress gate accepts, reused rather than re-made. The gate has to pass for a file to
#: become an asset at all, and these tests are not about the gate.
CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@dataclass(frozen=True, slots=True)
class Library:
    """A root Sift is allowed to change, and the directory behind it."""

    root: Root
    path: Path


@pytest.fixture
async def managed(library_store: LibraryStore, tmp_path: Path) -> Library:
    """A root in an ordinary directory Sift may write in."""
    directory = tmp_path / "library"
    directory.mkdir()
    root = await library_store.create_root(name="Videos", abs_path=directory)
    return Library(root=root, path=directory)


@pytest.fixture
async def read_only(
    library_store: LibraryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Library:
    """A root in a folder the filesystem will not let Sift write in, faked at `is_writable` for this
    folder only, since Windows ignores `chmod`."""
    from sift.kernel.paths import is_writable as real

    directory = tmp_path / "archive"
    directory.mkdir()

    def refusing_here(path: Path) -> bool:
        return False if directory in (path, *path.parents) else real(path)

    monkeypatch.setattr("sift.kernel.content.library.is_writable", refusing_here)
    root = await library_store.create_root(name="Archive", abs_path=directory)
    return Library(root=root, path=directory)


class RecordingPlaybackCache:
    """Stands in for the transcoded-segment cache and records what it was asked to drop."""

    def __init__(self) -> None:
        self.discarded: list[str] = []

    def discard_asset(self, asset_id: str) -> int:
        self.discarded.append(asset_id)
        return 0


@pytest.fixture
def playback_cache() -> RecordingPlaybackCache:
    return RecordingPlaybackCache()


@pytest.fixture
def deleter(
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    access: Repository,
    playback_cache: RecordingPlaybackCache,
) -> Deleter:
    """The deleter the application builds, with its collaborators: the scanner's own memory is
    the real one, so what a removal leaves for the next scan is read where a scan reads it."""
    return Deleter(
        temp_db,
        content_store,
        library_store,
        access,
        playback_cache,
        LibraryService(temp_db, library_store, access),
    )


@pytest.fixture
def add_file(content_store: ContentStore, settings: Settings) -> Callable[[Library, str], Any]:
    """Put a real, gate-passing file in a library and index it, as a scan would."""

    async def add(
        library: Library,
        rel_path: str,
        source: str = "accepted.mp4",
        folder_id: str | None = None,
    ) -> Ingested:
        target = library.path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((CORPUS / source).read_bytes())
        checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
        # A scan names the folder row it made on the way past. Optional here because most of these
        # tests are about one file rather than about where it sits.
        return await content_store.ingest(
            checked, root_id=library.root.id, rel_path=rel_path, folder_id=folder_id
        )

    return add


@pytest.fixture
async def admin(temp_db: Database) -> Viewer:
    """A real user, since deletions name their user by foreign key."""
    return await create_user(temp_db, Role.ADMIN)


@pytest.fixture
async def guest(temp_db: Database) -> Viewer:
    return await create_user(temp_db, Role.GUEST)
