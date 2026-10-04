# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which files share a song: the step that adds the tables, the index, the pairing that follows a
fingerprint, the group one hop wide and scoped to whoever is asking, and the route.

Against a real database carrying Sift's own schema. The pairing is held by what it WRITES
(`music_pairs`, the stamp, the one call to the hook) because that is what every later reader
takes as the answer; and the group by the stored verdict it is scoped through, which is a real
table kept by real triggers in the world fixture rather than something a fake could stand in for.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from sift.kernel import chromaprint
from sift.kernel.access import Effect, ObjectType, Repository
from sift.kernel.access.viewer import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobContext
from sift.slices.music import store as store_module
from sift.slices.music.matching import KEY_SCHEME, Match, keys_of
from sift.slices.music.router import same_music
from sift.slices.music.service import MusicService
from sift.slices.music.store import GROUP_LIMIT, Kept, MusicStore, Pair
from sift.testing.fixtures import Actors, World, hide

pytestmark = [pytest.mark.integration]

_EPOCH = 1_700_000_000

_ASSET = """
INSERT INTO assets
  (id, identity, media_type, size_bytes, original_filename, added_at, acodec, probed_at)
VALUES (?, ?, 'video', 10, ?, ?, ?, 1)
"""


def _song(values: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 2**32, size=values, dtype=np.uint32)


def _noisy(song: np.ndarray, rate: float, seed: int) -> np.ndarray:
    """The same song with a few of its bits flipped: another cut of it, as far as the rule sees."""
    bits = np.random.default_rng(seed).random((len(song), 32)) < rate
    mask = (bits * (1 << np.arange(32, dtype=np.uint64))).sum(axis=1).astype(np.uint32)
    return song ^ mask


#: One song, a second cut of it, and a song that is neither: 1,500 values, about three minutes.
SONG = _song(1500, 11)
OTHER_CUT = _noisy(SONG, 0.05, 12)
UNRELATED = _song(1500, 13)


def _print(values: Sequence[int] | np.ndarray) -> chromaprint.Fingerprint:
    return chromaprint.Fingerprint(
        algorithm=1,
        tool="ffmpeg version 7.1.5",
        duration_ms=chromaprint.covers_ms(len(values)),
        offset_ms=0,
        values=tuple(int(value) for value in values),
    )


def _kept(values: Sequence[int] | np.ndarray) -> Kept:
    return Kept(
        algorithm=1,
        tool="ffmpeg version 7.1.5",
        duration_ms=chromaprint.covers_ms(len(values)),
        offset_ms=0,
        fingerprint=struct.pack(f"<{len(values)}I", *(int(value) for value in values)),
        computed_at=_EPOCH,
    )


#: A copy of a file, there to read. A file owes a fingerprint only while it has one (see
#: `kernel.content.presence`), so every file here is given one.
_COPY = (
    "INSERT INTO asset_locations"
    " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
    " VALUES (?, ?, 'root-1', NULL, ?, ?, 1, 1)"
)
_ROOT = (
    "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('root-1', 'm', 'C:\\m', 1)"
)


async def _library(tmp_path: Path) -> Database:
    database = Database(tmp_path / "music.sqlite3")
    await database.connect()
    await database.initialize_schema()
    async with database.write() as connection:
        await connection.execute(_ROOT)
        for number in (1, 2, 3):
            await connection.execute(
                _ASSET, (f"asset-{number}", f"identity-{number}", f"{number}.mp4", _EPOCH, "aac")
            )
        await connection.execute(
            _ASSET, ("asset-silent", "identity-silent", "silent.mp4", _EPOCH, None)
        )
        for name in ("1", "2", "3", "silent"):
            await connection.execute(_COPY, (f"copy-{name}", f"asset-{name}", name, name))
    return database


@dataclass
class _Asset:
    id: str
    identity: str
    acodec: str | None


class _Content:
    def __init__(self, assets: dict[str, _Asset]) -> None:
        self._assets = assets

    async def get(self, asset_id: str) -> _Asset | None:
        return self._assets.get(asset_id)


class _Context:
    def __init__(self, payload: dict[str, Any], content: _Content) -> None:
        self.payload = payload
        self.content = content


_ASSETS = {
    "asset-1": _Asset("asset-1", "identity-1", "aac"),
    "asset-2": _Asset("asset-2", "identity-2", "aac"),
    "asset-3": _Asset("asset-3", "identity-3", "aac"),
    "asset-silent": _Asset("asset-silent", "identity-silent", None),
}


