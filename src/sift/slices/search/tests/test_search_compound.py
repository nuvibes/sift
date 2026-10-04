# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compound queries, what no filter may widen, and what the dropdown offers for a bare word."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel import site_icons
from sift.kernel.ids import new_id
from sift.slices.search.service import MAX_SUGGESTIONS
from sift.slices.search.tests.conftest import (
    EPOCH,
    World,
    db_path,
    found,
    restrict,
    share,
    sign_in,
    write,
)

# --- compound queries --------------------------------------------------------------------------
#
# A golden table for the connectives, each row run through the search and the grid, which share one
# engine. The conftest's library: `beach` is tagged, has a person, a username and a collection;
# `walk` has a tag; `private` has nothing; a fourth file is in the shut vault and never appears.

COMPOUND: list[tuple[str, set[str]]] = [
    # --- the pure-AND queries, which must answer as AND always does --------------------------
    ("tags:beach", {"beach"}),
    ("tags:beach tags:city", set()),
    ('tags:beach people:"Jane Doe"', {"beach"}),
    ("tags:nosuchtag", set()),
    # --- either one, within a dimension ---------------------------------------------------------
    ("tags:beach OR tags:city", {"beach", "walk"}),
    # A name nobody has is an empty group matching nothing, and the other side still answers.
    ("tags:nosuchtag OR tags:city", {"walk"}),
    ("tags:nosuchtag OR tags:alsonothing", set()),
    # --- either one, across dimensions: the thing a flat AND cannot say at all -----------------
    ('tags:city OR people:"Jane Doe"', {"walk", "beach"}),
    ('collections:"Best of" OR tags:city', {"beach", "walk"}),
    # --- a choice AND something else, which is what the precedence is for ------------------------
    ("tags:beach OR tags:city type:video", {"beach", "walk"}),
    ("tags:beach OR tags:city duration:5m+", {"walk"}),
    ("tags:beach OR tags:city tags:city", {"walk"}),
    # `in:` groups expand to subtrees, so a parent and its child filter to what is in the child.
    ("in:clips in:holiday", {"beach"}),
    ("in:holiday OR in:clips", {"beach", "walk", "private"}),
    ("in:holiday -tags", set()),
    # A choice is closed off from what is ANDed beside it, or these widen.
    ("type:image tags:beach OR tags:city", set()),
    ("rating:4+ tags:beach OR tags:city", set()),
    ("duration:1m- tags:city OR tags:beach", {"beach"}),
    # --- not that one ----------------------------------------------------------------------------
    ("-tags:beach", {"walk", "private"}),
    ("-tags:beach -tags:city", {"private"}),
    ("-tags:beach,city", {"private"}),
    ('-people:"Jane Doe"', {"walk", "private"}),
    ("-type:video", set()),
    # --- nothing on this dimension at all: the curation queues ------------------------------------
    ("-tags", {"private"}),
    ("tags:none", {"private"}),
    ("tags:any", {"beach", "walk"}),
    ("-people", {"walk", "private"}),
    ("-collections", {"walk", "private"}),
    ("-sites", {"walk", "private"}),
    # The Unorganized queue: nothing has been done to it.
    ("-tags -people -collections", {"private"}),
    # --- questions nothing can answer --------------------------------------------------------------
    ("tags:none tags:any", set()),
    ("tags:beach -tags:beach", set()),
    ("rating:none rating:any", set()),
    # --- a choice with a negation in it -------------------------------------------------------------
    ('-tags OR people:"Jane Doe"', {"private", "beach"}),
    ("tags:city OR -tags", {"walk", "private"}),
    # --- free text still applies on top of all of it --------------------------------------------------
    ("sunset tags:beach OR tags:city", {"beach", "walk"}),
    ("beach tags:beach OR tags:city", {"beach"}),
]


