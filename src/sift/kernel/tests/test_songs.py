# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's song: the one door, the field that follows it, the counts, and catalog step 81.

The claims worth breaking the build over: a file's song is written only through
`kernel/content/songs.py` and its Music field is always that song's name; a recording is one song
and a name with no recording is one song; Sift's writers only fill a gap; an Undo takes back only
the song its act put there; a song is counted per viewer exactly as its files are; and the step
moves every song a file carried onto a row, once, with one History line.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the ledger's tables)
from sift.kernel.access import AssetFilter, Effect, ObjectType, Repository, Where
from sift.kernel.access.schema import initialize_access, initialize_catalog
from sift.kernel.access.visibility import differences
from sift.kernel.content import songs
from sift.kernel.content.identity import (
    MUSIC_FROM_ACOUSTID,
    MUSIC_SHARED,
    music_provenance,
    seed_music_on,
    unseed_music_on,
)
from sift.kernel.db import Database
from sift.kernel.ledger import Object
from sift.kernel.migrations import check_allows, column_exists, table_exists, widen_a_check
from sift.testing.fixtures import Actors, World, hide

pytestmark = pytest.mark.anyio

SOURCE = Path(__file__).resolve().parents[2]


async def _field(database: Database, asset_id: str) -> str | None:
    row = await database.fetch_one("SELECT music FROM assets WHERE id = ?", (asset_id,))
    assert row is not None
    return None if row["music"] is None else str(row["music"])


async def _song_of(database: Database, asset_id: str) -> str | None:
    row = await database.fetch_one("SELECT song_id FROM song_files WHERE asset_id = ?", (asset_id,))
    return None if row is None else str(row["song_id"])


async def _songs(database: Database) -> list[tuple[str, str | None]]:
    rows = await database.fetch_all("SELECT name, recording_id FROM songs ORDER BY name, id")
    return [(str(row["name"]), row["recording_id"]) for row in rows]


# --- the door ---------------------------------------------------------------------------------


async def test_a_recording_is_one_song_and_a_name_with_none_joins_it(
    temp_db: Database, world: World
) -> None:
    """Two answers naming one recording are one song whatever each was called; a Site's page
    naming the same words joins that song; a name typed later joins it too."""
    async with temp_db.write() as connection:
        assert await seed_music_on(
            connection,
            world.solo,
            "Blue - Marla Quist",
            source=MUSIC_FROM_ACOUSTID,
            recording_id="rec-1",
            score=0.9,
        )
        assert await seed_music_on(
            connection,
            world.twin,
            "Blue (Radio)",
            source=MUSIC_FROM_ACOUSTID,
            recording_id="rec-1",
            score=0.8,
        )
        assert await seed_music_on(connection, world.loose, "blue - marla quist")
    assert await _songs(temp_db) == [("Blue - Marla Quist", "rec-1")]
    one = await _song_of(temp_db, world.solo)
    assert {await _song_of(temp_db, other) for other in (world.twin, world.loose)} == {one}
    # The field is the song's name on every file, whatever each answer was called.
    for asset_id in (world.solo, world.twin, world.loose):
        assert await _field(temp_db, asset_id) == "Blue - Marla Quist"


async def test_a_name_read_off_a_page_takes_the_recording_acoustid_names_later(
    temp_db: Database, world: World
) -> None:
    """A song named from a page (no recording) is THE song an answer of the same name means: it
    takes the recording rather than a second song being made beside it."""
    async with temp_db.write() as connection:
        assert await seed_music_on(connection, world.solo, "Green - Odo Vance")
        assert await seed_music_on(
            connection,
            world.twin,
            "Green - Odo Vance",
            source=MUSIC_FROM_ACOUSTID,
            recording_id="rec-2",
        )
        # A different recording of the same name is another song.
        assert await seed_music_on(
            connection,
            world.loose,
            "Green - Odo Vance",
            source=MUSIC_FROM_ACOUSTID,
            recording_id="rec-3",
        )
    assert await _songs(temp_db) == [("Green - Odo Vance", "rec-2"), ("Green - Odo Vance", "rec-3")]
    assert await _song_of(temp_db, world.solo) == await _song_of(temp_db, world.twin)
    assert await _song_of(temp_db, world.loose) != await _song_of(temp_db, world.solo)


