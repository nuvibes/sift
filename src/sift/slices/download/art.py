# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture a CREATOR is shown with, fetched once and kept.

**Once per creator, ever, not once per download.** Five hundred items from one creator is one
fetch, or none: anything keyed per download turns a bulk paste into five hundred requests for the
same picture.

**A Site's picture is never fetched.** A Site's mark is the icon pack that ships with Sift
(`kernel/site_icons`), and a Site the pack does not cover shows its letter. Rows an older version
stored for sites were dropped by the download schema's v25 step; the files those rows named sit
under the cache until it is cleared.

**It never touches a chosen cover, and that is structural rather than a rule.** A cover somebody
picked is a pointer to a file in their library, and lives on the site or the username. What is
here is a picture fetched from a website: a different kind of thing, in a different place, read only
when the chosen one is absent. So there is no code path in which scraping can overwrite a choice:
not because something checks first, but because the two are not stored in the same field.

**Cached here, never linked to.** A page that pointed an image straight at the remote site would
reach out to it every time somebody opened a screen, from their browser rather than from the server,
which both leaks who is looking and breaks an install with no internet. The offline check in the
build refuses an external reference outright, and it is right to.

**A creator is shown as their profile page's sharing image and nothing else**: on a profile page
it is that person's own picture, and the icon beside it is the site's.

Nothing is generated: a creator whose page offers no picture simply has none, and a screen draws
the name instead.

**What is kept is Sift's own picture, never the site's bytes.** Every picture that arrives from
outside goes through ONE door, the one an uploaded cover goes through (`kernel.covers`,
`CoverPictures.receive`): read to a cap, piped into ffmpeg, and written out as Sift's own JPEG. A
site's file is never written to the disk and never served. The door refuses what it cannot decode
as a picture, an SVG (a document that can carry script) among them, so the same rule holds for a
creator's face as for a cover somebody chose. Rows an older version kept as the site's own bytes
are brought through the door the first time they are read (see `_through_the_door`).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from sift.kernel.covers import CoverPictureRefused, CoverPictures, bytes_reader
from sift.kernel.db import Database, Row
from sift.kernel.http import read_capped
from sift.kernel.log import get_logger
from sift.kernel.wiring import Part
from sift.slices.download.sources.net import guarded_session

log = get_logger(__name__)

#: How long a fetched picture is trusted before anybody would think to look again. Long: a site's
#: own artwork changes about as often as its logo does, and the cost of being a month stale is that
#: a small picture on a row is the old one.
KEEP_FOR_SECONDS = 90 * 24 * 60 * 60

#: The most a picture may be. A site's sharing image is tens of kilobytes; anything past this is not
#: a logo, and it is arriving from a machine Sift does not control.
MAX_BYTES = 2 * 1024 * 1024

