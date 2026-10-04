# SPDX-License-Identifier: AGPL-3.0-or-later
"""The public internet and nothing else: the rule, and the proxy that holds the download tools to it.

A download is a link somebody pasted, fetched by the server. Everything reached from it (the page,
a CDN the page names, a redirect, every segment of a playlist) must be on the public internet:
never this machine's loopback, the private network it sits on, a link-local or metadata address.
`address_is_public` is that rule, in one place, for every way Sift fetches on a stranger's say-so.

Sift's own fetches apply it to every connection they open. The two download tools cannot be reached
that way: they are separate programs that follow redirects and fetch segments themselves. So they
are told to use `ToolProxy`, a proxy on loopback that applies the rule to every connection they ask
for (CONNECT for https, an absolute-URI request for plain http) and answers 403 for a
destination that is not public, before anything is opened.

**Direct**, the name is resolved here, every address it offers is checked, and the connection goes
to the checked address itself, so a name that answers differently a moment later reaches nothing new.

**Through a tunnel**, the proxy is chained to the tunnel's own, and a name is NOT resolved here: a
lookup on this machine would tell this machine's resolver every site the tunnel carries. A literal
address is checked; a name is resolved inside the tunnel, whose private ranges are the provider's
and not this network's.

The proxy runs on a thread of its own with its own event loop, so the bytes of a download never
queue behind a page being served. It starts on first use and lives as long as the process;
`ToolProxy.close` lets it go at shutdown.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import contextvars
import ipaddress
import re
import secrets
import socket
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlsplit

from sift.kernel.log import get_logger, security_event

log = get_logger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: The most a request or response head may be. A tool's head is a few hundred bytes.
HEAD_LIMIT = 64 * 1024

#: How long a client may take to send its request head once connected.
HEAD_TIMEOUT_SECONDS = 30.0

#: How long opening a connection to a destination, or to the tunnel's proxy, may take.
CONNECT_TIMEOUT_SECONDS = 30.0

#: The largest piece moved in one step of a relay.
RELAY_CHUNK = 64 * 1024

#: How many runs' traffic is kept to be asked about. A run is asked once, right after it ends.
REMEMBERED_RUNS = 256

#: The status line of a refusal. The reason phrase is what a tool quotes when it reports the failure.
REFUSED_STATUS = "403 Blocked by Sift: not a public address"

#: Request headers that belong to the hop from the tool to this proxy and never travel further.
#: `Expect` is dropped so no destination answers `100 Continue`, which would be a second head.
_REQUEST_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "proxy-authorization",
        "te",
        "upgrade",
        "expect",
    }
)

#: Response headers that belong to the hop from the destination to this proxy.
_RESPONSE_HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-connection"})

#: A host the C library reads as a numeric IPv4 address without asking DNS: `2130706433`,
#: `0177.0.0.1`, `0x7f.1`, `127.1`.
_NUMERIC_IPV4 = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$", re.IGNORECASE)

#: Resolves a host and port to every address it offers.
Resolver = Callable[[str, int], Awaitable[list[str]]]

#: Opens a connection to an address and port.
Dialer = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


# --- the rule ------------------------------------------------------------------------------------


def _blocked(ip: IPAddress) -> bool:
    """Whether an address is one the server must never be pointed at.

    `is_global` already excludes most of these; the rest are named so the intent is readable and a
    change to one library's notion of "global" cannot silently open a hole. The metadata address
    (169.254.169.254) is link-local and is caught by that.
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        # ::ffff:127.0.0.1 is the IPv4 address for every purpose that matters here.
        ip = mapped
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def address_is_public(address: str) -> bool:
    """Whether a resolved IP string is one the server may be pointed at.

    The single home of the address policy: the pasted-link check, the connector that pins Sift's
    own requests, and the tools' proxy all decide "public or not" here. An address that will not
    even parse is not public.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not _blocked(ip)


def literal_address(host: str) -> IPAddress | None:
    """The address a host names by itself, or None when it is a name.

    Includes the legacy numeric IPv4 spellings a client turns into an address without asking DNS,
    so `2130706433` is judged as the loopback address it is.
    """
    bare = host.strip("[]").rstrip(".")
    try:
        return ipaddress.ip_address(bare)
    except ValueError:
        pass
    if not _NUMERIC_IPV4.match(bare):
        return None
    try:
        return ipaddress.IPv4Address(socket.inet_aton(bare))
    except OSError:
        return None


def _names_this_machine(host: str) -> bool:
    """`localhost` and every name under it, which resolve to loopback without asking DNS."""
    name = host.rstrip(".").lower()
    return name == "localhost" or name.endswith(".localhost")


# --- what one run went through -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Refusal:
    """One connection the proxy would not open: where it was asked to go, and why not."""

    #: `host:port`, as the tool asked. Never a path or a query.
    destination: str
    #: A short stable code: `private_address` or `local_name`.
    reason: str


@dataclass(frozen=True, slots=True)
class Traffic:
    """What one run of a tool sent through the proxy, once its connections have closed."""

    connections: int = 0
    #: Bytes from the tool to its destinations, heads included.
    sent: int = 0
    #: Bytes from the destinations back to the tool, heads included.
    received: int = 0
    refused: tuple[Refusal, ...] = ()


@dataclass(slots=True)
class _Tally:
    sent: int = 0
    received: int = 0


# --- one request ---------------------------------------------------------------------------------


class _Answer(Exception):
    """The proxy answers the tool itself rather than connecting it anywhere."""

    def __init__(self, status: str, text: str, refusal: Refusal | None = None) -> None:
        super().__init__(status)
        self.status = status
        self.text = text
        self.refusal = refusal

    def reply(self) -> bytes:
        body = (self.text + "\n").encode("utf-8")
        head = (
            f"HTTP/1.1 {self.status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        )
        return head.encode("latin-1") + body


def _bad(text: str) -> _Answer:
    return _Answer("400 Bad Request", text)


@dataclass(frozen=True, slots=True)
class _Request:
    method: str
    target: str
    version: str
    headers: tuple[tuple[str, str], ...]
    host: str
    port: int
    #: The run this request belongs to, from the proxy address the tool was handed.
    run: str | None

    @property
    def tunnels(self) -> bool:
        return self.method == "CONNECT"

    @property
    def destination(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.lower() == name:
                return value
        return None


def _run_of(headers: tuple[tuple[str, str], ...]) -> str | None:
    """The run named in the proxy address's password, which a tool sends as Basic credentials.

    The password rather than the user: a client that is handed a user with no password sends no
    credentials at all.
    """
    for key, value in headers:
        if key.lower() != "proxy-authorization":
            continue
        scheme, _, encoded = value.partition(" ")
        if scheme.lower() != "basic":
            return None
        try:
            decoded = base64.b64decode(encoded.strip(), validate=True).decode("latin-1")
        except (binascii.Error, ValueError):
            return None
        return decoded.partition(":")[2] or None
    return None


def _parse(head: bytes) -> _Request:
    """A request head, as a tool sends it to a proxy. Raises `_Answer` for anything else."""
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise _bad("That is not an HTTP request.")
    method, target, version = parts
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, colon, value = line.partition(":")
        if not colon or not name or name != name.strip():
            raise _bad("A request header could not be read.")
        headers.append((name, value.strip()))
    if method == "CONNECT":
        where = urlsplit("//" + target)
    elif target[:7].lower() == "http://":
        where = urlsplit(target)
    else:
        raise _bad("Only CONNECT and plain http:// requests are proxied.")
    try:
        port = where.port
    except ValueError:
        raise _bad("That port is not a port.") from None
    host = where.hostname
    if not host or (method == "CONNECT" and port is None):
        raise _bad("That request names no destination.")
    pairs = tuple(headers)
    return _Request(method, target, version, pairs, host, port or 80, _run_of(pairs))


def _without(
    headers: tuple[tuple[str, str], ...], hop_by_hop: frozenset[str]
) -> list[tuple[str, str]]:
    """The headers that travel on, less the hop-by-hop ones and any `Connection` names."""
    named = {
        token.strip().lower()
        for key, value in headers
        if key.lower() == "connection"
        for token in value.split(",")
    }
    return [(k, v) for k, v in headers if k.lower() not in hop_by_hop and k.lower() not in named]


def _head(first: str, headers: list[tuple[str, str]]) -> bytes:
    lines = [first, *(f"{key}: {value}" for key, value in headers), "Connection: close", "", ""]
    return "\r\n".join(lines).encode("latin-1")


def _forward_head(request: _Request, *, absolute: bool) -> bytes:
    """The request as it travels on: to a tunnel's proxy whole, to a destination in origin form.

    `Connection: close` on every one: the next request a tool makes may be to another host, and it
    must come back through the proxy to be judged rather than ride a connection already open.
    """
    if absolute:
        target = request.target
    else:
        parts = urlsplit(request.target)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    headers = _without(request.headers, _REQUEST_HOP_BY_HOP)
    if request.header("host") is None:
        headers.insert(0, ("Host", request.destination))
    return _head(f"{request.method} {target} {request.version}", headers)


def _response_head(head: bytes) -> bytes:
    """A destination's response head, told the connection closes after it."""
    lines = head.decode("latin-1").split("\r\n")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, colon, value = line.partition(":")
        if colon:
            headers.append((name, value.strip()))
    return _head(lines[0], _without(tuple(headers), _RESPONSE_HOP_BY_HOP))


