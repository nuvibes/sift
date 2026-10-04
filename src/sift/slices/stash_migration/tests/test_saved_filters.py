# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stash's saved filters as Sift's saved searches: whole, or named in the report with the reason."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from sift.slices.stash_migration.reader import SavedFilter
from sift.slices.stash_migration.saved_filters import PICTURES_SUFFIX, translate

pytestmark = pytest.mark.unit

_TAGS = {4: "Beach", 5: "Big party"}
_PEOPLE = {1: "Jane Doe"}
_SITES = {2: "Another Studio"}


def _said(mode: str, criteria: Mapping[str, object], q: str = "", name: str = "Kept"):  # type: ignore[no-untyped-def]
    return translate(
        SavedFilter(mode, name, {"q": q}, criteria),
        tag=_TAGS.get,
        person=_PEOPLE.get,
        site=_SITES.get,
    )


def _items(*ids: int) -> dict[str, object]:
    return {"depth": 0, "items": [{"id": one, "label": "stale"} for one in ids]}


def test_a_scene_filter_becomes_a_search_over_videos_in_the_query_words() -> None:
    said = _said(
        "SCENES",
        {
            "tags": {"modifier": "INCLUDES", "value": _items(4, 5)},
            "performers": {"modifier": "INCLUDES_ALL", "value": _items(1)},
            "rating100": {"modifier": "BETWEEN", "value": {"value": 80, "value2": 100}},
        },
        q="pool",
    )
    assert said.why == ""
    assert said.query == (
        'media:video people:"Jane Doe" rating:8..10 tags:"Beach"|"Big party" pool'
    )


def test_names_are_read_from_the_ids_so_a_stale_label_is_not_what_is_searched() -> None:
    said = _said("SCENES", {"studios": {"modifier": "EXCLUDES", "value": _items(2)}})
    assert said.query == 'media:video -sites:"Another Studio"'


def test_an_image_filter_searches_pictures_and_is_named_apart_from_a_scene_one() -> None:
    said = _said("IMAGES", {}, name="Default")
    assert (said.name, said.query) == (f"Default{PICTURES_SUFFIX}", "media:image")


@pytest.mark.parametrize(
    ("criteria", "why"),
    [
        ({"organized": {"modifier": "EQUALS", "value": "false"}}, "organized"),
        ({"tags": {"modifier": "INCLUDES", "value": {"depth": -1, "items": [4]}}}, "tags"),
        ({"rating100": {"modifier": "NOT_BETWEEN", "value": {"value": 1}}}, "rating100"),
    ],
)
def test_a_filter_with_any_part_sift_cannot_say_is_not_brought_and_says_which(
    criteria: Mapping[str, object], why: str
) -> None:
    said = _said("SCENES", criteria)
    assert said.query == ""
    assert why in said.why


def test_a_filter_over_another_list_is_not_brought() -> None:
    said = _said("PERFORMERS", {"filter_favorites": {"modifier": "EQUALS", "value": "true"}})
    assert said.why == "a filter over performers"


def test_a_strict_bound_moves_by_one_before_it_is_scaled() -> None:
    above = _said("SCENES", {"rating100": {"modifier": "GREATER_THAN", "value": {"value": 60}}})
    fewer = _said("SCENES", {"o_counter": {"modifier": "LESS_THAN", "value": {"value": 3}}})
    assert (above.query, fewer.query) == ("media:video rating:6+", "media:video o_count:2-")


def test_ids_kept_as_a_bare_list_are_read_as_the_chosen_ones() -> None:
    """Filters saved by an older Stash keep a criterion's ids as a plain list rather than under
    `items`."""
    said = _said("SCENES", {"tags": {"modifier": "INCLUDES", "value": [4]}})
    assert said.query == 'media:video tags:"Beach"'


def test_a_criterion_whose_value_holds_no_ids_adds_nothing_to_the_search() -> None:
    """A value that is neither a list nor a set of items names nothing, so the search is the
    filter's other parts alone rather than a refusal over an empty choice."""
    said = _said("SCENES", {"tags": {"modifier": "INCLUDES", "value": "none"}})
    assert (said.query, said.why) == ("media:video", "")


