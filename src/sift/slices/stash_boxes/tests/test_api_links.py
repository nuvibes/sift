# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-box endpoints, over HTTP, against a real application: links, the ledger, the undecided names and kept pictures.

Nothing here reaches a network: the adapter on the running application is replaced, so what is
asserted is which questions the routes would ask and what they hand back.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel.records import FoundRecord, Subject
from sift.slices.stash_boxes.tests.api_support import (
    _a_site,
    _a_tag,
    _keep_local,
    _ledger_line,
    _linked_person,
    _read,
    _write,
    a_person,
    a_person_link,
    add_a_box,
    db_path,
    sign_in,
    stand_in,
    write_undecided,
)
from sift.testing.library import a_png, hide_for

pytestmark = pytest.mark.integration


def test_what_has_been_agreed_is_readable_by_anybody_looking_at_the_page(
    client: TestClient,
) -> None:
    """The one route in this file that is not admin-only, and the one that reaches no network. A
    record is shown to everybody looking at the page; asking a service anything spends a stored
    key, and that stays admin-only.

    A guest gets 404 here for a person nothing visible came from, which is the scoping rule doing
    its job, not an admin check. 403 would be the check, and that is the difference this asserts.
    """
    sign_in(client, "admin")
    person = a_person(client)

    assert client.get(f"/api/stash-boxes/links/person/{person}").json() == {
        "links": [],
        # No box made them; the request above did, so the maker is the user asking, with no pass
        # beside it because a person is not one.
        "made_by": {
            "kind": "you",
            "via": None,
            "act": None,
            "box_id": None,
            "box_name": None,
            "box_slug": None,
        },
    }

    sign_in(client, "guest")
    assert client.get(f"/api/stash-boxes/links/person/{person}").status_code == 404


def test_reading_the_links_of_a_subject_nobody_may_see_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")

    answer = client.get("/api/stash-boxes/links/person/01HX0000000000000000000009")

    assert answer.status_code == 404


def test_a_kind_of_subject_that_cannot_be_linked_is_refused(client: TestClient) -> None:
    """404 for a kind nothing can be linked to, and 404 for a subject that was never minted: the
    same answer to both, so a link route cannot become a way to ask whether something exists."""
    sign_in(client, "admin")

    assert client.get("/api/stash-boxes/links/widget/01HX0000000000000000000009").status_code == 404


def test_searching_by_name_asks_every_switched_on_box(client: TestClient) -> None:
    """Nothing is written. This is the question; the answer is a list of candidates with a picture
    and a count beside each, so a person can tell two people of the same name apart."""
    sign_in(client, "admin")
    add_a_box(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id="box",
            remote_id="r1",
            subject=Subject.PERSON,
            name="Jane Doe",
            confidence=0.5,
        )
    ]

    answer = client.get("/api/stash-boxes/search/person", params={"term": "jane"})

    assert answer.status_code == 200, answer.text
    assert adapter.asked == 1
    records = answer.json()["answers"][0]["records"]
    assert [one["name"] for one in records] == ["Jane Doe"]


# --- agreeing that a box's entry is one of ours ------------------------------------------------------


def test_a_link_is_written_from_a_fresh_read_and_can_be_taken_back(
    client: TestClient,
) -> None:
    """The record is fetched FRESH by id rather than taken from whichever search answer was on
    screen: a search answer is a match on a name and may be a month old; a link is somebody saying
    "this is them", and what is worth keeping under that statement is what the service says now."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id=box,
            remote_id="r1",
            subject=Subject.PERSON,
            name="Jane",
            fields={"birth_date": "1991-02-02"},
            confidence=1.0,
        )
    ]

    linked = client.put(f"/api/stash-boxes/links/person/{person}/{box}", json={"remote_id": "r1"})

    assert linked.status_code == 200, linked.text
    assert linked.json()["remote_id"] == "r1"
    held = client.get(f"/api/stash-boxes/links/person/{person}").json()["links"]
    assert [one["box_id"] for one in held] == [box]

    forgotten = client.delete(f"/api/stash-boxes/links/person/{person}/{box}")

    assert forgotten.status_code == 204, forgotten.text
    assert client.get(f"/api/stash-boxes/links/person/{person}").json()["links"] == []


def test_a_link_to_an_entry_the_box_does_not_know_is_refused_rather_than_written(
    client: TestClient,
) -> None:
    """A box that was asked and holds no such entry writes nothing, and says that is what happened.

    "Could not be asked" and "does not know that entry" have opposite next moves. This is the
    second: the box answered, so it is a 404 naming the box, and the sentence says to pick again.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    stand_in(client)

    refused = client.put(
        f"/api/stash-boxes/links/person/{person}/{box}", json={"remote_id": "nobody"}
    )

    assert refused.status_code == 404
    assert "StashDB was asked and has no entry by that id" in refused.json()["detail"]
    assert "could not be asked" not in refused.json()["detail"]
    assert client.get(f"/api/stash-boxes/links/person/{person}").json()["links"] == []