#: What a page itself declares as its picture, so nothing here is guessing at a URL that might
#: exist. Both attribute orders, because HTML does not order attributes.
_SHARING_IMAGE = (
    re.compile(
        r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']", re.IGNORECASE
    ),
    re.compile(
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image[\"']", re.IGNORECASE
    ),
)

#: A creator wears their own picture and NOTHING ELSE. There is no fallback here on purpose: the
#: only other thing a profile page declares is the site's own tab icon, and taking that gives a
#: person the site's logo as their face, which is worse than the monogram it would replace, as it
#: is the same face for everybody on that site. A page that names no picture means no picture,
#: and never the site's root favicon either, which is the same logo by another address.
_FOR_A_CREATOR = _SHARING_IMAGE

#: Only the page itself is read, and only this much of it. Everything above lives in the head, and a
#: whole page from a large site is megabytes of body nobody here has any use for.
_HEAD_BYTES = 64 * 1024

_READ = "SELECT scope, stored_at, path FROM site_art WHERE scope = ?"

# A creator's picture belongs to the USERNAME it was fetched for, by id (download v35). The scope is
# `<site key>:<username>`, the fetch's own cache key, and its username half is text: a username the
# Site renamed (matched on its number and renamed in place) or a person renamed since left the
# picture filed under a name nothing is called any more. So every row keeps the username's id, and
# every read by name goes through it, under the names the username and its person have NOW. A row
# no username answered to when it was kept (none was filed yet) is read by its scope, as before.

#: The username a scope's picture belongs to: that name on the Site the scope's site files under.
_LINK_USERNAME = """
UPDATE site_art SET username_id = (
  SELECT u.id FROM usernames u JOIN download_sites k ON k.site_id = u.site_id
   WHERE k.key = ? AND u.name = ? COLLATE NOCASE ORDER BY u.id LIMIT 1)
 WHERE scope = ? AND username_id IS NULL
"""
#: The picture of a username named this NOW on the Site this site key files under, whatever name
#: the picture was kept under.
_READ_LINKED = """
SELECT a.scope, a.stored_at, a.path FROM site_art a
  JOIN usernames u ON u.id = a.username_id
  JOIN download_sites k ON k.site_id = u.site_id
 WHERE k.key = ? AND u.name = ? COLLATE NOCASE
 ORDER BY a.stored_at DESC LIMIT 1
"""
# The same picture asked for by username alone, for a screen that knows a name and not a site. The
# newest wins: somebody with usernames on three sites has three pictures, and the most recently
# fetched is the one most likely to still be them.
#: Every name a creator picture answers to NOW: its username's and that username's person's, read
#: through the id; the scope's own name only for a row kept before any username answered to it. A
#: site's own mark has no colon in its scope, which is what tells the two apart.
_CREATOR_SCOPES = """
SELECT u.name AS name FROM site_art a JOIN usernames u ON u.id = a.username_id
UNION SELECT p.name FROM site_art a JOIN usernames u ON u.id = a.username_id
  JOIN people p ON p.id = u.person_id
UNION SELECT substr(scope, instr(scope, ':') + 1) FROM site_art
 WHERE username_id IS NULL AND scope LIKE '%:%'
"""
#: The other half of the same table: a scope with no colon in it belongs to a site.

_READ_BY_USERNAME = """
SELECT a.scope, a.stored_at, a.path FROM site_art a
  LEFT JOIN usernames u ON u.id = a.username_id
  LEFT JOIN people p ON p.id = u.person_id
 WHERE u.name = :name COLLATE NOCASE OR p.name = :name COLLATE NOCASE
    OR (a.username_id IS NULL AND a.scope LIKE '%:' || lower(:name))
 ORDER BY a.stored_at DESC LIMIT 1
"""
_WRITE = (
    "INSERT INTO site_art (scope, path, stored_at) VALUES (?, ?, ?)"
    " ON CONFLICT(scope) DO UPDATE SET path = excluded.path, stored_at = excluded.stored_at"
)
#: A row whose picture the door refused when it was brought through (an SVG, a file that was never
#: a picture, one that is gone). No picture, so the next download from that creator asks again.
_DROP = "DELETE FROM site_art WHERE scope = ?"


@dataclass(frozen=True, slots=True)
class Art:
    """A picture Sift has, and when it was taken."""

    path: Path
    stored_at: int


#: The folder under the cache that pictures were written into as the site sent them, before every
#: picture went through the cover door. Nothing is written here now; a stored path that begins with
#: it (or an absolute one, from before paths were relative) is a row kept the old way, and is
#: brought through the door the first time it is read. See `_through_the_door`.
_FOLDER = "site-art"


class ArtStore:
    """What picture each creator has, and the one place one is ever fetched.

    The table is `site_art`, and every scope in it is `site:username` now: the site-only rows
    from before the site fetch was removed were dropped by the schema's v25 step.

    Held on the application with the rest of this slice's stores. Everything it writes lives under
    the cache directory, so a person clearing the cache loses pictures and nothing else.
    """

    def __init__(self, database: Database, cache_dir: Path, pictures: CoverPictures) -> None:
        self._db = database
        self._cache = cache_dir
        #: The one door every picture from outside goes through. See the top of this module.
        self._pictures = pictures
        #: One row brought through the door at a time, so two screens reading the same old row at
        #: once make one picture rather than two (the second finds the first's work and reads it).
        self._bringing = asyncio.Lock()

    def _at(self, stored: str) -> Path:
        """Where a stored path actually is, now.

        The stored value is relative to the cache directory, so the row stays true when somebody
        moves their cache folder.

        Read tolerantly: a value that is still absolute (a database restored from a backup taken
        before v11) is used as it stands rather than joined onto the cache, which would produce a
        nonsense path. It heals the next time that site is used, and until then the file either
        opens or is reported missing, which is the same answer this gives for anything gone.
        """
        as_path = Path(stored)
        return as_path if as_path.is_absolute() else self._cache / as_path

    async def known(self, scope: str) -> Art | None:
        """The picture kept under one scope, or None if there is none.

        The scope as it is spelled today (`<site key>:<username>`); where no row was kept under
        that spelling, the picture of the username called that NOW on that site, kept under the
        name it had before a rename.
        """
        row = await self._db.fetch_one(_READ, (scope,))
        if row is None and ":" in scope:
            key, name = scope.split(":", 1)
            row = await self._db.fetch_one(_READ_LINKED, (key, name))
        return None if row is None else await self._art_of(row)

    async def _art_of(self, row: Row) -> Art | None:
        """What one stored row gives a screen: the door's picture, or nothing.

        A row whose file is gone (somebody cleared the cache) is not a picture. Reported as absent so
        the next download that names that creator fetches it again, rather than a screen asking for
        a file that is not there. Off the loop: this asks the filesystem, and the filesystem can be a
        network share.

        A row kept the old way (the site's own bytes) is brought through the door first, once, and
        what is answered is what the door made of it. Never the old file itself: those bytes are
        exactly what this module no longer serves.
        """
        stored = str(row["path"])
        if _kept_the_old_way(stored):
            return await self._through_the_door(str(row["scope"]), stored, int(row["stored_at"]))
        path = self._at(stored)
        if not await asyncio.to_thread(_is_there, path):
            return None
        return Art(path=path, stored_at=int(row["stored_at"]))

    async def _through_the_door(self, scope: str, stored: str, stored_at: int) -> Art | None:
        """Bring one row kept the old way through the door, and answer what it holds afterwards.

        THE CATCH-UP FOR ROWS ALREADY ON DISK, and it re-encodes rather than refetching or dropping.
        The bytes are already here, so a refetch would be a request to every creator's site for a
        picture Sift holds; dropping would take the face off every creator nobody downloads from
        again. So the file the site sent is read once and handed to the door like anything else
        from outside: a picture comes out as Sift's own and keeps its date, and what the door
        refuses (an SVG, a file that was never a picture, one that is gone) loses its row, which
        is the same answer as never having had one: the next download from that creator asks again.

        On first read rather than at a start, so it costs nothing for a creator nobody looks at, and
        under one lock, so two screens asking at the same time make one picture. The old file is
        left where it is, under the cache Sift may empty; nothing names it any more.
        """
        async with self._bringing:
            # Asked again inside the lock: whoever held it may have brought this very row through.
            row = await self._db.fetch_one(_READ, (scope,))
            if row is None:
                return None
            if not _kept_the_old_way(str(row["path"])):
                return await self._art_of(row)
            blob = await asyncio.to_thread(_read_quietly, self._at(stored))
            kept = blob is not None and await self._write(scope, blob, stored_at=stored_at)
            if not kept:
                await self._db.execute(_DROP, (scope,))
                log.info("download.art_dropped_at_the_door", scope=scope)
                return None
            log.info("download.art_brought_through_the_door", scope=scope)
            again = await self._db.fetch_one(_READ, (scope,))
        return None if again is None else await self._art_of(again)

    async def creators_with_art(self) -> list[str]:
        """Every username a creator picture is filed under, without the site half of the name.

        For a screen that draws many People together and would otherwise ask about each of them
        separately. The files are not checked here: this answers which names are worth asking
        about, and asking is what checks.
        """
        rows = await self._db.fetch_all(_CREATOR_SCOPES)
        return [str(row["name"]) for row in rows if row["name"]]

    async def for_creator(self, username: str) -> Art | None:
        """A creator's picture, found by their username rather than by site.

        For a screen that shows a name and does not know which site it came from. A download files
        what it fetched under a person named by the username, so the two are the same string, and
        a person Sift has no picture for simply keeps their monogram.
        """
        row = await self._db.fetch_one(_READ_BY_USERNAME, {"name": username})
        return None if row is None else await self._art_of(row)

    async def already_held(self, scope: str) -> bool:
        """Whether this scope already has a picture recent enough to leave alone.

        The one place the "do not replace what is there" rule is written, so that a second caller
        (a picture kept off the back of a stash-box link, say) cannot decide for itself
        and overwrite a picture the fetch would keep.
        """
        existing = await self.known(scope)
        return existing is not None and existing.stored_at > int(time.time()) - KEEP_FOR_SECONDS

    async def fill_if_empty(self, scope: str, page_url: str, *, proxy: str | None = None) -> None:
        """Fetch a creator's picture if they have none. Does nothing at all if they already have one.

        Cannot fail loudly, by design. This runs off the back of a download that has already
        succeeded, and a site with an unusual page, a slow response or no sharing image at all must
        not turn a completed download into a failed one. A picture is a nicety; the file is the work.
        """
        if await self.already_held(scope):
            return
        try:
            await self._fetch(scope, page_url, proxy=proxy)
        except Exception as exc:
            # Logged and dropped. The next download from this creator tries again, which is the
            # right cadence for something nobody is waiting on.
            log.info("download.art_not_fetched", scope=scope, detail=str(exc))

    async def _fetch(self, scope: str, page_url: str, *, proxy: str | None) -> None:
        """Read a creator's profile page for the picture it declares, and keep it.

        Over the guarded session, and through the site's own route: this is a request to the same
        site the download went to, so it goes out the same way: a site somebody deliberately does
        not contact directly must not be contacted directly for a profile picture either.
        """
        async with guarded_session(proxy=proxy) as session:
            async with session.get(page_url) as response:
                if response.status != 200:
                    log.info("download.art_page_refused", scope=scope, status=response.status)
                    return
                head = (await read_capped(response.content, _HEAD_BYTES)).decode("utf-8", "replace")

            found = _first_picture(head, page_url)
            if found is None:
                log.info("download.art_not_declared", scope=scope)
                return

            async with session.get(found) as picture:
                if picture.status != 200:
                    log.info("download.art_refused", scope=scope, status=picture.status)
                    return
                blob = await read_capped(picture.content, MAX_BYTES + 1)

        await self._write(scope, blob)

    async def keep_bytes(self, scope: str, blob: bytes) -> bool:
        """Keep a picture that arrived some other way than `_fetch`. True if it was kept.

        The same rules as a fetched one, asked in the same place: a scope that already has a
        picture keeps it, and bytes that are too large or are not a picture are never written.
        """
        if await self.already_held(scope):
            return False
        return await self._write(scope, blob)

    async def _write(self, scope: str, blob: bytes, *, stored_at: int | None = None) -> bool:
        """Take what arrived through the door and keep what it makes under this scope. True if kept.

        THE DOOR DECIDES WHAT IS A PICTURE, and nothing here second-guesses it. The bytes are handed
        to `CoverPictures.receive`, which pipes them into ffmpeg and writes Sift's own JPEG; what
        ffmpeg cannot read as a picture (a redirect page, a login wall, an error, an SVG) is
        refused there, with the same rule for every picture from outside. A signature check alone
        would wave an SVG through.

        The cap here is this store's own and smaller than the door's: a creator's picture is tens of
        kilobytes, and anything past two megabytes is not one.

        The row is written after the door has made its file and before the old one is forgotten, so
        a reader sees the old picture or the new one and never a row naming nothing.
        """
        if not blob or len(blob) > MAX_BYTES:
            log.info("download.art_wrong_size", scope=scope, size=len(blob))
            return False
        try:
            made = await self._pictures.receive(bytes_reader(blob))
        except CoverPictureRefused:
            log.info("download.art_not_a_picture", scope=scope)
            return False
        kept_as = await self._pictures.kept_as(made)
        if kept_as is None:  # pragma: no cover (the door answers only after writing its row)
            return False
        before = await self._db.fetch_one(_READ, (scope,))
        when = int(time.time()) if stored_at is None else stored_at
        await self._db.execute(_WRITE, (scope, kept_as, when))
        if ":" in scope:
            key, name = scope.split(":", 1)
            await self._db.execute(_LINK_USERNAME, (key, name, scope))
        if before is not None and not _kept_the_old_way(str(before["path"])):
            # The picture it replaces was the door's too, and nothing else names it.
            await self._pictures.forget(Path(str(before["path"])).stem)
        log.info("download.art_fetched", scope=scope)
        return True


def _kept_the_old_way(stored: str) -> bool:
    """Whether a stored path names the site's own bytes rather than the door's picture.

    Absolute (from before paths were relative) or under the old folder. Everything the door writes
    is under its own folder, so there is no third kind.
    """
    as_path = Path(stored)
    return as_path.is_absolute() or as_path.parts[:1] == (_FOLDER,)


def _is_there(path: Path) -> bool:
    """Whether the door's picture is on the disk, with something in it."""
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _read_quietly(path: Path) -> bytes | None:
    """An old row's file, read once to bring it through the door. None for one that is gone.

    Capped one byte past the limit, so an oversized file is refused by the size rule rather than
    read whole.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_BYTES + 1)
    except OSError:
        return None


def _first_picture(head: str, page_url: str) -> str | None:
    """The picture the page declares, as an absolute address, or None if it declares none."""
    for pattern in _FOR_A_CREATOR:
        found = pattern.search(head)
        if found is not None:
            return str(urljoin(page_url, found.group(1)))
    return None


__all__ = [
    "KEEP_FOR_SECONDS",
    "MAX_BYTES",
    "Art",
    "ArtStore",
    "CreatorPictures",
    "creator_scope",
    "creator_scope_for",
    "keeper",
]


def creator_scope(site_key: str, username: str) -> str:
    """What a creator's picture is filed under. One place, because two would drift."""
    return f"{site_key}:{username.lower()}"


def creator_scope_for(*, site: str, username: str, address: str | None) -> str | None:
    """The scope a creator's picture is filed under, from their Site, their name and their page.

    The site half is the download catalog's key where the page is on a site Sift downloads from,
    so a picture kept here and one a download fetches land on the same row. Otherwise it is the
    icon pack's slug for the page's host, else for the Site's name. None where none of the three
    says which site it is: a picture filed under a guess would never be found again.
    """
    from sift.kernel.site_icons import slug_for, slug_for_name
    from sift.slices.download.sources.registry import match_site

    if not username.strip():
        return None
    record = match_site(address) if address else None
    key = (
        record.key
        if record is not None
        else (slug_for(address) if address else None) or slug_for_name(site)
    )
    return creator_scope(key, username.strip()) if key else None


class CreatorPictures:
    """`kernel.seams.CreatorPicturesSeam`: a creator's picture handed in by another feature.

    The picture is asked for only when the creator has none, so a creator who already has one
    costs nobody a request. It cannot fail loudly, for the reason `fill_if_empty` gives: the work
    that led here has already succeeded, and a picture is a nicety.
    """

    def __init__(self, store: ArtStore) -> None:
        self._store = store

    async def keep(
        self,
        *,
        site: str,
        username: str,
        address: str | None,
        picture: Callable[[], Awaitable[bytes | None]],
    ) -> bool:
        scope = creator_scope_for(site=site, username=username, address=address)
        if scope is None or await self._store.already_held(scope):
            return False
        try:
            blob = await picture()
        except Exception as exc:
            log.info("download.art_not_fetched", scope=scope, detail=str(exc))
            return False
        if blob is None:
            return False
        return await self._store.keep_bytes(scope, blob)


def keeper(store: ArtStore) -> Callable[[str, str | None, str | None], Awaitable[None]]:
    """The shape the download job is given: a URL it just fetched, its route, and whose it was.

    The job knows a download's address and nothing about sites; this turns that into the site's key
    and the creator's own page on it. A site Sift has no record for is skipped rather than guessed
    at: a picture is not worth a request to somewhere nothing recognises.

    ONE picture, at most one request, only ever fetched once: the CREATOR's, from their own page on
    that site. It is what puts a face beside a name on every screen that shows one, and it is
    skipped for a site whose profile addresses Sift does not know. See `profile_url` on the site
    record. The SITE's own picture is never asked for: see the top of this module.
    """
    from sift.slices.download.sources.registry import match_site

    async def keep(url: str, proxy: str | None, username: str | None = None) -> None:
        if not username:
            return
        record = match_site(url)
        if record is None or not record.profile_url or not record.username_is_a_person:
            return
        await store.fill_if_empty(
            creator_scope(record.key, username),
            record.profile_url.format(username=username),
            proxy=proxy,
        )

    return keep


#: The picture each site is shown with.
SITE_ART: Part[ArtStore] = Part("site_art")
