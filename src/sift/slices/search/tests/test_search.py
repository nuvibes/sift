# SPDX-License-Identifier: AGPL-3.0-or-later
"""Searching a real library over HTTP: what is found, what is counted, what is offered.

Search is the one screen that lets somebody guess, so for a guest three channels must all say
nothing about what they may not see: the results, the count and the dropdown.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Concealment, Role, Viewer
from sift.kernel.ids import new_id
from sift.slices.auth import current_viewer
from sift.slices.search.filters import OFFERED_VALUES
from sift.slices.search.service import MAX_SUGGESTIONS
from sift.slices.search.tests.conftest import (
    EPOCH,
    World,
    db_path,
    found,
    reindex,
    restrict,
    share,
    sign_in,
    write,
)
from sift.slices.search.tests.test_search_memory import (
    remember,
    remembered,
)
from sift.testing.auth import hide_for_caller

pytestmark = [pytest.mark.integration]


# --- free text --------------------------------------------------------------------------------


def test_free_text_finds_a_file_by_its_name(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    ids, total = found(client, q="beach_sunset")
    assert ids == [world.beach]
    assert total == 1


def test_free_text_matches_the_MIDDLE_of_a_name(client: TestClient, world: World) -> None:
    """Free text matches the middle of a name, which is why the index is trigram."""
    sign_in(client, "admin")
    ids, total = found(client, q="sunset")
    assert set(ids) == {world.beach, world.walk}
    assert total == 2


def test_free_text_matches_inside_a_word_not_only_at_a_boundary(
    client: TestClient, world: World
) -> None:
    """`ach_sun` spans the underscore in the middle of one name and exists nowhere else."""
    sign_in(client, "admin")
    ids, _ = found(client, q="ach_sun")
    assert ids == [world.beach]


def test_free_text_searches_the_path_as_well_as_the_name(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    ids, _ = found(client, q="holiday")
    assert ids == [world.beach]


def test_two_words_both_have_to_match(client: TestClient, world: World) -> None:
    """Free text ANDs, in either order, which is what typing two words means."""
    sign_in(client, "admin")
    assert found(client, q="beach sunset")[0] == [world.beach]
    assert found(client, q="sunset beach")[0] == [world.beach]
    assert found(client, q="beach nothinglikethis")[0] == []


# --- the tokens -------------------------------------------------------------------------------


def test_a_tag_token_narrows_to_that_tag(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    assert found(client, q="tags:beach") == ([world.beach], 1)


def test_a_tag_that_does_not_exist_matches_nothing_rather_than_everything(
    client: TestClient, world: World
) -> None:
    """The direction that matters. A name nobody has must narrow, never widen."""
    sign_in(client, "admin")
    assert found(client, q="tags:no-such-tag") == ([], 0)


def test_two_tags_have_to_both_apply(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    assert found(client, q="tags:beach tags:city") == ([], 0)


def test_a_type_token_narrows_by_kind(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    _, videos = found(client, q="type:video")
    assert videos == 3
    # The only image is vaulted, so the vault holds across this filter.
    assert found(client, q="type:image") == ([], 0)


def test_a_folder_token_finds_what_is_inside_a_child_folder(
    client: TestClient, world: World
) -> None:
    """`in:` finds what is in a child folder, and the child alone does not find the parent's."""
    sign_in(client, "admin")
    parent, _ = found(client, q="in:clips")
    # The vaulted file in this folder stays absent, admins included.
    assert set(parent) == {world.beach, world.walk, world.private}

    child, _ = found(client, q="in:holiday")
    assert child == [world.beach]


