# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dashboard's endpoints, driven against the real application.

The socket is what most of this is about. Three of the app's standing rules are enforced by
machinery that only understands HTTP requests, and a WebSocket quietly escapes all three: the
route table's check cannot see it, the browser's same-origin policy does not cover it, and it
stays open long enough that authorising it once is a statement about the past. Each is tested
here by doing the thing rather than by reading the code.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from sift.kernel import sampling
from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs.worker_pool import WorkerPool, by_itself_job_types, job_name
from sift.main import create_app
from sift.slices.media_jobs import REBUILD_THUMBNAILS
from sift.slices.media_jobs import jobs as media_jobs_jobs
from sift.testing.auth import establish_session
from sift.testing.jobs import seed_job
from sift.testing.library import seed_asset, seed_root, write_rows

ROOT_ID = "01HX0000000000000000000R01"
FOLDER_ID = "01HX0000000000000000000F01"
ASSET_ID = "01HX0000000000000000000A01"

pytestmark = [pytest.mark.integration]

STREAM = "/api/jobs/stream"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    # No workers, and this file is exactly the one that needs them stopped.
    #
    # Every test here seeds a row in a state and asks what the API does to it. A running pool is
    # the other thing that changes those rows: it claims what is queued, so a job retried a
    # moment ago reads back as `running` rather than `queued` (correctly, by a pool doing its
    # job). It only loses the race when the machine is busy, so it passes alone and fails in a
    # full parallel run, which reads as flake and is not one.
    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def sign_in(client: TestClient, role: str) -> str:
    """A real user of this role, with a real session. Returns the user's id."""
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role=role, username=f"jobs-{role}", password="Jobs-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def seed(
    client: TestClient, job_id: str, *, state: str = "failed", job_type: str = "probe"
) -> None:
    seed_job(
        client.app.state.database.path,  # type: ignore[attr-defined]
        job_id,
        state=state,
        job_type=job_type,
        error="it did not work",
    )


def row_for(client: TestClient, job_id: str) -> dict[str, object]:
    """The row for one job, found by its id.

    Not `jobs[0]`. A booted application schedules recurring work of its own, so the newest row in
    the queue is not necessarily the one a test just seeded, and a test reading position zero would
    assert about whatever happened to be there.
    """
    page = client.get("/api/jobs", params={"limit": 100}).json()
    # And each kind by its type: a row of work that runs by itself which nobody pressed is quiet
    # on the unnamed list (`by_itself_job_types`), and named by its type every row of it is read.
    rows = list(page["jobs"])
    for kind in page["by_type"]:
        rows += client.get("/api/jobs", params={"limit": 100, "type": kind}).json()["jobs"]
    found: list[dict[str, object]] = [job for job in rows if job["id"] == job_id]
    assert found, f"no job {job_id} in the queue"
    return found[0]


# --- who may look -----------------------------------------------------------------------------


def test_a_guest_is_refused_every_endpoint_with_no_interface_involved(client: TestClient) -> None:
    """The dashboard reports what Sift is doing with the files in the library, which is a picture
    of the library. Hiding the nav item is not what keeps a guest out of it; this is."""
    seed(client, "01HX0000000000000000000009")
    sign_in(client, "guest")

    assert client.get("/api/jobs").status_code == 403
    assert client.post("/api/jobs/01HX0000000000000000000009/retry").status_code == 403
    assert client.post("/api/jobs/01HX0000000000000000000009/cancel").status_code == 403
    assert client.post("/api/jobs/retry-canceled").status_code == 403
    assert client.post("/api/jobs/clear-canceled").status_code == 403
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(STREAM):
        pass


def test_a_signed_out_caller_is_refused(client: TestClient) -> None:
    assert client.get("/api/jobs").status_code == 401
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(STREAM):
        pass


def test_an_admin_sees_the_queue(client: TestClient) -> None:
    seed(client, "01HX0000000000000000000001", state="failed", job_type="thumbnail")
    sign_in(client, "admin")

    body = client.get("/api/jobs", params={"type": "thumbnail"}).json()

    assert body["total"] == 1
    assert body["jobs"][0]["type"] == "thumbnail"
    assert body["jobs"][0]["state"] == "failed"
    # `counts` is the whole queue by design: it is what the dashboard's badge is drawn from, and a
    # badge that only counted the filter somebody happened to be looking through would be useless.
    # So this asserts the seeded row is in there rather than that it is alone: a booted application
    # schedules its own recurring housekeeping, and that is a real queued job, not noise to exclude.
    assert body["counts"]["failed"] == 1


def test_the_page_says_whether_the_pool_is_stepping_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Activity says so beside the running count, from this field and nothing it works out itself."""
    from sift.kernel import attention

    sign_in(client, "admin")
    # Set by hand either way: the real reading is whoever is at this computer's keyboard.
    reader = attention.Attention(lambda: 1.0)
    monkeypatch.setattr(attention, "ATTENTION", reader)
    reader.workers(8, step_back=False)
    assert client.get("/api/jobs").json()["stepping_back"] is False
    reader.workers(8, step_back=True)
    assert client.get("/api/jobs").json()["stepping_back"] is True


def test_the_page_says_the_share_the_step_back_keeps_to(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The leaf and the Activity line name the share, from this field: the setting's reading."""
    from sift.kernel import attention

    sign_in(client, "admin")
    reader = attention.Attention(lambda: 1.0)
    monkeypatch.setattr(attention, "ATTENTION", reader)
    assert client.get("/api/jobs").json()["step_back_share"] == 25
    reader.workers(12, step_back=True, share=40)
    assert client.get("/api/jobs").json()["step_back_share"] == 40


def test_the_page_says_why_the_work_steps_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel import attention

    sign_in(client, "admin")
    reader = attention.Attention(lambda: 1.0)
    monkeypatch.setattr(attention, "ATTENTION", reader)
    assert client.get("/api/jobs").json()["step_back_for"] is None
    reader.workers(8, step_back=True)
    assert client.get("/api/jobs").json()["step_back_for"] == "input"
    playing = attention.Attention(lambda: None, since_played=lambda: 1.0)
    monkeypatch.setattr(attention, "ATTENTION", playing)
    playing.workers(8, step_back=True)
    assert client.get("/api/jobs").json()["step_back_for"] == "playing"


def test_the_page_says_what_other_programs_keep_busy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel import attention, device_load

    sign_in(client, "admin")
    reader = attention.Attention(lambda: None)
    monkeypatch.setattr(attention, "ATTENTION", reader)
    monkeypatch.setattr(device_load.READER, "over", ["graphics"])
    assert client.get("/api/jobs").json()["step_back_over"] == []
    reader.workers(8, step_back=True, others_busy=True)
    assert client.get("/api/jobs").json()["step_back_over"] == ["graphics"]


def test_the_press_for_turbo_mode_is_answered_and_every_page_follows_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The leaf and the bolt: what the route answers, and what the page says, in each state."""
    from sift.kernel import attention

    sign_in(client, "admin")
    since = {"seconds": 1.0}
    reader = attention.Attention(lambda: since["seconds"])
    monkeypatch.setattr(attention, "ATTENTION", reader)

    def page() -> tuple[bool, bool]:
        body = client.get("/api/jobs").json()
        return body["stepping_back"], body["turbo_mode"]

    # Somebody here: stepping back, the leaf.
    reader.workers(8, step_back=True)
    assert page() == (True, False)
    # Pressed: turbo mode although somebody is here, the bolt.
    answer = client.post("/api/jobs/turbo-mode", json={"on": True})
    assert answer.status_code == 200
    assert answer.json() == {"stepping_back": False, "turbo_mode": True, "pressed": True}
    assert page() == (False, True)
    assert reader.workers(8, step_back=True) == 8
    # Pressed again: stepping back again.
    answer = client.post("/api/jobs/turbo-mode", json={"on": False})
    assert answer.json() == {"stepping_back": True, "turbo_mode": False, "pressed": False}
    assert page() == (True, False)
    assert reader.workers(8, step_back=True) == 2
    # Nobody here: neither is said, pressed or not, and the pool runs its full count by itself.
    since["seconds"] = 600.0
    assert reader.workers(8, step_back=True) == 8
    assert page() == (False, False)
    answer = client.post("/api/jobs/turbo-mode", json={"on": True})
    assert answer.json() == {"stepping_back": False, "turbo_mode": False, "pressed": True}
    assert page() == (False, False)


def test_the_press_for_turbo_mode_is_an_admins(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel import attention

    reader = attention.Attention(lambda: 1.0)
    monkeypatch.setattr(attention, "ATTENTION", reader)
    sign_in(client, "guest")
    assert client.post("/api/jobs/turbo-mode", json={"on": True}).status_code == 403
    assert reader.pressed is False


def test_the_update_check_and_the_backup_are_left_off_the_list_and_its_tallies(
    client: TestClient,
) -> None:
    """Background upkeep: neither a row nor a number on Activity, in the flat list, the folded one
    or a state's, and the total says the rows drawn. Named by type, it is still there to read."""
    upkeep = {
        "01HX0000000000000000000011": "update_check",
        "01HX0000000000000000000012": "backup_run",
    }
    for job_id, job_type in upkeep.items():
        seed(client, job_id, state="failed", job_type=job_type)
    seed(client, "01HX0000000000000000000013", state="failed", job_type="thumbnail")
    sign_in(client, "admin")

    for params in ({"limit": 100}, {"limit": 100, "fold": True}, {"state": "failed"}):
        page = client.get("/api/jobs", params=params).json()
        listed = {job["id"] for job in page["jobs"]}
        assert "01HX0000000000000000000013" in listed, params
        assert not listed & set(upkeep), params
        assert page["total"] == len(page["jobs"]), params
        assert not set(page["by_type"]) & set(upkeep.values()), params
        assert not set(page["work"]) & set(upkeep.values()), params
        assert page["counts"]["failed"] == 1, params

    named = client.get("/api/jobs", params={"type": "backup_run"}).json()
    assert [job["id"] for job in named["jobs"]] == ["01HX0000000000000000000012"]


