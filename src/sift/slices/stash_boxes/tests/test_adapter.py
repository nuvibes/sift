# SPDX-License-Identifier: AGPL-3.0-or-later
"""The adapter: what it sends, what it refuses to send, and what it makes of what comes back.

No network. The session is a stand-in that records the request and answers with whatever the test
put there, which is what makes the two rules that matter testable at all: a 200 carrying `errors`
is indistinguishable from a success at the socket, so it has to be proved at this seam.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest

from sift.kernel.records import FoundRecord, Subject
from sift.slices.stash_boxes.adapter import (
    _BY_FINGERPRINT,
    EXACT,
    LONE,
    ONE_OF_SEVERAL,
    USERNAME_REFS,
    Box,
    SessionFactory,
    StashBoxAdapter,
    StashBoxRefused,
    StashBoxUnreachable,
    _picture,
    as_json,
    from_json,
    network_name,
    ranked,
)
from sift.slices.stash_boxes.reading import _site

A_BOX = Box(id="box-1", name="StashDB", endpoint="https://stashdb.example/graphql", api_key="k")


class _Answer:
    def __init__(self, body: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body

    async def json(self, content_type: str | None = None) -> Any:
        return self._body

    async def __aenter__(self) -> _Answer:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Session:
    """Records what went out and answers with what the test put in."""

    def __init__(self, answers: list[_Answer]) -> None:
        self._answers = answers
        self.sent: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Answer:
        self.sent.append({"url": url, **kwargs})
        return self._answers.pop(0)


def _factory(session: _Session) -> SessionFactory:
    """The adapter's session factory, standing in a fake for the real client.

    Cast rather than annotated as what it really is. `SessionFactory` promises an `aiohttp`
    session, the double is not one and cannot be without dragging the whole client in, and the
    adapter only ever calls `post` and `get` on what it is handed. The cast says exactly that, at
    the boundary, once, which is better than no annotation at all: with no return type mypy stops
    checking the body entirely.
    """

    @asynccontextmanager
    async def open_session(**_: Any) -> AsyncIterator[_Session]:
        yield session

    return cast("SessionFactory", open_session)


def _adapter(*answers: _Answer) -> tuple[StashBoxAdapter, _Session]:
    session = _Session(list(answers))
    # A clock that never advances, so the pacing never actually sleeps in a test.
    return StashBoxAdapter(_factory(session), clock=lambda: 1000.0), session


async def test_a_200_carrying_errors_is_a_failure_and_not_an_empty_result() -> None:
    """The whole reason `errors` is read before `data`.

    An unauthenticated query answers 200 with an errors array. Read `data` first and a dead key is
    reported as "this person is not in the stash-box" for ever, which is the one failure nobody
    would go looking for.
    """
    adapter, _ = _adapter(_Answer({"errors": [{"message": "not authorized"}], "data": None}))

    with pytest.raises(StashBoxUnreachable) as refused:
        await adapter.search(A_BOX, "anybody")

    assert "not authorized" in str(refused.value)


async def test_the_key_travels_in_the_apikey_header() -> None:
    adapter, session = _adapter(_Answer({"data": {"searchPerformers": {"performers": []}}}))

    await adapter.search(A_BOX, "anybody")

    assert session.sent[0]["headers"]["ApiKey"] == "k"


async def test_a_box_with_no_key_is_asked_without_one_rather_than_refused() -> None:
    """An unkeyed box is a feature that is absent, not an error. Some queries need no key."""
    adapter, session = _adapter(_Answer({"data": {"searchPerformers": {"performers": []}}}))

    await adapter.search(dataclasses.replace(A_BOX, api_key=None), "anybody")

    assert "ApiKey" not in session.sent[0]["headers"]


async def test_a_refusal_is_a_hard_stop_and_says_so() -> None:
    adapter, _ = _adapter(_Answer({}, status=429, headers={"Retry-After": "30"}))

    with pytest.raises(StashBoxRefused) as refused:
        await adapter.search(A_BOX, "anybody")

    assert "slow down" in str(refused.value)


async def test_an_empty_term_asks_nobody_anything() -> None:
    adapter, session = _adapter()

    assert await adapter.search(A_BOX, "   ") == []
    assert session.sent == []


async def test_a_performer_comes_back_in_sifts_words() -> None:
    """Their vocabulary stops at this boundary: what comes out is a person with Sift's field keys."""
    adapter, _ = _adapter(
        _Answer(
            {
                "data": {
                    "searchPerformers": {
                        "performers": [
                            {
                                "id": "abc",
                                "name": "A Name",
                                "disambiguation": "the singer",
                                "aliases": ["Another"],
                                "country": "US",
                                "band_size": 32,
                                "cup_size": "B",
                                "waist_size": 24,
                                "hip_size": 34,
                                "scene_count": 12,
                                "urls": [
                                    {
                                        "url": "https://example.com/esmewrenfield",
                                        "site": {"name": "Site"},
                                    },
                                    {"url": "https://iafd.com/person/x", "site": {"name": "IAFD"}},
                                ],
                                "images": [
                                    {
                                        "id": "i",
                                        "url": "https://img/1",
                                        "width": 800,
                                        "height": 1200,
                                    }
                                ],
                            }
                        ]
                    }
                }
            }
        )
    )

    found = await adapter.search(A_BOX, "a name")

    assert len(found) == 1
    one = found[0]
    assert one.subject is Subject.PERSON
    assert one.name == "A Name"
    assert one.disambiguation == "the singer"
    assert one.file_count == 12
    assert one.fields["aliases"] == ["Another"]
    assert one.fields["measurements"] == "32-B-24-34"
    # The `urls[]` split, which is the mapping and not a formatting detail. A page somebody posts
    # to is a username on a site; a page in a reference database is a link about them. Flattened
    # into one list of addresses, the `site` object beside each one would be thrown away, and
    # that object IS what says which of the two an address is.
    # The Site is the ADDRESS'S host, never the box's "Site" beside it. A host Sift does not know
    # comes out with a blank site, for the writer to ask the library about (`_accounts`).
    assert one.fields["accounts"] == [
        {"site": "", "handle": "esmewrenfield", "url": "https://example.com/esmewrenfield"}
    ]
    assert one.fields["links"] == ["https://iafd.com/person/x"]
    assert one.image_url == "https://img/1"


async def test_a_superseded_id_is_followed_rather_than_orphaned() -> None:
    """A stash-box merges two entries and the old id starts answering with a pointer."""
    adapter, session = _adapter(
        _Answer({"data": {"findPerformer": {"id": "old", "name": "Old", "merged_into_id": "new"}}}),
        _Answer({"data": {"findPerformer": {"id": "new", "name": "New"}}}),
    )

    found = await adapter.person(A_BOX, "old")

    assert found is not None
    assert found.name == "New"
    assert len(session.sent) == 2


async def test_a_degenerate_fingerprint_is_never_sent() -> None:
    """An all-zero perceptual hash is answered with dozens of unrelated scenes.

    Refused before it goes out rather than filtered after, so a question that cannot mean anything
    is never asked, which also means it does not spend one of this box's paced requests.
    """
    adapter, session = _adapter()

    assert await adapter.recognise(A_BOX, {"phash": "0" * 16}) == []
    assert session.sent == []


async def test_a_perceptual_only_answer_with_a_pile_in_it_is_a_no() -> None:
    """Their matching is fuzzy and capped: a long list is the matcher shrugging, not candidates."""
    scenes = [{"id": str(n), "title": f"Scene {n}"} for n in range(20)]
    adapter, _ = _adapter(_Answer({"data": {"findScenesBySceneFingerprints": [scenes]}}))

    assert await adapter.recognise(A_BOX, {"phash": "a1b2c3d4e5f60718"}) == []


async def test_an_exact_hash_answer_is_kept_however_many_come_back() -> None:
    """An exact-file hash is an identity. The cap is about the FUZZY matcher, not about this."""
    scenes: list[dict[str, object]] = [{"id": str(n), "title": f"Scene {n}"} for n in range(20)]
    scenes[7]["fingerprints"] = [
        {"hash": "1234567890ABCDEF", "algorithm": "OSHASH", "duration": 60}
    ]
    adapter, _ = _adapter(_Answer({"data": {"findScenesBySceneFingerprints": [scenes]}}))

    found = await adapter.recognise(A_BOX, {"oshash": "1234567890abcdef"})

    assert len(found) == 20
    # The scene that carries the file's hash leads, because the caller keeps the first.
    assert found[0].remote_id == "7"
    assert found[0].confidence == EXACT


