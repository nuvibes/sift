# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two endpoints, over HTTP, against a real application.

Run this way rather than against the tidyings themselves because what is being asserted here is the
router's business and not theirs: who is refused, that looking removes nothing, and that a request
naming something that does not exist is turned down rather than quietly doing nothing.

Both routes describe the library as a whole and one of them destroys part of it, so both are
admin-only. The client hides the Maintenance section from a guest as a courtesy; these checks are
what actually stop them.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.wiring import QUEUE, part_of_app
from sift.main import create_app
from sift.slices.tidy.jobs import TIDY_SURVEY
from sift.testing.auth import establish_session

pytestmark = pytest.mark.integration

_EPOCH = 1_700_000_000
PASSWORD = "A-Tidy-Test-Passw0rd!"


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
        db_path(client), role=role, username=f"tidy-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def seed_stranded_asset(client: TestClient) -> str:
    """An asset in no folder at all, which is what removing a library folder leaves behind."""
    asset_id = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'image', ?)",
                (asset_id, f"digest-{asset_id}", _EPOCH),
            )
        ],
    )
    return asset_id


def counted(payload: dict[str, object], name: str) -> int | None:
    leftovers = payload["leftovers"]
    assert isinstance(leftovers, list)
    count: int | None = next(one["count"] for one in leftovers if one["name"] == name)
    return count


def surveyed_at(payload: dict[str, object], name: str) -> int | None:
    leftovers = payload["leftovers"]
    assert isinstance(leftovers, list)
    when: int | None = next(one["surveyed_at"] for one in leftovers if one["name"] == name)
    return when


def cache_dir(client: TestClient) -> Path:
    return Path(get_settings().cache_dir)


# --- the refusals -----------------------------------------------------------------------------


def test_a_guest_cannot_see_what_has_built_up(client: TestClient) -> None:
    """The survey counts what is in the library, which is as revealing as the library."""
    sign_in(client, "guest")

    assert client.get("/api/tidy").status_code == 403


def test_a_guest_cannot_run_a_tidying(client: TestClient) -> None:
    seed_stranded_asset(client)
    sign_in(client, "guest")

    assert client.post("/api/tidy/stranded-assets").status_code == 403

    sign_in(client, "admin")
    assert counted(client.get("/api/tidy").json(), "stranded-assets") == 1


def test_signed_out_is_refused_before_anything_is_counted(client: TestClient) -> None:
    assert client.get("/api/tidy").status_code == 401


def test_a_name_that_does_not_exist_is_turned_down(client: TestClient) -> None:
    """Rather than answering that nothing was removed, which reads as success."""
    sign_in(client, "admin")

    answer = client.post("/api/tidy/no-such-tidying")

    assert answer.status_code == 404


# --- what an admin sees ------------------------------------------------------------------------


def test_the_survey_names_everything_registered(client: TestClient) -> None:
    sign_in(client, "admin")

    payload = client.get("/api/tidy").json()

    names = {one["name"] for one in payload["leftovers"]}
    assert names == {
        "stranded-assets",
        "settled-failures",
        "leftover-derivatives",
        "repackaged-copies",
        "leftover-face-pictures",
        "orphaned-descriptions",
        "superseded-descriptions",
        "stranded-samples",
    }
    # Every entry has to explain itself: this is the text in front of an irreversible button.
    for one in payload["leftovers"]:
        assert one["title"]
        assert one["detail"]


def test_a_count_that_reads_the_disk_is_not_taken_on_a_look(client: TestClient) -> None:
    """The screen opens without reading the cache directory: a costly count is the last survey's,
    and before there has been one it is no count at all rather than a zero."""
    stray = cache_dir(client) / "ab" / "cd" / "thumb.jpg"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"orphaned")
    sign_in(client, "admin")

    payload = client.get("/api/tidy").json()

    assert counted(payload, "leftover-derivatives") is None
    assert surveyed_at(payload, "leftover-derivatives") is None
    assert payload["surveying"] is False
    assert counted(payload, "stranded-assets") == 0, "a cheap count is still taken now"


def test_asking_for_a_survey_queues_the_count(client: TestClient) -> None:
    sign_in(client, "admin")

    answer = client.post("/api/tidy/survey")

    assert answer.status_code == 200
    assert answer.json() == {"queued": True}


