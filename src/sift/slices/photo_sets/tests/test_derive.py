# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two rules that make a photo set without anybody asking.

Both rules are mostly about REFUSING, and that is where these tests are pointed. Grouping too
eagerly fills somebody's library with rows they did not make and have to delete by hand; grouping
too rarely costs one press of the manual verb. So every threshold is tested from both sides, at the
boundary, rather than only in the case where it fires.

Driven against the derive functions with the running application's own store and service, because
that is how they run: neither is reachable from a route, and both are handed their dependencies by
the composition root. The rest of the slice's world (a folder, real bytes, a clip sitting beside the
pictures) comes from the shared fixtures, so what is written here is only the part being tested.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Repository
from sift.kernel.content import ContentStore
from sift.kernel.db import Database, IntegrityError
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.kernel.photo_sets import MIN_PICTURES
from sift.kernel.vocabulary import VIA_FOLDER
from sift.slices.photo_sets.derive import (
    set_from_archive,
    set_from_folder,
    set_from_post,
    set_from_shoot,
    set_from_stash_library,
)
from sift.slices.photo_sets.service import _BY_FOLDER, PhotoSet, PhotoSetService
from sift.slices.photo_sets.tests.conftest import Shoot, db_path, read, write

pytestmark = [pytest.mark.integration]

#: What `_with_a_database` runs: a rule, already holding everything but its two dependencies.
Runner = Callable[[ContentStore, PhotoSetService], Awaitable[str | None]]

_EPOCH = 1_700_000_000

