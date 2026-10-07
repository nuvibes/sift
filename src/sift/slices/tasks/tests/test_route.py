# SPDX-License-Identifier: AGPL-3.0-or-later
"""Both routes of the Tasks screen, driven against the real application.

The one thing here that nothing else could prove is the wire between the settings write and the
queue. A cadence is an ordinary setting, and an ordinary setting write does not queue anything,
so switching automatic backups on has to reach the composition root's reaction to a saved
preference, or the choice is stored and no backup is ever queued until the next restart. That is a
fault a reading of either file alone says nothing about, so it is done here by really saving the
setting and really looking in the queue.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from functools import partial
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel import wiring
from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.jobs import unlisted_job_types
from sift.kernel.jobs.schedules import get_schedule
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.main import create_app
from sift.slices import tasks
from sift.slices.tasks import parts as tasks_parts
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]

TASKS = "/api/tasks"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    # No workers, for the reason the dashboard's own tests stop them: every check here reads what
    # is WAITING in the queue, and a worker is the other thing that changes that.
    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def sign_in(client: TestClient, role: str) -> None:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    _user_id, token, csrf = establish_session(
        db_path, role=role, username=f"sched-{role}", password="Sched-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf


def task(client: TestClient, task_id: str) -> dict[str, object]:
    answer = client.get(TASKS)
    assert answer.status_code == 200
    found: list[dict[str, object]] = [one for one in answer.json()["tasks"] if one["id"] == task_id]
    assert found, f"no task {task_id}"
    return found[0]


def run(client: TestClient, task_id: str, at: str = "now") -> int:
    return int(client.post(f"{TASKS}/{task_id}/run", json={"at": at}).status_code)


def test_a_guest_is_refused_both_routes(client: TestClient) -> None:
    """When the work runs describes how the installation is set up, not how anybody browses."""
    sign_in(client, "guest")
    assert client.get(TASKS).status_code == 403
    assert run(client, "backup") == 403


def test_every_task_a_pane_draws_is_answered_for_and_upkeep_is_not(client: TestClient) -> None:
    """The screen is generated from the declarations, so the reply is the whole list or nothing,
    less the upkeep nobody times, which no pane may draw a When for."""
    sign_in(client, "admin")
    answered = {one["id"]: one for one in client.get(TASKS).json()["tasks"]}
    assert {"scan", "generate", "identify", "backup"} <= set(answered)
    assert not {"quarantine-prune", "search-records-prune", "update-check"} & set(answered)
    assert answered["scan"]["press"] == "Scan now"
    assert answered["identify"]["press"] == "Identify now"
    assert answered["identify"]["reads"] == ["faces", "smart-search", "watermarks"]
    assert answered["backup"]["press"] == "Run now" and answered["backup"]["reads"] == []


def test_choosing_identifys_when_writes_the_three_it_reads(client: TestClient) -> None:
    """Identify's When is a reading: one answer written through the settings write lands on all
    three tasks, and three that disagree read as mixed."""
    sign_in(client, "admin")
    assert (
        client.put("/api/settings", json={"values": {"tasks.identify.when": "work"}}).status_code
        == 204
    )
    for one in ("faces", "smart-search", "watermarks", "identify"):
        assert task(client, one)["when"] == "work", one
    assert (
        client.put("/api/settings", json={"values": {"tasks.faces.when": "press"}}).status_code
        == 204
    )
    assert task(client, "identify")["when"] == "mixed"


def test_a_fresh_install_has_nothing_waiting_and_says_so(client: TestClient) -> None:
    """Off is the shipped answer for the backup and the sweep, and off has to read as off rather
    than as blank: a next run of null with the cadence in words, never an empty time.
    """
    sign_in(client, "admin")
    backup = task(client, "backup")
    assert backup["on"] is False
    assert backup["cadence"] == "Only when you run it"
    assert backup["next_run"] is None
    assert backup["last"] is None
    # What a waiting count counts, said on the row: a backup is a run, Identify faces files.
    assert (backup["unit"], backup["units"]) == ("run", "runs")
    assert (task(client, "faces")["unit"], task(client, "faces")["units"]) == ("file", "files")

    waiting = client.get("/api/jobs", params={"limit": 100, "type": "quarantine_prune"}).json()
    assert waiting["total"] == 0, "retention off out of the box: no sweep is placed"


def test_switching_a_cadence_on_through_the_settings_write_queues_the_next_run(
    client: TestClient,
) -> None:
    """The wire this file exists for. The row's control is the setting's own row, so this is the
    only write involved, and on its own it stores a choice and queues nothing."""
    sign_in(client, "admin")
    saved = client.put(
        "/api/settings",
        json={"values": {"backup.every_days": 1, "tasks.backup.when": "quiet"}},
    )
    assert saved.status_code == 204

    backup = task(client, "backup")
    assert backup["on"] is True
    # Quiet hours out of the box: a daily backup runs as the range opens.
    assert backup["cadence"] == "Every day, during quiet hours"
    # In quiet hours the range's opening is the time, so only How often is drawn beside it.
    assert backup["drawn_keys"] == ["backup.every_days"]
    assert backup["setting_keys"] == ["backup.every_days", "backup.at"]
    next_run = backup["next_run"]
    assert isinstance(next_run, int), "a backup that is on has a moment it is going to happen"

    waiting = client.get("/api/jobs", params={"limit": 100, "type": "backup_run"}).json()
    assert [one["id"] for one in waiting["jobs"]], "the next backup is a row in the queue"


def test_the_quarantine_sweep_is_armed_by_its_retention_rule(client: TestClient) -> None:
    """Its switch and its cadence are one number, which is the shape the screen draws."""
    sign_in(client, "admin")
    assert (
        client.put("/api/settings", json={"values": {"quarantine.keep_days": 14}}).status_code
        == 204
    )
    waiting = client.get("/api/jobs", params={"limit": 100, "type": "quarantine_prune"}).json()
    assert waiting["total"] == 1, "the rule on places the sweep's next run"


def test_run_now_queues_the_work_and_leaves_the_schedule_alone(client: TestClient) -> None:
    """Two things at the same time.

    The run has to really be queued (deduping it would collapse the press onto the delayed row
    that IS the schedule, so the button would do nothing and say it had worked) and the waiting
    row has to stay exactly where it was, or every press would bring the whole chain forward.
    """
    sign_in(client, "admin")
    client.put(
        "/api/settings",
        json={"values": {"backup.every_days": 1, "tasks.backup.when": "quiet"}},
    )
    before = task(client, "backup")["next_run"]

    assert run(client, "backup") == 200
    assert task(client, "backup")["next_run"] == before

    queued = client.get("/api/jobs", params={"limit": 100, "type": "backup_run"}).json()
    assert queued["total"] >= 2, "the run asked for now, beside the one that was already waiting"


def test_a_quiet_hours_press_says_whether_it_waits_for_the_range(client: TestClient) -> None:
    """Said by the reply, from this device's clock. A browser working it out by comparing the start
    with its own clock is wrong by however far the two clocks are apart: a run starting immediately
    read as one that "starts at" the present second."""
    sign_in(client, "admin")
    hour = time.localtime().tm_hour

    def at(hours_from_now: int) -> str:
        return f"{(hour + hours_from_now) % 24:02d}:00"

    def quiet_hours(start: str, end: str) -> None:
        saved = client.put(
            "/api/settings", json={"values": {"tasks.quiet_from": start, "tasks.quiet_until": end}}
        )
        assert saved.status_code == 204, saved.text

    quiet_hours(at(2), at(3))
    shut = client.post(f"{TASKS}/duplicates/run", json={"at": "quiet"}).json()
    assert shut["starts_at"] is not None and shut["waits"] is True, shut

    quiet_hours(at(-1), at(2))
    opened = client.post(f"{TASKS}/shoots/run", json={"at": "quiet"}).json()
    assert opened["starts_at"] is not None and opened["waits"] is False, opened

    assert client.post(f"{TASKS}/suggestions/run", json={"at": "now"}).json()["waits"] is False


def test_run_now_refuses_a_task_that_does_not_exist(client: TestClient) -> None:
    """A task nobody declared is a 404. Quiet hours is not a task but the range every task's When
    is read against."""
    sign_in(client, "admin")
    assert run(client, "recognition-night") == 404
    assert run(client, "nothing-of-the-kind") == 404


def test_a_pressed_backup_says_its_run_is_read_on_its_row_not_on_activity(
    client: TestClient,
) -> None:
    """The backup is upkeep, left off Activity's list, so a press cannot send somebody there to
    follow it; a pass Activity draws still can."""
    sign_in(client, "admin")

    backup = client.post(f"{TASKS}/backup/run", json={"at": "now"})
    duplicates = client.post(f"{TASKS}/duplicates/run", json={"at": "now"})

    assert backup.status_code == 200, backup.text
    assert backup.json()["job_ids"] and backup.json()["on_activity"] is False
    assert duplicates.status_code == 200, duplicates.text
    assert duplicates.json()["on_activity"] is True


def test_the_two_daily_prunes_are_upkeep_activity_does_not_list(client: TestClient) -> None:
    """Deleting old search history and old quarantined files runs once a day on its own. Each is
    upkeep: its row on Tasks and its line in History say what it did, and Activity's list of what
    is happening now draws neither, as it draws neither the backup nor the update check."""
    assert client.app is not None
    for task_id in ("quarantine-prune", "search-records-prune"):
        declared = get_schedule(task_id)
        assert declared is not None, task_id
        assert declared.job_type in unlisted_job_types(), task_id


def test_a_press_that_cannot_run_here_says_why_as_a_conflict(client: TestClient) -> None:
    """The starter's sentence reaches the person as it was written: a 409, because the task
    exists and the press was understood: it is this install's state that stands in the way."""
    sign_in(client, "admin")
    client.put("/api/settings", json={"values": {"stash_boxes.scan": False}})

    answer = client.post(f"{TASKS}/enrichment/run", json={"at": "now"})

    assert answer.status_code == 409
    assert "turned off" in answer.json()["detail"]


