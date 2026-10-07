# SPDX-License-Identifier: AGPL-3.0-or-later
"""The phone as a remote, driven against the real application and the real live connection.

Every rule the design names is a case here: a screen is its user's alone (another user's answers
exactly like a guessed id), a locked session neither lists nor commands, a Hidden file is named
only to a phone whose own session opened Hidden, every number is bounded, the routes are rate
limited, and a command rides the one live connection, wakes it early, expires in seconds and is
never repeated to a connection that opens later.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.changes import (
    COMMAND_SECONDS,
    MAX_COMMANDS_PER_BEAT,
    About,
    ChangeBus,
    RemoteAction,
    RemoteCommand,
    Subscription,
)
from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.main import create_app
from sift.slices.auth.crypto import hash_token
from sift.slices.live.router import REPAIRED, _rest
from sift.slices.live.tuning import BEAT_SECONDS
from sift.slices.remote import tuning
from sift.slices.remote.screens import (
    VALUES,
    Allowance,
    Extras,
    Report,
    Screen,
    Screens,
    refusal,
)
from sift.testing.auth import establish_session
from sift.testing.library import hide_for, seed_asset, seed_root, write_rows

pytestmark = [pytest.mark.integration]

A_ROOT = "01HX0000000000000000000R01"
A_FOLDER = "01HX0000000000000000000R02"
AN_ASSET = "01HX0000000000000000000R03"
PASSWORD = "Remote-Test-Passw0rd!"

DESK = "desk-screen-0001"
SCREENS_ROUTE = "/api/remote/screens"


def a_report(**changes: Any) -> dict[str, Any]:
    """A player's report, as the desktop sends it. Any field can be changed by name."""
    body: dict[str, Any] = {
        "label": "Sift on the desk",
        "surface": "player",
        "app": True,
        "playing": True,
        "position": 12.0,
        "length": 600.0,
        "file": None,
        "volume": 80,
        "muted": False,
        "supports": ["player.playPause", "player.seekTo", "player.volumeTo", "player.next"],
    }
    body.update(changes)
    return body


# --- the booted application -------------------------------------------------------------------


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(app) as c:
        db = db_path(c)
        seed_root(db, A_ROOT, folder_id=A_FOLDER, path=tmp_path / "library")
        seed_asset(
            db,
            AN_ASSET,
            root_id=A_ROOT,
            folder_id=A_FOLDER,
            root_path=tmp_path / "library",
            cache_dir=tmp_path / "cache",
        )
        yield c


def db_path(client: TestClient) -> Path:
    return Path(client.app.state.database.path)  # type: ignore[attr-defined]


def sign_in(client: TestClient, *, who: str = "one", role: str = "admin") -> tuple[str, str]:
    """A real user with a real session, now the one calling. Returns (user id, session token)."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"remote-{who}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id, token


def report(client: TestClient, screen: str = DESK, **changes: Any) -> int:
    status: int = client.post(f"{SCREENS_ROUTE}/{screen}", json=a_report(**changes)).status_code
    return status


def command(client: TestClient, action: str, value: float | None = None, screen: str = DESK) -> Any:
    return client.post(
        f"{SCREENS_ROUTE}/{screen}/commands", json={"action": action, "value": value}
    )


def listed(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(SCREENS_ROUTE)
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json()["screens"])


def bus_of(client: TestClient) -> ChangeBus:
    return client.app.state.changes  # type: ignore[attr-defined,no-any-return]


# --- pairing is the sign-in -------------------------------------------------------------------


def test_a_screen_offered_is_listed_for_its_own_user(client: TestClient) -> None:
    sign_in(client)
    assert report(client) == 204

    [screen] = listed(client)
    assert screen["screen"] == DESK
    assert screen["label"] == "Sift on the desk"
    assert screen["playing"] is True
    assert screen["supports"] == sorted(a_report()["supports"])
    assert screen["hidden"] is False


def test_another_users_screen_is_neither_listed_nor_commandable(client: TestClient) -> None:
    """Another user's screen answers exactly as a guessed id does: the same status, the same words."""
    sign_in(client, who="owner")
    report(client)
    sign_in(client, who="stranger")

    assert listed(client) == []
    theirs = command(client, "player.playPause", 0)
    guessed = command(client, "player.playPause", 0, screen="never-offered-01")
    assert theirs.status_code == guessed.status_code == 404
    assert theirs.json() == guessed.json()


