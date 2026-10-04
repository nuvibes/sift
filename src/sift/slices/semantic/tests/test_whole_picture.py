# SPDX-License-Identifier: AGPL-3.0-or-later
"""The look again at HEIF photographs described from one tile of them.

A description is timed in milliseconds and the whole-picture copy in seconds, so the cases here
sit a few SECONDS either side of the copy: a comparison that forgot to bring the copy's time to
milliseconds reads every description as newer than every copy, and the photo described from a
tile would never be described again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from sift.kernel.content import ContentStore, DerivativeKind
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.semantic import jobs as semantic_jobs
from sift.slices.semantic.records import Records
from sift.slices.semantic.whole_picture import WholePicture

pytestmark = pytest.mark.integration

MODEL = "siglip2-new"


async def seed(database: Database, asset_id: str, mime: str) -> None:
    """One photograph in the library, of this type."""
    await database.execute(
        "INSERT INTO assets (id, identity, media_type, mime, size_bytes, added_at) "
        "VALUES (?, ?, 'image', ?, 1, 1700000000)",
        (asset_id, f"digest-{asset_id}", mime),
    )


@dataclass
class Switch:
    on: bool = True

    async def enabled(self) -> bool:
        return self.on


@dataclass
class Context:
    payload: dict[str, Any] = field(default_factory=dict)
    progress: float | None = None
    note: str | None = None
    children: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def set_progress(self, value: float) -> None:
        self.progress = value

    async def set_note(self, value: str) -> None:
        self.note = value

    async def enqueue_child(
        self, job_type: str, payload: dict[str, Any] | None = None, **_options: Any
    ) -> str:
        self.children.append((job_type, payload or {}))
        return f"child-{len(self.children)}"


@pytest.fixture
async def tiles(temp_db: Database, content_store: ContentStore) -> WholePicture:
    return WholePicture(content=content_store, records=Records(temp_db))


async def copied_at(content: ContentStore, asset_id: str) -> int:
    """When this photo's whole-picture copy was written, in seconds."""
    return dict(await content.heif_stills())[asset_id] or 0


async def test_a_heif_photo_described_before_its_copy_is_described_again_and_one_after_is_not(
    temp_db: Database, content_store: ContentStore, tiles: WholePicture
) -> None:
    """Described before the copy, or with no copy yet: one tile. Described after: the whole
    picture. A photo that is not HEIF, or not described at all, is never on the list."""
    records = Records(temp_db)
    ids = {name: new_id() for name in ("before", "after", "no-copy", "unread", "jpeg")}
    for name in ("before", "after", "no-copy", "unread"):
        await seed(temp_db, ids[name], "image/heic")
    await seed(temp_db, ids["jpeg"], "image/jpeg")
    for name in ("before", "after"):
        await content_store.add_derivative(
            ids[name], DerivativeKind.RENDITION, extension="jpg", size_bytes=1
        )
    copy_ms = await copied_at(content_store, ids["before"]) * 1000
    await records.mark(ids["before"], revision=MODEL, frames=3, at_ms=copy_ms - 5000)
    await records.mark(ids["after"], revision=MODEL, frames=3, at_ms=copy_ms + 5000)
    await records.mark(ids["no-copy"], revision=MODEL, frames=3, at_ms=copy_ms + 5000)
    await records.mark(ids["jpeg"], revision=MODEL, frames=3, at_ms=1)
    stale = sorted((ids["before"], ids["no-copy"]))

    assert (await tiles.read_from_a_tile())[0] == stale
    assert await tiles.tile_pass_owed() is True

    context = Context()
    await semantic_jobs.tile_pass(context, service=Switch(), tiles=tiles)  # type: ignore[arg-type]

    assert context.children == [
        (semantic_jobs.SEMANTIC_DESCRIBE, {"asset_id": asset_id}) for asset_id in stale
    ]
    assert context.note is not None and context.note.startswith("2 photos queued")
    # Taken back, so the describing job's own guard has nothing on record to stop it.
    assert await records.described(ids["before"]) is None
    assert await records.described(ids["no-copy"]) is None
    assert await records.described(ids["after"]) is not None
    assert await records.described(ids["jpeg"]) is not None
    # And the pass ends: nothing described from a tile is left for the next start.
    assert await tiles.tile_pass_owed() is False


async def test_the_pass_walks_every_page_of_the_heif_stills(
    temp_db: Database, content_store: ContentStore, tiles: WholePicture
) -> None:
    records = Records(temp_db)
    for asset_id in ("h1", "h2", "h3"):
        await seed(temp_db, asset_id, "image/heic")
        await records.mark(asset_id, revision=MODEL, frames=1, at_ms=1)

    first, after = await tiles.read_from_a_tile(limit=2)
    second, end = await tiles.read_from_a_tile(after=after, limit=2)

    assert (first, after, second, end) == (["h1", "h2"], "h2", ["h3"], "h3")
    assert await tiles.read_from_a_tile(after=end) == ([], "")
    assert await records.described_at_of([]) == {}


async def test_the_pass_does_nothing_with_smart_search_switched_off(
    temp_db: Database, tiles: WholePicture
) -> None:
    records = Records(temp_db)
    await seed(temp_db, "h1", "image/heic")
    await records.mark("h1", revision=MODEL, frames=1, at_ms=1)
    context = Context()

    await semantic_jobs.tile_pass(context, service=Switch(on=False), tiles=tiles)  # type: ignore[arg-type]

    assert context.children == []
    assert context.progress is None
    assert await records.described("h1") is not None


async def test_a_pass_with_nothing_to_look_at_says_so(tiles: WholePicture) -> None:
    context = Context()

    await semantic_jobs.tile_pass(context, service=Switch(), tiles=tiles)  # type: ignore[arg-type]

    assert context.children == []
    assert context.note == "Every photo was already described from the whole picture."
