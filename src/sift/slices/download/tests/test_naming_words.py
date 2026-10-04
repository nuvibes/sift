# SPDX-License-Identifier: AGPL-3.0-or-later
"""The four naming words about the POST rather than the download: `{id}`, `{n}`, `{title}` and
`{posted}`: what each fills with, where each fact is read, and what an empty one leaves behind.

The rule running through all of them is the one every naming fact follows: a site that does not say
leaves the word empty, and an empty word leaves a clean name. `{posted}` has a second rule of its
own, which is that it is never the download moment: `{date}` already means that, and a posting
date that quietly fell back to it would be a wrong fact that looks exactly like a right one.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from sift.kernel import naming as kernel_naming
from sift.slices.download import naming
from sift.slices.download.sources import argv, subproc
from sift.slices.download.sources import downloader as downloader_mod
from sift.slices.download.sources import fetcher as fetcher_mod
from sift.slices.download.sources.downloader import Downloader
from sift.slices.download.sources.resolved import (
    NameFacts,
    ResolvedItem,
    ResolvedMedia,
    item_name_facts,
    tool_file_name_facts,
)
from sift.slices.download.sources.subproc import SubprocessResult

pytestmark = pytest.mark.anyio

_WHEN = datetime(2026, 9, 20, 10, 0).astimezone()
_POST = naming.Facts(
    site="TikTok",
    username="someone",
    original="6699ebfacbba",
    when=_WHEN,
    id="7401234567890123456",
    n=2,
    title="A post title",
    posted=date(2026, 9, 12),
)
_BARE = naming.Facts(site="TikTok", username="someone", original="6699ebfacbba", when=_WHEN)


# --- the words ------------------------------------------------------------------------------------


def test_each_new_word_fills_from_its_fact() -> None:
    assert naming.fill("{id}", _POST) == "7401234567890123456"
    assert naming.fill("{n}", _POST) == "2"
    assert naming.fill("{title}", _POST) == "A post title"
    assert naming.fill("{posted}", _POST) == "2026-09-12"


def test_the_screen_offers_the_four_words_with_a_sentence_each() -> None:
    for word in ("id", "n", "title", "posted"):
        assert naming.TOKENS[word].endswith("."), word


def test_a_tiktok_name_reads_creator_posted_and_id() -> None:
    """The name Sift ships for TikTok, filled the way a download fills it."""
    assert (
        naming.fill("{creator} - {posted} - {id}", _POST)
        == "someone - 2026-09-12 - 7401234567890123456"
    )


def test_posted_is_empty_where_the_site_does_not_say_and_never_the_download_date() -> None:
    """The download moment is `{date}`. A `{posted}` that fell back to it would put the download
    date in the name looking exactly like a posting date."""
    assert naming.fill("{posted}", _BARE) == ""
    assert naming.fill("{date}", _BARE) == "2026-09-20"


@pytest.mark.parametrize(
    "template",
    [
        "{posted} - {creator} - {id}",
        "{creator} - {posted} - {id}",
        "{creator} - {id} - {n}",
        "{title} - {creator}",
        "{creator} ({posted})",
        "{creator}_{n}",
    ],
)
def test_an_empty_word_leaves_no_separator_at_either_end(template: str) -> None:
    filled = naming.fill(template, _BARE)
    assert filled == "someone", filled


def test_a_posting_moment_is_written_on_this_devices_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Posted at 01:30 UTC on the 13th is the evening of the 12th four hours west of Greenwich,
    and the name is read by the person whose clock that is."""
    monkeypatch.setattr(kernel_naming, "_DEVICE_ZONE", timezone(timedelta(hours=-4)))
    moment = naming.Facts(posted=datetime(2026, 9, 13, 1, 30, tzinfo=UTC))
    assert naming.fill("{posted}", moment) == "2026-09-12"


