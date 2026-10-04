# SPDX-License-Identifier: AGPL-3.0-or-later
"""Concealed files, through the grid's own endpoints. Concealment is absence on the wire: not in
the list, the count or the pages either side."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.browse.tests.conftest import Library, give_derivative, share, sign_in
from sift.testing.auth import hide_for_caller

pytestmark = [pytest.mark.integration]


def conceal(client: TestClient, asset_id: str) -> None:
    """Hide one asset, for whoever is signed in."""
    hide_for_caller(client, "asset", asset_id)


def conceal_folder(client: TestClient, folder_id: str) -> None:
    hide_for_caller(client, "folder", folder_id)


def test_a_concealed_asset_is_absent_from_the_grid(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    conceal(client, library.private)

    body = client.get("/api/assets").json()
    assert [item["id"] for item in body["items"]] == [library.shared]


def test_a_concealed_asset_is_absent_from_the_count(client: TestClient, library: Library) -> None:
    """A concealed asset is absent from the count, not only the list."""
    sign_in(client, "admin")
    conceal(client, library.private)

    assert client.get("/api/assets").json()["total"] == 1, "the count included a concealed file"


def test_the_vault_conceals_from_the_admin_too(client: TestClient, library: Library) -> None:
    """It is concealment from the room, not permission. The person it hides things from is the
    person at the keyboard: that is the whole point of it."""
    sign_in(client, "admin")
    conceal(client, library.shared)

    body = client.get("/api/assets").json()
    assert library.shared not in [item["id"] for item in body["items"]]
    assert client.get(f"/api/assets/{library.shared}").status_code == 404


def test_a_concealed_asset_serves_no_bytes_of_any_kind(
    client: TestClient, library: Library
) -> None:
    """Detail, thumbnail, preview and original all refuse. Save-to-device answers 423 to the vault's
    own user, who hid it; the three a screen asks for stay 404 (see `sift.kernel.reach`)."""
    give_derivative(client, library.shared)
    sign_in(client, "admin")
    conceal(client, library.shared)

    for suffix in ("", "/thumb", "/preview"):
        answer = client.get(f"/api/assets/{library.shared}{suffix}")
        assert answer.status_code == 404, suffix

    saving = client.get(f"/api/assets/{library.shared}/save-to-device")
    assert saving.status_code == 423
    assert "vault" in saving.json()["detail"].lower()


def test_somebody_elses_vault_is_a_plain_404_and_never_the_423(
    client: TestClient, library: Library
) -> None:
    """Somebody else's vault is a plain 404 on all four, never the 423: `conceals` is per user."""
    give_derivative(client, library.shared)
    sign_in(client, "admin")
    conceal(client, library.shared)

    sign_in(client, "guest")
    for suffix in ("", "/thumb", "/preview", "/save-to-device"):
        answer = client.get(f"/api/assets/{library.shared}{suffix}")
        assert answer.status_code == 404, suffix
        assert "vault" not in answer.text.lower(), suffix


def test_concealing_a_folder_conceals_what_is_in_it(client: TestClient, library: Library) -> None:
    """Vaulting is done to a folder far more often than to a file."""
    sign_in(client, "admin")
    conceal_folder(client, library.folder)

    body = client.get("/api/assets").json()
    assert body["items"] == []
    assert body["total"] == 0


def test_a_guest_never_sees_a_concealed_asset_even_when_it_is_shared_with_them(
    client: TestClient, library: Library
) -> None:
    """The two axes are separate, and concealment is the one that wins. A share made before the
    file was hidden does not survive hiding it."""
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)
    conceal(client, library.shared)

    assert client.get("/api/assets").json()["total"] == 0
    assert client.get(f"/api/assets/{library.shared}").status_code == 404


def test_an_unlocked_vault_shows_the_hidden_screen_real_tiles_not_locked_ones(
    client: TestClient, library: Library
) -> None:
    """With the vault open the Hidden screen shows real tiles, not locked ones."""
    from sift.kernel.access import Role, Viewer
    from sift.slices.auth import current_viewer

    user_id = sign_in(client, "admin")
    conceal(client, library.shared)

    def unlocked_viewer() -> Viewer:
        return Viewer(id=user_id, role=Role.ADMIN, show_hidden=True)

    client.app.dependency_overrides[current_viewer] = unlocked_viewer  # type: ignore[attr-defined]
    try:
        body = client.get("/api/assets", params={"hidden": "true"}).json()
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]

    assert [item["id"] for item in body["items"]] == [library.shared]
    tile = body["items"][0]
    assert tile["concealed"] is False, "unlocked, the Hidden screen still sent a locked placeholder"
    assert tile["media_type"] == "video"
    assert tile["width"] is not None


def test_a_tile_says_whether_the_concealment_is_on_the_file_itself(
    client: TestClient, library: Library
) -> None:
    """A tile says whether the concealment is on the file itself (solid) or above it (hollow)."""
    from sift.kernel.access import Role, Viewer
    from sift.slices.auth import current_viewer

    user_id = sign_in(client, "admin")
    conceal(client, library.shared)
    conceal_folder(client, library.folder)

    def unlocked_viewer() -> Viewer:
        return Viewer(id=user_id, role=Role.ADMIN, show_hidden=True)

    client.app.dependency_overrides[current_viewer] = unlocked_viewer  # type: ignore[attr-defined]
    try:
        body = client.get("/api/assets", params={"hidden": "true"}).json()
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]

    by_id = {item["id"]: item for item in body["items"]}
    assert set(by_id) == {library.shared, library.private}

    # Everything under the folder is concealed...
    assert all(item["hidden"] for item in by_id.values())
    # ...and exactly one of them says the switch is on the file.
    assert by_id[library.shared]["hidden_here"] is True
    assert by_id[library.private]["hidden_here"] is False


def test_paging_does_not_reveal_a_gap_where_a_concealed_file_was(
    client: TestClient, library: Library
) -> None:
    """Pages are cut after the filter, so no short page shows where a concealed file was."""
    sign_in(client, "admin")
    conceal(client, library.shared)

    first = client.get("/api/assets", params={"limit": 1, "offset": 0}).json()
    assert len(first["items"]) == 1
    assert first["items"][0]["id"] == library.private
    assert first["total"] == 1