def test_a_folder_token_takes_a_path_as_well_as_a_name(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    assert found(client, q="in:clips/holiday")[0] == [world.beach]


def test_a_folder_token_takes_an_id_which_names_exactly_one_folder(
    client: TestClient, world: World
) -> None:
    """`in:` takes a folder id, the one unambiguous form, which the folder browser sends."""
    sign_in(client, "admin")
    assert found(client, q=f"in:{world.holiday}")[0] == [world.beach]


def test_a_folder_id_nobody_may_see_finds_nothing(client: TestClient, world: World) -> None:
    """A folder id is resolved against what this viewer may see, as a name is."""
    sign_in(client, "guest")
    assert found(client, q=f"in:{world.holiday}") == ([], 0)
    assert found(client, q="in:01JZZZZZZZZZZZZZZZZZZZZZZZ") == ([], 0)


def test_a_collection_token_narrows_to_its_contents(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    assert found(client, q='collections:"Best of"') == ([world.beach], 1)


def test_a_photo_set_token_narrows_to_the_pictures_in_it(client: TestClient, world: World) -> None:
    """`photo_sets:` narrows to the pictures in a set, as the Photo Sets screen sends it."""
    sign_in(client, "admin")
    assert found(client, q='photo_sets:"Beach shoot"') == ([world.beach], 1)


def test_a_photo_set_is_named_by_its_id_as_well_as_by_its_name(
    client: TestClient, world: World
) -> None:
    """A Photo Set is named by id as well as by name, since two sets can share a name."""
    sign_in(client, "admin")
    assert found(client, q=f"photo_sets:{world.photo_set}") == ([world.beach], 1)


def test_a_photo_set_nobody_minted_matches_nothing_rather_than_everything(
    client: TestClient, world: World
) -> None:
    """A Photo Set id nobody minted matches nothing."""
    sign_in(client, "admin")
    assert found(client, q="photo_sets:01JZZZZZZZZZZZZZZZZZZZZZZZ") == ([], 0)
    assert found(client, q='photo_sets:"no such shoot"') == ([], 0)


def test_the_dropdown_offers_a_photo_set_by_name(client: TestClient, world: World) -> None:
    """The dropdown offers a Photo Set by name."""
    sign_in(client, "admin")
    answer = client.get("/api/search/suggest", params={"q": "photo_sets:Bea"}).json()
    assert [row["value"] for row in answer["matches"]] == ["Beach shoot"]
    # The id rides with the row, so picking it opens that set.
    assert [row["id"] for row in answer["matches"]] == [world.photo_set]


def test_a_file_can_be_asked_whether_it_is_in_any_photo_set_at_all(
    client: TestClient, world: World
) -> None:
    """The curation queue: what has not been put into a shoot yet."""
    sign_in(client, "admin")
    assert found(client, q="photo_sets:any") == ([world.beach], 1)
    assert set(found(client, q="photo_sets:none")[0]) == {world.walk, world.private}


def test_a_site_token_narrows_to_that_site(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    assert found(client, q="sites:TikTok") == ([world.beach], 1)


def test_a_duration_token_narrows_by_length(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    assert found(client, q="duration:5m+")[0] == [world.walk]
    assert set(found(client, q="duration:1m-")[0]) == {world.beach, world.private}


def test_an_added_token_narrows_by_when_it_arrived(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    assert found(client, q="added:2026-07-01")[0] == [world.beach]
    assert found(client, q="added:2026-07-01..2026-07-02")[0] == [world.walk, world.beach]


def test_a_rating_token_reads_this_account_and_not_another(
    client: TestClient, world: World
) -> None:
    """Stars are per-user, so two people asking the same question get different answers."""
    admin = sign_in(client, "admin")
    other = sign_in(client, "guest", who="rater")
    share(client, "global", None, other)

    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_user_state "
                "(asset_id, user_id, favorite, rating, updated_at) VALUES (?, ?, 0, 5, 0)",
                (world.walk, admin),
            )
        ],
    )

    # The guest, who rated nothing, finds nothing at four stars or better.
    assert found(client, q="rating:4+") == ([], 0)

    sign_in(client, "admin")
    assert found(client, q="rating:4+") == ([world.walk], 1)


def test_a_favorite_token_reads_both_ways_round(client: TestClient, world: World) -> None:
    admin = sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_user_state (asset_id, user_id, favorite, updated_at) "
                "VALUES (?, ?, 1, 0)",
                (world.beach, admin),
            )
        ],
    )
    assert found(client, q="fav:yes")[0] == [world.beach]
    # `fav:no` includes files with no state row at all.
    assert world.walk in found(client, q="fav:no")[0]


def test_the_whole_example_from_the_specification(client: TestClient, world: World) -> None:
    """`people:jane tags:beach rating:4+` returns exactly what it says."""
    admin = sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_user_state "
                "(asset_id, user_id, favorite, rating, updated_at) VALUES (?, ?, 0, 5, 0)",
                (world.beach, admin),
            )
        ],
    )
    assert found(client, q="people:jane tags:beach rating:4+") == ([], 0)
    assert found(client, q='people:"Jane Doe" tags:beach rating:4+') == ([world.beach], 1)


