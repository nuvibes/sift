# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may read and write the people screens, and what the vault keeps back of a person's own
row; media concealment is the resolver's."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    Library,
    assign,
    db_path,
    make_person,
    read,
    share,
    sign_in,
    write,
)
from sift.testing.auth import TEST_PIN

# Only the locked vault is tested here: no HTTP route builds an unlocked viewer yet. The unlocked
# side is tested in the access layer.


def _vault(client: TestClient, person_id: str, name: str) -> None:
    """Put an existing person into the vault, last, as happens in life."""
    response = client.put(f"/api/people/{person_id}", json={"name": name, "vault": True})
    assert response.status_code == 200, response.text


def test_a_vaulted_person_is_absent_from_the_list_and_the_count(
    client: TestClient, library: Library
) -> None:
    """A vaulted person is absent from the list and the count, admins included."""
    sign_in(client)
    open_person = make_person(client, "Jane Doe")
    hidden = make_person(client, "Hidden One", vault=True)
    assign(client, [library.shared], [open_person])

    listed = client.get("/api/people").json()["items"]

    assert [person["id"] for person in listed] == [open_person]
    assert hidden not in [person["id"] for person in listed]


def test_a_vaulted_person_is_absent_from_autocomplete(client: TestClient) -> None:
    """Typing their name must not confirm they exist.

    An autocomplete row is nothing but a name, which is the identifying part, so the prefix
    that would match them returns nothing at all.
    """
    sign_in(client)
    make_person(client, "Hidden One", vault=True)

    assert client.get("/api/people", params={"prefix": "Hidden"}).json()["items"] == []


def test_a_vaulted_person_is_not_named_by_the_resolver(client: TestClient) -> None:
    """Not by their name and not by an alias. A username is not a third way in: a download
    files a file under the person directly, so the username names them by naming them."""
    sign_in(client)
    hidden = make_person(client, "Hidden One")
    assert client.post(f"/api/people/{hidden}/aliases", json={"alias": "H1"}).status_code == 201
    _vault(client, hidden, "Hidden One")

    for term in ("Hidden One", "H1"):
        response = client.get("/api/people/resolve", params={"term": term})
        assert response.json()["people"] == [], term


def test_a_vaulted_person_is_concealed_from_every_route_that_takes_their_id(
    client: TestClient,
) -> None:
    """A vaulted person is concealed from every route taking their id, as for one never minted."""
    sign_in(client)
    hidden = make_person(client, "Hidden One")
    alias = client.post(f"/api/people/{hidden}/aliases", json={"alias": "Her Other Name"})
    assert alias.status_code == 201, "added before they were hidden"
    alias_id = alias.json()["id"]
    _vault(client, hidden, "Hidden One")

    assert client.get(f"/api/people/{hidden}").status_code == 404
    assert client.get(f"/api/people/{hidden}/aliases").status_code == 404
    assert client.put(f"/api/people/{hidden}", json={"name": "Hidden One"}).status_code == 404
    assert client.delete(f"/api/people/{hidden}").status_code == 404
    assert client.post(f"/api/people/{hidden}/aliases", json={"alias": "X"}).status_code == 404
    assert client.delete(f"/api/people/{hidden}/aliases/{alias_id}").status_code == 404


def test_a_vaulted_person_cannot_be_assigned_to_anything(
    client: TestClient, library: Library
) -> None:
    """Assigning would say they exist, through a route that answers 200 or 404 on their id."""
    sign_in(client)
    hidden = make_person(client, "Hidden One")
    _vault(client, hidden, "Hidden One")

    assert assign(client, [library.shared], [hidden]).status_code == 404


def test_vaulting_a_person_afterwards_takes_them_out_of_the_list(client: TestClient) -> None:
    """The flag is a toggle, not something only set at creation."""
    sign_in(client)
    person = make_person(client, "Jane Doe")

    assert client.get("/api/people").json()["items"]

    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "vault": True})

    assert client.get("/api/people").json()["items"] == []


def test_a_guest_is_shown_only_people_they_can_reach_something_through(
    client: TestClient, library: Library
) -> None:
    """A guest sees only people they reach something through."""
    sign_in(client)
    person = make_person(client, "Jane Doe")
    assign(client, [library.shared], [person])

    guest = sign_in(client, "guest", who="two")

    assert client.get("/api/people").json()["items"] == []

    share(client, library.shared, guest)

    listed = client.get("/api/people").json()["items"]

    assert [(p["id"], p["asset_count"]) for p in listed] == [(person, 1)]


