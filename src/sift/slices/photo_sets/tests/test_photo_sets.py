# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making a photo set, filling it, sharing it and taking it away.

The claims worth breaking the build over: that grouping pictures moves nothing on disk, that a set
derived from a folder is a set of PICTURES in filename order, that deleting one takes its rows and
its grants and never its files, and that a set cannot be used to count what somebody was not shown.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Repository
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.photo_sets.service import PhotoSetService
from sift.slices.photo_sets.tests.conftest import (
    NEVER_EXISTED,
    STILLS,
    Shoot,
    contents,
    db_path,
    edit_items,
    grant_on_set,
    grants_naming,
    make_set,
    positions,
    read,
    set_ids,
    share,
    sign_in,
)
from sift.testing.auth import hide_for_caller
from sift.testing.library import a_png

pytestmark = [pytest.mark.integration]


# --- making one ------------------------------------------------------------------------------


def test_a_set_is_made_empty_and_turns_up_on_the_wall(client: TestClient) -> None:
    sign_in(client)
    set_id = make_set(client, "Beach shoot")

    listed = client.get("/api/photo-sets").json()["items"]
    assert [entry["id"] for entry in listed] == [set_id]
    assert listed[0]["name"] == "Beach shoot"
    assert listed[0]["item_count"] == 0
    assert listed[0]["cover_asset_id"] is None
    assert listed[0]["origin"] == "manual"


def test_a_guest_cannot_make_one(client: TestClient) -> None:
    """Making a set is an admin's, because a set is shared vocabulary: everybody who can see the
    pictures sees the grouping over them."""
    sign_in(client, "guest")
    assert client.post("/api/photo-sets", json={"name": "mine"}).status_code == 403


def test_an_id_that_was_never_minted_is_missing_rather_than_refused(client: TestClient) -> None:
    sign_in(client)
    assert client.get(f"/api/photo-sets/{NEVER_EXISTED}").status_code == 404


# --- filling one -----------------------------------------------------------------------------


def test_adding_pictures_moves_no_file(client: TestClient, shoot: Shoot) -> None:
    """The load-bearing promise. Every byte and every path is exactly where it was."""
    sign_in(client)
    before = {name: shoot.path_of(name).read_bytes() for name in STILLS}

    set_id = make_set(client, "Beach shoot")
    assert edit_items(client, set_id, shoot.pictures).status_code == 200

    for name in STILLS:
        assert shoot.path_of(name).exists()
        assert shoot.path_of(name).read_bytes() == before[name]
    assert contents(client, set_id) == shoot.pictures


def test_adding_the_same_picture_twice_changes_nothing_the_second_time(
    client: TestClient, shoot: Shoot
) -> None:
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    assert edit_items(client, set_id, [shoot.first]).json()["changed"] == 1
    assert edit_items(client, set_id, [shoot.first]).json()["changed"] == 0
    assert contents(client, set_id) == [shoot.first]


def test_taking_a_picture_out_leaves_the_file_alone(client: TestClient, shoot: Shoot) -> None:
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    edit_items(client, set_id, shoot.pictures)

    assert edit_items(client, set_id, [shoot.first], remove=True).json()["changed"] == 1
    assert contents(client, set_id) == [shoot.second]
    assert shoot.path_of(STILLS[0]).exists()


def test_a_picture_the_caller_cannot_see_is_skipped_and_the_rest_go_in(
    client: TestClient, shoot: Shoot
) -> None:
    """Resolved through the access layer first, so a set is never a way to name a file somebody was
    not shown, and the one it could not resolve is skipped rather than failing the whole call.

    What is asserted here is that the unresolvable id NEVER LANDS, while the picture beside it is
    not punished for it.
    """
    sign_in(client)
    set_id = make_set(client, "Beach shoot")

    answer = edit_items(client, set_id, [shoot.first, NEVER_EXISTED])

    assert answer.status_code == 200
    assert answer.json()["skipped"] == 1
    assert contents(client, set_id) == [shoot.first]


