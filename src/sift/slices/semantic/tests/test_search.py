# SPDX-License-Identifier: AGPL-3.0-or-later
"""What crosses the boundary between the search box and the index, and what does not.

**An answer, never a question.** A list of files and how far each sat from what was asked. The read
that decides who may see what then orders by that list, without ever knowing there is a vector
index, which is what keeps a pre-1.0 dependency swappable and the permission rules in one place.

**None and empty are different answers and the difference matters.** None means this install cannot
answer by meaning right now (switched off, no models, no add-on), and the caller falls back to
the ordinary order. An empty list means the question WAS asked and nothing came near, which is a
result and orders the page by nothing.

**A file is never its own lookalike.** Every file is nearest to itself, and "this is like this" is
not an answer to anything.
"""

from __future__ import annotations

from typing import Any

import pytest

from sift.kernel.access import Role, Viewer
from sift.slices.semantic.search import SemanticSearch
from sift.slices.semantic.store import Neighbour

pytestmark = pytest.mark.unit

U, GUEST = Viewer(id="u", role=Role.GUEST), Viewer(id="guest", role=Role.GUEST)
ADMIN = Viewer(id="admin", role=Role.ADMIN)


class Service:
    """The feature, answering whatever the test set up."""

    def __init__(
        self,
        *,
        query_vector: list[float] | None = None,
        supported: bool = True,
        describes: list[float] | None = None,
        found: list[Neighbour] | None = None,
        anything: bool = True,
    ) -> None:
        self._query_vector = query_vector
        self._supported = supported
        self._describes = describes if describes is not None else []
        self._found = found if found is not None else []
        self._anything = anything
        self.asked_for: list[int] = []
        self.askers: list[Viewer | None] = []
        self.asked_anything = 0
        self.asked_many: list[list[str]] = []

    async def describe_query(self, text: str) -> list[float] | None:
        return self._query_vector

    async def readiness(self) -> Any:
        return type("Readiness", (), {"supported": self._supported})()

    async def describes(self, asset_id: str) -> list[float]:
        return self._describes

    async def nearest(
        self, vector: list[float], *, limit: int, asker: Viewer | None = None
    ) -> list[Neighbour]:
        self.asked_for.append(limit)
        self.askers.append(asker)
        return self._found

    async def describes_anything(self) -> bool:
        self.asked_anything += 1
        return self._anything

    async def describes_many(self, asset_ids: list[str]) -> dict[str, list[float]]:
        self.asked_many.append(list(asset_ids))
        return {asset_id: self._describes for asset_id in asset_ids}

    async def similar_to(self, asset_id: str, *, limit: int, asker: Viewer | None) -> Any:
        self.asked_for.append(limit)
        self.askers.append(asker)
        neighbours = tuple((one.asset_id, one.distance) for one in self._found)
        return type("Similar", (), {"neighbours": neighbours})()


def search(**kwargs: Any) -> tuple[SemanticSearch, Service]:
    service = Service(**kwargs)
    return SemanticSearch(service), service  # type: ignore[arg-type]


# --- words ---------------------------------------------------------------------------------


async def test_only_the_last_few_queries_are_remembered() -> None:
    """A vector per distinct query; the oldest go first."""
    from sift.slices.semantic.search import REMEMBERED

    finder, _service = search(query_vector=[1.0], found=[])
    for index in range(REMEMBERED + 3):
        await finder.neighbours(f"query {index}", asker=U)

    held = finder._kept["u"].vectors
    assert len(held) == REMEMBERED
    assert "query 0" not in held and "query 3" in held


async def test_words_come_back_as_files_and_distances() -> None:
    finder, _service = search(
        query_vector=[1.0],
        found=[Neighbour("a", 0, 0.1), Neighbour("b", 2000, 0.4)],
    )

    assert await finder.neighbours("a dog on a beach", asker=None) == (("a", 0.1), ("b", 0.4))


async def test_an_install_that_cannot_answer_says_so_rather_than_nothing() -> None:
    """None, not an empty list, which would order a page by nothing."""
    finder, _service = search(query_vector=None)

    assert await finder.neighbours("anything", asker=None) is None


async def test_a_question_that_matched_nothing_is_an_empty_answer() -> None:
    finder, _service = search(query_vector=[1.0], found=[])

    assert await finder.neighbours("anything", asker=None) == ()


# --- a file --------------------------------------------------------------------------------


