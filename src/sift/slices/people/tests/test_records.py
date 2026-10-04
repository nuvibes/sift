# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person's record and a site's: fifteen columns and two tables.

These are about what the columns behind the record surface do, and mostly about what they do when
a caller says nothing, because a full-row writer blanks whatever it is not told about and that is a
real way to lose what somebody typed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.people.tests.conftest import Library, assign, make_person, share, sign_in


def _record(client: TestClient, person: str) -> dict[str, object]:
    held = client.get(f"/api/people/{person}").json()["record"]
    assert isinstance(held, dict)
    return held


def test_a_record_is_written_and_read_back(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane Doe")

    assert (
        client.put(
            f"/api/people/{person}",
            json={
                "name": "Jane Doe",
                "record": {
                    "birth_date": "1991-07-09",
                    "country": "US",
                    "hair_color": "brunette",
                    "measurements": "32-A-24-34",
                    "tattoos": ["left arm: a rose"],
                },
            },
        ).status_code
        == 200
    )

    held = _record(client, person)
    assert held["birth_date"] == "1991-07-09"
    assert held["country"] == "US"
    assert held["tattoos"] == ["left arm: a rose"], "a list comes back as a list, not as its JSON"


def test_an_edit_that_does_not_mention_the_record_leaves_it_alone(client: TestClient) -> None:
    """The trap this exists for: a full-row writer blanks what it is not told about.

    A rename form sends a name and knows nothing about a birthdate. If silence meant "clear it",
    renaming somebody would destroy everything else about them, quietly, and in the one direction
    nobody checks afterwards.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "record": {"country": "US"}})

    client.put(f"/api/people/{person}", json={"name": "Jane Doh"})

    assert _record(client, person)["country"] == "US"


def test_an_empty_record_really_does_clear_it(client: TestClient) -> None:
    """And the other half: an empty mapping is a real message, not the same as saying nothing.

    It is the record form saved with every box emptied, which is a thing somebody means to do.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "record": {"country": "US"}})

    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "record": {}})

    # The one flag is never absent: a person who is not a PMV creator is still an answer to that
    # question, so clearing the record leaves the switch off rather than gone.
    assert _record(client, person) == {"pmv_creator": False}


def test_a_number_typed_as_a_word_is_dropped_rather_than_refusing_the_whole_save(
    client: TestClient,
) -> None:
    """Fourteen good fields are not thrown away to punish a typo in the fifteenth."""
    sign_in(client)
    person = make_person(client, "Jane Doe")

    answer = client.put(
        f"/api/people/{person}",
        json={
            "name": "Jane Doe",
            "record": {"career_start_year": "nineteen ninety", "country": "US"},
        },
    )

    assert answer.status_code == 200
    held = _record(client, person)
    assert "career_start_year" not in held
    assert held["country"] == "US"


def test_an_emptied_box_is_nothing_rather_than_an_empty_string(client: TestClient) -> None:
    """Two values for "blank" would make every reader decide which of them means it."""
    sign_in(client)
    person = make_person(client, "Jane Doe")

    client.put(
        f"/api/people/{person}",
        json={"name": "Jane Doe", "record": {"country": "  ", "tattoos": ["", "  "]}},
    )

    assert _record(client, person) == {"pmv_creator": False}


def test_a_flag_is_yes_or_no_and_never_absent(client: TestClient) -> None:
    """The one column on `people` that refuses NULL, so it has two answers and no third.

    Every other field here is left OUT of the record when its column is null, because absent and
    empty mean the same thing to a screen that draws a dash either way. That reading would be a
    THIRD state for a flag, and every reader would have to fold it back into "no" its own way,
    which is how a mark comes to be drawn on one screen and not on the next.

    Written as a real `bool` rather than as the 0 or 1 SQLite keeps: the record goes to a screen
    that draws a word, and `1` beside "Is PMV creator" is the storage leaking onto a page.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")

    assert _record(client, person)["pmv_creator"] is False, "answered before anybody has ticked it"

    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "record": {"pmv_creator": True}})
    assert _record(client, person)["pmv_creator"] is True

    # And a form that sends its switch off, in either of the two shapes one arrives in.
    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "record": {"pmv_creator": False}})
    assert _record(client, person)["pmv_creator"] is False
    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "record": {"pmv_creator": "1"}})
    assert _record(client, person)["pmv_creator"] is True
    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "record": {"pmv_creator": "no"}})
    assert _record(client, person)["pmv_creator"] is False


def test_the_wall_carries_the_flag_although_it_carries_no_record(client: TestClient) -> None:
    """The one field on a person that is on the WALL as well as inside the record, deliberately.

    The wall draws a mark from it, on the card, and a card that had to fetch a record each would be
    sixty requests a page. Both come off the same column in the same statement, so the two readings
    cannot disagree, which is what this asserts rather than that each exists.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "record": {"pmv_creator": True}})

    listed = next(row for row in client.get("/api/people").json()["items"] if row["id"] == person)

    assert listed["record"] is None
    assert listed["pmv_creator"] is True
    assert client.get(f"/api/people/{person}").json()["pmv_creator"] is True


