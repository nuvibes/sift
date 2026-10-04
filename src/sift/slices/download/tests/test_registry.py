# SPDX-License-Identifier: AGPL-3.0-or-later
"""The host registry: which tool reads a URL, and what the URL says about who made the media."""

from __future__ import annotations

import pytest

from sift.slices.download.sources.registry import (
    Attribution,
    Backend,
    backend_for,
    classify,
    match_site,
)

_MATCHES = [
    ("https://www.tiktok.com/@someone/video/123", "TikTok", Backend.YTDLP),
    ("https://youtu.be/abc", "YouTube", Backend.YTDLP),
    ("https://www.instagram.com/someone/reel/1", "Instagram", Backend.YTDLP),
    # X leads with gallery-dl: its images are what gallery-dl reads and its video is yt-dlp's, and
    # the catalog names the second tool so whichever finds nothing hands over.
    ("https://x.com/someone/status/1", "X", Backend.GALLERYDL),
    ("https://old.reddit.com/r/x/comments/1", "Reddit", Backend.YTDLP),
    ("https://bunkr.site/a/xyz", "Bunkr", Backend.GALLERYDL),
    ("https://coomer.st/x/user/y", "Coomer", Backend.GALLERYDL),
    ("https://gofile.io/d/abc", "GoFile", Backend.GALLERYDL),
    # Kemono is its own site, not Coomer's second name.
    ("https://kemono.su/patreon/user/1", "Kemono", Backend.GALLERYDL),
    # Mirrors the extractor knows, which the tool registry must know too, or they would resolve
    # and then be filed under a site guessed from the domain.
    ("https://bunkr.cr/a/xyz", "Bunkr", Backend.GALLERYDL),
    ("https://bunkrr.su/a/xyz", "Bunkr", Backend.GALLERYDL),
    ("https://cyberfile.me/abc", "Cyberfile", Backend.GALLERYDL),
    ("https://fapello.su/someone/", "Fapello", Backend.GALLERYDL),
    ("https://jpg5.su/img/abc", "JPG5", Backend.GALLERYDL),
    ("https://turbo.cr/e/abc", "TurboVid", Backend.YTDLP),
    ("https://xbunkr.com/a/abc", "XBunkr", Backend.GALLERYDL),
    ("https://pmvhaven.com/video/some-title_01234567", "PMVHaven", Backend.YTDLP),
    ("https://goonbox.cr/img/abc", "GoonBox", Backend.YTDLP),
]


@pytest.mark.parametrize(("url", "site", "backend"), _MATCHES, ids=[case[0] for case in _MATCHES])
def test_a_known_host_dispatches_to_its_tool_and_site(
    url: str, site: str, backend: Backend
) -> None:
    spec = match_site(url)
    assert spec is not None
    assert spec.site == site
    assert backend_for(url) is backend


def test_an_unknown_host_falls_through_to_the_ytdlp_catch_all() -> None:
    assert match_site("https://vimeo.com/12345") is None
    assert backend_for("https://vimeo.com/12345") is Backend.YTDLP


def test_a_subdomain_matches_but_a_lookalike_does_not() -> None:
    assert match_site("https://m.tiktok.com/@a/video/1") is not None
    assert match_site("https://eviltiktok.com/@a/video/1") is None
    assert match_site("https://tiktok.com.attacker.test/x") is None


def test_the_at_username_is_read_from_the_path() -> None:
    assert classify("https://www.tiktok.com/@creator/video/1") == Attribution(
        site="TikTok", username="creator", username_is_a_person=True, names_creators=True
    )


def test_the_first_segment_is_the_username_where_the_site_puts_it_there() -> None:
    assert classify("https://x.com/someone/status/1") == Attribution(
        site="X", username="someone", username_is_a_person=True, names_creators=True
    )


def test_a_username_that_is_not_a_person_says_so() -> None:
    """Reddit's username is a BOARD. A rule that turned every username into somebody would make a
    person out of each subreddit anybody has ever saved from."""
    assert (
        classify("https://www.reddit.com/r/somewhere/comments/1/x/").username_is_a_person is False
    )


def test_an_unknown_host_never_claims_its_path_is_a_name() -> None:
    """Nothing about an address Sift does not recognize says its first segment is somebody."""
    assert classify("https://example.com/whoever/1").username_is_a_person is False


def test_a_site_whose_usernames_are_people_still_claims_nobody_when_there_is_no_username() -> None:
    """A flag set beside an absent username is a claim about nobody, read later as one about
    somebody."""
    found = classify("https://www.tiktok.com/video/1")
    assert found.username is None and found.username_is_a_person is False


def test_a_site_with_no_username_rule_yields_a_site_and_no_username() -> None:
    assert classify("https://gofile.io/d/abc") == Attribution(site="GoFile", username=None)


def test_an_unknown_host_gets_a_best_effort_site_from_its_domain() -> None:
    assert classify("https://www.vimeo.com/1") == Attribution(
        site="Vimeo", username=None, names_creators=True
    )


def test_a_hostless_or_single_label_url_has_no_site() -> None:
    assert classify("https://localhost/x") == Attribution(
        site=None, username=None, names_creators=True
    )


def test_a_tiktok_url_with_no_at_username_has_no_username() -> None:
    assert classify("https://www.tiktok.com/video/1") == Attribution(
        site="TikTok", username=None, names_creators=True
    )


