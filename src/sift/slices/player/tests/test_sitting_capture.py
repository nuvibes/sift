# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a sitting keeps beyond its time: what it was opened from, what happened inside it, the
client it happened on, and who and what the file carried then. And the User's own say over it.

Each of these is a fact that cannot be worked out afterwards, so each is asserted as it lands in the
row, and each NULL is asserted where it means "not recorded" rather than "none".
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import sift.main  # noqa: F401 (every component registers its schema and its history clearing)
from sift.kernel.access import Repository
from sift.kernel.client import CLIENT_HEADER, DEVICE_COOKIE_NAME
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.use_history import register_clearing, registered_clearings
from sift.slices.player.about import about_of
from sift.slices.player.plays import (
    MOST_SEEKS,
    MOST_SPEEDS,
    About,
    Inside,
    Named,
    Place,
    clear_plays,
    record_play,
)
from sift.slices.player.tests.conftest import Library, db_path, read, sign_in, write
from sift.testing.fixtures import Actors, World, hide

_EPOCH = 1_700_000_000


async def _row(database: Database) -> dict[str, object]:
    (row,) = await database.fetch_all("SELECT * FROM plays")
    return dict(row)


async def _names(database: Database) -> list[tuple[str, str, str]]:
    rows = await database.fetch_all("SELECT kind, ref, name FROM play_names ORDER BY rowid")
    return [(str(row["kind"]), str(row["ref"]), str(row["name"])) for row in rows]


async def _piece(
    database: Database,
    actors: Actors,
    world: World,
    *,
    watch_ms: int,
    already: int | None,
    inside: Inside,
    about: About | None = None,
    sitting: str = "one-evening",
    seeks: int = 0,
) -> None:
    await record_play(
        database,
        user_id=actors.admin.id,
        asset_id=world.solo,
        watch_ms=watch_ms,
        already_reported_ms=already,
        position_ms=watch_ms,
        heat={},
        sitting=sitting,
        seeks=seeks,
        inside=inside,
        about=about,
    )


# --- inside a sitting ----------------------------------------------------------------------------


