# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking AcoustID again about the files it did not know, and the artists an answer credits.

The claims worth breaking the build over: a press of Ask again takes only the files AcoustID did
not know and last asked more than `ASK_AGAIN_AFTER_DAYS` ago, counted before it starts, while one
file's own Ask again takes it at any age; a file asked again keeps its earlier answer until the new
one lands; an answer credits its artists, in AcoustID's order, on the song that is its recording
where that song credits nobody yet; and the one-time step credits every answer already kept, once,
with one History line.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, cast

import pytest

from sift.kernel.content import songs
from sift.kernel.jobs import JobContext
from sift.kernel.secret_store import SecretStore
from sift.slices.music import schema
from sift.slices.music.acoustid import AcoustIDClient, LookupAnswer, Recording, Result
from sift.slices.music.lookup import (
    ASK_AGAIN_AFTER_DAYS,
    LookupNotReady,
    LookupSettings,
    LookupStarter,
)
from sift.slices.music.settings import LOOKUP_KEY
from sift.slices.music.store import LookupKept, NameStore
from sift.slices.music.tests.test_lookup import (
    _ASSET,
    _EPOCH,
    _FINGERPRINT,
    _MASTER,
    _AcoustID,
    _context,
    _library,
    _Settings,
    _task,
    _Walk,
)

pytestmark = [pytest.mark.integration]

#: The moment these tests stand at, and a day.
_NOW = 1_800_000_000
_DAY = 86_400


async def _not_known(names: NameStore, asset_id: str, *, days_ago: int) -> None:
    """AcoustID answered this file with nothing it knew, so many days before now."""
    await names.keep_lookup(asset_id, LookupKept(status="nothing", lengths_sent=(187,)))
    await names.database.execute(
        "UPDATE music_lookups SET looked_up_at = ? WHERE asset_id = ?",
        (_NOW - days_ago * _DAY, asset_id),
    )


async def _with_files(tmp_path: Path) -> NameStore:
    database = await _library(tmp_path)
    values = struct.pack("<3I", 1, 2, 3)
    async with database.write() as connection:
        for asset_id in ("fresh", "named"):
            await connection.execute(
                _ASSET, (asset_id, f"i-{asset_id}", f"{asset_id}.mp4", _EPOCH, 200_000, None)
            )
            await connection.execute(_FINGERPRINT, (asset_id, 197_000, values))
    names = NameStore(database)
    await LookupSettings(
        names, SecretStore(database), cast(AcoustIDClient, None), _Settings({})
    ).set_key("k", _MASTER)
    return names


@pytest.mark.asyncio
async def test_ask_again_takes_only_the_unknown_files_asked_long_enough_ago(
    tmp_path: Path,
) -> None:
    names = await _with_files(tmp_path)
    queued: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def enqueue(job_type: str, payload: dict[str, Any], **options: Any) -> str:
        queued.append((job_type, payload, options))
        return "walk"

    try:
        await _not_known(names, "pmv", days_ago=ASK_AGAIN_AFTER_DAYS + 1)
        await _not_known(names, "fresh", days_ago=1)
        await names.keep_lookup("named", LookupKept(status="named", lengths_sent=(187,)))
        off = LookupStarter(names, _Settings({}), enqueue=enqueue, clock=lambda: _NOW)
        with pytest.raises(LookupNotReady):
            await off.start_again(requested_by="u-1")
        assert (await off.plan_again()).files == 0
        # Said beside the press whatever its age, and while the lookup is off.
        assert await off.not_known() == 2
        starter = LookupStarter(
            names, _Settings({LOOKUP_KEY: True}), enqueue=enqueue, clock=lambda: _NOW
        )
        # Counted before anything starts: the file not known a month ago, never the one not known
        # yesterday or the one AcoustID named.
        assert (await starter.plan_again()).files == 1
        assert await starter.start_again(requested_by="u-1") == ("walk", 1)
        [(job_type, payload, options)] = queued
        assert (job_type, payload, options["requested_by"]) == (
            "music_lookup_catch_up",
            {"again": True},
            "u-1",
        )
        walk = _Walk(priority=3)
        walk.job.payload = payload
        await starter.catch_up(cast(JobContext, walk))
        assert [(kind, child) for kind, child, _ in walk.children] == [
            ("music_lookup", {"asset_id": "pmv", "again": True})
        ]
        # One file's own Ask again takes it at any age, and only a file AcoustID did not know.
        queued.clear()
        assert await starter.start_for_files(
            ["fresh", "named"], requested_by="u-1", again=True
        ) == (
            "walk",
            1,
        )
        assert queued[0][1] == {"files": ["fresh"], "again": True}
        # The lookup's own press never asks again.
        assert await starter.owed() == 0
        # Asked again lately: nothing is waiting, and the press queues nothing.
        await _not_known(names, "pmv", days_ago=0)
        queued.clear()
        assert await starter.start_again(requested_by="u-1") == (None, 0)
        assert queued == []
    finally:
        await names.database.close()


