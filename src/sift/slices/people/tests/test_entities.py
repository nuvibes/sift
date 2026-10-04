# SPDX-License-Identifier: AGPL-3.0-or-later
"""That Site, Username and Person stay three entities, and that the columns later features read
are present from the first row."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.kernel.access import ENTITY_SORT_KEYS
from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    db_path,
    grant_on,
    make_person,
    read,
    sign_in,
)


def _columns(client: TestClient, table: str) -> set[str]:
    return {str(row["name"]) for row in read(db_path(client), f"PRAGMA table_info({table})")}


def test_the_three_entities_are_three_separate_tables(client: TestClient) -> None:
    """A Site, a Username and a Person are three tables: they vary independently."""
    sign_in(client)

    for table in ("sites", "usernames", "people"):
        assert _columns(client, table), f"{table} is missing"

    assert "person_id" in _columns(client, "usernames"), "a username points at a person"
    assert "site_id" in _columns(client, "usernames"), "a username sits on a site"
    assert "person_id" not in _columns(client, "sites"), "a site is not a person"
    assert "number" not in _columns(client, "people"), "a person is not a username"


def test_the_columns_a_later_phase_needs_are_already_here(client: TestClient) -> None:
    """The columns rich pages, face packs and people-foldering will read are already present, so
    they need no migration and nothing deletes them as unused."""
    sign_in(client)

    people = _columns(client, "people")

    assert {"cover_asset_id", "notes"} <= people

    assert "person_id" in _columns(client, "usernames")


def test_a_site_and_a_person_can_be_shared_and_a_username_cannot(client: TestClient) -> None:
    """Sharing is by Site or Person, never by Username, held by the check constraint."""
    sign_in(client)

    allowed = read(
        db_path(client),
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'acl_grants'",
    )
    definition = str(allowed[0]["sql"])

    # `acl_grants.object_type` names a Site by the wire's word.
    assert "'site'" in definition
    assert "'person'" in definition
    assert "'username'" not in definition


def test_an_unknown_person_is_missing_rather_than_refused(client: TestClient) -> None:
    sign_in(client)

    assert client.get(f"/api/people/{NEVER_EXISTED}/aliases").status_code == 404
    assert client.delete(f"/api/people/{NEVER_EXISTED}").status_code == 404
    assert client.put(f"/api/people/{NEVER_EXISTED}", json={"name": "someone"}).status_code == 404


def test_a_person_may_share_a_name_with_another_person(client: TestClient) -> None:
    """Two people may share a name."""
    sign_in(client)

    assert make_person(client, "Alex") != make_person(client, "Alex")


def test_a_blank_name_is_refused(client: TestClient) -> None:
    """A row of spaces is invisible in a list and impossible to click on."""
    sign_in(client)

    assert client.post("/api/people", json={"name": "   "}).status_code == 422
    assert client.post("/api/sites", json={"name": ""}).status_code == 422


# --- names that could never be searched for --------------------------------------------------


def test_a_person_named_with_a_control_character_is_cleaned(client: TestClient) -> None:
    """A control character in a name is cleaned on the way in, so the person stays findable."""
    sign_in(client)

    made = client.post("/api/people", json={"name": "ja\x00ne"})

    assert made.status_code == 201
    assert made.json()["name"] == "jane"


def test_a_person_named_with_a_double_quote_is_refused(client: TestClient) -> None:
    sign_in(client)

    refused = client.post("/api/people", json={"name": 'jane "jay" doe'})

    assert refused.status_code == 422
    assert "double quote" in refused.text


def test_an_alias_gets_the_same_rule(client: TestClient) -> None:
    """An alias is resolved by the same query path a name is, so it needs the same rule."""
    sign_in(client)
    person_id = make_person(client, "jane")

    cleaned = client.post(f"/api/people/{person_id}/aliases", json={"alias": "j\x00j"})
    refused = client.post(f"/api/people/{person_id}/aliases", json={"alias": 'j"j'})

    assert cleaned.status_code == 201
    assert cleaned.json()["alias"] == "jj"
    assert refused.status_code == 422


def test_an_edit_hands_back_the_restrict_that_is_still_in_force(client: TestClient) -> None:
    """An edit's reply carries the restriction still in force, since the screen replaces its row
    with the reply. Tested as a rename, which needs no unlocked vault."""
    sign_in(client, "admin")
    guest = sign_in(client, "guest")
    sign_in(client, "admin")

    person = make_person(client, "Marked")
    grant_on(client, "person", person, guest, "restrict")

    listed = client.get("/api/people").json()["items"]
    assert [row["restricted"] for row in listed if row["id"] == person] == [True]

    edited = client.put(f"/api/people/{person}", json={"name": "Marked Again", "vault": False})
    assert edited.status_code == 200, edited.text
    assert edited.json()["restricted"] is True, "the edit reply forgot a restrict still in force"


# --- notes ------------------------------------------------------------------------------------
#
# An admin's notes about somebody appear only on the person's own route: never on a wall's card,
# in search or in a log line.


def _write_notes(client: TestClient, person_id: str, name: str, notes: str):  # type: ignore[no-untyped-def]
    return client.put(f"/api/people/{person_id}", json={"name": name, "notes": notes})


def test_notes_come_back_on_the_persons_own_route(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane Doe")
    _write_notes(client, person, "Jane Doe", "met at the thing")

    answer = client.get(f"/api/people/{person}/notes")

    assert answer.status_code == 200
    assert answer.json() == {"notes": "met at the thing"}


def test_notes_are_not_on_the_card_the_wall_draws(client: TestClient) -> None:
    """Notes are not on the wall's card, which every list and screenshot carries."""
    sign_in(client)
    person = make_person(client, "Jane Doe")
    _write_notes(client, person, "Jane Doe", "met at the thing")

    listed = client.get("/api/people").json()["items"]
    card = next(row for row in listed if row["id"] == person)

    assert card.get("notes") is None
    assert "met at the thing" not in client.get("/api/people").text