def test_work_that_runs_by_itself_is_off_now_and_its_type_is_still_a_choice(
    client: TestClient,
) -> None:
    """The fingerprint pass a library taking in files asks for itself, done: no row on Now and no
    number there, in the flat list, the folded one or Done's. Its failure is still listed, the same
    work as a step of a download is still in that download, the Type choice still offers it by its
    name, and naming it reads it."""
    quiet = media_jobs_jobs.FINGERPRINT_FOR_STASH_BOXES
    assert quiet in by_itself_job_types()
    assert media_jobs_jobs.REBUILD_THUMBNAILS not in by_itself_job_types(), "a press is quiet"
    database = client.app.state.database.path  # type: ignore[attr-defined]
    seed_job(database, "01HX0000000000000000000031", state="done", job_type=quiet)
    seed(client, "01HX0000000000000000000032", state="failed", job_type=quiet)
    seed_job(database, "01HX0000000000000000000033", state="done", job_type="download")
    seed_job(
        database,
        "01HX0000000000000000000034",
        state="done",
        job_type="thumbnail",
        parent_id="01HX0000000000000000000033",
    )
    sign_in(client, "admin")

    for params in ({"limit": 100}, {"limit": 100, "fold": True}, {"state": "done"}):
        page = client.get("/api/jobs", params=params).json()
        listed = {job["id"] for job in page["jobs"]}
        assert "01HX0000000000000000000031" not in listed, params
        assert "01HX0000000000000000000033" in listed, params
        assert page["total"] == len(page["jobs"]), params
        drawn = sum(1 for job in page["jobs"] if job["state"] == "done")
        assert page["counts"].get("done", 0) == drawn + (params.get("fold") is True), params
        assert page["names"][quiet] == "Fingerprinting for duplicates", params
    flat = client.get("/api/jobs", params={"limit": 100}).json()
    assert {"01HX0000000000000000000032", "01HX0000000000000000000034"} <= {
        job["id"] for job in flat["jobs"]
    }, "a failure or a step of a run went quiet"

    named = client.get("/api/jobs", params={"type": quiet}).json()
    assert {job["id"] for job in named["jobs"]} == {
        "01HX0000000000000000000031",
        "01HX0000000000000000000032",
    }


def test_a_page_of_one_familys_steps_carries_no_tallies(client: TestClient) -> None:
    """The numbers above the list count the whole queue's tabs; a family's steps are a list
    inside one row, and numbers above them would count a universe no tab draws."""
    database = client.app.state.database.path  # type: ignore[attr-defined]
    seed_job(database, "01HX0000000000000000000051", state="done", job_type="download")
    seed_job(
        database,
        "01HX0000000000000000000052",
        state="done",
        job_type="thumbnail",
        parent_id="01HX0000000000000000000051",
    )
    sign_in(client, "admin")

    page = client.get("/api/jobs", params={"parent_id": "01HX0000000000000000000051"}).json()

    assert [job["id"] for job in page["jobs"]] == ["01HX0000000000000000000052"]
    assert page["tallies"] == {}
    assert client.get("/api/jobs").json()["tallies"] != {}, "the whole queue's tabs are counted"


def test_rows_of_a_type_no_handler_claims_are_older_tasks_never_their_ids(
    client: TestClient,
) -> None:
    """A type an older release left in the table is not named by its id anywhere: the Type choice
    offers it under "Older tasks" (`older`), its row reads "Older task", and `older=true` lists
    those rows and no other."""
    seed(client, "01HX0000000000000000000041", state="failed", job_type="face_asked_only")
    seed(client, "01HX0000000000000000000042", state="failed", job_type="thumbnail")
    sign_in(client, "admin")

    page = client.get("/api/jobs", params={"limit": 100}).json()
    assert "face_asked_only" not in page["names"]
    assert page["older"] == ["face_asked_only"]
    assert all(name != kind for kind, name in page["names"].items()), "a raw id is a name"
    assert row_for(client, "01HX0000000000000000000041")["name"] == "Older task"

    older = client.get("/api/jobs", params={"older": True}).json()
    assert [job["id"] for job in older["jobs"]] == ["01HX0000000000000000000041"]
    # Its tallies are its rows, every older kind added together on the server.
    assert older["tallies"] == {"failed": 1, "all": 1}


def test_stopped_jobs_can_be_started_again(client: TestClient) -> None:
    """The other half of stopping everything. Failures are a different situation and stay put.

    Stopping an import that got away is one press; without this, the only route back to the same
    work would be scanning the folders, walking the whole library to rediscover files it already
    knew about, with their payloads still sitting in the table.
    """
    seed(client, "01HX0000000000000000000021", state="canceled")
    seed(client, "01HX0000000000000000000022", state="canceled")
    seed(client, "01HX0000000000000000000023", state="failed")
    sign_in(client, "admin")

    assert client.post("/api/jobs/retry-canceled").json()["retried"] == 2

    by_state = client.get("/api/jobs", params={"limit": 100}).json()["by_type"]["probe"]
    assert by_state.get("canceled", 0) == 0
    assert by_state["failed"] == 1
    assert by_state["queued"] == 2

    # Nothing stopped is a success with a zero, the same as retrying nothing. A 404 here would make
    # a button that did what was asked look broken.
    assert client.post("/api/jobs/retry-canceled").json()["retried"] == 0


def test_stopped_jobs_can_be_thrown_away(client: TestClient) -> None:
    """The pile `clear-failed` does not touch, and the one that actually accumulates.

    A stop cancels the whole queue, so stopped rows can outnumber failures by thousands to one, and
    clearing the failures must not be the only way to clear the screen.
    """
    seed(client, "01HX0000000000000000000031", state="canceled")
    seed(client, "01HX0000000000000000000032", state="canceled")
    seed(client, "01HX0000000000000000000033", state="failed")
    seed(client, "01HX0000000000000000000034", state="done")
    sign_in(client, "admin")

    assert client.post("/api/jobs/clear-canceled").json()["cleared"] == 2

    probes = client.get("/api/jobs", params={"limit": 100}).json()["by_type"]["probe"]
    assert probes.get("canceled", 0) == 0
    # Neither of the other two piles is touched: one is work that broke and has its own button, the
    # other is the record of what the library actually has.
    assert probes["failed"] == 1
    assert probes["done"] == 1

    assert client.post("/api/jobs/clear-canceled").json()["cleared"] == 0


def test_the_work_tally_counts_the_whole_queue_and_not_the_page(client: TestClient) -> None:
    """The summary bars above the table are a fraction of the LIBRARY, not of the fifty rows shown.

    A count of the rows on screen is a number nobody asked for: with fifty rows on screen and a
    queue holding tens of thousands, a whole library being scanned would read "0 of 45":
    forty-five of the fifty visible rows scanning work, and none of THOSE finished, because the
    table leads with what has not.

    Asked with `limit=1`, so a tally that came from the page could only ever answer one.
    """
    for index in range(5):
        seed(client, f"01HX000000000000000000000{index}", state="done" if index < 3 else "queued")
    sign_in(client, "admin")

    # Named by type: a probe nobody pressed that heads its own row is quiet on the unnamed list.
    body = client.get("/api/jobs", params={"limit": 1, "type": "probe"}).json()

    assert len(body["jobs"]) == 1
    probes = body["by_type"]["probe"]
    assert probes["done"] == 3
    assert probes["queued"] == 2
    # The state chips are this tally with the kinds added together, so the two cannot disagree:
    # they are one read rather than two. Asserted as "at least", because a booted application
    # schedules its own recurring housekeeping and those are real queued jobs.
    assert body["counts"]["done"] >= 3
    assert sum(sum(states.values()) for states in body["by_type"].values()) == sum(
        body["counts"].values()
    )

    # And what is still to COME, counted from the library rather than from the queue. Without it a
    # bar's total climbs while somebody watches, because a pass queues a page at a time, so this
    # asserts the number is there and that it is the library's answer rather than the queue's.
    # Five seeded probes, none of them attached to a real file, so nothing is waiting for one.
    assert body["work"]["probe"]["waiting"] == 0
    assert body["work"]["probe"]["outstanding"] == 2


def test_the_queue_can_be_filtered_and_paged(client: TestClient) -> None:
    for index in range(5):
        seed(client, f"01HX000000000000000000000{index}", state="failed" if index < 3 else "done")
    sign_in(client, "admin")

    assert client.get("/api/jobs", params={"state": "failed"}).json()["total"] == 3
    # By type: a finished probe nobody pressed heads its own row, and so is quiet on the list.
    assert client.get("/api/jobs", params={"state": "done", "type": "probe"}).json()["total"] == 2
    assert len(client.get("/api/jobs", params={"limit": 2}).json()["jobs"]) == 2
    # The total describes the filter, not the page, and it is the same past the end. Otherwise
    # an empty last page would claim the queue held nothing.
    #
    # Scoped to the type these five were seeded as. A booted application schedules its own recurring
    # work, so an unfiltered total here counts whatever this version happens to queue at startup as
    # well, which makes this a test of the boot sequence rather than of paging, and it goes red on
    # a change that has nothing to do with either.
    paged = client.get("/api/jobs", params={"type": "probe", "limit": 2, "offset": 99}).json()
    assert paged["total"] == 5


def test_filtering_by_type_narrows_the_total_as_well_as_the_page(client: TestClient) -> None:
    """The parameter is `type`, and the total has to describe the same filter the page does.

    `job_type` is the queue's own name for the column, not the one this route takes, and FastAPI
    discards a query parameter no route declares, so a test asking for it would filter nothing
    and still pass while only one kind of job was queued.

    Both halves are asserted here because either alone is the bug: a page that filters under a total
    that does not is a list of one under a line saying there are five.
    """
    seed(client, "01HX0000000000000000000001", job_type="thumbnail")
    seed(client, "01HX0000000000000000000002", job_type="preview")
    seed(client, "01HX0000000000000000000003", job_type="preview")
    sign_in(client, "admin")

    previews = client.get("/api/jobs", params={"type": "preview"}).json()
    assert previews["total"] == 2
    assert [job["type"] for job in previews["jobs"]] == ["preview", "preview"]

    assert client.get("/api/jobs", params={"type": "thumbnail"}).json()["total"] == 1
    assert client.get("/api/jobs", params={"type": "nothing_of_that_name"}).json()["total"] == 0