@pytest.mark.parametrize(("query", "expected"), COMPOUND, ids=[row[0] for row in COMPOUND])
def test_a_compound_query_returns_the_right_set(
    client: TestClient, world: World, query: str, expected: set[str]
) -> None:
    """One row of the compound language, run for real."""
    sign_in(client, "admin")
    wanted = {getattr(world, name) for name in expected}

    ids, total = found(client, q=query)
    assert set(ids) == wanted
    assert total == len(wanted)


@pytest.mark.parametrize(("query", "expected"), COMPOUND, ids=[row[0] for row in COMPOUND])
def test_the_grid_answers_a_compound_query_the_same_way(
    client: TestClient, world: World, query: str, expected: set[str]
) -> None:
    """The grid answers every compound query as the search does."""
    sign_in(client, "admin")

    body = client.get("/api/assets", params={"q": query}).json()
    assert {item["id"] for item in body["items"]} == {getattr(world, name) for name in expected}
    assert body["total"] == len(expected)


def test_a_choice_is_not_quietly_an_and(client: TestClient, world: World) -> None:
    """An OR is not compiled as an AND: two tags no file shares, so the answers differ most."""
    sign_in(client, "admin")

    either, either_total = found(client, q="tags:beach OR tags:city")
    both, both_total = found(client, q="tags:beach tags:city")

    assert set(either) == {world.beach, world.walk}
    assert either_total == 2
    assert both == [] and both_total == 0


def test_the_address_bar_round_trips_a_compound_query(client: TestClient, world: World) -> None:
    """The address bar round-trips a compound query: the server spells each clause, the client only
    joins them."""
    sign_in(client, "admin")
    typed = 'holiday tags:beach OR tags:city -people:"Jane Doe" duration:5m+'

    parsed = client.get("/api/search/parse", params={"q": typed}).json()
    rebuilt = " ".join([*(clause["query"] for clause in parsed["clauses"]), parsed["text"]])

    assert found(client, q=rebuilt) == found(client, q=typed)
    # It stayed compound.
    assert any(clause["match"] == "any" for clause in parsed["clauses"])
    assert any(clause["negated"] for clause in parsed["clauses"])


def test_the_clauses_describe_what_was_asked_for(client: TestClient, world: World) -> None:
    """What the Filters screen draws its rows from."""
    sign_in(client, "admin")
    body = client.get(
        "/api/search/parse", params={"q": "tags:a OR tags:b -people rating:4+"}
    ).json()

    rows = {clause["query"]: clause for clause in body["clauses"]}
    assert rows["tags:a OR tags:b"]["field"] == "tags"
    assert rows["tags:a OR tags:b"]["values"] == ["a", "b"]
    assert rows["tags:a OR tags:b"]["match"] == "any"
    assert rows["-people"]["present"] is False
    assert rows["-people"]["negated"] is True
    assert rows["rating:4+"]["values"] == ["4+"]


# --- the security line --------------------------------------------------------------------------
#
# The filter half of the query is per request; the permission half is not. Held as a property over
# every query, not a list of cases.

WIDENING_ATTEMPTS = [
    # The connectives.
    "tags:beach OR tags:city",
    "-tags",
    "-tags:beach",
    "tags:none",
    "tags:any",
    "-people -collections -tags",
    'tags:nosuchtag OR people:"Jane Doe" OR collections:"Best of"',
    "tags:none OR tags:any",
    # Values shaped like the query language they are bound into.
    "tags:') OR 1=1 --",
    'tags:"1=1"',
    "tags:1 OR 1",
    "tags:*",
    "tags:NEAR",
    "in:../..",
    # Values that could widen if compiled wrongly.
    "tags:",
    "rating:abc",
    "people:nobody",
]


@pytest.mark.parametrize("query", WIDENING_ATTEMPTS)
def test_no_filter_can_widen_what_a_viewer_may_see(
    client: TestClient, world: World, query: str
) -> None:
    """No filter widens what a viewer may see: the results are a subset of the unfiltered ones, for
    a guest shown exactly one file."""
    guest = sign_in(client, "guest")
    share(client, "item", world.beach, guest)

    unfiltered, _ = found(client)
    assert set(unfiltered) == {world.beach}, "the fixture no longer proves anything"

    filtered, total = found(client, q=query)
    assert set(filtered) <= set(unfiltered), f"{query!r} reached a row the viewer may not see"
    assert total == len(filtered)