_OURS = {"oshash": "1234567890abcdef", "phash": "fdd5a1d5a0d5a1d5"}


def _answered(*scenes: dict[str, Any]) -> _Answer:
    return _Answer({"data": {"findScenesBySceneFingerprints": [list(scenes)]}})


async def test_a_scene_that_came_back_on_the_picture_alone_is_not_exact() -> None:
    """The oshash goes out beside the perceptual hash, so reading every answer as exact would
    trust whichever of the two the box had matched. A clip of a few seconds could be given three
    different films that way, each applied without anybody being asked."""
    adapter, _ = _adapter(
        _answered(
            {
                "id": "1",
                "title": "Some Other Film",
                "duration": 232,
                "fingerprints": [
                    {"hash": "fdd5a1d5a0d5a1d4", "algorithm": "PHASH", "duration": 232},
                    {"hash": "0000aaaa0000aaaa", "algorithm": "OSHASH", "duration": 232},
                ],
            }
        )
    )

    found = await adapter.recognise(A_BOX, _OURS)

    assert [one.confidence for one in found] == [LONE]


async def test_a_scene_carrying_the_files_md5_is_exact() -> None:
    adapter, _ = _adapter(
        _answered(
            {"id": "1", "fingerprints": [{"hash": "ab" * 16, "algorithm": "MD5", "duration": 9}]}
        )
    )

    found = await adapter.recognise(A_BOX, {"md5": "AB" * 16, "phash": _OURS["phash"]})

    assert [one.confidence for one in found] == [EXACT]


async def test_a_lone_scene_whose_picture_is_far_off_is_only_a_candidate() -> None:
    """The box matches within its own distance; past the one Stash's tagger shows, it is no
    evidence here."""
    far = f"{int(_OURS['phash'], 16) ^ 0x1FF:016x}"
    adapter, _ = _adapter(
        _answered({"id": "1", "fingerprints": [{"hash": far, "algorithm": "PHASH", "duration": 9}]})
    )

    found = await adapter.recognise(A_BOX, _OURS)

    assert [one.confidence for one in found] == [ONE_OF_SEVERAL]


async def test_a_pile_is_a_no_when_the_exact_hash_sent_matched_none_of_it() -> None:
    """Having SENT an oshash no longer lifts the ceiling: only a scene carrying it does."""
    scenes = [{"id": str(n), "title": f"Scene {n}"} for n in range(20)]
    adapter, _ = _adapter(_Answer({"data": {"findScenesBySceneFingerprints": [scenes]}}))

    assert await adapter.recognise(A_BOX, _OURS) == []


async def test_one_perceptual_answer_is_strong_but_not_certain() -> None:
    close = {"hash": "a1b2c3d4e5f60719", "algorithm": "PHASH", "duration": 60}
    adapter, _ = _adapter(_answered({"id": "1", "title": "One", "fingerprints": [close]}))

    found = await adapter.recognise(A_BOX, {"phash": "a1b2c3d4e5f60718"})

    assert [one.confidence for one in found] == [0.8]


def test_a_picture_reported_as_minus_one_wide_is_not_the_biggest_one() -> None:
    """A stash-box reports `width: -1` sometimes, and a broken picture then WINS on area.

    The fixture matters more than it looks. `-1 x -1` is an area of one, so a broken picture of that
    shape loses to anything real whether or not it was dropped, and a check built on one passes
    with the guard deleted. Two negatives multiply to a large positive, which is the arrangement
    where dropping it is the only thing standing between a screen and a picture that will not load.
    """
    biggest = _picture(
        [
            {"url": "https://img/broken", "width": -4000, "height": -6000},
            {"url": "https://img/real", "width": 400, "height": 600},
        ]
    )

    assert biggest == "https://img/real"


def test_a_picture_with_no_size_is_ranked_after_every_sized_one_and_never_dropped() -> None:
    """A box reports `width: -1` for a VECTOR logo, and for a studio whose only logo is one that
    is the only picture there is: dropping it would leave linked Sites with none. Last, so any raster
    wins; kept, so the cover fetch can draw it."""
    from sift.slices.stash_boxes.adapter import _pictures

    assert _pictures(
        [
            {"url": "https://img/vector", "width": -1, "height": -1},
            {"url": "https://img/small", "width": 40, "height": 20},
            {"url": "https://img/nosize"},
            {"url": "https://img/big", "width": 400, "height": 200},
        ]
    ) == ("https://img/big", "https://img/small", "https://img/vector", "https://img/nosize")
    assert _picture([{"url": "https://img/vector", "width": -1, "height": -1}]) == (
        "https://img/vector"
    )


def test_nothing_at_all_is_no_picture() -> None:
    assert _picture([]) is None
    assert _picture(None) is None
    assert _picture([{"width": 10, "height": 10}, "not a picture"]) is None


async def test_an_answer_survives_a_trip_through_the_cache() -> None:
    """What goes into the cache has to come back out as the same thing, or a cached answer is a
    different answer, which is the shape of bug nobody notices for a month."""
    adapter, _ = _adapter(
        _Answer(
            {
                "data": {
                    "searchPerformers": {
                        "performers": [{"id": "abc", "name": "A Name", "aliases": ["X"]}]
                    }
                }
            }
        )
    )
    found = await adapter.search(A_BOX, "a name")

    back = from_json(as_json(found))

    assert [one.name for one in back] == ["A Name"]
    assert back[0].fields["aliases"] == ["X"]
    assert back[0].subject is Subject.PERSON
    assert back[0].confidence == found[0].confidence


async def test_something_that_is_not_an_answer_is_a_failure_not_a_crash() -> None:
    adapter, _ = _adapter(_Answer("a page of html"))

    with pytest.raises(StashBoxUnreachable):
        await adapter.search(A_BOX, "anybody")


# --- pictures -------------------------------------------------------------------------------
#
# Fetched by Sift rather than linked to, because Sift's own pages run under `img-src 'self'`. That
# makes this a small proxy, and a proxy that will fetch whatever it is handed is a way into the
# network the machine sits on, so all three refusals below are load-bearing.


class _Bytes(_Answer):
    """A picture arriving the way one actually arrives: in pieces.

    `read(n)` on a response body returns whatever has arrived, not `n` bytes, so a double that
    hands over everything in one go would pass a proxy that serves only the first packet. The pieces
    are deliberately smaller than anything the code asks for, so a reader that does not loop is
    caught rather than accidentally satisfied.
    """

    PIECE = 3

    def __init__(
        self,
        body: bytes,
        kind: str = "image/jpeg",
        status: int = 200,
        piece: int | None = None,
    ) -> None:
        super().__init__(None, status, {"Content-Type": kind})
        self.content = self
        self._bytes = body
        # Overridable only so the cap test can hand over twelve megabytes without yielding it four
        # million times. Everything else takes the mean default.
        self._piece = piece or self.PIECE

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        for at in range(0, len(self._bytes), self._piece):
            yield self._bytes[at : at + self._piece]


class _GetSession(_Session):
    def get(self, url: str, **kwargs: Any) -> _Answer:
        self.sent.append({"url": url, **kwargs})
        return self._answers.pop(0)


def _getter(*answers: _Answer) -> tuple[StashBoxAdapter, _GetSession]:
    session = _GetSession(list(answers))
    return StashBoxAdapter(_factory(session), clock=lambda: 1000.0), session


async def test_a_picture_on_another_host_is_never_fetched() -> None:
    """The URL comes out of somebody else's stash-box. A doctored entry pointing at an address on
    this machine's own network must not be fetched by a server that can reach it."""
    adapter, session = _getter()

    assert await adapter.picture(A_BOX, "http://10.0.0.1/admin") is None
    assert session.sent == []


async def test_a_picture_that_is_not_an_image_is_refused() -> None:
    """Served from Sift's own origin, so what it claims to be matters."""
    adapter, _ = _getter(_Bytes(b"<html>", kind="text/html"))

    assert await adapter.picture(A_BOX, "https://stashdb.example/img/1") is None