def test_a_link_to_a_box_that_cannot_be_asked_says_why_in_the_boxs_own_words(
    client: TestClient,
) -> None:
    """The FIRST cause, apart from the second: the box was never reached, so nothing is known
    about the entry at all: a 502 carrying the adapter's own sentence, never the other one."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    stand_in(client).refuse = "Sift could not reach StashDB."

    refused = client.put(f"/api/stash-boxes/links/person/{person}/{box}", json={"remote_id": "r1"})

    assert refused.status_code == 502
    assert refused.json()["detail"] == "Sift could not reach StashDB."
    assert client.get(f"/api/stash-boxes/links/person/{person}").json()["links"] == []


def test_asking_a_box_again_about_something_already_linked(client: TestClient) -> None:
    """By hand, never on a timer. The cache beside it expires after thirty days and this does not
    wait for that."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id=box, remote_id="r1", subject=Subject.PERSON, name="Jane", confidence=1.0
        )
    ]
    client.put(f"/api/stash-boxes/links/person/{person}/{box}", json={"remote_id": "r1"})

    again = client.post(f"/api/stash-boxes/links/person/{person}/{box}/refresh")

    assert again.status_code == 200, again.text
    assert again.json()["remote_id"] == "r1"


def test_refreshing_or_forgetting_a_link_that_is_not_there_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)

    assert client.post(f"/api/stash-boxes/links/person/{person}/{box}/refresh").status_code == 404
    assert client.delete(f"/api/stash-boxes/links/person/{person}/{box}").status_code == 404


def test_a_link_to_a_site_or_a_tag_is_scoped_the_way_a_person_is(client: TestClient) -> None:
    """Three kinds of subject and one rule. 404 for one this user may not be shown and 404 for
    one that was never minted, so none of these becomes a way to ask whether something exists."""
    sign_in(client, "admin")
    box = add_a_box(client)
    gone = "01HX0000000000000000000009"

    for kind in ("site", "tag"):
        assert client.get(f"/api/stash-boxes/links/{kind}/{gone}").status_code == 404
        assert (
            client.put(
                f"/api/stash-boxes/links/{kind}/{gone}/{box}", json={"remote_id": "r1"}
            ).status_code
            == 404
        )
        assert client.post(f"/api/stash-boxes/links/{kind}/{gone}/{box}/refresh").status_code == 404
        assert client.delete(f"/api/stash-boxes/links/{kind}/{gone}/{box}").status_code == 404


def test_linking_through_a_box_nobody_configured_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")
    person = a_person(client)

    refused = client.put(
        f"/api/stash-boxes/links/person/{person}/01HX0000000000000000000009",
        json={"remote_id": "r1"},
    )

    assert refused.status_code == 404


# --- the ledger, and the names nobody could choose for -------------------------------------------
#
# The one part of this slice a signed-in guest may read, so what they leave out for that guest is
# the whole of their scoping.


