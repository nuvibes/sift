# SPDX-License-Identifier: AGPL-3.0-or-later
"""The library's endpoints, driven against the real application.

A root is the server's disk and is refused to a guest; a folder is somebody's library and is
scoped, so one a guest may not see is missing rather than forbidden: a 403 would confirm it exists.
"""

from __future__ import annotations

import json
import os
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel import db as db_module
from sift.kernel import library_write
from sift.kernel.access import Repository
from sift.kernel.config import get_settings
from sift.kernel.content import library as library_module
from sift.kernel.content.library import NotWritable
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.jobs.tuning import DEFAULT_PRIORITY, WAITED_ON_PRIORITY
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.kernel.paths import presence
from sift.main import create_app
from sift.slices.library_roots.router import (
    _REACHABLE_TIMEOUT,
    _created_at,
    _reachable,
)
from sift.slices.library_roots.tests.conftest import POSIX_ONLY, VIDEO_SECONDS, draw
from sift.testing.auth import TEST_PIN, establish_session, give_pin
from sift.testing.library import write_rows
from sift.testing.settings import set_app_setting

pytestmark = [pytest.mark.integration]

#: The check must give up before the filesystem answers.
SLOWER_THAN_THE_WAIT = _REACHABLE_TIMEOUT * 2

LIBRARY = "/api/library"
ROOTS = "/api/library/roots"
#: The Scan task's Run now: the whole-library pass.
SCAN_NOW = "/api/tasks/scan/run"
FOLDERS = "/api/library/folders"


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
def idle_client(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The application with no workers, so what is waiting in the queue stays waiting."""

    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    with TestClient(app) as c:
        yield c


def sign_in(client: TestClient, role: str) -> str:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role=role, username=f"library-{role}", password="Library-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.fixture
def library(tmp_path: Path) -> Path:
    directory = tmp_path / "media"
    directory.mkdir()
    return directory


def folders_named(client: TestClient, *names: str) -> dict[str, dict[str, object]]:
    """Wait for a scan to have made folder rows for these, then hand them back by name. A folder
    row exists once a scan has found a file inside it."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        found = {
            str(folder["name"]): dict(folder) for folder in client.get(FOLDERS).json()["folders"]
        }
        if all(name in found for name in names):
            return found
        time.sleep(0.1)
    raise AssertionError(f"the scan never made folder rows for {names}")


def add_root(client: TestClient, path: Path) -> dict[str, object]:
    response = client.post(ROOTS, json={"abs_path": str(path)})
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- adding a root ---------------------------------------------------------------------------


def test_a_root_is_added_and_listed_without_its_path(client: TestClient, library: Path) -> None:
    """A root's absolute path goes in and never comes back to the browser."""
    sign_in(client, "admin")
    created = add_root(client, library)

    assert created["name"] == library.name, "the name is read off the directory, not typed in"
    assert "abs_path" not in created

    listed = client.get(ROOTS).json()
    assert [root["name"] for root in listed["roots"]] == [library.name]
    assert all("abs_path" not in root for root in listed["roots"])


def test_an_overlapping_root_is_refused_in_words_a_person_can_act_on(
    client: TestClient, library: Path
) -> None:
    """The overlap refusal, as somebody adding a folder actually meets it."""
    sign_in(client, "admin")
    add_root(client, library)
    inside = library / "clips"
    inside.mkdir()

    response = client.post(ROOTS, json={"abs_path": str(inside)})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "already watching" in detail
    assert library.name in detail, "it says which library it clashes with"
    assert "constraint" not in detail.lower(), "and it does not say anything about a database"


def test_a_folder_that_is_not_there_is_refused_with_the_reason(
    client: TestClient, library: Path
) -> None:
    sign_in(client, "admin")

    response = client.post(ROOTS, json={"abs_path": str(library / "nope")})

    assert response.status_code == 400
    assert "cannot find" in response.json()["detail"]


def test_a_guest_cannot_see_or_add_a_root(client: TestClient, library: Path) -> None:
    """A root describes the server's disk. It is not a guest's business that one exists."""
    sign_in(client, "admin")
    add_root(client, library)

    sign_in(client, "guest")

    assert client.get(ROOTS).status_code == 403
    assert client.post(ROOTS, json={"abs_path": str(library)}).status_code == 403


# --- the vault ------------------------------------------------------------------------------
# A whole library can be put in the vault, so losing the way back in costs the most.


def test_a_library_cannot_be_hidden_without_a_pin(client: TestClient, library: Path) -> None:
    """Without a PIN a vaulted library would be lost, not concealed."""
    user_id = sign_in(client, "admin")

    refused = client.post(ROOTS, json={"abs_path": str(library), "vault": True})
    assert refused.status_code == 409
    assert "PIN" in refused.json()["detail"]

    give_pin(client.app.state.database.path, user_id)  # type: ignore[attr-defined]
    allowed = client.post(ROOTS, json={"abs_path": str(library), "vault": True})
    assert allowed.status_code == 201
    assert allowed.json()["vault"] is True


def test_an_existing_library_cannot_be_moved_into_the_vault_without_a_pin(
    client: TestClient, library: Path
) -> None:
    """The vault flag rides on the ordinary edit, so the refusal is about the flag going on."""
    user_id = sign_in(client, "admin")
    root = add_root(client, library)

    refused = client.patch(f"{ROOTS}/{root['id']}", json={"vault": True})
    assert refused.status_code == 409
    # An edit that says nothing about the vault is untouched by the rule.
    assert client.patch(f"{ROOTS}/{root['id']}", json={}).status_code == 200

    give_pin(client.app.state.database.path, user_id)  # type: ignore[attr-defined]
    assert client.patch(f"{ROOTS}/{root['id']}", json={"vault": True}).status_code == 200


def test_a_hidden_library_cannot_be_brought_back_until_the_vault_is_open(
    client: TestClient, library: Path
) -> None:
    """A hidden library cannot be brought back until the vault is open: this screen lists vaulted
    libraries, so no concealment stands in front of the write."""
    user_id = sign_in(client, "admin")
    db = client.app.state.database.path  # type: ignore[attr-defined]
    give_pin(db, user_id)
    root = add_root(client, library)
    assert client.patch(f"{ROOTS}/{root['id']}", json={"vault": True}).json()["vault"] is True

    refused = client.patch(f"{ROOTS}/{root['id']}", json={"vault": False})
    assert refused.status_code == 409
    assert "unlock" in refused.json()["detail"]

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    assert client.patch(f"{ROOTS}/{root['id']}", json={"vault": False}).json()["vault"] is False


# --- changing and removing -------------------------------------------------------------------


def test_a_root_keeps_the_name_the_directory_has(client: TestClient, library: Path) -> None:
    """There is no renaming: the name follows the directory, so there are never two names."""
    sign_in(client, "admin")
    root = add_root(client, library)

    response = client.patch(f"{ROOTS}/{root['id']}", json={})

    assert response.status_code == 200
    assert response.json()["name"] == library.name


def test_removing_a_root_deletes_no_files(client: TestClient, library: Path) -> None:
    """Removing a root deletes no files."""
    sign_in(client, "admin")
    kept = library / "holiday.mp4"
    kept.write_bytes(b"the user's file")
    root = add_root(client, library)

    assert client.delete(f"{ROOTS}/{root['id']}").status_code == 204

    assert client.get(ROOTS).json()["roots"] == []
    assert kept.read_bytes() == b"the user's file", "Sift forgets a library; it does not delete one"


