# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hearting, rating and tagging a person or a site.

The same three gestures the grid already puts on a tile, on the two things a library is organised
by. Most of what is worth testing here is not the writing (an upsert is an upsert) but the
three lines that separate these from the asset's:

  - a rating is **this viewer's**, so two users on one install do not overwrite each other;
  - a heart is **anybody's** and a tag is **admin-only**, because one is a private opinion and the
    other is shared vocabulary that changes what everyone's searches return;
  - a person nobody may be shown answers **404 to a write**, not 403, for the reason every other
    miss in this application does: a 403 confirms that the person exists.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    Library,
    grant_on,
    make_person,
    sign_in,
)


def _site(client: TestClient, name: str) -> str:
    response = client.post("/api/sites", json={"name": name, "kind": None})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _tag(client: TestClient, name: str) -> str:
    response = client.post("/api/tags", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _person_row(client: TestClient, person_id: str) -> dict[str, object]:
    listed = client.get("/api/people").json()["items"]
    return next(row for row in listed if row["id"] == person_id)


def _give_a_face_cover(client: TestClient, person_id: str, asset_id: str) -> None:
    """The state naming a face leaves behind: a cover that is a face out of a particular file.

    Written directly because getting here through the face feature means a model, a real video and
    a pass over it, none of which is what this is about.
    """
    from sift.slices.people.tests.conftest import db_path, write

    write(
        db_path(client),
        [
            (
                "UPDATE people SET cover_asset_id = ?, cover_track_id = 'a-face' WHERE id = ?",
                (asset_id, person_id),
            )
        ],
    )


# --- the heart and the stars ------------------------------------------------------------------


def test_a_person_can_be_hearted_and_the_list_says_so(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane")

    answer = client.put(f"/api/people/{person}/favorite", json={"favorite": True})

    assert answer.status_code == 200, answer.text
    assert answer.json()["favorite"] is True
    # And it is on the row the screen actually draws, not only in the reply to the write.
    assert _person_row(client, person)["favorite"] is True


def test_a_person_can_be_rated_and_unrated(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane")

    client.put(f"/api/people/{person}/rating", json={"rating": 4})
    assert _person_row(client, person)["rating"] == 4

    # None clears it. There is no rating that means "unrated" (see the model).
    client.put(f"/api/people/{person}/rating", json={"rating": None})
    assert _person_row(client, person)["rating"] is None


def test_zero_stars_is_not_how_a_rating_is_cleared(client: TestClient) -> None:
    """Refused rather than read as "unrated". Stored, a zero would sort and filter as a real
    rating for ever after, and no screen can draw it."""
    sign_in(client)
    person = make_person(client, "Jane")

    answer = client.put(f"/api/people/{person}/rating", json={"rating": 0})

    assert answer.status_code == 422, answer.text


def test_two_users_rate_the_same_person_independently(client: TestClient) -> None:
    """The property that makes this per-user rather than a column on the person.

    A rating is an opinion somebody holds. Two people sharing one install hold their own, and a
    shared column would mean the second one to press a star silently overwrote the first.
    """
    sign_in(client, who="one")
    person = make_person(client, "Jane")
    client.put(f"/api/people/{person}/rating", json={"rating": 5})

    sign_in(client, who="two")
    client.put(f"/api/people/{person}/rating", json={"rating": 2})
    assert _person_row(client, person)["rating"] == 2

    sign_in(client, who="one")
    assert _person_row(client, person)["rating"] == 5, "the first opinion was overwritten"


def test_a_site_can_be_hearted_and_rated_too(client: TestClient) -> None:
    sign_in(client)
    site = _site(client, "Instagram")

    assert client.put(f"/api/sites/{site}/favorite", json={"favorite": True}).is_success
    assert client.put(f"/api/sites/{site}/rating", json={"rating": 3}).is_success

    listed = client.get("/api/sites").json()["items"]
    row = next(p for p in listed if p["id"] == site)
    assert (row["favorite"], row["rating"]) == (True, 3)


def test_hearting_something_that_does_not_exist_is_a_404_rather_than_a_403(
    client: TestClient,
) -> None:
    """The rule the whole application follows. A 403 on a person somebody may not see confirms
    that the person exists, and for a library organised by person that is most of the secret."""
    sign_in(client)

    answer = client.put(f"/api/people/{NEVER_EXISTED}/favorite", json={"favorite": True})

    assert answer.status_code == 404


# --- the pin ------------------------------------------------------------------------------
#
# The third opinion, and the only one that changes WHERE a row belongs rather than what it says. It
# is written through the kernel rather than through this slice (see `kernel.content.entity_state`),
# which is why both kinds are asked here: one file holds both statements, and a table named
# wrongly in either is a pin that silently writes nothing.


def test_a_person_can_be_pinned_and_unpinned(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane")

    answer = client.put(f"/api/people/{person}/pin", json={"pinned": True})

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"pinned": True}
    assert _person_row(client, person)["pinned"] is True

    assert client.put(f"/api/people/{person}/pin", json={"pinned": False}).json() == {
        "pinned": False
    }
    assert _person_row(client, person)["pinned"] is False


def test_a_site_can_be_pinned_too(client: TestClient) -> None:
    sign_in(client)
    site = _site(client, "Instagram")

    assert client.put(f"/api/sites/{site}/pin", json={"pinned": True}).json() == {"pinned": True}

    listed = client.get("/api/sites").json()["items"]
    assert next(one for one in listed if one["id"] == site)["pinned"] is True


def test_a_pin_is_one_users_opinion_and_moves_nobody_elses_wall(client: TestClient) -> None:
    """The reason it is not admin-only, and the reason it is a table keyed by user: a pin says
    where a row belongs on THIS person's wall, and it moves nobody else's screen."""
    sign_in(client, who="one")
    person = make_person(client, "Jane")
    client.put(f"/api/people/{person}/pin", json={"pinned": True})

    sign_in(client, who="two")
    assert _person_row(client, person)["pinned"] is False

    sign_in(client, who="one")
    assert _person_row(client, person)["pinned"] is True


def test_pinning_something_that_does_not_exist_is_a_404_rather_than_a_403(
    client: TestClient,
) -> None:
    """The visibility check runs FIRST, so a pin cannot be used to find out whether an id names
    somebody, and it also resolves the id, which is what stops a state row being written against
    one that was never minted. Such a row is one nothing will ever clean up, because the cascade has
    nothing to cascade from."""
    sign_in(client)

    assert client.put(f"/api/people/{NEVER_EXISTED}/pin", json={"pinned": True}).status_code == 404
    assert client.put(f"/api/sites/{NEVER_EXISTED}/pin", json={"pinned": True}).status_code == 404


# --- tags ---------------------------------------------------------------------------------


def test_a_tag_can_be_put_on_a_person_and_taken_off(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane")
    tag = _tag(client, "beach")

    on = client.post(f"/api/people/{person}/tags", json={"tag_id": tag, "add": True})
    assert on.status_code == 200, on.text
    assert [row["name"] for row in on.json()] == ["beach"]

    off = client.post(f"/api/people/{person}/tags", json={"tag_id": tag, "add": False})
    assert off.json() == []


def test_tagging_the_same_person_twice_is_not_an_error(client: TestClient) -> None:
    # It is what doing it twice means. The same choice assigning a person to an asset makes.
    sign_in(client)
    person = make_person(client, "Jane")
    tag = _tag(client, "beach")

    client.post(f"/api/people/{person}/tags", json={"tag_id": tag, "add": True})
    again = client.post(f"/api/people/{person}/tags", json={"tag_id": tag, "add": True})

    assert again.status_code == 200
    assert len(again.json()) == 1


def test_a_guest_may_heart_a_person_but_not_tag_one(client: TestClient, library: Library) -> None:
    """The split that decides which of these is admin-only.

    A heart is a private opinion and costs nobody anything. A tag is shared vocabulary (it
    changes what every other user's searches return), so it is a write to the library rather
    than to yourself.

    The person has to be attributed to a file the guest can actually see. A person nobody can see
    anything of does not exist as far as a guest is concerned, and both calls would then be 404
    for a reason that has nothing to do with what is being tested here.
    """
    sign_in(client, "admin")
    person = make_person(client, "Jane")
    tag = _tag(client, "beach")
    attributed = client.post(
        "/api/assets/people", json={"asset_ids": [library.shared], "person_ids": [person]}
    )
    assert attributed.status_code == 200, attributed.text

    guest = sign_in(client, "guest", who="two")
    grant_on(client, "person", person, guest, "share")

    hearted = client.put(f"/api/people/{person}/favorite", json={"favorite": True})
    assert hearted.status_code == 200, hearted.text

    refused = client.post(f"/api/people/{person}/tags", json={"tag_id": tag, "add": True})
    assert refused.status_code == 403, refused.text


# --- the cover ----------------------------------------------------------------------------


def test_a_cover_cannot_be_set_to_a_file_the_setter_may_not_see(client: TestClient) -> None:
    """Otherwise setting a cover publishes that file to everybody who can see the person.

    The read side refuses to hand back a cover the ASKER may not open, so this is the second of
    two locks. Both are wanted: the read one stops a leak through a cover set before a grant
    changed, and this one stops it being set deliberately.
    """
    sign_in(client, "admin")
    person = make_person(client, "Jane")

    sign_in(client, "guest", who="two")
    refused = client.put(f"/api/people/{person}/cover", json={"asset_id": NEVER_EXISTED})

    assert refused.status_code in (403, 404)


# --- a site's own details -----------------------------------------------------------------


def test_a_site_url_has_to_be_one_a_browser_can_safely_follow(client: TestClient) -> None:
    """`javascript:` and `data:` are both accepted by an href and both run as the page that drew
    it. Checked where it is written rather than where it is drawn, because there will be more than
    one place that draws it and the one that forgets is never the one you expect."""
    sign_in(client)
    site = _site(client, "Instagram")

    refused = client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "links": ["javascript:alert(1)"]},
    )

    assert refused.status_code == 422, refused.text
    # Said as a sentence about the field, so the form can put it under Links.
    assert refused.json() == {"detail": "A link has to start with http:// or https://."}
    assert refused.headers["Sift-Field"] == "links"


def test_a_site_carries_its_link_and_notes(client: TestClient) -> None:
    """The address a card draws is the FIRST of the site's links (`sites.SITE_ADDRESS`)."""
    sign_in(client)
    site = _site(client, "Instagram")

    saved = client.put(
        f"/api/sites/{site}/details",
        json={"notes": "the big one", "links": ["https://instagram.com", "https://ig.example"]},
    )
    assert saved.status_code == 200, saved.text

    row = next(p for p in client.get("/api/sites").json()["items"] if p["id"] == site)
    assert row["site_url"] == "https://instagram.com"
    assert row["notes"] == "the big one"
    assert client.get(f"/api/sites/{site}").json()["site_url"] == "https://instagram.com"


def test_a_caller_still_sending_the_old_address_field_changes_nothing(client: TestClient) -> None:
    """`site_url` went from the write with its column (catalog v66). A caller that still sends it
    is ignored, as any unknown field is: it neither writes a second copy nor blanks the list."""
    sign_in(client)
    site = _site(client, "Instagram")
    client.put(f"/api/sites/{site}/details", json={"notes": None, "links": ["https://ig.example"]})

    stale = client.put(
        f"/api/sites/{site}/details", json={"site_url": "https://elsewhere.example", "notes": None}
    )

    assert stale.status_code == 200, stale.text
    row = next(p for p in client.get("/api/sites").json()["items"] if p["id"] == site)
    assert row["site_url"] == "https://ig.example"


# --- reading the tags back, and putting them on a site --------------------------------------


def test_the_tags_on_a_person_read_back(client: TestClient) -> None:
    """The other half of tagging, and the half a screen actually draws.

    Writing a tag and reading it back go through different routes, and the read is the one every
    page load uses, so a write that lands in a table nothing lists is a tag that appears to have
    been lost.
    """
    sign_in(client)
    person = make_person(client, "Jane")
    tag = _tag(client, "outfit")
    client.post(f"/api/people/{person}/tags", json={"tag_id": tag, "add": True})

    listed = client.get(f"/api/people/{person}/tags")

    assert listed.status_code == 200, listed.text
    assert [row["name"] for row in listed.json()] == ["outfit"]


def test_a_site_can_be_tagged_and_the_tags_read_back(client: TestClient) -> None:
    """A site is tagged exactly as a person is, and for the same reason: it is shared vocabulary
    over one of the two things a library is organised by."""
    sign_in(client)
    site = _site(client, "Instagram")
    tag = _tag(client, "photos")

    tagged = client.post(f"/api/sites/{site}/tags", json={"tag_id": tag, "add": True})

    assert tagged.status_code == 200, tagged.text
    assert [row["name"] for row in tagged.json()] == ["photos"]
    assert [row["name"] for row in client.get(f"/api/sites/{site}/tags").json()] == ["photos"]


def test_a_site_tag_can_be_taken_off_again(client: TestClient) -> None:
    sign_in(client)
    site = _site(client, "Instagram")
    tag = _tag(client, "photos")
    client.post(f"/api/sites/{site}/tags", json={"tag_id": tag, "add": True})

    removed = client.post(f"/api/sites/{site}/tags", json={"tag_id": tag, "add": False})

    assert removed.status_code == 200, removed.text
    assert removed.json() == []


def test_the_tag_routes_answer_404_for_a_site_that_is_not_there(client: TestClient) -> None:
    """The same miss every id-taking route answers, and the same shape: not there, rather than not
    allowed, because the second confirms it exists."""
    sign_in(client)

    assert client.get(f"/api/sites/{NEVER_EXISTED}/tags").status_code == 404
    assert (
        client.post(
            f"/api/sites/{NEVER_EXISTED}/tags", json={"tag_id": NEVER_EXISTED, "add": True}
        ).status_code
        == 404
    )


# --- the cover, once there is a real file to point at ---------------------------------------


def test_a_person_wears_the_cover_they_are_given(client: TestClient, library: Library) -> None:
    sign_in(client)
    person = make_person(client, "Jane")

    set_to = client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared})

    assert set_to.status_code == 200, set_to.text
    assert set_to.json()["cover_asset_id"] == library.shared


