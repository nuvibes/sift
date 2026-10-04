# SPDX-License-Identifier: AGPL-3.0-or-later
"""The site catalog, and the invariants that keep a host group, a name, a mirror and a fallback
tool from being written two ways."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

import pytest

from sift.kernel.access.viewer import Viewer
from sift.slices.download import naming
from sift.slices.download.sources import (
    goonbox,
    instagram,
    reddit,
    redgifs,
    registry,
    sites,
    tiktok,
    twitter,
    youtube,
)
from sift.slices.download.sources.registry import classify
from sift.slices.download.sources.sites import catalog
from sift.slices.download.sources.sites.catalog import (
    EVERY_SITE_FILLS,
    SITES,
    by_key,
    match,
    words_filled,
)

#: Every resolver that decides for itself whether it owns an address, against the catalog key whose
#: hosts it must ask about.
_RESOLVER_HOSTS = [
    (tiktok._TIKTOK_HOSTS, "tiktok"),
    (youtube._YOUTUBE_HOSTS, "youtube"),
    (instagram._INSTAGRAM_HOSTS, "instagram"),
    (twitter._X_HOSTS, "x"),
    (reddit._REDDIT_HOSTS, "reddit"),
    (redgifs._REDGIFS_HOSTS, "redgifs"),
    (goonbox._HOSTS, "goonbox"),
]


def test_no_host_belongs_to_two_sites() -> None:
    """Which record answers would otherwise depend on the order they are written in."""
    seen: dict[str, str] = {}
    for record in catalog.SITES:
        for host in record.hosts:
            assert host not in seen, f"{host} is claimed by both {seen[host]} and {record.key}"
            seen[host] = record.key


def test_every_site_has_its_own_key_and_its_own_name() -> None:
    """Every site has its own key and its own display name."""
    keys = [record.key for record in catalog.SITES]
    sites = [record.site for record in catalog.SITES]
    assert len(keys) == len(set(keys))
    assert len(sites) == len(set(sites))


@pytest.mark.parametrize("record", catalog.SITES, ids=lambda record: record.key)
def test_a_host_reachable_by_one_path_is_known_to_the_other(record: catalog.SiteRecord) -> None:
    """A host one path reaches is known to the other, or it downloads with no site or username."""
    for host in record.hosts:
        url = f"https://{host}/whatever"
        assert registry.match_site(url) is record, f"{host} is not attributed to {record.key}"
        assert registry.classify(url).site == record.site
        if record.extract is not None:
            assert sites.match_site(url) is record, f"{host} has no extractor path"
            assert sites.site_site(url) == record.site


@pytest.mark.parametrize("record", catalog.SITES, ids=lambda record: record.key)
def test_a_site_serving_both_kinds_has_somewhere_to_fall_back_to(
    record: catalog.SiteRecord,
) -> None:
    """A site serving both images and video has a fallback tool, unless its extractor answers."""
    assert record.media, f"{record.key} declares no media kind"
    if record.extract is None and set(record.media) == {"video", "images"}:
        assert record.fallback_backend is not None, (
            f"{record.key} serves both kinds through one tool and names no fallback"
        )
        assert record.fallback_backend is not record.backend


@pytest.mark.parametrize(
    ("declared", "key"), _RESOLVER_HOSTS, ids=[key for _, key in _RESOLVER_HOSTS]
)
def test_a_resolver_asks_about_the_catalog_hosts_and_not_its_own_copy(
    declared: tuple[str, ...], key: str
) -> None:
    assert declared == catalog.hosts_of(key)


def test_a_key_the_catalog_does_not_have_is_an_error_not_an_empty_list() -> None:
    """An empty host group would make a resolver quietly stop handling its own site."""
    with pytest.raises(KeyError):
        catalog.hosts_of("no-such-site")


def test_a_lookalike_domain_borrows_nothing() -> None:
    assert catalog.match("https://evil-bunkr.site/a/1") is None
    assert catalog.match("https://bunkr.site.attacker.test/a/1") is None
    assert catalog.match("https://cdn.bunkr.site/a/1") is not None  # a real subdomain


def test_no_site_key_can_collide_with_the_reserved_route_scope() -> None:
    """The default route is stored beside the per-site ones under a scope that is not a site. If a
    key could ever equal it, one site would silently become everybody's default."""
    from sift.kernel.tunnels import DEFAULT_SCOPE

    for record in catalog.SITES:
        assert record.key != DEFAULT_SCOPE
        assert record.key.replace("-", "").isalnum(), f"{record.key} is not a plain word"


