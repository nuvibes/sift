# SPDX-License-Identifier: AGPL-3.0-or-later
"""The box's own page for one of its entries, for the boxes whose pages Sift knows.

What a record's stash-box section presses through to. The things worth defending are the ones the
screen cannot see: a creator on a box whose studios are the creators lives under `/studios/`
there; a box Sift does not know the pages of gets no page rather than a guess; and the page is on
the same site as the one History opens for that box's scenes, because both read one table.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from sift.kernel.records import Subject
from sift.kernel.urls import scene_page
from sift.slices.stash_boxes.known_boxes import SITES_ARE_PEOPLE, SITES_ARE_SITES
from sift.slices.stash_boxes.service import BoxView, entry_page

pytestmark = pytest.mark.unit

AN_ID = "0b5d3c1e-7f2a-4c9e-9a41-2d6f8e1b3a70"


def _box(endpoint: str, sites_are: str = SITES_ARE_SITES) -> BoxView:
    return BoxView(
        id="box",
        name="A box",
        endpoint=endpoint,
        enabled=True,
        has_key=True,
        route=None,
        requests_per_minute=60,
        sites_are=sites_are,
    )


@pytest.mark.parametrize(
    ("subject", "shelf"),
    [(Subject.PERSON, "performers"), (Subject.SITE, "studios"), (Subject.TAG, "tags")],
)
def test_each_kind_is_on_the_boxs_own_shelf_for_it(subject: Subject, shelf: str) -> None:
    page = entry_page(_box("https://stashdb.org/graphql"), subject, AN_ID)

    assert page == f"https://stashdb.org/{shelf}/{AN_ID}"


def test_a_creator_on_a_box_whose_studios_are_the_creators_is_a_studio_there() -> None:
    """The fact the screen never holds: linked as a person here, filed as a studio there."""
    box = _box("https://pmvstash.org/graphql", SITES_ARE_PEOPLE)

    assert entry_page(box, Subject.PERSON, AN_ID) == f"https://pmvstash.org/studios/{AN_ID}"
    assert entry_page(box, Subject.TAG, AN_ID) == f"https://pmvstash.org/tags/{AN_ID}"


def test_the_page_is_on_the_site_history_opens_for_the_same_boxs_scenes() -> None:
    """One table says where a box's site is, so an entry and a scene can never be on two sites."""
    for endpoint in ("https://stashdb.org/graphql", "https://www.fansdb.cc/graphql"):
        entry = urlsplit(entry_page(_box(endpoint), Subject.TAG, AN_ID) or "")
        scene = urlsplit(scene_page(endpoint, AN_ID) or "")

        assert (entry.scheme, entry.netloc) == (scene.scheme, scene.netloc) != ("", "")


def test_nothing_typed_into_the_boxs_address_reaches_the_page() -> None:
    """Every signed-in viewer reads this answer; the address a box was added with is an admin's
    setting, and anything written into it beside the host stays there."""
    # A userinfo, a port, a path, a query and a fragment beside the host: every one stays
    # behind. (A name-and-password form reads as an address to the secrets scanner.)
    box = _box("https://noreply@stashdb.org:8443/graphql?x=1#y")

    assert entry_page(box, Subject.SITE, "s1") == "https://stashdb.org/studios/s1"


def test_an_id_is_one_segment_of_the_page_whatever_it_holds() -> None:
    page = entry_page(_box("https://stashdb.org/graphql"), Subject.PERSON, "../admin?x=1")

    assert page == "https://stashdb.org/performers/..%2Fadmin%3Fx%3D1"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://10.0.0.5:9998/graphql",
        "https://notstashdb.org/graphql",
        "https://stashdb.org.example/graphql",
        "not an address",
    ],
)
def test_a_box_whose_pages_sift_does_not_know_gets_none(endpoint: str) -> None:
    """A self-hosted box may serve its pages anywhere, and a link to a missing page is worse than
    an id drawn as text: History's rule for a scene, kept for an entry."""
    assert entry_page(_box(endpoint), Subject.PERSON, AN_ID) is None


def test_a_kind_no_box_keeps_has_no_page() -> None:
    assert entry_page(_box("https://stashdb.org/graphql"), Subject.ASSET, AN_ID) is None
