# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every row after the read counts an unread file as still to do, by one rule, as the read advances."""

from __future__ import annotations

from typing import Any, cast

import pytest

from sift.kernel.content import (
    ContentStore,
    DerivativeKind,
    Lack,
    lacks_derivative,
    lacks_fingerprint,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs.families import PRODUCT_TYPES, Family
from sift.kernel.jobs.queue_rows import FilesToRead
from sift.slices.importing.products import Product, ProductRegistry
from sift.slices.media_jobs.router import KindOfWork, _with_unread
from sift.testing.fixtures import LibraryRoot
from sift.wiring import work_ahead

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000

#: A stand-in for a feature's own table: a file has it once its fingerprint is taken.
_MADE = lacks_fingerprint()

#: The rows after the read, each with what it makes.
_ROWS: dict[Family, tuple[str, ...]] = {
    Family.GENERATE: ("thumbnails", "previews", "sprites"),
    Family.FINGERPRINT: ("fingerprints", "music"),
    Family.IDENTIFY: ("faces", "watermarks"),
    Family.SEMANTIC: ("meaning",),
}


def _lack(key: str) -> Lack:
    if key in ("thumbnails", "previews", "sprites"):
        kind = {"thumbnails": DerivativeKind.THUMB, "previews": DerivativeKind.PREVIEW}
        term = lacks_derivative([kind.get(key, DerivativeKind.SPRITE)])
        assert term is not None
        return term
    return _MADE


def _registry() -> ProductRegistry:
    async def on() -> bool:
        return True

    async def none(_ids: object) -> set[str]:
        return set()

    async def night() -> str:
        return "23:00"

    registry = ProductRegistry(night_start=night)
    for family, keys in _ROWS.items():
        for key in keys:

            async def lack(key: str = key) -> Lack:
                return _lack(key)

            registry.register(
                Product(
                    key=key,
                    label=key,
                    help="",
                    switched_on=on,
                    lack=lack,
                    lacking_among=none,
                    build=cast(Any, none),
                    family=family,
                )
            )
    return registry


async def _arrives(database: Database, root: LibraryRoot, media_type: str) -> str:
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (asset_id, f"digest-{asset_id}", media_type, _EPOCH),
    )
    await database.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, 'present', ?, ?)",
        (new_id(), asset_id, root.id, asset_id, asset_id, _EPOCH, _EPOCH),
    )
    return asset_id


async def _read_and_made(database: Database, asset_id: str, media_type: str) -> None:
    """The read, and every product made for it in one go: the most done a row can be."""
    await database.execute(
        "UPDATE assets SET probed_at = ?, duration_ms = ?, acodec = ?, phash = 'p',"
        " videohash = 'v', oshash = 'o', video_phash = 'vp', fingerprint_version = 99 WHERE id = ?",
        (
            _EPOCH,
            None if media_type == "image" else 5000,
            "aac" if media_type == "video" else None,
            asset_id,
        ),
    )
    for kind in DerivativeKind:
        await database.execute(
            "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, created_at,"
            " recipe_version) VALUES (?, ?, ?, ?, ?, 99)",
            (new_id(), asset_id, kind.value, f"{kind.value}/{asset_id}", _EPOCH),
        )


async def _bar(registry: ProductRegistry, content: ContentStore, key: str) -> tuple[int, int]:
    """One product's bar as Activity draws it: `(done, total)`."""
    left = (await work_ahead._product_left(registry, content, key, [])).waiting
    total = max(await work_ahead._product_wanted(registry, content, key), left)
    return total - left, total


async def test_no_row_after_the_read_is_further_on_than_the_read(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    kinds = ["video", "image", "gif", "image", "video", "image"]
    files = [(await _arrives(temp_db, library_root, kind), kind) for kind in kinds]
    registry = _registry()
    for step in range(len(files) + 1):
        if step:
            await _read_and_made(temp_db, *files[step - 1])
        read = await content_store.asset_count() - await content_store.unread_count()
        assert read == step
        for family, keys in _ROWS.items():
            bars = [await _bar(registry, content_store, key) for key in keys]
            assert all(done <= read for done, _total in bars), (step, family, bars)
            # Not merely held down: every file is in every bar it is made for, read or not.
            assert [total for _done, total in bars] == [
                await content_store.wanting_count(key) for key in keys
            ]
        whole = await _bar(registry, content_store, "faces")
        assert whole == (step, len(files)), step


async def test_the_unread_are_narrowed_to_the_kinds_the_work_is_made_for(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    for kind in ("video", "image", "gif"):
        await _arrives(temp_db, library_root, kind)

    coming = {key: await content_store.coming_count(key) for key in PRODUCT_TYPES}

    assert coming == {
        "thumbnails": 3,
        "previews": 2,
        "sprites": 2,
        "fingerprints": 3,
        "music": 1,
        "faces": 3,
        "watermarks": 3,
        "meaning": 3,
    }
    # A product the library has no narrowing for is wanted by every file.
    assert await content_store.coming_count("anything") == 3


@pytest.mark.parametrize("key", ["", *PRODUCT_TYPES])
def test_a_file_taken_in_leaves_the_walk_as_the_library_counts_it(key: str) -> None:
    """A walk's count falls by a file as the file becomes a row the library counts as coming, and
    each row's work and whole are the same on both sides of that moment."""
    job_type = PRODUCT_TYPES.get(key, "probe")
    walking = {job_type: KindOfWork(done=4, outstanding=0, failed=0, waiting=6, total=10)}
    a_row = {job_type: KindOfWork(done=4, outstanding=0, failed=0, waiting=7, total=11)}

    before = _with_unread(walking, {}, FilesToRead(by_kind={"video": 1.0}))[0][job_type]
    after = _with_unread(a_row, {}, FilesToRead())[0][job_type]

    assert (before.waiting, before.total) == (after.waiting, after.total) == (7, 11)