def test_removing_a_root_that_is_not_there_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")
    assert client.delete(f"{ROOTS}/01HX0000000000000000000009").status_code == 404


def test_a_rescan_queues_a_scan(client: TestClient, library: Path) -> None:
    sign_in(client, "admin")
    root = add_root(client, library)

    response = client.post(f"{ROOTS}/{root['id']}/rescan")

    assert response.status_code == 202
    assert response.json()["job_id"]


def test_a_scan_only_rescan_says_so_in_the_payload_and_an_ordinary_one_does_not(
    client: TestClient, library: Path
) -> None:
    """`scan_only` is left out of the payload when false, so an ordinary scan's identity (and the
    dedupe that matches it) is unchanged; both shapes are read back off the row."""
    sign_in(client, "admin")
    root = add_root(client, library)

    plain = client.post(f"{ROOTS}/{root['id']}/rescan")
    assert plain.status_code == 202, plain.text
    narrowed = client.post(f"{ROOTS}/{root['id']}/rescan", params={"scan_only": True})
    assert narrowed.status_code == 202, narrowed.text

    db = client.app.state.database.path  # type: ignore[attr-defined]
    with sqlite3.connect(db) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        payloads = {
            str(job_id): json.loads(str(payload))
            for job_id, payload in connection.execute(
                "SELECT id, payload FROM jobs WHERE type = 'scan'"
            )
        }
    assert "scan_only" not in payloads[plain.json()["job_id"]], (
        "an ordinary rescan carried the flag, so every scan in the library has a new identity"
    )
    assert payloads[narrowed.json()["job_id"]]["scan_only"] is True


def test_two_rescans_of_one_root_are_one_scan(idle_client: TestClient, library: Path) -> None:
    """A double press is one scan: both answers carry the same job id. No worker runs, so the
    first press is still waiting when the second arrives."""
    client = idle_client
    sign_in(client, "admin")
    root = add_root(client, library)

    first = client.post(f"{ROOTS}/{root['id']}/rescan")
    second = client.post(f"{ROOTS}/{root['id']}/rescan")

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["job_id"] == second.json()["job_id"]

    db = client.app.state.database.path  # type: ignore[attr-defined]
    with sqlite3.connect(db) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        queued = [
            str(payload)
            for (payload,) in connection.execute("SELECT payload FROM jobs WHERE type = 'scan'")
        ]
    assert len(queued) == 1, f"the same root was queued {len(queued)} times"


def test_an_added_folder_is_counted_ahead_of_its_walk_and_a_press_joins_that_count(
    idle_client: TestClient, library: Path
) -> None:
    client = idle_client
    sign_in(client, "admin")
    root = add_root(client, library)
    added = _counts(client)
    assert len(added) == 1, "adding a folder queued no count"

    pressed = client.post(f"{ROOTS}/{root['id']}/rescan")

    assert _counts(client) == added == [(pressed.json()["job_id"], WAITED_ON_PRIORITY)]


def _counts(client: TestClient) -> list[tuple[str, int]]:
    db = client.app.state.database.path  # type: ignore[attr-defined]
    with sqlite3.connect(db) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        return [
            (json.loads(str(payload))["scan_id"], int(priority))
            for payload, priority in connection.execute(
                "SELECT payload, priority FROM jobs WHERE type = 'scan_count'"
            )
        ]


def test_a_press_of_rescan_is_waited_on(client: TestClient, library: Path) -> None:
    """A press of Scan (Run now or one folder's) is queued at the waited-on priority, since a cap
    cannot help while a long pass holds every worker; priority is read at claiming."""
    sign_in(client, "admin")
    root = add_root(client, library)

    whole = client.post(SCAN_NOW, json={"at": "now"})
    one = client.post(f"{ROOTS}/{root['id']}/rescan")
    assert whole.status_code == 200, whole.text
    assert one.status_code == 202, one.text

    priorities = _priorities(client)
    (whole_id,) = whole.json()["job_ids"]
    assert priorities[whole_id] == WAITED_ON_PRIORITY
    assert priorities[one.json()["job_id"]] == WAITED_ON_PRIORITY
    assert WAITED_ON_PRIORITY < DEFAULT_PRIORITY, "a press would run LAST"


def test_a_scan_the_machine_started_stays_at_the_ordinary_priority(
    client: TestClient, library: Path
) -> None:
    """A scan the machine started, such as adding a folder, stays at the ordinary priority."""
    sign_in(client, "admin")
    add_root(client, library)

    walks = _priorities(client)
    assert walks, "adding a library folder queued no walk at all"
    assert set(walks.values()) == {DEFAULT_PRIORITY}, (
        "a walk nobody asked for is being treated as a press"
    )


def test_a_press_collapses_onto_a_machine_walk_and_takes_it_to_the_front(
    idle_client: TestClient, library: Path
) -> None:
    """A press that collapses onto a waiting machine walk takes it to the front: a collapse keeps
    the more urgent priority. No workers, so the walk is still waiting."""
    client = idle_client
    sign_in(client, "admin")
    root = add_root(client, library)
    machine = _priorities(client)
    assert set(machine.values()) == {DEFAULT_PRIORITY}

    pressed = client.post(f"{ROOTS}/{root['id']}/rescan")

    assert pressed.status_code == 202, pressed.text
    assert pressed.json()["job_id"] in machine, "the press did not collapse onto the machine's walk"
    assert _priorities(client)[pressed.json()["job_id"]] == WAITED_ON_PRIORITY


def _priorities(client: TestClient) -> dict[str, int]:
    """Every walk on the queue and how urgent it is."""
    db = client.app.state.database.path  # type: ignore[attr-defined]
    with sqlite3.connect(db) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        return {
            str(job_id): int(priority)
            for job_id, priority in connection.execute(
                "SELECT id, priority FROM jobs WHERE type IN ('scan', 'library_scan')"
            )
        }


def test_a_rescan_of_one_folder_is_not_collapsed_onto_a_rescan_of_the_root(
    client: TestClient, library: Path
) -> None:
    """A scan of one folder is not collapsed onto a scan of its root: different payload, different
    work."""
    sign_in(client, "admin")
    root = add_root(client, library)

    whole = client.post(f"{ROOTS}/{root['id']}/rescan")
    narrowed = client.post(f"{ROOTS}/{root['id']}/rescan", params={"scan_only": True})

    assert whole.json()["job_id"] != narrowed.json()["job_id"]


def test_a_press_of_scan_runs_whatever_the_scan_task_s_when_says(
    client: TestClient, library: Path
) -> None:
    """A press runs whatever the Scan task's When says: the When decides only whether a walk starts
    on its own."""
    sign_in(client, "admin")
    root = add_root(client, library)
    saved = client.put("/api/settings", json={"values": {"tasks.scan.when": "press"}})
    assert saved.status_code == 204, saved.text

    whole = client.post(SCAN_NOW, json={"at": "now"})
    one = client.post(f"{ROOTS}/{root['id']}/rescan")

    assert whole.status_code == 200, whole.text
    assert one.status_code == 202, one.text
    queued = _priorities(client)
    assert all(job_id in queued for job_id in whole.json()["job_ids"]), "nothing was queued"
    assert whole.json()["job_ids"], "the press was taken and did nothing"
    assert one.json()["job_id"] in queued


def _scans_queued(client: TestClient) -> int:
    """How many walks are on the queue, of either shape."""
    db = client.app.state.database.path  # type: ignore[attr-defined]
    with sqlite3.connect(db) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        (queued,) = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE type IN ('scan', 'library_scan')"
        ).fetchone()
    return int(queued)


