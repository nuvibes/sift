# SPDX-License-Identifier: AGPL-3.0-or-later
"""The compatibility matrix, pinned to a golden file, and the realtime projection.

A golden file makes a change to what every browser gets visible in a diff. The projection is
checked against measured media rather than against its own constants.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from sift.kernel.content import Asset
from sift.slices.player import policy, tuning
from sift.slices.player.tests.conftest import asset

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).parent / "fixtures" / "compatibility_matrix.json"


def capabilities(video: str, audio: str, containers: str) -> policy.ClientCapabilities:
    return policy.ClientCapabilities(
        video_codecs=frozenset(video.split()),
        audio_codecs=frozenset(audio.split()),
        containers=frozenset(containers.split()),
    )


# --- the golden matrix ------------------------------------------------------------------------


def test_the_compatibility_matrix_matches_the_golden_file() -> None:
    """Every (container, codec, audio, browser) combination and its route. Regenerate the fixture
    deliberately when the new answer is intended."""
    golden = json.loads(GOLDEN.read_text())

    for case in golden["cases"]:
        subject = asset(
            container=case["container"],
            vcodec=case["vcodec"],
            acodec=case["acodec"],
            width=case.get("width", 1920),
            height=case.get("height", 1080),
            fps=case.get("fps", 30.0),
        )
        client = capabilities(case["client_video"], case["client_audio"], case["client_containers"])
        plan = policy.decide(subject, client, max_height=1080, cpu_count=4)

        assert plan.route.value == case["route"], case["why"]


def test_the_golden_file_covers_every_route_that_is_reachable() -> None:
    """The golden file reaches every route. `REMUX` is absent: the tier is held closed until
    segments can follow the source's keyframes, and its rows sit under `transcode`."""
    golden = json.loads(GOLDEN.read_text())
    routes = {case["route"] for case in golden["cases"]}
    assert routes == {policy.Route.DIRECT.value, policy.Route.TRANSCODE.value}


# --- the three tiers, stated directly ------------------------------------------------------------


def test_a_browser_that_can_play_the_file_gets_it_untouched() -> None:
    """The majority path. No ffmpeg, no re-encode, full resolution."""
    plan = policy.decide(asset(), capabilities("h264", "aac", "mp4"), max_height=1080, cpu_count=4)
    assert plan.route is policy.Route.DIRECT
    assert plan.scale_height is None


def test_a_container_problem_alone_currently_transcodes_rather_than_repackaging() -> None:
    """Tier 2 is held closed: a stream copy cannot be cut where the playlist says, since input
    seeking lands on the preceding keyframe. Re-enabling it must be deliberate."""
    plan = policy.decide(
        asset(container="mkv", vcodec="hevc"),
        capabilities("h264 hevc", "aac", "mp4"),
        max_height=1080,
        cpu_count=4,
    )
    assert plan.route is policy.Route.TRANSCODE


def test_a_codec_the_browser_cannot_decode_is_transcoded() -> None:
    plan = policy.decide(
        asset(vcodec="hevc"), capabilities("h264", "aac", "mp4"), max_height=1080, cpu_count=4
    )
    assert plan.route is policy.Route.TRANSCODE


def test_an_unsupported_audio_codec_alone_forces_a_transcode() -> None:
    """Sound counts. A video the browser can decode with audio it cannot is not playable."""
    plan = policy.decide(
        asset(acodec="opus"), capabilities("h264", "aac", "mp4"), max_height=1080, cpu_count=4
    )
    assert plan.route is policy.Route.TRANSCODE


def test_a_container_the_browser_refuses_is_named_rather_than_a_codec_it_plays() -> None:
    """A refused Matroska box is named as the reason, not a codec the browser lists."""
    plan = policy.decide(
        asset(container="mkv", vcodec="vp9", acodec="opus"),
        capabilities("h264 vp9", "aac opus", "mp4 webm"),
        max_height=1080,
        cpu_count=4,
    )

    assert plan.route is policy.Route.TRANSCODE
    assert plan.reason.startswith("Your browser can't play files in this container (Matroska)")
    assert "repackaged copy" in plan.reason
    assert "VP9" not in plan.reason


def test_a_codec_the_browser_lacks_is_named_even_in_a_container_it_refuses() -> None:
    """The picture needs converting whatever box it is in, so the codec is the reason."""
    plan = policy.decide(
        asset(container="mkv", vcodec="vp9", acodec="opus"),
        capabilities("h264", "aac opus", "mp4"),
        max_height=1080,
        cpu_count=4,
    )

    assert plan.reason.startswith("Your browser cannot play VP9")
    assert "container" not in plan.reason


def test_sound_the_browser_lacks_is_named_rather_than_the_picture() -> None:
    plan = policy.decide(
        asset(acodec="ac3"), capabilities("h264", "aac", "mp4"), max_height=1080, cpu_count=4
    )

    assert plan.reason.startswith("Your browser cannot play this file's sound (AC3)")
    assert "H264" not in plan.reason


def test_a_silent_file_is_not_treated_as_having_unsupported_audio() -> None:
    """A NULL `acodec` is a silent clip, not an unsupported codec."""
    plan = policy.decide(
        asset(acodec=None), capabilities("h264", "", "mp4"), max_height=1080, cpu_count=4
    )
    assert plan.route is policy.Route.DIRECT