def test_every_kind_on_the_page_comes_with_the_name_the_screen_calls_it(
    client: TestClient,
) -> None:
    """The Type narrowing on Activity offers every kind the queue holds, and a kind with no row on
    the page in hand has no row to read its name off: the page names them all, by the same
    declaration a row's own name comes from."""
    seed(client, "01HX0000000000000000000001", job_type="thumbnail")
    seed(client, "01HX0000000000000000000002", job_type="preview")
    sign_in(client, "admin")

    page = client.get("/api/jobs", params={"type": "preview"}).json()
    assert set(page["names"]) == set(page["by_type"])
    assert page["names"]["thumbnail"] == job_name("thumbnail")
    assert page["names"]["thumbnail"] != "thumbnail", "the kind was named by its internal word"


def test_a_page_bigger_than_the_cap_is_refused(client: TestClient) -> None:
    """The dashboard reads this over a socket every time anything moves. An unbounded limit is a
    way to make the server do unbounded work, once per request."""
    sign_in(client, "admin")
    assert client.get("/api/jobs", params={"limit": 5000}).status_code == 422
    assert client.get("/api/jobs", params={"limit": 0}).status_code == 422
    assert client.get("/api/jobs", params={"offset": -1}).status_code == 422


def test_a_job_carries_why_it_is_not_progressing_and_nothing_else_about_itself(
    client: TestClient,
) -> None:
    """The error is the point of the screen. The payload is not: it is ids that mean nothing here,
    and this is the surface most likely to end up in a screenshot in a bug report."""
    seed(client, "01HX0000000000000000000001")
    sign_in(client, "admin")

    job = row_for(client, "01HX0000000000000000000001")

    assert job["error"] == "it did not work"
    assert "payload" not in job


# --- retry and cancel -------------------------------------------------------------------------


def test_an_admin_can_retry_a_failed_job(client: TestClient) -> None:
    seed(client, "01HX0000000000000000000001", state="failed")
    sign_in(client, "admin")

    assert client.post("/api/jobs/01HX0000000000000000000001/retry").status_code == 204
    assert row_for(client, "01HX0000000000000000000001")["state"] == "queued"


def test_an_admin_can_cancel_a_job_that_has_not_finished(client: TestClient) -> None:
    seed(client, "01HX0000000000000000000001", state="blocked")
    sign_in(client, "admin")

    assert client.post("/api/jobs/01HX0000000000000000000001/cancel").status_code == 204
    assert row_for(client, "01HX0000000000000000000001")["state"] == "canceled"


def test_an_admin_can_retry_everything_that_failed_in_one_go(client: TestClient) -> None:
    """A row at a time is not a workflow when a whole import failed the same way."""
    seed(client, "01HX0000000000000000000001", state="failed")
    seed(client, "01HX0000000000000000000002", state="failed")
    seed(client, "01HX0000000000000000000003", state="canceled")
    sign_in(client, "admin")

    answer = client.post("/api/jobs/retry-failed")

    assert answer.status_code == 200
    assert answer.json() == {"retried": 2}
    # By type: a waiting probe nobody pressed heads its own row, and so is quiet on the list.
    page = client.get("/api/jobs", params={"limit": 100, "type": "probe"}).json()["jobs"]
    states = {job["id"]: job["state"] for job in page}
    assert states["01HX0000000000000000000001"] == "queued"
    assert states["01HX0000000000000000000002"] == "queued"
    # Stopped on purpose, and left that way.
    assert states["01HX0000000000000000000003"] == "canceled"


def test_retrying_the_failures_when_there_are_none_is_a_zero_not_a_refusal(
    client: TestClient,
) -> None:
    sign_in(client, "admin")
    answer = client.post("/api/jobs/retry-failed")
    assert answer.status_code == 200
    assert answer.json() == {"retried": 0}


def test_a_guest_cannot_retry_the_failures(client: TestClient) -> None:
    sign_in(client, "guest")
    assert client.post("/api/jobs/retry-failed").status_code == 403


def test_an_admin_can_stop_the_whole_queue(client: TestClient) -> None:
    """For the queue that got away, where a row at a time is not an answer at all.

    Running work goes with the waiting work, and that is the point rather than a side effect: the
    rows in the queue were put there by a scan that is still walking, so calling off only what is
    waiting would leave the producer running and the queue would refill behind the press.

    What is already over is left alone. A failure is the clear-failed button's business and
    rewriting one as cancelled would lose the fact that it was tried and broke.
    """
    seed(client, "01HX0000000000000000000001", state="queued")
    seed(client, "01HX0000000000000000000002", state="running")
    seed(client, "01HX0000000000000000000003", state="blocked")
    seed(client, "01HX0000000000000000000004", state="failed")
    seed(client, "01HX0000000000000000000005", state="done")
    sign_in(client, "admin")

    answer = client.post("/api/jobs/cancel-all")

    assert answer.status_code == 200
    # At LEAST the three seeded ones, rather than exactly three. A booted application schedules
    # unfinished work of its own (a reconcile, a prune), and that is work this button is meant to
    # stop as well, so the exact number is a fact about what this build schedules at boot and not
    # about what was asked for here. The states below are the claim.
    assert answer.json()["stopped"] >= 3
    # By type: a finished probe nobody pressed heads its own row, and so is quiet on the list.
    page = client.get("/api/jobs", params={"limit": 100, "type": "probe"}).json()["jobs"]
    states = {job["id"]: job["state"] for job in page}
    assert states["01HX0000000000000000000001"] == "canceled"
    assert states["01HX0000000000000000000002"] == "canceled"
    assert states["01HX0000000000000000000003"] == "canceled"
    assert states["01HX0000000000000000000004"] == "failed"
    assert states["01HX0000000000000000000005"] == "done"


def test_stopping_the_queue_when_it_is_empty_is_a_zero_not_a_refusal(client: TestClient) -> None:
    """Pressed twice, because a booted application is never empty on the first press.

    It schedules maintenance work of its own at start-up, so asserting a zero straight away asserts
    something about this build's boot rather than about this route. The first press clears whatever
    that was; the second is the one with genuinely nothing to do.
    """
    sign_in(client, "admin")
    client.post("/api/jobs/cancel-all")

    answer = client.post("/api/jobs/cancel-all")

    assert answer.status_code == 200
    assert answer.json() == {"stopped": 0}


def test_a_guest_cannot_stop_the_queue(client: TestClient) -> None:
    """The queue is what the whole library is doing, and a guest may not stop it."""
    seed(client, "01HX0000000000000000000001", state="queued")
    sign_in(client, "guest")
    assert client.post("/api/jobs/cancel-all").status_code == 403


def test_cancelling_one_pass_records_its_run_as_stopped_by_hand(client: TestClient) -> None:
    """An Identify pass cancelled from Faces or Activity ends its run as stopped, not finished, so
    Faces does not read "Last scan ended" over a scan somebody just cancelled. The cancel tells the
    ledger which kinds of work it stopped, and only their runs end as stopped."""
    from sift.kernel.wiring import LEDGER

    seed(client, "01HX0000000000000000000001", state="running", job_type="identify_file")
    sign_in(client, "admin")
    ledger = getattr(client.app.state, LEDGER.name)  # type: ignore[attr-defined]
    identify = ledger.started("identify_file")
    generate = ledger.started("thumbnail")
    assert identify is not generate

    answer = client.post("/api/jobs/01HX0000000000000000000001/cancel")

    assert answer.status_code == 204
    assert identify.stopped
    assert not generate.stopped


def test_a_job_that_is_not_there_is_a_404_either_way(client: TestClient) -> None:
    sign_in(client, "admin")
    assert client.post("/api/jobs/01HX0000000000000000000404/retry").status_code == 404
    assert client.post("/api/jobs/01HX0000000000000000000404/cancel").status_code == 404


def test_a_write_without_the_csrf_token_is_refused(client: TestClient) -> None:
    """The session alone is not authority to act. A page on another site cannot read the token,
    and cannot produce the header that has to match it."""
    seed(client, "01HX0000000000000000000001", state="failed")
    sign_in(client, "admin")
    del client.headers[CSRF_HEADER_NAME]

    assert client.post("/api/jobs/01HX0000000000000000000001/retry").status_code == 403
    assert client.post("/api/jobs/01HX0000000000000000000001/cancel").status_code == 403
    assert client.post("/api/jobs/retry-failed").status_code == 403


# This screen holds no connection of its own: it is told the queue has moved by the one connection
# the application holds, and asks for a page like every other screen. Who is refused, what a
# cross-site handshake gets, and a user that stops being an admin under an open connection are
# checked where that connection lives.


# --- what a row is about ------------------------------------------------------------------------


def _a_file_to_be_about(client: TestClient, tmp_path: Path) -> str:
    """One real asset, with the root and folder rows a name lookup goes through."""
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    root_path = tmp_path / "library"
    root_path.mkdir(parents=True, exist_ok=True)
    seed_root(db_path, ROOT_ID, folder_id=FOLDER_ID, path=root_path)
    seed_asset(
        db_path,
        ASSET_ID,
        root_id=ROOT_ID,
        folder_id=FOLDER_ID,
        root_path=root_path,
        cache_dir=tmp_path / "cache",
        filename="holiday.mp4",
    )
    return ASSET_ID


def test_a_row_about_a_file_carries_the_file_it_is_about(
    client: TestClient, tmp_path: Path
) -> None:
    """The name AND the id, because the id is what the finished row links to.

    A row that says a name and cannot be opened is a dead end; a row that carries an
    id for a file that has gone is worse, because it is a link to a page that is not there.
    """
    asset_id = _a_file_to_be_about(client, tmp_path)
    seed_job(
        client.app.state.database.path,  # type: ignore[attr-defined]
        "01HX0000000000000000000042",
        state="done",
        job_type="thumbnail",
        payload={"asset_id": asset_id},
    )
    sign_in(client, "admin")

    row = row_for(client, "01HX0000000000000000000042")

    assert row["subject"] == "holiday.mp4"
    assert row["subject_id"] == asset_id


# --- a page of families, and the steps folded under one ------------------------------------------

_DOWNLOAD = "01HX00000000000000000000D1"
_PROBE = "01HX00000000000000000000D2"
_THUMBNAIL = "01HX00000000000000000000D3"
_WATERMARK = "01HX00000000000000000000D4"