def test_the_ledger_lists_what_a_box_has_been_agreed_to_know(client: TestClient) -> None:
    """A RECORD, not a decision: what Auto-enrich and Enrich have written, newest first, with which
    box said it and when. Read from Sift's own tables and never from the network."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = _linked_person(client, box, image_url=None)

    body = client.get("/api/stash-boxes/linked").json()

    assert [(one["subject"], one["id"], one["box"]) for one in body["items"]] == [
        ("person", person, "StashDB")
    ]
    assert body["total"] == 1
    # The name the BOX goes by, beside the name this library uses: the two can differ, and the
    # row says both rather than making somebody guess which it is showing.
    assert body["items"][0]["known_as"] == "Jane"


def test_the_ledger_tells_no_account_apart_from_filled_nothing_and_says_what_was_filled(
    client: TestClient,
) -> None:
    """Null and empty are two answers, and the ledger keeps them apart.

    A link with no recorded run must not read "filled nothing in". Planted here through the same
    routes the chooser uses: a bare link (no account), a take of nothing (filled nothing), and a
    take of a real field (said by name).
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id=box,
            remote_id="r1",
            subject=Subject.PERSON,
            name="Jane",
            fields={"birth_date": "1991-02-02"},
            confidence=1.0,
        )
    ]
    assert (
        client.put(f"/api/stash-boxes/links/person/{person}/{box}", json={"remote_id": "r1"})
    ).status_code == 200

    # A bare link: a run with no list, which is Sift keeping no account of fields. The line is the
    # only account on the wire: no list of words rides beside it for a client to build its own.
    row = client.get("/api/stash-boxes/linked").json()["items"][0]
    assert "wrote" not in row
    assert _ledger_line(client).endswith("was linked without filling anything in")

    took_nothing = client.post(
        f"/api/stash-boxes/links/person/{person}/{box}/take", json={"keys": []}
    )
    assert took_nothing.status_code == 200, took_nothing.text
    assert took_nothing.json() == {"fields": 0}
    assert _ledger_line(client).endswith("was linked and had nothing new to fill in")

    # A key the kept answer does not offer is ignored rather than written from the body.
    took = client.post(
        f"/api/stash-boxes/links/person/{person}/{box}/take",
        json={"keys": ["birth_date", "height_cm"]},
    )
    assert took.status_code == 200, took.text
    assert took.json() == {"fields": 1}
    assert "birthdate" in _ledger_line(client)
    assert (
        client.get(f"/api/stash-boxes/record/person/{person}").json()["values"]["birth_date"]
        == "1991-02-02"
    )


def test_the_ledger_line_names_what_a_box_filled_in_and_what_changed_since(
    client: TestClient,
) -> None:
    """A link's line names each field WITH ITS VALUE, never a bare
    count, and a value changed by hand afterwards says so; a link made before runs were recorded
    says exactly that and what the record agrees with it on, never "no record"."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client, "Wrenna Sable")
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id=box,
            remote_id="r1",
            subject=Subject.PERSON,
            name="Wrenna Sable",
            fields={"birth_date": "1991-02-02", "gender": "FEMALE"},
            confidence=1.0,
        )
    ]
    assert (
        client.put(f"/api/stash-boxes/links/person/{person}/{box}", json={"remote_id": "r1"})
    ).status_code == 200
    name = client.get("/api/stash-boxes/linked").json()["items"][0]["box"]
    assert _ledger_line(client) == f"{name} was linked without filling anything in"

    took = client.post(
        f"/api/stash-boxes/links/person/{person}/{box}/take",
        json={"keys": ["birth_date", "gender"]},
    )
    assert took.status_code == 200, took.text
    assert _ledger_line(client) == f"{name} filled in birthdate (1991-02-02) and gender (Female)"

    # Changed afterwards: the box's part stays said, and so does the change.
    _write(db_path(client), [("UPDATE people SET gender = 'MALE' WHERE id = ?", (person,))])
    assert _ledger_line(client) == (
        f"{name} filled in birthdate (1991-02-02) and gender (Female, since changed)"
    )

    # A link older than the run record: when, and what agrees today, never "no record".
    _write(db_path(client), [("DELETE FROM enrichment_runs WHERE local_id = ?", (person,))])
    line = _ledger_line(client)
    assert line == (
        f"{name} was linked before Sift recorded what a stash-box fills in, and agrees on"
        " birthdate (1991-02-02)"
    )
    assert "no record" not in line.casefold()


def test_taking_fields_needs_a_link_first_and_is_refused_to_a_guest(client: TestClient) -> None:
    """The values come from what was KEPT with the link, so with no link there is nothing to take;
    and writing a record is an admin's act, as every other write in this slice is."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)

    unlinked = client.post(
        f"/api/stash-boxes/links/person/{person}/{box}/take", json={"keys": ["birth_date"]}
    )
    assert unlinked.status_code == 409

    sign_in(client, "guest")
    assert (
        client.post(f"/api/stash-boxes/links/person/{person}/{box}/take", json={"keys": []})
    ).status_code in (403, 404)
    assert client.get(f"/api/stash-boxes/record/person/{person}").status_code in (403, 404)


def test_the_ledger_narrows_to_one_kind(client: TestClient) -> None:
    """Three kinds share the page, so the column that says which is also the one to narrow by."""
    sign_in(client, "admin")
    box = add_a_box(client)
    _linked_person(client, box, image_url=None)

    assert client.get("/api/stash-boxes/linked", params={"subject": "person"}).json()["total"] == 1
    assert client.get("/api/stash-boxes/linked", params={"subject": "tag"}).json()["items"] == []