def test_a_locked_session_neither_lists_nor_commands(client: TestClient) -> None:
    _, token = sign_in(client)
    report(client)
    write_rows(
        db_path(client),
        [("UPDATE sessions SET locked_at = 1 WHERE token_hash = ?", (hash_token(token),))],
    )

    assert client.get(SCREENS_ROUTE).status_code == 423
    assert command(client, "player.playPause", 0).status_code == 423
    assert report(client) == 423


def test_every_write_needs_the_csrf_token(client: TestClient) -> None:
    sign_in(client)
    report(client)
    del client.headers[CSRF_HEADER_NAME]

    assert report(client) == 403
    assert command(client, "player.playPause", 0).status_code == 403
    assert client.delete(f"{SCREENS_ROUTE}/{DESK}").status_code == 403


def test_a_screen_withdrawn_is_gone(client: TestClient) -> None:
    sign_in(client)
    report(client)
    assert client.delete(f"{SCREENS_ROUTE}/{DESK}").status_code == 204
    assert listed(client) == []
    # And again, for a screen that is not there: the same answer, because it is the same outcome.
    assert client.delete(f"{SCREENS_ROUTE}/{DESK}").status_code == 204


# --- Hidden -----------------------------------------------------------------------------------


def test_a_hidden_file_is_not_named_to_a_phone_that_has_not_opened_hidden(
    client: TestClient,
) -> None:
    user_id, _ = sign_in(client)
    hide_for(db_path(client), "asset", AN_ASSET, user_id)
    report(client, file=AN_ASSET)

    [screen] = listed(client)
    assert screen["file"] is None
    assert screen["hidden"] is True


def test_a_phone_that_opened_hidden_itself_is_told_the_file(client: TestClient) -> None:
    user_id, token = sign_in(client)
    hide_for(db_path(client), "asset", AN_ASSET, user_id)
    report(client, file=AN_ASSET)
    client.app.state.auth.vault_unlocks.unlock(hash_token(token))  # type: ignore[attr-defined]

    [screen] = listed(client)
    assert screen["file"] == AN_ASSET
    assert screen["hidden"] is False


def test_an_ordinary_file_is_named(client: TestClient) -> None:
    sign_in(client)
    report(client, file=AN_ASSET)
    [screen] = listed(client)
    assert screen["file"] == AN_ASSET
    assert screen["hidden"] is False


def test_a_file_the_phone_cannot_reach_is_not_named_and_not_called_hidden(
    client: TestClient,
) -> None:
    """A screen can name any id. The phone is told only what it could open itself."""
    sign_in(client, role="guest", who="guest")
    report(client, file=AN_ASSET)
    [screen] = listed(client)
    assert screen["file"] is None
    assert screen["hidden"] is False


def test_a_walls_cells_are_named_by_the_same_rule_as_its_file(client: TestClient) -> None:
    """A cell's file is the phone's to see only where the phone's own session may open it."""
    user_id, _ = sign_in(client)
    hide_for(db_path(client), "asset", AN_ASSET, user_id)
    report(
        client,
        surface="theater",
        supports=["theater.cell"],
        cells=3,
        focused=0,
        cell_files=[AN_ASSET, None, "01HX0000000000000000000R99"],
    )
    [screen] = listed(client)
    assert screen["cell_files"] == [
        {"file": None, "hidden": True},
        {"file": None, "hidden": False},
        {"file": None, "hidden": False},
    ]


