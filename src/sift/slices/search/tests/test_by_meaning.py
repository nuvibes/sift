# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking a model what the words mean, from a search box that must not know how.

It falls back to the ordinary order when the install cannot answer, it asks only when there is
something to ask, and what crosses the seam is files and distances, never a query.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NamedTuple

import pytest

from sift.kernel.access import (
    DEFAULT_SORT,
    SIMILARITY,
    AllOf,
    AnyOf,
    AssetFilter,
    Repository,
    Where,
)
from sift.kernel.db import Database
from sift.slices.search.filters import CANDIDATE_CEILING, CANDIDATES, FilterCompiler

pytestmark = pytest.mark.integration


class Recording:
    """The feature that owns the index, answering whatever the test set up."""

    def __init__(self, answer: tuple[tuple[str, float], ...] | None) -> None:
        self._answer = answer
        self.asked: list[str] = []

    async def neighbours(
        self, text: str, *, limit: int = CANDIDATES, asker: Any = None
    ) -> tuple[tuple[str, float], ...] | None:
        self.asked.append(text)
        return self._answer

    async def like_asset(
        self, asset_id: str, *, limit: int = CANDIDATES
    ) -> tuple[tuple[str, float], ...] | None:
        return self._answer

    async def can_answer(self) -> bool:
        return True


class Watching:
    """Stands in for the read, so what the filter carried into it can be looked at."""

    def __init__(self) -> None:
        self.filters: list[AssetFilter] = []
        self.sorts: list[str] = []

    async def visible_assets(self, viewer: Any, **kwargs: Any) -> Any:
        self.filters.append(kwargs["asset_filter"])
        self.sorts.append(kwargs["sort"])
        return type("Page", (), {"items": [], "total": 0})()


class Found(NamedTuple):
    """A page, and whether the search reached the end of what it was looking through."""

    page: Any
    complete: bool


def searching(read: Any, seam: Any) -> Callable[..., Awaitable[Found]]:
    """The two steps a library page takes, run directly: the engine compiles the query, and the
    scoped read is handed the constraints. What passes between them is under test."""
    compiler = FilterCompiler(read, semantic=seam)

    async def run(
        viewer: Any,
        raw: Mapping[str, str],
        *,
        limit: int,
        offset: int,
        sort: str = DEFAULT_SORT,
        by_meaning: bool | None = None,
    ) -> Found:
        # Search and arrangement are separate; by default, closest first means asking the model.
        asking = sort == SIMILARITY if by_meaning is None else by_meaning
        narrowed = await compiler.narrow(viewer, raw, by_meaning=asking, need=offset + limit)
        page = await read.visible_assets(
            viewer, limit=limit, offset=offset, asset_filter=narrowed.asset_filter, sort=sort
        )
        return Found(page, narrowed.complete)

    return run


def wired(
    database: Database, access: Repository, answer: tuple[tuple[str, float], ...] | None
) -> tuple[Callable[..., Awaitable[Found]], Recording, Watching]:
    seam = Recording(answer)
    watching = Watching()
    return searching(watching, seam), seam, watching