def test_a_TAG_this_account_may_not_see_is_left_off_the_ledger_too(client: TestClient) -> None:
    """The same rule for the other two kinds, and it is a DIFFERENT read.

    A person is resolved through the people wall; a site and a tag through their own. The row names
    the subject either way, so a guest who may not be shown a tag must not be shown the fact that a
    box knows it, and a subject routed through the wrong read would come back visible to somebody
    it should not.
    """
    admin = sign_in(client, "admin")
    box = add_a_box(client)
    tag = _a_tag(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id="box", remote_id="r-tag", subject=Subject.TAG, name="Beach", image_url=None
        )
    ]
    linked = client.put(f"/api/stash-boxes/links/tag/{tag}/{box}", json={"remote_id": "r-tag"})
    assert linked.status_code == 200, linked.text
    assert client.get("/api/stash-boxes/linked").json()["items"], "the link is on the ledger"

    hide_for(db_path(client), "tag", tag, admin)

    assert client.get("/api/stash-boxes/linked").json()["items"] == []


def test_a_subject_this_account_may_not_see_is_left_off_the_ledger(client: TestClient) -> None:
    """The row names a PERSON. A guest who may not be shown them must not be shown the fact that a
    box knows them either (which is a statement about that person, made on a page they are
    supposed to be absent from), and the totals beside the list count only what it may hold, so
    they do not say how many rows were left off either.
    """
    admin = sign_in(client, "admin")
    box = add_a_box(client)
    person = _linked_person(client, box, image_url=None)
    hide_for(db_path(client), "person", person, admin)
    write_undecided(client, "person", person, candidates=2)

    sign_in(client, "guest")

    linked = client.get("/api/stash-boxes/linked").json()
    assert (linked["items"], linked["total"], linked["files"]) == ([], 0, 0)
    undecided = client.get("/api/stash-boxes/undecided").json()
    assert (undecided["items"], undecided["total"]) == ([], 0)


def test_the_names_nobody_could_choose_for_are_a_list_rather_than_a_number(
    client: TestClient,
) -> None:
    """A declined name is a row on a list to work from, not only a number in a job's note."""
    sign_in(client, "admin")
    person = a_person(client)
    write_undecided(client, "person", person, candidates=2)

    body = client.get("/api/stash-boxes/undecided").json()

    assert [(one["subject"], one["id"], one["candidates"]) for one in body["items"]] == [
        ("person", person, 2)
    ]
    assert body["total"] == 1


def test_the_undecided_list_opens_at_its_row_and_where_it_was_once_that_row_is_decided(
    client: TestClient,
) -> None:
    """`from` names the row a page began at, the way every list in Organize keeps its page in its
    address. Choosing for that name takes it off the list (the ordinary way the row goes), and
    then `near`, where it was, is the page served: the rows after it have closed up, so the same
    place is the same page. Serving the top instead would be page one after every decision."""
    sign_in(client, "admin")
    names = sorted(
        a_person(client, name) for name in ("Bryn Calloway", "Cassia Lynn", "Elina Sorrel")
    )
    for one in names:
        write_undecided(client, "person", one, candidates=2)

    def page(**query: object) -> dict[str, Any]:
        answer = client.get("/api/stash-boxes/undecided", params={"limit": 1, **query})
        assert answer.status_code == 200, answer.text
        return dict(answer.json())

    found = page(**{"from": names[1], "near": 0})
    assert ([one["id"] for one in found["items"]], found["offset"]) == ([names[1]], 1), (
        "a row still on the list is where the page starts, wherever `near` says it was"
    )

    _write(
        db_path(client),
        [("DELETE FROM stash_box_undecided WHERE local_id = ?", (names[1],))],
    )
    gone = page(**{"from": names[1], "near": 1})
    assert ([one["id"] for one in gone["items"]], gone["offset"]) == ([names[2]], 1), (
        "a row decided away opens where the page was, not at the top"
    )
    assert page(**{"from": names[1]})["offset"] == 0, "with no `near`, the top"


def test_a_name_this_account_may_not_see_is_left_off_the_undecided_list(
    client: TestClient,
) -> None:
    """Same rule as the ledger beside it, and the same reason: the row IS a person."""
    admin = sign_in(client, "admin")
    person = a_person(client)
    write_undecided(client, "person", person, candidates=2)
    hide_for(db_path(client), "person", person, admin)

    sign_in(client, "guest")

    assert client.get("/api/stash-boxes/undecided").json()["items"] == []


