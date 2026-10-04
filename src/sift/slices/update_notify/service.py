# SPDX-License-Identifier: AGPL-3.0-or-later
"""Noticing that a newer version exists, and saying so.

The backend does not update itself, and nothing here shells out to anything. What it does is read
the release feed, compare the version it is running against the newest published one, and say so.
Applying the update is the desktop application's: it downloads the installer, checks the signed
manifest against the key compiled into it, and opens the installer: a person presses the button,
and the application is what has the standing to replace the program on the disk.

That split is deliberate rather than unfinished. The backend answers on the network; a backend
that could replace its own program would be handing that power to whoever reaches it.

The feed's address is the desktop application's too. It starts this backend and hands the address
over (`SIFT_RELEASE_FEED_URL`), so the notice here and the Install button there read the same
release. A backend started without one, from a source checkout, has no feed and checks nothing.

The properties that shape the rest of this module:

*Quiet.* The check is a convenience and the library is the product. No network, a rate limit, a
feed that has moved, a reply that is not the shape expected: every one of them ends as "nothing
known", never as an error on a screen and never as an exception out of a request.

*Silent about the installation.* Nothing is sent. The check is a plain read of a public address
with no query, no body and no version in it; the comparison happens here, on the machine. A feed
that is fetched identically by every installation cannot be used to count or recognize them.

*Bounded.* One fetch per run of the check, a short timeout, and a cap on how much of a reply is read.

*A task.* The check runs on its own clock as the update-check task (every few hours, in quiet hours,
or only when pressed: its When), and a screen asking for the answer reads what the last run found
and never goes out to the network itself, so a desktop window left open still sees each new
check and "Only when I press it" means no fetch at all.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import aiohttp

from sift.kernel.access import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.http import read_capped
from sift.kernel.log import get_logger
from sift.kernel.version import app_version
from sift.kernel.wiring import Part
from sift.slices.update_notify.version import is_newer

log = get_logger(__name__)

#: How often the check runs as soon as there is work to do. Long, because a new release is not
#: urgent and an application that polls a public endpoint often is a nuisance to that endpoint and a
#: signal to anyone watching the connection.
CHECK_INTERVAL_SECONDS = 6 * 60 * 60

#: How long the fetch may take before it is abandoned. Short: nobody is waiting on this answer, and
#: the failure it produces is indistinguishable from having no network, which is handled.
FETCH_TIMEOUT_SECONDS = 5.0

#: The most of a reply that is read. A feed is a small JSON document; anything larger is either
#: broken or trying to be, and neither is worth the memory.
MAX_FEED_BYTES = 256 * 1024

#: The most release-notes text that is kept. Notes are shown on a screen, not stored, and a release
#: with a novel attached should not become one.
MAX_NOTES_CHARS = 20_000

#: The longest release-page address kept. The one link the notes may carry is to this page.
MAX_PAGE_CHARS = 2048

#: Settings keys. The dismissal is a preference rather than a table of its own: there is no state
#: here worth a schema.
DISMISSED_KEY = "updates.dismissed_version"
ENABLED_KEY = "updates.check_for_new_versions"

#: Reads the release feed and returns the newest published release, or None if it could not be read
#: for any reason at all. Injected so the check can be exercised without a network.
Fetcher = Callable[[], Awaitable["Release | None"]]


class SessionFactory(Protocol):
    """Opens a session whose connections are vetted before they are made.

    A structural type rather than an import: the connector that refuses private addresses belongs to
    another feature, and a slice does not import a slice. The application wires the real one in at
    boot, so there is one such guard in the codebase and this is not a second copy of it.
    """

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
    """The update check. One per application, built at boot and held on app.state.

    The fetched release and the time it was fetched are held in memory rather than stored. The
    release is a copy of a public document that costs one request to obtain, and the timestamp
    exists only to space those requests out: neither is worth a row, and keeping both out of the
    database is what lets this feature own no schema at all. A restart forgets them and the next
    request fetches once.

    The dismissal is the one thing that does persist, because it records a decision a person made.
    """

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
        """Where things stand, from what the last check found. Never goes out to the network.

        The check is a task with a When of its own, and the task is the one thing that fetches, so
        "Only when I press it" means silence.
        """
        async with self._lock:
            return await self._describe()

    async def check(self) -> str:
        """Read the feed once, now, and say what was found in one sentence for the task's row.

        Stamped whether or not it worked, so an install with no network is not asked again until
        its next run. The sentence is the task's note: what the row under "last ran" reads.
        """
        async with self._lock:
            found = await self._refresh()
            report = await self._describe()
        if found is None:
            return "Could not read the list of Sift releases."
        if report.update_available:
            return f"Sift {report.latest_version} is available."
        return "Sift is up to date."

    async def dismiss(self, viewer: Viewer, version_seen: str) -> None:
        """Stop showing the banner for one specific version.

        Recorded against the version rather than as a plain "hide it", so the next release brings
        the notice back on its own. Nothing else is suppressed: the Updates section still says what
        is available.
        """
        await self._settings.apply(viewer, {DISMISSED_KEY: version_seen})

    # --- internals -----------------------------------------------------------------------------

    async def _refresh(self) -> Release | None:
        """Read the feed once, and record that it was attempted whether or not it worked.

        The timestamp is stamped even on failure: an installation with no outbound network tries
        once per run of the check, and it never learns to try harder.
        """
        self._last_checked = int(self._clock())
        release = await self._fetch()
        if release is not None and release != self._known:
            self._known = release
            # Held in memory, never written, so there is no transaction to announce on: the
            # banner and `Settings > Updates` follow the settings bell, as they do for a release
            # dismissed, and a new release reaches a window that is already open.
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


def _direct_session() -> AbstractAsyncContextManager[aiohttp.ClientSession]:
    """A plain session, for a feed given as a literal address. See `fetch_release`."""
    return aiohttp.ClientSession()


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
    """Read the newest published release from `feed_url`, or None if it could not be read.

    `feed_url` is the address the desktop application handed over at start, requested exactly as
    given. None, a backend started without one, makes no request at all.

    Every failure is the same failure here: unreachable, refused, rate-limited, redirected
    somewhere unexpected, or answering with something that is not the document expected. None of
    them is worth distinguishing to a caller whose only options are "say there is an update" and
    "say nothing", and none of them reaches a screen.

    `open_session` is the application's guarded connector, handed in at boot. It re-resolves the
    address and holds it to the public-only rule at connect time. A fixed public address is not a
    server-side request forgery on its own, but the name behind it is resolved by whatever DNS the
    machine is pointed at, and that is enough to make the check worth pinning like any other.

    A feed given as a literal IP address is fetched as given (`open_direct`), redirects refused.
    The address is set by whoever started this process (the desktop app, from its own settings
    file) and never by a request, and no DNS can move a literal, so the guard would add nothing
    and would refuse the two cases the override exists for: a release served on this machine to
    prove the update chain, and a mirror on the local network.
    """
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
                # Said, with the one fact that tells a moved feed from a refused one. The check's
                # own row says it could not read the list; this is what somebody asks next.
                log.info("update.check_refused", status=response.status)
                return None
            # Capped rather than read outright, and looped rather than asked for in one go:
            # `read(n)` hands back the first packet, which for a feed is a fragment of JSON that
            # cannot parse, so the check would report "no update" for ever. See `read_capped`.
            body = await read_capped(response.content, MAX_FEED_BYTES)
        return _read_release(body)
    except Exception:
        # Deliberately everything. A version check must not be able to fail in a way that reaches
        # a person, and the address is fixed and public, so there is no failure here that carries
        # information worth acting on.
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


#: The update check.
SERVICE: Part[UpdateService] = Part("update_notify")
