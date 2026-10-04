# SPDX-License-Identifier: AGPL-3.0-or-later
"""The token a host pastes to a guest: where to dial, the one-time key, and whose it is.

## The layout (version 2, 67 bytes)

    version (1, = 2) | IPv4 (4) | external port (2, big-endian) | secret (32)
    | expires (4, unix seconds, big-endian) | host device id (20) | server IPv4 (4)

in base32 without padding, in groups of four separated by hyphens: 108 characters and 26 hyphens.
It is text rather than a QR because it is pasted into whatever chat two people already use, and
Sift never sends it anywhere itself.

The address is the host's VPN EXIT as the provider reported it through NAT-PMP, never the home's.
The screen says so beside it, word for word (`SENTENCE`).

The server is the VPN server the host's tunnel connects to: its configuration's Endpoint, as the
tunnel client reports it. It is never dialled. The guest compares it with its own tunnel's server
and refuses to join when the two are one machine, because a dial from there to the host's exit is
answered by that server itself and never reaches the host. Two configurations for one server can
leave from different exits, so the exit alone cannot tell. All zeros means the host could not read
it, and the guest falls back to comparing exits.

Version 1 is the same without the server (63 bytes, 101 characters) and is still read: its server
is unknown.

## What the guest checks, every field

The version, the length the version gives, that it has not expired and does not claim to live
longer than a token can, that the address is a public IPv4 address and the port is not zero, and
that a server, when there is one, is a public IPv4 address. A token pointing at a private,
loopback or reserved address is refused: dialled through the guest's own tunnel it would reach
into the VPN provider's own network, which is somewhere a pasted string must not be able to send
anybody. Text shorter than any version's token is a token pasted in part; from there the version
is read before the length, so a token from a later version is told apart from a broken one.

## Never logged, never stored

The token carries the secret that locks the session, so it is held in memory by the session and
nowhere else: not a row, not a job's payload, not a log line. `Token.__repr__` leaves the secret
out so a stray `log.info(token=...)` cannot print it either.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import os
import re
import struct
from dataclasses import dataclass, field

from sift.slices.swap.device import ID_BYTES, group, id_bytes, id_from_bytes

#: The version this device writes.
VERSION = 2

#: 1 + 4 + 2 + 32 + 4 + 20 + 4.
LENGTH = 67

#: The version before the server was carried, still read: 1 + 4 + 2 + 32 + 4 + 20.
_V1 = 1
_V1_LENGTH = 63

#: How long a token can be used: a day from the press.
LIFETIME_SECONDS = 24 * 60 * 60

#: Clock difference forgiven between the two devices, on the "lives too long" check only.
_SKEW_SECONDS = 10 * 60

SECRET_BYTES = 32

#: The sentence the host's screen shows beside the token, word for word.
SENTENCE = "This token holds your VPN's address, not your home's, and it works once."

_LAYOUT = struct.Struct(">B4sH32sI20s4s")
_V1_LAYOUT = struct.Struct(">B4sH32sI20s")
if _LAYOUT.size != LENGTH or _V1_LAYOUT.size != _V1_LENGTH:  # pragma: no cover (an edit)
    raise RuntimeError("the token layout and its length disagree")

#: The server field of a host that could not read its own.
_NO_SERVER = bytes(4)

_SPACE = re.compile(r"\s+")


class TokenRefused(ValueError):
    """The pasted text is not a token this device can use. The message is the screen's words."""


@dataclass(frozen=True, slots=True)
class Token:
    address: str
    port: int
    secret: bytes = field(repr=False)
    expires: int
    host_device: str
    #: The VPN server the host's tunnel connects to, or None when the host could not read it (or
    #: the token is a version 1 token). Compared, never dialled; see the module's docstring.
    server: str | None = None

    @property
    def text(self) -> str:
        """The token as pasted."""
        packed = _LAYOUT.pack(
            VERSION,
            ipaddress.IPv4Address(self.address).packed,
            self.port,
            self.secret,
            self.expires,
            id_bytes(self.host_device),
            _NO_SERVER if self.server is None else ipaddress.IPv4Address(self.server).packed,
        )
        return group(base64.b32encode(packed).decode("ascii").rstrip("="))