def test_a_posting_day_is_written_as_the_site_gave_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare day is already a calendar day; converting it would move it."""
    monkeypatch.setattr(kernel_naming, "_DEVICE_ZONE", timezone(timedelta(hours=-4)))
    assert naming.fill("{posted}", naming.Facts(posted=date(2026, 9, 13))) == "2026-09-13"


def test_the_tools_id_is_kept_as_a_fact_where_the_name_drops_it() -> None:
    """RedGIFs' slug and Imgur's ID are the only unique part of their names."""
    assert naming.without_tool_id("Big_tags_here [calmbrightfox]") == "Big_tags_here"
    assert naming.tool_id("Big_tags_here [calmbrightfox]") == "calmbrightfox"


@pytest.mark.parametrize(
    "stem",
    [
        "image0 [image0]",  # a plain file: the tool repeated the name, which is not an ID
        "Holiday photo [2019]",  # a space in the name: not the tool's shape
        "no_bracket_at_all",
    ],
)
def test_a_stem_that_carries_no_tool_id_gives_none(stem: str) -> None:
    assert naming.tool_id(stem) is None


# --- which file of a post, and the post's facts ---------------------------------------------------


def _media(
    items: list[ResolvedItem],
    *,
    site: str = "Instagram",
    post_id: str | None = "DQmExampl3C",
    title: str | None = None,
    posted: date | None = None,
) -> ResolvedMedia:
    return ResolvedMedia(
        site=site,
        # A neutral address: the fetch asks the catalog about it, and a real Site's cookie rules
        # are not what these tests are about.
        source_url="https://site.example/a",
        source_host="site.example",
        items=items,
        post_id=post_id,
        title=title,
        posted=posted,
    )


def _direct(index: int, *, item_id: str | None = None) -> ResolvedItem:
    return ResolvedItem(
        index=index,
        url=f"https://cdn.example/{index}.jpg",
        media_type="image",
        ext=".jpg",
        item_id=item_id,
    )


def test_a_carousels_files_are_numbered_from_one() -> None:
    media = _media([_direct(0), _direct(1), _direct(2)])
    assert [item_name_facts(media, one).n for one in media.items] == [1, 2, 3]
    assert {item_name_facts(media, one).id for one in media.items} == {"DQmExampl3C"}


def test_a_post_of_one_file_has_no_number() -> None:
    media = _media([_direct(0)])
    assert item_name_facts(media, media.items[0]).n is None


def test_a_file_with_its_own_id_is_named_by_it_rather_than_the_posts() -> None:
    media = _media([_direct(0, item_id="abc123"), _direct(1)], post_id=None)
    assert [item_name_facts(media, one).id for one in media.items] == ["abc123", None]


def test_a_files_own_id_comes_before_the_posts_and_the_posts_title_before_the_clips() -> None:
    """A Reddit post embedding a clip: the clip's ID tells two clips apart, and the post's title
    and moment are what the Site's names are made of."""
    media = _media([], post_id="1abc23", title="The post", posted=date(2026, 1, 2))
    said = NameFacts(id="calmbrightfox", title="The clip", posted=date(2026, 1, 1))
    facts = tool_file_name_facts(media, Path("x [calmbrightfox].mp4"), said, one_of=1)
    assert facts == NameFacts(id="calmbrightfox", title="The post", posted=date(2026, 1, 2))
    unread = _media([], post_id="1abc23")
    facts = tool_file_name_facts(unread, Path("x [calmbrightfox].mp4"), said, one_of=1)
    assert (facts.title, facts.posted) == ("The clip", date(2026, 1, 1))


def test_without_the_tools_record_the_staged_id_and_then_the_post_stand() -> None:
    media = _media([], post_id="1abc23", title="The post")
    staged = tool_file_name_facts(media, Path("A_clip [calmbrightfox].mp4"), None, one_of=1)
    assert (staged.id, staged.title) == ("calmbrightfox", "The post")
    plain = tool_file_name_facts(media, Path("image0 [image0].jpg"), None, one_of=1)
    assert plain.id == "1abc23"


def test_an_x_pictures_number_is_read_from_the_name_the_tool_gave_it() -> None:
    """gallery-dl names an X post's pictures `<post number>_<picture number>`."""
    media = _media([], site="X", post_id="1700000000000000123")
    first = tool_file_name_facts(media, Path("1700000000000000123_1.jpg"), None, one_of=2)
    second = tool_file_name_facts(media, Path("1700000000000000123_2.jpg"), None, one_of=2)
    assert (first.id, first.n, second.n) == ("1700000000000000123", 1, 2)


def test_a_trailing_number_is_not_a_picture_number_unless_it_follows_the_posts_id() -> None:
    media = _media([], site="X", post_id="1700000000000000123")
    other = tool_file_name_facts(media, Path("holiday_2.jpg"), None, one_of=2)
    assert other.n is None


def test_a_lone_x_picture_has_no_number() -> None:
    media = _media([], site="X", post_id="1700000000000000123")
    only = tool_file_name_facts(media, Path("1700000000000000123_1.jpg"), None, one_of=1)
    assert only.n is None


# --- what yt-dlp says about each file -------------------------------------------------------------


def _facts_file(directory: Path, *lines: object) -> None:
    (directory / argv.TOOL_FACTS_FILE).write_text(
        "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n",
        encoding="utf-8",
    )


