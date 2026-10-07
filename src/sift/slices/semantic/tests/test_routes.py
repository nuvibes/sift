# SPDX-License-Identifier: AGPL-3.0-or-later
"""The semantic endpoints over HTTP, against the real application.

What spends the machine's time is admin-only (and refused with 409 while the feature is off);
what a search needs is open to anybody signed in. Status answers on every install.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs.worker_pool import registered_alone
from sift.main import create_app
from sift.slices.semantic import settings as semantic_settings
from sift.slices.semantic.jobs import SEMANTIC_FETCH_MODELS
from sift.slices.semantic.service import Readiness, SemanticService
from sift.testing.auth import establish_session
from sift.testing.library import seed_asset, seed_root
from sift.testing.settings import set_app_setting

pytestmark = pytest.mark.integration

PASSWORD = "A-Semantic-Test-Passw0rd!"


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


def sign_in(client: TestClient, role: str) -> str:
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"semantic-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def switch_on(client: TestClient) -> None:
    set_app_setting(db_path(client), semantic_settings.ENABLED_KEY, "true")


# --- who gets through the door ---------------------------------------------------------------


def test_a_guest_is_refused_every_one_of_them(client: TestClient) -> None:
    sign_in(client, "guest")

    assert client.get("/api/semantic/status").status_code == 403
    assert client.post("/api/semantic/models/fetch").status_code == 403
    assert client.delete("/api/semantic/index").status_code == 403


def test_nobody_signed_in_is_refused_every_one_of_them(client: TestClient) -> None:
    """Signed out, a read is 401 and a write is turned away first by the cross-site check."""
    assert client.get("/api/semantic/status").status_code == 401
    assert client.post("/api/semantic/models/fetch").status_code == 403


# --- what the screen reads ---------------------------------------------------------------------


def test_status_answers_on_a_fresh_install(client: TestClient) -> None:
    """Switched off, no models, nothing described, and every one of those is a different fact
    the screen has to be able to tell apart."""
    sign_in(client, "admin")

    body = client.get("/api/semantic/status").json()

    assert body["enabled"] is False
    assert body["ready"] is False
    assert body["installed"] == []
    assert body["described_files"] == 0
    # Off is a decision somebody made, not a fault to report.
    assert body["problem"] is None


def test_status_says_the_models_are_missing_once_it_is_switched_on(client: TestClient) -> None:
    sign_in(client, "admin")
    switch_on(client)

    body = client.get("/api/semantic/status").json()

    assert body["enabled"] is True
    assert body["ready"] is False
    assert "have not been obtained" in body["problem"]


def test_status_counts_the_files_not_read_yet_once_it_can_describe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed_root(
        db_path(client),
        "01HX00000000000000000000R1",
        folder_id="01HX00000000000000000000F1",
        path=tmp_path / "library",
    )
    seed_asset(
        db_path(client),
        "01HX00000000000000000000A1",
        root_id="01HX00000000000000000000R1",
        folder_id="01HX00000000000000000000F1",
        root_path=tmp_path / "library",
        cache_dir=tmp_path / "cache",
    )
    sign_in(client, "admin")
    assert client.get("/api/semantic/status").json()["unread_files"] == 0

    async def can_run(self: SemanticService) -> Readiness:
        return Readiness(supported=True, enabled=True, ready=True, family="stub", device="cpu")

    async def none(self: SemanticService, *_: object) -> int:
        return 0

    monkeypatch.setattr(SemanticService, "readiness", can_run)
    monkeypatch.setattr(SemanticService, "described_count", none)
    monkeypatch.setattr(SemanticService, "waiting_count", none)

    assert client.get("/api/semantic/status").json()["unread_files"] == 1


def test_status_reads_its_settings_once_and_an_admins_count_off_the_library(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pane's settings come from one read, and an admin's waiting files from the library's
    kept count rather than a walk of what they may see."""
    from collections import Counter
    from contextlib import contextmanager
    from typing import Any

    from sift.kernel import db as db_module
    from sift.kernel.content import ContentStore

    sign_in(client, "admin")
    switch_on(client)
    client.get("/api/semantic/status")
    heard: Counter[str] = Counter()
    judged = db_module._judged

    @contextmanager
    def counted(stage: str, statement: Any, *rest: Any, **options: Any) -> Iterator[Any]:
        heard[db_module.statement_name(statement)] += 1
        with judged(stage, statement, *rest, **options) as timing:
            yield timing

    monkeypatch.setattr(db_module, "_judged", counted)
    asked: list[bool] = []
    visible = ContentStore.count_lacking_visible

    async def spied(self: ContentStore, *args: Any, **options: Any) -> Any:
        asked.append(bool(options.get("admin")))
        return await visible(self, *args, **options)

    monkeypatch.setattr(ContentStore, "count_lacking_visible", spied)

    assert client.get("/api/semantic/status").status_code == 200
    assert heard["settings.app_value"] == 0, sorted(heard.items())
    assert sum(n for name, n in heard.items() if name.startswith("select:app_settings")) == 1
    assert asked == [True]