def test_a_cover_can_be_taken_off_again(client: TestClient, library: Library) -> None:
    """Null is how a cover is cleared, and it has to be told apart from "no field sent": one
    means take the picture off and the other means leave it alone."""
    sign_in(client)
    person = make_person(client, "Jane")
    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared})

    cleared = client.put(f"/api/people/{person}/cover", json={"asset_id": None})

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["cover_asset_id"] is None


def test_choosing_a_cover_takes_off_the_face_that_was_being_used_as_one(
    client: TestClient, library: Library
) -> None:
    """Pressing "set as cover" does something even for a person with a face cover.

    A cover is either a whole frame or a face cut out of one, and the two are stored together:
    the face is a fragment of that file. Naming somebody writes both. Choosing a different file has
    to clear the face too, or anybody who already had a face for a cover goes on being drawn as that
    face, from a file they are no longer pointed at.
    """
    sign_in(client)
    person = make_person(client, "Jane")
    _give_a_face_cover(client, person, library.shared)
    assert _person_row(client, person)["cover_track_id"] is not None

    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared})

    row = _person_row(client, person)
    assert row["cover_asset_id"] == library.shared
    assert row["cover_track_id"] is None, "the old face must not survive a new cover"


def test_covering_somebody_who_is_not_there_is_a_404(client: TestClient, library: Library) -> None:
    sign_in(client)

    missing = client.put(f"/api/people/{NEVER_EXISTED}/cover", json={"asset_id": library.shared})

    assert missing.status_code == 404