def test_the_drawer_the_desk_reports_is_the_drawer_the_phone_reads(client: TestClient) -> None:
    sign_in(client)
    report(
        client,
        supports=["player.repeat", "player.quality"],
        repeat="loop_one",
        shuffle=True,
        loop_marks=1,
        qualities=["1080p", "720p"],
        quality=1,
        favorite=True,
        count=3,
    )
    [screen] = listed(client)
    assert (screen["repeat"], screen["shuffle"], screen["loop_marks"]) == ("loop_one", True, 1)
    assert (screen["qualities"], screen["quality"]) == (["1080p", "720p"], 1)
    assert (screen["favorite"], screen["count"]) == (True, 3)


def test_a_report_choosing_past_its_own_list_is_refused(client: TestClient) -> None:
    sign_in(client)
    assert report(client, qualities=["1080p"], quality=1) == 422
    assert report(client, surface="theater", supports=[], layouts=["grid"], layout=1) == 422
    assert report(client, surface="theater", supports=[], cells=1, cell_files=[None, None]) == 422
    assert report(client, repeat="forever") == 422


# --- bounds -----------------------------------------------------------------------------------


def test_a_command_the_screen_did_not_offer_is_refused(client: TestClient) -> None:
    sign_in(client)
    report(client)
    assert command(client, "player.mute", 1).status_code == 409


def test_every_number_is_bounded(client: TestClient) -> None:
    sign_in(client)
    report(client)
    assert command(client, "player.volumeTo", 101).status_code == 422
    assert command(client, "player.volumeTo", 50.5).status_code == 422
    assert command(client, "player.seekTo", 601).status_code == 422
    assert command(client, "player.seekTo", -1).status_code == 422
    assert command(client, "player.playPause", 2).status_code == 422
    assert command(client, "player.next", 1).status_code == 422
    assert command(client, "player.playPause").status_code == 422
    assert command(client, "player.seekTo", 2_000_000).status_code == 422
    assert command(client, "player.volumeTo", 40).status_code == 202


def test_a_report_offering_another_surfaces_verb_is_refused(client: TestClient) -> None:
    sign_in(client)
    assert report(client, supports=["theater.pauseAll"]) == 422
    assert report(client, surface="theater", cells=2, focused=2, supports=[]) == 422
    assert report(client, volume=101) == 422
    assert report(client, screen="no") == 422


def a_still_clock(client: TestClient) -> None:
    """The app's screens, on a clock that does not move, so an allowance cannot refill mid-test."""
    frozen = Clock()
    client.app.state.remote_screens = Screens(frozen)  # type: ignore[attr-defined]


def test_commands_are_rate_limited(client: TestClient) -> None:
    sign_in(client)
    a_still_clock(client)
    report(client)
    answers = [command(client, "player.playPause", 1).status_code for _ in range(40)]
    assert answers[: tuning.COMMAND_BURST] == [202] * tuning.COMMAND_BURST
    assert set(answers[tuning.COMMAND_BURST :]) == {429}
    refused = command(client, "player.playPause", 1)
    assert int(refused.headers["Retry-After"]) >= 1


def test_reports_are_rate_limited(client: TestClient) -> None:
    sign_in(client)
    a_still_clock(client)
    answers = [report(client) for _ in range(tuning.REPORT_BURST + 10)]
    assert answers[: tuning.REPORT_BURST] == [204] * tuning.REPORT_BURST
    assert set(answers[tuning.REPORT_BURST :]) == {429}


# --- the one live connection ------------------------------------------------------------------


def test_a_command_rides_the_live_connection_to_the_users_tabs(client: TestClient) -> None:
    sign_in(client)
    report(client)
    with client.websocket_connect("/api/live/stream") as socket:
        sent = command(client, "player.seekTo", 42)
        assert sent.status_code == 202
        message = socket.receive_json()

    assert "remote" in message["about"]
    [carried] = message["commands"]
    assert carried == {
        "id": sent.json()["id"],
        "screen": DESK,
        "action": "player.seekTo",
        "value": 42.0,
    }


