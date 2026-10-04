# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture an entity is drawn as: serving it, and taking one in.

## Why this is a kernel test and not six slice tests

`serve_cover` is the whole of what a person, a Site, a username, a tag, a photo album and a
collection do when somebody asks for their picture, and `receive_cover` is the whole of what they do
when somebody uploads one. Each of those slices has its own test that its route calls these; what
THIS answers is the question none of them can, which is whether the one function they all call gets
every case right. Six copies of that would be five chances to check it slightly differently.

## The two cases that matter most

**A cover pointing at a file the asker may not open is a MISS, not a leak.** The write side checks
the asset against the viewer when the cover is chosen, but a permission can be taken away after
that, and the row keeps pointing where it pointed. So the read is scoped too, and the two are locks
on the same door rather than one lock counted twice.

**An uploaded picture never becomes a file the way it arrived.** The bytes go into ffmpeg on a pipe
and what lands on the disk is Sift's own JPEG. `test_the_bytes_that_arrive_are_not_the_bytes_that_
are_kept` is the one that says so out loud, and it is the whole security argument for the feature
made checkable.

Every refusal to SERVE is the same 404: no cover, not allowed, and not rendered yet are one answer
on purpose. A caller who could tell those apart could learn that a file exists and is being
withheld, which is the thing the concealment rules are for.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from sift.kernel import media
from sift.kernel.access import Viewer
from sift.kernel.access.repository.views import ServedDerivative
from sift.kernel.access.viewer import Role
from sift.kernel.config import Settings
from sift.kernel.content.identity import DerivativeKind
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.covers import (
    COVER_PICTURE_MAX_BYTES,
    ChosenCover,
    CoverHandle,
    CoverPictureRefused,
    CoverPictures,
    SubjectCovers,
    cover_change,
    cover_payload,
    forget_displaced,
    receive_cover,
    serve_cover,
)
from sift.kernel.db import Database
from sift.kernel.ledger import Actor, Object
from sift.kernel.records import Subject
from sift.kernel.serving import face_version
from sift.kernel.site_icons import token_of
from sift.testing.library import a_png

A_VIEWER = Viewer(id="01HX00000000000000000000AA", role=Role.ADMIN)


class _Answers:
    """A repository that answers with whatever this test wants, and remembers what it was asked.

    Written here rather than reached for from the slice fixtures, because what is under test is the
    branching in `serve_cover` and not the repository: a real one would need a database, a file on
    disk and a permission to take away, to prove something that is four `if`s.
    """

    def __init__(self, answer: ServedDerivative | Exception | None) -> None:
        self._answer = answer
        self.asked: list[tuple[str, DerivativeKind, Any]] = []

    async def serve_derivative(
        self,
        viewer: Viewer,
        asset_id: str,
        kind: DerivativeKind,
        *,
        params: Any = None,
    ) -> ServedDerivative | None:
        self.asked.append((asset_id, kind, params))
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def _request() -> Request:
    """The bare minimum a `Request` needs to be built without a server behind it."""
    return Request({"type": "http", "method": "GET", "headers": [], "app": FastAPI()})


def _reader(data: bytes) -> Any:
    """`UploadFile.read`'s shape, over bytes already in hand."""
    held = {"rest": data}

    async def read(size: int) -> bytes:
        chunk = held["rest"][:size]
        held["rest"] = held["rest"][size:]
        return chunk

    return read


#: Every database `_store` has opened during the test running now, closed by the fixture below.
#:
#: !! ONE UNCLOSED DATABASE HANGS THE WHOLE PROCESS, but only when a test FAILS. `connect()` starts
#: aiosqlite worker threads; on the passing path the store's last reference goes at the end of the
#: line and the threads go with it, and on the failing path pytest keeps the frame alive for its
#: traceback, so the threads outlive the event loop and `threading._shutdown` waits for them for
#: ever: the test reports `1 failed` and the process never exits.
#:
#: A list drained by an autouse fixture rather than a close at every call site: what every one of
#: them wants is a store, and what none of them wants is to remember this.
_OPENED: list[Database] = []


@pytest.fixture(autouse=True)
async def _close_what_was_opened() -> AsyncIterator[None]:
    """Close every database a test opened, whether it passed or not."""
    yield
    while _OPENED:
        await _OPENED.pop().close()


async def _store(tmp_path: Path) -> CoverPictures:
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    database = Database(tmp_path / "covers.sqlite3")
    await database.connect()
    _OPENED.append(database)
    await database.initialize_schema()
    return CoverPictures(database, settings)


pytestmark = pytest.mark.anyio


def _asked(app: FastAPI, *, art: str | None = "v1") -> Any:
    """One request to the app under test, with the client CLOSED afterwards.

    The address carries a token by default, because that is how the client builds every cover
    address it draws and because `keeps` will only promise a week to an address that carries one.
    Pass None to ask the way a screen that has forgotten it asks.

    !! A `TestClient` built inline and left to the garbage collector hangs the interpreter when the
    test FAILS, and only then. It holds an anyio portal on a thread of its own; on the passing
    path the reference count drops the moment the line ends and the thread goes with it, and on
    the failing path pytest keeps the frame alive
    for its traceback, so the thread outlives the session and `threading._shutdown` waits for it for
    ever: the test reports `1 failed` and the process never exits. That is worse than a broken test,
    because a case that never returns stalls a whole run of the suite.
    """
    with TestClient(app) as client:
        return client.get("/cover" if art is None else f"/cover?v={art}")


class TestTheRefusals:
    """Four ways to have nothing to send, and one answer to all of them."""

    async def test_an_entity_with_no_cover_is_a_404(self, tmp_path: Path) -> None:
        access = _Answers(None)

        with pytest.raises(HTTPException) as refused:
            await serve_cover(
                _request(),
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(),
                pictures=await _store(tmp_path),
            )

        assert refused.value.status_code == 404
        assert access.asked == [], "nothing should be read for a cover that was never chosen"

    async def test_a_picture_that_has_not_been_built_yet_is_a_404(self, tmp_path: Path) -> None:
        """Ordinary rather than exceptional: the still at a chosen moment is queued when the moment
        is chosen, and until it lands there is nothing to send. The client falls back to the file's
        own picture, exactly as a mark's tile does."""
        access = _Answers(None)

        with pytest.raises(HTTPException) as refused:
            await serve_cover(
                _request(),
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1", at_ms=61_500),
                pictures=await _store(tmp_path),
            )

        assert refused.value.status_code == 404

    async def test_a_cover_this_user_may_not_open_is_a_404(self, tmp_path: Path) -> None:
        """The read-side lock. `serve_derivative` is scoped to the viewer, so a cover pointing at
        something withheld comes back as nothing, and nothing is the same answer as no cover."""
        access = _Answers(None)

        with pytest.raises(HTTPException) as refused:
            await serve_cover(
                _request(),
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="withheld"),
                pictures=await _store(tmp_path),
            )

        assert refused.value.status_code == 404

    async def test_a_row_naming_a_path_outside_the_cache_is_a_404(self, tmp_path: Path) -> None:
        """A restored backup, or a row written before the check that now refuses it. The same
        answer as any other miss, rather than a 500 that says a path out loud."""
        access = _Answers(ValueError("not inside the cache"))

        with pytest.raises(HTTPException) as refused:
            await serve_cover(
                _request(),
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1"),
                pictures=await _store(tmp_path),
            )

        assert refused.value.status_code == 404

    async def test_an_uploaded_cover_whose_file_is_gone_is_a_404(self, tmp_path: Path) -> None:
        """The cache is disposable by design, so a swept picture is an ordinary situation. It is the
        same answer as every other miss and for the same reason."""
        pictures = await _store(tmp_path)

        with pytest.raises(HTTPException) as refused:
            await serve_cover(
                _request(),
                _Answers(None),  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(upload_id="never-stored"),
                pictures=pictures,
            )

        assert refused.value.status_code == 404


class TestWhatIsAskedFor:
    """The moment comes off the ROW, and it is half of the cache key."""

    async def test_a_cover_with_no_moment_asks_for_the_files_own_still(
        self, tmp_path: Path
    ) -> None:
        access = _Answers(None)

        with pytest.raises(HTTPException):
            await serve_cover(
                _request(),
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1"),
                pictures=await _store(tmp_path),
            )

        assert access.asked == [("a1", DerivativeKind.THUMB, None)]

    async def test_a_cover_with_a_moment_asks_for_that_moments_still(self, tmp_path: Path) -> None:
        """`params` is what files a still: `(asset_id, kind, params)` is unique, so the still at
        61,500ms and the file's own still are two rows and two files."""
        access = _Answers(None)

        with pytest.raises(HTTPException):
            await serve_cover(
                _request(),
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1", at_ms=61_500),
                pictures=await _store(tmp_path),
            )

        assert access.asked == [("a1", DerivativeKind.THUMB, {"at_ms": 61_500})]

    async def test_an_upload_wins_over_a_file(self, tmp_path: Path) -> None:
        """The one place the precedence is written. It is unreachable in practice (one statement
        writes both pointers, so no row carries both), and it is still stated, because "cannot
        happen" is a property of today's writers and this is a property of the cover."""
        pictures = await _store(tmp_path)
        upload_id = await pictures.receive(_reader(a_png()))
        access = _Answers(None)

        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1", at_ms=0, upload_id=upload_id),
                pictures=pictures,
            )

        answer = _asked(app)

        assert answer.status_code == 200
        assert access.asked == [], "the file was never read: the upload answered first"


class TestSendingIt:
    """The one path that has bytes at the end of it."""

    async def test_a_cover_that_exists_is_sent_as_a_jpeg(self, tmp_path: Path) -> None:
        picture = tmp_path / "cover.jpg"
        picture.write_bytes(b"\xff\xd8\xff\xd9")
        access = _Answers(ServedDerivative(path=picture, version="v1", concealed=False))
        pictures = await _store(tmp_path)

        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1"),
                pictures=pictures,
            )

        answer = _asked(app)

        assert answer.status_code == 200
        assert answer.headers["content-type"] == "image/jpeg"
        assert answer.content == b"\xff\xd8\xff\xd9"

    async def test_a_concealed_cover_is_never_given_a_keepable_address(
        self, tmp_path: Path
    ) -> None:
        """A picture of something in the vault must not survive in a browser's own store, where it
        would be readable with no request and so with no check."""
        picture = tmp_path / "cover.jpg"
        picture.write_bytes(b"\xff\xd8\xff\xd9")
        access = _Answers(ServedDerivative(path=picture, version="v1", concealed=True))
        pictures = await _store(tmp_path)

        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1"),
                pictures=pictures,
            )

        answer = _asked(app)

        assert answer.status_code == 200
        assert "no-cache" in answer.headers["cache-control"]


class TestHowLongItMayBeKept:
    """A keepable reply is a promise about the ADDRESS, and one of these cannot make it."""

    async def test_a_cover_with_no_moment_may_be_kept(self, tmp_path: Path) -> None:
        """The address carries which FILE is the cover, so it moves when the cover moves."""
        picture = tmp_path / "cover.jpg"
        picture.write_bytes(b"\xff\xd8\xff\xd9")
        access = _Answers(ServedDerivative(path=picture, version="v1", concealed=False))
        pictures = await _store(tmp_path)

        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1"),
                pictures=pictures,
            )

        answer = _asked(app, art="v1.a1")

        assert "max-age" in answer.headers["cache-control"]

    async def test_a_token_that_does_not_name_the_file_may_not_be_kept(
        self, tmp_path: Path
    ) -> None:
        """A token, but one naming a different file (or none): the cover was changed and this
        address is the old one, or a screen forgot to fold the file in. Kept a week, it would go on
        drawing the old cover, so it is answered the careful way and re-checked."""
        picture = tmp_path / "cover.jpg"
        picture.write_bytes(b"\xff\xd8\xff\xd9")
        access = _Answers(ServedDerivative(path=picture, version="v1", concealed=False))
        pictures = await _store(tmp_path)

        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1"),
                pictures=pictures,
            )

        assert "no-cache" in _asked(app, art="v1.a2").headers["cache-control"]
        assert "no-cache" in _asked(app, art="v1").headers["cache-control"]

    async def test_a_cover_asked_for_without_its_token_may_not_be_kept(
        self, tmp_path: Path
    ) -> None:
        """The same cover, at the same moment, asked for by an address that names nothing.

        `immutable` is a promise about the ADDRESS, so it cannot be made to `/cover` on its own:
        that address stands still through a change of cover and through the user's stamp moving,
        which is the one thing that has to take every kept picture away at once. The server must not
        answer both alike, reading only what it knows about the bytes.
        """
        picture = tmp_path / "cover.jpg"
        picture.write_bytes(b"\xff\xd8\xff\xd9")
        access = _Answers(ServedDerivative(path=picture, version="v1", concealed=False))
        pictures = await _store(tmp_path)

        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1"),
                pictures=pictures,
            )

        answer = _asked(app, art=None)

        assert "no-cache" in answer.headers["cache-control"]
        assert "max-age" not in answer.headers["cache-control"]

    async def test_a_cover_at_a_chosen_moment_may_not_unless_the_address_names_it(
        self, tmp_path: Path
    ) -> None:
        """An address that cannot name its contents must not be promised for a week. Moving a cover
        from one moment of a clip to another leaves an address naming only the file identical, and
        `immutable, max-age=1 week` would mean the browser never asks again. So the file alone is
        not enough here: the address has to carry the moment too, and one that does may be kept."""
        picture = tmp_path / "cover.jpg"
        picture.write_bytes(b"\xff\xd8\xff\xd9")
        access = _Answers(ServedDerivative(path=picture, version="v1", concealed=False))
        pictures = await _store(tmp_path)

        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1", at_ms=61_500),
                pictures=pictures,
            )

        answer = _asked(app, art="v1.a1")

        assert "no-cache" in answer.headers["cache-control"]
        assert "max-age" not in answer.headers["cache-control"]
        assert "no-cache" in _asked(app, art="v1.a1.1000").headers["cache-control"]
        assert "immutable" in _asked(app, art="v1.a1.61500").headers["cache-control"]

    async def test_an_uploaded_cover_may_not_be_kept_unless_the_address_names_it(
        self, tmp_path: Path
    ) -> None:
        """Kept only when the address carries the upload id AND the user's stamp.

        Not because the bytes are doubtful: an upload id is minted per picture, never reused, and
        what it names is never rewritten. Because an address that does not name the upload stays
        identical when one upload replaces another, and the stamp is the only thing that takes a
        kept upload away when what the user may see changes: an upload carries no concealed flag.
        """
        pictures = await _store(tmp_path)
        upload_id = await pictures.receive(_reader(a_png()))

        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                _Answers(None),  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(upload_id=upload_id),
                pictures=pictures,
            )

        answer = _asked(app)

        assert "no-cache" in answer.headers["cache-control"]
        stamped = face_version(A_VIEWER.cache_stamp)
        assert "no-cache" in _asked(app, art=f".{upload_id}").headers["cache-control"]
        assert "no-cache" in _asked(app, art=f"{stamped}.other").headers["cache-control"]
        assert "immutable" in _asked(app, art=f"{stamped}.{upload_id}").headers["cache-control"]


class TestTakingAPictureIn:
    """What an upload does, and what it refuses."""

    async def test_the_bytes_that_arrive_are_not_the_bytes_that_are_kept(
        self, tmp_path: Path
    ) -> None:
        """THE security property of the whole feature, stated as an assertion.

        A PNG goes in and a JPEG comes out, which is only possible if the bytes were re-encoded,
        and re-encoding is what disposes of camera metadata, of a polyglot file that is a picture
        and an archive at once, and of every decoder bug in every browser that will ever open it.
        """
        pictures = await _store(tmp_path)
        arriving = a_png()
        assert arriving.startswith(b"\x89PNG")

        upload_id = await pictures.receive(_reader(arriving))

        kept = await pictures.path_of(upload_id)
        assert kept is not None
        stored = kept.read_bytes()
        assert stored.startswith(b"\xff\xd8\xff"), "what is kept is a JPEG Sift wrote"
        assert stored != arriving

    async def test_where_a_picture_is_kept_is_said_relative_to_the_cache(
        self, tmp_path: Path
    ) -> None:
        """What a store outside the kernel writes down to find the picture again: the door's own
        relative path, the one `path_of` resolves, so a cache folder moved breaks no pointer."""
        pictures = await _store(tmp_path)
        upload_id = await pictures.receive(_reader(a_png()))

        relative = await pictures.kept_as(upload_id)
        kept = await pictures.path_of(upload_id)

        assert relative is not None and kept is not None
        assert not Path(relative).is_absolute()
        assert kept.parts[-len(Path(relative).parts) :] == Path(relative).parts
        assert await pictures.kept_as("no-such-picture") is None

    async def test_nothing_the_uploader_sent_is_ever_written_to_the_disk(
        self, tmp_path: Path
    ) -> None:
        """The stronger half of the property above: there is no moment at which a stranger's bytes
        exist under a name. Only one file appears in the cache and it is Sift's own picture."""
        pictures = await _store(tmp_path)

        await pictures.receive(_reader(a_png()))

        written = sorted(p.name for p in (tmp_path / "cache" / "covers").iterdir())
        assert len(written) == 1
        assert written[0].endswith(".jpg")

    async def test_an_svg_is_drawn_and_then_re_encoded_like_any_picture(
        self, tmp_path: Path
    ) -> None:
        """ffmpeg has no SVG decoder, so a vector logo is drawn to a PNG in memory first, and what
        lands is still one JPEG Sift wrote: never the SVG, never the PNG."""
        pictures = await _store(tmp_path)
        drawing = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20">'
            b'<rect width="40" height="20" fill="#3060c0"/></svg>'
        )

        upload_id = await pictures.receive(_reader(drawing))

        kept = await pictures.path_of(upload_id)
        assert kept is not None
        assert kept.read_bytes().startswith(b"\xff\xd8\xff")
        written = sorted(p.name for p in (tmp_path / "cache" / "covers").iterdir())
        assert written == [f"{upload_id}.jpg"]

    async def test_an_svg_that_draws_nothing_is_refused_in_sifts_words(
        self, tmp_path: Path
    ) -> None:
        pictures = await _store(tmp_path)

        with pytest.raises(CoverPictureRefused) as refused:
            await pictures.receive(_reader(b'<svg xmlns="http://www.w3.org/2000/svg"/>'))

        assert "JPEG" in str(refused.value)

    async def test_a_file_over_the_cap_is_refused(self, tmp_path: Path) -> None:
        """Enforced WHILE READING and not from the declared length, because a `Content-Length` is
        something the sender writes down, so a cap read from it is a cap the sender sets."""
        pictures = await _store(tmp_path)

        with pytest.raises(CoverPictureRefused) as refused:
            await pictures.receive(_reader(b"x" * (COVER_PICTURE_MAX_BYTES + 1)))

        assert "MB" in str(refused.value)

    async def test_an_empty_file_is_refused(self, tmp_path: Path) -> None:
        pictures = await _store(tmp_path)

        with pytest.raises(CoverPictureRefused):
            await pictures.receive(_reader(b""))

    async def test_something_that_is_not_a_picture_is_refused(self, tmp_path: Path) -> None:
        """And refused in Sift's own words. ffmpeg's stderr names the demuxers it tried and the
        path it was writing to, which tells the person who chose the file nothing and tells anybody
        else more than they should have."""
        pictures = await _store(tmp_path)

        with pytest.raises(CoverPictureRefused) as refused:
            await pictures.receive(_reader(b"this is not a picture, it is a sentence"))

        assert "JPEG" in str(refused.value)
        assert "ffmpeg" not in str(refused.value).lower()

    async def test_a_tool_that_succeeds_and_writes_nothing_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A zero exit is not proof there is a picture. `media_jobs.ffmpeg._seek` records a case
        where ffmpeg wrote nothing and exited 0. So the PICTURE is what gets checked, not the exit
        code."""
        pictures = await _store(tmp_path)

        async def wrote_nothing(*_args: Any, **_kwargs: Any) -> bytes:
            return b""

        monkeypatch.setattr(media, "run", wrote_nothing)

        with pytest.raises(CoverPictureRefused):
            await pictures.receive(_reader(a_png()))

    async def test_a_picture_nothing_points_at_is_forgotten(self, tmp_path: Path) -> None:
        """Row and file both. An uploaded cover is the one kind that OWNS bytes, so an un-chosen one
        is a cache that only ever grows with nothing on any screen to say so."""
        pictures = await _store(tmp_path)
        upload_id = await pictures.receive(_reader(a_png()))
        assert await pictures.path_of(upload_id) is not None

        await pictures.forget(upload_id)

        assert await pictures.path_of(upload_id) is None
        assert not (tmp_path / "cache" / "covers" / f"{upload_id}.jpg").exists()

    async def test_forgetting_something_twice_is_not_an_error(self, tmp_path: Path) -> None:
        """The cache is disposable and a file that is already gone is the outcome asked for."""
        pictures = await _store(tmp_path)
        upload_id = await pictures.receive(_reader(a_png()))

        await pictures.forget(upload_id)
        await pictures.forget(upload_id)

    async def test_a_row_pointing_outside_the_cache_is_not_followed(self, tmp_path: Path) -> None:
        """A restored backup or a hand-edited database is exactly where a row that predates a guard
        meets the code that assumes it. Compared lexically rather than resolved, because resolving
        follows a link and would judge it by where it points."""
        pictures = await _store(tmp_path)
        outside = tmp_path / "elsewhere.jpg"
        outside.write_bytes(b"\xff\xd8\xff\xd9")
        await pictures._db.execute(
            "INSERT INTO cover_pictures (id, rel_cache_path, size_bytes, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("escaping", "../elsewhere.jpg", 4, 0),
        )

        assert await pictures.path_of("escaping") is None


class TestReceivingOneForAnEntity:
    """The body the six upload routes share, and the cleanup that is why it is shared."""

    async def test_the_picture_is_stored_and_pointed_at(self, tmp_path: Path) -> None:
        pictures = await _store(tmp_path)
        pointed: list[str] = []

        async def point_at(upload_id: str) -> bool:
            pointed.append(upload_id)
            return True

        await receive_cover(pictures, _reader(a_png()), before=ChosenCover(), point_at=point_at)

        assert len(pointed) == 1
        assert await pictures.path_of(pointed[0]) is not None

    async def test_bytes_that_are_not_a_picture_answer_400(self, tmp_path: Path) -> None:
        """400 and not 404: the bytes were no good, which is something whoever chose the file can
        act on. A 404 would say the thing being given a cover is not there."""
        pictures = await _store(tmp_path)

        async def point_at(_upload_id: str) -> bool:  # pragma: no cover (never reached)
            return True

        with pytest.raises(HTTPException) as refused:
            await receive_cover(
                pictures, _reader(b"not a picture"), before=ChosenCover(), point_at=point_at
            )

        assert refused.value.status_code == 400

    async def test_a_picture_is_swept_when_the_pointer_does_not_land(self, tmp_path: Path) -> None:
        """The reason this body is shared rather than written six times. A picture is stored before
        anything points at it, so a write that does not land leaves a file and a row nothing
        references, and six copies of the undo is five chances to omit it in the arm nobody
        exercises."""
        pictures = await _store(tmp_path)
        offered: list[str] = []

        async def point_at(upload_id: str) -> bool:
            offered.append(upload_id)
            return False

        with pytest.raises(HTTPException) as refused:
            await receive_cover(pictures, _reader(a_png()), before=ChosenCover(), point_at=point_at)

        assert refused.value.status_code == 404
        assert await pictures.path_of(offered[0]) is None
        assert not any((tmp_path / "cache" / "covers").iterdir())

    async def test_the_picture_being_replaced_is_dropped(self, tmp_path: Path) -> None:
        pictures = await _store(tmp_path)
        was_there = await pictures.receive(_reader(a_png()))

        async def point_at(_upload_id: str) -> bool:
            return True

        await receive_cover(
            pictures,
            _reader(a_png()),
            before=ChosenCover(upload_id=was_there),
            point_at=point_at,
        )

        assert await pictures.path_of(was_there) is None

    async def test_a_file_cover_being_replaced_leaves_nothing_to_drop(self, tmp_path: Path) -> None:
        """A file cover points at something the library holds anyway and outlives being un-chosen.
        Only an uploaded one owns bytes."""
        pictures = await _store(tmp_path)

        await forget_displaced(pictures, ChosenCover(asset_id="a1", at_ms=0))


# --- a fetched picture as a subject's cover ------------------------------------------------------


#: The two doers the verbs are reached by, and the box a picture came from. Invented ids.
_RUN = Actor.sift("stash")
_PRESS = Actor.user("acct-1")
_BOX = Object(kind="box", id="box-1", name="Boxone")


class _Rows:
    """One kind of subject's cover column, stood in for: what each row points at."""

    def __init__(self, existing: dict[str, ChosenCover] | None = None) -> None:
        self.rows: dict[str, ChosenCover] = dict(existing or {})
        #: Who each write was said to be by, and which box it names: what its event records.
        self.said: dict[str, tuple[Actor, Object | None]] = {}

    async def chosen(self, local_id: str) -> ChosenCover:
        return self.rows.get(local_id, ChosenCover())

    async def point_at(
        self, local_id: str, upload_id: str, actor: Actor, box: Object | None
    ) -> bool:
        if local_id not in self.rows:
            return False
        self.rows[local_id] = ChosenCover(upload_id=upload_id)
        self.said[local_id] = (actor, box)
        return True


async def _subject_covers(tmp_path: Path, people: _Rows) -> tuple[SubjectCovers, CoverPictures]:
    pictures = await _store(tmp_path)
    covers = SubjectCovers(pictures)
    covers.register(Subject.PERSON, CoverHandle(chosen=people.chosen, point_at=people.point_at))
    return covers, pictures


async def test_filling_puts_a_picture_where_there_is_none_through_the_one_way_in(
    tmp_path: Path,
) -> None:
    """The bytes go through `receive` (the cap, the re-encode, the row after the file) and the
    subject's row is pointed at what came out. Nothing about a stash-box's picture is trusted
    that an uploaded one is not."""
    people = _Rows({"p1": ChosenCover()})
    covers, pictures = await _subject_covers(tmp_path, people)

    assert await covers.fill(Subject.PERSON, "p1", a_png(), actor=_RUN, box=_BOX) is True

    upload_id = people.rows["p1"].upload_id
    assert upload_id is not None
    assert await pictures.path_of(upload_id) is not None
    assert await covers.has_one(Subject.PERSON, "p1") is True


async def test_filling_stops_at_a_cover_of_either_kind(tmp_path: Path) -> None:
    """The unattended verb never replaces a choice: a still somebody chose from a clip, or a
    picture they uploaded, both stand."""
    people = _Rows({"p1": ChosenCover(asset_id="a1", at_ms=1000), "p2": ChosenCover()})
    covers, _pictures = await _subject_covers(tmp_path, people)
    assert await covers.fill(Subject.PERSON, "p2", a_png(), actor=_RUN, box=_BOX) is True
    before = people.rows["p2"]

    assert await covers.fill(Subject.PERSON, "p1", a_png(), actor=_RUN, box=_BOX) is False
    assert await covers.fill(Subject.PERSON, "p2", a_png(), actor=_RUN, box=_BOX) is False

    assert people.rows["p1"] == ChosenCover(asset_id="a1", at_ms=1000)
    assert people.rows["p2"] == before


async def test_keeping_replaces_and_sweeps_the_picture_it_displaced(tmp_path: Path) -> None:
    people = _Rows({"p1": ChosenCover()})
    covers, pictures = await _subject_covers(tmp_path, people)
    assert await covers.fill(Subject.PERSON, "p1", a_png(), actor=_RUN, box=_BOX) is True
    first = people.rows["p1"].upload_id
    assert first is not None

    assert await covers.keep(Subject.PERSON, "p1", a_png(), actor=_PRESS, box=_BOX) is True

    second = people.rows["p1"].upload_id
    assert second is not None and second != first
    assert await pictures.path_of(first) is None
    assert await pictures.path_of(second) is not None


async def test_each_verb_hands_its_own_doer_and_the_box_to_the_write(tmp_path: Path) -> None:
    """Who did it and where the picture came from reach the row's writer, which records both.

    With ONE actor, Sift, for every caller, a Keep picture press would be recorded as Sift's act and
    the line would say "Cover set to a new picture" without the box it came from."""
    people = _Rows({"p1": ChosenCover(), "p2": ChosenCover()})
    covers, _pictures = await _subject_covers(tmp_path, people)

    assert await covers.fill(Subject.PERSON, "p1", a_png(), actor=_RUN, box=_BOX) is True
    assert await covers.keep(Subject.PERSON, "p2", a_png(), actor=_PRESS, box=_BOX) is True

    assert people.said["p1"] == (_RUN, _BOX)
    assert people.said["p2"] == (_PRESS, _BOX)


def test_a_cover_payload_names_the_box_a_picture_came_from() -> None:
    """The event's own words: which act it was where no file is the cover, and the box."""
    assert cover_payload("a1", None, _BOX) is None
    assert json.loads(cover_payload(None, "u1", None) or "") == {"cover": "picture"}
    assert json.loads(cover_payload(None, "u1", _BOX) or "") == {
        "cover": "picture",
        "box": "Boxone",
        "box_id": "box-1",
    }
    # Taking a cover away came from no box, whatever the caller handed in.
    assert json.loads(cover_payload(None, None, _BOX) or "") == {"cover": "none"}
    # A box with no name kept is still named by its id, so the line can look the name up later
    # rather than saying "a stash-box" or, worse, an empty name.
    unnamed = Object(kind=_BOX.kind, id="box-2")
    assert json.loads(cover_payload(None, "u1", unnamed) or "") == {
        "cover": "picture",
        "box_id": "box-2",
    }


async def test_bytes_that_are_not_a_picture_are_refused_and_nothing_is_pointed(
    tmp_path: Path,
) -> None:
    people = _Rows({"p1": ChosenCover()})
    covers, _pictures = await _subject_covers(tmp_path, people)

    assert (
        await covers.fill(Subject.PERSON, "p1", b"<html>sign in</html>", actor=_RUN, box=_BOX)
        is False
    )
    assert people.rows["p1"] == ChosenCover()


async def test_a_row_that_has_gone_leaves_no_picture_behind(tmp_path: Path) -> None:
    """Stored before it is pointed at, so a row that vanished between the two is swept rather than
    left as a file nothing names."""
    people = _Rows()
    covers, pictures = await _subject_covers(tmp_path, people)

    assert await covers.fill(Subject.PERSON, "gone", a_png(), actor=_RUN, box=_BOX) is False
    assert await pictures.path_of("anything") is None


async def test_a_kind_nobody_registered_is_nothing_to_do(tmp_path: Path) -> None:
    covers, _pictures = await _subject_covers(tmp_path, _Rows())

    assert await covers.has_one(Subject.TAG, "t1") is False
    assert await covers.fill(Subject.TAG, "t1", a_png(), actor=_RUN, box=_BOX) is False
    assert await covers.keep(Subject.TAG, "t1", a_png(), actor=_PRESS, box=_BOX) is False


async def test_a_default_cover_yields_to_a_fetched_picture_and_a_cleared_one_takes_none(
    tmp_path: Path,
) -> None:
    """Where a cover stands is read off the entity's own row (`default_covers.standing`).

    `p1` wears the first file's still only because Sift's rule gave it that, so a stash-box's
    portrait takes its place. `p2` had its cover taken away by a person, so nothing unattended puts
    one back, however empty the pointers look.
    """
    people = _Rows({"p1": ChosenCover(asset_id="a1"), "p2": ChosenCover()})
    covers, pictures = await _subject_covers(tmp_path, people)
    async with pictures.database.write() as connection:
        await connection.execute(
            "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
            " VALUES ('a1', 'digest-a1', 1, 'video', 0)"
        )
        await connection.execute(
            "INSERT INTO people (id, name, created_at, cover_asset_id, cover_by_default)"
            " VALUES ('p1', 'Wren Halloway', 0, 'a1', 'a1')"
        )
        await connection.execute(
            "INSERT INTO people (id, name, created_at, cover_cleared_at)"
            " VALUES ('p2', 'Esme Wrenfield', 0, 1)"
        )

    assert await covers.has_one(Subject.PERSON, "p1") is False
    assert await covers.has_one(Subject.PERSON, "p2") is True
    assert await covers.fill(Subject.PERSON, "p2", a_png(), actor=_RUN, box=_BOX) is False
    assert people.rows["p2"] == ChosenCover()
    assert await covers.fill(Subject.PERSON, "p1", a_png(), actor=_RUN, box=_BOX) is True
    assert people.rows["p1"].upload_id is not None


def test_a_write_naming_no_picture_is_a_clear_and_any_picture_takes_the_mark_away() -> None:
    before = ChosenCover(asset_id="a1")
    cleared = cover_change(before, asset_id=None, at_ms=None, upload_id=None, frame=None)
    assert cleared.cleared_at is not None
    for asset_id, upload_id in (("a2", None), (None, "u1")):
        chosen = cover_change(
            before, asset_id=asset_id, at_ms=None, upload_id=upload_id, frame=None
        )
        assert chosen.cleared_at is None


class TestTheShippedLogo:
    """What an empty row answers with when the route hands in a picture that came with Sift.

    A Site nobody has chosen a picture for is drawn as its own site's logo, out of the pack in
    `kernel/site_icons`. The precedence lives here rather than at the route for the reason the
    rest of this module does: six things carry a cover, and a fallback written beside one of them
    is a second rule about which picture wins.
    """

    async def test_an_empty_row_is_answered_with_the_shipped_logo(self, tmp_path: Path) -> None:
        logo = tmp_path / "quillhouse.png"
        logo.write_bytes(b"\x89PNG\r\n\x1a\n")
        access = _Answers(None)
        pictures = await _store(tmp_path)

        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(),
                pictures=pictures,
                instead=logo,
            )

        answer = _asked(app)

        assert answer.status_code == 200
        assert answer.headers["content-type"] == "image/png"
        assert answer.content == b"\x89PNG\r\n\x1a\n"
        assert access.asked == [], "nothing in the library is read to answer with a shipped file"

    @staticmethod
    def _logo_app(tmp_path: Path, logo: Path, pictures: CoverPictures) -> FastAPI:
        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                _Answers(None),  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(),
                pictures=pictures,
                instead=logo,
            )

        return app

    async def test_the_shipped_logo_is_kept_under_the_address_that_names_it(
        self, tmp_path: Path
    ) -> None:
        """The logo is kept for a week only under the user's token AND the picture's own token.

        Not "never keepable", which would re-ask nearly every picture on each warm visit to the
        Sites wall for a reason about the address rather than the picture, so the address names
        the picture. `site_icons.token_of` is the picture's name: the
        release and a digest of the bytes, so a new pack moves it.
        """
        logo = tmp_path / "quillhouse.png"
        logo.write_bytes(b"\x89PNG\r\n\x1a\n")
        app = self._logo_app(tmp_path, logo, await _store(tmp_path))
        named = f"{face_version(A_VIEWER.cache_stamp)}.{token_of(logo)}"

        kept = _asked(app, art=named)

        assert kept.status_code == 200
        assert "immutable" in kept.headers["cache-control"]

    async def test_the_shipped_logo_under_any_other_address_is_re_checked(
        self, tmp_path: Path
    ) -> None:
        """The address means the logo until somebody chooses a picture and theirs after that, so a
        week-long promise is made only to an address that names the logo: the user's stamp
        alone does not (a new pack would stay the old logo for a week), nor the picture's token
        without the stamp, nor a bare address."""
        logo = tmp_path / "quillhouse.png"
        logo.write_bytes(b"\x89PNG\r\n\x1a\n")
        app = self._logo_app(tmp_path, logo, await _store(tmp_path))
        stamp = face_version(A_VIEWER.cache_stamp)

        for art in (stamp, f"{stamp}.not-this-picture", f".{token_of(logo)}", None):
            answer = _asked(app, art=art)
            assert answer.status_code == 200
            assert "no-cache" in answer.headers["cache-control"], art
            assert "immutable" not in answer.headers["cache-control"], art

    async def test_a_changed_picture_is_a_different_token(self, tmp_path: Path) -> None:
        """The token is the BYTES' name, so a pack that fetched a new logo for the same site moves
        the address rather than leaving the old picture kept under it."""
        one = tmp_path / "one.png"
        one.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
        two = tmp_path / "two.png"
        two.write_bytes(b"\x89PNG\r\n\x1a\nsecond")

        assert token_of(one) != token_of(two)

    async def test_a_site_with_no_logo_in_the_pack_is_still_a_404(self, tmp_path: Path) -> None:
        """The pack is a few hundred sites and a library holds whatever it holds, so most of the
        answers here are None, and None has to leave the behaviour exactly as it was."""
        with pytest.raises(HTTPException) as refused:
            await serve_cover(
                _request(),
                _Answers(None),  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(),
                pictures=await _store(tmp_path),
                instead=None,
            )

        assert refused.value.status_code == 404

    async def test_a_chosen_cover_that_is_not_rendered_yet_keeps_its_404(
        self, tmp_path: Path
    ) -> None:
        """The narrow case, and the reason the fallback asks whether the row is EMPTY rather than
        whether there is anything to send. A still at a chosen moment is queued when the moment is
        chosen; substituting a logo for the seconds it takes would read as the cover having been
        replaced, on the machine that had just chosen it."""
        logo = tmp_path / "quillhouse.png"
        logo.write_bytes(b"\x89PNG\r\n\x1a\n")

        with pytest.raises(HTTPException) as refused:
            await serve_cover(
                _request(),
                _Answers(None),  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1", at_ms=61_500),
                pictures=await _store(tmp_path),
                instead=logo,
            )

        assert refused.value.status_code == 404

    async def test_a_whole_file_cover_that_cannot_be_served_falls_through_to_the_logo(
        self, tmp_path: Path
    ) -> None:
        """The other half of that narrow case.

        The 404 above is kept because the client has a SECOND picture to try: the file's own
        still. A cover naming a whole file with no moment IS that still, so the second address is
        the same derivative, both are missing together, and without this the card would fall all the
        way to a coloured letter on a Site whose logo ships with Sift.
        """
        logo = tmp_path / "quillhouse.png"
        logo.write_bytes(b"\x89PNG\r\n\x1a\n")

        answer = await serve_cover(
            _request(),
            _Answers(None),  # type: ignore[arg-type]
            A_VIEWER,
            chosen=ChosenCover(asset_id="a1"),
            pictures=await _store(tmp_path),
            instead=logo,
        )

        assert answer.status_code == 200
        assert answer.media_type == "image/png"

    async def test_a_whole_file_cover_with_no_logo_to_fall_through_to_is_still_a_404(
        self, tmp_path: Path
    ) -> None:
        """A Site the pack has no picture for, and every other kind of entity, which pass None."""
        with pytest.raises(HTTPException) as refused:
            await serve_cover(
                _request(),
                _Answers(None),  # type: ignore[arg-type]
                A_VIEWER,
                chosen=ChosenCover(asset_id="a1"),
                pictures=await _store(tmp_path),
                instead=None,
            )

        assert refused.value.status_code == 404


# --- a frame that cannot be cut ---------------------------------------------------------------

_A_WINDOW = CoverFrame(x=0.25, y=0.0, w=0.5, h=1.0)


class TestAFrameThatCannotBeCut:
    """The whole picture, sent the careful way: its address names the frame, and a kept copy of
    the wrong window under it is the stale picture the address exists to prevent."""

    async def test_a_picture_that_is_not_there_is_not_cut(self, tmp_path: Path) -> None:
        pictures = await _store(tmp_path)

        assert await pictures.framed(tmp_path / "gone.png", _A_WINDOW) is None

    async def test_a_cut_the_tool_refuses_leaves_nothing_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pictures = await _store(tmp_path)
        source = tmp_path / "cover.png"
        source.write_bytes(a_png())

        async def refuses(*args: object, **kwargs: object) -> bytes:
            raise media.FFmpegError("ffmpeg failed: Invalid data found when processing input")

        monkeypatch.setattr(media, "run", refuses)

        assert await pictures.framed(source, _A_WINDOW) is None
        assert not list((tmp_path / "cache").rglob("*.jpg")), "a half-made cut was left"

    async def test_a_tool_that_succeeds_and_writes_no_cut_is_no_cut(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A zero exit is not proof there is a picture."""
        pictures = await _store(tmp_path)
        source = tmp_path / "cover.png"
        source.write_bytes(a_png())

        async def says_nothing(*args: object, **kwargs: object) -> bytes:
            return b""

        monkeypatch.setattr(media, "run", says_nothing)

        assert await pictures.framed(source, _A_WINDOW) is None
        assert not list((tmp_path / "cache").rglob("*.jpg"))

    @pytest.mark.parametrize("uploaded", [True, False], ids=["upload", "file"])
    async def test_the_whole_picture_is_sent_carefully_where_the_cut_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uploaded: bool
    ) -> None:
        pictures = await _store(tmp_path)
        picture = tmp_path / "cover.jpg"
        picture.write_bytes(b"\xff\xd8\xff\xd9")
        access = _Answers(ServedDerivative(path=picture, version="v1", concealed=False))
        upload_id = await pictures.receive(_reader(a_png())) if uploaded else None

        async def cannot(self: CoverPictures, source: Path, frame: CoverFrame) -> None:
            return None

        monkeypatch.setattr(CoverPictures, "framed", cannot)
        chosen = (
            ChosenCover(upload_id=upload_id, frame=_A_WINDOW)
            if uploaded
            else ChosenCover(asset_id="a1", frame=_A_WINDOW)
        )
        app = FastAPI()

        @app.get("/cover")
        async def read(request: Request) -> Any:
            return await serve_cover(
                request,
                access,  # type: ignore[arg-type]
                A_VIEWER,
                chosen=chosen,
                pictures=pictures,
            )

        answer = _asked(app, art=f"v1.a1.{_A_WINDOW.token}")

        assert answer.status_code == 200
        assert answer.headers["cache-control"] == "private, no-cache"
        if not uploaded:
            assert answer.content == b"\xff\xd8\xff\xd9", "not the whole picture"


def test_an_address_with_a_token_names_no_cover_where_there_is_none() -> None:
    """Nothing chosen is nothing to keep, whatever the address carries."""
    from sift.kernel.covers import names_its_cover

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "headers": [],
            "query_string": b"v=v1.a1",
            "app": FastAPI(),
        }
    )

    assert names_its_cover(request, ChosenCover(), stamp=0) is False
