# SPDX-License-Identifier: AGPL-3.0-or-later
"""The URL normaliser: the canonical form, and the wider cleaning a record's address gets.

The golden table is the control: two spellings of one link reduce to one string, or the ledger
reports a re-drop as new, and two different links do not. `normalize_url` feeds the ledger key, so
what it strips is fixed for ever; `cleaned_for_record` strips one thing more for a field on a
screen.
"""

from __future__ import annotations

import pytest

from sift.kernel.urls import (
    about_somebody,
    cleaned_for_record,
    is_a_creator_store,
    is_a_reference_page,
    normalize_url,
    scene_page,
    username_in,
    without_signature,
)

# Each pair is (input, canonical form). The right-hand side is what the input must reduce to.
_SAME = [
    ("https://example.com/watch/123", "https://example.com/watch/123"),
    # Case in the scheme and host, never in the path.
    ("HTTPS://Example.COM/watch/123", "https://example.com/watch/123"),
    # A trailing slash on a real path.
    ("https://example.com/watch/123/", "https://example.com/watch/123"),
    # A default port is not part of identity.
    ("https://example.com:443/watch/123", "https://example.com/watch/123"),
    ("http://example.com:80/watch/123", "http://example.com/watch/123"),
    # The fragment is a place in a page, not a resource.
    ("https://example.com/watch/123#t=42", "https://example.com/watch/123"),
    # Tracking parameters go; a meaningful one stays.
    ("https://example.com/watch?v=123&utm_source=feed", "https://example.com/watch?v=123"),
    ("https://example.com/p/abc?igshid=xyz&s=20", "https://example.com/p/abc"),
    # What remains is ordered, so two orderings are one string.
    ("https://example.com/w?b=2&a=1", "https://example.com/w?a=1&b=2"),
]


@pytest.mark.parametrize(("raw", "canonical"), _SAME, ids=[case[0] for case in _SAME])
def test_normalises_to_the_canonical_form(raw: str, canonical: str) -> None:
    assert normalize_url(raw) == canonical


def test_a_non_default_port_is_kept() -> None:
    assert normalize_url("https://example.com:8443/x") == "https://example.com:8443/x"


def test_the_root_path_keeps_its_slash() -> None:
    assert normalize_url("https://example.com/") == "https://example.com"


def test_a_query_of_only_tracking_loses_its_query() -> None:
    assert normalize_url("https://example.com/x?utm_campaign=a") == "https://example.com/x"


# --- signed links: an expiry and signature regenerated on every page load name one file

_SIGNED = (
    "https://cdn.discordapp.com/attachments/1100000000000000001/1200000000000000002/clip.mp4"
    "?ex=6a000001&is=6a000000&hm=abcdefabcd1234567890abcdef1234567890abcdef1234567890abcdef123456&"
)
_REISSUED = (
    "https://cdn.discordapp.com/attachments/1100000000000000001/1200000000000000002/clip.mp4"
    "?ex=7b0011ff&is=7b00ff11&hm=00000000001234567890abcdef1234567890abcdef1234567890abcdef123456&"
)


def test_a_signed_link_normalises_to_the_file_it_names() -> None:
    assert normalize_url(_SIGNED) == (
        "https://cdn.discordapp.com/attachments/1100000000000000001/1200000000000000002/clip.mp4"
    )


def test_the_same_names_elsewhere_are_kept() -> None:
    # `ex` and `is` are ordinary words. Dropping them everywhere could change which media a link
    # resolves to, which is the failure the denylist is written conservatively to avoid.
    assert normalize_url("https://example.com/x?ex=1&is=2") == "https://example.com/x?ex=1&is=2"


def test_the_address_shown_loses_the_signature_and_nothing_else() -> None:
    assert without_signature(_SIGNED) == (
        "https://cdn.discordapp.com/attachments/1100000000000000001/1200000000000000002/clip.mp4"
    )


