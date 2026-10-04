# SPDX-License-Identifier: AGPL-3.0-or-later
"""Answering "I already have this" without sending the file again: a `no-cache` check nobody
answers is a full download."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from sift import client
from sift.kernel import serving
from sift.kernel.serving import (
    CAREFUL,
    KEEPABLE,
    already_held,
    art_carries,
    art_version,
    face_version,
    keeps,
    serve_file,
)

CACHE = {"Cache-Control": "private, no-cache"}


def _asked(query: str) -> Request:
    """A request for a picture with this query string, built from a scope: only one key is read."""
    return Request({"type": "http", "method": "GET", "query_string": query.encode(), "headers": []})


class TestTheTokenOnAPictureAddress:
    """What makes an address name a picture rather than an asset."""

    def test_the_same_pictures_and_the_same_user_give_the_same_token(self) -> None:
        """The same pictures and user give the same token."""
        assert art_version("thumb:aaa|preview:bbb", 4) == art_version("thumb:aaa|preview:bbb", 4)

    def test_rebuilding_a_picture_gives_a_different_one(self) -> None:
        """A rebuilt picture's digest gives a new token."""
        assert art_version("thumb:aaa", 4) != art_version("thumb:bbb", 4)

    def test_and_so_does_hiding_something(self) -> None:
        """Hiding something moves the stamp, so the address changes with the permission."""
        assert art_version("thumb:aaa", 4) != art_version("thumb:aaa", 5)

    def test_nothing_recorded_about_the_pictures_gives_no_token_at_all(self) -> None:
        """Nothing recorded gives no token, so the picture is checked on every use."""
        assert art_version(None, 4) is None
        assert art_version("", 4) is None


class TestWhatTheMarksSayHasBeenBuilt:
    """Which pictures exist, from the same marks as the token, so the flag and the token agree and
    no wall asks for a hover clip to read a 404."""

    def test_a_kind_that_is_there_is_found(self) -> None:
        assert art_carries("thumb:aaa|preview:bbb|sprite:ccc", "preview")

    def test_a_kind_that_is_not_there_is_not(self) -> None:
        """A still built and a clip not: the clip is not there."""
        assert not art_carries("thumb:aaa|sprite:ccc", "preview")

    def test_nothing_built_carries_nothing(self) -> None:
        assert not art_carries(None, "preview")
        assert not art_carries("", "preview")

    def test_a_kind_is_matched_whole_and_never_as_a_substring(self) -> None:
        """A kind is matched whole, up to its colon."""
        assert not art_carries("thumb:previewish", "preview")
        assert not art_carries("thumbnail:aaa", "thumb")


class TestTheTokenOnAFaceAddress:
    """A face token is the user's stamp alone (crops and covers are never rewritten), so hiding the
    person still moves the address."""

    def test_the_same_user_gets_the_same_token(self) -> None:
        assert face_version(4) == face_version(4)

    def test_hiding_somebody_moves_every_face_address(self) -> None:
        assert face_version(4) != face_version(5)

    def test_it_is_always_a_token_and_never_nothing(self) -> None:
        """A face token always exists."""
        assert face_version(0) != ""


class TestHowLongAPictureMayBeKept:
    def test_a_picture_with_a_token_may_be_kept(self) -> None:
        assert keeps(_asked("v=abc123"), version="abc123", concealed=False) is KEEPABLE

    def test_a_concealed_one_may_not_be_kept_however_it_is_addressed(self) -> None:
        """A concealed picture is never kept, however addressed."""
        assert keeps(_asked("v=abc123"), version="abc123", concealed=True) is CAREFUL

    def test_and_neither_may_one_nothing_is_known_about(self) -> None:
        assert keeps(_asked("v=abc123"), version=None, concealed=False) is CAREFUL

    def test_an_address_carrying_no_token_may_not_be_kept_either(self) -> None:
        """A bare address is never kept: `immutable` promises a name never changes meaning, and a
        bare `/thumb` changes with a rebuild and with concealment."""
        assert keeps(_asked(""), version="abc123", concealed=False) is CAREFUL

    def test_an_empty_token_is_no_token(self) -> None:
        """`?v=` is the bare address."""
        assert keeps(_asked("v="), version="abc123", concealed=False) is CAREFUL

    def test_the_token_is_not_compared_with_the_one_the_server_holds(self) -> None:
        """The token is not compared with the server's: a cover composes its own. It decides whether
        a token was named, never which."""
        assert keeps(_asked("v=something-else"), version="abc123", concealed=False) is KEEPABLE

    def test_a_keepable_answer_is_private_and_a_week_long(self) -> None:
        """Keepable is `private` (no shared cache before a permission check) and a week long."""
        assert KEEPABLE["Cache-Control"] == "private, max-age=604800, immutable"


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """An application whose one route serves a file as Sift does."""
    picture = tmp_path / "thumb.jpg"
    picture.write_bytes(b"a picture, more or less")
    app = FastAPI()

    @app.get("/thumb")
    async def thumb(request: Request) -> Response:
        return await serve_file(request, picture, media_type="image/jpeg", headers=CACHE)

    @app.get("/gone")
    async def missing(request: Request) -> Response:
        return await serve_file(
            request, tmp_path / "nothing.jpg", media_type="image/jpeg", headers=CACHE
        )

    @app.get("/big")
    async def big(request: Request) -> Response:
        """The same file, served as if too big to carry in one trip."""
        with monkeypatch.context() as patched:
            patched.setattr(serving, "SMALL_FILE_BYTES", 0)
            return await serve_file(request, picture, media_type="image/jpeg", headers=CACHE)

    return TestClient(app)