def test_a_site_name_that_matches_nothing_yields_no_record() -> None:
    """The connections screen takes a typed site name, so it can be anything at all."""
    assert catalog.by_site("Something Made Up") is None


def test_a_site_listed_only_for_routing_says_so() -> None:
    """A row in the catalog is not a claim that downloading from the site works.

    Some are there so traffic to them can be given a way out, which needs the address recognised and
    nothing else. Without a field saying which, the matrix would read as support for every site it
    lists, and the difference is exactly what somebody consults it for.
    """
    by_key = {record.key: record for record in SITES}

    assert by_key["pornhub"].supported is True
    # A download watched working moves a record out of this list.
    for key in ("redtube", "xvideos"):
        assert by_key[key].supported is False, key
        # Recognised is the whole point: an address nothing matches cannot be given a route.
        assert by_key[key].hosts


def test_no_site_is_both_untried_and_proven() -> None:
    """No site is `tested` without being `supported`."""
    for record in SITES:
        assert not (record.tested and not record.supported), (
            f"{record.key} is marked tested but not supported"
        )


def test_an_uploader_is_read_from_the_segment_that_names_one() -> None:
    """These sites put the uploader after a named segment rather than at the front of the path, and
    which segment it is says whether it is a person or a studio."""
    assert match("https://www.pornhub.com/model/someone-here") is not None
    found = classify("https://www.pornhub.com/pornstar/someone-here")
    assert found.username == "someone-here"
    assert found.username_is_a_person is True
    # A page that names nobody yields nobody rather than the first thing in the path.
    assert classify("https://www.pornhub.com/view_video.php?viewkey=abc").username is None
    # A path none of whose segments introduces a person.
    assert classify("https://www.pornhub.com/video/search/abc").username is None


def test_every_site_says_whether_cookies_help_on_it() -> None:
    """Every record states `cookies_help` in the source, since a default and a deliberate no look
    alike at run time."""
    tree = ast.parse(Path(catalog.__file__).read_text(encoding="utf-8"))
    records = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SiteRecord"
    ]
    # Every record in the tuple, and nothing built some other way that this would never look at.
    assert len(records) == len(SITES)
    for record in records:
        named = {keyword.arg: keyword.value for keyword in record.keywords}
        key = named.get("key")
        assert isinstance(key, ast.Constant), ast.dump(record)
        assert "cookies_help" in named, key.value


def test_every_site_says_what_it_does_without_cookies() -> None:
    """Every record declares `cookies` and `cookies_why` in its source; a Required or Partial
    need waits for cookies on refusal; any other fails."""
    tree = ast.parse(Path(catalog.__file__).read_text(encoding="utf-8"))
    records = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SiteRecord"
    ]
    assert len(records) == len(SITES)
    for record in records:
        named = {keyword.arg: keyword.value for keyword in record.keywords}
        key = named.get("key")
        assert isinstance(key, ast.Constant), ast.dump(record)
        assert "cookies" in named, key.value
        assert "cookies_why" in named, key.value
    wanting = {catalog.CookieNeed.REQUIRED, catalog.CookieNeed.PARTIAL}
    for site in SITES:
        assert site.cookies_why.strip().endswith("."), site.key
        needs = {site.cookies, site.cookies_with_a_tool} - {None}
        assert site.cookies_help == bool(needs & wanting), site.key


