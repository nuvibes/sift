# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a photograph's orientation note, against real ffprobe output, and asserting each value
as the filters it produces, mirror before turn."""

from __future__ import annotations

from typing import Any

import pytest

from sift.kernel.config import Settings
from sift.slices.media_edit import orientation
from sift.slices.media_edit.orientation import UPRIGHT, Orientation, understand


def _probe_said(**frame: Any) -> dict[str, Any]:
    """What ffprobe hands back for one frame, in its own shape."""
    return {"frames": [frame]}


def test_a_picture_with_no_note_is_upright() -> None:
    assert understand(_probe_said(width=100, height=50)) == UPRIGHT


def test_nothing_at_all_is_upright() -> None:
    """A file probing could not describe is treated the way the rest of Sift treats every file."""
    assert understand({}) == UPRIGHT
    assert understand({"frames": []}) == UPRIGHT
    assert understand({"frames": ["not a frame"]}) == UPRIGHT


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        (1, UPRIGHT),
        (2, Orientation(mirrored=True)),
        (3, Orientation(quarter_turns=2)),
        (4, Orientation(quarter_turns=2, mirrored=True)),
        (5, Orientation(quarter_turns=1, mirrored=True)),
        (6, Orientation(quarter_turns=1)),
        (7, Orientation(quarter_turns=3, mirrored=True)),
        (8, Orientation(quarter_turns=3)),
    ],
)
def test_every_value_the_note_can_take_is_understood(tag: int, expected: Orientation) -> None:
    """All eight, including the four that are mirrored: a front camera produces those."""
    assert understand(_probe_said(tags={"Orientation": f"    {tag}"})) == expected


def test_a_note_outside_the_eight_values_is_upright() -> None:
    """Better than guessing. An unknown value is a file this cannot say anything true about."""
    assert understand(_probe_said(tags={"Orientation": "    9"})) == UPRIGHT
    assert understand(_probe_said(tags={"Orientation": ""})) == UPRIGHT
    assert understand(_probe_said(tags={"Orientation": None})) == UPRIGHT


def test_an_angle_is_read_when_there_is_no_tag() -> None:
    """The fallback, for a container that states a turn without carrying the tag itself."""
    said = _probe_said(side_data_list=[{"side_data_type": "3x3 displaymatrix", "rotation": -90}])
    assert understand(said) == Orientation(quarter_turns=1)


def test_the_tag_wins_over_the_angle() -> None:
    """The angle cannot express a mirror, so a file carrying both is read from the one that can."""
    said = _probe_said(
        tags={"Orientation": "    7"},
        side_data_list=[{"rotation": 90}],
    )
    assert understand(said) == Orientation(quarter_turns=3, mirrored=True)


def test_side_data_that_says_nothing_about_a_turn_is_upright() -> None:
    assert understand(_probe_said(side_data_list=[{"side_data_type": "something else"}])) == UPRIGHT
    assert understand(_probe_said(side_data_list=["not a description"])) == UPRIGHT


@pytest.mark.parametrize(
    ("turned", "chain"),
    [
        (UPRIGHT, ()),
        (Orientation(quarter_turns=1), ("transpose=1",)),
        (Orientation(quarter_turns=2), ("transpose=2,transpose=2",)),
        (Orientation(quarter_turns=3), ("transpose=2",)),
        (Orientation(mirrored=True), ("hflip",)),
        (Orientation(quarter_turns=1, mirrored=True), ("hflip", "transpose=1")),
    ],
)
def test_the_filters_put_the_picture_the_way_it_is_seen(
    turned: Orientation, chain: tuple[str, ...]
) -> None:
    """The mirror comes first. Put after the turn it is right for half the values and wrong for
    the other half, which looks like a picture that is nearly correct."""
    assert turned.filters() == chain


def test_the_question_is_asked_of_one_frame_and_nothing_more(
    settings: Settings, tmp_path: Any
) -> None:
    """Reading past the first frame is reading a whole file to answer what the first one answers."""
    argv = orientation.orientation_args(tmp_path / "photo.jpg", settings=settings)
    assert argv[argv.index("-read_intervals") + 1] == "%+#1"
    assert "-show_frames" in argv
    assert argv[argv.index("-select_streams") + 1] == "v:0"


@pytest.mark.anyio
async def test_a_file_the_tool_cannot_read_is_treated_as_upright(
    settings: Settings, tmp_path: Any
) -> None:
    """Which is what every other part of Sift already assumes about every file.

    Run against the real tool rather than a stand-in, because what this is guarding is the tool
    failing, and a stand-in raising the exception on cue proves the `except` and not the reach of
    it. Refusing instead would make the editor worse on the photographs that were never affected.
    """
    junk = tmp_path / "not-a-picture.jpg"
    junk.write_bytes(b"this is not a picture at all")

    # Two different failures, and the tool tells them apart. A file with nothing readable in it
    # answers with no frames and a success; a file that is not there at all fails outright. Both
    # have to end in the same place, because the editor must stay usable either way.
    assert await orientation.read_orientation(junk, settings=settings) == UPRIGHT
    assert await orientation.read_orientation(tmp_path / "gone.jpg", settings=settings) == UPRIGHT