def _a_download_with_its_steps(client: TestClient, asset_id: str, *, failed: bool) -> None:
    """A download's real shape: the download, its probing job, and the steps under that."""
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_job(db_path, _DOWNLOAD, state="done", job_type="download", payload={"download_id": "x"})
    about: dict[str, object] = {"asset_id": asset_id}
    seed_job(db_path, _PROBE, state="done", payload=about, parent_id=_DOWNLOAD)
    seed_job(
        db_path, _THUMBNAIL, state="done", job_type="thumbnail", payload=about, parent_id=_PROBE
    )
    seed_job(
        db_path,
        _WATERMARK,
        state="failed" if failed else "blocked",
        job_type="watermark_read",
        payload=about,
        parent_id=_PROBE,
    )


def test_a_folded_page_is_one_row_per_download_with_its_steps_counted(
    client: TestClient, tmp_path: Path
) -> None:
    """The download's own payload names no file; the row is named once, from its steps. A failed
    step makes the whole row Failed, so folding never hides one."""
    _a_download_with_its_steps(client, _a_file_to_be_about(client, tmp_path), failed=True)
    sign_in(client, "admin")

    body = client.get("/api/jobs", params={"fold": True, "type": "download"}).json()

    assert [row["id"] for row in body["jobs"]] == [_DOWNLOAD]
    assert body["total"] == 1
    steps = body["jobs"][0]["steps"]
    assert steps["count"] == 3
    assert steps["by_state"] == {"done": 2, "failed": 1}
    assert steps["state"] == "failed"
    assert (steps["subject"], steps["subject_id"]) == ("holiday.mp4", ASSET_ID)
    assert steps["at_least"] is False
    # And a flat page is unchanged: every row its own, with no family on it.
    flat = client.get("/api/jobs", params={"type": "download"}).json()
    assert flat["jobs"][0]["steps"] is None


def test_a_folded_row_whose_steps_name_no_one_file_names_nothing(client: TestClient) -> None:
    _a_download_with_its_steps(client, "01HX0000000000000000000A99", failed=False)
    sign_in(client, "admin")

    steps = client.get("/api/jobs", params={"fold": True, "type": "download"}).json()["jobs"][0][
        "steps"
    ]

    # The file those steps are about has no row, so there is no name to give, and no link.
    assert (steps["subject"], steps["subject_id"]) == (None, None)
    assert steps["state"] == "blocked"


def test_every_tally_counts_families_and_all_is_the_sum_of_the_states(
    client: TestClient,
) -> None:
    """One universe for every number above Activity's list: a family counts once under All and
    once under the state its row shows, and a step counts nowhere on its own. Seeded with folded
    steps, the case where rows and families disagree: a done download with a failed step (a
    Failed family, three done rows and one failed), a done download whose steps all finished,
    and a failed task with no steps. A state's tab lists exactly the families its number counts;
    the rows the bulk actions act on stay in `counts`."""
    _a_download_with_its_steps(client, "01HX0000000000000000000A99", failed=True)
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    whole = "01HX00000000000000000000E1"
    seed_job(db_path, whole, state="done", job_type="download", payload={"download_id": "y"})
    seed_job(db_path, "01HX00000000000000000000E2", state="done", parent_id=whole)
    seed_job(db_path, "01HX00000000000000000000E3", state="done", parent_id=whole)
    alone = "01HX00000000000000000000E4"
    seed_job(db_path, alone, state="failed", job_type="thumbnail")
    sign_in(client, "admin")

    body = client.get("/api/jobs", params={"fold": True, "limit": 100}).json()
    tallies = body["tallies"]

    states = {state: n for state, n in tallies.items() if state != "all"}
    assert tallies["all"] == sum(states.values()) == body["total"] == len(body["jobs"])
    assert tallies["failed"] == 2, "a family with a failed step is a failed family"
    assert tallies["done"] >= 1
    # The rows are another universe, and they stay where the bulk actions read them.
    assert body["counts"]["done"] >= 5 > tallies["done"]
    listed: list[str] = []
    for state, number in states.items():
        page = client.get("/api/jobs", params={"fold": True, "state": state, "limit": 100}).json()
        assert page["total"] == number == len(page["jobs"]), state
        assert page["tallies"] == tallies, "a state's page narrowed the tallies"
        assert {row["steps"]["state"] for row in page["jobs"]} == {state}, state
        listed += [row["id"] for row in page["jobs"]]
    assert sorted(listed) == sorted(row["id"] for row in body["jobs"]), "not one state each"
    assert {_DOWNLOAD, alone} <= {
        row["id"] for row in body["jobs"] if row["steps"]["state"] == "failed"
    }

    # A kind chosen lists rows of that kind, flat, and its tallies are those rows.
    kind = client.get("/api/jobs", params={"type": "probe", "limit": 100}).json()
    assert kind["tallies"]["all"] == sum(
        n for state, n in kind["tallies"].items() if state != "all"
    )
    assert kind["tallies"] == {**kind["by_type"]["probe"], "all": kind["total"]}

    # A family's own steps are one family's rows, never a fold.
    assert client.get("/api/jobs", params={"fold": True, "parent_id": _PROBE}).status_code == 422


def test_opening_a_folded_row_lists_its_steps_each_named(
    client: TestClient, tmp_path: Path
) -> None:
    _a_download_with_its_steps(client, _a_file_to_be_about(client, tmp_path), failed=True)
    sign_in(client, "admin")

    body = client.get(f"/api/jobs/{_DOWNLOAD}/steps").json()

    assert [row["id"] for row in body["jobs"]] == [_PROBE, _THUMBNAIL, _WATERMARK]
    assert [row["parent_id"] for row in body["jobs"]] == [_DOWNLOAD, _PROBE, _PROBE]
    assert {row["subject"] for row in body["jobs"]} == {"holiday.mp4"}
    assert (body["total"], body["at_least"]) == (3, False)


def test_only_a_row_that_heads_a_family_has_steps_to_open(client: TestClient) -> None:
    """A step's steps are its top's; a job that is not there has none. One 404 for both."""
    _a_download_with_its_steps(client, ASSET_ID, failed=False)
    sign_in(client, "admin")

    assert client.get(f"/api/jobs/{_PROBE}/steps").status_code == 404
    assert client.get("/api/jobs/01HX0000000000000000000404/steps").status_code == 404


def test_a_row_whose_file_has_gone_keeps_the_work_and_drops_the_link(
    client: TestClient, tmp_path: Path
) -> None:
    """The guard, made visible: the id in the payload resolves to nothing.

    Written against a payload that DOES name a file, so the two cases differ only in whether the
    file is still there: a job with no asset in its payload at all would pass whether the guard
    existed or not.
    """
    seed_job(
        client.app.state.database.path,  # type: ignore[attr-defined]
        "01HX0000000000000000000043",
        state="done",
        job_type="thumbnail",
        payload={"asset_id": "01HX0000000000000000000099"},
    )
    sign_in(client, "admin")

    row = row_for(client, "01HX0000000000000000000043")

    assert row["subject"] is None
    assert row["subject_id"] is None


def test_a_row_about_no_one_thing_has_neither(client: TestClient) -> None:
    """Whole-library work. The ordinary case, and the one the two above are told apart from."""
    seed(client, "01HX0000000000000000000044", state="done", job_type="face_sweep")
    sign_in(client, "admin")

    row = row_for(client, "01HX0000000000000000000044")

    assert row["subject"] is None
    assert row["subject_id"] is None


def test_a_row_about_a_whole_folder_is_named_by_the_folder(
    client: TestClient, tmp_path: Path
) -> None:
    """The other kind of subject, and it goes through a different lookup.

    A scan is about a folder rather than a file. Worth telling apart from the file case above
    because the two are resolved by separate queries, and only one of them can produce a link.
    """
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    root_path = tmp_path / "library"
    root_path.mkdir(parents=True, exist_ok=True)
    seed_root(db_path, ROOT_ID, folder_id=FOLDER_ID, path=root_path, name="Holidays")
    seed_job(
        db_path,
        "01HX0000000000000000000045",
        state="done",
        job_type="scan",
        payload={"root_id": ROOT_ID},
    )
    sign_in(client, "admin")

    row = row_for(client, "01HX0000000000000000000045")

    assert row["subject"] == "Holidays"
    # A folder is not a file, so there is nothing to open even though the name resolved.
    assert row["subject_id"] is None


async def test_nothing_is_named_when_there_is_no_database_to_ask() -> None:
    """The guard that keeps the queue drawable on an application assembled without one.

    Nothing in the running app reaches it, which is exactly why it is worth pinning: it is what
    stops a route that merely wants names from being the thing that refuses to answer at all.
    """
    from sift.kernel.access import Role, Viewer
    from sift.slices.media_jobs.router import _Shown, _subjects

    assert await _subjects(None, [], _Shown(None, Viewer(id="nobody", role=Role.ADMIN))) == {}


def test_a_file_the_vault_holds_back_is_not_named(client: TestClient, tmp_path: Path) -> None:
    """The vault conceals a file from its own admin too, and a task row is no way round it: with
    the vault shut the row says what the work was and nothing about which file."""
    asset_id = _a_file_to_be_about(client, tmp_path)
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_job(
        db_path,
        "01HX0000000000000000000046",
        state="done",
        job_type="thumbnail",
        payload={"asset_id": asset_id},
    )
    user_id = sign_in(client, "admin")
    write_rows(
        db_path,
        [
            (
                "INSERT OR REPLACE INTO viewer_assets (user_id, asset_id, concealed) VALUES (?, ?, 1)",
                (user_id, asset_id),
            )
        ],
    )

    row = row_for(client, "01HX0000000000000000000046")

    assert (row["subject"], row["subject_id"]) == (None, None)


# --- a client that goes away while the stream is pushing to it ------------------------------------
#
# Driven against the handler directly rather than through a test client, and that is deliberate.
# What these pin is what the loop does when the SEND fails, and a real client cannot be made to
# vanish at a chosen instant: asking for the race through a socket is how a test becomes one that
# hangs rather than one that fails. The stub answers exactly what the handler asks of a socket and
# nothing else, so a handler that starts asking something new fails here rather than passing.


# --- making every picture again -----------------------------------------------------------------
#
# A thumbnail is made once, when a file arrives, and nothing ever revisits it. So a library imported
# before a sizing was fixed keeps those pictures for ever, and a rescan does not help: a scan skips
# any file whose path, size and mtime are unchanged, which is what makes a rescan quick.