_INSERT_ASSET = """
INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at)
VALUES (?, ?, ?, 10, ?, ?)
"""
_INSERT_LOCATION = """
INSERT INTO asset_locations
    (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def put(
    client: TestClient, shoot: Shoot, name: str, kind: str, *, folder: str | None = None
) -> str:
    """One more file in the library, of a named kind. Returns its id."""
    asset_id = new_id()
    write(
        db_path(client),
        [
            (_INSERT_ASSET, (asset_id, f"digest-{name}", kind, name, _EPOCH)),
            (
                _INSERT_LOCATION,
                (
                    new_id(),
                    asset_id,
                    shoot.root,
                    folder or shoot.folder,
                    f"shoot/{name}",
                    name,
                    _EPOCH,
                    _EPOCH,
                ),
            ),
        ],
    )
    return asset_id


def derive_folder(client: TestClient, folder_id: str, name: str = "shoot") -> str | None:
    """Run the folder rule the way the composition root runs it, and report what it made."""

    async def run(content: ContentStore, service: PhotoSetService) -> str | None:
        return await set_from_folder(folder_id, name=name, content=content, service=service)

    return _with_a_database(client, run)


def fill_to_floor(client: TestClient, shoot: Shoot, *, short_by: int = 0) -> list[str]:
    """Pictures enough that the fixture's folder holds `MIN_PICTURES - short_by` of them.

    The fixture starts with two. Written against the constant so the day the floor moves nothing
    here needs to be counted.
    """
    wanted = MIN_PICTURES - short_by - 2
    return [put(client, shoot, f"{index:03}.jpg", "image") for index in range(3, 3 + wanted)]


def derive_archive(
    client: TestClient,
    asset_ids: list[str],
    *,
    root_id: str,
    rel_path: str = "galleries/100200.zip",
    name: str = "100200",
) -> str | None:
    """And the archive rule, keyed on the library and the path the way the scan calls it."""

    async def run(content: ContentStore, service: PhotoSetService) -> str | None:
        return await set_from_archive(
            asset_ids,
            root_id=root_id,
            rel_path=rel_path,
            name=name,
            content=content,
            service=service,
        )

    return _with_a_database(client, run)


def _with_a_database(client: TestClient, run: Runner) -> str | None:
    """Open a connection of this test's own, run the rule against it, and always close it.

    A connection of its own for the reason every fixture in this slice gives: the client drives the
    application on its own event loop, and a write issued from the test's loop meets a lock held on
    that one. Closed in a `finally` because a test that fails with one open leaves a thread holding
    the file, and the session then hangs at exit instead of reporting the failure.
    """
    settings = client.app.state.settings  # type: ignore[attr-defined]

    async def go() -> str | None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            content = ContentStore(database, settings)
            service = PhotoSetService(database, Repository(database, content))
            return await run(content, service)
        finally:
            await database.close()

    return asyncio.run(go())


# --- a folder of pictures --------------------------------------------------------------------


def test_a_folder_of_pictures_becomes_a_set(client: TestClient, shoot: Shoot) -> None:
    """The clip is taken out first, because the fixture's folder has one in it.

    Which is the point of using that fixture: the folder starts as one this rule must refuse, and
    the test has to make it into a shoot before it can pass.
    """
    write(db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (shoot.clip,))])
    fill_to_floor(client, shoot)

    made = derive_folder(client, shoot.folder)
    assert made is not None
    sets = read(db_path(client), "SELECT * FROM photo_sets", ())
    assert len(sets) == 1
    assert sets[0]["origin"] == "folder"
    assert sets[0]["folder_id"] == shoot.folder
    assert sets[0]["cover_asset_id"] is not None
    assert len(read(db_path(client), "SELECT * FROM photo_set_items", ())) == MIN_PICTURES


def test_one_picture_short_of_the_threshold_makes_nothing(client: TestClient, shoot: Shoot) -> None:
    """The boundary from below. Two files together is a pair, not a shoot."""
    write(db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (shoot.clip,))])
    assert derive_folder(client, shoot.folder) is None
    assert read(db_path(client), "SELECT * FROM photo_sets", ()) == []


def test_one_video_is_enough_to_refuse_the_whole_folder(client: TestClient, shoot: Shoot) -> None:
    """The discriminator, and the reason the rule is not "mostly pictures".

    The fixture's folder is exactly this case already: two stills and a clip. Adding pictures until
    it is well over the threshold proves the refusal is about the clip and not about the count.
    """
    for index in range(MIN_PICTURES + 3):
        put(client, shoot, f"{index:03}.jpg", "image")

    assert derive_folder(client, shoot.folder) is None
    assert read(db_path(client), "SELECT * FROM photo_sets", ()) == []


def test_a_gif_counts_as_a_picture(client: TestClient, shoot: Shoot) -> None:
    """A folder of reaction GIFs is a set of pictures in every way that shows on screen, and they
    are drawn on the same wall. Video is the discriminator, not "moves"."""
    write(db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (shoot.clip,))])
    fill_to_floor(client, shoot, short_by=1)
    put(client, shoot, "last.gif", "gif")

    assert derive_folder(client, shoot.folder) is not None


def test_sweeping_one_folder_twice_fills_the_set_rather_than_making_a_second(
    client: TestClient, shoot: Shoot
) -> None:
    """The property that makes it safe to run on every scan for ever.

    Two sets over one folder would be two answers to "what was this shoot", and a scan runs again
    every time the watcher notices anything at all.
    """
    write(db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (shoot.clip,))])
    fill_to_floor(client, shoot)
    first = derive_folder(client, shoot.folder)

    put(client, shoot, "extra.jpg", "image")
    second = derive_folder(client, shoot.folder)

    assert first == second
    assert len(read(db_path(client), "SELECT * FROM photo_sets", ())) == 1
    assert len(read(db_path(client), "SELECT * FROM photo_set_items", ())) == MIN_PICTURES + 1


def test_a_grouping_that_loses_the_race_to_make_a_folders_set_takes_the_winners(
    client: TestClient, shoot: Shoot
) -> None:
    """Two groupings can meet one folder at once (a scan beside a Stash import). The look finds
    nothing, both go to create, the unique index refuses the second: the loser answers with the
    winner's set rather than failing, so one folder is one set however the two are timed."""
    write(db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (shoot.clip,))])
    fill_to_floor(client, shoot)

    async def race(content: ContentStore, service: PhotoSetService) -> str | None:
        looked = service._db.fetch_one

        async def nothing_yet(sql: str, params: tuple[object, ...]) -> object:
            # The winner makes the set between this grouping's look and its write.
            if sql is _BY_FOLDER and not getattr(nothing_yet, "won", False):
                nothing_yet.won = True  # type: ignore[attr-defined]
                await service.create("winner", origin="folder", folder_id=shoot.folder)
                return None
            return await looked(sql, params)

        service._db.fetch_one = nothing_yet  # type: ignore[method-assign, assignment]
        return await set_from_folder(shoot.folder, name="shoot", content=content, service=service)

    made = _with_a_database(client, race)
    sets = read(db_path(client), "SELECT id, name FROM photo_sets", ())
    assert len(sets) == 1 and sets[0]["name"] == "winner" and made == sets[0]["id"]


