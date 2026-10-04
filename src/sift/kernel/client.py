# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which client a request came from: the kind of window, and the device it runs on.

Insights wants to say where a library is used (the desktop app, a browser on a computer, a phone,
a tablet, the Remote) and which of somebody's devices a sitting, a search or a page was on. Neither
can be worked out afterwards, so each record is stamped with it when it is written, and this is the
one place that says what the stamp is.

## The kind is what the window says it is

The window knows and the server does not. The desktop shell tells its own page that it is the app
(`bridge.isDesktop()`); a phone is a phone because the page is drawn in the phone's layout, which
is the page's own decision about its width; the Remote is a screen the page knows it is showing.
Guessing any of those from a user agent would be a second opinion that disagrees with the screen
somebody is looking at. So the page sends one word on every request (`CLIENT_HEADER`) and this
reads it against a closed list. A word not on the list, or none (a script, an older page), is
`unknown`, never a guess.

It is a fact about somebody's own use, said by their own page, and read back only to them, so a
page that lies about it misleads nobody but itself. Nothing is decided by it.

## The device is a cookie the server sets

A random value (`new_device`) in a cookie of its own (`DEVICE_COOKIE_NAME`), set by sign-in and by
the read of who is signed in when it is missing, and kept across signing out: it is the browser or
the app install, not the person or the session, so it says nothing about anybody. HttpOnly, so no
script can read it or copy it elsewhere. Two Users signing in on one browser share it, which is
right: it is one device.

Not the session row's id: a session ends at every sign-out and a device does not, so "which
device" would be a new answer every week. The session row keeps the device it began on
(`slices/auth`), which is what lets a list of where somebody is signed in say so later.

## How a writer reads it

`client_of(request)` for a route that holds its request. `current()` for a writer several calls
below one (the ledger's door), which the sign-in door fills once per request when it resolves the
session (`enter`). A task of Sift's own runs outside any request and reads None there: Sift's own
acts happen on the computer running it and are stamped with nothing.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final, Literal, Protocol, cast

#: The header a page names its kind of window in. Lower case, as a request's headers are read.
CLIENT_HEADER: Final = "sift-client"

#: The cookie that carries a device's id. Beside `SESSION_COOKIE_NAME` in spirit, here because the
#: device is not a sign-in.
DEVICE_COOKIE_NAME: Final = "sift_device"

#: How long the device cookie lives: 400 days, the most a browser keeps any cookie for. It is set
#: again at every sign-in, so a device used once a year keeps its id.
DEVICE_COOKIE_SECONDS: Final = 400 * 24 * 60 * 60

#: Every kind of window, as the page says it and as a record stores it.
ClientKind = Literal["app", "computer", "phone", "tablet", "remote", "unknown"]

#: The words, for a reader that checks a stored value or a test that walks them.
CLIENT_KINDS: Final[frozenset[str]] = frozenset(
    {"app", "computer", "phone", "tablet", "remote", "unknown"}
)

#: What a device id looks like: what `new_device` makes, and nothing a cookie could carry that is
#: not. A cookie edited by hand to something else reads as no device rather than being stored.
_DEVICE_SHAPE: Final = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@dataclass(frozen=True, slots=True)
class Client:
    """The client of one request: which kind of window, and which device (None before it has one).

    Frozen, and two plain values, so a writer can store both columns without knowing where they
    came from, and a test can build one by hand.
    """

    kind: ClientKind
    device: str | None


#: A request that said nothing about itself.
UNKNOWN: Final = Client("unknown", None)


class Carries(Protocol):
    """What a request or a socket has that this reads: its headers and its cookies."""

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def cookies(self) -> Mapping[str, str]: ...


def kind_from(said: str | None) -> ClientKind:
    """The kind a page named, or `unknown` for nothing or a word not on the list."""
    word = (said or "").strip().lower()
    return cast("ClientKind", word) if word in CLIENT_KINDS else "unknown"


def device_from(said: str | None) -> str | None:
    """A device id as a cookie carried it, or None when it carried none or something else."""
    if said is None or not _DEVICE_SHAPE.fullmatch(said):
        return None
    return said


def new_device() -> str:
    """A fresh device id: 128 random bits, nothing else, so it says nothing about anybody."""
    return secrets.token_urlsafe(16)


def client_of(connection: Carries) -> Client:
    """The client of this request or socket, read off what it carries."""
    return Client(
        kind=kind_from(connection.headers.get(CLIENT_HEADER)),
        device=device_from(connection.cookies.get(DEVICE_COOKIE_NAME)),
    )


_CURRENT: ContextVar[Client | None] = ContextVar("sift_client", default=None)


def enter(client: Client) -> None:
    """Say which client the request being served came from, for the writers below its route.

    Called by the sign-in door once a session has resolved, so it is set for exactly the requests
    somebody signed in made. Each request is served in a task of its own, so the value is that
    request's and goes with it; nothing resets it because nothing outlives it.
    """
    _CURRENT.set(client)


def current() -> Client | None:
    """The client of the request being served, or None outside one (a task of Sift's own)."""
    return _CURRENT.get()