REBUILD = "/api/jobs/rebuild-thumbnails"

#: Read from the module that registers it. A job type spelled by hand here would filter for nothing,
#: and every assertion below is about a count coming back empty or not.
THUMBNAIL = media_jobs_jobs.THUMBNAIL


def _probed(client: TestClient, *asset_ids: str) -> None:
    """Files a picture could be made of: a row with a probe recorded against it."""
    write_rows(
        client.app.state.database.path,  # type: ignore[attr-defined]
        [
            (
                "INSERT INTO assets (id, identity, media_type, added_at, probed_at)"
                " VALUES (?, ?, 'video', ?, 1)",
                (asset_id, f"digest-{asset_id}", position),
            )
            for position, asset_id in enumerate(asset_ids)
        ],
    )


def test_asking_how_big_a_rebuild_would_be_queues_nothing(client: TestClient) -> None:
    """Separate from the run for the same reason the tidy survey is separate from the tidying: it
    is minutes of the machine on a large library, and a control whose size is only visible after it
    has started is one nobody can use carefully."""
    sign_in(client, "admin")
    _probed(client, "01HX0000000000000000000T01", "01HX0000000000000000000T02")

    body = client.get(REBUILD).json()

    assert body["total"] == 2
    assert body["queued"] == 0, "counting is not starting"
    waiting = client.get("/api/jobs", params={"type": THUMBNAIL, "limit": 100}).json()
    assert waiting["total"] == 0


def test_a_file_nothing_has_probed_is_not_counted(client: TestClient) -> None:
    """A picture is made from what probing found. A row nothing has read yet has nothing to make
    one from, and counting it would put a number on the screen the run cannot reach."""
    sign_in(client, "admin")
    write_rows(
        client.app.state.database.path,  # type: ignore[attr-defined]
        [
            (
                "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
                ("01HX0000000000000000000T09", "digest-unprobed"),
            )
        ],
    )

    assert client.get(REBUILD).json()["total"] == 0


def test_a_rebuild_queues_one_sweep_over_the_library(client: TestClient) -> None:
    """One paged sweep, not one job per file written inside the request, where a 100,000-file
    library would be 100,000 enqueues before the request answered. The sweep pages and can be
    cancelled like any other job; the count the press answers with is the files it will reach."""
    sign_in(client, "admin")
    _probed(client, "01HX0000000000000000000T01", "01HX0000000000000000000T02")

    body = client.post(REBUILD).json()

    assert body["total"] == 2 and body["queued"] == 1
    waiting = client.get("/api/jobs", params={"type": REBUILD_THUMBNAILS, "limit": 100}).json()
    assert waiting["total"] == 1


def test_pressing_it_twice_queues_nothing_the_second_time(client: TestClient) -> None:
    """`dedupe` is what makes it harmless. A file that already has a thumbnail job waiting gets no
    second one, so an impatient second press does not double the work."""
    sign_in(client, "admin")
    _probed(client, "01HX0000000000000000000T01")
    client.post(REBUILD)

    again = client.post(REBUILD).json()

    assert again["total"] == 1, "the file is still one a picture could be made of"
    waiting = client.get("/api/jobs", params={"type": REBUILD_THUMBNAILS, "limit": 100}).json()
    assert waiting["total"] == 1, "and the sweep is queued once, not twice"


def test_a_rebuild_of_an_empty_library_is_a_zero_rather_than_a_refusal(client: TestClient) -> None:
    sign_in(client, "admin")

    body = client.post(REBUILD).json()

    assert body == {"total": 0, "queued": 0}


def test_a_guest_can_neither_count_nor_start_a_rebuild(client: TestClient) -> None:
    """The count describes the size of the whole library and the run is the largest amount of work
    any route here can ask for."""
    sign_in(client, "guest")

    assert client.get(REBUILD).status_code == 403
    assert client.post(REBUILD).status_code == 403


def test_a_rebuild_needs_the_csrf_header(client: TestClient) -> None:
    sign_in(client, "admin")
    del client.headers[CSRF_HEADER_NAME]

    assert client.post(REBUILD).status_code == 403


# --- bringing the hover clips up to the shape that is set ---------------------------------------
#
# Its own control, not a corner of the one above. A clip is built once, when a file arrives, and
# nothing revisits it, so changing how long a preview runs reaches the files imported afterwards
# and leaves the rest of the library alone for ever. A rescan cannot do it either.

RESTYLE = "/api/jobs/rebuild-previews"

REBUILD_PREVIEWS = media_jobs_jobs.REBUILD_PREVIEWS


def _clip(client: TestClient, asset_id: str, shape: str) -> None:
    """A file with a hover clip recorded against it, built to the named shape."""
    _probed(client, asset_id)
    params = json.dumps(
        media_jobs_jobs.preview_recipe(sampling.preview_shape(shape)),
        sort_keys=True,
        separators=(",", ":"),
    )
    write_rows(
        client.app.state.database.path,  # type: ignore[attr-defined]
        [
            (
                "INSERT INTO derivatives"
                " (id, asset_id, kind, rel_cache_path, params, size_bytes, created_at)"
                " VALUES (?, ?, 'preview', ?, ?, 1000, 1)",
                (f"d-{asset_id}", asset_id, f"aa/bb/{asset_id}/preview.mp4", params),
            )
        ],
    )


def test_a_clip_of_another_shape_is_counted_without_queuing_anything(client: TestClient) -> None:
    """Counting is not starting, for the reason the thumbnail survey above gives."""
    sign_in(client, "admin")
    _clip(client, "01HX0000000000000000000P01", "brief")

    body = client.get(RESTYLE).json()

    assert body["total"] == 1
    assert body["queued"] == 0
    waiting = client.get("/api/jobs", params={"type": REBUILD_PREVIEWS, "limit": 100}).json()
    assert waiting["total"] == 0


def test_a_library_already_the_shape_that_is_set_counts_nothing(client: TestClient) -> None:
    """Zero is the ordinary answer and is what lets the screen say the library is up to date
    rather than offering a button whose effect would be nothing."""
    sign_in(client, "admin")
    _clip(client, "01HX0000000000000000000P02", sampling.DEFAULT_PREVIEW_SHAPE)

    assert client.get(RESTYLE).json()["total"] == 0


def test_a_file_with_no_clip_at_all_is_not_counted(client: TestClient) -> None:
    """A still, a video whose job has not run, or one previews are switched off for. Three ordinary
    states, none of which a rebuild should quietly start work on: filling those in is a scan's
    job, and this one replaces what exists."""
    sign_in(client, "admin")
    _probed(client, "01HX0000000000000000000P03")

    assert client.get(RESTYLE).json()["total"] == 0


def test_a_restyle_queues_one_sweep_rather_than_one_job_per_file(client: TestClient) -> None:
    """The opposite of the thumbnail rebuild beside it, and deliberate: working out WHICH clips are
    out of date is a query that belongs with the job, and running it inside a request would hold
    the connection open while a large library is read."""
    sign_in(client, "admin")
    _clip(client, "01HX0000000000000000000P04", "brief")
    _clip(client, "01HX0000000000000000000P05", "brief")

    body = client.post(RESTYLE).json()

    assert body["total"] == 2, "it says how many files it is about"
    assert body["queued"] == 1, "and queues one sweep to find them"
    waiting = client.get("/api/jobs", params={"type": REBUILD_PREVIEWS, "limit": 100}).json()
    assert waiting["total"] == 1


def test_a_restyle_with_nothing_to_do_queues_nothing(client: TestClient) -> None:
    """A sweep queued over an up-to-date library is a row on the dashboard that finds nothing."""
    sign_in(client, "admin")
    _clip(client, "01HX0000000000000000000P06", sampling.DEFAULT_PREVIEW_SHAPE)

    assert client.post(RESTYLE).json() == {"total": 0, "queued": 0}
    waiting = client.get("/api/jobs", params={"type": REBUILD_PREVIEWS, "limit": 100}).json()
    assert waiting["total"] == 0


def test_a_guest_can_neither_count_nor_start_a_restyle(client: TestClient) -> None:
    """It re-encodes every hover clip in the library, which is the second largest amount of work
    any route here can ask for."""
    sign_in(client, "guest")

    assert client.get(RESTYLE).status_code == 403
    assert client.post(RESTYLE).status_code == 403


def test_a_restyle_needs_the_csrf_header(client: TestClient) -> None:
    sign_in(client, "admin")
    del client.headers[CSRF_HEADER_NAME]

    assert client.post(RESTYLE).status_code == 403


def test_broken_jobs_can_be_thrown_away_and_only_those(client: TestClient) -> None:
    """The other clear.

    A failure is worth keeping while it can still be retried and worth nothing once the reason it
    failed is gone: a card that was not installed at the time, a drive that has gone, settings
    nobody uses now. Without it the only way to shift them would be to retry every one and watch
    it fail a second time.

    Cancelled and finished work is left alone: one is somebody's decision and the other ages out by
    itself. Both are asserted, because a clear that took everything would pass a test that only
    counted what went.
    """
    seed(client, "01HX0000000000000000000041", state="failed")
    seed(client, "01HX0000000000000000000042", state="failed")
    seed(client, "01HX0000000000000000000043", state="canceled")
    seed(client, "01HX0000000000000000000044", state="done")
    sign_in(client, "admin")

    assert client.post("/api/jobs/clear-failed").json()["cleared"] == 2

    probes = client.get("/api/jobs", params={"limit": 100}).json()["by_type"]["probe"]
    assert probes.get("failed", 0) == 0
    assert probes["canceled"] == 1
    assert probes["done"] == 1

    # Nothing to clear is a success with a zero, the same as retrying nothing.
    assert client.post("/api/jobs/clear-failed").json()["cleared"] == 0


