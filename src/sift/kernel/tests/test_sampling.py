# SPDX-License-Identifier: AGPL-3.0-or-later
"""The frame-sampling ladder, pinned.

These timestamps decide which frames become a preview, a sprite and a stored perceptual hash, and a
silently changed rate would stop every older hash matching. The golden file catches any drift; the
boundaries are also written out by hand, computed from the ladder as specified, because a golden
file generated from the code only proves the code still does what it did.
"""

from __future__ import annotations

import ast
import json
from itertools import pairwise
from pathlib import Path

import pytest

from sift.kernel.sampling import (
    DEFAULT_PREVIEW_SHAPE,
    LADDER,
    MAX_FACE_FRAMES,
    MAX_FRAMES,
    MAX_SPRITE_FRAMES,
    PREVIEW_SHAPES,
    Piece,
    PreviewShape,
    face_frames,
    face_moments,
    frame_rate,
    hash_frames,
    picture_span,
    preview_segments,
    preview_shape,
    sample_frames,
    sprite_frames,
)

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).parent / "fixtures" / "sampler_ladder.json"


# --- the ladder, by hand


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        # Each rung at its boundary and one millisecond past: a rung includes its limit.
        (1, 1.0),
        (20_000, 1.0),
        (20_001, 1.0 / 3.0),
        (60_000, 1.0 / 3.0),
        (60_001, 1.0 / 10.0),
        (300_000, 1.0 / 10.0),
        # Past the last rung the rate is a budget: thirty frames, however long it is.
        (600_000, 30 / 600),
        (7_200_000, 30 / 7200),
    ],
)
def test_the_rate_is_the_ladder(duration_ms: int, expected: float) -> None:
    assert frame_rate(duration_ms) == pytest.approx(expected)


def test_an_unknown_duration_samples_the_start() -> None:
    """A file the probe could not measure still gets frame zero, not a failed thumbnail job."""
    assert sample_frames(0) == (0,)
    assert sample_frames(-1) == (0,)
    assert frame_rate(0) == LADDER[0][1]


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        # 5s at 1fps: none at 5s, where the video has ended.
        (5_000, (0, 1_000, 2_000, 3_000, 4_000)),
        # 10s is still on the first rung.
        (10_000, tuple(range(0, 10_000, 1_000))),
        # 30s: second rung, a frame every three seconds.
        (30_000, (0, 3_000, 6_000, 9_000, 12_000, 15_000, 18_000, 21_000, 24_000, 27_000)),
        # 600s: past the last rung. Thirty frames, one every twenty seconds.
        (600_000, tuple(range(0, 600_000, 20_000))),
    ],
)
def test_the_timestamps_are_the_ladder(duration_ms: int, expected: tuple[int, ...]) -> None:
    assert sample_frames(duration_ms) == expected


def test_the_first_frame_is_always_sampled() -> None:
    """Frame zero is what a preview opens on, at any duration."""
    for duration_ms in (1, 999, 20_000, 60_000, 300_000, 7_200_000):
        assert sample_frames(duration_ms)[0] == 0


def test_nothing_is_sampled_past_the_end() -> None:
    for duration_ms in (1, 5_000, 20_001, 300_000, 301_000, 7_200_000):
        assert max(sample_frames(duration_ms)) < duration_ms


def test_no_duration_ever_costs_more_than_the_budget() -> None:
    """No rung exceeds MAX_FRAMES even at its own boundary, its worst case, which is why
    `sample_frames` needs no cap."""
    worst_cases = [int(limit * 1000) for limit, _ in LADDER]
    worst_cases += [300_001, 600_000, 3_600_000, 7_200_000, 86_400_000]
    for duration_ms in worst_cases:
        assert len(sample_frames(duration_ms)) <= MAX_FRAMES, duration_ms


