# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding who a face belongs to, and grouping the ones nobody has named; the grouping must
split too much rather than too little."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from sift.slices.faces import clustering, matching, recognize, tracking, tuning
from sift.slices.faces.models import Attribution, Match, Reference, Vector
from sift.slices.faces.tests.conftest import DIMENSION, person_vector, unit

pytestmark = pytest.mark.unit


def reference(person_id: str, index: int, variant: int = 0) -> Reference:
    return Reference(
        id=f"{person_id}-{variant}",
        person_id=person_id,
        vector=person_vector(index, variant),
        quality=0.9,
        crop_digest=f"{person_id}-{variant}",
    )


def blend(first: float, second: float) -> tuple[float, ...]:
    """A face that resembles the person at index 0 by `first` and the one at index 1 by `second`."""
    values = [0.0] * DIMENSION
    values[0] = first
    values[1] = second
    length = sum(value * value for value in values) ** 0.5
    return tuple(value / length for value in values)


def gallery_of(*people: tuple[str, int, int]) -> matching.Gallery:
    by_person: dict[str, list[Reference]] = {}
    for person_id, index, count in people:
        by_person[person_id] = [reference(person_id, index, variant) for variant in range(count)]
    return matching.build_gallery(by_person)


# --- matching --------------------------------------------------------------------------------------


def test_a_face_matches_the_person_it_looks_like() -> None:
    gallery = gallery_of(("ada", 0, 3), ("grace", 4, 3))

    match = matching.best_match(person_vector(0, 1), gallery)

    assert match is not None
    assert match.person_id == "ada"
    assert match.confidence > tuning.AUTO_APPLY_CONFIDENCE


def test_a_face_that_looks_like_nobody_matches_nobody() -> None:
    gallery = gallery_of(("ada", 0, 3))

    assert matching.best_match(person_vector(4), gallery) is None


def test_matching_against_an_empty_gallery_is_not_an_error() -> None:
    assert matching.best_match(person_vector(0), gallery_of()) is None


def test_a_face_with_no_description_matches_nobody() -> None:
    assert matching.best_match((), gallery_of(("ada", 0, 2))) is None


def test_a_person_this_face_was_rejected_for_is_removed_before_the_best_is_chosen() -> None:
    """Removed before choosing, or a rejected person hides the second best."""
    gallery = gallery_of(("ada", 0, 3), ("grace", 1, 3))
    face = blend(0.8, 0.6)

    assert matching.best_match(face, gallery).person_id == "ada"  # type: ignore[union-attr]

    instead = matching.best_match(face, gallery, rejected=["ada"])

    assert instead is not None
    assert instead.person_id == "grace"


def test_rejecting_everybody_leaves_nobody_to_match() -> None:
    gallery = gallery_of(("ada", 0, 2))

    assert matching.best_match(person_vector(0), gallery, rejected=["ada"]) is None


def test_a_confident_match_is_attached_and_a_doubtful_one_is_only_offered() -> None:
    assert matching.verdict(Match("ada", 0.95)) is Attribution.MATCHED
    assert matching.verdict(Match("ada", 0.5)) is Attribution.SUGGESTED
    assert matching.verdict(None) is None


def test_the_bar_drops_only_once_sift_knows_somebody_well() -> None:
    """One number for everybody is the wrong shape: a comparison against twenty pictures of
    somebody is evidence a comparison against two is not."""
    assert tuning.bar_for(0) == tuning.AUTO_APPLY_CONFIDENCE
    assert tuning.bar_for(tuning.STRONG_REFERENCES - 1) == tuning.AUTO_APPLY_CONFIDENCE
    assert tuning.bar_for(tuning.STRONG_REFERENCES) == tuning.STRONG_APPLY_CONFIDENCE
    assert tuning.bar_for(200) == tuning.STRONG_APPLY_CONFIDENCE


def test_what_a_strong_gallery_earns_is_a_relaxation_of_whatever_bar_is_in_force() -> None:
    """The setting is moved, not replaced, so an admin's raised bar is kept."""
    assert tuning.bar_for(0, bar=0.8) == 0.8
    assert tuning.bar_for(20, bar=0.8) == pytest.approx(0.75)
    # Never below the suggest line, or suggestions would become attachments.
    assert tuning.bar_for(20, bar=tuning.SUGGEST_CONFIDENCE) == tuning.SUGGEST_CONFIDENCE


