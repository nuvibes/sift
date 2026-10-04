# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tag tree: a tag filed under one other, a filter on a parent that takes in its branch, a
tag's page that lists what is filed under it, and a loop that is refused before anything is written.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.tags_ratings.service import TagService
from sift.slices.tags_ratings.tests.conftest import (
    Library,
    assign,
    db_path,
    make_tag,
    read,
    sign_in,
)

pytestmark = [pytest.mark.integration]


def _file_under(
    client: TestClient, tag_id: str, name: str, parent: str | None
) -> dict[str, object]:
    response = client.put(f"/api/tags/{tag_id}", json={"name": name, "parent": parent or ""})
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_a_tag_is_filed_under_another_by_name_and_its_record_opens_it(client: TestClient) -> None:
    sign_in(client)
    outdoors = make_tag(client, "Outdoors")
    sunset = make_tag(client, "Sunset")

    saved = _file_under(client, sunset, "Sunset", "outdoors")

    assert saved["record"] == {"parent": "Outdoors", "parent_id": outdoors}
    assert client.get(f"/api/tags/{sunset}").json()["record"]["parent_id"] == outdoors


def test_a_parent_that_names_no_tag_yet_is_made(client: TestClient) -> None:
    sign_in(client)
    sunset = make_tag(client, "Sunset")

    saved = _file_under(client, sunset, "Sunset", "Outdoors")

    made = read(db_path(client), "SELECT id FROM tags WHERE name = 'Outdoors'")
    assert [one["id"] for one in made] == [saved["record"]["parent_id"]]  # type: ignore[index]


def test_an_empty_part_of_takes_a_tag_back_to_the_top(client: TestClient) -> None:
    sign_in(client)
    sunset = make_tag(client, "Sunset")
    _file_under(client, sunset, "Sunset", "Outdoors")

    saved = _file_under(client, sunset, "Sunset", None)

    assert saved["record"] == {}


def test_a_filter_on_a_parent_takes_in_the_files_under_its_children(
    client: TestClient, library: Library
) -> None:
    """The shared file carries only `Sunset`; asking for `Outdoors` finds it, and asking for
    `Sunset` does not find the file that carries only `Outdoors`."""
    sign_in(client)
    outdoors = make_tag(client, "Outdoors")
    sunset = make_tag(client, "Sunset")
    _file_under(client, sunset, "Sunset", "Outdoors")
    assign(client, [library.shared], [sunset])
    assign(client, [library.private], [outdoors])

    under_outdoors = client.get("/api/assets", params={"tags": "Outdoors"}).json()
    under_sunset = client.get("/api/assets", params={"tags": "Sunset"}).json()

    assert {one["id"] for one in under_outdoors["items"]} == {library.shared, library.private}
    assert [one["id"] for one in under_sunset["items"]] == [library.shared]


def test_a_tags_page_counts_and_lists_the_tags_filed_under_it(client: TestClient) -> None:
    sign_in(client)
    outdoors = make_tag(client, "Outdoors")
    sunset = make_tag(client, "Sunset")
    make_tag(client, "Beach")
    _file_under(client, sunset, "Sunset", "Outdoors")

    listed = client.get("/api/tags", params={"parent": outdoors}).json()
    counts = client.get(f"/api/related/tag/{outdoors}").json()

    assert [one["id"] for one in listed["items"]] == [sunset]
    assert listed["total"] == 1
    assert counts["tags_within"] == 1
    assert client.get(f"/api/related/tag/{sunset}").json()["tags_within"] == 0


def test_a_loop_is_refused_and_leaves_the_tag_as_it_was(client: TestClient) -> None:
    """Filing `Outdoors` under `Sunset` while `Sunset` is under `Outdoors` would make each the
    other's ancestor. Refused before the rename in the same save is written."""
    sign_in(client)
    outdoors = make_tag(client, "Outdoors")
    sunset = make_tag(client, "Sunset")
    _file_under(client, sunset, "Sunset", "Outdoors")

    looped = client.put(f"/api/tags/{outdoors}", json={"name": "Outdoor", "parent": "Sunset"})
    itself = client.put(f"/api/tags/{sunset}", json={"name": "Sunset", "parent": "Sunset"})

    assert looped.status_code == 422
    assert itself.status_code == 422
    kept = client.get(f"/api/tags/{outdoors}").json()
    assert kept["name"] == "Outdoors"
    assert kept["record"] == {}


def test_a_loop_made_after_the_check_is_still_refused_by_the_save(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tree can change between the route's early check and the save (another save filing the
    other way). The save asks again and refuses with the same sentence, so no loop is ever stored."""
    sign_in(client)
    outdoors = make_tag(client, "Outdoors")
    sunset = make_tag(client, "Sunset")
    _file_under(client, sunset, "Sunset", "Outdoors")
    expected = client.put(f"/api/tags/{outdoors}", json={"name": "Outdoors", "parent": "Sunset"})

    async def _checked_before_the_tree_moved(*_: object) -> None:
        return None

    monkeypatch.setattr(TagService, "refuse_a_loop", _checked_before_the_tree_moved)
    looped = client.put(f"/api/tags/{outdoors}", json={"name": "Outdoors", "parent": "Sunset"})

    assert looped.status_code == 422
    assert looped.json() == expected.json()
    assert client.get(f"/api/tags/{outdoors}").json()["record"] == {}


def test_deleting_a_parent_leaves_its_children_at_the_top(client: TestClient) -> None:
    sign_in(client)
    outdoors = make_tag(client, "Outdoors")
    sunset = make_tag(client, "Sunset")
    _file_under(client, sunset, "Sunset", "Outdoors")

    assert client.delete(f"/api/tags/{outdoors}").status_code in (200, 204)

    assert client.get(f"/api/tags/{sunset}").json()["record"] == {}


async def test_a_library_from_before_the_tree_gains_the_parent_column_and_its_index() -> None:
    """The step a library at catalog 70 takes: the column every tag then has, and its index.

    The step itself, on the tags table alone: the walk on to the newest version reads tables a
    lone tags table does not have."""
    import aiosqlite  # nosemgrep: sift-no-database-driver-outside-kernel (a test opening the scratch file it made)

    from sift.kernel.access.schema import file_tags_in_a_tree

    async with aiosqlite.connect(":memory:") as connection:
        await connection.execute("CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        await connection.execute("INSERT INTO tags (id, name) VALUES ('t', 'Outdoors')")

        await file_tags_in_a_tree(connection)

        columns = {row[1] for row in await connection.execute_fetchall("PRAGMA table_info(tags)")}
        indexes = {row[1] for row in await connection.execute_fetchall("PRAGMA index_list(tags)")}
        kept = await connection.execute_fetchall("SELECT name, parent_id FROM tags")
    assert "parent_id" in columns
    assert "ix_tags_parent" in indexes
    assert list(kept) == [("Outdoors", None)]