def test_a_negation_cannot_turn_into_the_things_it_excludes(
    client: TestClient, world: World
) -> None:
    """`-tags:beach` is applied inside the permission rule, so a guest gets none of the unrelated
    files."""
    guest = sign_in(client, "guest")
    share(client, "item", world.beach, guest)

    assert found(client, q="-tags:beach") == ([], 0)
    assert found(client, q="-tags") == ([], 0)
    assert found(client, q="-people -tags -collections") == ([], 0)


def test_a_choice_cannot_reach_past_the_permission_rule(client: TestClient, world: World) -> None:
    """A run of ORs is parenthesised, so it cannot swallow the permission test after it."""
    guest = sign_in(client, "guest")
    share(client, "item", world.beach, guest)

    # `city` is on a file this guest may not see.
    ids, total = found(client, q="tags:beach OR tags:city")
    assert set(ids) == {world.beach}
    assert total == 1

    assert found(client, q="tags:city OR tags:nosuchtag") == ([], 0)


def test_a_vaulted_file_stays_concealed_behind_every_connective(
    client: TestClient, world: World
) -> None:
    """A vaulted file stays concealed behind every connective and curation queue."""
    sign_in(client, "admin")

    for query in ("-tags", "tags:none", "-tags -people -collections", "-tags OR -people"):
        ids, _ = found(client, q=query)
        assert world.vaulted not in ids, query


UNREADABLE = [
    "rating:abc",
    "rating:4x",
    "duration:soon",
    "added:yesterday",
    "type:sculpture",
    "fav:maybe",
    "rating:",
    "duration:",
    "tags:",
]


@pytest.mark.parametrize("query", UNREADABLE)
def test_a_value_nobody_could_act_on_returns_nothing_rather_than_everything(
    client: TestClient, world: World, query: str
) -> None:
    """An unreadable value returns nothing, not everything. Run as an admin, who sees three files,
    so the two answers differ."""
    sign_in(client, "admin")
    assert found(client) == ([world.private, world.walk, world.beach], 3), "the fixture changed"

    assert found(client, q=query) == ([], 0), query


def test_a_token_naming_nothing_costs_no_lookup(client: TestClient, world: World) -> None:
    """A bare `tags:` matches nothing without a catalog lookup, counted since the result is the
    same either way."""
    sign_in(client, "admin")
    access = client.app.state.access  # type: ignore[attr-defined]

    asked = 0
    real = access.suggest_tags

    async def counting(*args: object, **kwargs: object) -> object:
        nonlocal asked
        asked += 1
        return await real(*args, **kwargs)

    access.suggest_tags = counting
    try:
        assert found(client, q="tags:") == ([], 0)
        assert asked == 0

        # A token naming something still asks, so the count is meaningful.
        assert found(client, q="tags:beach") == ([world.beach], 1)
        assert asked == 1
    finally:
        access.suggest_tags = real


def test_an_order_the_server_does_not_offer_is_refused(client: TestClient, world: World) -> None:
    """An unknown order is refused, as the grid refuses one."""
    sign_in(client, "admin")
    assert client.get("/api/assets", params={"q": "sunset", "sort": "loudest"}).status_code == 422


def test_a_search_is_ordered_by_how_well_it_matched_unless_told_otherwise(
    client: TestClient, world: World
) -> None:
    """A search is ordered by match unless told otherwise; the grid keeps its own default."""
    sign_in(client, "admin")

    closest, _ = found(client, q="sunset")
    oldest, _ = found(client, q="sunset", sort="oldest")

    assert set(closest) == set(oldest) == {world.beach, world.walk}
    assert oldest == [world.beach, world.walk], "the fixture no longer pins an order"


