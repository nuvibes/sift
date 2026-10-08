# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pause, resume and cancel for a pass, a sub-task or the whole queue, and what Run now presses."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.media_jobs.tests.test_api import app, client, seed, sign_in

pytestmark = [pytest.mark.integration]

__all__ = ["app", "client"]


def test_every_run_now_on_a_pass_presses_a_task_and_a_part_it_has(client: TestClient) -> None:
    """Run now on a pass presses the tasks `FAMILY_RUNS` names: each id a task Tasks lists, each
    part one of that task's, and every pass has one."""
    from sift.kernel.jobs.families import LONG_PASSES
    from sift.slices.media_jobs.router_controls import FAMILY_RUNS

    sign_in(client, "admin")
    tasks = {one["id"]: one for one in client.get("/api/tasks").json()["tasks"]}
    assert set(FAMILY_RUNS) == set(LONG_PASSES)
    for presses in FAMILY_RUNS.values():
        for _type, task, part in presses:
            assert task in tasks, task
            if part is not None:
                assert part in [one["key"] for one in tasks[task]["parts"]], (task, part)
    page = client.get("/api/jobs").json()
    assert page["families"]["generate"]["runs"] == [{"task": "generate", "parts": None}]


def test_a_pass_a_sub_task_or_the_whole_queue_pauses_and_resumes(client: TestClient) -> None:
    seed(client, "01HX00000000000000000000C1", state="blocked", job_type="face_scan")
    seed(client, "01HX00000000000000000000C2", state="queued", job_type="identify")
    sign_in(client, "admin")

    held = client.post("/api/jobs/pause", json={"family": "identify"}).json()
    assert held["paused"] is False and "face_scan" in held["types"]
    page = client.get("/api/jobs", params={"fold": "true", "limit": 100}).json()
    assert page["families"]["identify"]["paused"] is True
    assert page["families"]["identify"]["reason"] == "Paused."
    assert page["families"]["scan"]["paused"] is False
    row = {one["id"]: one for one in page["jobs"]}["01HX00000000000000000000C2"]
    assert row["steps"]["state"] == "paused"
    flat = client.get("/api/jobs", params={"type": "identify"}).json()["jobs"]
    assert [one["state"] for one in flat if one["id"] == "01HX00000000000000000000C2"] == ["paused"]

    assert client.post("/api/jobs/resume", json={"family": "identify"}).json()["types"] == []
    assert client.post("/api/jobs/pause", json={"type": "face_scan"}).json()["types"] == [
        "face_scan"
    ]
    assert client.post("/api/jobs/pause", json={}).json()["paused"] is True
    assert client.get("/api/jobs").json()["paused"] is True
    assert client.post("/api/jobs/resume", json={}).json()["paused"] is False
    assert client.post("/api/jobs/pause", json={"type": "nothing_runs_this"}).status_code == 404

    assert client.post("/api/jobs/cancel-work", json={"type": "face_scan"}).json() == {"stopped": 1}
    assert client.post("/api/jobs/cancel-work", json={}).status_code == 400


async def test_a_sub_task_switched_off_with_nothing_queued_is_drawn_and_counted_in_nothing() -> (
    None
):
    from sift.kernel.jobs.switchboard import Switchboard
    from sift.slices.media_jobs.activity_wire import KindOfWork
    from sift.slices.media_jobs.router_controls import parts_off, runs, switched_off

    board = Switchboard()
    music_on = False

    async def music_starts() -> bool:
        return music_on

    async def broken() -> bool:
        raise OSError("no settings")

    board.declare_shown(music_starts, "audio_fingerprint", "fingerprint_file")
    board.declare_shown(broken, "x")
    work = {
        "audio_fingerprint": KindOfWork(done=0, outstanding=0, failed=0, total=15),
        "fingerprint_file": KindOfWork(done=1, outstanding=2, failed=0, total=9),
    }
    kinds = ["audio_fingerprint", "fingerprint_file", "x", "fingerprint_stash_box"]
    off = await switched_off(board, kinds, work)
    assert off == ["audio_fingerprint"], "pressed work of a switched-off kind still counts"
    music_on = True
    assert await switched_off(board, kinds, work) == []
    (line,) = parts_off([*off, "nothing_counted"], work)
    assert line.on is False and line.total == 15
    from sift.kernel.jobs.families import Family

    assert [one.task for one in runs(Family.FINGERPRINT, off)] == ["generate"]


def test_a_pause_where_no_tasks_run_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from sift.slices.media_jobs import router_controls

    monkeypatch.setattr(router_controls, "part_or_none", lambda *_args: None)
    with pytest.raises(HTTPException) as refused:
        router_controls._running_pool(object())  # type: ignore[arg-type]
    assert refused.value.status_code == 409


async def test_music_and_duplicate_fingerprints_read_off_by_the_import_gate(
    client: TestClient,
) -> None:
    """Out of the box music fingerprints are off, and nothing refuses their claim-only follow-on."""
    from sift.kernel import wiring

    board = wiring.part_of_app(client.app, wiring.QUEUE).switchboard  # type: ignore[arg-type]
    assert await board.shown_off("audio_fingerprint")
    assert await board.refusal("audio_fingerprint") is None
    assert not await board.shown_off("fingerprint_file")


def test_a_pass_paused_holds_its_products_run_as_another_passs_steps(client: TestClient) -> None:
    """Fingerprint's music runs as Generate's per-file steps: its pause holds the music product,
    not Generate, and its cancel stops the steps that make only music."""
    from sift.kernel import wiring
    from sift.testing.jobs import seed_job

    db = client.app.state.database.path  # type: ignore[attr-defined]
    seed_job(
        db,
        "01HX00000000000000000000D1",
        state="blocked",
        job_type="generate_file",
        payload={"asset_id": "A1", "products": ["music"]},
    )
    seed_job(
        db,
        "01HX00000000000000000000D2",
        state="blocked",
        job_type="generate_file",
        payload={"asset_id": "A2", "products": ["thumbnails", "music"]},
    )
    sign_in(client, "admin")

    client.post("/api/jobs/pause", json={"family": "fingerprint"})
    holding = wiring.part_of_app(client.app, wiring.POOL).holding  # type: ignore[arg-type]
    assert {"music", "fingerprints"} <= holding.products
    assert "generate_file" not in holding.held
    client.post("/api/jobs/resume", json={"family": "fingerprint"})
    assert holding.products == frozenset()

    assert client.post("/api/jobs/cancel-work", json={"family": "fingerprint"}).json() == {
        "stopped": 1
    }


def test_a_press_head_reads_in_the_press_words_and_is_no_kind_of_work(client: TestClient) -> None:
    from sift.testing.jobs import seed_job

    db = client.app.state.database.path  # type: ignore[attr-defined]
    head = "01HX00000000000000000000E1"
    seed_job(
        db,
        head,
        state="done",
        job_type="press",
        payload={"title": "Creating hover previews for 2 files"},
    )
    for n, step in enumerate(("01HX00000000000000000000E2", "01HX00000000000000000000E3")):
        seed_job(
            db,
            step,
            state="blocked",
            job_type="generate_file",
            payload={"asset_id": f"A{n}", "products": ["previews"]},
            parent_id=head,
        )
    sign_in(client, "admin")

    page = client.get("/api/jobs", params={"fold": "true", "limit": 100}).json()
    row = {one["id"]: one for one in page["jobs"]}[head]
    assert row["name"] == "Creating hover previews for 2 files"
    assert row["steps"]["count"] == 2
    assert "press" not in page["older"] and "press" not in page["names"]