async def test_a_vector_is_refused_everywhere_but_a_cover_fetch() -> None:
    """The chooser's picture route hands a box's bytes to a browser as they are, and an SVG
    served from Sift's own address is a document running under Sift's origin. Only a fetch whose
    bytes go to the cover door, which draws one safely, takes it."""
    drawing = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    adapter, _ = _getter(_Bytes(drawing, kind="image/svg+xml"))

    assert await adapter.picture(A_BOX, "https://stashdb.example/img/1") is None

    adapter, _ = _getter(_Bytes(drawing, kind="image/svg+xml"))

    got = await adapter.picture(A_BOX, "https://stashdb.example/img/1", vector=True)

    assert got == (drawing, "image/svg+xml")


async def test_a_picture_over_the_cap_is_refused_rather_than_read() -> None:
    from sift.slices.stash_boxes.adapter import MAX_PICTURE_BYTES

    adapter, _ = _getter(_Bytes(b"x" * (MAX_PICTURE_BYTES + 1), piece=1024 * 1024))

    assert await adapter.picture(A_BOX, "https://stashdb.example/img/1") is None


async def test_a_picture_on_the_boxs_own_host_comes_back_with_its_type() -> None:
    """The body is deliberately longer than one piece of the double.

    A body no longer than one piece would come back whole even from a reader that does not loop.
    """
    whole = b"\xff\xd8\xff" + b"a picture is longer than one packet" * 40
    adapter, session = _getter(_Bytes(whole, kind="image/jpeg"))

    got = await adapter.picture(A_BOX, "https://stashdb.example/img/1")

    assert got == (whole, "image/jpeg")
    # No key on a request that does not need one. Stash-box images are public.
    assert "ApiKey" not in str(session.sent[0].get("headers", {}))


async def test_a_picture_arrives_whole_rather_than_as_its_first_packet() -> None:
    """A picture arriving in pieces is read whole.

    A response body does not arrive in one piece and `read(n)` does not wait for n bytes: it
    returns what is in the buffer. A reader that does not loop would serve the first packet: a 200,
    a correct content type, and an image the browser could not finish drawing.
    """
    whole = bytes(range(256)) * 500  # comfortably more than any one read
    adapter, _ = _getter(_Bytes(whole))

    got = await adapter.picture(A_BOX, "https://stashdb.example/img/1")

    assert got is not None
    assert len(got[0]) == len(whole), "the picture came back truncated"


# --- sites and tags, which come back the way a person does ----------------------------------------


async def test_a_site_comes_back_as_a_site_in_sifts_words() -> None:
    """Their word for one is a studio and it stops at this file. The parent comes across as a NAME
    rather than as their id: a stash-box's id names a row in somebody else's library, so what a
    person can be shown and asked about is the name."""
    adapter, _ = _adapter(
        _Answer(
            {
                "data": {
                    "searchStudio": [
                        {
                            "id": "s1",
                            "name": "A Studio",
                            "aliases": ["Elsewhere", 7],
                            "parent": {"id": "p1", "name": "A Network"},
                            "urls": [{"url": "https://example.test/s"}, {"nothing": "here"}],
                        }
                    ]
                }
            }
        )
    )

    found = await adapter.search_sites(A_BOX, "a studio")

    assert len(found) == 1
    one = found[0]
    assert one.subject is Subject.SITE
    assert one.name == "A Studio"
    assert one.fields["aliases"] == ["Elsewhere"], "an alias that is not text is not an alias"
    assert one.fields["parent"] == "A Network"
    assert one.fields["links"] == ["https://example.test/s"]


async def test_a_site_with_no_parent_carries_none_rather_than_an_empty_one() -> None:
    adapter, _ = _adapter(_Answer({"data": {"searchStudio": [{"id": "s1", "name": "A Studio"}]}}))

    found = await adapter.search_sites(A_BOX, "a studio")

    assert "parent" not in found[0].fields


async def test_a_tag_comes_back_with_its_one_category() -> None:
    """One word, never a tree. A stash-box's group of categories is a grouping of CATEGORIES rather
    than of tags, Sift has nowhere to keep it, and a second word beside the first would be a field
    nothing fills in."""
    adapter, _ = _adapter(
        _Answer(
            {
                "data": {
                    "searchTag": [
                        {
                            "id": "t1",
                            "name": "beach",
                            "description": "Sand.",
                            "aliases": ["seaside"],
                            "category": {"id": "c1", "name": "Location", "group": "SCENE"},
                        }
                    ]
                }
            }
        )
    )

    found = await adapter.search_tags(A_BOX, "beach")

    assert found[0].subject is Subject.TAG
    assert found[0].fields["category"] == "Location"
    assert "group" not in found[0].fields


async def test_a_name_that_is_only_spaces_asks_nobody_anything() -> None:
    """Every one of these would otherwise send a request to three public services to ask about
    nothing, through a throttle that exists to stop Sift doing that."""
    adapter, session = _adapter()

    assert await adapter.search_sites(A_BOX, "   ") == []
    assert await adapter.search_tags(A_BOX, "  ") == []
    assert session.sent == []


async def test_one_site_and_one_tag_by_id_come_back_or_do_not() -> None:
    adapter, _ = _adapter(
        _Answer({"data": {"findStudio": {"id": "s1", "name": "A Studio"}}}),
        _Answer({"data": {"findStudio": None}}),
        _Answer({"data": {"findTag": {"id": "t1", "name": "beach"}}}),
        _Answer({"data": {"findTag": None}}),
    )

    assert (await adapter.site(A_BOX, "s1")) is not None
    assert (await adapter.site(A_BOX, "gone")) is None
    assert (await adapter.tag(A_BOX, "t1")) is not None
    assert (await adapter.tag(A_BOX, "gone")) is None


async def test_one_scene_by_id_is_asked_by_its_id_and_read_as_certain() -> None:
    adapter, session = _adapter(
        _Answer({"data": {"findScene": {"id": "s1", "title": "Tide pools"}}}),
        _Answer({"data": {"findScene": None}}),
    )

    found = await adapter.scene(A_BOX, "s1")

    assert found is not None
    assert (found.subject, found.remote_id, found.confidence) == (Subject.ASSET, "s1", 1.0)
    assert "findScene(id: $id)" in session.sent[0]["json"]["query"]
    assert session.sent[0]["json"]["variables"] == {"id": "s1"}
    assert await adapter.scene(A_BOX, "gone") is None


async def test_a_superseded_id_that_leads_nowhere_answers_with_nothing() -> None:
    """Followed once, because a chain of merges that loops is somebody else's bug and not something
    to hang on, and the entry it was merged into can have been removed since."""
    adapter, _ = _adapter(
        _Answer({"data": {"findPerformer": {"id": "old", "name": "Old", "merged_into_id": "new"}}}),
        _Answer({"data": {"findPerformer": None}}),
    )

    assert await adapter.person(A_BOX, "old") is None


async def test_a_pointer_to_itself_is_not_followed() -> None:
    adapter, session = _adapter(
        _Answer(
            {"data": {"findPerformer": {"id": "same", "name": "A Name", "merged_into_id": "same"}}}
        )
    )

    found = await adapter.person(A_BOX, "same")

    assert found is not None and found.name == "A Name"
    assert len(session.sent) == 1


# --- what a mark reads as -------------------------------------------------------------------------


async def test_a_mark_reads_as_one_line_however_much_of_it_the_stash_box_carries() -> None:
    """Tattoos and piercings arrive as a place and a description, and a stash-box often has only
    one of the two. A record row reading "None: a rose" is worse than one reading "a rose"."""
    adapter, _ = _adapter(
        _Answer(
            {
                "data": {
                    "searchPerformers": {
                        "performers": [
                            {
                                "id": "p1",
                                "name": "A Name",
                                "tattoos": [
                                    {"location": "left arm", "description": "a rose"},
                                    {"location": "right arm"},
                                    {"description": "a star"},
                                    {},
                                    "not an entry at all",
                                ],
                            }
                        ]
                    }
                }
            }
        )
    )

    found = await adapter.search(A_BOX, "a name")

    assert found[0].fields["tattoos"] == ["left arm: a rose", "right arm", "a star"]


# --- what a service that is not answering looks like -----------------------------------------------


async def test_an_id_the_box_does_not_know_answers_with_nothing() -> None:
    adapter, _ = _adapter(_Answer({"data": {"findPerformer": None}}))

    assert await adapter.person(A_BOX, "nobody") is None


