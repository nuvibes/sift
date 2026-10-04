# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rule that turns loose pictures into shoots, with the index stood in for.

Every case here is about a decision the rule makes rather than about arithmetic: what it does with
a picture the index has never described, where the distance cuts, and what it refuses to propose.
Those are the lines that decide whether somebody is shown a grouping they can check.
"""

from __future__ import annotations

import asyncio
import itertools
import math
import random
import time
from typing import Any

import pytest

from sift.slices.shoots import clustering
from sift.slices.shoots.clustering import DISTANCE, MOST, REACH, among, shoots

#: The floor these cases are run at. `shoots` takes it as a required keyword: the number itself is
#: the Photo Sets' rule and the composition root hands it across the seam, so a test of the RULE
#: names the smallest interesting floor and the last case below holds the floor actually being
#: obeyed at the live figure.
_LEAST = 3

#: What the pass runs at in use: the Photo Sets' `MIN_PICTURES`, written out here
#: because this slice may not import that one. `test_the_floor_is_the_photo_sets_own` in
#: `test_service.py` is what holds the two together.
_LIVE_FLOOR = 10


def index(answers: dict[str, list[tuple[str, float]]]):  # type: ignore[no-untyped-def]
    """An index that answers what it was told to, and None for anything it has never described."""

    async def neighbours(asset_id: str) -> tuple[tuple[str, float], ...] | None:
        found = answers.get(asset_id)
        return None if found is None else tuple(found)

    return neighbours


@pytest.mark.asyncio
async def test_a_run_inside_the_distance_is_one_shoot() -> None:
    found = await shoots(
        ["a", "b", "c"],
        index({"a": [("b", 0.1), ("c", 0.2)], "b": [], "c": []}),
        least=_LEAST,
    )
    assert found == [["a", "b", "c"]]


@pytest.mark.asyncio
async def test_a_neighbour_past_the_distance_is_left_out() -> None:
    """And everything after it, because the index answers nearest first."""
    found = await shoots(
        ["a", "b", "c", "d"],
        index(
            {
                "a": [("b", 0.1), ("c", DISTANCE + 0.01), ("d", DISTANCE + 0.02)],
                "b": [],
                "c": [("d", 0.05)],
                "d": [],
            }
        ),
        least=_LEAST,
    )
    # `a` keeps only `b`, which is two and fewer than the fewest, so nothing is proposed from it.
    # `c` and `d` are two as well. Neither pair is a shoot.
    assert found == []


@pytest.mark.asyncio
async def test_a_picture_the_index_has_never_described_is_not_a_shoot_of_one() -> None:
    """None is a fact about how far the background pass has got, not a failure and not a group."""
    found = await shoots(["a", "b", "c"], index({"b": [("c", 0.1)], "c": []}), least=_LEAST)
    assert found == []


@pytest.mark.asyncio
async def test_a_group_smaller_than_the_fewest_is_not_proposed() -> None:
    found = await shoots(["a", "b"], index({"a": [("b", 0.01)], "b": []}), least=_LEAST)
    assert found == []


@pytest.mark.asyncio
async def test_a_group_past_the_ceiling_is_dropped_rather_than_trimmed() -> None:
    """A trimmed group would be an arbitrary sixty of a hundred presented as a found shoot."""
    pool = [f"a{one:03d}" for one in range(MOST + 5)]
    found = await shoots(pool, index({pool[0]: [(one, 0.05) for one in pool[1:]]}), least=_LEAST)
    assert found == []


@pytest.mark.asyncio
async def test_a_seed_is_spent_once_but_its_members_are_not() -> None:
    """The two halves of what stops the rule looping and what stops it losing work.

    A seed is claimed BEFORE its group is judged, so a picture that leads nothing is not asked
    about twice: a creator whose thousand pictures never cluster pays one lookup each and not one
    per pair. What is NOT claimed is the members of a group that was refused for being too small:
    they were never part of a shoot, and a later seed may well lead one that includes them.
    """
    answers = {
        "a": [("b", 0.01)],
        # Nearness is mutual, as the index answers it: `b` sees `a` as `a` sees `b`.
        "b": [("a", 0.01), ("c", 0.01), ("d", 0.01)],
        "c": [],
        "d": [],
    }
    found = await shoots(["a", "b", "c", "d"], index(answers), least=_LEAST)
    # `a` claims itself and finds only `b`, which is two and too few, so nothing is proposed and
    # `b` is left available. `b` then leads the three that really are one, and `a`, spent as a
    # seed, is not among them.
    assert found == [["b", "c", "d"]]


@pytest.mark.asyncio
async def test_a_picture_already_in_a_shoot_is_not_in_a_second_one() -> None:
    found = await shoots(
        ["a", "b", "c", "d", "e"],
        index(
            {
                "a": [("b", 0.01), ("c", 0.02)],
                "d": [("b", 0.01), ("c", 0.01), ("e", 0.01)],
                "b": [],
                "c": [],
                "e": [],
            }
        ),
        least=_LEAST,
    )
    assert found == [["a", "b", "c"]]


@pytest.mark.asyncio
async def test_the_pool_order_is_the_order_of_the_shoot() -> None:
    """A gallery arrives numbered, and a set built in the index's order is that sitting shuffled."""
    found = await shoots(
        ["a", "b", "c"],
        index({"a": [("c", 0.01), ("b", 0.02)], "b": [], "c": []}),
        least=_LEAST,
    )
    assert found == [["a", "c", "b"]]