async def test_what_the_model_said_is_carried_into_the_read(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    search, seam, watching = wired(temp_db, access, (("a", 0.1), ("b", 0.4)))

    await search(actors.admin, {"q": "a dog on a beach"}, limit=10, offset=0, sort=SIMILARITY)

    assert seam.asked == ["a dog on a beach"]
    assert watching.filters[0].neighbours == (("a", 0.1), ("b", 0.4))
    assert watching.sorts[0] == SIMILARITY


async def test_an_install_that_cannot_answer_falls_back_to_the_ordinary_order(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """An install that cannot answer orders by nothing, falling through to the grid's order."""
    search, _seam, watching = wired(temp_db, access, None)

    await search(actors.admin, {"q": "a dog"}, limit=10, offset=0, sort=SIMILARITY)

    assert watching.filters[0].neighbours == ()
    # The order asked for stands; it has nothing to rank by.
    assert watching.sorts[0] == SIMILARITY


async def test_an_empty_box_is_not_a_question_worth_asking_a_model(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    search, seam, _watching = wired(temp_db, access, (("a", 0.1),))

    await search(actors.admin, {"q": "   "}, limit=10, offset=0, sort=SIMILARITY)

    assert seam.asked == []


async def test_the_ordinary_order_never_asks_a_model_anything(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """The ordinary order never asks the model: the feature is opt-in per search."""
    search, seam, _watching = wired(temp_db, access, (("a", 0.1),))

    await search(actors.admin, {"q": "a dog"}, limit=10, offset=0)

    assert seam.asked == []


async def test_an_install_with_no_such_feature_at_all_searches_normally(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """With no semantic slice registered, asking for the order still works."""
    watching = Watching()
    search = searching(watching, None)

    await search(actors.admin, {"q": "a dog"}, limit=10, offset=0, sort=SIMILARITY)

    assert watching.filters[0].neighbours == ()


# --- what the words are actually FOR ------------------------------------------------------------
#
# Once the model answers, the words stop being a filter on their own: a search for what a picture
# shows cannot be a re-ordering of a search for what it is named.


def narrowing(page_filter: AssetFilter) -> str:
    """The filter as the SQL it becomes."""
    sql, _bound = page_filter.predicate()
    return sql


async def test_the_typed_words_stop_narrowing_once_a_model_has_answered(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """Once the model answers, the words no longer narrow alone: the model's files and the text
    index's literal matches are ORed, so neither replaces the other."""
    search, _seam, watching = wired(temp_db, access, (("a", 0.1), ("b", 0.4)))

    await search(actors.admin, {"q": "bikini"}, limit=10, offset=0, sort=SIMILARITY)

    sql = narrowing(watching.filters[0])
    assert "assets_fts" in sql, sql
    assert "json_each" in sql, sql
    # OR, not AND, asserted on the SQL.
    assert " OR " in sql, sql
    # The text index appears once, inside the union: `text AND (named OR text)` would be plain text.
    assert sql.count("assets_fts MATCH") == 1, sql
    assert "assets_fts" not in sql.split(" OR ")[0], sql
    # The words stay on the filter: they are what was asked.
    assert watching.filters[0].text == "bikini"


def union(page_filter: AssetFilter) -> AnyOf:
    """The OR the words were answered by, found rather than indexed into."""
    top = page_filter.where
    assert isinstance(top, AllOf), top
    found = [part for part in top.parts if isinstance(part, AnyOf)]
    assert len(found) == 1, top
    return found[0]


async def test_a_short_word_in_the_query_cannot_answer_on_its_own(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """The literal half is two clauses (trigram match and short-term scan) entering the union as
    one AND, so a one-letter substring scan cannot answer alone."""
    search, _seam, watching = wired(temp_db, access, (("a", 0.1), ("b", 0.4)))

    await search(actors.admin, {"q": "a red sports car"}, limit=10, offset=0, sort=SIMILARITY)

    words = union(watching.filters[0])
    assert len(words.parts) == 2, words
    named, literal = words.parts
    assert named == Where("assets", ("a", "b")), named
    # The literal arm ANDs both text clauses.
    assert isinstance(literal, AllOf), literal
    assert set(literal.parts) == {Where("text_match"), Where("text_contains")}, literal

    sql = narrowing(watching.filters[0])
    assert sql.count("assets_fts MATCH") == 1, sql
    assert " OR " in sql, sql
    scan = sql.index(":text_contains")
    match = sql.index("assets_fts MATCH")
    assert " OR " not in sql[min(scan, match) : max(scan, match)], sql


async def test_the_model_narrows_to_what_it_named_and_nothing_else(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    search, _seam, watching = wired(temp_db, access, (("a", 0.1), ("b", 0.4)))

    await search(actors.admin, {"q": "bikini"}, limit=10, offset=0, sort=SIMILARITY)

    _sql, bound = watching.filters[0].predicate()
    named = [value for value in bound.values() if value == '["a", "b"]']
    assert named, bound


async def test_a_token_beside_the_words_still_narrows(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """A token beside the words still narrows the whole answer."""
    search, seam, watching = wired(temp_db, access, (("a", 0.1),))

    await search(actors.admin, {"q": "bikini type:video"}, limit=10, offset=0, sort=SIMILARITY)

    assert seam.asked == ["bikini"]
    sql = narrowing(watching.filters[0])
    # ANDed over the union, so it filters the text matches and the model's alike.
    assert "media_type" in sql, sql
    assert sql.index("media_type") < sql.index(" OR "), sql
    assert "assets_fts" in sql, sql


async def test_a_model_that_found_nothing_is_an_answer_and_not_a_fallback(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """An empty answer is "nothing near"; only None means the install cannot answer."""
    search, _seam, watching = wired(temp_db, access, ())

    await search(actors.admin, {"q": "bikini"}, limit=10, offset=0, sort=SIMILARITY)

    sql = narrowing(watching.filters[0])
    assert "json_each" in sql, sql
    _sql, bound = watching.filters[0].predicate()
    assert "[]" in [value for value in bound.values() if isinstance(value, str)], bound


async def test_falling_back_keeps_the_words_narrowing(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """Falling back keeps the words narrowing, or the control would widen every search."""
    search, _seam, watching = wired(temp_db, access, None)

    await search(actors.admin, {"q": "bikini"}, limit=10, offset=0, sort=SIMILARITY)

    assert "assets_fts" in narrowing(watching.filters[0])


class Ranking:
    """A model answering exactly `limit` rows while it has them, so a short list means the index ran
    out."""

    def __init__(self, order: list[str]) -> None:
        self._order = order
        self.asked: list[int] = []

    async def neighbours(
        self, text: str, *, limit: int = CANDIDATES, asker: Any = None
    ) -> tuple[tuple[str, float], ...] | None:
        self.asked.append(limit)
        return tuple((name, at / 1000) for at, name in enumerate(self._order[:limit]))

    async def like_asset(
        self, asset_id: str, *, limit: int = CANDIDATES
    ) -> tuple[tuple[str, float], ...] | None:
        return None

    async def can_answer(self) -> bool:
        return True


def _named(node: Any) -> tuple[str, ...]:
    """The ids the model's answer filtered to, wherever they sit in the tree."""
    if isinstance(node, Where) and node.key == "assets":
        return tuple(str(value) for value in node.values)
    for part in getattr(node, "parts", ()):
        found = _named(part)
        if found:
            return found
    return ()


class Selective:
    """A read that keeps only the files it was told about, as a tight filter does."""

    def __init__(self, allowed: set[str]) -> None:
        self._allowed = allowed
        self.totals: list[int] = []

    async def visible_assets(self, viewer: Any, **kwargs: Any) -> Any:
        kept = [name for name in _named(kwargs["asset_filter"].where) if name in self._allowed]
        self.totals.append(len(kept))
        return type("Page", (), {"items": kept, "total": len(kept)})()


async def test_it_looks_past_the_first_ask_when_the_filter_cuts_that_far(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """A filter that rejects the first ask's candidates makes the search ask deeper, up to the
    ceiling the vector index answers to."""
    order = [f"asset-{at}" for at in range(900)]
    matching = {f"asset-{at}" for at in range(CANDIDATES + 100, CANDIDATES + 110)}
    seam = Ranking(order)
    read = Selective(matching)
    search = searching(read, seam)

    found = await search(
        actors.admin, {"q": "a dog on a beach"}, limit=10, offset=0, sort=SIMILARITY
    )

    assert found.page.total == 10
    assert found.complete is True
    # It widened, and the first ask is still the small one.
    assert seam.asked[0] == CANDIDATES
    assert len(seam.asked) > 1


async def test_a_page_filled_by_the_first_ask_never_looks_further(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """A page filled by the first ask with every candidate surviving asks once."""
    order = [f"asset-{at}" for at in range(900)]
    seam = Ranking(order)
    read = Selective(set(order))
    search = searching(read, seam)

    found = await search(actors.admin, {"q": "a dog"}, limit=10, offset=0, sort=SIMILARITY)

    assert seam.asked == [CANDIDATES]
    assert found.complete is True


async def test_it_looks_deeper_when_the_narrowing_cut_more_than_half_the_candidates(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """A full page is still asked deeper when the filter cut more than half the candidates, and the
    page is built from the deeper ask."""
    order = [f"asset-{at}" for at in range(900)]
    # Half the ranking matches, spread through it, so looking deeper finds more.
    seam = Ranking(order)
    read = Selective({f"asset-{at}" for at in range(0, 900, 4)})
    search = searching(read, seam)

    found = await search(
        actors.admin, {"q": "a dog on a beach"}, limit=10, offset=0, sort=SIMILARITY
    )

    assert seam.asked == [CANDIDATES, CANDIDATE_CEILING]
    assert found.page.total == CANDIDATE_CEILING // 4
    # A full page off a cut pool is complete: "stopped short" would tell somebody to filter.
    assert found.complete is True


async def test_it_says_so_when_it_stops_looking_rather_than_showing_a_short_page(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """A search unsatisfied within the ceiling says it stopped, rather than looking empty."""
    order = [f"asset-{at}" for at in range(50_000)]
    seam = Ranking(order)
    read = Selective(set())
    search = searching(read, seam)

    found = await search(actors.admin, {"q": "a dog"}, limit=10, offset=0, sort=SIMILARITY)

    assert found.page.total == 0
    assert found.complete is False
    assert seam.asked[-1] == CANDIDATE_CEILING


async def test_a_later_page_has_to_have_the_rows_under_it(
    temp_db: Database, access: Repository, actors: Any
) -> None:
    """A later page needs the rows under it, so an offset widens the ask too."""
    order = [f"asset-{at}" for at in range(900)]
    seam = Ranking(order)
    read = Selective({f"asset-{at}" for at in range(CANDIDATES + 100, CANDIDATES + 160)})
    search = searching(read, seam)

    found = await search(actors.admin, {"q": "a dog"}, limit=10, offset=50, sort=SIMILARITY)

    assert found.page.total == 60
    assert found.complete is True
