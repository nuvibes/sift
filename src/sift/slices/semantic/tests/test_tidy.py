# SPDX-License-Identifier: AGPL-3.0-or-later
"""Descriptions held for files that are no longer in the library.

The only leftover in Sift that nothing cascades away. Every other table naming an asset carries a
foreign key back to it, so removing a file removes what was recorded about it in the same
statement, but the vectors live in a virtual table, and SQLite takes no foreign key on one. So a
deleted file leaves its descriptions behind: invisible, never returned by any search, and growing.

Two properties are proved here, and both are the ones a wrong version would still look right
without. **The survey removes nothing**, so the count can be read before deciding. And **the run
removes only what belongs to files that have gone**: taking one file too many is silent, costs a
description somebody paid to compute, and is only discovered by that file quietly dropping out of
every search by meaning.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from sift.kernel.config import Settings, ensure_directories
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tidy import Resources
from sift.slices.semantic.store import DIMENSION, VectorStore
from sift.slices.semantic.tidy import OrphanedDescriptions

pytestmark = pytest.mark.integration

#: The model every description in these tests comes from, unless a test says otherwise.
REVISION = "siglip2-test"

_EPOCH = 1_700_000_000


def unit(*leading: float) -> list[float]:
    """A vector of the right width. The numbers do not matter here: only which file they are
    filed under, so any non-zero direction will do."""
    return [*leading, *([0.0] * (DIMENSION - len(leading)))]


@pytest_asyncio.fixture
async def resources(tmp_path: Path) -> AsyncIterator[Resources]:
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    ensure_directories(settings)
    database = Database.for_data_dir(settings.data_dir)
    await database.connect()
    await database.initialize_schema()
    try:
        yield Resources(database=database, settings=settings)
    finally:
        await database.close()


async def _asset(resources: Resources) -> str:
    """One file in the library. Only its id matters to this tidying."""
    asset_id = new_id()
    await resources.database.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'image', ?)",
        (asset_id, f"digest-{asset_id}", _EPOCH),
    )
    return asset_id


async def test_nothing_is_counted_on_a_library_nobody_has_deleted_from(
    resources: Resources,
) -> None:
    """The ordinary state, and the one the maintenance screen shows on almost every install. It has
    to answer without the index existing at all, which is what a machine that has never switched
    this on looks like."""
    surveyed = await OrphanedDescriptions(resources).survey()

    assert surveyed.count == 0
    assert surveyed.title and surveyed.detail
    # Rows in a table, not files on disk. A number here would invite somebody to run it to reclaim
    # space, which is not what this is for.
    assert surveyed.frees_bytes is None


async def test_a_description_of_a_file_that_has_gone_is_counted(resources: Resources) -> None:
    store = VectorStore(resources.database)
    here = await _asset(resources)
    await store.put(here, [(0, unit(1.0))], revision=REVISION)
    await store.put("01HX0000000000000000000ZZZ", [(0, unit(0.0, 1.0))], revision=REVISION)

    assert (await OrphanedDescriptions(resources).survey()).count == 1


async def test_the_survey_removes_nothing(resources: Resources) -> None:
    """The rule that holds for every tidying: it says what it would remove before removing
    anything. A survey with a side effect is a control nobody can use carefully."""
    store = VectorStore(resources.database)
    await store.put("01HX0000000000000000000ZZZ", [(0, unit(1.0))], revision=REVISION)

    await OrphanedDescriptions(resources).survey()
    await OrphanedDescriptions(resources).survey()

    assert await store.held_ids() == ["01HX0000000000000000000ZZZ"]


async def test_running_it_removes_the_orphans_and_keeps_everything_else(
    resources: Resources,
) -> None:
    """The one thing that can go wrong is taking a file that is still here. Nothing on any screen
    would show it: the file keeps its thumbnail, its tags and its place in the library, and simply
    stops being found by a search by meaning."""
    store = VectorStore(resources.database)
    here = await _asset(resources)
    await store.put(here, [(0, unit(1.0))], revision=REVISION)
    await store.put("01HX0000000000000000000ZZZ", [(0, unit(0.0, 1.0))], revision=REVISION)

    removed = await OrphanedDescriptions(resources).run()

    assert removed == 1
    assert await store.held_ids() == [here]
    assert await store.describes(here, revision=REVISION) != []


async def test_running_it_on_a_library_with_nothing_to_remove_does_nothing(
    resources: Resources,
) -> None:
    store = VectorStore(resources.database)
    here = await _asset(resources)
    await store.put(here, [(0, unit(1.0))], revision=REVISION)

    assert await OrphanedDescriptions(resources).run() == 0
    assert await store.held_ids() == [here]


async def test_deleting_the_file_is_what_makes_its_description_an_orphan(
    resources: Resources,
) -> None:
    """The whole mechanism, end to end: described while it was here, orphaned by the delete, and
    the count moving is the proof that nothing cascaded it away on the way out."""
    store = VectorStore(resources.database)
    asset_id = await _asset(resources)
    await store.put(asset_id, [(0, unit(1.0))], revision=REVISION)

    assert (await OrphanedDescriptions(resources).survey()).count == 0

    await resources.database.execute("DELETE FROM assets WHERE id = ?", (asset_id,))

    assert (await OrphanedDescriptions(resources).survey()).count == 1
    assert await OrphanedDescriptions(resources).run() == 1
    assert await store.held_ids() == []


# --- descriptions from a model that is no longer the one in use ----------------------------------


class _Chosen:
    """The preferences seam, answering which set of models is chosen and nothing else."""

    def __init__(self, family: str) -> None:
        self._family = family

    async def get_app(self, key: str) -> object:
        return self._family

    async def get_user(self, user_id: str, key: str) -> object:
        return None


async def _described(resources: Resources, revision: str) -> str:
    """One file, described by this model: frames in the index and the record beside them."""
    from sift.slices.semantic.records import Records

    asset_id = await _asset(resources)
    await VectorStore(resources.database).put(asset_id, [(0, unit(1.0))], revision=revision)
    await Records(resources.database).mark(asset_id, revision=revision, frames=1, at_ms=1)
    return asset_id


async def test_descriptions_by_a_model_no_longer_in_use_are_counted_and_taken_on_a_press(
    resources: Resources,
) -> None:
    """Nothing in a search reads them (every read asks the model in use) and they stay until
    somebody presses this, because going back to that model would mean describing again."""
    from dataclasses import replace

    from sift.slices.semantic import weights
    from sift.slices.semantic.records import Records
    from sift.slices.semantic.tidy import SupersededDescriptions

    in_use = weights.working_set("compact")[0].revision
    kept = await _described(resources, in_use)
    await _described(resources, "a-model-switched-away-from")
    chosen = replace(resources, preferences=_Chosen("compact"))
    tidying = SupersededDescriptions(chosen)

    assert (await tidying.survey()).count == 1
    assert (await tidying.survey()).count == 1, "the survey removes nothing"

    assert await tidying.run() == 1

    store = VectorStore(resources.database)
    assert await store.revisions_held() == {in_use: 1}
    assert await store.describes(kept, revision=in_use) != []
    assert await Records(resources.database).described_by_others(in_use) == 0
    assert (await tidying.survey()).count == 0


async def test_with_nothing_to_say_which_model_is_chosen_nothing_is_counted_or_taken(
    resources: Resources,
) -> None:
    """None is the honest answer to "which are stale" when the chosen model cannot be read, and
    it must never read as "all of them"."""
    from sift.slices.semantic.tidy import SupersededDescriptions

    await _described(resources, "a-model-switched-away-from")
    tidying = SupersededDescriptions(resources)

    assert (await tidying.survey()).count is None
    assert await tidying.run() == 0
    assert await VectorStore(resources.database).count() > 0


async def test_a_chosen_model_this_build_does_not_know_counts_nothing_and_takes_nothing(
    resources: Resources,
) -> None:
    """A setting naming a model family a later or earlier build knew and this one does not: which
    descriptions are stale is then unknowable, and unknowable must never read as "all of them":
    that would delete the only descriptions the library has."""
    from dataclasses import replace

    from sift.slices.semantic.tidy import SupersededDescriptions

    await _described(resources, "a-model-switched-away-from")
    tidying = SupersededDescriptions(replace(resources, preferences=_Chosen("no-such-family")))

    assert (await tidying.survey()).count is None
    assert await tidying.run() == 0
    assert await VectorStore(resources.database).count() > 0