def test_the_tools_record_is_read_by_file_name(tmp_path: Path) -> None:
    _facts_file(
        tmp_path,
        {
            "id": "Xy7Qm2Lp9Ka",
            "title": "A video title",
            "upload_date": "20260912",
            "timestamp": 1_789_000_000,
            "extractor_key": "Youtube",
            "filepath": str(tmp_path / "A_video_title [Xy7Qm2Lp9Ka].mp4"),
        },
    )
    said = argv.tool_name_facts(tmp_path)
    assert said == {
        "A_video_title [Xy7Qm2Lp9Ka].mp4": NameFacts(
            id="Xy7Qm2Lp9Ka",
            title="A video title",
            posted=datetime.fromtimestamp(1_789_000_000, UTC),
        )
    }


def test_a_day_stands_where_the_tool_knew_no_moment(tmp_path: Path) -> None:
    _facts_file(
        tmp_path,
        {"id": "a", "upload_date": "20260912", "extractor_key": "X", "filepath": "/w/a.mp4"},
    )
    assert argv.tool_name_facts(tmp_path)["a.mp4"].posted == date(2026, 9, 12)


def test_a_plain_file_address_says_nothing_about_a_post(tmp_path: Path) -> None:
    """Its ID and title are the file's own stem, and its date is the server's Last-Modified."""
    _facts_file(
        tmp_path,
        {
            "id": "image0",
            "title": "image0",
            "upload_date": "20260925",
            "extractor_key": "Generic",
            "filepath": "/w/image0 [image0].jpg",
        },
    )
    assert argv.tool_name_facts(tmp_path) == {}


def test_an_unreadable_record_is_passed_over_and_a_missing_one_is_empty(tmp_path: Path) -> None:
    assert argv.tool_name_facts(tmp_path) == {}
    _facts_file(tmp_path, "not json", {"no": "filepath"}, {"id": "b", "filepath": "/w/b.mp4"})
    assert list(argv.tool_name_facts(tmp_path)) == ["b.mp4"]


def test_the_tools_record_is_not_taken_for_media(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"x")
    _facts_file(tmp_path, {"id": "b", "filepath": "/w/clip.mp4"})
    assert subproc.output_files(tmp_path) == [tmp_path / "clip.mp4"]


def test_ytdlp_is_asked_to_write_the_record_inside_its_own_directory(tmp_path: Path) -> None:
    built = argv.build_ytdlp_argv("https://example.com/v", tmp_path)
    at = built.index("--print-to-file")
    assert built[at + 1] == argv.TOOL_FACTS_TEMPLATE
    assert built[at + 2] == str(tmp_path / argv.TOOL_FACTS_FILE)


# --- the handover: the fetch hands each file's facts to the namer ---------------------------------


def _returns(result: SubprocessResult) -> Callable[..., Awaitable[SubprocessResult]]:
    async def fake_run(_argv: list[str], **_kwargs: object) -> SubprocessResult:
        return result

    return fake_run


async def test_a_tool_files_facts_come_back_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = tmp_path / "Some_tags [calmbrightfox].mp4"
    staged.write_bytes(b"x")
    _facts_file(
        tmp_path,
        {
            "id": "calmbrightfox",
            "title": "Some tags",
            "timestamp": 1_789_000_000,
            "extractor_key": "RedGifs",
            "filepath": str(staged),
        },
    )
    media = _media(
        [ResolvedItem.subprocess(source_url="https://site.example/a", backend="ytdlp")],
        site="RedGIFs",
        post_id=None,
    )

    async def fake_resolve(_url: str, **_kwargs: object) -> ResolvedMedia:
        return media

    monkeypatch.setattr(downloader_mod, "resolve", fake_resolve)
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", "")))

    fetched = await Downloader().fetch(media.source_url, into=tmp_path)

    assert fetched.files == [staged]
    assert fetched.names[staged].id == "calmbrightfox"
    assert fetched.names[staged].title == "Some tags"


async def test_a_file_fetched_directly_carries_its_posts_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [_direct(0), _direct(1)]
    media = _media(items, title="A caption", posted=date(2026, 9, 12))

    async def fake_resolve(_url: str, **_kwargs: object) -> ResolvedMedia:
        return media

    @contextlib.asynccontextmanager
    async def fake_session(**_kwargs: object) -> AsyncIterator[object]:
        yield object()

    async def fake_fetch(_session: object, *, dest: Path, **_kwargs: object) -> tuple[Path, int]:
        dest.write_bytes(b"x")
        return dest, 1

    monkeypatch.setattr(downloader_mod, "resolve", fake_resolve)
    monkeypatch.setattr(downloader_mod, "guarded_session", fake_session)
    monkeypatch.setattr(fetcher_mod, "fetch_to_file", fake_fetch)

    fetched = await Downloader().fetch(media.source_url, into=tmp_path)

    assert [fetched.names[one] for one in fetched.files] == [
        NameFacts(id="DQmExampl3C", n=1, title="A caption", posted=date(2026, 9, 12)),
        NameFacts(id="DQmExampl3C", n=2, title="A caption", posted=date(2026, 9, 12)),
    ]