def test_the_scan_task_s_when_is_drawn_on_the_importing_screen(client: TestClient) -> None:
    """Scan's When is drawn on Importing, on by default; Settings draws no second control
    for it, nor for duplicates or suggestions."""
    sign_in(client, "admin")

    tasks = {one["id"]: one for one in client.get("/api/tasks").json()["tasks"]}
    for task_id in ("scan", "duplicates", "suggestions"):
        assert task_id in tasks, f"{task_id} has no row to choose its When on"
        assert tasks[task_id]["set_in"] == "importing", f"{task_id} is not drawn on Importing"
    assert tasks["scan"]["when"] == "work"

    sections = client.get("/api/settings").json()["sections"]
    drawn = {setting["key"] for section in sections for setting in section["settings"]}
    for retired in ("importing.scan", "dedup.scan", "suggestions.scan"):
        assert retired not in drawn, f"{retired} is drawn beside the When that replaced it"


def test_a_rescan_of_a_root_that_is_not_there_is_a_404(client: TestClient) -> None:
    """A rescan of a missing root is a 404, not a job that fails unseen."""
    sign_in(client, "admin")
    assert client.post(f"{ROOTS}/01HX0000000000000000000009/rescan").status_code == 404


def test_scanning_everything_is_ONE_request(client: TestClient, library: Path) -> None:
    """Scanning every folder is one request, read from the library now rather than from what the
    screen last listed."""
    sign_in(client, "admin")
    add_root(client, library)

    response = client.post(SCAN_NOW, json={"at": "now"})

    assert response.status_code == 200, response.text
    assert len(response.json()["job_ids"]) == 1
    # The Scan task's Run now is the one door: `/library/rescan` is not an address.
    assert client.post(f"{LIBRARY}/rescan").status_code == 405


def test_scanning_everything_is_admin_only(client: TestClient) -> None:
    """A guest may not start work."""
    sign_in(client, "guest")
    assert client.post(SCAN_NOW, json={"at": "now"}).status_code == 403


def test_an_empty_library_is_a_pass_that_finds_nothing_and_not_a_refusal(
    client: TestClient,
) -> None:
    """ "Every folder" in an empty library is a pass that finds nothing, not a 404."""
    sign_in(client, "admin")
    assert client.post(SCAN_NOW, json={"at": "now"}).status_code == 200


# --- the folder tree -------------------------------------------------------------------------


def test_a_new_root_comes_with_a_folder_to_browse(client: TestClient, library: Path) -> None:
    sign_in(client, "admin")
    add_root(client, library)

    folders = client.get(FOLDERS).json()["folders"]

    assert [folder["name"] for folder in folders] == [library.name]
    assert folders[0]["parent_id"] is None


def test_a_guest_with_no_grants_sees_an_empty_tree(client: TestClient, library: Path) -> None:
    """Empty, not forbidden. There is nothing here to tell them about."""
    sign_in(client, "admin")
    add_root(client, library)

    sign_in(client, "guest")
    response = client.get(FOLDERS)

    assert response.status_code == 200
    assert response.json()["folders"] == []


def test_a_folder_a_guest_may_not_see_is_missing_rather_than_forbidden(
    client: TestClient, library: Path
) -> None:
    """A 403 would confirm the folder exists."""
    sign_in(client, "admin")
    add_root(client, library)
    hidden = client.get(FOLDERS).json()["folders"][0]["id"]

    sign_in(client, "guest")
    response = client.get(f"{FOLDERS}/{hidden}")

    assert response.status_code == 404
    assert response.json()["detail"] == "not found"


def test_a_folder_that_never_existed_answers_exactly_the_same(
    client: TestClient, library: Path
) -> None:
    """Made-up and forbidden are indistinguishable from outside."""
    sign_in(client, "admin")
    add_root(client, library)
    hidden = client.get(FOLDERS).json()["folders"][0]["id"]

    sign_in(client, "guest")
    denied = client.get(f"{FOLDERS}/{hidden}")
    absent = client.get(f"{FOLDERS}/01HX0000000000000000000009")

    assert denied.status_code == absent.status_code
    assert denied.json() == absent.json()


def test_only_an_admin_is_told_how_many_files_a_folder_holds(
    client: TestClient, library: Path
) -> None:
    """None is not zero: the number was not offered."""
    sign_in(client, "admin")
    add_root(client, library)
    folder = client.get(FOLDERS).json()["folders"][0]

    detail = client.get(f"{FOLDERS}/{folder['id']}").json()

    assert detail["file_count"] == 0
    assert detail["name"] == library.name


# --- the folder tree, filtered and moved -----------------------------------------------------