@pytest.mark.integration
async def test_a_pass_that_has_just_finished_is_not_left_reading_not_started(
    temp_db: Database,
) -> None:
    """The route tells the library count what the queue has finished and what is still
    outstanding before it reads it.

    Both halves of a pass's row are held for a few seconds (the queue's tally and the library's
    count of what is left) on clocks of their own. Untold, at the end of a run the tally would
    empty first, the count would still hold the files the run had just finished, and Activity
    would draw the finished pass as "Not started, under a minute" until the count caught up.
    Built with a count that never goes
    stale on its own, so the only thing that can bring the new answer is the run ending.
    """
    from sift.kernel.access import Role, Viewer
    from sift.kernel.jobs import JobContext, JobQueue, register_handler
    from sift.kernel.jobs.work_ahead import WorkAhead
    from sift.slices.media_jobs.router import _page, _Shown

    async def nothing(_context: JobContext) -> None:
        return None

    register_handler("ending_kind", nothing, name="A kind that ends")
    await temp_db.initialize_schema()
    queue = JobQueue(temp_db, summary_fresh_for=0)
    ahead = WorkAhead(fresh_for=3600.0)
    lacking = 3

    async def left() -> int:
        return lacking

    ahead.register("ending_kind", left)
    job_id = await queue.enqueue("ending_kind")
    assert job_id is not None
    viewer = Viewer(id="nobody", role=Role.ADMIN)

    async def waiting() -> int | None:
        page = await _page(
            queue,
            ahead,
            None,
            state=None,
            job_type=None,
            parent_id=None,
            limit=10,
            offset=0,
            shown=_Shown(None, viewer),
        )
        return page.work["ending_kind"].waiting

    assert await waiting() == 3
    lacking = 0
    await queue.cancel(job_id)

    assert await waiting() == 0, "a run that had ended was drawn from the count taken before it"


async def test_the_families_are_drawn_without_a_ledger_to_estimate_from() -> None:
    """A route built without the ledger still draws every long pass; what it cannot say is the
    time left, and it says nothing rather than inventing it."""
    from sift.kernel.jobs.switchboard import Switchboard
    from sift.slices.media_jobs.router import _families

    families = await _families({}, None, Switchboard())

    assert families, "the long passes are drawn from the registry, not from the ledger"
    # An empty board allows everything, which is what having nothing declared means.
    assert all(one.on and one.ready and one.problem is None for one in families.values())


async def test_a_family_says_it_is_switched_off_and_why_it_cannot_run() -> None:
    """Both answers are the server's, per family.

    Which handler is registered under which family is known only here, so a screen working it out
    from its own map would read "no estimate" for a family added later and could not describe a
    pass somebody had switched off.
    """
    from sift.kernel.jobs import Readiness, Switch, Switchboard, register_handler
    from sift.kernel.jobs.families import Family
    from sift.slices.media_jobs.router import _families

    async def nothing(_context: object) -> None:
        return None

    register_handler("walking", nothing, name="Walking", family=Family.SCAN)
    register_handler("looking", nothing, name="Looking", family=Family.IDENTIFY)

    async def off() -> bool:
        return False

    async def cannot() -> Readiness:
        return Readiness(ready=False, problem="The graphics card runtime is not installed.")

    board = Switchboard()
    board.declare(Switch(key="importing.scan", refusal="Scanning is off.", on=off), "walking")
    board.declare_ready(Family.IDENTIFY, cannot)

    families = await _families({}, None, board)

    assert families["scan"].on is False
    assert families["scan"].ready is True, "a switch is not a missing runtime"
    assert families["identify"].on is True, "a family with no switch reads as on"
    assert families["identify"].ready is False
    assert families["identify"].problem == "The graphics card runtime is not installed."


async def test_the_reason_a_pass_is_not_running_is_the_servers_to_say(
    client: TestClient,
) -> None:
    """Only the server can tell "held for the night" from "idle": the night window is the pool's
    per-type limits going to nought, which no browser can see."""
    from sift.kernel.jobs import registered_families, registered_product_carriers
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.switchboard import Switchboard
    from sift.slices.media_jobs.router import (
        NOTHING_WAITING,
        WAITING_FOR_WINDOW,
        KindOfWork,
        _families,
    )

    types = registered_families()
    carriers = registered_product_carriers()
    scans = sorted(
        one for one, family in types.items() if family is Family.IDENTIFY and one not in carriers
    )
    assert scans, "the identify family has no registered types to hold"

    # Every Identify type at nothing, the coordinators included: a press's own cap is what would
    # let the family occupy a worker while its window holds the rest.
    everything = [one for one, family in types.items() if family is Family.IDENTIFY]
    held = WorkerPool(queue=None, concurrency=12, limits=dict.fromkeys(everything, 0))  # type: ignore[arg-type]
    work = {
        one: KindOfWork(done=1, outstanding=0, failed=0, waiting=99, total=100) for one in scans
    }

    waiting = await _families(work, None, Switchboard(), held)
    assert waiting["identify"].reason == WAITING_FOR_WINDOW
    assert waiting["identify"].at_once == 1, "every type capped at nothing can occupy nobody"

    # The window holds the family's OWN types; a coordinator left free is a press's entitlement
    # and does not make a held family read as free.
    assert sorted(set(everything) - set(scans)), (
        "the identify family has no coordinator to leave free"
    )
    own_only = WorkerPool(queue=None, concurrency=12, limits=dict.fromkeys(scans, 0))  # type: ignore[arg-type]
    still = await _families(work, None, Switchboard(), own_only)
    assert still["identify"].reason == WAITING_FOR_WINDOW

    free = WorkerPool(queue=None, concurrency=12, limits={})  # type: ignore[arg-type]
    running = await _families(work, None, Switchboard(), free)
    assert running["identify"].reason is None, "work to do and nobody holding it is not a reason"
    assert running["identify"].at_once == 12

    empty = {
        one: KindOfWork(done=100, outstanding=0, failed=0, waiting=0, total=100) for one in scans
    }
    finished = await _families(empty, None, Switchboard(), free)
    assert finished["identify"].reason == NOTHING_WAITING
    assert (finished["identify"].done, finished["identify"].total) == (
        100 * len(scans),
        100 * len(scans),
    )


async def test_the_housekeeping_names_the_work_that_is_not_a_pass(client: TestClient) -> None:
    """Every one of these is in the `other` family, which is not drawn as a pass, so without the
    housekeeping rows they would have nowhere to appear. Every named type is a type a handler
    claims."""
    from sift.kernel.jobs import registered_handlers
    from sift.kernel.jobs.families import HOUSEKEEPING

    claimed = registered_handlers()
    for chore in HOUSEKEEPING:
        assert chore.job_type in claimed, (
            f"{chore.job_type} is named on the screen and run by nobody"
        )
        assert chore.label and not chore.label.endswith("."), chore.label


def test_every_pass_and_chore_names_a_task_that_exists(client: TestClient) -> None:
    """Activity links each bar to its task's row on Tasks by the id the jobs page sends.

    A chore or a family naming a task nobody declared is a link that opens Tasks and rings nothing
    (silence where the row should be), so every name is held to the task registry here, and the
    page is read once to prove the names travel on the wire.
    """
    from sift.kernel.jobs.families import HOUSEKEEPING
    from sift.kernel.jobs.schedules import registered_schedules
    from sift.slices.media_jobs.router import FAMILY_TASKS

    declared = set(registered_schedules())
    named = [chore.task for chore in HOUSEKEEPING if chore.task] + list(FAMILY_TASKS.values())
    assert named, "nothing names a task, so this proves nothing"
    assert not sorted(set(named) - declared), sorted(set(named) - declared)

    sign_in(client, "admin")
    page = client.get("/api/jobs").json()
    chores = {one["job_type"]: one.get("task") for one in page.get("housekeeping", [])}
    assert chores.get("dedup_scan") == "duplicates"
    assert page["families"]["scan"]["task"] == "scan"


def test_a_failed_scan_says_so_on_the_scan_row_in_plain_words(
    client: TestClient, tmp_path: Path
) -> None:
    """Only while its folder is still a library folder: no later scan can make the rest good."""
    from sift.kernel.jobs.failure_words import in_plain_words

    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_root(db_path, ROOT_ID, folder_id=FOLDER_ID, path=tmp_path)
    error = "The folder stopped answering partway through the scan. Scan it again once it's back."
    for job_id, root_id in (
        ("01HX0000000000000000000077", ROOT_ID),
        ("01HX0000000000000000000079", "gone"),
    ):
        seed_job(db_path, job_id, job_type="scan", error=error, payload={"root_id": root_id})
    seed(client, "01HX0000000000000000000078")
    sign_in(client, "admin")
    scan = client.get("/api/jobs").json()["families"]["scan"]
    assert (scan["failed"], scan["last_error"]) == (1, in_plain_words(error))


def test_the_scan_row_counts_what_its_walk_has_still_to_read(client: TestClient) -> None:
    """A quarter of 40 counted files read and none taken in yet: before, "0 of 0"."""
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    walk = "01HX0000000000000000000079"
    seed_job(db_path, walk, state="running", job_type="scan", payload={"root_id": "r"})
    to_read = json.dumps({"video": 10, "image": 30})
    write_rows(
        db_path,
        [
            (
                "UPDATE jobs SET units = 40, progress = 0.25, to_read = ? WHERE id = ?",
                (to_read, walk),
            )
        ],
    )
    sign_in(client, "admin")

    scan = client.get("/api/jobs").json()["families"]["scan"]

    assert (scan["done"], scan["total"], scan["waiting"]) == (0, 30, 30)


async def test_a_build_narrowed_to_one_product_is_that_products_familys_work() -> None:
    """A task typed by its coordinator counts under the family of what it MAKES.

    A Smart Search run presses the Build filtered to meaning, whose tasks are Identify's type; the
    screen must say Smart Search is running, not Identify.
    """
    from sift.kernel.jobs import register_handler
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.switchboard import Switchboard
    from sift.slices.media_jobs.router import KindOfWork, _families

    async def nothing(_context: object) -> None:
        return None

    register_handler(
        "building", nothing, name="Building", family=Family.IDENTIFY, carries_products=True
    )

    from sift.kernel.jobs import JobState
    from sift.kernel.jobs.queue import LiveProducts

    class Queue:
        async def live_products(self, job_types: list[str]) -> list[LiveProducts]:
            assert "building" in job_types
            return [LiveProducts("building", JobState.QUEUED, False, ("meaning",), 1)]

    work = {"building": KindOfWork(done=0, outstanding=1, failed=0, left_units=1.0)}
    families = await _families(work, None, Switchboard(), None, Queue())  # type: ignore[arg-type]

    assert families["semantic"].reason is None, "the meaning task is Smart Search's work, running"
    assert families["identify"].reason == "Nothing waiting", "and none of it is Identify's"
    assert (families["semantic"].outstanding, families["identify"].outstanding) == (1, 0)
    assert families["semantic"].waiting == 1