def test_the_wall_narrows_by_who_makes_the_edits(client: TestClient) -> None:
    """The facet, both ways round, and both rows always add up to the whole wall.

    The column refuses NULL, so there is no third group of people the panel quietly leaves out,
    which is the property that makes `yes` and `no` a complete division rather than two filters.
    """
    sign_in(client)
    creator = make_person(client, "Jane Doe")
    other = make_person(client, "Alex Vane")
    client.put(f"/api/people/{creator}", json={"name": "Jane Doe", "record": {"pmv_creator": True}})

    def ids(value: str) -> set[str]:
        answered = client.get("/api/people", params={"pmv_creator": value}).json()
        return {row["id"] for row in answered["items"]}

    assert ids("yes") == {creator}
    assert ids("no") == {other}

    counted = client.get("/api/people/facets", params={"facet": "pmv_creator"}).json()
    assert {one["value"]: one["count"] for one in counted["values"]} == {"yes": 1, "no": 1}


def test_the_wall_does_not_carry_a_record(client: TestClient) -> None:
    """A page of sixty rows draws none of it, and fifteen fields each is a payload nobody reads."""
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.put(f"/api/people/{person}", json={"name": "Jane Doe", "record": {"country": "US"}})

    listed = next(row for row in client.get("/api/people").json()["items"] if row["id"] == person)

    assert listed["record"] is None


