# SPDX-License-Identifier: AGPL-3.0-or-later
"""A song's name spreading through the files that share it, and taken back by its receipt.

Against a real database carrying Sift's own schema: the rule that a name fills only an empty
field is one SQL statement, and the refusal is read inside the same transaction as the write, so
both are only proved by running them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the ledger's tables)
from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.access import sentences as say
from sift.kernel.access.history import history_of_asset
from sift.kernel.access.history_line import Event
from sift.kernel.content import songs
from sift.kernel.db import Database
from sift.kernel.workbench import Named, Recorded
from sift.slices.music.names import QUEUE, SharedNameReceipts, SongNames
from sift.slices.music.store import MusicStore, NameStore
from sift.testing.fixtures import Actors, World

pytestmark = [pytest.mark.integration]

_EPOCH = 1_700_000_000

_ASSET = """
INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at, music)
VALUES (?, ?, 'video', 10, ?, ?, ?)
"""
_PAIR = """
INSERT INTO music_pairs (a_id, b_id, ber, offset_s, windows, matching, computed_at)
VALUES (?, ?, 0.1, 0.0, 5, 5, 1)
"""


async def _library(
    tmp_path: Path, music: dict[str, str | None], pairs: list[tuple[str, str]]
) -> Database:
    database = Database(tmp_path / "names.sqlite3")
    await database.connect()
    await database.initialize_schema()
    async with database.write() as connection:
        for asset_id, song in music.items():
            await connection.execute(
                _ASSET, (asset_id, f"identity-{asset_id}", f"{asset_id}.mp4", _EPOCH, song)
            )
        for first, second in pairs:
            await connection.execute(_PAIR, (min(first, second), max(first, second)))
    return database


def _spreader(database: Database, touched: list[str] | None = None) -> SongNames:
    async def note(asset_ids: object) -> None:
        if touched is not None:
            touched.extend(asset_ids)  # type: ignore[arg-type]

    return SongNames(NameStore(database), group_of=MusicStore(database).music_group, touched=note)


async def _music(database: Database, asset_id: str) -> str | None:
    row = await database.fetch_one("SELECT music FROM assets WHERE id = ?", (asset_id,))
    assert row is not None
    return None if row["music"] is None else str(row["music"])


async def _receipts(database: Database) -> list[dict[str, object]]:
    rows = await database.fetch_all(
        "SELECT id, verb, object_kind, object_id, payload, title FROM workbench_decisions"
        " WHERE queue = ? ORDER BY id",
        (QUEUE,),
    )
    return [dict(row) for row in rows]


@pytest.mark.asyncio
async def test_a_named_file_gives_its_song_to_every_empty_file_in_its_group(tmp_path: Path) -> None:
    """One hop through the pairs: b pairs with a, c pairs with b. d already has a song of its own."""
    database = await _library(
        tmp_path,
        {"a": "Blue - Marla Quist", "b": None, "c": None, "d": "Red - Juno Marsh"},
        [("a", "b"), ("b", "c"), ("a", "d")],
    )
    touched: list[str] = []
    try:
        named = await _spreader(database, touched).spread("a")
        assert sorted(named) == ["b", "c"]
        assert touched == named
        assert await _music(database, "b") == "Blue - Marla Quist"
        assert await _music(database, "c") == "Blue - Marla Quist"
        # FILLS ONLY WHEN EMPTY: d kept its own.
        assert await _music(database, "d") == "Red - Juno Marsh"
        receipts = await _receipts(database)
        assert len(receipts) == 2
        one = receipts[0]
        assert (one["verb"], one["object_kind"], one["object_id"]) == ("song_named", "asset", "a")
        payload = json.loads(str(one["payload"]))
        assert payload["song"] == "Blue - Marla Quist"
        assert payload["source"] == "shared"
        assert payload["from"] == "a"
        assert payload["link"]["href"] == "/asset/a"
        assert one["title"] == (
            "Sift named the song Blue - Marla Quist, from the same music as a.mp4"
        )
        # Where each name came from is the membership's own record now (`song_files`): both
        # files are on a's song, carried from a.
        noted = await database.fetch_all(
            "SELECT asset_id, source, from_asset_id FROM song_files"
            " WHERE asset_id IN ('b', 'c') ORDER BY asset_id"
        )
        assert [tuple(row) for row in noted] == [("b", "shared", "a"), ("c", "shared", "a")]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_empty_file_takes_the_song_most_of_its_group_carries(tmp_path: Path) -> None:
    database = await _library(
        tmp_path,
        {"x": None, "p": "Blue - Marla Quist", "q": "Blue - Marla Quist", "r": "Red - Juno Marsh"},
        [("x", "p"), ("x", "q"), ("x", "r")],
    )
    try:
        assert await _spreader(database).spread("x") == ["x"]
        assert await _music(database, "x") == "Blue - Marla Quist"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_group_that_disagrees_evenly_names_nothing(tmp_path: Path) -> None:
    database = await _library(
        tmp_path,
        {"x": None, "p": "Blue - Marla Quist", "r": "Red - Juno Marsh"},
        [("x", "p"), ("x", "r")],
    )
    try:
        assert await _spreader(database).spread("x") == []
        assert await _music(database, "x") is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_undo_takes_the_name_off_and_the_refusal_keeps_it_off(tmp_path: Path) -> None:
    database = await _library(tmp_path, {"a": "Blue - Marla Quist", "b": None}, [("a", "b")])
    try:
        spreader = _spreader(database)
        await spreader.spread("a")
        (receipt,) = await _receipts(database)
        receipts = SharedNameReceipts(NameStore(database))
        viewer = Viewer(id="admin-1", role=Role.ADMIN)
        assert await receipts.reverse(viewer, str(receipt["id"]), str(receipt["payload"]))
        assert await _music(database, "b") is None
        assert await database.fetch_one("SELECT 1 FROM song_files WHERE asset_id = 'b'") is None
        # THE REFUSAL HOLDS: the next settle of the group does not name it again.
        assert await spreader.spread("a") == []
        assert await _music(database, "b") is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_undo_leaves_a_name_somebody_typed_over_the_shared_one(tmp_path: Path) -> None:
    database = await _library(tmp_path, {"a": "Blue - Marla Quist", "b": None}, [("a", "b")])
    try:
        await _spreader(database).spread("a")
        (receipt,) = await _receipts(database)
        # Somebody typed another song onto b, through the one door a typed song takes.
        async with database.write() as connection:
            await songs.choose(connection, "b", "Typed - Robin Vale", made=songs.UNSAID)
        viewer = Viewer(id="admin-1", role=Role.ADMIN)
        assert await SharedNameReceipts(NameStore(database)).reverse(
            viewer, str(receipt["id"]), str(receipt["payload"])
        )
        assert await _music(database, "b") == "Typed - Robin Vale"
        refused = await database.fetch_one(
            "SELECT song FROM music_name_refusals WHERE asset_id = 'b'"
        )
        assert refused is not None and refused["song"] == "Blue - Marla Quist"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_receipt_that_names_no_file_is_not_taken_back(tmp_path: Path) -> None:
    database = await _library(tmp_path, {"a": None}, [])
    try:
        viewer = Viewer(id="admin-1", role=Role.ADMIN)
        receipts = SharedNameReceipts(NameStore(database))
        assert not await receipts.reverse(viewer, "r", "not json")
        assert not await receipts.reverse(viewer, "r", json.dumps({"song": "Blue"}))
    finally:
        await database.close()


def _recorded(payload: dict[str, object]) -> Recorded:
    import json

    return Recorded(
        id="01R", queue=QUEUE, payload=json.dumps(payload), title="stored", detail="", decided_at=0
    )


def test_a_shared_name_is_worded_from_what_it_recorded_and_a_bare_receipt_keeps_its_title() -> None:
    """The line names the song, the file and the file it came from as things, never the stored
    words; a receipt that recorded no file cannot be worded and says so with None."""
    receipts = SharedNameReceipts.__new__(SharedNameReceipts)
    said = receipts.worded(_recorded({"asset": "a", "song": "Blue - Marla Quist", "from": "b"}))
    assert said is not None
    assert said.said == (
        "Sift named the song ",
        "Blue - Marla Quist",
        " on ",
        Named(kind="asset", id="a"),
        ", from the same music as ",
        Named(kind="asset", id="b"),
    )
    alone = receipts.worded(_recorded({"asset": "a", "song": "Blue - Marla Quist"}))
    assert alone is not None and alone.said[-1] == Named(kind="asset", id="a")
    assert receipts.worded(_recorded({"song": "Blue - Marla Quist"})) is None


@pytest.mark.asyncio
async def test_a_settled_pairing_spreads_the_song_and_then_asks_whether_to_look_it_up(
    tmp_path: Path,
) -> None:
    """In that order: a name that arrived through the group is one the lookup need not send."""
    database = await _library(tmp_path, {"a": "Blue - Marla Quist", "b": None}, [("a", "b")])
    asked: list[tuple[str, str | None]] = []

    async def after(asset_id: str) -> None:
        asked.append((asset_id, await _music(database, "b")))

    try:
        names = SongNames(
            NameStore(database), group_of=MusicStore(database).music_group, after_spread=after
        )
        await names.on_pairs_settled("a", [])
        assert asked == [("a", "Blue - Marla Quist")]

        # With nothing after the spread, the spread is the whole of it.
        await _spreader(database).on_pairs_settled("b", [])
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_that_shares_its_song_with_nothing_names_nothing(tmp_path: Path) -> None:
    database = await _library(tmp_path, {"a": None}, [])
    try:
        assert await _spreader(database).spread("a") == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_song_this_file_refused_is_not_taken_from_its_group(tmp_path: Path) -> None:
    """The file would take the song most of its group carries, but somebody took that song off
    it, and a refusal outranks the group. Nothing is named, on it or through it."""
    database = await _library(
        tmp_path,
        {"x": None, "p": "Blue - Marla Quist", "q": "Blue - Marla Quist", "y": None},
        [("x", "p"), ("x", "q"), ("x", "y")],
    )
    try:
        async with database.write() as connection:
            await NameStore.refuse_on(connection, "x", "Blue - Marla Quist")
        assert await _spreader(database).spread("x") == []
        assert await _music(database, "x") is None
        assert await _receipts(database) == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_song_that_cleans_to_nothing_is_never_given(tmp_path: Path) -> None:
    """A stored name of nothing but control characters reads as a song to the group and as nothing
    once cleaned: no file takes it and no receipt is written."""
    database = await _library(tmp_path, {"a": "\x07", "b": None}, [("a", "b")])
    try:
        assert await _spreader(database).spread("a") == []
        assert await _music(database, "b") is None
        assert await _receipts(database) == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_field_holding_only_spaces_is_left_as_it_is_and_nothing_is_recorded(
    tmp_path: Path,
) -> None:
    """Blank reads as no song to the group, but the name is written only into a field that holds
    NOTHING, so the write is refused, and a line saying the file was named would be false."""
    database = await _library(tmp_path, {"a": "Blue - Marla Quist", "b": "   "}, [("a", "b")])
    try:
        assert await _spreader(database).spread("a") == []
        assert await _music(database, "b") == "   "
        assert await _receipts(database) == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_shared_names_receipt_draws_no_picture_and_one_it_cannot_read_is_not_reversed(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path, {"a": None}, [])
    try:
        receipts = SharedNameReceipts(NameStore(database))
        viewer = Viewer(id="u", role=Role.ADMIN)
        assert await receipts.pictures_of(viewer, json.dumps({"asset": "a", "song": "x"})) == ()
        assert await receipts.reverse(viewer, "r", json.dumps(["a", "x"])) is False
        assert await receipts.reverse(viewer, "r", "{not json") is False
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_file_a_song_was_shared_from_says_where_it_went(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Its own page says the spread once, naming every file that took the name, with no Undo:
    each Undo stays on the file that received the name. Taken back, that file leaves the line."""
    async with temp_db.write() as connection:
        await connection.execute(
            "UPDATE assets SET music = 'Blue - Marla Quist' WHERE id = ?", (world.solo,)
        )
        for other in (world.twin, world.loose):
            await connection.execute(_PAIR, (min(world.solo, other), max(world.solo, other)))
    named = await _spreader(temp_db).spread(world.solo)
    assert sorted(named) == sorted([world.twin, world.loose])

    def shared(thread: list[Event]) -> list[Event]:
        return [
            one
            for one in thread
            if say.text_of(one.pieces).endswith(", from the same music as this file")
        ]

    lines = shared(await history_of_asset(temp_db, access, actors.admin, world.solo))
    assert len(lines) == 1
    assert lines[0].undo is None
    assert {link.id for link in lines[0].links} >= {world.twin, world.loose}
    assert say.text_of(lines[0].pieces).startswith("Sift named the song Blue - Marla Quist on ")

    receipt = next(one for one in await _receipts(temp_db) if world.loose in str(one["payload"]))
    await temp_db.execute(
        "UPDATE workbench_decisions SET reversed_at = 1 WHERE id = ?", (receipt["id"],)
    )
    lines = shared(await history_of_asset(temp_db, access, actors.admin, world.solo))
    assert len(lines) == 1
    assert world.loose not in {link.id for link in lines[0].links}


