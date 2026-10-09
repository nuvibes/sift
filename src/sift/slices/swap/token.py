# SPDX-License-Identifier: AGPL-3.0-or-later
"""The token a host pastes to a guest: where to dial, the one-time key, and whose it is.

Version 2 is 67 bytes, `version | IPv4 | port | secret | expires | host device | server IPv4`, in
base32 groups of four; version 1 lacks the server. The address is the host's VPN exit, and the
server is only compared: two tunnels on one server cannot reach each other. Every field is
checked, a private address refused, and the token is never logged or stored.
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
    #: The host tunnel's VPN server, or None when unknown; compared, never dialled.
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
    """A tunnel's server as a token carries it: a public IPv4 address, else None (unknown)."""
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
    """A new token for this host, valid for a day from `now`; an uncarriable `server` is unknown."""
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
    ip, port, secret, expires, device, server_packed = _unpacked(cleaned)
    if expires <= now:
        raise TokenRefused("This token has run out. Ask them to start a new swap.")
    if expires > now + LIFETIME_SECONDS + _SKEW_SECONDS:
        raise TokenRefused("This isn't a swap token. Paste the whole token you were sent.")
    if port == 0:
        raise TokenRefused("This token doesn't hold an address Sift can dial.")
    if len(device) != ID_BYTES:  # pragma: no cover (the layout fixes it)
        raise TokenRefused("This isn't a swap token. Paste the whole token you were sent.")
    server = _server_in(server_packed)
    return Token(
        address=_check_address(str(ipaddress.IPv4Address(ip))),
        port=port,
        secret=secret,
        expires=expires,
        host_device=id_from_bytes(device),
        server=server,
    )


def _unpacked(cleaned: str) -> tuple[bytes, int, bytes, int, bytes, bytes]:
    """A cleaned token's fields by its version's layout; refused when it has none."""
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
    return ip, port, secret, expires, device, server_packed


def _server_in(server_packed: bytes) -> str | None:
    """The tunnel server a token names, or None; one that is not public is refused."""
    server: str | None = None
    if server_packed != _NO_SERVER:
        server = server_or_none(str(ipaddress.IPv4Address(server_packed)))
        if server is None:
            # A host writes zeros for a server it cannot carry: only an altered token holds one.
            raise TokenRefused("This isn't a swap token. Paste the whole token you were sent.")
    return server
