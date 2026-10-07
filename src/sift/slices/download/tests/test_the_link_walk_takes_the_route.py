# SPDX-License-Identifier: AGPL-3.0-or-later
"""A link's redirect walk goes out the way its download does, measured against stand-ins on loopback.

The Site and the tunnel are both stood in for: a local server that counts what reaches it and
whether a tunnel carried it, and a local HTTP proxy in the tunnel's place.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import pytest

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.db import Database
from sift.kernel.jobs import JobFailedPermanently
from sift.kernel.public_net import TOOL_PROXY
from sift.kernel.tunnels import (
    DIRECT,
    EgressRouter,
    ListenPorts,
    TunnelError,
    TunnelProcess,
    TunnelSpec,
)
from sift.slices.download import attempt, url_guard
from sift.slices.download.jobs import download
from sift.slices.download.landing import who_posted
from sift.slices.download.service import DownloadService
from sift.slices.download.sources import curl, net
from sift.slices.download.sources.downloader import Downloader
from sift.slices.download.sources.progress import Report, nowhere
from sift.slices.download.sources.registry import Attribution
from sift.slices.download.sources.resolved import Fetched, ResolvedItem
from sift.slices.download.tests.conftest import MakeContext, RealImport, png_bytes
from sift.slices.download.tests.jobs_support import _caps, _seed_download
from sift.slices.download.url_guard import UrlRejected

_SITE_NAME = "stand-in-site.example"
#: A name this machine's resolver answers with a private address.
_PRIVATE_NAME = "stand-in-private.example"
_CARRIED = "x-stand-in-tunnel"


@dataclass
class _Site:
    """Counts each request, and whether the stand-in tunnel carried it."""

    hops: int
    last: str | None = None
    seen: list[tuple[str, str, bool]] = field(default_factory=list)

    def answer(self, path: str) -> bytes:
        if path != "/start" and not path.startswith("/hop"):
            return b"HTTP/1.1 200 OK\r\n"
        step = 0 if path == "/start" else int(path.removeprefix("/hop"))
        if step < self.hops:
            return f"HTTP/1.1 302 Found\r\nLocation: /hop{step + 1}\r\n".encode()
        if self.last is not None:
            return f"HTTP/1.1 302 Found\r\nLocation: {self.last}\r\n".encode()
        return b"HTTP/1.1 200 OK\r\n"

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
        method, path, _version = head.split("\r\n", 1)[0].split(" ")
        self.seen.append((method, path, f"\r\n{_CARRIED}:" in head.lower()))
        writer.write(self.answer(path) + b"Content-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    def direct(self, method: str) -> int:
        return sum(1 for asked, _path, carried in self.seen if asked == method and not carried)

    def carried(self, method: str) -> int:
        return sum(1 for asked, _path, carried in self.seen if asked == method and carried)


def _proxy_to(site_port: int) -> Callable[..., Awaitable[None]]:
    """A plain HTTP proxy in a tunnel's place: whatever host is asked for, the stand-in Site."""

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
        first, rest = head.split("\r\n", 1)
        method, target, _version = first.split(" ")
        upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", site_port)
        path = urlsplit(target).path
        upstream_writer.write(f"{method} {path} HTTP/1.1\r\n{_CARRIED}: 1\r\n{rest}".encode())
        await upstream_writer.drain()
        writer.write(await upstream_reader.read())
        await writer.drain()
        upstream_writer.close()
        writer.close()

    return serve


@dataclass
class _Ground:
    site: _Site
    site_port: int
    proxy_port: int

    @property
    def url(self) -> str:
        return f"http://{_SITE_NAME}:{self.site_port}/start"


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every time this machine's resolver is asked a stand-in name."""
    names: list[str] = []
    real = socket.getaddrinfo
    answers = {_SITE_NAME: "127.0.0.1", _PRIVATE_NAME: "10.0.0.5"}

    def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        if host in answers:
            names.append(host)
            return real(answers[host], port, *args, **kwargs)
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    # The stand-in Site is on loopback, which the address rule refuses; here it counts as public.
    for module in (url_guard, net):
        rule = module.address_is_public
        monkeypatch.setattr(
            module,
            "address_is_public",
            lambda address, rule=rule: address == "127.0.0.1" or rule(address),
        )
    yield names


@asynccontextmanager
async def _standing(site: _Site) -> AsyncIterator[_Ground]:
    site_server = await asyncio.start_server(site.serve, "127.0.0.1", 0)
    site_port = site_server.sockets[0].getsockname()[1]
    proxy_server = await asyncio.start_server(_proxy_to(site_port), "127.0.0.1", 0)
    try:
        yield _Ground(site, site_port, proxy_server.sockets[0].getsockname()[1])
    finally:
        site_server.close()
        proxy_server.close()


