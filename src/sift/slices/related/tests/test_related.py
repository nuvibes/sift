# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers on an entity page's tab strip.

Each number must be right (the lopsided fixture gives Jane two files and Rick one) and must be the
number the wall will draw, so the tests fetch the wall itself and compare.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel import db as db_module
from sift.kernel.records import FoundRecord, Subject
from sift.slices.related.router import _files_of
from sift.slices.related.tests.conftest import World, db_path, sign_in, write
from sift.slices.stash_boxes.adapter import as_json

_HIDE_TAG = """
INSERT INTO tag_user_state (tag_id, user_id, hidden, updated_at) VALUES (?, ?, 1, 0)
ON CONFLICT(tag_id, user_id) DO UPDATE SET hidden = 1
"""


def counts(client: TestClient, kind: str, entity_id: str) -> dict[str, object]:
    answer = client.get(f"/api/related/{kind}/{entity_id}")
    assert answer.status_code == 200, answer.text
    seen: dict[str, object] = answer.json()
    return seen


def test_a_person_page_gets_every_number_in_one_request(client: TestClient, world: World) -> None:
    sign_in(client)
    seen = counts(client, "person", world.jane)
    # Nor is the disagreement mark: nothing here is linked to a stash-box, so it is nought.
    assert seen.pop("disagreements") == 0
    assert seen.pop("disagreement_boxes") == []
    assert seen.pop("files_bytes") is not None
    # Jane is on both files; the video has the site, collection and Loop, the photograph the set.
    assert seen == {
        "files": 2,
        "photo_sets": 1,
        "loops": 1,
        # Only a site can be part of another site: absent, not nought.
        "sites_within": None,
        "tags_within": None,
        "tags": 2,
        "people": 1,
        "sites": 1,
        "collections": 1,
        "songs": 0,
    }


def test_the_numbers_are_the_ones_the_walls_will_draw(client: TestClient, world: World) -> None:
    """Each number is compared with the wall it stands for, not with a literal."""
    sign_in(client)
    seen = counts(client, "person", world.jane)
    for wall, path in (
        ("photo_sets", "/api/photo-sets"),
        ("loops", "/api/loops"),
        ("tags", "/api/tags"),
        ("sites", "/api/sites"),
        ("collections", "/api/collections"),
    ):
        page = client.get(path, params={"person": world.jane, "limit": 60}).json()
        assert seen[wall] == page["total"], wall
        assert seen[wall] == len(page["items"]), wall


