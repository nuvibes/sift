# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the download endpoint tests share: the application, a signed-in client and stand-ins."""

from __future__ import annotations

import asyncio
import functools
import importlib
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.slices.auth.crypto import generate_master_key
from sift.testing.auth import establish_session

#: The check route's own module, reached by name rather than imported.
#:
#: `from sift.slices.download import router` does NOT give it. The package re-exports the slice's
#: `APIRouter` under that same name, so the attribute wins over the submodule and every spelling of
#: the plain import (including the string form monkeypatch takes) lands on the router object and
#: fails with "has no attribute". This is the one spelling that always means the module.
download_api = importlib.import_module("sift.slices.download.router")


# Every endpoint this slice mounts, with a body where one is needed. Nothing here is reachable
# without an admin, and this list is what says so for each one rather than for the router.
_ENDPOINTS = [
    ("POST", "/api/downloads", {"url": "https://example.com/x"}),
    ("GET", "/api/downloads", None),
    ("POST", "/api/downloads/01HXsomeid/cancel", None),
    ("POST", "/api/downloads/01HXsomeid/anyway", None),
    ("GET", "/api/site-connections", None),
    ("POST", "/api/site-connections", {"site": "TikTok", "cookie": "c=1"}),
    ("DELETE", "/api/site-connections/01HXsomeid", None),
    ("POST", "/api/site-connections/preview", {"site": "TikTok", "cookie": "c=1"}),
    ("POST", "/api/site-connections/01HXsomeid/check", None),
    ("POST", "/api/downloads/01HXsomeid/remove", None),
    ("POST", "/api/downloads/01HXsomeid/pause", None),
    ("POST", "/api/downloads/01HXsomeid/resume", None),
    ("POST", "/api/downloads/01HXsomeid/restore", None),
    ("POST", "/api/downloads/bulk-preview", {"url": "https://www.youtube.com/playlist?list=P"}),
    ("POST", "/api/downloads/bulk", {"url": "https://www.youtube.com/playlist?list=P"}),
    ("GET", "/api/supported-sites", None),
    ("POST", "/api/downloads/01HXsomeid/retry", None),
    ("POST", "/api/downloads/01HXsomeid/first", None),
    ("GET", "/api/site-options", None),
    ("PUT", "/api/site-options/youtube", {"naming": "{site}"}),
    ("DELETE", "/api/site-options/youtube", None),
    ("POST", "/api/site-options/preview", {"naming": "{site}"}),
    ("GET", "/api/creator-art", None),
    ("GET", "/api/creator-art/someone", None),
    ("GET", "/api/downloads/01HXsomeid/files", None),
    ("GET", "/api/downloads/glance", None),
    ("POST", "/api/downloads/seen", None),
]


def sign_in(client: TestClient, role: str) -> str:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role=role, username=f"dl-{role}", password="Dl-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def _call(client: TestClient, method: str, path: str, body: dict[str, str] | None) -> int:
    return int(client.request(method, path, json=body).status_code)


async def _block_the_job_of(database: Database, download_id: str) -> None:
    """Park the download's job the way the handler does when no cookies are saved for the site.

    Written against the job row rather than by running the handler, because what is under test is
    what SAVING does, and a test that had to make a real fetch refuse first would be testing the
    refusal again and this only by implication.
    """
    await database.execute(
        "UPDATE jobs SET state = 'blocked', error = ? WHERE id ="
        " (SELECT job_id FROM downloads WHERE id = ?)",
        ("Waiting for cookies", download_id),
    )


def _on_the_apps_loop(
    client: TestClient, work: Callable[..., Awaitable[None]], *args: object
) -> None:
    """Run a write against the application's own database ON THE APPLICATION'S LOOP.

    The database's write lock belongs to the loop the client runs the app on. `asyncio.run` makes
    a second loop, and the lock refuses it ("bound to a different event loop") the moment the app
    is holding the lock while this waits, which depends on what the app happens to be doing, so
    the same test passes or fails by timing. The client's portal is that loop.
    """
    portal = client.portal
    assert portal is not None, "the client is not running the application"
    portal.call(functools.partial(work, *args))


async def _fail_the_row(database: Database, download_id: str) -> None:
    """End the row the way the handler's failure does, through the service's own statement."""
    from sift.slices.download.service import _SET_FAILED

    await database.execute(_SET_FAILED, ("It failed.", None, None, 1, download_id))


def _a_folder_in(client: TestClient, tmp_path: Path) -> str:
    """A real library and the folder row standing for it, made through Sift's own API.

    Through the API rather than by reaching into the store, because everything else in this file is
    a synchronous test against one client: an async test here opens a second event loop beside the
    one the client is running on, and the session never completes.
    """
    directory = tmp_path / "library"
    directory.mkdir(parents=True, exist_ok=True)
    made = client.post("/api/library/roots", json={"abs_path": str(directory)})
    assert made.status_code == 201, made.text
    folders = client.get("/api/library/folders", params={"root": made.json()["id"]}).json()
    return str(folders["folders"][0]["id"])


def _paste_choices(client: TestClient) -> list[object]:
    """What each queued row carries for the paste's own choice, read off the rows themselves:
    what the ROUTES wrote down, which is the half the service's own test cannot see."""
    path = client.app.state.database.path  # type: ignore[attr-defined]

    async def run() -> list[object]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            rows = await database.fetch_all("SELECT remember FROM downloads ORDER BY id")
            return [row["remember"] for row in rows]
        finally:
            await database.close()

    return asyncio.run(run())


