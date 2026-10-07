# SPDX-License-Identifier: AGPL-3.0-or-later
"""The first faces of every card on a page of people, read for the page in one go."""

from __future__ import annotations

import pytest

# For its side effect: registering the table the ledger is written to, so a slice test's
# database has it. Registration happens at import and the fixtures migrate at setup.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.content import Ingested
from sift.kernel.db import Database
from sift.slices.faces.models import Attribution
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import make_person
from sift.slices.faces.tests.test_store import record

pytestmark = pytest.mark.integration


async def test_each_cards_surest_faces_are_one_read_for_the_page(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The surest matched faces of every card on the page, each person's as `surest_matched`
    pages them, read in one statement."""
    track_ids = await record(store, clip.asset.id, count=5)
    busy = await make_person(temp_db, "Ada Lovelace")
    quiet = await make_person(temp_db, "Grace Hopper")
    nobody = await make_person(temp_db, "Jane Doe")
    for track_id, confidence in zip(track_ids[:4], (0.7, 0.95, 0.8, 0.9), strict=True):
        await store.attribute(
            track_id, busy, confidence=confidence, attribution=Attribution.MATCHED
        )
    await store.attribute(track_ids[4], quiet, confidence=0.9, attribution=Attribution.CONFIRMED)

    heads = await store.surest_matched_heads([busy, quiet, nobody], each=3)

    assert {person: [track.id for track in tracks] for person, tracks in heads.items()} == {
        busy: [track.id for track in await store.surest_matched(busy, limit=3, offset=0)]
    }
    assert heads[busy][0].id == track_ids[1]
    assert await store.surest_matched_heads([], each=3) == {}
