# SPDX-License-Identifier: AGPL-3.0-or-later
"""A site's history, over HTTP.

The read is proved in the kernel beside the tables it gathers. What is proved here is the three
things only the route decides: that a site this user may not be shown answers the same 404 an id
that was never minted gets, that the limit is a bounded query parameter, and that the reply is the
shape a client was promised.

The one case worth its own test is the empty one. `sites` records no moment, so a site nothing
has been filed under has a genuinely empty thread, and a route that answered 404 for that would be
indistinguishable from the refusal above.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    db_path,
    make_username,
    read,
    sign_in,
    write,
)

pytestmark = [pytest.mark.integration]


def a_site(client: TestClient, name: str = "Studio") -> str:
    """A site with one username on it, seeded the way a download seeds them."""
    make_username(client, name, "harlowquin")
    rows = read(db_path(client), "SELECT id FROM sites WHERE name = ?", (name,))
    found = rows[0]["id"]
    assert isinstance(found, str)
    return found


def test_a_site_with_a_username_on_it_says_so(client: TestClient) -> None:
    sign_in(client, "admin")
    site = a_site(client)

    answer = client.get(f"/api/sites/{site}/history")

    assert answer.status_code == 200
    events = answer.json()
    assert [event["kind"] for event in events] == ["filed"]
    # Named, and the name is the way to it: "1 username added to it" would not say which one.
    assert events[0]["what"] == "The username harlowquin was added to it"
    assert [(one["kind"], one["text"]) for one in events[0]["pieces"] if one["kind"]] == [
        ("username", "harlowquin")
    ]
    assert events[0]["via"] is None


def test_a_site_nothing_has_happened_to_is_an_empty_thread_and_not_a_404(
    client: TestClient,
) -> None:
    """`sites` records no moment at all, so there is no arrival line to draw. An empty list is
    the honest answer and the 404 is reserved for a site this user may not be shown."""
    sign_in(client, "admin")
    site = new_id()
    write(db_path(client), [("INSERT INTO sites (id, name) VALUES (?, 'Quiet')", (site,))])

    answer = client.get(f"/api/sites/{site}/history")

    assert answer.status_code == 200
    assert answer.json() == []


def test_a_site_that_was_never_minted_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")

    assert client.get(f"/api/sites/{NEVER_EXISTED}/history").status_code == 404


def test_signing_out_is_refused_rather_than_answered(client: TestClient) -> None:
    assert client.get(f"/api/sites/{new_id()}/history").status_code == 401


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_a_limit_outside_the_bound_is_refused_rather_than_clamped(
    client: TestClient, limit: int
) -> None:
    sign_in(client, "admin")
    site = a_site(client)

    assert client.get(f"/api/sites/{site}/history?limit={limit}").status_code == 422