def test_the_tree_can_be_asked_for_one_folder_s_children(client: TestClient, library: Path) -> None:
    """One folder's children, as the browser asks when a row is opened."""
    draw(library / "clips" / "a.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    sign_in(client, "admin")
    root = add_root(client, library)
    top = client.get(FOLDERS).json()["folders"][0]
    client.post(f"{ROOTS}/{root['id']}/rescan")
    folders_named(client, "clips")

    children = client.get(FOLDERS, params={"parent": top["id"]})

    assert children.status_code == 200
    assert all(folder["parent_id"] == top["id"] for folder in children.json()["folders"])


# --- what a folder IS ---------------------------------------------------------------------------
# A folder's properties are facts about the server's disk and physical counts, so the route is
# admin-only.


def _properties_holding(client: TestClient, folder_id: str, *, files: int) -> dict[str, Any]:
    """A folder's properties, once the scan has taken in every file under it, which can lag behind
    the row appearing."""
    deadline = time.monotonic() + 30
    while True:
        said = client.get(f"{FOLDERS}/{folder_id}/properties")
        assert said.status_code == 200, said.text
        facts = dict(said.json())
        if facts["file_count"] == files or time.monotonic() > deadline:
            return facts
        time.sleep(0.1)


def test_a_folder_s_properties_are_its_path_its_size_and_what_is_under_it(
    client: TestClient, library: Path
) -> None:
    """A folder's count and size are over its whole subtree, as a file manager's "Contains" is."""
    draw(library / "clips" / "a.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    draw(library / "clips" / "more" / "b.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    sign_in(client, "admin")
    root = add_root(client, library)
    client.post(f"{ROOTS}/{root['id']}/rescan")
    folders = folders_named(client, "clips", "more")

    facts = _properties_holding(client, str(folders["clips"]["id"]), files=2)
    assert facts["location"] == str(library / "clips")
    assert facts["file_count"] == 2, "the file in the subfolder counts towards the folder above it"
    assert facts["folder_count"] == 1, "`more`, and not `clips` itself"
    # A floor: what ffmpeg produces is not this test's business.
    assert facts["size_bytes"] > 0
    assert facts["created_at"] is not None


def test_a_library_folder_does_not_count_itself_among_its_folders(
    client: TestClient, library: Path
) -> None:
    """A library root does not count itself among its folders. Its path prefix is empty, so every
    folder row matches it, its own included."""
    draw(library / "clips" / "a.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    draw(library / "clips" / "more" / "b.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    sign_in(client, "admin")
    root = add_root(client, library)
    top = client.get(FOLDERS).json()["folders"][0]
    client.post(f"{ROOTS}/{root['id']}/rescan")
    folders_named(client, "clips", "more")

    facts = _properties_holding(client, str(top["id"]), files=2)

    assert facts["folder_count"] == 2, "`clips` and `more`, and NOT the library folder itself"
    assert facts["file_count"] == 2


def _facts_holding(
    client: TestClient, parent_id: str, folder_id: str, *, files: int
) -> dict[str, Any]:
    """One folder's row from the facts route, once its subtree count has settled."""
    deadline = time.monotonic() + 30
    while True:
        said = client.get(f"{FOLDERS}/facts", params={"parent": parent_id})
        assert said.status_code == 200, said.text
        inside = {str(one["id"]): dict(one) for one in said.json()["folders"]}
        row = inside.get(folder_id)
        if (row is not None and row["file_count"] == files) or time.monotonic() > deadline:
            assert row is not None, f"the facts route never listed {folder_id}"
            return row
        time.sleep(0.1)


def test_the_facts_an_order_needs_come_back_for_a_whole_screenful(
    client: TestClient, library: Path
) -> None:
    """One request gives the size and time facts for a screenful of folders, over each subtree."""
    draw(library / "clips" / "a.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    draw(library / "clips" / "more" / "b.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    sign_in(client, "admin")
    root = add_root(client, library)
    client.post(f"{ROOTS}/{root['id']}/rescan")
    folders = folders_named(client, "clips", "more")

    more = _facts_holding(client, str(folders["clips"]["id"]), str(folders["more"]["id"]), files=1)

    said = client.get(f"{FOLDERS}/facts", params={"parent": folders["clips"]["id"]})
    assert {one["id"] for one in said.json()["folders"]} == {folders["more"]["id"]}, (
        "the DIRECT children, which is what a screen draws"
    )
    assert more["file_count"] == 1
    assert more["newest_at"] is not None


def test_the_facts_route_is_not_shadowed_by_the_folder_it_sits_beside(
    client: TestClient, library: Path
) -> None:
    """`/folders/facts` is declared before `/folders/{folder_id}`, or the router answers it as a
    folder named "facts"."""
    sign_in(client, "admin")

    assert client.get(f"{FOLDERS}/facts").status_code == 200


def test_a_guest_is_not_told_what_is_in_the_folders_either(
    client: TestClient, library: Path
) -> None:
    """A guest is not told the folder facts: they count what a folder physically holds."""
    sign_in(client, "admin")
    add_root(client, library)

    sign_in(client, "guest")

    assert client.get(f"{FOLDERS}/facts").status_code == 403


def test_a_guest_is_not_told_how_big_a_folder_is(client: TestClient, library: Path) -> None:
    """A guest is refused the properties outright: size and location describe the server's disk."""
    draw(library / "clips" / "a.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    sign_in(client, "admin")
    root = add_root(client, library)
    client.post(f"{ROOTS}/{root['id']}/rescan")
    folder = folders_named(client, "clips")["clips"]

    sign_in(client, "guest")

    assert client.get(f"{FOLDERS}/{folder['id']}/properties").status_code == 403


def test_properties_for_a_folder_whose_row_will_not_resolve_on_the_disk_is_a_404(
    client: TestClient, library: Path
) -> None:
    """A row whose path escapes its library is refused by the confinement every write uses."""
    sign_in(client, "admin")
    root = add_root(client, library)
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    escaping = "01HX0000000000000000000041"
    write_rows(
        db_path,
        [
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
                " VALUES (?, ?, NULL, ?, ?)",
                (escaping, root["id"], "../outside", "outside"),
            )
        ],
    )

    assert client.get(f"{FOLDERS}/{escaping}/properties").status_code == 404


def test_a_folder_whose_library_row_has_gone_is_a_404_rather_than_an_error(
    client: TestClient, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder whose library row has gone answers 404, as a torn-down library passes through."""
    draw(library / "clips" / "a.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    sign_in(client, "admin")
    root = add_root(client, library)
    client.post(f"{ROOTS}/{root['id']}/rescan")
    folder = folders_named(client, "clips")["clips"]

    monkeypatch.setattr(library_module.LibraryStore, "get_root", _says_nothing)

    assert client.get(f"{FOLDERS}/{folder['id']}/properties").status_code == 404


async def _says_nothing(*args: object, **kwargs: object) -> None:
    return None


def test_a_folder_the_repository_will_not_describe_is_left_out_of_the_facts(
    client: TestClient, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder the repository will not describe is left out of the facts rather than raising."""
    draw(library / "clips" / "a.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    sign_in(client, "admin")
    root = add_root(client, library)
    client.post(f"{ROOTS}/{root['id']}/rescan")
    assert client.get(f"{FOLDERS}/facts").json()["folders"], "there is something to leave out"

    monkeypatch.setattr(Repository, "folder_contents", _says_nothing)

    said = client.get(f"{FOLDERS}/facts")
    assert said.status_code == 200
    assert said.json()["folders"] == []


def test_a_directory_that_will_not_say_when_it_was_made_says_nothing(tmp_path: Path) -> None:
    """None where the filesystem will not answer, never an invented date."""
    assert _created_at(tmp_path / "never-made") is None
    assert _created_at(tmp_path) is not None


def test_properties_for_a_folder_that_is_not_there_is_a_404(client: TestClient) -> None:
    """A 404, the same answer as a folder somebody may not see."""
    sign_in(client, "admin")

    assert client.get(f"{FOLDERS}/01HX0000000000000000000009/properties").status_code == 404


# --- changing a root -------------------------------------------------------------------------


def test_changing_a_root_that_is_not_there_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")

    response = client.patch(f"{ROOTS}/01HX0000000000000000000009", json={})

    assert response.status_code == 404


def test_a_root_cannot_be_renamed_at_all(client: TestClient, library: Path) -> None:
    """A name sent to the patch route is ignored."""
    sign_in(client, "admin")
    root = add_root(client, library)

    response = client.patch(f"{ROOTS}/{root['id']}", json={"name": "Videos"})

    assert response.status_code == 200
    assert response.json()["name"] == library.name


# --- a root Sift may change the files in ---------------------------------------------------------
# There is no per-root `managed` flag: adding a folder is the permission, and whether the disk
# allows a write is asked per folder at the moment of the write (`check_folder_may_change`).


# --- the parts that are reached without a whole application ------------------------------------


async def test_telling_the_watcher_nothing_when_there_is_no_watcher() -> None:
    """Without an application there is no watcher, and the rewatch asks rather than assumes."""
    from types import SimpleNamespace

    from sift.slices.library_roots.router import _rewatch

    await _rewatch(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))  # type: ignore[arg-type]


def test_adding_a_root_reads_it_straight_away(client: TestClient, library: Path) -> None:
    """Adding a folder reads it straight away; watching reports only later changes."""
    sign_in(client, "admin")
    add_root(client, library)

    # A job does not name its path, so the claim is on the count of scans. The parameter is
    # `type`: an unknown parameter is discarded, which would count every job.
    queued = client.get("/api/jobs", params={"type": "scan"}).json()
    assert queued["total"] == 1, "adding a folder queued no scan of it"
    assert queued["jobs"][0]["type"] == "scan"


def test_a_folder_can_be_added_without_reading_it_yet(client: TestClient, library: Path) -> None:
    """`scan: false`, asked only by the first-run flow, adds the folder without reading it yet;
    the row is still there."""
    sign_in(client, "admin")

    response = client.post(ROOTS, json={"abs_path": str(library), "scan": False})

    assert response.status_code == 201, response.text
    assert [root["name"] for root in client.get(ROOTS).json()["roots"]] == [library.name]
    queued = client.get("/api/jobs", params={"type": "scan"}).json()
    assert queued["total"] == 0, "adding a folder read it anyway"


def test_asking_what_was_skipped_in_a_folder_that_is_not_there_is_a_404(client: TestClient) -> None:
    """A 404 rather than an empty list, which would read as "nothing was refused here"."""
    sign_in(client, "admin")

    never = "01HX0000000000000000000099"
    assert client.get(f"/api/library/roots/{never}/rejections").status_code == 404


def test_what_a_folder_refused_is_listed_by_name(idle_client: TestClient, library: Path) -> None:
    """What a folder refused is listed by name, as the scanner writes it."""
    client = idle_client
    sign_in(client, "admin")
    root = str(add_root(client, library)["id"])
    write_rows(
        client.app.state.database.path,  # type: ignore[attr-defined]
        [
            (
                "INSERT INTO scan_rejections "
                "(id, root_id, rel_path, size_bytes, mtime_ns, reason, detected, "
                " first_seen_at, last_seen_at) "
                "VALUES ('01HX0000000000000000000098', ?, 'clips/broken.mp4', 12, 0, "
                "'not_decodable', 'text', 0, 0)",
                (root,),
            )
        ],
    )

    # The quarantine screen draws both piles for every root in one go.
    (mine,) = [one for one in client.get(QUARANTINE).json()["left_alone"] if one["root_id"] == root]
    listed = mine["rejections"]

    assert [one["rel_path"] for one in listed] == ["clips/broken.mp4"]
    assert listed[0]["reason"] == "not_decodable"
    assert listed[0]["detected"] == "text"


# --- the sharing mark on a folder row -----------------------------------------------------------
# The added-folders list carries the sharing mark too: a share deeper in has no row here, so the
# tree keeps it as well.


def test_a_shared_folder_row_carries_the_mark_and_an_ordinary_one_does_not(
    client: TestClient, library: Path
) -> None:
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    root = add_root(client, library)
    folder = client.get(FOLDERS).json()["folders"][0]

    plain = next(row for row in client.get(ROOTS).json()["roots"] if row["id"] == root["id"])
    assert plain["shared"] is False
    assert plain["restricted"] is False

    client.put(
        "/api/sharing",
        json={
            "object_type": "folder",
            "object_id": folder["id"],
            "subject_user_id": guest,
            "effect": "share",
        },
    )

    marked = next(row for row in client.get(ROOTS).json()["roots"] if row["id"] == root["id"])
    assert marked["shared"] is True
    # Decided on the folder itself: the filled mark.
    assert marked["shared_here"] is True
    assert marked["restricted"] is False


def test_a_root_with_no_folder_row_yet_simply_has_no_mark(
    client: TestClient, tmp_path: Path
) -> None:
    """A root not yet walked has no folder row, so it has no mark rather than a guess."""
    empty = tmp_path / "nothing-in-here"
    empty.mkdir()
    sign_in(client, "admin")
    root = add_root(client, empty)

    row = next(one for one in client.get(ROOTS).json()["roots"] if one["id"] == root["id"])

    assert row["shared"] is False
    assert row["restricted"] is False
    assert row["shared_here"] is False
    assert row["restricted_here"] is False


def test_the_root_row_and_the_tree_row_give_the_same_answer(
    client: TestClient, library: Path
) -> None:
    """The root row and the tree row read the mark once: a share on the root shows on both."""
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    root = add_root(client, library)
    folder = client.get(FOLDERS).json()["folders"][0]

    client.put(
        "/api/sharing",
        json={
            "object_type": "root",
            "object_id": root["id"],
            "subject_user_id": guest,
            "effect": "share",
        },
    )

    row = next(one for one in client.get(ROOTS).json()["roots"] if one["id"] == root["id"])
    tree = next(one for one in client.get(FOLDERS).json()["folders"] if one["id"] == folder["id"])

    assert row["shared"] == tree["shared"] is True
    assert row["shared_here"] == tree["shared_here"] is False
    assert row["restricted"] == tree["restricted"] is False


@POSIX_ONLY
async def test_a_folder_whose_parent_cannot_be_searched_counts_as_not_there(tmp_path: Path) -> None:
    """A permission error raising out of the check counts as not there: the guard fails closed."""
    shut = tmp_path / "shut"
    (shut / "share").mkdir(parents=True)
    shut.chmod(0o000)
    try:
        assert await _reachable(shut / "share") is False
    finally:
        shut.chmod(0o700)


async def test_a_check_that_raises_counts_as_not_there_on_every_operating_system(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check that raises is a refusal on every operating system."""
    monkeypatch.setattr(sys.modules["sift.slices.library_roots.router"], "presence", _refuses)

    assert await _reachable(tmp_path) is False


async def test_a_share_that_never_answers_is_given_up_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A share that never answers is given up on at the real bound; the folder is there, so the
    False comes from giving up."""

    def slowly(_path: Path) -> str:
        time.sleep(SLOWER_THAN_THE_WAIT)
        return "here"

    # The package exports a `router` object, so the module is reached by name.
    monkeypatch.setattr(sys.modules["sift.slices.library_roots.router"], "presence", slowly)

    started = time.monotonic()
    assert await _reachable(tmp_path) is False
    waited = time.monotonic() - started
    assert waited < SLOWER_THAN_THE_WAIT, "it waited for the filesystem instead of for itself"


async def test_a_folder_on_this_computer_is_read_on_the_spot(tmp_path: Path) -> None:
    """A local folder is asked through `presence` directly, with no thread or timeout."""
    assert presence(tmp_path) == "here"
    assert presence(tmp_path / "never") == "missing"


@POSIX_ONLY
async def test_a_local_folder_whose_parent_cannot_be_searched_is_silent(tmp_path: Path) -> None:
    """A local stat refused is silent, not gone: the same fail-closed rule."""
    shut = tmp_path / "shut"
    (shut / "disk").mkdir(parents=True)
    shut.chmod(0o000)
    try:
        assert presence(shut / "disk") == "silent"
    finally:
        shut.chmod(0o700)


def test_a_folder_on_this_computer_is_asked_too(client: TestClient, library: Path) -> None:
    """Every root's presence is asked, local ones too: a disk can be unplugged."""
    sign_in(client, "admin")
    local = add_root(client, library)

    listed = client.get(ROOTS).json()["roots"]

    assert [root["id"] for root in listed] == [local["id"]]
    assert listed[0]["reachable"] is True


def test_a_folder_that_has_gone_says_so(client: TestClient, library: Path, tmp_path: Path) -> None:
    """The same row read with its folder there and then gone, so the field cannot always be True."""
    sign_in(client, "admin")
    add_root(client, library)
    assert client.get(ROOTS).json()["roots"][0]["reachable"] is True

    library.rename(tmp_path / "carried-off")

    gone = client.get(ROOTS).json()["roots"][0]
    assert gone["reachable"] is False
    # The disk answered and the folder is not on it.
    assert gone["presence"] == "missing"


def test_a_folder_on_another_machine_is_still_asked_behind_the_guard(
    client: TestClient, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A share goes through the waited check, so a slow stat answers silent without holding the
    screen."""
    sign_in(client, "admin")
    root = add_root(client, library)
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    write_rows(db_path, [("UPDATE library_roots SET kind = 'nas' WHERE id = ?", (root["id"],))])

    def slowly(_path: Path) -> str:
        time.sleep(SLOWER_THAN_THE_WAIT)
        return "here"

    # The package exports a `router` object, so the module is reached by name.
    monkeypatch.setattr(sys.modules["sift.slices.library_roots.router"], "presence", slowly)

    started = time.monotonic()
    listed = client.get(ROOTS).json()["roots"][0]
    assert listed["reachable"] is False
    # Kept waiting: silent, never missing.
    assert listed["presence"] == "silent"
    assert time.monotonic() - started < SLOWER_THAN_THE_WAIT


def test_a_rescan_can_be_narrowed_to_one_folder(client: TestClient, library: Path) -> None:
    """A rescan can be narrowed to one folder."""
    sign_in(client, "admin")
    root = add_root(client, library)
    top = client.get(FOLDERS).json()["folders"][0]

    response = client.post(f"{ROOTS}/{root['id']}/rescan", params={"folder_id": top["id"]})

    assert response.status_code == 202, response.text
    assert response.json()["job_id"]


def test_a_rescan_of_a_folder_that_is_not_there_is_a_404(client: TestClient, library: Path) -> None:
    """A rescan of a missing folder is a 404 up front; the job checks again regardless."""
    sign_in(client, "admin")
    root = add_root(client, library)

    response = client.post(
        f"{ROOTS}/{root['id']}/rescan", params={"folder_id": "01HX0000000000000000000009"}
    )

    assert response.status_code == 404


def test_a_rescan_of_a_folder_in_another_root_is_a_404(client: TestClient, tmp_path: Path) -> None:
    """A folder from another library is not one of this root's."""
    sign_in(client, "admin")
    first = tmp_path / "one"
    second = tmp_path / "two"
    for one in (first, second):
        one.mkdir()
    root_one = add_root(client, first)
    root_two = add_root(client, second)
    theirs = next(
        folder
        for folder in client.get(FOLDERS).json()["folders"]
        if folder["root_id"] == root_two["id"]
    )

    response = client.post(f"{ROOTS}/{root_one['id']}/rescan", params={"folder_id": theirs["id"]})

    assert response.status_code == 404


# --- the two piles of files Sift would not take ------------------------------------------------
# Moved files and left-alone files are on one screen: they have opposite answers to "where is my
# file".

QUARANTINE = "/api/library/quarantine"


def _quarantine_a_file(client: TestClient, name: str, **note: object) -> Path:
    """Put a file in the quarantine directory with the note the ingress gate writes beside it."""
    import json

    from sift.kernel.ingress import NOTE_SUFFIX

    settings = client.app.state.settings  # type: ignore[attr-defined]
    directory: Path = settings.quarantine_dir
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_bytes(b"refused bytes")
    target.with_name(target.name + NOTE_SUFFIX).write_text(json.dumps(note), encoding="utf-8")
    return target


def _refuse_a_file_in_place(client: TestClient, root_id: str, rel_path: str) -> None:
    """The row a scan writes for a file it will not take. Callers run with no workers, since a scan
    that misses the file forgets the refusal."""
    write_rows(
        client.app.state.database.path,  # type: ignore[attr-defined]
        [
            (
                "INSERT INTO scan_rejections "
                "(id, root_id, rel_path, size_bytes, mtime_ns, reason, detected, "
                " first_seen_at, last_seen_at) "
                "VALUES ('01HX0000000000000000000097', ?, ?, 34, 0, "
                "'not_decodable', 'text', 0, 0)",
                (root_id, rel_path),
            )
        ],
    )


def test_both_piles_are_shown_and_are_labelled_apart(
    idle_client: TestClient, library: Path
) -> None:
    """Both piles are shown and labelled apart: only a moved file can be deleted from here."""
    client = idle_client
    sign_in(client, "admin")
    root = str(add_root(client, library)["id"])
    _refuse_a_file_in_place(client, root, "clips/left-alone.mp4")
    _quarantine_a_file(
        client,
        "abc-moved.bin",
        original_name="invoice.pdf",
        reason="not_decodable",
        detected="pdf",
        origin="download",
        size_bytes=34,
        quarantined_at=1_700_000_000,
    )

    shown = client.get(QUARANTINE).json()

    (moved,) = shown["moved"]
    assert moved["id"] == "abc-moved.bin"
    assert moved["original_name"] == "invoice.pdf", "what it was called before Sift renamed it"
    assert moved["reason"] == "not_decodable" and moved["detected"] == "pdf"
    assert moved["explained"] is True

    (left,) = shown["left_alone"]
    assert left["root_id"] == root and left["root_name"] == library.name
    assert [one["rel_path"] for one in left["rejections"]] == ["clips/left-alone.mp4"]

    assert shown["keep_days"] == 0, "the shipped retention rule is OFF (see DEFAULT_KEEP_DAYS)"


def test_a_folders_refusals_are_a_page_with_the_whole_count_beside_it(
    idle_client: TestClient, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder's refusals come a page at a time, with the whole count beside them."""
    import importlib

    client = idle_client
    sign_in(client, "admin")
    root = str(add_root(client, library)["id"])
    _refuse_a_file_in_place(client, root, "clips/one.mp4")
    # The helper's row has a fixed id, so the second refusal is written beside it.
    write_rows(
        client.app.state.database.path,  # type: ignore[attr-defined]
        [
            (
                "INSERT INTO scan_rejections "
                "(id, root_id, rel_path, size_bytes, mtime_ns, reason, detected, "
                " first_seen_at, last_seen_at) "
                "VALUES ('01HX0000000000000000000098', ?, 'clips/two.mp4', 34, 0, "
                "'not_decodable', 'text', 0, 0)",
                (root,),
            )
        ],
    )
    monkeypatch.setattr(
        importlib.import_module("sift.slices.library_roots.router"), "REJECTIONS_PAGE", 1
    )

    (left,) = client.get(QUARANTINE).json()["left_alone"]

    assert [one["rel_path"] for one in left["rejections"]] == ["clips/one.mp4"]
    assert left["rejections_total"] == 2


def test_a_folder_that_refused_nothing_is_not_listed_at_all(
    client: TestClient, library: Path
) -> None:
    """A folder that refused nothing has no row."""
    sign_in(client, "admin")
    add_root(client, library)

    assert client.get(QUARANTINE).json()["left_alone"] == []


def test_a_file_with_no_note_is_listed_and_says_the_reason_is_not_known(
    client: TestClient,
) -> None:
    """A quarantined file with no note is listed, saying the reason is not known."""
    sign_in(client, "admin")
    directory = client.app.state.settings.quarantine_dir  # type: ignore[attr-defined]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "no-note.bin").write_bytes(b"refused bytes")

    (moved,) = client.get(QUARANTINE).json()["moved"]

    assert moved["explained"] is False and moved["reason"] == "unknown"


def test_the_stored_retention_rule_is_what_the_screen_reports(client: TestClient) -> None:
    """The screen reports the retention rule from the reader the job uses."""
    sign_in(client, "admin")
    set_app_setting(
        client.app.state.database.path,  # type: ignore[attr-defined]
        "quarantine.keep_days",
        "7",
    )

    assert client.get(QUARANTINE).json()["keep_days"] == 7


def test_a_quarantined_file_is_deleted_with_its_note(client: TestClient) -> None:
    """A quarantined file is deleted with its note."""
    sign_in(client, "admin")
    target = _quarantine_a_file(client, "abc-moved.bin", reason="not_decodable")
    from sift.kernel.ingress import NOTE_SUFFIX

    assert client.delete(f"{QUARANTINE}/abc-moved.bin").status_code == 204

    assert not target.exists()
    assert not target.with_name(target.name + NOTE_SUFFIX).exists()
    assert client.get(QUARANTINE).json()["moved"] == []


def _decided(client: TestClient) -> list[dict[str, Any]]:
    """Each receipt on the Decisions tab of History, newest first."""
    page: dict[str, Any] = client.get("/api/ledger", params={"decisions": "true"}).json()
    return [one["receipt"] for one in page["items"]]


def test_deleting_a_quarantined_file_is_written_into_the_record(client: TestClient) -> None:
    """Deleting a quarantined file is recorded as a decision that cannot be undone, written after
    the unlink so it never claims a deletion that failed."""
    sign_in(client, "admin")
    _quarantine_a_file(client, "abc-moved.bin", reason="not_decodable")

    assert client.delete(f"{QUARANTINE}/abc-moved.bin").status_code == 204

    (written,) = _decided(client)
    assert written["queue"] == "quarantine"
    assert "abc-moved.bin" in written["detail"]
    assert "cannot be undone" in written["detail"]


def test_a_delete_that_found_nothing_writes_no_record(client: TestClient) -> None:
    """A delete that found nothing writes no record."""
    sign_in(client, "admin")

    assert client.delete(f"{QUARANTINE}/never-existed.bin").status_code == 404

    assert _decided(client) == []


def test_deleting_a_quarantined_file_that_is_not_there_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")

    assert client.delete(f"{QUARANTINE}/never-existed.bin").status_code == 404


def test_a_name_reaching_out_of_the_quarantine_directory_deletes_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    """A name carrying a separator deletes nothing outside the quarantine directory."""
    sign_in(client, "admin")
    outside = tmp_path / "not-yours.bin"
    outside.write_bytes(b"somebody else's file")

    assert client.delete(f"{QUARANTINE}/..%2F..%2Fnot-yours.bin").status_code in (404, 405)

    assert outside.exists(), "and the file outside the directory is untouched"


def test_a_guest_is_shown_neither_pile(client: TestClient) -> None:
    """A guest is shown neither pile: both name files on the server's disk."""
    sign_in(client, "guest")

    assert client.get(QUARANTINE).status_code == 403
    assert client.delete(f"{QUARANTINE}/anything.bin").status_code == 403


def test_forgetting_a_refusal_lets_the_next_scan_look_at_the_file_again(
    idle_client: TestClient, library: Path
) -> None:
    """Forgetting a refusal puts the file back in front of the gate, not past it."""
    client = idle_client
    sign_in(client, "admin")
    root = str(add_root(client, library)["id"])
    _refuse_a_file_in_place(client, root, "clips/broken.mp4")

    response = client.post(
        f"/api/library/roots/{root}/rejections/allow", json={"rel_path": "clips/broken.mp4"}
    )

    assert response.status_code == 204
    assert [
        one for one in client.get(QUARANTINE).json()["left_alone"] if one["rejections"]
    ] == [], "the refusal is forgotten, so the folder has nothing left alone in it"


def test_forgetting_a_refusal_is_written_into_the_record(
    idle_client: TestClient, library: Path
) -> None:
    """Forgetting a refusal is recorded: which file, which folder, what happens next."""
    client = idle_client
    sign_in(client, "admin")
    root = str(add_root(client, library)["id"])
    _refuse_a_file_in_place(client, root, "clips/broken.mp4")

    client.post(
        f"/api/library/roots/{root}/rejections/allow", json={"rel_path": "clips/broken.mp4"}
    )

    (written,) = _decided(client)
    assert written["queue"] == "skipped"
    assert "clips/broken.mp4" in written["detail"]
    assert "Nothing was moved or deleted" in written["detail"]


def test_a_decision_about_a_file_that_was_never_imported_names_nothing(
    idle_client: TestClient, library: Path
) -> None:
    """A decision about a never-imported file names no subject. The root id is not a folder id, so
    it is not written as one."""
    client = idle_client
    sign_in(client, "admin")
    root = str(add_root(client, library)["id"])
    _refuse_a_file_in_place(client, root, "clips/broken.mp4")

    client.post(
        f"/api/library/roots/{root}/rejections/allow", json={"rel_path": "clips/broken.mp4"}
    )

    db = client.app.state.database.path  # type: ignore[attr-defined]
    with sqlite3.connect(db) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        # The allow's own decision; a task run names its run instead.
        linked = connection.execute(
            "SELECT s.decision_id FROM workbench_decision_subjects s"
            " JOIN workbench_decisions d ON d.id = s.decision_id WHERE d.queue = 'skipped'"
        ).fetchall()
    assert linked == []
    assert _decided(client), "and yet it was recorded"


def test_forgetting_a_refusal_in_a_folder_that_is_not_there_is_a_404(client: TestClient) -> None:
    """A 204 would read as forgotten for a folder that does not exist."""
    sign_in(client, "admin")

    never = "01HX0000000000000000000099"
    response = client.post(
        f"/api/library/roots/{never}/rejections/allow", json={"rel_path": "clips/broken.mp4"}
    )

    assert response.status_code == 404


def test_a_guest_cannot_forget_a_refusal(idle_client: TestClient, library: Path) -> None:
    client = idle_client
    sign_in(client, "admin")
    root = str(add_root(client, library)["id"])
    _refuse_a_file_in_place(client, root, "clips/broken.mp4")
    sign_in(client, "guest")

    response = client.post(
        f"/api/library/roots/{root}/rejections/allow", json={"rel_path": "clips/broken.mp4"}
    )

    assert response.status_code == 403


# --- when the machine refuses --------------------------------------------------------------------
# The call is made to fail, since Windows ignores the `chmod` bits; each guard fails closed.


def _refuses(*_args: object, **_kwargs: object) -> object:
    raise PermissionError(13, "Permission denied")


async def test_a_folder_the_machine_will_not_answer_about_counts_as_not_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused stat counts as not there: the guard fails closed."""
    monkeypatch.setattr(os, "stat", _refuses)

    assert await _reachable(tmp_path) is False


async def test_a_local_folder_the_machine_will_not_answer_about_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused local stat is silent, not absent."""
    monkeypatch.setattr(os, "stat", _refuses)

    assert presence(tmp_path) == "silent"


# --- arranging folders from inside Sift -------------------------------------------------------
# Each refusal is a 400 with the kernel's sentence, or a 404.


def add_managed_root(client: TestClient, path: Path) -> dict[str, object]:
    """A library, which is all arranging needs."""
    response = client.post(ROOTS, json={"abs_path": str(path)})
    assert response.status_code == 201, response.text
    return dict(response.json())


def top_folder_of(client: TestClient, root_id: str) -> str:
    """The folder row standing for a library, which is where a first folder goes."""
    folders = client.get(FOLDERS, params={"root": root_id}).json()["folders"]
    assert folders, "a library has a folder row standing for itself from the moment it is added"
    return str(folders[0]["id"])


def test_an_admin_makes_a_folder_and_it_is_on_the_disk(client: TestClient, library: Path) -> None:
    """A folder made is on the disk and in the tree at the same time."""
    sign_in(client, "admin")
    root = add_managed_root(client, library)

    made = client.post(
        FOLDERS, json={"parent_id": top_folder_of(client, str(root["id"])), "name": "Holidays"}
    )

    assert made.status_code == 201, made.text
    assert made.json()["rel_path"] == "Holidays"
    assert (library / "Holidays").is_dir()


def test_a_folder_on_the_disk_no_scan_recorded_is_placed_rather_than_refused(
    client: TestClient, library: Path
) -> None:
    """An empty folder no scan recorded is placed rather than refused; placed twice it is the same
    folder, and a name nothing holds is made."""
    sign_in(client, "admin")
    root = add_managed_root(client, library)
    (library / "Inbox").mkdir()
    top = top_folder_of(client, str(root["id"]))

    refused = client.post(FOLDERS, json={"parent_id": top, "name": "Inbox"})
    placed = client.post(f"{FOLDERS}/placed", json={"parent_id": top, "name": "Inbox"})
    again = client.post(f"{FOLDERS}/placed", json={"parent_id": top, "name": "Inbox"})
    made = client.post(f"{FOLDERS}/placed", json={"parent_id": top, "name": "Holidays"})

    assert refused.status_code == 400
    assert placed.status_code == 200, placed.text
    assert placed.json()["rel_path"] == "Inbox"
    assert again.json()["id"] == placed.json()["id"]
    assert made.status_code == 200 and made.json()["rel_path"] == "Holidays"
    assert (library / "Holidays").is_dir()


def test_placing_a_folder_where_a_file_has_the_name_is_refused_in_words(
    client: TestClient, library: Path
) -> None:
    sign_in(client, "admin")
    root = add_managed_root(client, library)
    (library / "Inbox").write_bytes(b"")
    top = top_folder_of(client, str(root["id"]))

    refused = client.post(f"{FOLDERS}/placed", json={"parent_id": top, "name": "Inbox"})
    gone = client.post(f"{FOLDERS}/placed", json={"parent_id": "no-such-folder", "name": "Inbox"})
    unnamed = client.post(f"{FOLDERS}/placed", json={"parent_id": top, "name": "no/slashes"})

    assert refused.status_code == 400
    assert '"Inbox"' in refused.json()["detail"]
    assert gone.status_code == 400 and unnamed.status_code == 400


def test_making_a_folder_in_a_library_sift_may_only_read_is_refused_in_words(
    client: TestClient, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Making a folder where the disk will not let Sift write is refused in words. The disk is made
    to say no, since `chmod` is ignored on Windows."""
    sign_in(client, "admin")
    root = add_root(client, library)

    def refuse(_candidate: Path) -> None:
        raise NotWritable(
            "This folder is read-only, so nothing can change anything in it \u2014 not Sift, and "
            "not any other program."
        )

    monkeypatch.setattr(library_write, "check_folder_writable", refuse)

    refused = client.post(
        FOLDERS, json={"parent_id": top_folder_of(client, str(root["id"])), "name": "Holidays"}
    )

    assert refused.status_code == 400
    assert "read-only" in refused.json()["detail"]
    assert "Holidays" not in [entry.name for entry in library.iterdir()]


def test_a_folder_is_renamed_on_the_disk_and_keeps_its_id(
    client: TestClient, library: Path
) -> None:
    """A renamed folder keeps its id, which shares and rules are written on."""
    sign_in(client, "admin")
    root = add_managed_root(client, library)
    made = client.post(
        FOLDERS, json={"parent_id": top_folder_of(client, str(root["id"])), "name": "Holidays"}
    ).json()

    changed = client.patch(f"{FOLDERS}/{made['id']}", json={"name": "Trips"})

    assert changed.status_code == 200, changed.text
    assert changed.json()["id"] == made["id"]
    assert changed.json()["rel_path"] == "Trips"
    assert (library / "Trips").is_dir()
    assert not (library / "Holidays").exists()


def test_renaming_a_folder_to_something_that_is_not_a_name_is_refused_in_words(
    client: TestClient, library: Path
) -> None:
    """A name that is not a filename is refused with the kernel's sentence, not a 500."""
    sign_in(client, "admin")
    root = add_managed_root(client, library)
    made = client.post(
        FOLDERS, json={"parent_id": top_folder_of(client, str(root["id"])), "name": "Holidays"}
    ).json()

    refused = client.patch(f"{FOLDERS}/{made['id']}", json={"name": "no/slashes"})

    assert refused.status_code == 400
    assert (library / "Holidays").is_dir()


def test_a_library_is_told_where_it_moved_to(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """Saying a library moved changes one stored path; nothing under it is re-read."""
    sign_in(client, "admin")
    root = add_root(client, library)
    moved = tmp_path / "moved"
    library.rename(moved)

    answer = client.post(f"{ROOTS}/{root['id']}/moved", json={"abs_path": str(moved)})

    assert answer.status_code == 200, answer.text
    assert answer.json()["id"] == root["id"]
    assert answer.json()["name"] == "moved"


def test_telling_sift_a_library_that_is_not_there_has_moved_is_a_404(
    client: TestClient, tmp_path: Path
) -> None:
    """A real folder with an id no library has is a 404."""
    sign_in(client, "admin")
    somewhere = tmp_path / "somewhere"
    somewhere.mkdir()

    answer = client.post(
        f"{ROOTS}/01HX000000000000000000GONE/moved", json={"abs_path": str(somewhere)}
    )

    assert answer.status_code == 404


def test_telling_sift_a_library_moved_somewhere_that_is_not_there_is_refused_in_words(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """A move is refused in words by the same checks adding a library makes."""
    sign_in(client, "admin")
    root = add_root(client, library)

    answer = client.post(
        f"{ROOTS}/{root['id']}/moved", json={"abs_path": str(tmp_path / "never-existed")}
    )

    assert answer.status_code == 400


@pytest.fixture
def statements(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every statement run from here on, by name."""
    seen: list[str] = []
    real = db_module._judged

    @contextmanager
    def counted(stage: str, statement: Any, *rest: Any, **options: Any) -> Iterator[Any]:
        seen.append(db_module.statement_name(statement))
        with real(stage, statement, *rest, **options) as timing:
            yield timing

    monkeypatch.setattr(db_module, "_judged", counted)
    return seen


def test_the_quarantine_reads_every_folder_in_the_same_number_of_statements(
    idle_client: TestClient, tmp_path: Path, statements: list[str]
) -> None:
    """Three times the folders, each refusing files, and not one statement more."""
    client = idle_client
    sign_in(client, "admin")
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    made: list[str] = []

    def read_after(more: int) -> int:
        for _ in range(more):
            directory = tmp_path / f"refusing-{len(made)}"
            directory.mkdir()
            answer = client.post(ROOTS, json={"abs_path": str(directory), "scan": False})
            root = str(answer.json()["id"])
            made.append(root)
            write_rows(
                db_path,
                [
                    (
                        "INSERT INTO scan_rejections (id, root_id, rel_path, size_bytes, mtime_ns,"
                        " reason, detected, first_seen_at, last_seen_at)"
                        " VALUES (?, ?, ?, 12, 0, 'not_decodable', 'text', 0, 0)",
                        (new_id(), root, name),
                    )
                    for name in ("b/two.mp4", "a/one.mp4")
                ],
            )
        statements.clear()
        answer = client.get(QUARANTINE)
        assert answer.status_code == 200
        piles = {one["root_id"]: one for one in answer.json()["left_alone"]}
        assert set(piles) == set(made)
        for pile in piles.values():
            assert [one["rel_path"] for one in pile["rejections"]] == ["a/one.mp4", "b/two.mp4"]
            assert pile["rejections_total"] == 2
        return len(statements)

    few = read_after(2)
    assert read_after(4) == few
