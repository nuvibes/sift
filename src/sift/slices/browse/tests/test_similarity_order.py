# SPDX-License-Identifier: AGPL-3.0-or-later
"""Similarity as an order: closest to the words of a Smart Search, or to the file a wall is like.

Two decisions on the route, both with a right answer for every input. What a page is ordered by
when nothing is named (a Smart Search reads closest to its words first), and whether the words are
answered by the model (an order of Similarity to a FILE must not send its typed words there).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import DEFAULT_SORT, RELEVANCE, SIMILARITY, AssetFilter
from sift.slices.browse.router import ordering_for
from sift.slices.browse.tests.conftest import Library, sign_in
from sift.slices.search.filters import FilterCompiler

pytestmark = pytest.mark.integration

RANKED = (("a", 0.1), ("b", 0.2))


def test_a_smart_search_with_no_order_named_reads_closest_to_its_words_first() -> None:
    """Closest match is the word index's score, which a file only the model named does not have:
    as the default of a Smart Search it would read newest first."""
    words = AssetFilter(text="a red car", neighbours=RANKED)

    assert ordering_for(None, words, by_meaning=True) == SIMILARITY
    assert ordering_for(None, AssetFilter(text="a red car"), by_meaning=True) == SIMILARITY


def test_a_search_for_the_words_still_reads_closest_match_first() -> None:
    assert ordering_for(None, AssetFilter(text="a red car")) == RELEVANCE
    assert ordering_for(None, AssetFilter(text="a red car", neighbours=RANKED)) == RELEVANCE


def test_an_order_named_on_a_smart_search_is_the_order_given() -> None:
    words = AssetFilter(text="a red car", neighbours=RANKED)

    assert ordering_for("newest", words, by_meaning=True) == "newest"
    assert ordering_for(RELEVANCE, words, by_meaning=True) == RELEVANCE
    assert ordering_for(SIMILARITY, AssetFilter(), by_meaning=False) == SIMILARITY


def test_nothing_to_compare_with_is_the_ordinary_order() -> None:
    assert ordering_for(None, AssetFilter(), by_meaning=True) == DEFAULT_SORT


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Whether each request's words were handed to the model, as the route decided it."""
    seen: list[bool] = []
    narrow = FilterCompiler.narrow

    async def recording(self: FilterCompiler, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["by_meaning"])
        return await narrow(self, *args, **kwargs)

    monkeypatch.setattr(FilterCompiler, "narrow", recording)
    return seen


def test_similarity_to_a_file_keeps_its_words_a_search_for_the_words(
    client: TestClient, library: Library, asked: list[bool]
) -> None:
    """`meaning=0` beside `sort=similarity` is a wall like a file with words typed on it: the
    words are found as words, and the closeness is to the file."""
    sign_in(client, "admin")

    answer = client.get(
        "/api/assets",
        params={"q": f"like:{library.shared} clip", "sort": SIMILARITY, "meaning": "0"},
    )

    assert answer.status_code == 200
    assert asked == [False]


def test_an_older_link_ordered_by_similarity_still_searches_by_meaning(
    client: TestClient, library: Library, asked: list[bool]
) -> None:
    sign_in(client, "admin")

    client.get("/api/assets", params={"q": "clip", "sort": SIMILARITY})
    client.get("/api/assets", params={"q": "clip", "meaning": "1"})
    client.get("/api/assets", params={"q": "clip", "sort": "newest"})

    assert asked == [True, True, False]
