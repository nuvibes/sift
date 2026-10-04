# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pass that takes a raised floor to the sets made before it moved."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from sift.kernel.access import Repository
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.photo_sets import MIN_PICTURES
from sift.slices.photo_sets.jobs import dissolve_under_floor
from sift.slices.photo_sets.service import PhotoSetService
from sift.slices.photo_sets.tests.conftest import Shoot, db_path, read, write
from sift.slices.photo_sets.tests.test_derive import put


class _Context:
    """The three things the handler asks of its context, and nothing the queue would need."""

    def __init__(self) -> None:
        self.progress: list[float] = []
        self.note: str | None = None

    async def set_progress(self, fraction: float) -> None:
        self.progress.append(fraction)

    async def set_note(self, note: str) -> None:
        self.note = note

    async def raise_if_canceled(self) -> None:
        return None


def _a_set(client: TestClient, *, origin: str, pictures: list[str]) -> str:
    """A set written straight into the table, with the pictures it holds."""
    set_id = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO photo_sets (id, name, origin, created_at) VALUES (?, ?, ?, ?)",
                (set_id, f"{origin} {set_id[-4:]}", origin, 1_700_000_000),
            ),
            *(
                (
                    "INSERT INTO photo_set_items (photo_set_id, asset_id) VALUES (?, ?)",
                    (set_id, asset_id),
                )
                for asset_id in pictures
            ),
        ],
    )
    return set_id


def _run(client: TestClient) -> _Context:
    """Run the pass against this test's database, on a connection of its own. See the derive
    tests for why the client's own loop cannot be borrowed for a write."""
    settings = client.app.state.settings  # type: ignore[attr-defined]
    context = _Context()

    async def go() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            content = ContentStore(database, settings)
            service = PhotoSetService(database, Repository(database, content))
            await dissolve_under_floor(context, service=service)  # type: ignore[arg-type]
        finally:
            await database.close()

    asyncio.run(go())
    return context


def _ids(client: TestClient) -> set[str]:
    return {str(row["id"]) for row in read(db_path(client), "SELECT id FROM photo_sets", ())}


def test_sets_sift_made_under_the_floor_are_dissolved_and_a_persons_are_not(
    client: TestClient, shoot: Shoot
) -> None:
    """When the floor rises, nothing else looks back at a set. What goes is only
    what Sift made and only what is short; a set assembled by hand is somebody's whatever its
    size, and the pictures themselves are never touched."""
    pictures = [put(client, shoot, f"{index:03}.jpg", "image") for index in range(3)]
    short = _a_set(client, origin="filename", pictures=pictures)
    theirs = _a_set(client, origin="manual", pictures=pictures)
    tall = _a_set(
        client,
        origin="folder",
        pictures=[
            put(client, shoot, f"tall-{index:03}.jpg", "image") for index in range(MIN_PICTURES)
        ],
    )

    context = _run(client)

    assert _ids(client) == {theirs, tall}
    assert context.note == (
        f"Deleted 1 Photo Set with fewer than {MIN_PICTURES} photos. The photos are unchanged."
    )
    assert context.progress[-1] == 1.0
    still_there: list[dict[str, Any]] = read(
        db_path(client), "SELECT id FROM assets WHERE id IN (?, ?, ?)", tuple(pictures)
    )
    assert len(still_there) == 3
    deleted = read(
        db_path(client),
        "SELECT actor_kind, actor_id, payload FROM workbench_decisions WHERE verb = 'deleted'",
        (),
    )
    # Sift, by the task that did it, and why: the line can say "fewer than 10 pictures".
    assert deleted == [
        {
            "actor_kind": "sift",
            "actor_id": "photo_set_floor",
            "payload": json.dumps({"under_floor": MIN_PICTURES}),
        }
    ]
    assert short not in _ids(client)


def test_a_library_at_the_floor_is_left_alone(client: TestClient, shoot: Shoot) -> None:
    pictures = [put(client, shoot, f"{index:03}.jpg", "image") for index in range(MIN_PICTURES)]
    kept = _a_set(client, origin="shoot", pictures=pictures)

    context = _run(client)

    assert _ids(client) == {kept}
    assert context.note == f"No Photo Set Sift created has fewer than {MIN_PICTURES} photos."


def test_a_long_dissolve_says_how_far_it_has_got_as_it_goes(
    client: TestClient, shoot: Shoot
) -> None:
    """Every twenty-five sets it tells the queue how far it is and asks whether to stop, so a
    library with thousands of short sets shows movement and can be cancelled partway."""
    picture = put(client, shoot, "000.jpg", "image")
    for _ in range(26):
        _a_set(client, origin="filename", pictures=[picture])

    context = _run(client)

    assert _ids(client) == set()
    assert context.progress == [25 / 26, 1.0]
