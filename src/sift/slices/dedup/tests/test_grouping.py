# SPDX-License-Identifier: AGPL-3.0-or-later
"""The clustering and the keeper chain: pure arithmetic over handed records, deciding which file
is about to be deleted. Never chain across methods, and a component past the cap is not a group.
"""

from __future__ import annotations

import pytest

from sift.kernel.content.duplicates import Measure
from sift.slices.dedup.grouping import (
    DEFAULT_MAX_GROUP,
    Group,
    PairRow,
    Rule,
    group_pairs,
    keeper_of,
)


def measure(
    asset_id: str,
    *,
    size: int | None = None,
    width: int | None = None,
    height: int | None = None,
    added: int = 0,
) -> Measure:
    return Measure(asset_id=asset_id, size_bytes=size, width=width, height=height, added_at=added)


def pair(one: str, other: str, *, method: str = "phash", distance: int = 1) -> PairRow:
    return PairRow(
        id=f"{one}-{other}-{method}", asset_a=one, asset_b=other, method=method, distance=distance
    )


def only(groups: list[Group]) -> Group:
    (found,) = groups
    return found


# --- the keeper chain -------------------------------------------------------------------------


def test_a_tie_break_separates_the_leaders_and_never_overrules_them() -> None:
    """A tie-break separates the leaders and never overrules them: two files tie at 1920x1080 and
    the larger file is 720."""
    files = [
        measure("a", size=100, width=1920, height=1080),
        measure("b", size=200, width=1280, height=720),
        measure("c", size=50, width=1920, height=1080),
    ]

    assert keeper_of(files, "higher_res") == "a", "the largest of the two at the top resolution"
    assert keeper_of(files, "larger") == "b"
    assert keeper_of(files, "smaller") == "c"


def test_every_rule_leads_its_own_chain() -> None:
    """Every rule leads its own chain and the others follow."""
    tied_on_pixels = [
        measure("a", size=100, width=1920, height=1080, added=1),
        measure("b", size=200, width=1920, height=1080, added=2),
    ]

    # Resolution cannot separate them, so the chain falls through to size.
    assert keeper_of(tied_on_pixels, "higher_res") == "b"

    tied_on_both = [
        measure("a", size=100, width=1920, height=1080, added=1),
        measure("b", size=100, width=1920, height=1080, added=2),
    ]

    # And then to when the library first saw them.
    assert keeper_of(tied_on_both, "higher_res") == "b"
    assert keeper_of(tied_on_both, "older") == "a"
    assert keeper_of(tied_on_both, "newer") == "b"


def test_nothing_at_all_can_separate_them_and_that_is_an_answer() -> None:
    """None is an answer when nothing separates them, never a coin toss."""
    identical = [
        measure("a", size=100, width=1920, height=1080, added=5),
        measure("b", size=100, width=1920, height=1080, added=5),
    ]

    for rule in ("higher_res", "larger", "smaller", "newer", "older"):
        assert keeper_of(identical, rule) is None, rule


def test_a_figure_nobody_has_stops_that_step_rather_than_deciding_it() -> None:
    """A missing figure skips that step rather than letting a missing column decide."""
    no_dimensions = [
        measure("a", size=100, added=1),
        measure("b", size=200, added=2),
    ]

    assert keeper_of(no_dimensions, "higher_res") == "b", "falls through to size"

    nothing_known = [measure("a"), measure("b")]
    assert keeper_of(nothing_known, "higher_res") is None


def test_one_file_is_not_a_group_and_has_no_keeper() -> None:
    """There is nothing to keep it INSTEAD OF, so answering with it would read as a decision."""
    assert keeper_of([measure("a", size=100)], "larger") is None
    assert keeper_of([], "larger") is None


@pytest.mark.parametrize("rule", ["larger", "smaller", "higher_res", "newer", "older"])
def test_every_rule_keeps_a_file_that_is_actually_in_the_group(rule: Rule) -> None:
    """Every rule keeps a file that is in the group."""
    files = [
        measure("a", size=100, width=640, height=480, added=1),
        measure("b", size=200, width=1920, height=1080, added=2),
        measure("c", size=300, width=1280, height=720, added=3),
    ]

    kept = keeper_of(files, rule)

    assert kept in {"a", "b", "c"}


# --- the clustering ---------------------------------------------------------------------------


def test_three_copies_of_one_clip_are_one_question_and_not_three() -> None:
    """Three copies are one question, not pairs whose answers could contradict."""
    facts = {one: measure(one, size=100, width=1920, height=1080) for one in "abc"}
    facts["b"] = measure("b", size=500, width=1920, height=1080)

    found = only(group_pairs([pair("a", "b"), pair("b", "c")], facts=facts, rule="higher_res"))

    assert found.ids == ("a", "b", "c")
    assert found.keeper == "b"
    assert found.pairs == ("a-b-phash", "b-c-phash"), "both rows, so a dismissal can reach them"