async def test_sift_only_fills_a_gap_and_a_shared_name_joins_its_files_song(
    temp_db: Database, world: World
) -> None:
    async with temp_db.write() as connection:
        assert await seed_music_on(connection, world.solo, "Blue - Marla Quist")
        # A file that has a song keeps it, whatever names it next, and no song is made for the
        # name it was not given.
        assert not await seed_music_on(connection, world.solo, "Red - Juno Marsh")
        # A name carried from the same music joins THAT file's song, by id, not by name.
        assert await seed_music_on(
            connection,
            world.twin,
            "Blue - Marla Quist",
            source=MUSIC_SHARED,
            from_asset=Object(kind="asset", id=world.solo, name="solo.mp4"),
        )
    assert await _song_of(temp_db, world.twin) == await _song_of(temp_db, world.solo)
    assert await _songs(temp_db) == [("Blue - Marla Quist", None)]
    provenance = await music_provenance(temp_db, world.twin, "Blue - Marla Quist")
    assert provenance is not None
    assert (provenance.source, provenance.from_asset_id) == ("shared", world.solo)
    assert provenance.song_id == await _song_of(temp_db, world.solo)


async def test_the_field_follows_the_song_through_a_rename_a_move_and_a_delete(
    temp_db: Database, world: World
) -> None:
    """THE ONE HOME: nothing writes the Music field but the triggers, so a song renamed renames
    every file on it, a file moved to another song takes its name, and a song deleted empties
    each field its files had."""
    async with temp_db.write() as connection:
        await seed_music_on(connection, world.solo, "Blue")
        await seed_music_on(connection, world.twin, "Red")
    blue, red = await _song_of(temp_db, world.solo), await _song_of(temp_db, world.twin)
    await temp_db.execute("UPDATE songs SET name = 'Blue Again' WHERE id = ?", (blue,))
    assert await _field(temp_db, world.solo) == "Blue Again"
    await temp_db.execute("UPDATE song_files SET song_id = ? WHERE asset_id = ?", (red, world.solo))
    assert await _field(temp_db, world.solo) == "Red"
    await temp_db.execute("DELETE FROM songs WHERE id = ?", (red,))
    assert await _field(temp_db, world.solo) is None
    assert await _field(temp_db, world.twin) is None


async def test_a_person_choosing_a_song_moves_the_file_by_hand_and_blank_takes_it_off(
    temp_db: Database, world: World
) -> None:
    async with temp_db.write() as connection:
        assert await seed_music_on(connection, world.solo, "Blue", source=MUSIC_FROM_ACOUSTID)
        chosen = await songs.choose(connection, world.solo, "Typed", made=songs.UNSAID)
    assert chosen.changed and chosen.before is not None and chosen.after is not None
    assert await _field(temp_db, world.solo) == "Typed"
    row = await temp_db.fetch_one("SELECT source FROM song_files WHERE asset_id = ?", (world.solo,))
    assert row is not None and row["source"] is None
    async with temp_db.write() as connection:
        cleared = await songs.choose(connection, world.solo, "  ", made=songs.UNSAID)
    assert cleared.after is None
    assert await _field(temp_db, world.solo) is None


async def test_an_undo_takes_back_only_the_song_its_act_put_there(
    temp_db: Database, world: World
) -> None:
    """By the song's id where the act says it, so a rename since does not stop it; never a song
    somebody chose by hand since."""
    async with temp_db.write() as connection:
        await seed_music_on(connection, world.solo, "Blue")
        await seed_music_on(
            connection,
            world.twin,
            "Blue",
            source=MUSIC_SHARED,
            from_asset=Object(kind="asset", id=world.solo, name="solo.mp4"),
        )
        await seed_music_on(
            connection,
            world.loose,
            "Blue",
            source=MUSIC_SHARED,
            from_asset=Object(kind="asset", id=world.solo, name="solo.mp4"),
        )
        # Chosen by hand since, away and back: the same song, now theirs.
        await songs.choose(connection, world.loose, "Green", made=songs.UNSAID)
        await songs.choose(connection, world.loose, "Blue", made=songs.UNSAID)
    blue = await _song_of(temp_db, world.solo)
    assert await _song_of(temp_db, world.loose) == blue
    await temp_db.execute("UPDATE songs SET name = 'Blue Renamed' WHERE id = ?", (blue,))
    async with temp_db.write() as connection:
        assert await unseed_music_on(connection, world.twin, "Blue", song_id=blue)
        assert not await unseed_music_on(connection, world.loose, "Blue", song_id=blue)
    assert await _field(temp_db, world.twin) is None
    assert await _field(temp_db, world.loose) == "Blue Renamed"


