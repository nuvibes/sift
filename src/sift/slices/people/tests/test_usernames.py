# SPDX-License-Identifier: AGPL-3.0-or-later
"""Usernames, and saying who they belong to.

Over HTTP rather than against the service, for the reason the rest of this folder gives: what is
being claimed is a permission surface and a promise about what a write touches, and both of those
live in the router, the dependencies and the access layer together.

The claims here, in order of how much they would cost to get wrong:

- A username nothing visible came from is not named to a guest, just as the site it sits on is not.
- Joining a username to somebody makes every file under it count under them.
- Joining writes the username as an also-known-as name, so they are findable by the spelling that is
  actually on the files, which is the half people notice.
- Taking the join back off leaves the person, and leaves what was agreed to.
- Creating a person and pointing at one that exists are two different asks, and asking for both or
  neither is refused rather than resolved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.wiring import reindexer
from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    Library,
    db_path,
    make_person,
    make_username,
    read,
    sign_in,
    write,
)

pytestmark = pytest.mark.integration


def _attach_file(path: Path, asset_id: str, username_id: str) -> None:
    """File one asset under one username, the way a download does."""
    write(
        path,
        [
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (asset_id, username_id),
            )
        ],
    )


def test_an_unknown_sort_is_refused_rather_than_answered_in_some_order(
    client: TestClient,
) -> None:
    """Every sibling of this route refuses an unknown order, and so does this one."""
    sign_in(client)

    assert client.get("/api/usernames", params={"sort": "sideways"}).status_code == 422


def test_a_username_with_a_visible_file_is_listed(client: TestClient, library: Library) -> None:
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.shared, username)

    page = client.get("/api/usernames").json()
    assert [one["username"] for one in page["items"]] == ["esmewrenfield"]
    assert page["items"][0]["asset_count"] == 1


def test_a_username_nobody_may_see_a_file_of_is_not_named_to_a_guest(
    client: TestClient, library: Library
) -> None:
    """The rule the site's own wall already applies, one level down.

    A username's row names a person, a site and a spelling. Answering it to anyone who may see none
    of its files would publish all three, which is exactly what the scoping withholds everywhere
    else, and a by-id read that skipped the rule would do it.
    """
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.private, username)

    sign_in(client, role="guest", who="two")
    assert client.get("/api/usernames").json()["items"] == []


def test_a_username_that_was_never_minted_is_not_found(client: TestClient) -> None:
    sign_in(client)
    assert client.put(f"/api/usernames/{NEVER_EXISTED}", json={}).status_code == 404


def _held(client: TestClient, username: str) -> dict[str, object]:
    """One username's row as the list gives it. A username has no by-id read: it has no page of its
    own, and the list is what every screen that shows one reads."""
    rows = client.get("/api/usernames", params={"limit": 200}).json()["items"]
    return dict(next(one for one in rows if one["id"] == username))


def test_joining_a_username_to_somebody_makes_their_files_count_under_them(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    person = make_person(client, "Neve Arbor")
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.shared, username)

    answer = client.post(f"/api/usernames/{username}/person", json={"person_id": person})
    assert answer.status_code == 200, answer.text
    assert answer.json()["person_id"] == person
    assert answer.json()["person_name"] == "Neve Arbor"

    rows = read(db_path(client), "SELECT person_id FROM usernames WHERE id = ?", (username,))
    assert rows[0]["person_id"] == person

    # And the FILES, which is the half that makes the join mean anything. Without it the pointer is
    # invisible everywhere except this one screen: the person's page, the People wall and every
    # `people:` query read `asset_people`, and all three would still show nothing.
    filed = read(
        db_path(client),
        "SELECT asset_id, source FROM asset_people WHERE person_id = ?",
        (person,),
    )
    assert [(one["asset_id"], one["source"]) for one in filed] == [(library.shared, "username")]


def test_answering_who_a_username_is_writes_the_usernames_queues_receipt_once(
    client: TestClient, library: Library
) -> None:
    """The usernames queue's answer is a decision like every other queue's: ONE line, written as
    that queue's receipt (counted, with Undo), never a receipt beside a second line for one act."""
    sign_in(client)
    person = make_person(client, "Neve Arbor")
    username = make_username(client, "SomeSite", "esmewrenfield")

    client.post(f"/api/usernames/{username}/person", json={"person_id": person})

    rows = read(
        db_path(client),
        "SELECT queue, title, verb, object_id, payload, user_id FROM workbench_decisions"
        " WHERE verb = 'linked' AND object_kind = 'person'",
    )
    assert [(one["queue"], one["title"], one["object_id"]) for one in rows] == [
        ("usernames", "You said esmewrenfield is Neve Arbor", person)
    ]
    assert rows[0]["user_id"] is not None
    assert json.loads(str(rows[0]["payload"]))["username_id"] == username

    undone = client.post(f"/api/workbench/decisions/{_decision(client)}/undo")
    assert undone.status_code in (200, 204), undone.text
    held = read(db_path(client), "SELECT person_id FROM usernames WHERE id = ?", (username,))
    assert held[0]["person_id"] is None