def test_a_guest_cannot_write_what_an_admin_wrote_about_somebody(client: TestClient) -> None:
    """A guest may read Details through the scoped read but cannot write them: the person route is
    admin-only."""
    sign_in(client)
    person = make_person(client, "Jane Doe")
    _write_notes(client, person, "Jane Doe", "met at the thing")

    sign_in(client, "guest")

    refused = _write_notes(client, person, "Jane Doe", "something else")

    assert refused.status_code in (401, 403)
    sign_in(client)
    assert client.get(f"/api/people/{person}/notes").json() == {"notes": "met at the thing"}


def test_notes_are_not_searchable(client: TestClient) -> None:
    """A note is not an index. Searching the words in one finds nobody by them."""
    sign_in(client)
    person = make_person(client, "Jane Doe")
    _write_notes(client, person, "Jane Doe", "zzqqx")

    found = client.get("/api/people", params={"prefix": "zzqqx", "anywhere": "true"}).json()

    assert [row["id"] for row in found["items"]] == []


def test_a_note_is_redacted_out_of_any_log_line_that_carries_one() -> None:
    """A note is redacted out of any log line, as a credential is."""
    from sift.kernel.log import REDACTED, redact

    assert redact("met at the thing", "notes") == REDACTED
    assert redact("met at the thing", "person_notes") == REDACTED


# --- the order a wall is in ---------------------------------------------------------------------


def test_an_order_neither_wall_knows_is_refused_rather_than_ignored(client: TestClient) -> None:
    """An unknown order is refused by both walls."""
    sign_in(client)

    assert client.get("/api/people", params={"sort": "nonsense"}).status_code == 422
    assert client.get("/api/sites", params={"sort": "nonsense"}).status_code == 422


def test_both_walls_take_every_order_they_offer(client: TestClient) -> None:
    """Both walls take every order in `ENTITY_SORT_KEYS`."""
    sign_in(client)

    for order in sorted(ENTITY_SORT_KEYS):
        assert client.get("/api/people", params={"sort": order}).status_code == 200, order
        assert client.get("/api/sites", params={"sort": order}).status_code == 200, order


def test_the_universal_orders_actually_order(client: TestClient) -> None:
    """The universal orders really order. Names made in the order Dorothy, Ada, Carol, Betty, so
    alphabetical and creation order disagree."""
    sign_in(client)
    for name in ("Dorothy", "Ada", "Carol", "Betty"):
        make_person(client, name)

    def wall(order: str) -> list[str]:
        answer = client.get("/api/people", params={"sort": order, "limit": 50})
        assert answer.status_code == 200, order
        return [one["name"] for one in answer.json()["items"]]

    assert wall("name_az") == ["Ada", "Betty", "Carol", "Dorothy"]
    assert wall("name_za") == ["Dorothy", "Carol", "Betty", "Ada"]

    # The ULID's leading bits are its creation millisecond.
    assert wall("oldest") == ["Dorothy", "Ada", "Carol", "Betty"]
    assert wall("newest") == ["Betty", "Carol", "Ada", "Dorothy"]


def test_the_size_orders_run_both_ways(client: TestClient) -> None:
    """`largest` and `smallest` both reach the statement."""
    sign_in(client)
    for name in ("Ada", "Betty"):
        make_person(client, name)

    for order in ("largest", "smallest"):
        answer = client.get("/api/people", params={"sort": order, "limit": 50})
        assert answer.status_code == 200, order

    # Every count is zero here, so both tie and fall through to name order.
    largest = [
        one["name"] for one in client.get("/api/people", params={"sort": "largest"}).json()["items"]
    ]
    assert largest == ["Ada", "Betty"]