async def test_a_song_chosen_for_a_file_with_none_is_put_on_and_choosing_it_again_changes_nothing(
    temp_db: Database, world: World
) -> None:
    async with temp_db.write() as connection:
        chosen = await songs.choose(connection, world.solo, "Typed", made=songs.UNSAID)
        again = await songs.choose(connection, world.solo, "typed", made=songs.UNSAID)
    assert chosen.before is None and chosen.after is not None
    assert await _field(temp_db, world.solo) == "Typed"
    assert not again.changed and again.after == chosen.after


async def test_a_blank_choice_on_a_file_with_no_song_empties_a_field_of_spaces(
    temp_db: Database, world: World
) -> None:
    """An older library can hold spaces in a field no song stands behind: a blank choice leaves
    the field empty, as a blank choice on a file with a song does."""
    await temp_db.execute("UPDATE assets SET music = '   ' WHERE id = ?", (world.solo,))
    async with temp_db.write() as connection:
        cleared = await songs.choose(connection, world.solo, None, made=songs.UNSAID)
    assert (cleared.before, cleared.after) == (None, None)
    assert await _field(temp_db, world.solo) is None


async def test_a_name_that_cleans_to_nothing_is_no_song_and_no_artist(
    temp_db: Database, world: World
) -> None:
    async with temp_db.write() as connection:
        assert await songs.song_called(connection, "  ", made=songs.UNSAID) is None
        assert await songs.artist_called(connection, " \t ", made=songs.UNSAID) is None
        assert (
            await songs.name_song_on(connection, world.solo, "   ", source=songs.SOURCE_SITE)
            is None
        )
    assert await _songs(temp_db) == []
    assert await _song_of(temp_db, world.solo) is None


async def test_a_writer_that_is_not_one_of_the_sources_is_refused(
    temp_db: Database, world: World
) -> None:
    async with temp_db.write() as connection:
        with pytest.raises(ValueError, match="not a source of a song"):
            await songs.name_song_on(connection, world.solo, "Blue", source="typed")
        song_id = await songs.song_called(connection, "Blue", made=songs.UNSAID)
        assert song_id is not None
        with pytest.raises(ValueError, match="not a source of a credit"):
            await songs.credit_where_none(connection, song_id, ["Marla Quist"], source="typed")


async def test_a_recording_names_its_one_song(temp_db: Database, world: World) -> None:
    async with temp_db.write() as connection:
        await seed_music_on(
            connection, world.solo, "Blue", source=MUSIC_FROM_ACOUSTID, recording_id="rec-1"
        )
        assert await songs.song_of_recording(connection, "rec-1") == await songs.song_of_file(
            connection, world.solo
        )
        assert await songs.song_of_recording(connection, "rec-unknown") is None


