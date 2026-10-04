# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tag kept local says so on every row a screen is handed.

The argument is the people slice's, word for word. See
`slices/people/tests/test_kept_local_on_the_wire.py`. A MENU asks the one route that answers about
one row as it opens; a MARK is drawn on sixty cards with nobody pressing anything, so it reads the
row. The write reply is held here for the same reason it is held there: a screen puts it in place
of the row it was holding, so a field left out of it is a mark a rename takes off the card.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.tags_ratings.tests.conftest import db_path, make_tag, sign_in, write

_KEEP_A_TAG = "UPDATE tags SET keep_local = 1 WHERE id = ?"


def test_a_tag_kept_local_says_so_on_the_wall(client: TestClient) -> None:
    sign_in(client)
    tag = make_tag(client, "kept-here")
    write(db_path(client), [(_KEEP_A_TAG, (tag,))])

    rows = client.get("/api/tags").json()["items"]

    assert [row["keep_local"] for row in rows if row["id"] == tag] == [True]


def test_a_tag_nobody_kept_local_says_so_too(client: TestClient) -> None:
    """The other half, so the field is not simply always true."""
    sign_in(client)
    tag = make_tag(client, "ordinary")

    rows = client.get("/api/tags").json()["items"]

    assert [row["keep_local"] for row in rows if row["id"] == tag] == [False]


def test_a_tag_kept_local_says_so_on_its_own_page(client: TestClient) -> None:
    sign_in(client)
    tag = make_tag(client, "kept-here")
    write(db_path(client), [(_KEEP_A_TAG, (tag,))])

    assert client.get(f"/api/tags/{tag}").json()["keep_local"] is True


def test_renaming_a_tag_hands_the_mark_back(client: TestClient) -> None:
    sign_in(client)
    tag = make_tag(client, "kept-here")
    write(db_path(client), [(_KEEP_A_TAG, (tag,))])

    answer = client.put(f"/api/tags/{tag}", json={"name": "kept-here-still"})

    assert answer.status_code == 200, answer.text
    assert answer.json()["keep_local"] is True