def test_sweeping_a_folder_again_keeps_the_cover_somebody_chose(
    client: TestClient, shoot: Shoot
) -> None:
    """The first picture becomes the cover only when there is not one already: a cover somebody
    chose is a decision, and this is a default. A scan runs again on every change to the folder,
    so a default that re-applied would undo the choice the next time a picture arrived."""
    write(db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (shoot.clip,))])
    fill_to_floor(client, shoot)
    made = derive_folder(client, shoot.folder)
    write(
        db_path(client),
        [("UPDATE photo_sets SET cover_asset_id = ? WHERE id = ?", (shoot.second, made))],
    )

    put(client, shoot, "extra.jpg", "image")
    assert derive_folder(client, shoot.folder) == made

    (row,) = read(db_path(client), "SELECT cover_asset_id FROM photo_sets WHERE id = ?", (made,))
    assert row["cover_asset_id"] == shoot.second


def test_sweeping_an_unchanged_folder_again_adds_nothing_and_says_nothing(
    client: TestClient, shoot: Shoot
) -> None:
    """The ordinary case, which is every scan of a library nobody has touched.

    The set is found rather than made and nothing is added, so the line that announces a shoot must
    not fire again: a log that says a shoot was derived on every scan of an unchanged folder is a
    log nobody can read a real event out of.
    """
    write(db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (shoot.clip,))])
    fill_to_floor(client, shoot)
    first = derive_folder(client, shoot.folder)

    second = derive_folder(client, shoot.folder)

    assert first == second
    assert len(read(db_path(client), "SELECT * FROM photo_sets", ())) == 1
    assert len(read(db_path(client), "SELECT * FROM photo_set_items", ())) == MIN_PICTURES


def test_only_the_folders_own_files_are_looked_at(client: TestClient, shoot: Shoot) -> None:
    """A video in a subfolder does not disqualify the shoot above it.

    Reading the subtree would make the answer depend on how somebody nested their library rather
    than on what is in the folder, and a shoot with an outtakes folder under it is still a shoot.
    """
    below = new_id()
    write(
        db_path(client),
        [
            ("DELETE FROM asset_locations WHERE asset_id = ?", (shoot.clip,)),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
                (below, shoot.root, shoot.folder, "shoot/extras", "extras"),
            ),
        ],
    )
    fill_to_floor(client, shoot)
    put(client, shoot, "outtake.mp4", "video", folder=below)

    assert derive_folder(client, shoot.folder) is not None


# --- what any derived set is -----------------------------------------------------------------


def test_a_derived_run_of_pictures_becomes_a_set_in_the_order_it_was_handed_in(
    client: TestClient, shoot: Shoot
) -> None:
    """The order is the assertion. A shoot arrives numbered, and a set in any other order is that
    shoot shuffled, which is why the ids are handed in rather than read back from a query."""
    arrived = [put(client, shoot, f"{index:03}.jpg", "image") for index in range(MIN_PICTURES + 1)]
    arrived.reverse()

    made = _derive_shoot(client, arrived, name="summer")
    assert made is not None
    members = [
        str(row["asset_id"])
        for row in read(
            db_path(client),
            "SELECT asset_id FROM photo_set_items WHERE photo_set_id = ? ORDER BY position",
            (made,),
        )
    ]
    assert members == arrived