def test_a_selection_where_NOTHING_can_be_reached_writes_nothing_and_still_answers(
    client: TestClient, shoot: Shoot
) -> None:
    """The far edge of the test above: the fall-through of the `if wanted:` guarding the write.

    Every other case names at least one picture that resolves. Named only ids it cannot reach,
    the route must still answer 200 with a count: the reply exists to say what happened, and
    "nothing, and here is why" is something a screen can show. And it must not call the service at
    all, because an empty write is a transaction bought for nothing.
    """
    sign_in(client)
    set_id = make_set(client, "Beach shoot")

    answer = edit_items(client, set_id, [NEVER_EXISTED])

    assert answer.status_code == 200
    assert answer.json()["changed"] == 0
    assert answer.json()["skipped"] == 1
    assert contents(client, set_id) == []


def test_the_set_holds_its_own_order_and_the_wall_reads_it(
    client: TestClient, shoot: Shoot
) -> None:
    """A shoot was numbered, and showing it shuffled is showing something else. The order is stored
    as positions and comes back through the asset query's own arm: the newest-first order the grid
    would otherwise use is the opposite, because the fixture adds them in the other order."""
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    edit_items(client, set_id, [shoot.second, shoot.first])

    assert positions(client, set_id) == {shoot.second: 0, shoot.first: 1}
    assert contents(client, set_id) == [shoot.second, shoot.first]


# --- from a folder ---------------------------------------------------------------------------


def test_the_cover_has_to_be_something_in_the_set(client: TestClient, shoot: Shoot) -> None:
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    edit_items(client, set_id, [shoot.first])

    assert (
        client.put(f"/api/photo-sets/{set_id}/cover", json={"asset_id": shoot.first}).json()[
            "cover_asset_id"
        ]
        == shoot.first
    )


def test_notes_are_kept_and_can_be_cleared(client: TestClient) -> None:
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    assert (
        client.put(f"/api/photo-sets/{set_id}/notes", json={"notes": "Shot at dusk"}).json()[
            "notes"
        ]
        == "Shot at dusk"
    )
    assert (
        client.put(f"/api/photo-sets/{set_id}/notes", json={"notes": None}).json()["notes"] is None
    )


def test_the_heart_and_the_stars_belong_to_the_account_that_pressed_them(
    client: TestClient,
) -> None:
    """One row per user. What somebody else thinks of a shoot is not a fact about the shoot."""
    admin = sign_in(client)
    set_id = make_set(client, "Beach shoot")
    client.put(f"/api/photo-sets/{set_id}/favorite", json={"favorite": True})
    client.put(f"/api/photo-sets/{set_id}/rating", json={"rating": 4})
    assert client.get(f"/api/photo-sets/{set_id}").json()["favorite"] is True

    other = sign_in(client, "admin", who="two")
    assert other != admin
    assert client.get(f"/api/photo-sets/{set_id}").json()["favorite"] is False
    assert client.get(f"/api/photo-sets/{set_id}").json()["rating"] is None


# --- who may be told what ----------------------------------------------------------------------


def test_a_guest_is_not_told_about_a_set_holding_nothing_they_can_see(
    client: TestClient, shoot: Shoot
) -> None:
    """The count is the reason this matters. A set reporting "2 pictures" to somebody who may see
    none of them has published the size of the restricted set, which is the number the whole model
    is keeping back, so the row is absent rather than shown with a zero."""
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    edit_items(client, set_id, shoot.pictures)

    sign_in(client, "guest")
    assert set_ids(client) == []
    assert client.get(f"/api/photo-sets/{set_id}").status_code == 404


def test_sharing_a_set_reveals_what_is_in_it(client: TestClient, shoot: Shoot) -> None:
    """The point of sharing a subject rather than a folder: it reaches whatever is in it, and keeps
    reaching what arrives later, without anybody going back to it."""
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    edit_items(client, set_id, shoot.pictures)

    guest = sign_in(client, "guest")
    assert set_ids(client) == []

    sign_in(client)
    grant_on_set(client, set_id, guest)

    sign_in(client, "guest")
    assert set_ids(client) == [set_id]
    assert sorted(contents(client, set_id)) == sorted(shoot.pictures)