def test_a_long_video_gets_exactly_the_budget() -> None:
    for duration_ms in (300_001, 600_000, 3_600_000, 7_200_000, 86_400_000):
        assert len(sample_frames(duration_ms)) == MAX_FRAMES, duration_ms


def test_the_samples_are_evenly_spread() -> None:
    """Samples are evenly spread, so two copies of one video agree on the moments picked."""
    timestamps = sample_frames(3_600_000)
    gaps = {second - first for first, second in pairwise(timestamps)}
    assert len(gaps) == 1


# --- fingerprint frames


def test_a_fingerprint_always_samples_the_same_number_of_frames() -> None:
    """A fingerprint samples the same number of frames at every length, or two cannot be compared:
    the difference between `hash_frames` and the ladder."""
    for duration_ms in (1, 1_000, 20_000, 60_000, 300_000, 3_600_000, 86_400_000):
        assert len(hash_frames(duration_ms)) == MAX_FRAMES, duration_ms


def test_a_fingerprint_of_an_unmeasurable_file_is_still_something() -> None:
    assert hash_frames(0) == (0,)
    assert hash_frames(-1) == (0,)


def test_a_fingerprint_samples_the_whole_file() -> None:
    """Evenly spread, start to end."""
    frames = hash_frames(600_000)
    assert frames[0] == 0
    assert max(frames) < 600_000
    gaps = {second - first for first, second in pairwise(frames)}
    assert len(gaps) == 1


def test_a_long_file_fingerprints_on_the_same_frames_the_ladder_picks() -> None:
    """Past the last rung the two agree: that rung is already MAX_FRAMES across the duration. They
    diverge only on short files, where the ladder thins and a fingerprint must not."""
    assert hash_frames(3_600_000) == sample_frames(3_600_000)
    assert hash_frames(5_000) != sample_frames(5_000)


# --- one ladder, and only one


#: The names that make up the ladder, defined in the sampler and imported everywhere else.
LADDER_NAMES = frozenset(
    {
        "MAX_FRAMES",
        "LADDER",
        "frame_rate",
        "sample_frames",
        "hash_frames",
        # The sprite's own rate and the face pass's density, guarded the same way.
        "MAX_SPRITE_FRAMES",
        "SPRITE_LADDER",
        "SPRITE_LONG_RATE",
        "sprite_frames",
        "MAX_FACE_FRAMES",
        "face_frames",
    }
)


def test_nothing_else_in_the_app_defines_a_sampling_ladder() -> None:
    """Nothing else in the app defines a sampling ladder.

    The frames picked outlive the run (a stored fingerprint, a preview and a sprite on disk), so a
    second copy would match nothing and fail nothing. Read by parsing, so a name in a comment or a
    string is not a second implementation.
    """
    source_root = Path(__file__).parents[2]
    sampler_module = source_root / "kernel" / "sampling.py"

    offenders: list[str] = []
    for module in source_root.rglob("*.py"):
        if module == sampler_module or "/tests/" in module.as_posix():
            continue
        tree = ast.parse(module.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in LADDER_NAMES
            ):
                offenders.append(f"{module.relative_to(source_root)} defines {node.name}()")
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in LADDER_NAMES:
                        offenders.append(f"{module.relative_to(source_root)} defines {target.id}")

    assert not offenders, (
        "the frame-sampling ladder has a second implementation: "
        + "; ".join(offenders)
        + ". Import it from the sampler instead."
    )


def test_the_check_for_a_second_ladder_can_fail(tmp_path: Path) -> None:
    """A planted second ladder is caught."""
    planted = tmp_path / "copy.py"
    planted.write_text("def sample_frames(duration_ms):\n    return (0,)\n")

    found = [
        node.name
        for node in ast.walk(ast.parse(planted.read_text()))
        if isinstance(node, ast.FunctionDef) and node.name in LADDER_NAMES
    ]
    assert found == ["sample_frames"]


# --- the golden file