def test_the_strip_never_walks_a_thread(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """History's number is its own request's: the strip answers none and reads no thread."""
    heard: list[str] = []
    judged = db_module._judged

    @contextmanager
    def counted(stage: str, statement: Any, *rest: Any, **options: Any) -> Iterator[Any]:
        heard.append(db_module.statement_name(statement))
        with judged(stage, statement, *rest, **options) as timing:
            yield timing

    monkeypatch.setattr(db_module, "_judged", counted)
    sign_in(client)
    for kind, one in (("person", world.jane), ("tag", world.portrait), ("site", world.site)):
        heard.clear()
        assert "history" not in counts(client, kind, one), kind
        # Every thread first asks which feature tables exist; no wall does.
        assert not [name for name in heard if "sqlite_master" in name], kind


def test_seen_with_does_not_count_the_person_whose_page_it_is(
    client: TestClient, world: World
) -> None:
    """Seen with drops the page's own person: Rick and Jane are both one, for different reasons."""
    sign_in(client)
    assert counts(client, "person", world.jane)["people"] == 1
    assert counts(client, "person", world.rick)["people"] == 1

    wall = client.get("/api/people", params={"with_person": world.jane}).json()
    assert wall["total"] == 1
    assert [one["name"] for one in wall["items"]] == ["Rick"]


def test_a_tag_page_asks_for_what_a_tag_page_shows(client: TestClient, world: World) -> None:
    """A tag page answers only its own tabs; an absent tab is None, an empty one nought."""
    sign_in(client)
    seen = counts(client, "tag", world.portrait)
    assert seen["tags"] is None
    assert seen["files"] == 1
    assert seen["people"] == 2
    assert seen["loops"] == 1
    assert seen["photo_sets"] == 0


def test_a_collection_has_no_photo_sets_or_collections_tab(
    client: TestClient, world: World
) -> None:
    """A collection has a Loops tab but no Photo Sets or Collections tab."""
    sign_in(client)
    seen = counts(client, "collection", world.collection)
    assert seen["loops"] == 1
    assert seen["photo_sets"] is None
    assert seen["collections"] is None
    assert seen["files"] == 1
    assert seen["people"] == 2


def test_a_photo_set_page_counts_over_its_own_pictures(client: TestClient, world: World) -> None:
    sign_in(client)
    seen = counts(client, "photo_set", world.photo_set)
    # No stash-box knows a Photo Set, so there is no mark.
    assert seen.pop("disagreements") is None
    assert seen.pop("disagreement_boxes") == []
    assert seen.pop("files_bytes") is not None
    assert seen == {
        "files": 1,
        "photo_sets": None,
        "loops": None,
        "sites_within": None,
        "tags_within": None,
        "tags": 1,
        "people": 1,
        "sites": 0,
        "collections": None,
        "songs": None,
    }


def test_every_number_is_scoped_to_what_this_account_may_see(
    client: TestClient, world: World
) -> None:
    """Every number is scoped to what this account may see, so no count publishes what is hidden."""
    admin = sign_in(client)
    assert counts(client, "person", world.jane)["tags"] == 2

    write(db_path_of(client), [(_HIDE_TAG, (world.portrait, admin))])
    assert counts(client, "person", world.jane)["tags"] == 1
    assert client.get("/api/tags", params={"person": world.jane}).json()["total"] == 1


def test_an_unknown_kind_is_refused_rather_than_answered_emptily(client: TestClient) -> None:
    """An unknown kind is a 422, not an empty strip."""
    sign_in(client)
    assert client.get("/api/related/sandwich/whatever").status_code == 422


def test_an_id_naming_nothing_is_every_count_at_nought(client: TestClient, world: World) -> None:
    """An id naming nothing is every count at nought, the same as one this account may not see."""
    sign_in(client)
    seen = counts(client, "person", "01JQZZZZZZZZZZZZZZZZZZZZZZ")
    assert seen["files"] == 0
    assert seen["tags"] == 0
    assert seen["people"] == 0
    # Every kind of page: a tag's People tab is counted off the people wall, not "seen with".
    assert counts(client, "tag", "01JQZZZZZZZZZZZZZZZZZZZZZZ")["people"] == 0


def test_a_subject_with_no_files_count_is_refused() -> None:
    """A kind added to the strip without its Files number fails loudly, never as a nought."""
    with pytest.raises(TypeError, match="no files count"):
        _files_of(object())


def test_a_guest_shown_nothing_sees_nothing(client: TestClient, world: World) -> None:
    sign_in(client, role="guest", who="two")
    seen = counts(client, "person", world.jane)
    assert seen["files"] == 0
    assert seen["tags"] == 0
    assert seen["collections"] == 0


def db_path_of(client: TestClient):  # type: ignore[no-untyped-def]
    return client.app.state.database.path  # type: ignore[attr-defined]


_MAKE_NETWORK = "INSERT INTO sites (id, name) VALUES (?, ?)"
_PART_OF = "UPDATE sites SET parent_id = ? WHERE id = ?"

#: An id `_is_object_id` accepts, so the network needs no request to create it.
_NETWORK = "01JQZZZZZZZZZZZZZZZZZZZZ01"


def _a_network(client: TestClient, world: World, name: str = "Northlight Group") -> str:
    """One site made the parent of the fixture's site: a network."""
    write(
        db_path_of(client),
        [(_MAKE_NETWORK, (_NETWORK, name)), (_PART_OF, (_NETWORK, world.site))],
    )
    return _NETWORK


def test_a_network_reaches_what_its_labels_published(client: TestClient, world: World) -> None:
    """A network's page counts what is filed under the labels that are part of it."""
    sign_in(client)
    network = _a_network(client, world)

    seen = counts(client, "site", network)
    assert seen["files"] == 1
    assert seen["people"] == 2
    assert seen["tags"] == 1
    # A file keeps the site it was filed under.
    assert counts(client, "site", world.site)["files"] == 1


def test_a_network_lists_the_sites_that_are_part_of_it(client: TestClient, world: World) -> None:
    """A network's Sites tab and its number come from one listing."""
    sign_in(client)
    network = _a_network(client, world)

    page = client.get("/api/sites", params={"parent": network}).json()
    assert [one["id"] for one in page["items"]] == [world.site]
    assert page["total"] == 1
    assert counts(client, "site", network)["sites_within"] == 1

    # A site nothing is part of has the tab, at nought.
    assert counts(client, "site", world.site)["sites_within"] == 0


def test_the_card_and_the_tab_report_the_same_number(client: TestClient, world: World) -> None:
    """A site card's `asset_count` is the rolled-up count the tab shows."""
    sign_in(client)
    network = _a_network(client, world)

    row = next(
        one
        for one in client.get("/api/sites", params={"limit": 50}).json()["items"]
        if one["id"] == network
    )
    assert row["asset_count"] == counts(client, "site", network)["files"]
    assert row["people_count"] == counts(client, "site", network)["people"]


def test_a_parent_loop_terminates_rather_than_recursing(client: TestClient, world: World) -> None:
    """A parent cycle terminates: both expansions use UNION, not UNION ALL."""
    sign_in(client)
    network = _a_network(client, world)
    # Two sites, each the parent of the other.
    write(db_path_of(client), [(_PART_OF, (world.site, network))])

    assert counts(client, "site", network)["files"] == 1
    assert client.get("/api/sites", params={"limit": 50}).status_code == 200
    assert client.get("/api/sites", params={"parent": network}).json()["total"] == 1


def test_a_record_with_a_disagreement_wears_the_number_that_says_so(
    client: TestClient, world: World
) -> None:
    """The disagreement mark matches the panel's own route, built from one rule."""
    sign_in(client)
    _link_a_box_that_disagrees(client, world)

    seen = counts(client, "person", world.jane)
    waiting = client.get(f"/api/stash-boxes/disagreements/person/{world.jane}")
    assert waiting.status_code == 200, waiting.text

    assert seen["disagreements"] == len(waiting.json()["disagreements"])
    assert seen["disagreements"] == 1


def test_nobody_but_an_admin_is_told_a_record_has_questions_waiting(
    client: TestClient, world: World
) -> None:
    """Only an admin is told a record has questions waiting; for anyone else it is None."""
    sign_in(client)
    _link_a_box_that_disagrees(client, world)

    sign_in(client, role="guest", who="two")
    assert counts(client, "person", world.jane)["disagreements"] is None


def _link_a_box_that_disagrees(client: TestClient, world: World) -> None:
    """One box linked to Jane, holding a birthdate the library lacks, written as kept at linking."""
    added = client.post(
        "/api/stash-boxes",
        json={"name": "ExampleDB", "endpoint": "https://example.test/graphql"},
    )
    assert added.status_code == 201, added.text
    box = str(added.json()["id"])

    held = client.put(
        f"/api/people/{world.jane}",
        json={"name": "Jane", "record": {"birth_date": "1990-01-01"}},
    )
    assert held.status_code == 200, held.text

    record = FoundRecord(
        source_id=box,
        remote_id="r1",
        subject=Subject.PERSON,
        name="Jane",
        fields={"birth_date": "1991-02-02"},
        confidence=1.0,
    )
    write(
        db_path(client),
        [
            (
                "INSERT INTO person_stash_box_links"
                " (person_id, box_id, remote_id, payload, fetched_at)"
                " VALUES (?, ?, 'r1', ?, 0)",
                (world.jane, box, as_json([record])),
            )
        ],
    )


def _people_wall(client: TestClient, **params: str) -> dict[str, int]:
    """One page of the People wall, as a name-to-number map of what each card prints."""
    answer = client.get("/api/people", params=params)
    assert answer.status_code == 200, answer.text
    return {one["name"]: int(one["asset_count"]) for one in answer.json()["items"]}


def _files_reaching(client: TestClient, *names: str) -> int:
    """How many files this viewer may see with all of these people, asked as the card's press asks
    (see `relatedHref` in the client)."""
    answer = client.get("/api/assets", params=[("people", name) for name in names])
    assert answer.status_code == 200, answer.text
    return int(answer.json()["total"])


def test_a_seen_with_card_counts_the_wall_its_press_opens(client: TestClient, world: World) -> None:
    """Jane's card on Rick's Seen with tab counts the wall it opens: the two together, one."""
    sign_in(client)
    assert _files_reaching(client, "Jane", "Rick") == 1

    cards = _people_wall(client, with_person=world.rick, count="narrowed")
    assert cards == {"Jane": 1}


def test_the_people_wall_still_counts_the_whole(client: TestClient, world: World) -> None:
    """The People wall counts each person's whole, asked both by default and with `count=whole`.
    An unfiltered wall's two tallies agree by construction; the tag test below is the one that
    tells them apart."""
    sign_in(client)
    assert _files_reaching(client, "Jane") == 2

    assert _people_wall(client) == {"Jane": 2, "Rick": 1}
    assert _people_wall(client, count="whole") == {"Jane": 2, "Rick": 1}


def test_a_tag_s_people_tab_counts_that_tag_s_files(client: TestClient, world: World) -> None:
    """On a tag's People tab Jane's card counts that tag's files: one, where her wall says two."""
    sign_in(client)
    assert _people_wall(client, tag=world.portrait, count="narrowed") == {"Jane": 1, "Rick": 1}
    assert _people_wall(client, tag=world.portrait) == {"Jane": 2, "Rick": 1}


def test_which_tally_a_card_prints_moves_no_row_and_no_total(
    client: TestClient, world: World
) -> None:
    """Which tally a card prints moves no row and no total."""
    sign_in(client)
    narrowed = client.get("/api/people", params={"tag": world.portrait, "count": "narrowed"})
    whole = client.get("/api/people", params={"tag": world.portrait, "count": "whole"})
    assert narrowed.json()["total"] == whole.json()["total"] == 2
    assert [one["id"] for one in narrowed.json()["items"]] == [
        one["id"] for one in whole.json()["items"]
    ]


def test_a_tally_nobody_offers_is_refused(client: TestClient, world: World) -> None:
    """An unknown tally is refused rather than read as one of the two."""
    sign_in(client)
    refused = client.get("/api/people", params={"tag": world.portrait, "count": "biggest"})
    assert refused.status_code == 422, refused.text


def test_a_wall_ordered_by_size_obeys_the_number_its_cards_print(
    client: TestClient, world: World
) -> None:
    """A wall sorted by size follows the number its cards print: on `portrait`'s People tab Jane and
    Rick tie, and the name settles it."""
    sign_in(client)
    both = {"tag": world.portrait, "sort": "smallest"}
    whole = client.get("/api/people", params=both | {"count": "whole"}).json()
    narrowed = client.get("/api/people", params=both | {"count": "narrowed"}).json()
    assert [one["name"] for one in whole["items"]] == ["Rick", "Jane"]
    assert [one["name"] for one in narrowed["items"]] == ["Jane", "Rick"]


# --- the same rule on the three other walls that carry a filter ----------------------------
# Tags, Photo Sets and Sites cards carry the page they were pressed from too, and each listing lives
# in its own slice. Each test adds one row so the two tallies can differ.


def _wall(client: TestClient, where: str, field: str, **params: str) -> dict[str, int]:
    """One page of an entity wall, as a name-to-number map of what each card prints."""
    answer = client.get(where, params=params)
    assert answer.status_code == 200, answer.text
    return {one["name"]: int(one[field]) for one in answer.json()["items"]}


def test_a_person_s_tags_tab_counts_that_person_s_files(client: TestClient, world: World) -> None:
    """`portrait` on both files: Rick's card for it counts one, the plain tags wall two."""
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
                (world.photo, world.portrait),
            )
        ],
    )
    sign_in(client)

    assert _wall(client, "/api/tags", "asset_count", person=world.rick, count="narrowed") == {
        "portrait": 1
    }
    assert _wall(client, "/api/tags", "asset_count", person=world.rick) == {"portrait": 2}
    assert _wall(client, "/api/tags", "asset_count") == {"portrait": 2, "outdoors": 1}