def test_a_guest_shown_one_picture_is_told_that_count_and_no_other(
    client: TestClient, shoot: Shoot
) -> None:
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    edit_items(client, set_id, shoot.pictures)

    guest = sign_in(client, "guest")
    sign_in(client)
    share(client, shoot.first, guest)

    sign_in(client, "guest")
    listed = client.get("/api/photo-sets").json()["items"]
    assert [entry["item_count"] for entry in listed] == [1]
    assert contents(client, set_id) == [shoot.first]


# --- taking one away ---------------------------------------------------------------------------


def test_deleting_a_set_keeps_every_file_and_forgets_every_grant(
    client: TestClient, shoot: Shoot
) -> None:
    """A grant left behind names an object that no longer exists, and the next id minted could
    collide with it, which is a share nobody made."""
    admin = sign_in(client)
    set_id = make_set(client, "Beach shoot")
    edit_items(client, set_id, shoot.pictures)
    grant_on_set(client, set_id, admin)
    assert grants_naming(client, set_id)

    assert client.delete(f"/api/photo-sets/{set_id}").status_code == 204
    assert set_ids(client) == []
    assert grants_naming(client, set_id) == []
    for name in STILLS:
        assert shoot.path_of(name).exists()


def test_a_guest_cannot_delete_one(client: TestClient) -> None:
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    sign_in(client, "guest")
    assert client.delete(f"/api/photo-sets/{set_id}").status_code in (403, 404)


# --- the rest of the surface, so the gate measures a whole slice ---------------------------------


def test_an_unknown_order_is_refused_rather_than_quietly_ignored(client: TestClient) -> None:
    """A caller who asked for an order and silently got another has a wall that looks wrong for no
    visible reason."""
    sign_in(client)
    assert client.get("/api/photo-sets", params={"sort": "sideways"}).status_code == 422


def test_the_wall_can_be_put_in_every_order_it_offers(client: TestClient, shoot: Shoot) -> None:
    """Every word the bar above this wall can send, actually sent. An order the server refuses is an
    empty screen with the reason in a response nobody reads."""
    sign_in(client)
    make_set(client, "Alpha")
    edit_items(client, make_set(client, "Beta"), shoot.pictures)
    for order in (
        "newest",
        "oldest",
        "name_az",
        "name_za",
        "largest",
        "smallest",
        "favorite",
        "rating",
        "seen",
    ):
        answer = client.get("/api/photo-sets", params={"sort": order})
        assert answer.status_code == 200, f"{order}: {answer.text}"
        assert len(answer.json()["items"]) == 2


def test_the_size_orders_really_order_a_wall_of_sets(client: TestClient, shoot: Shoot) -> None:
    """Accepted is not the same as obeyed.

    Every arm of the order is a `CASE` on a bound word, so a missing arm does not fail: it matches
    no branch and the wall falls through to whatever the query ends on, which here is the set's
    NAME. On three of the entity walls the fall-through IS the size arm, which is why those are not
    asked this question.

    THREE sets, and the names are chosen so that alphabetical order matches NEITHER size order. With
    two, name order always agrees with one of them, so that arm could be deleted and nothing would
    move, which is the "a fixture whose two sources agree" trap.
    """
    sign_in(client)
    middling = make_set(client, "a middling one")
    empty = make_set(client, "b nothing in it")
    full = make_set(client, "c every picture")
    edit_items(client, middling, shoot.pictures[:1])
    edit_items(client, full, shoot.pictures)
    assert len(shoot.pictures) > 1, "the fixture needs a set that is bigger than the middling one"

    def wall(order: str) -> list[str]:
        return [
            row["id"]
            for row in client.get("/api/photo-sets", params={"sort": order}).json()["items"]
        ]

    # What a missing arm would leave: by name, which is neither of the two below.
    assert wall("name_az") == [middling, empty, full]

    assert wall("largest") == [full, middling, empty]
    assert wall("smallest") == [empty, middling, full]