def test_the_survey_asked_for_is_the_one_the_screen_reads_back(client: TestClient) -> None:
    """The count runs in the background and lands where the screen reads it: a costly leftover
    that had no count at all has one, with the moment it was taken, once the job has run."""
    stray = cache_dir(client) / "ab" / "cd" / "thumb.jpg"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"orphaned")
    sign_in(client, "admin")
    assert client.post("/api/tidy/survey").json() == {"queued": True}

    deadline = time.monotonic() + 30
    payload = client.get("/api/tidy").json()
    while surveyed_at(payload, "leftover-derivatives") is None:
        assert time.monotonic() < deadline, "the survey never ran"
        time.sleep(0.05)
        payload = client.get("/api/tidy").json()

    assert counted(payload, "leftover-derivatives") == 1
    assert payload["surveying"] is False


def test_a_survey_already_under_way_is_not_doubled(client: TestClient) -> None:
    """A second press while the count is going is answered, not queued behind the first: the
    directory would be read twice for one number."""
    sign_in(client, "admin")
    portal = client.portal
    assert portal is not None
    # Waiting on the queue's own terms: a job whose time has not come is live and not claimable,
    # so the count is under way for as long as this test needs it to be.
    queue = part_of_app(client.app, QUEUE)  # type: ignore[arg-type]
    portal.call(functools.partial(queue.enqueue, TIDY_SURVEY, {}, run_after=2**31 - 1))

    assert client.get("/api/tidy").json()["surveying"] is True
    assert client.post("/api/tidy/survey").json() == {"queued": False}


def test_a_guest_cannot_ask_for_a_survey(client: TestClient) -> None:
    sign_in(client, "guest")
    assert client.post("/api/tidy/survey").status_code == 403


def test_running_a_costly_tidying_keeps_a_fresh_count(client: TestClient) -> None:
    """What the screen reads back after a run is what is left, not what the old survey said."""
    stray = cache_dir(client) / "ab" / "cd" / "thumb.jpg"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"orphaned")
    write(
        db_path(client),
        [
            (
                "INSERT INTO tidy_surveys (name, count, frees_bytes, surveyed_at)"
                " VALUES ('leftover-derivatives', 5, 50, 1)",
                (),
            )
        ],
    )
    sign_in(client, "admin")
    assert counted(client.get("/api/tidy").json(), "leftover-derivatives") == 5

    answer = client.post("/api/tidy/leftover-derivatives").json()

    assert answer["removed"] == 1
    assert counted(answer, "leftover-derivatives") == 0
    when = surveyed_at(answer, "leftover-derivatives")
    assert when is not None and when > 1
    assert not stray.exists()


def test_looking_twice_removes_nothing(client: TestClient) -> None:
    """The separation between reading and running is the safety property of this screen."""
    seed_stranded_asset(client)
    sign_in(client, "admin")

    client.get("/api/tidy")
    client.get("/api/tidy")

    assert counted(client.get("/api/tidy").json(), "stranded-assets") == 1


def test_running_one_removes_it_and_says_what_is_left(client: TestClient) -> None:
    seed_stranded_asset(client)
    sign_in(client, "admin")

    answer = client.post("/api/tidy/stranded-assets")

    assert answer.status_code == 200
    assert answer.json()["removed"] == 1
    # The fresh survey travels with the result: removing rows strands the files they named, so the
    # numbers move in ways a client could not work out for itself.
    assert counted(answer.json(), "stranded-assets") == 0


def test_running_one_leaves_the_others_alone(client: TestClient) -> None:
    """One at a time, because each is permanent and each is a different decision."""
    seed_stranded_asset(client)
    write(
        db_path(client),
        [
            (
                "INSERT INTO jobs (id, type, state, priority, payload, attempts, max_attempts, "
                "created_at, updated_at) VALUES (?, 'probe', 'failed', 0, '{}', 3, 3, ?, ?)",
                (new_id(), _EPOCH, _EPOCH),
            )
        ],
    )
    sign_in(client, "admin")

    answer = client.post("/api/tidy/settled-failures")

    assert answer.json()["removed"] == 1
    assert counted(answer.json(), "stranded-assets") == 1


# --- settling the database down ---------------------------------------------------------------
#
# Its own route rather than another tidying, and the tests follow that split: a tidying REMOVES
# something and this removes nothing. Every row has to survive it, which is the property worth
# asserting: the rest is a number on a screen.

OPTIMIZE = "/api/tidy/database/optimize"


def test_settling_the_database_keeps_every_row(client: TestClient) -> None:
    """The whole promise. It folds the log back in and re-plans the indexes; nothing goes."""
    sign_in(client, "admin")
    asset_id = seed_stranded_asset(client)

    response = client.post(OPTIMIZE)

    assert response.status_code == 200
    still_there = client.get("/api/tidy").json()
    assert counted(still_there, "stranded-assets") == 1, f"{asset_id} should have survived"


