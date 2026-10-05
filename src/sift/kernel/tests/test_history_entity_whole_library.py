# SPDX-License-Identifier: AGPL-3.0-or-later
"""A decision naming no file is an admin's line on every entity thread, and in its count."""

from __future__ import annotations

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.history_entity import history_count_of_entity
from sift.kernel.db import Database
from sift.kernel.tests.test_history_entity import (
    COLLECTION,
    PHOTO_SET,
    SITE,
    SONG,
    TAG,
    grant,
    make_collection,
    make_file,
    make_photo_set,
    make_site,
    make_song,
    make_tag,
)
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio

DECISION = "01HX0000000000000000000881"
SHOWN = "01HX0000000000000000000882"


async def _make_all(database: Database) -> dict[str, str]:
    await make_tag(database)
    await make_site(database)
    await make_collection(database, owner=None)
    await make_photo_set(database)
    await make_song(database, kind=None, via=None, user=None)
    return {
        "tag": TAG,
        "site": SITE,
        "collection": COLLECTION,
        "photo_set": PHOTO_SET,
        "song": SONG,
    }


async def test_a_pass_over_the_whole_library_is_an_admins_line_on_every_thread(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    things = await _make_all(temp_db)
    await temp_db.execute(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at)"
        " VALUES (?, 'asked-only', NULL, 'Sift looked over the whole library', '', '{}', 1)",
        (DECISION,),
    )
    for kind, one in things.items():
        await temp_db.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
            " VALUES (?, ?, ?)",
            (DECISION, kind, one),
        )

    async def counts(viewer: Viewer) -> list[int]:
        return [
            await history_count_of_entity(temp_db, viewer, kind, one)
            for kind, one in things.items()
        ]

    guest_before, admin_before = await counts(actors.guest), await counts(actors.admin)
    await make_file(temp_db, SHOWN)
    await grant(temp_db, "item", SHOWN, user_id=actors.guest.id)
    await temp_db.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
        " VALUES (?, 'asset', ?)",
        (DECISION, SHOWN),
    )

    assert [a - b for a, b in zip(await counts(actors.guest), guest_before, strict=True)] == [1] * 5
    assert await counts(actors.admin) == admin_before
