# SPDX-License-Identifier: AGPL-3.0-or-later
"""The matcher: what it flags, what it refuses to flag, and what it costs to ask.

The number of comparisons is asserted like any other property: an all-pairs matcher passes every
other test here and never finishes on a large library.
"""

from __future__ import annotations

import random

import pytest

from sift.kernel.content.duplicates import Fingerprint
from sift.slices.dedup.matcher import (
    DEFAULT_ACCURACY,
    FRAME_HEX,
    LEVELS,
    NEAR_FRAME_BITS,
    NEAR_VIDEO_FRAMES,
    VIDEO_FRAMES,
    WIDEST,
    Accuracy,
    Matcher,
)

pytestmark = pytest.mark.unit

#: Fixed, so a failure is reproducible and the comparison bound is not noise.
SEED = 20260719


def fresh(seed: int = SEED) -> random.Random:
    return random.Random(seed)


def a_hash(rng: random.Random) -> str:
    """One frame fingerprint: 16 hex characters, as probing always stores them."""
    return f"{rng.getrandbits(64):016x}"


def nudged(fingerprint: str, bits: int, rng: random.Random) -> str:
    """The same fingerprint with exactly `bits` flipped: a re-encode is 0 bits, a downscale 4."""
    value = int(fingerprint, 16)
    for bit in rng.sample(range(63), bits):
        value ^= 1 << bit
    return f"{value:016x}"


def still(asset_id: str, phash: str, *, digest: str | None = None) -> Fingerprint:
    return Fingerprint(
        asset_id=asset_id,
        identity=digest or f"digest-{asset_id}",
        media_type="image",
        phash=phash,
        videohash=None,
    )


def video(asset_id: str, frames: list[str], *, digest: str | None = None) -> Fingerprint:
    """A GIF: thirty frame fingerprints laid end to end."""
    return Fingerprint(
        asset_id=asset_id,
        identity=digest or f"digest-{asset_id}",
        media_type="gif",
        phash=frames[0],
        videohash="".join(frames),
    )


def movie(
    asset_id: str,
    video_phash: str,
    *,
    digest: str | None = None,
    duration_ms: int | None = 210_000,
) -> Fingerprint:
    """A video: one number for the whole video, and how long it runs."""
    return Fingerprint(
        asset_id=asset_id,
        identity=digest or f"digest-{asset_id}",
        media_type="video",
        phash=None,
        videohash=None,
        video_phash=video_phash,
        duration_ms=duration_ms,
    )


def frames_of(rng: random.Random, count: int = VIDEO_FRAMES) -> list[str]:
    return [a_hash(rng) for _ in range(count)]


# --- the threshold, on stills -----------------------------------------------------------------


@pytest.mark.parametrize("bits", [0, 1, 4, 8, NEAR_FRAME_BITS])
def test_a_picture_that_went_through_something_is_flagged(bits: int) -> None:
    """Every distance within the threshold is found, the boundary itself included."""
    rng = fresh()
    base = a_hash(rng)
    pairs = Matcher().find([still("a", base), still("b", nudged(base, bits, rng))])

    assert [(pair.asset_a, pair.asset_b, pair.distance) for pair in pairs] == [("a", "b", bits)]
    assert pairs[0].method == "phash"


def test_two_different_pictures_are_not_flagged() -> None:
    """Two different pictures (about 32 bits apart) are not flagged."""
    rng = fresh()
    stills = [still(f"a{index}", a_hash(rng)) for index in range(200)]

    assert Matcher().find(stills) == []


def test_a_picture_just_past_the_boundary_is_not_flagged() -> None:
    """One bit past the boundary is a different picture."""
    rng = fresh()
    base = a_hash(rng)
    far = nudged(base, NEAR_FRAME_BITS + 1, rng)

    assert Matcher().find([still("a", base), still("b", far)]) == []


# --- the distinction that keeps exact duplicates out of the queue ------------------------------


def test_two_assets_with_the_same_digest_never_become_a_pair() -> None:
    """The same bytes never become a pair: they belong in the reclaim view."""
    rng = fresh()
    base = a_hash(rng)
    same = [still("a", base, digest="identical"), still("b", base, digest="identical")]

    assert Matcher().find(same) == []


def test_two_assets_with_different_digests_and_the_same_fingerprint_are_a_pair() -> None:
    """Different bytes with the same fingerprint are a real near duplicate."""
    rng = fresh()
    base = a_hash(rng)
    pairs = Matcher().find([still("a", base, digest="one"), still("b", base, digest="two")])

    assert len(pairs) == 1
    assert pairs[0].distance == 0