async def test_a_refusal_the_service_gives_is_reported_by_its_status() -> None:
    adapter, _ = _adapter(_Answer({"data": None}, status=500))

    with pytest.raises(StashBoxUnreachable) as refused:
        await adapter.search(A_BOX, "anybody")

    assert "500" in str(refused.value)


async def test_a_connection_that_fails_names_the_box_and_never_the_address() -> None:
    """A client error can carry the URL, and the URL can carry a key when somebody has configured
    one that way, so what is reported is the message this file writes, never the exception."""
    import aiohttp

    class _Broken(_Session):
        def post(self, url: str, **kwargs: Any) -> _Answer:
            raise aiohttp.ClientError("https://stashdb.example/graphql?apikey=the-real-key")

    session = _Broken([])
    adapter = StashBoxAdapter(_factory(session), clock=lambda: 1000.0)

    with pytest.raises(StashBoxUnreachable) as refused:
        await adapter.search(A_BOX, "anybody")

    assert str(refused.value) == "Sift could not reach StashDB."
    assert "the-real-key" not in str(refused.value)


async def test_a_service_that_does_not_answer_in_time_says_so() -> None:
    class _Slow(_Session):
        def post(self, url: str, **kwargs: Any) -> _Answer:
            raise TimeoutError

    adapter = StashBoxAdapter(_factory(_Slow([])), clock=lambda: 1000.0)

    with pytest.raises(StashBoxUnreachable) as refused:
        await adapter.search(A_BOX, "anybody")

    assert "did not answer in time" in str(refused.value)


# --- reading a username out of an address----------------------------------------------------------


async def test_an_address_with_nothing_after_the_host_carries_no_handle() -> None:
    """A username is what somebody would type into a search box after seeing it on a filename, and
    a Username spelled `www` is worse than no Username at all."""
    adapter, _ = _adapter(
        _Answer(
            {
                "data": {
                    "searchPerformers": {
                        "performers": [
                            {
                                "id": "p1",
                                "name": "A Name",
                                "urls": [
                                    # Nothing after the host at all.
                                    {"url": "https://example.test/", "site": {"name": "A Site"}},
                                    # A numeric id, which says the username is not the last piece.
                                    {"url": "https://example.test/1234", "site": {"name": "B"}},
                                    # A file, for the same reason.
                                    {
                                        "url": "https://example.test/index.html",
                                        "site": {"name": "C"},
                                    },
                                    # No site beside it, which does not matter (see below).
                                    {"url": "https://example.test/esmewrenfield"},
                                    # And one that is a username.
                                    {"url": "https://example.test/neve", "site": {"name": "D"}},
                                ],
                            }
                        ]
                    }
                }
            }
        )
    )

    found = await adapter.search(A_BOX, "a name")

    # The two with a name in them are usernames (the one with no `site` beside it too, since the
    # site is the address's host and never the box's word), and the three with none are LINKS.
    # Dropping them would lose the one address a person's own site is ever given as.
    assert found[0].fields["accounts"] == [
        {"site": "", "handle": "esmewrenfield", "url": "https://example.test/esmewrenfield"},
        {"site": "", "handle": "neve", "url": "https://example.test/neve"},
    ]
    assert found[0].fields["links"] == [
        "https://example.test/",
        "https://example.test/1234",
        "https://example.test/index.html",
    ]


# --- a box whose creators are filed as studios -----------------------------------------------
#
# PMVStash is the real one: a creator there is found under studios, and a search for them among
# performers comes back empty.

A_CREATOR_BOX = Box(
    id="box-2",
    name="PMVStash",
    endpoint="https://pmvstash.example/graphql",
    api_key="k",
    studios_are_people=True,
)


_A_SCENE = {
    "id": "s1",
    "title": "An Edit",
    "studio": {"id": "c1", "name": "quillmoss"},
    "performers": [{"as": "Someone Else", "performer": {"name": "Someone Else"}}],
}


async def test_a_recognised_file_files_the_studio_as_a_PERSON_on_such_a_box() -> None:
    """The half of the flag the SCAN goes through, which is how most files are recognised at all.

    Without it every file matched on PMVStash would put its creator into `site`, and the enricher
    would make a Site out of that field and attribute the file to nobody.

    The creator comes first and the performers keep their places: a box can carry both, and on one
    that files creators as studios the creator is the primary attribution.
    """
    adapter, _ = _adapter(_Answer({"data": {"findScenesBySceneFingerprints": [[_A_SCENE]]}}))

    found = await adapter.recognise(A_CREATOR_BOX, {"oshash": "abc123abc123abc1"})

    assert found[0].fields["people"] == ["quillmoss", "Someone Else"]
    # ...and the answer says which of them that is, in a field of its own rather than by position.
    assert found[0].fields["creator"] == "quillmoss"
    # Absent rather than empty: `site` is the field that makes the enricher invent a site, and
    # there is no site in this answer for it to invent one from.
    assert "site" not in found[0].fields


async def test_a_person_picked_on_such_a_box_is_fetched_from_its_STUDIOS() -> None:
    """The chooser's search reads studios there, so the id it hands back is a studio's, and the
    link fetches by that id, so it must ask `findStudio`, not `findPerformer`."""
    adapter, session = _adapter(
        _Answer({"data": {"findStudio": {"id": "c1", "name": "quillmoss", "aliases": ["nu"]}}})
    )

    found = await adapter.person(A_CREATOR_BOX, "c1")

    assert found is not None
    assert (found.subject, found.remote_id, found.name) == (Subject.PERSON, "c1", "quillmoss")
    assert "findStudio" in session.sent[0]["json"]["query"]
    assert "findPerformer" not in session.sent[0]["json"]["query"]


async def test_an_ordinary_box_still_fetches_a_person_from_its_PERFORMERS() -> None:
    """The other side, so the rule cannot become "every box asks its studios"."""
    adapter, session = _adapter(_Answer({"data": {"findPerformer": {"id": "p1", "name": "Jane"}}}))

    assert await adapter.person(A_BOX, "p1") is not None
    assert "findPerformer" in session.sent[0]["json"]["query"]


_THE_OLD_WORD = "platform"  # the old word, spelled once


def test_a_kept_record_in_a_word_this_build_does_not_write_is_unreadable() -> None:
    """The reader forgives nothing: the rows are rewritten by the schema (v14), not translated.

    A reader that translated an old word on every read would keep it alive in the rows and hide the
    next stale one the same way. `test_schema` proves the rewrite; this proves the reader does not
    cover for a row the rewrite missed, so such a row is logged rather than hidden.
    """
    kept = json.dumps(
        [{"source_id": "b", "remote_id": "s1", "subject": _THE_OLD_WORD, "name": "A Site"}]
    )

    assert from_json(kept) == []
    assert [(one.subject, one.name) for one in from_json(kept.replace(_THE_OLD_WORD, "site"))] == [
        (Subject.SITE, "A Site")
    ]


async def test_an_ordinary_box_still_files_the_studio_as_a_SITE() -> None:
    """The other side, so the rule cannot quietly become "no box has sites".

    Without this the branch could be inverted, or the flag ignored the other way, and the suite
    would stay green while StashDB and FansDB stopped filing anything under a site.
    """
    adapter, _ = _adapter(_Answer({"data": {"findScenesBySceneFingerprints": [[_A_SCENE]]}}))

    found = await adapter.recognise(A_BOX, {"oshash": "abc123abc123abc1"})

    assert found[0].fields["site"] == "quillmoss"
    assert found[0].fields["people"] == ["Someone Else"]
    # And no creator is named, for the same reason the other way round: the studio here IS a site,
    # it is already in `site`, and a `creator` on this answer would mark a site as a person.
    assert "creator" not in found[0].fields


async def test_a_person_is_looked_for_among_the_STUDIOS_on_such_a_box() -> None:
    """The question goes to the half of the service the creators are on, and what comes back is a
    person, because that is what the entry describes here."""
    adapter, session = _adapter(
        _Answer(
            {
                "data": {
                    "searchStudio": [
                        {
                            "id": "c1",
                            "name": "quillmoss",
                            "aliases": ["quillmoss_"],
                            "urls": [{"url": "https://example.test/quillmoss"}],
                        }
                    ]
                }
            }
        )
    )

    found = await adapter.search(A_CREATOR_BOX, "quillmoss")

    assert "searchStudio" in session.sent[0]["json"]["query"], "the studios half must be asked"
    assert len(found) == 1
    one = found[0]
    assert one.subject is Subject.PERSON
    assert one.name == "quillmoss"
    assert one.fields["aliases"] == ["quillmoss_"]
    assert one.fields["links"] == ["https://example.test/quillmoss"]


