# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which rung fires, and the one number the whole feature's judgement rests on."""

from __future__ import annotations

import pytest

from sift.kernel.attribution import FolderFaces
from sift.slices.suggestions.ladder import (
    DOMINANT_FLOOR,
    DOMINANT_LEAD,
    DOMINANT_SHARE,
    DOMINANT_SHARE_WITH_A_LEAD,
    Action,
    Evidence,
    decide,
    dominant,
)
from sift.slices.suggestions.naming import Reading


class TestDominance:
    def test_at_the_threshold_it_is_dominance(self) -> None:
        # Six of ten is exactly three fifths.
        assert dominant({"pile-a": 6, "pile-b": 4}, with_faces=10) == "pile-a"

    def test_below_the_threshold_it_is_not(self) -> None:
        assert dominant({"pile-a": 5, "pile-b": 5}, with_faces=10) is None
        assert dominant({"pile-a": 5, "pile-b": 4}, with_faces=10) is None

    def test_a_tiny_group_is_never_dominance(self) -> None:
        # Two files agreeing out of two is every file that has a face, and still proves nothing.
        assert dominant({"pile-a": 2}, with_faces=2) is None
        assert DOMINANT_FLOOR == 3

    def test_a_pile_of_two_across_forty_seven_files_is_nothing(self) -> None:
        assert dominant({"pile-a": 2}, with_faces=47) is None

    def test_a_tie_for_the_top_names_nobody(self) -> None:
        assert dominant({"pile-a": 6, "pile-b": 6}, with_faces=12) is None

    def test_nothing_at_all(self) -> None:
        assert dominant({}, with_faces=0) is None
        assert dominant({"pile-a": 9}, with_faces=0) is None

    def test_just_under_half_is_never_dominance_whatever_the_lead(self) -> None:
        # 49 of 100 files with a face, and no other group at all.
        assert dominant({"pile-a": 49}, with_faces=100) is None
        assert dominant({"pile-a": 49, "pile-b": 1}, with_faces=100) is None

    def test_half_with_ten_times_the_runner_up_is_dominance(self) -> None:
        assert dominant({"pile-a": 50, "pile-b": 5}, with_faces=100) == "pile-a"
        # No runner-up at all is the lead by definition.
        assert dominant({"pile-a": 50}, with_faces=100) == "pile-a"
        assert pytest.approx(0.5) == DOMINANT_SHARE_WITH_A_LEAD
        assert DOMINANT_LEAD == 10

    def test_half_with_nine_times_the_runner_up_is_not(self) -> None:
        # 45 of 90 is exactly half, and 45 is nine times 5.
        assert dominant({"pile-a": 45, "pile-b": 5}, with_faces=90) is None

    def test_three_fifths_alone_needs_no_lead(self) -> None:
        # Six of ten against four: one and a half times the runner-up, and still dominance.
        assert dominant({"pile-a": 6, "pile-b": 4}, with_faces=10) == "pile-a"

    def test_the_lead_road_keeps_the_floor(self) -> None:
        # Two of four with nothing else: half and an unbeaten lead, and still two photographs.
        assert dominant({"pile-a": 2}, with_faces=4) is None

    def test_the_share_is_over_the_files_that_have_a_face(self) -> None:
        # Forty-seven files, six of which are landscapes: forty-one have a face and all agree.
        assert dominant({"pile-a": 41}, with_faces=41) == "pile-a"
        assert pytest.approx(0.6) == DOMINANT_SHARE