def test_the_wall_is_paged_and_the_pages_do_not_overlap_or_lose_anybody(
    client: TestClient, library: Library
) -> None:
    """Paging that repeats or drops a row is worse than none: the wall is where somebody is
    named, and a person who falls between two pages is a person who cannot be found at all."""
    sign_in(client)
    for index in range(7):
        make_person(client, f"Person {index}")

    first = client.get("/api/people", params={"limit": 3, "offset": 0}).json()
    second = client.get("/api/people", params={"limit": 3, "offset": 3}).json()
    third = client.get("/api/people", params={"limit": 3, "offset": 6}).json()

    assert [len(page["items"]) for page in (first, second, third)] == [3, 3, 1]
    walked = [row["id"] for page in (first, second, third) for row in page["items"]]
    assert len(set(walked)) == 7


def test_the_total_is_what_this_user_may_see_rather_than_what_exists(
    client: TestClient, library: Library
) -> None:
    """The number a paginator is drawn from. Reporting the absolute one would say how many are
    being kept back, which is the thing concealing them was for."""
    sign_in(client)
    reachable = make_person(client, "Jane Doe")
    assign(client, [library.shared], [reachable])
    make_person(client, "Nobody Reachable")

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)

    answered = client.get("/api/people", params={"limit": 1}).json()

    assert answered["total"] == 1
    assert [row["id"] for row in answered["items"]] == [reachable]


def test_a_page_past_the_end_is_empty_rather_than_wrapping_round(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    make_person(client, "Jane Doe")

    answered = client.get("/api/people", params={"limit": 10, "offset": 500}).json()

    assert answered["items"] == []


def test_a_count_never_includes_what_the_asker_was_not_shown(
    client: TestClient, library: Library
) -> None:
    """Two users asking about one person are meant to get different numbers."""
    sign_in(client)
    person = make_person(client, "Jane Doe")
    assign(client, [library.shared, library.private], [person])

    assert client.get("/api/people").json()["items"][0]["asset_count"] == 2

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)

    assert client.get("/api/people").json()["items"][0]["asset_count"] == 1


def test_the_resolver_names_nobody_a_guest_could_not_already_see(
    client: TestClient, library: Library
) -> None:
    """The resolver names nobody a guest could not already see."""
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.post(f"/api/people/{person}/aliases", json={"alias": "JD"})
    assign(client, [library.shared], [person])

    guest = sign_in(client, "guest", who="two")

    assert client.get("/api/people").json()["items"] == []
    assert client.get("/api/people/resolve", params={"term": "Jane Doe"}).json()["people"] == []
    assert client.get("/api/people/resolve", params={"term": "JD"}).json()["people"] == []

    share(client, library.shared, guest)

    found = client.get("/api/people/resolve", params={"term": "JD"}).json()["people"]

    assert [p["id"] for p in found] == [person], "shared, so now they may be named"
    # And the card is the same card the list draws, so its count is the list's, never a zero
    # beside somebody the list shows with files.
    assert found[0]["asset_count"] == 1


