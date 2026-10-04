# SPDX-License-Identifier: AGPL-3.0-or-later
"""A cover a person clears stays empty; every other empty cover is the first file's picture.

The cover route is the one door a clear comes through, and its statement writes the mark beside the
pointers (`kernel/covers.py cleared_mark`), so Sift's own rule (`kernel/access/default_covers.py`)
leaves the entity empty until somebody chooses a picture again. A merge moves filings rather than
writing them, so it asks the rule for its survivor itself. A Site is left out of the rule: it is its
letter or its icon until somebody chooses a picture, and a clear still writes the mark.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.people.tests.conftest import (
    Library,
    assign,
    db_path,
    make_person,
    make_username,
    read,
    sign_in,
    write,
)

_COVER_OF = {
    "people": "SELECT cover_asset_id, cover_cleared_at FROM people WHERE id = ?",
    "sites": "SELECT cover_asset_id, cover_cleared_at FROM sites WHERE id = ?",
}


def _cover(client: TestClient, table: str, entity: str) -> tuple[object, object]:
    (row,) = read(db_path(client), _COVER_OF[table], (entity,))
    return row["cover_asset_id"], row["cover_cleared_at"]


def test_a_person_cleared_by_hand_stays_empty_until_a_cover_is_chosen(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    person = make_person(client, "Wren Halloway")
    assert assign(client, [library.shared], [person]).status_code == 200
    assert _cover(client, "people", person) == (library.shared, None)

    assert client.put(f"/api/people/{person}/cover", json={"asset_id": None}).status_code == 200
    asset, cleared = _cover(client, "people", person)
    assert asset is None and cleared is not None
    assign(client, [library.private], [person])
    assert _cover(client, "people", person)[0] is None

    client.put(f"/api/people/{person}/cover", json={"asset_id": library.private})
    assert _cover(client, "people", person) == (library.private, None)


def test_a_site_cleared_by_hand_stays_empty_too(client: TestClient, library: Library) -> None:
    sign_in(client)
    username = make_username(client, "Marrowvale", "wren")
    (row,) = read(db_path(client), "SELECT site_id FROM usernames WHERE id = ?", (username,))
    site = str(row["site_id"])
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_usernames (asset_id, username_id, decided_at) VALUES (?, ?, 1)",
                (library.shared, username),
            )
        ],
    )
    # Filed, and no frame of the file: the rule does not reach a Site.
    assert _cover(client, "sites", site) == (None, None)

    client.put(f"/api/sites/{site}/cover", json={"asset_id": None})
    other = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'x', 0)",
                (other, site),
            ),
            (
                "INSERT INTO asset_usernames (asset_id, username_id, decided_at) VALUES (?, ?, 2)",
                (library.private, other),
            ),
        ],
    )
    asset, cleared = _cover(client, "sites", site)
    assert asset is None and cleared is not None


def test_a_merge_gives_a_survivor_with_no_cover_its_first_file(
    client: TestClient, library: Library
) -> None:
    """A merge MOVES filings rather than writing new ones, so the rule is asked after it."""
    sign_in(client)
    keeping = make_person(client, "Wrenna Sable")
    losing = make_person(client, "Pell Quorley")
    assign(client, [library.shared], [losing])
    # The person going has files and no picture, so there is nothing of theirs to carry across.
    client.put(f"/api/people/{losing}/cover", json={"asset_id": None})
    assert _cover(client, "people", keeping) == (None, None)

    merged = client.post("/api/people/merge", json={"people": [losing], "into": keeping})

    assert merged.status_code == 200, merged.text
    assert _cover(client, "people", keeping) == (library.shared, None)


def test_a_site_merge_gives_a_survivor_with_no_cover_no_frame_of_a_file(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    kept = make_username(client, "Marrowvale", "wren")
    going = make_username(client, "Marrowvale Studios", "wren")
    sites: dict[str, str] = {}
    for username in (kept, going):
        (row,) = read(db_path(client), "SELECT site_id FROM usernames WHERE id = ?", (username,))
        sites[username] = str(row["site_id"])
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_usernames (asset_id, username_id, decided_at) VALUES (?, ?, 1)",
                (library.shared, going),
            )
        ],
    )
    client.put(f"/api/sites/{sites[going]}/cover", json={"asset_id": None})
    assert _cover(client, "sites", sites[kept]) == (None, None)

    merged = client.post("/api/sites/merge", json={"sites": [sites[going]], "into": sites[kept]})

    assert merged.status_code == 200, merged.text
    assert _cover(client, "sites", sites[kept])[0] is None
