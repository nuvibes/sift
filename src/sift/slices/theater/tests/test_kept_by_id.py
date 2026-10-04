# SPDX-License-Identifier: AGPL-3.0-or-later
"""A saved wall keeps each thing its cells name by id, and reads back under today's names.

A cell's source is typed filter text, and kept as typed it would name a tag by NAME: rename the
tag and the wall would go on asking for a name nothing has, so the cell would draw nothing and
still read the old name. These hold both halves through the routes: what is stored, and what is
read back after a rename.
"""

from __future__ import annotations

# The stored row is the subject: what the cell KEEPS, read past the route that renames it on the
# way out, and a tag row seeded without the tag routes. A scratch test library, no application
# write to race.
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.theater.tests.conftest import cell, db_path, sign_in, wall

pytestmark = pytest.mark.integration

ARRANGEMENTS = "/api/theater/arrangements"


def _tag(client: TestClient, name: str) -> str:
    identifier = new_id()
    with sqlite3.connect(
        db_path(client)
    ) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        connection.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (identifier, name)
        )
    return identifier


def _stored_sources(client: TestClient) -> list[str]:
    with sqlite3.connect(
        db_path(client)
    ) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        rows = connection.execute("SELECT source FROM theater_cells ORDER BY position").fetchall()
    return [str(row[0]) for row in rows]


def test_a_cell_is_kept_by_id_and_reads_under_the_name_it_has_today(client: TestClient) -> None:
    sign_in(client)
    harbour = _tag(client, "Harbour")
    sent = wall(cells=[cell("tags:Harbour dusk"), cell("people:nobody-by-that-name")])
    assert client.post(ARRANGEMENTS, json=sent).status_code == 201

    stored = _stored_sources(client)
    assert harbour in stored[0]
    assert "Harbour" not in stored[0]
    # A name that names nothing is kept as it was typed: there is no id to keep.
    assert stored[1] == "people:nobody-by-that-name"

    with sqlite3.connect(
        db_path(client)
    ) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        connection.execute("UPDATE tags SET name = 'Quayside' WHERE id = ?", (harbour,))

    read = client.get(ARRANGEMENTS).json()["items"][0]["cells"]
    assert "tags:Quayside" in read[0]["source"]
    assert "dusk" in read[0]["source"]
    assert harbour not in read[0]["source"]
    assert read[1]["source"] == "people:nobody-by-that-name"


def test_changing_a_wall_keeps_its_cells_by_id_too(client: TestClient) -> None:
    sign_in(client)
    harbour = _tag(client, "Harbour")
    saved = client.post(ARRANGEMENTS, json=wall()).json()
    changed = wall(cells=[cell("tags:Harbour"), cell()])
    assert client.patch(f"{ARRANGEMENTS}/{saved['id']}", json=changed).status_code == 200
    assert _stored_sources(client)[0] == f"tags:{harbour}"


def test_a_deleted_thing_stays_an_id_and_matches_nothing(client: TestClient) -> None:
    """The id is what the text has to say about a thing that is gone; no name is invented."""
    sign_in(client)
    harbour = _tag(client, "Harbour")
    assert client.post(ARRANGEMENTS, json=wall(cells=[cell("tags:Harbour"), cell()])).status_code
    with sqlite3.connect(
        db_path(client)
    ) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        connection.execute("DELETE FROM tags WHERE id = ?", (harbour,))
    read = client.get(ARRANGEMENTS).json()["items"][0]["cells"]
    assert read[0]["source"] == f"tags:{harbour}"