class _Tool:
    """Fetches the link through whatever route it is handed, as the tools do."""

    ran = False

    def handles(self, _url: str) -> bool:
        return True

    async def fetch(
        self,
        url: str,
        *,
        into: Path,
        cookies_file: Path | None = None,
        proxy: str | None = None,
        already_have: Callable[[str], Awaitable[bool]] | None = None,
        report: Report = nowhere,
    ) -> Fetched:
        self.ran = True
        async with aiohttp.ClientSession() as session, session.get(url, proxy=proxy) as answer:
            await answer.read()
        return Fetched(files=[])


def _router(
    ground: _Ground, monkeypatch: pytest.MonkeyPatch, *, route: str = "t1", up: bool = True
) -> EgressRouter:
    async def chosen() -> str:
        return route

    async def metrics() -> tuple[int | None, str | None]:
        return 1, "192.0.2.44"

    tunnel = TunnelProcess(TunnelSpec(id="t1", name="Sweden"))
    monkeypatch.setattr(tunnel, "running", lambda: up)
    monkeypatch.setattr(tunnel, "_ports", ListenPorts(proxy=ground.proxy_port, status=0))
    monkeypatch.setattr(tunnel, "_read_metrics", metrics)
    return EgressRouter({"t1": tunnel}, read_default=chosen)


@pytest.fixture
def run(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> Callable[[str, EgressRouter, _Tool], Awaitable[str]]:
    """One download of a link to its end (here always a failure); the row's sentence."""

    async def one(url: str, router: EgressRouter, tool: _Tool) -> str:
        await _seed_download(temp_db, "d1", url=url)
        context = await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
        )
        with pytest.raises(JobFailedPermanently):
            await download(
                context,
                service=download_service,
                downloader=tool,
                import_file=real_import,
                router=router,
            )
        row = await download_service.get("d1")
        assert row is not None and row.error is not None
        return row.error

    return one


Run = Callable[[str, EgressRouter, _Tool], Awaitable[str]]