def test_a_page_of_either_list_holds_its_size_of_what_this_account_may_see(
    client: TestClient,
) -> None:
    """A row this account may not see is stepped over before the page is cut, not after: a page
    of one is never empty while rows remain, the total counts what the pages hold, and a row's
    place is counted among the rows shown."""
    admin = sign_in(client, "admin")
    box = add_a_box(client)
    people = sorted(_linked_person(client, box, image_url=None) for _ in range(3))
    _write(db_path(client), [("UPDATE person_stash_box_links SET fetched_at = 0", ())])
    for one in people:
        write_undecided(client, "person", one, candidates=2)
    hide_for(db_path(client), "person", people[0], admin)

    for address, row_of in (
        ("/api/stash-boxes/linked", lambda one: f"{one}:{box}"),
        ("/api/stash-boxes/undecided", lambda one: one),
    ):

        def page(query: dict[str, object], address: str = address) -> dict[str, Any]:
            answer = client.get(address, params={"limit": 1, **query})
            assert answer.status_code == 200, answer.text
            return dict(answer.json())

        first = page({"offset": 0})
        assert ([one["id"] for one in first["items"]], first["total"]) == ([people[1]], 2), address
        assert [one["id"] for one in page({"offset": 1})["items"]] == [people[2]], address
        assert page({"from": row_of(people[2])})["offset"] == 1, address
        assert page({"from": row_of(people[0]), "near": 0})["offset"] == 0, address


def test_keeping_a_picture_reads_the_address_out_of_sifts_own_copy(client: TestClient) -> None:
    """The address never comes out of the request.

    What the browser holds is Sift's own proxy address for the picture; handing that back is an
    address on the wrong host, which the adapter refuses. So the body names the subject and the
    picture is looked up in what this box already said.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    person = _linked_person(client, box, image_url="https://stashdb.example/jane.png")
    asked: list[str] = []

    async def picture(box_: object, url: str, *, vector: bool = False) -> tuple[bytes, str]:
        asked.append(url)
        return (a_png(), "image/png")

    client.app.state.stash_boxes._adapter.picture = picture  # type: ignore[attr-defined]

    answer = client.post(
        f"/api/stash-boxes/{box}/picture/keep",
        json={"subject": "person", "local_id": person},
    )

    assert answer.status_code == 204, answer.text
    assert asked == ["https://stashdb.example/jane.png"]
    # And it is the person's COVER (the one picture every wall draws them by), not a file in a
    # cache no screen reads. See `SubjectCovers`.
    cover = client.get(f"/api/people/{person}/cover")
    assert cover.status_code == 200, cover.text
    assert cover.headers["content-type"].startswith("image/")
    # By the user who pressed it, naming the box: "Cover set to StashDB's picture".
    (said,) = _read(
        db_path(client),
        "SELECT actor_kind, payload FROM workbench_decisions"
        " WHERE verb = 'edited' AND payload LIKE '%cover%'",
        (),
    )
    assert said["actor_kind"] == "user"
    assert json.loads(str(said["payload"]))["box_id"] == box


def test_keeping_a_picture_for_a_box_nobody_configured_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")
    person = a_person(client)

    answer = client.post(
        "/api/stash-boxes/01HX0000000000000000000009/picture/keep",
        json={"subject": "person", "local_id": person},
    )

    assert answer.status_code == 404


def test_keeping_a_picture_for_somebody_who_is_not_there_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)

    answer = client.post(
        f"/api/stash-boxes/{box}/picture/keep",
        json={"subject": "person", "local_id": "01HX0000000000000000000009"},
    )

    assert answer.status_code == 404


def test_keeping_a_picture_from_a_box_that_is_not_linked_to_them_is_a_404(
    client: TestClient,
) -> None:
    """ "This box is not linked to that" is the honest answer, and it is what the sweep gets."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)

    answer = client.post(
        f"/api/stash-boxes/{box}/picture/keep",
        json={"subject": "person", "local_id": person},
    )

    assert answer.status_code == 404


def test_keeping_a_picture_the_box_never_offered_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    person = _linked_person(client, box, image_url=None)

    answer = client.post(
        f"/api/stash-boxes/{box}/picture/keep",
        json={"subject": "person", "local_id": person},
    )

    assert answer.status_code == 404


