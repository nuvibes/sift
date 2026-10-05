# SPDX-License-Identifier: AGPL-3.0-or-later
"""The job this feature claims, and its routes.

The handler is the Build's unit of work: a file given its fingerprint and its bar filled. The routes
are admin-only and behind the CSRF check where they write, which are stood in for here because
they have their own tests.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel import wiring
from sift.kernel.access import Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.db import Connection
from sift.kernel.jobs import JobContext, registered_handlers, registered_job_names
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.music import schema
from sift.slices.music.jobs import AUDIO_FINGERPRINT, register_handlers
from sift.slices.music.lookup import (
    ASK_AGAIN_AFTER_DAYS,
    NOT_READY,
    NOTHING_TO_ASK_AGAIN,
    LookupNotReady,
    LookupPlan,
)
from sift.slices.music.router import router
from sift.slices.music.service import SERVICE, MusicService

pytestmark = [pytest.mark.unit]


class _Service:
    """What the handler and the route ask of the service, written down."""

    def __init__(self) -> None:
        self.fingerprinted: list[object] = []

    async def fingerprint(self, context: JobContext, *, settings: Settings) -> None:
        self.fingerprinted.append(context)


class _Context:
    def __init__(self) -> None:
        self.progress: list[float] = []

    async def set_progress(self, fraction: float) -> None:
        self.progress.append(fraction)


@pytest.mark.asyncio
async def test_the_job_is_claimed_and_fills_its_bar_when_the_file_is_done() -> None:
    service = _Service()
    register_handlers(service=cast(MusicService, service), settings=Settings())

    assert registered_job_names()[AUDIO_FINGERPRINT] == "Fingerprinting music"
    context = _Context()
    await registered_handlers()[AUDIO_FINGERPRINT](cast(JobContext, context))

    assert service.fingerprinted == [context]
    assert context.progress == [1.0]


@pytest.mark.asyncio
async def test_a_database_already_at_this_version_is_left_alone() -> None:
    """The tables are made once. An upgrade that re-ran the CREATEs would be harmless today and a
    fault the day one of them stops saying IF NOT EXISTS."""
    ran: list[Any] = []

    class _Connection:
        async def execute(self, *args: Any) -> None:
            ran.append(args)

    await schema.initialize(cast(Connection, _Connection()), schema.VERSION)
    assert ran == []


# --- the lookup's settings routes ------------------------------------------------------------------


class _Lookup:
    """The lookup's settings, answering from what it holds and writing down every change."""

    def __init__(self) -> None:
        self.sealed: list[tuple[str, bytes]] = []
        self.forgotten = 0
        self.checked: list[bytes | None] = []

    async def state(self, key: bytes | None) -> dict[str, Any]:
        return {
            "on": True,
            "key_set": bool(self.sealed),
            "key_ready": key is not None,
            "route": None,
        }

    async def set_key(self, key: str, master: bytes) -> None:
        self.sealed.append((key, master))

    async def forget_key(self) -> bool:
        self.forgotten += 1
        return True

    async def check(self, master: bytes | None) -> tuple[bool, str]:
        self.checked.append(master)
        return False, "No AcoustID key is set."


class _Starter:
    def __init__(self, *, starts: bool) -> None:
        self.starts = starts
        self.started = 0

    async def owed(self) -> int:
        return 7

    async def plan_again(self) -> LookupPlan:
        return LookupPlan(files=1, not_known=2)

    async def cannot_run(self) -> str | None:
        return None if self.starts else NOT_READY

    async def start_catch_up(self, **press: object) -> str:  # pragma: no cover
        raise AssertionError("nothing on the music routes presses the lookup task")


_KEY = b"k" * 32


def _lookup_app(lookup: _Lookup, starter: _Starter, *, key: bytes | None = _KEY) -> TestClient:
    from sift.slices.auth import master_key
    from sift.slices.music.lookup import LOOKUP, LOOKUP_STARTER

    app = FastAPI()
    app.include_router(router)
    setattr(app.state, LOOKUP.name, lookup)
    setattr(app.state, LOOKUP_STARTER.name, starter)
    app.dependency_overrides[require_admin] = lambda: Viewer(id="user-1", role=Role.ADMIN)
    app.dependency_overrides[csrf_protect] = lambda: None
    app.dependency_overrides[master_key] = lambda: key
    return TestClient(app)