def test_free_text_and_a_token_apply_together(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    assert found(client, q="sunset tags:beach") == ([world.beach], 1)
    assert found(client, q="sunset tags:city") == ([world.walk], 1)


# --- one engine, over HTTP --------------------------------------------------------------------


def test_the_modal_and_the_tokens_return_the_same_page(client: TestClient, world: World) -> None:
    """The modal and the tokens return byte-identical pages, count included."""
    sign_in(client, "admin")

    typed = client.get("/api/assets", params={"q": "tags:beach type:video"}).json()
    clicked = client.get("/api/assets", params={"tags": "beach", "type": "video"}).json()

    assert typed == clicked
    assert typed["total"] == 1


def test_the_grid_and_the_search_agree_about_the_same_query(
    client: TestClient, world: World
) -> None:
    """The grid and the search answer the same query the same way."""
    sign_in(client, "admin")

    grid = client.get("/api/assets", params={"q": "sunset", "tags": "beach"}).json()
    search = client.get("/api/assets", params={"q": "sunset", "tags": "beach"}).json()

    assert [item["id"] for item in grid["items"]] == [item["id"] for item in search["items"]]
    assert grid["total"] == search["total"] == 1


# --- alias resolution, through the kernel's one resolver ---------------------------------------


@pytest.mark.parametrize("term", ["Jane Doe", "JD", "janed"])
def test_a_person_is_found_by_name_by_alias_and_by_a_linked_username(
    client: TestClient, world: World, term: str
) -> None:
    """A person is found by name, alias or linked username, all through the kernel's resolver."""
    sign_in(client, "admin")
    assert found(client, q=f'people:"{term}"') == ([world.beach], 1)


def test_a_name_nobody_goes_by_finds_nothing(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    assert found(client, q="people:Nobody") == ([], 0)


# --- permission scoping: results, counts AND suggestions ---------------------------------------


def test_a_guest_shown_one_file_finds_only_that_one(client: TestClient, world: World) -> None:
    guest = sign_in(client, "guest")
    share(client, "item", world.beach, guest)

    ids, total = found(client, q="sunset")
    assert ids == [world.beach]
    # The count is the assertion: two beside one row would say the other exists.
    assert total == 1


def test_a_guest_searching_the_exact_name_of_a_restricted_file_gets_nothing(
    client: TestClient, world: World
) -> None:
    """The direct guess. They know the filename and ask for it directly."""
    guest = sign_in(client, "guest")
    share(client, "item", world.beach, guest)

    assert found(client, q="private_notes") == ([], 0)
    assert found(client, q="private") == ([], 0)


def test_a_restricted_file_does_not_move_a_count(client: TestClient, world: World) -> None:
    """A count that changed when a hidden file matched would publish it without showing it."""
    guest = sign_in(client, "guest")
    share(client, "global", None, guest)
    restrict(client, "item", world.private, guest)

    ids, total = found(client, q="mp4")
    assert world.private not in ids
    assert total == len(ids)


def test_the_dropdown_never_offers_a_name_the_asker_may_not_know(
    client: TestClient, world: World
) -> None:
    """The dropdown offers a guest nothing they may not know."""
    sign_in(client, "guest")

    for token in ("tags:", "people:", "sites:", "collections:"):
        body = client.get("/api/search/suggest", params={"q": token}).json()
        assert body["matches"] == [], f"{token} leaked a name to a guest"


def test_the_dropdown_offers_what_the_asker_can_actually_see(
    client: TestClient, world: World
) -> None:
    """The other side of the branch, so the assertion above cannot pass by returning nothing."""
    sign_in(client, "admin")

    body = client.get("/api/search/suggest", params={"q": "tags:be"}).json()
    assert [row["value"] for row in body["matches"]] == ["beach"]
    assert body["token"] == "tags"

    people = client.get("/api/search/suggest", params={"q": "people:ja"}).json()
    assert [row["value"] for row in people["matches"]] == ["Jane Doe"]


def test_a_name_is_looked_up_by_the_whole_run_of_words(client: TestClient, world: World) -> None:
    """A name is looked up by the whole run of words: `jane d`, not `d`."""
    sign_in(client, "admin")

    body = client.get("/api/search/suggest", params={"q": "jane d"}).json()
    assert "Jane Doe" in [row["value"] for row in body["matches"]]
    # The name's span is both words, so a chip replaces all that was typed.
    assert body["matched_from"] == 0
    # A filter's span is still the last word.
    assert body["replace_from"] == 5


def test_the_phrase_falls_back_to_the_last_word_when_nothing_is_called_that(
    client: TestClient, world: World
) -> None:
    """With nothing called the whole phrase, the dropdown falls back to the last word."""
    sign_in(client, "admin")

    body = client.get("/api/search/suggest", params={"q": "nothingiscalledthis beach"}).json()

    assert [row["value"] for row in body["matches"]] == ["beach"]
    # Fell back, so the span is the word's.
    assert body["matched_from"] == body["replace_from"]


def test_a_suggested_count_is_the_asker_s_own(client: TestClient, world: World) -> None:
    """A suggested count is the asker's own: two files carry the tag and the guest may see one."""
    guest = sign_in(client, "guest")
    share(client, "item", world.beach, guest)
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
                (world.walk, world.tag_beach),
            )
        ],
    )

    # An admin sees both, so the number is not always one.
    sign_in(client, "admin")
    admin_view = client.get("/api/search/suggest", params={"q": "tags:beach"}).json()
    assert [(row["value"], row["count"]) for row in admin_view["matches"]] == [("beach", 2)]

    sign_in(client, "guest")
    body = client.get("/api/search/suggest", params={"q": "tags:beach"}).json()
    assert [(row["value"], row["count"]) for row in body["matches"]] == [("beach", 1)]


# --- the vault --------------------------------------------------------------------------------


def test_a_vaulted_file_is_absent_from_results_counts_and_suggestions(
    client: TestClient, world: World
) -> None:
    """Concealed from everyone, admins included, until the vault is unlocked."""
    sign_in(client, "admin")

    ids, total = found(client, q="vaulted_secret")
    assert ids == []
    assert total == 0

    everything, count = found(client, q="mp4")
    assert world.vaulted not in everything
    assert count == len(everything)


def test_vaulting_a_person_takes_their_files_out_of_search(
    client: TestClient, world: World
) -> None:
    """The vault rides the logical axis too, and search must honour it the same way."""
    sign_in(client, "admin")
    assert found(client, q="beach_sunset")[0] == [world.beach]

    hide_for_caller(client, "person", world.person)

    assert found(client, q="beach_sunset") == ([], 0)
    # The person is no longer offered.
    body = client.get("/api/search/suggest", params={"q": "people:ja"}).json()
    assert body["matches"] == []


def test_a_bare_word_offers_matches_from_across_the_catalog(
    client: TestClient, world: World
) -> None:
    """A bare word suggests the tag, person, site or collection it starts, each with its field."""
    sign_in(client, "admin")

    # The rows span fields, so there is no single token.
    body = client.get("/api/search/suggest", params={"q": "be"}).json()
    assert body["token"] is None
    assert body["replace_from"] == 0
    pairs = {(row["field"], row["value"]) for row in body["matches"]}
    assert ("tags", "beach") in pairs
    assert ("collections", "Best of") in pairs

    people = client.get("/api/search/suggest", params={"q": "Jan"}).json()
    assert ("people", "Jane Doe") in {(row["field"], row["value"]) for row in people["matches"]}

    # A site, asserted apart since no word starts all four kinds here.
    sites = client.get("/api/search/suggest", params={"q": "Tik"}).json()
    assert ("sites", "TikTok") in {(row["field"], row["value"]) for row in sites["matches"]}

    # The word at the caret is replaced from where it starts.
    mid = client.get("/api/search/suggest", params={"q": "tags:city be"}).json()
    assert mid["replace_from"] == 10
    assert ("tags", "beach") in {(row["field"], row["value"]) for row in mid["matches"]}


