# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person's history, over HTTP.

The read itself is proved in the kernel, beside the tables it gathers. What is proved here is the
three things only the route decides: that somebody this user may not be shown answers the same
404 an id that was never minted gets, that the limit is a bounded query parameter rather than
anything a caller can ask for, and that what comes back on the wire is the shape a client was
promised.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
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

pytestmark = [pytest.mark.integration]


def born(client: TestClient, person: str) -> int:
    """When this person's row says they were created.

    Read back rather than assumed. A person is created through the API and the row takes the real
    clock, so a decision stamped at a fixed epoch would sort BEFORE their own arrival, which is
    the history telling the truth about the fixture rather than a fault in the read.
    """
    rows = read(db_path(client), "SELECT created_at FROM people WHERE id = ?", (person,))
    when = rows[0]["created_at"]
    assert isinstance(when, int)
    return when


def test_somebody_says_they_were_added_even_when_nothing_else_has_happened(
    client: TestClient,
) -> None:
    """One line, in the app's own voice, with a time on it, and it says WHO.

    Their arrival is read off their own row, so it is the event every person in the library has,
    which makes an empty list a reliable sign that something went wrong rather than somebody nobody
    has touched.

    !! THE ACTOR IS `you`: v41 of the catalog records which of the three makers made a person, and
    this one was typed into the People screen by the user asking. Somebody made before that
    version reads `somebody`, which is the only honest answer a row that recorded no maker has.
    """
    sign_in(client, "admin")
    person = make_person(client, "Neve Alder")

    answer = client.get(f"/api/people/{person}/history")

    assert answer.status_code == 200
    events = answer.json()
    assert [event["kind"] for event in events] == ["added"]
    assert events[0]["what"] == "You added Neve Alder to the library"
    assert events[0]["actor"] == "you"
    assert events[0]["undo"] is None
    assert events[0]["reversed"] is False


def test_files_named_under_them_arrive_counted_and_dated(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    person = make_person(client, "Harlow Quin")
    named_at = born(client, person) + 60
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_people (asset_id, person_id, source, decided_at)"
                " VALUES (?, ?, 'folder', ?)",
                (library.shared, person, named_at),
            ),
        ],
    )

    events = client.get(f"/api/people/{person}/history").json()

    assert [event["kind"] for event in events] == ["added", "named"]
    # No folder has been answered as them, so the line says only what it can support
    # (see `sentences.named_on`, which has an arm for each of none, one and several).
    assert events[1]["what"] == "Sift named them on 1 file from a folder name"
    assert events[1]["at"] == named_at
    assert events[1]["actor"] == "sift"
    assert events[1]["actor_name"] == "Sift"


def test_somebody_this_user_may_not_be_shown_is_a_404_and_not_an_empty_list(
    client: TestClient,
) -> None:
    """The same answer an id that was never minted gets.

    An empty list would say they exist and nothing has happened to them, which is a different
    answer and one this user is not entitled to.
    """
    sign_in(client, "admin")
    hidden = make_person(client, "Marlow Vane", vault=True)

    client.cookies.clear()
    sign_in(client, "guest")

    refused = client.get(f"/api/people/{hidden}/history")
    missing = client.get(f"/api/people/{NEVER_EXISTED}/history")

    assert refused.status_code == 404
    assert missing.status_code == 404
    assert refused.json() == missing.json()


def test_a_guest_may_read_a_history_of_somebody_they_can_see(
    client: TestClient, library: Library
) -> None:
    """Authenticated, not admin: what was decided about a person is part of what their record says.

    Nothing in this history is about another USER, which is what a file's history withholds. The
    guest reaches them the one way a guest reaches anybody: through a file they were given.
    """
    sign_in(client, "admin")
    person = make_person(client, "Rowan Pike")
    assign(client, [library.shared], [person])

    guest = sign_in(client, "guest", who="two")
    share(client, library.shared, guest)

    answer = client.get(f"/api/people/{person}/history")

    assert answer.status_code == 200
    # Sorted rather than compared in order: both rows take the real clock a moment apart, and a
    # machine's clock can step backwards, so which of the two is first is not a property of the
    # read. The ORDER is asserted in the kernel tests, where the times are fixed.
    assert sorted(event["kind"] for event in answer.json()) == ["added", "named"]


def test_signing_out_is_refused_rather_than_answered(client: TestClient) -> None:
    person_id = new_id()

    assert client.get(f"/api/people/{person_id}/history").status_code == 401


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_a_limit_outside_the_bound_is_refused_rather_than_clamped(
    client: TestClient, limit: int
) -> None:
    """A query parameter is something a caller wrote down, so it is answered rather than corrected.

    The kernel clamps as well, which is what protects a direct call; this is the door saying no.
    """
    sign_in(client, "admin")
    person = make_person(client, "Neve Alder")

    assert client.get(f"/api/people/{person}/history?limit={limit}").status_code == 422


def test_the_limit_keeps_the_newest(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    person = make_person(client, "Neve Alder")
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_people (asset_id, person_id, source, decided_at)"
                " VALUES (?, ?, NULL, ?)",
                (library.shared, person, born(client, person) + 86400),
            ),
        ],
    )

    events = client.get(f"/api/people/{person}/history?limit=1").json()

    assert [event["kind"] for event in events] == ["named"]


def test_a_decision_of_a_kind_that_can_never_be_undone_offers_no_button(
    client: TestClient,
) -> None:
    """Only the registry knows which kinds of decision are final (`copies` releases a copy from a
    disk, which is gone), and only this route can ask it. The same decision under a reversible
    queue offers the button, so this cannot pass with the whole read broken."""
    sign_in(client, "admin")
    person = make_person(client, "Neve Alder")
    at = born(client, person) + 60
    final, ordinary = new_id(), new_id()
    write(
        db_path(client),
        [
            statement
            for decision, queue in ((final, "copies"), (ordinary, "duplicates"))
            for statement in (
                (
                    "INSERT INTO workbench_decisions"
                    " (id, queue, user_id, title, detail, payload, decided_at, reversed_at)"
                    " VALUES (?, ?, NULL, 'Kept one of a group', 'It did something.', '{}', ?,"
                    " NULL)",
                    (decision, queue, at),
                ),
                (
                    "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
                    " VALUES (?, 'person', ?)",
                    (decision, person),
                ),
            )
        ],
    )

    offered = {
        one["undo"]["id"] if one["undo"] else None
        for one in client.get(f"/api/people/{person}/history").json()
        if one["kind"] == "decided"
    }

    assert offered == {None, ordinary}
