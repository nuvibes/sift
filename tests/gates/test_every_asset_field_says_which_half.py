# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every field of a file is drawn in About (what somebody wrote) or Media (what the file measures),
by its declared `Group`: editable is RECORD, a drawn field nobody can fill (not typed, not written
by a stash-box) is MEDIA, so a new machine fact cannot default into the typed half."""

from __future__ import annotations

import pytest

from sift.kernel.records import Group, Shown, Subject, every_field, fields_of

pytestmark = [pytest.mark.gate, pytest.mark.unit]


def test_a_field_somebody_types_into_is_what_they_wrote() -> None:
    """Editable and measured is a contradiction."""
    for one in every_field():
        if one.editable:
            assert one.group is Group.RECORD, (
                f"{one.subject.value}.{one.key} is editable and declared {one.group.value}; "
                "a field somebody types into is part of what they wrote"
            )


def test_a_fact_nobody_can_fill_in_is_the_file_speaking() -> None:
    """A drawn fact nobody can fill in is the file speaking, in the MEDIA half."""
    for one in fields_of(Subject.ASSET):
        if one.shown is Shown.ELSEWHERE or one.editable or one.imported:
            continue
        assert one.group is Group.MEDIA, (
            f"asset.{one.key} can be filled in by nobody, so it is measured off the file; "
            "declare group=Group.MEDIA on it"
        )


def test_the_measured_half_belongs_to_files_alone() -> None:
    """A person's record is what somebody wrote, all of it."""
    for one in every_field():
        if one.subject is Subject.ASSET:
            continue
        assert one.group is Group.RECORD, (
            f"{one.subject.value}.{one.key} is declared {one.group.value}, and only a file has a "
            "measured half, and no other record is drawn in two"
        )


def test_both_halves_are_actually_declared() -> None:
    """Both halves are declared, so neither rule passes by finding nothing."""
    halves = {one.group for one in fields_of(Subject.ASSET)}
    assert halves == {Group.RECORD, Group.MEDIA}
