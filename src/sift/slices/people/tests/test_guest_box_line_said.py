# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person's box line, read by a guest: what the box filled in is said by its kind alone."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.people.tests.conftest import Library
from sift.slices.people.tests.test_guest_box_lines import _as_guest

pytestmark = pytest.mark.integration


def test_a_guest_is_told_a_username_and_a_tag_and_nothing_about_either(
    client: TestClient, library: Library
) -> None:
    one, _admin = _as_guest(client, library)

    lines = client.get(f"/api/people/{one.person}/history").json()

    assert [line["what"] for line in lines if line["kind"] == "enriched"] == [
        "StashDB filled in their usernames (a username) and tags (a tag)"
        " when you applied its answer"
    ]