def test_a_picture_that_will_not_come_back_is_a_404_rather_than_a_kept_blank(
    client: TestClient,
) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    person = _linked_person(client, box, image_url="https://stashdb.example/jane.png")

    async def picture(box_: object, url: str, *, vector: bool = False) -> tuple[bytes, str] | None:
        return None

    client.app.state.stash_boxes._adapter.picture = picture  # type: ignore[attr-defined]

    answer = client.post(
        f"/api/stash-boxes/{box}/picture/keep",
        json={"subject": "person", "local_id": person},
    )

    assert answer.status_code == 404


def test_bytes_that_are_not_a_picture_are_refused_rather_than_filed(client: TestClient) -> None:
    """A login wall saved under a `.png` is a broken image on every screen that name appears on."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = _linked_person(client, box, image_url="https://stashdb.example/jane.png")

    async def picture(box_: object, url: str, *, vector: bool = False) -> tuple[bytes, str]:
        return (b"<html>sign in</html>", "text/html")

    client.app.state.stash_boxes._adapter.picture = picture  # type: ignore[attr-defined]

    answer = client.post(
        f"/api/stash-boxes/{box}/picture/keep",
        json={"subject": "person", "local_id": person},
    )

    assert answer.status_code == 400


def test_a_picture_is_kept_under_a_sites_own_name_too(client: TestClient) -> None:
    """The subject is resolved through the read that knows what kind of thing it is, and the
    picture lands on THAT kind's cover column."""
    sign_in(client, "admin")
    box = add_a_box(client)
    site = _a_site(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id="box",
            remote_id="r1",
            subject=Subject.SITE,
            name="Northlight",
            image_url="https://stashdb.example/northlight.png",
        )
    ]
    linked = client.put(f"/api/stash-boxes/links/site/{site}/{box}", json={"remote_id": "r1"})
    assert linked.status_code == 200, linked.text

    async def picture(box_: object, url: str, *, vector: bool = False) -> tuple[bytes, str]:
        return (a_png(), "image/png")

    adapter.picture = picture  # type: ignore[attr-defined]

    answer = client.post(
        f"/api/stash-boxes/{box}/picture/keep",
        json={"subject": "site", "local_id": site},
    )

    assert answer.status_code == 204, answer.text
    assert client.get(f"/api/sites/{site}/cover").status_code == 200


def test_a_picture_is_kept_under_a_tags_own_name_too(client: TestClient) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    tag = _a_tag(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id="box",
            remote_id="r1",
            subject=Subject.TAG,
            name="Beach",
            image_url="https://stashdb.example/beach.png",
        )
    ]
    linked = client.put(f"/api/stash-boxes/links/tag/{tag}/{box}", json={"remote_id": "r1"})
    assert linked.status_code == 200, linked.text

    async def picture(box_: object, url: str, *, vector: bool = False) -> tuple[bytes, str]:
        return (a_png(), "image/png")

    adapter.picture = picture  # type: ignore[attr-defined]

    answer = client.post(
        f"/api/stash-boxes/{box}/picture/keep",
        json={"subject": "tag", "local_id": tag},
    )

    assert answer.status_code == 204, answer.text
    assert client.get(f"/api/tags/{tag}/cover").status_code == 200


# --- every press about something kept local, and the reads beside it ------------------------------


def test_every_press_that_would_ask_about_or_apply_to_a_person_kept_local_is_refused_kept_local(
    client: TestClient,
) -> None:
    """The chooser's search, a link, a refresh and keeping a box's picture as the cover: each
    answers the door's sentence with a 409 (never a 500, never a quiet 200), and no box is
    asked anything."""
    from sift.slices.stash_boxes.service import KEPT_LOCAL

    sign_in(client, "admin")
    box = add_a_box(client)
    adapter = stand_in(client)
    person = a_person(client)
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})
    _keep_local(client, "person", person)
    links = f"/api/stash-boxes/links/person/{person}/{box}"

    pressed = {
        "search": client.get(
            "/api/stash-boxes/search/person", params={"term": "Jane", "about": person}
        ),
        "link": client.put(links, json={"remote_id": "r1"}),
        "refresh": client.post(f"{links}/refresh"),
        "cover": client.post(
            f"/api/stash-boxes/{box}/picture/keep", json={"subject": "person", "local_id": person}
        ),
    }

    assert {press: answer.status_code for press, answer in pressed.items()} == dict.fromkeys(
        pressed, 409
    )
    assert {answer.json()["detail"] for answer in pressed.values()} == {KEPT_LOCAL}
    assert adapter.asked == 0