def test_renaming_a_set_changes_what_the_wall_says(client: TestClient) -> None:
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    assert client.put(f"/api/photo-sets/{set_id}", json={"name": "Dusk shoot"}).json()["name"] == (
        "Dusk shoot"
    )
    assert client.get(f"/api/photo-sets/{set_id}").json()["name"] == "Dusk shoot"


def test_the_box_above_the_wall_matches_anywhere_in_a_name(client: TestClient) -> None:
    """The wall's box sends `anywhere`, as the People wall's does: part of a name finds it."""
    sign_in(client)
    make_set(client, "Beach shoot")
    make_set(client, "Dusk shoot")
    make_set(client, "Harbour")

    def names(**params: str) -> list[str]:
        answer = client.get("/api/photo-sets", params={"sort": "name_az", **params})
        assert answer.status_code == 200, answer.text
        return [one["name"] for one in answer.json()["items"]]

    assert names(prefix="shoot") == []
    assert names(prefix="shoot", anywhere="true") == ["Beach shoot", "Dusk shoot"]


def test_a_set_in_the_vault_is_gone_from_the_wall_until_it_comes_back(
    client: TestClient, shoot: Shoot
) -> None:
    """Hiding is one user's business and reaches nobody else's screen, which is why it is not an
    admin's, unlike everything around it."""
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    edit_items(client, set_id, shoot.pictures)

    # Straight into the row the resolver reads. Hiding through the route needs the vault unlocked on
    # this session, and what is being asked here is what concealment DOES, not what guards it: the
    # PIN is tested where it is enforced.
    assert client.put(f"/api/photo-sets/{set_id}/vault", json={"vault": True}).status_code == 204
    assert set_ids(client) == []
    assert client.get(f"/api/photo-sets/{set_id}").status_code == 404

    # Sealed while the vault is shut, like every other kind of thing: a 204 where a 404 belongs
    # would tell anybody holding the id that the set is there, which is what concealing it was for.
    assert client.put(f"/api/photo-sets/{set_id}/vault", json={"vault": False}).status_code == 404

    # Taken back out from behind the concealment, which is what an unlock does.
    hide_for_caller(client, "photo_set", set_id, hidden=False)
    assert set_ids(client) == [set_id]


def test_a_tag_goes_on_a_set_and_comes_off_again(client: TestClient) -> None:
    """The same tags a file, a person and a site carry, on the shoot rather than on each picture."""
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    tag_id = client.post("/api/tags", json={"name": "dusk"}).json()["id"]

    assert client.get(f"/api/photo-sets/{set_id}/tags").json() == []
    on = client.post(f"/api/photo-sets/{set_id}/tags", json={"tag_id": tag_id})
    assert [tag["id"] for tag in on.json()] == [tag_id]

    off = client.post(f"/api/photo-sets/{set_id}/tags", json={"tag_id": tag_id, "add": False})
    assert off.json() == []


def test_a_guest_cannot_tag_a_set(client: TestClient) -> None:
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    tag_id = client.post("/api/tags", json={"name": "dusk"}).json()["id"]
    sign_in(client, "guest")
    assert client.post(f"/api/photo-sets/{set_id}/tags", json={"tag_id": tag_id}).status_code == 403


def test_taking_out_something_that_was_never_in_changes_nothing(
    client: TestClient, shoot: Shoot
) -> None:
    """Zero removed, and no revocation stamp moved. A stamp bumped for a write that did nothing
    would empty every cached picture for everybody the set reaches, for no reason."""
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    edit_items(client, set_id, [shoot.first])
    assert edit_items(client, set_id, [shoot.second], remove=True).json()["changed"] == 0
    assert contents(client, set_id) == [shoot.first]


