# SPDX-License-Identifier: AGPL-3.0-or-later
"""`?asset=` on the photo-set list: which sets hold this one picture.

The file's own screen draws a chip per set it is in, and this is the one question that answers it
without walking every set's items. The same leaf the walls narrow by, reached by name.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.photo_sets.tests.conftest import Shoot, sign_in


def _make(client: TestClient, name: str) -> str:
    made = client.post("/api/photo-sets", json={"name": name})
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def test_the_list_can_be_narrowed_to_the_sets_holding_one_picture(
    client: TestClient, shoot: Shoot
) -> None:
    sign_in(client)
    holding = _make(client, "holds it")
    _make(client, "does not")
    put = client.post(f"/api/photo-sets/{holding}/items", json={"asset_ids": [shoot.first]})
    assert put.status_code == 200, put.text

    named = {
        one["name"]
        for one in client.get("/api/photo-sets", params={"asset": shoot.first}).json()["items"]
    }
    assert named == {"holds it"}
    assert client.get("/api/photo-sets", params={"asset": shoot.second}).json()["items"] == []