#: What each Site does with no cookies, measured with Sift's default tool. Changing an answer here
#: needs a new measurement.
_MEASURED: dict[str, tuple[str, str | None]] = {
    "tiktok": ("not_needed", "not_needed"),
    "youtube": ("partial", None),
    "instagram": ("not_needed", "required"),
    "x": ("partial", None),
    "redgifs": ("not_needed", None),
    "reddit": ("partial", None),
    "goonbox": ("not_needed", None),
    "pmvhaven": ("not_needed", None),
    "fapello": ("not_needed", None),
    "coomer": ("not_needed", None),
    "kemono": ("not_needed", None),
    "pornhub": ("partial", None),
    "hqporner": ("not_needed", None),
    "redtube": ("not_needed", None),
    "xvideos": ("not_needed", None),
    "bunkr": ("not_needed", None),
    "cyberdrop": ("not_needed", None),
    "cyberfile": ("not_needed", None),
    "gofile": ("not_needed", None),
    "discord": ("not_needed", None),
    "pixeldrain": ("not_needed", None),
    "xbunkr": ("not_needed", None),
    "jpg5": ("not_needed", None),
    "turbovid": ("not_needed", None),
    "saint": ("not_needed", None),
    "imgur": ("not_needed", None),
}


def test_every_site_carries_its_measured_cookie_need_and_none_is_unknown() -> None:
    """Every Site carries one of the three measured cookie needs."""
    assert {need.value for need in catalog.CookieNeed} == {"required", "partial", "not_needed"}
    declared = {
        site.key: (
            site.cookies.value,
            site.cookies_with_a_tool.value if site.cookies_with_a_tool is not None else None,
        )
        for site in SITES
    }
    assert declared == _MEASURED


def test_the_need_that_counts_is_the_one_for_the_method_in_force() -> None:
    """Instagram through Sift's own method needs nothing; pointed at a tool, every post needs
    cookies. A Site with one answer gives it either way."""
    instagram = catalog.by_site("Instagram")
    youtube = catalog.by_site("YouTube")
    assert instagram is not None and youtube is not None
    assert catalog.cookie_need(instagram, tool_chosen=False) is catalog.CookieNeed.NOT_NEEDED
    assert catalog.cookie_need(instagram, tool_chosen=True) is catalog.CookieNeed.REQUIRED
    assert catalog.cookie_need(youtube, tool_chosen=True) is catalog.CookieNeed.PARTIAL


#: A site's own word for a state, said on screen in what it means.
_SITE_JARGON = ("quarantin",)


def test_a_sites_reason_for_cookies_is_said_in_plain_words() -> None:
    """The line under a Site's cookie need says what it means, never the site's own jargon."""
    for site in SITES:
        said = site.cookies_why.lower()
        assert not any(word in said for word in _SITE_JARGON), (site.key, site.cookies_why)
    reddit_site = catalog.by_site("Reddit")
    assert reddit_site is not None
    assert reddit_site.cookies_why == (
        "Communities behind a content warning, and private ones, need cookies."
    )


# --- What Sift names each Site's files when nobody typed a rule -------------------------------

#: The shipped name per Site. EMPTY keeps the name the file arrived with.
_SHIPPED_NAMING: dict[str, str] = {
    "tiktok": "{creator} - {posted} - {id}",
    "youtube": "{creator} - {name}",
    "instagram": "{creator} - {id}",
    "x": "{creator} - {name}",
    "redgifs": "{creator} - {id}",
    "reddit": "{site} - {title}",
    "goonbox": "{creator} - {name}",
    "pmvhaven": "{creator} - {name}",
    "fapello": "{creator} - {name}",
    "coomer": "",
    "kemono": "",
    "pornhub": "",
    "hqporner": "",
    "redtube": "",
    "xvideos": "",
    "bunkr": "",
    "cyberdrop": "",
    "cyberfile": "",
    "gofile": "",
    "discord": "",
    "pixeldrain": "",
    "xbunkr": "",
    "jpg5": "",
    "turbovid": "",
    "saint": "",
    "imgur": "{site} - {id}",
}