def test_a_still_image_is_served_as_itself() -> None:
    plan = policy.decide(
        asset(media_type="image", vcodec="", acodec=None),
        policy.ClientCapabilities.nothing(),
        max_height=1080,
        cpu_count=4,
    )
    assert plan.route is policy.Route.DIRECT


def test_a_client_that_reported_nothing_gets_a_transcode() -> None:
    """The safe direction: a black screen is worse than an unnecessary conversion."""
    plan = policy.decide(asset(), policy.ClientCapabilities.nothing(), max_height=1080, cpu_count=4)
    assert plan.route is policy.Route.TRANSCODE


# --- AV1, which is the whole point of asking the client -----------------------------------


def test_av1_is_direct_played_by_a_browser_that_supports_it() -> None:
    """AV1, the costliest codec to transcode, is direct-played wherever the browser supports it."""
    plan = policy.decide(
        asset(vcodec="av1"),
        capabilities("h264 av1", "aac", "mp4"),
        max_height=1080,
        cpu_count=4,
    )
    assert plan.route is policy.Route.DIRECT
    assert plan.projected_realtime is None, "nothing was projected, because nothing is converted"


def test_4k_that_the_browser_can_play_is_never_downscaled() -> None:
    """A 4K file the browser can decode is direct-played at full size, whatever the ceiling."""
    plan = policy.decide(
        asset(vcodec="av1", width=3840, height=2160, fps=60.0),
        capabilities("av1", "aac", "mp4"),
        max_height=720,
        cpu_count=2,
    )
    assert plan.route is policy.Route.DIRECT
    assert plan.scale_height is None


# --- the realtime gate ---------------------------------------------------------------------------


def test_native_resolution_is_kept_when_the_machine_can_sustain_it() -> None:
    """Native resolution is kept when the machine can sustain it."""
    plan = policy.decide(
        asset(vcodec="hevc"), capabilities("h264", "aac", "mp4"), max_height=1080, cpu_count=4
    )
    assert plan.route is policy.Route.TRANSCODE
    assert plan.scale_height is None
    assert plan.streamable is True


def test_4k_is_kept_at_full_size_when_the_machine_is_fast_enough() -> None:
    """4K HEVC at 30 fps on 8 cores projects above realtime, so the ceiling is left unused."""
    plan = policy.decide(
        asset(vcodec="hevc", width=3840, height=2160, fps=30.0),
        capabilities("h264", "aac", "mp4"),
        max_height=1080,
        cpu_count=8,
    )
    assert plan.route is policy.Route.TRANSCODE
    assert plan.scale_height is None, "fast enough at full size, so the ceiling must not apply"
    assert plan.streamable is True


def test_a_file_too_heavy_at_full_size_is_downscaled_rather_than_refused() -> None:
    """4K HEVC at 60 fps on 8 cores is too slow at native (1.1x) and fine at 1080p (2.1x), so it
    plays reduced and says why; the frame rate puts it over the line."""
    plan = policy.decide(
        asset(vcodec="hevc", width=3840, height=2160, fps=60.0),
        capabilities("h264", "aac", "mp4"),
        max_height=1080,
        cpu_count=8,
    )
    assert plan.route is policy.Route.TRANSCODE
    assert plan.scale_height == 1080
    assert plan.streamable is True
    assert "1080p" in plan.reason


def test_the_ceiling_is_a_short_side_so_a_portrait_file_is_reduced_to_the_size_it_is_named() -> (
    None
):
    """A portrait 4K under a 1080p ceiling is reduced to 1080 across and called 1080p."""
    plan = policy.decide(
        asset(vcodec="hevc", width=2160, height=3840, fps=60.0),
        capabilities("h264", "aac", "mp4"),
        max_height=1080,
        cpu_count=8,
    )
    assert plan.route is policy.Route.TRANSCODE
    assert plan.scale_height == 1920
    assert "1080p" in plan.reason


def test_a_portrait_file_within_the_ceiling_by_its_short_side_is_left_alone() -> None:
    # 1080 across and 1920 tall under a 1080p ceiling is 1080p.
    assert policy._height_for_short(1080, 1080, 1920) is None
    assert policy._height_for_short(1080, 1920, 1080) is None
    assert policy._height_for_short(720, 1920, 1080) == 720
    assert policy._height_for_short(720, 1080, 1920) == 1280
    assert policy._height_for_short(720, None, 1080) == 720
    # No height read: nothing to scale to, and no division by zero.
    assert policy._height_for_short(720, 1920, None) is None
    assert policy._height_for_short(720, 1920, 0) is None


def test_a_ten_bit_file_is_converted_for_a_browser_that_decodes_only_eight_and_the_sentence_says_so() -> (
    None
):
    """A ten-bit file is converted for a browser that decodes only HEVC Main, and the sentence
    names the ten-bit kind."""
    eight_bit_only = policy.ClientCapabilities(
        video_codecs=frozenset({"hevc"}),
        audio_codecs=frozenset({"aac"}),
        containers=frozenset({"mp4"}),
    )
    ten_bit_too = policy.ClientCapabilities(
        video_codecs=frozenset({"hevc"}),
        audio_codecs=frozenset({"aac"}),
        containers=frozenset({"mp4"}),
        video_codecs_10bit=frozenset({"hevc"}),
    )
    deep = replace(asset(vcodec="hevc"), bit_depth=10)

    converted = policy.decide(deep, eight_bit_only, max_height=1080, cpu_count=8)
    assert converted.route is policy.Route.TRANSCODE
    assert "10-bit HEVC" in converted.reason

    played = policy.decide(deep, ten_bit_too, max_height=1080, cpu_count=8)
    assert played.route is policy.Route.DIRECT

    # Unknown depth reads as eight.
    unknown = replace(deep, bit_depth=None)
    assert policy.decide(unknown, eight_bit_only, max_height=1080, cpu_count=8).route is (
        policy.Route.DIRECT
    )


