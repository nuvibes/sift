# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making a music fingerprint and asking AcoustID are two tasks, and the first never asks.

A fingerprint is read from a file's sound on this device and sends nothing anywhere. A lookup sends
that fingerprint and the file's length to AcoustID. So the lookup is a task of its own with a When
of its own, Only when I press it out of the box, and a fingerprint's run, pressed or not, leaves
the file owed rather than asked about.

The fingerprint's path is run against a real database with the real service, names and starter;
the task's row, press, dry run and the switch on its job are read through the real application.
AcoustID is never reached: no worker runs, and the only key here is a stand-in.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sift.slices.workbench.schema  # noqa: F401 (the ledger's tables)
from sift.kernel import wiring
from sift.kernel.config import Settings, get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.jobs import BACKGROUND_PRIORITY, JobContext
from sift.kernel.jobs.quiet_hours import WHEN_PRESS, WHEN_QUIET, WHEN_WORK
from sift.kernel.jobs.schedules import when_key
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.kernel.secret_store import SecretStore
from sift.main import create_app
from sift.slices import music, tasks
from sift.slices.music.acoustid import AcoustIDClient
from sift.slices.music.jobs import AUDIO_FINGERPRINT
from sift.slices.music.lookup import (
    LOOKUP_TASK,
    MUSIC_LOOKUP,
    NOT_READY,
    LookupSettings,
    LookupStarter,
)
from sift.slices.music.names import SongNames
from sift.slices.music.service import MusicService
from sift.slices.music.settings import LOOKUP_KEY
from sift.slices.music.store import Kept, MusicStore, NameStore
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]

_EPOCH = 1_700_000_000
_MASTER = bytes(range(32))

_ASSET = """
INSERT INTO assets
  (id, identity, media_type, size_bytes, original_filename, added_at, acodec, probed_at,
   duration_ms)
VALUES (?, ?, 'video', 10, ?, ?, 'aac', 1, ?)
"""


class _Settings:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    async def get_app(self, key: str) -> Any:
        return self.values.get(key)

    async def get_user(self, user_id: str, key: str) -> Any:  # pragma: no cover
        raise AssertionError("no per-user setting here")


@dataclass
class _Asset:
    id: str
    identity: str
    acodec: str | None
    duration_ms: int


class _Content:
    def __init__(self, asset: _Asset) -> None:
        self._asset = asset

    async def get(self, asset_id: str) -> _Asset | None:
        return self._asset if asset_id == self._asset.id else None


def _context(*, pressed: bool) -> JobContext:
    """A fingerprint job's context: Run now's, marked as a press, or one nobody pressed."""
    job = SimpleNamespace(
        timing="now" if pressed else None, requested_by="user-1" if pressed else None, priority=1
    )
    asset = _Asset("pmv", "identity-pmv", "aac", 187_000)
    return cast(
        JobContext,
        SimpleNamespace(payload={"asset_id": "pmv"}, content=_Content(asset), job=job),
    )


@dataclass
class _Ran:
    """What one fingerprint run did beyond the fingerprint: who was asked, and what was queued."""

    considered: list[str]
    queued: list[tuple[str, dict[str, Any], dict[str, Any]]]
    starter: LookupStarter