# --- a still is never comparable to a video -----------------------------------------------------


def test_a_photograph_is_never_a_duplicate_of_a_video() -> None:
    """A photograph is never a duplicate of a video: fingerprints of different lengths are not
    comparable, which is not zero."""
    rng = fresh()
    shared = a_hash(rng)
    both = [still("picture", shared), video("movie", [shared, *frames_of(rng, VIDEO_FRAMES - 1)])]

    assert Matcher().find(both) == []


def test_a_fingerprint_of_the_wrong_width_is_skipped() -> None:
    """A frame fingerprint that is not thirty frames is skipped, and two such are no pair."""
    rng = fresh()
    short = video("short", frames_of(rng, 29))
    other = video("other", frames_of(rng, 29))

    assert Matcher().find([short, other]) == []


# --- videos -------------------------------------------------------------------------------------


def test_a_re_encoded_video_is_flagged() -> None:
    """A re-encoded video, every frame moved a little, is flagged."""
    rng = fresh()
    original = frames_of(rng)
    copy = [nudged(frame, 4, rng) for frame in original]

    pairs = Matcher().find([video("a", original), video("b", copy)])

    assert len(pairs) == 1
    assert pairs[0].method == "videohash"
    assert pairs[0].distance == 0


def test_two_different_videos_are_not_flagged() -> None:
    rng = fresh()

    assert Matcher().find([video("a", frames_of(rng)), video("b", frames_of(rng))]) == []


def test_a_video_with_an_added_intro_is_still_the_same_video() -> None:
    """Five frames of intro do not break a match: videos are scored by frames that agree, not by a
    total distance."""
    rng = fresh()
    original = frames_of(rng)
    with_intro = frames_of(rng, 5) + original[5:]

    pairs = Matcher().find([video("a", original), video("b", with_intro)])

    assert len(pairs) == 1
    assert pairs[0].distance == 5


def test_a_video_that_differs_by_more_than_a_fifth_is_not_flagged() -> None:
    """The bar is twenty-four of thirty frames."""
    rng = fresh()
    original = frames_of(rng)
    changed = frames_of(rng, VIDEO_FRAMES - NEAR_VIDEO_FRAMES + 1) + original[7:]

    assert Matcher().find([video("a", original), video("b", changed)]) == []


def test_a_copy_whose_timeline_has_shifted_is_still_found() -> None:
    """A copy whose timeline shifted is found by comparing frames as a set, not by position."""
    rng = fresh()
    original = frames_of(rng)
    shifted = original[5:] + frames_of(rng, 5)

    positional = Matcher().find([video("a", original), video("b", shifted)])

    assert len(positional) == 1, "a shifted copy is the case fixed sampling cannot see"
    assert positional[0].distance == 5


# --- what it costs to ask --------------------------------------------------------------------


def test_candidate_generation_does_not_compare_everything_to_everything() -> None:
    """Candidate generation on ten thousand assets stays far under all-pairs: the bound tolerates
    tuning and cannot pass with the search removed."""
    rng = fresh()
    library = [still(f"a{index:06d}", a_hash(rng)) for index in range(10_000)]

    matcher = Matcher()
    matcher.find(library)

    all_pairs = len(library) * (len(library) - 1) // 2
    assert matcher.comparisons < 1_000_000
    assert matcher.comparisons < all_pairs // 40


def test_the_search_still_finds_what_it_is_supposed_to_in_a_large_library() -> None:
    """The search is still complete under the bound: a planted pair at the threshold's edge is
    found among ten thousand assets."""
    rng = fresh()
    library = [still(f"a{index:06d}", a_hash(rng)) for index in range(10_000)]
    planted = a_hash(rng)
    library.append(still("planted-one", planted))
    library.append(still("planted-two", nudged(planted, NEAR_FRAME_BITS, rng)))

    found = Matcher().find(library)

    assert ("planted-one", "planted-two") in [(pair.asset_a, pair.asset_b) for pair in found]


def test_videos_are_bounded_too_and_not_merely_stills() -> None:
    """Videos are bounded too: one shared frame (a fade, a title card) is not grounds to compare."""
    rng = fresh()
    library = [video(f"v{index:04d}", frames_of(rng)) for index in range(400)]

    matcher = Matcher()
    found = matcher.find(library)

    all_pairs = len(library) * (len(library) - 1) // 2
    assert found == [], "unrelated videos are not duplicates of each other"
    assert matcher.comparisons < all_pairs, (
        "the search costs more than comparing everything to everything, which is the one thing "
        "it must never do"
    )