@pytest.mark.parametrize("hops", [0, 2])
async def test_a_tunnelled_link_is_walked_through_its_tunnel_and_never_named_here(
    hops: int, asked: list[str], run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _standing(_Site(hops)) as ground:
        await run(ground.url, _router(ground, monkeypatch), _Tool())
    assert ground.site.carried("HEAD") == hops + 1
    assert ground.site.direct("HEAD") == ground.site.direct("GET") == 0
    assert asked == []


@pytest.mark.parametrize("hops", [0, 2])
async def test_a_link_whose_tunnel_is_down_sends_nothing_at_all(
    hops: int, asked: list[str], run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _Tool()
    async with _standing(_Site(hops)) as ground:
        said = await run(ground.url, _router(ground, monkeypatch, up=False), tool)
    assert ground.site.seen == [] and asked == [] and not tool.ran
    assert "Sweden" in said


async def test_a_direct_link_is_walked_from_here_and_never_by_the_system_proxy(
    asked: list[str], run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _standing(_Site(2)) as ground:
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{ground.proxy_port}")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        await run(ground.url, _router(ground, monkeypatch, route=DIRECT), _Tool())
    assert ground.site.direct("HEAD") == 3
    assert ground.site.carried("HEAD") == 0
    assert asked, "a direct link is checked where it resolves, on this machine"


async def test_a_tunnelled_redirect_to_a_private_address_is_refused_before_the_tool(
    asked: list[str], run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _Tool()
    async with _standing(_Site(1, last="http://10.0.0.9/panel")) as ground:
        said = await run(ground.url, _router(ground, monkeypatch), tool)
    assert ground.site.carried("HEAD") == 2 and ground.site.direct("HEAD") == 0
    assert not tool.ran
    assert "private or local" in said


async def test_a_private_link_is_refused_before_its_route_is_taken(
    asked: list[str], run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused by its address alone: a tunnel that is down never gets to say so."""
    tool = _Tool()
    async with _standing(_Site(0)) as ground:
        said = await run("http://10.0.0.42/panel", _router(ground, monkeypatch, up=False), tool)
    assert "private or local" in said
    assert ground.site.seen == [] and not tool.ran


# --- every other request for the link: no name of a tunnelled Site is asked here ---------------


def _on(ground: _Ground, path: str = "/x", *, name: str = _SITE_NAME) -> str:
    return f"http://{name}:{ground.site_port}{path}"


def _proxy(ground: _Ground) -> str:
    return f"http://127.0.0.1:{ground.proxy_port}"


async def test_a_page_or_api_read_through_a_tunnel_names_nothing_here(asked: list[str]) -> None:
    """The resolvers, the re-resolve of a stale address, and the creator and music reads."""
    async with _standing(_Site(0)) as ground:
        got = await curl.guarded_get(_on(ground), proxy=_proxy(ground))
        posted = await curl.guarded_post(_on(ground, "/api"), proxy=_proxy(ground))
    assert got.status_code == posted.status_code == 200
    assert ground.site.carried("GET") == ground.site.carried("POST") == 1
    assert asked == []


async def test_a_direct_page_read_is_still_resolved_and_held_to_the_rule(
    asked: list[str],
) -> None:
    async with _standing(_Site(0)) as ground:
        with pytest.raises(UrlRejected):
            await curl.guarded_get(_on(ground, name=_PRIVATE_NAME))
        with pytest.raises(UrlRejected):
            await curl.guarded_post(_on(ground, name=_PRIVATE_NAME))
    assert asked == [_PRIVATE_NAME, _PRIVATE_NAME]
    assert ground.site.seen == []


async def test_a_media_address_fetched_through_a_tunnel_names_nothing_here(
    asked: list[str], tmp_path: Path
) -> None:
    async with _standing(_Site(1)) as ground:
        item = ResolvedItem(index=0, url=_on(ground, "/start"), media_type="video", ext="mp4")
        async with net.guarded_session(proxy=_proxy(ground)) as session:
            await Downloader()._fetch_fresh(session, item, tmp_path, proxy=_proxy(ground))
    assert ground.site.carried("GET") == 2 and ground.site.direct("GET") == 0
    assert asked == []


async def test_a_direct_media_address_is_still_resolved_and_pinned(
    asked: list[str], tmp_path: Path
) -> None:
    async with _standing(_Site(0, last=_on(_Ground(_Site(0), 1, 1), name=_PRIVATE_NAME))) as ground:
        item = ResolvedItem(index=0, url=_on(ground, "/start"), media_type="video", ext="mp4")
        async with net.guarded_session() as session:
            with pytest.raises(UrlRejected):
                await Downloader()._fetch_fresh(session, item, tmp_path, proxy=None)
    # The address and its redirect were both checked here, and the private one refused.
    assert _SITE_NAME in asked and _PRIVATE_NAME in asked
    assert ground.site.direct("GET") == 1 and ground.site.carried("GET") == 0


async def test_a_session_read_through_a_tunnel_names_nothing_here(asked: list[str]) -> None:
    """The Site readers' guarded session and the art read, which use it."""
    async with (
        _standing(_Site(0)) as ground,
        net.guarded_session(proxy=_proxy(ground)) as session,
        session.get(_on(ground)) as answer,
    ):
        assert answer.status == 200
    assert ground.site.carried("GET") == 1 and asked == []


async def test_a_tool_chained_to_a_tunnel_names_nothing_here(asked: list[str]) -> None:
    """The tools, and the playlist read a bulk paste makes, go through the tool proxy."""
    async with _standing(_Site(0)) as ground:
        through = TOOL_PROXY.address_for(_proxy(ground))
        async with (
            aiohttp.ClientSession() as session,
            session.get(_on(ground), proxy=through) as answer,
        ):
            assert answer.status == 200
    assert ground.site.carried("GET") == 1 and asked == []


# --- one route held from the first request to the last ------------------------------------------


class _Writes(_Tool):
    """Fetches through its route, then leaves a picture behind as the tools do."""

    def __init__(self, then: Callable[[], None] = lambda: None) -> None:
        self.then = then

    async def fetch(self, url: str, *, into: Path, **kwargs: Any) -> Fetched:
        await super().fetch(url, into=into, **kwargs)
        (into / "clip.png").write_bytes(png_bytes())
        self.then()
        return Fetched(files=[into / "clip.png"])


@pytest.fixture
def landed(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> Callable[..., Awaitable[str]]:
    """One download that lands, with an art read standing in for every read after the tool."""

    async def one(url: str, router: EgressRouter, tool: _Tool, **reads: Any) -> str:
        await _seed_download(temp_db, "d1", url=url)
        context = await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
        )
        await download(
            context,
            service=download_service,
            downloader=tool,
            import_file=real_import,
            router=router,
            **reads,
        )
        row = await download_service.get("d1")
        assert row is not None
        return row.status

    return one


def _the_tunnel(router: EgressRouter) -> TunnelProcess:
    return router._tunnels["t1"]


async def test_the_reads_after_the_tool_go_out_on_the_route_still_held(
    asked: list[str],
    landed: Callable[..., Awaitable[str]],
    monkeypatch: pytest.MonkeyPatch,
    download_service: DownloadService,
) -> None:
    seen: dict[str, Any] = {}
    async with _standing(_Site(0)) as ground:
        router = _router(ground, monkeypatch)

        async def keep_art(url: str, proxy: str | None, _username: str | None) -> None:
            seen["proxy"], seen["leases"] = proxy, _the_tunnel(router)._leases
            async with aiohttp.ClientSession() as session, session.get(url, proxy=proxy):
                pass

        status = await landed(ground.url, router, _Writes(), keep_art=keep_art)
    assert status == "done"
    # Held twice while the read runs: the download's own hold, and the read's confirmation of it.
    assert seen == {"proxy": _proxy(ground), "leases": 2}
    assert _the_tunnel(router)._leases == 0
    assert ground.site.direct("GET") == 0 and ground.site.carried("GET") == 2
    assert asked == []
    row = await download_service.get("d1")
    assert row is not None and row.reads_refused is None, "every read went through"


async def test_a_tunnel_stopped_after_the_tool_sends_nothing_more_and_says_so(
    asked: list[str],
    landed: Callable[..., Awaitable[str]],
    monkeypatch: pytest.MonkeyPatch,
    download_service: DownloadService,
) -> None:
    said: list[dict[str, Any]] = []
    monkeypatch.setattr(attempt.log, "warning", lambda event, **fields: said.append(fields))
    reads: list[str | None] = []

    async def keep_art(_url: str, proxy: str | None, _username: str | None) -> None:
        reads.append(proxy)

    async with _standing(_Site(0)) as ground:
        router = _router(ground, monkeypatch)

        def stop() -> None:
            # What stopping a tunnel immediately does: no process, and no port it owns.
            monkeypatch.setattr(_the_tunnel(router), "running", lambda: False)

        status = await landed(ground.url, router, _Writes(then=stop), keep_art=keep_art)
    assert status == "done"
    assert reads == []
    assert [(m, carried) for m, _p, carried in ground.site.seen] == [("HEAD", True), ("GET", True)]
    # The creator read (this host's page might name one) and the art read, each refused by name.
    assert [one["read"] for one in said] == ["creator", "art"]
    assert all("Sweden" in one["detail"] for one in said)
    row = await download_service.get("d1")
    assert row is not None
    assert (
        row.reads_refused
        == f"Sift didn't read who posted it or the Site's pictures. {said[0]['detail']}"
    )


@pytest.mark.parametrize("stopped", [False, True])
async def test_the_music_read_is_made_on_the_held_route_or_not_at_all(
    stopped: bool,
    asked: list[str],
    landed: Callable[..., Awaitable[str]],
    monkeypatch: pytest.MonkeyPatch,
    download_service: DownloadService,
) -> None:
    """The one Site that records music; its page is asked on the route the row names, or never."""
    reads: list[tuple[str | None, int]] = []
    async with _standing(_Site(0)) as ground:
        router = _router(ground, monkeypatch)

        async def read_music(_url: str, *, proxy: str | None = None) -> str | None:
            reads.append((proxy, _the_tunnel(router)._leases))
            return None

        def stop() -> None:
            if stopped:
                monkeypatch.setattr(_the_tunnel(router), "running", lambda: False)

        link = f"http://pmvhaven.com:{ground.site_port}/video/a-title_01234567"
        status = await landed(link, router, _Writes(then=stop), read_music=read_music)
    assert status == "done"
    assert reads == ([] if stopped else [(_proxy(ground), 2)])
    assert ground.site.direct("GET") == 0 and asked == []
    row = await download_service.get("d1")
    assert row is not None
    said = row.reads_refused or ""
    assert (
        said.startswith("Sift didn't read who posted it or the music. The tunnel Sweden") is stopped
    )


async def test_a_creator_read_after_its_tunnel_stopped_is_not_made() -> None:
    async def down(_tunnel_id: str) -> None:
        raise TunnelError("The tunnel Sweden is not available.")

    asks: list[str | None] = []

    async def read_creator(_url: str, *, proxy: str | None = None) -> str | None:
        asks.append(proxy)
        return "someone"

    attribution = Attribution(site="Example", username=None, names_creators=True)
    reach = attempt.Reach(partial(EgressRouter({}, ensure_started=down).through, "t1"))
    who = await who_posted(attribution, Fetched(files=[]), "https://x.test/", reach, read_creator)
    assert who.username is None and asks == []
    assert reach.said() == "Sift didn't read who posted it. The tunnel Sweden is not available."


async def test_a_direct_read_after_the_tool_is_made_with_no_proxy() -> None:
    reach = attempt.Reach(partial(EgressRouter({}).through, None))
    assert await attempt.on_route(reach, lambda proxy: _answer(proxy), what="art") == "none"
    assert reach.said() is None


def test_the_row_names_every_read_its_tunnel_refused_in_the_tunnels_own_words() -> None:
    reach = attempt.Reach(lambda: nullcontext(None))
    reach.refused.update(creator="The tunnel Sweden is turned off.", art="Later.", music="Later.")
    assert reach.said() == (
        "Sift didn't read who posted it, the Site's pictures or the music. "
        "The tunnel Sweden is turned off."
    )


async def _answer(proxy: str | None) -> str:
    return proxy or "none"
