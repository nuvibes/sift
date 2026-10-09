# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which client a request came from: the kind of window its page says, and a device cookie."""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final, Literal, Protocol, cast

#: Lower case, as a request's headers are read.
CLIENT_HEADER: Final = "sift-client"

DEVICE_COOKIE_NAME: Final = "sift_device"

#: The most a browser keeps any cookie; set again at every sign-in.
DEVICE_COOKIE_SECONDS: Final = 400 * 24 * 60 * 60

ClientKind = Literal["app", "computer", "phone", "tablet", "remote", "unknown"]

CLIENT_KINDS: Final[frozenset[str]] = frozenset(
    {"app", "computer", "phone", "tablet", "remote", "unknown"}
)

#: A cookie edited to anything else reads as no device rather than being stored.
_DEVICE_SHAPE: Final = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@dataclass(frozen=True, slots=True)
class Client:
    """One request's client: the kind of window, and the device (None before it has one)."""

    kind: ClientKind
    device: str | None


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
    """Record the client of the request being served, for the writers below its route."""
    _CURRENT.set(client)


def current() -> Client | None:
    """The client of the request being served, or None outside one (a task of Sift's own)."""
    return _CURRENT.get()
