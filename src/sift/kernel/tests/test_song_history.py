# SPDX-License-Identifier: AGPL-3.0-or-later
"""A song Sift named, in the feed: one settle's spread is ONE line, and only one settle's.

The music build writes one act per file it names (`identity.seed_music_on`): the unit of Undo is the
file, and each file's own History needs its own line. The feed is the reader that wants the press,
and a spread is one: "Sift named the song Blue on 2 files, from the same music as solo.mp4",
with the files under "Show each" and the file it came from linked in the sentence.

The fold is keyed by the song and by what it was named from (`history_events._FOLD_KEY`), and the
second half of these tests is why: a Run now pairs file after file seconds apart, and a Site's
downloads land a minute apart, so a key that left either out would fold one song's line over acts
that named another song, or named it from another file.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history import Event, history_of_asset
from sift.kernel.access.history_feed import press_of, presses_recent
from sift.kernel.content import songs
from sift.kernel.content.identity import MUSIC_FROM_ACOUSTID, MUSIC_SHARED, seed_music_on
from sift.kernel.db import Database
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.ledger import Object, Reversal
from sift.kernel.workbench import Workbench
from sift.slices.workbench.router import ledger
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

#: The receipts' queue the spread writes under (`slices/music/names.QUEUE`).
NAMES_QUEUE = "music_names"


async def _shared(database: Database, world: World, song: str, *to: str) -> None:
    """One settle's spread, as `names.SongNames._give` writes it: each file named after `solo`."""
    async with database.write() as connection:
        for asset_id in to:
            assert await seed_music_on(
                connection,
                asset_id,
                song,
                source=MUSIC_SHARED,
                from_asset=Object(kind="asset", id=world.solo, name="solo.mp4"),
                facts={"asset": asset_id},
                receipt=Reversal(queue=NAMES_QUEUE, title=f"Sift named the song {song}", detail=""),
            )


def _words(pieces: Sequence[object]) -> str:
    return "".join(f"{one.lead}{one.text}" for one in pieces)  # type: ignore[attr-defined]


async def test_one_settle_s_spread_is_one_line_that_says_the_song_and_where_it_came_from(
    temp_db: Database, world: World, actors: Actors
) -> None:
    await _shared(temp_db, world, "Blue", world.twin, world.loose)

    presses, total = await presses_recent(temp_db, actors.admin)
    assert total == 1
    [press] = presses
    assert press.folded == 2
    assert [(one.kind, one.id) for one in press.objects] == [("asset", world.solo)]
    assert {one.id for one in press.subjects} == {world.twin, world.loose}
    # Undo all reaches both receipts, each through its own queue's reverser.
    members = await press_of(temp_db, actors.admin, press.event.id)
    assert sorted(queue for _one, queue in members) == [NAMES_QUEUE, NAMES_QUEUE]

    page = await ledger(
        database=temp_db, bench=Workbench(), runs=Ledger(temp_db), viewer=actors.admin
    )
    [item] = page.items
    assert (
        _words(item.pieces)
        == "Sift named the song Blue on 2 files, from the same music as solo.mp4"
    )
    assert [(one.kind, one.id) for one in item.pieces if one.kind is not None] == [
        ("asset", world.solo)
    ]
    [files] = item.detail
    assert {one.id for one in files.entries} == {world.twin, world.loose}


async def test_another_song_from_the_same_page_in_the_same_minute_is_another_line(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """Two songs from one Site's page a moment apart are two lines; the same song from it is one."""
    page = Object(kind="site", id=world.site, name="Studio")
    async with temp_db.write() as connection:
        for asset_id, song in ((world.solo, "Blue"), (world.twin, "Green")):
            assert await seed_music_on(connection, asset_id, song, page=page)
    presses, _total = await presses_recent(temp_db, actors.admin)
    assert sorted(one.folded for one in presses) == [1, 1]

    async with temp_db.write() as connection:
        assert await seed_music_on(connection, world.loose, "Blue", page=page)
    presses, _total = await presses_recent(temp_db, actors.admin)
    assert sorted(one.folded for one in presses) == [1, 2]
    folded = next(one for one in presses if one.folded == 2)
    assert {one.id for one in folded.subjects} == {world.solo, world.loose}


async def test_the_same_song_shared_from_another_file_is_another_line(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """Two settles a second apart, one song, two files it came from: two lines, each naming its
    own. AcoustID's names are never folded with a shared one."""
    await _shared(temp_db, world, "Blue", world.twin)
    async with temp_db.write() as connection:
        assert await seed_music_on(
            connection,
            world.loose,
            "Blue",
            source=MUSIC_SHARED,
            from_asset=Object(kind="asset", id=world.twin, name="twin.mp4"),
            receipt=Reversal(queue=NAMES_QUEUE, title="Sift named the song Blue", detail=""),
        )
    presses, _total = await presses_recent(temp_db, actors.admin)
    assert sorted(one.folded for one in presses) == [1, 1]
    assert {one.event.object.id for one in presses if one.event.object} == {world.solo, world.twin}

    # The file taken off its song by hand (the one door), so AcoustID may name it again.
    async with temp_db.write() as connection:
        await songs.choose(connection, world.loose, None, made=songs.UNSAID)
    async with temp_db.write() as connection:
        assert await seed_music_on(connection, world.loose, "Blue", source=MUSIC_FROM_ACOUSTID)
    presses, _total = await presses_recent(temp_db, actors.admin)
    assert sorted(one.folded for one in presses) == [1, 1, 1]


async def test_the_file_a_song_was_shared_from_says_each_song_s_spread_once(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Read from the file the names came from, one settle's receipts are one act: one line naming
    every file it reached. Another song given from the same file is another act and another line."""
    await _shared(temp_db, world, "Blue", world.twin, world.loose)

    def given(thread: Sequence[Event]) -> list[Event]:
        return [one for one in thread if _words(one.pieces).endswith("the same music as this file")]

    [spread] = given(await history_of_asset(temp_db, access, actors.admin, world.solo))
    assert _words(spread.pieces).startswith("Sift named the song Blue on ")
    assert {link.id for link in spread.links} >= {world.twin, world.loose}

    await temp_db.execute("DELETE FROM song_files WHERE asset_id = ?", (world.loose,))
    await temp_db.execute("UPDATE assets SET music = NULL WHERE id = ?", (world.loose,))
    await _shared(temp_db, world, "Green", world.loose)
    lines = given(await history_of_asset(temp_db, access, actors.admin, world.solo))
    assert sorted(_words(one.pieces).split(" on ")[0] for one in lines) == [
        "Sift named the song Blue",
        "Sift named the song Green",
    ]