async def test_the_network_a_creator_posts_under_never_lands_on_their_RECORD() -> None:
    """It is shown in the chooser, where it tells two entries apart and claims nothing, and it is
    kept out of the fields: a site's name written under somebody's name on a person's record
    reads as another person of the same name being distinguished."""
    adapter, _ = _adapter(
        _Answer(
            {
                "data": {
                    "searchStudio": [
                        {
                            "id": "c1",
                            "name": "quillmoss",
                            "parent": {"id": "n1", "name": "A Network"},
                        }
                    ]
                }
            }
        )
    )

    found = await adapter.search(A_CREATOR_BOX, "quillmoss")

    assert found[0].disambiguation == "A Network"
    assert "parent" not in found[0].fields


async def test_such_a_box_offers_no_SITES_at_all() -> None:
    """The mirror of the fault. Answering a site lookup with the creators would fill somebody's
    Sites list with humans; an empty answer is the truth about a box like this."""
    adapter, session = _adapter()

    assert await adapter.search_sites(A_CREATOR_BOX, "quillmoss") == []
    assert session.sent == [], "nothing should have been asked at all"


async def test_an_ordinary_box_still_asks_the_PERFORMERS() -> None:
    """The case the three above must not break."""
    adapter, session = _adapter(
        _Answer({"data": {"searchPerformers": {"performers": [{"id": "p1", "name": "Somebody"}]}}})
    )

    found = await adapter.search(A_BOX, "somebody")

    assert "searchPerformers" in session.sent[0]["json"]["query"]
    assert [one.name for one in found] == ["Somebody"]


def test_a_term_with_no_words_in_it_ranks_nothing_and_marks_nothing() -> None:
    """The list comes back exactly as the service ranked it.

    Every entry answers "all nought of the words", so partitioning would be a no-op that still
    rewrote every record, and marking them all as answering everything would be a claim about a
    question nobody asked. The service's own order is the only honest answer here.
    """
    found = [
        FoundRecord(source_id="box", remote_id="1", subject=Subject.PERSON, name="Jane"),
        FoundRecord(source_id="box", remote_id="2", subject=Subject.PERSON, name="Doe"),
    ]

    assert ranked(found, "   ") is found
    assert ranked(found, "") is found


def test_the_entries_answering_every_word_typed_lead_the_chooser() -> None:
    """A stash-box's search matches ANY of the words, because it is built to complete one somebody
    is typing. Ask for a two-word name and it answers with the person and nine others who share a
    first name or a surname, and the nine look exactly as much like answers.

    Marked and moved rather than dropped: an entry filed under a spelling nobody here would type,
    an accent, a middle name, comes back only through the fuzzy half, and dropping it would make
    them unreachable through Sift while the service itself finds them.
    """
    found = [
        FoundRecord(source_id="box", remote_id="1", subject=Subject.PERSON, name="Jane Someone"),
        FoundRecord(source_id="box", remote_id="2", subject=Subject.PERSON, name="Jane Doe"),
    ]

    marked = ranked(found, "jane doe")

    assert [one.remote_id for one in marked] == ["2", "1"]
    assert [one.every_word for one in marked] == [True, False]


def test_a_name_matched_only_in_part_is_kept_and_marked() -> None:
    # The half that would be lost by filtering. Nothing here is thrown away; the screen draws the
    # rest behind a press, with a count.
    found = [FoundRecord(source_id="box", remote_id="1", subject=Subject.PERSON, name="Jane")]

    marked = ranked(found, "jane doe")

    assert [one.remote_id for one in marked] == ["1"]
    assert marked[0].every_word is False


def test_a_word_is_matched_against_every_name_an_entry_goes_by() -> None:
    """ "Any of them finds it" is the rule everywhere else in Sift, so somebody typing a stage name
    must not be told there is no such person."""
    found = [
        FoundRecord(
            source_id="box",
            remote_id="1",
            subject=Subject.PERSON,
            name="Jane Doe",
            disambiguation="Northlight",
            fields={"aliases": ["Janie Northlight"]},
        )
    ]

    assert ranked(found, "janie northlight")[0].every_word is True


def test_the_services_own_order_is_kept_within_each_half() -> None:
    # A stable partition rather than a sort: their ranking is fine, and re-ordering inside a half
    # would be Sift second-guessing a service about its own data.
    found = [
        FoundRecord(source_id="box", remote_id=str(n), subject=Subject.PERSON, name=name)
        for n, name in enumerate(["Jane Doe", "Jane Someone", "Jane Doe Two", "Jane Else"])
    ]

    assert [one.remote_id for one in ranked(found, "jane doe")] == ["0", "2", "1", "3"]


def test_the_scene_query_asks_for_every_field_the_record_reads() -> None:
    """The request and the mapper are two halves of one agreement, and only one of them is visible
    when a field goes missing.

    A field dropped from the query costs nothing at the mapper: `raw.get("tags")` answers None, the
    record comes back without them, and the rule that would have imported them has nothing to act
    on. Nothing raises and nothing on screen says which half is at fault.
    """
    for asked in ("title", "details", "release_date", "production_date", "code", "duration"):
        assert asked in _BY_FINGERPRINT, asked
    # The four that are nested, each with the one sub-field the mapper reads off it.
    assert "tags { name }" in _BY_FINGERPRINT
    # The studio's other names, parent and addresses too: they are what tell a creator from a
    # studio.
    assert "studio { id name aliases parent { id name } urls { url } }" in _BY_FINGERPRINT
    assert "performers { as performer { id name } }" in _BY_FINGERPRINT
    # What says which of the hashes sent a scene actually matched.
    assert "fingerprints { hash algorithm duration }" in _BY_FINGERPRINT
    assert "urls { url" in _BY_FINGERPRINT


def test_a_kept_answer_that_is_not_a_list_of_records_reads_as_nothing() -> None:
    """The tolerant reader's own refusal, and the one arm of it nothing reached.

    A single unreadable link row would take out the reconcile screen, the pile waiting to be
    enriched, and therefore the WHOLE Organize board, so every shape this can be handed answers
    with an empty list rather than raising. A payload that decodes to JSON but is not a list of
    records is one of those shapes: a version that wrote an object, or a truncated array that
    happened to close.
    """
    assert from_json('{"not": "a list"}') == []
    assert from_json('"a bare string"') == []
    assert from_json("null") == []
    # And the known positive beside them, so this is a refusal rather than a reader that always
    # answers with nothing.
    assert from_json("[]") == []


# --- the way out: a stored route is a tunnel id, resolved by the downloads' own resolver ---------


def _routed(route: str | None, opened: list[str | None], *, up: bool = True) -> Any:
    """A route opener standing in for `EgressRouter.through`: records what it was asked to open."""

    @asynccontextmanager
    async def through(asked: str | None) -> AsyncIterator[str | None]:
        opened.append(asked)
        if asked is None:
            yield None
            return
        if not up:
            raise RuntimeError("The tunnel is not available, so nothing was sent.")
        yield "http://127.0.0.1:61080"

    return through


async def _ask_through(route: str | None, *, up: bool = True) -> tuple[_Session, list[str | None]]:
    session = _Session([_Answer({"errors": [{"message": "stop here"}]})])
    opened: list[str | None] = []
    adapter = StashBoxAdapter(
        _factory(session), through=_routed(route, opened, up=up), clock=lambda: 1000.0
    )
    with pytest.raises(StashBoxUnreachable):
        await adapter.search(dataclasses.replace(A_BOX, route=route), "anybody")
    return session, opened


async def test_a_tunnel_id_is_resolved_to_its_proxy_address_and_never_sent_as_one() -> None:
    """A stored id handed to the client AS the proxy would reach nothing."""
    session, opened = await _ask_through("01TUNNELID")

    assert opened == ["01TUNNELID"]
    assert session.sent[0]["proxy"] == "http://127.0.0.1:61080"


async def test_a_stored_proxy_address_is_used_as_it_stands() -> None:
    session, opened = await _ask_through("http://127.0.0.1:3128")

    assert opened == []  # an address is not a tunnel id, and is not looked up as one
    assert session.sent[0]["proxy"] == "http://127.0.0.1:3128"


async def test_a_direct_box_goes_out_with_no_proxy() -> None:
    session, _ = await _ask_through(None)

    assert session.sent[0]["proxy"] is None


