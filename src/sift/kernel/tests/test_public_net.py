# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tool proxy, driven the way the download tools drive it: CONNECT for https, absolute URIs for
plain http, a redirect followed by asking again. A local server stands in for the internet; the
proxy's resolver says which public address a name has, and its dialer sends that address to the
local server, so what is dialed is exactly what was checked.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import http.client
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import pytest
import structlog

from sift.kernel import public_net
from sift.kernel.public_net import (
    REFUSED_STATUS,
    Refusal,
    ToolProxy,
    address_is_public,
    literal_address,
)

PUBLIC = "93.184.216.34"
OTHER_PUBLIC = "93.184.216.35"

#: What each name resolves to, as far as the proxy is told.
NAMES: dict[str, list[str]] = {
    "media.example.com": [PUBLIC],
    "mixed.example.com": [PUBLIC, "10.0.0.5"],
    "lan.example.com": ["192.168.1.20"],
    "flaky.example.com": [OTHER_PUBLIC, PUBLIC],
    "gone.example.com": ["93.184.216.36"],
}


class _Site(BaseHTTPRequestHandler):
    """The internet, as far as these tests go."""

    seen: list[str] = []  # noqa: RUF012 (one server per test, reset by the fixture)

    def log_message(self, *_args: Any) -> None:
        return None

    def do_GET(self) -> None:
        self.seen.append(self.requestline)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.5/secret")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/silent":
            self.close_connection = True
            return
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Keep-Alive", "timeout=5")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self.seen.append(self.requestline)
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Quiet(ThreadingHTTPServer):
    def handle_error(self, *_args: Any) -> None:
        return None  # a test that hangs up on purpose is not an error worth printing


@pytest.fixture
def site() -> Iterator[ThreadingHTTPServer]:
    _Site.seen = []
    server = _Quiet(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


class _Network:
    """The proxy's view of the world: names, and where a dial to a public address really lands."""

    def __init__(self, site_port: int) -> None:
        self.site_port = site_port
        self.resolved: list[str] = []
        self.dialed: list[tuple[str, int]] = []

    async def resolve(self, host: str, _port: int) -> list[str]:
        self.resolved.append(host)
        if host in NAMES:
            return NAMES[host]
        found = literal_address(host)
        if found is not None:
            return [str(found)]
        raise OSError("not found")

    async def dial(
        self, address: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        self.dialed.append((address, port))
        if address != PUBLIC:
            raise OSError("unreachable")
        return await asyncio.open_connection("127.0.0.1", self.site_port)


@pytest.fixture
def network(site: ThreadingHTTPServer) -> _Network:
    return _Network(site.server_address[1])


@pytest.fixture
def proxy(network: _Network) -> Iterator[ToolProxy]:
    made = ToolProxy(resolve=network.resolve, dial=network.dial)
    yield made
    made.close()


def _credentials(address: str) -> dict[str, str]:
    parts = urlsplit(address)
    token = base64.b64encode(f"{parts.username}:{parts.password}".encode()).decode()
    return {"Proxy-Authorization": f"Basic {token}"}


def _tunnel(address: str, host: str, port: int) -> http.client.HTTPConnection:
    """What a tool does for https: CONNECT through the proxy, then speak inside the tunnel."""
    parts = urlsplit(address)
    connection = http.client.HTTPConnection("127.0.0.1", parts.port, timeout=10)
    connection.set_tunnel(host, port, headers=_credentials(address))
    return connection


def _opener(address: str) -> urllib.request.OpenerDirector:
    """What a tool does for plain http: absolute URIs to the proxy, redirects followed."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": address}))


def _raw(address: str, data: bytes, *, hang_up: bool = False) -> bytes:
    """Send exactly `data` to the proxy and read everything it answers.

    `hang_up` closes the sending half once `data` is sent, as a client that gives up does.
    """
    with socket.create_connection(("127.0.0.1", urlsplit(address).port), timeout=10) as sock:
        sock.sendall(data)
        if hang_up:
            sock.shutdown(socket.SHUT_WR)
        chunks = []
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks)


def _listening(address: str) -> bool:
    """Whether anything still listens at the proxy address. A listening socket accepts a connection
    immediately whether or not it is served, so a short wait answers it; a refusal here takes longer."""
    try:
        socket.create_connection(("127.0.0.1", urlsplit(address).port), timeout=0.5).close()
    except OSError:
        return False
    return True


def _settled(proxy: ToolProxy, address: str, connections: int) -> public_net.Traffic:
    """The run's traffic once `connections` of its connections have closed and been counted."""
    deadline = time.monotonic() + 5
    while True:
        traffic = proxy.traffic_of(address)
        assert traffic is not None
        if traffic.connections >= connections or time.monotonic() > deadline:
            return traffic
        time.sleep(0.01)


# --- the rule -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:2800:220:1:248:1893:25c8:1946", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("192.168.1.20", False),
        ("172.16.0.1", False),
        ("100.64.0.1", False),
        ("169.254.169.254", False),
        ("::1", False),
        ("::ffff:127.0.0.1", False),
        ("fe80::1", False),
        ("0.0.0.0", False),  # noqa: S104 (an address judged, not bound)
        ("224.0.0.1", False),
        ("not-an-ip", False),
    ],
)
def test_address_is_public(address: str, public: bool) -> None:
    assert address_is_public(address) is public