async def test_a_queued_runs_units_never_join_a_familys_denominator() -> None:
    """A kind nothing can count a library total for adds nothing to done or total.

    A Build pressed over 80,000 files, its queued units added to the total, would make Identify
    read "9,000 of 180,000" on a library of 100,000. The bar is defined at rest from the library's
    own counts and stays that way while work is in flight.
    """
    from sift.kernel.jobs import register_handler
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.switchboard import Switchboard
    from sift.slices.media_jobs.router import KindOfWork, _families

    async def nothing(_context: object) -> None:
        return None

    register_handler("looking", nothing, name="Looking", family=Family.IDENTIFY)
    register_handler("running_over", nothing, name="Running over", family=Family.IDENTIFY)
    work = {
        "looking": KindOfWork(done=9000, outstanding=0, failed=0, waiting=91000, total=100000),
        "running_over": KindOfWork(done=0, outstanding=1, failed=0, left_units=80000.0),
    }
    families = await _families(work, None, Switchboard())

    assert (families["identify"].done, families["identify"].total) == (9000, 100000)


async def test_a_family_is_priced_by_the_media_kinds_its_work_is_waiting_on() -> None:
    """The estimate is asked with the mix of kinds a family's work waits on, or with none."""
    from sift.kernel.jobs import register_handler
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.switchboard import Switchboard
    from sift.slices.media_jobs.router import KindOfWork, _families

    async def nothing(_context: object) -> None:
        return None

    register_handler("picturing", nothing, name="Picturing", family=Family.GENERATE)
    register_handler("stripping", nothing, name="Stripping", family=Family.GENERATE)
    asked: dict[str, object] = {}

    class Book:
        async def estimate(self, family: Family, *_args: object, **named: object) -> None:
            asked[family.value] = named.get("kinds")

    work = {
        "picturing": KindOfWork(done=0, outstanding=0, failed=0, waiting=2, total=2),
        "stripping": KindOfWork(done=0, outstanding=0, failed=0, waiting=2, total=2),
    }
    kinds = {"picturing": {"video": 2}, "stripping": {"video": 1, "image": 1}}
    await _families(work, Book(), Switchboard(), kinds=kinds)  # type: ignore[arg-type]

    assert asked["generate"] == {"video": 3.0, "image": 1.0}
    assert asked["identify"] is None


async def test_a_time_priced_from_the_benchmark_is_sent_as_the_least_it_takes() -> None:
    """Only the benchmark's floor is sent as one; a time priced from runs is a window."""
    from sift.kernel.jobs import register_handler
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.ledger import Estimate
    from sift.kernel.jobs.switchboard import Switchboard
    from sift.slices.media_jobs.router import KindOfWork, _families

    async def nothing(_context: object) -> None:
        return None

    register_handler("picturing", nothing, name="Picturing", family=Family.GENERATE)
    register_handler("facing", nothing, name="Facing", family=Family.IDENTIFY)

    class Book:
        async def estimate(self, family: Family, *_args: object, **_named: object) -> Estimate:
            floor = family is Family.IDENTIFY
            return Estimate(7200, 7200 if floor else 9000, 0 if floor else 40, 4, floor=floor)

    work = {
        "picturing": KindOfWork(done=0, outstanding=0, failed=0, waiting=2, total=2),
        "facing": KindOfWork(done=0, outstanding=0, failed=0, waiting=2, total=2),
    }
    families = await _families(work, Book(), Switchboard())  # type: ignore[arg-type]

    assert families["identify"].at_least
    assert not families["generate"].at_least


async def test_a_pass_held_by_the_window_says_when_the_window_opens(client: TestClient) -> None:
    """ "Waiting for tonight's window." alone cannot tell ten minutes from ten hours. The hour is a
    setting the feature owns, handed to the switchboard by the composition root; a family that
    declared it names it, and one whose read fails says what it can without inventing an hour."""
    from sift.kernel.jobs import registered_families, registered_product_carriers
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.switchboard import Switchboard
    from sift.slices.media_jobs.router import WAITING_FOR_WINDOW, KindOfWork, _families

    types = registered_families()
    carriers = registered_product_carriers()
    scans = sorted(
        one for one, family in types.items() if family is Family.IDENTIFY and one not in carriers
    )
    assert scans, "the identify family has no registered types to hold"
    everything = [one for one, family in types.items() if family is Family.IDENTIFY]
    held = WorkerPool(queue=None, concurrency=12, limits=dict.fromkeys(everything, 0))  # type: ignore[arg-type]
    work = {
        one: KindOfWork(done=1, outstanding=0, failed=0, waiting=99, total=100) for one in scans
    }

    async def at_ten() -> str:
        return "22:00"

    board = Switchboard()
    board.declare_window(Family.IDENTIFY, at_ten)
    waiting = await _families(work, None, board, held)
    assert waiting["identify"].reason == "Waiting for tonight's window, which opens at 22:00."

    async def unreadable() -> str:
        raise RuntimeError("the settings store is closed")

    broken = Switchboard()
    broken.declare_window(Family.IDENTIFY, unreadable)
    assert (await _families(work, None, broken, held))["identify"].reason == WAITING_FOR_WINDOW

    # And a family that is not held is not asked: the hour answers "held until when" only.
    free = WorkerPool(queue=None, concurrency=12, limits={})  # type: ignore[arg-type]
    assert (await _families(work, None, board, free))["identify"].reason is None


async def test_a_family_of_two_kinds_says_each_ones_figure_in_its_own_words(
    client: TestClient,
) -> None:
    """Identify is the faces pass AND the watermark read, and "9,000 of 200,000" over both is the
    library counted twice and a figure about neither. Each kind with a count of its own travels
    as a part, captioned in the words its handler declared."""
    from sift.kernel.jobs.switchboard import Switchboard
    from sift.slices.media_jobs.router import KindOfWork, _families

    # By the names the queue knows them by: a slice's tests do not import another slice.
    work = {
        "face_scan": KindOfWork(done=9000, outstanding=0, failed=0, waiting=91000, total=100000),
        "watermark_read": KindOfWork(done=0, outstanding=0, failed=0, waiting=100000, total=100000),
    }

    identify = (await _families(work, None, Switchboard()))["identify"]

    assert identify.total == 200000, "the sum is still sent, for the bar a one-part family draws"
    assert [(part.caption, part.done, part.total) for part in identify.parts] == [
        ("files looked at for faces", 9000, 100000),
        ("files read for watermarks", 0, 100000),
    ]


async def test_a_pass_whose_every_job_waits_for_quiet_hours_says_so_rather_than_running() -> None:
    """Generate set to "In quiet hours", a file arrives in the afternoon: its pictures wait, and
    Activity says so rather than "Running". Waiting is said only while it is true: every job held,
    none running, the range shut: a press among them, one already running or the range open is a
    pass that is working.
    """
    from sift.kernel.jobs import register_handler
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.switchboard import QuietHold, Switchboard
    from sift.slices.media_jobs.router import WAITING_FOR_QUIET_HOURS, KindOfWork, _families

    async def nothing(_context: object) -> None:
        return None

    register_handler("drawing_quietly", nothing, name="Drawing", family=Family.GENERATE)

    class Queue:
        def __init__(self, held: int) -> None:
            self.held = held

        async def live_products(self, job_types: list[str]) -> list[object]:
            return []

        async def held_by_type(self, types: frozenset[str]) -> dict[str, int]:
            return {"drawing_quietly": self.held} if "drawing_quietly" in types else {}

    def board(is_open: bool) -> Switchboard:
        answer = QuietHold(open=is_open, types=frozenset({"drawing_quietly"}))

        async def ask() -> QuietHold:
            return answer

        made = Switchboard()
        made.declare_quiet_hours(ask)
        return made

    work = {"drawing_quietly": KindOfWork(done=0, outstanding=3, failed=0, left_units=3.0)}
    shut = board(False)

    waiting = await _families(work, None, shut, None, Queue(3))  # type: ignore[arg-type]
    assert waiting["generate"].reason == WAITING_FOR_QUIET_HOURS

    one_running = {"drawing_quietly": {"running": 1}}
    running = await _families(work, None, shut, None, Queue(3), one_running)  # type: ignore[arg-type]
    assert running["generate"].reason is None, "the job already running finishes"
    pressed = await _families(work, None, shut, None, Queue(2))  # type: ignore[arg-type]
    assert pressed["generate"].reason is None, "a press among them runs now"
    opened = await _families(work, None, board(True), None, Queue(3))  # type: ignore[arg-type]
    assert opened["generate"].reason is None, "and in quiet hours it runs"


async def test_a_chore_whose_every_job_waits_for_quiet_hours_says_so_as_a_pass_does() -> None:
    """Find duplicate files set to "In quiet hours", a file arrives in the afternoon: the sweep it
    queues waits for the range, and Activity's housekeeping row says "Waiting for quiet hours", as
    a Generate row held the same way does. The chore says the pass's sentence by the pass's rule:
    every row held, none running, and nothing held at all while the range is on (the read answers
    nothing then).
    """
    from sift.kernel.jobs import WorkKind, WorkSummary
    from sift.slices.media_jobs.router import WAITING_FOR_QUIET_HOURS, _housekeeping

    class Queue:
        async def last_finished_runs(self, types: list[str]) -> dict[str, object]:
            return {}

    def sweep(running: int = 0) -> WorkSummary:
        states = {"dedup_scan": {"queued": 1 - running, "running": running}}
        return WorkSummary(states=states, run={"dedup_scan": WorkKind(outstanding=1)}, since=0)

    def reason_of(chores: Sequence[object]) -> object:
        return next(one for one in chores if one.job_type == "dedup_scan").reason  # type: ignore[attr-defined]

    held = await _housekeeping(Queue(), sweep(), None, None, {"dedup_scan": 1})  # type: ignore[arg-type]
    assert reason_of(held) == WAITING_FOR_QUIET_HOURS
    going = await _housekeeping(Queue(), sweep(running=1), None, None, {"dedup_scan": 1})  # type: ignore[arg-type]
    assert reason_of(going) is None, "a sweep already running finishes"
    pressed = await _housekeeping(Queue(), sweep(), None, None, {})  # type: ignore[arg-type]
    assert reason_of(pressed) is None, "a pressed sweep, or one in quiet hours, runs now"