def test_a_guest_cannot_read_the_other_names_of_someone_they_may_not_see(
    client: TestClient, library: Library
) -> None:
    """The alias list is every other name a person goes by, which is the identifying part.

    Handing it to somebody the people list is refusing to show them to would give away more than
    the list ever would, through a route that only needs an id to reach.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.post(f"/api/people/{person}/aliases", json={"alias": "JD"})
    assign(client, [library.shared], [person])

    guest = sign_in(client, "guest", who="two")

    assert client.get(f"/api/people/{person}/aliases").status_code == 404

    share(client, library.shared, guest)

    assert client.get(f"/api/people/{person}/aliases").status_code == 200


def test_the_resolver_does_not_hand_back_the_notes_written_about_somebody(
    client: TestClient, library: Library
) -> None:
    """Free text an admin wrote is not part of naming somebody, and this route is not admin-only."""
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.put(
        f"/api/people/{person}",
        json={"name": "Jane Doe", "notes": "something private"},
    )

    found = client.get("/api/people/resolve", params={"term": "Jane Doe"}).json()["people"]

    assert found[0]["notes"] is None


def test_the_notes_about_somebody_are_not_on_the_card_any_route_builds(
    client: TestClient, library: Library
) -> None:
    """Notes are on no card any route builds; Details are read on their own route."""
    sign_in(client)
    # A default cover is a picture, and the library's files are videos.
    write(
        db_path(client),
        [
            ("UPDATE assets SET media_type = 'image' WHERE id = ?", (one,))
            for one in [library.shared]
        ],
    )
    person = make_person(client, "Jane Doe")
    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "notes": "something private"})
    assign(client, [library.shared], [person])

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)

    on_asset = client.get(f"/api/assets/{library.shared}/people").json()

    assert [p["name"] for p in on_asset] == ["Jane Doe"]
    assert on_asset[0]["notes"] is None
    # Filed by hand with no picture of her own, she wears the file she was filed on, which this
    # guest may see (`test_a_person_filed_by_hand_wears_that_file_when_they_have_no_picture`).
    assert on_asset[0]["cover_asset_id"] == library.shared


def test_a_person_filed_by_hand_wears_that_file_when_they_have_no_picture(
    client: TestClient, library: Library
) -> None:
    """Filing somebody on a file by hand gives them that file as their picture when they have none,
    as a stash-box's filing does: the filing row fires the default-cover rule
    (`kernel/access/default_covers.py`), whoever wrote it. A picture somebody chose stays."""
    sign_in(client)
    # A default cover is a picture, and the library's files are videos.
    write(
        db_path(client),
        [
            ("UPDATE assets SET media_type = 'image' WHERE id = ?", (one,))
            for one in [library.shared]
        ],
    )
    bare = make_person(client, "Tamsin Vole")
    chosen = make_person(client, "Orrin Blythe")
    client.put(f"/api/people/{chosen}/cover", json={"asset_id": library.private})

    assert assign(client, [library.shared], [bare, chosen]).status_code < 300

    covers = {
        str(row["id"]): row["cover_asset_id"]
        for row in read(db_path(client), "SELECT id, cover_asset_id FROM people", ())
    }
    assert covers[bare] == library.shared
    assert covers[chosen] == library.private


def test_a_guest_can_read_the_details_on_somebody_but_cannot_write_them(
    client: TestClient, library: Library
) -> None:
    """A guest reads Details but cannot write them."""
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "notes": "what I wrote"})
    assign(client, [library.shared], [person])

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)

    read = client.get(f"/api/people/{person}/notes")
    assert read.status_code == 200
    assert read.json()["notes"] == "what I wrote"

    # And the write is still refused, which is what makes the read safe to open.
    refused = client.put(f"/api/people/{person}", json={"name": "Jane Doe", "notes": "mine now"})
    assert refused.status_code == 403
    assert guest is not None


def test_details_on_somebody_a_guest_may_not_see_is_a_404_not_a_refusal(
    client: TestClient, library: Library
) -> None:
    """Somebody outside a guest's library has no Details to read, and is not confirmed to exist.

    The route is open to a guest, so this is the case that matters: reading it goes through the
    same scoped lookup the name does, and a 403 here would confirm that the person is real.
    """
    sign_in(client)
    hidden = make_person(client, "Not Yours")
    client.put(f"/api/people/{hidden}", json={"name": "Not Yours", "notes": "private"})
    assign(client, [library.private], [hidden])

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)
    assert guest is not None

    assert client.get(f"/api/people/{hidden}/notes").status_code == 404


def test_a_persons_cover_rides_along_when_the_viewer_may_see_it(
    client: TestClient, library: Library
) -> None:
    """The face a person is drawn as, so a screen can draw them as a face rather than a name.

    Shown only because the cover is an asset this guest may see (here, the very asset they are
    looking at). A cover is a picture of one of their files, and one you may not open is one you are
    not told about.
    """
    sign_in(client)
    person = make_person(client, "Reya")
    assign(client, [library.shared], [person])
    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared})

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)

    on_asset = client.get(f"/api/assets/{library.shared}/people").json()
    assert on_asset[0]["cover_asset_id"] == library.shared


def test_a_cover_the_viewer_cannot_see_is_withheld_not_leaked(
    client: TestClient, library: Library
) -> None:
    """A cover set to a file this guest has no grant on is withheld: its id would otherwise say a
    hidden asset exists. They still get the person (reachable through the shared clip) with no
    cover, exactly as they would for a person who has none set."""
    sign_in(client)
    person = make_person(client, "Reya")
    assign(client, [library.shared], [person])
    # The cover is the private asset, which an admin may see and so may set; the guest may not.
    client.put(f"/api/people/{person}/cover", json={"asset_id": library.private})

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)

    on_asset = client.get(f"/api/assets/{library.shared}/people").json()
    assert [p["name"] for p in on_asset] == ["Reya"]
    assert on_asset[0]["cover_asset_id"] is None


def test_editing_somebody_without_sending_notes_leaves_them_alone(client: TestClient) -> None:
    """No route hands notes to a screen, so a screen cannot send them back.

    Treating that silence as "clear them" would make every edit (a rename, the vault) destroy
    them quietly. An explicit null still clears them, which is the difference between not saying and
    saying nothing.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "notes": "keep me"})

    client.put(f"/api/people/{person}", json={"name": "Jane Renamed"})

    kept = read(db_path(client), "SELECT notes FROM people WHERE id = ?", (person,))
    assert kept[0]["notes"] == "keep me"

    client.put(f"/api/people/{person}", json={"name": "Jane Renamed", "notes": None})

    cleared = read(db_path(client), "SELECT notes FROM people WHERE id = ?", (person,))
    assert cleared[0]["notes"] is None