def test_a_task_says_its_parts_and_a_press_on_a_part_it_lacks_is_refused(
    client: TestClient,
) -> None:
    """The menu is drawn from the same declaration the press is checked against."""
    sign_in(client, "admin")
    answered = {one["id"]: one for one in client.get(TASKS).json()["tasks"]}
    assert answered["scan"]["locations"] is True and answered["scan"]["parts"] == []
    # A Build carries its folders to every page, so Generate and Identify offer them too.
    assert answered["generate"]["locations"] is True and answered["identify"]["locations"] is True
    assert answered["faces"]["locations"] is False
    assert answered["identify"]["dry"] is True
    # A task whose run decides and writes in one pass has a dry run once its feature splits it.
    assert all(
        answered[one]["dry"]
        for one in ("scan", "duplicates", "suggestions", "shoots", "enrichment", "backup")
    )
    assert [one["key"] for one in answered["identify"]["parts"]] == [
        "faces",
        "meaning",
        "watermarks",
    ]
    refused = client.post(f"{TASKS}/identify/run", json={"parts": ["thumbnails"]})
    assert refused.status_code == 400 and "no part called" in refused.json()["detail"]
    assert client.post(f"{TASKS}/backup/run", json={"locations": ["x"]}).status_code == 400


def test_a_run_press_files_no_decision_and_its_body_takes_no_card(client: TestClient) -> None:
    """A press of a task's run is a run, stopped and read on Activity like every other. No card on
    Organize presses one, so its body takes no `card` and the press files nothing under any
    queue."""
    database = client.app.state.database  # type: ignore[attr-defined]
    portal = client.portal
    assert portal is not None, "the client is used inside its context, where its portal is open"

    sign_in(client, "admin")
    refused = client.post(f"{TASKS}/music/run", json={"at": "now", "card": True})
    assert refused.status_code == 422
    pressed = client.post(f"{TASKS}/music/run", json={"at": "now"})
    assert pressed.status_code == 200, pressed.text
    rows = portal.call(
        database.fetch_all, "SELECT queue FROM workbench_decisions WHERE queue = 'music'"
    )
    assert rows == []