def test_status_names_which_models_and_device_are_chosen(client: TestClient) -> None:
    sign_in(client, "admin")

    body = client.get("/api/semantic/status").json()

    assert body["family"] == "compact"
    assert body["device"] == "cpu"


# --- what it refuses to start ------------------------------------------------------------------


def test_the_models_are_not_fetched_while_the_feature_is_off(client: TestClient) -> None:
    """The one network call the switch exists to prevent."""
    sign_in(client, "admin")

    answer = client.post("/api/semantic/models/fetch")

    assert answer.status_code == 409


def test_fetching_the_models_queues_a_job_once_it_is_on(client: TestClient) -> None:
    sign_in(client, "admin")
    switch_on(client)

    answer = client.post("/api/semantic/models/fetch")

    assert answer.status_code == 200
    assert answer.json()["job_id"]
    # A second press joins it: two downloads would append to one partial file.
    again = client.post("/api/semantic/models/fetch", params={"again": "true"})
    assert again.json()["job_id"] == answer.json()["job_id"]
    assert SEMANTIC_FETCH_MODELS in registered_alone()


# --- removing the index --------------------------------------------------------------------------


def test_the_index_can_be_removed_even_with_the_feature_off(client: TestClient) -> None:
    """Clearing up what an earlier decision left behind is exactly what somebody does after
    switching it off, so this one does not refuse."""
    sign_in(client, "admin")

    answer = client.delete("/api/semantic/index")

    assert answer.status_code == 200
    assert answer.json()["removed_frames"] == 0
    # Queued: on a large library removing it is minutes of writes, never one request's.
    assert answer.json()["job_id"]


# --- what looks like this, and who may ask ---------------------------------------------------


def test_anybody_signed_in_may_ask_what_looks_like_a_file(client: TestClient) -> None:
    """The one route here that is not admin-only. It is a way of browsing rather than a control
    over the install, and refusing a guest would make the feature admin-only in effect."""
    sign_in(client, "guest")

    answer = client.get("/api/assets/01HX0000000000000000000099/similar")

    assert answer.status_code == 200


def test_a_file_nothing_knows_about_answers_nothing_rather_than_refusing(
    client: TestClient,
) -> None:
    """The same answer a file they may not see gives. A refusal that differs from an empty answer
    is a way to ask whether a file exists."""
    sign_in(client, "admin")

    body = client.get("/api/assets/01HX0000000000000000000099/similar").json()

    assert body["items"] == []


def test_the_answer_says_which_of_the_two_ways_found_it(client: TestClient) -> None:
    """They are not interchangeable, and a screen that drew them identically would have somebody
    comparing "nearly the same picture" against "a model thinks these resemble each other"."""
    sign_in(client, "admin")

    body = client.get("/api/assets/01HX0000000000000000000099/similar").json()

    assert body["tier"] in {"looks", "matches"}


def test_anybody_signed_in_may_ask_whether_it_can_search_by_meaning(client: TestClient) -> None:
    """What the search box needs to decide whether to offer a control. One boolean, never why."""
    sign_in(client, "guest")

    answer = client.get("/api/semantic/available")

    assert answer.status_code == 200
    assert answer.json() == {"available": False}


def test_nobody_signed_in_is_not_told_whether_it_can(client: TestClient) -> None:
    assert client.get("/api/semantic/available").status_code == 401


def test_anybody_signed_in_may_ask_how_much_has_been_described(client: TestClient) -> None:
    """Anybody signed in may ask how much is described; an unready install answers two zeroes."""
    sign_in(client, "guest")

    answer = client.get("/api/semantic/coverage")

    assert answer.status_code == 200
    assert answer.json() == {"described": 0, "library": 0}


def test_an_install_that_can_run_the_feature_is_told_the_scoped_counts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ready install answers the service's scoped counts."""

    async def can_run(self: SemanticService) -> Readiness:
        return Readiness(supported=True, enabled=True, ready=True, family="stub", device="cpu")

    monkeypatch.setattr(SemanticService, "readiness", can_run)
    sign_in(client, "guest")

    answer = client.get("/api/semantic/coverage")

    assert answer.status_code == 200
    assert answer.json() == {"described": 0, "library": 0}


def test_nobody_signed_in_is_not_told_how_much_has_been_described(client: TestClient) -> None:
    """Scoped counts, and the scope starts with being signed in at all."""
    assert client.get("/api/semantic/coverage").status_code == 401