def test_an_ordinary_address_is_shown_exactly_as_it_is() -> None:
    # Returned unchanged rather than rebuilt, so nothing about an untouched link can be reordered
    # or re-encoded on the way to a screen.
    assert without_signature("https://example.com/x?a=1&b=2") == "https://example.com/x?a=1&b=2"


# --- the record's address -----------------------------------------------------------------------


def test_the_record_loses_from_and_the_ledger_key_does_not() -> None:
    """IMPORTANT: `from` is taken off the record's field and NOT off the ledger key, which would
    re-key every stored URL carrying one and download those files again."""
    address = "https://example.com/watch/1?from=downloads&page=2"

    assert cleaned_for_record(address) == "https://example.com/watch/1?page=2"
    assert normalize_url(address) == "https://example.com/watch/1?from=downloads&page=2"


def test_the_record_does_everything_the_canonical_form_does() -> None:
    """One more parameter, not a different set of rules."""
    messy = "HTTPS://Example.COM/watch/123/?utm_source=feed&b=2&a=1#t=42"

    assert cleaned_for_record(messy) == "https://example.com/watch/123?a=1&b=2"


def test_a_signed_address_keeps_its_signature_on_a_record() -> None:
    """Per-parameter, never "drop the query". A signed host's link 404s without its signature, and
    this field is one somebody clicks."""
    signed = "https://cdn.discordapp.com/attachments/1/2/clip.mp4?ex=6a000001&is=6a000000&hm=abc"

    assert "hm=abc" in cleaned_for_record(signed)


def test_nothing_in_is_nothing_out() -> None:
    """A file Sift did not fetch has no address, and that is not something to raise about."""
    assert cleaned_for_record("") == ""
    assert cleaned_for_record("   ") == ""


# --- the name somebody goes by, read off the address of their page --------------------------------

# (address, the name in it). Blank means the address carries no name and is kept as a link.
_NAMES = [
    # The last piece, on nearly every site, with the query and the fragment gone.
    ("https://example.com/models/esmewren", "esmewren"),
    ("https://example.com/esmewren?lang=en#top", "esmewren"),
    ("https://example.com/esmewren/", "esmewren"),
    # A page TAB is which part of the page is showing, never whose page it is, however many.
    ("https://fansly.com/esmewren/posts", "esmewren"),
    ("https://www.reddit.com/user/esmewren/submitted/?sort=top", "esmewren"),
    ("https://www.pornhub.com/pornstar/esmewren/About", "esmewren"),
    ("https://example.com/esmewren/store/items", "esmewren"),
    ("https://example.com/videos", ""),
    # YouTube: the name is in `@name`, `c/name`, `user/name` or the old bare `name`...
    ("https://www.youtube.com/@esmewren", "@esmewren"),
    ("https://www.youtube.com/@esmewren/featured", "@esmewren"),
    ("https://www.youtube.com/c/esmewren/videos", "esmewren"),
    ("https://m.youtube.com/user/esmewren", "esmewren"),
    ("https://www.youtube.com/esmewren", "esmewren"),
    # ...and never in a channel's id, a route of the site's own, or a `c/` with nothing after it.
    ("https://www.youtube.com/channel/UCesmewrenfield00000000A", ""),
    ("https://www.youtube.com/channel/UCesmewrenfield00000000A/about", ""),
    ("https://www.youtube.com/watch?v=abc", ""),
    ("https://www.youtube.com/c", ""),
    ("https://www.youtube.com/c/esmewren/x/y", "esmewren"),
    # ManyVids puts the name after the site's number, or after `Activity`, and never last.
    ("https://www.manyvids.com/Profile/1001/esmewren/Store/Videos/", "esmewren"),
    ("https://www.manyvids.com/Profile/1001/esmewren/Clips/2002/a-title", "esmewren"),
    ("https://www.manyvids.com/Activity/esmewren/1001/club", "esmewren"),
    ("https://www.manyvids.com/Profile/1001", ""),
    ("https://www.manyvids.com/Activity", ""),
    ("https://www.manyvids.com/esmewren", "esmewren"),
    # A numbered slug answers with the name after the number; a short number is part of a name.
    ("https://www.mydirtyhobby.com/profil/4206129-esmewren", "esmewren"),
    ("https://example.com/4-ever-esme", "4-ever-esme"),
    # Nothing, a number, a file, a path through a script, a record id: no name at all.
    ("https://example.com/", ""),
    ("https://example.com/1234", ""),
    ("https://example.com/index.html", ""),
    ("https://example.com/details.php/id/s00321", ""),
    ("https://example.com/performers/3a9e7c1d-5b2f-4d8e-8c6a-0f1e2d3c4b5a", ""),
]


