# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture a creator is shown with: fetched once per creator, ever, and kept in the cache.

Every picture goes through the cover door (`CoverPictures.receive`); a site's bytes never are."""

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

#: A site's artwork changes about as often as its logo.
KEEP_FOR_SECONDS = 90 * 24 * 60 * 60

MAX_BYTES = 2 * 1024 * 1024

_SHARING_IMAGE = (
    re.compile(
        r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']", re.IGNORECASE
    ),
    re.compile(
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image[\"']", re.IGNORECASE
    ),
)

#: No fallback: the page's other icon is the site's logo, the same face for everybody.
_FOR_A_CREATOR = _SHARING_IMAGE

_HEAD_BYTES = 64 * 1024

_READ = "SELECT scope, stored_at, path FROM site_art WHERE scope = ?"


#: Kept by username id, so a rename does not orphan the picture.
_LINK_USERNAME = """
UPDATE site_art SET username_id = (
  SELECT u.id FROM usernames u JOIN download_sites k ON k.site_id = u.site_id
   WHERE k.key = ? AND u.name = ? COLLATE NOCASE ORDER BY u.id LIMIT 1)
 WHERE scope = ? AND username_id IS NULL
"""
_READ_LINKED = """
SELECT a.scope, a.stored_at, a.path FROM site_art a
  JOIN usernames u ON u.id = a.username_id
  JOIN download_sites k ON k.site_id = u.site_id
 WHERE k.key = ? AND u.name = ? COLLATE NOCASE
 ORDER BY a.stored_at DESC LIMIT 1
"""
_CREATOR_SCOPES = """
SELECT u.name AS name FROM site_art a JOIN usernames u ON u.id = a.username_id
UNION SELECT p.name FROM site_art a JOIN usernames u ON u.id = a.username_id
  JOIN people p ON p.id = u.person_id
UNION SELECT substr(scope, instr(scope, ':') + 1) FROM site_art
 WHERE username_id IS NULL AND scope LIKE '%:%'
"""

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
_DROP = "DELETE FROM site_art WHERE scope = ?"


@dataclass(frozen=True, slots=True)
class Art:
    """A picture Sift has, and when it was taken."""

    path: Path
    stored_at: int


#: Where pictures were kept as the site sent them; such rows are re-encoded on read.
_FOLDER = "site-art"


class ArtStore:
    """What picture each creator has, and the one place one is ever fetched; all under the cache."""

    def __init__(self, database: Database, cache_dir: Path, pictures: CoverPictures) -> None:
        self._db = database
        self._cache = cache_dir
        self._pictures = pictures
        self._bringing = asyncio.Lock()

    def _at(self, stored: str) -> Path:
        """Where a stored path is now: relative to the cache, or absolute from an older database."""
        as_path = Path(stored)
        return as_path if as_path.is_absolute() else self._cache / as_path

    async def known(self, scope: str) -> Art | None:
        """The picture kept under one scope, or that of the username called that now on the site."""
        row = await self._db.fetch_one(_READ, (scope,))
        if row is None and ":" in scope:
            key, name = scope.split(":", 1)
            row = await self._db.fetch_one(_READ_LINKED, (key, name))
        return None if row is None else await self._art_of(row)

    async def _art_of(self, row: Row) -> Art | None:
        """What one stored row gives a screen: the door's picture, or None when its file is gone."""
        stored = str(row["path"])
        if _kept_the_old_way(stored):
            return await self._through_the_door(str(row["scope"]), stored, int(row["stored_at"]))
        path = self._at(stored)
        if not await asyncio.to_thread(_is_there, path):
            return None
        return Art(path=path, stored_at=int(row["stored_at"]))

    async def _through_the_door(self, scope: str, stored: str, stored_at: int) -> Art | None:
        """Re-encode one row kept the old way through the door on first read; drop it if refused."""
        async with self._bringing:
            # Asked again inside the lock: the holder may have brought this row through.
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
        """Every username a creator picture is filed under, for a screen of many People."""
        rows = await self._db.fetch_all(_CREATOR_SCOPES)
        return [str(row["name"]) for row in rows if row["name"]]

    async def for_creator(self, username: str) -> Art | None:
        """A creator's picture found by username alone, the newest when several sites have one."""
        row = await self._db.fetch_one(_READ_BY_USERNAME, {"name": username})
        return None if row is None else await self._art_of(row)

    async def already_held(self, scope: str) -> bool:
        """Whether this scope already has a picture recent enough to leave alone."""
        existing = await self.known(scope)
        return existing is not None and existing.stored_at > int(time.time()) - KEEP_FOR_SECONDS

    async def fill_if_empty(self, scope: str, page_url: str, *, proxy: str | None = None) -> None:
        """Fetch a creator's picture if they have none; never fails the download it follows."""
        if await self.already_held(scope):
            return
        try:
            await self._fetch(scope, page_url, proxy=proxy)
        except Exception as exc:
            log.info("download.art_not_fetched", scope=scope, detail=str(exc))

    async def _fetch(self, scope: str, page_url: str, *, proxy: str | None) -> None:
        """Read a creator's profile page for its sharing image, over the site's own route."""
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
        """Keep a picture that arrived some other way than `_fetch`. True if it was kept."""
        if await self.already_held(scope):
            return False
        return await self._write(scope, blob)

    async def _write(self, scope: str, blob: bytes, *, stored_at: int | None = None) -> bool:
        """Hand the bytes to the door and keep what it makes under this scope. True if kept."""
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
            await self._pictures.forget(Path(str(before["path"])).stem)
        log.info("download.art_fetched", scope=scope)
        return True


def _kept_the_old_way(stored: str) -> bool:
    """Whether a stored path names the site's own bytes rather than the door's picture."""
    as_path = Path(stored)
    return as_path.is_absolute() or as_path.parts[:1] == (_FOLDER,)


def _is_there(path: Path) -> bool:
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _read_quietly(path: Path) -> bytes | None:
    """An old row's file, capped one byte past the limit; None for one that is gone."""
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
    """The scope for a creator's picture from their Site, name and page; None over a guess."""
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
    """`kernel.seams.CreatorPicturesSeam`: a creator's picture handed in by another feature."""

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
    """The download job's hook: fetch the creator's picture from their page on a known site."""
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


SITE_ART: Part[ArtStore] = Part("site_art")