def test_lookalikes_come_back_resolved_against_whoever_asked(client: TestClient) -> None:
    """The candidates are found without any notion of who is asking (it is arithmetic over
    numbers), so the answer goes back through the ordinary read. That is what narrows it to what
    this user may actually see, in the one statement every other screen uses."""
    import asyncio

    from sift.kernel.db import Database
    from sift.slices.semantic.similar import Similar, Tier

    sign_in(client, "admin")
    first, second = "01HX0000000000000000000101", "01HX0000000000000000000102"
    root_id = "01HX0000000000000000000100"

    async def seed() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            await database.execute(
                "INSERT INTO library_roots (id, name, abs_path, created_at) "
                "VALUES (?, 'Videos', '/library/videos', 1700000000)",
                (root_id,),
            )
            for index, asset_id in enumerate((first, second)):
                await database.execute(
                    "INSERT INTO assets (id, identity, media_type, size_bytes, added_at) "
                    "VALUES (?, ?, 'video', 1, ?)",
                    (asset_id, f"digest-{asset_id}", 1700000000 + index),
                )
                await database.execute(
                    "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                    "filename, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, NULL, ?, ?, 1700000000, 1700000000)",
                    (f"loc-{asset_id}", asset_id, root_id, f"{asset_id}.mp4", f"{asset_id}.mp4"),
                )
        finally:
            await database.close()

    asyncio.run(seed())

    async def pretend(asset_id: str, *, limit: int = 200, asker: object = None) -> Similar:
        return Similar(tier=Tier.LOOKS, neighbours=((second, 0.2),))

    client.app.state.semantic.similar_to = pretend  # type: ignore[attr-defined]

    body = client.get(f"/api/assets/{first}/similar").json()

    assert body["tier"] == "looks"
    assert [item["id"] for item in body["items"]] == [second]
    assert body["items"][0]["media_type"] == "video"


def test_a_file_the_asker_may_not_see_answers_exactly_as_an_invented_id_does(
    client: TestClient, tmp_path: Path
) -> None:
    """A file the asker may not see answers exactly as an invented id: the subject is resolved
    before the lookalikes. The stand-in answers per subject and the neighbour is shared, so the two
    replies would really differ without the guard."""
    from sift.slices.semantic.similar import Similar, Tier
    from sift.testing.library import seed_asset, seed_root, share_folder

    # Asset ids differ before their last character: `seed_asset` derives the location id from it.
    withheld_id, neighbour_id = "01HX0000000000000000000201", "01HX0000000000000000000212"
    private_root, private_folder = "01HX0000000000000000000200", "01HX0000000000000000000203"
    shared_root, shared_folder = "01HX0000000000000000000204", "01HX0000000000000000000205"
    invented_id = "01HX0000000000000000000299"

    settings = get_settings()
    # Separate directories, because a root's path is unique: two roots sharing one is a state the
    # application cannot produce.
    private_path = tmp_path / "library" / "private"
    shared_path = tmp_path / "library" / "shared"
    for directory in (private_path, shared_path):
        directory.mkdir(parents=True, exist_ok=True)

    guest_id = sign_in(client, "guest")

    # One file nobody shared, and one the guest may see. The similarity runs from the first to the
    # second, which is precisely the direction the guard has to stop.
    seed_root(
        db_path(client), private_root, folder_id=private_folder, path=private_path, name="Priv"
    )
    seed_root(
        db_path(client), shared_root, folder_id=shared_folder, path=shared_path, name="Shared"
    )
    seed_asset(
        db_path(client),
        withheld_id,
        root_id=private_root,
        folder_id=private_folder,
        root_path=private_path,
        cache_dir=settings.cache_dir,
        filename="withheld.mp4",
    )
    seed_asset(
        db_path(client),
        neighbour_id,
        root_id=shared_root,
        folder_id=shared_folder,
        root_path=shared_path,
        cache_dir=settings.cache_dir,
        filename="neighbour.mp4",
    )
    share_folder(db_path(client), shared_folder, guest_id, grant_id="01HX0000000000000000000206")

    async def pretend(asset_id: str, *, limit: int = 200, asker: object = None) -> Similar:
        """The service's own shape: neighbours for a file it has described, nothing for anything
        else. An id that names no file has no fingerprint to compare, which is the cheap tier."""
        if asset_id == withheld_id:
            return Similar(tier=Tier.LOOKS, neighbours=((neighbour_id, 0.2),))
        return Similar(tier=Tier.MATCHES, neighbours=())

    client.app.state.semantic.similar_to = pretend  # type: ignore[attr-defined]

    # The guest really can reach the neighbour, so an unguarded reply would carry its id.
    assert client.get(f"/api/assets/{neighbour_id}/similar").status_code == 200

    invented = client.get(f"/api/assets/{invented_id}/similar")
    withheld = client.get(f"/api/assets/{withheld_id}/similar")

    assert withheld.status_code == invented.status_code == 200
    assert withheld.json() == invented.json()
    assert withheld.json() == {"tier": "matches", "items": []}
