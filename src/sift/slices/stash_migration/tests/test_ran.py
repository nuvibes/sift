# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the screen is told about the run over the last read."""

from __future__ import annotations

from sift.kernel.jobs import JobState, TaskRun
from sift.slices.stash_migration.said import note_of_landing as _said_landed
from sift.slices.stash_migration.said import note_of_run as _said
from sift.slices.stash_migration.service import ran_after
from sift.slices.stash_migration.tally import Tally


def _run(ended: int | None, note: str | None) -> TaskRun:
    return TaskRun(
        id="01RUN",
        started_at=None if ended is None else ended - 10,
        finished_at=ended,
        state=JobState.DONE,
        note=note,
        runs_total=1,
    )


def test_a_run_after_the_read_is_said_in_its_own_sentence() -> None:
    ran = ran_after(_run(2_000, "Imported 12,000 of 15,000 files."), read_at=1_000)
    assert ran == {"ended_at": 2_000, "said": "Imported 12,000 of 15,000 files."}


def test_a_run_from_before_the_read_or_none_at_all_says_nothing() -> None:
    assert ran_after(_run(500, "Imported 1 of 1 files."), read_at=1_000) is None
    assert ran_after(None, read_at=0) is None
    assert ran_after(_run(None, None), read_at=0) is None


def test_a_run_that_said_nothing_still_says_when() -> None:
    assert ran_after(_run(2_000, None), read_at=0) == {"ended_at": 2_000, "said": ""}


def test_the_sentence_names_only_what_came_and_says_what_waits_for_its_files() -> None:
    """Nothing lands without its file: the markers that stayed behind do not ask for another run;
    they wait with their files, and so do the People,
    Sites and Tags only those files carry, and the list is on the screen below the sentence."""
    tally = Tally(
        scenes=10,
        scenes_matched=8,
        saved_searches=3,
        waiting_scenes=2,
        marks_waiting=34,
        people_waiting=3,
    )
    said = _said(tally)
    assert "0 Loops" not in said and "Run this again" not in said
    assert said.endswith(
        " Also 3 saved searches. 2 files this library doesn't have yet wait, with 34 markers and"
        " 3 People on them. Each comes across on its own once its file is in Sift. The list is"
        " below."
    )
    tally = Tally(marks=2, saved_searches=1)
    assert _said(tally).endswith(" Also 2 Loops and 1 saved search.")


def test_the_sentences_say_imported_the_importing_pane_s_word() -> None:
    """Import, Importing and Imported are the screen's words for files arriving (the vocabulary's
    banned words say which phrase they replace)."""
    tally = Tally(scenes=3, scenes_matched=2, images=4, images_matched=1, people=5, sites=2, tags=2)
    assert _said(tally).startswith(
        "Imported 2 of 3 files and 1 of 4 pictures, with 5 People, 2 Sites and 2 Tags."
    )
    assert (
        _said_landed(3, Tally(marks=1))
        == "Imported what Stash kept for 3 files that arrived, with 1 Loop."
    )


def test_one_of_a_kind_is_said_as_one() -> None:
    tally = Tally(waiting_images=1, tags_waiting=1, sites_without_files=1)
    said = _said(tally)
    assert " Of those, 1 Site has no files in Stash either." in said
    assert " 1 picture this library doesn't have yet waits, with 1 Tag on it." in said
    one_each = Tally(images=12, images_matched=12, people=1, sites=1, tags=1, pictures=1)
    assert _said(one_each).startswith(
        "Imported 0 of 0 files and 12 of 12 pictures, with 1 Person, 1 Site and 1 Tag."
        " Also 1 cover."
    )


def test_a_landing_with_no_loops_says_no_loops() -> None:
    """The landing's sentence names Loops only when some came with the files, so a pass that
    brought none never says it brought zero."""
    assert _said_landed(1, Tally()) == "Imported what Stash kept for 1 file that arrived."