def _decision(client: TestClient) -> str:
    rows = read(db_path(client), "SELECT id FROM workbench_decisions WHERE queue = 'usernames'")
    return str(rows[0]["id"])


def test_joining_writes_the_username_as_a_name_they_can_be_found_by(
    client: TestClient, library: Library
) -> None:
    """The half people notice. Without it a person's files all carry a spelling that finds nobody."""
    sign_in(client)
    person = make_person(client, "Neve Arbor")
    username = make_username(client, "SomeSite", "esmewrenfield")

    client.post(f"/api/usernames/{username}/person", json={"person_id": person})

    aliases = client.get(f"/api/people/{person}/aliases").json()
    assert [one["alias"] for one in aliases] == ["esmewrenfield"]


def test_the_username_is_not_written_as_a_name_when_it_already_is_the_name(
    client: TestClient,
) -> None:
    """An alias identical to the name adds nothing to find them by, and puts a row in a list that
    exists to hold the OTHER spellings."""
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")

    client.post(f"/api/usernames/{username}/person", json={"new_person_name": "esmewrenfield"})

    person = read(db_path(client), "SELECT person_id FROM usernames WHERE id = ?", (username,))[0][
        "person_id"
    ]
    assert client.get(f"/api/people/{person}/aliases").json() == []