def test_every_site_ships_the_name_it_was_given() -> None:
    assert {site.key: site.default_naming for site in SITES} == _SHIPPED_NAMING


@pytest.mark.parametrize("record", catalog.SITES, ids=lambda record: record.key)
def test_a_shipped_name_uses_only_words_its_site_can_fill(record: catalog.SiteRecord) -> None:
    """A shipped name uses only words its Site can fill."""
    used = {word.lower() for word in naming._TOKEN.findall(record.default_naming)}
    assert used <= set(naming.TOKENS), (record.key, used - set(naming.TOKENS))
    assert used <= set(words_filled(record)), (record.key, used - set(words_filled(record)))


def test_the_words_a_site_declares_are_its_own_and_are_real_words() -> None:
    """`name_words` holds only what the Site adds: a word every download fills, or `creator` (which
    follows `username_is_a_person`), declared there as well would be a second answer to one
    question, and a word no template can use would be offered and never filled."""
    assert set(EVERY_SITE_FILLS) <= set(naming.TOKENS)
    for site in SITES:
        own = set(site.name_words)
        assert len(own) == len(site.name_words), site.key
        assert own <= set(naming.TOKENS), (site.key, own - set(naming.TOKENS))
        assert not own & {*EVERY_SITE_FILLS, "creator"}, site.key


def test_a_site_names_a_creator_only_where_it_names_a_person() -> None:
    discord = by_key("discord")
    tiktok = by_key("tiktok")
    assert discord is not None and tiktok is not None
    assert "creator" not in words_filled(discord)
    assert words_filled(tiktok)[:5] == (*EVERY_SITE_FILLS, "creator")


def test_a_key_the_catalog_does_not_have_has_no_record() -> None:
    assert by_key("imgur") is not None
    assert by_key("no-such-site") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://imgur.com/Ab3xY7q",
        "https://i.imgur.com/Ab3xY7q.mp4",
        "https://m.imgur.com/a/Qz81mWc",
        "https://imgur.io/Ab3xY7q",
    ],
)
def test_an_imgur_address_is_the_imgur_site(url: str) -> None:
    """Imgur has a record, so it can be given a rule, and nobody is read from its page."""
    record = match(url)
    assert record is not None and record.key == "imgur"
    attribution = classify(url)
    assert attribution.site == "Imgur"
    assert attribution.username is None
    assert attribution.names_creators is False


def test_imgur_keeps_the_name_its_earlier_downloads_were_filed_under() -> None:
    """The catch-all names a Site from the host. The record's display name is that same word,
    which files a download the catch-all named under it without a step."""
    record = by_key("imgur")
    assert record is not None
    assert registry._site_from_host("imgur.com") == record.site


def test_an_imgur_download_is_named_by_its_site_and_its_id() -> None:
    record = by_key("imgur")
    assert record is not None
    facts = naming.Facts(site=record.site, original="imgur.com", id="Ab3xY7q")
    assert naming.fill(record.default_naming, facts) == "Imgur - Ab3xY7q"


@pytest.mark.anyio
async def test_the_sites_list_carries_each_shipped_name_and_its_words() -> None:
    """What the naming field shows for a blank box, and which words it offers, come from the record:
    one answer, so the screen and the download cannot disagree."""
    from sift.slices.download.router import supported_sites

    listing = {one.key: one for one in await supported_sites(viewer=cast(Viewer, None))}
    assert listing.keys() == {site.key for site in SITES}
    for site in SITES:
        assert listing[site.key].default_naming == site.default_naming
        assert listing[site.key].name_words == list(words_filled(site))
    assert listing["tiktok"].default_naming == "{creator} - {posted} - {id}"
    assert "creator" not in listing["discord"].name_words
