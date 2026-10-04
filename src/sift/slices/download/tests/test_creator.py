# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the uploader off a page that does not put them in the address.

The shapes below are the real ones, checked against live pages rather than imagined. The first is
a site whose address is a title and an id, whose downloads would otherwise arrive filed under the
site and nobody, and whose page carries a perfectly good `VideoObject` with a `creator` on it.
"""

from __future__ import annotations

import pytest

from sift.slices.download.sources import curl as curl_mod
from sift.slices.download.sources.creator import creator_in_page, creator_of

# The shape a video page publishes: an Organization for the site, then the media object naming its
# creator as a Person. Both blocks matter: the first is what stops the site's own name being read
# as somebody's.
REAL_SHAPE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization","name":"PMVHaven",
 "url":"https://pmvhaven.com"}
</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"VideoObject","name":"Some Title",
 "duration":"PT4M26S",
 "creator":{"@type":"Person","name":"quillmoss","url":"https://pmvhaven.com/profile/quillmoss"}}
</script>
</head><body></body></html>
"""

SITE = "https://pmvhaven.com/video/some-title_0123456789abcdef01234567"


def test_the_creator_is_read_off_a_page_whose_address_carries_no_username() -> None:
    assert creator_in_page(REAL_SHAPE, url=SITE) == "quillmoss"


def test_a_page_with_no_metadata_at_all_names_nobody() -> None:
    assert creator_in_page("<html><body>a video</body></html>", url=SITE) is None


def test_a_block_that_is_not_json_is_stepped_over_rather_than_raising() -> None:
    """One malformed block on a page must not cost the good one beside it."""
    broken = REAL_SHAPE.replace('{"@context":"https://schema.org","@type":"Organization"', "{oh no")
    assert creator_in_page(broken, url=SITE) == "quillmoss"


def test_an_organization_author_is_not_a_person() -> None:
    """The author of everything on a magazine's site is the magazine. Filing every file under a
    Person named after the publisher is worse than filing them under nobody."""
    page = REAL_SHAPE.replace(
        '"@type":"Person","name":"quillmoss"', '"@type":"Organization","name":"quillmoss"'
    )
    assert creator_in_page(page, url=SITE) is None


def test_a_person_named_after_the_site_is_refused() -> None:
    """Some pages declare a Person whose name is the brand. It is caught by the Organization the
    same page declares."""
    page = REAL_SHAPE.replace('"name":"quillmoss","url"', '"name":"PMVHaven","url"')
    assert creator_in_page(page, url=SITE) is None


def test_a_person_named_after_the_host_is_refused_with_no_organization_block() -> None:
    """And caught by the host when the page declares no organization at all, which is the case
    the Organization check alone would miss."""
    page = """
    <script type="application/ld+json">
    {"@type":"VideoObject","creator":{"@type":"Person","name":"Pmvhaven"}}
    </script>
    """
    assert creator_in_page(page, url=SITE) is None


def test_an_author_inside_a_graph_is_found() -> None:
    """`@graph` is the third legal way to wrap these and is as common as the other two. A reader
    that handles only the shape it first met works on one site and finds nothing on the next."""
    page = """
    <script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[
      {"@type":"WebSite","name":"Somewhere"},
      {"@type":"VideoObject","author":{"@type":"Person","name":"Ada Lovelace"}}]}
    </script>
    """
    assert creator_in_page(page, url="https://somewhere.example/v/1") == "Ada Lovelace"


def test_a_bare_string_author_is_not_trusted() -> None:
    """`"author": "Some Name"` has not said whether that is a human or the publication, and that
    distinction is the whole guard. Unlabelled is unknown, not assumed."""
    page = """
    <script type="application/ld+json">
    {"@type":"VideoObject","author":"Some Name"}
    </script>
    """
    assert creator_in_page(page, url="https://somewhere.example/v/1") is None


def test_an_article_author_is_not_read() -> None:
    """An Article has a journalist and a Product has a manufacturer. Neither is the sense of
    "who made this" that a media library files things under."""
    page = """
    <script type="application/ld+json">
    {"@type":"NewsArticle","author":{"@type":"Person","name":"A Reporter"}}
    </script>
    """
    assert creator_in_page(page, url="https://somewhere.example/v/1") is None


def test_a_sentence_in_the_author_field_is_not_a_username() -> None:
    long_name = "a" * 200
    page = f"""
    <script type="application/ld+json">
    {{"@type":"VideoObject","author":{{"@type":"Person","name":"{long_name}"}}}}
    </script>
    """
    assert creator_in_page(page, url="https://somewhere.example/v/1") is None


@pytest.mark.parametrize("kind", ["VideoObject", "ImageObject", "AudioObject"])
def test_every_media_type_is_read(kind: str) -> None:
    page = (
        '<script type="application/ld+json">'
        f'{{"@type":"{kind}","creator":{{"@type":"Person","name":"Ada"}}}}'
        "</script>"
    )
    assert creator_in_page(page, url="https://somewhere.example/v/1") == "Ada"


def test_a_top_level_list_of_blocks_is_read_the_same_as_one_object() -> None:
    """The second of the three legal wrappings. A page may publish a bare array where another
    publishes one object, and both are correct: a reader that handles only one finds nothing on
    the other and files the download under nobody."""
    page = """
    <script type="application/ld+json">
    [{"@type":"Organization","name":"Somewhere"},
     {"@type":"VideoObject","creator":{"@type":"Person","name":"Grace Hopper"}}]
    </script>
    """
    assert creator_in_page(page, url="https://somewhere.example/v/1") == "Grace Hopper"