def test_an_empty_box_offers_every_filter_there_is(client: TestClient, world: World) -> None:
    """An empty box lists every filter: where the query language documents itself."""
    sign_in(client, "admin")

    body = client.get("/api/search/suggest", params={"q": ""}).json()

    fields = [row["field"] for row in body["filters"]]
    assert "tags" in fields
    assert "people" in fields
    assert "duration" in fields
    # Each carries a label, a hint and an example.
    tags = next(row for row in body["filters"] if row["field"] == "tags")
    assert tags["label"] and tags["hint"] and tags["example"]
    # A filter written by a click shows where, never an id nobody types.
    network = next(row for row in body["filters"] if row["field"] == "network")
    assert (network["example"], network["set_from"]) == ("network:", "from the Network column")
    assert not any(re.search(r"[0-9A-Z]{8,}", row["example"]) for row in body["filters"])
    # Inserted at the start: no half-typed word to replace.
    assert body["replace_from"] == 0


def test_typing_the_name_of_a_thing_offers_the_filter_for_it(
    client: TestClient, world: World
) -> None:
    """Typing a filter's name ("peop") offers that filter."""
    sign_in(client, "admin")

    narrowed = client.get("/api/search/suggest", params={"q": "peop"}).json()
    assert [row["field"] for row in narrowed["filters"]] == ["people"]
    # Over the half-typed word.
    assert narrowed["replace_from"] == 0

    # The label finds the token too: `in:` is called Folder.
    by_label = client.get("/api/search/suggest", params={"q": "folder"}).json()
    assert [row["field"] for row in by_label["filters"]] == ["in"]

    # A word naming no filter offers none; a filter's names are its token and its label.
    for nothing in ("zzz", "person"):
        assert client.get("/api/search/suggest", params={"q": nothing}).json()["filters"] == []


def test_the_filters_are_offered_beside_matches_rather_than_instead_of_them(
    client: TestClient, world: World
) -> None:
    """Filters are offered beside matches, as two groups in one list."""
    sign_in(client, "admin")

    body = client.get("/api/search/suggest", params={"q": "ta"}).json()

    assert [row["field"] for row in body["filters"]] == ["tags"]
    assert body["matches"] != [] or body["recent"] == []


def test_a_filter_is_not_offered_inside_a_token(client: TestClient, world: World) -> None:
    """No filter is offered inside a token."""
    sign_in(client, "admin")

    body = client.get("/api/search/suggest", params={"q": "people:Jan"}).json()

    assert body["filters"] == []
    assert body["token"] == "people"


def test_media_offers_its_three_kinds_after_its_prefix(client: TestClient, world: World) -> None:
    """After `media:` the list offers the kinds, narrowed by what follows."""
    sign_in(client, "admin")

    body = client.get("/api/search/suggest", params={"q": "media:"}).json()
    assert body["token"] == "media"
    assert [row["value"] for row in body["matches"]] == ["video", "image", "gif"]

    narrowed = client.get("/api/search/suggest", params={"q": "media:g"}).json()
    assert [row["value"] for row in narrowed["matches"]] == ["gif"]


def test_every_filter_with_a_list_offers_it_after_its_prefix(
    client: TestClient, world: World
) -> None:
    """One rule for every filter with a fixed set of answers, in both of the route's forms."""
    sign_in(client, "admin")

    for one, values in OFFERED_VALUES.items():
        typed = client.get("/api/search/suggest", params={"q": f"{one.value}:"}).json()
        assert [row["value"] for row in typed["matches"]] == list(values), one
        asked = client.get("/api/search/suggest", params={"field": one.value}).json()
        assert [row["value"] for row in asked["matches"]] == list(values), one


def test_a_bare_word_offers_the_FILE_somebody_gave_that_name(
    client: TestClient, world: World
) -> None:
    """A bare word offers the file whose title it is; picking it opens the file, since there is no
    `files:` filter."""
    sign_in(client, "admin")
    named = client.put(f"/api/assets/{world.beach}", json={"title": "Sunrise Over Everything"})
    assert named.status_code == 204, named.text

    body = client.get("/api/search/suggest", params={"q": "Sunrise"}).json()

    files = [row for row in body["matches"] if row["opens"] == "file"]
    assert [(row["value"], row["id"]) for row in files] == [
        ("Sunrise Over Everything", world.beach)
    ]
    # No token to complete to.
    assert files[0]["field"] is None


def test_a_file_row_is_the_one_this_viewer_may_open(client: TestClient, world: World) -> None:
    """A file row comes through the scoped page read, so it is one this viewer may open."""
    sign_in(client, "admin")
    client.put(f"/api/assets/{world.private}", json={"title": "Sunrise Over Everything"})
    sign_in(client, "guest")

    body = client.get("/api/search/suggest", params={"q": "Sunrise"}).json()

    assert [row for row in body["matches"] if row["opens"] == "file"] == []


