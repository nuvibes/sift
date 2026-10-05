# SPDX-License-Identifier: AGPL-3.0-or-later
"""A ranking made before the read holds only the asker's files, and the vault's only while open."""

from __future__ import annotations

from dataclasses import replace

import pytest

from sift.kernel.access import Role
from sift.kernel.access.ranked import VIEWER_FILES, ranked_among
from sift.kernel.db import Database
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration


async def _library(db: Database) -> None:
    """Three files: one shared with the guest, one not, one the guest shut in their vault."""
    await db.initialize_schema()
    async with db.write() as c:
        for root in ("theirs", "mine"):
            await c.execute(
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, 0)",
                (root, root, f"/library/{root}"),
            )
        for asset_id, root in (("shared", "mine"), ("kept", "mine"), ("other", "theirs")):
            await c.execute(
                "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
                " VALUES (?, ?, 1, 'image', 0)",
                (asset_id, f"digest-{asset_id}"),
            )
            await c.execute(
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
                " filename, first_seen_at, last_seen_at) VALUES (?, ?, ?, NULL, ?, ?, 0, 0)",
                (f"l-{asset_id}", asset_id, root, asset_id, asset_id),
            )


async def _share_and_hide(db: Database, user_id: str) -> None:
    async with db.write() as c:
        await c.execute(
            "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect,"
            " created_at) VALUES (?, 'root', 'mine', ?, 'share', 0)",
            (f"g-{user_id}", user_id),
        )
        await c.execute(
            "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
            " VALUES ('kept', ?, 1, 0, 0)",
            (user_id,),
        )


async def _ranked(db: Database, binds: object) -> set[str]:
    rows = await db.fetch_all(VIEWER_FILES, binds)  # type: ignore[arg-type]
    return {str(row["asset_id"]) for row in rows}


async def test_a_guest_ranks_their_own_files_and_the_vault_only_while_open(
    temp_db: Database,
) -> None:
    await _library(temp_db)
    guest = await create_user(temp_db, Role.GUEST)
    await _share_and_hide(temp_db, guest.id)

    shut = await ranked_among(temp_db, guest)
    opened = await ranked_among(temp_db, replace(guest, show_hidden=True))

    assert await _ranked(temp_db, shut) == {"shared"}
    assert await _ranked(temp_db, opened) == {"shared", "kept"}


async def test_an_admin_with_nothing_held_back_ranks_every_file_unscoped(
    temp_db: Database,
) -> None:
    await _library(temp_db)
    admin = await create_user(temp_db, Role.ADMIN)

    assert await ranked_among(temp_db, admin) is None
    assert await ranked_among(temp_db, replace(admin, show_hidden=True)) is None


async def test_an_admins_shut_vault_is_left_out_and_an_open_one_is_not(
    temp_db: Database,
) -> None:
    await _library(temp_db)
    admin = await create_user(temp_db, Role.ADMIN)
    await _share_and_hide(temp_db, admin.id)

    assert await _ranked(temp_db, await ranked_among(temp_db, admin)) == {"shared", "other"}
    assert await ranked_among(temp_db, replace(admin, show_hidden=True)) is None