# --- a link dropped ON something -----------------------------------------------------------------
#
# The aim is resolved when the link is QUEUED, while there is a request and a viewer to resolve it
# against. The job that files the result runs minutes later with neither, so a check made there
# would either be no check at all or a second permission model. These are that check.

_A_LINK = "https://www.tiktok.com/@a/video/2"


def _aim_on_the_ledger(client: TestClient) -> tuple[str, str]:
    """Where the one queued download was aimed, read through the service that reads it.

    Through the kernel's own handle rather than a driver of this test's own: a connection opened
    here misses the pragmas and the single-writer lock, and a read that skips the access layer skips
    its permission checks with it. A second handle on the same file is free under WAL, and it is the
    only way to ask an async question from a synchronous test client.

    The ROW rather than `DownloadService.aim_of`, which is what the job reads it with: that function
    has its own rule (all three parts or nothing) and its own tests in `test_jobs.py`. What is
    being asked here is what the ROUTE wrote down.
    """
    path = client.app.state.database.path  # type: ignore[attr-defined]

    async def run() -> tuple[str, str]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            row = await database.fetch_one("SELECT aimed_kind, aimed_id, aimed_by FROM downloads")
            assert row is not None, "no download was queued at all"
            assert row["aimed_by"], "nobody was recorded as having dropped it"
            return str(row["aimed_kind"]), str(row["aimed_id"])
        finally:
            await database.close()

    return asyncio.run(run())


def _made(client: TestClient, path: str, body: dict[str, object]) -> str:
    answer = client.post(path, json=body)
    assert answer.status_code == 201, answer.text
    return str(answer.json()["id"])


# --- the cookies screen: the read-back, the check, and what a row is shown as -------------------


def _unlock(client: TestClient) -> str:
    """Sign in as an admin and put a master key in memory, which sealing and unsealing both need."""
    user_id = sign_in(client, "admin")
    client.app.state.master_keys.store(user_id, generate_master_key())  # type: ignore[attr-defined]
    return user_id


#: A jar in the format a browser extension exports, for one site, expiring a long way out.
#: Seven tab separated fields: domain, every subdomain, path, secure only, expiry, name, value.
def _exported(expires: int, *, host: str = ".tiktok.com", name: str = "sessionid") -> str:
    return (
        "# Netscape HTTP Cookie File\n"
        + "\t".join([host, "TRUE", "/", "TRUE", str(expires), name, "kept-secret"])
        + "\n"
    )


class _Answered:
    """A site's reply, as far as the check reads it: its status."""

    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> _Answered:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Site:
    """A session standing in for the network: answers with one status, or fails to connect."""

    def __init__(self, status: int | None) -> None:
        self._status = status
        self.sent: list[dict[str, str]] = []

    async def __aenter__(self) -> _Site:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def get(self, url: str, *, headers: dict[str, str]) -> _Answered:
        self.sent.append(headers)
        if self._status is None:
            raise OSError("connection refused")
        return _Answered(self._status)


class _Direct:
    """A router with no tunnel for the site: the way out is the machine's own."""

    def route_for(self, _url: str) -> _Direct:
        return self

    async def __aenter__(self) -> str | None:
        return None

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _ask(monkeypatch: pytest.MonkeyPatch, router_: object, site: _Site) -> bool | None:
    monkeypatch.setattr(download_api, "guarded_session", lambda *, proxy: site)
    answer: bool | None = asyncio.run(
        download_api._still_accepted(router_, "https://www.tiktok.com/", "sessionid=kept")
    )
    return answer


def _landed_in(client: TestClient, url: str, folder_id: str) -> str:
    """A download that landed and recorded the folder its job resolved."""
    download_id = str(client.post("/api/downloads", json={"url": url}).json()["id"])
    _write_row(
        client,
        "UPDATE downloads SET folder_id = ?, state = 'done' WHERE id = ?",
        (folder_id, download_id),
    )
    return download_id


def _ledger_rows(client: TestClient) -> int:
    """How many rows the ledger holds, hidden ones included, through the kernel's own handle."""
    path = client.app.state.database.path  # type: ignore[attr-defined]

    async def run() -> int:
        database = Database(path, readers=1)
        await database.connect()
        try:
            row = await database.fetch_one("SELECT COUNT(*) AS how_many FROM downloads")
            return 0 if row is None else int(row["how_many"])
        finally:
            await database.close()

    return asyncio.run(run())


def _write_row(client: TestClient, statement: str, values: tuple[object, ...]) -> None:
    """One write to the ledger, through the kernel's own handle, for a state no request can make."""
    path = client.app.state.database.path  # type: ignore[attr-defined]

    async def run() -> None:
        database = Database(path, readers=1)
        await database.connect()
        try:
            await database.execute(statement, values)
        finally:
            await database.close()

    asyncio.run(run())


async def _finished_from(database: Database, download_id: str, *, site: str, username: str) -> None:
    await database.execute(
        "UPDATE downloads SET state = 'done', site = ?, username = ? WHERE id = ?",
        (site, username, download_id),
    )


async def _named_from(database: Database, download_id: str) -> None:
    await database.execute(
        "UPDATE downloads SET state = 'done', site = 'TikTok', username = 'harlowquin',"
        " finished_at = 1787000000, post_id = '7409999999999999999', title = 'Tide pools',"
        " posted = '2026-09-01', original = 'b7c1e9a2f4d6' WHERE id = ?",
        (download_id,),
    )
