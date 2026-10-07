# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Loop's own picture: the frame where it begins, filed under the moment so two Loops of one
video are two pictures. The moment comes from the row, never the caller, and a picture not built
yet says so."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.content import DerivativeKind
from sift.kernel.content.identity import derivative_relpath, params_key
from sift.kernel.db import Database, Row
from sift.kernel.ids import new_id
from sift.kernel.wiring import part_of_app
from sift.slices.loops import jobs
from sift.slices.loops.service import SERVICE, LoopService
from sift.slices.loops.tests.conftest import Videos, db_path, mark, read, share, sign_in, write

pytestmark = [pytest.mark.integration]

_INSERT_DERIVATIVE = """
INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, params, content_hash, created_at)
VALUES (?, ?, 'thumb', ?, ?, 'deadbeef', 1)
"""


def build_still(client: TestClient, asset_id: str, at_ms: int, bytes_: bytes = b"a jpeg") -> None:
    """Put a still on disk and a row in the table as the job would, at `derivative_relpath`."""
    relative = derivative_relpath(
        asset_id, DerivativeKind.THUMB, extension="jpg", params={"at_ms": at_ms}
    )
    cache = client.app.state.settings.cache_dir  # type: ignore[attr-defined]
    destination = cache / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(bytes_)
    write(
        db_path(client),
        [(_INSERT_DERIVATIVE, (new_id(), asset_id, relative, params_key({"at_ms": at_ms})))],
    )


# --- the moment is half of the key ---------------------------------------------------------


def test_two_marks_of_one_video_ask_for_two_different_pictures(
    client: TestClient, videos: Videos
) -> None:
    """A video's two Loops ask for two different pictures."""
    early = derivative_relpath(
        videos.shared, DerivativeKind.THUMB, extension="jpg", params={"at_ms": 1_000}
    )
    late = derivative_relpath(
        videos.shared, DerivativeKind.THUMB, extension="jpg", params={"at_ms": 40_000}
    )
    assert early != late
    # And neither is the video's own still, which is filed under no parameters at all.
    plain = derivative_relpath(videos.shared, DerivativeKind.THUMB, extension="jpg")
    assert plain not in {early, late}


def test_saving_a_mark_asks_for_its_still(client: TestClient, videos: Videos) -> None:
    """Saving a Loop queues its still immediately."""
    sign_in(client)
    saved = mark(client, videos.shared, 6_000, 9_000)
    assert saved.status_code == 201, saved.text

    queued = read(
        db_path(client),
        "SELECT payload FROM jobs WHERE type = 'loop_thumbnail'",
    )
    assert len(queued) == 1
    assert json.loads(str(queued[0]["payload"])) == {
        "asset_id": videos.shared,
        "at_ms": 6_000,
    }


def test_two_marks_at_one_moment_queue_one_job(client: TestClient, videos: Videos) -> None:
    """A pair of Loops at one moment queue one job."""
    sign_in(client)
    assert mark(client, videos.shared, 2_000, 5_000).status_code == 201
    assert mark(client, videos.shared, 2_000, 8_000).status_code == 201

    queued = read(db_path(client), "SELECT id FROM jobs WHERE type = 'loop_thumbnail'")
    assert len(queued) == 1


# --- serving it ------------------------------------------------------------------------------


def test_the_marks_own_picture_is_served(client: TestClient, videos: Videos) -> None:
    sign_in(client)
    loop_id = mark(client, videos.shared, 3_000, 7_000).json()["id"]
    build_still(client, videos.shared, 3_000, b"the frame at three seconds")

    answer = client.get(f"/api/loops/{loop_id}/thumb")
    assert answer.status_code == 200
    assert answer.content == b"the frame at three seconds"
    assert answer.headers["content-type"] == "image/jpeg"


def test_a_mark_whose_picture_is_not_built_yet_is_a_404_and_says_so_on_the_row(
    client: TestClient, videos: Videos
) -> None:
    """Both halves matter. The 404 is what the address does; `still: false` is what lets the tile
    draw the video's own picture instead of requesting one that is not there and reading the refusal
    as a broken image."""
    sign_in(client)
    saved = mark(client, videos.shared, 3_000, 7_000).json()
    assert saved["still"] is False
    assert client.get(f"/api/loops/{saved['id']}/thumb").status_code == 404

    build_still(client, videos.shared, 3_000)
    assert client.get(f"/api/loops/{saved['id']}").json()["still"] is True


def test_a_row_naming_a_path_outside_the_cache_is_a_miss_rather_than_an_error(
    client: TestClient, videos: Videos
) -> None:
    """A row naming a path outside the cache is a miss."""
    sign_in(client)
    loop_id = mark(client, videos.shared, 3_000, 7_000).json()["id"]
    build_still(client, videos.shared, 3_000)
    write(db_path(client), [("UPDATE derivatives SET rel_cache_path = ?", ("../outside/x.jpg",))])

    assert client.get(f"/api/loops/{loop_id}/thumb").status_code == 404