@pytest.mark.parametrize(("address", "name"), _NAMES)
def test_the_name_in_an_address(address: str, name: str) -> None:
    assert username_in(address) == name


def test_a_reference_database_is_matched_on_its_host_and_the_hosts_below_it() -> None:
    assert is_a_reference_page("https://www.themoviedb.org/person/1000017-esme-wrenfield")
    assert is_a_reference_page("https://themoviedb.org/person/1")
    assert not is_a_reference_page("https://notthemoviedb.org/person/1")
    assert not is_a_reference_page("https://fansly.com/esmewren")
    assert not is_a_reference_page("not an address")


def test_a_creator_store_is_matched_on_its_host_and_the_hosts_below_it() -> None:
    """A creator's own page on a store is a home of hers, told apart from a social page: the host
    or a domain below it, never a host that only ends in the same letters."""
    assert is_a_creator_store("https://www.manyvids.com/Profile/1/esmewren/Store/Videos")
    assert is_a_creator_store("https://onlyfans.com/esmewrenfield")
    assert not is_a_creator_store("https://notmanyvids.com/x")
    assert not is_a_creator_store("https://www.themoviedb.org/person/1")
    assert not is_a_creator_store("not an address")


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ({"name": "Third-party databases"}, True),
        ({"name": "Databases"}, True),
        ({"name": "Other stash-boxes"}, True),
        ({"name": "Social media"}, False),
        ({"name": None}, False),
        ("Databases", False),
        (None, False),
    ],
)
def test_a_boxs_own_category_says_a_page_is_about_somebody(
    category: object, expected: bool
) -> None:
    assert about_somebody("https://refs.example.test/people/esmewren", category) is expected


def test_a_known_reference_host_is_about_somebody_whatever_its_category() -> None:
    assert about_somebody("https://www.iafd.com/person/x", {"name": "Social media"})
    assert about_somebody("https://www.babepedia.com/babe/x")
    assert about_somebody("https://javstash.org/performers/x")


def test_a_stash_box_scene_page_is_on_the_box_it_came_from() -> None:
    """The three boxes whose pages Sift knows, found by the host their API answers on, with or
    without `www.`."""
    assert scene_page("https://stashdb.org/graphql", "a1") == "https://stashdb.org/scenes/a1"
    assert scene_page("https://www.fansdb.cc/graphql", "b2") == "https://fansdb.cc/scenes/b2"
    assert scene_page("https://pmvstash.org/graphql", "c3") == "https://pmvstash.org/scenes/c3"


def test_a_box_whose_pages_are_not_known_gets_no_address() -> None:
    """A self-hosted box may serve its pages anywhere, and a host that merely CONTAINS a known one
    is not it: a link that lands on a missing page is worse than none."""
    assert scene_page("https://stash.example.test/graphql", "a1") is None
    assert scene_page("https://notstashdb.org/graphql", "a1") is None
    assert scene_page("https://stashdb.org.example.test/graphql", "a1") is None


def test_a_match_with_no_scene_id_has_no_page() -> None:
    assert scene_page("https://stashdb.org/graphql", None) is None
    assert scene_page("https://stashdb.org/graphql", "") is None


def test_a_scene_id_is_quoted_before_it_goes_into_an_address() -> None:
    """The id is another service's word: a slash or a query in it stays inside the one segment."""
    assert (
        scene_page("https://stashdb.org/graphql", "a/b?c") == "https://stashdb.org/scenes/a%2Fb%3Fc"
    )