def test_a_title_is_a_filter_of_its_own(client: TestClient, world: World) -> None:
    """`title:` is its own filter: in a downloaded library the title is the name worth searching."""
    sign_in(client, "admin")
    client.put(f"/api/assets/{world.beach}", json={"title": "Sunrise Over Everything"})

    found = client.get("/api/assets", params={"q": "title:Sunrise"}).json()

    assert [one["id"] for one in found["items"]] == [world.beach]


def test_a_one_letter_word_is_too_short_to_match_across_the_catalog(
    client: TestClient, world: World
) -> None:
    """One letter is below the floor; only recents it prefixes are offered."""
    sign_in(client, "admin")
    remember(client, "b later")

    body = client.get("/api/search/suggest", params={"q": "b"}).json()
    assert body["matches"] == []
    assert [row["label"] for row in body["recent"]] == ["b later"]


def test_a_stored_query_with_a_wildcard_in_it_is_matched_literally(
    client: TestClient, world: World
) -> None:
    """`%` and `_` are wildcards to LIKE and ordinary characters in something somebody typed."""
    sign_in(client, "admin")
    remember(client, "100% sure")
    remember(client, "nothing alike")

    hit = client.get("/api/search/suggest", params={"q": "100%"}).json()["recent"]
    assert [row["label"] for row in hit] == ["100% sure"]
    assert client.get("/api/search/suggest", params={"q": "_"}).json()["recent"] == []


def test_the_history_is_capped(client: TestClient, world: World) -> None:
    """The history is capped; each write is checked so a failed one is not misread as the cap."""
    from sift.slices.search.service import MAX_HISTORY

    sign_in(client, "admin")
    for number in range(MAX_HISTORY + 5):
        answer = remember(client, f"query number {number}")
        assert answer.status_code == 204, f"search {number} was not recorded: {answer.text}"

    items = remembered(client)
    assert len(items) == MAX_HISTORY

    assert items[0] == f"query number {MAX_HISTORY + 4}", (
        "the newest search is not at the top of the history. Every search above returned 200, so "
        "each one was written, which leaves the ORDER the list is read in, or the trim that "
        f"decides which rows survive it. What came back, newest first: {items[:6]}. Both the read "
        "and the trim sort by created_at then id, so a tie in created_at is broken by the id, and "
        "ids are monotonic within a process. If this fails again, print the rows: a created_at that "
        "went backwards or a non-monotonic id is the only way the newest row loses."
    )


# --- paging -----------------------------------------------------------------------------------


def test_a_page_past_the_end_still_reports_the_real_total(client: TestClient, world: World) -> None:
    """An empty last page reporting zero would contradict the pages that led somebody to it."""
    sign_in(client, "admin")
    ids, total = found(client, q="mp4", limit=2, offset=50)
    assert ids == []
    assert total == 3


def test_an_unauthenticated_search_is_refused(client: TestClient, world: World) -> None:
    client.cookies.clear()
    assert client.get("/api/assets", params={"q": "beach"}).status_code == 401


def test_the_epoch_is_what_the_fixture_says() -> None:
    """The fixture's epoch, which the date assertions are written against."""
    assert EPOCH == 1_782_864_000


# --- the paths a request cannot easily reach ---------------------------------------------------


def test_a_query_whose_range_ends_before_it_starts_matches_nothing(
    client: TestClient, world: World
) -> None:
    """Bounds that cannot both hold are refused by the kernel and answered with an empty page."""
    sign_in(client, "admin")
    assert found(client, q="rating:4+ rating:2-") == ([], 0)
    assert found(client, q="duration:5m+ duration:1m-") == ([], 0)


def test_a_concealed_result_is_a_locked_tile_and_says_nothing_else(
    client: TestClient, world: World
) -> None:
    """In placeholder mode a concealed result is a locked tile that says nothing else."""
    user_id = sign_in(client, "admin")

    def placeholder_viewer() -> Viewer:
        return Viewer(
            id=user_id, role=Role.ADMIN, show_hidden=False, concealment=Concealment.PLACEHOLDER
        )

    client.app.dependency_overrides[current_viewer] = placeholder_viewer  # type: ignore[attr-defined]
    try:
        body = client.get("/api/assets", params={"q": "mp4"}).json()
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]

    concealed = [item for item in body["items"] if item["concealed"]]
    assert [item["id"] for item in concealed] == [world.vaulted]
    assert concealed[0]["media_type"] == ""
    assert concealed[0]["duration_ms"] is None
    assert concealed[0]["rating"] is None
    assert "vaulted_secret" not in str(body)


def test_an_unlocked_search_finds_a_vaulted_file_in_full(client: TestClient, world: World) -> None:
    """With the vault open, a hidden result is found in full rather than as a placeholder."""
    user_id = sign_in(client, "admin")

    def unlocked_viewer() -> Viewer:
        return Viewer(id=user_id, role=Role.ADMIN, show_hidden=True)

    client.app.dependency_overrides[current_viewer] = unlocked_viewer  # type: ignore[attr-defined]
    try:
        body = client.get("/api/assets", params={"q": "vaulted_secret"}).json()
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]

    matches = [item for item in body["items"] if item["id"] == world.vaulted]
    assert len(matches) == 1
    assert matches[0]["concealed"] is False
    assert matches[0]["media_type"] != ""