async def test_a_tunnel_that_is_down_refuses_in_the_boxes_words_and_sends_nothing() -> None:
    session = _Session([])
    adapter = StashBoxAdapter(
        _factory(session), through=_routed("01T", [], up=False), clock=lambda: 1000.0
    )

    with pytest.raises(StashBoxUnreachable, match="StashDB is set to use a tunnel"):
        await adapter.search(dataclasses.replace(A_BOX, route="01T"), "anybody")
    assert session.sent == []  # never a fall back to the machine's own address


async def test_a_box_on_a_private_address_gets_the_whole_guard_and_only_its_proxy() -> None:
    """No host is exempt: the boxes Sift asks are the official public ones, so the session is opened
    with the box's way out and nothing that could loosen the public-only rule for its endpoint. The
    refusal itself is proved against a real loopback listener in the download slice's `test_net.py`.
    """
    asked: list[dict[str, Any]] = []
    session = _Session([_Answer({"errors": [{"message": "stop here"}]})])

    @asynccontextmanager
    async def open_session(**kwargs: Any) -> AsyncIterator[_Session]:
        asked.append(kwargs)
        yield session

    adapter = StashBoxAdapter(cast("SessionFactory", open_session), clock=lambda: 1000.0)
    lan_box = dataclasses.replace(A_BOX, endpoint="http://192.168.1.20:9999/graphql")
    with pytest.raises(StashBoxUnreachable):
        await adapter.search(lan_box, "anybody")

    assert asked == [{"proxy": None}]


# --- which Site an address is filed under -------------------------------------------------------
#
# The box names a `site` beside each address, and that name is its word for the KIND of link:
# "Reddit User", "Studio Profile", "Modeling Agency", "Home". A library filed by it would grow Sites
# called exactly that, holding unrelated sites' usernames and every agency side by side. The Site
# is the address's own host, named the way the icon pack that ships with Sift names it.


def _a_person_listing(*urls: tuple[str, str]) -> _Answer:
    """A box's answer about one person whose `urls[]` are these (address, the box's word) pairs."""
    return _Answer(
        {
            "data": {
                "searchPerformers": {
                    "performers": [
                        {
                            "id": "p1",
                            "name": "Esme Wrenfield",
                            "urls": [{"url": url, "site": {"name": word}} for url, word in urls],
                        }
                    ]
                }
            }
        }
    )


async def test_an_address_is_filed_under_its_hosts_site_never_under_the_boxs_word() -> None:
    adapter, _ = _adapter(
        _a_person_listing(
            ("https://www.reddit.com/user/esmewren", "Reddit User"),
            ("https://www.kink.com/model/esmewren", "Studio Profile"),
        )
    )

    found = await adapter.search(A_BOX, "esme")

    assert found[0].fields["accounts"] == [
        {"site": "Reddit", "handle": "esmewren", "url": "https://www.reddit.com/user/esmewren"},
        {"site": "Kink", "handle": "esmewren", "url": "https://www.kink.com/model/esmewren"},
    ]
    assert "links" not in found[0].fields


async def test_a_site_the_box_spells_another_way_takes_the_name_sift_gives_it() -> None:
    """The box's own spelling of a real site is still the box's word: the pack's name wins."""
    adapter, _ = _adapter(_a_person_listing(("https://justfor.fans/esmewren", "JustForFans")))

    found = await adapter.search(A_BOX, "esme")

    assert found[0].fields["accounts"] == [
        {"site": "JustFor.Fans", "handle": "esmewren", "url": "https://justfor.fans/esmewren"}
    ]


async def test_a_page_on_a_known_sites_subdomain_is_that_site() -> None:
    adapter, _ = _adapter(
        _a_person_listing(("https://old.reddit.com/user/esmewren", "Reddit User"))
    )

    found = await adapter.search(A_BOX, "esme")

    assert found[0].fields["accounts"] == [
        {"site": "Reddit", "handle": "esmewren", "url": "https://old.reddit.com/user/esmewren"}
    ]


async def test_a_host_the_pack_does_not_know_carries_no_site_and_never_the_boxs_word() -> None:
    """Blank, for the writer to ask the library about, never "Home" or "Modeling Agency"."""
    adapter, _ = _adapter(
        _a_person_listing(
            ("https://esmewren.example.test/about/esmewren", "Home"),
            ("https://agency.example.test/talent/esmewren", "Modeling Agency"),
        )
    )

    found = await adapter.search(A_BOX, "esme")

    assert found[0].fields["accounts"] == [
        {"site": "", "handle": "esmewren", "url": "https://esmewren.example.test/about/esmewren"},
        {"site": "", "handle": "esmewren", "url": "https://agency.example.test/talent/esmewren"},
    ]


async def test_a_reference_database_is_still_a_link_whatever_the_box_calls_it() -> None:
    adapter, _ = _adapter(_a_person_listing(("https://www.iafd.com/person/esmewren", "Home")))

    found = await adapter.search(A_BOX, "esme")

    assert "accounts" not in found[0].fields
    assert found[0].fields["links"] == ["https://www.iafd.com/person/esmewren"]


async def test_a_stash_boxs_own_page_is_a_link_never_a_username() -> None:
    """ThePornDB, StashDB and FansDB are the reference databases themselves: a page about a person."""
    pages = (
        "https://theporndb.net/performers/esme-wrenfield",
        "https://stashdb.org/performers/esmewren",
        "https://fansdb.cc/performers/esmewren",
        "https://fansdb.xyz/performers/esmewren",
    )
    adapter, _ = _adapter(_a_person_listing(*((page, "Home") for page in pages)))

    found = await adapter.search(A_BOX, "esme")

    assert "accounts" not in found[0].fields
    assert found[0].fields["links"] == list(pages)


def _a_listing_of_sites(*urls: tuple[str, dict[str, Any]]) -> _Answer:
    """A box's answer about one person whose `urls[]` carry these whole `site` objects."""
    performer = {
        "id": "p1",
        "name": "Esme Wrenfield",
        "urls": [{"url": u, "site": s} for u, s in urls],
    }
    return _Answer({"data": {"searchPerformers": {"performers": [performer]}}})


@pytest.mark.parametrize("category", ["Third-party databases", "Databases", "Other stash-boxes"])
async def test_a_site_the_box_files_as_a_database_is_a_link_whatever_its_host(
    category: str,
) -> None:
    """A host no list knows, read as a reference by the box's own category for its site."""
    page = "https://refs.example.test/people/esmewren"
    site = {"name": "Refbook", "url": "https://refs.example.test/", "category": {"name": category}}
    adapter, session = _adapter(_a_listing_of_sites((page, site)))

    found = await adapter.search(A_BOX, "esme")

    assert "accounts" not in found[0].fields
    assert found[0].fields["links"] == [page]
    assert "urls { url site { id name url category { name } } }" in session.sent[0]["json"]["query"]


async def test_a_site_the_box_files_as_social_media_stays_a_username() -> None:
    page = "https://posts.example.test/esmewren"
    site = {
        "name": "Postly",
        "url": "https://posts.example.test/",
        "category": {"name": "Social media"},
    }
    adapter, _ = _adapter(_a_listing_of_sites((page, site)))

    found = await adapter.search(A_BOX, "esme")

    assert found[0].fields["accounts"] == [{"site": "", "handle": "esmewren", "url": page}]


async def test_a_persons_own_home_page_with_no_name_in_it_is_a_link() -> None:
    adapter, _ = _adapter(_a_person_listing(("https://esmewren.example.test/", "Home")))

    found = await adapter.search(A_BOX, "esme")

    assert "accounts" not in found[0].fields
    assert found[0].fields["links"] == ["https://esmewren.example.test/"]


async def test_a_page_tab_a_channel_id_and_the_film_database_are_not_usernames() -> None:
    """The name in a page's address, never the tab showing, the channel's id or a slug about them.

    Read by the kernel (`sift.kernel.urls.username_in`, whose own tests hold every shape); this
    holds the adapter to it: a tab is taken off, an address with no name in it and the film
    database's page are links: none of them becomes a username, and so none becomes an alias.
    """
    adapter, _ = _adapter(
        _a_person_listing(
            ("https://www.manyvids.com/Profile/1001/esmewren/Store/Videos", "ManyVids"),
            ("https://www.youtube.com/channel/UCesmewrenfield00000000A", "YouTube"),
            ("https://www.themoviedb.org/person/1000017-esme-wrenfield", "Home"),
        )
    )

    found = await adapter.search(A_BOX, "esme")

    accounts = found[0].fields["accounts"]
    assert isinstance(accounts, list)
    assert [entry["handle"] for entry in accounts] == ["esmewren"]
    assert found[0].fields["links"] == [
        "https://www.youtube.com/channel/UCesmewrenfield00000000A",
        "https://www.themoviedb.org/person/1000017-esme-wrenfield",
    ]