@pytest.mark.asyncio
async def test_a_song_taken_off_by_hand_is_never_named_again_until_put_back_by_hand(
    tmp_path: Path,
) -> None:
    """The kernel's song door tells this feature what a person's hand means (`songs.on_hand`): a
    song taken off a file by hand is a refusal the spread and the lookup keep; put back by hand,
    the refusal goes; a song deleted takes itself off every file it held, the same way."""
    database = await _library(
        tmp_path, {"a": "Blue - Marla Quist", "b": None, "c": None}, [("a", "b"), ("a", "c")]
    )

    async def refused() -> list[tuple[str, str]]:
        rows = await database.fetch_all(
            "SELECT asset_id, song FROM music_name_refusals ORDER BY asset_id"
        )
        return [(str(row["asset_id"]), str(row["song"])) for row in rows]

    try:
        spreader = _spreader(database)
        assert sorted(await spreader.spread("a")) == ["b", "c"]
        row = await database.fetch_one("SELECT song_id FROM song_files WHERE asset_id = 'b'")
        assert row is not None
        song_id = str(row["song_id"])
        async with database.write() as connection:
            assert await songs.take_off(connection, "b", song_id)
        assert await refused() == [("b", "Blue - Marla Quist")]
        # The group settles again: the name does not come back.
        assert await spreader.spread("a") == []
        assert await _music(database, "b") is None
        # Put back by hand, it lands, and the refusal goes.
        async with database.write() as connection:
            assert await songs.put_on_by_hand(connection, "b", song_id)
        assert await _music(database, "b") == "Blue - Marla Quist"
        assert await refused() == []
        # The song deleted: every file it held is taken off it, and none is named it again.
        async with database.write() as connection:
            assert sorted(await songs.let_go_of(connection, song_id)) == ["b", "c"]
            await connection.execute("DELETE FROM songs WHERE id = ?", (song_id,))
        assert await refused() == [("b", "Blue - Marla Quist"), ("c", "Blue - Marla Quist")]
        assert await spreader.spread("a") == []
        assert (await _music(database, "b"), await _music(database, "c")) == (None, None)
    finally:
        await database.close()