def test_the_ladder_matches_the_golden_file() -> None:
    """Every case in one go, against a checked-in file."""
    golden = json.loads(GOLDEN.read_text())

    for case in golden["videos"]:
        duration_ms = case["duration_ms"]
        assert frame_rate(duration_ms) == pytest.approx(case["rate"]), duration_ms
        assert list(sample_frames(duration_ms)) == case["timestamps_ms"], duration_ms

    for case in golden["fingerprints"]:
        assert list(hash_frames(case["duration_ms"])) == case["timestamps_ms"], case

    for case in golden["sprites"]:
        assert list(sprite_frames(case["duration_ms"])) == case["timestamps_ms"], case

    assert golden["max_frames"] == MAX_FRAMES
    assert golden["max_sprite_frames"] == MAX_SPRITE_FRAMES


# --- the scrub strip's own rate


@pytest.mark.parametrize(
    ("seconds", "every"),
    [
        (7, 0.5),  # a short clip: hunting a moment, so fine detail
        (20, 0.5),
        (60, 1.0),
        (120, 1.0),
        (600, 3.0),
        (3600, 10.0),  # an hour-long video: looking for a scene
    ],
)
def test_the_scrub_strip_gets_denser_the_shorter_the_clip(seconds: int, every: float) -> None:
    """The scrub strip gets denser the shorter the clip: one frame every ten seconds gives a short
    clip a single tile, and every half-second gives a long video an unopenable sheet."""
    frames = sprite_frames(seconds * 1000)
    assert seconds / len(frames) == pytest.approx(every)


def test_a_very_long_video_gets_a_coarser_strip_rather_than_an_unopenable_sheet() -> None:
    """Past the cap a long video's strip gets coarser: a sheet is one JPEG, and browsers refuse
    to decode one past a few thousand pixels."""
    three_hours = sprite_frames(3 * 3600 * 1000)

    assert len(three_hours) == MAX_SPRITE_FRAMES
    assert (3 * 3600) / len(three_hours) <= 30


def test_the_scrub_strip_is_far_denser_than_the_fingerprint_ladder() -> None:
    """The strip is far denser than the fingerprint ladder's thirty frames, one every four minutes
    across two hours."""
    two_hours = 2 * 3600 * 1000
    assert len(sprite_frames(two_hours)) > len(hash_frames(two_hours)) * 10


def test_the_scrub_strip_starts_at_the_beginning() -> None:
    assert sprite_frames(60_000)[0] == 0


def test_every_scrub_timestamp_is_inside_the_video() -> None:
    """A timestamp past the end seeks to nothing, and the stored grid would disagree with the
    sheet."""
    duration = 97_531
    assert all(0 <= at < duration for at in sprite_frames(duration))


def test_a_clip_with_no_known_duration_still_gets_one_frame() -> None:
    assert sprite_frames(0) == (0,)
    assert sprite_frames(-1) == (0,)


def test_the_fingerprint_ladder_is_untouched_by_the_sprites_arrival() -> None:
    """`MAX_FRAMES` and the ladder are unchanged: every stored fingerprint was computed from
    them."""
    assert MAX_FRAMES == 30
    assert LADDER == ((20.0, 1.0), (60.0, 1.0 / 3.0), (300.0, 1.0 / 10.0))


# --- the face ladder
#
# A fingerprint wants the same count at every length; a face pass wants a count that never falls as
# a file grows, or somebody who appears once in a long film is not found.


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (1, 1),
        (5, 5),
        (20, 20),  # the end of once-a-second
        (160, 25),  # halfway up the climb
        (300, 30),  # the budget, reached
        (3600, 30),  # and held for everything longer
    ],
)
def test_the_face_ladder_climbs_and_then_holds(seconds: int, expected: int) -> None:
    assert face_moments(seconds * 1000) == expected


def test_a_file_with_no_known_length_still_gets_a_look() -> None:
    """A file with no readable duration still gets a look."""
    assert face_moments(0) == 1
    assert face_moments(-1) == 1
    assert face_frames(0) == (0,)