def test_a_query_carrying_a_value_longer_than_anything_could_be_named_matches_nothing(
    client: TestClient, world: World
) -> None:
    """A value longer than any name matches nothing rather than being trimmed."""
    from sift.slices.search.filters import MAX_VALUE

    sign_in(client, "admin")
    assert found(client, q=f"tags:{'x' * (MAX_VALUE + 1)}") == ([], 0)


# --- what has been shared ---------------------------------------------------------------------


def test_the_sharing_filter_finds_what_was_decided_on_the_file(
    client: TestClient, world: World
) -> None:
    """`sharing:` finds what was decided on the file itself, not everything a folder decision
    reaches."""
    admin = sign_in(client, "admin")
    guest = sign_in(client, "guest")
    share(client, "item", world.beach, guest)
    restrict(client, "item", world.walk, guest)
    # On the folder: reaches everything under it, decided on none of them.
    restrict(client, "folder", world.holiday, guest)
    del admin
    sign_in(client, "admin")

    assert found(client, sharing="shared") == ([world.beach], 1)
    assert found(client, sharing="restricted") == ([world.walk], 1)

    everything, total = found(client)
    said_here = {world.beach, world.walk}
    quiet, quiet_total = found(client, sharing="none")
    assert set(quiet) == set(everything) - said_here
    assert quiet_total == total - len(said_here)


def test_a_guest_is_not_told_which_decisions_were_made_where(
    client: TestClient, world: World
) -> None:
    """`sharing:` is admin-only and matches nothing for a guest: how access was arranged is not
    theirs to read."""
    guest = sign_in(client, "guest")
    share(client, "item", world.beach, guest)
    sign_in(client, "guest")

    assert found(client) == ([world.beach], 1)
    assert found(client, sharing="shared") == ([], 0)


# --- what a word NAMES, for the band above the results ----------------------------------------


def test_a_surname_finds_the_person_on_the_results_path(client: TestClient, world: World) -> None:
    """A surname finds the person on the results path, not only their files."""
    sign_in(client, "admin")

    body = client.get("/api/search/named", params={"q": "doe"}).json()

    named = [(row["field"], row["value"]) for row in body["items"]]
    assert ("people", "Jane Doe") in named


def test_the_dropdown_stays_short_however_much_a_word_matches(
    client: TestClient, world: World
) -> None:
    """The dropdown matches anywhere in a name; a hard cap on rows and exact-first order keep it
    short."""
    sign_in(client, "admin")
    for index in range(MAX_SUGGESTIONS + 10):
        write(
            db_path(client),
            [
                (
                    "INSERT INTO tags (id, name, created_at) VALUES (?,?,?)",
                    (new_id(), f"seaside-{index}", EPOCH),
                )
            ],
        )

    body = client.get("/api/search/suggest", params={"q": "side"}).json()

    assert len(body["matches"]) <= MAX_SUGGESTIONS
    assert body["matches"], "a word in the middle of a name still has to find it"


def test_the_band_answers_with_the_things_a_word_names(client: TestClient, world: World) -> None:
    """The band names every kind the rail has a page for, each with its filter field."""
    sign_in(client, "admin")

    body = client.get("/api/search/named", params={"q": "beach"}).json()

    assert ("tags", "beach") in [(row["field"], row["value"]) for row in body["items"]]


def test_a_guest_is_told_about_nothing_they_could_not_search_for(
    client: TestClient, world: World
) -> None:
    """The band reads the same scoped suggesters as the dropdown, so a guest is told nothing a
    search would not return."""
    sign_in(client, "guest")

    body = client.get("/api/search/named", params={"q": "doe"}).json()

    assert body["items"] == []


