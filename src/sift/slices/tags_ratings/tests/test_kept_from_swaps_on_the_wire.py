# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tag marked "Don't swap" says so on every row a screen is handed.

The people slice's argument (`slices/people/tests/test_kept_from_swaps_on_the_wire.py`): swap mode
draws a mark on every card that will not go, so the mark rides on the row; the wall, the by-id read
and the write reply are three statements and a screen reads all three.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.tags_ratings.tests.conftest import db_path, make_tag, sign_in, write

_MARK_A_TAG = "UPDATE tags SET keep_from_swaps = 1 WHERE id = ?"


def test_a_tag_marked_dont_swap_says_so_on_the_wall_its_page_and_a_rename(
    client: TestClient,
) -> None:
    sign_in(client)
    marked = make_tag(client, "kept-here")
    other = make_tag(client, "ordinary")
    write(db_path(client), [(_MARK_A_TAG, (marked,))])

    rows = {row["id"]: row for row in client.get("/api/tags").json()["items"]}

    assert (rows[marked]["keep_from_swaps"], rows[other]["keep_from_swaps"]) == (True, False)
    assert rows[marked]["keep_local"] is False
    assert client.get(f"/api/tags/{marked}").json()["keep_from_swaps"] is True
    answer = client.put(f"/api/tags/{marked}", json={"name": "kept-here-still"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["keep_from_swaps"] is True