@pytest.mark.unit
async def test_what_happened_inside_a_sitting_adds_up_across_its_pieces(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The start is the first piece's; the seeks follow each other; the time at each speed, the
    time full screen and the passes through the end add; magnified stays once it was."""
    await _piece(
        temp_db,
        actors,
        world,
        watch_ms=30_000,
        already=None,
        seeks=1,
        inside=Inside(
            start_ms=12_000,
            seek_log=((40_000, 90_000),),
            speeds={1.0: 20_000, 1.5: 10_000},
            fullscreen_ms=5_000,
            completions=1,
            magnified=False,
        ),
    )
    await _piece(
        temp_db,
        actors,
        world,
        watch_ms=10_000,
        already=30_000,
        seeks=1,
        inside=Inside(
            start_ms=99_000,
            seek_log=((95_000, 0),),
            speeds={1.5: 10_000},
            fullscreen_ms=2_000,
            completions=2,
            magnified=True,
        ),
    )

    row = await _row(temp_db)
    assert row["start_ms"] == 12_000
    assert json.loads(str(row["seek_log"])) == [[40_000, 90_000], [95_000, 0]]
    assert json.loads(str(row["speeds"])) == {"1": 20_000, "1.5": 20_000}
    assert (row["fullscreen_ms"], row["completions"], row["magnified"]) == (7_000, 3, 1)
    assert row["seeks"] == 2


@pytest.mark.unit
async def test_seeks_past_the_cap_are_counted_and_not_kept(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A sitting scrubbed for an hour is still one row: the pairs stop at the cap, the count goes
    on, so a reader can tell how many were let go."""
    many = tuple((n * 1_000, n * 1_000 + 500) for n in range(MOST_SEEKS - 2))
    await _piece(
        temp_db,
        actors,
        world,
        watch_ms=1_000,
        already=None,
        seeks=len(many),
        inside=Inside(seek_log=many),
    )
    more = ((1, 2), (3, 4), (5, 6), (7, 8))
    await _piece(
        temp_db,
        actors,
        world,
        watch_ms=1_000,
        already=1_000,
        seeks=len(more),
        inside=Inside(seek_log=more),
    )

    row = await _row(temp_db)
    kept = json.loads(str(row["seek_log"]))
    assert len(kept) == MOST_SEEKS and kept[-2:] == [[1, 2], [3, 4]]
    assert row["seeks"] == MOST_SEEKS + 2


@pytest.mark.unit
async def test_speeds_past_the_cap_are_let_go_and_those_kept_go_on_adding(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A browser that sends a new speed every piece cannot grow the row: once the cap is reached a
    speed already kept still adds its time and a new one is not kept."""
    first = {1.0 + n / 10: 1_000 for n in range(MOST_SPEEDS)}
    await _piece(temp_db, actors, world, watch_ms=1_000, already=None, inside=Inside(speeds=first))
    await _piece(
        temp_db,
        actors,
        world,
        watch_ms=1_000,
        already=1_000,
        inside=Inside(speeds={1.0: 500, 9.0: 500}),
    )

    speeds = json.loads(str((await _row(temp_db))["speeds"]))
    assert len(speeds) == MOST_SPEEDS and "9" not in speeds
    assert speeds["1"] == 1_500


@pytest.mark.unit
async def test_an_unreadable_seek_list_or_speed_map_is_started_again_from_the_next_piece(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A stored value the reader cannot parse is not carried forward and does not refuse the
    piece: the next piece's own seeks and speeds are what the sitting keeps."""
    await _piece(
        temp_db,
        actors,
        world,
        watch_ms=1_000,
        already=None,
        inside=Inside(seek_log=((1, 2),), speeds={1.0: 1_000}),
    )
    await temp_db.execute("UPDATE plays SET seek_log = 'not a list', speeds = '[1, 2]'")
    await _piece(
        temp_db,
        actors,
        world,
        watch_ms=1_000,
        already=1_000,
        inside=Inside(seek_log=((3, 4),), speeds={2.0: 500}),
    )

    row = await _row(temp_db)
    assert json.loads(str(row["seek_log"])) == [[3, 4]]
    assert json.loads(str(row["speeds"])) == {"2": 500}


@pytest.mark.unit
async def test_a_piece_that_measured_nothing_inside_leaves_it_unrecorded(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """NULL is "not recorded", which is what every sitting before this reads as and what an older
    client sends; a later piece that does measure counts from itself, never from an invented zero
    for the piece before it."""
    await _piece(temp_db, actors, world, watch_ms=1_000, already=None, inside=Inside())
    row = await _row(temp_db)
    for column in (
        "start_ms",
        "seek_log",
        "speeds",
        "fullscreen_ms",
        "completions",
        "magnified",
        "opened_from_id",
        "device_id",
        "client_kind",
        "about",
    ):
        assert row[column] is None, column

    await _piece(
        temp_db, actors, world, watch_ms=1_000, already=1_000, inside=Inside(completions=1)
    )
    row = await _row(temp_db)
    assert row["completions"] == 1 and row["fullscreen_ms"] is None


# --- where, on what, about what -----------------------------------------------------------------


@pytest.mark.unit
async def test_the_thing_opened_from_and_the_client_are_written_by_the_first_piece(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    first = Place(
        screen="panel",
        opened_from="person",
        opened_from_id=world.person,
        device_id="a-device-of-sixteen",
        client_kind="phone",
    )
    later = Place(screen="panel", opened_from="tag", opened_from_id=world.tag, client_kind="app")
    for watch_ms, already, place in ((0, None, first), (4_000, 0, later)):
        await record_play(
            temp_db,
            user_id=actors.admin.id,
            asset_id=world.solo,
            watch_ms=watch_ms,
            already_reported_ms=already,
            position_ms=0,
            heat={},
            sitting="one-look",
            place=place,
        )

    row = await _row(temp_db)
    assert (row["opened_from"], row["opened_from_id"]) == ("person", world.person)
    assert (row["device_id"], row["client_kind"]) == ("a-device-of-sixteen", "phone")


@pytest.mark.unit
async def test_a_sitting_keeps_what_its_file_carried_and_a_rename_does_not_rewrite_it(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Ids in the row, names kept once per name: a second sitting under the same names writes no
    name, a rename writes one more, and the first sitting's ids are untouched by an unlink."""
    about = await about_of(temp_db, access, actors.admin, world.solo)
    assert [one.id for one in about.people] == [world.person]
    assert [one.id for one in about.tags] == [world.tag]
    assert [one.id for one in about.sites] == [world.site]
    await _piece(temp_db, actors, world, watch_ms=1_000, already=None, inside=Inside(), about=about)

    packed = json.loads(str((await _row(temp_db))["about"]))
    assert packed == {"people": [world.person], "tags": [world.tag], "sites": [world.site]}
    assert await _names(temp_db) == [
        ("person", world.person, "person"),
        ("tag", world.tag, "tag"),
        ("site", world.site, "site"),
    ]

    await _piece(
        temp_db,
        actors,
        world,
        watch_ms=1_000,
        already=None,
        inside=Inside(),
        about=about,
        sitting="again",
    )
    assert len(await _names(temp_db)) == 3

    await temp_db.execute("UPDATE tags SET name = 'renamed' WHERE id = ?", (world.tag,))
    await temp_db.execute("DELETE FROM asset_people WHERE person_id = ?", (world.person,))
    later = await about_of(temp_db, access, actors.admin, world.solo)
    await _piece(
        temp_db,
        actors,
        world,
        watch_ms=1_000,
        already=None,
        inside=Inside(),
        about=later,
        sitting="third",
    )

    assert (await _names(temp_db))[-1] == ("tag", world.tag, "renamed")
    rows = await temp_db.fetch_all("SELECT sitting, about FROM plays ORDER BY made_at, id")
    abouts = {str(row["sitting"]): json.loads(str(row["about"])) for row in rows}
    assert abouts["one-evening"]["people"] == [world.person]
    assert "people" not in abouts["third"]


@pytest.mark.unit
async def test_a_thing_the_viewer_may_not_be_shown_is_not_written_into_their_history(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await hide(temp_db, "tag", world.tag, actors.admin.id)

    about = await about_of(temp_db, access, actors.admin, world.solo)

    assert about.tags == () and [one.id for one in about.people] == [world.person]


@pytest.mark.unit
async def test_a_song_is_kept_with_its_name(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    song = new_id()
    await temp_db.execute(
        "INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, 'A Quiet Harbour', 'a quiet harbour', ?)",
        (song, _EPOCH),
    )
    await temp_db.execute(
        "INSERT INTO song_files (asset_id, song_id) VALUES (?, ?)", (world.solo, song)
    )

    about = await about_of(temp_db, access, actors.admin, world.solo)

    assert about.song == Named(song, "A Quiet Harbour")
    assert json.loads(about.packed())["song"] == song

    await _piece(temp_db, actors, world, watch_ms=1_000, already=None, inside=Inside(), about=about)

    assert ("song", song, "A Quiet Harbour") in await _names(temp_db)


# --- the User's own say -------------------------------------------------------------------------


@pytest.mark.unit
def test_a_second_clearing_under_one_name_is_refused_and_the_first_stays() -> None:
    """Two features clearing under one name would leave one part of a User's history kept after
    Clear, so the second is refused at import rather than quietly replacing the first."""

    async def another(_connection: object, _user_id: str) -> int:
        return 0

    with pytest.raises(ValueError, match="registered twice"):
        register_clearing("player", another)
    assert registered_clearings()["player"] is clear_plays


@pytest.mark.unit
async def test_clear_takes_the_sittings_the_names_and_the_theater_sessions_of_one_user_only(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    assert registered_clearings()["player"] is clear_plays
    about = About(people=(Named(world.person, "person"),))
    for user in (actors.admin, actors.guest):
        await record_play(
            temp_db,
            user_id=user.id,
            asset_id=world.solo,
            watch_ms=1_000,
            already_reported_ms=None,
            position_ms=None,
            heat={},
            about=about,
        )
        await temp_db.execute(
            "INSERT INTO theater_sessions (id, user_id, session, started_at, made_at)"
            " VALUES (?, ?, 'a-wall', ?, ?)",
            (new_id(), user.id, _EPOCH, _EPOCH),
        )

    async with temp_db.write() as connection:
        gone = await clear_plays(connection, actors.admin.id)

    assert gone == 3
    for table in ("plays", "play_names", "theater_sessions"):
        rows = await temp_db.fetch_all(f"SELECT user_id FROM {table}")  # noqa: S608  # nosemgrep: sift-no-string-built-sql
        assert [str(row["user_id"]) for row in rows] == [actors.guest.id], table


# --- through the route ----------------------------------------------------------------------------


@pytest.mark.integration
def test_the_route_stamps_the_client_and_keeps_a_folder_it_was_opened_from(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    asset_id = library.id_of("h264")
    folder = new_id()
    client.cookies.set(DEVICE_COOKIE_NAME, "a-device-0123456789")

    response = client.post(
        f"/api/assets/{asset_id}/view",
        headers={CLIENT_HEADER: "tablet"},
        json={
            "watch_ms": 1_000,
            "sitting": "from-a-folder",
            "screen": "panel",
            "opened_from": "folder",
            "opened_from_id": folder,
            "start_ms": 2_000,
            "seek_log": [{"from_ms": 2_500, "to_ms": 9_000}],
            "speeds": {"1": 600, "2": 400, "fast": 5, "64": 9},
            "fullscreen_ms": 300,
            "completions": 0,
        },
    )

    assert response.status_code == 204
    (row,) = read(db_path(client), "SELECT * FROM plays", ())
    assert (row["opened_from"], row["opened_from_id"]) == ("folder", folder)
    assert (row["device_id"], row["client_kind"]) == ("a-device-0123456789", "tablet")
    assert row["start_ms"] == 2_000 and json.loads(row["seek_log"]) == [[2_500, 9_000]]
    # A speed nobody can play at is dropped, not refused: the report arrives from a closing page.
    assert json.loads(row["speeds"]) == {"1": 600, "2": 400}
    assert (row["fullscreen_ms"], row["completions"], row["magnified"]) == (300, 0, None)


@pytest.mark.integration
def test_a_sitting_opened_from_a_search_keeps_the_record_of_that_search(
    client: TestClient, library: Library
) -> None:
    user_id = sign_in(client)
    asset_id = library.id_of("h264")
    event = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO search_events (id, user_id, kind, subject, at) VALUES (?, ?, 'query', ?, ?)",
                (event, user_id, "quiet harbour", _EPOCH),
            )
        ],
    )

    client.post(
        f"/api/assets/{asset_id}/view",
        json={
            "watch_ms": 1_000,
            "sitting": "from-a-search",
            "opened_from": "search",
            "searched": "  quiet   harbour ",
            "opened_from_id": "ignored-for-a-search",
        },
    )
    client.post(
        f"/api/assets/{asset_id}/view",
        json={"watch_ms": 1_000, "opened_from": "search", "searched": "never searched"},
    )

    rows = read(db_path(client), "SELECT sitting, opened_from_id FROM plays ORDER BY sitting", ())
    assert [(row["sitting"], row["opened_from_id"]) for row in rows] == [
        (None, None),
        ("from-a-search", event),
    ]


@pytest.mark.integration
def test_a_search_with_no_words_is_tied_to_no_record(client: TestClient, library: Library) -> None:
    """Blank words name no search, so the sitting keeps no link even where some record's subject
    is blank too: a sitting is tied to the search a person typed, never to a guess."""
    user_id = sign_in(client)
    write(
        db_path(client),
        [
            (
                "INSERT INTO search_events (id, user_id, kind, subject, at) VALUES (?, ?, 'query', '', ?)",
                (new_id(), user_id, _EPOCH),
            )
        ],
    )

    client.post(
        f"/api/assets/{library.id_of('h264')}/view",
        json={"watch_ms": 1_000, "sitting": "blank", "opened_from": "search", "searched": "   "},
    )

    rows = read(db_path(client), "SELECT opened_from, opened_from_id FROM plays", ())
    assert [(row["opened_from"], row["opened_from_id"]) for row in rows] == [("search", None)]


@pytest.mark.integration
def test_insights_is_a_screen_a_file_is_opened_from(client: TestClient, library: Library) -> None:
    sign_in(client)

    response = client.post(
        f"/api/assets/{library.id_of('h264')}/view",
        json={"watch_ms": 1_000, "opened_from": "insights"},
    )

    assert response.status_code == 204
    (row,) = read(db_path(client), "SELECT opened_from FROM plays", ())
    assert row["opened_from"] == "insights"


@pytest.mark.integration
def test_a_paused_history_writes_no_sitting_and_still_counts_the_view(
    client: TestClient, library: Library
) -> None:
    """The pause is the User's: no sitting while it is on. The view count and the resume point are
    not the record and go on working, or nothing could be picked up where it was left."""
    user_id = sign_in(client)
    asset_id = library.id_of("h264")
    assert client.put("/api/settings", json={"values": {"history.keep": False}}).status_code == 204

    client.post(f"/api/assets/{asset_id}/view", json={"watch_ms": 4_000, "sitting": "paused"})

    assert read(db_path(client), "SELECT id FROM plays", ()) == []
    (state,) = read(
        db_path(client),
        "SELECT view_count FROM asset_user_state WHERE asset_id = ? AND user_id = ?",
        (asset_id, user_id),
    )
    assert state["view_count"] == 1
