# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making a file that has just been produced, whole, into a Loop.

"Save as Loop" cuts a clip, and the Loop's row is about that clip rather than the video it came
from. It is a job because the clip is a background encode with no id when the button is pressed.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from sift.kernel.jobs import JobContext, JobQueue
from sift.kernel.wiring import QUEUE, part_of_app
from sift.slices.loops import jobs
from sift.slices.loops.service import SERVICE, LoopService
from sift.slices.loops.tests.conftest import DURATION_MS, Videos, mark, sign_in

pytestmark = [pytest.mark.integration]


def _service(client: TestClient) -> LoopService:
    return part_of_app(client.app, SERVICE)  # type: ignore[arg-type]


def _mark_whole(
    client: TestClient,
    asset_id: str,
    running: int,
    *,
    cut_from: str | None = None,
    cut_start: int | None = None,
) -> None:
    """Run the real `jobs.mark_whole` handler on a job claimed off the real queue, with the payload
    the editor hands it. `duration_ms` is handed in: the probe that measures the new file runs
    alongside, unordered."""
    queue: JobQueue = part_of_app(client.app, QUEUE)  # type: ignore[arg-type]

    payload: dict[str, object] = {"asset_id": asset_id, "duration_ms": running}
    if cut_from is not None:
        payload["cut_from_asset_id"] = cut_from
    if cut_start is not None:
        payload["cut_from_start_ms"] = cut_start

    async def run() -> None:
        await queue.enqueue(jobs.LOOP_WHOLE, payload)
        # Claim the job this enqueued: saving a Loop also queues a `loop_thumbnail` ahead of it.
        claimed = None
        while claimed is None or claimed.type != jobs.LOOP_WHOLE:
            claimed = await queue.claim("worker-one")
            assert claimed is not None, "the job this enqueued never came back off the queue"
        await jobs.mark_whole(
            JobContext(job=claimed, worker_id="worker-one", queue=queue),
            service=_service(client),
        )

    asyncio.run(run())


def test_the_loop_is_about_the_CLIP_and_covers_the_whole_of_it(
    client: TestClient, videos: Videos
) -> None:
    """A Loop about the clip covers the whole of it, so opening it plays the clip."""
    sign_in(client)

    _mark_whole(client, videos.shared, 4_000)

    (row,) = client.get("/api/loops").json()["items"]
    assert row["asset_id"] == videos.shared
    assert row["start_ms"] == 0
    # The tile's badge is the Loop's length: the whole file.
    assert row["duration_ms"] == row["end_ms"]


def test_the_length_is_the_one_it_was_HANDED(client: TestClient, videos: Videos) -> None:
    """The length is the one handed in, exact for a re-encoded clip."""
    sign_in(client)

    _mark_whole(client, videos.shared, 7_250)

    (row,) = client.get("/api/loops").json()["items"]
    assert (row["start_ms"], row["end_ms"]) == (0, 7_250)


def test_a_piece_with_no_length_is_left_alone(client: TestClient, videos: Videos) -> None:
    """A job with no length marks nothing and does not fail: the clip is already fine."""
    sign_in(client)

    _mark_whole(client, videos.shared, 0)

    assert client.get("/api/loops").json()["items"] == []


def test_the_job_type_is_named_so_the_editor_can_ask_for_it_without_knowing_what_it_is() -> None:
    """The editor enqueues a job type string from the composition root; neither slice imports the
    other."""
    assert jobs.LOOP_WHOLE
    assert jobs.LOOP_WHOLE != jobs.LOOP_STILLS


# --- the mark this file was cut from -------------------------------------------------------------
#
# An older Loop of a stretch becomes the clip cut from it rather than a second row beside it. The
# pairing travels as plain facts about the cut (which file, from where, how long), since neither
# side can carry the other's id.


def _mark(client: TestClient, asset_id: str, start: int, end: int, **extra: object) -> str:
    """One older-style Loop of a stretch, asserted made so a refusal cannot pass the tests below."""
    made = mark(client, asset_id, start, end, **extra)
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def test_the_mark_this_was_cut_from_is_retired(client: TestClient, videos: Videos) -> None:
    """A Loop of eight to twelve seconds is retired when the clip of those four seconds lands."""
    sign_in(client)
    _mark(client, videos.private, 8_000, 12_000)

    _mark_whole(client, videos.shared, 4_000, cut_from=videos.private, cut_start=8_000)

    (row,) = client.get("/api/loops").json()["items"]
    assert row["asset_id"] == videos.shared
    assert (row["start_ms"], row["end_ms"]) == (0, 4_000)