def test_asking_not_to_be_found_by_the_username_is_honoured(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Neve Arbor")
    username = make_username(client, "SomeSite", "esmewrenfield")

    client.post(f"/api/usernames/{username}/person", json={"person_id": person, "as_alias": False})

    assert client.get(f"/api/people/{person}/aliases").json() == []


def test_a_username_can_become_somebody_new(client: TestClient) -> None:
    sign_in(client)
    username = make_username(client, "SomeSite", "newcomer")

    answer = client.post(f"/api/usernames/{username}/person", json={"new_person_name": "Newcomer"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["person_name"] == "Newcomer"

    named = [one["name"] for one in client.get("/api/people").json()["items"]]
    assert "Newcomer" in named


def test_asking_for_both_at_once_is_refused(client: TestClient) -> None:
    """The branch with the larger consequence has to be asked for on purpose.

    A single field that created somebody when it failed to resolve would make the creating branch
    the accident, which is the one thing this route is shaped to prevent.
    """
    sign_in(client)
    person = make_person(client, "Neve Arbor")
    username = make_username(client, "SomeSite", "esmewrenfield")

    answer = client.post(
        f"/api/usernames/{username}/person",
        json={"person_id": person, "new_person_name": "Somebody Else"},
    )
    assert answer.status_code == 400


def test_asking_for_neither_is_refused(client: TestClient) -> None:
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")
    assert client.post(f"/api/usernames/{username}/person", json={}).status_code == 400


def test_pointing_at_somebody_who_is_not_there_is_not_found(client: TestClient) -> None:
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")
    answer = client.post(f"/api/usernames/{username}/person", json={"person_id": NEVER_EXISTED})
    assert answer.status_code == 404


def test_taking_the_join_off_leaves_the_person_and_the_name(client: TestClient) -> None:
    """An undo that deleted the person would delete a row somebody may have been editing since."""
    sign_in(client)
    person = make_person(client, "Neve Arbor")
    username = make_username(client, "SomeSite", "esmewrenfield")
    client.post(f"/api/usernames/{username}/person", json={"person_id": person})

    assert client.delete(f"/api/usernames/{username}/person").status_code == 200

    assert _held(client, username)["person_id"] is None
    assert client.get(f"/api/people/{person}").status_code == 200
    assert [one["alias"] for one in client.get(f"/api/people/{person}/aliases").json()] == [
        "esmewrenfield"
    ]


def test_only_what_was_sent_is_written(client: TestClient, library: Library) -> None:
    """A screen that edits one field must not blank the one beside it.

    This is the trap every partial write in the application faces: a model of optional fields all
    defaulting to None is a full-row writer in a partial writer's clothes.
    """
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.shared, username)

    client.put(
        f"/api/usernames/{username}",
        json={"display_name": "Neve", "url": "https://somesite.test/esmewrenfield"},
    )
    client.put(f"/api/usernames/{username}", json={"display_name": "Neve A."})

    held = _held(client, username)
    assert held["display_name"] == "Neve A."
    assert held["url"] == "https://somesite.test/esmewrenfield"


def test_a_guest_may_not_say_who_a_username_is(client: TestClient, library: Library) -> None:
    sign_in(client)
    person = make_person(client, "Neve Arbor")
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.shared, username)

    sign_in(client, role="guest", who="two")
    answer = client.post(f"/api/usernames/{username}/person", json={"person_id": person})
    assert answer.status_code in {401, 403, 404}


def test_taking_the_join_off_unfiles_what_it_filed_and_leaves_what_it_did_not(
    client: TestClient, library: Library
) -> None:
    """The undo takes back its own writes, and only its own.

    An attribution somebody made by hand is not this decision's to remove, which is exactly what
    the source column on the row is for. Without it an undo would either leave the files filed under
    somebody they are not, or sweep up a decision a person took separately.
    """
    sign_in(client)
    person = make_person(client, "Neve Arbor")
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.shared, username)
    _attach_file(db_path(client), library.private, username)
    # Filed by hand, before the join, and nothing here may touch it.
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_people (asset_id, person_id, source) VALUES (?, ?, NULL)",
                (library.private, person),
            )
        ],
    )

    client.post(f"/api/usernames/{username}/person", json={"person_id": person})
    client.delete(f"/api/usernames/{username}/person")

    left = read(
        db_path(client),
        "SELECT asset_id, source FROM asset_people WHERE person_id = ?",
        (person,),
    )
    assert [(one["asset_id"], one["source"]) for one in left] == [(library.private, None)]


def test_a_blank_username_is_not_a_question_anybody_can_answer(
    client: TestClient, library: Library
) -> None:
    """A download that could not read who posted something files it under an empty username.

    A library has one of those per site, holding everything anonymous. Nobody can say who "" is,
    so a queue holding them is a queue with permanent rows, and a queue that can never be emptied
    is one people stop opening. It is still listed and still has a page; it is just not waiting.
    """
    sign_in(client)
    named = make_username(client, "SomeSite", "esmewrenfield")
    write(
        db_path(client),
        [
            (
                "INSERT INTO usernames (id, site_id, name, created_at)"
                " VALUES (?, (SELECT id FROM sites WHERE name = ?), '', 0)",
                (NEVER_EXISTED, "SomeSite"),
            )
        ],
    )
    for username in (named, NEVER_EXISTED):
        _attach_file(db_path(client), library.shared, username)

    waiting = client.get("/api/usernames", params={"unattached": "true"}).json()
    assert [one["username"] for one in waiting["items"]] == ["esmewrenfield"]

    # Still listed, and still reachable, when nothing is being asked about.
    everything = client.get("/api/usernames").json()
    assert sorted(one["username"] for one in everything["items"]) == ["", "esmewrenfield"]