def test_an_hdr_file_being_converted_says_its_colours_are_being_mapped() -> None:
    """Converting an HDR file maps its colours and says so; a direct play keeps HDR."""
    hdr = replace(asset(vcodec="hevc"), bit_depth=10, color_transfer="smpte2084")
    assert hdr.is_hdr
    assert not asset().is_hdr
    assert not replace(asset(), color_transfer="bt709").is_hdr

    converted = policy.decide(hdr, capabilities("h264", "aac", "mp4"), max_height=1080, cpu_count=8)
    assert converted.route is policy.Route.TRANSCODE
    assert "HDR" in converted.reason

    ten_bit = policy.ClientCapabilities(
        video_codecs=frozenset({"hevc"}),
        audio_codecs=frozenset({"aac"}),
        containers=frozenset({"mp4"}),
        video_codecs_10bit=frozenset({"hevc"}),
    )
    played = policy.decide(hdr, ten_bit, max_height=1080, cpu_count=8)
    assert played.route is policy.Route.DIRECT
    assert "HDR" not in played.reason


def test_a_file_that_would_stall_is_reported_rather_than_offered() -> None:
    """A transcode below realtime stalls, so the player is told and may try anyway: the projection
    is an estimate."""
    plan = policy.decide(
        asset(vcodec="av1", width=3840, height=2160, fps=60.0),
        capabilities("h264", "aac", "mp4"),
        max_height=1080,
        cpu_count=2,
    )
    assert plan.route is policy.Route.TRANSCODE
    assert plan.streamable is False
    assert plan.projected_realtime is not None
    assert plan.projected_realtime < tuning.MIN_REALTIME_RATIO
    assert "try anyway" in plan.reason


def test_a_file_that_cannot_be_measured_is_allowed_through() -> None:
    """An unprobed file has no dimensions and is let through, or every fresh import is refused."""
    plan = policy.decide(
        asset(vcodec="hevc", width=None, height=None, fps=None),
        capabilities("h264", "aac", "mp4"),
        max_height=1080,
        cpu_count=2,
    )
    assert plan.streamable is True
    assert plan.projected_realtime is None


# --- a file Sift has not read --------------------------------------------------------------------


def _unread(*, mime: str) -> Asset:
    """A video row as a scan leaves it before probing: a mime and nothing else."""
    from dataclasses import replace

    return replace(
        asset(vcodec=None, acodec=None, width=None, height=None, fps=None, duration_ms=None),
        container=None,
        mime=mime,
        probed_at=None,
    )


def test_a_file_sift_has_not_read_is_not_offered_a_plan() -> None:
    """An unread file gets a state of its own, not a plan: `decide` needs its codecs."""
    plan = policy.decide(
        _unread(mime="video/x-matroska"),
        capabilities("h264 hevc", "aac", "mp4 mov"),
        max_height=1080,
        cpu_count=2,
    )

    assert plan.route is policy.Route.UNREAD
    assert "not read" in plan.reason and "browser" not in plan.reason
    assert plan.scale_height is None and plan.projected_realtime is None


def test_an_unread_file_in_a_box_the_browser_opens_is_sent_as_it_is() -> None:
    """An unread file in a container the browser opens is sent as it is."""
    plan = policy.decide(
        _unread(mime="video/mp4"),
        capabilities("h264", "aac", "mp4"),
        max_height=1080,
        cpu_count=2,
    )

    assert plan.route is policy.Route.DIRECT
    assert "not read" in plan.reason


def test_an_unread_picture_is_still_a_picture() -> None:
    """A still has nothing to read before it can be shown."""
    from dataclasses import replace

    still = replace(asset(media_type="image", vcodec=None), probed_at=None, mime="image/jpeg")
    plan = policy.decide(still, capabilities("", "", ""), max_height=1080, cpu_count=2)

    assert plan.route is policy.Route.DIRECT


# --- the projection, against real measurements -----------------------------------------------