def _context(asset_id: str, **payload: Any) -> JobContext:
    return cast(JobContext, _Context({"asset_id": asset_id, **payload}, _Content(_ASSETS)))


class _Told:
    """The hook, recording every call."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, list[Pair]]] = []
        self._fail = fail

    async def __call__(self, asset_id: str, pairs: Sequence[Pair]) -> None:
        self.calls.append((asset_id, list(pairs)))
        if self._fail:
            raise RuntimeError("the reader of the pairs fell over")


def _service(store: MusicStore, told: _Told | None = None) -> MusicService:
    async def allowed(job_type: str, asset_id: str | None = None) -> bool:
        return True

    if told is None:
        return MusicService(store=store, allowed=allowed, job_type="audio_fingerprint")
    return MusicService(
        store=store, allowed=allowed, job_type="audio_fingerprint", on_pairs_settled=told
    )


async def _pairs(database: Database) -> list[tuple[str, str]]:
    rows = await database.fetch_all("SELECT a_id, b_id FROM music_pairs ORDER BY a_id, b_id")
    return [(str(row["a_id"]), str(row["b_id"])) for row in rows]


async def _stamp(database: Database, asset_id: str) -> int | None:
    row = await database.fetch_one(
        "SELECT indexed_scheme FROM audio_fingerprints WHERE asset_id = ?", (asset_id,)
    )
    assert row is not None
    return None if row["indexed_scheme"] is None else int(row["indexed_scheme"])


async def _refuse_to_read(*args: object, **kwargs: object) -> chromaprint.Fingerprint:
    raise AssertionError("pairing a fingerprint already kept must not open the file")


# --- the step ---------------------------------------------------------------------------------


_V3_TABLES = (
    "audio_fingerprint_keys",
    "music_pairs",
    "music_names",
    "music_name_refusals",
    "music_lookups",
)

#: Back to v2 as v2 shipped it: none of the five tables, and no column.
_BACK_TO_V2 = (
    "DROP TABLE audio_fingerprint_keys",
    "DROP TABLE music_pairs",
    "DROP TABLE music_names",
    "DROP TABLE music_name_refusals",
    "DROP TABLE music_lookups",
    "ALTER TABLE audio_fingerprints DROP COLUMN indexed_scheme",
)


async def test_the_keys_are_replaced_and_the_candidates_are_the_files_over_the_floor(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        for asset_id, values in (("asset-1", SONG), ("asset-2", OTHER_CUT), ("asset-3", UNRELATED)):
            await store.keep(asset_id, _kept(values))
            await store.index_keys(asset_id, keys_of(values.tolist()))
        # Indexed a second time: the first set is gone rather than doubled.
        await store.index_keys("asset-2", keys_of(OTHER_CUT.tolist()))
        rows = await database.fetch_all(
            "SELECT COUNT(*) AS keys FROM audio_fingerprint_keys WHERE asset_id = 'asset-2'"
        )
        assert int(rows[0]["keys"]) == len(keys_of(OTHER_CUT.tolist()))

        found = await store.candidates("asset-1", keys_of(SONG.tolist()))
        # The other cut, and never the file itself; the unrelated song shares a handful by chance
        # and is under the floor.
        assert [one.asset_id for one in found] == ["asset-2"]
        assert found[0].shared > 100
        assert found[0].duration_ms == chromaprint.covers_ms(len(OTHER_CUT))
        assert await store.candidates("asset-1", set()) == []
        # Bounded, the most shared first: from the other cut, the song itself leads.
        await store.index_keys("asset-3", keys_of(SONG.tolist()))
        (first,) = await store.candidates("asset-2", keys_of(OTHER_CUT.tolist()), limit=1)
        assert first.asset_id in {"asset-1", "asset-3"}
        assert len(await store.candidates("asset-2", keys_of(OTHER_CUT.tolist()))) == 2
    finally:
        await database.close()


# --- the pairing, as the fingerprint task continues ------------------------------------------


async def test_a_fingerprint_just_read_is_paired_and_the_hook_told_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = await _library(tmp_path)
    store = MusicStore(database)
    told = _Told()
    service = _service(store, told)

    async def read(*args: object, **kwargs: object) -> chromaprint.Fingerprint:
        return _print(OTHER_CUT)

    async def reachable(content: object, asset_id: str, **kwargs: object) -> object:
        return type("Source", (), {"path": Path("unused")})()

    monkeypatch.setattr(chromaprint, "read_whole_track", read)
    monkeypatch.setattr("sift.slices.music.service.resolve_decodable", reachable)
    try:
        await store.keep("asset-1", _kept(SONG))
        await store.keep("asset-3", _kept(UNRELATED))
        for asset_id in ("asset-1", "asset-3"):
            await store.index_keys(asset_id, keys_of(await _values(store, asset_id)))
            await store.settle(asset_id)

        await service.fingerprint(_context("asset-2"), settings=Settings())

        assert await _pairs(database) == [("asset-1", "asset-2")]
        assert await _stamp(database, "asset-2") == KEY_SCHEME
        assert len(told.calls) == 1
        (asset_id, pairs), *_ = told.calls
        assert asset_id == "asset-2"
        assert [(one.a_id, one.b_id) for one in pairs] == [("asset-1", "asset-2")]
        (only,) = pairs
        assert only.ber < 0.1
        assert only.windows > 1
        assert only.matching == only.windows
        assert await store.pairs_of("asset-1") == pairs
        assert await store.pairs_of("asset-2") == pairs
    finally:
        await database.close()


async def _values(store: MusicStore, asset_id: str) -> tuple[int, ...]:
    kept = await store.fingerprint_of(asset_id)
    assert kept is not None
    return kept.values


async def test_a_fingerprint_kept_before_pairing_is_paired_from_its_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every fingerprint kept before this step existed: it has one, so nothing is read, and it is
    not paired, so it is paired now. A second run is a point read and nothing more."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    told = _Told()
    service = _service(store, told)
    monkeypatch.setattr(chromaprint, "read_whole_track", _refuse_to_read)
    try:
        await store.keep("asset-1", _kept(SONG))
        await store.keep("asset-2", _kept(OTHER_CUT))
        await service.fingerprint(_context("asset-1"), settings=Settings())
        # asset-2 had no keys yet when asset-1 searched: nothing to find.
        assert await _pairs(database) == []
        await service.fingerprint(_context("asset-2"), settings=Settings())
        assert await _pairs(database) == [("asset-1", "asset-2")]
        assert [asset_id for asset_id, _ in told.calls] == ["asset-1", "asset-2"]
        assert await store.indexed("asset-1")
        assert await store.indexed("asset-2")

        await service.fingerprint(_context("asset-2"), settings=Settings())
        assert len(told.calls) == 2, "an indexed file is not paired again"
    finally:
        await database.close()


async def test_a_claimed_fingerprint_is_paired_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Taken at staging, claimed by identity, and paired in the same task, without a read."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    told = _Told()
    service = _service(store, told)
    monkeypatch.setattr(chromaprint, "read_whole_track", _refuse_to_read)
    try:
        await store.keep("asset-1", _kept(SONG))
        await store.index_keys("asset-1", keys_of(SONG.tolist()))
        await store.keep_pending("identity-2", _kept(OTHER_CUT))
        await service.fingerprint(_context("asset-2", claim_only=True), settings=Settings())
        assert await _pairs(database) == [("asset-1", "asset-2")]
        assert await store.indexed("asset-2")
        assert len(told.calls) == 1
    finally:
        await database.close()


async def test_an_empty_fingerprint_is_stamped_and_nobody_is_told(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = await _library(tmp_path)
    store = MusicStore(database)
    told = _Told()
    service = _service(store, told)
    monkeypatch.setattr(chromaprint, "tool_version", _tool)
    try:
        await service.fingerprint(_context("asset-silent"), settings=Settings())
        assert await store.indexed("asset-silent")
        assert told.calls == []
        assert await _pairs(database) == []
    finally:
        await database.close()


async def _tool(settings: Settings) -> str:
    return "ffmpeg version 7.1.5"


async def test_a_failure_past_the_fingerprint_keeps_it_and_leaves_the_file_unpaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fingerprint is the expensive half and is already written; a pairing that falls over is
    logged, and the file is left unstamped so the next run pairs it again, and tells again."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    failing = _Told(fail=True)
    monkeypatch.setattr(chromaprint, "read_whole_track", _refuse_to_read)
    try:
        await store.keep("asset-1", _kept(SONG))
        await store.index_keys("asset-1", keys_of(SONG.tolist()))
        await store.keep("asset-2", _kept(OTHER_CUT))
        await _service(store, failing).fingerprint(_context("asset-2"), settings=Settings())
        assert len(failing.calls) == 1
        assert await store.has("asset-2")
        assert not await store.indexed("asset-2")

        told = _Told()
        await _service(store, told).fingerprint(_context("asset-2"), settings=Settings())
        assert await store.indexed("asset-2")
        assert len(told.calls) == 1
        assert await _pairs(database) == [("asset-1", "asset-2")]
    finally:
        await database.close()


async def test_a_fingerprint_read_again_loses_its_stamp_and_its_old_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keys and pairs cut from the old values do not describe the new ones."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)
    monkeypatch.setattr(chromaprint, "read_whole_track", _refuse_to_read)
    try:
        await store.keep("asset-1", _kept(SONG))
        await store.index_keys("asset-1", keys_of(SONG.tolist()))
        await store.keep("asset-2", _kept(OTHER_CUT))
        await service.fingerprint(_context("asset-2"), settings=Settings())
        assert await _pairs(database) == [("asset-1", "asset-2")]

        await store.keep("asset-2", _kept(UNRELATED))
        assert not await store.indexed("asset-2")
        await service.fingerprint(_context("asset-2"), settings=Settings())
        assert await _pairs(database) == []
    finally:
        await database.close()


async def test_the_work_owed_is_an_unpaired_fingerprint_as_well_as_a_missing_one(
    tmp_path: Path,
) -> None:
    """The Build's count and the page's question describe one set: waiting for a read, or read and
    not yet paired. Paired, or empty, is done."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)
    lack = store.lack()
    # nosemgrep: sift-no-string-built-sql
    count = f"SELECT COUNT(*) AS files FROM assets a WHERE {lack.condition}"  # noqa: S608
    try:
        (row,) = await database.fetch_all(count, lack.params)  # nosemgrep: sift-no-string-built-sql
        assert int(row["files"]) == 3
        await store.keep("asset-1", _kept(SONG))
        (row,) = await database.fetch_all(count, lack.params)  # nosemgrep: sift-no-string-built-sql
        assert int(row["files"]) == 3, "a fingerprint not yet paired is still owed its pairing"
        assert await service.lacking_among(["asset-1", "asset-2"]) == {"asset-1", "asset-2"}
        await store.settle("asset-1")
        (row,) = await database.fetch_all(count, lack.params)  # nosemgrep: sift-no-string-built-sql
        assert int(row["files"]) == 2
        assert await service.lacking_among(["asset-1", "asset-2"]) == {"asset-2"}
        assert await store.lacking_among([]) == set()
    finally:
        await database.close()