def test_a_dry_run_press_queues_the_dry_run_and_nothing_else(client: TestClient) -> None:
    sign_in(client, "admin")
    before = client.get("/api/jobs", params={"limit": 100}).json()["total"]
    answer = client.post(f"{TASKS}/generate/run", json={"dry": True})
    assert answer.status_code == 200 and answer.json()["dry"] is True
    queued = client.get("/api/jobs", params={"limit": 100, "type": "task_dry_run"}).json()
    assert queued["total"] == 1
    assert client.get("/api/jobs", params={"limit": 100}).json()["total"] == before + 1


def test_generate_for_some_folders_is_counted_planned_and_named_over_them_only(
    client: TestClient, tmp_path: Path
) -> None:
    """Through the real wiring: the run a press on one folder queues carries that folder and is
    weighed by its files, and the dry run counts and names only them. The other folder's file
    arrived first, so a walk that ignored the folders would name it first."""
    database = client.app.state.database  # type: ignore[attr-defined]
    portal = client.portal
    assert portal is not None, "the client is used inside its context, where its portal is open"
    user_id, token, csrf = establish_session(
        database.path, role="admin", username="sched-admin", password="Sched-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    roots: dict[str, str] = {}
    for at, name in enumerate(("garden", "holidays")):
        root_id, asset_id = new_id(), new_id()
        roots[name] = root_id
        for statement, values in (
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root_id, name, str(tmp_path / name), 1_700_000_000),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, width, height, duration_ms,"
                " size_bytes, original_filename, added_at, probed_at)"
                " VALUES (?, ?, 'video', 1920, 1080, 4000, 14, ?, ?, ?)",
                (asset_id, f"digest-{name}", f"{name}.mp4", 1_700_000_000 + at, 1_700_000_000),
            ),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, rel_path, filename,"
                " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id(), asset_id, root_id, f"{name}.mp4", f"{name}.mp4", 1, 1),
            ),
        ):
            portal.call(database.execute, statement, values)
    holidays = roots["holidays"]

    pressed = client.post(f"{TASKS}/generate/run", json={"at": "now", "locations": [holidays]})

    assert pressed.status_code == 200, pressed.text
    row = portal.call(
        database.fetch_one, "SELECT payload FROM jobs WHERE type = 'generate' AND parent_id IS NULL"
    )
    assert row is not None
    payload = json.loads(row["payload"])
    assert payload["roots"] == [holidays] and payload["files"] == 1

    service = wiring.part_of_app(client.app, tasks.SERVICE)  # type: ignore[arg-type]
    only = tasks.Selection(locations=(holidays,))
    report = portal.call(service.rehearse, "generate", only, user_id)
    assert report.said.startswith("Generate for holidays would work on 1 file."), report.said
    assert report.names == ("holidays.mp4",)


