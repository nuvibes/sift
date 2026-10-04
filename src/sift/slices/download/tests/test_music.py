# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the track off a page, and refusing to invent one. The fixtures are real Nuxt payloads in
shape (a flat array of indexes); the names are invented."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sift.slices.download.sources import curl
from sift.slices.download.sources.music import music_in_page, music_of

pytestmark = pytest.mark.anyio

URL = "https://pmvhaven.com/video/night-drive_0c0ffee000000000000beef0"
OTHER = "https://pmvhaven.com/video/something-else_aaaaaaaaaaaaaaaaaaaaaaaa"


def page(payload: list[object]) -> str:
    """A page carrying one Nuxt data island, the way the real one does."""
    return (
        "<html><body><div>markup nobody reads</div>"
        f'<script type="application/json" id="__NUXT_DATA__">{json.dumps(payload)}</script>'
        "</body></html>"
    )


#: The real shape: the video is a dict of REFERENCES, and every one of them is an index.
def payload_for(video_id: str, artist: str | None, song: str | None) -> list[object]:
    entries: list[object] = [
        {"_id": 1, "title": 2, "music": 3},  # 0: the video
        video_id,  # 1
        "NIGHT DRIVE",  # 2
        [4],  # 3: the music list
        {"artist": 5, "song": 6},  # 4
        artist,  # 5
        song,  # 6
    ]
    return entries


def test_the_track_is_read_through_the_references() -> None:
    """Both halves resolved and joined the way the site itself writes them."""
    assert music_in_page(
        page(payload_for("0c0ffee000000000000beef0", "Marlo Venn", "Night drive")), url=URL
    ) == ("Marlo Venn - Night drive")


def test_a_video_with_no_music_is_no_music() -> None:
    """`music: []` is the ordinary case: the page saying nobody has recorded one."""
    payload: list[object] = [{"_id": 1, "music": 2}, "0c0ffee000000000000beef0", []]
    assert music_in_page(page(payload), url=URL) is None


def test_the_music_of_a_SUGGESTED_video_is_never_taken() -> None:
    """The music of a suggested video on the same page is never taken."""
    payload: list[object] = [
        {"_id": 1, "music": 2},  # 0: some other video, listed first
        "aaaaaaaaaaaaaaaaaaaaaaaa",
        [3],
        {"artist": 4, "song": 5},
        "Somebody Else",
        "Not This Song",
        {"_id": 7, "music": 8},  # 6: the one the address names
        "0c0ffee000000000000beef0",
        [9],
        {"artist": 10, "song": 11},
        "Marlo Venn",
        "Night drive",
    ]
    assert music_in_page(page(payload), url=URL) == "Marlo Venn - Night drive"


def test_a_page_whose_video_is_not_in_it_names_nothing() -> None:
    payload = payload_for("0c0ffee000000000000beef0", "Marlo Venn", "Night drive")
    assert music_in_page(page(payload), url=OTHER) is None


def test_half_a_track_is_still_worth_keeping() -> None:
    """A title with no artist is still kept."""
    assert music_in_page(
        page(payload_for("0c0ffee000000000000beef0", None, "Night drive")), url=URL
    ) == ("Night drive")
    assert music_in_page(
        page(payload_for("0c0ffee000000000000beef0", "Marlo Venn", None)), url=URL
    ) == ("Marlo Venn")


def test_nothing_that_is_not_a_video_address_is_asked() -> None:
    """A page whose address is not a video reads as no music."""
    payload = payload_for("0c0ffee000000000000beef0", "Marlo Venn", "Night drive")
    assert music_in_page(page(payload), url="https://pmvhaven.com/profile/quillmoss") is None


def test_a_page_with_no_island_names_nothing() -> None:
    assert music_in_page("<html><body>nothing here</body></html>", url=URL) is None


def test_an_island_that_is_not_json_names_nothing() -> None:
    broken = '<script id="__NUXT_DATA__">[1, 2,</script>'
    assert music_in_page(broken, url=URL) is None


def test_a_reference_that_points_at_itself_does_not_hang() -> None:
    """A reference pointing at itself does not hang."""
    payload: list[object] = [{"_id": 1, "music": 2}, "0c0ffee000000000000beef0", 2]
    assert music_in_page(page(payload), url=URL) is None


def test_a_reference_pointing_outside_the_payload_names_nothing() -> None:
    payload: list[object] = [{"_id": 1, "music": 99}, "0c0ffee000000000000beef0"]
    assert music_in_page(page(payload), url=URL) is None


def test_a_page_whose_island_is_not_a_list_names_nothing() -> None:
    """A page whose payload is not a list names nothing."""
    assert music_in_page(page({"not": "a list"}), url=URL) is None  # type: ignore[arg-type]


def test_a_video_whose_track_list_is_empty_names_nothing() -> None:
    """An empty track list names nothing."""
    payload: list[object] = [{"_id": 1, "music": 2}, "0c0ffee000000000000beef0", []]

    assert music_in_page(page(payload), url=URL) is None


def test_a_track_entry_that_is_not_a_row_names_nothing() -> None:
    # An entry that is not a row must not raise inside a finished download.
    payload: list[object] = [{"_id": 1, "music": 2}, "0c0ffee000000000000beef0", [3], "not a row"]

    assert music_in_page(page(payload), url=URL) is None


async def test_fetching_a_page_that_will_not_come_names_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page that will not come names nothing and never raises: the download already landed."""

    async def _refuse(*args: object, **kwargs: object) -> object:
        raise RuntimeError("the address is not one the server may be pointed at")

    monkeypatch.setattr(curl, "guarded_get", _refuse)

    assert await music_of(URL) is None


async def test_a_page_that_answers_with_anything_but_200_names_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _not_found(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(status_code=404, text="")

    monkeypatch.setattr(curl, "guarded_get", _not_found)

    assert await music_of(URL) is None


async def test_a_page_that_does_come_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    body = page(payload_for("0c0ffee000000000000beef0", "Marlo Venn", "Night drive"))

    async def _answer(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(status_code=200, text=body)

    monkeypatch.setattr(curl, "guarded_get", _answer)

    assert await music_of(URL) == "Marlo Venn - Night drive"
