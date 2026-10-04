# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture a creator is shown with, and never a site's.

Two properties matter more than the fetching, and both are here: it happens once per creator rather
than once per download (five hundred items from one creator must not be five hundred requests for
one picture), and it can never fail a download that has already succeeded. A Site's mark is the
icon pack that ships with Sift, so no download asks a site for its own picture at all.

The fetch mechanics below are exercised under plain scopes because the store does not care what a
scope names; what decides that only creators are fetched is `keeper`, and it has tests of its own.

Most of them hand the store a stand-in for the cover door (`_Door`), because what they test is the
store's bookkeeping and not ffmpeg. The door's own promise (a site's bytes never kept, an SVG never
kept as one, a picture re-encoded to Sift's own JPEG at its own size) is tested against the REAL
door at the end, under "the door".
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import http
from sift.kernel.config import Settings
from sift.kernel.covers import CoverPictureRefused, CoverPictures
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.download import art as art_mod
from sift.slices.download.art import ArtStore
from sift.testing.library import a_png

pytestmark = pytest.mark.anyio


#: What the stand-in door writes before the bytes it was handed, so a test can see both that the
#: picture went through the door and that all of it did.
_DOOR = b"made by the door:"


def _kept(blob: bytes) -> bytes:
    """What the stand-in door keeps for these bytes."""
    return _DOOR + blob


class _Door:
    """The cover door's shape (`CoverPictures`), keeping a PNG-looking blob and refusing the rest.

    The real door pipes the bytes into ffmpeg; this one writes them behind a mark into the same
    folder under the same kind of name, which is all the store's bookkeeping can see.
    """

    def __init__(self, cache: Path) -> None:
        self._cache = cache
        self.made: dict[str, str] = {}
        self.forgotten: list[str] = []

    async def receive(self, read: Callable[[int], Any]) -> str:
        blob = b""
        while chunk := await read(1 << 20):
            blob += chunk
        if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
            raise CoverPictureRefused("not a picture")
        made = new_id()
        path = self._cache / "covers" / f"{made}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_kept(blob))
        self.made[made] = f"covers/{made}.jpg"
        return made

    async def kept_as(self, upload_id: str) -> str | None:
        return self.made.get(upload_id)

    async def forget(self, upload_id: str) -> None:
        self.forgotten.append(upload_id)


@pytest.fixture
def door(tmp_path: Path) -> _Door:
    return _Door(tmp_path)


@pytest.fixture
async def store(temp_db: Database, tmp_path: Path, door: _Door) -> ArtStore:
    await temp_db.initialize_schema()
    return ArtStore(temp_db, tmp_path, door)  # type: ignore[arg-type]


#: The first bytes of a PNG, which is what makes these fixtures pictures rather than words.
#:
#: The store checks that what arrived begins the way a picture does, so a placeholder like
#: `b"a picture"` is refused: it is a site answering with prose. The tag after the
#: signature is only so one fixture can be told from another in an assertion.
def _png(tag: bytes = b"") -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag


async def test_a_site_nobody_has_a_picture_for_has_none(store: ArtStore) -> None:
    assert await store.known("youtube") is None


async def test_a_stored_path_is_relative_to_the_cache(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What is written down, and it is not an absolute path.

    Every reference to a cached file in Sift is relative to the cache directory, so nothing breaks
    when somebody moves their cache folder. Asserted on the STORED value rather than on the file,
    because the file would be found either way and the stored value is the part that matters.
    """
    page = b'<head><meta property="og:image" content="https://example.test/her.png"/></head>'
    opened, _ = _serving(
        {
            "https://example.test/": _Response(200, page),
            "https://example.test/her.png": _Response(200, _png(b"a picture")),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)
    await store.fill_if_empty("example:her", "https://example.test/")

    row = await store._db.fetch_one("SELECT path FROM site_art WHERE scope = ?", ("example:her",))
    assert row is not None
    assert str(row["path"]).startswith("covers/")

    kept = await store.known("example:her")
    assert kept is not None
    assert kept.path.is_absolute()
    assert kept.path.is_file()


async def test_a_path_still_absolute_is_brought_through_the_door(
    store: ArtStore, tmp_path: Path
) -> None:
    """A database restored from a backup taken before the column became relative.

    Joining an absolute path onto the cache directory would produce a nonsense path, so the file is
    read where it stands, and like every row kept the old way it is brought through the door the
    first time it is read: what is answered is the door's picture, never that file.
    """
    picture = tmp_path / "somewhere-else" / "youtube.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(_png(b"a picture"))
    await store._db.execute(
        "INSERT INTO site_art (scope, path, stored_at) VALUES (?, ?, ?)",
        ("youtube", str(picture), 4_000_000_000),
    )

    kept = await store.known("youtube")

    assert kept is not None
    assert kept.path != picture
    assert kept.path.read_bytes() == _kept(_png(b"a picture"))


async def test_a_second_download_from_a_site_does_not_fetch_again(
    store: ArtStore, tmp_path: Path
) -> None:
    """The whole design constraint. A bulk paste is one fetch for the logo, or none."""
    picture = tmp_path / "site-art" / "youtube.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(_png(b"a picture"))
    await store._db.execute(
        "INSERT INTO site_art (scope, path, stored_at) VALUES (?, ?, ?)",
        ("youtube", str(picture), 4_000_000_000),
    )

    fetched: list[str] = []

    async def _never_called(*args: object, **kwargs: object) -> None:
        fetched.append("fetched")

    store._fetch = _never_called  # type: ignore[method-assign]
    await store.fill_if_empty("youtube", "https://youtube.com/")
    assert fetched == []


async def test_a_site_whose_picture_was_cleared_is_fetched_again(
    store: ArtStore, tmp_path: Path
) -> None:
    """A row whose file has gone (somebody emptied the cache) is not a picture. Reported as
    present it would make every screen ask for a file that is not there."""
    await store._db.execute(
        "INSERT INTO site_art (scope, path, stored_at) VALUES (?, ?, ?)",
        ("youtube", str(tmp_path / "site-art" / "gone.png"), 4_000_000_000),
    )
    assert await store.known("youtube") is None


async def test_a_fetch_that_goes_wrong_never_reaches_the_caller(store: ArtStore) -> None:
    """This runs behind a download that has already worked. A site with an odd page, a slow answer
    or no artwork at all must not turn a finished download into a failed one."""

    async def _explodes(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the site answered with nonsense")

    store._fetch = _explodes  # type: ignore[method-assign]
    await store.fill_if_empty("youtube", "https://youtube.com/")


async def test_a_picture_is_stored_and_read_back(store: ArtStore, tmp_path: Path) -> None:
    picture = tmp_path / "site-art" / "redgifs.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(_png(b"a picture"))
    await store._db.execute(
        "INSERT INTO site_art (scope, path, stored_at) VALUES (?, ?, ?)",
        ("redgifs", str(picture), 100),
    )

    known = await store.known("redgifs")
    assert known is not None
    assert known.path.read_bytes() == _kept(_png(b"a picture"))
    # Brought through once and written down, keeping its date: the next read is the door's row.
    row = await store._db.fetch_one("SELECT path, stored_at FROM site_art WHERE scope = 'redgifs'")
    assert row is not None
    assert str(row["path"]).startswith("covers/")
    assert int(row["stored_at"]) == 100


async def test_the_picture_and_a_chosen_cover_are_not_the_same_field(temp_db: Database) -> None:
    """The reason a scraped picture can never replace a chosen one is that they are kept apart.

    A cover is a pointer to a file in the library. This is a picture from a website. Nothing checks
    before overwriting because there is no field for one to overwrite the other in.
    """
    await temp_db.initialize_schema()
    art_columns = {
        str(row["name"])
        for row in await temp_db.fetch_all("SELECT name FROM pragma_table_info('site_art')")
    }
    assert "cover_asset_id" not in art_columns

    # And the sites table has the cover field, so a chosen one has somewhere of its own to live. A
    # username no longer has one: it has no page of its own, and the catalog's v58 step dropped it.
    site_columns = {
        str(row["name"])
        for row in await temp_db.fetch_all("SELECT name FROM pragma_table_info('sites')")
    }
    assert "cover_asset_id" in site_columns


# --- fetching one -------------------------------------------------------------------------------


class _Response:
    """One canned answer from a site, shaped the way the transfer reads it."""

    def __init__(self, status: int, body: bytes, kind: str = "image/png") -> None:
        self.status = status
        self.content = _Body(body)
        self.headers = {"Content-Type": kind}

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _Body:
    """A response body that arrives the way a real one does: in pieces, a few bytes at a time.

    A real body is read in whatever has arrived so far, so code that asked for two megabytes and
    did not loop would get the first packet and write it out as a complete picture. A double that
    handed everything over in one call would be more obliging than the network and hide that.

    Deliberately small pieces, smaller than anything the code asks for, so a reader that does not
    loop is caught rather than accidentally satisfied.
    """

    PIECE = 3

    def __init__(self, blob: bytes) -> None:
        self._blob = blob

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        for at in range(0, len(self._blob), self.PIECE):
            yield self._blob[at : at + self.PIECE]


class _Session:
    """A site that answers with a page and then with a picture. Records what was asked for."""

    def __init__(self, answers: dict[str, _Response]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def get(self, url: str) -> _Response:
        self.asked.append(url)
        return self.answers.get(url, _Response(404, b""))

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def _serving(
    answers: dict[str, _Response],
) -> tuple[Callable[..., Any], _Session]:
    session = _Session(answers)

    @contextlib.asynccontextmanager
    async def opened(**_kwargs: object) -> AsyncIterator[_Session]:
        yield session

    return opened, session


async def test_a_picture_is_found_whichever_way_round_the_page_writes_it(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTML does not order attributes, and a real page may write `content` before `property`."""
    page = b'<head><meta content="https://example.test/her.png" property="og:image"/></head>'
    opened, _ = _serving(
        {
            "https://example.test/": _Response(200, page),
            "https://example.test/her.png": _Response(200, _png(b"her picture")),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")

    known = await store.known("example")
    assert known is not None
    assert known.path.read_bytes() == _kept(_png(b"her picture"))


async def test_a_relative_address_is_resolved_against_the_page(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = b'<head><meta property="og:image" content="/pictures/her.png"></head>'
    opened, _session = _serving(
        {
            "https://example.test/": _Response(200, page),
            "https://example.test/pictures/her.png": _Response(200, _png(b"her picture")),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")
    assert await store.known("example") is not None


async def test_a_site_that_declares_no_picture_simply_has_none(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is generated. A screen draws the site's name, which is a fine answer."""
    opened, _ = _serving({"https://example.test/": _Response(200, b"<head></head>")})
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")
    assert await store.known("example") is None


async def test_a_page_that_will_not_load_leaves_the_site_without_one(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened, _ = _serving({})
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")
    assert await store.known("example") is None


async def test_a_picture_that_will_not_load_leaves_the_site_without_one(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = b'<head><meta property="og:image" content="https://example.test/gone.png"></head>'
    opened, _ = _serving({"https://example.test/": _Response(200, page)})
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")
    assert await store.known("example") is None


async def test_something_far_too_large_to_be_a_logo_is_not_kept(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is arriving from a machine Sift does not control, and a logo is tens of kilobytes."""
    page = b'<head><meta property="og:image" content="https://example.test/huge.png"></head>'
    opened, _ = _serving(
        {
            "https://example.test/": _Response(200, page),
            "https://example.test/huge.png": _Response(200, _png(b"x" * art_mod.MAX_BYTES)),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")
    assert await store.known("example") is None


async def test_the_kept_file_is_named_by_sift_rather_than_by_the_site(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name a site chose is a name from a remote machine. This one cannot be anything but a
    filename, whatever the address said."""
    page = (
        b'<head><meta property="og:image" content="https://example.test/../../etc/passwd"></head>'
    )
    opened, _ = _serving(
        {
            "https://example.test/": _Response(200, page),
            "https://example.test/../../etc/passwd": _Response(200, _png(b"the artwork")),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("../../sneaky", "https://example.test/")
    known = await store.known("../../sneaky")
    assert known is not None
    assert ".." not in known.path.name
    assert "/" not in known.path.name


@pytest.mark.parametrize(
    ("address", "kind"),
    [
        ("logo.exe", "image/png"),
        ("logo", "application/octet-stream"),
        ("logo.svg", "image/svg+xml"),
    ],
    ids=["a suffix in the address", "no useful type", "a type that says SVG"],
)
async def test_what_is_kept_is_the_doors_picture_whatever_the_site_said(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch, address: str, kind: str
) -> None:
    """A suffix in a link is a claim by whoever wrote the link, and a content type a claim by
    whoever sent the bytes. Neither decides anything now: the door reads the bytes and writes Sift's
    own picture, and that is the one kind of file this store ever names."""
    page = f'<head><meta property="og:image" content="https://example.test/{address}"></head>'
    opened, _ = _serving(
        {
            "https://example.test/": _Response(200, page.encode()),
            f"https://example.test/{address}": _Response(200, _png(b"bytes"), kind),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")
    known = await store.known("example")
    assert known is not None
    assert known.path.suffix == ".jpg"
    assert known.path.read_bytes() == _kept(_png(b"bytes"))


async def test_something_that_is_not_a_picture_at_all_is_not_kept(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A site that answers a request for its artwork with a page: a login wall, an error, a
    redirect. Nothing is kept: a page written out under a `.png` is a broken image on every row that
    site appears on, which is worse than the site having no picture at all."""
    page = b'<head><meta property="og:image" content="https://example.test/logo.png"></head>'
    opened, _ = _serving(
        {
            "https://example.test/": _Response(200, page),
            "https://example.test/logo.png": _Response(200, b"<html>no</html>", "text/html"),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")
    assert await store.known("example") is None
    # Refused at the WRITE, not merely hidden at the read: no row and no file. The read also
    # checks the bytes, so only the absence of the row proves the write-side check.
    rows = await store._db.fetch_all("SELECT scope FROM site_art")
    assert [str(row["scope"]) for row in rows] == []
    assert not (store._cache / "site-art").exists()


async def test_a_picture_that_arrives_in_pieces_is_kept_whole(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A picture bigger than one packet is read whole.

    A response body is read in whatever has arrived so far, not in the amount asked for, so a
    reader that did not loop would write a picture out as its first fragment: a logo stored as a
    band of colour with the rest missing. Nothing would fail anywhere: a truncated PNG is a file,
    and a browser draws as much of one as it was given.
    """
    picture = _png(bytes(range(256)) * 40)  # comfortably more than the double hands over at once
    page = b'<head><meta property="og:image" content="https://example.test/share.png"></head>'
    opened, _ = _serving(
        {
            "https://example.test/": _Response(200, page),
            "https://example.test/share.png": _Response(200, picture),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")

    known = await store.known("example")
    assert known is not None
    assert known.path.read_bytes() == _kept(picture)


async def test_a_page_that_arrives_in_pieces_is_read_to_the_end(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rule one step earlier, for the page that declares the picture.

    What a site declares sits in its head, which on a real page is behind a few kilobytes of
    scripts and stylesheets, so a reader that stopped at the first piece of the body would find no
    artwork declared and give up.
    """
    page = b"<head>" + b"<script></script>" * 200
    page += b'<meta property="og:image" content="https://example.test/share.png"></head>'
    opened, _ = _serving(
        {
            "https://example.test/": _Response(200, page),
            "https://example.test/share.png": _Response(200, _png(b"the artwork")),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")
    assert await store.known("example") is not None


async def test_a_link_from_a_site_with_no_record_asks_for_nothing(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A picture is not worth a request to somewhere Sift does not recognise."""
    opened, session = _serving({})
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await art_mod.keeper(store)("https://nowhere-sift-knows.example/x", None, "someone")
    assert session.asked == []


async def test_a_download_never_asks_a_site_for_its_own_picture(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Site's mark is the icon pack Sift ships, or its letter, never a picture fetched from it.

    Three downloads: one with no creator, one whose site has no profile addresses, and one with a
    creator whose site does. Only the last asks anything, and only for the creator's own page,
    never the site's front page or its `/favicon.ico`.
    """
    opened, session = _serving({})
    monkeypatch.setattr(art_mod, "guarded_session", opened)
    keep = art_mod.keeper(store)

    await keep("https://www.youtube.com/watch?v=1", None, None)
    await keep("https://coomer.st/onlyfans/user/someone", None, "someone")
    await keep("https://pmvhaven.com/video/x", None, "Hollowgrain")

    assert session.asked == ["https://pmvhaven.com/profile/Hollowgrain"]
    rows = await store._db.fetch_all("SELECT scope FROM site_art")
    assert [str(row["scope"]) for row in rows] == []


async def test_a_download_with_a_creator_asks_that_creators_own_page(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One picture, one request: the creator's own, from their profile. It is the face beside a name
    on every screen that shows one."""
    opened, session = _serving({})
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await art_mod.keeper(store)("https://pmvhaven.com/video/x", None, "Hollowgrain")

    assert session.asked == ["https://pmvhaven.com/profile/Hollowgrain"]


async def test_a_creators_picture_is_their_own_rather_than_the_sites(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile page's sharing image is that person's picture, while its icon is the site's mark,
    so the icon is never taken, even where the page declares it first."""
    profile = (
        b'<head><link rel="apple-touch-icon" href="/site-mark.png">'
        b'<meta property="og:image" content="https://pmvhaven.com/her.jpg"></head>'
    )
    opened, _ = _serving(
        {
            "https://pmvhaven.com/profile/Hollowgrain": _Response(200, profile),
            "https://pmvhaven.com/her.jpg": _Response(200, _png(b"her picture")),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await art_mod.keeper(store)("https://pmvhaven.com/video/x", None, "Hollowgrain")

    hers = await store.known(art_mod.creator_scope("pmvhaven", "Hollowgrain"))
    assert hers is not None
    assert hers.path.read_bytes() == _kept(_png(b"her picture"))


async def test_a_creator_whose_page_names_no_picture_gets_none_rather_than_the_sites_logo(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one fallback that must not exist.

    A profile page declares two things: that person's picture, and the site's own tab icon. Taking
    the icon when there is no picture gives a person the site's logo as their face: the same face
    for everybody on that site, which is worse than the monogram it would replace.
    """
    profile = b'<head><link rel="apple-touch-icon" href="https://site.test/logo.png"></head>'
    opened, _ = _serving(
        {
            "https://site.test/profile/someone": _Response(200, profile),
            "https://site.test/logo.png": _Response(200, _png(b"the site's own logo")),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("site:someone", "https://site.test/profile/someone")

    assert await store.known("site:someone") is None


async def test_a_creator_page_that_declares_nothing_takes_no_favicon(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root favicon is the SITE's own mark, and a person's page on that site serves the same
    one, so taking it for a creator gives every creator on the site the same face, which is the
    whole thing the no-fallback rule exists to prevent."""
    opened, session = _serving(
        {
            "https://example.test/u/someone": _Response(
                200, b"<head><title>nothing</title></head>"
            ),
            "https://example.test/favicon.ico": _Response(200, _png(b"the mark"), "image/x-icon"),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("site:someone", "https://example.test/u/someone")

    assert not any(one.endswith("favicon.ico") for one in session.asked)
    assert await store.known("site:someone") is None


async def test_a_creators_row_whose_file_is_not_a_picture_is_no_picture(
    store: ArtStore, tmp_path: Path
) -> None:
    """The row is not the answer: the file behind it is.

    A cache emptied and refilled by something else, a truncated write, a half-finished download: any
    of them leaves a row naming a file that is no longer a picture, and reported as present it makes
    every card on a screen full of People ask for a broken image.
    """
    not_a_picture = tmp_path / "site-art" / "someone.png"
    not_a_picture.parent.mkdir(parents=True)
    not_a_picture.write_bytes(b"<html>a login wall</html>")
    await store._db.execute(
        "INSERT INTO site_art (scope, path, stored_at) VALUES (?, ?, ?)",
        ("youtube:someone", str(not_a_picture), 4_000_000_000),
    )

    assert await store.for_creator("someone") is None


async def test_reading_a_picture_stops_at_the_cap_rather_than_at_the_far_end(
    store: ArtStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is reading from a machine Sift does not control, so the read is bounded here rather than
    by whatever that machine feels like sending, and arriving AT the cap is what the caller then
    treats as too big."""
    page = b'<head><meta property="og:image" content="https://example.test/huge.png"></head>'
    opened, _ = _serving(
        {
            "https://example.test/": _Response(200, page),
            "https://example.test/huge.png": _Response(
                200, _png(b"x" * (art_mod.MAX_BYTES + http.chunk_bytes()))
            ),
        }
    )
    monkeypatch.setattr(art_mod, "guarded_session", opened)

    await store.fill_if_empty("example", "https://example.test/")
    assert await store.known("example") is None


# --- a picture handed in by another feature ------------------------------------------------------


async def test_a_picture_handed_in_fills_a_blank(store: ArtStore) -> None:
    """Bytes that arrived some other way are kept under the scope and read back as a picture."""
    assert await store.keep_bytes("onlyfans:quillmoss", _png(b"from a box")) is True
    known = await store.known("onlyfans:quillmoss")
    assert known is not None
    assert known.path.read_bytes() == _kept(_png(b"from a box"))


async def test_a_picture_handed_in_never_replaces_one_held(store: ArtStore) -> None:
    """The rule a fetch keeps holds for handed-in bytes too: a creator's picture stays theirs."""
    assert await store.keep_bytes("onlyfans:quillmoss", _png(b"first")) is True
    assert await store.keep_bytes("onlyfans:quillmoss", _png(b"second")) is False
    known = await store.known("onlyfans:quillmoss")
    assert known is not None
    assert known.path.read_bytes() == _kept(_png(b"first"))


@pytest.mark.parametrize(
    "blob",
    [b"", b"<html>a login wall</html>", _png(b"x" * art_mod.MAX_BYTES)],
    ids=["empty", "not a picture", "too large"],
)
async def test_handed_in_bytes_that_are_not_a_picture_are_not_kept(
    store: ArtStore, blob: bytes
) -> None:
    assert await store.keep_bytes("onlyfans:quillmoss", blob) is False
    assert await store._db.fetch_all("SELECT scope FROM site_art") == []


@pytest.mark.parametrize(
    ("site", "address", "scope"),
    [
        # A site Sift downloads from: its catalog key, the row a download's own read fills.
        ("PMVHaven", "https://pmvhaven.com/profile/QuillMoss", "pmvhaven:quillmoss"),
        # A host only the icon pack knows: the pack's slug for it.
        ("OnlyFans", "https://onlyfans.com/QuillMoss", "onlyfans:quillmoss"),
        # No page: the pack's slug for the Site's name.
        ("Fansly", None, "fansly:quillmoss"),
    ],
)
def test_a_creators_scope_is_said_the_way_a_download_would(
    site: str, address: str | None, scope: str
) -> None:
    assert art_mod.creator_scope_for(site=site, username="QuillMoss", address=address) == scope


def test_a_creator_on_a_site_nothing_names_has_no_scope() -> None:
    """A picture filed under a guessed site would never be found again, so there is none."""
    assert (
        art_mod.creator_scope_for(
            site="Marrowvale Studios", username="quillmoss", address="https://example.test/q"
        )
        is None
    )


async def test_a_creator_with_a_picture_costs_the_box_no_request(store: ArtStore) -> None:
    """The picture is asked for only where there is none, so a held one is never fetched again."""
    await store.keep_bytes("onlyfans:quillmoss", _png(b"held"))
    asked: list[str] = []

    async def picture() -> bytes | None:
        asked.append("asked")
        return _png(b"new")

    kept = await art_mod.CreatorPictures(store).keep(
        site="OnlyFans",
        username="quillmoss",
        address="https://onlyfans.com/quillmoss",
        picture=picture,
    )
    assert kept is False
    assert asked == []


async def test_a_creator_with_none_is_given_the_one_handed_in(store: ArtStore) -> None:
    async def picture() -> bytes | None:
        return _png(b"from a box")

    kept = await art_mod.CreatorPictures(store).keep(
        site="OnlyFans", username="quillmoss", address=None, picture=picture
    )
    assert kept is True
    found = await store.for_creator("quillmoss")
    assert found is not None
    assert found.path.read_bytes() == _kept(_png(b"from a box"))


async def test_a_picture_that_will_not_come_is_not_an_error(store: ArtStore) -> None:
    """The match that led here has already been written; a picture is a nicety."""

    async def explodes() -> bytes | None:
        raise RuntimeError("the box went away")

    async def nothing() -> bytes | None:
        return None

    pictures = art_mod.CreatorPictures(store)
    for picture in (explodes, nothing):
        assert (
            await pictures.keep(
                site="OnlyFans", username="quillmoss", address=None, picture=picture
            )
            is False
        )
    assert await store._db.fetch_all("SELECT scope FROM site_art") == []


async def test_a_refreshed_picture_lets_the_one_it_replaces_go(
    store: ArtStore, door: _Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A picture past its time is fetched again, and the door's old one is forgotten, not kept.

    The door's pictures live in the cover store, where nothing but a pointer keeps one: a refresh
    that left the old one behind would be a cache that only ever grows.
    """
    assert await store.keep_bytes("onlyfans:quillmoss", _png(b"old")) is True
    [first] = list(door.made)
    monkeypatch.setattr(art_mod, "KEEP_FOR_SECONDS", -1)

    assert await store.keep_bytes("onlyfans:quillmoss", _png(b"new")) is True

    assert door.forgotten == [first]
    known = await store.known("onlyfans:quillmoss")
    assert known is not None
    assert known.path.read_bytes() == _kept(_png(b"new"))


async def test_an_old_row_the_door_refuses_is_dropped(store: ArtStore, tmp_path: Path) -> None:
    """A row kept the old way whose file the door will not take (an SVG kept as a site sent it) is
    no picture, and its row goes, so the next download from that creator asks again."""
    kept_raw = tmp_path / "site-art" / "onlyfans_quillmoss.svg"
    kept_raw.parent.mkdir(parents=True)
    kept_raw.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>")
    await store._db.execute(
        "INSERT INTO site_art (scope, path, stored_at) VALUES (?, ?, ?)",
        ("onlyfans:quillmoss", "site-art/onlyfans_quillmoss.svg", 4_000_000_000),
    )

    assert await store.for_creator("quillmoss") is None
    assert await store._db.fetch_all("SELECT scope FROM site_art") == []


async def test_a_row_whose_picture_left_the_cache_is_no_picture(store: ArtStore) -> None:
    """The cache is Sift's to empty, so a row can outlive its file: that is no picture, and
    neither is a file left empty by a write that never finished."""
    await store.keep_bytes("onlyfans:quillmoss", _png(b"held"))
    held = await store.known("onlyfans:quillmoss")
    assert held is not None

    held.path.write_bytes(b"")
    assert await store.known("onlyfans:quillmoss") is None
    held.path.unlink()
    assert await store.known("onlyfans:quillmoss") is None


async def test_an_old_row_another_screen_brought_through_first_is_not_made_twice(
    store: ArtStore, door: _Door, tmp_path: Path
) -> None:
    """Two screens ask for the same old row at once: the one that waited for the lock reads the
    row again and answers what the first made, and a row dropped meanwhile is no picture."""
    old = tmp_path / "site-art" / "onlyfans_quillmoss.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(_png(b"old"))
    stored = "site-art/onlyfans_quillmoss.png"
    await store._db.execute(
        "INSERT INTO site_art (scope, path, stored_at) VALUES (?, ?, ?)",
        ("onlyfans:quillmoss", stored, 4_000_000_000),
    )
    first = await store.known("onlyfans:quillmoss")
    assert first is not None and len(door.made) == 1

    again = await store._through_the_door("onlyfans:quillmoss", stored, 4_000_000_000)
    assert again is not None and again.path == first.path
    assert len(door.made) == 1, "the row was brought through a second time"

    assert await store._through_the_door("fansly:nobody", stored, 4_000_000_000) is None


def test_a_creator_with_no_name_has_no_scope() -> None:
    assert art_mod.creator_scope_for(site="Fansly", username="  ", address=None) is None


# --- the door ------------------------------------------------------------------------------------
#
# The real one: `CoverPictures` over a real database and the real ffmpeg, because what these hold is
# the security property itself and a stand-in would only be testing itself.


@pytest.fixture
async def real_store(temp_db: Database, tmp_path: Path) -> ArtStore:
    await temp_db.initialize_schema()
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    return ArtStore(temp_db, settings.cache_dir, CoverPictures(temp_db, settings))


def _size_of_jpeg(blob: bytes) -> tuple[int, int]:
    """A JPEG's width and height, read from its frame header."""
    at = 2
    while at < len(blob):
        marker, length = blob[at + 1], int.from_bytes(blob[at + 2 : at + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(blob[at + 7 : at + 9], "big"), int.from_bytes(
                blob[at + 5 : at + 7], "big"
            )
        at += 2 + length
    raise AssertionError("no frame header")


async def test_a_sites_picture_is_kept_as_sifts_own_jpeg(real_store: ArtStore) -> None:
    """The site's bytes are never what is kept: a picture arrives re-encoded, at its own size.

    Something riding behind the picture (here words after its end, which is where a polyglot keeps
    its second self) does not survive the re-encode, and that is the property: what is on the disk
    was written by Sift's ffmpeg, not by whoever sent it.
    """
    sent = a_png(24, 16) + b"SOMETHING-RIDING-ALONG"
    assert await real_store.keep_bytes("onlyfans:quillmoss", sent) is True

    known = await real_store.known("onlyfans:quillmoss")
    assert known is not None
    kept = known.path.read_bytes()
    assert kept != sent
    assert kept.startswith(b"\xff\xd8\xff")
    assert b"SOMETHING-RIDING-ALONG" not in kept
    assert _size_of_jpeg(kept) == (24, 16)


async def test_an_svg_is_never_kept_as_one(real_store: ArtStore, tmp_path: Path) -> None:
    """An SVG is a document that can carry script, so it is never kept and served as it came.

    The door either refuses it or draws it into a picture of its own (the raster step inside the
    door); either way nothing kept is an SVG, and nothing under the cache holds its text.
    """
    svg = (
        b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg' width='16' height='16'>"
        b"<script>alert(1)</script><rect width='16' height='16' fill='#336'/></svg>"
    )
    await real_store.keep_bytes("onlyfans:quillmoss", svg)

    known = await real_store.known("onlyfans:quillmoss")
    if known is not None:
        assert known.path.read_bytes().startswith(b"\xff\xd8\xff")
    for one in (tmp_path / "cache").rglob("*"):
        if one.is_file():
            assert b"<svg" not in one.read_bytes()
            assert one.suffix != ".svg"


async def test_a_page_where_a_picture_should_be_is_refused_by_the_door(
    real_store: ArtStore,
) -> None:
    assert await real_store.keep_bytes("onlyfans:quillmoss", b"<html>a login wall</html>") is False
    assert await real_store.known("onlyfans:quillmoss") is None


async def test_a_creators_picture_follows_its_username_and_person_through_a_rename(
    store: ArtStore,
) -> None:
    """The picture belongs to the username by id, and is read under the names it has NOW.

    The scope is the fetch's cache key and spells the username as it was: renamed on the Site (or
    its person renamed), the picture was filed under a name nothing answered to any more.
    """
    db = store._db
    await db.execute("INSERT INTO sites (id, name) VALUES ('s1', 'TikTok')")
    await db.execute(
        "INSERT INTO download_sites (key, site_id, filed_at) VALUES ('tiktok', 's1', 0)"
    )
    await db.execute("INSERT INTO people (id, name, created_at) VALUES ('p1', 'Riverbend', 0)")
    await db.execute(
        "INSERT INTO usernames (id, site_id, name, person_id, created_at)"
        " VALUES ('u1', 's1', 'riverbend', 'p1', 0)"
    )
    assert await store.keep_bytes("tiktok:riverbend", _png(b"face"))

    await db.execute("UPDATE usernames SET name = 'riverbend_clips' WHERE id = 'u1'")
    await db.execute("UPDATE people SET name = 'Wren Ashdown' WHERE id = 'p1'")

    assert await store.known("tiktok:riverbend_clips") is not None
    assert await store.already_held("tiktok:riverbend_clips")
    assert await store.for_creator("riverbend_clips") is not None
    assert await store.for_creator("Wren Ashdown") is not None
    assert await store.for_creator("riverbend") is None
    assert set(await store.creators_with_art()) == {"riverbend_clips", "Wren Ashdown"}
