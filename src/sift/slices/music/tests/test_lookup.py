# SPDX-License-Identifier: AGPL-3.0-or-later
"""The AcoustID lookup as a task: when it sends nothing, the lengths it tries, what it keeps.

Against a real database; AcoustID is a client this file stands in for, so what is held here is
which requests WOULD have left and what the library says afterwards. No network.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the ledger's tables)
from sift.kernel import db as db_module
from sift.kernel.db import Database, statement_name
from sift.kernel.jobs import JobBlocked, JobContext, JobFailedPermanently
from sift.kernel.jobs.quiet_hours import WHEN_PRESS, WHEN_WORK
from sift.kernel.jobs.schedules import when_key
from sift.kernel.secret_store import SecretStore
from sift.slices.music.acoustid import (
    AcoustIDClient,
    AcoustIDRefused,
    LookupAnswer,
    Recording,
    Result,
)
from sift.slices.music.lookup import (
    LENGTH_STEPS,
    LOOKUP_TASK,
    NOT_READY,
    SHORTEST_MS,
    LookupNotReady,
    LookupSettings,
    LookupStarter,
    LookupTask,
    lengths_for,
)
from sift.slices.music.settings import LOOKUP_KEY, LOOKUP_ROUTE_KEY
from sift.slices.music.store import LookupKept, MusicStore, NameStore

pytestmark = [pytest.mark.integration]

_EPOCH = 1_700_000_000
_MASTER = bytes(range(32))

_ASSET = """
INSERT INTO assets
  (id, identity, media_type, size_bytes, original_filename, added_at, duration_ms, music)
VALUES (?, ?, 'video', 10, ?, ?, ?, ?)
"""
_FINGERPRINT = """
INSERT INTO audio_fingerprints
  (asset_id, algorithm, tool, duration_ms, offset_ms, fingerprint, computed_at)
VALUES (?, 1, 'ffmpeg', ?, 0, ?, 1)
"""

#: `music_lookup_key` belongs to the music schema; where it is absent the test stands it up itself
#: so the rest can be proved. `test_the_key_table_ships` below is what says whether it ships.
_KEY_TABLE = """
CREATE TABLE IF NOT EXISTS music_lookup_key (
  id        INTEGER PRIMARY KEY CHECK (id = 1),
  secret_id TEXT NOT NULL,
  set_at    INTEGER NOT NULL
)
"""


class _Settings:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    async def get_app(self, key: str) -> Any:
        return self.values.get(key)

    async def get_user(self, user_id: str, key: str) -> Any:  # pragma: no cover
        raise AssertionError("no per-user setting here")


class _Asset:
    def __init__(self, duration_ms: int) -> None:
        self.duration_ms = duration_ms


class _Content:
    def __init__(self, duration_ms: int) -> None:
        self._duration_ms = duration_ms

    async def get(self, asset_id: str) -> _Asset:
        return _Asset(self._duration_ms)


class _Context:
    def __init__(self, asset_id: str, *, master: bytes | None, duration_ms: int) -> None:
        self.payload = {"asset_id": asset_id}
        self.content = _Content(duration_ms)
        self._master = master
        self.progress: list[float] = []

    async def master_key(self) -> bytes | None:
        return self._master

    async def set_progress(self, value: float) -> None:
        self.progress.append(value)


class _Walk:
    """The context a press's walk is handed: its own row, and the children it queues under it."""

    def __init__(self, *, priority: int = 7) -> None:
        self.job = SimpleNamespace(priority=priority, payload={})
        self.children: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.notes: list[str] = []
        self.progress: list[float] = []

    async def enqueue_child(self, job_type: str, payload: dict[str, Any], **options: Any) -> str:
        self.children.append((job_type, payload, options))
        return "child"

    async def set_note(self, note: str) -> None:
        self.notes.append(note)

    async def set_progress(self, value: float) -> None:
        self.progress.append(value)


#: The settings of an install that allowed the lookup and chose As files arrive for its task.
_ARRIVING = {LOOKUP_KEY: True, when_key(LOOKUP_TASK): WHEN_WORK}


class _AcoustID:
    """Answers each length from a table; records every ask. Stands where the client would send."""

    def __init__(self, answers: dict[int, LookupAnswer | Exception]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, int, str | None]] = []

    async def ask(self, key: str, fingerprint: str, duration: int, *, route: str | None) -> Any:
        self.asked.append((key, duration, route))
        found = self.answers.get(duration, LookupAnswer(results=()))
        if isinstance(found, Exception):
            raise found
        return found


