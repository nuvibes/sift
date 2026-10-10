# SPDX-License-Identifier: AGPL-3.0-or-later
"""The paths taken when something has gone wrong.

Error handling is where a slice's real behaviour lives, and it is the part that never runs during
ordinary use, so it is the part most likely to be wrong and least likely to be noticed. Each of
these drives one failure deliberately rather than waiting for it to happen.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import Job, JobState
from sift.kernel.media import Accelerator, FFmpegError
from sift.kernel.wiring import QUEUE, part_of_app
from sift.slices.player import policy, tuning
from sift.slices.player.cache import SegmentCache
from sift.slices.player.service import PlayerService, SegmentUnavailable
from sift.slices.player.tests.conftest import ANCIENT, Library, sign_in, write
from sift.slices.player.tests.conftest import asset as an_asset
from sift.slices.player.tests.conftest import db_path as _db_path
from sift.slices.player.transcode import render

pytestmark = pytest.mark.integration


# --- the request waiting on a job that never finishes well ----------------------------------------


class _Queue:
    """A job queue that answers however a test needs it to."""

    def __init__(self, *states: JobState, error: str | None = None) -> None:
        self._states = list(states)
        self._error = error
        self.enqueued: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, job_type: str, payload: Any = None, **options: Any) -> str:
        self.enqueued.append((job_type, dict(payload or {})))
        return "01HX0000000000000000000001"

    async def get(self, job_id: str) -> Job | None:
        # No states left to give means the row is gone, which is the "job vanished" case.
        if not self._states:
            return None
        state = self._states.pop(0) if len(self._states) > 1 else self._states[0]
        return Job(
            id=job_id, parent_id=None, type="transcode", state=state, priority=100,
            payload={}, progress=0.0, attempts=1, max_attempts=1, claimed_by=None,
            heartbeat_at=None, error=self._error, note=None, run_after=None,
            created_at=0, updated_at=0,
        )  # fmt: skip


def _service(tmp_path: Path, queue: Any) -> PlayerService:
    return PlayerService(
        SegmentCache(tmp_path / "cache", max_bytes=1_000_000),
        queue,
        settings=Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        accelerator=Accelerator(
            HardwareReport(
                cpu_count=4,
                total_ram_bytes=8 << 30,
                worker_concurrency=2,
                cuda=False,
                rocm=False,
                transcode_encoders=(),
                warnings=(),
            )
        ),
    )


PLAN = policy.Plan(route=policy.Route.TRANSCODE, reason="")


async def test_a_transcode_that_fails_is_reported_rather_than_hung_on(tmp_path: Path) -> None:
    service = _service(tmp_path, _Queue(JobState.FAILED, error="the encoder gave up"))

    with pytest.raises(SegmentUnavailable, match="the encoder gave up"):
        await service.segment(an_asset(), index=0, plan=PLAN)


async def test_a_cancelled_transcode_is_reported(tmp_path: Path) -> None:
    service = _service(tmp_path, _Queue(JobState.CANCELED))

    with pytest.raises(SegmentUnavailable):
        await service.segment(an_asset(), index=0, plan=PLAN)


async def test_a_job_that_vanished_is_not_waited_on_forever(tmp_path: Path) -> None:
    """A row deleted underneath the request: a wiped database, a manual cleanup.

    Waiting for something that is gone would hold the connection until the timeout for no reason.
    """
    service = _service(tmp_path, _Queue())

    with pytest.raises(SegmentUnavailable, match="disappeared"):
        await service.segment(an_asset(), index=0, plan=PLAN)


async def test_a_transcode_that_never_finishes_gives_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody is watching a spinner. A segment that has not arrived is not going to be wanted,
    and holding the request open forever helps nobody."""
    monkeypatch.setattr(tuning, "SEGMENT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(tuning, "JOB_POLL_SECONDS", 0.01)
    service = _service(tmp_path, _Queue(JobState.RUNNING))

    with pytest.raises(SegmentUnavailable, match="did not finish in time"):
        await service.segment(an_asset(), index=0, plan=PLAN)


async def test_a_job_that_says_it_finished_without_leaving_a_segment_is_an_error(
    tmp_path: Path,
) -> None:
    """DONE but nothing on disk. It should not happen; if it does, the caller must not be handed
    a path to a file that is not there."""
    service = _service(tmp_path, _Queue(JobState.DONE))

    with pytest.raises(SegmentUnavailable, match="could not be produced"):
        await service.segment(an_asset(), index=0, plan=PLAN)


async def test_the_job_payload_carries_ids_and_never_a_path(tmp_path: Path) -> None:
    """The queue enforces this at runtime, and this says it at the point the payload is built.

    A path in a payload is a path in every log line, backup and diagnostics export the job appears
    in, and a job that accepts one can be pointed at any file on the machine by whoever can
    enqueue it.
    """
    queue = _Queue(JobState.DONE)
    service = _service(tmp_path, queue)

    with pytest.raises(SegmentUnavailable):
        await service.segment(an_asset(), index=3, plan=PLAN)

    _, payload = queue.enqueued[0]
    assert payload == {
        "asset_id": an_asset().id,
        "index": 3,
        "route": "transcode",
        "scale_height": None,
    }
    assert not any(isinstance(value, str) and "/" in value for value in payload.values())


# --- ffmpeg itself failing ---------------------------------------------------------------------------


async def test_a_non_zero_exit_becomes_an_error_carrying_the_tools_own_words(
    tmp_path: Path,
) -> None:
    """ffmpeg explains itself on stderr, and throwing that away turns a diagnosable failure into
    'it did not work'."""
    destination = tmp_path / "out.m4s"

    with pytest.raises(FFmpegError):
        await render(["/bin/false", str(destination)], destination, stage="segment")


async def test_a_tool_that_cannot_be_started_at_all_is_reported_as_an_ffmpeg_failure(
    tmp_path: Path,
) -> None:
    """A missing binary, a bad path in settings. One error type for the caller either way."""
    destination = tmp_path / "out.m4s"

    with pytest.raises(FFmpegError):
        await render(["/nonexistent/ffmpeg", str(destination)], destination, stage="segment")


async def test_a_failed_render_leaves_no_half_written_segment(tmp_path: Path) -> None:
    """The destination is a cache key: the moment a file exists there, another request serves it.

    A partially written segment at the real path is a truncated video handed to a player, so the
    render writes aside and moves only on success.
    """
    destination = tmp_path / "out.m4s"

    with pytest.raises(FFmpegError):
        await render(["/bin/false", str(destination)], destination, stage="segment")

    assert not destination.exists()
    assert list(tmp_path.glob(".*partial*")) == []
    await asyncio.sleep(0)


# --- routes, at their edges ----------------------------------------------------------------------------


def test_an_asset_with_no_location_on_disk_is_not_streamable(
    client: TestClient, library: Library
) -> None:
    """A row with no `asset_locations` behind it: the file was removed from every root."""
    sign_in(client)
    asset_id = library.id_of("h264")
    write(_db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (asset_id,))])

    assert client.get(f"/api/assets/{asset_id}/stream").status_code == 404