def test_the_dropdown_offers_folders_and_collections_by_name(
    client: TestClient, world: World
) -> None:
    """The two suggesters with no prefix parameter of their own, filtered here instead."""
    sign_in(client, "admin")

    folders = client.get("/api/search/suggest", params={"q": "in:clips"}).json()
    assert {row["value"] for row in folders["matches"]} == {"clips", "clips/holiday"}

    # By the folder's own name as well as its path.
    child = client.get("/api/search/suggest", params={"q": "in:holi"}).json()
    assert [row["value"] for row in child["matches"]] == ["clips/holiday"]

    collections = client.get("/api/search/suggest", params={"q": "collections:Bes"}).json()
    assert [row["value"] for row in collections["matches"]] == ["Best of"]

    sites = client.get("/api/search/suggest", params={"q": "sites:Tik"}).json()
    assert [(row["value"], row["count"]) for row in sites["matches"]] == [("TikTok", 1)]


def _a_root_called(client: TestClient, name: str, at: str) -> None:
    """A library root and the folder that is it: an empty path, named after the root."""
    root = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?,?,?,?)",
                (root, name, at, EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?,?,?,?,?)",
                (new_id(), root, None, "", name),
            ),
        ],
    )


def test_two_roots_of_the_same_name_are_one_row_in_the_in_list(
    client: TestClient, world: World
) -> None:
    """Two roots of the same name are one row in the `in:` list; the first wins."""
    sign_in(client, "admin")
    _a_root_called(client, "spare drive", "/media/one")
    _a_root_called(client, "spare drive", "/media/two")

    offered = client.get("/api/search/suggest", params={"q": "in:spare"}).json()

    assert [row["value"] for row in offered["matches"]] == ["spare drive"]


def test_the_in_list_stops_at_the_number_the_dropdown_may_show(
    client: TestClient, world: World
) -> None:
    """The `in:` list breaks at the dropdown's cap rather than slicing a full read."""
    sign_in(client, "admin")
    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?,?,?,?,?)",
            (new_id(), world.root, world.clips, f"clips/take{number:02d}", f"take{number:02d}"),
        )
        for number in range(MAX_SUGGESTIONS + 5)
    ]
    write(db_path(client), statements)

    offered = client.get("/api/search/suggest", params={"q": "in:clips"}).json()

    assert len(offered["matches"]) == MAX_SUGGESTIONS


def test_two_roots_of_the_same_name_are_one_row_for_a_bare_word(
    client: TestClient, world: World
) -> None:
    """The bare-word band deduplicates roots by name too."""
    sign_in(client, "admin")
    _a_root_called(client, "spare drive", "/media/one")
    _a_root_called(client, "spare drive", "/media/two")

    body = client.get("/api/search/suggest", params={"q": "spare"}).json()

    assert [row["value"] for row in body["matches"] if row["field"] == "in"] == ["spare drive"]


def test_a_blank_search_is_not_written_to_a_history(client: TestClient, world: World) -> None:
    """A blank search is not written to the history, asserted on the service behind the route's
    own guard."""
    from sift.slices.search.service import SearchService

    user_id = sign_in(client, "admin")
    service: SearchService = client.app.state.search  # type: ignore[attr-defined]
    viewer = Viewer(id=user_id, role=Role.ADMIN)

    asyncio.run(_remember(service, viewer, "   "))

    assert remembered(client) == []


async def _remember(service: object, viewer: Viewer, query: str) -> None:
    await service.remember(viewer, query)  # type: ignore[attr-defined]


def test_a_value_the_parser_cannot_read_empties_the_page_rather_than_filling_it(
    client: TestClient, world: World
) -> None:
    """An unreadable value empties the page over HTTP, rather than filling it."""
    sign_in(client, "admin")
    for typed in ("rating:abc", "type:sculpture", "added:yesterday", "fav:maybe", "duration:soon"):
        assert found(client, q=typed) == ([], 0), typed

    # The control: a readable value is not emptied.
    assert found(client, q="type:video")[1] == 3


def test_a_query_with_more_terms_than_the_cap_is_answered_rather_than_obeyed(
    client: TestClient, world: World
) -> None:
    """Over the term cap the answer is nothing. The repeated tag is real, so the test fails if the
    cap goes."""
    from sift.slices.search.filters import MAX_TERMS

    sign_in(client, "admin")
    assert found(client, tags="beach") == ([world.beach], 1)

    over = ",".join(["beach"] * (MAX_TERMS + 1))
    assert found(client, tags=over) == ([], 0)


# --- what has not been rated yet ---------------------------------------------------------------


def test_unrated_is_a_question_the_star_range_cannot_ask(client: TestClient, world: World) -> None:
    """`rating:none` finds what is unrated, which no star range can ask."""
    admin = sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_user_state "
                "(asset_id, user_id, favorite, rating, updated_at) VALUES (?, ?, 0, 5, 0)",
                (world.beach, admin),
            )
        ],
    )

    rated, rated_total = found(client, q="rating:any")
    assert rated == [world.beach]
    assert rated_total == 1

    unrated, unrated_total = found(client, q="rating:none")
    assert world.beach not in unrated
    assert set(unrated) == {world.walk, world.private}
    assert unrated_total == 2


def test_a_file_hearted_but_never_rated_is_still_unrated(client: TestClient, world: World) -> None:
    """A file hearted but never rated is unrated: the rating is read, not the row's presence."""
    admin = sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_user_state (asset_id, user_id, favorite, updated_at) "
                "VALUES (?, ?, 1, 0)",
                (world.beach, admin),
            )
        ],
    )

    assert world.beach in found(client, q="rating:none")[0]
    assert found(client, q="rating:any") == ([], 0)


