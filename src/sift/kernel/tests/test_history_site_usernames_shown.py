# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Site's thread names, to a guest, only the usernames with a file the guest may be shown."""

from __future__ import annotations

import pytest

import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.history import Event
from sift.kernel.access.history_entity import history_of_site
from sift.kernel.db import Database
from sift.kernel.tests.test_history_entity import (
    SITE,
    USERNAME,
    grant,
    make_file,
    make_site,
    make_username,
)
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio

SHOWN = "01HX0000000000000000000960"
KEPT = "01HX0000000000000000000961"
KEPT_USERNAME = "01HX0000000000000000000962"


def _usernames_said(events: list[Event]) -> list[str]:
    return [one.what for one in events if "username" in one.what]


async def test_a_guest_is_told_only_the_username_on_a_file_they_may_see(
    temp_db: Database, actors: Actors
) -> None:
    await make_site(temp_db)
    await make_username(temp_db)
    await make_username(temp_db, KEPT_USERNAME, name="quillmoss")
    for asset_id, username in ((SHOWN, USERNAME), (KEPT, KEPT_USERNAME)):
        await make_file(temp_db, asset_id)
        await temp_db.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
            (asset_id, username),
        )
    await grant(temp_db, "item", SHOWN, user_id=actors.guest.id)

    # Not named, and not counted either: "and a username" would say one exists.
    assert _usernames_said(await history_of_site(temp_db, actors.guest, SITE)) == [
        "The username harlowquin was added to it"
    ]
    assert _usernames_said(await history_of_site(temp_db, actors.admin, SITE)) == [
        "The usernames harlowquin and quillmoss were added to it"
    ]