def test_a_command_does_not_wait_for_the_next_beat(client: TestClient) -> None:
    """The connection is woken by the command, not found by its next look a second later.

    The command is sent a moment after the connection has settled into a rest, so a beat that was
    not woken would hold it for most of a second; a woken one sends it immediately.
    """
    sign_in(client)
    report(client)
    with client.websocket_connect("/api/live/stream") as socket:
        time.sleep(BEAT_SECONDS / 10)
        started = time.monotonic()
        command(client, "player.playPause", 0)
        socket.receive_json()
        took = time.monotonic() - started
    assert took < BEAT_SECONDS / 2


def test_a_command_is_not_repeated_to_a_connection_that_opens_later(client: TestClient) -> None:
    """The reconnecting tab asks for a repair, and the repair does not carry the command."""
    sign_in(client)
    report(client)
    marker = client.get("/api/live").json()["marker"]
    command(client, "player.playPause", 0)
    # Something else moved too, so the late connection is owed a repair and is sent one.
    report(client, playing=False)

    with client.websocket_connect(f"/api/live/stream?since={marker}") as socket:
        message = socket.receive_json()
    assert "remote" not in message["about"]
    assert message["commands"] == []
    assert "screens" in message["about"]


def test_a_changed_screen_rings_the_screens_subject_and_a_heartbeat_does_not(
    client: TestClient,
) -> None:
    user_id, _ = sign_in(client)
    bus = bus_of(client)
    subscription = bus.subscribe(user_id)
    try:
        report(client)
        assert subscription.take(as_admin=True).about == (About.SCREENS,)
        report(client, position=12.0)
        assert not subscription.take(as_admin=True)
        report(client, playing=False)
        assert subscription.take(as_admin=True).about == (About.SCREENS,)
        client.delete(f"{SCREENS_ROUTE}/{DESK}")
        assert subscription.take(as_admin=True).about == (About.SCREENS,)
    finally:
        bus.release(subscription)


def test_the_repair_rereads_screens_and_never_replays_a_command() -> None:
    assert About.SCREENS in REPAIRED
    assert About.REMOTE not in REPAIRED
    assert About.OPINIONS not in REPAIRED


# --- the bus, without a socket ----------------------------------------------------------------


def a_command(n: int = 0) -> RemoteCommand:
    return RemoteCommand(id=f"c{n}", screen=DESK, action=RemoteAction.NEXT, value=None)


def test_a_command_wakes_the_connection_and_the_take_settles_it() -> None:
    subscription = Subscription("u")
    assert not subscription.wake.is_set()
    subscription.note_command(a_command(), expires_at=10.0)
    assert subscription.wake.is_set()

    pending = subscription.take(as_admin=False, now=5.0)
    assert pending.about == (About.REMOTE,)
    assert pending.commands == (a_command(),)
    assert not subscription.wake.is_set()


def test_a_command_past_its_time_is_dropped_and_says_nothing() -> None:
    subscription = Subscription("u")
    subscription.note_command(a_command(), expires_at=10.0)
    assert not subscription.take(as_admin=False, now=10.0)


def test_only_the_newest_commands_are_held() -> None:
    subscription = Subscription("u")
    for n in range(MAX_COMMANDS_PER_BEAT + 5):
        subscription.note_command(a_command(n), expires_at=10.0)
    pending = subscription.take(as_admin=False, now=0.0)
    assert len(pending.commands) == MAX_COMMANDS_PER_BEAT
    assert pending.commands[-1] == a_command(MAX_COMMANDS_PER_BEAT + 4)


def test_publishing_a_command_reaches_only_its_user_and_does_not_move_the_mark() -> None:
    bus = ChangeBus()
    mine, theirs = bus.subscribe("me"), bus.subscribe("them")
    mark = bus.mark
    before = time.monotonic()

    assert bus.publish_command("me", a_command()) == 1
    assert bus.mark == mark
    assert mine.take(as_admin=False, now=before + COMMAND_SECONDS - 0.5).commands == (a_command(),)
    assert not theirs.take(as_admin=False)
    assert bus.publish_command("nobody", a_command()) == 0


