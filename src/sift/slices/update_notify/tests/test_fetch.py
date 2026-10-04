# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the update check puts on the wire: nothing about this installation, through the guarded
connector; and silence when the wire does not answer."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import pytest

from sift.slices.update_notify.service import (
    MAX_FEED_BYTES,
    Release,
    fetch_release,
)

NOTES = "Fixes a crash when a folder disappears mid-scan."

#: The address the desktop application hands over. The check requests it exactly as given.
FEED = "https://releases.example/sift/latest"


class Recorded:
    """Everything one outbound request was made of, plus the factory that stands in for the
    application's guarded connector."""

    def __init__(self) -> None:
        self.url: str = ""
        self.kwargs: dict[str, Any] = {}
        self.session_kwargs: dict[str, Any] = {}
        self.answer: dict[str, Any] = {}
        self.open: Any = None


class FakeResponse:
    """A feed arriving in pieces smaller than any read, so a reader that does not loop is caught."""

    PIECE = 3

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.content = self
        self._body = body

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        for at in range(0, len(self._body), self.PIECE):
            yield self._body[at : at + self.PIECE]

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    def __init__(self, recorded: Recorded, response: FakeResponse | Exception) -> None:
        self._recorded = recorded
        self._response = response

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self._recorded.url = url
        self._recorded.kwargs = kwargs
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[Recorded]:
    """Stand in for the guarded session, recording what the check tried to send."""
    recorded = Recorded()
    holder: dict[str, FakeResponse | Exception] = {
        "response": FakeResponse(200, json.dumps({"tag_name": "v1.5.0", "body": NOTES}).encode())
    }

    @asynccontextmanager
    async def fake_session(**kwargs: Any) -> AsyncIterator[FakeSession]:
        recorded.session_kwargs = kwargs
        yield FakeSession(recorded, holder["response"])

    recorded.answer = holder
    recorded.open = fake_session
    yield recorded


async def test_a_published_release_is_read(wire: Recorded) -> None:
    assert await fetch_release(wire.open, FEED) == Release(version="v1.5.0", notes=NOTES)


async def test_the_release_page_is_kept_only_when_it_is_https(wire: Recorded) -> None:
    """The one link the notes may carry. Anything but an https address is dropped, not shown."""
    page = "https://releases.example/sift/tag/v1.5.0"
    for offered, kept in (
        (page, page),
        ("http://releases.example/sift/tag/v1.5.0", ""),
        ("javascript:alert(1)", ""),
        ("https://someone:secret@example.com/x", ""),
        ("https://" + "x" * 3000, ""),
        (42, ""),
    ):
        body = {"tag_name": "v1.5.0", "body": NOTES, "html_url": offered}
        wire.answer["response"] = FakeResponse(200, json.dumps(body).encode())
        found = await fetch_release(wire.open, FEED)
        assert found is not None
        assert found.page == kept, offered


async def test_no_feed_means_no_request_at_all(wire: Recorded) -> None:
    """A backend started without a feed, from a source checkout, checks nothing."""
    for nothing in (None, ""):
        assert await fetch_release(wire.open, nothing) is None
    assert wire.url == ""


def _never(**_: Any) -> Any:
    raise AssertionError("the wrong session was opened for this feed")


async def test_a_feed_given_as_a_literal_address_is_read_as_given_with_no_redirects(
    wire: Recorded,
) -> None:
    """The desktop app's feed override, set in its own settings file: a release served on this
    machine, or a mirror on the local network. No DNS can move a literal address, so the guard is
    not asked, and a redirect, which could lead anywhere, is not followed."""
    for feed in (
        "http://127.0.0.1:8123/latest",
        "http://[::1]:8123/latest",
        "http://192.168.1.20/latest",
    ):
        found = await fetch_release(_never, feed, open_direct=wire.open)
        assert found is not None and found.version == "v1.5.0", feed
        assert wire.url == feed
        assert wire.kwargs["allow_redirects"] is False


async def test_a_feed_given_by_name_goes_through_the_guard(wire: Recorded) -> None:
    """A name is resolved by whatever DNS the machine uses, so it is held to the public-only rule
    whatever it names, `localhost` included (see the guard test below)."""
    found = await fetch_release(wire.open, FEED, open_direct=_never)
    assert found is not None and found.version == "v1.5.0"
    assert wire.kwargs["allow_redirects"] is True