def test_unrated_is_this_account_s_own_question(client: TestClient, world: World) -> None:
    """Stars are per-user, so what is unrated depends on who is asking."""
    admin = sign_in(client, "admin")
    guest = sign_in(client, "guest", who="unrated")
    share(client, "global", None, guest)
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_user_state "
                "(asset_id, user_id, favorite, rating, updated_at) VALUES (?, ?, 0, 4, 0)",
                (world.beach, admin),
            )
        ],
    )

    # The guest rated nothing.
    assert found(client, q="rating:any") == ([], 0)
    assert world.beach in found(client, q="rating:none")[0]


def test_asking_for_unrated_and_a_star_count_at_once_matches_nothing(
    client: TestClient, world: World
) -> None:
    """Two things that cannot both hold. Answered as nothing, never as everything."""
    sign_in(client, "admin")
    assert found(client, q="rating:none rating:4+") == ([], 0)


# --- terms too short for the index --------------------------------------------------------------


def test_a_search_of_one_or_two_characters_still_finds_things(
    client: TestClient, world: World
) -> None:
    """One or two characters still find things, by a scan beside the trigram index."""
    sign_in(client, "admin")

    assert found(client, q="Ja") == ([world.beach], 1)
    assert found(client, q="J") == ([world.beach], 1)


def test_a_short_term_and_a_long_one_both_have_to_match(client: TestClient, world: World) -> None:
    """A short term and a long one both have to match."""
    sign_in(client, "admin")

    assert found(client, q="beach Ja") == ([world.beach], 1)
    # `sunset` matches two files; `Ja` narrows to one.
    assert found(client, q="sunset Ja") == ([world.beach], 1)
    # A short term matching nothing empties the result.
    assert found(client, q="sunset zq") == ([], 0)


def test_a_short_term_that_is_a_wildcard_is_matched_literally(
    client: TestClient, world: World
) -> None:
    """A short `%` or `_` is matched literally, not as a LIKE wildcard."""
    sign_in(client, "admin")

    # `sunsetwalk` has no underscore, so a literal `_` and a wildcard give different answers.
    underscores, _ = found(client, q="_")
    assert set(underscores) == {world.beach, world.private}
    assert world.walk not in underscores

    assert found(client, q="%") == ([], 0)


def test_a_short_term_is_still_permission_scoped(client: TestClient, world: World) -> None:
    """The short-term scan is permission scoped like the index lookup."""
    guest = sign_in(client, "guest")
    share(client, "item", world.beach, guest)

    ids, total = found(client, q="_")
    assert ids == [world.beach]
    assert total == 1


# --- search is not an oracle --------------------------------------------------------------------


#: Every way of asking, pointed at a thing the asker may not see.
_PROBES = [
    "",
    "buried",
    "bur",
    "bu",
    "b",
    "_",
    "%",
    "buried_plans",
    "tags:buried-tag",
    "tags:",
    'people:"Buried Person"',
    "people:",
    "sites:BuriedSite",
    "sites:",
    'collections:"Buried Set"',
    "collections:",
    "in:buried",
    "in:clips",
    "in:",
    "type:video",
    "type:image",
    "type:gif",
    "rating:none",
    "rating:any",
    "rating:4+",
    "fav:yes",
    "fav:no",
    "added:7d",
    "added:2026-07-01",
    "duration:1m-",
    "duration:5m+",
]

#: And every dropdown, at the prefixes that would offer the hidden names.
_SUGGESTS = [
    "",
    "tags:",
    "tags:bu",
    "people:",
    "people:Bu",
    "sites:",
    "sites:Bu",
    "collections:",
    "collections:Bu",
    "in:",
    "in:bu",
]


def _answers(client: TestClient) -> dict[str, object]:
    """Everything this user can learn from the search surface, as one comparable value."""
    seen: dict[str, object] = {}
    for probe in _PROBES:
        body = client.get("/api/assets", params={"q": probe}).json()
        seen[f"search {probe!r}"] = (body["total"], [item["id"] for item in body["items"]])
    for probe in _SUGGESTS:
        body = client.get("/api/search/suggest", params={"q": probe}).json()
        seen[f"suggest {probe!r}"] = [(row["value"], row["count"]) for row in body["matches"]]
    return seen