class _Quiet:
    """A connection whose browser never sends anything, so only a timer or a wake ends a rest."""

    async def receive(self) -> dict[str, Any]:
        await asyncio.sleep(3600)
        return {}


class _Closing:
    async def receive(self) -> dict[str, Any]:
        return {"type": "websocket.disconnect"}


def test_a_wake_ends_the_beat_early() -> None:
    async def run() -> tuple[bool, float]:
        wake = asyncio.Event()
        asyncio.get_running_loop().call_later(0.05, wake.set)
        started = time.monotonic()
        still_there = await _rest(cast(Any, _Quiet()), wake)
        return still_there, time.monotonic() - started

    still_there, took = asyncio.run(run())
    assert still_there is True
    assert took < BEAT_SECONDS / 2


def test_a_rest_with_a_wake_still_ends_on_a_frame_and_on_the_beat() -> None:
    async def closing() -> bool:
        return await _rest(cast(Any, _Closing()), asyncio.Event())

    async def quiet() -> tuple[bool, float]:
        started = time.monotonic()
        answer = await _rest(cast(Any, _Quiet()), asyncio.Event())
        return answer, time.monotonic() - started

    assert asyncio.run(closing()) is False
    answer, took = asyncio.run(quiet())
    assert answer is True
    assert took >= BEAT_SECONDS * 0.9


# --- the store, on its own clock --------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def a_screen_report(**changes: Any) -> Report:
    fields: dict[str, Any] = {
        "label": "desk",
        "surface": "player",
        "app": True,
        "playing": True,
        "position": 10.0,
        "length": 100.0,
        "file": None,
        "volume": 50,
        "muted": False,
        "supports": frozenset({RemoteAction.SEEK_TO, RemoteAction.THEATER_CELL}),
        "acted_on": None,
        "cells": 0,
        "focused": None,
    }
    fields.update(changes)
    return Report(**fields)


def test_a_screen_that_stops_speaking_is_let_go() -> None:
    clock = Clock()
    screens = Screens(clock)
    screens.report("u", DESK, a_screen_report())
    clock.now += tuning.SCREEN_SECONDS - 1
    assert screens.find("u", DESK) is not None
    clock.now += 1
    assert screens.find("u", DESK) is None
    assert screens.of("u") == []


def test_a_playing_screen_is_carried_on_by_the_time_since_and_stops_at_its_length() -> None:
    clock = Clock()
    screens = Screens(clock)
    screens.report("u", DESK, a_screen_report())
    clock.now += 5
    [screen] = screens.of("u")
    assert screen.position_at(clock.now) == 15.0
    assert screen.position_at(clock.now + 500) == 100.0
    screens.report("u", DESK, a_screen_report(playing=False, position=15.0))
    clock.now += 5
    assert screens.of("u")[0].position_at(clock.now) == 15.0


def test_a_heartbeat_is_not_news_and_a_seek_is() -> None:
    clock = Clock()
    screens = Screens(clock)
    assert screens.report("u", DESK, a_screen_report()) is True
    clock.now += 5
    assert screens.report("u", DESK, a_screen_report(position=15.0)) is False
    clock.now += 5
    assert screens.report("u", DESK, a_screen_report(position=90.0)) is True
    assert screens.report("u", DESK, a_screen_report(position=90.0, acted_on="c1")) is True


def test_past_the_most_screens_the_quietest_goes() -> None:
    clock = Clock()
    screens = Screens(clock)
    for n in range(tuning.MOST_SCREENS_PER_USER + 1):
        screens.report("u", f"screen-{n:04}", a_screen_report())
        clock.now += 1
    held = [one.screen_id for one in screens.of("u")]
    assert len(held) == tuning.MOST_SCREENS_PER_USER
    assert "screen-0000" not in held


def test_withdrawing_a_screen_nobody_offered_says_so() -> None:
    screens = Screens(Clock())
    assert screens.withdraw("u", DESK) is False
    screens.report("u", DESK, a_screen_report())
    assert screens.withdraw("u", DESK) is True
    assert screens.of("u") == []


