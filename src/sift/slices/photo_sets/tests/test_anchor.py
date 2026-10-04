# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Photo Set page's anchor is a position in the set's own order.

The set is the one ordering on the file wall that is not a sort: the page reads it, and the anchor
(`from=`) has to be resolved in the same order, or a link into a set lands one file wide of the
picture it names and every re-read of the page swaps its tiles for the wrong page.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.photo_sets.tests.conftest import Shoot, edit_items, make_set, sign_in


def test_the_anchor_inside_a_photo_set_is_in_the_sets_order(
    client: TestClient, shoot: Shoot
) -> None:
    sign_in(client)
    set_id = make_set(client, "Beach shoot")
    edit_items(client, set_id, [shoot.second, shoot.first])

    first = client.get("/api/assets", params={"photo_set": set_id, "from": shoot.second}).json()
    second = client.get("/api/assets", params={"photo_set": set_id, "from": shoot.first}).json()

    # Newest first would put `first` (added last) at the top; the set says otherwise.
    assert first["offset"] == 0
    assert second["offset"] == 1