def test_a_derived_set_is_an_ordinary_set_on_the_wall(client: TestClient, shoot: Shoot) -> None:
    """The end of the rule is a row somebody can open, not a row in a table.

    Over HTTP on purpose: everything above writes through the service, and a set that never appeared
    on the wall would pass every one of those tests.
    """
    from sift.slices.photo_sets.tests.conftest import sign_in

    sign_in(client)
    arrived = [put(client, shoot, f"{index:03}.jpg", "image") for index in range(MIN_PICTURES)]
    made = _derive_shoot(client, arrived, name="summer")

    wall = client.get("/api/photo-sets").json()
    assert [one["id"] for one in wall["items"]] == [made]
    assert wall["items"][0]["name"] == "summer"
    assert wall["items"][0]["origin"] == "shoot"
    assert client.get(f"/api/photo-sets/{made}").json()["item_count"] == MIN_PICTURES


# --- a ZIP of pictures ------------------------------------------------------------------------
#
# A rule that created a set unconditionally, with no identity on an archive-derived set, would make
# another one on every scan of the same archive: one row per scan for one shoot, and the pictures
# not duplicated at all, which is what would make it read as several shoots rather than as a
# fault.


def test_an_archive_of_pictures_becomes_a_set(client: TestClient, shoot: Shoot) -> None:
    """The identity is written down, not merely the grouping."""
    pictures = [put(client, shoot, f"1{n:02d}.jpg", "image") for n in range(MIN_PICTURES)]

    made = derive_archive(client, pictures, root_id=shoot.root)

    assert made is not None
    sets = read(db_path(client), "SELECT * FROM photo_sets", ())
    assert len(sets) == 1
    assert sets[0]["origin"] == "archive"
    assert sets[0]["archive_root_id"] == shoot.root
    assert sets[0]["archive_rel_path"] == "galleries/100200.zip"
    assert sets[0]["cover_asset_id"] == pictures[0]


def test_scanning_one_archive_twice_leaves_one_set(client: TestClient, shoot: Shoot) -> None:
    """One archive is one set, however often it is scanned.

    The second call is the SAME archive with the same pictures, which is what every rescan of an
    unchanged library is. Without the identity this would leave two rows, and one more per scan.
    """
    pictures = [put(client, shoot, f"2{n:02d}.jpg", "image") for n in range(MIN_PICTURES)]

    first = derive_archive(client, pictures, root_id=shoot.root)
    second = derive_archive(client, pictures, root_id=shoot.root)

    assert first == second
    assert len(read(db_path(client), "SELECT * FROM photo_sets", ())) == 1
    assert len(read(db_path(client), "SELECT * FROM photo_set_items", ())) == MIN_PICTURES


def test_a_rescan_that_found_more_pictures_fills_the_set_it_already_made(
    client: TestClient, shoot: Shoot
) -> None:
    """Idempotent is not the same as inert: what arrived since still goes in."""
    pictures = [put(client, shoot, f"3{n:02d}.jpg", "image") for n in range(MIN_PICTURES)]
    first = derive_archive(client, pictures, root_id=shoot.root)

    pictures.append(put(client, shoot, "3ZZ.jpg", "image"))
    second = derive_archive(client, pictures, root_id=shoot.root)

    assert first == second
    assert len(read(db_path(client), "SELECT * FROM photo_sets", ())) == 1
    assert len(read(db_path(client), "SELECT * FROM photo_set_items", ())) == MIN_PICTURES + 1


def test_the_same_path_in_two_libraries_is_two_shoots(client: TestClient, shoot: Shoot) -> None:
    """Why the key is the pair and not the path.

    Two libraries can each hold a `galleries/100200.zip`, and they are two different shoots. Keyed
    on the path alone this folds one library's pictures into the other library's set, which is a
    worse fault than the duplicate it would be fixing, because it is a wrong answer rather than an
    extra one.

    The second library is a real row because the column is a real reference. A set naming a library
    that is not there would be a set claiming to mirror somewhere nothing can look.
    """
    other_root = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (other_root, "second", "/libraries/second", _EPOCH),
            )
        ],
    )
    here = [put(client, shoot, f"4{n:02d}.jpg", "image") for n in range(MIN_PICTURES)]
    elsewhere = [put(client, shoot, f"5{n:02d}.jpg", "image") for n in range(MIN_PICTURES)]

    first = derive_archive(client, here, root_id=shoot.root)
    second = derive_archive(client, elsewhere, root_id=other_root)

    assert first != second
    assert len(read(db_path(client), "SELECT * FROM photo_sets", ())) == 2