def test_the_screens_own_numbers_bound_a_seek_and_a_cell() -> None:
    player = Screen("u", DESK, a_screen_report(), 0.0)
    wall = Screen("u", DESK, a_screen_report(surface="theater", cells=4, length=None), 0.0)
    assert refusal(RemoteAction.SEEK_TO, 100.0, player) is None
    assert refusal(RemoteAction.SEEK_TO, 100.5, player) == "That is out of range."
    assert refusal(RemoteAction.SEEK_TO, 5000.0, wall) is None
    assert refusal(RemoteAction.THEATER_CELL, 3, wall) is None
    assert refusal(RemoteAction.THEATER_CELL, 4, wall) == "That is out of range."
    assert refusal(RemoteAction.BACK, 10.5, player) is None
    assert refusal(RemoteAction.BACK, 0.5, player) == "That is out of range."
    assert refusal(RemoteAction.VOLUME_TO, float("nan"), player) == "That needs a value."


def test_a_choice_is_held_to_the_list_the_screen_reported() -> None:
    wall = Screen(
        "u",
        DESK,
        a_screen_report(
            surface="theater",
            extras=Extras(layouts=("single", "grid"), presets=("Evening",), qualities=()),
        ),
        0.0,
    )
    assert refusal(RemoteAction.THEATER_LAYOUT, 1, wall) is None
    assert refusal(RemoteAction.THEATER_LAYOUT, 2, wall) == "That is out of range."
    assert refusal(RemoteAction.THEATER_PRESET, 0, wall) is None
    assert refusal(RemoteAction.THEATER_PRESET, 1, wall) == "That is out of range."
    assert refusal(RemoteAction.THEATER_QUALITY, 0, wall) == "That is out of range."
    assert refusal(RemoteAction.THEATER_SEEK_TO, 100.5, wall) == "That is out of range."
    assert refusal(RemoteAction.REPEAT, 3, wall) == "That is out of range."
    assert refusal(RemoteAction.THEATER_TIMER, 3601, wall) == "That is out of range."
    assert refusal(RemoteAction.THEATER_SOLO, 1, wall) == "That takes no value."


def test_every_verb_the_phone_can_send_is_bounded() -> None:
    """A verb added to the enum and not to the bounds would raise on its first press."""
    assert set(VALUES) == set(RemoteAction)


def test_an_allowance_refills_at_its_rate() -> None:
    clock = Clock()
    allowance = Allowance(2, 1.0, clock)
    assert allowance.spend("u") == 0.0
    assert allowance.spend("u") == 0.0
    assert allowance.spend("u") == pytest.approx(1.0)
    assert allowance.spend("someone else") == 0.0
    clock.now += 1.0
    assert allowance.spend("u") == 0.0


# --- the client holds the same words and the same numbers -------------------------------------

CLIENT = Path(__file__).resolve().parents[5] / "frontend" / "src" / "lib"


def test_the_desk_names_every_verb_the_phone_can_send() -> None:
    """The client's list of remote verbs is this enum, word for word and in order.

    A verb here and not there is a button on the phone the desk never answers, and the reverse is a
    verb the desk offers and the server refuses. Read out of the source, because the client's list
    is a plain array long before anything runs.
    """
    source = (CLIENT / "shell" / "shortcuts.ts").read_text(encoding="utf-8")
    block = re.search(r"export const REMOTE_ACTIONS = \[(.*?)\]", source, re.DOTALL)
    assert block is not None, "the client's list of remote verbs has moved"
    assert re.findall(r"'([\w.]+)'", block.group(1)) == [action.value for action in RemoteAction]