@pytest.mark.asyncio
async def test_a_file_kept_local_since_it_was_not_known_is_never_asked_again(
    tmp_path: Path,
) -> None:
    """Kept local means nothing about the file leaves this device: a file AcoustID did not know
    long ago and that is kept local now is not counted by Ask again or in the "didn't know" line,
    and a press queues nothing."""
    names = NameStore(await _library(tmp_path))
    queued: list[str] = []

    async def enqueue(job_type: str, payload: dict[str, Any], **options: Any) -> str:
        queued.append(job_type)
        return "walk"

    try:
        await LookupSettings(
            names, SecretStore(names.database), cast(AcoustIDClient, None), _Settings({})
        ).set_key("k", _MASTER)
        await _not_known(names, "pmv", days_ago=ASK_AGAIN_AFTER_DAYS + 1)
        await names.database.execute("UPDATE assets SET keep_local = 1 WHERE id = 'pmv'")
        starter = LookupStarter(
            names, _Settings({LOOKUP_KEY: True}), enqueue=enqueue, clock=lambda: _NOW
        )
        assert (await starter.plan_again()).files == 0
        assert await starter.not_known() == 0
        assert (await starter.plan_again()).not_known == 0
        assert await starter.start_again(requested_by="u-1") == (None, 0)
        assert queued == []
    finally:
        await names.database.close()


@pytest.mark.asyncio
async def test_a_file_named_while_its_ask_again_waited_is_not_asked(tmp_path: Path) -> None:
    """Somebody names the song on a file while its Ask again is queued: when the lookup runs, the
    file carries a song, and AcoustID is asked nothing about it."""
    database = await _library(tmp_path)
    names = NameStore(database)
    try:
        await _not_known(names, "pmv", days_ago=40)
        await database.execute("UPDATE assets SET music = 'Blue - Marla Quist' WHERE id = 'pmv'")
        silent = _AcoustID({})
        task, _spread = await _task(database, silent, {LOOKUP_KEY: True})
        context = _context()
        context.payload["again"] = True
        await task.run(context)
        assert silent.asked == []
        kept = await names.lookup_of("pmv")
        assert kept is not None and kept.status == "nothing"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_asked_again_keeps_its_answer_until_the_new_one_lands(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    names = NameStore(database)
    try:
        await _not_known(names, "pmv", days_ago=40)
        # Not asked again by a lookup that is not marked so: an answer stands.
        silent = _AcoustID({})
        task, _spread = await _task(database, silent, {LOOKUP_KEY: True})
        await task.run(_context())
        assert silent.asked == []
        # Asked again, AcoustID still does not know it: the answer is kept anew, still nothing.
        context = _context()
        context.payload["again"] = True
        await task.run(context)
        kept = await names.lookup_of("pmv")
        assert kept is not None and kept.status == "nothing" and len(silent.asked) == 3
        row = await database.fetch_one("SELECT looked_up_at FROM music_lookups")
        assert row is not None and int(row["looked_up_at"]) != _NOW - 40 * _DAY
        # Asked again and known now: the song is named on the file, its artists credited.
        recording = Recording(
            "rec-1", "Blue", "Marla Quist, Odo Venn", artist_names=("Marla Quist", "Odo Venn")
        )
        answer = LookupAnswer(results=(Result(id="r", score=0.9, recordings=(recording,)),))
        task, _spread = await _task(database, _AcoustID({187: answer}), {LOOKUP_KEY: True})
        await task.run(context)
        kept = await names.lookup_of("pmv")
        assert kept is not None and kept.status == "named"
        credited = await database.fetch_all(
            "SELECT a.name, sa.source FROM song_artists sa JOIN artists a ON a.id = sa.artist_id"
            " ORDER BY sa.position"
        )
        assert [(one["name"], one["source"]) for one in credited] == [
            ("Marla Quist", "acoustid"),
            ("Odo Venn", "acoustid"),
        ]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_library_from_before_the_names_moved_onto_the_songs_drops_their_old_record(
    tmp_path: Path,
) -> None:
    """The record of where a file's name came from lives on the songs now, so the old table goes
    on the next boot of an older library, and a replay of the step finds nothing to drop."""
    database = await _library(tmp_path)
    try:
        await database.execute("CREATE TABLE music_names (asset_id TEXT PRIMARY KEY)")
        async with database.write() as connection:
            await schema.initialize(connection, 3)
            await schema.initialize(connection, 3)
        assert (
            await database.fetch_one(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'music_names'"
            )
            is None
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_step_credits_every_answer_kept_once_with_one_history_line(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        async with database.write() as connection:
            song_id = await songs.song_called(
                connection, "Blue - Marla Quist", made=songs.BY_LOOKUP, recording_id="rec-9"
            )
            assert song_id is not None
        await database.execute(
            "INSERT INTO music_lookups (asset_id, looked_up_at, lengths_sent, status,"
            " recording_id, title, artists, score)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("pmv", 1, "[187]", "named", "rec-9", "Blue", "Marla Quist, Odo Venn", 0.9),
        )
        async with database.write() as connection:
            await schema.initialize(connection, 4)
            await schema.initialize(connection, 4)
        credited = await database.fetch_all(
            "SELECT a.name, a.created_by_via FROM song_artists sa"
            " JOIN artists a ON a.id = sa.artist_id WHERE sa.song_id = ? ORDER BY sa.position",
            (song_id,),
        )
        assert [(one["name"], one["created_by_via"]) for one in credited] == [
            ("Marla Quist", "music_lookup"),
            ("Odo Venn", "music_lookup"),
        ]
        lines = await database.fetch_all(
            "SELECT verb, actor_id, payload FROM workbench_decisions WHERE verb = 'added'"
        )
        assert [(one["verb"], one["actor_id"]) for one in lines] == [("added", "update")]
        assert json.loads(str(lines[0]["payload"])) == {"credited": 1, "artists": 2}
    finally:
        await database.close()