async def _fingerprint(tmp_path: Path, settings: dict[str, Any], *, pressed: bool) -> _Ran:
    """Fingerprint one full-length file through the real service with the lookup allowed and a key
    set, the way an install with the lookup turned on does."""
    database = Database(tmp_path / "two-tasks.sqlite3")
    await database.connect()
    await database.initialize_schema()
    try:
        async with database.write() as connection:
            await connection.execute(_ASSET, ("pmv", "identity-pmv", "pmv.mp4", _EPOCH, 187_000))
        names, store = NameStore(database), MusicStore(database)
        await LookupSettings(
            names, SecretStore(database), cast(AcoustIDClient, None), _Settings({})
        ).set_key("k", _MASTER)
        queued: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

        async def enqueue(job_type: str, payload: dict[str, Any], **options: Any) -> str:
            queued.append((job_type, payload, options))
            return "job"

        starter = LookupStarter(names, _Settings(settings), enqueue=enqueue)
        considered: list[str] = []

        async def consider(asset_id: str) -> bool:
            considered.append(asset_id)
            return await starter.consider(asset_id)

        song_names = SongNames(names, group_of=store.music_group, after_spread=consider)

        async def allowed(job_type: str, asset_id: str | None = None) -> bool:
            return True

        service = MusicService(
            store=store,
            allowed=allowed,
            job_type=AUDIO_FINGERPRINT,
            on_pairs_settled=song_names.on_pairs_settled,
        )
        # Taken at staging, so the run claims it and opens nothing: the path every arrival takes.
        await store.keep_pending(
            "identity-pmv",
            Kept(
                algorithm=1,
                tool="ffmpeg version 7.1.5",
                duration_ms=184_000,
                offset_ms=0,
                fingerprint=struct.pack("<3I", 1, 2, 3),
                computed_at=_EPOCH,
            ),
        )
        await service.fingerprint(_context(pressed=pressed), settings=Settings())
        assert await store.has("pmv"), "the fingerprint was made"
        return _Ran(considered=considered, queued=queued, starter=starter)
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("pressed", [True, False], ids=["pressed", "by itself"])
@pytest.mark.parametrize(
    "when", [None, WHEN_PRESS], ids=["the lookup's When never chosen", "Only when I press it"]
)
async def test_a_fingerprint_run_pressed_or_by_itself_asks_acoustid_nothing(
    tmp_path: Path, pressed: bool, when: str | None
) -> None:
    """The lookup is on and has a key, and the lookup task's When is the one every install starts
    with: making the fingerprint queues no lookup and sends nothing, whoever started it. The file is
    left owed, for a press of the lookup task."""
    settings: dict[str, Any] = {LOOKUP_KEY: True}
    if when is not None:
        settings[when_key(LOOKUP_TASK)] = when
    ran = await _fingerprint(tmp_path, settings, pressed=pressed)
    assert ran.considered == ["pmv"], "the pairing settled and the starter was asked"
    assert ran.queued == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pressed", [True, False], ids=["pressed", "by itself"])
@pytest.mark.parametrize("when", [WHEN_WORK, WHEN_QUIET])
async def test_a_when_somebody_chose_queues_the_lookup_tasks_own_job_and_nothing_is_sent_inline(
    tmp_path: Path, pressed: bool, when: str
) -> None:
    """As files arrive, or During quiet hours: the fingerprint queues the lookup task's own job,
    unpressed whatever started the fingerprint, so its own When decides when it runs, and the
    fingerprint job itself sends nothing (nothing in it holds AcoustID's client)."""
    ran = await _fingerprint(
        tmp_path, {LOOKUP_KEY: True, when_key(LOOKUP_TASK): when}, pressed=pressed
    )
    assert ran.queued == [
        (MUSIC_LOOKUP, {"asset_id": "pmv"}, {"priority": BACKGROUND_PRIORITY, "dedupe": True})
    ]


# --- through the real application -----------------------------------------------------------------

TASKS = "/api/tasks"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    # No workers: every check reads what is WAITING, and nothing may run a lookup.
    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _sign_in(client: TestClient) -> str:
    database = client.app.state.database  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        database.path, role="admin", username="lookup-admin", password="Lookup-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return str(user_id)


def _rows(client: TestClient) -> dict[str, dict[str, Any]]:
    answer = client.get(TASKS)
    assert answer.status_code == 200
    return {one["id"]: one for one in answer.json()["tasks"]}


def _jobs(client: TestClient, job_type: str) -> list[Any]:
    portal = client.portal
    assert portal is not None, "the client is used inside its context, where its portal is open"
    database = client.app.state.database  # type: ignore[attr-defined]
    return list(
        portal.call(
            database.fetch_all,
            "SELECT requested_by, timing, parent_id FROM jobs WHERE type = ?",
            (job_type,),
        )
    )


def _as_a_press(refusal: Any) -> Any:
    """`Switchboard.refusal` asked for a press, as a callable the portal calls with one argument."""

    async def pressed(job_type: str) -> str | None:
        return cast(str | None, await refusal(job_type, pressed=True))

    return pressed