def test_a_guest_sees_the_record(client: TestClient, library: Library) -> None:
    """A record is what somebody IS, and it is the same record for everybody looking.

    `notes` is the field that is not, and it is not in here: it has its own field and its own
    route, so widening this cannot leak it.

    The person has to be ON a file the guest may see, and that is not incidental: somebody with no
    files at all is invisible to a guest by the ordinary rule, so a version of this test without a
    library would pass by getting a 404 and prove nothing.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")
    assign(client, [library.shared], [person])
    client.put(
        f"/api/people/{person}",
        json={"name": "Jane Doe", "notes": "private", "record": {"country": "US"}},
    )

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)
    seen = client.get(f"/api/people/{person}")

    assert seen.status_code == 200, "the guest can see her at all, otherwise this proves nothing"
    assert seen.json()["record"]["country"] == "US"
    assert seen.json()["notes"] is None


# --- a site ----------------------------------------------------------------------------------


def _site(client: TestClient, name: str = "Somewhere") -> str:
    return str(client.post("/api/sites", json={"name": name}).json()["id"])


def test_a_site_is_made_with_its_details_in_one_write_or_not_at_all(client: TestClient) -> None:
    """The New page sends the whole record once: a refused address refuses the create, and no
    bare Site is left behind; a good one lands with its details."""
    sign_in(client)
    refused = client.post("/api/sites", json={"name": "Harbour Films", "links": ["not an address"]})
    assert refused.status_code == 422
    assert all(one["name"] != "Harbour Films" for one in client.get("/api/sites").json()["items"])

    made = client.post(
        "/api/sites",
        json={
            "name": "Harbour Films",
            "notes": "A small studio.",
            "aliases": ["HF"],
            "links": ["https://example.test/hf"],
        },
    )
    assert made.status_code == 201
    held = client.get(f"/api/sites/{made.json()['id']}").json()
    assert held["notes"] == "A small studio."
    assert "HF" in held["record"]["aliases"]
    assert [one["url"] if isinstance(one, dict) else one for one in held["record"]["links"]] == [
        "https://example.test/hf"
    ]


def test_a_person_is_made_with_aliases_and_links_in_one_write_or_not_at_all(
    client: TestClient,
) -> None:
    """The same shape for a person: the lists are checked by the body before anything is made."""
    sign_in(client)
    refused = client.post("/api/people", json={"name": "Quiet Harbour", "links": ["nope"]})
    assert refused.status_code == 422
    assert all(one["name"] != "Quiet Harbour" for one in client.get("/api/people").json()["items"])

    made = client.post(
        "/api/people",
        json={"name": "Quiet Harbour", "aliases": ["QH"], "links": ["https://example.test/qh"]},
    )
    assert made.status_code == 201
    person = str(made.json()["id"])
    held = client.get(f"/api/people/{person}").json()
    assert held["name"] == "Quiet Harbour"
    assert [one["alias"] for one in client.get(f"/api/people/{person}/aliases").json()] == ["QH"]
    assert [one["url"] for one in client.get(f"/api/people/{person}/links").json()] == [
        "https://example.test/qh"
    ]


def test_a_sites_other_names_are_written_and_read_back(client: TestClient) -> None:
    sign_in(client)
    site = _site(client)

    client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "aliases": ["Elsewhere", "Otherwhere"]},
    )

    assert client.get(f"/api/sites/{site}").json()["record"]["aliases"] == [
        "Elsewhere",
        "Otherwhere",
    ]


def test_a_sites_names_are_replaced_whole_rather_than_added_to(client: TestClient) -> None:
    """The form sends the entire list every time, so this is the before and the after."""
    sign_in(client)
    site = _site(client)
    client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "aliases": ["Elsewhere"]},
    )

    client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "aliases": ["Otherwhere"]},
    )

    assert client.get(f"/api/sites/{site}").json()["record"]["aliases"] == ["Otherwhere"]


def test_a_details_edit_that_does_not_mention_the_names_leaves_them(client: TestClient) -> None:
    sign_in(client)
    site = _site(client)
    client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "aliases": ["Elsewhere"]},
    )

    client.put(f"/api/sites/{site}/details", json={"notes": "a note"})

    assert client.get(f"/api/sites/{site}").json()["record"]["aliases"] == ["Elsewhere"]


def test_a_parent_typed_by_name_attaches_to_the_site_that_already_has_it(
    client: TestClient,
) -> None:
    """Find-or-create under the same case-insensitive name every other route uses.

    Typing a network that already exists must attach to it rather than making a second one beside
    it, which is the fault a unique name exists to prevent.
    """
    sign_in(client)
    parent = _site(client, "A Network")
    child = _site(client, "A Label")

    client.put(
        f"/api/sites/{child}/details",
        json={"notes": None, "parent": "a network"},
    )

    assert client.get(f"/api/sites/{child}").json()["record"]["parent"] == "A Network"
    sites = client.get("/api/sites").json()["items"]
    assert len([one for one in sites if one["id"] == parent]) == 1
    assert len([one for one in sites if one["name"].lower() == "a network"]) == 1


SITE_LOOP = (
    "A Site can't be part of itself or of a Site that's already part of it. "
    "Choose a Site from another network."
)


def test_a_site_cannot_be_its_own_parent(client: TestClient) -> None:
    """A cycle of one, refused in words the record form shows as they are: dropped, the field
    looked as though it had worked. Refused before anything is written, notes included."""
    sign_in(client)
    site = _site(client, "A Network")

    answer = client.put(
        f"/api/sites/{site}/details",
        json={"notes": "Typed in the same save", "parent": "a network"},
    )

    assert answer.status_code == 422
    assert answer.json()["detail"] == SITE_LOOP
    held = client.get(f"/api/sites/{site}").json()
    assert "parent" not in held["record"]
    assert not held.get("notes")


def test_a_site_cannot_be_filed_under_one_already_part_of_it(client: TestClient) -> None:
    """The longer loop: a label under a network, then the network under that label."""
    sign_in(client)
    network = _site(client, "A Network")
    label = _site(client, "A Label")
    assert (
        client.put(
            f"/api/sites/{label}/details", json={"notes": None, "parent": "A Network"}
        ).status_code
        < 300
    )

    answer = client.put(f"/api/sites/{network}/details", json={"notes": None, "parent": "A Label"})

    assert answer.status_code == 422
    assert answer.json()["detail"] == SITE_LOOP
    assert "parent" not in client.get(f"/api/sites/{network}").json()["record"]
    # And a Site outside the branch is still a parent it can have.
    _site(client, "Elsewhere")
    assert (
        client.put(
            f"/api/sites/{network}/details", json={"notes": None, "parent": "Elsewhere"}
        ).status_code
        < 300
    )
    assert client.get(f"/api/sites/{network}").json()["record"]["parent"] == "Elsewhere"


def test_a_sites_record_saved_whole_renames_and_writes_its_details_in_one_request(
    client: TestClient,
) -> None:
    sign_in(client)
    site = _site(client, "A Label")

    answer = client.put(
        f"/api/sites/{site}",
        json={
            "name": "Another Label",
            "notes": "Typed in the same save",
            "aliases": ["Elsewhere"],
            "parent": "A Network",
            "links": ["https://label.example"],
        },
    )

    assert answer.status_code == 200, answer.text
    # The reply carries the record, the new parent's id included, so the page needs no re-read.
    assert answer.json()["record"]["parent"] == "A Network"
    assert answer.json()["record"]["parent_id"]
    held = client.get(f"/api/sites/{site}").json()
    assert held["name"] == "Another Label"
    assert held["notes"] == "Typed in the same save"
    assert held["record"]["aliases"] == ["Elsewhere"]
    assert held["record"]["links"] == ["https://label.example"]


def test_a_refused_parent_refuses_the_rename_saved_beside_it(client: TestClient) -> None:
    """One save, one answer: a parent that makes a loop leaves the Site exactly as it was, its name
    included, rather than renamed with the rest refused."""
    sign_in(client)
    network = _site(client, "A Network")
    label = _site(client, "A Label")
    client.put(f"/api/sites/{label}", json={"name": "A Label", "parent": "A Network"})

    answer = client.put(
        f"/api/sites/{network}",
        json={"name": "A Renamed Network", "notes": "a note", "parent": "A Label"},
    )

    assert answer.status_code == 422
    assert answer.json()["detail"] == SITE_LOOP
    held = client.get(f"/api/sites/{network}").json()
    assert held["name"] == "A Network"
    assert not held.get("notes")
    assert "parent" not in held["record"]


def test_a_parent_named_as_the_site_is_about_to_be_called_is_a_loop(client: TestClient) -> None:
    """Asked under the name the save gives it, so the rename and the parent cannot land apart."""
    sign_in(client)
    site = _site(client, "A Label")

    answer = client.put(f"/api/sites/{site}", json={"name": "A Network", "parent": "a network"})

    assert answer.status_code == 422
    assert client.get(f"/api/sites/{site}").json()["name"] == "A Label"


def test_a_taken_name_refuses_the_details_saved_beside_it(client: TestClient) -> None:
    sign_in(client)
    _site(client, "A Network")
    site = _site(client, "A Label")

    answer = client.put(
        f"/api/sites/{site}",
        json={"name": "a network", "notes": "a note", "aliases": ["Elsewhere"]},
    )

    assert answer.status_code == 409
    held = client.get(f"/api/sites/{site}").json()
    assert not held.get("notes")
    assert "aliases" not in held["record"]


def test_a_rename_alone_leaves_the_details_as_they_were(client: TestClient) -> None:
    sign_in(client)
    site = _site(client, "A Label")
    client.put(f"/api/sites/{site}/details", json={"notes": "kept", "aliases": ["Elsewhere"]})

    assert client.put(f"/api/sites/{site}", json={"name": "Another Label"}).status_code == 200

    held = client.get(f"/api/sites/{site}").json()
    assert held["notes"] == "kept"
    assert held["record"]["aliases"] == ["Elsewhere"]


def test_a_persons_other_names_and_addresses_ride_on_the_one_save(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Wren Halloway")
    client.put(
        f"/api/people/{person}",
        json={"name": "Wren Halloway", "aliases": ["Wren", "wren"], "links": ["https://a.example"]},
    )
    assert [one["url"] for one in client.get(f"/api/people/{person}/links").json()] == [
        "https://a.example"
    ]

    answer = client.put(
        f"/api/people/{person}",
        json={"name": "Wren Halloway", "aliases": ["Wren", "Halloway"], "links": []},
    )

    assert answer.status_code == 200, answer.text
    aliases = [one["alias"] for one in client.get(f"/api/people/{person}/aliases").json()]
    assert sorted(aliases) == ["Halloway", "Wren"]
    assert client.get(f"/api/people/{person}/links").json() == []


def test_a_refused_address_refuses_the_rename_saved_beside_it(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Wren Halloway")

    answer = client.put(
        f"/api/people/{person}",
        json={"name": "Esme Wrenfield", "aliases": ["Wren"], "links": ["javascript:alert(1)"]},
    )

    assert answer.status_code == 422
    assert client.get(f"/api/people/{person}").json()["name"] == "Wren Halloway"
    assert client.get(f"/api/people/{person}/aliases").json() == []


def test_an_age_is_worked_out_from_the_birthdate_rather_than_stored(client: TestClient) -> None:
    """There is no column for it, and there should not be. An age written down is wrong within a
    year of being written and nothing would ever come back to correct it, while the date it comes
    from is a fact that does not change, so the only honest place for it is the read."""
    sign_in(client)
    person = make_person(client, "Jane")
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1990-06-01"}}
    )

    held = _record(client, person)

    assert isinstance(held["age"], int)
    assert held["age"] >= 30


def test_a_partial_date_is_no_age_rather_than_a_wrong_one(client: TestClient) -> None:
    """The column is TEXT and an import can put anything in it. A record row reading "Age 55" for
    somebody born in `1970-00-00` is worse than one reading nothing."""
    sign_in(client)
    person = make_person(client, "Jane")
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1970-00-00"}}
    )

    held = _record(client, person)

    assert held["birth_date"] == "1970-00-00"
    assert "age" not in held


def test_a_date_that_is_not_one_at_all_is_no_age(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane")
    client.put(f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "sometime"}})

    assert "age" not in _record(client, person)


def test_a_birthdate_in_the_future_is_no_age(client: TestClient) -> None:
    """A typo rather than a person, and a negative age on a record is a number nobody can read."""
    sign_in(client)
    person = make_person(client, "Jane")
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "2999-01-01"}}
    )

    assert "age" not in _record(client, person)


def test_a_birthdate_further_back_than_anybody_lives_is_no_age(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane")
    client.put(
        f"/api/people/{person}", json={"name": "Jane", "record": {"birth_date": "1800-01-01"}}
    )

    assert "age" not in _record(client, person)


def test_somebody_with_no_birthdate_has_no_age(client: TestClient) -> None:
    # Absent rather than null, like every other column that is not filled in: a person with nothing
    # on their record would otherwise send fifteen nulls to a browser on every page.
    sign_in(client)
    person = make_person(client, "Jane")

    assert "age" not in _record(client, person)


def test_a_person_made_with_a_record_has_it_from_the_one_write(client: TestClient) -> None:
    """The New page sends the whole record with the name, so the person is never seen bare."""
    sign_in(client)

    made = client.post(
        "/api/people", json={"name": "Quiet Harbour", "record": {"hair_color": "AUBURN"}}
    )

    assert made.status_code == 201
    held = client.get(f"/api/people/{made.json()['id']}").json()
    assert held["record"]["hair_color"] == "AUBURN"


def test_a_save_without_an_entry_drops_that_other_name(client: TestClient) -> None:
    sign_in(client)
    person = client.post("/api/people", json={"name": "Quiet Harbour", "aliases": ["QH", "Q"]})
    person_id = str(person.json()["id"])

    saved = client.put(f"/api/people/{person_id}", json={"name": "Quiet Harbour", "aliases": ["Q"]})

    assert saved.status_code == 200
    assert [one["alias"] for one in client.get(f"/api/people/{person_id}/aliases").json()] == ["Q"]


def test_the_lists_on_a_persons_write_are_held_to_the_one_at_a_time_limits(
    client: TestClient,
) -> None:
    """An entry too long for the one-at-a-time route is refused in the list too, before anything
    is made; a list sent as null is the same as one not sent."""
    sign_in(client)
    too_long_alias = client.post(
        "/api/people", json={"name": "Quiet Harbour", "aliases": ["a" * 121]}
    )
    too_long_link = client.post(
        "/api/people",
        json={"name": "Quiet Harbour", "links": ["https://example.test/" + "a" * 2000]},
    )
    assert (too_long_alias.status_code, too_long_link.status_code) == (422, 422)
    assert all(one["name"] != "Quiet Harbour" for one in client.get("/api/people").json()["items"])

    made = client.post(
        "/api/people", json={"name": "Quiet Harbour", "aliases": None, "links": None}
    )

    assert made.status_code == 201
    assert client.get(f"/api/people/{made.json()['id']}/aliases").json() == []
