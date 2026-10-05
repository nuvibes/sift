# SPDX-License-Identifier: AGPL-3.0-or-later
"""A guest is told nothing of a person, tag or Site they may not be shown: no name, id or count."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.people.tests.conftest import (
    Library,
    db_path,
    make_person,
    make_username,
    share,
    sign_in,
    write,
)
from sift.testing.auth import TEST_PIN
from sift.testing.library import hidden_row

pytestmark = pytest.mark.integration


def _file_under(path: Path, asset_id: str, username_id: str) -> None:
    write(
        path,
        [
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (asset_id, username_id),
            )
        ],
    )


def _waiting(client: TestClient) -> dict[str, int]:
    page = client.get("/api/usernames", params={"unattached": "true"}).json()
    return {one["username"]: one["name_candidates"] for one in page["items"]}


def _open_vault(client: TestClient) -> None:
    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200


def test_the_waiting_usernames_count_no_person_for_a_guest(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    known = make_username(client, "SomeSite", "fennmarchetti")
    unknown = make_username(client, "SomeSite", "delphineostrow")
    for one in (known, unknown):
        _file_under(db_path(client), library.shared, one)
    make_person(client, "fennmarchetti", vault=True)
    assert _waiting(client) == {"delphineostrow": 0, "fennmarchetti": 1}
    _open_vault(client)
    assert _waiting(client) == {"delphineostrow": 0, "fennmarchetti": 1}

    guest = sign_in(client, role="guest", who="two")
    share(client, library.shared, guest)
    assert _waiting(client) == {"delphineostrow": 0, "fennmarchetti": 0}
    _open_vault(client)
    assert _waiting(client) == {"delphineostrow": 0, "fennmarchetti": 0}


def _username_row(client: TestClient, username_id: str) -> dict[str, object]:
    page = client.get("/api/usernames").json()
    return next(one for one in page["items"] if one["id"] == username_id)


def test_a_username_names_no_person_the_guest_may_not_be_shown(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    seen = make_person(client, "Delphine Ostrow")
    unseen = make_person(client, "Fenn Marchetti")
    username = make_username(client, "SomeSite", "fennmarchetti")
    write(
        db_path(client),
        [
            ("UPDATE usernames SET person_id = ? WHERE id = ?", (unseen, username)),
            (
                "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
                (library.shared, seen),
            ),
            (
                "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
                (library.private, unseen),
            ),
        ],
    )
    _file_under(db_path(client), library.shared, username)

    row = _username_row(client, username)
    assert (row["person_id"], row["person_name"]) == (unseen, "Fenn Marchetti")
    _open_vault(client)
    row = _username_row(client, username)
    assert (row["person_id"], row["person_name"]) == (unseen, "Fenn Marchetti")

    guest = sign_in(client, role="guest", who="two")
    share(client, library.shared, guest)
    row = _username_row(client, username)
    assert (row["person_id"], row["person_name"]) == (None, None)
    assert "Fenn Marchetti" not in client.get("/api/usernames").text
    _open_vault(client)
    assert (_username_row(client, username)["person_name"]) is None

    # The same row once the person is one the guest may be shown.
    sign_in(client)
    share(client, library.private, guest)
    sign_in(client, role="guest", who="two")
    row = _username_row(client, username)
    assert (row["person_id"], row["person_name"]) == (unseen, "Fenn Marchetti")


def _tag_row(client: TestClient, tag_id: str) -> dict[str, object]:
    page = client.get("/api/tags").json()
    items = page["items"] if isinstance(page, dict) else page
    return next(one for one in items if one["id"] == tag_id)


def test_the_tags_wall_names_no_parent_the_guest_may_not_be_shown(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    parent, child = new_id(), new_id()
    write(
        db_path(client),
        [
            ("INSERT INTO tags (id, name, created_at) VALUES (?, 'harbour', 0)", (parent,)),
            (
                "INSERT INTO tags (id, name, parent_id, created_at) VALUES (?, 'jetty', ?, 0)",
                (child, parent),
            ),
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (library.shared, child)),
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (library.private, parent)),
        ],
    )
    row = _tag_row(client, child)
    assert (row["parent_id"], row["parent_name"]) == (parent, "harbour")
    _open_vault(client)
    row = _tag_row(client, child)
    assert (row["parent_id"], row["parent_name"]) == (parent, "harbour")

    guest = sign_in(client, role="guest", who="two")
    share(client, library.shared, guest)
    row = _tag_row(client, child)
    assert (row["parent_id"], row["parent_name"]) == (None, None)
    assert "harbour" not in client.get("/api/tags").text
    _open_vault(client)
    assert _tag_row(client, child)["parent_name"] is None

    sign_in(client)
    share(client, library.private, guest)
    sign_in(client, role="guest", who="two")
    row = _tag_row(client, child)
    assert (row["parent_id"], row["parent_name"]) == (parent, "harbour")


def test_the_network_above_a_site_a_guest_may_see_is_one_they_may_see(
    client: TestClient, library: Library
) -> None:
    """Why a Site's `parent` is handed over unscoped: a file under a Site counts for its network."""
    sign_in(client)
    network, site = new_id(), new_id()
    write(
        db_path(client),
        [
            ("INSERT INTO sites (id, name) VALUES (?, 'Harbour Network')", (network,)),
            (
                "INSERT INTO sites (id, name, parent_id) VALUES (?, 'Another Label', ?)",
                (site, network),
            ),
        ],
    )
    username = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'jettyone', 0)",
                (username, site),
            )
        ],
    )
    _file_under(db_path(client), library.shared, username)

    guest = sign_in(client, role="guest", who="two")
    share(client, library.shared, guest)
    record = client.get(f"/api/sites/{site}").json()
    assert record["record"]["parent_id"] == network
    assert client.get(f"/api/sites/{network}").status_code == 200

    # Hidden by the guest, the network takes the Site under it along while the vault is shut.
    write(db_path(client), [hidden_row("site", network, guest)])
    assert client.get(f"/api/sites/{site}").status_code == 404
