# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real library, real files, and a real remover behind the seam.

Two removers are offered, and which one a test takes says what that test is about.

`recorder` records what it was asked to remove and removes nothing. Tests that use it are asking
whether this feature *proposes* the right thing (which asset, in which mode, narrowed to which
copy), without a file moving.

`deleter` is the real one from the delete feature, with a real bin behind it. Tests that use it
are asking what actually happens to bytes on a disk, and the most important test in this slice is
one of them: run everything, and assert nothing was removed. A double could not answer that
question honestly, because a double is exactly the thing that would fail to remove a file whether
or not the code tried to.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pytest

from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.content.duplicates import DuplicateReads
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.jobs import JobQueue
from sift.slices.dedup.service import DedupService
from sift.testing.fixtures import FakeClock, create_user

#: Files the ingress gate accepts. Reused rather than re-made: a file has to pass the gate to
#: become an asset at all, and none of these tests are about the gate.
CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@dataclass(frozen=True, slots=True)
class Library:
    root: Root
    path: Path


@dataclass
class Asked:
    """One thing the feature asked to have removed."""

    asset_id: str
    mode: str
    actor: str
    location_id: str | None


@dataclass
class Recorder:
    """The removal seam, recording rather than removing.

    Satisfies the `Remover` protocol structurally: there is no inheritance here on purpose, so
    the protocol is doing real work: if the seam's shape changed, this would stop matching it and
    the type checker would say so.
    """

    asked: list[Asked] = field(default_factory=list)

    async def remove(
        self,
        asset_id: str,
        *,
        mode: Literal["sift", "disk"],
        actor: Viewer,
        location_id: str | None = None,
    ) -> None:
        self.asked.append(
            Asked(asset_id=asset_id, mode=mode, actor=actor.id, location_id=location_id)
        )


class Preferences:
    """The two settings the real deleter reads, at their registered defaults."""

    async def get_app(self, key: str) -> Any:
        return 30

    async def get_user(self, user_id: str, key: str) -> Any:
        return False


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(1_700_000_000)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
async def managed(library_store: LibraryStore, tmp_path: Path) -> Library:
    """A root Sift has been given write access to.

    Managed deliberately: a read-only root would make every "nothing was deleted" test pass for
    the wrong reason: the deleter refuses those outright, so the test would be proving the root
    was read-only rather than proving this feature never asks.
    """
    directory = tmp_path / "library"
    directory.mkdir()
    root = await library_store.create_root(name="Videos", abs_path=directory)
    return Library(root=root, path=directory)


@pytest.fixture
def add_file(content_store: ContentStore, settings: Settings) -> Callable[..., Any]:
    """Put a real, gate-passing file in a library and index it, the way a scan would."""

    async def add(library: Library, rel_path: str, source: str = "accepted.mp4") -> Ingested:
        target = library.path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((CORPUS / source).read_bytes())
        checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
        return await content_store.ingest(checked, root_id=library.root.id, rel_path=rel_path)

    return add


@pytest.fixture
def reads(temp_db: Database) -> DuplicateReads:
    return DuplicateReads(temp_db)


@pytest.fixture
async def service(
    temp_db: Database, reads: DuplicateReads, recorder: Recorder, clock: FakeClock
) -> DedupService:
    await temp_db.initialize_schema()
    return DedupService(temp_db, reads, recorder, clock=clock.now)


@pytest.fixture
async def sweep_queue(temp_db: Database, clock: FakeClock) -> JobQueue:
    await temp_db.initialize_schema()
    return JobQueue(temp_db, clock=clock.now)


@pytest.fixture
def deleter(
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    access: Repository,
) -> Any:
    """The real remover. A delete from disk is final; there is nothing behind it.

    The transcoded-segment cache is a fake here for the same reason it is one in the delete slice's
    own tests: what happens to playback's cache is playback's test, and these are about whether a
    duplicate really leaves the disk.
    """
    from sift.slices.delete.service import Deleter
    from sift.slices.library_roots.service import LibraryService

    class NoPlaybackCache:
        def discard_asset(self, asset_id: str) -> int:
            return 0

    return Deleter(
        temp_db,
        content_store,
        library_store,
        access,
        NoPlaybackCache(),
        LibraryService(temp_db, library_store, access),
    )


@pytest.fixture
async def real_service(
    temp_db: Database, reads: DuplicateReads, deleter: Any, clock: FakeClock
) -> DedupService:
    """The feature wired to the thing that really removes files."""
    await temp_db.initialize_schema()
    return DedupService(temp_db, reads, deleter, clock=clock.now)


@pytest.fixture
async def admin(temp_db: Database) -> Viewer:
    return await create_user(temp_db, Role.ADMIN)


@pytest.fixture
async def guest(temp_db: Database) -> Viewer:
    return await create_user(temp_db, Role.GUEST)