def test_the_face_count_never_falls_as_a_file_grows() -> None:
    """The face count never falls as a file grows: a longer file never gets fewer looks."""
    counts = [face_moments(seconds * 1000) for seconds in range(1, 601)]
    assert counts == sorted(counts)


def test_density_scales_the_count_rather_than_the_rate() -> None:
    """Density scales the COUNT the ladder has already worked out, not a rate."""
    ordinary = len(face_frames(120_000))

    assert len(face_frames(120_000, density=0.5)) == max(1, round(ordinary * 0.5))
    assert len(face_frames(120_000, density=2)) == min(MAX_FACE_FRAMES, round(ordinary * 2))


def test_no_density_can_ask_for_more_than_the_ceiling() -> None:
    """The ceiling stops a big number in a setting becoming an afternoon of decoding."""
    assert len(face_frames(3_600_000, density=100)) == MAX_FACE_FRAMES


def test_a_density_of_nothing_is_refused_rather_than_looking_at_no_frames() -> None:
    """Zero would be a face pass that costs a job, finds nobody and reports done."""
    for nothing in (0, -1.0):
        with pytest.raises(ValueError, match="density of zero"):
            face_frames(60_000, density=nothing)


def test_the_face_moments_are_inside_the_file_and_in_order() -> None:
    for duration in (1_000, 45_000, 600_000):
        moments = face_frames(duration)
        assert moments[0] == 0
        assert list(moments) == sorted(moments)
        assert max(moments) < duration


# --- the hover preview's moments


def test_a_preview_opens_on_the_first_frame_whatever_shape_it_is() -> None:
    """A preview's first piece starts at zero: the clip replaces the tile's still in place, and
    starting anywhere else makes the tile jump under the pointer."""
    for shape in PREVIEW_SHAPES:
        for duration in (1_000, 8_000, 30_000, 600_000, 10_800_000):
            assert preview_segments(duration, shape)[0].start_ms == 0


def test_a_short_file_is_one_piece_from_the_beginning_rather_than_a_montage() -> None:
    """Below twice the budget a file is one piece from the start: a montage would skip almost
    nothing, pay five cuts, and make a larger file."""
    shape = preview_shape("full")

    for duration in (4_000, 12_000, 23_999):
        pieces = preview_segments(duration, shape)
        assert len(pieces) == 1
        assert pieces[0].start_ms == 0


def test_a_file_shorter_than_the_preview_gives_what_it_has_and_no_more() -> None:
    """Twelve seconds of a six-second file would ask ffmpeg to invent six."""
    pieces = preview_segments(6_000, preview_shape("full"))

    assert pieces == (Piece(0, 6_000),)


def test_a_length_the_probe_could_not_work_out_is_not_an_error() -> None:
    """A file whose duration is unknown still gets a preview of what is there."""
    for unknown in (0, -1):
        assert preview_segments(unknown, preview_shape("full")) == (Piece(0, 12_000),)


def test_a_long_file_is_sampled_evenly_across_the_whole_of_it() -> None:
    """Evenly spaced across the whole file."""
    pieces = preview_segments(600_000, preview_shape("full"))

    assert [piece.start_ms for piece in pieces] == [
        0,
        75_000,
        150_000,
        225_000,
        300_000,
        375_000,
        450_000,
        525_000,
    ]
    assert {piece.length_ms for piece in pieces} == {1_500}


def test_every_shape_offered_is_a_whole_number_of_cuts() -> None:
    """Every shape is a whole number of cuts: `preview_segments` rounds, so eight seconds cut every
    1.5 would quietly become 7.5 under a label promising eight."""
    for shape in PREVIEW_SHAPES:
        pieces = preview_segments(10 * round(shape.total_seconds * 1000), shape)

        assert sum(piece.length_ms for piece in pieces) == round(shape.total_seconds * 1000), shape