def test_one_video_in_the_archive_refuses_the_whole_thing(client: TestClient, shoot: Shoot) -> None:
    """The same discriminator the other two rules use, asserted here because this function makes
    its own promise about what it PRODUCES rather than about who happens to call it."""
    mixed = [put(client, shoot, f"6{n:02d}.jpg", "image") for n in range(MIN_PICTURES)]
    mixed.append(put(client, shoot, "6ZZ.mp4", "video"))

    assert derive_archive(client, mixed, root_id=shoot.root) is None
    assert read(db_path(client), "SELECT * FROM photo_sets", ()) == []


def test_two_pictures_in_an_archive_are_not_a_shoot(client: TestClient, shoot: Shoot) -> None:
    """The threshold from below, the same as the other two rules."""
    pair = [put(client, shoot, f"7{n:02d}.jpg", "image") for n in range(MIN_PICTURES - 1)]

    assert derive_archive(client, pair, root_id=shoot.root) is None
    assert read(db_path(client), "SELECT * FROM photo_sets", ()) == []


# --- a shoot somebody agreed to, and one post's pictures ----------------------------------------


def _derive_shoot(client: TestClient, asset_ids: list[str], name: str = "a shoot") -> str | None:
    async def run(content: ContentStore, service: PhotoSetService) -> str | None:
        return await set_from_shoot(asset_ids, name=name, content=content, service=service)

    return _with_a_database(client, run)


def _derive_post(
    client: TestClient, asset_ids: list[str], name: str = "quietharbour"
) -> str | None:
    async def run(content: ContentStore, service: PhotoSetService) -> str | None:
        made = await set_from_post(asset_ids, name=name, content=content, service=service)
        return None if made is None else f"{made.id} {made.name}"

    return _with_a_database(client, run)


def test_an_agreed_shoot_becomes_a_set_that_says_a_pass_proposed_it(
    client: TestClient, shoot: Shoot
) -> None:
    """Its own origin, so a later pass can tell it from one a person assembled, and no address:
    nothing was visited to find it."""
    pictures = [put(client, shoot, f"{index:03}.jpg", "image") for index in range(MIN_PICTURES)]

    made = _derive_shoot(client, pictures, name="By the window")

    assert made is not None
    [row] = read(db_path(client), "SELECT * FROM photo_sets", ())
    assert (row["id"], row["name"], row["origin"], row["origin_url"]) == (
        made,
        "By the window",
        "shoot",
        None,
    )
    assert row["cover_asset_id"] == pictures[0]


def test_a_shoot_short_of_the_floor_makes_nothing(client: TestClient, shoot: Shoot) -> None:
    pictures = [put(client, shoot, f"{index:03}.jpg", "image") for index in range(MIN_PICTURES - 1)]

    assert _derive_shoot(client, pictures) is None
    assert read(db_path(client), "SELECT * FROM photo_sets", ()) == []


def test_one_posts_pictures_become_a_set_read_from_their_names(
    client: TestClient, shoot: Shoot
) -> None:
    """The row comes back, so the receipt names what the set is CALLED; the origin says the names
    were read, not that anything was fetched."""
    pictures = [put(client, shoot, f"{index:03}.jpg", "image") for index in range(MIN_PICTURES)]

    made = _derive_post(client, pictures, name="quietharbour")

    assert made is not None
    [row] = read(db_path(client), "SELECT * FROM photo_sets", ())
    assert made == f"{row['id']} quietharbour"
    assert (row["origin"], row["origin_url"]) == ("filename", None)