async def test_a_chore_whose_last_run_failed_says_why_in_plain_words() -> None:
    """ "Yesterday, failed" says nothing of what failed, and the tool's own text (a memory address,
    a decoder's name) says nothing anybody could act on. The chore carries the failure's kind in
    plain words and which job it was; the job's row keeps the tool's text."""
    from sift.kernel.jobs import JobState, WorkSummary
    from sift.kernel.jobs.failure_words import KINDS
    from sift.kernel.jobs.queue import TaskRun
    from sift.slices.media_jobs.router import _housekeeping

    failed = TaskRun(
        id="job-1",
        started_at=100,
        finished_at=102,
        state=JobState.FAILED,
        note=None,
        runs_total=1,
        error="FFmpegError: the picture has no frames\n[webp] image data not found\n",
    )

    class Queue:
        async def last_finished_runs(self, types: list[str]) -> dict[str, object]:
            return dict.fromkeys(types, failed)

    chores = await _housekeeping(Queue(), WorkSummary(states={}, run={}, since=0), None, None)  # type: ignore[arg-type]
    one = next(chore for chore in chores if chore.job_type == "transcode")
    assert one.last_state == "failed"
    unreadable = next(kind for kind in KINDS if kind.name == "unreadable")
    assert one.last_error == unreadable.words
    assert "webp" not in one.last_error
    assert one.last_job == "job-1"


async def test_a_chore_with_work_left_is_priced_from_the_ledger_as_a_pass_is() -> None:
    """The housekeeping row wears the same two bounds a pass's row does, asked of the same ledger
    for the chore's own type and what it has left."""
    from types import SimpleNamespace

    from sift.kernel.jobs import WorkKind, WorkSummary
    from sift.kernel.jobs.families import Family
    from sift.slices.media_jobs.router import _housekeeping

    class Queue:
        async def last_finished_runs(self, types: list[str]) -> dict[str, object]:
            return {}

    asked: list[tuple[Family, list[str], float]] = []

    class Ledger:
        async def estimate(
            self, family: Family, job_types: Sequence[str], *, left: float, at_once: int
        ) -> object:
            asked.append((family, list(job_types), left))
            return SimpleNamespace(quick_seconds=60, slow_seconds=240)

    summary = WorkSummary(
        states={"dedup_scan": {"queued": 2}}, run={"dedup_scan": WorkKind(outstanding=2)}, since=0
    )

    chores = await _housekeeping(Queue(), summary, Ledger(), None)  # type: ignore[arg-type]

    sweep = next(one for one in chores if one.job_type == "dedup_scan")
    assert (sweep.quick_seconds, sweep.slow_seconds) == (60, 240)
    assert asked == [(Family.OTHER, ["dedup_scan"], 2)]


async def test_a_swap_on_activity_waits_for_them_then_says_its_own_time_never_the_last_swaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A swap's length is the people's, not the machine's: before the files move its row says
    "Waiting for them", then what its own measured rate leaves, and the swaps before it are
    never asked."""
    from typing import Any

    from sift.kernel.jobs import WorkKind, WorkSummary
    from sift.kernel.jobs import families as families_module
    from sift.kernel.jobs.families import OwnEstimate
    from sift.slices.media_jobs.router import _housekeeping

    class Queue:
        async def last_finished_runs(self, types: list[str]) -> dict[str, object]:
            return {}

    class Ledger:
        async def estimate(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("a swap is never priced from the runs before it")

    said = [OwnEstimate(waiting=True)]
    monkeypatch.setitem(families_module._OWN_ESTIMATES, "swap_session", lambda: said[0])
    summary = WorkSummary(
        states={"swap_session": {"running": 1}},
        run={"swap_session": WorkKind(outstanding=1)},
        since=0,
    )

    def swaps(chores: Sequence[object]) -> Any:
        return next(one for one in chores if one.job_type == "swap_session")  # type: ignore[attr-defined]

    waiting = swaps(await _housekeeping(Queue(), summary, Ledger(), None))  # type: ignore[arg-type]
    assert (waiting.reason, waiting.quick_seconds) == ("Waiting for them", None)
    said[0] = OwnEstimate(seconds=90)
    moving = swaps(await _housekeeping(Queue(), summary, Ledger(), None))  # type: ignore[arg-type]
    assert (moving.reason, moving.quick_seconds, moving.slow_seconds) == (None, 90, 90)


async def test_a_carrier_making_two_families_counts_under_both_and_prices_neither(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task making faces and meaning together is both families' work, and its cost is not one
    family's, so it lends its finished items to no family's price. One making a single family's
    products does. Counted by the queue, grouped by the products the rows name: no payload of a
    run's thousands is read here."""
    import importlib

    from sift.kernel.jobs import JobState
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.queue import LiveProducts
    from sift.slices.media_jobs.router import KindOfWork

    # By module path: the package's `router` attribute is the APIRouter, not this module.
    router = importlib.import_module("sift.slices.media_jobs.router")
    live: dict[str, list[tuple[tuple[str, ...], int]]] = {
        "build_mixed": [(("faces", "meaning"), 1), (("faces",), 2)],
        "build_pictures": [(("thumbnails", "previews"), 1)],
        "build_idle": [],
    }

    class Queue:
        async def live_products(self, job_types: list[str]) -> list[LiveProducts]:
            return [
                LiveProducts(job_type, JobState.QUEUED, False, products, count)
                for job_type in job_types
                for products, count in live[job_type]
            ]

        async def live_payloads(self, job_type: str) -> list[dict[str, object]]:
            raise AssertionError("every payload of a run read to count it")

    monkeypatch.setattr(router, "registered_product_carriers", lambda: frozenset(live))
    work = {"build_mixed": KindOfWork(done=0, outstanding=3, failed=0, left_units=9.0)}

    left, outstanding, single = await router._carried(work, Queue())

    assert left == {Family.IDENTIFY: 9.0, Family.SEMANTIC: 3.0, Family.GENERATE: 1.0}
    assert outstanding == {Family.IDENTIFY: 3, Family.SEMANTIC: 1, Family.GENERATE: 1}
    assert single == {"build_pictures": Family.GENERATE}


# --- a wait for the password ------------------------------------------------------------------


def test_a_job_parked_for_the_password_says_so_and_is_counted(client: TestClient) -> None:
    """A restart seals every saved key and keeps the session signed in, so a run that needs one
    parks. Its row says which key in the screen's words and offers the field; the page counts it
    for the unlock bar; a wait for anything else keeps its own words and is not counted."""
    database = client.app.state.database.path  # type: ignore[attr-defined]
    seed_job(
        database,
        "01HX0000000000000000000071",
        state="blocked",
        error="Waiting for your password to unlock the stash-box keys.",
    )
    seed_job(
        database, "01HX0000000000000000000072", state="blocked", error="The models are not on disk."
    )
    sign_in(client, "admin")

    assert row_for(client, "01HX0000000000000000000071")["waits_for_password"] is True
    assert row_for(client, "01HX0000000000000000000072")["waits_for_password"] is False
    assert client.get("/api/jobs").json()["password_wanted"] == 1


async def test_a_chore_whose_work_waits_for_the_password_says_so() -> None:
    """Enrichment's questions parked on a sealed key read "Waiting for your password" on Activity
    rather than a bare "Waiting"; one already running says nothing of it."""
    from sift.kernel.jobs import WorkKind, WorkSummary
    from sift.slices.media_jobs.router import WAITING_FOR_UNLOCK, _housekeeping

    class Queue:
        async def last_finished_runs(self, types: list[str]) -> dict[str, object]:
            return {}

    def scan(running: int = 0) -> WorkSummary:
        states = {"stash_box_scan": {"blocked": 1 - running, "running": running}}
        return WorkSummary(states=states, run={"stash_box_scan": WorkKind(outstanding=1)}, since=0)

    def reason_of(chores: Sequence[object]) -> object:
        return next(one for one in chores if one.job_type == "stash_box_scan").reason  # type: ignore[attr-defined]

    sealed = {"stash_box_scan": 1}
    parked = await _housekeeping(Queue(), scan(), None, None, {}, sealed)  # type: ignore[arg-type]
    assert reason_of(parked) == WAITING_FOR_UNLOCK
    going = await _housekeeping(Queue(), scan(running=1), None, None, {}, sealed)  # type: ignore[arg-type]
    assert reason_of(going) is None
    other = await _housekeeping(Queue(), scan(), None, None, {}, {})  # type: ignore[arg-type]
    assert reason_of(other) is None, "a wait for something else was said to be the password"


def test_work_sift_started_by_itself_is_left_off_unless_something_in_it_failed(
    client: TestClient,
) -> None:
    """A folder counted, a folder checked for changes: rows nobody pressed, left off the list. A
    family a failure is folded into stays, and says what failed and why in one line."""
    for job_id, kind in (
        ("01HX00000000000000000000B1", "scan_count"),
        ("01HX00000000000000000000B2", "library_reconcile"),
    ):
        seed(client, job_id, state="done", job_type=kind)
    seed(client, "01HX00000000000000000000B3", state="done", job_type="scan")
    seed_job(
        client.app.state.database.path,  # type: ignore[attr-defined]
        "01HX00000000000000000000B4",
        state="failed",
        job_type="probe",
        error="ffmpeg said\nexit code 69",
        parent_id="01HX00000000000000000000B3",
    )
    sign_in(client, "admin")

    page = client.get("/api/jobs", params={"fold": "true", "limit": 100}).json()
    listed = {one["id"]: one for one in page["jobs"]}
    assert "01HX00000000000000000000B1" not in listed
    assert "01HX00000000000000000000B2" not in listed
    failure = listed["01HX00000000000000000000B3"]["steps"]["failure"]
    assert failure == {
        "name": job_name("probe"),
        "reason": "exit code 69",
        "subject": None,
        "attempts": 0,
    }
    step = client.get("/api/jobs/01HX00000000000000000000B3/steps").json()["jobs"][0]
    assert step["reason"] == "exit code 69"