def test_something_that_is_not_an_object_at_all_is_passed_over() -> None:
    """A list of strings, or a number where a node was expected. This is somebody else's markup and
    it is regularly wrong, so anything that is not a node is skipped rather than reached into."""
    page = """
    <script type="application/ld+json">
    ["just a string", 7, {"@type":"VideoObject","creator":{"@type":"Person","name":"Ada"}}]
    </script>
    """
    assert creator_in_page(page, url="https://somewhere.example/v/1") == "Ada"


def test_a_type_written_as_a_list_is_read() -> None:
    """`@type` is legally a string OR a list, and pages use both. A node typed
    `["VideoObject", "Clip"]` is a video, and a reader that only understood the string form would
    pass over it."""
    page = """
    <script type="application/ld+json">
    {"@type":["VideoObject","Clip"],"creator":{"@type":["Person","Thing"],"name":"Ada"}}
    </script>
    """
    assert creator_in_page(page, url="https://somewhere.example/v/1") == "Ada"


def test_a_list_of_authors_takes_the_first_that_is_a_person() -> None:
    """Pages name a studio and an uploader in one field. The Person is the one a library files
    under, and it is not always first."""
    page = """
    <script type="application/ld+json">
    {"@type":"VideoObject","author":[
      {"@type":"Organization","name":"A Studio"},
      {"@type":"Person","name":"Ada"}]}
    </script>
    """
    assert creator_in_page(page, url="https://somewhere.example/v/1") == "Ada"


def test_a_list_of_authors_with_no_person_in_it_names_nobody() -> None:
    """The half that stops the test above passing on a reader that takes whatever is last."""
    page = """
    <script type="application/ld+json">
    {"@type":"VideoObject","author":[
      {"@type":"Organization","name":"A Studio"},
      {"@type":"Organization","name":"Another"}]}
    </script>
    """
    assert creator_in_page(page, url="https://somewhere.example/v/1") is None


def test_an_address_with_no_host_to_read_still_answers() -> None:
    """The site's own name is normally excluded by its host. There is no host here, so there is
    nothing to exclude, and the read must go on rather than fall over on the missing label."""
    page = """
    <script type="application/ld+json">
    {"@type":"VideoObject","creator":{"@type":"Person","name":"Ada"}}
    </script>
    """
    assert creator_in_page(page, url="not-a-url-at-all") == "Ada"


# --- fetching the page, which is allowed to fail and never to raise --------------------------------


class _Fetched:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


async def test_a_page_that_answers_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def guarded_get(url: str, **_: object) -> _Fetched:
        return _Fetched(200, REAL_SHAPE)

    monkeypatch.setattr(curl_mod, "guarded_get", guarded_get)

    assert await creator_of(SITE) == "quillmoss"


async def test_a_page_that_answers_with_anything_but_success_names_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def guarded_get(url: str, **_: object) -> _Fetched:
        return _Fetched(404, REAL_SHAPE)

    monkeypatch.setattr(curl_mod, "guarded_get", guarded_get)

    assert await creator_of(SITE) is None


async def test_a_page_that_will_not_be_fetched_at_all_names_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Swallowed on purpose, including the guard's own refusal. This decorates a download that has
    already landed, and there is no failure of it worth surfacing to somebody who wanted the file."""

    async def guarded_get(url: str, **_: object) -> _Fetched:
        raise RuntimeError("the address resolves somewhere the server may not be pointed at")

    monkeypatch.setattr(curl_mod, "guarded_get", guarded_get)

    assert await creator_of(SITE) is None


# The head of a signed-out Instagram post page: no schema.org block, and its own address names the
# account the bare link did not.
INSTAGRAM_HEAD = """
<html><head>
<meta property="og:type" content="article" />
<meta property="og:url" content="https://www.instagram.com/quillmoss/p/CxY7kLm2PqR/" />
<link rel="canonical" href="https://www.instagram.com/p/CxY7kLm2PqR/" />
</head><body></body></html>
"""
BARE_POST = "https://www.instagram.com/p/CxY7kLm2PqR/"


def test_a_bare_post_link_is_named_by_the_address_its_page_gives_itself() -> None:
    assert creator_in_page(INSTAGRAM_HEAD, url=BARE_POST) == "quillmoss"
    reel = "https://www.instagram.com/reel/CxY7kLm2PqR/?igsh=x"
    assert creator_in_page(INSTAGRAM_HEAD, url=reel) == "quillmoss"


def test_a_page_address_on_another_site_or_naming_nobody_names_nobody() -> None:
    elsewhere = INSTAGRAM_HEAD.replace("www.instagram.com/quillmoss", "x.com/quillmoss")
    assert creator_in_page(elsewhere, url=BARE_POST) is None
    bare = INSTAGRAM_HEAD.replace("instagram.com/quillmoss/p/", "instagram.com/p/")
    assert creator_in_page(bare, url=BARE_POST) is None
    # Reddit's username is a board, never a person.
    board = (
        '<meta content="https://www.reddit.com/r/somewhere/comments/1f3xk9a/" property="og:url">'
    )
    assert creator_in_page(board, url="https://www.reddit.com/comments/1f3xk9a/") is None
