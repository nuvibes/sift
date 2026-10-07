# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three ways Sift names a song on a file, through the one writer, and where a name came from.

`seed_music_on` is the kernel's one writer of `assets.music` for Sift's own acts: a Site's page, an
AcoustID answer, a name shared from another file. Each fills only an empty field and records one
`song_named` act; `music_provenance` reads the act back to say which of the three (or a person)
put the current name there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the ledger's tables)
from sift.kernel.content import songs
from sift.kernel.content.identity import (
    MUSIC_FROM_ACOUSTID,
    MUSIC_SHARED,
    MUSIC_TYPED,
    MusicProvenance,
    cleaned_song,
    music_provenance,
    seed_music_on,
    song_and_length,
    songs_of,
    unseed_music_on,
)
from sift.kernel.content.identity_fields import MUSIC_FROM_SITE, songs_and_lengths
from sift.kernel.db import Database
from sift.kernel.ledger import Object, Reversal

pytestmark = [pytest.mark.integration]

_ASSET = """
INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at)
VALUES (?, ?, 'video', 10, ?, 1)
"""


async def _library(tmp_path: Path) -> Database:
    database = Database(tmp_path / "songs.sqlite3")
    await database.connect()
    await database.initialize_schema()
    async with database.write() as connection:
        for one in ("a", "b"):
            await connection.execute(_ASSET, (one, f"i-{one}", f"{one}.mp4"))
    return database


def test_a_song_is_cleaned_as_text_and_never_as_an_address() -> None:
    """A song is not an address: a URL cleaner would cut "Where Is Home? - Band" at the question
    mark."""
    assert cleaned_song("  Where Is Home? - Band ") == "Where Is Home? - Band"
    assert cleaned_song("Song #1 -  X") == "Song #1 - X"
    assert cleaned_song("\x00 ") == ""


