# SPDX-License-Identifier: AGPL-3.0-or-later
"""The locked-placeholder mode, and the query carried to the grid. No session builds the mode yet,
so it is reached by handing the route a viewer that has it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import AssetView, Concealment, Role, Viewer
from sift.kernel.content import Asset, DerivativeKind
from sift.kernel.content import schema as content_schema
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.auth import current_viewer
from sift.slices.browse import schema as browse_schema
from sift.slices.browse.models import summary
from sift.slices.browse.tests.conftest import Library, db_path, give_derivative, sign_in, write
from sift.testing.auth import hide_for_caller

pytestmark = [pytest.mark.integration]


def _asset(asset_id: str = "01HX0000000000000000000009") -> Asset:
    return Asset(
        id=asset_id,
        identity="digest",
        media_type="video",
        mime="video/mp4",
        width=1920,
        height=1080,
        duration_ms=4000,
        fps=30.0,
        size_bytes=99,
        container="mp4",
        vcodec="h264",
        acodec="aac",
        bit_depth=8,
        phash=None,
        videohash=None,
        original_filename="secret-holiday.mp4",
        added_at=1_700_000_000,
        probed_at=1_700_000_000,
    )


def test_a_placeholder_tile_describes_nothing_but_its_own_concealment() -> None:
    """A placeholder tile describes nothing but its own concealment."""
    tile = summary(
        AssetView(asset=_asset(), concealed=True), revealed=False, favorite=True, rating=5
    )

    assert tile.concealed is True
    assert tile.width is None
    assert tile.height is None
    assert tile.duration_ms is None
    assert tile.rating is None
    assert tile.favorite is False
    assert "holiday" not in tile.model_dump_json()


def test_an_ordinary_tile_carries_what_the_grid_needs_to_lay_it_out() -> None:
    """The other half of the branch: a visible asset is described fully, because the justified
    grid cannot place a tile whose proportions it does not know."""
    tile = summary(
        AssetView(asset=_asset(), concealed=False), revealed=False, favorite=True, rating=4
    )

    assert tile.concealed is False
    assert (tile.width, tile.height) == (1920, 1080)
    assert (tile.favorite, tile.rating) == (True, 4)


def test_an_unlocked_viewer_gets_the_real_tile_even_for_a_concealed_asset() -> None:
    """`concealed` on the row is structural (this asset is vaulted) and `revealed` is the
    other half of the answer: whether THIS viewer's vault is open. Unlocked, a hidden asset is
    described exactly as fully as an ordinary one, which is what makes the Hidden screen able to
    show it at all rather than a wall of locked tiles.
    """
    tile = summary(
        AssetView(asset=_asset(), concealed=True), revealed=True, favorite=True, rating=5
    )

    assert tile.concealed is False
    assert (tile.width, tile.height) == (1920, 1080)
    assert (tile.favorite, tile.rating) == (True, 5)
    assert tile.media_type == "video"


def test_the_detail_of_a_placeholder_says_no_more_than_the_tile_does(
    client: TestClient, library: Library
) -> None:
    """Opening a locked tile says no more than the tile."""
    user_id = sign_in(client, "admin")

    def placeholder_viewer() -> Viewer:
        return Viewer(
            id=user_id,
            role=Role.ADMIN,
            show_hidden=False,
            concealment=Concealment.PLACEHOLDER,
        )

    client.app.dependency_overrides[current_viewer] = placeholder_viewer  # type: ignore[attr-defined]
    try:
        hide_for_caller(client, "asset", library.shared)

        body = client.get(f"/api/assets/{library.shared}").json()

        assert body["concealed"] is True
        assert body["original_filename"] is None
        assert body["width"] is None
        assert body["duration_ms"] is None
        assert "shared.mp4" not in str(body)
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_a_placeholder_gets_no_scrub_strip_either(client: TestClient, library: Library) -> None:
    """A placeholder gets no scrub strip: the strip lookup bypasses the scoped read, so permission
    is asked strictly."""
    user_id = sign_in(client, "admin")

    def placeholder_viewer() -> Viewer:
        return Viewer(
            id=user_id,
            role=Role.ADMIN,
            show_hidden=False,
            concealment=Concealment.PLACEHOLDER,
        )

    client.app.dependency_overrides[current_viewer] = placeholder_viewer  # type: ignore[attr-defined]
    try:
        give_derivative(
            client,
            library.shared,
            DerivativeKind.SPRITE,
            body=b"sheet-bytes",
            params={"columns": 5, "rows": 6, "tile_width": 320},
            digest="0123456789abcdef",
        )
        hide_for_caller(client, "asset", library.shared)

        assert client.get(f"/api/assets/{library.shared}/sprite").status_code == 404
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_opening_a_hidden_asset_directly_gets_its_contents_once_unlocked(
    client: TestClient, library: Library
) -> None:
    """Once unlocked, opening a hidden asset directly returns its contents."""
    user_id = sign_in(client, "admin")

    def unlocked_viewer() -> Viewer:
        return Viewer(id=user_id, role=Role.ADMIN, show_hidden=True)

    client.app.dependency_overrides[current_viewer] = unlocked_viewer  # type: ignore[attr-defined]
    try:
        hide_for_caller(client, "asset", library.shared)

        body = client.get(f"/api/assets/{library.shared}").json()

        assert body["concealed"] is False
        assert body["original_filename"] == "shared.mp4"
        assert body["width"] is not None
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_the_grid_honours_a_query_through_the_one_engine(
    client: TestClient, library: Library
) -> None:
    """A query in the address reaches the read through `app.state.filter_engine`: one matching
    nothing empties the grid, total included."""
    sign_in(client, "admin")

    plain = client.get("/api/assets").json()
    assert plain["total"] > 0

    queried = client.get("/api/assets", params={"tags": "no-such-tag-exists"}).json()

    assert queried["total"] == 0
    assert queried["items"] == []


def test_the_grid_asks_how_far_below_a_folder_it_means(
    client: TestClient, library: Library
) -> None:
    """The folder depth reaches the statement end to end: address, parser, compiler, read."""
    sign_in(client, "admin")

    # One of the two files moves a level down, so the parent holds one loose file and one folder.
    below = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                "VALUES (?, ?, ?, ?, ?)",
                (below, library.root, library.folder, "clips/inner", "inner"),
            ),
            (
                "UPDATE asset_locations SET folder_id = ?, rel_path = ? WHERE asset_id = ?",
                (below, "clips/inner/private.mp4", library.private),
            ),
        ],
    )

    under = client.get("/api/assets", params={"in": library.folder}).json()
    assert {item["id"] for item in under["items"]} == {library.shared, library.private}

    inside = client.get("/api/assets", params={"in": library.folder, "depth": "direct"}).json()
    assert {item["id"] for item in inside["items"]} == {library.shared}
    # The count comes from the same statement as the rows, so a depth applied to one and not the
    # other would read as a wall that cannot add up.
    assert inside["total"] == 1

    # And a depth nobody could read narrows to nothing rather than quietly answering with more
    # than was asked for.
    unreadable = client.get("/api/assets", params={"in": library.folder, "depth": "deeper"}).json()
    assert unreadable["total"] == 0


async def test_an_initializer_at_its_current_version_does_nothing(temp_db: Database) -> None:
    """Told the schema is already where it should be, an initializer must not touch it.

    It matters more than it looks: once these carry statements that alter a table to get from one
    version to the next, "does nothing" is the difference between a quiet restart and one that
    rewrites a table that was already correct.
    """
    await temp_db.initialize_schema()
    saved = await temp_db.fetch_all("SELECT id FROM save_log")

    async with temp_db.write() as connection:
        await browse_schema.initialize_save_log(connection, on_disk=browse_schema.SAVE_LOG_VERSION)
        await content_schema.initialize_user_state(
            connection, on_disk=content_schema.USER_STATE_VERSION
        )

    assert await temp_db.fetch_all("SELECT id FROM save_log") == saved