def test_a_still_never_chains_into_a_clips_component() -> None:
    """A still never chains into a clip's component: the methods measure on different scales."""
    facts = {one: measure(one, size=100) for one in "abc"}

    found = group_pairs(
        [pair("a", "b", method="phash"), pair("b", "c", method="video_phash")],
        facts=facts,
        rule="larger",
    )

    assert sorted(one.ids for one in found) == [("a", "b"), ("b", "c")]
    assert {one.method for one in found} == {"phash", "video_phash"}


def test_a_component_past_the_cap_is_handed_back_as_work_for_a_person() -> None:
    """A component past the cap comes back marked, with its size, and nothing in it pre-marked."""
    hub = [pair("hub", f"x{index}") for index in range(DEFAULT_MAX_GROUP)]
    facts = {"hub": measure("hub", size=999)} | {
        f"x{index}": measure(f"x{index}", size=index) for index in range(DEFAULT_MAX_GROUP)
    }

    found = only(group_pairs(hub, facts=facts, rule="larger"))

    assert len(found.ids) == DEFAULT_MAX_GROUP + 1
    assert found.too_big is True
    assert found.keeper is None, "a rule may not vouch for a chain"
    assert found.needs_a_person is True


def test_a_component_exactly_at_the_cap_is_still_a_group() -> None:
    """The boundary, because an off-by-one here silently takes the ordinary case out of the
    automatic path or lets a chain into it."""
    edge = [pair("hub", f"x{index}") for index in range(DEFAULT_MAX_GROUP - 1)]
    facts = {"hub": measure("hub", size=999)} | {
        f"x{index}": measure(f"x{index}", size=index) for index in range(DEFAULT_MAX_GROUP - 1)
    }

    found = only(group_pairs(edge, facts=facts, rule="larger"))

    assert len(found.ids) == DEFAULT_MAX_GROUP
    assert found.too_big is False
    assert found.keeper == "hub"


def test_a_group_carries_the_closest_pair_in_it_however_the_rows_arrived() -> None:
    """A group carries its closest pair whatever order the pairs arrived in."""
    facts = {one: measure(one, size=100) for one in "abcd"}
    rows = [
        pair("c", "d", distance=9),
        pair("a", "b", distance=2),
        pair("b", "c", distance=7),
    ]

    found = only(group_pairs(rows, facts=facts, rule="larger"))

    assert found.ids == ("a", "b", "c", "d")
    assert found.distance == 2


def test_the_closest_groups_come_first_and_the_order_is_total() -> None:
    """Closest groups first, then by smallest id, so the order is total."""
    facts = {one: measure(one, size=100) for one in "abcdef"}
    rows = [
        pair("e", "f", distance=5),
        pair("c", "d", distance=1),
        pair("a", "b", distance=1),
    ]

    found = group_pairs(rows, facts=facts, rule="larger")

    assert [one.ids for one in found] == [("a", "b"), ("c", "d"), ("e", "f")]


def test_a_chain_goes_after_the_groups_it_is_as_close_as() -> None:
    """A chain goes after the groups it is as close as, since nothing can be done with it."""
    facts = {one: measure(one, size=ord(one)) for one in "abcdefghijklmnop"}
    chain = [pair("a", one, distance=0) for one in "bcdefghijk"]
    ordinary = [pair("l", "m", distance=0), pair("n", "o", distance=1)]

    found = group_pairs(chain + ordinary, facts=facts, rule="larger")

    assert [(one.ids[0], one.too_big) for one in found] == [("l", False), ("a", True), ("n", False)]


def test_a_file_nobody_has_a_measure_for_stops_the_rule_for_the_WHOLE_group() -> None:
    """A file with no measure stops the rule for the whole group, so no keeper is chosen."""
    found = only(
        group_pairs(
            [pair("a", "b"), pair("b", "c")],
            facts={
                "a": measure("a", size=100, width=1920, height=1080),
                "b": measure("b", size=500, width=1920, height=1080),
            },
            rule="higher_res",
        )
    )

    assert found.ids == ("a", "b", "c")
    assert found.keeper is None, "the two it could measure must not decide the fate of the third"
    assert found.needs_a_person is True

    # And with a pair, where the same mutation is masked by there being only one file to weigh.
    two = only(
        group_pairs(
            [pair("a", "b")],
            facts={"a": measure("a", size=100, width=1920, height=1080)},
            rule="higher_res",
        )
    )
    assert two.ids == ("a", "b")
    assert two.keeper is None


def test_nothing_pending_is_no_groups_rather_than_an_error() -> None:
    """The state this whole screen is trying to reach."""
    assert group_pairs([], facts={}, rule="higher_res") == []
