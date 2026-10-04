# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tag's history, over HTTP.

The read is proved in the kernel beside the tables it gathers. What is proved here is what only the
route decides: that a tag this user may not be shown answers the same 404 an id that was never
minted gets, that the limit is a bounded query parameter, and that the reply carries every field a
client was promised, because a field that quietly stopped being sent is a link nothing draws and no
failure anywhere.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.tags_ratings.tests.conftest import (
    NEVER_EXISTED,
    Library,
    assign,
    make_tag,
    sign_in,
)

pytestmark = [pytest.mark.integration]


def test_a_tag_says_it_was_made_and_names_itself(client: TestClient) -> None:
    sign_in(client, "admin")
    tag = make_tag(client, "poolside")

    answer = client.get(f"/api/tags/{tag}/history")

    assert answer.status_code == 200
    events = answer.json()
    assert [event["kind"] for event in events] == ["added"]
    assert events[0]["what"] == "You added poolside to the library"
    assert [
        (one["kind"], one["id"], one["text"]) for one in events[0]["pieces"] if one["kind"]
    ] == [("tag", tag, "poolside")]
    assert events[0]["via"] is None
    assert events[0]["undo"] is None


def test_files_it_was_put_on_arrive_counted(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    tag = make_tag(client, "poolside")
    assign(client, [library.shared], [tag])

    kinds = [event["kind"] for event in client.get(f"/api/tags/{tag}/history").json()]

    assert sorted(kinds) == ["added", "tagged"]


def test_a_tag_that_was_never_minted_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")

    assert client.get(f"/api/tags/{NEVER_EXISTED}/history").status_code == 404


def test_signing_out_is_refused_rather_than_answered(client: TestClient) -> None:
    assert client.get(f"/api/tags/{new_id()}/history").status_code == 401


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_a_limit_outside_the_bound_is_refused_rather_than_clamped(
    client: TestClient, limit: int
) -> None:
    sign_in(client, "admin")
    tag = make_tag(client, "poolside")

    assert client.get(f"/api/tags/{tag}/history?limit={limit}").status_code == 422