def test_a_refresh_of_a_box_that_cannot_be_reached_is_a_502_in_the_boxes_words(
    client: TestClient,
) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    adapter = stand_in(client)
    adapter.refuse = "Sift could not reach StashDB."
    person = a_person(client)
    a_person_link(client, person, box, {"birth_date": "1991-02-02"})

    answer = client.post(f"/api/stash-boxes/links/person/{person}/{box}/refresh")

    assert answer.status_code == 502
    assert answer.json()["detail"] == "Sift could not reach StashDB."


def test_what_is_held_and_taking_fields_answer_404_for_a_record_nobody_may_see(
    client: TestClient,
) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    unseen = "01HX0000000000000000000999"

    held = client.get(f"/api/stash-boxes/record/person/{unseen}")
    taken = client.post(
        f"/api/stash-boxes/links/person/{unseen}/{box}/take", json={"keys": ["birth_date"]}
    )

    assert (held.status_code, taken.status_code) == (404, 404)


def test_a_kind_this_build_has_no_writer_for_holds_nothing_to_show(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 in words, not a KeyError, where the composition handed in no writer for the kind."""
    from sift.kernel import wiring

    sign_in(client, "admin")
    person = a_person(client)
    writers = wiring.part_of_app(client.app, wiring.ENRICHER).writers  # type: ignore[arg-type]
    monkeypatch.delitem(writers, Subject.PERSON)

    answer = client.get(f"/api/stash-boxes/record/person/{person}")

    assert answer.status_code == 404
    assert answer.json()["detail"] == "nothing of that kind can be linked"


def test_a_kept_link_carries_the_boxs_own_page_for_the_entry(
    client: TestClient,
) -> None:
    """What the record's stash-box section presses through to, on the one route every viewer
    reads. Worked out on the server (see `entry_page`), so the screen never builds an address on a
    box. A box whose pages Sift does not know carries none, and its id is drawn as text."""
    sign_in(client, "admin")
    known = client.post(
        "/api/stash-boxes",
        json={"name": "StashDB", "endpoint": "https://stashdb.org/graphql", "api_key": "a-key"},
    ).json()["id"]
    unknown = add_a_box(client)
    person = a_person(client)
    stand_in(client).records = [
        FoundRecord(
            source_id=known, remote_id="r1", subject=Subject.PERSON, name="Jane", confidence=1.0
        )
    ]

    linked = client.put(f"/api/stash-boxes/links/person/{person}/{known}", json={"remote_id": "r1"})
    client.put(f"/api/stash-boxes/links/person/{person}/{unknown}", json={"remote_id": "r1"})

    assert linked.json()["page_url"] == "https://stashdb.org/performers/r1"
    held = client.get(f"/api/stash-boxes/links/person/{person}").json()["links"]
    assert sorted((one["box_id"] == known, one["page_url"]) for one in held) == [
        (False, None),
        (True, "https://stashdb.org/performers/r1"),
    ]


# --- which box gave a field ----------------------------------------------------------------------


def test_a_link_says_which_fields_its_box_gave_from_the_runs_written_at_the_time(
    client: TestClient,
) -> None:
    """The record's hover says "From <box>, fetched <when>" and the band says what each box filled
    in: both read `gave`, worked out from the runs written with each write. A list is merged from
    every box that answered, so it is never one box's."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id=box,
            remote_id="r1",
            subject=Subject.PERSON,
            name="Jane",
            fields={"birth_date": "1991-02-02", "height_cm": 177, "aliases": ["Jay"]},
            confidence=1.0,
        )
    ]
    assert (
        client.put(f"/api/stash-boxes/links/person/{person}/{box}", json={"remote_id": "r1"})
    ).status_code == 200
    held = client.get(f"/api/stash-boxes/links/person/{person}").json()["links"]
    assert held[0]["gave"] == [], "a bare link filled nothing in"

    took = client.post(
        f"/api/stash-boxes/links/person/{person}/{box}/take",
        json={"keys": ["birth_date", "height_cm", "aliases"]},
    )
    assert took.status_code == 200, took.text

    held = client.get(f"/api/stash-boxes/links/person/{person}").json()["links"]
    assert sorted(held[0]["gave"]) == ["birth_date", "height_cm"]


