# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real library, real files and the real write seam; ffmpeg is stood in for only where a test is
not about ffmpeg."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# Registers the ledger's table, which a produced file's event is written to.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import library_write
from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.content.library import NotWritable, check_folder_writable
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.jobs import JobContext, JobQueue, register_handler
from sift.slices.media_edit.editor import EditService
from sift.slices.media_edit.service import CompressService
from sift.slices.organize.service import Organizer
from sift.testing.fixtures import FakeClock, create_user

#: A file the ingress gate accepts. The gate has to pass for anything to become an asset, and none
#: of these tests are about the gate.
CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@dataclass(frozen=True, slots=True)
class Library:
    """A root, and the directory behind it."""

    root: Root
    path: Path


@dataclass
class FakeDuplicates:
    """Records the pairs it was told are deliberately different, and settles nothing."""

    pairs: list[tuple[str, str]] = field(default_factory=list)

    async def mark_unrelated(self, first_asset_id: str, second_asset_id: str) -> None:
        self.pairs.append((first_asset_id, second_asset_id))


@dataclass
class FakeReindexer:
    """Records what it was told changed."""

    touched_ids: list[str] = field(default_factory=list)

    async def touched(self, asset_id: str) -> None:
        self.touched_ids.append(asset_id)

    async def touched_many(self, asset_ids: Any) -> None:  # pragma: no cover - unused here
        self.touched_ids.extend(asset_ids)

    async def queue_many(self, asset_ids: Any) -> None:  # pragma: no cover (no rename)
        await self.touched_many(asset_ids)

    async def renamed(self) -> None:  # pragma: no cover - unused here
        return None


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(1_700_000_000)


@pytest.fixture
async def managed(library_store: LibraryStore, tmp_path: Path) -> Library:
    """A root somebody has opted into letting Sift write into."""
    directory = tmp_path / "library"
    directory.mkdir()
    root = await library_store.create_root(name="Videos", abs_path=directory)
    return Library(root=root, path=directory)


@pytest.fixture
async def read_only(
    library_store: LibraryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Library:
    """A root whose folder the filesystem will not let Sift write in: the one blocking predicate is
    made to fail, since `chmod` is ignored on Windows."""
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
    """The real write seam. Not a double: what is being checked is that it refuses."""
    return Organizer(temp_db, content_store, library_store, access, clock=clock.now)


@pytest.fixture
def targets() -> dict[str, Any]:
    """The stored target numbers, as a settings reader would answer them."""
    return {}


@pytest.fixture
def compressor(
    temp_db: Database,
    access: Repository,
    job_queue: JobQueue,
    organizer: Organizer,
    targets: dict[str, Any],
    clock: FakeClock,
) -> CompressService:
    async def read_app_setting(key: str) -> Any:
        return targets.get(key)

    return CompressService(temp_db, access, job_queue, organizer, read_app_setting, clock=clock.now)


@pytest.fixture
def editor(
    temp_db: Database,
    access: Repository,
    job_queue: JobQueue,
    organizer: Organizer,
    content_store: ContentStore,
    settings: Settings,
    clock: FakeClock,
) -> EditService:
    """The editor over the same library and write seam, with every setting at its default."""

    async def nothing_stored(_key: str) -> object:
        return None

    return EditService(
        temp_db,
        access,
        job_queue,
        organizer,
        content_store,
        settings,
        nothing_stored,
        clock=clock.now,
    )


@pytest.fixture
def add_file(
    content_store: ContentStore, library_store: LibraryStore, settings: Settings
) -> Callable[..., Any]:
    """Put a real, gate-passing file in a library and index it. `data` gives distinct bytes, since
    identical bytes are one asset."""

    async def add(
        library: Library,
        rel_path: str,
        source: str = "accepted.mp4",
        data: bytes | None = None,
    ) -> Ingested:
        target = library.path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((CORPUS / source).read_bytes() if data is None else data)

        parent = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
        folder_id: str | None
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
    return await create_user(temp_db, Role.ADMIN)


@pytest.fixture
async def guest(temp_db: Database) -> Viewer:
    return await create_user(temp_db, Role.GUEST)


@pytest.fixture
def stub_handlers() -> None:
    """Claim the two job types with no-op handlers, since the queue refuses an unhandled type."""

    async def nothing(context: JobContext) -> None:
        return None

    register_handler("compress", nothing, name="Test job")
    register_handler("compress_sample", nothing, name="Test job")
    register_handler("edit", nothing, name="Test job")
    register_handler("probe", nothing, name="Test job")
    # What the composition root names as the extra job an edit may ask for. A stub, because what
    # matters here is that this slice enqueues a NAME it was handed and never learns what it is.
    register_handler("loop_whole", nothing, name="Test job")