class TestTheRungs:
    def test_a_named_group_attributes_with_nothing_asked(self) -> None:
        verdict = decide(
            Reading(name="nadia vance"),
            FolderFaces(looked_at=10, with_faces=10, named={"person-1": 8}),
            files=10,
            named_by=[],
            looking=True,
            rejected=False,
        )
        assert verdict.action is Action.ATTRIBUTE
        assert verdict.evidence is Evidence.NAMED_GROUP
        assert verdict.person_id == "person-1"

    def test_a_folder_holding_somebody_elses_own_folder_is_not_given_away_by_its_faces(
        self,
    ) -> None:
        """Rung 1 refuses, and the folder's own name still has its say: here it names nobody."""
        faces = FolderFaces(looked_at=10, with_faces=10, named={"person-1": 8})
        refused = decide(
            Reading(),
            faces,
            files=10,
            named_by=[],
            looking=True,
            rejected=False,
            owned_inside={"person-1", "person-2"},
        )
        hers = decide(
            Reading(),
            faces,
            files=10,
            named_by=[],
            looking=True,
            rejected=False,
            owned_inside={"person-1"},
        )
        assert refused.action is Action.NOTHING
        assert hers.action is Action.ATTRIBUTE
        assert hers.person_id == "person-1"

    def test_a_folder_read_by_its_name_alone_is_not_given_to_the_faces_in_it(self) -> None:
        verdict = decide(
            Reading(name="harlowquin"),
            FolderFaces(looked_at=10, with_faces=10, named={"person-1": 8}),
            files=10,
            named_by=["person-9"],
            looking=True,
            rejected=False,
            by_name_only=True,
        )
        assert verdict.evidence is Evidence.KNOWN_NAME
        assert verdict.person_id == "person-9"

    def test_a_name_already_in_the_library_attributes_with_nothing_asked(self) -> None:
        verdict = decide(
            Reading(name="nadia vance"),
            FolderFaces(looked_at=10, with_faces=0),
            files=10,
            named_by=["person-9"],
            looking=True,
            rejected=False,
        )
        assert verdict.action is Action.ATTRIBUTE
        assert verdict.evidence is Evidence.KNOWN_NAME
        assert verdict.person_id == "person-9"

    def test_two_matches_link_nobody(self) -> None:
        verdict = decide(
            Reading(name="jane"),
            FolderFaces(looked_at=10, with_faces=10, piles={"pile-a": 9}),
            files=10,
            named_by=["person-1", "person-2"],
            looking=True,
            rejected=False,
        )
        assert verdict.action is Action.NOTHING

    def test_a_name_and_one_group_is_offered(self) -> None:
        verdict = decide(
            Reading(name="nadia vance"),
            FolderFaces(looked_at=10, with_faces=10, piles={"pile-a": 9}),
            files=10,
            named_by=[],
            looking=True,
            rejected=False,
        )
        assert verdict.action is Action.OFFER
        assert verdict.evidence is Evidence.FACE_GROUP
        assert verdict.group_id == "pile-a"

    def test_a_name_with_no_faces_at_all_is_offered_and_marked(self) -> None:
        verdict = decide(
            Reading(name="nadia vance"),
            FolderFaces(looked_at=10, with_faces=0),
            files=10,
            named_by=[],
            looking=True,
            rejected=False,
        )
        assert verdict.action is Action.OFFER
        assert verdict.evidence is Evidence.NAME_ONLY

    def test_a_folder_nothing_has_looked_at_yet_waits(self) -> None:
        verdict = decide(
            Reading(name="nadia vance"),
            FolderFaces(looked_at=3, with_faces=0),
            files=10,
            named_by=[],
            looking=True,
            rejected=False,
        )
        assert verdict.action is Action.WAIT

    def test_with_recognition_off_there_is_nothing_to_wait_for(self) -> None:
        verdict = decide(
            Reading(name="nadia vance"),
            FolderFaces(),
            files=10,
            named_by=[],
            looking=False,
            rejected=False,
        )
        assert verdict.action is Action.OFFER
        assert verdict.evidence is Evidence.NAME_ONLY

    def test_many_different_faces_is_never_one_person(self) -> None:
        verdict = decide(
            Reading(name="sandbar runways"),
            FolderFaces(looked_at=10, with_faces=10, piles={"a": 3, "b": 3, "c": 2, "d": 2}),
            files=10,
            named_by=[],
            looking=True,
            rejected=False,
        )
        assert verdict.action is Action.NOTHING

    def test_a_rejected_name_never_comes_back(self) -> None:
        verdict = decide(
            Reading(name="sandbar runways"),
            FolderFaces(looked_at=10, with_faces=10, piles={"pile-a": 9}),
            files=10,
            named_by=[],
            looking=True,
            rejected=True,
        )
        assert verdict.action is Action.NOTHING

    def test_a_folder_with_no_name_claims_nothing(self) -> None:
        verdict = decide(
            Reading(name=""),
            FolderFaces(looked_at=10, with_faces=10, piles={"pile-a": 9}),
            files=10,
            named_by=[],
            looking=True,
            rejected=False,
        )
        assert verdict.action is Action.NOTHING