def test_a_mark_of_a_DIFFERENT_stretch_is_left_alone(client: TestClient, videos: Videos) -> None:
    """A Loop of a different stretch of the same video is left alone: matched on the stretch."""
    sign_in(client)
    _mark(client, videos.private, 20_000, 26_000)

    _mark_whole(client, videos.shared, 4_000, cut_from=videos.private, cut_start=8_000)

    kept = {
        (row["asset_id"], row["start_ms"], row["end_ms"])
        for row in client.get("/api/loops").json()["items"]
    }
    assert (videos.private, 20_000, 26_000) in kept
    assert (videos.shared, 0, 4_000) in kept


def test_a_job_that_names_no_source_retires_nothing(client: TestClient, videos: Videos) -> None:
    """A job naming no source retires nothing, as when the player cuts an unmarked stretch."""
    sign_in(client)
    _mark(client, videos.private, 8_000, 12_000)

    _mark_whole(client, videos.shared, 4_000)

    assert len(client.get("/api/loops").json()["items"]) == 2


def test_the_wall_is_told_which_rows_ARE_their_file(client: TestClient, videos: Videos) -> None:
    """`whole` tells the wall which rows are their file, deciding whether to offer a cut."""
    sign_in(client)
    _mark(client, videos.shared, 8_000, 12_000)
    _mark(client, videos.shared, 0, DURATION_MS)

    rows = {
        (row["start_ms"], row["end_ms"]): row["whole"]
        for row in client.get("/api/loops").json()["items"]
    }
    assert rows[(8_000, 12_000)] is False
    assert rows[(0, DURATION_MS)] is True


def test_what_the_mark_KNEW_moves_onto_the_row_that_replaces_it(
    client: TestClient, videos: Videos
) -> None:
    """The retired Loop's name and tags move onto the clip's row: `loop_tags` cascades on delete."""
    sign_in(client)
    tag_id = client.post("/api/tags", json={"name": "the good bit"}).json()["id"]
    old = _mark(client, videos.private, 8_000, 12_000, name="the good bit")
    assert client.post(f"/api/loops/{old}/tags", json={"tag_id": tag_id}).status_code == 200

    _mark_whole(client, videos.shared, 4_000, cut_from=videos.private, cut_start=8_000)

    (row,) = client.get("/api/loops").json()["items"]
    assert row["asset_id"] == videos.shared
    assert row["name"] == "the good bit"
    assert [one["id"] for one in row["tags"]] == [tag_id]


def test_a_tag_taken_off_a_mark_is_off_it(client: TestClient, videos: Videos) -> None:
    """Taking a tag off a Loop takes off only that tag."""
    sign_in(client)
    keep = client.post("/api/tags", json={"name": "keep this"}).json()["id"]
    drop = client.post("/api/tags", json={"name": "drop this"}).json()["id"]
    mark = _mark(client, videos.private, 8_000, 12_000, name="a moment")
    for tag_id in (keep, drop):
        assert client.post(f"/api/loops/{mark}/tags", json={"tag_id": tag_id}).status_code == 200

    taken = client.post(f"/api/loops/{mark}/tags", json={"tag_id": drop, "add": False})

    assert taken.status_code == 200
    (row,) = client.get("/api/loops").json()["items"]
    assert [one["id"] for one in row["tags"]] == [keep]


def test_a_row_that_would_supersede_ITSELF_is_left_where_it_is(
    client: TestClient, videos: Videos
) -> None:
    """A row that would supersede itself is left where it is, with its name and tags. Driven at the
    service, since no job can produce this shape."""
    sign_in(client)
    tag_id = client.post("/api/tags", json={"name": "the good bit"}).json()["id"]
    only = _mark(client, videos.private, 8_000, 12_000, name="kept")
    assert client.post(f"/api/loops/{only}/tags", json={"tag_id": tag_id}).status_code == 200

    async def run() -> int:
        return await _service(client).supersede(videos.private, 8_000, 12_000, into=only)

    retired = asyncio.run(run())

    assert retired == 0
    (row,) = client.get("/api/loops").json()["items"]
    assert row["id"] == only
    # It keeps what it knew.
    assert row["name"] == "kept"
    assert [one["id"] for one in row["tags"]] == [tag_id]