# Instagram and X put a route word where a profile puts the username.
@pytest.mark.parametrize(
    ("url", "username"),
    [
        ("https://www.instagram.com/somebody/", "somebody"),
        ("https://www.instagram.com/somebody/reel/abc/", "somebody"),
        ("https://www.instagram.com/p/DAbCdEf/", None),
        ("https://www.instagram.com/reel/DAbCdEf/", None),
        ("https://www.instagram.com/tv/DAbCdEf/", None),
        ("https://www.instagram.com/explore/tags/whatever/", None),
        # The username is the segment AFTER `stories`, which is the one route that carries one.
        ("https://www.instagram.com/stories/somebody/123456/", "somebody"),
        ("https://www.instagram.com/stories/", None),
        # The bare site, pasted with nothing after it. There are no segments to read at all, and
        # the first thing this does is index into them.
        ("https://www.instagram.com/", None),
    ],
)
def test_an_instagram_route_word_is_not_somebody(url: str, username: str | None) -> None:
    found = classify(url)
    assert found.site == "Instagram"
    assert found.username == username
    # And the flag never outlives the username: no username means no claim that one names a person.
    assert found.username_is_a_person is (username is not None)


@pytest.mark.parametrize(
    ("url", "username"),
    [
        ("https://x.com/somebody/status/1", "somebody"),
        ("https://x.com/i/status/1", None),
        ("https://x.com/i/web/status/1", None),
        ("https://x.com/home", None),
        ("https://x.com/hashtag/whatever", None),
        ("https://twitter.com/somebody", "somebody"),
    ],
)
def test_an_x_route_word_is_not_somebody(url: str, username: str | None) -> None:
    found = classify(url)
    assert found.site == "X"
    assert found.username == username


# Whether the page is worth asking about a creator, which `username_is_a_person` cannot answer.
@pytest.mark.parametrize(
    ("url", "worth_asking"),
    [
        # No decision has ever been made about an unknown site. Its page is the only thing that
        # can say, and this is the case the whole thing was built for.
        ("https://example-host.test/video/1", True),
        # A creator site whose addresses never carry a username. It has no username rule at all, so
        # the page is the only thing that can name the uploader, which is what this flag is for.
        ("https://pmvhaven.com/video/some-title_01234567", True),
        # A creator site whose address happens not to carry the username. Same site, same people.
        ("https://www.instagram.com/p/DAbCdEf/", True),
        ("https://www.tiktok.com/video/1", True),
        # Reddit yields a BOARD on purpose. Going to the page to find a human overrides that.
        ("https://www.reddit.com/r/somewhere/comments/1/x/", False),
        # A file host's uploader is a bucket.
        ("https://gofile.io/d/abc", False),
    ],
)
def test_whether_a_site_is_worth_asking_for_a_creator(url: str, worth_asking: bool) -> None:
    assert classify(url).names_creators is worth_asking


def test_a_redgifs_profile_names_its_uploader() -> None:
    """A RedGIFs profile link names its uploader."""
    found = classify("https://www.redgifs.com/users/somecreator")
    assert found.site == "RedGIFs"
    assert found.username == "somecreator"
    assert found.username_is_a_person is True


def test_a_redgifs_clip_names_nobody_from_its_address() -> None:
    """`/watch/<id>` identifies a clip, not a person. Who posted it is on the page and in the site's
    own API, which is where the resolver reads it, so the address yields nothing rather than a
    person called "watch"."""
    found = classify("https://www.redgifs.com/watch/somecodename")
    assert found.site == "RedGIFs"
    assert found.username is None
    assert found.username_is_a_person is False


def test_a_content_network_is_named_after_the_service_it_serves() -> None:
    # Two attachment hosts for one service. A row saying `cdn.discordapp.com` would be naming a
    # machine, and it has to read the same as the name the tunnel routing lists the site under.
    for address in (
        "https://cdn.discordapp.com/attachments/1/2/clip.mp4?ex=1&is=2&hm=3",
        "https://media.discordapp.net/attachments/1/2/still.png",
    ):
        assert classify(address).site == "Discord"
        assert match_site(address) is not None


def test_a_conversation_is_not_an_attachment() -> None:
    """A Discord channel address matches no record: reading one needs an authenticated client."""
    channel = "https://discord.com/channels/1/2"
    assert match_site(channel) is None
    assert classify(channel).site == "Discord"


def test_a_lookalike_host_is_not_the_service() -> None:
    assert classify("https://notdiscordapp.com/x").site == "Notdiscordapp"


def test_a_country_registry_is_not_the_site_name() -> None:
    # `labels[-2]` is the registry on a two-label ending, so read naively every UK, Australian and
    # Japanese address would be `Co` on a row.
    assert classify("https://videos.example.co.uk/x").site == "Example"
    assert classify("https://example.com.au/x").site == "Example"


def test_an_instagram_share_link_names_nobody() -> None:
    """`/s/<code>` is a share shortlink and the code is opaque.

    Read as a username it would make one out of a base64 blob, and on a site whose usernames
    become People, a person named after one too.
    """
    assert classify("https://www.instagram.com/s/AGVzdGlueQ==/").username is None


def test_an_instagram_highlight_names_nobody() -> None:
    """The segment after `stories` is usually the username, and for a highlight it is the literal
    word `highlights`. Read as a username it would produce a Person called Highlights."""
    assert (
        classify("https://www.instagram.com/stories/highlights/17900000000000000/").username is None
    )


def test_an_instagram_story_still_names_its_username() -> None:
    """The case the two above must not break: a story tray IS the person's."""
    assert classify("https://www.instagram.com/stories/quillmoss/123/").username == ("quillmoss")