def test_the_lookup_state_carries_the_count_the_catch_up_would_queue() -> None:
    answer = _lookup_app(_Lookup(), _Starter(starts=True)).get("/music/lookup")

    assert answer.status_code == 200
    assert answer.json() == {
        "on": True,
        "key_set": False,
        "key_ready": True,
        "route": None,
        "source": "acoustid",
        "label": "AcoustID",
        "ready": True,
        "owed": 7,
        "not_known": 2,
        "ask_again": 1,
        "ask_again_after_days": ASK_AGAIN_AFTER_DAYS,
    }


def test_a_key_is_sealed_only_when_the_saved_keys_are_open_and_it_says_something() -> None:
    """Locked since a restart: refused with the way to unlock. Blank: refused. Otherwise sealed
    under the key that is open, and the answer says one is set."""
    lookup = _Lookup()
    locked = _lookup_app(lookup, _Starter(starts=True), key=None).put(
        "/music/lookup/key", json={"key": "the-key"}
    )
    assert locked.status_code == 409
    assert "locked" in locked.json()["detail"]

    client = _lookup_app(lookup, _Starter(starts=True))
    blank = client.put("/music/lookup/key", json={"key": "   "})
    assert blank.status_code == 422
    assert lookup.sealed == []

    sealed = client.put("/music/lookup/key", json={"key": "the-key"})
    assert sealed.status_code == 200
    assert sealed.json()["key_set"] is True
    assert lookup.sealed == [("the-key", _KEY)]


def test_removing_the_key_forgets_it_and_answers_the_state() -> None:
    lookup = _Lookup()
    answer = _lookup_app(lookup, _Starter(starts=True)).delete("/music/lookup/key")

    assert answer.status_code == 200
    assert lookup.forgotten == 1
    assert answer.json()["owed"] == 7
    assert answer.json()["not_known"] == 2, "asked and not known, said apart from the owed"


def test_the_music_routes_have_no_second_door_to_the_lookup_tasks_run() -> None:
    """Asking AcoustID about the library is the lookup task's Run now, on the tasks' own run
    route. The Music pane has no press of its own, so nothing here can start it."""
    answer = _lookup_app(_Lookup(), _Starter(starts=True)).post("/music/lookup/run")
    assert answer.status_code in (404, 405)


def test_the_check_answers_what_the_key_check_said() -> None:
    lookup = _Lookup()
    answer = _lookup_app(lookup, _Starter(starts=True)).post("/music/lookup/check")

    assert answer.status_code == 200
    assert answer.json() == {"ok": False, "said": "No AcoustID key is set."}
    assert lookup.checked == [_KEY]


class _Pressed:
    """The lookup's starter as a press of Enrich or Ask again reaches it: refused while the lookup
    cannot run, otherwise queuing as many of the files it is handed as the test says."""

    def __init__(self, *, queues: int, ready: bool = True) -> None:
        self.queues, self.ready = queues, ready
        self.handed: list[tuple[list[str], str, bool]] = []

    async def start_for_files(
        self, asset_ids: list[str], *, requested_by: str, again: bool = False
    ) -> tuple[str | None, int]:
        if not self.ready:
            raise LookupNotReady(NOT_READY)
        self.handed.append((list(asset_ids), requested_by, again))
        return ("job-1" if self.queues else None), self.queues

    async def start_again(self, *, requested_by: str) -> tuple[str | None, int]:
        if not self.ready:
            raise LookupNotReady(NOT_READY)
        return ("job-1" if self.queues else None), self.queues


class _Actionable:
    """What this user may act on: every file but the one in Hidden."""

    async def actionable_of(self, viewer: Viewer, asset_ids: list[str]) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(
            allowed=[one for one in dict.fromkeys(asset_ids) if one != "in-hidden"]
        )