def server_or_none(address: str | None) -> str | None:
    """A tunnel's server as a token carries it: a public IPv4 address, else None.

    None for anything else (no answer, an IPv6 server, a private one): the token then says it
    does not know, and the guest compares exits alone.
    """
    if not address:
        return None
    try:
        parsed = ipaddress.ip_address(address.strip().strip("[]"))
    except ValueError:
        return None
    if not isinstance(parsed, ipaddress.IPv4Address) or not parsed.is_global:
        return None
    if parsed.is_multicast:
        return None
    return str(parsed)


def _check_address(address: str) -> str:
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError as error:
        raise TokenRefused("This token doesn't hold an address Sift can dial.") from error
    if not parsed.is_global or parsed.is_multicast:
        raise TokenRefused("This token doesn't hold an address Sift can dial.")
    return str(parsed)


def mint(
    public_ipv4: str,
    external_port: int,
    host_device: str,
    now: int,
    *,
    secret: bytes | None = None,
    server: str | None = None,
) -> Token:
    """A new token for this host, valid for a day from `now`, with a fresh secret.

    `server` is the host tunnel's VPN server as its client reports it; anything a token cannot
    carry (see `server_or_none`) is written as unknown rather than refused."""
    if not 0 < external_port < 65536:
        raise ValueError("the external port is not a port")
    key = os.urandom(SECRET_BYTES) if secret is None else secret
    if len(key) != SECRET_BYTES:
        raise ValueError("a token's secret is 32 bytes")
    id_bytes(host_device)
    return Token(
        address=_check_address(public_ipv4),
        port=external_port,
        secret=key,
        expires=now + LIFETIME_SECONDS,
        host_device=host_device,
        server=server_or_none(server),
    )


def parse(text: str, now: int) -> Token:
    """Read a pasted token, checking every field. Raises `TokenRefused` with the screen's words."""
    cleaned = _SPACE.sub("", text or "").replace("-", "").upper()
    if not cleaned or len(cleaned) > 2 * LENGTH:
        raise TokenRefused("This isn't a swap token. Paste the whole token you were sent.")
    try:
        packed = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (binascii.Error, ValueError) as error:
        raise TokenRefused("This isn't a swap token. Paste the whole token you were sent.") from (
            error
        )
    # Shorter than any version's token is a part of one; as long as one, the version decides.
    if len(packed) < _V1_LENGTH:
        raise TokenRefused("This isn't a swap token. Paste the whole token you were sent.")
    if packed[0] not in (VERSION, _V1):
        raise TokenRefused(
            "This token was made by a different version of Sift. Ask them for a new one."
        )
    server_packed = _NO_SERVER
    if packed[0] == VERSION and len(packed) == LENGTH:
        _version, ip, port, secret, expires, device, server_packed = _LAYOUT.unpack(packed)
    elif packed[0] == _V1 and len(packed) == _V1_LENGTH:
        _version, ip, port, secret, expires, device = _V1_LAYOUT.unpack(packed)
    else:
        raise TokenRefused("This isn't a swap token. Paste the whole token you were sent.")
    if expires <= now:
        raise TokenRefused("This token has run out. Ask them to start a new swap.")
    if expires > now + LIFETIME_SECONDS + _SKEW_SECONDS:
        raise TokenRefused("This isn't a swap token. Paste the whole token you were sent.")
    if port == 0:
        raise TokenRefused("This token doesn't hold an address Sift can dial.")
    if len(device) != ID_BYTES:  # pragma: no cover (the layout fixes it)
        raise TokenRefused("This isn't a swap token. Paste the whole token you were sent.")
    server: str | None = None
    if server_packed != _NO_SERVER:
        server = server_or_none(str(ipaddress.IPv4Address(server_packed)))
        if server is None:
            # A host writes zeros for a server it cannot carry, so only an altered token holds
            # one that is not public.
            raise TokenRefused("This isn't a swap token. Paste the whole token you were sent.")
    return Token(
        address=_check_address(str(ipaddress.IPv4Address(ip))),
        port=port,
        secret=secret,
        expires=expires,
        host_device=id_from_bytes(device),
        server=server,
    )