def test_a_bar_at_the_top_of_the_scale_is_a_choice_and_not_a_number() -> None:
    """At 100 somebody is always asked, however strong the gallery."""
    assert tuning.bar_for(500, bar=tuning.ALWAYS_ASK) == tuning.ALWAYS_ASK


def test_a_person_is_described_by_one_blend_of_their_references_by_default() -> None:
    """Measured as the better answer at ordinary gallery sizes: keeping several descriptions is
    the obvious idea and it makes matching very slightly worse."""
    described = matching.describe([reference("ada", 0, variant) for variant in range(5)])

    assert len(described) == 1
    assert len(described[0]) == DIMENSION


def test_a_person_with_no_references_is_described_by_nothing() -> None:
    assert matching.describe([]) == []


def test_keeping_several_descriptions_per_person_is_available_and_off() -> None:
    """Built and defaulted off, as only the arithmetic at ordinary sizes disagrees."""
    references = [reference("ada", 0, variant) for variant in range(3)]
    references += [reference("ada", 4, variant) for variant in range(3)]

    assert tuning.MATCH_GROUPS == 1
    assert len(matching.describe(references, groups=1)) == 1
    assert len(matching.describe(references, groups=4)) == 2


def test_several_descriptions_per_person_find_both_of_two_distinct_looks() -> None:
    """What the machinery is for, on the case it was reasoned about: somebody whose references
    genuinely fall into two groups."""
    references = [reference("ada", 0, variant) for variant in range(3)]
    references += [reference("ada", 4, variant) for variant in range(3)]
    by_person = {"ada": references}

    blended = matching.build_gallery(by_person, groups=1)
    grouped = matching.build_gallery(by_person, groups=4)

    second_look = person_vector(4, 1)
    assert matching.best_match(second_look, blended) is not None
    assert matching.best_match(second_look, grouped) is not None
    assert (
        matching.best_match(second_look, grouped).confidence  # type: ignore[union-attr]
        > matching.best_match(second_look, blended).confidence  # type: ignore[union-attr]
    )


# --- clustering ------------------------------------------------------------------------------------


def test_faces_of_one_person_pile_up_together() -> None:
    vectors = [person_vector(0, variant) for variant in range(4)]

    piles = clustering.pile_up(vectors)

    assert len(piles) == 1
    assert sorted(piles[0]) == [0, 1, 2, 3]


def test_two_similar_people_never_land_in_one_pile_even_at_the_cost_of_extra_piles() -> None:
    """The tuning decision as a property: more piles than people, never fewer."""
    first = [person_vector(0, variant) for variant in range(3)]
    second = [person_vector(1, variant) for variant in range(3)]

    piles = clustering.pile_up(first + second)

    for pile in piles:
        sources = {index < 3 for index in pile}
        assert len(sources) == 1, "two different people ended up in one pile"
    assert len(piles) >= 2


def test_a_new_face_joins_the_pile_it_belongs_to_rather_than_starting_another() -> None:
    """What keeps grouping cheap once a library has been through it once."""
    centroids = [person_vector(0), person_vector(4)]

    assert clustering.nearest_pile(person_vector(0, 1), centroids) == 0
    assert clustering.nearest_pile(person_vector(4, 1), centroids) == 1
    assert clustering.nearest_pile(person_vector(2), centroids) is None
    assert clustering.nearest_pile(person_vector(0), []) is None


def test_a_batch_of_new_faces_is_placed_as_each_would_be_on_its_own() -> None:
    centroids = [person_vector(0), person_vector(4)]
    faces = [person_vector(0, 1), person_vector(4, 1), person_vector(2), person_vector(4, 2)]

    placed = clustering.nearest_piles(faces, centroids)

    assert placed == [clustering.nearest_pile(face, centroids) for face in faces]
    assert placed == [0, 1, None, 1]
    assert clustering.nearest_piles(faces, []) == [None] * 4
    assert clustering.nearest_piles([], centroids) == []