def test_a_first_request_gets_the_file_and_something_to_ask_with(served: TestClient) -> None:
    response = served.get("/thumb")
    assert response.status_code == 200
    assert response.content == b"a picture, more or less"
    assert response.headers["etag"]
    assert response.headers["last-modified"]
    assert response.headers["cache-control"] == "private, no-cache"


def test_asking_again_with_the_same_tag_gets_no_body_at_all(served: TestClient) -> None:
    tag = served.get("/thumb").headers["etag"]

    response = served.get("/thumb", headers={"If-None-Match": tag})

    assert response.status_code == 304
    assert response.content == b""


def test_a_not_modified_answer_still_carries_the_tag_and_the_caching_rule(
    served: TestClient,
) -> None:
    """A 304 keeps the validators, or the browser has nothing to ask with."""
    tag = served.get("/thumb").headers["etag"]

    response = served.get("/thumb", headers={"If-None-Match": tag})

    assert response.headers["etag"] == tag
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["last-modified"]


def test_a_stale_tag_gets_the_file(served: TestClient) -> None:
    response = served.get("/thumb", headers={"If-None-Match": '"something-else"'})
    assert response.status_code == 200
    assert response.content == b"a picture, more or less"


def test_a_file_that_has_gone_behaves_as_it_did_before(served: TestClient) -> None:
    with pytest.raises(RuntimeError):
        served.get("/gone")


def test_a_big_file_is_streamed_rather_than_read_whole(served: TestClient) -> None:
    """A big file is streamed, with the same validators."""
    small = served.get("/thumb")

    response = served.get("/big")

    assert response.status_code == 200
    assert response.content == b"a picture, more or less"
    assert response.headers["etag"] == small.headers["etag"]
    assert response.headers["cache-control"] == "private, no-cache"


def test_a_big_file_still_answers_not_modified(served: TestClient) -> None:
    tag = served.get("/big").headers["etag"]

    response = served.get("/big", headers={"If-None-Match": tag})

    assert response.status_code == 304
    assert response.content == b""


# --- the decision itself


def test_a_matching_tag_wins() -> None:
    assert already_held({"if-none-match": '"abc"'}, {"etag": '"abc"'})


def test_one_matching_tag_in_a_list_is_enough() -> None:
    assert already_held({"if-none-match": '"x", "abc" , "y"'}, {"etag": '"abc"'})


def test_a_weak_tag_matches_the_strong_one_it_was_made_from() -> None:
    """A weak tag matches its strong original."""
    assert already_held({"if-none-match": 'W/"abc"'}, {"etag": '"abc"'})


def test_a_star_matches_anything_that_exists() -> None:
    assert already_held({"if-none-match": "*"}, {"etag": '"abc"'})


def test_a_different_tag_does_not_match() -> None:
    assert not already_held({"if-none-match": '"abc"'}, {"etag": '"def"'})


def test_a_tag_beats_a_date_even_when_the_date_would_have_matched() -> None:
    """A tag beats a date, which cannot tell two writes in one second apart."""
    asked = {
        "if-none-match": '"stale"',
        "if-modified-since": "Wed, 21 Oct 2026 07:28:00 GMT",
    }
    holding = {"etag": '"fresh"', "last-modified": "Wed, 21 Oct 2026 07:28:00 GMT"}
    assert not already_held(asked, holding)


def test_a_date_is_consulted_when_there_is_no_tag() -> None:
    asked = {"if-modified-since": "Wed, 21 Oct 2026 07:28:00 GMT"}
    holding = {"last-modified": "Wed, 21 Oct 2026 07:28:00 GMT"}
    assert already_held(asked, holding)


def test_a_file_written_since_the_date_asked_about_is_sent() -> None:
    asked = {"if-modified-since": "Wed, 21 Oct 2026 07:28:00 GMT"}
    holding = {"last-modified": "Wed, 21 Oct 2026 09:00:00 GMT"}
    assert not already_held(asked, holding)


def test_a_date_that_cannot_be_read_sends_the_file() -> None:
    """An unreadable date sends the file."""
    asked = {"if-modified-since": "some time last week"}
    holding = {"last-modified": "Wed, 21 Oct 2026 07:28:00 GMT"}
    assert not already_held(asked, holding)


def test_asking_nothing_gets_the_file() -> None:
    assert not already_held({}, {"etag": '"abc"', "last-modified": "Wed, 21 Oct 2026 07:28:00 GMT"})


class TestHowLongTheClientIsKept:
    """The client's three caching rules, by the file a request is answered with: what decides
    whether a phone shows the installed release."""

    def test_a_hashed_file_is_kept_for_a_year_without_asking(self) -> None:
        hashed = client.CLIENT_DIR / "_app" / "immutable" / "entry" / "start.abc123.js"
        assert client.cache_control_for("_app/immutable/entry/start.abc123.js", hashed) == (
            client.FOREVER
        )

    def test_every_address_answered_with_the_shell_is_never_kept(self) -> None:
        """The shell is never kept: a restored tab reuses a `no-cache` copy without asking."""
        for path in ("", "browse", "asset/01HX0000000000000000000A01", "settings/privacy"):
            assert client.cache_control_for(path, client.INDEX) == "no-store", path
        # By its own name it is resolved, which need not be spelled as `INDEX` is.
        listed = Path("elsewhere") / "web" / "index.html"
        assert client.cache_control_for("index.html", listed) == "no-store"

    def test_any_other_file_is_kept_and_asked_about(self) -> None:
        version = client.CLIENT_DIR / "_app" / "version.json"
        assert client.cache_control_for("_app/version.json", version) == "no-cache"