def test_the_lookup_is_a_task_beside_the_fingerprints_pressed_only_and_refused_in_words(
    client: TestClient,
) -> None:
    """Its own row after the fingerprints', its own When (Only when I press it), a dry run, and
    a press refused in words while the switch is off. Its job is refused on its own under that
    When, at the enqueue and at the claim, and never when pressed."""
    user_id = _sign_in(client)
    rows = _rows(client)
    order = list(rows)
    assert order.index(LOOKUP_TASK) == order.index("music") + 1
    lookup = rows[LOOKUP_TASK]
    assert lookup["title"] == "Name songs with AcoustID"
    assert "music fingerprint and length to AcoustID" in lookup["explain"]
    assert (lookup["when"], lookup["on"]) == (WHEN_PRESS, False)
    assert [one["value"] for one in lookup["whens"]] == [WHEN_WORK, WHEN_QUIET, WHEN_PRESS]
    assert lookup["dry"] is True and lookup["set_in"] == "music"
    assert lookup["off"] == {"key": LOOKUP_KEY, "section": "Music"}
    assert rows["music"]["when"] == WHEN_PRESS

    refused = client.post(f"{TASKS}/{LOOKUP_TASK}/run", json={"at": "now"})
    assert refused.status_code == 409
    assert refused.json()["detail"] == NOT_READY
    assert _jobs(client, music.MUSIC_LOOKUP_CATCH_UP) == []

    portal = client.portal
    assert portal is not None
    service = wiring.part_of_app(client.app, tasks.SERVICE)  # type: ignore[arg-type]
    report = portal.call(service.rehearse, LOOKUP_TASK, tasks.EVERYTHING, user_id)
    assert report.said.startswith("Name songs with AcoustID would do nothing on this device now.")
    assert report.refusals == (NOT_READY,)

    switchboard = wiring.part_of_app(client.app, wiring.QUEUE).switchboard  # type: ignore[arg-type]
    assert portal.call(switchboard.refusal, MUSIC_LOOKUP) is not None
    assert portal.call(_as_a_press(switchboard.refusal), MUSIC_LOOKUP) is None
    chosen = client.put("/api/settings", json={"values": {when_key(LOOKUP_TASK): WHEN_WORK}})
    assert chosen.status_code == 204
    assert portal.call(switchboard.refusal, MUSIC_LOOKUP) is None


def test_a_press_with_the_lookup_allowed_queues_one_walk_as_that_press_and_the_dry_run_counts(
    client: TestClient, tmp_path: Path
) -> None:
    """Run now queues one walk, named for the person and marked now, and nothing else; the dry run
    counts and names the file it would ask about and queues and sends nothing."""
    user_id = _sign_in(client)
    portal = client.portal
    assert portal is not None
    database = client.app.state.database  # type: ignore[attr-defined]
    root_id, asset_id = new_id(), new_id()
    for statement, values in (
        (
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (root_id, "songs", str(tmp_path / "songs"), _EPOCH),
        ),
        (
            "INSERT INTO assets (id, identity, media_type, width, height, duration_ms,"
            " size_bytes, original_filename, added_at, probed_at)"
            " VALUES (?, ?, 'video', 1920, 1080, 187000, 14, 'mix.mp4', ?, ?)",
            (asset_id, "digest-mix", _EPOCH, _EPOCH),
        ),
        (
            "INSERT INTO asset_locations (id, asset_id, root_id, rel_path, filename,"
            " first_seen_at, last_seen_at) VALUES (?, ?, ?, 'mix.mp4', 'mix.mp4', 1, 1)",
            (new_id(), asset_id, root_id),
        ),
        (
            "INSERT INTO audio_fingerprints"
            " (asset_id, algorithm, tool, duration_ms, offset_ms, fingerprint, computed_at)"
            " VALUES (?, 1, 'ffmpeg', 184000, 0, ?, 1)",
            (asset_id, struct.pack("<3I", 1, 2, 3)),
        ),
    ):
        portal.call(database.execute, statement, values)
    assert client.put("/api/settings", json={"values": {LOOKUP_KEY: True}}).status_code == 204
    lookup = wiring.part_of_app(client.app, music.LOOKUP)  # type: ignore[arg-type]
    portal.call(lookup.set_key, "stand-in", _MASTER)

    pressed = client.post(f"{TASKS}/{LOOKUP_TASK}/run", json={"at": "now"})

    assert pressed.status_code == 200, pressed.text
    walks = _jobs(client, music.MUSIC_LOOKUP_CATCH_UP)
    assert [(row["requested_by"], row["timing"]) for row in walks] == [(user_id, "now")]
    assert _jobs(client, MUSIC_LOOKUP) == [], "the walk queues the lookups when it runs"

    service = wiring.part_of_app(client.app, tasks.SERVICE)  # type: ignore[arg-type]
    report = portal.call(service.rehearse, LOOKUP_TASK, tasks.EVERYTHING, user_id)
    assert report.said.startswith(
        "Name songs with AcoustID would ask AcoustID about 1 file, sending each one's music "
        "fingerprint and length."
    ), report.said
    assert report.names == ("mix.mp4",)
    assert _jobs(client, MUSIC_LOOKUP) == []
