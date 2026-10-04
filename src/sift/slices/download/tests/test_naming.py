# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a downloaded file is called, mostly on sites that do not carry every fact."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from sift.kernel import naming as kernel_naming
from sift.slices.download import naming
from sift.slices.download.sources.argv import build_ytdlp_argv

# Names are in the device's local time, and the suite holds the clock to UTC; fixed here because
# this line runs at collection, before the zone is held.
_WHEN = datetime(2026, 8, 13, 14, 5, tzinfo=UTC)
pytestmark = pytest.mark.anyio

_FULL = naming.Facts(site="YouTube", username="someone", original="A video title", when=_WHEN)


def test_every_token_fills_in() -> None:
    assert (
        naming.fill("{site} {creator} {name} {date} {time}", _FULL)
        == "YouTube someone A video title 2026-08-13 14-05"
    )


def test_the_date_and_time_in_a_name_are_the_devices_own_not_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The date and time in a name are the device's own, proved with a named zone."""
    monkeypatch.setattr(kernel_naming, "_DEVICE_ZONE", timezone(timedelta(hours=-4)))
    utc = naming.Facts(original="x", when=datetime(2026, 9, 25, 3, 37, tzinfo=UTC))
    assert naming.fill("{date} {time}", utc) == "2026-09-24 23-37"
    # And "now", which is what every download actually passes, is the same clock.
    now = naming.fill("{date}", naming.Facts(original="x"))
    assert now == datetime.now(UTC).astimezone(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")


def test_an_empty_template_means_keep_the_name_it_already_had() -> None:
    """The default, and a real answer rather than an absence: the resolvers already read a good
    name out of most responses, and inventing one over the top of that is a step backwards."""
    assert naming.fill("", _FULL) == ""
    assert naming.fill("   ", _FULL) == ""


def test_a_missing_fact_does_not_leave_the_punctuation_around_it() -> None:
    """The failure this exists to stop. A file host knows no uploader, so a template written while
    looking at a video site produces a name starting with a dash and a space on every one of them."""
    anonymous = naming.Facts(site="GoFile", original="photo-01", when=_WHEN)
    assert naming.fill("{creator} - {name}", anonymous) == "photo-01"
    assert naming.fill("{name} - {creator}", anonymous) == "photo-01"
    assert naming.fill("{creator}", anonymous) == ""


def test_a_template_of_only_missing_facts_yields_nothing_rather_than_punctuation() -> None:
    nothing_known = naming.Facts(original="x", when=_WHEN)
    assert naming.fill("{site} - {creator}", nothing_known) == ""


def test_a_token_nobody_recognises_is_left_visible_rather_than_dropped() -> None:
    """A typo has to be visible in the preview. Silently dropped, it is discovered as a hundred
    files that are all missing the same part of their name."""
    assert "{uploaderr}" in naming.fill("{uploaderr} {name}", _FULL)


def test_a_name_from_a_site_cannot_walk_out_of_its_folder() -> None:
    """The values arrive from a remote site. A separator or a run of dots that got through would
    put the file somewhere other than the folder it was meant for."""
    hostile = naming.Facts(site="../../etc", username="a/b", original="..\\..\\passwd", when=_WHEN)
    filled = naming.fill("{site} {creator} {name}", hostile)
    assert "/" not in filled
    assert "\\" not in filled
    assert ".." not in filled


def test_a_very_long_name_is_cut_to_something_a_filesystem_will_take() -> None:
    long_one = naming.Facts(original="x" * 500, when=_WHEN)
    assert len(naming.fill("{name}", long_one)) <= 120


async def test_renaming_moves_the_file_and_keeps_its_extension(tmp_path: Path) -> None:
    staged = tmp_path / "abc123.mp4"
    staged.write_bytes(b"x")
    landed = await naming.rename(staged, "{site} - {name}", _FULL)
    assert landed.name == "YouTube - A video title.mp4"
    assert landed.read_bytes() == b"x"


async def test_a_template_that_yields_nothing_leaves_the_file_alone(tmp_path: Path) -> None:
    staged = tmp_path / "abc123.mp4"
    staged.write_bytes(b"x")
    assert await naming.rename(staged, "", _FULL) == staged
    assert staged.exists()


async def test_a_name_already_taken_takes_the_next_number(tmp_path: Path) -> None:
    """A name already taken takes the next number; the file there is not written over."""
    (tmp_path / "YouTube.mp4").write_bytes(b"first")
    staged = tmp_path / "second.mp4"
    staged.write_bytes(b"second")

    landed = await naming.rename(staged, "{site}", _FULL)

    assert landed.name == "YouTube-1.mp4"
    assert landed.read_bytes() == b"second"
    # Untouched.
    assert (tmp_path / "YouTube.mp4").read_bytes() == b"first"


async def test_a_carousel_lands_under_one_name_and_its_numbered_siblings(tmp_path: Path) -> None:
    """A carousel lands under one name and its numbered siblings."""
    for index, body in enumerate((b"one", b"two", b"three")):
        # Staged as the fetcher stages a carousel: the site's own name, kept apart by its index.
        staged = tmp_path / f"cdn_n-{index}.jpg"
        staged.write_bytes(body)
        await naming.rename(staged, "{site}", _FULL)

    # The jpgs only. `tmp_path` is not empty here: a fixture puts its own directories in it, and
    # listing the lot asserts on those too.
    assert sorted(one.name for one in tmp_path.glob("*.jpg")) == [
        "YouTube-1.jpg",
        "YouTube-2.jpg",
        "YouTube.jpg",
    ]
    # Each kept its own bytes rather than three names over one file.
    assert (tmp_path / "YouTube.jpg").read_bytes() == b"one"
    assert (tmp_path / "YouTube-2.jpg").read_bytes() == b"three"


async def test_more_collisions_than_the_cap_leaves_the_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the collision cap the file keeps its own name."""
    monkeypatch.setattr(kernel_naming, "MOST_COLLISIONS", 2)
    for taken in ("YouTube.mp4", "YouTube-1.mp4", "YouTube-2.mp4"):
        (tmp_path / taken).write_bytes(b"taken")
    staged = tmp_path / "another.mp4"
    staged.write_bytes(b"mine")

    assert await naming.rename(staged, "{site}", _FULL) == staged
    assert staged.read_bytes() == b"mine"


async def test_a_name_the_filesystem_refuses_leaves_the_file_alone(tmp_path: Path) -> None:
    """Every limit here is cleared by the length cut above, so this is the belt to that braces: the
    download succeeded, and only its name is not what was asked for."""
    staged = tmp_path / "abc.mp4"
    staged.write_bytes(b"x")
    landed = await naming.rename(staged, "{name}", naming.Facts(original="ok", when=_WHEN))
    assert landed.exists()


def test_every_token_the_screen_offers_actually_fills_in() -> None:
    """The list is what a settings screen shows somebody. A token offered and not implemented is a
    template that silently keeps its own braces in the middle of every filename."""
    for token in naming.TOKENS:
        filled = naming.fill(f"{{{token}}}", _FULL)
        assert "{" not in filled, f"{token} is offered but does not fill in"


# --- the two ways a rename decides there is nothing to do ----------------------------------------


def test_a_file_already_called_what_the_template_asks_for_is_left_alone(tmp_path: Path) -> None:
    """A file already called what the template asks for is left alone."""
    landed = tmp_path / "holiday.mp4"
    landed.write_bytes(b"x")

    assert naming._free_name(landed, "holiday") is None


def test_a_collision_whose_numbered_name_is_this_very_file_is_left_alone(tmp_path: Path) -> None:
    """A collision whose numbered name is this very file is left alone."""
    (tmp_path / "holiday.mp4").write_bytes(b"first")
    landed = tmp_path / "holiday-1.mp4"
    landed.write_bytes(b"second")

    assert naming._free_name(landed, "holiday") is None
    # The known positive: a THIRD file with the same wanted stem does get a number.
    third = tmp_path / "from-the-cdn.mp4"
    third.write_bytes(b"third")
    assert naming._free_name(third, "holiday") == tmp_path / "holiday-2.mp4"


def test_the_tools_id_comes_off_a_staged_name_and_nothing_else_does() -> None:
    """The tool writes `<title> [<id>]` with restricted names, so its one space is the template's.
    The id comes off (a real one and a repeated one alike), and a name of any other shape stays."""
    assert naming.without_tool_id("Some_title_here [ab12ab12ab12a]") == "Some_title_here"
    assert naming.without_tool_id("image0 [image0]") == "image0"
    assert naming.without_tool_id(
        "tumblr_k3pzvm7Qe82wbxa4h_720 [tumblr_k3pzvm7Qe82wbxa4h_720]"
    ) == ("tumblr_k3pzvm7Qe82wbxa4h_720")
    # Known negatives: a space in the title is not the tool's (a gallery tool keeps a site's own
    # spelling), nor an empty bracket, more name after it, brackets inside, or no bracket at all.
    assert naming.without_tool_id("Holiday photo [2019]") == "Holiday photo [2019]"
    assert naming.without_tool_id("image0 []") == "image0 []"
    assert naming.without_tool_id("image0 [image0] again") == "image0 [image0] again"
    assert naming.without_tool_id("take [2] [take [2]]") == "take [2] [take [2]]"
    assert naming.without_tool_id("plain") == "plain"


async def test_an_empty_template_takes_the_tools_id_off_the_file(tmp_path: Path) -> None:
    staged = tmp_path / "k4mzq1_-_RDownload [k4mzq1_-_RDownload].mp4"
    staged.write_bytes(b"x")

    produced = await naming.rename(
        staged, "", naming.Facts(site="Discord", username=None, original="x")
    )

    assert produced.name == "k4mzq1_-_RDownload.mp4"
    assert produced.exists() and not staged.exists()


async def test_two_videos_of_one_title_land_as_the_title_and_the_next_number(
    tmp_path: Path,
) -> None:
    """Why the tool still writes the id into staging: under a title-only template it would skip
    the second clip as already downloaded. Here both arrive, and neither lands over the other."""
    staging = tmp_path / "staging"
    staging.mkdir()
    first = staging / "Same_title [111aaa].mp4"
    second = staging / "Same_title [222bbb].mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    named = [
        await naming.rename(staged, "", naming.Facts(original=staged.stem))
        for staged in (first, second)
    ]

    assert [path.name for path in named] == ["Same_title.mp4", "Same_title-1.mp4"]
    assert [path.read_bytes() for path in named] == [b"first", b"second"]


#: A Discord attachment whose file an Instagram downloader named: a stem well past forty bytes. The
#: query string is the CDN's signature and plays no part in the name.
_LONG_STEM = "quillmoss_20260630_reel_3900000000000000123_1_3900000000000000123"
_ATTACHMENT = (
    "https://cdn.discordapp.com/attachments/1100000000000000001/1500000000000000002/"
    f"{_LONG_STEM}.mp4?ex=6a9f8722&hm=20f2e297&is=6a9e35a2"
)


def _as_the_tool_writes_it(output: str, *, title: str, media_id: str, ext: str) -> str:
    """The name yt-dlp makes of its `--output` template, `%(field).<n>B` cut to n bytes."""

    def fill(found: re.Match[str]) -> str:
        field, width = found.group(1), found.group(2)
        value = {"title": title, "id": media_id, "ext": ext}[field]
        if width is None:
            return value
        return value.encode()[: int(width)].decode("utf-8", "ignore")

    return Path(re.sub(r"%\((\w+)\)(?:\.(\d+)B|s)", fill, output)).name


async def test_a_long_discord_attachment_is_named_once_through_the_real_naming_path(
    tmp_path: Path,
) -> None:
    """A long Discord attachment is named once, not with its stem repeated."""
    staging = tmp_path / "staging"
    staging.mkdir()
    argv = build_ytdlp_argv(_ATTACHMENT, staging)
    output = argv[argv.index("--output") + 1]
    stem = unquote(Path(urlsplit(_ATTACHMENT).path).stem)
    written = _as_the_tool_writes_it(output, title=stem, media_id=stem, ext="mp4")
    # What the tool hands Sift: the whole stem, then the first forty bytes of it in brackets.
    assert written == f"{_LONG_STEM} [quillmoss_20260630_reel_3900000000000000].mp4"
    staged = staging / written
    staged.write_bytes(b"x")

    # Exactly as the download job names it: an empty template, `{name}` the stem without the id.
    produced = await naming.rename(
        staged,
        naming.DEFAULT_TEMPLATE,
        naming.Facts(site="Discord", username=None, original=naming.without_tool_id(staged.stem)),
    )

    assert produced.name == f"{_LONG_STEM}.mp4"
    assert [path.name for path in staging.iterdir()] == [f"{_LONG_STEM}.mp4"]


async def test_a_real_id_the_template_writes_never_reaches_the_name(tmp_path: Path) -> None:
    """Not `Some_title _ab12ab12ab12a_.mp4` in the library: the real template, filled with a site's
    title and id as the tool restricts them, names the title alone."""
    staging = tmp_path / "staging"
    staging.mkdir()
    argv = build_ytdlp_argv("https://example.com/view_video?key=ab12ab12ab12a", staging)
    output = argv[argv.index("--output") + 1]
    written = _as_the_tool_writes_it(
        output, title="Some_title_with_a_Name", media_id="ab12ab12ab12a", ext="mp4"
    )
    staged = staging / written
    staged.write_bytes(b"x")

    produced = await naming.rename(
        staged, "", naming.Facts(original=naming.without_tool_id(staged.stem))
    )

    assert produced.name == "Some_title_with_a_Name.mp4"


def test_a_filled_template_uses_the_name_without_the_id() -> None:
    """`{name}` is what every template names the file by, so an id left on it there is on every
    template's output too: the job hands the stem in with the id taken off."""
    facts = naming.Facts(
        site="Discord",
        original=naming.without_tool_id(f"{_LONG_STEM} [quillmoss_20260630_reel_3900000000000000]"),
        when=_WHEN,
    )
    assert naming.fill("{site} {name}", facts) == f"Discord {_LONG_STEM}"