def test_a_position_is_read_in_the_order_the_page_is_taken_in(client: TestClient) -> None:
    """`?from=` resolves in the same order the page is served in, the new orders included."""
    sign_in(client)
    for name in ("Dorothy", "Ada", "Carol", "Betty"):
        make_person(client, name)

    alphabetical = client.get("/api/people", params={"sort": "name_az", "limit": 50}).json()
    ordered = [one["id"] for one in alphabetical["items"]]

    landed = client.get(
        "/api/people", params={"sort": "name_az", "limit": 2, "from": ordered[2]}
    ).json()

    assert [one["id"] for one in landed["items"]] == ordered[2:4]
    assert landed["offset"] == 2, "the position was read in the alphabetical order, not the default"


# --- opening the wall where somebody left it -------------------------------------------------


def test_the_people_wall_can_be_opened_where_it_was_left(client: TestClient) -> None:
    """`?from=` opens the People wall at a row, which a link can carry; a page number cannot."""
    sign_in(client)
    for name in ("Ada", "Betty", "Carol", "Dorothy"):
        make_person(client, name)

    every = client.get("/api/people", params={"limit": 50}).json()
    ordered = [one["id"] for one in every["items"]]
    assert len(ordered) == 4

    anchored = client.get("/api/people", params={"limit": 2, "from": ordered[2]}).json()

    assert [one["id"] for one in anchored["items"]] == ordered[2:4]
    assert anchored["offset"] == 2, "the answer says where it landed, so a pager can say so too"
    assert anchored["total"] == every["total"], "anchoring narrows nothing"


def test_the_people_wall_resolves_an_anchor_inside_the_narrowing_it_was_given(
    client: TestClient,
) -> None:
    """An anchor is resolved inside the same prefix filter as the page."""
    sign_in(client)
    for name in ("Ada", "Betty", "Bella", "Carol"):
        make_person(client, name)

    narrowed = client.get("/api/people", params={"limit": 50, "prefix": "B"}).json()
    ordered = [one["id"] for one in narrowed["items"]]
    assert len(ordered) == 2, "needs two matches, or an offset of zero proves nothing"

    anchored = client.get(
        "/api/people", params={"limit": 50, "prefix": "B", "from": ordered[1]}
    ).json()

    assert anchored["offset"] == 1
    assert [one["id"] for one in anchored["items"]] == [ordered[1]]


def test_a_people_anchor_that_is_gone_opens_the_top_rather_than_refusing(
    client: TestClient,
) -> None:
    """A gone, filtered-out or unseeable anchor opens the top: telling them apart would leak."""
    sign_in(client)
    make_person(client, "Ada")

    top = client.get("/api/people", params={"limit": 5}).json()
    stale = client.get("/api/people", params={"limit": 5, "from": NEVER_EXISTED}).json()
    nonsense = client.get("/api/people", params={"limit": 5, "from": "not-an-id"}).json()

    assert stale == top
    assert nonsense == top, "an id that is not an id is a stale link, not a bad request"


def test_a_person_being_kept_back_has_no_position_on_the_wall(client: TestClient) -> None:
    """A person in the vault has no position: the anchor gives the top, as an unknown id does."""
    sign_in(client)
    make_person(client, "Ada")
    hidden = make_person(client, "Zelda", vault=True)

    top = client.get("/api/people", params={"limit": 5}).json()
    assert hidden not in [one["id"] for one in top["items"]], "concealed, as the vault intends"

    anchored = client.get("/api/people", params={"limit": 5, "from": hidden}).json()

    assert anchored == top


# --- opening the Sites wall where it was left -------------------------------------------------


def _make_site(client: TestClient, name: str) -> str:
    answer = client.post("/api/sites", json={"name": name})
    assert answer.status_code == 201, answer.text
    return str(answer.json()["id"])


def test_the_sites_wall_can_be_opened_where_it_was_left(client: TestClient) -> None:
    """`?from=` opens the Sites wall at a Site, read in `name_za` as the page is."""
    sign_in(client)
    for name in ("site-one", "site-two", "site-three", "site-four"):
        _make_site(client, name)

    every = client.get("/api/sites", params={"sort": "name_za", "limit": 50}).json()
    ordered = [one["id"] for one in every["items"]]
    assert len(ordered) == 4

    landed = client.get(
        "/api/sites", params={"sort": "name_za", "limit": 2, "from": ordered[2]}
    ).json()

    assert [one["id"] for one in landed["items"]] == ordered[2:4]
    assert landed["offset"] == 2, "the answer says where it landed, so the pager can say so too"
    assert landed["total"] == every["total"], "anchoring narrows nothing"


def test_a_site_anchor_that_resolves_to_nothing_serves_the_first_page(client: TestClient) -> None:
    """A Site anchor resolving to nothing serves the first page, as an unknown id does."""
    sign_in(client)
    for name in ("site-one", "site-two"):
        _make_site(client, name)

    landed = client.get(
        "/api/sites", params={"sort": "name_az", "limit": 2, "from": NEVER_EXISTED}
    ).json()

    assert landed["offset"] == 0
    assert [one["name"] for one in landed["items"]] == ["site-one", "site-two"]