def _press_app(starter: _Pressed) -> TestClient:
    from sift.slices.music.lookup import LOOKUP_STARTER

    app = FastAPI()
    app.include_router(router)
    setattr(app.state, LOOKUP_STARTER.name, starter)
    app.dependency_overrides[require_admin] = lambda: Viewer(id="user-1", role=Role.ADMIN)
    app.dependency_overrides[csrf_protect] = lambda: None
    app.dependency_overrides[wiring.access] = _Actionable
    return TestClient(app)


@pytest.mark.parametrize(
    ("again", "queues", "left_out", "said"),
    [
        (
            False,
            1,
            2,
            "Looking up 1 file on AcoustID. Left out 2 files that have a song already, were asked"
            " before, or have no music to send.",
        ),
        (False, 3, 0, "Looking up 3 files on AcoustID."),
        (False, 0, 3, "No file here needs looking up on AcoustID."),
        (True, 2, 1, "Asking AcoustID again about 2 files."),
        (True, 0, 3, "AcoustID knew this file or was never asked about it."),
    ],
    ids=["some-left-out", "all-queued", "none-wanted", "again", "again-none"],
)
def test_an_enrich_press_sends_only_what_the_user_may_act_on_and_says_what_it_did(
    again: bool, queues: int, left_out: int, said: str
) -> None:
    """A file in Hidden is never handed to the lookup, a file named twice is one file, and the
    sentence counts what was queued and what was left out."""
    starter = _Pressed(queues=queues)
    answer = _press_app(starter).post(
        "/music/lookup/files",
        json={"asset_ids": ["a", "b", "b", "in-hidden"], "again": again},
    )

    assert answer.status_code == 200
    assert answer.json() == {
        "job_id": "job-1" if queues else None,
        "queued": queues,
        "left_out": left_out,
        "said": said,
    }
    assert starter.handed == [(["a", "b"], "user-1", again)]


@pytest.mark.parametrize(
    ("queues", "said"),
    [(4, "Asking AcoustID again about 4 files."), (0, NOTHING_TO_ASK_AGAIN)],
)
def test_ask_again_says_how_many_it_will_ask_about(queues: int, said: str) -> None:
    answer = _press_app(_Pressed(queues=queues)).post("/music/lookup/again")

    assert answer.status_code == 200
    assert answer.json()["said"] == said
    assert answer.json()["job_id"] == ("job-1" if queues else None)


@pytest.mark.parametrize(
    ("path", "body"),
    [("/music/lookup/files", {"asset_ids": ["a"]}), ("/music/lookup/again", None)],
)
def test_a_press_while_the_lookup_cannot_run_is_refused_in_words(
    path: str, body: dict[str, Any] | None
) -> None:
    answer = _press_app(_Pressed(queues=1, ready=False)).post(path, json=body)

    assert answer.status_code == 409
    assert answer.json()["detail"] == NOT_READY


def test_a_file_that_shares_its_song_with_nothing_answers_an_empty_strip() -> None:
    """Visible, and no group: the same empty answer a file the viewer may not see gets."""

    class _Access:
        async def can_view(self, viewer: Viewer, asset_id: str) -> bool:
            return True

        async def assets_of(self, viewer: Viewer, ids: list[str]) -> dict[str, object]:
            raise AssertionError("nothing to read when the group is empty")

    class _Groups:
        async def same_music_of(self, user_id: str, asset_id: str, *, reveal: bool) -> list[str]:
            return []

    from sift.slices.auth import current_viewer

    app = FastAPI()
    app.include_router(router)
    setattr(app.state, SERVICE.name, _Groups())
    app.dependency_overrides[current_viewer] = lambda: Viewer(id="user-1", role=Role.GUEST)
    app.dependency_overrides[wiring.access] = _Access

    answer = TestClient(app).get("/assets/01A/same-music")

    assert answer.status_code == 200
    assert answer.json() == {"files": [], "count": 0}