def test_the_desk_reports_as_often_as_the_server_expects() -> None:
    source = (CLIENT / "remote" / "offer.svelte.ts").read_text(encoding="utf-8")
    found = re.search(r"export const REPORT_EVERY_MS = ([\d_]+);", source)
    assert found is not None, "the client's report interval has moved"
    assert int(found.group(1).replace("_", "")) == tuning.REPORT_EVERY_SECONDS * 1000
    assert tuning.REPORT_EVERY_SECONDS * 3 <= tuning.SCREEN_SECONDS


# --- the phone driving a screen, which the desk says ------------------------------------------

PHONE = "phone-remote-0001"


def hold(client: TestClient, label: str = "Safari on an iPhone or iPad", screen: str = DESK) -> int:
    route = f"{SCREENS_ROUTE}/{screen}/controllers/{PHONE}"
    status: int = client.put(route, json={"label": label}).status_code
    return status


def test_a_phone_driving_a_screen_is_named_on_it_until_it_lets_go(client: TestClient) -> None:
    user_id, _ = sign_in(client)
    report(client)
    bus = bus_of(client)
    subscription = bus.subscribe(user_id)
    try:
        assert listed(client)[0]["controlled_by"] == []
        assert hold(client) == 204
        assert subscription.take(as_admin=True).about == (About.SCREENS,)
        assert listed(client)[0]["controlled_by"] == ["Safari on an iPhone or iPad"]
        # A renewal is a heartbeat: nothing moved, so nobody is woken.
        assert hold(client) == 204
        assert not subscription.take(as_admin=True)
        gone = client.delete(f"{SCREENS_ROUTE}/{DESK}/controllers/{PHONE}")
        assert gone.status_code == 204
        assert subscription.take(as_admin=True).about == (About.SCREENS,)
        assert listed(client)[0]["controlled_by"] == []
    finally:
        bus.release(subscription)


def test_a_phone_cannot_drive_a_screen_that_is_not_its_users(client: TestClient) -> None:
    sign_in(client, who="owner")
    report(client)
    sign_in(client, who="stranger")
    assert hold(client) == 404
    assert hold(client, screen="never-offered-0001") == 404


def test_a_phone_that_stops_saying_so_is_let_go_and_a_withdrawn_screen_takes_its_phones() -> None:
    clock = Clock()
    screens = Screens(clock)
    screens.report("u", DESK, a_screen_report())
    assert screens.hold("u", DESK, PHONE, "Chrome on Android") is True
    assert screens.hold("u", "not-a-screen-01", PHONE, "Chrome on Android") is None
    clock.now += tuning.SCREEN_SECONDS - 1
    screens.report("u", DESK, a_screen_report())
    assert screens.controlled_by("u", DESK) == ["Chrome on Android"]
    clock.now += 2
    screens.report("u", DESK, a_screen_report())
    assert screens.controlled_by("u", DESK) == []

    screens.hold("u", DESK, PHONE, "Chrome on Android")
    assert screens.withdraw("u", DESK) is True
    screens.report("u", DESK, a_screen_report())
    assert screens.controlled_by("u", DESK) == []


# --- a browser holding something back ---------------------------------------------------------

QUIET_ROUTE = "/api/remote/quiet"
LAPTOP = "laptop-tab-0001"


def test_the_screens_answer_counts_a_browser_not_offering_until_it_speaks_up(
    client: TestClient,
) -> None:
    """A tab with its switch off says only that it exists; the phone reads the count."""
    user_id, _ = sign_in(client)
    bus = bus_of(client)
    subscription = bus.subscribe(user_id)
    try:
        assert client.get(SCREENS_ROUTE).json()["not_offering"] == 0
        assert client.put(f"{QUIET_ROUTE}/{LAPTOP}").status_code == 204
        assert subscription.take(as_admin=True).about == (About.SCREENS,)
        answer = client.get(SCREENS_ROUTE).json()
        assert answer["not_offering"] == 1
        assert answer["screens"] == []
        # A renewal is a heartbeat: nobody is woken.
        assert client.put(f"{QUIET_ROUTE}/{LAPTOP}").status_code == 204
        assert not subscription.take(as_admin=True)
        assert client.delete(f"{QUIET_ROUTE}/{LAPTOP}").status_code == 204
        assert subscription.take(as_admin=True).about == (About.SCREENS,)
        assert client.get(SCREENS_ROUTE).json()["not_offering"] == 0
    finally:
        bus.release(subscription)


