# SPDX-License-Identifier: AGPL-3.0-or-later
"""The passes over a file, on its History over HTTP: drawn for whoever may see the file, and for
nobody else.

The lines themselves are proved in the kernel (`kernel/tests/test_history_every_pass.py`). What is
proved here is the vault: AcoustID's answer and a pass that gave up are lines about one file, and a
file in somebody's vault is not a file anybody else may be told has a History at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.browse.tests.conftest import Library, db_path, share, sign_in, write
from sift.testing.auth import hide_for_caller

pytestmark = [pytest.mark.integration]

_LATER = 1_700_003_600


def _passes_on(client: TestClient, asset_id: str) -> None:
    write(
        db_path(client),
        [
            (
                "INSERT INTO music_lookups (asset_id, looked_up_at, lengths_sent, status)"
                " VALUES (?, ?, '[61]', 'nothing')",
                (asset_id, _LATER),
            ),
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
                " VALUES (?, 'previews', 'not_decodable', 'x', 0, ?)",
                (asset_id, _LATER),
            ),
        ],
    )


def test_the_passes_are_drawn_for_whoever_may_see_the_file(
    client: TestClient, library: Library
) -> None:
    _passes_on(client, library.shared)
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    said = [
        event["what"]
        for event in client.get(f"/api/assets/{library.shared}/history").json()["items"]
    ]
    assert "Sift asked AcoustID and it didn't know the song" in said
    assert "Sift could not generate a hover preview for this file" in said


def test_a_file_in_the_vault_or_not_shared_draws_none_of_them(
    client: TestClient, library: Library
) -> None:
    """A guest the file was never shared with gets the 404 a made-up id gets; the admin whose own
    vault holds it, shut, is told it is in the vault (`kernel.reach.vault_locked`). Neither answer
    carries a line of its History."""
    _passes_on(client, library.private)
    sign_in(client, "guest")
    answer = client.get(f"/api/assets/{library.private}/history")
    assert answer.status_code == 404
    assert "AcoustID" not in answer.text and "hover preview" not in answer.text

    sign_in(client, "admin")
    assert "AcoustID" in client.get(f"/api/assets/{library.private}/history").text
    hide_for_caller(client, "asset", library.private)
    answer = client.get(f"/api/assets/{library.private}/history")
    assert answer.status_code == 423
    assert "AcoustID" not in answer.text and "hover preview" not in answer.text