def test_the_files_wall_shows_what_was_posted_under_this_username_only(
    client: TestClient, library: Library
) -> None:
    """The filtering the username's page draws with. Not a search word (see the leaf's comment)."""
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.shared, username)

    page = client.get("/api/assets", params={"username": username}).json()
    assert [one["id"] for one in page["items"]] == [library.shared]
    assert page["total"] == 1


# --- the editable half of a username --------------------------------------------------------------


def test_a_username_is_joined_and_unjoined_through_its_own_edit(
    client: TestClient, library: Library
) -> None:
    """`person_id` is the one field on this write that is not a plain replace: sending null detaches
    and omitting it leaves whoever holds the username alone."""
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.shared, username)
    person = make_person(client, "Neve")

    joined = client.put(f"/api/usernames/{username}", json={"person_id": person})
    assert joined.status_code == 200, joined.text
    assert joined.json()["person_id"] == person

    named = client.put(f"/api/usernames/{username}", json={"display_name": "Neve Arb"})
    assert named.json()["person_id"] == person, "a write that says nothing leaves the join alone"
    assert named.json()["display_name"] == "Neve Arb"

    detached = client.put(f"/api/usernames/{username}", json={"person_id": None})
    assert detached.json()["person_id"] is None


def test_editing_only_a_usernames_address_asks_for_no_reindex(
    client: TestClient, library: Library
) -> None:
    """A write that touches no indexed word touches no index.

    The display name is indexed beside the username, and a join or detach moves a person's files in
    and out of the same set, so both of those name their files and have them rewritten. An address
    is in no column the index gathers, so a write of only that has nothing to refresh. It is worth a
    guard rather than a rebuild "just in case": the set here is every file under the username, which
    on a busy username is thousands of rows rewritten inside the request for a field nothing reads.
    """
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.shared, username)
    told: list[list[str]] = []

    class _Recorder:
        """The reindex seam, watched. Only the group call is reachable from this route."""

        async def touched(self, asset_id: str) -> None: ...

        async def touched_many(self, asset_ids: list[str]) -> None:
            told.append(list(asset_ids))

        async def queue_many(self, asset_ids: list[str]) -> None:  # pragma: no cover (no rename)
            await self.touched_many(asset_ids)

        async def renamed(self) -> None: ...

    client.app.dependency_overrides[reindexer] = _Recorder  # type: ignore[attr-defined]
    try:
        written = client.put(
            f"/api/usernames/{username}", json={"url": "https://example.test/esmewrenfield"}
        )
        assert written.status_code == 200, written.text
        assert written.json()["url"] == "https://example.test/esmewrenfield"
        assert told == [], "an address change rewrote the search rows of every file on the username"

        # The control, so the assertion above is about this write rather than about an override
        # that was never going to be called: a display name IS indexed, and does name its files.
        named = client.put(f"/api/usernames/{username}", json={"display_name": "Neve Arb"})
        assert named.status_code == 200, named.text
        assert told == [[library.shared]]
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_joining_a_username_to_somebody_who_is_not_there_is_refused(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.shared, username)

    refused = client.put(f"/api/usernames/{username}", json={"person_id": NEVER_EXISTED})

    assert refused.status_code == 404


def test_asking_for_both_a_person_and_a_new_one_is_refused_rather_than_resolved(
    client: TestClient, library: Library
) -> None:
    """The branch with the larger consequence has to be asked for on purpose: one of the two makes
    a row in People, and a single argument meaning either would make the creating one an accident."""
    sign_in(client)
    username = make_username(client, "SomeSite", "esmewrenfield")
    _attach_file(db_path(client), library.shared, username)
    person = make_person(client, "Neve")

    both = client.post(
        f"/api/usernames/{username}/person",
        json={"person_id": person, "new_person_name": "Somebody Else"},
    )
    neither = client.post(f"/api/usernames/{username}/person", json={})

    assert both.status_code == 400
    assert neither.status_code == 400