@pytest.mark.parametrize(
    ("label", "width", "height", "fps", "vcodec", "cores", "measured"),
    [
        # At 4 cores with 2-second segments; the projection may differ but must fall on the same
        # side of the line.
        ("h264_1080p", 1920, 1080, 30, "h264", 4, 4.3),
        ("hevc_8bit_1080p30", 1080, 1920, 30, "hevc", 4, 4.2),
        ("av1_8bit_1080p", 1920, 1080, 30, "av1", 4, 3.4),
        ("hevc_10bit_1080p60", 1080, 1920, 60, "hevc", 4, 2.2),
        ("av1_8bit_4k", 3840, 2160, 60, "av1", 4, 0.7),
        ("h264_4k_120fps", 2160, 3840, 120, "h264", 4, 0.5),
        ("2-core av1 1080p60", 1920, 1080, 60, "av1", 2, 0.86),
        ("2-core hevc 1080p30", 1080, 1920, 30, "hevc", 2, 1.78),
    ],
)
def test_the_projection_agrees_with_the_spike_about_what_can_stream(
    label: str, width: int, height: int, fps: float, vcodec: str, cores: int, measured: float
) -> None:
    """The model and the measurement classify every file the same way; erring towards refusal is
    allowed, since the player offers to try anyway."""
    projected = policy.projected_realtime(
        width=width, height=height, fps=fps, vcodec=vcodec, cpu_count=cores
    )
    assert projected is not None

    would_stream = measured >= tuning.MIN_REALTIME_RATIO
    if would_stream:
        # Conservative by at most a factor of two.
        assert projected >= measured / 2, f"{label}: far too pessimistic"
    else:
        assert projected < tuning.MIN_REALTIME_RATIO, (
            f"{label}: projected {projected:.2f} would be offered, but it measured "
            f"{measured:.2f} and would stall forever"
        )


def test_the_calibration_row_is_reproduced() -> None:
    """HEVC 10-bit 1080p at 60 fps on 4 cores is the calibration row, 2.2x."""
    projected = policy.projected_realtime(
        width=1080, height=1920, fps=60, vcodec="hevc", cpu_count=4
    )
    assert projected is not None
    assert 2.1 <= projected <= 2.3


def test_downscaling_helps_but_does_not_make_the_decode_free() -> None:
    """Scaling cheapens the encode but the decode stays at full resolution."""
    native = policy.projected_realtime(width=3840, height=2160, fps=60, vcodec="av1", cpu_count=4)
    scaled = policy.projected_realtime(
        width=3840, height=2160, fps=60, vcodec="av1", cpu_count=4, scale_height=1080
    )
    assert native is not None and scaled is not None
    assert scaled > native, "scaling must help"
    assert scaled < native * 4, "but not by the full pixel ratio, since the decode is unchanged"


def test_scaling_up_is_never_attempted() -> None:
    """A ceiling above the source's own height leaves the file alone rather than enlarging it."""
    unscaled = policy.projected_realtime(
        width=1920, height=1080, fps=30, vcodec="h264", cpu_count=4
    )
    asked_higher = policy.projected_realtime(
        width=1920, height=1080, fps=30, vcodec="h264", cpu_count=4, scale_height=2160
    )
    assert unscaled == asked_higher


def test_a_file_with_no_recorded_frame_rate_is_assumed_ordinary() -> None:
    """A row with no `fps` is assumed to be 30, so the stall check still applies to it."""
    assumed = policy.projected_realtime(
        width=1920, height=1080, fps=None, vcodec="h264", cpu_count=4
    )
    explicit = policy.projected_realtime(
        width=1920, height=1080, fps=30.0, vcodec="h264", cpu_count=4
    )
    assert assumed == explicit


@pytest.mark.parametrize("bad", [0, -1, None])
def test_a_file_with_no_dimensions_cannot_be_projected(bad: int | None) -> None:
    assert (
        policy.projected_realtime(width=bad, height=1080, fps=30, vcodec="h264", cpu_count=4)
        is None
    )
    assert (
        policy.projected_realtime(width=1920, height=bad, fps=30, vcodec="h264", cpu_count=4)
        is None
    )


def test_a_machine_reporting_no_cores_is_treated_as_having_one() -> None:
    """A machine reporting no cores counts as one."""
    assert (
        policy.projected_realtime(width=320, height=240, fps=15, vcodec="h264", cpu_count=0)
        is not None
    )


# --- segment arithmetic --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        (10_000, 5),  # exactly five 2-second segments
        (5_400, 3),  # 2 + 2 + 1.4
        (1_000, 1),  # shorter than one segment
        (0, 1),
        (None, 1),
    ],
)
def test_how_many_segments_a_file_is_cut_into(duration_ms: int | None, expected: int) -> None:
    assert policy.segment_count(duration_ms) == expected


def test_the_last_segment_is_short_rather_than_padded() -> None:
    """The last segment is short rather than padded: 2 s, 2 s, 1.4 s."""
    start, duration = policy.segment_bounds(2, 5_400)
    assert start == 4.0
    assert duration == pytest.approx(1.4)


def test_a_file_with_no_duration_yields_one_full_segment() -> None:
    start, duration = policy.segment_bounds(0, None)
    assert start == 0.0
    assert duration == tuning.SEGMENT_SECONDS


def test_a_segment_past_the_end_has_no_length() -> None:
    _, duration = policy.segment_bounds(50, 10_000)
    assert duration == 0.0


# --- Where to start from ------------------------------------------------------------------------
# `resume_point` lives in `sift.kernel.content.user_state`; these lengths are read by
# `watched_to_the_end`.
_ONE_MINUTE_MS = 60_000
_ONE_HOUR_MS = 60 * 60 * 1000


# --- deciding from the file that is actually served ---------------------------------------------


def test_a_file_with_no_repair_is_described_by_its_own_row() -> None:
    """A file with no repair is described by its own row."""
    original = asset(container="mkv")

    assert policy.as_served(original, repaired=False) is original


