# SPDX-License-Identifier: AGPL-3.0-or-later
"""Over HTTP: a decision naming no file is an admin's line on a tag's and a Site's History."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.people.tests.conftest import Library, db_path, share, sign_in, write

pytestmark = pytest.mark.integration

PASS = "Sift looked over the whole library for this"


def _seeded(client: TestClient, library: Library) -> tuple[str, str, str]:
    """A tag and a Site on the shared file, and one decision about both that names no file."""
    tag, site, username, decision = new_id(), new_id(), new_id(), new_id()
    write(
        db_path(client),
        [
            ("INSERT INTO tags (id, name, created_at) VALUES (?, 'seagrass', 0)", (tag,)),
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (library.shared, tag)),
            ("INSERT INTO sites (id, name) VALUES (?, 'Studio')", (site,)),
            (
                "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'tidepool', 0)",
                (username, site),
            ),
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (library.shared, username),
            ),
            (
                "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
                " decided_at) VALUES (?, 'asked-only', NULL, ?, '', '{}', 1700000500)",
                (decision, PASS),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
                " VALUES (?, 'tag', ?), (?, 'site', ?)",
                (decision, tag, decision, site),
            ),
        ],
    )
    return tag, site, decision


def _decided(client: TestClient, url: str) -> int:
    answer = client.get(url)
    assert answer.status_code == 200, answer.text
    return len([one for one in answer.json() if one["kind"] == "decided"])


def _threads(tag: str, site: str) -> list[str]:
    return [f"/api/tags/{tag}/history", f"/api/sites/{site}/history"]


def test_a_guest_is_not_told_of_a_pass_over_the_whole_library(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    tag, site, decision = _seeded(client, library)
    share(client, library.shared, sign_in(client, role="guest", who="two"))

    assert [_decided(client, url) for url in _threads(tag, site)] == [0, 0]

    write(
        db_path(client),
        [
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
                " VALUES (?, 'asset', ?)",
                (decision, library.shared),
            )
        ],
    )
    assert [_decided(client, url) for url in _threads(tag, site)] == [1, 1]


def test_an_admin_is_told_of_it(client: TestClient, library: Library) -> None:
    sign_in(client)
    tag, site, _decision = _seeded(client, library)

    assert [_decided(client, url) for url in _threads(tag, site)] == [1, 1]