def test_a_post_with_a_video_in_it_makes_no_set(client: TestClient, shoot: Shoot) -> None:
    pictures = [put(client, shoot, f"{index:03}.jpg", "image") for index in range(MIN_PICTURES)]
    pictures.append(put(client, shoot, "clip.mp4", "video"))

    assert _derive_post(client, pictures) is None
    assert read(db_path(client), "SELECT * FROM photo_sets", ()) == []


# --- what a folder holds of its own ---------------------------------------------------------------


def _member_of(client: TestClient, shoot: Shoot, name: str) -> str:
    """A picture inside `gallery.zip`, which sits in the fixture's folder. Returns its id."""
    asset_id = new_id()
    write(
        db_path(client),
        [
            (_INSERT_ASSET, (asset_id, f"digest-{name}", "image", name, _EPOCH)),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
                " first_seen_at, last_seen_at, archive_rel_path, member_path)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    asset_id,
                    shoot.root,
                    shoot.folder,
                    f"shoot/gallery.zip/{name}",
                    name,
                    _EPOCH,
                    _EPOCH,
                    "shoot/gallery.zip",
                    name,
                ),
            ),
        ],
    )
    return asset_id


def test_a_folder_holding_only_an_archive_is_not_a_second_set_of_its_pictures(
    client: TestClient, shoot: Shoot
) -> None:
    """The archive's pictures carry the folder the archive sits in, and they are the archive's set.
    Counted as the folder's own, the same pictures would make a second set with the folder's
    name."""
    write(
        db_path(client),
        [
            (
                "DELETE FROM asset_locations WHERE asset_id IN (?, ?, ?)",
                (*shoot.pictures, shoot.clip),
            )
        ],
    )
    members = [_member_of(client, shoot, f"{index:03}.jpg") for index in range(MIN_PICTURES + 2)]

    assert derive_folder(client, shoot.folder) is None
    assert derive_archive(client, members, root_id=shoot.root, rel_path="shoot/gallery.zip")
    assert len(read(db_path(client), "SELECT * FROM photo_sets", ())) == 1


def test_a_folder_is_judged_by_the_files_that_are_there(client: TestClient, shoot: Shoot) -> None:
    """A copy marked missing is not in the folder now: the clip that has gone no longer refuses the
    shoot, and pictures that have gone do not count towards one."""
    write(
        db_path(client),
        [("UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (shoot.clip,))],
    )
    fill_to_floor(client, shoot)
    assert derive_folder(client, shoot.folder) is not None

    write(
        db_path(client),
        [
            ("DELETE FROM photo_sets", ()),
            ("UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (shoot.first,)),
        ],
    )
    assert derive_folder(client, shoot.folder) is None


# --- a folder taken out of the library and added back ---------------------------------------------


def _taken_out_and_back(client: TestClient, shoot: Shoot) -> str:
    """What removing the library and adding it again does to the rows: the folder and the library
    get new rows, the pictures are the same files. Returns the new folder's id."""
    root, folder = new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root, "library again", "/libraries/again", _EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
                (folder, root, None, "shoot", "shoot"),
            ),
            # The foreign keys' own SET NULL, written out: the old rows are not deleted here, so
            # the fixture's cleanup is untouched.
            ("UPDATE photo_sets SET folder_id = NULL, archive_root_id = NULL", ()),
            ("UPDATE asset_locations SET folder_id = ?, root_id = ?", (folder, root)),
        ],
    )
    return folder


def test_a_folder_added_back_finds_its_set_rather_than_making_a_second(
    client: TestClient, shoot: Shoot
) -> None:
    """The set keeps its id, and with it its name, cover and tags."""
    write(db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (shoot.clip,))])
    fill_to_floor(client, shoot)
    made = derive_folder(client, shoot.folder)
    write(db_path(client), [("UPDATE photo_sets SET name = 'renamed by somebody'", ())])

    folder = _taken_out_and_back(client, shoot)
    again = derive_folder(client, folder)

    assert again == made
    [row] = read(db_path(client), "SELECT * FROM photo_sets", ())
    assert (row["folder_id"], row["name"]) == (folder, "renamed by somebody")