def test_a_moment_of_a_clip_can_be_the_cover(client: TestClient, shoot: Shoot) -> None:
    """A set can hold a clip, so its cover can name a moment of one. The still is queued rather
    than rendered on the request; what a request can answer is that the choice was written."""
    sign_in(client)
    set_id = make_set(client, "Shoot")
    edit_items(client, set_id, [shoot.clip])

    written = client.put(
        f"/api/photo-sets/{set_id}/cover", json={"asset_id": shoot.clip, "at_ms": 4200}
    )

    assert written.status_code == 200, written.text
    assert written.json()["cover_asset_id"] == shoot.clip


def test_a_picture_from_outside_the_library_can_be_uploaded_and_read_back(
    client: TestClient,
) -> None:
    """A picture of the SET rather than one of its pictures. In as a PNG, out as Sift's own JPEG."""
    sign_in(client)
    set_id = make_set(client, "Shoot")

    sent = client.post(
        f"/api/photo-sets/{set_id}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )

    assert sent.status_code == 200, sent.text
    assert sent.json()["cover_upload_id"] is not None
    assert sent.json()["cover_asset_id"] is None

    served = client.get(f"/api/photo-sets/{set_id}/cover")

    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.content.startswith(b"\xff\xd8\xff"), "what is served is a JPEG Sift wrote"


def test_bytes_that_are_not_a_picture_are_refused(client: TestClient) -> None:
    """The one route here where a stranger chooses the bytes; ffmpeg is what reads them."""
    sign_in(client)
    set_id = make_set(client, "Shoot")

    sent = client.post(
        f"/api/photo-sets/{set_id}/cover-picture",
        files={"file": ("chosen.png", b"this is a sentence, not a picture", "image/png")},
    )

    assert sent.status_code == 400


def test_a_set_with_no_cover_serves_a_404(client: TestClient) -> None:
    """The same 404 as a set that is not there, and as a cover the asker may not open."""
    sign_in(client)
    set_id = make_set(client, "Shoot")

    assert client.get(f"/api/photo-sets/{set_id}/cover").status_code == 404
    assert client.get(f"/api/photo-sets/{NEVER_EXISTED}/cover").status_code == 404


def test_uploading_onto_a_set_that_is_not_there_is_a_404(client: TestClient) -> None:
    """Checked BEFORE the bytes are read, so naming a set that does not exist cannot make Sift run
    ffmpeg."""
    sign_in(client)

    sent = client.post(
        f"/api/photo-sets/{NEVER_EXISTED}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )

    assert sent.status_code == 404


# --- the pin ------------------------------------------------------------------------------------


def test_a_pinned_set_comes_first_whatever_the_wall_is_sorted_by(client: TestClient) -> None:
    """The whole claim a pin makes, and it is about EVERY order rather than one more of them: an
    arm written under `CASE :entity_sort` would be a pin that quietly stopped working the moment
    anybody changed the sort, with nothing on screen to say why the row moved.

    `Zoetrope` is last alphabetically and last by creation, so a pin on it moves it to the front of
    two orders that disagree about where it belongs.
    """
    sign_in(client)
    make_set(client, "Ada")
    last = make_set(client, "Zoetrope")

    answer = client.put(f"/api/photo-sets/{last}/pin", json={"pinned": True})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"pinned": True}

    def wall(sort: str) -> list[str]:
        listed = client.get("/api/photo-sets", params={"sort": sort, "limit": 50})
        assert listed.status_code == 200, listed.text
        return [one["name"] for one in listed.json()["items"]]

    assert wall("name_az")[0] == "Zoetrope"
    assert wall("newest")[0] == "Zoetrope"

    assert client.put(f"/api/photo-sets/{last}/pin", json={"pinned": False}).json() == {
        "pinned": False
    }
    assert wall("name_az") == ["Ada", "Zoetrope"]


def test_a_pin_is_refused_for_a_set_that_is_not_there(client: TestClient) -> None:
    """The set is resolved BEFORE the write, and the write cannot fail: the state row is this
    user's own and would be created happily against an id that names nothing: a row nothing
    will ever clean up, because the cascade has nothing to cascade from."""
    sign_in(client)

    assert (
        client.put(f"/api/photo-sets/{NEVER_EXISTED}/pin", json={"pinned": True}).status_code == 404
    )


def test_a_pin_does_not_float_a_file_out_of_the_sequence_it_is_in(
    client: TestClient, shoot: Shoot
) -> None:
    """A SHOOT WAS NUMBERED, and a private opinion must not renumber it.

    The `ELSE 0` in the pin's ORDER BY arm is what stops a wall that does not curate floating the
    pin. A plain Browse wall cannot test it: `arranged` leaves all three arrangement arms OUT when
    nothing is arranged, because a sort key that is an expression cannot be answered from an index.

    A photo set is the case the `ELSE 0` genuinely holds: the wall IS arranged (by the set's own
    positions) and it does NOT curate, so `pinned_first` is false while the arm is present. That
    is the only shape in which the else branch is reached and can be wrong.
    """
    sign_in(client)
    set_id = make_set(client, "Shoot")
    ordered = [shoot.first, shoot.second, shoot.clip]
    edit_items(client, set_id, ordered)
    assert contents(client, set_id) == ordered, "the set did not start in the order it was given"

    # The LAST one, so floating it would be unmissable.
    assert client.put(f"/api/assets/{shoot.clip}/pin", json={"pinned": True}).status_code == 200

    assert contents(client, set_id) == ordered, (
        "a pin floated a file out of the sequence somebody numbered"
    )

    # THE KNOWN POSITIVE, and without it the assertion above passes against a pin that never wrote
    # anything: the same file, on a wall that DOES curate, is floated.
    curated = client.get("/api/assets", params={"limit": 50, "pinned_first": True})
    assert curated.status_code == 200, curated.text
    assert next(one["id"] for one in curated.json()["items"]) == shoot.clip


def test_a_prefix_narrows_the_wall_of_sets(client: TestClient) -> None:
    """A picker draws a PAGE of this wall, so the narrowing belongs here: filtering the page in the
    browser can only rearrange the rows in hand, which is a silent ceiling the moment there are more
    sets than one page holds. Matched without regard to case, and a wildcard typed into the box is
    an ordinary character rather than a pattern."""
    sign_in(client)
    make_set(client, "Alpha")
    make_set(client, "Beta")

    def names(prefix: str) -> list[str]:
        answer = client.get(
            "/api/photo-sets", params={"sort": "name_az", "limit": 50, "prefix": prefix}
        )
        assert answer.status_code == 200, answer.text
        return [one["name"] for one in answer.json()["items"]]

    assert names("") == ["Alpha", "Beta"]
    assert names("al") == ["Alpha"]
    assert names("_") == []


# --- opening the wall where it was left ------------------------------------------------------


def test_the_photo_sets_wall_can_be_opened_where_it_was_left(client: TestClient) -> None:
    """`from=` names a set to start the page at, instead of an offset.

    The wall pages by whole rows, so how many cards fit depends on the size of the screen, which
    makes a page NUMBER useless in a link and the set somebody was looking at the durable thing."""
    sign_in(client)
    for name in ("Alpha", "Beta", "Gamma", "Delta"):
        make_set(client, name)

    every = client.get("/api/photo-sets", params={"sort": "name_az", "limit": 50}).json()
    ordered = [one["id"] for one in every["items"]]
    assert len(ordered) == 4

    landed = client.get(
        "/api/photo-sets", params={"sort": "name_az", "limit": 2, "from": ordered[2]}
    ).json()

    assert [one["id"] for one in landed["items"]] == ordered[2:4]
    assert landed["offset"] == 2, "the answer says where it landed, so the pager can say so too"
    assert landed["total"] == every["total"], "anchoring narrows nothing"


def test_a_sets_position_is_read_in_the_order_the_page_is_taken_in(client: TestClient) -> None:
    """The position is cut out of the wall's own statement, so the two cannot hold different
    orderings. A position read one way and a page taken another lands somebody NEAR where the link
    pointed, which reads as imprecision rather than as a bug."""
    sign_in(client)
    for name in ("Alpha", "Beta", "Gamma", "Delta"):
        make_set(client, name)

    backwards = client.get("/api/photo-sets", params={"sort": "name_za", "limit": 50}).json()
    ordered = [one["id"] for one in backwards["items"]]

    landed = client.get(
        "/api/photo-sets", params={"sort": "name_za", "limit": 2, "from": ordered[2]}
    ).json()

    assert landed["offset"] == 2, "the position was read in the reversed order, not the default"
    assert [one["id"] for one in landed["items"]] == ordered[2:4]


def test_an_anchor_that_resolves_to_nothing_serves_the_first_page(client: TestClient) -> None:
    """A stale link is not an error, and a made-up id learns nothing.

    A set that has since been renamed, hidden or deleted is still a link to this wall, so the wall
    is what it opens. It is also why an id nobody minted answers the same page a concealed one
    does: trying ids can never tell the two apart.
    """
    sign_in(client)
    for name in ("Alpha", "Beta"):
        make_set(client, name)

    first = client.get("/api/photo-sets", params={"sort": "name_az", "limit": 50}).json()
    stale = client.get(
        "/api/photo-sets", params={"sort": "name_az", "limit": 50, "from": NEVER_EXISTED}
    ).json()

    assert stale["offset"] == 0
    assert [one["id"] for one in stale["items"]] == [one["id"] for one in first["items"]]


def test_a_photo_set_listing_names_an_uploaded_cover_so_it_is_kept(client: TestClient) -> None:
    """The wall hands out what the address needs to name this upload, so the picture is KEPT.

    The server makes the week-long promise only to an address carrying the user's token and the
    upload id (`kernel/covers.py names_its_cover`); a wall row that carried no token would leave
    every uploaded cover re-checked on every visit. Built from the wire exactly as `coverToken` in
    `lib/entity/art.ts` builds it.
    """
    sign_in(client)
    thing = make_set(client, "Shoot")
    sent = client.post(
        f"/api/photo-sets/{thing}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )
    assert sent.status_code == 200, sent.text

    listed = client.get("/api/photo-sets")
    assert listed.status_code == 200, listed.text
    row = next(one for one in listed.json()["items"] if one["id"] == thing)
    assert row["art"], "the wall row carries the account's token"
    served = client.get(
        f"/api/photo-sets/{thing}/cover", params={"v": f"{row['art']}.{row['cover_upload_id']}"}
    )

    assert served.status_code == 200
    assert "immutable" in served.headers["cache-control"]


def test_renaming_a_set_to_the_name_it_has_records_nothing(client: TestClient) -> None:
    """A save that changed nothing is not an act somebody's History should carry."""
    sign_in(client)
    set_id = make_set(client, "Beach shoot")

    assert client.put(f"/api/photo-sets/{set_id}", json={"name": "Beach shoot"}).status_code == 200
    assert read(db_path(client), "SELECT id FROM workbench_decisions WHERE verb = 'renamed'") == []


def test_a_set_gone_before_the_write_is_answered_as_missing_and_records_nothing(
    client: TestClient,
) -> None:
    """The route resolves the set first, but it can go between that read and the write. Each
    writer then answers None (or, for a delete, nothing at all) and writes no line about a
    grouping that is not there."""
    settings = client.app.state.settings  # type: ignore[attr-defined]
    actor = Actor.user("someone")
    written_before = read(db_path(client), "SELECT id FROM workbench_decisions", ())

    async def go() -> list[object]:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            content = ContentStore(database, settings)
            service = PhotoSetService(database, Repository(database, content))
            answers: list[object] = [
                await service.rename(NEVER_EXISTED, "Dusk", actor=actor),
                await service.set_notes(NEVER_EXISTED, "a note", actor=actor),
                await service.set_cover(NEVER_EXISTED, None, actor=actor),
            ]
            await service.delete(NEVER_EXISTED, actor=actor)
            return answers
        finally:
            await database.close()

    assert asyncio.run(go()) == [None, None, None]
    assert read(db_path(client), "SELECT id FROM workbench_decisions", ()) == written_before