async def test_two_tool_runs_do_not_list_the_first_runs_file_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every run reads back the whole directory, so the second run's listing holds the first's
    file as well. Each file is handed over once."""
    runs = iter(["one [a1].mp4", "two [b2].mp4"])

    async def fake_run(_argv: list[str], **_kwargs: object) -> SubprocessResult:
        (tmp_path / next(runs)).write_bytes(b"x")
        return SubprocessResult(0, "", "")

    media = _media(
        [
            ResolvedItem.subprocess(source_url="https://site.example/a1", backend="ytdlp"),
            ResolvedItem.subprocess(source_url="https://site.example/b2", backend="ytdlp", index=1),
        ],
        site="Reddit",
        post_id="1abc23",
    )

    async def fake_resolve(_url: str, **_kwargs: object) -> ResolvedMedia:
        return media

    monkeypatch.setattr(downloader_mod, "resolve", fake_resolve)
    monkeypatch.setattr(subproc, "run", fake_run)

    fetched = await Downloader().fetch(media.source_url, into=tmp_path)

    assert fetched.files == [tmp_path / "one [a1].mp4", tmp_path / "two [b2].mp4"]
    assert [fetched.names[one].id for one in fetched.files] == ["a1", "b2"]


# --- Discord: the posting time is in the attachment's own address ---------------------------------


def test_a_discord_attachment_says_when_it_was_posted() -> None:
    """The attachment ID is a Discord ID, whose top bits are the moment. This one is the worked
    example Discord's own reference gives for the format."""
    from sift.slices.download.sources import discord

    url = "https://cdn.discordapp.com/attachments/1/175928847299117063/image0.jpg?ex=1&hm=2"
    posted = discord.posted_from_link(url)
    assert posted == datetime(2016, 4, 30, 11, 18, 25, 796000, tzinfo=UTC)


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example/attachments/1/175928847299117063/image0.jpg",  # not Discord
        "https://cdn.discordapp.com/emojis/175928847299117063.png",  # not an attachment
        "https://cdn.discordapp.com/attachments/1/notanumber/image0.jpg",
    ],
)
def test_an_address_that_is_not_a_discord_attachment_says_nothing(url: str) -> None:
    from sift.slices.download.sources import discord

    assert discord.posted_from_link(url) is None


@pytest.mark.parametrize("digits", [40, 400, 5000])
def test_an_attachment_id_too_large_to_be_a_moment_says_nothing(digits: int) -> None:
    """The address is pasted by a person and is not Discord's to vouch for. An ID past any moment
    a clock can hold (or past what a float, or `int` itself, will take) is no posting time, and
    never a failed download: the name is a convenience."""
    from sift.slices.download.sources import discord

    url = f"https://cdn.discordapp.com/attachments/1/{'9' * digits}/image0.jpg"
    assert discord.posted_from_link(url) is None


def test_a_posting_moment_past_what_the_clock_holds_falls_back_to_the_day() -> None:
    """The tool's `timestamp` is a number off the wire; one no clock can hold is not the moment,
    and the day beside it still is. A day that is not a calendar day is no day at all."""
    assert argv._posted(10**20, "20240131") == date(2024, 1, 31)
    assert argv._posted(10**20, None) is None
    assert argv._posted(None, "20241399") is None


async def test_a_discord_file_is_handed_over_with_its_posting_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the whole fetch: the tool says nothing about a plain file, and the address does."""
    staged = tmp_path / "image0 [image0].jpg"
    staged.write_bytes(b"x")
    _facts_file(tmp_path, {"id": "image0", "extractor_key": "Generic", "filepath": str(staged)})
    monkeypatch.setattr(subproc, "run", _returns(SubprocessResult(0, "", "")))

    fetched = await Downloader().fetch(
        "https://cdn.discordapp.com/attachments/1/175928847299117063/image0.jpg", into=tmp_path
    )

    assert fetched.names[staged] == NameFacts(
        posted=datetime(2016, 4, 30, 11, 18, 25, 796000, tzinfo=UTC)
    )