def test_a_site_wears_the_cover_it_is_given(client: TestClient, library: Library) -> None:
    sign_in(client)
    site = _site(client, "Instagram")

    set_to = client.put(f"/api/sites/{site}/cover", json={"asset_id": library.shared})

    assert set_to.status_code == 200, set_to.text
    row = next(p for p in client.get("/api/sites").json()["items"] if p["id"] == site)
    assert row["cover_asset_id"] == library.shared


def test_a_site_cover_cannot_be_a_file_the_setter_may_not_see(
    client: TestClient, library: Library
) -> None:
    """The same second lock the person cover has. Setting a cover publishes that file to everybody
    who can see the site, so it may only be set to something the setter can already open."""
    sign_in(client, "admin")
    site = _site(client, "Instagram")

    sign_in(client, "guest", who="two")
    refused = client.put(f"/api/sites/{site}/cover", json={"asset_id": library.private})

    assert refused.status_code in (403, 404)


def test_covering_a_site_that_is_not_there_is_a_404(client: TestClient, library: Library) -> None:
    sign_in(client)

    missing = client.put(f"/api/sites/{NEVER_EXISTED}/cover", json={"asset_id": library.shared})

    assert missing.status_code == 404


def test_details_on_a_site_that_is_not_there_are_a_404(client: TestClient) -> None:
    sign_in(client)

    missing = client.put(f"/api/sites/{NEVER_EXISTED}/details", json={"notes": None})

    assert missing.status_code == 404


def test_a_blank_link_is_no_link_rather_than_an_empty_one(client: TestClient) -> None:
    """Spaces typed into the box and then cleared. Stored as nothing, so the screen draws no link
    rather than an anchor pointing at the empty string."""
    sign_in(client)
    site = _site(client, "Instagram")
    client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "links": ["https://instagram.com"]},
    )

    cleared = client.put(f"/api/sites/{site}/details", json={"notes": None, "links": ["   "]})

    assert cleared.status_code == 200, cleared.text
    row = next(p for p in client.get("/api/sites").json()["items"] if p["id"] == site)
    assert row["site_url"] is None