def test_a_repaired_file_is_described_as_the_mp4_it_is_served_as() -> None:
    """A repaired Matroska file is decided as the MP4 it is served as."""
    served = policy.as_served(asset(container="mkv"), repaired=True)

    assert served.container == "mp4"
    assert served.mime == "video/mp4"


def test_nothing_but_the_container_and_the_type_is_restated() -> None:
    """Only the container and type are restated; every other column still describes the bytes."""
    original = asset(container="mkv", vcodec="hevc", acodec="aac", width=3840, height=2160, fps=60)

    served = policy.as_served(original, repaired=True)

    untouched = [field for field in original.__slots__ if field not in {"container", "mime"}]
    assert untouched, "the asset has no other fields, so this test proves nothing"
    for field in untouched:
        assert getattr(served, field) == getattr(original, field), field


def test_a_repaired_matroska_file_direct_plays_for_a_browser_that_reads_mp4() -> None:
    """The decision follows the bytes that will be sent."""
    original = asset(container="mkv", vcodec="hevc")
    client = policy.ClientCapabilities(
        video_codecs=frozenset({"hevc"}),
        audio_codecs=frozenset({"aac"}),
        containers=frozenset({"mp4"}),
    )

    before = policy.decide(original, client, max_height=1080, cpu_count=4)
    after = policy.decide(
        policy.as_served(original, repaired=True), client, max_height=1080, cpu_count=4
    )

    assert before.route is policy.Route.TRANSCODE
    assert after.route is policy.Route.DIRECT


# --- the ladder --------------------------------------------------------------------------------


def test_no_rung_is_ever_the_same_size_as_the_file_or_bigger() -> None:
    """No rung is the file's own height or larger: that would re-encode at the same size or up."""
    for source_height in (2160, 1440, 1080, 720, 481, 480, 360, 240):
        assert all(rung.height < source_height for rung in policy.rungs(None, source_height)), (
            source_height
        )


def test_a_file_with_no_known_height_is_offered_no_rungs() -> None:
    """A file with no known height is offered no rungs."""
    assert policy.rungs(None, None) == ()
    assert policy.rungs(None, 0) == ()


def test_a_rung_keeps_the_shape_of_the_picture_and_lands_on_an_even_width() -> None:
    """A rung keeps the picture's shape on an even width: H.264 stores colour at half resolution."""
    for height in (1080, 720, 480):
        width = policy.scaled_width(1920, 1080, height)
        assert width is not None
        assert width % 2 == 0, f"{width} is odd and the encoder would refuse it"
        assert abs(width / height - 1920 / 1080) < 0.01


def test_an_unusual_shape_is_scaled_by_its_own_ratio_rather_than_assumed_widescreen() -> None:
    """An unusual shape is scaled by its own ratio."""
    assert policy.scaled_width(1080, 1920, 720) == 404


# --- what the stream may claim to be ------------------------------------------------------------


def test_the_declared_level_rises_with_the_picture() -> None:
    """The declared H.264 level rises with the picture, since a master playlist publishes it and a
    decoder may enforce it."""
    small = policy.h264_level(1280, 720, 30)
    full = policy.h264_level(1920, 1080, 30)
    big = policy.h264_level(3840, 2160, 30)

    assert small[0] == "3.1"
    assert full[0] == "4.1", "not 4.0: same macroblock limits, a bitrate ceiling CRF can pass"
    assert big[0] == "5.1", "4K does not fit in level 4.1 and never did"
    assert small < full < big, "the levels must be ordered, and the hex with them"


def test_a_higher_frame_rate_needs_a_higher_level_at_the_same_size() -> None:
    """A level bounds macroblocks per second, so 1080p60 needs a higher one than 1080p30."""
    assert policy.h264_level(1920, 1080, 60)[0] == "4.2"


def test_an_unmeasured_picture_declares_the_highest_level_rather_than_the_lowest() -> None:
    """An unmeasured picture declares the highest level, never under-promising."""
    assert policy.h264_level(None, None, None)[0] == "6.2"


def test_the_codec_string_names_both_tracks_and_the_right_level() -> None:
    """The codec string names both tracks and the level."""
    assert policy.codec_string(1920, 1080, 30) == "avc1.640029,mp4a.40.2"


# --- two budgets -------------------------------------------------------------------------------


def _ratio(**overrides: object) -> float:
    base: dict[str, object] = {
        "width": 3840,
        "height": 2160,
        "fps": 30.0,
        "vcodec": "h264",
        "cpu_count": 4,
    }
    base.update(overrides)
    answer = policy.projected_realtime(**base)  # type: ignore[arg-type]
    assert answer is not None
    return answer


def test_the_processor_only_model_is_untouched_when_there_is_no_encoder_rate() -> None:
    """Without an encoder rate the processor-only model still reproduces the 2.21x calibration."""
    assert _ratio(width=1920, height=1080, fps=60.0, vcodec="hevc") == pytest.approx(2.21, abs=0.02)


def test_a_hardware_encoder_is_not_charged_to_the_processor() -> None:
    """A hardware encoder is not charged to the processor: the two run on different silicon."""
    on_cpu = _ratio()
    on_card = _ratio(encoder_rate=1_250_000_000.0)

    assert on_card > on_cpu