def test_a_guest_may_read_the_screens_and_write_none_of_them(
    client: TestClient, library: Library
) -> None:
    """Creating, renaming, deleting and assigning change what every other user sees."""
    sign_in(client)
    person = make_person(client, "Jane Doe")

    sign_in(client, "guest", who="two")

    assert client.get("/api/people").status_code == 200
    assert client.get("/api/sites").status_code == 200
    assert client.post("/api/people", json={"name": "Someone"}).status_code == 403
    assert client.put(f"/api/people/{person}", json={"name": "X"}).status_code == 403
    assert client.delete(f"/api/people/{person}").status_code == 403
    assert client.post("/api/sites", json={"name": "X"}).status_code == 403
    assert client.post(f"/api/people/{person}/aliases", json={"alias": "X"}).status_code == 403
    assert assign(client, [library.shared], [person]).status_code == 403


def test_signing_in_is_required_for_all_of_it(client: TestClient) -> None:
    client.cookies.clear()

    assert client.get("/api/people").status_code == 401
    assert client.get("/api/sites").status_code == 401
    assert client.get("/api/people/resolve", params={"term": "x"}).status_code == 401


def test_an_asset_nobody_shared_is_missing_rather_than_refused(
    client: TestClient, library: Library
) -> None:
    """A 403 on an asset somebody was never shown confirms it exists."""
    sign_in(client)
    make_person(client, "Jane Doe")

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)

    assert client.get(f"/api/assets/{library.shared}/people").status_code == 200
    assert client.get(f"/api/assets/{library.private}/people").status_code == 404


# --- hiding somebody, for one user ----------------------------------------------------------------
#
#
# Hiding somebody for one user has a route of its own, like every other entity.


def test_a_guest_can_hide_somebody_for_themselves(client: TestClient, library: Library) -> None:
    """And for nobody else. A vault is per user, so an admin goes on seeing them."""
    sign_in(client)
    person = make_person(client, "Marion Dell")
    assign(client, [library.shared], [person])

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)
    assert [one["id"] for one in client.get("/api/people").json()["items"]] == [person]

    hidden = client.put(f"/api/people/{person}/vault", json={"vault": True})

    assert hidden.status_code == 204, hidden.text
    assert client.get("/api/people").json()["items"] == [], "still listed to the user that hid"

    sign_in(client)
    assert [one["id"] for one in client.get("/api/people").json()["items"]] == [person], (
        "one user's vault reached another's screen"
    )


def test_somebody_hidden_cannot_be_taken_back_out_while_the_vault_is_locked(
    client: TestClient,
) -> None:
    """The seal, the same one a site, a collection and a tag have.

    It matters more in this direction than in the other: somebody this user hid is absent from
    their scoped list, so before the PIN is entered the unhide is the 404 an unknown id gets:
    answering at all would confirm they are there.
    """
    sign_in(client)
    person = make_person(client, "Marion Dell")

    assert client.put(f"/api/people/{person}/vault", json={"vault": True}).status_code == 204
    assert client.put(f"/api/people/{person}/vault", json={"vault": False}).status_code == 404
    assert client.get("/api/people").json()["items"] == [], (
        "still concealed after the refused write"
    )

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200

    assert client.put(f"/api/people/{person}/vault", json={"vault": False}).status_code == 204
    assert [one["id"] for one in client.get("/api/people").json()["items"]] == [person]


def test_the_vault_route_answers_a_concealed_person_as_it_answers_an_unknown_one(
    client: TestClient,
) -> None:
    """One id, one answer, in both directions. A different status is the reveal."""
    sign_in(client)
    person = make_person(client, "Marion Dell")
    assert client.put(f"/api/people/{person}/vault", json={"vault": True}).status_code == 204

    for unreachable in (person, NEVER_EXISTED):
        for wanted in (True, False):
            answered = client.put(f"/api/people/{unreachable}/vault", json={"vault": wanted})
            assert answered.status_code == 404, (unreachable, wanted, answered.text)