def test_a_value_typed_over_a_boxs_is_no_longer_said_to_be_from_it() -> None:
    """The runs say which box WROTE a field last; the value held now says whether that is still
    where it came from."""
    from sift.slices.stash_boxes.router import _still_given
    from sift.slices.stash_boxes.service import Linked

    link = Linked(
        source_id="b1",
        source_name="StashDB",
        remote_id="r1",
        record=FoundRecord(
            source_id="b1",
            remote_id="r1",
            subject=Subject.PERSON,
            name="Jane",
            fields={"height_cm": 177, "country": "CA", "eye_color": "Brown", "aliases": ["Jay"]},
        ),
        fetched_at=0,
    )
    given = {"height_cm": "b1", "country": "b1", "eye_color": "b2", "aliases": "b1"}

    assert _still_given(
        given, {"height_cm": 177.0, "country": " ca ", "aliases": ["Jay"]}, link
    ) == ["height_cm", "country"]
    assert _still_given(given, {"height_cm": 157, "country": None}, link) == []


def test_a_studio_that_may_be_a_person_is_asked_and_answered_over_http(
    client: TestClient,
) -> None:
    """The question's page, its Yes with the receipt an Undo is pressed on, and the Undo itself."""
    from sift.testing.library import write_rows

    sign_in(client, "admin")
    write_rows(
        db_path(client),
        [
            (
                "INSERT INTO stash_boxes (id, name, endpoint, created_at)"
                " VALUES ('box-9', 'StashDB', 'https://stashdb.example/graphql', 0)",
                (),
            ),
            (
                "INSERT INTO sites (id, name, name_sort, created_at, created_by_kind,"
                " created_by_via) VALUES ('site-9', 'Esme Wrenfield', 'esme wrenfield', 1, 'box',"
                " 'stash')",
                (),
            ),
            (
                "INSERT INTO site_links (id, site_id, url, created_at) VALUES ('l-9', 'site-9',"
                " 'https://www.manyvids.com/Profile/1001/Esme-Wrenfield/Store/Videos/', 1)",
                (),
            ),
            (
                "INSERT INTO usernames (id, site_id, name, name_sort, created_at)"
                " VALUES ('u-9', 'site-9', '', '', 1)",
                (),
            ),
            *(
                statement
                for at, people in enumerate((["Esme Wrenfield"], ["Wren Halloway"]))
                for statement in (
                    (
                        "INSERT INTO assets (id, identity, media_type, added_at)"
                        " VALUES (?, ?, 'video', 0)",
                        (f"a-{at}", f"a-{at}"),
                    ),
                    (
                        "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at)"
                        " VALUES (?, 'u-9', 'stash_box', 1)",
                        (f"a-{at}",),
                    ),
                    (
                        "INSERT INTO asset_stash_box_matches"
                        " (asset_id, box_id, remote_id, payload, grade, state, found_at)"
                        " VALUES (?, 'box-9', ?, ?, 'certain', 'applied', 1)",
                        (
                            f"a-{at}",
                            f"s-{at}",
                            json.dumps([{"fields": {"site": "Esme Wrenfield", "people": people}}]),
                        ),
                    ),
                )
            ),
        ],
    )

    asked = client.get("/api/stash-boxes/creator-sites")
    assert asked.status_code == 200
    assert [
        (one["name"], one["files"], one["credited"], one["site"], one["handle"])
        for one in asked.json()["items"]
    ] == [("Esme Wrenfield", 2, 1, "ManyVids", "Esme-Wrenfield")]

    yes = client.post("/api/stash-boxes/creator-sites/site-9/username")
    assert yes.status_code == 200
    assert yes.json()["said"] == "Esme Wrenfield is a username on ManyVids, not a Site"
    named = yes.json()["pieces"][0]
    assert (named["kind"], named["text"]) == ("username", "Esme Wrenfield")
    assert named["href"] == f"/browse?username={named['id']}"
    assert client.post("/api/stash-boxes/creator-sites/site-9/username").status_code == 404
    assert client.get("/api/stash-boxes/creator-sites").json()["items"] == []

    undone = client.post(f"/api/workbench/decisions/{yes.json()['receipt_id']}/undo")
    assert undone.status_code == 200
    assert [one["id"] for one in client.get("/api/stash-boxes/creator-sites").json()["items"]] == [
        "site-9"
    ]

    # No: it stays a Site, said so, and is not asked about again; neither answer can follow it.
    no = client.post("/api/stash-boxes/creator-sites/site-9/site")
    assert no.status_code == 200, no.text
    assert no.json()["said"] == "Esme Wrenfield stays a Site"
    assert no.json()["receipt_id"]
    assert client.get("/api/stash-boxes/creator-sites").json()["items"] == []
    assert client.post("/api/stash-boxes/creator-sites/site-9/site").status_code == 404
    assert client.post("/api/stash-boxes/creator-sites/site-9/username").status_code == 404
    assert client.post("/api/stash-boxes/creator-sites/no-such-site/site").status_code == 404