def test_taking_the_join_back_off_a_username_that_has_none_is_a_404(
    client: TestClient, library: Library
) -> None:
    sign_in(client)

    assert client.delete(f"/api/usernames/{NEVER_EXISTED}/person").status_code == 404


def test_a_username_that_is_a_record_id_never_becomes_an_alias(client: TestClient) -> None:
    """A stash-box reference page is addressed by a UUID, and the last segment becomes the username.

    Without the guard, bare UUIDs end up as aliases, each matching one of that person's own
    usernames. The join still happens (the username is that person's), and only the alias is
    refused, because a record id is not a name and nobody will ever be found by typing one.
    """
    sign_in(client)
    person = make_person(client, "Neve Arbor")
    username = make_username(client, "StashDB", "5e1f0c2a-7b3d-4e8f-9a6c-1d2b3c4e5f60")

    joined = client.post(f"/api/usernames/{username}/person", json={"person_id": person})

    assert joined.status_code == 200, joined.text
    assert client.get(f"/api/people/{person}/aliases").json() == []
    # And the join itself landed: what is refused is the spelling, not the decision.
    assert (
        read(db_path(client), "SELECT person_id FROM usernames WHERE id = ?", (username,))[0][
            "person_id"
        ]
        == person
    )


def test_a_username_that_merely_contains_a_record_id_is_still_an_alias(client: TestClient) -> None:
    """The rule is anchored at both ends, and this is the case that says why it has to be.

    A username nobody would call an id (it has a word on the end of it) is a spelling somebody
    could really type, and a looser rule would take it away.
    """
    sign_in(client)
    person = make_person(client, "Neve Arbor")
    username = make_username(client, "SomeSite", "5e1f0c2a-7b3d-4e8f-9a6c-1d2b3c4e5f60-official")

    client.post(f"/api/usernames/{username}/person", json={"person_id": person})

    assert [one["alias"] for one in client.get(f"/api/people/{person}/aliases").json()] == [
        "5e1f0c2a-7b3d-4e8f-9a6c-1d2b3c4e5f60-official"
    ]


def test_a_username_listed_under_one_person_or_site_carries_its_number_and_its_site_s_mark(
    client: TestClient, library: Library
) -> None:
    """A username has no page of its own: it is drawn on a person's Sites tab and under a
    person's card on a site's People tab, which ask this list filtered to that person or that site.
    So what a page would draw beside it (the site's own number, how it was learned, and the
    site's logo) rides on that list, and on the waiting list the Organize queue draws, and on no
    other."""
    sign_in(client)
    username = make_username(client, "Instagram", "esmewrenfield")
    person = make_person(client, "Neve Arbor")
    _attach_file(db_path(client), library.shared, username)
    write(
        db_path(client),
        [
            (
                "UPDATE usernames SET person_id = ?, url = 'https://www.instagram.com/esmewrenfield',"
                " number = '4815162342', number_via = 'metadata',"
                " number_agreed = 3 WHERE id = ?",
                (person, username),
            )
        ],
    )
    site = str(
        read(db_path(client), "SELECT site_id FROM usernames WHERE id = ?", (username,))[0][
            "site_id"
        ]
    )

    for narrowed in ({"person_id": person}, {"site_id": site}):
        one = client.get("/api/usernames", params=narrowed).json()["items"][0]
        assert one["username"] == "esmewrenfield"
        assert (one["site_id"], one["site_name"]) == (site, "Instagram")
        assert one["asset_count"] == 1
        assert one["url"] == "https://www.instagram.com/esmewrenfield"
        assert (one["number"], one["number_via"]) == ("4815162342", "metadata")
        assert "3 of this username's pictures" in one["number_said"]
        assert isinstance(one["site_icon"], str) and one["site_icon"]

    # The waiting list draws them too: the Organize queue's card wears the Site's logo and the
    # ID. Taken off the person, so it is waiting.
    write(db_path(client), [("UPDATE usernames SET person_id = NULL WHERE id = ?", (username,))])
    waiting = client.get("/api/usernames", params={"unattached": "true"}).json()["items"][0]
    assert waiting["username"] == "esmewrenfield"
    assert waiting["number"] == "4815162342"
    assert isinstance(waiting["site_icon"], str) and waiting["site_icon"]

    # The wall that lists every username does not pay for any of it.
    plain = client.get("/api/usernames").json()["items"][0]
    assert (plain["number"], plain["number_via"], plain["site_icon"]) == (None,) * 3