def test_the_video_having_a_still_does_not_mean_the_mark_does(
    client: TestClient, videos: Videos
) -> None:
    """`thumb` answers for the video and `still` for this moment."""
    sign_in(client)
    saved = mark(client, videos.shared, 12_000, 15_000).json()
    # The video's own still, at no particular moment.
    relative = derivative_relpath(videos.shared, DerivativeKind.THUMB, extension="jpg")
    cache = client.app.state.settings.cache_dir  # type: ignore[attr-defined]
    (cache / relative).parent.mkdir(parents=True, exist_ok=True)
    (cache / relative).write_bytes(b"the first frame")
    write(
        db_path(client),
        [(_INSERT_DERIVATIVE, (new_id(), videos.shared, relative, params_key(None)))],
    )

    row = client.get(f"/api/loops/{saved['id']}").json()
    assert row["thumb"] is True
    assert row["still"] is False
    assert client.get(f"/api/loops/{saved['id']}/thumb").status_code == 404


def test_a_picture_of_a_video_you_may_not_see_is_a_404(client: TestClient, videos: Videos) -> None:
    """A picture of a video the guest may not see is a 404, as for a Loop never made."""
    sign_in(client)
    loop_id = mark(client, videos.private, 3_000, 7_000).json()["id"]
    build_still(client, videos.private, 3_000)

    sign_in(client, role="guest", who="two")
    assert client.get(f"/api/loops/{loop_id}/thumb").status_code == 404


def test_a_guest_shown_the_video_is_shown_the_marks_picture(
    client: TestClient, videos: Videos
) -> None:
    """The other direction, or the test above would pass on a route that refused everybody."""
    admin = sign_in(client)
    loop_id = mark(client, videos.shared, 3_000, 7_000).json()["id"]
    build_still(client, videos.shared, 3_000, b"visible frame")
    assert admin

    guest = sign_in(client, role="guest", who="three")
    share(client, videos.shared, guest)
    answer = client.get(f"/api/loops/{loop_id}/thumb")
    assert answer.status_code == 200
    assert answer.content == b"visible frame"


# --- catching up with marks that have no still yet -------------------------------------------


def test_the_sweep_finds_marks_with_no_picture_and_skips_the_ones_that_have_one(
    client: TestClient, videos: Videos
) -> None:
    """The sweep finds exactly the Loops with no picture, so it terminates."""
    sign_in(client)
    mark(client, videos.shared, 3_000, 7_000)
    mark(client, videos.shared, 20_000, 24_000)
    build_still(client, videos.shared, 3_000)

    service = part_of_app(client.app, SERVICE)  # type: ignore[arg-type]
    pending = asyncio.run(_pending(service))
    assert [(row["asset_id"], row["start_ms"]) for row in pending] == [(videos.shared, 20_000)]


async def _pending(service: LoopService) -> list[Row]:
    """Read through a connection of this test's own. See the conftest on event loops."""
    database = Database(db_path_for(service), readers=1)
    await database.connect()
    try:
        return list(await LoopService(database).marks_without_still(50))
    finally:
        await database.close()


def db_path_for(service: LoopService) -> Path:
    return service._db.path


# --- the sweep itself ----------------------------------------------------------------------------


async def test_the_sweep_asks_for_a_picture_per_moment_it_found() -> None:
    """The sweep handler asks for one picture per moment it found."""
    asked: list[tuple[str, int]] = []

    class _Found:
        async def marks_without_still(self, limit: int) -> list[dict[str, object]]:
            assert limit == jobs.SWEEP_LIMIT
            return [
                {"asset_id": "a1", "start_ms": 3_000},
                {"asset_id": "a2", "start_ms": 12_000},
            ]

        async def wants_still(self, asset_id: str, start_ms: int) -> None:
            asked.append((asset_id, start_ms))

    await jobs.backfill_stills(_context(), service=_Found())  # type: ignore[arg-type]

    assert asked == [("a1", 3_000), ("a2", 12_000)]


async def test_a_sweep_that_found_nothing_asks_for_nothing() -> None:
    """The state a library reaches and then stays in. Asked for at every boot, so a pass that did
    work anyway would be work done for ever."""
    asked: list[tuple[str, int]] = []

    class _Empty:
        async def marks_without_still(self, _limit: int) -> list[dict[str, object]]:
            return []

        async def wants_still(self, asset_id: str, start_ms: int) -> None:
            asked.append((asset_id, start_ms))

    await jobs.backfill_stills(_context(), service=_Empty())  # type: ignore[arg-type]

    assert asked == []


def _context() -> object:
    """The job context, which this handler never reads."""
    return object()


async def test_a_service_with_nobody_listening_asks_for_nothing_and_does_not_fall_over(
    tmp_path: Path,
) -> None:
    """The composition root wires the queue in; a service built without one is what every test that
    only wants the arithmetic gets. Failing to build a still is a mark wearing its video's own
    picture."""
    database = Database(tmp_path / "loops.sqlite3", readers=1)
    await database.connect()
    try:
        await LoopService(database).wants_still("a1", 3_000)
    finally:
        await database.close()
