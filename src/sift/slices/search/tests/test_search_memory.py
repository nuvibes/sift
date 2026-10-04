# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the search box remembers: the searches and picks offered back, and their record."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from sift.kernel.db import Database
from sift.slices.search.tests.conftest import (
    World,
    db_path,
    found,
    read,
    reindex,
    sign_in,
    write,
)

# --- FTS5 syntax, which is text and never grammar ----------------------------------------------


SYNTAX = [
    '"',
    '""',
    "*",
    "NEAR",
    "OR",
    "AND",
    "NOT",
    "^beach",
    "-beach",
    "beach OR sunset",
    'beach" OR "x',
    "NEAR(beach sunset, 2)",
    "filename:beach",
    "{filename}:beach",
    "beach*",
    "(",
    ")",
    "\x00",
    "%",
    "_",
    "'; DROP TABLE assets; --",
]


@pytest.mark.parametrize("typed", SYNTAX, ids=range(len(SYNTAX)))
def test_a_query_full_of_search_syntax_is_answered_rather_than_obeyed(
    client: TestClient, world: World, typed: str
) -> None:
    """None of it errors, and none of it is executed as an operator.

    The library is still there afterwards, which is the assertion the last case is for: a query
    that reached the database as anything but a bound value would have had four assets to remove.
    """
    sign_in(client, "admin")
    response = client.get("/api/assets", params={"q": typed})
    assert response.status_code == 200, response.text

    assert read(db_path(client), "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 4


def test_operator_words_are_matched_as_the_letters_they_are(
    client: TestClient, world: World
) -> None:
    """Proof that the escaping is doing something rather than the queries above being harmless.

    A file whose name contains the words NEAR and AND is found by searching for them; read as
    operators they would have meant proximity and conjunction, and the query would have matched
    on either side of them and returned more. Both words are at least three characters, which is
    what the trigram index can hold: `OR` is two, and would prove nothing either way.
    """
    sign_in(client, "admin")
    write(
        db_path(client),
        [
            (
                "UPDATE assets SET original_filename = 'NEAR AND NOT b.mp4' WHERE id = ?",
                (world.private,),
            )
        ],
    )
    reindex(db_path(client), asset_id=world.private)

    assert found(client, q="NEAR AND")[0] == [world.private]


def remember(client: TestClient, query: str) -> Response:
    """Say that this user made this search, the way the box does when one is submitted.

    A write rather than a side effect of looking: the read behind the results answers every screen
    made of tiles, so recording there would fill the list with an entry for opening the library.
    """
    answer: Response = client.post(
        "/api/search/history", json={"kind": "query", "subject": query, "label": query}
    )
    return answer


def remember_pick(client: TestClient, kind: str, subject: str, label: str) -> Response:
    """Say that this user picked this thing out of the dropdown, the way the box does."""
    answer: Response = client.post(
        "/api/search/history", json={"kind": kind, "subject": subject, "label": label}
    )
    return answer


def recalled(client: TestClient) -> list[dict[str, str]]:
    """The memory as the dropdown reads it with an empty box, newest first."""
    answer = client.get("/api/search/suggest", params={"q": ""})
    assert answer.status_code == 200, answer.text
    rows: list[dict[str, str]] = answer.json()["recent"]
    return rows


def remembered(client: TestClient) -> list[str]:
    """Just the words on each row, for the assertions that are only about the order."""
    return [row["label"] for row in recalled(client)]


# --- history ----------------------------------------------------------------------------------


def test_a_search_is_remembered_and_offered_back(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    remember(client, "tags:beach")
    remember(client, "sunset")

    assert remembered(client) == ["sunset", "tags:beach"]


def test_a_picked_thing_is_remembered_as_what_it_is(client: TestClient, world: World) -> None:
    """The other half of the memory.

    Picking a person out of the dropdown takes you to them, and without this the list under Recent
    would hold only what had been typed and entered. What is kept is what it IS (which one, and
    what it was called) and never the address, because an address is a route and routes move.
    """
    sign_in(client, "admin")
    remember(client, "sunset")
    assert remember_pick(client, "people", "p1", "Orla Fennimore").status_code == 204

    assert recalled(client)[0] == {
        "kind": "people",
        "subject": "p1",
        "label": "Orla Fennimore",
    }
    # Beside the typed searches rather than in a list of its own: the dropdown shows one list, so
    # the newest thing is the newest thing whichever kind it is.
    assert remembered(client) == ["Orla Fennimore", "sunset"]


def test_the_same_thing_picked_twice_is_one_entry_moved_to_the_top(
    client: TestClient, world: World
) -> None:
    """Somebody opens the same person most days. Fifty rows of one name is a memory with nothing in
    it."""
    sign_in(client, "admin")
    remember_pick(client, "people", "p1", "Orla Fennimore")
    remember(client, "sunset")
    remember_pick(client, "people", "p1", "Orla Fennimore")

    assert remembered(client) == ["Orla Fennimore", "sunset"]


def test_a_renamed_thing_shows_its_new_name(client: TestClient, world: World) -> None:
    """The subject is the identity and the label is only ever shown, which is why picking somebody
    again after a rename updates the words rather than adding a second row under the old ones."""
    sign_in(client, "admin")
    remember_pick(client, "people", "p1", "Orla Fennimore")
    remember_pick(client, "people", "p1", "Orla Fennimore-Kane")

    assert remembered(client) == ["Orla Fennimore-Kane"]


def test_a_pick_with_nothing_in_it_is_not_written(client: TestClient, world: World) -> None:
    """A row with no words is a blank line in somebody's memory, and a row with nothing identifying
    it is one that can be drawn and never followed. Neither is worth a place in the fifty.

    Both halves, and a real one after them: without the last assertion this would pass against a
    route that wrote nothing at all.
    """
    sign_in(client, "admin")

    assert remember_pick(client, "people", "", "Orla Fennimore").status_code == 204
    assert remember_pick(client, "people", "p1", "   ").status_code == 204
    assert remembered(client) == []

    assert remember_pick(client, "people", "p1", "Orla Fennimore").status_code == 204
    assert remembered(client) == ["Orla Fennimore"]


def test_a_kind_of_thing_the_box_does_not_know_is_refused(client: TestClient, world: World) -> None:
    """The client is what turns a kind into somewhere to go, so a kind nothing recognises is a row
    that can never be drawn, sitting in a fifty-row memory in place of something that works.

    A real field is the control. Without it this would pass against a route that refused
    everything, including the kinds it is supposed to take.
    """
    sign_in(client, "admin")

    assert remember_pick(client, "not-a-thing", "x", "X").status_code == 422
    assert remembered(client) == []

    assert remember_pick(client, "sites", "t1", "TikTok").status_code == 204
    assert remembered(client) == ["TikTok"]


def test_a_picked_thing_comes_back_under_what_it_says(client: TestClient, world: World) -> None:
    """Filtered on the words the row shows, because that is what somebody typing is looking at.

    Matching on the subject instead would hide a person whose name plainly begins with what was
    typed, because their subject is an id nobody has ever seen.
    """
    sign_in(client, "admin")
    remember_pick(client, "people", "01HZZZ", "Orla Fennimore")

    body = client.get("/api/search/suggest", params={"q": "Orla"}).json()
    assert [row["label"] for row in body["recent"]] == ["Orla Fennimore"]
    assert client.get("/api/search/suggest", params={"q": "01HZ"}).json()["recent"] == []


def test_looking_at_results_writes_no_history(client: TestClient, world: World) -> None:
    """Looking at results writes no history; a search is recorded only by a posted write."""
    sign_in(client, "admin")
    response = client.get("/api/assets", params={"q": "sunset"})

    assert response.status_code == 200  # the search itself still runs
    assert remembered(client) == []  # nothing was written


def test_an_empty_search_is_not_remembered(client: TestClient, world: World) -> None:
    """An empty box is not a search, and filling somebody's history with blanks every time they
    cleared it would be its own small bug."""
    sign_in(client, "admin")
    remember(client, "")
    remember(client, "   ")

    assert remembered(client) == []


def test_the_same_search_twice_is_one_entry_moved_to_the_top(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    for query in ("beach", "sunset", "beach"):
        remember(client, query)

    assert remembered(client) == ["beach", "sunset"]


def events(client: TestClient) -> list[tuple[str, str, object, object]]:
    """Every recorded search, oldest first, read straight off the table.

    Off the table because no route reads it. The record has to be right from the moment it is
    written, because the moment is gone the instant the next search lands.
    """

    async def run() -> list[tuple[str, str, object, object]]:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            found = await database.fetch_all(
                "SELECT kind, subject, results, opened_id FROM search_events ORDER BY id"
            )
            return [
                (str(row["kind"]), str(row["subject"]), row["results"], row["opened_id"])
                for row in found
            ]
        finally:
            await database.close()

    return asyncio.run(run())


def test_a_search_is_recorded_beside_the_memory_of_it(client: TestClient, world: World) -> None:
    """Two tables from one act, and neither can answer the other's question.

    The memory is a recents list by design (one row per query, unique, trimmed to fifty), so it
    holds the LAST time each search was made and nothing about how often. Running the same search
    twice moves one row; it must write two records.
    """
    sign_in(client, "admin")
    assert remember(client, "beach").status_code == 204
    assert remember(client, "beach").status_code == 204

    assert remembered(client) == ["beach"]
    assert events(client) == [
        ("query", "beach", None, None),
        ("query", "beach", None, None),
    ]


def test_a_search_records_what_it_found_and_a_pick_records_what_was_opened(
    client: TestClient, world: World
) -> None:
    """The two facts the recents list has nowhere to put.

    A search that found nothing is the interesting kind and is indistinguishable afterwards from
    one that found everything. A pick IS an opening, so the thing picked is the thing opened.
    """
    sign_in(client, "admin")
    client.post(
        "/api/search/history",
        json={"kind": "query", "subject": "nothing here", "label": "nothing here", "results": 0},
    )
    client.post(
        "/api/search/history",
        json={"kind": "people", "subject": world.person, "label": "Orla Fennimore"},
    )

    assert events(client) == [
        ("query", "nothing here", 0, None),
        ("people", world.person, None, world.person),
    ]


def test_a_record_goes_with_the_user_that_made_it(client: TestClient, world: World) -> None:
    """The key cascades, which is the deliberate forget: a user removed takes its searches."""
    guest = sign_in(client, "guest")
    remember(client, "theirs")
    assert len(events(client)) == 1

    async def remove() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            await database.execute("DELETE FROM users WHERE id = ?", (guest,))
        finally:
            await database.close()

    asyncio.run(remove())

    assert events(client) == []


def test_clearing_the_memory_forgets_the_record_too(client: TestClient, world: World) -> None:
    """Somebody who empties their searches means every trace of them: the dropdown's memory, the
    record of each search and what was opened from one. Leaving the record would keep what they
    asked to forget. Their saved searches stay: each was kept on purpose."""
    sign_in(client, "admin")
    remember(client, "beach")
    remember(client, "sunset")
    assert len(events(client)) == 2

    assert client.delete("/api/search/history", params={"q": "beach"}).status_code == 204
    assert [event[1] for event in events(client)] == ["sunset"]

    assert client.delete("/api/search/history").status_code == 204
    assert remembered(client) == []
    assert events(client) == []


def test_history_belongs_to_the_account_that_made_it(client: TestClient, world: World) -> None:
    """One user's history is never a window into another's."""
    sign_in(client, "admin")
    remember(client, "something private")

    sign_in(client, "guest")
    assert remembered(client) == []


def test_history_is_clearable_whole_or_one_entry_at_a_time(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    for query in ("beach", "sunset"):
        remember(client, query)

    assert client.delete("/api/search/history", params={"q": "beach"}).status_code == 204
    assert remembered(client) == ["sunset"]

    assert client.delete("/api/search/history").status_code == 204
    assert remembered(client) == []


def test_clearing_one_account_s_history_leaves_another_s_alone(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    remember(client, "kept")

    sign_in(client, "guest")
    remember(client, "dropped")
    client.delete("/api/search/history")

    sign_in(client, "admin")
    assert remembered(client) == ["kept"]


def test_the_dropdown_shows_recent_searches_for_a_word_that_names_nothing(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    remember(client, "zzz note")

    # A bare word matches across the catalog first (the next test). Only when it names no tag,
    # person, site or collection does the dropdown fall back to the recents it is a prefix of.
    body = client.get("/api/search/suggest", params={"q": "zzz"}).json()
    assert [row["label"] for row in body["recent"]] == ["zzz note"]
    assert body["matches"] == []
