# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workbench endpoints, over HTTP, against a real application.

Run this way rather than against the service because what is asserted here is the router's business
and not the service's: who is refused, that the badge is its own cheap answer, and that a request
naming a decision nobody made is turned down rather than quietly doing nothing.

Deciding what the library says about the people in it is admin work, whole. A guest is refused
outright rather than handed an empty board: a board answered with nothing still says which queues
exist and how big each one is, and those are facts about the library.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.main import create_app
from sift.testing.auth import establish_session

pytestmark = pytest.mark.integration

PASSWORD = "A-Workbench-Test-Passw0rd!"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as running:
        yield running


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def write(path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    """Seed through a connection of the test's own.

    The client drives the application on its own event loop, and a write issued from this loop
    would meet a lock held on that one.
    """

    async def run() -> None:
        database = Database(path, readers=1)
        await database.connect()
        try:
            for sql, params in statements:
                await database.execute(sql, params)
        finally:
            await database.close()

    asyncio.run(run())


def sign_in(client: TestClient, role: str) -> str:
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"bench-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def seed_decision(
    client: TestClient,
    *,
    queue: str = "folders",
    payload: str = "{}",
    title: str = "Reya Solberg — 47 files",
) -> str:
    """One receipt in the record.

    `title` is a parameter because a run of receipts saying the same thing is drawn as ONE row.
    See `Store.folded`. A test that wants three rows has to seed three different sentences, which is
    what three decisions about three folders look like.
    """
    decision_id = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO workbench_decisions"
                " (id, queue, user_id, title, detail, payload, decided_at, reversed_at)"
                " VALUES (?, ?, NULL, ?, ?, ?, 0, NULL)",
                (decision_id, queue, title, "47 files filed.", payload),
            )
        ],
    )
    return decision_id


# --- the refusals -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/workbench"),
        ("get", "/api/ledger?decisions=true"),
    ],
)
def test_a_guest_is_refused_rather_than_shown_an_empty_board(
    client: TestClient, method: str, path: str
) -> None:
    sign_in(client, "guest")

    assert getattr(client, method)(path).status_code == 403


def test_a_guest_cannot_take_a_decision_back(client: TestClient) -> None:
    decision_id = seed_decision(client)
    sign_in(client, "guest")

    assert client.post(f"/api/workbench/decisions/{decision_id}/undo").status_code == 403


# --- the board --------------------------------------------------------------------------------


def test_the_board_lists_what_registered(client: TestClient) -> None:
    sign_in(client, "admin")

    answer = client.get("/api/workbench")

    assert answer.status_code == 200
    body: dict[str, Any] = answer.json()
    # Folder-led attribution needs nothing switched on, so it is always there. The face queues are
    # absent on an install where recognition has never been turned on, which is this one.
    assert "folders" in {one["name"] for one in body["queues"]}


def test_the_board_of_named_queues_answers_for_those_alone(client: TestClient) -> None:
    """What a queue's page asks for when the library moves: its own queues, not every survey."""
    sign_in(client, "admin")

    body: dict[str, Any] = client.get(
        "/api/workbench", params=[("only", "folders"), ("only", "no-such-queue")]
    ).json()

    assert [one["name"] for one in body["queues"]] == ["folders"]


def test_every_queue_says_what_deciding_one_means(client: TestClient) -> None:
    """On screen, never in a tooltip. A bulk decision offered without saying what it does is a leap
    of faith, and this screen's decisions are the largest in the application."""
    sign_in(client, "admin")

    body: dict[str, Any] = client.get("/api/workbench").json()

    for queue in body["queues"]:
        assert queue["decision"].strip()
        assert queue["title"].strip()


def test_every_queue_sends_both_wordings_of_what_its_number_counts(client: TestClient) -> None:
    """The phrase for many and the phrase for one, together, on every card the board draws.

    The card leads with the figure, so the phrase beside it is what says what was counted, and a
    pile comes down to its LAST item on its way to being empty, which is the state it is looked at
    in most, and "1 shoots to agree" or "1 groups that look alike" is wrong.

    Asserted over the wire rather than over the survey, and that distinction is not a nicety: the
    shell copies each field onto `QueueView` by hand, so a survey can carry both wordings while the
    reply carries one, and `test_board.py`'s survey test stays green throughout.
    """
    sign_in(client, "admin")

    body: dict[str, Any] = client.get("/api/workbench").json()

    assert body["queues"], "a board with no queues would pass every rule by having nothing to check"
    for queue in body["queues"]:
        assert queue["verb"].strip(), f"{queue['name']}: nothing says what the number counts"
        assert queue["verb_one"].strip(), f"{queue['name']}: nothing says what ONE of them is"
        assert queue["verb_one"] != queue["verb"], (
            f"{queue['name']}: a pile of one would still read in the plural"
        )


def test_the_board_carries_no_record_of_its_own(client: TestClient) -> None:
    """What was decided is History's, narrowed to Decisions: one place to look. The board is the
    work waiting, and the old record's address answers nothing of its own."""
    seed_decision(client)
    sign_in(client, "admin")

    body: dict[str, Any] = client.get("/api/workbench").json()
    decided = client.get("/api/ledger", params={"decisions": "true"}).json()

    assert "decisions" not in body and "decided" not in body
    assert [one["receipt"]["title"] for one in decided["items"]] == ["Reya Solberg — 47 files"]
    assert client.get("/api/workbench/decisions").status_code in (404, 405)


# --- taking one back ---------------------------------------------------------------------------


def test_a_decision_nobody_made_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")

    answer = client.post("/api/workbench/decisions/01HX0000000000000000000000/undo")

    assert answer.status_code == 404


def test_a_record_this_version_cannot_read_says_nothing_was_put_back(client: TestClient) -> None:
    """Rather than failing the request. A record can outlive the version that wrote it, and a
    payload missing a key it once had is a record that cannot be reversed, which is a different
    thing from the undo being broken, and reads differently to whoever pressed it."""
    decision_id = seed_decision(client, payload="{}")
    sign_in(client, "admin")

    answer = client.post(f"/api/workbench/decisions/{decision_id}/undo")

    assert answer.status_code == 200
    assert answer.json() == {"undone": False, "put_back": 0, "of": 1, "said": None}


def test_a_decision_from_a_queue_this_version_lost_is_a_404(client: TestClient) -> None:
    decision_id = seed_decision(client, queue="something-removed")
    sign_in(client, "admin")

    answer = client.post(f"/api/workbench/decisions/{decision_id}/undo")

    assert answer.status_code == 404