# --- the box's own ids for the people and the site a scene names ----------------------------------


async def test_a_recognised_scene_keeps_the_boxs_own_id_for_each_person_and_its_site() -> None:
    """The query asks for `performer { id name }` and `studio { id name }`, and a mapper that
    dropped both ids would leave a person a confirmed match INVENTED a bare name with no link, and
    the cover a link brings would never come. The id is the performer's whatever name the credit uses, so it
    is keyed by the name that goes into `people`: that is the name the writer makes the row under."""
    scene = {
        "id": "s1",
        "title": "A Clip",
        "studio": {"id": "st-1", "name": "Northlight Media"},
        "performers": [
            {"as": "Jane Somebody", "performer": {"id": "pf-1", "name": "Jane Doe"}},
            {"performer": {"id": "pf-2", "name": "Esme Wrenfield"}},
            {"performer": {"name": "someone with no id"}},
        ],
    }
    adapter, _ = _adapter(_Answer({"data": {"findScenesBySceneFingerprints": [[scene]]}}))

    (found,) = await adapter.recognise(A_BOX, {"oshash": "abc123abc123abc1"})

    assert found.refs == {
        "person": {"Jane Somebody": "pf-1", "Esme Wrenfield": "pf-2"},
        "site": {"Northlight Media": "st-1"},
    }
    assert found.id_for("person", " jane somebody ") == "pf-1"
    assert found.id_for("person", "someone with no id") is None
    # ...and the ids survive being KEPT, which is where the apply reads them from: a match is
    # written to Sift's own table and read back before anything is applied.
    (kept,) = from_json(as_json([found]))
    assert kept.refs == found.refs


async def test_on_a_creators_box_the_studios_id_is_the_CREATORS() -> None:
    """The studio is a person on such a box, so its id lands under the kind its name does: a
    site id for a name that is made into a person would link nobody."""
    adapter, _ = _adapter(_Answer({"data": {"findScenesBySceneFingerprints": [[_A_SCENE]]}}))

    (found,) = await adapter.recognise(A_CREATOR_BOX, {"oshash": "abc123abc123abc1"})

    assert found.refs == {"person": {"quillmoss": "c1"}}


def test_a_kept_record_with_no_ids_or_ids_of_a_strange_shape_still_reads() -> None:
    """Every record kept before the ids were carries none, and must read exactly as it did."""
    plain = json.dumps(
        [{"source_id": "b", "remote_id": "r", "subject": "asset", "name": "A Clip", "fields": {}}]
    )
    strange = plain.replace('"fields": {}', '"fields": {}, "refs": {"person": ["not", "a map"]}')

    assert [one.refs for one in from_json(plain)] == [{}]
    assert [one.refs for one in from_json(strange)] == [{}]


# --- the edges of an answer ------------------------------------------------------------------------


async def test_a_box_set_to_a_tunnel_where_no_tunnels_exist_refuses_and_sends_nothing() -> None:
    """Built with no route opener (a caller that has no tunnels to hand in), a tunnel id is a
    box that cannot be asked, never one asked directly from the machine's own address."""
    session = _Session([])
    adapter = StashBoxAdapter(_factory(session), clock=lambda: 1000.0)

    with pytest.raises(StashBoxUnreachable, match="set to use a tunnel"):
        await adapter.search(dataclasses.replace(A_BOX, route="01TUNNELID"), "anybody")
    assert session.sent == []


async def test_a_person_the_creators_box_has_no_studio_for_answers_with_nothing() -> None:
    adapter, _ = _adapter(_Answer({"data": {"findStudio": None}}))

    assert await adapter.person(A_CREATOR_BOX, "c-gone") is None


async def test_a_credit_that_is_not_a_record_is_passed_over_and_the_rest_kept() -> None:
    """One malformed entry in a scene's credits costs that credit, not the scene."""
    scene = {
        "id": "s1",
        "title": "A Clip",
        "performers": ["not a credit", {"performer": {"id": "pf-1", "name": "Jane Doe"}}],
    }
    adapter, _ = _adapter(_Answer({"data": {"findScenesBySceneFingerprints": [[scene]]}}))

    (found,) = await adapter.recognise(A_BOX, {"oshash": "abc123abc123abc1"})

    assert found.fields["people"] == ["Jane Doe"]
    assert found.refs == {"person": {"Jane Doe": "pf-1"}}


def test_a_kept_kind_whose_ids_are_all_empty_is_left_out_and_the_others_kept() -> None:
    kept = json.dumps(
        [
            {
                "source_id": "b",
                "remote_id": "r",
                "subject": "asset",
                "name": "A Clip",
                "fields": {},
                "refs": {"person": {"Jane Doe": "pf-1"}, "site": {"Northlight Media": ""}},
            }
        ]
    )

    assert [one.refs for one in from_json(kept)] == [{"person": {"Jane Doe": "pf-1"}}]


# --- a creator a box files as a studio under a network -----------------------------------------
#
# Some boxes keep every creator as a studio named "<handle> (<network>)" beneath a studio for the
# network. Such a studio is the creator's username on the network's Site, never a Site.

A_FAN_BOX = Box(id="box-3", name="FansDB", endpoint="https://fans.example/graphql", api_key="k")


def _creator_scene(studio: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "s9",
        "title": "A Clip",
        "studio": studio,
        "performers": [{"as": None, "performer": {"id": "p9", "name": "Esme Wrenfield"}}],
    }


async def test_a_creator_studio_under_a_network_is_a_username_on_the_networks_site() -> None:
    """The Site is the one the creator's own page is on, and the name is the studio's without the
    network in brackets. No `site` at all: that field is what would make a Site of the creator."""
    studio = {
        "id": "c9",
        "name": "quillmoss (OnlyFans)",
        "parent": {"id": "n1", "name": "OnlyFans (network)"},
        "urls": [{"url": "https://onlyfans.com/quillmoss"}],
    }
    adapter, session = _adapter(
        _Answer({"data": {"findScenesBySceneFingerprints": [[_creator_scene(studio)]]}})
    )

    found = await adapter.recognise(A_FAN_BOX, {"oshash": "abc123abc123abc1"})

    assert "parent { id name }" in session.sent[0]["json"]["query"]
    assert "site" not in found[0].fields
    assert found[0].fields["accounts"] == [
        {"site": "OnlyFans", "handle": "quillmoss", "url": "https://onlyfans.com/quillmoss"}
    ]
    assert found[0].fields["people"] == ["Esme Wrenfield"]
    # The box's id for the studio is kept with the answer, under the username it now is.
    assert found[0].refs["username"] == {"quillmoss": "c9"}
    assert "site" not in found[0].refs


async def test_a_creator_with_no_page_lands_on_the_site_the_pack_names_the_network() -> None:
    adapter, _ = _adapter(
        _Answer(
            {
                "data": {
                    "findScenesBySceneFingerprints": [
                        [
                            _creator_scene(
                                {
                                    "id": "c9",
                                    "name": "quillmoss (OnlyFans)",
                                    "parent": {"id": "n1", "name": "OnlyFans (network)"},
                                }
                            )
                        ]
                    ]
                }
            }
        )
    )

    found = await adapter.recognise(A_FAN_BOX, {"oshash": "abc123abc123abc1"})

    assert found[0].fields["accounts"] == [{"site": "OnlyFans", "handle": "quillmoss", "url": ""}]