def test_a_folder_holding_a_few_of_an_old_shoots_pictures_does_not_take_it_over(
    client: TestClient, shoot: Shoot
) -> None:
    """Most both ways: here the new folder's pictures are mostly new, so it is a shoot of its own."""
    write(db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (shoot.clip,))])
    fill_to_floor(client, shoot)
    made = derive_folder(client, shoot.folder)
    folder = _taken_out_and_back(client, shoot)
    for index in range(MIN_PICTURES + 1):
        put(client, shoot, f"new-{index:03}.jpg", "image", folder=folder)

    again = derive_folder(client, folder)

    assert again is not None and again != made
    assert len(read(db_path(client), "SELECT * FROM photo_sets", ())) == 2


def test_an_archive_added_back_finds_its_set_rather_than_making_a_second(
    client: TestClient, shoot: Shoot
) -> None:
    pictures = [put(client, shoot, f"8{n:02d}.jpg", "image") for n in range(MIN_PICTURES)]
    made = derive_archive(client, pictures, root_id=shoot.root)
    other = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (other, "library again", "/libraries/again", _EPOCH),
            ),
            ("UPDATE photo_sets SET archive_root_id = NULL", ()),
        ],
    )

    again = derive_archive(client, pictures, root_id=other)

    assert again == made
    [row] = read(db_path(client), "SELECT * FROM photo_sets", ())
    assert row["archive_root_id"] == other


def test_an_archive_grouped_after_a_stash_import_takes_the_set_the_import_made(
    client: TestClient, shoot: Shoot
) -> None:
    """With ZIP sets switched off at the scan, the import makes the gallery's set; switched on, the
    archive's own grouping takes that set rather than making the shoot twice."""
    pictures = [put(client, shoot, f"9{n:02d}.jpg", "image") for n in range(MIN_PICTURES)]

    async def imported(content: ContentStore, service: PhotoSetService) -> str | None:
        return await set_from_stash_library(
            pictures, name="harbor", content=content, service=service
        )

    made = _with_a_database(client, imported)
    grouped = derive_archive(client, pictures, root_id=shoot.root, rel_path="harbor.zip")

    assert grouped == made
    [row] = read(db_path(client), "SELECT * FROM photo_sets", ())
    assert row["origin"] == "stash_library"
    assert (row["archive_root_id"], row["archive_rel_path"]) == (shoot.root, "harbor.zip")


def test_the_set_holding_a_grouping_is_one_with_every_picture_where_they_are_most_of_it(
    client: TestClient, shoot: Shoot
) -> None:
    """A grouping read from elsewhere takes the set already made of its pictures rather than
    making a second; a set it only overlaps, or one far bigger than it, is not its set."""

    answers: list[str | None] = []

    async def run(content: ContentStore, service: PhotoSetService) -> str | None:
        made = await service.create("three")
        pictures = [shoot.first, shoot.second, shoot.clip]
        await service.add(made.id, pictures, actor=Actor.sift(VIA_FOLDER))
        answers.append(await service.holding([shoot.first, shoot.second, shoot.first]))
        answers.append(await service.holding([shoot.first]))
        answers.append(await service.holding([shoot.first, new_id()]))
        answers.append(await service.holding([]))
        return made.id

    made = _with_a_database(client, run)

    assert answers == [made, None, None, None]


def test_a_refusal_that_is_not_another_grouping_s_set_is_not_swallowed(
    client: TestClient, shoot: Shoot
) -> None:
    """Only the unique index losing to a winner is answered with the winner's set. Any other
    refusal of the write, with no set there to take, is raised rather than read as nothing."""

    async def run(content: ContentStore, service: PhotoSetService) -> str | None:
        async def refused() -> PhotoSet:
            raise IntegrityError("FOREIGN KEY constraint failed")

        with pytest.raises(IntegrityError):
            await service._made_once(refused, _BY_FOLDER, (new_id(),))
        return None

    _with_a_database(client, run)