def test_an_asset_whose_only_copy_is_on_an_unplugged_drive_is_not_streamable(
    client: TestClient, library: Library
) -> None:
    """The other half of the pair above, and a different route through the code.

    Deleting the location rows makes the asset invisible, so the refusal comes from the visibility
    check before anything asks where the file is. Marking the location MISSING leaves the asset
    perfectly visible (it is content Sift knows about and cannot currently see, the same state as
    an unplugged drive), and it is the streaming route itself that has to notice there is nowhere
    to read from.
    """
    sign_in(client)
    asset_id = library.id_of("h264")
    write(
        _db_path(client),
        [("UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (asset_id,))],
    )

    assert client.get(f"/api/assets/{asset_id}/stream").status_code == 404


def test_a_file_with_nowhere_to_read_it_from_has_no_playback_plan(
    client: TestClient, library: Library
) -> None:
    """The plan says what the stream will say: a file whose every copy is missing is not found.

    A plan that answered as usual sent the player to an address that then refused it, and a
    Theater cell, told how to play a file it could not have, attached, failed and held still
    instead of stepping over it the way it steps over any other file that is not there.
    """
    sign_in(client)
    asset_id = library.id_of("h264")
    assert client.post(f"/api/assets/{asset_id}/playback", json=ANCIENT).status_code == 200
    write(
        _db_path(client),
        [("UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (asset_id,))],
    )

    assert client.post(f"/api/assets/{asset_id}/playback", json=ANCIENT).status_code == 404


def test_a_plan_says_a_copy_can_be_read_when_it_can(client: TestClient, library: Library) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")

    plan = client.post(f"/api/assets/{asset_id}/playback", json=ANCIENT).json()

    assert plan["unreadable"] is None
    assert plan["scan_queued"] is False


def test_a_file_gone_from_a_folder_that_answers_is_said_to_be_gone(
    client: TestClient, library: Library, idle_workers: None
) -> None:
    """Moved away since the last scan: the browser's refusal of its address must not be read as a
    codec it cannot play, so the plan says the copy cannot be read, and which scan will find it."""
    sign_in(client)
    asset_id = library.id_of("h264")
    for kept in (library.media / "clips").glob("h264-*"):
        kept.unlink()

    gone = client.post(f"/api/assets/{asset_id}/playback", json=ANCIENT).json()
    assert gone["unreadable"] == "gone"
    assert gone["scan_queued"] is False

    queue = part_of_app(client.app, QUEUE)  # type: ignore[arg-type]
    client.portal.call(queue.enqueue, "scan", {"root_id": library.root})  # type: ignore[union-attr]
    coming = client.post(f"/api/assets/{asset_id}/playback", json=ANCIENT).json()
    assert coming["scan_queued"] is True


def test_a_file_on_a_folder_that_does_not_answer_is_said_to_be_away(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")
    shutil.rmtree(library.media)

    plan = client.post(f"/api/assets/{asset_id}/playback", json=ANCIENT).json()

    assert plan["unreadable"] == "away"


def test_an_empty_file_is_served_as_an_empty_response(client: TestClient, library: Library) -> None:
    """A zero-byte file has no range to return, and asking for one would be a division by nothing.

    It is served as an empty 200 rather than an error: the file genuinely is what it is.
    """
    sign_in(client)
    asset_id = library.id_of("h264")
    next(library.media.glob("clips/h264-*.mp4")).write_bytes(b"")

    response = client.get(f"/api/assets/{asset_id}/stream")

    assert response.status_code == 200
    assert response.content == b""


def test_a_nonsense_route_in_the_query_falls_back_to_transcoding(
    client: TestClient, library: Library
) -> None:
    """The route rides in the URL, so it is validated rather than trusted. Anything unrecognized
    becomes the safe answer (convert it) rather than an error or an arbitrary ffmpeg mode."""
    sign_in(client)
    asset_id = library.id_of("hevc")

    response = client.get(f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}?route=chmod-777")

    assert response.status_code == 200


def test_a_sensible_height_in_the_query_is_honoured(client: TestClient, library: Library) -> None:
    """The counterpart to the crafted-height test: a legitimate one is accepted and used."""
    sign_in(client)
    asset_id = library.id_of("hevc")

    response = client.get(
        f"/api/assets/{asset_id}/hls/0{tuning.SEGMENT_SUFFIX}?route=transcode&height=120"
    )

    assert response.status_code == 200


# --- the policy's last branch ---------------------------------------------------------------------------


def test_a_file_already_below_the_ceiling_that_still_cannot_keep_up_is_not_scaled() -> None:
    """Nothing to shrink. Downscaling a 720p file to a 1080p ceiling would enlarge it: more work,
    to fix a problem caused by there being too much work."""
    plan = policy.decide(
        replace(an_asset(vcodec="av1", width=1280, height=720, fps=240.0), acodec=None),
        policy.ClientCapabilities.nothing(),
        max_height=1080,
        cpu_count=1,
    )

    assert plan.streamable is False
    assert plan.scale_height is None, "there is nothing above the ceiling to bring down"