@pytest.mark.parametrize(
    ("host", "literal"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("[::1]", "::1"),
        ("2130706433", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("0x7f.1", "127.0.0.1"),
        ("127.1", "127.0.0.1"),
        ("10.0.0.5.", "10.0.0.5"),
        ("media.example.com", None),
        ("999999999999", None),
    ],
)
def test_a_literal_address_is_read_in_every_spelling_a_client_accepts(
    host: str, literal: str | None
) -> None:
    found = literal_address(host)
    assert (str(found) if found is not None else None) == literal


# --- direct ---------------------------------------------------------------------------------------


def test_what_a_tool_sends_after_a_plain_request_is_drained_and_not_forwarded(
    proxy: ToolProxy, network: _Network
) -> None:
    """A plain-http request with no body is complete at its blank line. Bytes a tool sends after
    it are read and dropped so the tool is never blocked writing them, and none reach the site."""
    _Site.seen = []
    address = proxy.address_for()
    token = _credentials(address)["Proxy-Authorization"]
    head = (
        "GET http://media.example.com/hello HTTP/1.1\r\nHost: media.example.com\r\n"
        f"Proxy-Authorization: {token}\r\n\r\n"
    )
    reply = _raw(address, head.encode() + b"stray bytes after the request")
    assert reply.endswith(b"hello")
    assert _Site.seen == ["GET /hello HTTP/1.1"]


def test_a_connect_to_a_private_address_is_refused_with_403(
    proxy: ToolProxy, network: _Network
) -> None:
    address = proxy.address_for()
    connection = _tunnel(address, "10.0.0.5", 443)
    with pytest.raises(OSError, match="403 Blocked by Sift"):
        connection.request("GET", "/")
    assert network.dialed == []
    traffic = proxy.traffic_of(address)
    assert traffic is not None
    assert traffic.refused == (Refusal("10.0.0.5:443", "private_address"),)


def test_the_proxys_lines_carry_nothing_of_the_download_that_started_it(
    proxy: ToolProxy, network: _Network, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proxy serves every tool for the life of the process. Its first listener is started by
    whichever download runs a tool first, and a listener started under that download's log context
    would name it on every line after, refusals included, for ever."""
    seen: list[dict[str, Any]] = []

    def record(kind: str, **fields: Any) -> None:
        seen.append(dict(structlog.contextvars.get_contextvars()))

    monkeypatch.setattr(public_net, "security_event", record)
    with structlog.contextvars.bound_contextvars(download_id="the-first"):
        address = proxy.address_for()
    connection = _tunnel(address, "10.0.0.5", 443)
    with pytest.raises(OSError, match="403 Blocked by Sift"):
        connection.request("GET", "/")
    assert seen == [{}]


def test_a_public_name_passes_through_to_the_address_that_was_checked(
    proxy: ToolProxy, network: _Network
) -> None:
    address = proxy.address_for()
    connection = _tunnel(address, "media.example.com", 80)
    connection.request("GET", "/hello")
    assert connection.getresponse().read() == b"hello"
    connection.close()
    assert network.dialed == [(PUBLIC, 80)]
    traffic = _settled(proxy, address, 1)
    assert traffic.refused == ()
    assert traffic.sent > 0 and traffic.received > len(b"hello")


def test_a_name_with_one_private_address_among_public_ones_is_refused(
    proxy: ToolProxy, network: _Network
) -> None:
    connection = _tunnel(proxy.address_for(), "mixed.example.com", 443)
    with pytest.raises(OSError, match="403"):
        connection.request("GET", "/")
    assert network.dialed == []


def test_a_name_that_resolves_into_the_lan_is_refused(proxy: ToolProxy) -> None:
    connection = _tunnel(proxy.address_for(), "lan.example.com", 443)
    with pytest.raises(OSError, match="403"):
        connection.request("GET", "/")


@pytest.mark.parametrize("host", ["2130706433", "0x7f.1", "[::1]", "169.254.169.254"])
def test_a_literal_private_address_is_refused_in_any_spelling(proxy: ToolProxy, host: str) -> None:
    reply = _raw(proxy.address_for(), f"CONNECT {host}:80 HTTP/1.1\r\n\r\n".encode())
    assert reply.startswith(f"HTTP/1.1 {REFUSED_STATUS}".encode())


def test_a_plain_request_goes_in_origin_form_and_comes_back_closed(
    proxy: ToolProxy, site: ThreadingHTTPServer
) -> None:
    with _opener(proxy.address_for()).open("http://media.example.com/hello", timeout=10) as reply:
        assert reply.read() == b"hello"
        assert reply.headers["Connection"] == "close"
        assert reply.headers["Keep-Alive"] is None
    assert _Site.seen == ["GET /hello HTTP/1.1"]


def test_a_redirect_from_public_to_private_is_refused_at_the_second_hop(
    proxy: ToolProxy, network: _Network
) -> None:
    address = proxy.address_for()
    with pytest.raises(urllib.error.HTTPError) as refused:
        _opener(address).open("http://media.example.com/redirect", timeout=10)
    assert refused.value.code == 403
    assert _Site.seen == ["GET /redirect HTTP/1.1"]
    assert network.dialed == [(PUBLIC, 80)]
    traffic = proxy.traffic_of(address)
    assert traffic is not None
    assert traffic.refused == (Refusal("10.0.0.5:80", "private_address"),)


def test_a_request_body_is_forwarded_whole(proxy: ToolProxy) -> None:
    request = urllib.request.Request("http://media.example.com/echo", data=b"a body", method="POST")
    with _opener(proxy.address_for()).open(request, timeout=10) as reply:
        assert reply.read() == b"a body"


def test_an_unresolvable_name_is_a_502_and_not_a_refusal(proxy: ToolProxy) -> None:
    address = proxy.address_for()
    reply = _raw(address, b"CONNECT missing.example.com:443 HTTP/1.1\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 502 ")


def test_an_unreachable_address_is_tried_and_the_next_one_used(
    proxy: ToolProxy, network: _Network
) -> None:
    connection = _tunnel(proxy.address_for(), "flaky.example.com", 80)
    connection.request("GET", "/hello")
    assert connection.getresponse().read() == b"hello"
    connection.close()
    assert network.dialed == [(OTHER_PUBLIC, 80), (PUBLIC, 80)]


def test_no_address_reachable_is_a_502(proxy: ToolProxy) -> None:
    reply = _raw(proxy.address_for(), b"CONNECT gone.example.com:443 HTTP/1.1\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 502 ")


def test_a_destination_that_answers_nothing_is_a_502(proxy: ToolProxy) -> None:
    reply = _raw(
        proxy.address_for(),
        b"GET http://media.example.com/silent HTTP/1.1\r\nHost: media.example.com\r\n\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 502 ")


@pytest.mark.parametrize(
    ("head", "status"),
    [
        (b"NONSENSE\r\n\r\n", b"400"),
        (b"GET https://media.example.com/ HTTP/1.1\r\n\r\n", b"400"),
        (b"GET /relative HTTP/1.1\r\n\r\n", b"400"),
        (b"CONNECT media.example.com HTTP/1.1\r\n\r\n", b"400"),
        (b"CONNECT media.example.com:99999 HTTP/1.1\r\n\r\n", b"400"),
        (b"GET http:///x HTTP/1.1\r\n\r\n", b"400"),
        (b"GET http://media.example.com/ HTTP/1.1\r\nno colon\r\n\r\n", b"400"),
        (b"GET http://media.example.com/ HTTP/1.1\r\nContent-Length: x\r\n\r\n", b"400"),
        (
            b"POST http://media.example.com/ HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"501",
        ),
        (b"GET http://media.example.com/ HTTP/1.1\r\nX: " + b"a" * 300 + b"\r\n\r\n", b"431"),
    ],
)
def test_what_is_not_a_proxy_request_is_answered_and_nothing_opened(
    proxy: ToolProxy,
    network: _Network,
    head: bytes,
    status: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A small limit, so an over-long head arrives whole and the refusal is not raced by a reset.
    monkeypatch.setattr(public_net, "HEAD_LIMIT", 256)
    reply = _raw(proxy.address_for(), head)
    assert reply.split(b" ")[1] == status
    assert network.dialed == []


def test_a_request_with_no_host_header_is_given_one(proxy: ToolProxy) -> None:
    reply = _raw(proxy.address_for(), b"GET http://media.example.com:80/hello HTTP/1.0\r\n\r\n")
    assert reply.startswith(b"HTTP/1.0 200") and reply.endswith(b"hello")


def test_a_client_that_hangs_up_before_its_head_costs_nothing(proxy: ToolProxy) -> None:
    assert _raw(proxy.address_for(), b"CONNECT media", hang_up=True) == b""


def test_a_body_shorter_than_it_said_ends_the_connection(proxy: ToolProxy) -> None:
    reply = _raw(
        proxy.address_for(),
        b"POST http://media.example.com/echo HTTP/1.1\r\nContent-Length: 50\r\n\r\nshort",
        hang_up=True,
    )
    assert reply == b""


@pytest.mark.parametrize("value", ["Bearer abc", "Basic %%%", "Basic " + "c2lmdA=="])
def test_credentials_that_name_no_run_are_still_served_and_counted_nowhere(
    proxy: ToolProxy, value: str
) -> None:
    address = proxy.address_for()
    reply = _raw(
        address,
        f"GET http://media.example.com/hello HTTP/1.1\r\nProxy-Authorization: {value}\r\n\r\n".encode(),
    )
    assert reply.endswith(b"hello")
    traffic = proxy.traffic_of(address)
    assert traffic is not None and traffic.connections == 0


# --- through a tunnel ------------------------------------------------------------------------------


class _Upstream:
    """A tunnel's proxy: records what it was asked, and carries it to the local site."""

    def __init__(self, site_port: int) -> None:
        self.site_port = site_port
        self.heads: list[bytes] = []
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = client.recv(65536)
            if not chunk:
                client.close()
                return
            head += chunk
        self.heads.append(head)
        if b"refuse.example.com" in head:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            client.close()
            return
        site = socket.create_connection(("127.0.0.1", self.site_port))
        if head.startswith(b"CONNECT"):
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            site.sendall(head)
        back = threading.Thread(target=self._pipe, args=(site, client), daemon=True)
        back.start()
        self._pipe(client, site)
        back.join(timeout=10)
        for end in (client, site):
            end.close()

    @staticmethod
    def _pipe(source: socket.socket, sink: socket.socket) -> None:
        """Carry one direction until its sender is done, then pass that on as a half-close.

        Both sockets are closed only once both directions are done. On Windows, closing a socket
        that another thread is still waiting to read from resets the connection, and a reset
        throws away whatever the far end had received and not read yet: a reply cut after its head.
        """
        try:
            while chunk := source.recv(65536):
                sink.sendall(chunk)
        except OSError:
            pass
        with contextlib.suppress(OSError):
            sink.shutdown(socket.SHUT_WR)

    def close(self) -> None:
        self.listener.close()


@pytest.fixture
def upstream(site: ThreadingHTTPServer) -> Iterator[_Upstream]:
    made = _Upstream(site.server_address[1])
    yield made
    made.close()


def test_a_tunnelled_connect_is_chained_and_the_name_is_not_resolved_here(
    proxy: ToolProxy, network: _Network, upstream: _Upstream
) -> None:
    address = proxy.address_for(upstream.url)
    assert proxy.chained_to(address) == upstream.url
    connection = _tunnel(address, "media.example.com", 443)
    connection.request("GET", "/hello")
    assert connection.getresponse().read() == b"hello"
    connection.close()
    assert upstream.heads[0].startswith(b"CONNECT media.example.com:443 HTTP/1.1\r\n")
    # No local lookup: that would tell this machine's resolver which sites the tunnel carries.
    assert network.resolved == [] and network.dialed == []


@pytest.mark.parametrize(
    ("host", "reason"),
    [
        ("10.0.0.5", "private_address"),
        ("2130706433", "private_address"),
        ("localhost", "local_name"),
    ],
)
def test_a_tunnelled_connect_to_this_machine_or_a_private_address_is_refused(
    proxy: ToolProxy, upstream: _Upstream, host: str, reason: str
) -> None:
    address = proxy.address_for(upstream.url)
    connection = _tunnel(address, host, 443)
    with pytest.raises(OSError, match="403"):
        connection.request("GET", "/")
    assert upstream.heads == []
    traffic = proxy.traffic_of(address)
    assert traffic is not None and traffic.refused[0].reason == reason


def test_the_tunnel_stand_in_hands_a_slow_reader_the_whole_reply(upstream: _Upstream) -> None:
    """The stand-in itself, read late: the reply and the site's hang-up are both in before the
    first read, which is what a loaded machine does to the proxy reading from it."""
    with socket.create_connection(("127.0.0.1", upstream.port), timeout=10) as raw:
        raw.sendall(
            b"GET http://media.example.com/hello HTTP/1.1\r\nHost: media.example.com\r\n"
            b"Connection: close\r\n\r\n"
        )
        time.sleep(0.5)
        reply = b""
        while chunk := raw.recv(65536):
            reply += chunk
    assert reply.startswith(b"HTTP/1.0 200") and reply.endswith(b"hello")


def test_a_tunnelled_plain_request_goes_to_the_tunnel_whole(
    proxy: ToolProxy, upstream: _Upstream
) -> None:
    with _opener(proxy.address_for(upstream.url)).open(
        "http://media.example.com/hello", timeout=10
    ) as reply:
        assert reply.read() == b"hello"
    forwarded = upstream.heads[0].decode("latin-1")
    assert forwarded.startswith("GET http://media.example.com/hello HTTP/1.1\r\n")
    assert "Connection: close" in forwarded
    assert "Proxy-Authorization" not in forwarded


def test_a_tunnel_that_cannot_reach_the_destination_is_a_502(
    proxy: ToolProxy, upstream: _Upstream
) -> None:
    reply = _raw(
        proxy.address_for(upstream.url), b"CONNECT refuse.example.com:443 HTTP/1.1\r\n\r\n"
    )
    assert reply.startswith(b"HTTP/1.1 502 ")


def test_a_tunnel_that_is_not_listening_is_a_502(proxy: ToolProxy) -> None:
    with socket.create_server(("127.0.0.1", 0)) as taken:
        port = taken.getsockname()[1]
    reply = _raw(
        proxy.address_for(f"http://127.0.0.1:{port}"),
        b"CONNECT media.example.com:443 HTTP/1.1\r\n\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 502 ")


def test_a_tunnel_that_hangs_up_without_answering_is_a_502(proxy: ToolProxy) -> None:
    with socket.create_server(("127.0.0.1", 0)) as rude:
        address = proxy.address_for(f"http://127.0.0.1:{rude.getsockname()[1]}")

        def hang_up() -> None:
            client, _ = rude.accept()
            client.recv(65536)
            client.close()

        threading.Thread(target=hang_up, daemon=True).start()
        reply = _raw(address, b"CONNECT media.example.com:443 HTTP/1.1\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 502 ")


# --- the addresses it hands out --------------------------------------------------------------------


@pytest.mark.parametrize(
    "upstream_url",
    ["socks5://127.0.0.1:1080", "http://127.0.0.1", "http://u:p@127.0.0.1:8080", "http://h:x"],
)
def test_only_a_plain_http_proxy_can_be_chained_to(proxy: ToolProxy, upstream_url: str) -> None:
    with pytest.raises(ValueError, match="plain HTTP proxy"):
        proxy.address_for(upstream_url)


def test_one_listener_per_way_out_and_one_run_per_address(proxy: ToolProxy) -> None:
    first, second = proxy.address_for(), proxy.address_for()
    tunnelled = proxy.address_for("http://127.0.0.1:45000")
    assert urlsplit(first).port == urlsplit(second).port != urlsplit(tunnelled).port
    assert urlsplit(first).password != urlsplit(second).password
    assert urlsplit(first).hostname == "127.0.0.1"
    assert proxy.chained_to(first) is None
    with pytest.raises(LookupError):
        proxy.chained_to("http://sift:x@127.0.0.1:1")
    assert proxy.traffic_of("http://sift:unknown@127.0.0.1:1") is None
    assert proxy.traffic_of("http://127.0.0.1:1") is None


def test_only_the_latest_runs_are_remembered(
    proxy: ToolProxy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(public_net, "REMEMBERED_RUNS", 2)
    oldest = proxy.address_for()
    proxy.address_for()
    proxy.address_for()
    assert proxy.traffic_of(oldest) is None


def test_closing_stops_it_and_the_next_use_starts_it_again(proxy: ToolProxy) -> None:
    address = proxy.address_for()
    proxy.close()
    assert not _listening(address)
    proxy.close()  # a second close has nothing to do
    again = proxy.address_for()
    reply = _raw(again, b"GET http://media.example.com/hello HTTP/1.1\r\n\r\n")
    assert reply.endswith(b"hello")


def test_closing_ends_a_connection_that_is_open(proxy: ToolProxy) -> None:
    address = proxy.address_for()
    with socket.create_connection(("127.0.0.1", urlsplit(address).port), timeout=10) as held:
        held.sendall(b"CONNECT media.example.com:80 HTTP/1.1\r\n\r\n")
        assert held.recv(1024).startswith(b"HTTP/1.1 200")
        proxy.close()
        try:
            ended = held.recv(1024)
        except ConnectionResetError:
            ended = b""
        assert ended == b""


def test_an_address_is_served_from_the_moment_it_is_handed(proxy: ToolProxy) -> None:
    """A tool connects the instant it starts; the listener is accepting before its address exists."""
    for _ in range(5):
        address = proxy.address_for()
        reply = _raw(address, b"GET http://media.example.com/hello HTTP/1.1\r\n\r\n")
        assert reply.endswith(b"hello")
        proxy.close()
        assert not _listening(address)


def test_a_port_that_cannot_be_had_is_said_and_nothing_is_kept(
    proxy: ToolProxy, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy.address_for()  # the proxy's thread is running before the bind is refused

    class _Taken(socket.socket):
        def bind(self, address: Any, /) -> None:
            raise OSError("taken")

    monkeypatch.setattr(socket, "socket", _Taken)
    with pytest.raises(OSError, match="taken"):
        proxy.address_for("http://127.0.0.1:45000")
    monkeypatch.undo()
    assert proxy.chained_to(proxy.address_for("http://127.0.0.1:45000")) == "http://127.0.0.1:45000"


def test_a_listener_is_had_where_the_operating_system_has_no_exclusive_bind(
    proxy: ToolProxy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(socket, "SO_EXCLUSIVEADDRUSE", raising=False)
    reply = _raw(proxy.address_for(), b"GET http://media.example.com/hello HTTP/1.1\r\n\r\n")
    assert reply.endswith(b"hello")


def test_the_real_resolver_is_asked_and_its_answer_judged(network: _Network) -> None:
    """With nothing injected: the system resolver, and `localhost` refused for what it resolves to."""
    made = ToolProxy()
    try:
        reply = _raw(made.address_for(), b"CONNECT localhost:80 HTTP/1.1\r\n\r\n")
    finally:
        made.close()
    assert reply.startswith(f"HTTP/1.1 {REFUSED_STATUS}".encode())
    assert network.dialed == []


def test_the_process_has_one_proxy_the_tools_share() -> None:
    assert isinstance(public_net.TOOL_PROXY, ToolProxy)