def test_the_band_does_not_offer_a_root_folder_it_could_not_filter_on(
    client: TestClient, world: World
) -> None:
    """The band does not offer a library root, whose `in:` chip would have no label."""
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?,?,?,?,?)",
                (new_id(), world.root, None, "", "beachfront"),
            ),
        ],
    )

    body = client.get("/api/search/named", params={"q": "beach"}).json()

    assert [row for row in body["items"] if row["field"] == "tags"], "the tag still answers"
    assert not [row for row in body["items"] if not row["value"]], (
        f"the band offered an entity with no name: {body['items']}"
    )


def test_the_named_band_carries_what_names_each_cover(client: TestClient, world: World) -> None:
    """Band rows carry what names each cover, as the entity walls do, so the browser may keep them
    (`kernel/covers.py`)."""
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "UPDATE people SET cover_asset_id = ?, cover_at_ms = ? WHERE id = ?",
                (world.beach, 1500, world.person),
            )
        ],
    )

    person = next(
        row
        for row in client.get("/api/search/named", params={"q": "doe"}).json()["items"]
        if row["field"] == "people"
    )
    assert (person["cover_asset_id"], person["cover_at_ms"], person["cover_upload_id"]) == (
        world.beach,
        1500,
        None,
    )
    assert person["art"], "the account's token travels beside the cover, as on every wall"
    assert person["icon"] is None, "only a Site is answered with a shipped logo"

    site = next(
        row
        for row in client.get("/api/search/named", params={"q": "tiktok"}).json()["items"]
        if row["field"] == "sites"
    )
    # The same two keys the Sites wall and cover route use.
    assert site["icon"] == site_icons.icon_token(None, "TikTok")
    assert site["art"]


def test_a_named_row_with_no_cover_carries_no_token(client: TestClient, world: World) -> None:
    """A folder has no cover address, so it carries no token."""
    sign_in(client, "admin")

    rows = client.get("/api/search/named", params={"q": "clips"}).json()["items"]

    folders = [row for row in rows if row["field"] == "in"]
    assert folders, rows
    assert all(row["art"] is None and row["icon"] is None for row in folders)


def _person(client: TestClient, name: str) -> str:
    """Somebody in the library, by name, through the scoped suggester."""
    person = new_id()
    write(
        db_path(client),
        [("INSERT INTO people (id, name, created_at) VALUES (?,?,?)", (person, name, EPOCH))],
    )
    return person


def test_the_dropdown_finds_a_name_by_its_middle(client: TestClient, world: World) -> None:
    """The dropdown finds a name by its middle, as the results band does."""
    sign_in(client, "admin")
    _person(client, "Reya Solberg")

    answer = client.get("/api/search/suggest", params={"q": "solb"})
    assert answer.status_code == 200, answer.text
    assert "Reya Solberg" in [row["value"] for row in answer.json()["matches"]]


def test_a_word_at_the_start_still_sorts_above_one_in_the_middle(
    client: TestClient, world: World
) -> None:
    """A name starting with the word sorts above one containing it."""
    sign_in(client, "admin")
    _person(client, "Sonia Vance")
    _person(client, "Reya Solberg")

    answer = client.get("/api/search/suggest", params={"q": "so"})
    values = [row["value"] for row in answer.json()["matches"]]
    assert values.index("Sonia Vance") < values.index("Reya Solberg")


def test_a_library_folder_is_offered_by_its_own_name(client: TestClient, world: World) -> None:
    """A library folder is offered by its own name, which `in:` resolves as well as a path."""
    sign_in(client, "admin")
    root, folder = new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?,?,?,?)",
                (root, "redgifs archive", "/media/archive", EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?,?,?,?,?)",
                (folder, root, None, "", "redgifs archive"),
            ),
        ],
    )

    answer = client.get("/api/search/suggest", params={"q": "arch"})
    assert answer.status_code == 200, answer.text
    offered = [row["value"] for row in answer.json()["matches"] if row["field"] == "in"]
    assert offered == ["redgifs archive"]

    # The offered value filters to that folder.
    listed = client.get("/api/assets", params={"in": "redgifs archive", "limit": "1"})
    assert listed.status_code == 200, listed.text