async def test_taking_a_file_off_a_song_it_is_not_on_tells_nobody(
    temp_db: Database, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[tuple[str, str]] = []

    async def hand(connection: object, asset_id: str, name: str) -> None:
        told.append((asset_id, name))

    monkeypatch.setattr(songs, "_TAKEN_OFF", {"test": hand})
    async with temp_db.write() as connection:
        await seed_music_on(connection, world.solo, "Blue")
        await seed_music_on(connection, world.twin, "Red")
        red = await songs.song_of_file(connection, world.twin)
        assert red is not None
        assert not await songs.take_off(connection, world.solo, red)
        assert told == []
        assert await songs.take_off(connection, world.twin, red)
    assert told == [(world.twin, "Red")]
    assert await _field(temp_db, world.solo) == "Blue"


async def test_with_no_feature_listening_a_song_taken_off_is_only_itself(
    temp_db: Database, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library without the music feature registers no hand: the act is only itself."""
    monkeypatch.setattr(songs, "_TAKEN_OFF", {})
    async with temp_db.write() as connection:
        await seed_music_on(connection, world.solo, "Blue")
        blue = await songs.song_of_file(connection, world.solo)
        assert blue is not None
        assert await songs.take_off(connection, world.solo, blue)
    assert await _field(temp_db, world.solo) is None


async def test_a_writer_credits_only_a_song_that_credits_nobody_yet(
    temp_db: Database, world: World
) -> None:
    async with temp_db.write() as connection:
        blue = await songs.song_called(connection, "Blue", made=songs.UNSAID)
        assert blue is not None
        assert not await songs.credit_where_none(
            connection, blue, [], source=songs.CREDIT_FROM_SWAP
        )
        assert await songs.credit_where_none(
            connection, blue, ["Marla Quist", "\t", "Odo Venn"], source=songs.CREDIT_FROM_SWAP
        )
        assert not await songs.credit_from_answer(connection, blue, "Juno Marsh")
        credited = [one.name for one in await songs.credits_of(connection, blue)]
    assert credited == ["Marla Quist", "Odo Venn"]
    made = await temp_db.fetch_all("SELECT created_by_via FROM artists ORDER BY name")
    assert {str(row["created_by_via"]) for row in made} == {"swap"}


async def test_an_artist_renamed_keeps_its_songs_and_a_name_that_says_nothing_is_refused(
    temp_db: Database, world: World
) -> None:
    async with temp_db.write() as connection:
        blue = await songs.song_called(connection, "Blue", made=songs.UNSAID)
        assert blue is not None
        await songs.credit(connection, blue, ["Odo Ven"], made=songs.UNSAID, source=None)
        (odo,) = await songs.credits_of(connection, blue)
        assert await songs.rename_artist(connection, "no-such-artist", "Anything") is None
        assert await songs.rename_artist(connection, odo.id, " \t ") is None
        same = await songs.rename_artist(connection, odo.id, "Odo  Ven")
        assert same == songs.ArtistRenamed(before="Odo Ven", into=odo.id, songs=())
        # Its own name in another case is no other artist: a rename, not a fold.
        cased = await songs.rename_artist(connection, odo.id, "odo ven")
        assert cased == songs.ArtistRenamed(before="Odo Ven", into=odo.id, songs=(blue,))
        renamed = await songs.rename_artist(connection, odo.id, "Odo Venn")
        assert renamed == songs.ArtistRenamed(before="odo ven", into=odo.id, songs=(blue,))
        assert [one.name for one in await songs.credits_of(connection, blue)] == ["Odo Venn"]


async def test_the_step_credits_each_kept_answer_once_with_one_history_line(
    temp_db: Database, world: World
) -> None:
    """Every kept answer credits the song that is its recording, where one is, once; one History
    line for the library, none for a run that credited nothing."""
    async with temp_db.write() as connection:
        await seed_music_on(
            connection, world.solo, "Blue", source=MUSIC_FROM_ACOUSTID, recording_id="rec-1"
        )
        answers: list[tuple[str, str | None]] = [
            ("rec-1", "Marla Quist, Odo Venn"),
            ("rec-gone", "Juno Marsh"),
        ]
        counts = await songs.credit_kept_answers(connection, answers)
        again = await songs.credit_kept_answers(connection, answers)
    assert counts == {"songs": 1, "artists": 2}
    assert again == {"songs": 0, "artists": 0}
    lines = await temp_db.fetch_all(
        "SELECT actor_id, touched, payload FROM workbench_decisions WHERE verb = 'added'"
    )
    assert [(row["actor_id"], json.loads(str(row["payload"]))) for row in lines] == [
        ("update", {"credited": 1, "artists": 2})
    ]


async def test_the_triggers_are_put_right_at_boot_and_a_stray_one_goes(
    temp_db: Database, world: World
) -> None:
    """A trigger dropped or edited by hand would leave a file's field saying another song's name;
    every boot puts the build's own back, and drops one of the family this build does not have."""
    some = next(iter(songs.TRIGGERS))
    # nosemgrep: sift-no-string-built-sql (a trigger name from the build's own list, never typed)
    await temp_db.execute(f"DROP TRIGGER {some}")
    await temp_db.execute(
        "CREATE TRIGGER song_music_stray AFTER DELETE ON songs BEGIN SELECT 1; END"
    )
    async with temp_db.write() as connection:
        await songs.keep_true(connection)
    rows = await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'song_music_%'"
    )
    assert sorted(str(row["name"]) for row in rows) == sorted(songs.TRIGGERS)
    async with temp_db.write() as connection:
        await seed_music_on(connection, world.solo, "Blue")
    assert await _field(temp_db, world.solo) == "Blue"


async def test_the_triggers_wait_for_their_tables(tmp_path: Path) -> None:
    """A catalog brought up alone has no songs table yet: nothing is put right, nothing fails."""
    database = Database(tmp_path / "alone.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            await songs.keep_true(connection)
        assert await database.fetch_all("SELECT name FROM sqlite_master") == []
    finally:
        await database.close()


def test_nothing_but_the_door_writes_a_files_music_field() -> None:
    """A second writer of the field is a second home for the song. Every statement in the shipped
    tree that sets `music` on `assets` is in the door's own module."""
    setting = re.compile(r"UPDATE\s+assets\s+SET[^\"']*\bmusic\s*=", re.IGNORECASE)
    writers = sorted(
        str(path.relative_to(SOURCE))
        for path in SOURCE.rglob("*.py")
        if "tests" not in path.parts and setting.search(path.read_text(encoding="utf-8"))
    )
    assert writers == [str(Path("kernel/content/songs.py"))], writers


# --- counted per viewer, exactly as its files are -----------------------------------------------


async def test_a_song_is_counted_for_whoever_may_see_its_files_and_hidden_files_hide_it(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    async with temp_db.write() as connection:
        await seed_music_on(connection, world.solo, "Blue")
        await seed_music_on(
            connection,
            world.twin,
            "Blue",
            source=MUSIC_SHARED,
            from_asset=Object(kind="asset", id=world.solo, name="solo.mp4"),
        )
    blue = await _song_of(temp_db, world.solo)
    assert blue is not None
    page = await access.list_songs(actors.admin)
    assert [(one.id, one.item_count) for one in page.items] == [(blue, 2)]
    # A guest given nothing is shown no song at all, by id or on the wall.
    assert (await access.list_songs(actors.guest)).items == []
    assert await access.visible_song(actors.guest, blue) is None
    # A file in the vault is not counted while it is shut.
    await hide(temp_db, "asset", world.twin, actors.admin.id)
    shut = await access.visible_song(actors.admin, blue)
    assert shut is not None and shut.item_count == 1
    async with temp_db.write() as connection:
        # The stored counts are the facts: every trigger on `song_files` moved them right.
        assert await differences(connection) == []


async def test_no_id_is_no_song_and_a_page_that_cannot_hold_a_row_is_refused(
    access: Repository, actors: Actors
) -> None:
    """An absent id bound into the wall's read would ask for every song and answer the first, so
    it is no song before it is bound; a page of no rows, or one before the first, is a caller's
    mistake said as one rather than an empty wall."""
    assert await access.visible_song(actors.admin, "") is None
    assert await access.position_of_song(actors.admin, "") is None
    with pytest.raises(ValueError, match="at least one row"):
        await access.list_songs(actors.admin, limit=0)
    with pytest.raises(ValueError, match="before the first row"):
        await access.list_songs(actors.admin, offset=-1)


async def test_a_songs_place_is_taken_on_the_wall_narrowed_the_way_it_was(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Filtered to the files a filter reaches, as the listing is: a song on no file the filter
    keeps is not on that wall at all."""
    async with temp_db.write() as connection:
        await seed_music_on(connection, world.solo, "Blue")
        await seed_music_on(connection, world.twin, "Grey")
    blue, grey = await _song_of(temp_db, world.solo), await _song_of(temp_db, world.twin)
    assert blue is not None and grey is not None
    narrowed = AssetFilter(where=Where("assets", (world.twin,)))

    assert await access.position_of_song(actors.admin, grey, asset_filter=narrowed) == 0
    assert await access.position_of_song(actors.admin, blue, asset_filter=narrowed) is None


# --- catalog step 81 --------------------------------------------------------------------------

#: `music_names` as the music feature's version 3 made it, for a library from before songs.
_MUSIC_NAMES = """
CREATE TABLE music_names (
  asset_id      TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  song          TEXT NOT NULL,
  source        TEXT NOT NULL CHECK (source IN ('site', 'acoustid', 'shared')),
  from_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
  recording_id  TEXT,
  score         REAL,
  named_at      INTEGER NOT NULL
)
"""


async def test_the_step_moves_every_song_a_file_carries_onto_one_row(
    temp_db: Database, world: World
) -> None:
    """Grouped by recording where the feature recorded one and the field still says it, by name
    otherwise (case and spacing folded), a name joining the recording called the same; sources
    kept; a name typed over Sift's reads as typed; one History line; and twice is once."""
    async with temp_db.write() as connection:
        await connection.execute("DELETE FROM songs")
        await connection.execute(_MUSIC_NAMES)
        for asset_id, music in (
            (world.solo, "Blue - Marla Quist"),
            (world.twin, "blue  -  marla quist"),
            (world.loose, "Typed Over"),
        ):
            await connection.execute("UPDATE assets SET music = ? WHERE id = ?", (music, asset_id))
        await connection.execute(
            "INSERT INTO music_names VALUES (?, 'Blue - Marla Quist', 'acoustid', NULL, 'rec-9',"
            " 0.9, 100)",
            (world.solo,),
        )
        await connection.execute(
            "INSERT INTO music_names VALUES (?, 'Something Else', 'site', NULL, NULL, NULL, 200)",
            (world.loose,),
        )
        counts = await songs.move_named_songs(connection)
        again = await songs.move_named_songs(connection)
    assert counts == {"songs": 2, "files": 3, "respelled": 1}
    assert again == {"songs": 0, "files": 0, "respelled": 0}
    assert await _songs(temp_db) == [("Blue - Marla Quist", "rec-9"), ("Typed Over", None)]
    rows = await temp_db.fetch_all("SELECT asset_id, source FROM song_files ORDER BY asset_id")
    sources = {str(row["asset_id"]): row["source"] for row in rows}
    assert sources == {world.solo: "acoustid", world.twin: None, world.loose: None}
    # The field respelled to its song's name.
    assert await _field(temp_db, world.twin) == "Blue - Marla Quist"
    lines = await temp_db.fetch_all(
        "SELECT verb, actor_id, payload FROM workbench_decisions WHERE verb = 'added'"
    )
    assert [(row["verb"], row["actor_id"]) for row in lines] == [("added", "update")]
    assert json.loads(str(lines[0]["payload"])) == {"songs": 2, "files": 3}


#: `music_names` as a library can hold it where a file the name came from went while nothing
#: cleared the pointer: the same table without the reference.
_MUSIC_NAMES_UNCHECKED = _MUSIC_NAMES.replace(" REFERENCES assets(id) ON DELETE SET NULL", "")


async def test_the_step_makes_a_pages_name_by_the_download_and_drops_a_pointer_to_a_file_gone(
    temp_db: Database, world: World
) -> None:
    """A name read off a Site's page is a song the download made; a field of a tab is no song;
    and a name carried from a file that is no longer here keeps the name and loses the pointer."""
    async with temp_db.write() as connection:
        await connection.execute("DELETE FROM songs")
        # nosemgrep: sift-no-string-built-sql (the test's own text of an older table)
        await connection.execute(_MUSIC_NAMES_UNCHECKED)
        for asset_id, music in (
            (world.solo, "From A Page"),
            (world.twin, "Carried"),
            (world.loose, "\t"),
        ):
            await connection.execute("UPDATE assets SET music = ? WHERE id = ?", (music, asset_id))
        await connection.execute(
            "INSERT INTO music_names VALUES (?, 'From A Page', 'site', NULL, NULL, NULL, 100)",
            (world.solo,),
        )
        await connection.execute(
            "INSERT INTO music_names VALUES (?, 'Carried', 'shared', 'a-file-since-gone', NULL,"
            " NULL, 200)",
            (world.twin,),
        )
        counts = await songs.move_named_songs(connection)
    assert counts == {"songs": 2, "files": 2, "respelled": 0}
    made = await temp_db.fetch_all("SELECT name, created_by_via FROM songs ORDER BY name")
    assert [(row["name"], row["created_by_via"]) for row in made] == [
        ("Carried", None),
        ("From A Page", "download"),
    ]
    carried = await temp_db.fetch_one(
        "SELECT source, from_asset_id FROM song_files WHERE asset_id = ?", (world.twin,)
    )
    assert carried is not None and (carried["source"], carried["from_asset_id"]) == ("shared", None)
    assert await _song_of(temp_db, world.loose) is None


async def test_the_step_over_a_library_with_no_song_writes_no_history_line(
    temp_db: Database, world: World
) -> None:
    async with temp_db.write() as connection:
        await connection.execute("DELETE FROM songs")
        await connection.execute("UPDATE assets SET music = NULL")
        counts = await songs.move_named_songs(connection)
    assert counts == {"songs": 0, "files": 0, "respelled": 0}
    assert await temp_db.fetch_all("SELECT 1 FROM workbench_decisions WHERE verb = 'added'") == []


# --- a song is hidden and shared like every other thing with a page (catalog 82, access 5) ------


async def test_a_song_hidden_conceals_its_files_and_a_song_shared_hands_them_over(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The verdict's own arms: a hide on a song conceals the files that carry it for the user who
    hid it, a share on a song hands them to a guest, and the stored answers stay the facts."""
    async with temp_db.write() as connection:
        await seed_music_on(connection, world.loose, "Blue")
    blue = await _song_of(temp_db, world.loose)
    assert blue is not None
    assert not await access.can_view(actors.guest, world.loose)
    await access.grant(ObjectType.SONG, blue, actors.guest.id, Effect.SHARE)
    assert await access.can_view(actors.guest, world.loose)
    assert [one.id for one in (await access.list_songs(actors.guest)).items] == [blue]
    await hide(temp_db, "song", blue, actors.guest.id)
    # Concealed by the song, for the one who hid it: not on the wall, not by id.
    assert (await access.list_songs(actors.guest)).items == []
    assert await access.visible_song(actors.guest, blue) is None
    assert await access.visible_song(actors.admin, blue) is not None
    rows = await temp_db.fetch_all(
        "SELECT concealed FROM viewer_assets WHERE user_id = ? AND asset_id = ?",
        (actors.guest.id, world.loose),
    )
    assert [row["concealed"] for row in rows] == [1]
    async with temp_db.write() as connection:
        assert await differences(connection) == []


async def test_catalog_82_and_access_5_widen_what_a_library_at_81_and_4_allows(
    temp_db: Database, world: World
) -> None:
    """The two steps over a library as the build before left it: the song's hide columns, the
    artists' tables, a song that may arrive by swap, and a grant that may name a song. Twice is
    once."""
    async with temp_db.write() as connection:
        await widen_a_check(connection, "song_files", was=_SWAP_NOW, now=_SWAP_WAS)
        await widen_a_check(connection, "acl_grants", was=_GRANT_NOW, now=_GRANT_WAS)
        for statement in (
            "DROP TABLE song_artists",
            "DROP TABLE artists",
            "DROP TABLE song_user_state",
            _SONG_USER_STATE_AT_81,
        ):
            await connection.execute(statement)
        assert not await check_allows(connection, "song_files", "swap")
        for _ in range(2):
            await initialize_catalog(connection, 81)
            await initialize_access(connection, 4)
        assert await check_allows(connection, "song_files", "swap")
        assert await check_allows(connection, "acl_grants", "song")
        assert await column_exists(connection, "song_user_state", "hidden")
        assert await table_exists(connection, "song_artists")


def test_a_kept_answer_splits_into_its_artists_in_order_once_each() -> None:
    assert songs.split_artists("Marla Quist,  Odo  Venn, marla quist") == [
        "Marla Quist",
        "Odo Venn",
    ]
    assert songs.split_artists(None) == []


#: `song_user_state` as catalog 81 made it: no hide.
_SONG_USER_STATE_AT_81 = """
CREATE TABLE song_user_state (
  song_id    TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  favorite   INTEGER NOT NULL DEFAULT 0,
  rating     INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 10),
  pinned     INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (song_id, user_id)
)
"""

#: The fragments the two steps widen, read back the other way to stand a library at 81 and 4.
_SWAP_WAS = "source IN ('acoustid', 'site', 'shared')"
_SWAP_NOW = "source IN ('acoustid', 'site', 'shared', 'swap')"
_GRANT_WAS = "'photo_set')"
_GRANT_NOW = "'photo_set','song')"