def _named(title: str = "Blue", artists: str = "Marla Quist") -> LookupAnswer:
    return LookupAnswer(
        results=(Result(id="r", score=0.9, recordings=(Recording("rec-1", title, artists),)),)
    )


async def _library(tmp_path: Path, *, duration_ms: int = 187_000) -> Database:
    database = Database(tmp_path / "lookup.sqlite3")
    await database.connect()
    await database.initialize_schema()
    await database.execute(_KEY_TABLE)
    values = struct.pack("<3I", 1, 2, 3)
    async with database.write() as connection:
        await connection.execute(_ASSET, ("pmv", "i-pmv", "pmv.mp4", _EPOCH, duration_ms, None))
        await connection.execute(_FINGERPRINT, ("pmv", duration_ms - 3000, values))
    return database


async def _task(
    database: Database, client: _AcoustID, settings: dict[str, Any], *, key: str | None = "k"
) -> tuple[LookupTask, list[str]]:
    names = NameStore(database)
    secrets = SecretStore(database)
    if key is not None:
        await LookupSettings(names, secrets, cast(AcoustIDClient, client), _Settings({})).set_key(
            key, _MASTER
        )
    spread: list[str] = []

    async def spread_from(asset_id: str) -> list[str]:
        spread.append(asset_id)
        return []

    async def touched(asset_ids: object) -> None:
        return None

    task = LookupTask(
        names,
        _Settings(settings),
        secrets,
        cast(AcoustIDClient, client),
        fingerprint_of=MusicStore(database).fingerprint_of,
        spread=spread_from,
        touched=touched,
    )
    return task, spread


def _context(*, master: bytes | None = _MASTER, duration_ms: int = 187_000) -> JobContext:
    return cast(JobContext, _Context("pmv", master=master, duration_ms=duration_ms))


def test_the_lengths_are_the_files_own_then_thirteen_either_side() -> None:
    """A late answer can come 13 s over the file's length (187 s -> 200 s); AcoustID reaches
    7 s either side of what it is sent, so three lookups cover 20 s either way."""
    assert LENGTH_STEPS == (0, -13, 13)
    assert lengths_for(187_000) == (187, 174, 200)
    assert lengths_for(10_400) == (10, 23)


