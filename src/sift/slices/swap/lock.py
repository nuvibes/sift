# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lock on a swap's connection: TLS 1.3 keyed by the token's secret, and nothing else.

## A pre-shared key, and NO certificate on either side

Both ends hold the same 32 random bytes (the host made them, the guest read them from the token),
so TLS can be keyed by them directly (`set_psk_server_callback` / `set_psk_client_callback`,
identity `sift-swap-1`). A connection that completes the handshake is one whose other end holds
the secret. That is the whole proof: there is no certificate to check, pin or get wrong.

!! THE SERVER CONTEXT MUST NOT HOLD A CERTIFICATE. With CPython 3.13 and OpenSSL 3.5, a server
context that loads a certificate as well as the key lets a client with NO key in (it simply takes
the certificate handshake instead) and gives even a right-key client a certificate session. So
nothing here loads one, and `tests/test_lock.py` refuses any call that would (a scan of this slice's
source) as well as proving a keyless client is turned away.

## The rest of the lock

TLS 1.3 at minimum: a PSK over 1.2 is a different and weaker construction, and the standard
library would otherwise offer it. The ciphers are the library's own TLS 1.3 set; nothing narrows
them. A handshake not complete within ten seconds is a failed connection.

**Proof of connection is the completed handshake, never a proxy's "200".** A tunnel provider's
CONNECT can answer "200" at once for a port nobody is listening on.
`dial` reads the proxy's answer only to know whether to go on; whether anybody is THERE is decided
by the handshake that follows.
"""

from __future__ import annotations

import asyncio
import ssl
import sys
from collections.abc import Callable
from urllib.parse import urlsplit

from sift.kernel.log import get_logger

log = get_logger(__name__)

#: The PSK identity both ends name. Versioned, so a later lock can be told apart by its identity.
IDENTITY = "sift-swap-1"

#: A handshake not complete in this long is a failed connection.
HANDSHAKE_SECONDS = 10.0

#: The longest a proxy's answer to CONNECT may be before it is not a proxy's answer.
_PROXY_REPLY_CAP = 8 * 1024


#: Whether this interpreter can key TLS with a pre-shared key at all. The callbacks arrived in
#: CPython 3.13 and need an OpenSSL built with PSK; the shipped build is 3.13 (with OpenSSL 3.5). On
#: anything older a swap is refused with `NOT_HERE`, never attempted some other way.
PSK_AVAILABLE = sys.version_info >= (3, 13) and hasattr(ssl.SSLContext, "set_psk_server_callback")

NOT_HERE = "This copy of Sift can't lock a swap. Update Sift, then try again."


class LockFailed(ConnectionError):
    """The connection could not be locked: wrong key, no key, no answer, or no handshake in time."""


def _require_key(secret: bytes) -> bytes:
    if len(secret) != 32:
        raise ValueError("the lock's key is 32 bytes")
    return bytes(secret)


def server_context(secret: bytes) -> ssl.SSLContext:
    """The host's context: the key and nothing else. See the module docstring for why."""
    key = _require_key(secret)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3

    def answer(identity: str | None) -> bytes:
        # An unknown identity gets an empty key, which OpenSSL treats as "no such identity" and
        # the handshake fails, never a fall back to anything else.
        return key if identity == IDENTITY else b""

    _server_key(context, answer)
    return context


def client_context(secret: bytes) -> ssl.SSLContext:
    """The guest's context: the key, and no certificate check because there is no certificate."""
    key = _require_key(secret)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    _client_key(context, lambda _hint: (IDENTITY, key))
    return context


def _server_key(context: ssl.SSLContext, answer: Callable[[str | None], bytes]) -> None:
    context.set_psk_server_callback(answer)


def _client_key(
    context: ssl.SSLContext, answer: Callable[[str | None], tuple[str | None, bytes]]
) -> None:
    context.set_psk_client_callback(answer)


def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
    except Exception:  # pragma: no cover (already gone)
        log.debug("swap.lock_close_failed")


async def accept(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    secret: bytes,
    *,
    seconds: float = HANDSHAKE_SECONDS,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Lock an accepted connection as the host. Raises `LockFailed`; the connection is closed."""
    try:
        await asyncio.wait_for(
            writer.start_tls(server_context(secret), ssl_handshake_timeout=seconds), seconds
        )
    except (TimeoutError, OSError, ssl.SSLError, ConnectionError) as error:
        _close(writer)
        raise LockFailed("the connection could not be locked") from error
    return reader, writer


async def _through_proxy(
    proxy: str, address: str, port: int, seconds: float
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A plain connection to `address:port` through an HTTP CONNECT proxy on this device."""
    parts = urlsplit(proxy)
    if parts.scheme != "http" or not parts.hostname or parts.port is None:
        raise LockFailed("the tunnel's proxy is not an address Sift can use")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(parts.hostname, parts.port), seconds
    )
    target = f"{address}:{port}"
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode("ascii"))
    try:
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), seconds)
    except (TimeoutError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
        _close(writer)
        raise LockFailed("the tunnel did not carry the connection") from error
    status = head.split(b"\r\n", 1)[0].split()
    if len(head) > _PROXY_REPLY_CAP or len(status) < 2 or status[1] != b"200":
        _close(writer)
        raise LockFailed("the tunnel did not carry the connection")
    return reader, writer


async def dial(
    address: str,
    port: int,
    proxy: str | None,
    secret: bytes,
    *,
    seconds: float = HANDSHAKE_SECONDS,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Dial the host as the guest and lock the connection. Raises `LockFailed`.

    `proxy` is the guest's own tunnel (`http://127.0.0.1:<port>`). None dials directly, and only
    a test does that: `session.py` refuses a swap whose guest has no tunnel before it gets here.
    """
    context = client_context(secret)
    try:
        if proxy is None:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port), seconds)
        else:
            reader, writer = await _through_proxy(proxy, address, port, seconds)
    except LockFailed:
        raise
    except (TimeoutError, OSError) as error:
        raise LockFailed("the host could not be reached") from error
    try:
        await asyncio.wait_for(
            writer.start_tls(context, server_hostname="", ssl_handshake_timeout=seconds), seconds
        )
    except (TimeoutError, OSError, ssl.SSLError, ConnectionError) as error:
        _close(writer)
        raise LockFailed("the connection could not be locked") from error
    return reader, writer


def negotiated(writer: asyncio.StreamWriter) -> str | None:
    """The TLS version a locked connection agreed, for a test to prove it is 1.3."""
    obj = writer.get_extra_info("ssl_object")
    return None if obj is None else obj.version()