def test_an_id_this_library_never_received_is_searched_by_the_name_stash_kept() -> None:
    """Where an id reads back as nothing here (not brought, or not a number at all), the label
    Stash stored with the filter is the name searched, decoded as Stash encodes it."""
    said = _said(
        "SCENES",
        {
            "tags": {
                "modifier": "INCLUDES",
                "value": {
                    "items": [{"id": 99, "label": "sunset%20glow"}, {"id": "x", "label": "dusk"}]
                },
            }
        },
    )
    assert said.query == 'media:video tags:"sunset glow"|"dusk"'


def test_an_id_with_no_name_here_or_in_stash_keeps_the_filter_out() -> None:
    """A part that names nothing a search can spell would widen the filter, so it is refused."""
    said = _said("SCENES", {"performers": {"modifier": "INCLUDES", "value": [99]}})
    assert (said.query, said.why) == ("", "performers Sift cannot name")


def test_whether_a_file_has_any_at_all_is_said_in_the_query_words() -> None:
    """Stash's "is null" and "not null" ask whether a file carries any of a kind at all."""
    none = _said("SCENES", {"tags": {"modifier": "IS_NULL"}})
    some = _said("SCENES", {"studios": {"modifier": "NOT_NULL"}})
    assert (none.query, some.query) == ("media:video -tags", "media:video sites:any")


def test_ids_excluded_alone_become_one_exclusion_each() -> None:
    """A criterion that only leaves things out still leaves each of them out."""
    said = _said(
        "SCENES",
        {"tags": {"modifier": "INCLUDES", "value": {"items": [], "excluded": [4, 5]}}},
    )
    assert said.query == 'media:video -tags:"Beach" -tags:"Big party"'


def test_a_way_of_choosing_names_that_sift_has_no_word_for_keeps_the_filter_out() -> None:
    said = _said("SCENES", {"tags": {"modifier": "MATCHES_REGEX", "value": [4]}})
    assert (said.query, said.why) == ("", "tags matches_regex")


@pytest.mark.parametrize(
    ("criterion", "query"),
    [
        ({"modifier": "IS_NULL"}, "rating:none"),
        ({"modifier": "NOT_NULL"}, "-rating:none"),
        ({"modifier": "EQUALS", "value": {"value": 80}}, "rating:8"),
        ({"modifier": "NOT_EQUALS", "value": {"value": 80}}, "-rating:8"),
    ],
)
def test_a_rating_is_compared_in_stars(criterion: Mapping[str, object], query: str) -> None:
    """Stash rates out of a hundred and Sift in stars, so every comparison is said in stars,
    and "no rating" is its own word rather than a number."""
    assert _said("SCENES", {"rating100": criterion}).query == f"media:video {query}"


@pytest.mark.parametrize(
    ("criterion", "query"),
    [
        ({"modifier": "IS_NULL"}, "o_count:0"),
        ({"modifier": "NOT_NULL"}, "o_count:1+"),
        ({"modifier": "EQUALS", "value": {"value": 2}}, "o_count:2"),
    ],
)
def test_a_count_with_no_value_is_a_count_of_nought(
    criterion: Mapping[str, object], query: str
) -> None:
    """A count is never missing, only nought, so "is null" asks for nought and "not null" for
    one and up."""
    assert _said("SCENES", {"o_counter": criterion}).query == f"media:video {query}"


@pytest.mark.parametrize("value", [{}, {"value": "lots"}])
def test_a_comparison_with_no_number_keeps_the_filter_out(value: Mapping[str, object]) -> None:
    said = _said("SCENES", {"rating100": {"modifier": "EQUALS", "value": value}})
    assert (said.query, said.why) == ("", "rating100 with no number")


def test_a_filter_with_no_name_is_not_brought() -> None:
    """A saved search is found by its name, so one with none would be a search nobody can open."""
    said = _said("SCENES", {}, name="")
    assert said.why == "a filter with no name"


def test_a_criterion_that_is_not_a_set_of_choices_keeps_the_filter_out() -> None:
    said = _said("SCENES", {"tags": "Beach"})
    assert (said.query, said.why) == ("", "tags")
