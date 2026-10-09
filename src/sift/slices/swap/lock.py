# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lock on a swap's connection: TLS 1.3 keyed by the token's secret, with no certificate."""

from __future__ import annotations

import asyncio
import ssl
import sys
from collections.abc import Callable
from urllib.parse import urlsplit

from sift.kernel.log import get_logger

log = get_logger(__name__)

#: Versioned, so a later lock can be told apart by its identity.
IDENTITY = "sift-swap-1"

#: A handshake not complete in this long is a failed connection.
HANDSHAKE_SECONDS = 10.0

#: The longest a proxy's answer to CONNECT may be before it is not a proxy's answer.
_PROXY_REPLY_CAP = 8 * 1024


#: PSK callbacks need CPython 3.13 and an OpenSSL with PSK; elsewhere a swap is refused.
PSK_AVAILABLE = sys.version_info >= (3, 13) and hasattr(ssl.SSLContext, "set_psk_server_callback")

NOT_HERE = "This copy of Sift can't lock a swap. Update Sift, then try again."


class LockFailed(ConnectionError):
    """The connection could not be locked: wrong key, no key, no answer, or no handshake in time."""


def _require_key(secret: bytes) -> bytes:
    if len(secret) != 32:
        raise ValueError("the lock's key is 32 bytes")
    return bytes(secret)


def server_context(secret: bytes) -> ssl.SSLContext:
    """The host's context, the key alone: a certificate here would let a keyless client in."""
    key = _require_key(secret)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3

    def answer(identity: str | None) -> bytes:
        # An unknown identity gets an empty key, so the handshake fails rather than falling back.
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
    """Dial the host as the guest and lock the connection. Raises `LockFailed`."""
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