@pytest.mark.parametrize(
    ("studio", "site"),
    [
        # A producer's label under its owner: no brackets, so a Site.
        (
            {"id": "c1", "name": "Marrowvale Studios", "parent": {"id": "n", "name": "Marrowvale"}},
            "Marrowvale Studios",
        ),
        # Brackets that are not the parent's name, under a network the pack knows: a Site.
        (
            {
                "id": "c1",
                "name": "Marrowvale Studio (Official)",
                "parent": {"id": "n", "name": "OnlyFans (network)"},
            },
            "Marrowvale Studio (Official)",
        ),
        # The creator shape on a network no page and no pack names: left a Site, never invented.
        (
            {"id": "c1", "name": "quillmoss (Quillhouse)", "parent": {"name": "Quillhouse"}},
            "quillmoss (Quillhouse)",
        ),
        # No parent at all: a Site.
        ({"id": "c1", "name": "quillmoss (OnlyFans)"}, "quillmoss (OnlyFans)"),
    ],
)
async def test_a_studio_that_is_not_a_creator_stays_a_site(
    studio: dict[str, Any], site: str
) -> None:
    adapter, _ = _adapter(
        _Answer({"data": {"findScenesBySceneFingerprints": [[_creator_scene(studio)]]}})
    )

    found = await adapter.recognise(A_FAN_BOX, {"oshash": "abc123abc123abc1"})

    assert found[0].fields["site"] == site
    assert "accounts" not in found[0].fields
    assert found[0].refs["site"] == {site: "c1"}


async def test_a_box_whose_studios_are_people_never_reads_one_as_a_username() -> None:
    studio = {
        "id": "c9",
        "name": "quillmoss (OnlyFans)",
        "parent": {"id": "n1", "name": "OnlyFans (network)"},
        "urls": [{"url": "https://onlyfans.com/quillmoss"}],
    }
    adapter, _ = _adapter(
        _Answer({"data": {"findScenesBySceneFingerprints": [[_creator_scene(studio)]]}})
    )

    found = await adapter.recognise(A_CREATOR_BOX, {"oshash": "abc123abc123abc1"})

    assert found[0].fields["creator"] == "quillmoss (OnlyFans)"
    assert "accounts" not in found[0].fields


def test_a_network_is_called_by_its_own_name_and_never_the_boxs_bracket() -> None:
    """Only the bracket goes; a name that is nothing but the bracket is left alone."""
    assert network_name("Harbour Network") == "Harbour Network"
    assert network_name("Quillhouse (Network)") == "Quillhouse"
    assert network_name("Quillhouse (network) ") == "Quillhouse"
    assert network_name("(Network)") == "(Network)"


@pytest.mark.parametrize(
    "raw",
    [
        {"id": "n1", "name": "Quillhouse (Network)"},
        {"id": "n1", "name": "Quillhouse (network)", "parent": None},
        {"id": "s2", "name": "Quillhouse", "parent": {"id": "n1", "name": "Quillhouse (Network)"}},
        {"id": "s3", "name": "Northlight", "parent": {"id": "n1", "name": "Quillhouse (network)"}},
    ],
)
def test_no_site_a_box_names_carries_the_network_bracket_in_its_name(raw: dict[str, Any]) -> None:
    """The network is a mark on a Site (its Sites within), never a word in its name: not the
    network's own name, and not the parent a studio names, whatever the box spells."""
    found = _site(A_BOX, raw, confidence=1.0)

    for said in (found.fields.get("name"), found.fields.get("parent")):
        assert said is None or "network" not in str(said).casefold()


def test_a_studios_parent_carries_its_own_name_and_the_boxs_id_under_both_spellings() -> None:
    """So the parent a studio names is made under its own name and linked to that studio by id,
    and a network's own record agrees with the Site that name made, keeping the box's spelling."""
    studio = _site(
        A_BOX,
        {
            "id": "s1",
            "name": "Northlight Media",
            "parent": {"id": "n1", "name": "Quillhouse (Network)"},
        },
        confidence=1.0,
    )
    assert studio.fields["parent"] == "Quillhouse"
    assert studio.id_for("site", "Quillhouse") == "n1"
    assert studio.id_for("site", "Quillhouse (Network)") == "n1"

    network = _site(A_BOX, {"id": "n1", "name": "Quillhouse (Network)"}, confidence=1.0)
    assert network.fields["name"] == "Quillhouse"
    assert network.fields["aliases"] == ["Quillhouse (Network)"]
    assert network.name == "Quillhouse (Network)", "the chooser shows the box's own spelling"

    flagship = _site(
        A_BOX,
        {"id": "s2", "name": "Quillhouse", "parent": {"id": "n1", "name": "Quillhouse (Network)"}},
        confidence=1.0,
    )
    # The flagship IS the network: one Site, which names no parent rather than itself.
    assert "parent" not in flagship.fields
    assert flagship.refs == {}


# --- a creator's own studio, with no network above it ------------------------------------------


def _a_creators_scene(*performers: str) -> dict[str, Any]:
    return {
        "id": "s9",
        "title": "A Clip",
        "studio": {
            "id": "st-9",
            "name": "Esme Wrenfield",
            "aliases": ["esmewrenfield clips"],
            "urls": [
                {"url": "https://x.com/esmewrenfield"},
                {"url": "https://www.manyvids.com/Profile/1001/Esme-Wrenfield/Store/Videos/"},
            ],
        },
        "performers": [
            {"performer": {"id": f"pf-{at}", "name": one}} for at, one in enumerate(performers)
        ],
    }


async def test_a_creators_own_store_crediting_her_is_her_username_and_never_a_site() -> None:
    """Her studio, her store page among its links, her on the scene: a username on ManyVids, filed
    with the box's id for the studio so her picture is kept the way a network creator's is."""
    scene = _a_creators_scene("Esme Wrenfield")
    adapter, _ = _adapter(_Answer({"data": {"findScenesBySceneFingerprints": [[scene]]}}))

    (found,) = await adapter.recognise(A_BOX, {"oshash": "abc123abc123abc1"})

    assert "site" not in found.fields
    assert found.fields["accounts"] == [
        {
            "site": "ManyVids",
            "handle": "Esme-Wrenfield",
            "url": "https://www.manyvids.com/Profile/1001/Esme-Wrenfield/Store/Videos/",
        }
    ]
    assert found.refs[USERNAME_REFS] == {"Esme-Wrenfield": "st-9"}


async def test_a_scene_of_her_studio_that_does_not_credit_her_stays_a_site_for_now() -> None:
    """One sign of two: filed as before, and asked about on Organize once its scenes are counted."""
    scene = _a_creators_scene("Wren Halloway")
    adapter, _ = _adapter(_Answer({"data": {"findScenesBySceneFingerprints": [[scene]]}}))

    (found,) = await adapter.recognise(A_BOX, {"oshash": "abc123abc123abc1"})

    assert found.fields["site"] == "Esme Wrenfield"
    assert "accounts" not in found.fields


def test_a_creators_first_page_on_a_host_sift_knows_names_the_site() -> None:
    """A page on a host Sift has no name for is passed over for the next one it does."""
    from sift.slices.stash_boxes.adapter import creator_account

    found = creator_account(
        {
            "name": "quillmoss (OnlyFans)",
            "parent": {"name": "OnlyFans (network)"},
            "urls": [
                "not a mapping",
                {"url": "https://example.invalid/quillmoss"},
                {"url": ""},
                {"url": "https://onlyfans.com/quillmoss"},
            ],
        }
    )
    assert found is not None
    assert (found.site, found.url) == ("OnlyFans", "https://onlyfans.com/quillmoss")


def test_a_scenes_evidence_reads_only_the_fingerprints_it_can_compare() -> None:
    """A malformed entry or an empty hash says nothing, and a perceptual hash is measured only
    against one that was sent."""
    from sift.slices.stash_boxes.adapter import _evidence

    scene = {
        "fingerprints": [
            "not a mapping",
            {"algorithm": "OSHASH", "hash": ""},
            {"algorithm": "PHASH", "hash": "ffffffffffffffff"},
            {"algorithm": "OSHASH", "hash": "ABC123ABC123ABC1"},
        ]
    }
    assert _evidence(scene, {"OSHASH": "abc123abc123abc1"}) == (True, None)
    assert _evidence({"fingerprints": None}, {"PHASH": "ffffffffffffffff"}) == (False, None)
    # Of several perceptual hashes the nearest is kept, and one that cannot be measured is not.
    nearest = {
        "fingerprints": [
            {"algorithm": "PHASH", "hash": "fffffffffffffff0"},
            {"algorithm": "PHASH", "hash": "ff00ffffffffffff"},
            {"algorithm": "PHASH", "hash": "not-a-hash"},
        ]
    }
    assert _evidence(nearest, {"PHASH": "ffffffffffffffff"}) == (False, 4)