async def test_a_pair_is_kept_smaller_id_first_and_a_file_holds_only_its_own(
    tmp_path: Path,
) -> None:
    match = Match(ber=0.1, offset_s=12.5, windows=10, matching=9, short=False)
    forward = Pair.of("asset-1", "asset-2", match, _EPOCH)
    backward = Pair.of("asset-2", "asset-1", match, _EPOCH)
    assert (forward.a_id, forward.b_id, forward.offset_s) == ("asset-1", "asset-2", 12.5)
    assert (backward.a_id, backward.b_id, backward.offset_s) == ("asset-1", "asset-2", -12.5)
    with pytest.raises(ValueError, match="itself"):
        Pair.of("asset-1", "asset-1", match, _EPOCH)
    database = await _library(tmp_path)
    try:
        with pytest.raises(ValueError, match="pairs it is in"):
            await MusicStore(database).write_pairs("asset-3", [forward])
    finally:
        await database.close()


async def test_a_file_s_pairs_written_ring_the_same_music_bell_once_and_nothing_rings_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[tuple[object, object]] = []
    monkeypatch.setattr(store_module, "announce", lambda who, about: told.append((who, about)))
    match = Match(ber=0.1, offset_s=0.0, windows=10, matching=9, short=False)
    database = await _library(tmp_path)
    try:
        music = MusicStore(database)
        await music.write_pairs("asset-1", [Pair.of("asset-1", "asset-2", match, _EPOCH)])
        await music.write_pairs("asset-3", [])
    finally:
        await database.close()
    assert told == [(EVERY_ADMIN, About.SAME_MUSIC)]