async def test_the_request_carries_nothing_about_this_installation(wire: Recorded) -> None:
    """The privacy property, asserted on the request itself rather than on a policy elsewhere.

    A version check is exactly the kind of request that grows a query parameter later: an install
    id "to count active installations", a version "to serve the right notes". This is the test that
    goes red when it does.
    """
    await fetch_release(wire.open, FEED)

    parts = urlsplit(wire.url)
    assert wire.url == FEED
    assert parts.scheme == "https"
    assert parts.query == ""
    assert parts.fragment == ""

    # Nothing is sent in the request either: no body, no form, no parameters.
    assert "data" not in wire.kwargs
    assert "json" not in wire.kwargs
    assert "params" not in wire.kwargs

    # The only headers are what any client sends. Nothing names Sift, the machine, or the library.
    sent = " ".join(f"{name}: {value}" for name, value in wire.kwargs["headers"].items()).lower()
    for leak in ("sift", "version", "install", "id", "assets", "library", "count"):
        assert leak not in sent


async def test_no_more_than_the_cap_is_read(wire: Recorded) -> None:
    """A feed is a small document. A reply that is not one does not get to be held in memory."""
    huge = json.dumps({"tag_name": "v1.5.0", "body": "x" * (MAX_FEED_BYTES * 2)}).encode()
    wire.answer["response"] = FakeResponse(200, huge)

    # Truncated mid-document, so it is no longer JSON and reads as nothing at all, which is the
    # correct answer for a reply this size, not an error.
    assert await fetch_release(wire.open, FEED) is None


@pytest.mark.parametrize("status", [301, 400, 403, 404, 429, 500, 503])
async def test_a_reply_that_is_not_a_release_is_silence(wire: Recorded, status: int) -> None:
    """Rate-limited, moved, or broken: all the same answer, and none of them an error."""
    wire.answer["response"] = FakeResponse(status, b"")

    assert await fetch_release(wire.open, FEED) is None


@pytest.mark.parametrize(
    "failure",
    [
        OSError("no route to host"),
        TimeoutError(),
        ValueError("something unexpected"),
        RuntimeError("something worse"),
    ],
)
async def test_any_failure_at_all_is_silence(wire: Recorded, failure: Exception) -> None:
    """Nothing that happens here may reach a person. The library is the product; this is a
    nicety."""
    wire.answer["response"] = failure

    assert await fetch_release(wire.open, FEED) is None


async def test_the_request_goes_through_the_guard_that_refuses_private_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check reaches the internet from the server, so it is held to the same address rule as
    every other outbound request rather than trusting a fixed address to stay public.

    Pointed at a name that resolves to loopback, the connector refuses before a socket is opened.
    The refusal is proved by the shared rule having been consulted, not merely by the answer being
    empty: an unreachable address would produce that too.
    """
    from sift.slices.download.sources import net
    from sift.slices.download.url_guard import address_is_public

    asked: list[str] = []
    real = address_is_public

    def spy(address: str) -> bool:
        asked.append(address)
        return real(address)

    monkeypatch.setattr(net, "address_is_public", spy)

    # The real connector, not the stand-in: this is the one test about what the guard actually does.
    assert await fetch_release(net.guarded_session, "http://localhost:9/releases") is None
    # EITHER loopback address. `localhost` resolves to ::1 first on Windows and to 127.0.0.1
    # first on Linux, so naming one of them would make this a test that only passed on one
    # operating system, possibly the one Sift ships to. What is being proved is that
    # the shared rule was consulted about a loopback address, which either form is.
    assert {"127.0.0.1", "::1"} & set(asked), asked


async def test_a_literal_feed_is_read_through_a_plain_session_by_default() -> None:
    """The desktop app's override pointed at a release served on this machine, with nothing
    handed in for the direct session: the check opens a plain one of its own and reads the
    release. The guarded connector would refuse a loopback address outright, so a release read
    here is the proof that it was not the one asked."""
    from aiohttp import web

    async def latest(_request: web.Request) -> web.Response:
        return web.json_response({"tag_name": "v1.5.0", "body": NOTES})

    app = web.Application()
    app.router.add_get("/latest", latest)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        port = runner.addresses[0][1]
        found = await fetch_release(_never, f"http://127.0.0.1:{port}/latest")
    finally:
        await runner.cleanup()

    assert found == Release(version="v1.5.0", notes=NOTES)