def test_the_answer_is_the_slower_half_and_not_the_faster_one() -> None:
    """The answer is the slower half: a fast card still waits for the processor to decode."""
    plenty = _ratio(encoder_rate=1e15)
    decode_only = _ratio(cpu_count=4, encoder_rate=1e15)

    assert plenty == decode_only
    assert plenty < 1e6, "an unbounded encoder must not produce an unbounded answer"


def test_a_slower_card_and_a_slower_processor_both_bring_the_answer_down() -> None:
    """Either half can bind."""
    balanced = _ratio(encoder_rate=1_250_000_000.0)

    assert _ratio(encoder_rate=100_000_000.0) < balanced
    assert _ratio(cpu_count=1, encoder_rate=1_250_000_000.0) < balanced


def test_the_decode_estimate_is_the_one_the_projection_uses() -> None:
    """`decode_seconds` is the decode the projection charges, since the measured encoder rate is a
    segment's time minus it."""
    seconds = policy.decode_seconds(
        width=3840, height=2160, fps=30.0, vcodec="h264", cpu_count=4, video_seconds=2.0
    )
    assert seconds is not None
    # Decode alone: (w*h*fps*0.5) / (110M * cores) per second of video.
    assert seconds == pytest.approx(2.0 * (3840 * 2160 * 30 * 0.5) / (110_000_000 * 4), rel=1e-6)


# --- the remux tier ------------------------------------------------------------------------------


def _reader(**overrides: object) -> policy.ClientCapabilities:
    base: dict[str, object] = {
        "video_codecs": frozenset({"hevc"}),
        "audio_codecs": frozenset({"aac"}),
        "containers": frozenset({"mp4"}),
    }
    base.update(overrides)
    return policy.ClientCapabilities(**base)  # type: ignore[arg-type]


def test_only_a_container_problem_asks_for_a_repackaged_copy() -> None:
    """Only a container problem asks for a repackaged copy; a new box fixes no codec."""
    container_problem = asset(container="mkv", vcodec="hevc", acodec="aac")
    assert policy.needs_repackaging(container_problem, _reader())

    playable = asset(container="mp4", vcodec="hevc", acodec="aac")
    assert not policy.needs_repackaging(playable, _reader())

    codec_problem = asset(container="mkv", vcodec="av1", acodec="aac")
    assert not policy.needs_repackaging(codec_problem, _reader())

    audio_problem = asset(container="mkv", vcodec="hevc", acodec="opus")
    assert not policy.needs_repackaging(audio_problem, _reader())


def test_a_photograph_is_never_repackaged() -> None:
    """A photograph is never repackaged."""
    picture = asset(media_type="image", container="jpg", vcodec=None, acodec=None)
    assert not policy.needs_repackaging(picture, _reader())


def test_a_file_served_from_a_copy_says_so_rather_than_passing_as_ordinary() -> None:
    """A file served from a copy says so in the stats panel."""
    original = asset(container="mkv", vcodec="hevc")
    plan = policy.decide(
        policy.as_served(original, repaired=True),
        _reader(),
        max_height=1080,
        cpu_count=4,
        copy=policy.Copy.REPACKAGED,
    )

    assert plan.route is policy.Route.REMUX
    assert "repackaged" in plan.reason.lower()


def test_a_repackaged_copy_names_the_container_the_file_is_stored_in() -> None:
    """A repackaged copy names the stored container, not the MP4 served."""
    original = asset(container="mkv", vcodec="hevc", acodec="aac")
    plan = policy.decide(
        policy.as_served(original, repaired=True),
        _reader(),
        max_height=1080,
        cpu_count=4,
        copy=policy.Copy.REPACKAGED,
        stored_container=original.container,
    )

    assert plan.route is policy.Route.REMUX
    assert plan.reason.startswith("Your browser cannot read this file's container (Matroska), so")
    assert "repackaged copy" in plan.reason
    # The interleave's copy names no container.
    repaired = policy.decide(
        policy.as_served(asset(container="mp4", vcodec="hevc", acodec="aac"), repaired=True),
        _reader(),
        max_height=1080,
        cpu_count=4,
        copy=policy.Copy.REPAIRED,
        stored_container="mp4",
    )
    assert repaired.reason == policy._COPY_REASON[policy.Copy.REPAIRED]


def test_the_plan_route_hands_the_policy_the_stored_container() -> None:
    # The served asset is the MP4 copy; only the route holds the stored container.
    router = (Path(__file__).resolve().parents[1] / "router.py").read_text(encoding="utf-8")
    assert "stored_container=asset.container," in router


def test_a_copy_written_for_the_INTERLEAVE_does_not_blame_the_container() -> None:
    """A copy written for the interleave does not blame the container: the file is an MP4 the
    browser reads."""
    original = asset(container="mp4", vcodec="hevc", acodec="aac")
    plan = policy.decide(
        policy.as_served(original, repaired=True),
        _reader(),
        max_height=1080,
        cpu_count=4,
        copy=policy.Copy.REPAIRED,
    )

    assert plan.route is policy.Route.REMUX
    assert "container" not in plan.reason.lower()
    # What the copy is, and that the file on disk is untouched (worded beside `_COPY_REASON`).
    assert "repackaged copy" in plan.reason
    assert "the same picture and sound" in plan.reason
    assert "untouched" in plan.reason