@pytest.mark.asyncio
async def test_the_switch_is_asked_when_the_task_runs_and_nothing_is_sent(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    client = _AcoustID({187: _named()})
    try:
        task, _spread = await _task(database, client, {LOOKUP_KEY: False})
        await task.run(_context())
        assert client.asked == []
        assert await NameStore(database).lookup_of("pmv") is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_no_key_sends_nothing_and_a_locked_key_waits_for_a_sign_in(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    client = _AcoustID({187: _named()})
    try:
        task, _spread = await _task(database, client, {LOOKUP_KEY: True}, key=None)
        await task.run(_context())
        assert client.asked == []
        task, _spread = await _task(database, client, {LOOKUP_KEY: True})
        with pytest.raises(JobBlocked):
            await task.run(_context(master=None))
        assert client.asked == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_kept_local_is_never_looked_up(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    client = _AcoustID({187: _named()})
    try:
        await database.execute("UPDATE assets SET keep_local = 1 WHERE id = 'pmv'")
        task, _spread = await _task(database, client, {LOOKUP_KEY: True})
        await task.run(_context())
        assert client.asked == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_lengths_are_tried_in_order_and_the_first_name_stops_them(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    client = _AcoustID({200: _named()})
    try:
        task, spread = await _task(
            database, client, {LOOKUP_KEY: True, LOOKUP_ROUTE_KEY: "tunnel-1"}
        )
        await task.run(_context())
        assert [(duration, route) for _key, duration, route in client.asked] == [
            (187, "tunnel-1"),
            (174, "tunnel-1"),
            (200, "tunnel-1"),
        ]
        assert client.asked[0][0] == "k"
        kept = await NameStore(database).lookup_of("pmv")
        assert kept is not None
        assert (kept.status, kept.lengths_sent, kept.recording_id) == (
            "named",
            (187, 174, 200),
            "rec-1",
        )
        row = await database.fetch_one("SELECT music FROM assets WHERE id = 'pmv'")
        assert row is not None and row["music"] == "Blue - Marla Quist"
        # The file is on the recording's song, which says AcoustID put it there (`song_files`).
        source = await database.fetch_one(
            "SELECT f.source, s.recording_id FROM song_files f JOIN songs s ON s.id = f.song_id"
            " WHERE f.asset_id = 'pmv'"
        )
        assert source is not None
        assert (source["source"], source["recording_id"]) == ("acoustid", "rec-1")
        # And the name goes on to the files sharing the song.
        assert spread == ["pmv"]
        # One answer kept is one answer: the file is not asked about again.
        assert not await NameStore(database).wants_lookup("pmv", shortest_ms=SHORTEST_MS)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_first_length_that_names_a_song_is_the_last_one_sent(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    client = _AcoustID({174: _named(), 200: _named("Red")})
    try:
        task, _spread = await _task(database, client, {LOOKUP_KEY: True})
        await task.run(_context())
        assert [duration for _key, duration, _route in client.asked] == [187, 174]
        row = await database.fetch_one("SELECT music FROM assets WHERE id = 'pmv'")
        assert row is not None and row["music"] == "Blue - Marla Quist"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_nothing_named_is_kept_as_nothing_and_a_refusal_as_a_refusal(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        task, _spread = await _task(database, _AcoustID({}), {LOOKUP_KEY: True})
        await task.run(_context())
        kept = await NameStore(database).lookup_of("pmv")
        assert kept is not None and (kept.status, kept.lengths_sent) == ("nothing", (187, 174, 200))
        await database.execute("DELETE FROM music_lookups")
        refusing = _AcoustID({187: AcoustIDRefused("AcoustID refused the lookup: bad key.")})
        task, _spread = await _task(database, refusing, {LOOKUP_KEY: True})
        with pytest.raises(JobFailedPermanently, match="bad key"):
            await task.run(_context())
        kept = await NameStore(database).lookup_of("pmv")
        assert kept is not None and (kept.status, kept.lengths_sent) == ("refused", (187,))
        # A refusal is asked again; an answer is not.
        assert await NameStore(database).wants_lookup("pmv", shortest_ms=SHORTEST_MS)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_starter_queues_only_with_the_switch_a_key_and_a_file_worth_asking(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    queued: list[tuple[Any, ...]] = []

    async def enqueue(*args: Any, **kwargs: Any) -> str:
        queued.append((args, kwargs))
        return "job"

    try:
        names = NameStore(database)
        off = LookupStarter(names, _Settings({**_ARRIVING, LOOKUP_KEY: False}), enqueue=enqueue)
        assert not await off.consider("pmv")
        on = LookupStarter(names, _Settings(_ARRIVING), enqueue=enqueue)
        assert not await on.consider("pmv")  # no key yet
        await LookupSettings(
            names, SecretStore(database), cast(AcoustIDClient, None), _Settings({})
        ).set_key("k", _MASTER)
        # The switch and a key say what MAY be sent; the lookup task's When says when. Pressed only,
        # or never chosen, a settling pairing queues nothing.
        for unchosen in ({LOOKUP_KEY: True}, {**_ARRIVING, when_key(LOOKUP_TASK): WHEN_PRESS}):
            pressed_only = LookupStarter(names, _Settings(unchosen), enqueue=enqueue)
            assert not await pressed_only.starts_on_its_own()
            assert not await pressed_only.consider("pmv")
        assert queued == []
        assert await on.consider("pmv")
        assert queued[0][0] == ("music_lookup", {"asset_id": "pmv"})
        await database.execute("UPDATE assets SET duration_ms = 12000 WHERE id = 'pmv'")
        assert not await on.consider("pmv")  # a clip is not worth a request
        await database.execute(
            "UPDATE assets SET duration_ms = 187000, music = 'Blue - Marla Quist' WHERE id = 'pmv'"
        )
        assert not await on.consider("pmv")  # a file that carries a song already
        assert not await on.consider("gone")  # nor a file that is not there
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_files_fingerprinted_before_the_lookup_are_counted_and_caught_up_on_a_press(
    tmp_path: Path,
) -> None:
    """A library fingerprinted first and allowed the lookup afterwards would have nothing that ever
    asked about it: a file is considered where its pairing settles, once. The count says how many
    are owed, and only the catch-up's press queues them, through the same question a settling
    pairing asks, so a file with a song, a clip, or one already answered is left alone."""
    database = await _library(tmp_path)
    values = struct.pack("<3I", 1, 2, 3)
    async with database.write() as connection:
        # Owed, like `pmv`.
        await connection.execute(_ASSET, ("pmv-2", "i-2", "two.mp4", _EPOCH, 200_000, None))
        await connection.execute(_FINGERPRINT, ("pmv-2", 197_000, values))
        # A song already on it; a clip; a fingerprint with nothing in it.
        await connection.execute(_ASSET, ("sung", "i-3", "three.mp4", _EPOCH, 200_000, "Blue"))
        await connection.execute(_FINGERPRINT, ("sung", 197_000, values))
        await connection.execute(_ASSET, ("clip", "i-4", "four.mp4", _EPOCH, 12_000, None))
        await connection.execute(_FINGERPRINT, ("clip", 11_000, values))
        await connection.execute(_ASSET, ("silent", "i-5", "five.mp4", _EPOCH, 200_000, None))
        await connection.execute(_FINGERPRINT, ("silent", 0, b""))
    queued: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(job_type: str, payload: dict[str, Any], **_: Any) -> str:
        queued.append((job_type, payload))
        return "job"

    try:
        names = NameStore(database)
        off = LookupStarter(names, _Settings({LOOKUP_KEY: False}), enqueue=enqueue)
        # Pressed only (no When chosen): a press is still a press.
        on = LookupStarter(names, _Settings({LOOKUP_KEY: True}), enqueue=enqueue)

        async def press(starter: LookupStarter) -> str | None:
            return await starter.start_catch_up(at="now", requested_by="user-1", priority=3)

        # Nothing is owed, and nothing starts, while nothing could be asked: refused in words.
        assert await on.owed() == 0
        assert await on.cannot_run() == NOT_READY
        with pytest.raises(LookupNotReady, match="Turn on Name songs with AcoustID"):
            await press(on)
        await LookupSettings(
            names, SecretStore(database), cast(AcoustIDClient, None), _Settings({})
        ).set_key("k", _MASTER)
        assert await off.owed() == 0
        with pytest.raises(LookupNotReady):
            await press(off)
        assert queued == []

        assert await on.owed() == 2
        assert await on.cannot_run() is None
        assert await press(on) == "job"
        assert queued == [("music_lookup_catch_up", {})]
        queued.clear()
        # THE WALK QUEUES EACH LOOKUP UNDER ITSELF, so each carries the press, and nothing through
        # the starter's own enqueue, which is the unpressed door a settling pairing uses.
        walk = _Walk(priority=3)
        await on.catch_up(cast(JobContext, walk))
        assert queued == []
        assert sorted(payload["asset_id"] for _, payload, _ in walk.children) == ["pmv", "pmv-2"]
        assert {job_type for job_type, _, _ in walk.children} == {"music_lookup"}
        assert {options["priority"] for _, _, options in walk.children} == {3}
        assert walk.notes == ["Queued 2 files to be looked up on AcoustID."]
        planned = await on.plan(first=1)
        assert (planned.files, planned.first) == (2, ("pmv",))
        assert (await off.plan(first=5)).files == 0

        # An answer kept takes a file off the count.
        await database.execute(
            "INSERT INTO music_lookups (asset_id, looked_up_at, lengths_sent, status)"
            " VALUES ('pmv', 1, '[187]', 'nothing')"
        )
        assert await on.owed() == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_key_is_sealed_replaced_and_removed_and_never_read_back(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        names, secrets = NameStore(database), SecretStore(database)
        lookup = LookupSettings(names, secrets, cast(AcoustIDClient, None), _Settings({}))
        await lookup.set_key("first", _MASTER)
        first = await names.key_id()
        await lookup.set_key("second", _MASTER)
        assert await secrets.open(str(first), _MASTER) is None  # the old one is forgotten
        state = await lookup.state(_MASTER)
        assert state == {"on": False, "key_set": True, "key_ready": True, "route": None}
        assert "second" not in repr(state)
        assert (await lookup.state(None))["key_ready"] is False
        assert await lookup.forget_key()
        assert (await lookup.state(_MASTER))["key_set"] is False
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_key_table_ships(tmp_path: Path) -> None:
    """The music schema carries `music_lookup_key`; the fixture above only stands it up until then."""
    database = Database(tmp_path / "schema.sqlite3")
    await database.connect()
    await database.initialize_schema()
    try:
        found = await database.fetch_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'music_lookup_key'"
        )
        assert found is not None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_press_of_enrich_on_some_files_asks_only_the_ones_that_want_it(
    tmp_path: Path,
) -> None:
    """AcoustID as an Enrich choice: the lookup task pressed for THOSE files, as one walk carrying
    them, each lookup its child. A file with a song, one AcoustID already answered (a song it did
    not know is never asked again by a press), and one that is not a song are left out; and the
    press is refused in words while the lookup is off or has no key, as every press is."""
    database = await _library(tmp_path)
    values = struct.pack("<3I", 1, 2, 3)
    async with database.write() as connection:
        await connection.execute(_ASSET, ("sung", "i-3", "three.mp4", _EPOCH, 200_000, "Blue"))
        await connection.execute(_FINGERPRINT, ("sung", 197_000, values))
        await connection.execute(_ASSET, ("known", "i-4", "four.mp4", _EPOCH, 200_000, None))
        await connection.execute(_FINGERPRINT, ("known", 197_000, values))
    queued: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def enqueue(job_type: str, payload: dict[str, Any], **options: Any) -> str:
        queued.append((job_type, payload, options))
        return "job"

    try:
        names = NameStore(database)
        await names.keep_lookup("known", LookupKept(status="nothing", lengths_sent=(187,)))
        starter = LookupStarter(names, _Settings({LOOKUP_KEY: True}), enqueue=enqueue)
        with pytest.raises(LookupNotReady, match="Turn on Name songs with AcoustID"):
            await starter.start_for_files(["pmv"], requested_by="user-1")
        await LookupSettings(
            names, SecretStore(database), cast(AcoustIDClient, None), _Settings({})
        ).set_key("k", _MASTER)
        answer = await starter.start_for_files(["pmv", "sung", "known", "pmv"], requested_by="u-1")
        assert answer == ("job", 1)
        [(job_type, payload, options)] = queued
        # One walk, carrying the files and the press (who asked), never the library's walk.
        assert (job_type, payload) == ("music_lookup_catch_up", {"files": ["pmv"]})
        assert options["requested_by"] == "u-1"
        walk = _Walk(priority=3)
        walk.job.payload = payload
        await starter.catch_up(cast(JobContext, walk))
        assert [(one, child["asset_id"]) for one, child, _ in walk.children] == [
            ("music_lookup", "pmv")
        ]
        # Nothing here wants a lookup: nothing is queued, and the press says so.
        queued.clear()
        assert await starter.start_for_files(["sung", "known"], requested_by="u-1") == (None, 0)
        assert queued == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_page_of_owed_files_leaves_out_one_that_carries_a_song(tmp_path: Path) -> None:
    """The page itself, before any file on it is weighed: a file that carries a song comes off it,
    asked of the kernel, and the next page still starts after the last id read."""
    database = await _library(tmp_path)
    async with database.write() as connection:
        await connection.execute(_ASSET, ("sung", "i-3", "three.mp4", _EPOCH, 200_000, "Blue"))
        await connection.execute(_FINGERPRINT, ("sung", 197_000, struct.pack("<3I", 1, 2, 3)))
    try:
        page = await NameStore(database).owed_lookups(after="", limit=10, shortest_ms=SHORTEST_MS)
        assert (page.files, page.last) == (("pmv",), "sung")
    finally:
        await database.close()


async def test_a_file_kept_local_is_never_queued_nor_caught_up(tmp_path: Path) -> None:
    """The starter keeps the promise the task keeps: nothing about a file kept local leaves this
    device, so it is not even queued, by a settling pairing or by the catch-up."""
    database = await _library(tmp_path)
    queued: list[str] = []

    async def enqueue(job_type: str, payload: dict[str, Any], **_: Any) -> str:
        queued.append(str(payload.get("asset_id")))
        return "job"

    try:
        names = NameStore(database)
        await LookupSettings(
            names, SecretStore(database), cast(AcoustIDClient, None), _Settings({})
        ).set_key("k", _MASTER)
        await database.execute("UPDATE assets SET keep_local = 1 WHERE id = 'pmv'")
        on = LookupStarter(names, _Settings(_ARRIVING), enqueue=enqueue)

        assert not await on.consider("pmv")
        walk = _Walk()
        await on.catch_up(cast(JobContext, walk))
        assert queued == [] and walk.children == []
        assert walk.notes == ["No file was waiting to be looked up on AcoustID."]
        assert (await on.plan(first=5)).files == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_lookup_queued_without_its_file_is_a_bug_and_says_so(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        task, _spread = await _task(database, _AcoustID({}), {LOOKUP_KEY: True})
        context = cast(JobContext, _Context("", master=_MASTER, duration_ms=187_000))
        with pytest.raises(ValueError, match="needs the id of its file"):
            await task.run(context)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_key_that_will_not_open_sends_nothing(tmp_path: Path) -> None:
    """Sealed under another password (a restored backup, say): nothing is sent and nothing is
    kept, so the file is asked about again once the key is entered."""
    database = await _library(tmp_path)
    client = _AcoustID({187: _named()})
    try:
        task, _spread = await _task(database, client, {LOOKUP_KEY: True})
        await task.run(_context(master=bytes(32)))
        assert client.asked == []
        assert await NameStore(database).lookup_of("pmv") is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_that_gained_a_song_since_it_was_queued_sends_nothing(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    client = _AcoustID({187: _named()})
    try:
        await database.execute("UPDATE assets SET music = 'Blue - Marla Quist' WHERE id = 'pmv'")
        task, _spread = await _task(database, client, {LOOKUP_KEY: True})
        await task.run(_context())
        assert client.asked == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_whose_fingerprint_holds_nothing_sends_nothing(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    client = _AcoustID({187: _named()})
    try:
        await database.execute(
            "UPDATE audio_fingerprints SET fingerprint = x'' WHERE asset_id = 'pmv'"
        )
        task, _spread = await _task(database, client, {LOOKUP_KEY: True})
        await task.run(_context())
        assert client.asked == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_fingerprint_gone_before_the_lookup_runs_sends_nothing(tmp_path: Path) -> None:
    """The row said a lookup was wanted; by the time the job runs the fingerprint is gone (re-made
    under another algorithm, its file deleted). Nothing to send, and nothing to raise about."""
    database = await _library(tmp_path)
    client = _AcoustID({187: _named()})

    async def gone(asset_id: str) -> None:
        return None

    try:
        task, _spread = await _task(database, client, {LOOKUP_KEY: True})
        task._fingerprint_of = gone
        await task.run(_context())
        assert client.asked == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_song_taken_off_the_file_while_acoustid_was_asked_is_not_put_back(
    tmp_path: Path,
) -> None:
    """The refusal is read inside the write, so one made while the answer was on its way still
    holds. The answer is kept (the file is not asked about again), but the name is not written
    and goes to no other file: a person's No outranks AcoustID's Yes."""
    database = await _library(tmp_path)

    class _RefusedMeanwhile(_AcoustID):
        async def ask(self, key: str, fingerprint: str, duration: int, *, route: str | None) -> Any:
            async with database.write() as connection:
                await NameStore.refuse_on(connection, "pmv", "Blue - Marla Quist")
            return await super().ask(key, fingerprint, duration, route=route)

    try:
        task, spread = await _task(database, _RefusedMeanwhile({187: _named()}), {LOOKUP_KEY: True})
        await task.run(_context())

        kept = await NameStore(database).lookup_of("pmv")
        assert kept is not None and kept.status == "named"
        row = await database.fetch_one("SELECT music FROM assets WHERE id = 'pmv'")
        assert row is not None and row["music"] is None
        assert spread == []
    finally:
        await database.close()


def test_the_lookup_and_its_catch_up_are_claimed_under_their_names() -> None:
    """The catch-up walks one at a time: a second press while one runs would queue nothing new."""

    from sift.kernel.jobs.worker_pool import (
        registered_alone,
        registered_handlers,
        registered_job_names,
    )
    from sift.slices.music.lookup import MUSIC_LOOKUP, MUSIC_LOOKUP_CATCH_UP, register_handlers

    async def nothing(context: object) -> None:
        return None

    task = cast(LookupTask, SimpleNamespace(run=nothing))
    starter = cast(LookupStarter, SimpleNamespace(catch_up=nothing))
    register_handlers(task, starter)

    assert {MUSIC_LOOKUP, MUSIC_LOOKUP_CATCH_UP} <= set(registered_handlers())
    assert registered_job_names()[MUSIC_LOOKUP] == "Looking up song on AcoustID"
    assert MUSIC_LOOKUP_CATCH_UP in registered_alone()
    assert MUSIC_LOOKUP not in registered_alone()


@pytest.mark.asyncio
async def test_an_empty_key_is_refused_and_forgetting_no_key_says_there_was_none(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        lookup = LookupSettings(
            NameStore(database), SecretStore(database), cast(AcoustIDClient, None), _Settings({})
        )
        with pytest.raises(ValueError, match="cannot be empty"):
            await lookup.set_key("   ", _MASTER)
        assert await lookup.forget_key() is False
    finally:
        await database.close()


class _Checks:
    """The client's key check, answering yes and writing down which key and route it was handed."""

    def __init__(self) -> None:
        self.checked: list[tuple[str, str | None]] = []

    async def check(self, key: str, *, route: str | None) -> tuple[bool, str]:
        self.checked.append((key, route))
        return True, "AcoustID accepted the key."


@pytest.mark.asyncio
async def test_checking_the_key_says_why_it_cannot_be_checked_and_otherwise_asks(
    tmp_path: Path,
) -> None:
    """No key, a key locked since a restart, a key that will not open: each is a sentence and
    nothing is sent. An open key is checked through the route the lookup would use."""
    database = await _library(tmp_path)
    client = _Checks()
    try:
        names, secrets = NameStore(database), SecretStore(database)
        lookup = LookupSettings(
            names, secrets, cast(AcoustIDClient, client), _Settings({LOOKUP_ROUTE_KEY: "tunnel-1"})
        )
        assert await lookup.check(_MASTER) == (False, "No AcoustID key is set.")
        await lookup.set_key("the-key", _MASTER)
        locked = await lookup.check(None)
        assert locked[0] is False and "locked because Sift restarted" in locked[1]
        assert await lookup.check(bytes(32)) == (
            False,
            "The AcoustID key cannot be read. Delete it and enter it again.",
        )
        assert client.checked == []

        assert await lookup.check(_MASTER) == (True, "AcoustID accepted the key.")
        assert client.checked == [("the-key", "tunnel-1")]
    finally:
        await database.close()


@pytest.mark.parametrize(
    ("given", "stored"),
    [(None, None), ("direct", None), ("  ", None), (" tunnel-1 ", "tunnel-1"), ("wg_2", "wg_2")],
)
def test_the_route_is_a_tunnels_id_or_nothing_for_direct(given: object, stored: object) -> None:
    from sift.kernel.settings_registry import get_registered

    setting = get_registered(LOOKUP_ROUTE_KEY)
    assert setting is not None
    assert setting.validate(given) == stored


@pytest.mark.parametrize("given", [7, "x" * 65, "tunnel 1", "tunnel/1"])
def test_a_route_that_cannot_be_a_tunnels_id_is_refused(given: object) -> None:
    """Checked for shape only: whether the tunnel exists is asked when a lookup goes out."""
    from sift.kernel.settings_registry import SettingError, get_registered

    setting = get_registered(LOOKUP_ROUTE_KEY)
    assert setting is not None
    with pytest.raises(SettingError):
        setting.validate(given)


# --- what a press still owes, and who is told when an answer lands --------------------------------


_KEEP_ANSWER = (
    "INSERT INTO music_lookups (asset_id, looked_up_at, lengths_sent, status) VALUES (?, 1, ?, ?)"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "owed"),
    [("named", False), ("nothing", False), ("refused", True), ("failed", True)],
)
async def test_an_answer_is_asked_and_answered_and_only_a_failed_ask_is_owed_again(
    tmp_path: Path, status: str, owed: bool
) -> None:
    """A file AcoustID named, or did not know, was asked and answered: it is not in the count,
    the walk does not ask it again, and the dry run does not plan it. Not known is said apart. A
    refusal or a failure is no answer, and stays owed until an ask gets one."""
    database = await _library(tmp_path)
    try:
        names = NameStore(database)
        await LookupSettings(
            names, SecretStore(database), cast(AcoustIDClient, None), _Settings({})
        ).set_key("k", _MASTER)
        await database.execute(_KEEP_ANSWER, ("pmv", "[187]", status))
        queued: list[str] = []

        async def enqueue(job_type: str, payload: dict[str, Any], **_: Any) -> str:
            queued.append(job_type)
            return "walk"

        starter = LookupStarter(names, _Settings({LOOKUP_KEY: True}), enqueue=enqueue)
        walk = _Walk()
        await starter.catch_up(cast(JobContext, walk))
        planned = await starter.plan(first=5)
        expected = 1 if owed else 0
        assert await starter.owed() == expected
        assert planned.files == expected and len(walk.children) == expected
        assert await starter.not_known() == (1 if status == "nothing" else 0)
        # A press with nothing left to ask queues nothing, and says so through the task's press.
        started = await starter.start_catch_up(at="now", requested_by="user-1", priority=3)
        assert (started is not None) is owed
        assert queued == (["music_lookup_catch_up"] if owed else [])
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_kept_local_is_not_counted_as_owed(tmp_path: Path) -> None:
    """The count is the walk's own answer: a file kept local is never asked about, so it is never
    in "no song yet", and a press over it alone has nothing to ask."""
    database = await _library(tmp_path)
    try:
        names = NameStore(database)
        await LookupSettings(
            names, SecretStore(database), cast(AcoustIDClient, None), _Settings({})
        ).set_key("k", _MASTER)
        starter = LookupStarter(names, _Settings({LOOKUP_KEY: True}), enqueue=_never)
        assert await starter.owed() == 1
        await database.execute("UPDATE assets SET keep_local = 1 WHERE id = 'pmv'")
        assert await starter.owed() == 0
        assert await starter.start_catch_up(at="now", requested_by="user-1", priority=3) is None
    finally:
        await database.close()


async def _never(*args: Any, **kwargs: Any) -> str:  # pragma: no cover
    raise AssertionError("nothing is queued")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["nothing", "named"])
async def test_every_answer_kept_is_told_to_every_admin_on_the_works_bell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    """The Music pane's counts and the task's row follow the work's bell, so every answer kept
    rings it: a song named, and a file AcoustID did not know, which rings nothing else."""
    from sift.kernel import changes
    from sift.kernel.audience import EVERY_ADMIN
    from sift.kernel.changes import About
    from sift.slices.music import lookup as lookup_module

    told: list[tuple[object, About]] = []
    monkeypatch.setattr(changes, "announce", lambda audience, about: told.append((audience, about)))
    monkeypatch.setattr(
        lookup_module, "announce", lambda audience, about: told.append((audience, about))
    )
    database = await _library(tmp_path)
    try:
        client = _AcoustID({187: _named()} if answer == "named" else {})
        task, _spread = await _task(database, client, {LOOKUP_KEY: True})
        await task.run(_context())
        assert (EVERY_ADMIN, About.JOBS) in told
        kept = await NameStore(database).lookup_of("pmv")
        assert kept is not None and kept.status == answer
    finally:
        await database.close()


#: Still asked of the kernel one file at a time: Don't enrich.
_PER_FILE_IN_THE_KERNEL = {"catalog.kept_local_over"}


@pytest.mark.asyncio
async def test_the_counts_beside_the_lookup_ask_a_page_in_the_same_number_of_statements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Music pane's owed and not-known counts: three times the files, no statement more."""
    seen: list[str] = []
    real = db_module._judged

    @contextmanager
    def counted(stage: str, statement: Any, *rest: Any, **options: Any) -> Iterator[Any]:
        seen.append(statement_name(statement))
        with real(stage, statement, *rest, **options) as timing:
            yield timing

    database = await _library(tmp_path)
    try:
        names = NameStore(database)
        await LookupSettings(
            names, SecretStore(database), cast(AcoustIDClient, None), _Settings({})
        ).set_key("k", _MASTER)
        starter = LookupStarter(names, _Settings({LOOKUP_KEY: True}), enqueue=_never)
        values = struct.pack("<3I", 1, 2, 3)
        made = 0

        async def counts_after(more: int) -> tuple[int, tuple[int, int, int]]:
            nonlocal made
            async with database.write() as connection:
                for _ in range(more):
                    made += 1
                    for kind in ("owed", "unknown"):
                        one = f"{kind}-{made:03d}"
                        await connection.execute(
                            _ASSET, (one, f"i-{one}", f"{one}.mp4", _EPOCH, 187_000, None)
                        )
                        await connection.execute(_FINGERPRINT, (one, 184_000, values))
                    await connection.execute(
                        "INSERT INTO music_lookups (asset_id, looked_up_at, lengths_sent, status)"
                        " VALUES (?, 1, '[187]', 'nothing')",
                        (f"unknown-{made:03d}",),
                    )
            seen.clear()
            with monkeypatch.context() as patched:
                patched.setattr(db_module, "_judged", counted)
                owed = await starter.owed()
                again = await starter.plan_again()
            ours = [one for one in seen if one not in _PER_FILE_IN_THE_KERNEL]
            return len(ours), (owed, again.not_known, again.files)

        few, answer = await counts_after(3)
        assert answer == (4, 3, 3), "the library's own file is owed too"
        many, answer = await counts_after(6)
        assert answer == (10, 9, 9)
        assert many == few
    finally:
        await database.close()