def test_a_static_video_is_not_a_duplicate_of_everything_it_shares_a_frame_with() -> None:
    """A static video is not a duplicate of everything sharing its one frame."""
    rng = fresh()
    held = a_hash(rng)
    other = frames_of(rng)
    other[7] = held

    assert Matcher().find([video("static", [held] * VIDEO_FRAMES), video("movie", other)]) == []


def test_frames_pair_off_one_to_one() -> None:
    """Frames pair off one to one, so one image cannot answer for thirty frames. Asserted on the
    comparison itself, since the candidate gate would hide it."""
    rng = fresh()
    held = a_hash(rng)
    held_throughout = [held] * VIDEO_FRAMES
    two_thirds = [held if index % 3 else a_hash(rng) for index in range(VIDEO_FRAMES)]

    apart = Matcher()._frames_apart("".join(held_throughout), "".join(two_thirds))

    assert apart == 10, "twenty of thirty frames pair off, so ten do not"
    assert apart is not None and apart > VIDEO_FRAMES - NEAR_VIDEO_FRAMES, (
        "and that is far enough apart to keep the pair out of the queue"
    )


def test_a_video_pair_is_compared_the_same_way_round_either_way() -> None:
    """A video pair compares the same either way round, so scan order cannot change the answer."""
    rng = fresh()
    held = a_hash(rng)
    one = "".join([held] * VIDEO_FRAMES)
    two = "".join(held if index % 3 else a_hash(rng) for index in range(VIDEO_FRAMES))

    matcher = Matcher()

    assert matcher._frames_apart(one, two) == matcher._frames_apart(two, one)


# --- the pair itself ---------------------------------------------------------------------------


def test_a_pair_is_written_in_a_settled_order() -> None:
    """A pair is written in a settled order, matching the table's unique key."""
    rng = fresh()
    base = a_hash(rng)
    near = nudged(base, 2, rng)

    forwards = Matcher().find([still("zzz", base), still("aaa", near)])
    backwards = Matcher().find([still("aaa", near), still("zzz", base)])

    assert (forwards[0].asset_a, forwards[0].asset_b) == ("aaa", "zzz")
    assert (backwards[0].asset_a, backwards[0].asset_b) == ("aaa", "zzz")


# --- videos, judged by one number for the whole video ------------------------------------------


def test_two_encodes_of_one_video_are_flagged() -> None:
    """Two encodes of one video, two bits apart, are flagged."""
    rng = fresh()
    original = a_hash(rng)
    pairs = Matcher().find([movie("a", original), movie("b", nudged(original, 2, rng))])

    assert [(pair.asset_a, pair.asset_b) for pair in pairs] == [("a", "b")]
    assert pairs[0].method == "video_phash"
    assert pairs[0].distance == 2


def test_two_unrelated_videos_are_left_alone() -> None:
    """Two unrelated videos are left alone."""
    rng = fresh()
    assert Matcher().find([movie("a", a_hash(rng)), movie("b", a_hash(rng))]) == []


def test_the_scan_files_out_to_the_most_generous_level_and_no_further() -> None:
    """The scan files out to the most generous level, which is read-time filtering's reach, and no
    further, where unrelated videos collide."""
    rng = fresh()
    original = a_hash(rng)
    edge = WIDEST["video_phash"]

    at_the_edge = Matcher().find([movie("a", original), movie("b", nudged(original, edge, rng))])
    assert len(at_the_edge) == 1, f"the scan should reach {edge} bits"

    just_past = Matcher().find([movie("a", original), movie("b", nudged(original, edge + 1, rng))])
    assert just_past == [], f"the scan should not reach {edge + 1} bits"


@pytest.mark.parametrize(
    ("accuracy", "reach"),
    [(Accuracy.EXACT, 0), (Accuracy.HIGH, 2), (Accuracy.MEDIUM, 4), (Accuracy.LOW, 6)],
)
def test_each_named_level_means_exactly_the_distance_it_says(
    accuracy: Accuracy, reach: int
) -> None:
    """Each named level's video distance is pinned; the names are the interface."""
    assert LEVELS["video_phash"][accuracy] == reach