# --- the group ----------------------------------------------------------------------------------


async def _fourth(temp_db: Database, world: World) -> str:
    """A fourth file beside the world's three, so a chain can be longer than one hop."""
    fourth = new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, 'digest-fourth', 1, 'video', ?)",
        (fourth, _EPOCH),
    )
    await temp_db.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, NULL, 'fourth.mp4', 'fourth.mp4', ?, ?)",
        (new_id(), fourth, world.root, _EPOCH, _EPOCH),
    )
    return fourth


_PAIR = """
INSERT INTO music_pairs (a_id, b_id, ber, offset_s, windows, matching, computed_at)
VALUES (?, ?, ?, 0, 5, 5, ?)
"""


async def _chain(temp_db: Database, links: Sequence[tuple[str, str, float]]) -> None:
    """Pairs written straight into the table: the group reads the table, whoever wrote it."""
    for first, second, ber in links:
        one = Pair.of(
            first, second, Match(ber=ber, offset_s=0.0, windows=5, matching=5, short=False), _EPOCH
        )
        await temp_db.execute(_PAIR, (one.a_id, one.b_id, one.ber, one.computed_at))


async def test_the_group_is_one_hop_closest_first_and_never_the_file(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    """solo - twin - loose - fourth: from solo, twin and loose, and fourth is a hop too far. A file
    reached through another scores the weaker of the two links."""
    fourth = await _fourth(temp_db, world)
    await _chain(
        temp_db,
        [
            (world.solo, world.twin, 0.2),
            (world.twin, world.loose, 0.1),
            (world.loose, fourth, 0.05),
        ],
    )
    store = MusicStore(temp_db)
    assert await store.music_group(world.solo) == [world.twin, world.loose]
    assert await store.same_music_of(actors.admin.id, world.solo) == [world.twin, world.loose]
    # From the middle, both sides are one hop: twin's partners and theirs.
    # loose and fourth tie at 0.1 (fourth is reached through loose) and the id decides.
    assert await store.music_group(world.twin) == [world.loose, fourth, world.solo]
    assert world.twin not in await store.music_group(world.twin)
    assert GROUP_LIMIT >= 100


async def test_the_group_answers_to_the_viewer_and_never_bridges_through_a_hidden_file(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    await _chain(temp_db, [(world.solo, world.twin, 0.2), (world.twin, world.loose, 0.1)])
    store = MusicStore(temp_db)
    guest = actors.guest.id
    # A guest shared nothing sees nothing of the group.
    assert await store.same_music_of(guest, world.solo) == []
    # Shared solo and twin: twin, and not loose, which is not theirs to see.
    for one in (world.solo, world.twin):
        await access.grant(ObjectType.ITEM, one, guest, Effect.SHARE)
    assert await store.same_music_of(guest, world.solo) == [world.twin]
    # An admin hides twin: it is gone from the group with the vault shut, and it is no
    # bridge to loose either way: a chain through a hidden file would describe it.
    await hide(temp_db, "asset", world.twin, actors.admin.id)
    admin = actors.admin.id
    assert await store.same_music_of(admin, world.solo) == []
    assert await store.same_music_of(admin, world.solo, reveal=True) == [world.twin]
    # Sift's own acts see the whole group whatever anybody has hidden.
    assert await store.music_group(world.solo) == [world.twin, world.loose]


async def test_the_route_answers_a_file_it_cannot_see_as_one_that_is_not_there(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    await _chain(temp_db, [(world.solo, world.twin, 0.2), (world.twin, world.loose, 0.1)])
    service = _service(MusicStore(temp_db))
    seen = await same_music(world.solo, service, access, actors.admin)
    assert [one.id for one in seen.files] == [world.twin, world.loose]
    assert seen.count == 2
    assert seen.files[0].media_type == "video"
    # The guest may not see solo: the same empty answer as an id that names nothing at all.
    hidden_from = await same_music(world.solo, service, access, actors.guest)
    nowhere = await same_music("01HX0000000000000000000000", service, access, actors.guest)
    assert hidden_from.model_dump() == nowhere.model_dump() == {"files": [], "count": 0}
    # Shared twin and loose but not solo: solo's group is theirs to see, and solo is not, so it
    # is still the answer an id that names nothing gets, and nothing about solo is worked out.
    for one in (world.twin, world.loose):
        await access.grant(ObjectType.ITEM, one, actors.guest.id, Effect.SHARE)
    still_hidden = await same_music(world.solo, service, access, actors.guest)
    assert still_hidden.model_dump() == nowhere.model_dump()
    # And from loose, the guest sees twin, and solo is neither drawn nor counted.
    from_loose = await same_music(world.loose, service, access, actors.guest)
    assert [one.id for one in from_loose.files] == [world.twin]
    assert from_loose.count == 1
    viewer_with_vault = Viewer(id=actors.admin.id, role=actors.admin.role, show_hidden=True)
    assert (await same_music(world.solo, service, access, viewer_with_vault)).count == 2