@pytest.mark.asyncio
async def test_each_source_fills_only_an_empty_field_and_says_where_it_came_from(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        async with database.write() as connection:
            assert await seed_music_on(
                connection,
                "a",
                "Blue - Marla Quist",
                source=MUSIC_FROM_ACOUSTID,
                facts={"score": 0.9},
            )
            # FILLS ONLY WHEN EMPTY, whatever the source.
            assert not await seed_music_on(connection, "a", "Red - Juno Marsh")
            assert await seed_music_on(
                connection,
                "b",
                "Blue - Marla Quist",
                source=MUSIC_SHARED,
                from_asset=Object(kind="asset", id="a", name="a.mp4"),
                receipt=Reversal(queue="music_names", title="t", detail="d"),
            )
        rows = await database.fetch_all(
            "SELECT id, queue, actor_id, object_kind, object_id, payload FROM workbench_decisions"
            " WHERE verb = 'song_named' ORDER BY id"
        )
        acoustid, shared = (dict(row) for row in rows)
        acoustid_said = json.loads(acoustid["payload"])
        # The song it was put on, by id: the act's own record of which song an Undo takes back.
        song_id = acoustid_said.pop("song_id")
        assert acoustid_said == {
            "song": "Blue - Marla Quist",
            "source": "acoustid",
            "score": 0.9,
        }
        assert json.loads(shared["payload"])["song_id"] == song_id
        assert acoustid["object_kind"] is None
        # The lookup is a task of its own; the spread rides the fingerprint task that settled it.
        assert (acoustid["actor_id"], shared["actor_id"]) == (
            "music_lookup",
            "fingerprint",
        )
        assert (shared["queue"], shared["object_kind"], shared["object_id"]) == (
            "music_names",
            "asset",
            "a",
        )
        assert (await music_provenance(database, "a", "Blue - Marla Quist")) is not None
        provenance = await music_provenance(database, "b", "Blue - Marla Quist")
        assert provenance is not None
        assert (provenance.source, provenance.from_asset_id) == ("shared", "a")
        # The act is the receipt: what an Undo goes through, handed over rather than guessed.
        assert provenance.act_id == shared["id"]
        # A name somebody typed over Sift's reads as typed, and no name reads as none.
        typed = await music_provenance(database, "b", "Typed - Robin Vale")
        assert typed is not None and typed.source == "typed"
        assert await music_provenance(database, "b", None) is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_source_that_breaks_its_own_rule_is_refused(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        async with database.write() as connection:
            with pytest.raises(ValueError, match="shared song names the file"):
                await seed_music_on(connection, "a", "Blue", source=MUSIC_SHARED)
            with pytest.raises(ValueError, match="only a song named from a Site"):
                await seed_music_on(
                    connection,
                    "a",
                    "Blue",
                    source=MUSIC_FROM_ACOUSTID,
                    page=Object(kind="site", id="s", name="Quillhouse"),
                )
            with pytest.raises(ValueError, match="not a source"):
                await seed_music_on(connection, "a", "Blue", source="radio")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_songs_a_group_carries_and_one_files_song_and_length(tmp_path: Path) -> None:
    """The music feature's two questions about the files themselves, asked of the kernel: a feature
    may not read the files' table."""
    database = await _library(tmp_path)
    try:
        await database.execute(
            "UPDATE assets SET music = '  Blue - Marla Quist ', duration_ms = 190000 WHERE id = 'a'"
        )
        await database.execute("UPDATE assets SET music = '   ' WHERE id = 'b'")
        # Only a file carrying a song answers, stripped; a blank song is no song.
        assert await songs_of(database, ["a", "b", "gone"]) == {"a": "Blue - Marla Quist"}
        assert await songs_of(database, []) == {}
        own = await song_and_length(database, "a")
        assert own is not None
        assert (own.music, own.duration_ms) == ("  Blue - Marla Quist ", 190000)
        bare = await song_and_length(database, "b")
        assert bare is not None and bare.duration_ms is None
        await database.execute("UPDATE assets SET music = NULL WHERE id = 'b'")
        cleared = await song_and_length(database, "b")
        assert cleared is not None and cleared.music is None
        assert await song_and_length(database, "gone") is None
        # A page's worth together answers as each alone; a file not there is absent.
        page = await songs_and_lengths(database, ["a", "b", "gone", "a"])
        assert sorted(page) == ["a", "b"]
        assert page["a"] == own and page["b"] == cleared
        assert await songs_and_lengths(database, []) == {}
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_name_no_act_of_sifts_wrote_reads_as_typed(tmp_path: Path) -> None:
    """Nothing records a name somebody typed, so a file with a song and no act behind it is theirs."""
    database = await _library(tmp_path)
    try:
        provenance = await music_provenance(database, "a", "Typed - Robin Vale")
        assert provenance == MusicProvenance(source=MUSIC_TYPED)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_act_this_build_cannot_read_is_no_claim_on_the_name(tmp_path: Path) -> None:
    """A receipt outlives the version that wrote it. One whose payload is not readable, or names a
    source this build does not know, says nothing about where the name came from, so the name
    reads as the person's, never as a source guessed at."""
    database = await _library(tmp_path)
    try:
        for asset_id, payload in (
            ("a", "not json"),
            ("b", json.dumps({"song": "Blue - Marla Quist", "source": "radio"})),
        ):
            async with database.write() as connection:
                await connection.execute(
                    "INSERT INTO workbench_decisions (id, queue, title, detail, payload,"
                    " decided_at, verb) VALUES (?, 'ledger', '', '', ?, 1, 'song_named')",
                    (f"act-{asset_id}", payload),
                )
                await connection.execute(
                    "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
                    " VALUES (?, 'asset', ?)",
                    (f"act-{asset_id}", asset_id),
                )
            provenance = await music_provenance(database, asset_id, "Blue - Marla Quist")
            assert provenance == MusicProvenance(source=MUSIC_TYPED), payload
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_name_with_no_song_behind_it_is_read_off_the_act_that_wrote_it(
    tmp_path: Path,
) -> None:
    """A name written before songs were kept as rows has only its act to say where it came from:
    a shared name names the file it was shared from, and an act that names no source was a
    Site's page, the one source there was."""
    database = await _library(tmp_path)
    try:
        for asset_id, payload in (
            ("a", {"song": "Blue - Marla Quist", "source": "shared", "from": "b"}),
            ("b", {"song": "Blue - Marla Quist"}),
        ):
            async with database.write() as connection:
                await connection.execute(
                    "INSERT INTO workbench_decisions (id, queue, title, detail, payload,"
                    " decided_at, verb) VALUES (?, 'ledger', '', '', ?, 1, 'song_named')",
                    (f"act-{asset_id}", json.dumps(payload)),
                )
                await connection.execute(
                    "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
                    " VALUES (?, 'asset', ?)",
                    (f"act-{asset_id}", asset_id),
                )

        assert await music_provenance(database, "a", "Blue - Marla Quist") == MusicProvenance(
            source=MUSIC_SHARED, from_asset_id="b", act_id="act-a"
        )
        assert await music_provenance(database, "b", "Blue - Marla Quist") == MusicProvenance(
            source=MUSIC_FROM_SITE, act_id="act-b"
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_name_comes_off_only_while_the_file_still_says_it(tmp_path: Path) -> None:
    """Sift's name, taken back, must not take a name somebody typed over it since."""
    database = await _library(tmp_path)
    try:
        async with database.write() as connection:
            await seed_music_on(connection, "a", "Blue - Marla Quist")
            await seed_music_on(connection, "b", "Blue - Marla Quist")
            # Somebody typed another song onto b, through the one door a typed song takes.
            await songs.choose(connection, "b", "Typed - Robin Vale", made=songs.UNSAID)
        async with database.write() as connection:
            assert await unseed_music_on(connection, "a", "Blue - Marla Quist")
            assert not await unseed_music_on(connection, "b", "Blue - Marla Quist")
        assert await songs_of(database, ["a", "b"]) == {"b": "Typed - Robin Vale"}
    finally:
        await database.close()
