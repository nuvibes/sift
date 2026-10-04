# SPDX-License-Identifier: AGPL-3.0-or-later
"""`like:<id>`: the files similar to one file, as a filter every other token composes with.

Three properties. The subject is checked before anything is compared, so the filter is no way to
ask whether a file exists. The ranking is carried into the read, so a wall of lookalikes can be
closest first. And a tight filter beside it widens the ask, as it does for the words.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel.access import Role, Viewer, Where
from sift.slices.search.filters import (
    CANDIDATE_CEILING,
    CANDIDATES,
    FilterCompiler,
    parse,
    problems_in,
)

pytestmark = pytest.mark.unit

SUBJECT = "01HX0000000000000000000001"
HIDDEN = "01HX0000000000000000000002"
VIEWER = Viewer(id="viewer", role=Role.GUEST)


class Lookalikes:
    """The index, answering as much of a long ranking as it is asked for."""

    def __init__(self, order: list[str]) -> None:
        self._order = order
        self.asked: list[tuple[str, int]] = []

    async def lookalikes(self, asset_id: str, *, limit: int) -> tuple[tuple[str, float], ...]:
        self.asked.append((asset_id, limit))
        return tuple((name, at / 1000) for at, name in enumerate(self._order[:limit]))


class Read:
    """The scoped read: which subjects this viewer may see, and a page filtered to `allowed`."""

    def __init__(self, visible: set[str], allowed: set[str] | None = None) -> None:
        self._visible = visible
        self._allowed = allowed
        self.filters: list[Any] = []

    async def can_view(self, viewer: Any, asset_id: str) -> bool:
        return asset_id in self._visible

    async def visible_assets(self, viewer: Any, **kwargs: Any) -> Any:
        asset_filter = kwargs["asset_filter"]
        self.filters.append(asset_filter)
        named = _named(asset_filter.where)
        kept = [one for one in named if self._allowed is None or one in self._allowed]
        return SimpleNamespace(items=kept, total=len(kept))


def _named(node: Any) -> tuple[str, ...]:
    if isinstance(node, Where) and node.key == "assets":
        return tuple(str(value) for value in node.values)
    for part in getattr(node, "parts", ()):
        found = _named(part)
        if found:
            return found
    return ()


async def test_a_visible_file_filters_to_its_lookalikes_closest_first() -> None:
    seam = Lookalikes(["a", "b", "c"])
    compiler = FilterCompiler(Read({SUBJECT}), semantic=seam)  # type: ignore[arg-type]

    narrowed = await compiler.narrow(VIEWER, {"q": f"like:{SUBJECT}"}, by_meaning=False, need=10)

    assert seam.asked == [(SUBJECT, CANDIDATES)]
    assert set(_named(narrowed.asset_filter.where)) == {"a", "b", "c"}
    assert narrowed.asset_filter.neighbours == (("a", 0.0), ("b", 0.001), ("c", 0.002))


async def test_a_file_two_likes_both_name_sits_at_the_nearer_of_its_two_distances() -> None:
    """`like:` twice is the files near either one; a file both name is as close as it is to the
    nearer of the two, and is listed once."""
    other = "01HX0000000000000000000003"

    class TwoRankings:
        async def lookalikes(self, asset_id: str, *, limit: int) -> tuple[tuple[str, float], ...]:
            if asset_id == SUBJECT:
                return (("a", 0.1), ("b", 0.4))
            return (("b", 0.2), ("a", 0.3))

    compiler = FilterCompiler(Read({SUBJECT, other}), semantic=TwoRankings())  # type: ignore[arg-type]

    narrowed = await compiler.narrow(
        VIEWER, {"q": f"like:{SUBJECT} or like:{other}"}, by_meaning=False, need=10
    )

    assert narrowed.asset_filter.neighbours == (("a", 0.1), ("b", 0.2))


async def test_a_file_the_viewer_cannot_see_answers_as_an_invented_id_does() -> None:
    """Checked BEFORE the comparison: asked afterwards, a concealed file would come back with a
    populated list and an invented id with an empty one, and the two replies would differ."""
    seam = Lookalikes(["a", "b"])
    compiler = FilterCompiler(Read({SUBJECT}), semantic=seam)  # type: ignore[arg-type]

    hidden = await compiler.narrow(VIEWER, {"like": HIDDEN}, by_meaning=False, need=10)
    invented = await compiler.narrow(
        VIEWER, {"like": "01HX0000000000000000000009"}, by_meaning=False, need=10
    )

    assert seam.asked == []
    assert hidden.asset_filter == invented.asset_filter
    assert _named(hidden.asset_filter.where) == ()


def test_a_value_that_is_no_id_is_named_as_a_problem() -> None:
    found = problems_in(parse({"q": "like:beach"}), now=0)

    assert [(one.field, one.value) for one in found] == [("like", "beach")]
    assert problems_in(parse({"q": f"like:{SUBJECT}"}), now=0) == []


async def test_a_tight_filter_beside_it_looks_further_down_the_ranking() -> None:
    order = [f"asset-{at}" for at in range(900)]
    wanted = {f"asset-{at}" for at in range(CANDIDATES + 100, CANDIDATES + 110)}
    seam = Lookalikes(order)
    compiler = FilterCompiler(Read({SUBJECT}, wanted), semantic=seam)  # type: ignore[arg-type]

    narrowed = await compiler.narrow(VIEWER, {"like": SUBJECT}, by_meaning=False, need=10)

    assert [limit for _, limit in seam.asked] == [CANDIDATES, CANDIDATE_CEILING]
    assert narrowed.complete is True


async def test_a_query_without_one_asks_the_index_nothing() -> None:
    seam = Lookalikes(["a"])
    compiler = FilterCompiler(Read({SUBJECT}), semantic=seam)  # type: ignore[arg-type]

    narrowed = await compiler.narrow(VIEWER, {"q": "media:video"}, by_meaning=False, need=10)

    assert seam.asked == []
    assert narrowed.asset_filter.neighbours == ()
