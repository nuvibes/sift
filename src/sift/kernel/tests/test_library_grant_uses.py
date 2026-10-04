# SPDX-License-Identifier: AGPL-3.0-or-later
"""A granted folder follows what uses it.

A grant is made when a folder is chosen for a use (a library, the backup folder) and given back when
the last use lets go, so nobody keeps the list of granted folders by hand. What matters here is the
safe side: a grant something still uses is never given back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from structlog.testing import capture_logs

# For its side effect: registering the table the ledger is written to, so this test's database
# has it. Removing a library records the removal, and a kernel process that has never imported
# the workbench slice genuinely does not have that table.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.content import LibraryStore, RootOverlap
from sift.kernel.content import library as library_module
from sift.kernel.content.library import LibraryError
from sift.kernel.ledger import Actor
from sift.testing.logs import uncached_log


async def test_removing_a_library_gives_its_folder_back(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    await library_store.ensure_grant(media)
    root = await library_store.create_root(name="media", abs_path=media)

    await library_store.delete_root(root.id, actor=Actor.sift("folder"))

    assert await library_store.grants() == []


async def test_a_give_back_that_fails_keeps_the_grant_and_the_removal_still_stands(
    library_store: LibraryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The library is already gone when its folder is given back, so a failure there keeps a
    grant (the safe side) and is logged, never reported as a removal that failed."""
    uncached_log(monkeypatch, library_module)
    media = tmp_path / "media"
    media.mkdir()
    await library_store.ensure_grant(media)
    root = await library_store.create_root(name="media", abs_path=media)

    async def broken() -> list[object]:
        raise OSError("the database is busy")

    monkeypatch.setattr(library_store, "release_unused_grants", broken)
    with capture_logs() as logs:
        removed = await library_store.delete_root(root.id, actor=Actor.sift("folder"))

    assert removed is True
    assert await library_store.get_root(root.id) is None
    assert [Path(grant.abs_path) for grant in await library_store.grants()] == [media.resolve()]
    assert "library.grants_release_failed" in [entry["event"] for entry in logs]


async def test_a_folder_another_library_still_uses_is_kept(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    (media / "one").mkdir(parents=True)
    (media / "two").mkdir()
    await library_store.ensure_grant(media)
    one = await library_store.create_root(name="one", abs_path=media / "one")
    await library_store.create_root(name="two", abs_path=media / "two")

    await library_store.delete_root(one.id, actor=Actor.sift("folder"))

    assert [Path(grant.abs_path) for grant in await library_store.grants()] == [media.resolve()]


async def test_a_registered_use_keeps_its_folder_and_says_why(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    grant = await library_store.ensure_grant(backups)

    async def backup_folder() -> list[Path]:
        return [backups.resolve()]

    library_store.use_grants_for("Backups are saved in that folder.", backup_folder)

    assert await library_store.release_unused_grants() == []
    with pytest.raises(LibraryError, match="Backups are saved"):
        await library_store.revoke_grant(grant.id)


async def test_a_folder_nothing_uses_is_given_back(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    left = tmp_path / "left"
    left.mkdir()
    await library_store.ensure_grant(left)

    released = await library_store.release_unused_grants()

    assert [Path(grant.abs_path) for grant in released] == [left.resolve()]
    assert await library_store.grants() == []


async def test_a_folder_already_covered_is_the_grant_that_covers_it(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    (media / "inside").mkdir(parents=True)
    outer = await library_store.ensure_grant(media)

    assert (await library_store.ensure_grant(media / "inside")).id == outer.id
    assert (await library_store.ensure_grant(media)).id == outer.id
    assert len(await library_store.grants()) == 1


async def test_a_folder_that_would_widen_a_grant_is_still_refused(
    library_store: LibraryStore, tmp_path: Path
) -> None:
    inner = tmp_path / "media" / "inner"
    inner.mkdir(parents=True)
    await library_store.ensure_grant(inner)

    with pytest.raises(RootOverlap):
        await library_store.ensure_grant(tmp_path / "media")
