# SPDX-License-Identifier: AGPL-3.0-or-later
"""Noticing that a newer version exists, and saying so; installing is the desktop app's alone."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import aiohttp

from sift.kernel.access import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.fetch import outbound_session
from sift.kernel.http import read_capped
from sift.kernel.log import get_logger
from sift.kernel.version import app_version
from sift.kernel.wiring import Part
from sift.slices.update_notify.version import is_newer

log = get_logger(__name__)

#: Long: a release is not urgent, and polling a public endpoint often is a nuisance and a signal.
CHECK_INTERVAL_SECONDS = 6 * 60 * 60

FETCH_TIMEOUT_SECONDS = 5.0

#: A feed is a small JSON document; anything larger is broken or trying to be.
MAX_FEED_BYTES = 256 * 1024

MAX_NOTES_CHARS = 20_000

MAX_PAGE_CHARS = 2048

DISMISSED_KEY = "updates.dismissed_version"
ENABLED_KEY = "updates.check_for_new_versions"

#: Injected so the check can be exercised without a network.
Fetcher = Callable[[], Awaitable["Release | None"]]


class SessionFactory(Protocol):
    """Opens a session whose connections are vetted before they are made."""

    def __call__(self) -> AbstractAsyncContextManager[aiohttp.ClientSession]: ...


class SettingsStore(Protocol):
    """The preference store, named by its shape for the same reason."""

    async def get_app(self, key: str) -> Any: ...

    async def apply(self, viewer: Viewer, updates: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class Release:
    """One published release: what it is called, what changed in it, and where it is published."""

    version: str
    notes: str
    page: str = ""
    """The release's own page, https only, or empty. The one address the notes may link to."""


@dataclass(frozen=True, slots=True)
class UpdateReport:
    """What the screen draws. Always answerable, including when nothing is known."""

    current_version: str
    latest_version: str | None
    update_available: bool
    notes: str
    release_page: str
    """Where the release is published, or empty. The only link the notes are drawn with."""

    last_checked: int
    dismissed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "notes": self.notes,
            "release_page": self.release_page,
            "last_checked": self.last_checked,
            "dismissed": self.dismissed,
        }


class UpdateService:
    """The update check, one per application; the fetched release is held in memory only."""

    def __init__(
        self,
        settings: SettingsStore,
        fetch: Fetcher,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._fetch = fetch
        self._clock = clock
        self._known: Release | None = None
        self._last_checked: int = 0
        self._lock = asyncio.Lock()

    async def report(self) -> UpdateReport:
        """Where things stand, from what the last check found; never goes out to the network."""
        async with self._lock:
            return await self._describe()

    async def check(self) -> str:
        """Read the feed once, now, and say what was found in one sentence for the task's row."""
        async with self._lock:
            found = await self._refresh()
            report = await self._describe()
        if found is None:
            return "Could not read the list of Sift releases."
        if report.update_available:
            return f"Sift {report.latest_version} is available."
        return "Sift is up to date."

    async def dismiss(self, viewer: Viewer, version_seen: str) -> None:
        """Stop showing the banner for one specific version; the next release brings it back."""
        await self._settings.apply(viewer, {DISMISSED_KEY: version_seen})

    # --- internals -----------------------------------------------------------------------------

    async def _refresh(self) -> Release | None:
        """Read the feed once, stamping the attempt whether or not it worked."""
        self._last_checked = int(self._clock())
        release = await self._fetch()
        if release is not None and release != self._known:
            self._known = release
            # Held in memory, so the banner follows the settings bell rather than a transaction.
            announce_now(EVERY_ADMIN, About.SETTINGS)
        return release

    async def _describe(self) -> UpdateReport:
        current = app_version()
        latest = self._known.version if self._known is not None else None
        available = latest is not None and is_newer(latest, current)
        dismissed = latest is not None and await self._settings.get_app(DISMISSED_KEY) == latest
        return UpdateReport(
            current_version=current,
            latest_version=latest,
            update_available=available,
            notes=self._known.notes if self._known is not None and available else "",
            release_page=self._known.page if self._known is not None and available else "",
            last_checked=self._last_checked,
            dismissed=dismissed,
        )


@asynccontextmanager
async def _direct_session() -> AsyncIterator[aiohttp.ClientSession]:
    """A plain session, for a feed given as a literal address. See `fetch_release`."""
    async with await outbound_session() as session:
        yield session


def _given_as_an_address(feed_url: str) -> bool:
    """Whether the feed's host is a literal IP address rather than a name."""
    try:
        ipaddress.ip_address((urlsplit(feed_url).hostname or "").strip("[]"))
    except ValueError:
        return False
    return True


async def fetch_release(
    open_session: SessionFactory,
    feed_url: str | None = None,
    *,
    open_direct: SessionFactory = _direct_session,
) -> Release | None:
    """Read the newest published release from `feed_url`, or None if it could not be read."""
    if not feed_url:
        log.info("update.no_feed")
        return None
    literal = _given_as_an_address(feed_url)
    try:
        async with (
            (open_direct if literal else open_session)() as session,
            session.get(
                feed_url,
                timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS),
                headers={"Accept": "application/json"},
                allow_redirects=not literal,
            ) as response,
        ):
            if response.status != 200:
                # Said with the one fact that tells a moved feed from a refused one.
                log.info("update.check_refused", status=response.status)
                return None
            # Capped and looped: `read(n)` alone hands back a fragment of JSON that cannot parse.
            body = await read_capped(response.content, MAX_FEED_BYTES)
        return _read_release(body)
    except Exception:
        # Everything: a version check must never fail in a way that reaches a person.
        log.info("update.check_failed")
        return None


def _read_release(body: bytes) -> Release | None:
    """Pull the version and the notes out of a feed document, or None if it is not one."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        return None
    notes = payload.get("body")
    page = payload.get("html_url")
    return Release(
        version=tag.strip(),
        notes=notes[:MAX_NOTES_CHARS] if isinstance(notes, str) else "",
        page=str(page) if _is_release_page(page) else "",
    )


def _is_release_page(value: object) -> bool:
    """Whether a feed's page address is one the screen may link to: https, a host, a sane length."""
    if not isinstance(value, str) or len(value) > MAX_PAGE_CHARS:
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.hostname) and parts.username is None


__all__ = [
    "CHECK_INTERVAL_SECONDS",
    "DISMISSED_KEY",
    "ENABLED_KEY",
    "MAX_FEED_BYTES",
    "MAX_NOTES_CHARS",
    "MAX_PAGE_CHARS",
    "Fetcher",
    "Release",
    "SessionFactory",
    "SettingsStore",
    "UpdateReport",
    "UpdateService",
    "fetch_release",
]


SERVICE: Part[UpdateService] = Part("update_notify")
