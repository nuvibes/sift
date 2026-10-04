# SPDX-License-Identifier: AGPL-3.0-or-later
"""A press on part of a task is checked against what the task declares, and a dry run's report
says what the run would do in plain sentences."""

from __future__ import annotations

import pytest

from sift.slices.tasks.parts import (
    EVERYTHING,
    NAME_CHARS,
    NOTE_CHARS,
    DryReport,
    NotAPart,
    Plan,
    PlanLine,
    Selection,
    TaskPart,
    TaskParts,
    narrowed,
)

pytestmark = pytest.mark.unit

_GENERATE = TaskParts(
    subtasks=(TaskPart("thumbnails", "Thumbnails"), TaskPart("previews", "Hover previews"))
)
_SCAN = TaskParts(locations=True)


def test_a_press_naming_nothing_is_the_whole_task() -> None:
    assert narrowed(_GENERATE, None, None, []) == EVERYTHING
    assert EVERYTHING.whole


def test_the_parts_a_task_declares_are_the_only_ones_a_press_may_name() -> None:
    assert narrowed(_GENERATE, ["previews", "previews"], None, []) == Selection(parts=("previews",))
    with pytest.raises(NotAPart, match="no part called faces"):
        narrowed(_GENERATE, ["faces"], None, [])
    with pytest.raises(NotAPart, match="at least one part"):
        narrowed(_GENERATE, [], None, [])
    with pytest.raises(NotAPart, match="can't be run in parts"):
        narrowed(_SCAN, ["thumbnails"], None, [])


def test_folders_are_checked_against_the_library_as_it_is_now() -> None:
    assert narrowed(_SCAN, None, ["root-a"], ["root-a", "root-b"]) == Selection(
        locations=("root-a",)
    )
    with pytest.raises(NotAPart, match="no longer in your library"):
        narrowed(_SCAN, None, ["root-gone"], ["root-a"])
    with pytest.raises(NotAPart, match="at least one folder"):
        narrowed(_SCAN, None, [], ["root-a"])
    with pytest.raises(NotAPart, match="some folders only"):
        narrowed(_GENERATE, None, ["root-a"], ["root-a"])


def test_a_report_counts_each_part_names_the_first_files_and_says_nothing_changed() -> None:
    plan = Plan(
        files=1204,
        lines=(PlanLine("Thumbnails", 1200), PlanLine("Hover previews", 4)),
        names=("beach.mp4", "dunes.jpg"),
    )
    assert plan.report("Generate") == (
        "Generate would work on 1,204 files. By part: Thumbnails 1,200, Hover previews 4. "
        "First files: beach.mp4, dunes.jpg, and 1,202 more. Nothing was changed."
    )


def test_a_report_is_fields_the_row_lays_out_and_comes_back_from_its_note() -> None:
    plan = Plan(
        files=3,
        lines=(PlanLine("New backup", 1), PlanLine("Older backups deleted", 2)),
        names=("one.zip", "two.zip"),
        named="Older backups it would delete",
        nameable=2,
        doing="would write three.zip and delete 2 older backups",
    )
    report = plan.reported("Automatic backup")
    assert report.said == (
        "Automatic backup would write three.zip and delete 2 older backups. "
        "Older backups it would delete: one.zip, two.zip. Nothing was changed."
    )
    assert report.more == 0 and report.named == "Older backups it would delete"
    assert DryReport.of_note(report.note()) == report
    assert DryReport.of_note(report.said) is None
    assert DryReport.of_note("{not json") is None


def test_a_report_always_fits_its_note_and_counts_what_it_stopped_naming() -> None:
    long = "a" * 400 + ".mp4"
    plan = Plan(files=500, lines=(PlanLine("Thumbnails", 500),), names=(long,) * 10)
    report = plan.reported("Generate")
    assert len(report.note()) <= NOTE_CHARS
    assert all(len(one) <= NAME_CHARS and one.endswith(".mp4") for one in report.names)
    assert report.more == 500 - len(report.names)


def test_a_report_with_nothing_to_do_says_so_and_why_a_part_cannot_run() -> None:
    plan = Plan(files=0, refusals=("Recognition needs its models.",))
    assert plan.report("Identify") == (
        "Identify would do nothing on this device now. Recognition needs its models. "
        "Nothing was changed."
    )
    assert Plan(files=0).report("Generate") == (
        "Generate has nothing to do. Every file already has what it would make. "
        "Nothing was changed."
    )


def test_a_note_that_is_json_but_no_report_is_read_as_a_plain_sentence() -> None:
    assert DryReport.of_note('{"said": 3}') is None
    assert DryReport.of_note('{"parts": []}') is None


def test_a_plan_with_files_and_no_names_says_no_first_files() -> None:
    plan = Plan(files=2, lines=(PlanLine("Thumbnails", 2),))
    assert plan.report("Generate") == "Generate would work on 2 files. Nothing was changed."