def test_the_outer_edge_differs_per_kind_and_is_what_the_scan_files_to() -> None:
    """Each kind has its own outer edge, and `WIDEST` is what the scan files to."""
    assert WIDEST == {"phash": NEAR_FRAME_BITS, "video_phash": 6, "videohash": 6}
    assert WIDEST["videohash"] == VIDEO_FRAMES - NEAR_VIDEO_FRAMES
    for kind, table in LEVELS.items():
        assert table[Accuracy.LOW] == WIDEST[kind], (
            f"{kind}'s loosest level is not what it files to"
        )
        assert table[Accuracy.EXACT] == 0, f"{kind} should call bit-for-bit exact"


def test_the_default_is_the_middle_level() -> None:
    """The default is the middle level."""
    assert DEFAULT_ACCURACY is Accuracy.MEDIUM
    assert LEVELS["video_phash"][DEFAULT_ACCURACY] == 4


def test_videos_of_very_different_lengths_are_filed_with_the_gap_recorded() -> None:
    """Videos of very different lengths are filed with the gap recorded; the length rule applies
    when the queue is read, so moving it needs no rescan."""
    rng = fresh()
    original = a_hash(rng)
    close = nudged(original, 2, rng)

    together = Matcher().find(
        [movie("a", original, duration_ms=210_000), movie("b", close, duration_ms=210_000)]
    )
    apart = Matcher().find(
        [movie("a", original, duration_ms=210_000), movie("b", close, duration_ms=600_000)]
    )

    assert [pair.duration_gap_ms for pair in together] == [0]
    assert [pair.duration_gap_ms for pair in apart] == [390_000]


def test_a_few_seconds_of_difference_is_still_the_same_video() -> None:
    """A few seconds of difference is recorded as the small gap it is."""
    rng = fresh()
    original = a_hash(rng)
    pairs = Matcher().find(
        [
            movie("a", original, duration_ms=1_468_000),
            movie("b", nudged(original, 2, rng), duration_ms=1_476_000),
        ]
    )
    assert [pair.duration_gap_ms for pair in pairs] == [8_000]


def test_a_video_of_unknown_length_records_no_gap_rather_than_zero() -> None:
    """An unknown length records no gap rather than zero, which would read as a perfect match."""
    rng = fresh()
    original = a_hash(rng)
    pairs = Matcher().find(
        [movie("a", original, duration_ms=None), movie("b", nudged(original, 2, rng))]
    )
    assert [pair.duration_gap_ms for pair in pairs] == [None]


def test_the_scan_itself_applies_no_length_rule_at_all() -> None:
    """The scan applies no length rule: one second against two and a half hours is still filed."""
    rng = fresh()
    original = a_hash(rng)
    pairs = Matcher().find(
        [
            movie("a", original, duration_ms=1_000),
            movie("b", nudged(original, 2, rng), duration_ms=9_000_000),
        ]
    )
    assert [pair.duration_gap_ms for pair in pairs] == [8_999_000]


def test_a_video_not_yet_fingerprinted_is_skipped_rather_than_compared() -> None:
    """A video still carrying only the thirty-frame value is skipped until the catch-up pass fills
    the whole-video fingerprint."""
    rng = fresh()
    frames = frames_of(rng)

    def before_the_change(asset_id: str) -> Fingerprint:
        return Fingerprint(
            asset_id=asset_id,
            identity=f"digest-{asset_id}",
            media_type="video",
            phash=frames[0],
            videohash="".join(frames),
            video_phash=None,
            duration_ms=210_000,
        )

    assert Matcher().find([before_the_change("old"), before_the_change("twin")]) == []


def test_a_video_whose_whole_video_fingerprint_is_the_wrong_width_is_skipped() -> None:
    """A whole-video fingerprint that is not sixteen hex characters is skipped, and two such are
    no pair."""
    rng = fresh()
    frames = frames_of(rng)
    too_wide = "".join(frames)

    assert len(too_wide) != FRAME_HEX
    assert Matcher().find([movie("one", too_wide), movie("two", too_wide)]) == []


def test_the_three_kinds_are_never_compared_against_each_other() -> None:
    """A photograph, a GIF and a video are never compared with each other."""
    rng = fresh()
    shared = a_hash(rng)
    frames = frames_of(rng)
    frames[0] = shared

    pairs = Matcher().find([still("photo", shared), video("loop", frames), movie("video", shared)])

    assert pairs == []


def test_two_copies_of_the_same_bytes_are_not_a_near_duplicate() -> None:
    """Identical bytes are not a near duplicate."""
    rng = fresh()
    original = a_hash(rng)
    same = [movie("a", original, digest="same"), movie("b", original, digest="same")]
    assert Matcher().find(same) == []