def test_a_rebuilt_pile_keeps_the_identity_of_the_old_pile_it_is_nearest() -> None:
    """A full rebuild keeps identities: middles paired best first, each side once."""
    previous = [person_vector(0), person_vector(1), person_vector(2)]
    current = [person_vector(2, 1), person_vector(6), person_vector(0, 1)]

    carried = clustering.carry_identities(previous, current)

    assert carried == {0: 2, 2: 0}
    assert clustering.carry_identities([], current) == {}
    assert clustering.carry_identities(previous, []) == {}


def test_two_rebuilt_piles_near_one_old_pile_do_not_both_take_its_identity() -> None:
    """Each side is used once; the pairing moves on to the old pile still free."""
    previous = [person_vector(0), person_vector(1)]
    # The free old pile's match comes LAST in likeness, after the taken pile's second-best.
    current = [person_vector(0, 1), person_vector(0, 2), person_vector(1, 3)]

    carried = clustering.carry_identities(previous, current)

    assert carried == {0: 0, 2: 1}


def test_grouping_nothing_produces_nothing() -> None:
    assert clustering.pile_up([]) == []


def test_a_face_that_joined_nothing_is_still_shown() -> None:
    """Piles of one are shown, or the screen of what could not be placed hides most of it."""
    assert clustering.worth_showing(1) is True
    assert clustering.worth_showing(tuning.MIN_PILE_SIZE) is True
    assert clustering.worth_showing(0) is False, "nothing at all is still nothing"


def test_grouping_can_be_capped_so_a_persons_references_yield_a_handful_of_groups() -> None:
    vectors = [person_vector(index) for index in range(6)]

    assert len(clustering.agglomerate(vectors, join_above=0.99, most=2)) == 2


def compare_everything_with_everything(
    vectors: Sequence[Vector], *, join_above: float, most: int | None = None
) -> list[list[int]]:
    """Grouping written the obvious, cubic way, which the real one has to agree with."""
    if not vectors:
        return []
    matrix = np.asarray(vectors, dtype=np.float32)
    likeness = matrix @ matrix.T
    groups: list[list[int]] = [[index] for index in range(len(vectors))]

    while len(groups) > 1:
        best = -2.0
        pair: tuple[int, int] | None = None
        for first in range(len(groups)):
            for second in range(first + 1, len(groups)):
                score = float(likeness[np.ix_(groups[first], groups[second])].mean())
                if score > best:
                    best, pair = score, (first, second)
        assert pair is not None
        if best < join_above and (most is None or len(groups) <= most):
            break
        first, second = pair
        groups[first] = groups[first] + groups[second]
        groups.pop(second)
    return groups


def scattered_faces(seed: int, count: int) -> list[Vector]:
    """Descriptions in every direction, so islands are large and near-ties actually happen."""
    generator = np.random.default_rng(seed)
    return [unit(row.tolist()) for row in generator.normal(size=(count, DIMENSION))]


@pytest.mark.parametrize("seed", range(12))
def test_the_fast_grouping_returns_exactly_what_comparing_everything_returns(seed: int) -> None:
    """The fast grouping gives the obvious grouping's answer, group for group, in order."""
    vectors = scattered_faces(seed, 70)

    assert clustering.agglomerate(vectors, join_above=0.5) == compare_everything_with_everything(
        vectors, join_above=0.5
    )


@pytest.mark.parametrize("floor", [0.0, 0.25, 0.5, 0.75, 0.95])
def test_the_two_agree_at_every_threshold_including_the_ones_that_join_everything(
    floor: float,
) -> None:
    """At a low bar the whole library is one island, the case most likely to go wrong."""
    vectors = scattered_faces(99, 60)

    assert clustering.agglomerate(vectors, join_above=floor) == compare_everything_with_everything(
        vectors, join_above=floor
    )


@pytest.mark.parametrize("most", [1, 2, 3, 5])
def test_the_two_agree_when_the_number_of_groups_is_capped(most: int) -> None:
    """The capped route can join across islands, so it does not use them; it still agrees."""
    vectors = scattered_faces(7, 40)

    assert clustering.agglomerate(
        vectors, join_above=0.5, most=most
    ) == compare_everything_with_everything(vectors, join_above=0.5, most=most)


