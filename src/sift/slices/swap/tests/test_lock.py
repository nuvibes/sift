# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lock: TLS 1.3 keyed by the token's secret, on loopback, and every way in it refuses.

The certificate hazard is a test here twice over: behaviourally (a client with no key, and one with
only a certificate, are turned away, which a server context holding a certificate would let in),
and statically: nothing in this slice's source loads a certificate at all, which is read in
`test_what_a_swap_never_says.py` so that it runs on an interpreter where these skip.
"""

from __future__ import annotations

import asyncio
import datetime
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from sift.slices.swap import lock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13"),
]

_KEY = b"\x11" * 32


@asynccontextmanager
async def _host(seconds: float = 2.0) -> AsyncIterator[tuple[int, list[str]]]:
    """A loopback listener that locks each connection and echoes four bytes back."""
    said: list[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            reader, writer = await lock.accept(reader, writer, _KEY, seconds=seconds)
        except lock.LockFailed:
            said.append("refused")
            return
        said.append(str(lock.negotiated(writer)))
        try:
            writer.write(await reader.readexactly(4))
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield int(server.sockets[0].getsockname()[1]), said
    finally:
        server.close()


async def _settled(said: list[str]) -> None:
    for _ in range(100):
        if said:
            return
        await asyncio.sleep(0.02)


async def test_the_right_key_locks_with_tls_1_3() -> None:
    async with _host() as (port, said):
        reader, writer = await lock.dial("127.0.0.1", port, None, _KEY)
        assert lock.negotiated(writer) == "TLSv1.3"
        # No certificate came from the host: there is none to come.
        assert writer.get_extra_info("ssl_object").getpeercert(binary_form=True) is None
        writer.write(b"ping")
        await writer.drain()
        assert await reader.readexactly(4) == b"ping"
        writer.close()
        await _settled(said)
        assert said == ["TLSv1.3"]


async def test_a_wrong_key_is_refused() -> None:
    async with _host() as (port, said):
        with pytest.raises(lock.LockFailed):
            await lock.dial("127.0.0.1", port, None, b"\x12" * 32)
        await _settled(said)
        assert said == ["refused"]


def _keyless() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _certificate(tmp_path: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "swap-test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "c.pem", tmp_path / "k.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


async def _try(port: int, context: ssl.SSLContext) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port, ssl=context, server_hostname=""), 5
        )
    except (ssl.SSLError, OSError, TimeoutError):
        return False
    writer.close()
    return True


async def test_no_key_and_a_certificate_alone_are_refused(tmp_path: Path) -> None:
    """THE CERTIFICATE HAZARD: with a certificate on the host, both of these get in."""
    cert, key = _certificate(tmp_path)
    with_certificate = _keyless()
    with_certificate.load_cert_chain(cert, key)
    async with _host() as (port, said):
        assert not await _try(port, _keyless())
        assert not await _try(port, with_certificate)
        await _settled(said)
        assert set(said) == {"refused"}


async def test_a_handshake_not_done_in_time_is_a_failed_connection() -> None:
    async with _host(seconds=0.3) as (port, said):
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        loop = asyncio.get_running_loop()
        began = loop.time()
        await _settled(said)
        writer.close()
        assert said == ["refused"]
        assert loop.time() - began < 2


def test_a_key_that_is_not_thirty_two_bytes_is_refused() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        lock.server_context(b"\x11" * 31)


@asynccontextmanager
async def _proxy(answer: bytes | None) -> AsyncIterator[str]:
    """A CONNECT proxy on loopback that answers with `answer`, or hangs up with none."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        if answer is not None:
            writer.write(answer)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()


async def test_a_tunnel_that_is_not_an_http_proxy_is_refused_before_anything_is_dialled() -> None:
    for proxy in ("socks5://127.0.0.1:1080", "http://127.0.0.1", "http:///nowhere"):
        with pytest.raises(lock.LockFailed, match="not an address Sift can use"):
            await lock.dial("8.8.4.4", 40000, proxy, _KEY)


async def test_a_tunnel_that_will_not_carry_the_connection_is_a_failed_lock() -> None:
    """Refused, or hung up on with no answer: either way the host was never reached, and the
    guest hears so rather than waiting on a connection that goes nowhere."""
    for answer in (b"HTTP/1.1 403 Forbidden\r\n\r\n", b"HTTP/1.1\r\n\r\n", None):
        async with _proxy(answer) as proxy:
            with pytest.raises(lock.LockFailed, match="did not carry the connection"):
                await lock.dial("8.8.4.4", 40000, proxy, _KEY, seconds=2.0)


async def test_a_host_nobody_is_listening_on_could_not_be_reached() -> None:
    server = await asyncio.start_server(lambda _r, _w: None, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    server.close()
    await server.wait_closed()

    with pytest.raises(lock.LockFailed, match="could not be reached"):
        await lock.dial("127.0.0.1", port, None, _KEY, seconds=2.0)
