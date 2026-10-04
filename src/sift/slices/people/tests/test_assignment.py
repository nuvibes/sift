# SPDX-License-Identifier: AGPL-3.0-or-later
"""Attaching a person to media, and the attribution a download gets for free.

The load-bearing test in this file is the one asserting that assigning somebody moves no file.
That is the promise the whole storage model makes (organise logically, rearrange nothing), and
it is the kind of promise that stays true until one day it quietly does not.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from sift.kernel.access import (
    MADE_BY_A_PERSON,
    Repository,
    link_username_to_asset,
    seed_site_username,
)
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.people.service import PeopleService
from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    Library,
    assign,
    db_path,
    grant_on,
    make_person,
    make_username,
    read,
    sign_in,
    write,
)
from sift.testing.fixtures import Actors, World


def _seed_like_a_download(path: Path, *, site: str, name: str, asset_id: str) -> str:
    """The two calls a download makes to attribute what it just fetched.

    Driven directly rather than by running a real download, because a download needs the network,
    an extractor and a file. What is under test is the attribution, and these are the same two
    kernel functions the download service calls, imported from the same place, so a change to
    either is a change to both.
    """

    async def run() -> str:
        database = Database(path, readers=1)
        await database.connect()
        try:
            _, username_id = await seed_site_username(
                database, site=site, name=name, made=MADE_BY_A_PERSON
            )
            await link_username_to_asset(database, asset_id=asset_id, username_id=username_id)
            return username_id
        finally:
            await database.close()

    return asyncio.run(run())


def test_assigning_a_person_moves_no_file(client: TestClient, library: Library) -> None:
    """The one that matters most.

    Every path, every byte and every location row is exactly as it was. Assigning somebody writes
    one row in a join table; if this ever fails, the feature has started rearranging a library
    that somebody else arranged.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")

    before_bytes = library.path_of("shared").read_bytes()
    before_locations = read(db_path(client), "SELECT * FROM asset_locations ORDER BY id")

    response = assign(client, [library.shared], [person])

    assert response.status_code == 200, response.text
    assert response.json() == {
        "changed": 1,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }

    assert library.path_of("shared").exists()
    assert library.path_of("shared").read_bytes() == before_bytes
    assert read(db_path(client), "SELECT * FROM asset_locations ORDER BY id") == before_locations
    assert sorted(p.name for p in (library.media / "clips").iterdir()) == [
        "private.mp4",
        "shared.mp4",
    ]


