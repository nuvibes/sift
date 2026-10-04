# SPDX-License-Identifier: AGPL-3.0-or-later
"""The settings endpoints over HTTP against the real application, with one per-user setting
registered per test."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.settings_registry import SECTIONS
from sift.main import create_app
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]

GUEST_SAVE_KEY = "guests.can_save_to_device"


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


def _sign_in(client: TestClient, role: str) -> None:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    _, token, csrf = establish_session(
        db_path, role=role, username=f"settings-{role}", password="Settings-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf


def _section(body: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return next(section["settings"] for section in body["sections"] if section["name"] == name)


def _keys(body: dict[str, Any]) -> set[str]:
    return {entry["key"] for section in body["sections"] for entry in section["settings"]}


# --- reading --------------------------------------------------------------------------------


def test_reading_settings_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/settings").status_code == 401


def test_an_admin_sees_the_global_setting(client: TestClient) -> None:
    _sign_in(client, "admin")
    body = client.get("/api/settings").json()
    save = next(
        entry for entry in _section(body, "Privacy and Security") if entry["key"] == GUEST_SAVE_KEY
    )
    assert save["value"] is False
    assert save["scope"] == "app"
    assert save["label"]


def test_a_guest_does_not_see_the_global_setting(client: TestClient) -> None:
    _sign_in(client, "guest")
    body = client.get("/api/settings").json()
    assert GUEST_SAVE_KEY not in _keys(body)


def test_every_section_is_present_even_when_empty(client: TestClient) -> None:
    """Every declared section is sent in declared order, even when nothing filled it."""
    _sign_in(client, "admin")
    body = client.get("/api/settings").json()

    names = [section["name"] for section in body["sections"]]

    assert names == list(SECTIONS)


# --- writing global settings ----------------------------------------------------------------


def test_an_admin_can_turn_the_global_setting_on(client: TestClient) -> None:
    _sign_in(client, "admin")
    response = client.put("/api/settings", json={"values": {GUEST_SAVE_KEY: True}})
    assert response.status_code == 204
    body = client.get("/api/settings").json()
    save = next(
        entry for entry in _section(body, "Privacy and Security") if entry["key"] == GUEST_SAVE_KEY
    )
    assert save["value"] is True


def test_a_saved_change_tells_the_application_which_keys_moved(client: TestClient) -> None:
    """The hook the watcher hangs off. A successful save names the keys that changed so anything that
    has to act on one (the watcher rebuilding its observers when polling is turned on) is told,
    without the settings route knowing what listens. A refused save fires nothing."""
    from sift.slices import performance

    told: list[set[str]] = []

    async def spy(changed: set[str]) -> None:
        told.append(changed)

    client.app.state.on_settings_changed = spy  # type: ignore[attr-defined]
    _sign_in(client, "admin")

    ok = client.put("/api/settings", json={"values": {performance.WORKER_COUNT_KEY: 3}})
    assert ok.status_code == 204
    assert told == [{performance.WORKER_COUNT_KEY}]

    # A rejected batch stored nothing, so there is nothing to have changed and the hook stays quiet.
    told.clear()
    bad = client.put("/api/settings", json={"values": {performance.WORKER_COUNT_KEY: -1}})
    assert bad.status_code == 422
    assert told == []


def test_a_guest_cannot_write_the_global_setting(client: TestClient) -> None:
    """The server refuses it, not the screen. This is the request a modified client makes."""
    _sign_in(client, "guest")
    response = client.put("/api/settings", json={"values": {GUEST_SAVE_KEY: True}})
    assert response.status_code == 403
    # And the value did not move: an admin reads it back off.
    _sign_in(client, "admin")
    body = client.get("/api/settings").json()
    save = next(
        entry for entry in _section(body, "Privacy and Security") if entry["key"] == GUEST_SAVE_KEY
    )
    assert save["value"] is False


def test_an_unknown_key_is_a_400(client: TestClient) -> None:
    _sign_in(client, "admin")
    response = client.put("/api/settings", json={"values": {"not.a.real.setting": 1}})
    assert response.status_code == 400


def test_a_bad_value_is_a_422(client: TestClient) -> None:
    _sign_in(client, "admin")
    response = client.put("/api/settings", json={"values": {GUEST_SAVE_KEY: "yes"}})
    assert response.status_code == 422


def test_a_write_without_the_csrf_header_is_refused(client: TestClient) -> None:
    _sign_in(client, "admin")
    del client.headers[CSRF_HEADER_NAME]
    response = client.put("/api/settings", json={"values": {GUEST_SAVE_KEY: True}})
    assert response.status_code == 403


# --- writing a per-user setting -------------------------------------------------------------


@pytest.fixture
def extra_user_setting() -> Iterator[str]:
    """Register one per-user setting for a single test, then remove just it.

    No shipped feature declares a user setting yet, so there is nothing to exercise the guest write
    path against. Only this key is removed on teardown, so the app's own global setting is untouched.
    """
    from sift.kernel.settings_registry import _REGISTRY, register_setting

    key = "test.api_loop_mode"
    register_setting(
        key=key,
        scope="user",
        default="loop_one",
        choices=["loop_one", "once"],
        choice_labels=["Repeat", "Stop"],
        section="Playback",
        label="Repeat",
        help="What happens when a clip ends.",
    )
    try:
        yield key
    finally:
        _REGISTRY.pop(key, None)


def test_a_guest_can_change_their_own_per_user_setting(
    client: TestClient, extra_user_setting: str
) -> None:
    _sign_in(client, "guest")
    response = client.put("/api/settings", json={"values": {extra_user_setting: "once"}})
    assert response.status_code == 204
    body = client.get("/api/settings").json()
    entry = next(item for item in _section(body, "Playback") if item["key"] == extra_user_setting)
    assert entry["value"] == "once"


def test_saving_works_on_an_application_that_wants_no_telling(
    client: TestClient, extra_user_setting: str
) -> None:
    """The hook is optional, and the branch where it is absent is the one nothing exercised.

    A settings write is not allowed to depend on somebody having wired a listener up: an
    application assembled without one still has to save. Asserted by taking the listener off the
    running app rather than by building a second one, so what is under test is the same route.
    """
    _sign_in(client, "guest")
    monkeypatched = getattr(client.app.state, "on_settings_changed", None)  # type: ignore[attr-defined]
    assert monkeypatched is not None, "the app really does ship with one, so removing it means this"
    del client.app.state.on_settings_changed  # type: ignore[attr-defined]
    try:
        response = client.put("/api/settings", json={"values": {extra_user_setting: "once"}})
    finally:
        client.app.state.on_settings_changed = monkeypatched  # type: ignore[attr-defined]

    assert response.status_code == 204
    body = client.get("/api/settings").json()
    entry = next(item for item in _section(body, "Playback") if item["key"] == extra_user_setting)
    assert entry["value"] == "once"


# --- the arrangement of the interface ---------------------------------------------------------
#
# The rail arrangement's own pair of routes, following the user.
#: The one key this holds today. Written out rather than imported, so a rename has to be made twice
#: on purpose: a client already storing the old spelling is somebody's rail quietly reset.
RAIL_ORDER = "rail.order"


def test_a_user_that_arranged_nothing_reads_back_an_empty_arrangement(
    client: TestClient,
) -> None:
    """The ordinary answer on a fresh user, and it has to be an answer rather than a 404: the
    shell asks for this on every load and an error would be drawn as one."""
    _sign_in(client, "admin")

    response = client.get("/api/settings/interface")

    assert response.status_code == 200
    assert response.json() == {"state": {}}


def test_an_arrangement_is_written_and_read_back_by_the_same_user(client: TestClient) -> None:
    _sign_in(client, "guest")

    saved = client.put("/api/settings/interface", json={"state": {RAIL_ORDER: "browse,people"}})

    assert saved.status_code == 204
    assert client.get("/api/settings/interface").json() == {"state": {RAIL_ORDER: "browse,people"}}


def test_one_user_never_reads_another_s_arrangement(client: TestClient) -> None:
    """Every signed-in user may write their own, guest included, and it reaches nobody else. The
    check is here as well as in the service because this is the door: the route is what decides
    whose arrangement is being asked for."""
    _sign_in(client, "guest")
    client.put("/api/settings/interface", json={"state": {RAIL_ORDER: "browse,people"}})

    _sign_in(client, "admin")

    assert client.get("/api/settings/interface").json() == {"state": {}}


def test_a_key_nothing_declares_is_a_400(client: TestClient) -> None:
    """A closed list, because nothing on the server reads these values: an open key space would
    be a per-user scratchpad anybody signed in could fill."""
    _sign_in(client, "admin")

    response = client.put("/api/settings/interface", json={"state": {"something.else": "anything"}})

    assert response.status_code == 400


def test_a_value_the_store_cannot_hold_is_a_422(client: TestClient) -> None:
    _sign_in(client, "admin")

    response = client.put("/api/settings/interface", json={"state": {RAIL_ORDER: "browse people"}})

    assert response.status_code == 422
    # And nothing was stored: a refused arrangement leaves the one they had.
    assert client.get("/api/settings/interface").json() == {"state": {}}


def test_arranging_the_interface_needs_a_session(client: TestClient) -> None:
    """Reading is a 401: there is no user to answer for.

    Writing is refused a step earlier, by the same cross-site guard every other write has: no
    session means no token, and a request that cannot present one never reaches the route. Both are
    refusals; asserting the exact one keeps the difference visible if either ever moves.
    """
    assert client.get("/api/settings/interface").status_code == 401
    assert (
        client.put("/api/settings/interface", json={"state": {RAIL_ORDER: "browse"}}).status_code
        == 403
    )


# --- what was still to be set up ---------------------------------------------------------------
#
# There is no first-run flow to serve; what those settings default to is each slice's own test.