def test_it_reports_what_the_file_took_before_and_after(client: TestClient) -> None:
    """Three numbers, and the third is not simply the difference: a run that ends with a bigger
    file (another writer arrived while it ran) reports nothing freed rather than a negative,
    because "freed -4 kB" is not a thing anybody can read."""
    sign_in(client, "admin")

    body = client.post(OPTIMIZE).json()

    assert body["was_bytes"] > 0, "a booted database is not zero bytes"
    assert body["now_bytes"] > 0
    assert body["freed_bytes"] == max(0, body["was_bytes"] - body["now_bytes"])
    assert body["freed_bytes"] >= 0


def test_running_it_twice_is_harmless(client: TestClient) -> None:
    """There is nothing to fold the second time, and that is an ordinary answer rather than a
    failure: it is the state somebody presses the button to reach."""
    sign_in(client, "admin")

    assert client.post(OPTIMIZE).status_code == 200
    assert client.post(OPTIMIZE).status_code == 200


def test_pressing_it_refreshes_the_statistics_whatever_a_timer_has_just_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The statistics are kept current on their own (at boot, at the end of every
    whole-library pass and daily) and those asks are turned away if one ran a moment ago. That is
    right for a timer and wrong for somebody who has pressed a button and is watching it finish, so
    this one is never refused, and it is the wide form that considers every table.
    """
    asked: list[tuple[str, bool, bool]] = []

    async def note(
        self: Database, *, reason: str, every_table: bool = False, force: bool = False
    ) -> bool:
        asked.append((reason, every_table, force))
        return True

    monkeypatch.setattr(Database, "refresh_statistics", note)
    sign_in(client, "admin")

    assert client.post(OPTIMIZE).status_code == 200
    assert asked == [("tidy", True, True)]


def test_the_log_is_folded_after_the_search_index_is_rebuilt(client: TestClient) -> None:
    """The rebuilt index arrives in the write-ahead log as a copy of the whole index. Folded
    before the rebuild, the log would end as large as the index and the file read as grown."""
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            ("INSERT INTO assets_fts (asset_id, title) VALUES (?, ?)", (new_id(), f"harbour {n}"))
            for n in range(6)
        ],
    )

    assert client.post(OPTIMIZE).status_code == 200

    log = db_path(client).with_name(db_path(client).name + "-wal")
    assert not log.exists() or log.stat().st_size == 0


def test_a_guest_cannot_settle_the_database(client: TestClient) -> None:
    """It holds the database while it runs, so it is work anybody who could call it could make the
    server do."""
    sign_in(client, "guest")

    assert client.post(OPTIMIZE).status_code == 403


def test_settling_the_database_needs_a_session(client: TestClient) -> None:
    assert client.post(OPTIMIZE).status_code == 401


def test_it_is_refused_without_the_csrf_header(client: TestClient) -> None:
    sign_in(client, "admin")
    del client.headers[CSRF_HEADER_NAME]

    assert client.post(OPTIMIZE).status_code == 403


async def test_a_library_with_no_search_index_is_settled_anyway(tmp_path: Path) -> None:
    """The index is a component like any other and an install can boot before it exists.

    Letting a missing table fail the whole run would be pure loss: the other two steps remove
    nothing and are worth doing on their own, so the fold is skipped and said so in the log rather
    than raised.
    """
    from sift.slices.tidy.router import _optimize_search

    database = Database(tmp_path / "no-index.sqlite3", readers=1)
    await database.connect()
    try:
        async with database.write() as connection:
            await _optimize_search(connection)  # there is no assets_fts here at all
    finally:
        await database.close()


def test_the_size_counts_the_write_ahead_log_and_not_the_main_file_alone(tmp_path: Path) -> None:
    """The log is usually the part that grew. Quoting the main file alone would report a run that
    reclaimed a gigabyte as having freed nothing, which is the number the screen shows."""
    from sift.slices.tidy.router import _file_size

    class Somewhere:
        path = tmp_path / "sized.sqlite3"

    Somewhere.path.write_bytes(b"a" * 100)
    main_only = _file_size(Somewhere)  # type: ignore[arg-type]
    Somewhere.path.with_name("sized.sqlite3-wal").write_bytes(b"b" * 250)

    assert main_only == 100
    assert _file_size(Somewhere) == 350  # type: ignore[arg-type]


def test_a_file_that_is_not_there_counts_as_nothing_rather_than_failing(tmp_path: Path) -> None:
    """A database with no log beside it is the ordinary state after a checkpoint, and the size has
    to be readable in exactly the moment the run is trying to report on."""
    from sift.slices.tidy.router import _file_size

    class Nowhere:
        path = tmp_path / "never-made.sqlite3"

    assert _file_size(Nowhere) == 0  # type: ignore[arg-type]