def test_assigning_the_same_person_twice_changes_nothing_the_second_time(
    client: TestClient, library: Library
) -> None:
    """Drag the same clip onto the same face twice and the second is a no-op."""
    sign_in(client)
    person = make_person(client, "Jane Doe")

    assert assign(client, [library.shared], [person]).json() == {
        "changed": 1,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert assign(client, [library.shared], [person]).json() == {
        "changed": 0,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }


def test_a_person_can_be_taken_off_again(client: TestClient, library: Library) -> None:
    sign_in(client)
    person = make_person(client, "Jane Doe")
    assign(client, [library.shared], [person])

    assert assign(client, [library.shared], [person], add=False).json() == {
        "changed": 1,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert client.get(f"/api/assets/{library.shared}/people").json() == []


def _count_of(client: TestClient, person_id: str) -> int:
    """How many files the People wall says this person is in."""
    listed = client.get("/api/people").json()["items"]
    return int(next(row["asset_count"] for row in listed if row["id"] == person_id))


def test_taking_a_person_off_takes_the_file_off_their_count(
    client: TestClient, library: Library
) -> None:
    """The other half of a detach, and the half nothing was watching.

    Somebody named onto the wrong file is only really off it once the wall stops counting it. The
    count is worked out from the join rather than stored, so this is what proves the row went:
    a detach that only stopped the file's own screen listing them would pass the test above.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")
    assign(client, [library.shared], [person])
    assert _count_of(client, person) == 1

    assign(client, [library.shared], [person], add=False)

    assert _count_of(client, person) == 0


def test_one_unknown_asset_is_skipped_and_the_rest_are_assigned(
    client: TestClient, library: Library
) -> None:
    """The file that could be reached IS assigned, and the reply says one was left out and why.

    Not a 404 over the whole call: "a partial success that does not say which part succeeded is
    worse than a refusal" is an argument against a SILENT partial success, and this is one that
    speaks. The case that matters is not a made-up id at all: it is a selection of three with one
    of them in the viewer's own vault, which a whole-call refusal would assign none of, saying only
    "That person could not be assigned."
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")

    response = assign(client, [library.shared, NEVER_EXISTED], [person])

    assert response.status_code == 200
    assert response.json() == {
        "changed": 1,
        "skipped": 1,
        "reason": "Sift could not find the file.",
        # And the same refusal worded for more than one, which the screen picks between using a
        # count only it knows. See `kernel.reach.BulkWriteDone.reason`.
        "reason_many": "Sift could not find the files.",
        # Not the vault: a made-up id is out of reach for a reason no PIN would fix, and offering
        # to unlock for it would be an offer that changes nothing.
        "vault_locked": False,
    }
    assert [row["name"] for row in client.get(f"/api/assets/{library.shared}/people").json()] == [
        "Jane Doe"
    ]


def test_a_selection_where_NOTHING_can_be_reached_writes_nothing_and_says_so(
    client: TestClient, library: Library
) -> None:
    """The far edge of the test above.

    One reachable file and one made-up id is a partial success. EVERY id out of reach is not a
    partial anything: nothing is assigned, and the route must still answer 200 with a count rather
    than a 404: the reply's own job is to say what happened, and "nothing, and here is why" is an
    answer a screen can show.

    What it also has to not do is ask the search index to reindex an empty list. Every other test
    assigns at least one file, so the `if actionable.allowed` guard's fall-through is taken here
    and nowhere else. The people router has TWO of them, one per bulk route.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")

    response = assign(client, [NEVER_EXISTED], [person])

    assert response.status_code == 200
    assert response.json() == {
        "changed": 0,
        "skipped": 1,
        "reason": "Sift could not find the file.",
        "reason_many": "Sift could not find the files.",
        "vault_locked": False,
    }
    assert client.get(f"/api/assets/{library.shared}/people").json() == []


def test_one_unknown_person_fails_the_whole_call(client: TestClient, library: Library) -> None:
    sign_in(client)
    person = make_person(client, "Jane Doe")

    response = assign(client, [library.shared], [person, NEVER_EXISTED])

    assert response.status_code == 404
    assert client.get(f"/api/assets/{library.shared}/people").json() == []


def test_who_is_in_an_asset_comes_back_in_a_stable_order(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    zoe = make_person(client, "Zoe")
    alex = make_person(client, "Alex")
    assign(client, [library.shared], [zoe, alex])

    listed = client.get(f"/api/assets/{library.shared}/people").json()

    assert [person["name"] for person in listed] == ["Alex", "Zoe"]


def test_a_pass_that_named_somebody_says_which_pass_and_names_it(
    client: TestClient, library: Library
) -> None:
    """The word and the NAME, for each of the three passes that put a name on a file.

    The word alone is not an answer on the install this feature exists for: "a stash-box" on a
    library with three of them configured sends somebody through Settings to work out which, and "a
    username" on a file filed under four of them names none of them. Each of the three gets its name
    from a different place
    (the box from the file's applied match, the username from the pairing, and the folder from a
    fixed phrase because the row does not record which folder), which is exactly why they are
    worth asserting together rather than one at a time.
    """
    sign_in(client)
    by_box = make_person(client, "Recognised Rosalind")
    by_folder = make_person(client, "Filed Fenella")
    by_username = make_person(client, "Resolved Rhona")
    assign(client, [library.shared], [by_box, by_folder, by_username])

    username = _seed_like_a_download(
        db_path(client), site="examplesite", name="rhona", asset_id=library.shared
    )
    marked = "UPDATE asset_people SET source = ? WHERE asset_id = ? AND person_id = ?"
    statements: list[tuple[str, tuple[object, ...]]] = [
        ("UPDATE usernames SET person_id = ? WHERE id = ?", (by_username, username)),
        (
            "INSERT INTO stash_boxes (id, name, endpoint, created_at)"
            " VALUES ('box', 'PMVStash', 'https://example.invalid/box', 0)",
            (),
        ),
        (
            "INSERT INTO asset_stash_box_matches"
            " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
            " VALUES (?, 'box', 'remote', '[]', 'certain', 'applied', 0, 1)",
            (library.shared,),
        ),
        (marked, ("stash_box", library.shared, by_box)),
        (marked, ("folder", library.shared, by_folder)),
        (marked, ("username", library.shared, by_username)),
    ]
    write(db_path(client), statements)

    listed = client.get(f"/api/assets/{library.shared}/people").json()

    assert {row["name"]: (row["source"], row["source_name"]) for row in listed} == {
        "Recognised Rosalind": ("stash_box", "PMVStash"),
        "Filed Fenella": ("folder", "its folder"),
        "Resolved Rhona": ("username", "rhona"),
    }
    # The flag says the same thing for all three.
    assert all(row["automatic"] for row in listed)


def test_a_name_somebody_put_there_by_hand_is_named_by_nobody(
    client: TestClient, library: Library
) -> None:
    """No word and no name. A person did it, and there is nothing to attribute.

    Worth its own test rather than a line in the one above: the name is read from the file's
    applied matches, which are a fact about the FILE, so a file a box recognised must not put
    that box's name against a person somebody attached by hand.
    """
    sign_in(client)
    by_hand = make_person(client, "Chosen Clemency")
    assign(client, [library.shared], [by_hand])
    recognised: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO stash_boxes (id, name, endpoint, created_at)"
            " VALUES ('box', 'StashDB', 'https://example.invalid/box', 0)",
            (),
        ),
        (
            "INSERT INTO asset_stash_box_matches"
            " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
            " VALUES (?, 'box', 'remote', '[]', 'certain', 'applied', 0, 1)",
            (library.shared,),
        ),
    ]
    write(db_path(client), recognised)

    listed = client.get(f"/api/assets/{library.shared}/people").json()

    assert [(row["automatic"], row["source"], row["source_name"]) for row in listed] == [
        (False, None, None)
    ]


def test_a_pass_this_version_has_never_heard_of_travels_without_a_name(
    client: TestClient, library: Library
) -> None:
    """A word from a newer build, read by an older one: the word still goes, the name does not.

    The column is free text and a later version may write a pass into it that this one has no
    reading for. Kept rather than dropped, because the flag is still true (something automatic
    put the name there and a screen must still say so), and left unnamed, because inventing a
    phrase for a pass nothing here knows about would be a guess presented as attribution.
    """
    sign_in(client)
    somebody = make_person(client, "Inferred Isolde")
    assign(client, [library.shared], [somebody])
    write(
        db_path(client),
        [
            (
                "UPDATE asset_people SET source = ? WHERE asset_id = ? AND person_id = ?",
                ("a_pass_from_the_future", library.shared, somebody),
            )
        ],
    )

    listed = client.get(f"/api/assets/{library.shared}/people").json()

    assert [(row["automatic"], row["source"], row["source_name"]) for row in listed] == [
        (True, "a_pass_from_the_future", None)
    ]


def test_a_download_seeds_its_site_with_no_user_action(
    client: TestClient, library: Library
) -> None:
    """The payoff. Somebody drops a link and gets attribution for nothing typed.

    The username is recorded against the file and is searchable; it is not a row anybody
    manages, so what shows up on a screen is the site (and, once the download has matched the
    username to somebody, the person).
    """
    sign_in(client)

    _seed_like_a_download(db_path(client), site="TikTok", name="@creator", asset_id=library.shared)

    sites = client.get("/api/sites").json()["items"]

    assert [one["name"] for one in sites] == ["TikTok"]


def test_a_second_download_from_one_username_does_not_duplicate_it(
    client: TestClient, library: Library
) -> None:
    """Attribution accrues rather than piling up.

    Two files from the same username is one username with two files, not two usernames that
    both claim to be the same name and split the library between them.
    """
    sign_in(client)

    first = _seed_like_a_download(
        db_path(client), site="TikTok", name="creator", asset_id=library.shared
    )
    second = _seed_like_a_download(
        db_path(client), site="TikTok", name="creator", asset_id=library.private
    )

    assert first == second
    assert len(client.get("/api/sites").json()["items"]) == 1
    assert len(client.get("/api/usernames").json()["items"]) == 1

    links = read(db_path(client), "SELECT * FROM asset_usernames WHERE username_id = ?", (first,))

    assert len(links) == 2, "one username, two files"


def test_a_download_and_the_screen_write_the_same_username(
    client: TestClient, library: Library
) -> None:
    """One write path, so a username typed in and a username seen by an extractor are one row.

    Two insert paths into this table is how you end up with two usernames that are the same
    username, and nothing on screen that explains why the media is split across them.
    """
    sign_in(client)

    typed = make_username(client, "TikTok", "creator")
    seeded = _seed_like_a_download(
        db_path(client), site="TikTok", name="creator", asset_id=library.shared
    )

    assert typed == seeded


def _stamp_of(client: TestClient, user_id: str) -> int:
    """How many times what this user may see has changed. Read straight from the row, because
    nothing puts it in a response: it reaches a screen only inside a picture's address."""
    rows = read(db_path(client), "SELECT cache_stamp FROM users WHERE id = ?", (user_id,))
    assert rows, "no such username"
    return int(str(rows[0]["cache_stamp"]))


def test_detaching_somebody_reaches_the_guest_that_person_was_shared_with(
    client: TestClient, library: Library
) -> None:
    """A person is what a share is attached to, so taking them off a file revokes access to it.

    Nothing belonging to the guest is written when it happens (the row that goes is the join),
    so unless the detach raises their number, every picture address they already hold goes on
    working out of their browser's own store, with no request and therefore no check.
    """
    guest = sign_in(client, "guest")
    admin = sign_in(client)
    person_id = make_person(client, "Alex")
    assert assign(client, [library.shared], [person_id]).status_code == 200
    grant_on(client, "person", person_id, guest)
    before_guest = _stamp_of(client, guest)
    before_admin = _stamp_of(client, admin)

    assert assign(client, [library.shared], [person_id], add=False).status_code == 200

    assert _stamp_of(client, guest) > before_guest, (
        "the guest's picture addresses still work after the person who reached them was detached"
    )
    assert _stamp_of(client, admin) == before_admin, (
        "the admin's whole grid was thrown away for a change to somebody else's access"
    )


def test_attaching_somebody_already_attached_costs_nobody_anything(
    client: TestClient, library: Library
) -> None:
    """Nothing changed, so no address needs to move. Dragging a clip onto a person it already
    names must not empty the grid of everyone that person reaches."""
    guest = sign_in(client, "guest")
    sign_in(client)
    person_id = make_person(client, "Alex")
    assert assign(client, [library.shared], [person_id]).status_code == 200
    grant_on(client, "person", person_id, guest)
    before = _stamp_of(client, guest)

    assert assign(client, [library.shared], [person_id]).status_code == 200

    assert _stamp_of(client, guest) == before, "a repeated attach invalidated everybody's pictures"


def test_taking_a_person_off_is_remembered(client: TestClient, library: Library) -> None:
    """Unassigning writes down that it happened, and re-assigning forgets it.

    Deleting the join row says what is true now and nothing about what should happen next. A folder
    read as somebody's is re-read whenever it grows, and without this record a name taken off by
    hand is put straight back by the next pass, silently, on a library nobody audits.

    Written whoever made the attribution. Limiting it to the ones a pass wrote would leave the case
    open: a name put on by hand and taken off again is put back by the same rung.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")
    assert assign(client, [library.shared], [person]).status_code == 200

    assert assign(client, [library.shared], [person], add=False).status_code == 200
    refusals = read(
        db_path(client),
        "SELECT asset_id, person_id FROM asset_person_refusals",
    )
    assert refusals == [{"asset_id": library.shared, "person_id": person}]

    # Put back by hand: the refusal goes, or it would block a pass from agreeing with a decision
    # somebody has already made.
    assert assign(client, [library.shared], [person]).status_code == 200
    assert read(db_path(client), "SELECT asset_id FROM asset_person_refusals") == []


# --- filing files under a site ------------------------------------------------------------------


def _file_under(client: TestClient, asset_ids: list[str], site_ids: list[str]):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/assets/sites",
        json={"asset_ids": asset_ids, "site_ids": site_ids},
    )


def _site_id(client: TestClient, name: str) -> str:
    """Make a site by making a username on it, which is the only way one comes into existence."""
    make_username(client, site=name, name="somebody")
    rows = read(db_path(client), "SELECT id FROM sites WHERE name = ?", (name,))
    return str(rows[0]["id"])


def test_filing_a_selection_where_NOTHING_can_be_reached_writes_nothing(
    client: TestClient, library: Library
) -> None:
    """The same far edge on the other bulk route. See the people one above for why it matters."""
    sign_in(client)
    site = _site_id(client, "Northlight")

    answer = _file_under(client, [NEVER_EXISTED], [site])

    assert answer.status_code == 200
    assert answer.json()["changed"] == 0
    assert answer.json()["skipped"] == 1


def test_filing_a_selection_under_a_site_moves_no_file(
    client: TestClient, library: Library
) -> None:
    """The same promise assigning a person makes, and it is the one worth asserting: a join table
    is written and every path, byte and location row is exactly as it was."""
    sign_in(client)
    before = library.path_of("shared").read_bytes()
    locations = read(db_path(client), "SELECT * FROM asset_locations ORDER BY id")

    site = _site_id(client, "Northlight")
    answer = _file_under(client, [library.shared, library.private], [site])

    assert answer.status_code == 200, answer.text
    assert answer.json()["changed"] == 2
    assert library.path_of("shared").read_bytes() == before
    assert read(db_path(client), "SELECT * FROM asset_locations ORDER BY id") == locations


def test_a_file_filed_under_a_site_is_found_through_it(
    client: TestClient, library: Library
) -> None:
    """The whole point of writing a username row rather than only the site: every question about a
    site reaches a file through `asset_usernames`, so a site recorded any other way leaves the file
    findable by nothing."""
    sign_in(client)
    site = _site_id(client, "Northlight")

    _file_under(client, [library.shared], [site])

    rows = read(
        db_path(client),
        "SELECT au.asset_id FROM asset_usernames au"
        " JOIN usernames u ON u.id = au.username_id"
        " WHERE u.site_id = ? AND u.name = ''",
        (site,),
    )
    assert [row["asset_id"] for row in rows] == [library.shared]


def test_filing_the_same_files_twice_writes_one_row(client: TestClient, library: Library) -> None:
    """Pressing it again is somebody making sure, not somebody asking for a second row."""
    sign_in(client)
    site = _site_id(client, "Northlight")

    _file_under(client, [library.shared], [site])
    _file_under(client, [library.shared], [site])

    rows = read(
        db_path(client),
        "SELECT au.asset_id FROM asset_usernames au"
        " JOIN usernames u ON u.id = au.username_id"
        " WHERE u.site_id = ? AND u.name = ''",
        (site,),
    )
    assert len(rows) == 1


def test_a_site_that_does_not_exist_files_nothing_at_all(
    client: TestClient, library: Library
) -> None:
    """Resolved first, all of them, and one that cannot be fails the whole call. A partial success
    that does not say which part succeeded is worse than a refusal."""
    sign_in(client)
    site = _site_id(client, "Northlight")

    answer = _file_under(client, [library.shared], [site, NEVER_EXISTED])

    assert answer.status_code == 404
    assert read(db_path(client), "SELECT asset_id FROM asset_usernames") == []


def test_a_file_that_does_not_exist_is_skipped_and_the_rest_are_filed(
    client: TestClient, library: Library
) -> None:
    """The FILES are skipped one by one; the SITES above are still all-or-nothing.

    The two halves differ, and they should. A site id that resolves to nothing is a broken caller and
    there is no useful half of "file these under this site" when the site does not exist. A file
    that cannot be reached is the ordinary case this skipping exists for, and the rest of the
    selection has no reason to suffer for it.
    """
    sign_in(client)
    site = _site_id(client, "Northlight")

    answer = _file_under(client, [library.shared, NEVER_EXISTED], [site])

    assert answer.status_code == 200
    assert answer.json()["skipped"] == 1
    assert [
        row["asset_id"] for row in read(db_path(client), "SELECT asset_id FROM asset_usernames")
    ] == [library.shared]


def test_the_count_is_files_rather_than_pairs(client: TestClient, library: Library) -> None:
    """Filing two files under two sites is two files. A number that counted the pairs would report
    four of something nobody selected."""
    sign_in(client)
    one = _site_id(client, "Northlight")
    two = _site_id(client, "Mirrorbeam")

    answer = _file_under(client, [library.shared, library.private], [one, two])

    assert answer.json()["changed"] == 2


async def _maker_of_site(database: Database, name: str) -> tuple[str, str | None, str | None]:
    row = await database.fetch_one(
        "SELECT created_by_kind, created_by_via, created_by_user_id FROM sites WHERE name = ?",
        (name,),
    )
    assert row is not None, f"no Site called {name}"
    return (row["created_by_kind"], row["created_by_via"], row["created_by_user_id"])


async def test_a_site_a_filing_invents_names_whoever_filed(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The user who filed is the new Site's maker, and a pass of Sift's is named by its word."""
    service = PeopleService(temp_db, access)
    user_id = actors.admin.id

    await service.file_under_sites([world.solo], ["Northlight Group"], actor=Actor.user(user_id))
    await service.file_under_sites([world.solo], ["Quiet Studio"], actor=Actor.sift("folder"))

    assert await _maker_of_site(temp_db, "Northlight Group") == ("user", None, user_id)
    assert await _maker_of_site(temp_db, "Quiet Studio") == ("sift", "folder", None)


async def test_filing_nothing_under_nothing_opens_no_write(
    temp_db: Database, access: Repository
) -> None:
    """Nought files, no write opened, and no library change announced to anybody watching.

    Asked of the SERVICE rather than of the route, and that is the point of it: the request model
    refuses an empty list at the boundary, so nothing arriving over HTTP can reach this. What it
    guards is the next caller INSIDE the slice, and `telling(...)` announces a library change to
    every admin on the way in, so an unguarded empty call would wake every open screen to report
    that nothing had happened.
    """
    service = PeopleService(temp_db, access)

    assert await service.file_under_sites([], ["Northlight"], actor=Actor.sift("folder")) == 0
    assert (
        await service.file_under_sites(
            ["01HX0000000000000000000001"], [], actor=Actor.sift("folder")
        )
        == 0
    )

    # The other half of the pair, and the same guard for the same reason: the picker's tick can be
    # cleared as well as set, so unfiling is reachable with an empty list from exactly the callers
    # filing is.
    assert (
        await service.unfile_from_sites(
            [], ["01HX0000000000000000000002"], actor=Actor.sift("folder")
        )
        == 0
    )
    assert (
        await service.unfile_from_sites(
            ["01HX0000000000000000000001"], [], actor=Actor.sift("folder")
        )
        == 0
    )