def test_a_person_s_photo_sets_tab_counts_that_person_s_pictures(
    client: TestClient, world: World
) -> None:
    """The video added to the set, so the set is two and Rick reaches one of them."""
    write(
        db_path(client),
        [
            (
                "INSERT INTO photo_set_items (photo_set_id, asset_id, position) VALUES (?, ?, 1)",
                (world.photo_set, world.video),
            )
        ],
    )
    sign_in(client)

    assert _wall(client, "/api/photo-sets", "item_count", person=world.rick, count="narrowed") == {
        "the shoot": 1
    }
    assert _wall(client, "/api/photo-sets", "item_count", person=world.rick) == {"the shoot": 2}


def test_a_person_s_sites_tab_counts_that_person_s_files(client: TestClient, world: World) -> None:
    """The photo filed under the same username, so the site is two and Rick reaches one of them."""
    account = client.get("/api/sites").json()
    assert account  # the wall answers before anything is added to it
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_usernames (asset_id, username_id)"
                " SELECT ?, username_id FROM asset_usernames WHERE asset_id = ?",
                (world.photo, world.video),
            )
        ],
    )
    sign_in(client)

    assert _wall(client, "/api/sites", "asset_count", person=world.rick, count="narrowed") == {
        "example.test": 1
    }
    assert _wall(client, "/api/sites", "asset_count", person=world.rick) == {"example.test": 2}


def test_the_three_other_walls_refuse_a_tally_nobody_offers(
    client: TestClient, world: World
) -> None:
    """Rather than reading it as one of the two, exactly as the People wall does."""
    sign_in(client)
    for where in ("/api/tags", "/api/photo-sets", "/api/sites"):
        refused = client.get(where, params={"person": world.rick, "count": "biggest"})
        assert refused.status_code == 422, f"{where}: {refused.text}"