def test_the_pair_search_finds_every_close_pair_and_nothing_else() -> None:
    """The swap point, checked against what a close pair is: none missed, none invented."""
    vectors = scattered_faces(3, 45)
    matrix = np.asarray(vectors, dtype=np.float32)
    likeness = matrix @ matrix.T
    expected = {
        (first, second)
        for first in range(len(vectors))
        for second in range(first + 1, len(vectors))
        if float(likeness[first, second]) >= 0.4
    }

    assert set(clustering.pairs_above(vectors, floor=0.4)) == expected


def test_the_pair_search_spans_more_than_one_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tiny block exercises the blocking; the answer must not change."""
    vectors = scattered_faces(5, 50)
    expected = set(clustering.pairs_above(vectors, floor=0.3))

    monkeypatch.setattr(clustering, "_BLOCK_VALUES", 20)

    assert set(clustering.pairs_above(vectors, floor=0.3)) == expected


def test_a_face_linked_to_nothing_is_its_own_island_and_survives_the_grouping() -> None:
    """Most of a library's faces join nothing, so this is the ordinary path."""
    apart = [person_vector(index) for index in range(5)]

    piles = clustering.pile_up(apart)

    assert [sorted(pile) for pile in piles] == [[0], [1], [2], [3], [4]]


# --- the middle of a set -----------------------------------------------------------------------------


def test_the_middle_of_several_descriptions_is_itself_a_description() -> None:
    middle = tracking.centroid([person_vector(0), person_vector(0, 1)])

    assert len(middle) == DIMENSION
    assert recognize.similarity(middle, middle) == pytest.approx(1.0, abs=1e-6)


def test_there_is_no_middle_of_nothing() -> None:
    with pytest.raises(ValueError, match="no middle"):
        tracking.centroid([])


def test_descriptions_that_cancel_out_do_not_produce_a_division_by_nothing() -> None:
    opposite = tuple(-value for value in person_vector(0))

    assert tracking.centroid([person_vector(0), opposite]) == (0.0,) * DIMENSION


def test_asking_for_close_pairs_among_nothing_returns_nothing() -> None:
    assert list(clustering.pairs_above([], floor=0.5)) == []


def test_one_description_on_its_own_is_one_group_under_a_cap_too() -> None:
    """The capped route takes the other path through the merging, so its own one-item case is
    reachable and is checked rather than assumed."""
    assert clustering.agglomerate([person_vector(0)], join_above=0.5, most=2) == [[0]]


def test_a_large_island_is_worth_a_line_in_the_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """A large island is logged; the logger is recorded, as the stream fails under xdist."""
    said: list[tuple[str, int]] = []
    monkeypatch.setattr(clustering, "_LARGE_ISLAND", 2)
    monkeypatch.setattr(
        clustering.log, "info", lambda event, **fields: said.append((event, fields["faces"]))
    )
    together = [person_vector(0, variant) for variant in range(4)]

    piles = clustering.pile_up(together)

    assert len(piles) == 1
    assert said == [("faces.cluster.large_island", 4)]


def test_how_closely_a_face_resembles_one_person_is_the_best_of_their_rows() -> None:
    gallery = gallery_of(("alice", 0, 2), ("bob", 1, 1))
    rows = matching.rows_by_person(gallery)

    score = matching.likeness(person_vector(0, 0), gallery, rows["alice"])

    assert score is not None
    assert score == pytest.approx(
        max(float(gallery.vectors[row] @ person_vector(0, 0)) for row in rows["alice"])
    )
    assert score > (matching.likeness(person_vector(0, 0), gallery, rows["bob"]) or 0.0)


def test_nothing_to_compare_with_is_no_likeness_rather_than_a_score_of_nothing() -> None:
    """A person with no rows, or a face with no description: None, never 0.0: a question is
    re-scored whatever the number, so a made-up number would be acted on."""
    gallery = gallery_of(("alice", 0, 1))
    rows = matching.rows_by_person(gallery)

    assert matching.likeness(person_vector(0, 0), gallery, None) is None
    assert matching.likeness(person_vector(0, 0), gallery, np.asarray([], dtype=np.intp)) is None
    assert matching.likeness((), gallery, rows["alice"]) is None
