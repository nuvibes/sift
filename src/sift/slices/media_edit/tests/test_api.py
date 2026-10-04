# SPDX-License-Identifier: AGPL-3.0-or-later
"""The compression endpoints, driven against the real application.

The rules are proven where the service is tested. What only a real request can show is proven here:
that a refusal arrives as the right status with its own sentence intact, that a guest is turned
away by the server rather than by a hidden button, and that the answers a guest gets do not tell
them whether a file is there.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.slices.media_edit.jobs import SAMPLES_DIRECTORY
from sift.slices.media_edit.service import COMPRESS_SAMPLE
from sift.testing.auth import establish_session
from sift.testing.jobs import seed_job
from sift.testing.library import seed_asset, seed_root, set_media_shape, write_rows

pytestmark = [pytest.mark.integration]

ROOT_ID = "01HX0000000000000000000201"
TOP_FOLDER = "01HX0000000000000000000202"
ASSET_ID = "01HX0000000000000000000203"
SAMPLE_JOB = "01HX0000000000000000000204"
MISSING_ASSET = "01HX0000000000000000000299"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def library(tmp_path: Path) -> Path:
    directory = tmp_path / "media"
    directory.mkdir()
    return directory


def sign_in(client: TestClient, role: str) -> str:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role=role, username=f"compress-{role}", password="Compress-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def seed(client: TestClient, library: Path, tmp_path: Path) -> None:
    """A library, a folder and a real file, written straight into the running database.

    Synchronous, and that matters: the test client runs the application on a loop of its own, and
    seeding through the application's own stores would touch the same database from a second loop.
    """
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_root(db_path, ROOT_ID, folder_id=TOP_FOLDER, path=library)
    seed_asset(
        db_path,
        ASSET_ID,
        root_id=ROOT_ID,
        folder_id=TOP_FOLDER,
        root_path=library,
        cache_dir=tmp_path / "cache",
    )


# --- preflight ------------------------------------------------------------------------------


def test_preflight_answers_an_admin_per_file(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    response = client.post(
        "/api/compress/preflight", json={"asset_ids": [ASSET_ID], "preset": "small"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["files"]) == 1
    assert body["files"][0]["asset_id"] == ASSET_ID


def test_preflight_is_refused_for_a_guest(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "guest")

    response = client.post(
        "/api/compress/preflight", json={"asset_ids": [ASSET_ID], "preset": "small"}
    )

    assert response.status_code == 403


def test_preflight_with_nothing_named_says_so_rather_than_failing(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """The body is optional at the route so the permission answer comes first. See the router."""
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    response = client.post("/api/compress/preflight")

    assert response.status_code == 409


def test_an_unreachable_target_comes_back_with_a_reason_and_an_alternative(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    _make_it_a_long_video(db_path)

    response = client.post(
        "/api/compress/preflight",
        json={"asset_ids": [ASSET_ID], "preset": "custom", "custom_target_mb": 1},
    )

    body = response.json()
    assert body["unreachable_count"] == 1
    assert body["files"][0]["reason"]
    assert body["suggested_target_bytes"]


# --- starting -------------------------------------------------------------------------------


def test_an_admin_can_start_one(client: TestClient, library: Path, tmp_path: Path) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    _make_it_a_long_video(db_path)

    # A target the video can actually meet. The preset numbers are far below what two hours of 4K
    # can be reduced to, which is the warning path and is tested above.
    response = client.post(
        "/api/compress",
        json={"asset_ids": [ASSET_ID], "preset": "custom", "custom_target_mb": 2000},
    )

    assert response.status_code == 200, response.text
    assert response.json()["started"] == 1


def test_starting_is_refused_for_a_guest(client: TestClient, library: Path, tmp_path: Path) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "guest")

    response = client.post("/api/compress", json={"asset_ids": [ASSET_ID], "preset": "small"})

    assert response.status_code == 403


def test_starting_with_nothing_named_says_so(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    assert client.post("/api/compress").status_code == 409


# --- the sample -----------------------------------------------------------------------------


def test_an_admin_can_ask_for_a_sample(client: TestClient, library: Path, tmp_path: Path) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    response = client.post(
        f"/api/assets/{ASSET_ID}/compress/sample",
        json={"asset_ids": [ASSET_ID], "preset": "small"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["job_id"]


def test_a_sample_with_no_body_asks_for_a_compatible_one(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """The body is optional so the permission answer comes before the validation one."""
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    assert client.post(f"/api/assets/{ASSET_ID}/compress/sample").status_code == 200


def test_a_guest_asking_for_a_sample_is_told_there_is_no_such_file(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """404, not 403. The other answer confirms the file is there."""
    seed(client, library, tmp_path)
    sign_in(client, "guest")

    response = client.post(
        f"/api/assets/{ASSET_ID}/compress/sample",
        json={"asset_ids": [ASSET_ID], "preset": "small"},
    )

    assert response.status_code == 404


def test_reading_a_finished_sample_hands_the_bytes_over(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_job(db_path, SAMPLE_JOB, state="done", job_type=COMPRESS_SAMPLE)
    samples = tmp_path / "cache" / SAMPLES_DIRECTORY
    samples.mkdir(parents=True, exist_ok=True)
    (samples / f"{SAMPLE_JOB}.mp4").write_bytes(b"a few seconds")

    response = client.get(f"/api/compress/samples/{SAMPLE_JOB}")

    assert response.status_code == 200
    assert response.content == b"a few seconds"
    # Never kept by a browser: it is thrown away and a second one lands elsewhere anyway.
    assert response.headers["cache-control"] == "no-store"


def test_a_sample_that_is_not_there_is_a_404(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_job(db_path, SAMPLE_JOB, state="done", job_type=COMPRESS_SAMPLE)

    assert client.get(f"/api/compress/samples/{SAMPLE_JOB}").status_code == 404


def test_a_job_that_is_not_a_sample_is_a_404(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """The id has to name a job of this one type, or this becomes a way to read other things."""
    seed(client, library, tmp_path)
    sign_in(client, "admin")
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_job(db_path, SAMPLE_JOB, state="done", job_type="probe")

    assert client.get(f"/api/compress/samples/{SAMPLE_JOB}").status_code == 404


@pytest.mark.parametrize("attempt", ["../../etc/passwd", "not-an-id", "", "."])
def test_a_sample_id_that_is_not_an_id_is_refused(
    client: TestClient, library: Path, tmp_path: Path, attempt: str
) -> None:
    """The filename is built from the id, so the id is checked before it is used as one."""
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    response = client.get(f"/api/compress/samples/{attempt}")

    assert response.status_code in (404, 405)


def test_reading_a_sample_is_refused_for_a_guest(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "guest")
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_job(db_path, SAMPLE_JOB, state="done", job_type=COMPRESS_SAMPLE)

    assert client.get(f"/api/compress/samples/{SAMPLE_JOB}").status_code == 403


# --- what a file was made from ------------------------------------------------------------------


def test_an_ordinary_file_reports_no_provenance(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    response = client.get(f"/api/assets/{ASSET_ID}/produced")

    assert response.status_code == 200
    assert response.json() is None


def test_asking_about_a_file_that_is_not_there_is_a_404(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    assert client.get(f"/api/assets/{MISSING_ASSET}/produced").status_code == 404


def test_a_file_nothing_was_made_from_answers_an_empty_list(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """The route is driven, not merely declared. A matrix entry is a promise, not evidence."""
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    response = client.get(f"/api/assets/{ASSET_ID}/made-from")

    assert response.status_code == 200
    assert response.json() == {"copies": []}


def test_asking_what_was_made_from_a_file_that_is_not_there_is_a_404(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    assert client.get(f"/api/assets/{MISSING_ASSET}/made-from").status_code == 404


def _make_it_a_long_video(db_path: Path) -> None:
    """Give the seeded clip the shape of something worth compressing."""
    set_media_shape(
        db_path,
        ASSET_ID,
        width=3840,
        height=2160,
        duration_ms=8040000,
        fps=24,
        size_bytes=32000000000,
    )


# --- editing ---------------------------------------------------------------------------------------


def _make_it_a_cuttable_video(db_path: Path) -> None:
    """The seeded row carries no mime, and the container a cut lands in is read from it.

    Set here rather than in the shared helper: what a seeded asset needs differs per feature, and
    a mime added to the helper for this would change what every other feature's tests are given.
    """
    _make_it_a_long_video(db_path)
    write_rows(db_path, [("UPDATE assets SET mime = 'video/mp4' WHERE id = ?", (ASSET_ID,))])


def test_an_admin_is_told_what_an_edit_would_do(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    _make_it_a_cuttable_video(client.app.state.database.path)  # type: ignore[attr-defined]
    sign_in(client, "admin")

    response = client.post(
        f"/api/assets/{ASSET_ID}/edit/preflight",
        json={"steps": [{"operation": "clip", "start_ms": 90000, "duration_ms": 15000}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["allowed"] is True
    assert body["output_filename"] == "clip-from-1m30s.mp4"
    # FALSE, and it is a clip. `approximate_start` is about a COPIED stream landing on the last
    # keyframe before the moment; a clip is re-encoded from the moment asked for, which is the
    # whole difference between it and a trim.
    assert body["approximate_start"] is False


def test_an_edit_that_will_not_work_comes_back_as_an_answer_rather_than_an_error(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """A rectangle dragged off the edge is somebody in the middle of doing something, not a fault."""
    seed(client, library, tmp_path)
    _make_it_a_cuttable_video(client.app.state.database.path)  # type: ignore[attr-defined]
    sign_in(client, "admin")

    response = client.post(
        f"/api/assets/{ASSET_ID}/edit/preflight",
        json={"steps": [{"operation": "clip", "start_ms": 8000000, "duration_ms": 900000}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["allowed"] is False


def test_starting_an_edit_queues_it_and_says_what_it_will_be_called(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    _make_it_a_cuttable_video(client.app.state.database.path)  # type: ignore[attr-defined]
    sign_in(client, "admin")

    response = client.post(
        f"/api/assets/{ASSET_ID}/edit",
        json={"steps": [{"operation": "trim", "start_ms": 0, "duration_ms": 60000}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["output_filename"] == "clip-trimmed.mp4"


def test_starting_an_edit_that_will_not_work_is_a_conflict_with_its_own_sentence(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    _make_it_a_cuttable_video(client.app.state.database.path)  # type: ignore[attr-defined]
    sign_in(client, "admin")

    response = client.post(
        f"/api/assets/{ASSET_ID}/edit",
        json={"steps": [{"operation": "clip", "start_ms": 8000000, "duration_ms": 900000}]},
    )

    assert response.status_code == 409
    assert "past the end" in response.json()["detail"]


def test_a_guest_asking_about_an_edit_is_told_there_is_no_such_file(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """Not "only an admin can do that", which would confirm the file is there."""
    seed(client, library, tmp_path)
    sign_in(client, "guest")

    response = client.post(
        f"/api/assets/{ASSET_ID}/edit/preflight",
        json={"steps": [{"operation": "rotate", "turn": "right"}]},
    )

    assert response.status_code == 404


def test_a_guest_starting_an_edit_is_told_the_same_thing(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "guest")

    response = client.post(
        f"/api/assets/{ASSET_ID}/edit", json={"steps": [{"operation": "rotate", "turn": "right"}]}
    )

    assert response.status_code == 404


def test_a_request_with_no_body_is_answered_after_the_account_is_settled(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """The order is the point. Answered the other way round, a malformed request and a well-formed
    one get different replies from a route the caller may not use either way, which turns the
    shape of a request into a way of asking whether a file exists."""
    seed(client, library, tmp_path)
    _make_it_a_cuttable_video(client.app.state.database.path)  # type: ignore[attr-defined]
    sign_in(client, "admin")

    assert client.post(f"/api/assets/{ASSET_ID}/edit/preflight").status_code == 409
    assert client.post(f"/api/assets/{ASSET_ID}/edit").status_code == 409

    sign_in(client, "guest")
    assert client.post(f"/api/assets/{ASSET_ID}/edit/preflight").status_code == 404
    assert client.post(f"/api/assets/{ASSET_ID}/edit").status_code == 404


def test_the_editor_is_told_how_big_the_picture_is(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """Asked once when the panel opens, because a rectangle needs something to be dragged over."""
    seed(client, library, tmp_path)
    _make_it_a_cuttable_video(client.app.state.database.path)  # type: ignore[attr-defined]
    sign_in(client, "admin")

    response = client.get(f"/api/assets/{ASSET_ID}/edit/frame")

    assert response.status_code == 200, response.text
    assert response.json()["asset_id"] == ASSET_ID


def test_a_guest_asking_how_big_a_picture_is_told_there_is_no_such_file(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """Not "only an admin can do that", which would confirm the file is there."""
    seed(client, library, tmp_path)
    sign_in(client, "guest")

    response = client.get(f"/api/assets/{ASSET_ID}/edit/frame")

    assert response.status_code == 404