def test_the_repaired_sentence_is_the_players_word_for_word_on_the_client() -> None:
    # The client's `REPAIRED_WORDS` in `player/facts.ts` must match this sentence word for word.
    facts = Path(__file__).resolve().parents[5] / "frontend" / "src" / "lib" / "player" / "facts.ts"
    source = facts.read_text(encoding="utf-8")
    start = source.index("export const REPAIRED_WORDS =")
    declared = source[start : source.index(";", start)]
    # Either quote: the formatter double-quotes a piece holding an apostrophe.
    pieces = re.findall(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", declared)
    client = "".join(one or other for one, other in pieces).encode("ascii").decode("unicode_escape")
    assert client == policy._COPY_REASON[policy.Copy.REPAIRED]


def test_the_two_copies_do_not_share_a_sentence() -> None:
    # The two reasons must differ.
    said = {
        kind: policy.decide(
            policy.as_served(asset(container="mkv", vcodec="hevc"), repaired=True),
            _reader(),
            max_height=1080,
            cpu_count=4,
            copy=kind,
        ).reason
        for kind in policy.Copy
    }

    assert len(set(said.values())) == len(policy.Copy)


# --- a chosen size ------------------------------------------------------------------------------


def test_choosing_a_size_leaves_the_free_path_deliberately() -> None:
    """Choosing a size leaves the direct-play path, so a 4K file that plays can be turned down."""
    playable = asset(container="mp4", vcodec="hevc", height=2160, width=3840)

    left_alone = policy.decide(playable, _reader(), max_height=1080, cpu_count=4)
    chosen = policy.decide(playable, _reader(), max_height=1080, cpu_count=4, requested_height=720)

    assert left_alone.route is policy.Route.DIRECT
    assert chosen.route is policy.Route.TRANSCODE
    assert chosen.scale_height == 720


def test_asking_for_more_than_the_file_has_is_the_file_rather_than_an_upscale() -> None:
    """Asking for more than the file has is the file at its own size."""
    small = asset(container="mp4", vcodec="hevc", height=480, width=854)

    plan = policy.decide(small, _reader(), max_height=1080, cpu_count=4, requested_height=1080)

    assert plan.scale_height is None, "a rung taller than the source must not enlarge it"


def test_a_chosen_size_is_still_told_honestly_whether_it_will_keep_up() -> None:
    """A chosen size is still told whether it will keep up."""
    huge = asset(container="mp4", vcodec="av1", height=2160, width=3840, fps=60.0)

    plan = policy.decide(huge, _reader(), max_height=2160, cpu_count=1, requested_height=2160)

    assert plan.streamable is False


def test_a_size_is_named_by_its_short_side_so_a_phone_clip_is_not_1920p() -> None:
    """A size is named by its short side, so a portrait phone clip is 1080p, not "1920p"."""
    assert policy.size_name(3840, 2160) == "4K"
    assert policy.size_name(2160, 3840) == "4K"
    assert policy.size_name(1920, 1080) == "1080p"
    assert policy.size_name(1080, 1920) == "1080p"
    assert policy.size_name(1280, 720) == "720p"
    assert policy.size_name(720, 1280) == "720p"


def test_a_wide_crop_is_named_for_the_size_it_reaches_not_the_one_below_it() -> None:
    # A 3840x1600 crop is 1440p: the largest name its short side reaches.
    assert policy.size_name(3840, 1600) == "1440p"
    assert policy.size_name(3840, 2160) == "4K"


def test_something_smaller_than_every_name_is_given_its_own_numbers() -> None:
    # Smaller than every name: its own numbers.
    assert policy.size_name(320, 240) == "320 x 240"


def test_a_file_that_was_never_measured_has_no_name_rather_than_a_guessed_one() -> None:
    """An unmeasured file has no size name rather than a guessed one."""
    assert policy.size_name(None, None) is None
    assert policy.size_name(1920, None) is None
    assert policy.size_name(0, 1080) is None
    assert policy.size_name(-1920, -1080) is None


def test_the_ladder_never_offers_a_rung_at_the_files_own_height() -> None:
    """The ladder offers no rung at the file's own height; the file itself is the top entry."""
    assert 2160 not in [rung.height for rung in policy.rungs(3840, 2160)]
    assert policy.rungs(3840, 2160)[0].height == 1440
    assert policy.rungs(1920, 1080)[0].height == 720
    assert all(rung.height < 2160 for rung in policy.rungs(3840, 2160))


def test_a_landscape_ladder_is_exactly_what_it_always_was() -> None:
    """A landscape ladder is unchanged, so cached segments under these heights stay valid."""
    for width, height in ((1920, 1080), (3840, 2160), (1280, 720), (2560, 1440)):
        by_short = [rung.height for rung in policy.rungs(width, height)]
        expected = [step for step in tuning.LADDER_HEIGHTS if step < height]
        assert by_short == expected, (width, height)
        assert all(rung.short == rung.height for rung in policy.rungs(width, height))


def test_a_portrait_ladder_is_named_for_the_size_the_picture_actually_is() -> None:
    """A portrait ladder is named for its short side, so a 2160x3840 file is not offered "2160p"."""
    ladder = policy.rungs(2160, 3840)
    assert [rung.short for rung in ladder] == [1440, 1080, 720, 480, 360]
    assert [rung.height for rung in ladder] == [2560, 1920, 1280, 852, 640]
    assert 2160 not in [rung.short for rung in ladder]
    assert all(rung.height < 3840 for rung in ladder)


def test_every_rung_height_is_even_whatever_the_shape() -> None:
    # H.264 stores colour at half resolution, so an encoder refuses an odd height.
    for width, height in ((2160, 3840), (1080, 1920), (1000, 1777), (720, 1281), (1920, 1080)):
        assert all(rung.height % 2 == 0 for rung in policy.rungs(width, height)), (width, height)


def test_a_square_video_is_treated_as_its_own_short_side() -> None:
    ladder = policy.rungs(1080, 1080)
    assert [rung.short for rung in ladder] == [720, 480, 360]
    assert [rung.height for rung in ladder] == [720, 480, 360]


# --- what cannot be measured, and what cannot be carried ---------------------------------------


def test_a_file_that_has_never_been_probed_has_no_decode_estimate() -> None:
    """An unprobed file has no decode estimate; the caller lets it through."""
    assert (
        policy.decode_seconds(
            width=None, height=None, fps=30.0, vcodec="h264", cpu_count=8, video_seconds=2.0
        )
        is None
    )


def test_a_file_that_has_never_been_probed_has_no_scaled_width() -> None:
    """An unprobed file has no scaled width to declare."""
    assert policy.scaled_width(None, None, 720) is None


def test_a_picture_larger_than_every_level_gets_the_highest_one_there_is() -> None:
    """Above the level table the highest level is declared, as an 8K file gets."""
    highest = policy.h264_level(15360, 8640, 120.0)
    assert highest == policy.h264_level(30720, 17280, 240.0)
    assert highest != policy.h264_level(1920, 1080, 30.0)


def test_a_rung_that_rounds_away_to_nothing_is_not_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rung that rounds to an even zero is dropped."""
    monkeypatch.setattr(tuning, "LADDER_HEIGHTS", (360, 1))

    assert policy.rungs(640, 480) == (policy.Rung(short=360, height=360),)


# --- what makes a sitting a view, and what makes a file finished -------------------------------
# The server decides what counts as a view, for all three screens that report a sitting.


def test_a_picture_is_viewed_the_instant_it_is_opened() -> None:
    """A picture, GIFs included, is viewed the instant it is opened."""
    assert policy.watch_needed("image", None) == 0
    assert policy.watch_needed("gif", 4_000) == 0
    assert policy.counts_as_a_view("image", None, 0) is True


def test_a_video_whose_length_is_not_known_yet_asks_for_the_dwell() -> None:
    """A video with no length yet asks for the dwell, so opening it is not watching it."""
    assert policy.watch_needed("video", None) == policy.watch_needed("video", 0)
    assert policy.watch_needed("video", None) > 0
    assert policy.counts_as_a_view("video", None, 0) is False


def test_a_video_is_viewed_after_a_quarter_of_it_capped_floored_and_never_past_its_end() -> None:
    """A video is viewed after a quarter of it, capped, floored and never past its end."""
    assert policy.watch_needed("video", 20_000) == 5_000
    assert policy.watch_needed("video", _ONE_HOUR_MS) == 30_000
    assert policy.watch_needed("video", 4_000) == 2_000
    assert policy.watch_needed("video", 1_000) == 1_000
    assert policy.counts_as_a_view("video", 20_000, 4_999) is False
    assert policy.counts_as_a_view("video", 20_000, 5_000) is True


def test_a_sitting_that_ran_backwards_is_not_a_view_of_anything() -> None:
    """A sitting that ran backwards is no view, even of a picture whose threshold is zero."""
    assert policy.counts_as_a_view("image", None, -1) is False
    assert policy.counts_as_a_view("video", _ONE_HOUR_MS, -1) is False


def test_a_file_with_no_timeline_is_never_finished() -> None:
    """A file with no timeline is never finished."""
    assert policy.watched_to_the_end(None, 5_000) is False
    assert policy.watched_to_the_end(0, 5_000) is False
    assert policy.watched_to_the_end(_ONE_HOUR_MS, None) is False
    assert policy.watched_to_the_end(_ONE_HOUR_MS, 0) is False
    assert policy.watched_to_the_end(_ONE_HOUR_MS, _ONE_HOUR_MS - 1_000) is True


# --- which of the two copies a served file is, which the row cannot say --------------------------


def test_a_file_with_a_wide_audio_gap_is_the_REPAIRED_copy() -> None:
    """A wide audio gap on the stored asset marks the repaired copy; repair and repackage write the
    same derivative kind, and `as_served` has already rewritten the container."""
    from dataclasses import replace

    from sift.kernel.mp4 import NEEDS_REPAIR_BYTES
    from sift.slices.player.router import _why_the_copy

    interleaved = replace(asset(), interleave_gap=NEEDS_REPAIR_BYTES)
    assert _why_the_copy(interleaved) is policy.Copy.REPAIRED

    # Just under the line, or never measured: an ordinary repackage.
    assert _why_the_copy(replace(asset(), interleave_gap=NEEDS_REPAIR_BYTES - 1)) is (
        policy.Copy.REPACKAGED
    )
    assert _why_the_copy(replace(asset(), interleave_gap=None)) is policy.Copy.REPACKAGED
