# SPDX-License-Identifier: AGPL-3.0-or-later
"""The quality menu: what is offered, and what is refused (a photograph, a file with nothing below
it, and Auto before anything is converted)."""

from __future__ import annotations

import pytest

from sift.slices.player import policy
from sift.slices.player.router import Quality, _qualities
from sift.slices.player.tests.conftest import asset

pytestmark = pytest.mark.unit

BASE = "/api/assets/01HX0000000000000000000001"


def _plan(route: policy.Route, *, streamable: bool = True) -> policy.Plan:
    return policy.Plan(route=route, reason="because", streamable=streamable)


def _menu(
    *,
    route: policy.Route = policy.Route.DIRECT,
    width: int | None = 1920,
    height: int | None = 1080,
    media_type: str = "video",
    cpu_count: int = 8,
    encoder_rate: float | None = None,
    streamable: bool = True,
) -> list[Quality]:
    return _qualities(
        asset(width=width, height=height, media_type=media_type),
        _plan(route, streamable=streamable),
        base=BASE,
        default_url=f"{BASE}/stream",
        cpu_count=cpu_count,
        encoder_rate=encoder_rate,
    )


def test_a_photograph_has_no_quality_menu() -> None:
    """There is one way to look at a picture, so a control offering a choice is a control that
    cannot do anything, which is worse than no control at all."""
    assert _menu(media_type="image") == []


def test_a_file_with_nothing_below_it_has_no_quality_menu() -> None:
    """A 360p clip has no rung worth making underneath it. What would be left is a menu of one
    entry: the file, named, beside nothing to switch to."""
    assert _menu(width=640, height=360) == []


def test_a_file_of_unknown_size_has_no_quality_menu() -> None:
    """A row that has never been probed cannot be laddered, because every rung is defined as
    strictly below a number nobody has."""
    assert _menu(width=None, height=None) == []


def test_the_menu_leads_with_the_file_itself_and_then_descends() -> None:
    labels = [entry.label for entry in _menu()]
    assert labels == ["Original", "720p", "480p", "360p"]


def test_the_file_itself_is_named_the_file_rather_than_a_number() -> None:
    """Named by height, a phone video stored 1080 wide and 1920 tall would come out "1920p",
    directly above a rung called "1080p" that is in fact the same 1080 across. Every way of naming
    the source by a number is wrong for some shape of file, so it is not named by one."""
    original = _menu(width=1080, height=1920)[0]
    assert original.label == "Original"
    assert original.height is None
    assert original.detail is not None
    assert "1080" in original.detail


def test_original_is_the_full_size_conversion_when_the_plan_reduced_the_picture() -> None:
    """When the plan reduced the picture, Original asks for full size with its own projection."""
    reduced = policy.Plan(
        route=policy.Route.TRANSCODE, reason="because", scale_height=1080, streamable=True
    )
    menu = _qualities(
        asset(width=3840, height=2160),
        reduced,
        base=BASE,
        default_url=f"{BASE}/hls/index.m3u8?route=transcode&height=1080",
        cpu_count=2,
        encoder_rate=None,
    )
    by_label = {one.label: one for one in menu}

    assert by_label["Original"].url == f"{BASE}/hls/index.m3u8?route=transcode"
    assert by_label["Original"].smooth is False, "two cores cannot convert 4K at full size"
    assert by_label["1080p"].url == f"{BASE}/hls/index.m3u8?route=transcode&height=1080", (
        "the reduced picture is the rung of that size, which the player opens on"
    )
    assert [one.label for one in menu] == [
        "Auto",
        "Original",
        "1440p",
        "1080p",
        "720p",
        "480p",
        "360p",
    ]


def test_a_reduced_portrait_picture_that_matches_no_rung_is_named_for_its_size() -> None:
    """The ceiling is a height and a portrait ladder is keyed by the short side, so a 2160x3840
    file reduced to 1080 tall is 608 across: no rung. It is still what the player opens on, so
    it is on the menu under the name of what it comes to."""
    reduced = policy.Plan(
        route=policy.Route.TRANSCODE, reason="because", scale_height=1080, streamable=True
    )
    menu = _qualities(
        asset(width=2160, height=3840),
        reduced,
        base=BASE,
        default_url=f"{BASE}/hls/index.m3u8?route=transcode&height=1080",
        cpu_count=8,
        encoder_rate=None,
    )
    labels = [one.label for one in menu]

    assert labels[:3] == ["Auto", "Original", "608p"]
    assert menu[2].url == f"{BASE}/hls/index.m3u8?route=transcode&height=1080"
    assert menu[2].height == 1080


def test_a_rung_is_named_by_its_short_side_and_asked_for_by_its_height() -> None:
    """The two are the same number for a landscape file and are not for a portrait one, where
    naming a rung by its height would produce "2160p" for a picture 1215 across, reading as 4K,
    directly under the file itself, which really is 4K."""
    tallest = _menu(width=2160, height=3840)[1]
    assert tallest.label == "1440p"
    assert "height=2560" in tallest.url


def test_auto_is_offered_only_once_the_file_is_already_being_converted() -> None:
    """On a file playing untouched there is nothing to switch between without starting a
    conversion, and doing that unasked is the behaviour the free path exists to avoid."""
    assert _menu()[0].label == "Original"

    converted = _menu(route=policy.Route.TRANSCODE)
    assert converted[0].label == "Auto"
    assert converted[0].auto is True
    assert converted[0].url.endswith("/hls/master.m3u8")


def test_a_repackaged_file_is_offered_auto_as_well() -> None:
    """It is not being played off the disk, so the ladder exists behind it either way."""
    assert _menu(route=policy.Route.REMUX)[0].label == "Auto"


def test_the_file_itself_carries_whether_it_will_play_smoothly() -> None:
    """It is the plan already made, named, so the answer is the one `/playback` reached rather
    than a second opinion formed here."""
    assert _menu(streamable=False)[0].smooth is False
    assert _menu(streamable=True)[0].smooth is True


def test_a_rung_this_machine_cannot_keep_up_with_is_marked_rough() -> None:
    """Below the realtime line the buffer drains faster than it fills and the player stalls
    forever. The rung is still offered (somebody may want it anyway), and it is not offered
    silently. On two cores a 4K source is under the line at the top of the ladder and over it
    further down, so the same menu carries both answers."""
    rungs = _menu(width=3840, height=2160, cpu_count=2)[1:]
    assert not rungs[0].smooth
    assert rungs[-1].smooth


def test_a_machine_with_an_encoder_keeps_up_where_one_without_does_not() -> None:
    """Decoding stays the processor's work and the encoding moves off it, so the two halves run at
    once and the answer is the slower of them, not their sum. The same machine and the same file
    that could not manage the top of the ladder manages all of it."""
    fast = _menu(width=3840, height=2160, cpu_count=2, encoder_rate=1_000_000_000.0)
    assert all(entry.smooth for entry in fast[1:])