#: A song on a file, written as every writer does: a `songs` row and the file's membership.
_A_SONG = "01KZSEARCHS0NG000000000000"


_SONG_ROW = (
    "INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, 'Paper Lantern',"
    " 'paper lantern', 0)"
)


_ON_THE_SONG = "INSERT INTO song_files (asset_id, song_id, added_at) VALUES (?, ?, 0)"


def test_a_song_is_offered_by_a_bare_word(client: TestClient, world: World) -> None:
    """A song is offered by a bare word, from the scoped Songs wall."""
    sign_in(client, "admin")
    write(db_path(client), [(_SONG_ROW, (_A_SONG,)), (_ON_THE_SONG, (world.beach, _A_SONG))])

    body = client.get("/api/search/suggest", params={"q": "lantern"}).json()
    songs = [row for row in body["matches"] if row["field"] == "songs"]
    assert [(row["value"], row["id"]) for row in songs] == [("Paper Lantern", _A_SONG)]
    # The count is the asker's, from the wall's statement.
    assert songs[0]["count"] == 1


def test_a_track_on_a_file_somebody_may_not_see_is_not_offered_to_them(
    client: TestClient, world: World
) -> None:
    """A song on a file a guest may not see is not offered to them, nor counted."""
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    write(db_path(client), [(_SONG_ROW, (_A_SONG,)), (_ON_THE_SONG, (world.private, _A_SONG))])
    restrict(client, "item", world.private, guest)

    sign_in(client, "guest")
    body = client.get("/api/search/suggest", params={"q": "lantern"}).json()
    assert [row for row in body["matches"] if row["field"] == "songs"] == []

    # The other side of the branch, so the assertion above cannot pass by returning nothing.
    sign_in(client, "admin")
    seen = client.get("/api/search/suggest", params={"q": "lantern"}).json()
    assert [row["value"] for row in seen["matches"] if row["field"] == "songs"] == ["Paper Lantern"]


def test_a_track_can_be_searched_for_as_a_filter_and_not_only_offered(
    client: TestClient, world: World
) -> None:
    """A song can be asked for as a filter, matched anywhere in the stored line."""
    sign_in(client, "admin")
    write(
        db_path(client),
        [("UPDATE assets SET music = ? WHERE id = ?", ("Example Band - Low Tide", world.beach))],
    )

    found = client.get("/api/assets", params={"q": 'music:"low tide"'})

    assert found.status_code == 200, found.text
    assert [one["id"] for one in found.json()["items"]] == [world.beach]


def test_a_track_nothing_is_set_to_finds_nothing(client: TestClient, world: World) -> None:
    # The other side of the branch, so the test above cannot pass by matching everything.
    sign_in(client, "admin")
    write(
        db_path(client),
        [("UPDATE assets SET music = ? WHERE id = ?", ("Example Band - Low Tide", world.beach))],
    )

    found = client.get("/api/assets", params={"q": "music:nobody"})

    assert found.json()["items"] == []


def test_a_field_this_version_does_not_know_completes_to_nothing_rather_than_refusing(
    client: TestClient, world: World
) -> None:
    """A field this version does not know completes to nothing rather than refusing; field and
    prefix arrive apart and are never parsed."""
    sign_in(client, "admin")

    body = client.get(
        "/api/search/suggest", params={"field": "no_such_dimension", "prefix": "ja"}
    ).json()

    assert body["matches"] == []
    assert body["token"] == "no_such_dimension"
    assert body["for_query"] == "ja"


def test_asking_about_one_field_answers_with_that_fields_matches_alone(
    client: TestClient, world: World
) -> None:
    """Asking about one field answers with that field's matches alone."""
    sign_in(client, "admin")

    body = client.get("/api/search/suggest", params={"field": "people", "prefix": ""}).json()

    assert body["token"] == "people"
    assert {row["field"] for row in body["matches"]} <= {"people"}