async def test_a_file_comes_back_with_what_looks_like_it() -> None:
    finder, _service = search(
        describes=[1.0],
        found=[Neighbour("mine", 0, 0.0), Neighbour("other", 0, 0.2)],
    )

    assert await finder.like_asset("mine") == (("other", 0.2),)


async def test_a_machine_that_cannot_hold_the_index_answers_none() -> None:
    finder, _service = search(supported=False, describes=[1.0])

    assert await finder.like_asset("mine") is None


async def test_a_file_the_pass_has_not_reached_answers_none() -> None:
    """Not a failure: a fact about how far the background work has got. The caller falls back to
    the tier that needs no index."""
    finder, _service = search(describes=[])

    assert await finder.like_asset("mine") is None


async def test_more_candidates_are_asked_for_than_a_page_holds() -> None:
    """The permission rules run afterwards, so a page's worth would come back short."""
    finder, service = search(query_vector=[1.0], found=[])

    await finder.neighbours("anything", asker=None)

    assert service.asked_for == [200]


# --- what is remembered between the grid's repeated questions ---------------------------------


class Counting(Service):
    """The stand-in again, counting how often the model is asked to describe words."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.described = 0

    async def describe_query(self, text: str) -> list[float] | None:
        self.described += 1
        return await super().describe_query(text)


async def test_the_same_words_are_ranked_once_under_one_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grid asks the same question several times per search; the model and the index
    answer it once while nothing has changed."""
    from sift.slices.semantic import search as module

    monkeypatch.setattr(module, "current_mark", lambda: "m1")
    service = Counting(query_vector=[1.0], found=[Neighbour("A", 0, 0.1)])
    searching = SemanticSearch(service)  # type: ignore[arg-type]
    first = await searching.neighbours("red bikini", limit=200, asker=U)
    second = await searching.neighbours("red bikini", limit=200, asker=U)
    assert first == second == (("A", 0.1),)
    assert service.described == 1
    assert service.asked_for == [200]
    # A deeper reach is a different ranking, but the same words: the index again, the model not.
    await searching.neighbours("red bikini", limit=400, asker=U)
    assert service.described == 1
    assert service.asked_for == [200, 400]


async def test_a_moved_mark_ranks_again_but_keeps_the_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.slices.semantic import search as module

    marks = iter(["m1", "m2"])
    monkeypatch.setattr(module, "current_mark", lambda: next(marks))
    service = Counting(query_vector=[1.0], found=[Neighbour("A", 0, 0.1)])
    searching = SemanticSearch(service)  # type: ignore[arg-type]
    await searching.neighbours("red bikini", asker=U)
    await searching.neighbours("red bikini", asker=U)
    assert service.asked_for == [200, 200], "the index moved, so it is asked again"
    assert service.described == 1, "what the words mean did not move with it"