def test_a_person_with_no_usernames_lists_none_and_asks_for_no_facts(
    client: TestClient,
) -> None:
    """A person's Sites tab with nothing on it: an empty page, and the facts beside each username
    are asked for no username at all."""
    sign_in(client)
    person = make_person(client, "Neve Arbor")

    page = client.get("/api/usernames", params={"person_id": person})

    assert page.status_code == 200, page.text
    assert page.json()["items"] == []


def test_a_username_gone_between_the_page_and_its_facts_is_drawn_as_the_page_read_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The facts are a second read over the page's usernames. One deleted between the two is drawn
    as the page found it, with no number, rather than failing the tab."""
    from sift.slices.people.service import PeopleService

    sign_in(client)
    username = make_username(client, "Instagram", "esmewrenfield")
    person = make_person(client, "Neve Arbor")
    write(
        db_path(client), [("UPDATE usernames SET person_id = ? WHERE id = ?", (person, username))]
    )
    real = PeopleService.username_facts

    async def gone_meanwhile(self: PeopleService, username_ids: list[str]) -> object:
        await self._db.execute("DELETE FROM usernames WHERE id = ?", (username,))
        return await real(self, username_ids)

    monkeypatch.setattr(PeopleService, "username_facts", gone_meanwhile)

    items = client.get("/api/usernames", params={"person_id": person}).json()["items"]

    assert [one["username"] for one in items] == ["esmewrenfield"]
    assert items[0]["number"] is None


def test_a_typed_number_is_read_back_with_where_it_came_from_and_is_not_replaced_unasked(
    client: TestClient,
) -> None:
    """The write of the Site's number (its ID on screen) fills a blank, and refuses to replace
    one unless replacing was asked for.

    The answer is the row read back rather than the request echoed, so the number arrives with its
    provenance: the screen marks a typed number as one it may trust by `number_via`, not by
    reading the sentence."""
    sign_in(client)
    username = make_username(client, "Instagram", "esmewrenfield")

    typed = client.put(f"/api/usernames/{username}", json={"number": "4242"})
    assert typed.status_code == 200, typed.text
    body = typed.json()
    assert (body["number"], body["number_via"]) == ("4242", "typed")
    assert body["number_said"] == "You typed this ID."

    again = client.put(f"/api/usernames/{username}", json={"number": "9999"})
    assert again.status_code == 409
    assert again.json()["detail"] == "This username already has a different ID."
    # Typing the number it already has is not a disagreement.
    same = client.put(f"/api/usernames/{username}", json={"number": "4242"})
    assert same.status_code == 200, same.text


def test_a_number_is_corrected_when_replacing_is_asked_for_and_is_recorded_as_typed(
    client: TestClient,
) -> None:
    """The sheet's Edit, behind its confirmation: the number is replaced and becomes a TYPED one,
    whatever the row said before: a person has now vouched for it."""
    sign_in(client)
    username = make_username(client, "Instagram", "esmewrenfield")
    write(
        db_path(client),
        [
            (
                "UPDATE usernames SET number = '4815162342', number_via = 'metadata',"
                " number_agreed = 3 WHERE id = ?",
                (username,),
            )
        ],
    )

    fixed = client.put(
        f"/api/usernames/{username}", json={"number": "31415926", "replace_number": True}
    )

    assert fixed.status_code == 200, fixed.text
    body = fixed.json()
    assert (body["number"], body["number_via"], body["number_said"]) == (
        "31415926",
        "typed",
        "You typed this ID.",
    )
    row = read(db_path(client), "SELECT number_agreed FROM usernames WHERE id = ?", (username,))
    assert row[0]["number_agreed"] is None


def test_the_page_link_is_checked_by_the_write_itself() -> None:
    """The model the route reads, on its own: a page is a web address or nothing, and a cleared
    display name is no display name."""
    from pydantic import ValidationError

    from sift.slices.people.models import UsernameWrite

    with pytest.raises(ValidationError):
        UsernameWrite(url="javascript:alert(1)")
    assert UsernameWrite(url=" https://www.instagram.com/esmewrenfield ").url == (
        "https://www.instagram.com/esmewrenfield"
    )
    assert UsernameWrite(url="   ").url is None
    assert UsernameWrite(display_name="  ").display_name is None
    # And sent as null (the sheet clearing both), they stay nothing rather than failing.
    cleared = UsernameWrite.model_validate({"url": None, "display_name": None})
    assert (cleared.url, cleared.display_name) == (None, None)


def test_the_display_name_and_the_page_link_are_edited_and_the_page_is_checked(
    client: TestClient,
) -> None:
    """The sheet edits both. The page is drawn as a link, so `javascript:` is refused where it is
    written; a cleared field is no value rather than an empty one."""
    sign_in(client)
    username = make_username(client, "Instagram", "esmewrenfield")

    refused = client.put(f"/api/usernames/{username}", json={"url": "javascript:alert(1)"})
    assert refused.status_code == 422, refused.text

    saved = client.put(
        f"/api/usernames/{username}",
        json={"display_name": " Neve A. ", "url": " https://www.instagram.com/esmewrenfield "},
    )
    assert saved.status_code == 200, saved.text
    assert (saved.json()["display_name"], saved.json()["url"]) == (
        "Neve A.",
        "https://www.instagram.com/esmewrenfield",
    )

    cleared = client.put(f"/api/usernames/{username}", json={"display_name": "  ", "url": ""})
    assert (cleared.json()["display_name"], cleared.json()["url"]) == (None, None)


@pytest.mark.parametrize("replacing", [False, True])
def test_an_id_another_username_on_the_site_has_is_a_409_naming_it_never_a_500(
    client: TestClient, replacing: bool
) -> None:
    """Two usernames on one Site cannot share its number (`ux_usernames_number`), and very often the
    two are one person who renamed. Left to the uniqueness rule it would be a 500; it is a 409
    that names the other username, onto a blank and over a number alike, and
    writes nothing else the request carried."""
    sign_in(client)
    old_name = make_username(client, "Instagram", "wrenly")
    new_name = make_username(client, "Instagram", "wrenly_official")
    elsewhere = make_username(client, "Fansly", "wrenly_fansly")
    statements: list[tuple[str, tuple[object, ...]]] = [
        ("UPDATE usernames SET number = '31415926' WHERE id = ?", (old_name,)),
        ("UPDATE usernames SET number = '31415926' WHERE id = ?", (elsewhere,)),
    ]
    if replacing:
        statements.append(("UPDATE usernames SET number = '11' WHERE id = ?", (new_name,)))
    write(db_path(client), statements)

    clash = client.put(
        f"/api/usernames/{new_name}",
        json={"number": "31415926", "replace_number": replacing, "display_name": "Wren"},
    )

    assert clash.status_code == 409, clash.text
    assert clash.json()["detail"] == (
        "wrenly already has this Instagram ID. These may be the same person, renamed."
    )
    row = read(
        db_path(client), "SELECT number, display_name FROM usernames WHERE id = ?", (new_name,)
    )
    assert (row[0]["number"], row[0]["display_name"]) == ("11" if replacing else None, None)


def test_the_sites_wall_narrows_by_usernames(client: TestClient, library: Library) -> None:
    """`?usernames=yes` keeps the sites somebody posts on.

    Proved against a wall where the narrowing CHANGES the answer: an ignored parameter would hand
    back both sites and pass nothing.
    """
    sign_in(client)
    make_username(client, "SomeSite", "esmewrenfield")
    assert client.post("/api/sites", json={"name": "BareSite"}).status_code in (200, 201)

    def names(params: dict[str, str]) -> list[str]:
        page = client.get("/api/sites", params={**params, "limit": "200"}).json()
        return sorted(str(one["name"]) for one in page["items"])

    assert {"SomeSite", "BareSite"} <= set(names({}))
    assert "BareSite" not in names({"usernames": "yes"})
    assert "SomeSite" in names({"usernames": "yes"})


# --- the waiting queue keeps its page in its address ---------------------------------------------


def _waiting_queue(client: TestClient, library: Library) -> tuple[list[str], str]:
    """Three usernames waiting, one file each, and one joined to somebody that sorts before them.

    One file each, so the queue's order is the spelling's: the three waiting are at 0, 1 and 2. The
    joined one is on the ordinary wall at 0 and on the queue nowhere, which is what proves the
    position is taken in the queue and not in the wall.
    """
    sign_in(client)
    waiting = [make_username(client, "SomeSite", name) for name in ("brynly", "cassly", "dellow")]
    joined = make_username(client, "SomeSite", "adelwren")
    for username in (*waiting, joined):
        _attach_file(db_path(client), library.shared, username)
    person = make_person(client, "Neve Arbor")
    assert client.post(f"/api/usernames/{joined}/person", json={"person_id": person}).is_success
    return waiting, person


def _queue_page(client: TestClient, **params: str) -> tuple[int, list[str]]:
    page = client.get("/api/usernames", params={"unattached": "true", "limit": "1", **params})
    assert page.status_code == 200
    body = page.json()
    return body["offset"], [one["username"] for one in body["items"]]


def test_the_waiting_queue_opens_at_the_username_its_address_names(
    client: TestClient, library: Library
) -> None:
    """Page two of Usernames Waiting is left for a sheet or the people picker, and come back to.

    The address names the first card of the page (`from`), and the queue opens at that card's place
    IN THE QUEUE: 1, not 2, although the ordinary wall has the joined username in front of it.
    """
    waiting, _person = _waiting_queue(client, library)

    assert _queue_page(client, **{"from": waiting[1], "near": "1"}) == (1, ["cassly"])
    # Where the page was is only the fallback: the row itself wins when it is still waiting.
    assert _queue_page(client, **{"from": waiting[2], "near": "0"}) == (2, ["dellow"])


def test_the_waiting_queue_opens_where_the_page_was_once_its_first_username_is_answered(
    client: TestClient, library: Library
) -> None:
    """Saying who the page's first username is takes it off the queue, the ordinary way the row
    the address names goes. The cards after it close up, so the same place is the same page."""
    waiting, person = _waiting_queue(client, library)
    assert client.post(f"/api/usernames/{waiting[1]}/person", json={"person_id": person}).is_success

    assert _queue_page(client, **{"from": waiting[1], "near": "1"}) == (1, ["dellow"])


def test_the_waiting_queue_opens_at_the_top_when_its_address_names_neither(
    client: TestClient, library: Library
) -> None:
    """A row gone with no `near` beside it, or one that never existed: the top, and the same answer
    for both: nothing is learned by trying ids."""
    waiting, person = _waiting_queue(client, library)
    assert client.post(f"/api/usernames/{waiting[1]}/person", json={"person_id": person}).is_success

    assert _queue_page(client, **{"from": waiting[1]}) == (0, ["brynly"])
    assert _queue_page(client, **{"from": NEVER_EXISTED}) == (0, ["brynly"])
    # And a page asked for by offset says where it began, as the anchored one does.
    assert _queue_page(client, offset="1") == (1, ["dellow"])