def _body_length(request: _Request) -> int:
    """How many body bytes follow a plain request's head."""
    if request.header("transfer-encoding") is not None:
        raise _Answer("501 Not Implemented", "A chunked request body is not proxied.")
    declared = request.header("content-length")
    if declared is None:
        return 0
    if not declared.isdigit():
        raise _bad("That request's length is not a number.")
    return int(declared)


async def _pump(
    source: asyncio.StreamReader, sink: asyncio.StreamWriter, count: Callable[[int], None]
) -> None:
    while chunk := await source.read(RELAY_CHUNK):
        sink.write(chunk)
        count(len(chunk))
        await sink.drain()


async def _first_of(*work: Awaitable[None]) -> None:
    """Run each until one finishes, then stop the rest. A closed or reset side ends the relay."""
    tasks = [asyncio.ensure_future(item) for item in work]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _discard(source: asyncio.StreamReader) -> None:
    """Read until the tool closes. Anything it sends after its request is not proxied."""
    while await source.read(RELAY_CHUNK):
        pass


async def _default_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def _default_dial(
    address: str, port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(address, port, limit=HEAD_LIMIT)


async def _closed(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


# --- the proxy -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Upstream:
    url: str
    host: str
    port: int


def _upstream_of(url: str | None) -> _Upstream | None:
    """The tunnel's proxy a listener chains to. Only a plain HTTP proxy, which is what a tunnel is."""
    if url is None:
        return None
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme != "http" or not parts.hostname or port is None or parts.username is not None:
        raise ValueError("The download tools can only be chained to a plain HTTP proxy.")
    return _Upstream(url, parts.hostname, port)


@dataclass(frozen=True, slots=True)
class _Listener:
    server: asyncio.Server
    upstream: _Upstream | None
    port: int


@dataclass(slots=True)
class _Run:
    connections: int = 0
    sent: int = 0
    received: int = 0
    refused: list[Refusal] = field(default_factory=list)


class ToolProxy:
    """A loopback proxy the download tools are told to use, refusing any destination not public.

    One listener per way out: direct, or chained to one tunnel's proxy. Each address handed out
    names a run of its own in its credentials, so what one run was refused can be asked afterwards
    without mixing it with another run on the same route. The credentials name a run and grant
    nothing; a connection without them is served the same way.
    """

    def __init__(self, *, resolve: Resolver | None = None, dial: Dialer | None = None) -> None:
        self._resolve = resolve if resolve is not None else _default_resolve
        self._dial = dial if dial is not None else _default_dial
        # Two locks, because the proxy's own thread takes one of them. `_starting` guards the loop
        # and the listeners and is never taken there, so a caller may hold it while waiting for a
        # listener to start; `_lock` guards the runs, which the proxy's thread records into.
        self._starting = threading.Lock()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._listeners: dict[_Upstream | None, _Listener] = {}
        self._runs: OrderedDict[str, _Run] = OrderedDict()
        # Touched only on the proxy's own loop.
        self._handlers: set[asyncio.Task[object]] = set()

    # --- the caller's side, on any thread ---

    def address_for(self, upstream: str | None = None) -> str:
        """The proxy address one run of a tool is handed, chained to `upstream` when there is one.

        Starts the proxy on first use. Raises ValueError for an upstream that is not a plain HTTP
        proxy. Nothing here waits on the network; the first address for a way out waits for the
        proxy's own thread to start accepting on it, so it is served from the moment it is handed.
        """
        chained = _upstream_of(upstream)
        run = secrets.token_hex(8)
        with self._starting:
            listener = self._listeners.get(chained)
            if listener is None:
                listener = self._listen(chained)
        with self._lock:
            self._runs[run] = _Run()
            while len(self._runs) > REMEMBERED_RUNS:
                self._runs.popitem(last=False)
        return f"http://sift:{run}@127.0.0.1:{listener.port}"

    def traffic_of(self, address: str) -> Traffic | None:
        """What the run handed `address` went through, or None for an address this did not hand out."""
        run = urlsplit(address).password
        with self._lock:
            found = self._runs.get(run) if run is not None else None
            if found is None:
                return None
            return Traffic(found.connections, found.sent, found.received, tuple(found.refused))

    def chained_to(self, address: str) -> str | None:
        """The tunnel proxy the listener behind `address` chains to; None when it goes direct."""
        port = urlsplit(address).port
        with self._starting:
            for listener in self._listeners.values():
                if listener.port == port:
                    return listener.upstream.url if listener.upstream is not None else None
        raise LookupError("That address is not one this proxy handed out.")

    def close(self) -> None:
        """Stop every listener and connection, and the proxy's thread. The next use starts it again."""
        with self._starting:
            loop, thread = self._loop, self._thread
            listeners = list(self._listeners.values())
            self._loop = self._thread = None
            self._listeners.clear()
        if loop is None or thread is None:
            return
        done = asyncio.run_coroutine_threadsafe(self._shut(listeners), loop)
        with contextlib.suppress(Exception):
            done.result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        # A loop that did not stop in that time is left to the process exit rather than waited on.
        with contextlib.suppress(RuntimeError):
            loop.close()

    # --- setting up, under `_starting` ---

    def _listen(self, chained: _Upstream | None) -> _Listener:
        loop = self._running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # On Windows another program may otherwise bind the same port with SO_REUSEADDR and
            # receive the tools' connections in this proxy's place.
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            sock.setblocking(False)
            port = int(sock.getsockname()[1])
            # Under a context of its own: `call_soon_threadsafe` copies the caller's, and every
            # connection the listener accepts would then log under whichever download started it.
            started = contextvars.Context().run(
                asyncio.run_coroutine_threadsafe, self._serve(sock, chained), loop
            )
            server = started.result(timeout=5)
        except OSError:
            sock.close()
            raise
        listener = _Listener(server, chained, port)
        self._listeners[chained] = listener
        return listener

    def _running_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="sift-tool-proxy", daemon=True)
            thread.start()
            self._loop, self._thread = loop, thread
        return self._loop

    # --- on the proxy's loop ---

    async def _serve(self, sock: socket.socket, upstream: _Upstream | None) -> asyncio.Server:
        async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await self._handle(reader, writer, upstream)

        return await asyncio.start_server(accept, sock=sock, limit=HEAD_LIMIT)

    async def _shut(self, listeners: list[_Listener]) -> None:
        for listener in listeners:
            listener.server.close()
        for task in list(self._handlers):
            task.cancel()
        await asyncio.gather(*self._handlers, return_exceptions=True)

    async def _handle(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream: _Upstream | None,
    ) -> None:
        # Always a task: the server runs each connection as one.
        task = cast("asyncio.Task[object]", asyncio.current_task())
        self._handlers.add(task)
        tally = _Tally()
        request: _Request | None = None
        try:
            head = await asyncio.wait_for(
                client_reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_SECONDS
            )
            request = _parse(head)
            await self._exchange(request, upstream, client_reader, client_writer, tally)
        except _Answer as answer:
            if answer.refusal is not None:
                self._refused(request, answer.refusal)
            client_writer.write(answer.reply())
            with contextlib.suppress(ConnectionError):
                await client_writer.drain()
        except asyncio.LimitOverrunError:
            client_writer.write(_Answer("431 Request Header Fields Too Large", "Too long.").reply())
        except (asyncio.IncompleteReadError, TimeoutError, OSError):
            pass
        finally:
            await _closed(client_writer)
            self._count(request, tally)
            self._handlers.discard(task)

    async def _exchange(
        self,
        request: _Request,
        upstream: _Upstream | None,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        tally: _Tally,
    ) -> None:
        length = 0 if request.tunnels else _body_length(request)
        server_reader, server_writer = await self._open(request, upstream)
        try:
            if request.tunnels:
                if upstream is not None:
                    await self._connect_through(request, server_reader, server_writer, tally)
                client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await _first_of(
                    _pump(client_reader, server_writer, lambda n: _add(tally, sent=n)),
                    _pump(server_reader, client_writer, lambda n: _add(tally, received=n)),
                )
                return
            forwarded = _forward_head(request, absolute=upstream is not None)
            server_writer.write(forwarded)
            tally.sent += len(forwarded)
            while length:
                chunk = await client_reader.read(min(length, RELAY_CHUNK))
                if not chunk:
                    raise asyncio.IncompleteReadError(b"", length)
                server_writer.write(chunk)
                tally.sent += len(chunk)
                length -= len(chunk)
            await server_writer.drain()
            await _first_of(
                self._respond(server_reader, client_writer, tally), _discard(client_reader)
            )
        finally:
            await _closed(server_writer)

    async def _respond(
        self,
        server_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        tally: _Tally,
    ) -> None:
        try:
            head = await server_reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            client_writer.write(_Answer("502 Bad Gateway", "No answer came back.").reply())
            await client_writer.drain()
            return
        rewritten = _response_head(head)
        client_writer.write(rewritten)
        tally.received += len(rewritten)
        await _pump(server_reader, client_writer, lambda n: _add(tally, received=n))

    async def _open(
        self, request: _Request, upstream: _Upstream | None
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Judge the destination, then connect: to it, or to the tunnel's proxy in front of it."""
        literal = literal_address(request.host)
        if literal is not None:
            self._judge(request, [str(literal)])
        if upstream is not None:
            if literal is None and _names_this_machine(request.host):
                raise self._refusal(request, "local_name")
            log.info("tool_proxy.passed", destination=request.destination, route="tunnel")
            # The tunnel's own proxy on loopback, reached as it is: it is not a destination.
            return await self._connect(upstream.host, upstream.port, _default_dial)
        try:
            addresses = await self._resolve(request.host, request.port)
        except (OSError, UnicodeError):
            raise _Answer("502 Bad Gateway", "That address could not be found.") from None
        self._judge(request, addresses)
        for address in addresses:
            try:
                opened = await self._connect(address, request.port, self._dial)
            except _Answer:
                continue
            log.info(
                "tool_proxy.passed",
                destination=request.destination,
                address=address,
                route="direct",
            )
            return opened
        raise _Answer("502 Bad Gateway", "That address could not be reached.")

    def _judge(self, request: _Request, addresses: list[str]) -> None:
        """Refuse unless every address is public: a client is free to connect to any of them."""
        if not addresses or not all(address_is_public(address) for address in addresses):
            raise self._refusal(request, "private_address")

    def _refusal(self, request: _Request, reason: str) -> _Answer:
        security_event("ssrf_blocked", reason=reason, destination=request.destination, via="tool")
        return _Answer(
            REFUSED_STATUS,
            "Sift does not let a download reach this device or its private network.",
            Refusal(request.destination, reason),
        )

    async def _connect(
        self, address: str, port: int, dial: Dialer
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(dial(address, port), CONNECT_TIMEOUT_SECONDS)
        except (OSError, TimeoutError):
            raise _Answer("502 Bad Gateway", "That address could not be reached.") from None

    async def _connect_through(
        self,
        request: _Request,
        server_reader: asyncio.StreamReader,
        server_writer: asyncio.StreamWriter,
        tally: _Tally,
    ) -> None:
        """Ask the tunnel's proxy for the tunnel the tool asked this one for."""
        asked = (f"CONNECT {request.target} HTTP/1.1\r\nHost: {request.target}\r\n\r\n").encode(
            "latin-1"
        )
        server_writer.write(asked)
        tally.sent += len(asked)
        try:
            head = await asyncio.wait_for(
                server_reader.readuntil(b"\r\n\r\n"), CONNECT_TIMEOUT_SECONDS
            )
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            raise _Answer("502 Bad Gateway", "The tunnel did not answer.") from None
        status = head.split(b" ", 2)[1:2]
        if status != [b"200"]:
            raise _Answer("502 Bad Gateway", "The tunnel could not reach that address.")

    # --- the record, from the proxy's loop ---

    def _refused(self, request: _Request | None, refusal: Refusal) -> None:
        with self._lock:
            run = self._runs.get(request.run) if request and request.run else None
            if run is not None:
                run.refused.append(refusal)

    def _count(self, request: _Request | None, tally: _Tally) -> None:
        with self._lock:
            run = self._runs.get(request.run) if request and request.run else None
            if run is not None:
                run.connections += 1
                run.sent += tally.sent
                run.received += tally.received


def _add(tally: _Tally, *, sent: int = 0, received: int = 0) -> None:
    tally.sent += sent
    tally.received += received


#: The one proxy the download tools use, for the life of the process.
TOOL_PROXY = ToolProxy()


__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "HEAD_LIMIT",
    "REFUSED_STATUS",
    "TOOL_PROXY",
    "Dialer",
    "Refusal",
    "Resolver",
    "ToolProxy",
    "Traffic",
    "address_is_public",
    "literal_address",
]