@pytest.mark.asyncio
async def test_a_group_under_the_live_floor_is_not_proposed() -> None:
    """Nine pictures of one sitting, at the floor the pass really runs at, are not a shoot.

    The rule obeys whatever floor it is handed; what can go wrong is which number reaches it, so
    the case that matters is the live one.
    """
    pool = [f"a{one}" for one in range(_LIVE_FLOOR - 1)]
    near = {pool[0]: [(one, 0.05) for one in pool[1:]]}
    assert await shoots(pool, index(near), least=_LIVE_FLOOR) == []
    assert await shoots(pool, index(near), least=_LEAST) == [pool]


@pytest.mark.asyncio
async def test_in_memory_the_pool_groups_exactly_as_the_index_grouped_it() -> None:
    """The pass compares the pool's own numbers in memory, and gets the same answer as asking the
    index for each picture's library-wide nearest and keeping the pool's members. Same fixture both
    ways (sittings of unit vectors with noise, a pool, and pictures OUTSIDE the pool crowding the
    index's answer) and the same shoots come out."""
    rng = random.Random(7)

    def unit(values: list[float]) -> list[float]:
        length = math.sqrt(sum(one * one for one in values))
        return [one / length for one in values]

    vectors: dict[str, list[float]] = {}
    for sitting in range(12):
        centre = [rng.gauss(0.0, 1.0) for _ in range(32)]
        for one in range(rng.randint(1, 9)):
            vectors[f"s{sitting}-{one}"] = unit([v + rng.gauss(0.0, 0.03) for v in centre])
    pool = sorted(vectors)
    outside = {
        f"x{n}": unit([v + rng.gauss(0.0, 0.03) for v in vectors[rng.choice(pool)]])
        for n in range(40)
    }
    library = {**vectors, **outside}

    async def library_wide(asset_id: str) -> tuple[tuple[str, float], ...] | None:
        seed = library[asset_id]
        apart = sorted(
            (math.dist(seed, other), key) for key, other in library.items() if key != asset_id
        )
        return tuple((key, distance) for distance, key in apart[:REACH])

    before = await shoots(pool, library_wide, least=_LEAST)
    after = await shoots(pool, await among(pool, vectors), least=_LEAST)

    assert before, "the fixture has to group something or it proves nothing"
    assert after == before


@pytest.mark.asyncio
async def test_a_picture_with_no_numbers_leads_nothing_in_memory_either() -> None:
    neighbours = await among(["a", "b"], {"a": [1.0, 0.0]})
    assert await neighbours("b") is None
    assert await neighbours("a") == ()


@pytest.mark.asyncio
async def test_a_pool_with_no_numbers_at_all_answers_none_for_every_picture() -> None:
    """Nothing described yet is how far the background pass has got: no neighbours, not a failure."""
    neighbours = await among(["a", "b"], {"a": [], "b": []})

    assert await neighbours("a") is None
    assert await shoots(["a", "b"], neighbours, least=2) == []


@pytest.mark.asyncio
async def test_the_pools_numbers_are_arranged_off_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pool of a few thousand pictures is millions of numbers: arranged on the loop, one creator's
    grouping holds every page and video for as long, so the arranging goes to a thread."""
    arrange = clustering._matrix_of

    def slow(ids: Any, vectors: Any) -> Any:
        time.sleep(0.3)
        return arrange(ids, vectors)

    monkeypatch.setattr(clustering, "_matrix_of", slow)
    ticks: list[float] = []

    async def tick() -> None:
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.02)
    try:
        neighbours = await among(["a", "b"], {"a": [1.0, 0.0], "b": [1.0, 0.0]})
        assert await neighbours("a") == (("b", 0.0),)
        await asyncio.sleep(0.02)
    finally:
        ticker.cancel()
    longest = max(later - earlier for earlier, later in itertools.pairwise(ticks))
    assert longest < 0.15, f"the loop was held for {longest:.3f} s by the arithmetic"