async def test_nothing_is_kept_when_the_words_cannot_be_described(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that is not ready yet answers nothing, and the next ask gets to find out."""
    from sift.slices.semantic import search as module

    monkeypatch.setattr(module, "current_mark", lambda: "m1")
    service = Counting(query_vector=None, found=[Neighbour("A", 0, 0.1)])
    searching = SemanticSearch(service)  # type: ignore[arg-type]
    assert await searching.neighbours("red bikini", asker=U) is None
    service._query_vector = [1.0]
    assert await searching.neighbours("red bikini", asker=U) == (("A", 0.1),)
    assert service.described == 2


async def test_one_users_search_is_never_quick_for_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quick answer would tell a guest that an admin just searched those words."""
    from sift.slices.semantic import search as module

    monkeypatch.setattr(module, "current_mark", lambda: "m1")
    service = Counting(query_vector=[1.0], found=[Neighbour("A", 0, 0.1)])
    searching = SemanticSearch(service)  # type: ignore[arg-type]
    await searching.neighbours("red bikini", asker=ADMIN)
    await searching.neighbours("red bikini", asker=GUEST)
    assert service.described == 2
    assert service.asked_for == [200, 200]


async def test_another_users_searches_never_push_out_ones_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.slices.semantic import search as module

    monkeypatch.setattr(module, "current_mark", lambda: "m1")
    service = Counting(query_vector=[1.0], found=[Neighbour("A", 0, 0.1)])
    searching = SemanticSearch(service)  # type: ignore[arg-type]
    await searching.neighbours("red bikini", asker=GUEST)
    for index in range(module.REMEMBERED + 1):
        await searching.neighbours(f"query {index}", asker=ADMIN)
    asked = len(service.asked_for)
    await searching.neighbours("red bikini", asker=GUEST)
    assert len(service.asked_for) == asked


async def test_nothing_is_kept_without_an_asker(monkeypatch: pytest.MonkeyPatch) -> None:
    from sift.slices.semantic import search as module

    monkeypatch.setattr(module, "current_mark", lambda: "m1")
    service = Counting(query_vector=[1.0], found=[Neighbour("A", 0, 0.1)])
    searching = SemanticSearch(service)  # type: ignore[arg-type]
    await searching.neighbours("red bikini", asker=None)
    await searching.neighbours("red bikini", asker=None)
    assert service.described == 2
    assert not searching._kept


async def test_only_the_latest_askers_are_remembered() -> None:
    from sift.slices.semantic.search import ASKERS

    finder, _service = search(query_vector=[1.0], found=[])
    for index in range(ASKERS + 1):
        await finder.neighbours("red bikini", asker=Viewer(id=f"user {index}", role=Role.GUEST))
    await finder.neighbours("red bikini", asker=Viewer(id="user 1", role=Role.GUEST))

    assert len(finder._kept) == ASKERS
    assert "user 0" not in finder._kept and list(finder._kept)[-1] == "user 1"


async def test_the_asker_is_who_the_index_ranks_for() -> None:
    finder, service = search(query_vector=[1.0], found=[])

    await finder.neighbours("red bikini", asker=GUEST)

    assert service.askers == [GUEST]


@pytest.mark.parametrize(
    "changed",
    [{"cache_stamp": 1}, {"show_hidden": True}, {"role": Role.ADMIN}],
    ids=["a grant taken away", "the vault opened", "made an admin"],
)
async def test_a_kept_ranking_never_outlives_what_the_asker_may_see(
    monkeypatch: pytest.MonkeyPatch, changed: dict[str, Any]
) -> None:
    """Each moves what the asker may see without the mark moving, so each ranks again."""
    from dataclasses import replace

    from sift.slices.semantic import search as module

    monkeypatch.setattr(module, "current_mark", lambda: "m1")
    finder, service = search(query_vector=[1.0], found=[Neighbour("A", 0, 0.1)])
    await finder.neighbours("red bikini", asker=GUEST)
    await finder.neighbours("red bikini", asker=replace(GUEST, **changed))

    assert service.askers == [GUEST, replace(GUEST, **changed)]


# --- whether there is anything to compare against at all ------------------------------------


async def test_an_install_with_descriptions_can_answer() -> None:
    finder, service = search(anything=True)

    assert await finder.can_answer() is True
    assert service.asked_anything == 1, "one read, not one per file"


async def test_an_install_that_has_described_nothing_cannot_answer() -> None:
    """The index works and holds nothing the model in use put there: a new library, the feature
    switched on this minute, or a model changed under the previous one's numbers."""
    finder, _service = search(anything=False)

    assert await finder.can_answer() is False


async def test_a_machine_that_cannot_hold_the_index_cannot_answer() -> None:
    """Asked BEFORE the index is read: on this machine there is nothing to read."""
    finder, service = search(supported=False, anything=True)

    assert await finder.can_answer() is False
    assert service.asked_anything == 0


# --- many files at once --------------------------------------------------------------------


async def test_many_files_are_described_in_one_ask() -> None:
    finder, service = search(describes=[0.6, 0.8])

    assert await finder.describe_many(["a", "b"]) == {"a": [0.6, 0.8], "b": [0.6, 0.8]}
    assert service.asked_many == [["a", "b"]]


async def test_a_machine_that_cannot_hold_the_index_describes_none_of_them() -> None:
    """None, not an empty map: an empty map would read as "none of these files was described",
    and the caller would go on asking file by file for answers that can never come."""
    finder, service = search(supported=False, describes=[0.6, 0.8])

    assert await finder.describe_many(["a", "b"]) is None
    assert service.asked_many == []


async def test_a_files_lookalikes_are_the_strip_s_answer_at_the_wall_s_length() -> None:
    """A wall filtered by `like:` asks the question the strip under a file asks, for more of it, so
    the wall holds what the strip draws."""
    finder, service = search(found=[Neighbour("b", 0, 0.2), Neighbour("c", 0, 0.3)])

    assert await finder.lookalikes("a", limit=7, asker=U) == (("b", 0.2), ("c", 0.3))
    assert service.asked_for == [7]
    assert service.askers == [U]