def test_a_shape_that_cannot_be_cut_evenly_is_refused_when_it_is_built() -> None:
    """A shape that cannot be cut evenly is refused at import, the moment it is written."""
    with pytest.raises(ValueError, match="cannot be cut evenly"):
        PreviewShape("odd", "8 seconds", 8.0, 1.5)

    for nonsense in ((0.0, 1.5), (6.0, 0.0), (-6.0, 1.5)):
        with pytest.raises(ValueError):
            PreviewShape("nonsense", "whatever", *nonsense)


def test_a_shape_that_divides_on_dust_is_still_accepted() -> None:
    """Whole milliseconds are counted, because 0.3 / 0.1 is 2.9999999999999996."""
    assert PreviewShape("fine", "0.3 seconds", 0.3, 0.1).total_seconds == 0.3


def test_the_choice_changes_how_long_it_runs_and_not_how_fast_it_cuts() -> None:
    """Every shape cuts at the same pace, so the choice is only how long to look; a third shape at
    another pace would have to be decided on purpose."""
    assert len({shape.segment_seconds for shape in PREVIEW_SHAPES}) == 1

    for shape in PREVIEW_SHAPES:
        pieces = preview_segments(600_000, shape)
        assert len(pieces) == round(shape.total_seconds / shape.segment_seconds)


def test_the_pieces_add_up_to_the_length_that_was_asked_for() -> None:
    """A clip is prefetched by the hundred, so its length is a fixed budget."""
    for shape in PREVIEW_SHAPES:
        pieces = preview_segments(3_600_000, shape)
        assert sum(piece.length_ms for piece in pieces) == round(shape.total_seconds * 1000)


def test_no_piece_of_a_montage_runs_past_the_end_of_the_file() -> None:
    """No montage piece runs past the end: past the floor the gap between starts is at least twice
    a piece, so no clamp is needed."""
    for shape in PREVIEW_SHAPES:
        for duration in (20_000, 60_001, 3_600_000, 10_800_000):
            for piece in preview_segments(duration, shape):
                assert piece.start_ms + piece.length_ms <= duration


def test_the_moments_are_in_order_and_never_overlap() -> None:
    """Two pieces covering the same seconds would show one moment twice."""
    for shape in PREVIEW_SHAPES:
        pieces = preview_segments(300_000, shape)
        for before, after in pairwise(pieces):
            assert before.start_ms + before.length_ms <= after.start_ms


def test_a_shape_nobody_offers_answers_with_the_default() -> None:
    """An unknown shape (a hand-edited database, an older version's) answers with the default,
    rather than every video losing its preview."""
    assert preview_shape("no-such-shape").key == DEFAULT_PREVIEW_SHAPE
    assert preview_shape("").key == DEFAULT_PREVIEW_SHAPE


def test_every_offered_shape_can_be_looked_up_by_its_own_name() -> None:
    """The list the settings screen draws and the lookup the job runs are the same list."""
    for shape in PREVIEW_SHAPES:
        assert preview_shape(shape.key) is shape


def test_the_default_shape_is_one_of_the_shapes_offered() -> None:
    """Otherwise a fresh install falls through to whatever is first."""
    assert DEFAULT_PREVIEW_SHAPE in {shape.key for shape in PREVIEW_SHAPES}


def test_a_file_whose_sound_outlasts_its_picture_is_sampled_across_the_picture() -> None:
    """The shorter of picture and file where the picture's length is known, never the picture's
    alone (a stream may state more than the file plays); the file's where it is not."""
    assert picture_span(5_025, 2_300) == 2_300
    assert picture_span(10_000, 12_000) == 10_000
    assert picture_span(10_000, 0) == 10_000
    assert picture_span(10_000, None) == 10_000
    assert picture_span(None, 4_000) == 4_000
    assert picture_span(None, None) == 0