def test_scan_for_one_folder_plans_what_its_walk_would_read_and_mark_missing(
    client: TestClient, tmp_path: Path
) -> None:
    """Through the real wiring: a file on the disk that no row records is one Scan would take in,
    and a row whose file is gone is one it would mark missing. The plan writes neither."""
    database = client.app.state.database  # type: ignore[attr-defined]
    library = client.app.state.library  # type: ignore[attr-defined]
    portal = client.portal
    assert portal is not None, "the client is used inside its context, where its portal is open"
    user_id, token, csrf = establish_session(
        database.path, role="admin", username="sched-admin", password="Sched-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    directory = tmp_path / "holidays"
    directory.mkdir()
    (directory / "arrived.mp4").write_bytes(b"not read by a plan")
    root = portal.call(partial(library.create_root, name="holidays", abs_path=directory))
    asset_id = new_id()
    for statement, values in (
        (
            "INSERT INTO assets (id, identity, media_type, width, height, duration_ms,"
            " size_bytes, original_filename, added_at, probed_at)"
            " VALUES (?, 'digest-left', 'video', 1920, 1080, 4000, 14, 'left.mp4', 1, 1)",
            (asset_id,),
        ),
        (
            "INSERT INTO asset_locations (id, asset_id, root_id, rel_path, filename,"
            " first_seen_at, last_seen_at) VALUES (?, ?, ?, 'left.mp4', 'left.mp4', 1, 1)",
            (new_id(), asset_id, root.id),
        ),
    ):
        portal.call(database.execute, statement, values)

    service = wiring.part_of_app(client.app, tasks.SERVICE)  # type: ignore[arg-type]
    report = portal.call(service.rehearse, "scan", tasks.Selection(locations=(root.id,)), user_id)

    assert report.said.startswith(
        "Scan for holidays would take in 1 new file and mark 1 file missing."
    ), report.said
    assert report.names == ("arrived.mp4",)
    held = portal.call(
        database.fetch_all, "SELECT status FROM asset_locations WHERE root_id = ?", (root.id,)
    )
    assert [row["status"] for row in held] == ["present"]


def test_a_finished_dry_runs_report_is_laid_out_on_its_row_as_fields(client: TestClient) -> None:
    """The row draws the report's parts and names from fields, not from the sentence."""
    sign_in(client, "admin")
    assert client.post(f"{TASKS}/generate/run", json={"dry": True}).status_code == 200
    plan = tasks_parts.Plan(
        files=4,
        lines=(tasks_parts.PlanLine("Thumbnails", 3), tasks_parts.PlanLine("Hover previews", 1)),
        names=("beach.mp4",),
    )
    note = plan.reported("Generate").note()
    database = client.app.state.database  # type: ignore[attr-defined]
    portal = client.portal
    assert portal is not None, "the client is used inside its context, where its portal is open"
    portal.call(
        database.execute,
        "UPDATE jobs SET state = 'done', note = ? WHERE type = 'task_dry_run'",
        (note,),
    )

    report = task(client, "generate")["dry_run"]["report"]  # type: ignore[index]

    assert report["headline"] == "Generate would work on 4 files."
    assert report["parts"] == [
        {"label": "Thumbnails", "count": 3},
        {"label": "Hover previews", "count": 1},
    ]
    assert (report["names"], report["more"], report["cannot"]) == (["beach.mp4"], 3, [])

    portal.call(
        database.execute,
        "UPDATE jobs SET state = 'failed', error = 'it stopped' WHERE type = 'task_dry_run'",
        (),
    )

    failed = task(client, "generate")["dry_run"]
    assert failed["outcome"] == "failed" and failed["report"] is None  # type: ignore[index]
