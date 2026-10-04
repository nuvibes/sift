# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every pass over a file says so on the file's History, its empty answer as plainly as a full one.

A music fingerprint, AcoustID's answer, a pass that gave up, the details read again and a stash-box
match nobody has checked each leave a row, and a History that read none of them would make a file
put through Run task look exactly like a file nobody touched. One test per line, each pinning the
sentence word for word, then the two rules that keep them honest: one act is one line (AcoustID's
named song is the song's own line, never a second), and the number on the History tab is the
length of the list it opens to.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

# The feature tables these lines read, registered so a kernel database has them.
import sift.slices.music.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history import Actor, Event, count_of_asset_history, history_of_asset
from sift.kernel.access.history_folds import EPISODE_GAP
from sift.kernel.access.sentences import MEANS
from sift.kernel.db import Database
from sift.kernel.ledger import Actor as ActorOf
from sift.kernel.ledger import record_event
from sift.kernel.vocabulary import VIA_MUSIC_LOOKUP, Subject
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

#: The fixture library's arrival moment (`testing.fixtures._EPOCH`); every line here is an hour on.
ARRIVED = 1_700_000_000
LATER = ARRIVED + 3_600
BOX = "01HX0000000000000000000951"
#: The kinds the lines under test are drawn as, and the folded sitting they can become.
_KINDS = frozenset({"scanned", "asked", "left_out", "ready", "song_named"})


def _words(event: Event) -> str:
    return "".join(f"{one.lead}{one.text}" for one in event.pieces)


async def _lines(
    database: Database, access: Repository, actors: Actors, asset_id: str
) -> list[Event]:
    """The file's History as the admin reads it, only the kinds these lines are drawn as: the
    fixture library names people and tags on its files, and those lines are not these."""
    drawn = await history_of_asset(database, access, actors.admin, asset_id)
    return [one for one in drawn if one.kind in _KINDS]


async def _music_print(database: Database, asset_id: str, fingerprint: bytes, at: int) -> None:
    await database.execute(
        "INSERT INTO audio_fingerprints"
        " (asset_id, algorithm, tool, duration_ms, offset_ms, fingerprint, computed_at)"
        " VALUES (?, 2, 'fpcalc', 61000, 0, ?, ?)",
        (asset_id, fingerprint, at),
    )


async def _asked(database: Database, asset_id: str, status: str, title: str | None = None) -> None:
    await database.execute(
        "INSERT INTO music_lookups (asset_id, looked_up_at, lengths_sent, status, title)"
        " VALUES (?, ?, '[61]', ?, ?)",
        (asset_id, LATER, status, title),
    )


async def _left_out(
    database: Database, asset_id: str, product: str, code: str, *, transient: bool
) -> None:
    await database.execute(
        "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
        " VALUES (?, ?, ?, 'the tool said something long', ?, ?)",
        (asset_id, product, code, int(transient), LATER),
    )


async def test_a_music_fingerprint_made_is_a_line_and_an_empty_one_is_said_plainly(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _music_print(temp_db, world.solo, b"\x01\x02\x03\x04", LATER)
    await _music_print(temp_db, world.twin, b"", LATER)

    [made] = await _lines(temp_db, access, actors, world.solo)
    assert _words(made) == "Sift generated a music fingerprint for this file"
    assert (made.kind, made.actor, made.at) == ("scanned", Actor.SIFT, LATER)
    [empty] = await _lines(temp_db, access, actors, world.twin)
    assert _words(empty) == "Sift could not read any sound in this file to fingerprint"


async def test_a_music_fingerprint_beside_the_pictures_is_one_sitting_of_housekeeping(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Routine, as the pictures Sift generates are: one sitting is one line that opens to each."""
    await temp_db.execute(
        "INSERT INTO derivatives (id, asset_id, kind, params, rel_cache_path, created_at)"
        " VALUES ('d1', ?, 'thumb', '{}', 'x.jpg', ?)",
        (world.solo, LATER),
    )
    await _music_print(temp_db, world.solo, b"\x01", LATER + 5)

    [sitting] = await _lines(temp_db, access, actors, world.solo)
    assert _words(sitting) == "Sift processed this file"
    [steps] = sitting.detail
    assert [one.name for one in steps.links] == [
        "Sift generated a thumbnail for this file",
        "Sift generated a music fingerprint for this file",
    ]


@pytest.mark.parametrize(
    ("status", "said"),
    [
        ("nothing", "Sift asked AcoustID and it didn't know the song"),
        ("failed", "Sift asked AcoustID and got no answer, so this file will be asked about again"),
        ("refused", "Sift asked AcoustID and it refused, so this file will be asked about again"),
    ],
)
async def test_what_acoustid_answered_is_a_line_of_its_own(
    temp_db: Database, access: Repository, world: World, actors: Actors, status: str, said: str
) -> None:
    """Never folded into a sitting: asking AcoustID sends something about the file to a service
    somebody else runs, so it stands on its own line even beside the fingerprint it sent."""
    await _music_print(temp_db, world.solo, b"\x01", LATER - 2)
    await _asked(temp_db, world.solo, status)

    lines = await _lines(temp_db, access, actors, world.solo)
    assert [_words(one) for one in lines] == [
        "Sift generated a music fingerprint for this file",
        said,
    ]
    asked = lines[1]
    assert (asked.kind, asked.actor, asked.actor_name, asked.at) == (
        "asked",
        Actor.SIFT,
        "Sift",
        LATER,
    )
    assert not asked.routine
    assert "AcoustID" in MEANS["asked"]


async def test_a_song_acoustid_named_on_the_file_is_one_line_not_two(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The answer that named the song the file was given is the song's own line."""
    await _asked(temp_db, world.solo, "named", "Blue")
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=ActorOf.sift(VIA_MUSIC_LOOKUP),
            verb="song_named",
            subject=Subject(kind="asset", id=world.solo, name="solo.mp4"),
            payload=json.dumps({"song": "Blue", "source": "acoustid"}),
        )

    lines = await _lines(temp_db, access, actors, world.solo)
    assert [one.kind for one in lines] == ["song_named"]
    assert "AcoustID" in _words(lines[0])


async def test_a_song_acoustid_named_that_the_file_was_not_given_is_still_said(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The file kept the song it had, or somebody took this one off it: the ask still happened."""
    await _asked(temp_db, world.solo, "named", "Blue")
    [asked] = await _lines(temp_db, access, actors, world.solo)
    assert _words(asked) == "Sift asked AcoustID and it named the song Blue"


async def test_a_pass_that_gave_up_says_what_it_could_not_do(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _left_out(temp_db, world.solo, "thumbnails", "not_decodable", transient=False)
    await _left_out(temp_db, world.solo, "faces", "no_copy", transient=True)

    lines = await _lines(temp_db, access, actors, world.solo)
    assert sorted(_words(one) for one in lines) == [
        "Sift could not generate a thumbnail for this file",
        "Sift could not look for faces in this file yet",
    ]
    assert {one.kind for one in lines} == {"left_out"}
    # The tool's own text never reaches the line.
    assert all("the tool said" not in _words(one) for one in lines)


async def test_the_details_read_again_are_said_and_the_read_that_took_the_file_in_is_not(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    for asset_id, at in ((world.solo, LATER), (world.twin, ARRIVED + EPISODE_GAP)):
        await temp_db.execute(
            "INSERT INTO asset_probes (asset_id, probe_version, tool, body, probed_at)"
            " VALUES (?, 1, 'ffprobe', x'00', ?)",
            (asset_id, at),
        )

    [again] = await _lines(temp_db, access, actors, world.solo)
    assert _words(again) == "Sift read this file's details again"
    assert await _lines(temp_db, access, actors, world.twin) == []


async def test_a_stash_box_match_nobody_has_checked_says_the_box_was_asked(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'StashDB', ?, ?)",
        (BOX, "https://stashdb.example/graphql", ARRIVED),
    )
    await temp_db.execute(
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at)"
        " VALUES (?, ?, 'r1', '{}', 'likely', 'waiting', ?)",
        (world.solo, BOX, LATER),
    )

    [waiting] = await _lines(temp_db, access, actors, world.solo)
    assert _words(waiting) == "Sift asked StashDB and its match is waiting to be checked"
    assert (waiting.kind, waiting.actor) == ("asked", Actor.SIFT)


async def test_nobody_is_named_who_the_tables_do_not_record(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A press names its presser on the job, and none of these tables keeps it: Sift, not a guess
    at whoever happens to be reading."""
    await _music_print(temp_db, world.solo, b"\x01", LATER)
    await _asked(temp_db, world.solo, "nothing")
    await _left_out(temp_db, world.solo, "meaning", "no_frame_decoded", transient=False)

    for viewer in (actors.admin, actors.guest):
        drawn = await history_of_asset(temp_db, access, viewer, world.solo)
        assert {one.actor for one in drawn if one.kind in _KINDS} == {Actor.SIFT}


async def test_the_number_on_the_history_tab_is_the_length_of_the_list(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _music_print(temp_db, world.solo, b"\x01", LATER - 2)
    await _asked(temp_db, world.solo, "nothing")
    await _left_out(temp_db, world.solo, "previews", "not_decodable", transient=False)
    await temp_db.execute(
        "INSERT INTO asset_probes (asset_id, probe_version, tool, body, probed_at)"
        " VALUES (?, 1, 'ffprobe', x'00', ?)",
        (world.solo, LATER - 1),
    )

    before = await count_of_asset_history(temp_db, access, actors.admin, world.loose)
    for asset_id in (world.solo, world.loose):
        drawn = await history_of_asset(temp_db, access, actors.admin, asset_id)
        counted = await count_of_asset_history(temp_db, access, actors.admin, asset_id)
        assert counted == len(drawn)
    # The fingerprint and the read again are one sitting, so the four rows are three lines.
    kinds: Sequence[str] = [one.kind for one in await _lines(temp_db, access, actors, world.solo)]
    assert sorted(kinds) == ["asked", "left_out", "ready"]
    assert before == await count_of_asset_history(temp_db, access, actors.admin, world.loose)


async def test_acoustid_s_answer_is_said_where_the_music_fingerprints_table_is_not(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Each pass's line is guarded on its own table, so a library without the fingerprints table
    still says what AcoustID answered rather than losing the line or failing the page."""
    await _asked(temp_db, world.solo, "nothing")
    await temp_db.execute("DROP TABLE audio_fingerprints")

    lines = await _lines(temp_db, access, actors, world.solo)
    assert [_words(one) for one in lines] == ["Sift asked AcoustID and it didn't know the song"]