def test_a_guest_cannot_tell_whether_a_restricted_asset_exists_at_all(
    client: TestClient, world: World
) -> None:
    """A guest's answers to every probe are identical whether or not a restricted asset exists.
    It is compared against itself, with no expected values; the added asset carries one of
    everything so each dimension has something to leak."""
    guest = sign_in(client, "guest")
    share(client, "item", world.beach, guest)

    before = _answers(client)

    buried = new_id()
    tag, person, username, site, collection, folder = (new_id() for _ in range(6))
    write(
        db_path(client),
        [
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?,?,?,?,?)",
                (folder, world.root, world.clips, "clips/buried", "buried"),
            ),
            ("INSERT INTO tags (id, name, created_at) VALUES (?,?,?)", (tag, "buried-tag", EPOCH)),
            (
                "INSERT INTO people (id, name, created_at) VALUES (?,?,?)",
                (person, "Buried Person", EPOCH),
            ),
            (
                "INSERT INTO people_aliases (id, person_id, alias) VALUES (?,?,?)",
                (new_id(), person, "BP"),
            ),
            (
                "INSERT INTO sites (id, name, kind) VALUES (?,?,?)",
                (site, "BuriedSite", "video"),
            ),
            (
                "INSERT INTO usernames (id, site_id, name, person_id, created_at) "
                "VALUES (?,?,?,?,?)",
                (username, site, "buriedhandle", person, EPOCH),
            ),
            (
                "INSERT INTO collections (id, name, created_at) VALUES (?,?,?)",
                (collection, "Buried Set", EPOCH),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, duration_ms, size_bytes, "
                "original_filename, added_at) VALUES (?,?,'image',600000,1,?,?)",
                (buried, "digest-buried", "buried_plans.mp4", EPOCH + 500),
            ),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                "filename, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    new_id(),
                    buried,
                    world.root,
                    folder,
                    "clips/buried/buried_plans.mp4",
                    "buried_plans.mp4",
                    EPOCH,
                    EPOCH,
                ),
            ),
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?,?)", (buried, tag)),
            ("INSERT INTO asset_people (asset_id, person_id) VALUES (?,?)", (buried, person)),
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?,?)",
                (buried, username),
            ),
            (
                "INSERT INTO collection_items (collection_id, asset_id, position) VALUES (?,?,?)",
                (collection, buried, 0),
            ),
        ],
    )
    reindex(db_path(client))

    assert _answers(client) == before


def test_the_same_probes_do_change_for_somebody_allowed_to_see_it(
    client: TestClient, world: World
) -> None:
    """The control: the same probes do change for an admin, so the test above can detect a leak."""
    sign_in(client, "admin")
    before = _answers(client)

    buried = new_id()
    tag = new_id()
    write(
        db_path(client),
        [
            ("INSERT INTO tags (id, name, created_at) VALUES (?,?,?)", (tag, "buried-tag", EPOCH)),
            (
                "INSERT INTO assets (id, identity, media_type, duration_ms, size_bytes, "
                "original_filename, added_at) VALUES (?,?,'video',60000,1,?,?)",
                (buried, "digest-buried", "buried_plans.mp4", EPOCH + 500),
            ),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                "filename, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    new_id(),
                    buried,
                    world.root,
                    world.clips,
                    "clips/buried_plans.mp4",
                    "buried_plans.mp4",
                    EPOCH,
                    EPOCH,
                ),
            ),
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?,?)", (buried, tag)),
        ],
    )
    reindex(db_path(client))

    assert _answers(client) != before


# --- telling the client what a typed query means ------------------------------------------------


def test_the_server_says_what_a_typed_query_means(client: TestClient, world: World) -> None:
    """The server says what a typed query means, so the Filters modal needs no second parser."""
    sign_in(client, "admin")

    body = client.get(
        "/api/search/parse", params={"q": 'holiday tags:beach people:"Jane Doe" rating:4+'}
    ).json()

    # The free text stays in the box.
    assert body["text"] == "holiday"
    assert body["terms"] == {
        "people": ["Jane Doe"],
        "tags": ["beach"],
        "rating": ["4+"],
    }


def test_a_repeated_token_comes_back_as_both_values(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    body = client.get("/api/search/parse", params={"q": "tags:beach tags:sunset"}).json()
    assert body["terms"] == {"tags": ["beach", "sunset"]}


def test_parsing_an_empty_query_says_so_rather_than_failing(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    body = client.get("/api/search/parse").json()
    assert body == {"text": "", "clauses": [], "terms": {}, "problems": []}


def test_a_value_nobody_could_act_on_is_named_on_the_parse_with_its_reason(
    client: TestClient, world: World
) -> None:
    """An unreadable value is named on the parse with its reason, and still matches nothing."""
    sign_in(client, "admin")
    body = client.get("/api/search/parse", params={"q": "rating:4+x tags:beach"}).json()
    assert body["problems"] == [
        {"field": "rating", "value": "4+x", "reason": "a rating is a number of stars"}
    ]
    # In `Field` declaration order (`_FIELD_ORDER`), not typed order.
    assert [clause["field"] for clause in body["clauses"]] == ["tags", "rating"]

    clean = client.get("/api/search/parse", params={"q": "rating:4+ tags:beach"}).json()
    assert clean["problems"] == []


def test_a_word_that_is_not_a_token_stays_free_text(client: TestClient, world: World) -> None:
    """An unknown field is not a failed filter: it is the text it looks like."""
    sign_in(client, "admin")
    body = client.get("/api/search/parse", params={"q": "colour:blue tags:beach"}).json()
    assert body["text"] == "colour:blue"
    assert body["terms"] == {"tags": ["beach"]}


def test_parsing_is_refused_without_a_session(client: TestClient, world: World) -> None:
    client.cookies.clear()
    assert client.get("/api/search/parse", params={"q": "tags:beach"}).status_code == 401


def test_what_the_parse_says_is_what_the_search_does(client: TestClient, world: World) -> None:
    """Rewriting a typed query as the parse's parameters finds exactly the same things."""
    sign_in(client, "admin")
    typed = 'sunset tags:beach people:"Jane Doe"'

    parsed = client.get("/api/search/parse", params={"q": typed}).json()
    rewritten: dict[str, str] = {"q": parsed["text"]}
    for field, values in parsed["terms"].items():
        rewritten[field] = ",".join(f'"{value}"' for value in values)

    assert found(client, q=typed) == found(client, **rewritten)
    assert found(client, q=typed)[1] == 1