def test_a_browser_not_offering_is_its_own_users_alone(client: TestClient) -> None:
    sign_in(client, who="laptop")
    client.put(f"{QUIET_ROUTE}/{LAPTOP}")
    sign_in(client, who="stranger")
    assert client.get(SCREENS_ROUTE).json()["not_offering"] == 0
    assert client.delete(f"{QUIET_ROUTE}/{LAPTOP}").status_code == 204


def test_a_browser_not_offering_stops_counting_once_it_offers_or_goes_quiet() -> None:
    clock = Clock()
    screens = Screens(clock)
    assert screens.keep_quiet("u", LAPTOP) is True
    assert screens.not_offering("u") == 1
    # Offering the same tab is the switch turned on: it is a screen now, not a tab holding back.
    screens.report("u", LAPTOP, a_screen_report(app=False))
    assert screens.not_offering("u") == 0
    assert screens.keep_quiet("u", LAPTOP) is True
    clock.now += tuning.SCREEN_SECONDS - 1
    assert screens.not_offering("u") == 1
    clock.now += 1
    assert screens.not_offering("u") == 0
    assert screens.speak_up("u", LAPTOP) is False
    for n in range(tuning.MOST_SCREENS_PER_USER + 2):
        screens.keep_quiet("u", f"tab-{n:011d}")
    assert screens.not_offering("u") == tuning.MOST_SCREENS_PER_USER


def test_past_the_most_phones_on_one_screen_the_quietest_is_let_go() -> None:
    clock = Clock()
    screens = Screens(clock)
    screens.report("u", DESK, a_screen_report())
    for n in range(tuning.MOST_SCREENS_PER_USER + 1):
        screens.hold("u", DESK, f"phone-{n:04}", f"Phone {n}")
        clock.now += 1

    named = screens.controlled_by("u", DESK)
    assert len(named) == tuning.MOST_SCREENS_PER_USER
    assert "Phone 0" not in named


def test_one_phone_letting_go_leaves_the_others_and_a_stranger_letting_go_is_no_news() -> None:
    screens = Screens(Clock())
    screens.report("u", DESK, a_screen_report())
    screens.hold("u", DESK, "phone-0001", "Chrome on Android")
    screens.hold("u", DESK, "phone-0002", "Safari on an iPhone or iPad")

    assert screens.let_go("u", DESK, "phone-0003") is False
    assert screens.let_go("u", DESK, "phone-0001") is True
    assert screens.controlled_by("u", DESK) == ["Safari on an iPhone or iPad"]
    assert screens.let_go("u", DESK, "phone-0002") is True
    assert screens.controlled_by("u", DESK) == []


def test_withdrawing_one_screen_or_one_quiet_tab_leaves_the_users_others() -> None:
    screens = Screens(Clock())
    screens.report("u", DESK, a_screen_report())
    screens.report("u", "screen-0002", a_screen_report())
    screens.keep_quiet("u", "tab-00000000001")
    screens.keep_quiet("u", "tab-00000000002")

    assert screens.withdraw("u", DESK) is True
    assert [one.screen_id for one in screens.of("u")] == ["screen-0002"]
    assert screens.speak_up("u", "tab-00000000001") is True
    assert screens.not_offering("u") == 1


def test_a_phone_letting_go_of_a_screen_it_was_not_driving_wakes_nobody(
    client: TestClient,
) -> None:
    user_id, _ = sign_in(client)
    report(client)
    bus = bus_of(client)
    subscription = bus.subscribe(user_id)
    try:
        gone = client.delete(f"{SCREENS_ROUTE}/{DESK}/controllers/{PHONE}")
        assert gone.status_code == 204
        assert not subscription.take(as_admin=True)
    finally:
        bus.release(subscription)
