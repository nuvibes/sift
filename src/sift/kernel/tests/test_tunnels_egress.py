# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which way out a request takes, and the refusal that keeps that promise.

The router is the kernel's; which Site a URL belongs to is the download slice's catalog, handed in
as `site_of`. Here a stand-in answers it (a URL on `reddit.com` is the `reddit` Site and nothing
else is any Site), so what is under test is the router's own rule: a Site's route wins over the
default, a route naming a tunnel that is gone or down refuses, and nothing is ever sent directly
because a tunnel was missing. `slices/download/tests/test_egress.py` proves the real catalog is
what the application hands it.

The refusal is tested harder than the routing, because the routing failing is visible and the
refusal failing is not.
"""

from __future__ import annotations

import pytest

from sift.kernel.tunnels import (
    DIRECT,
    DIRECT_LABEL,
    EgressRouter,
    ListenPorts,
    TunnelError,
    TunnelProcess,
    TunnelSpec,
)

_PROXY = "http://127.0.0.1:45000"
_URL = "https://www.reddit.com/r/x/comments/1/t/"


def _site_of(url: str) -> str | None:
    """The stand-in for the download slice's catalog."""
    return "reddit" if "reddit.com" in url else None


# --- choosing the route ---------------------------------------------------------------------------


async def test_with_nothing_configured_everything_goes_out_directly() -> None:
    router = EgressRouter({})
    assert await router.route_of(_URL) == DIRECT
    async with router.route_for(_URL) as proxy:
        assert proxy is None


async def test_a_site_follows_the_global_default_until_it_has_one_of_its_own() -> None:
    async def default() -> str:
        return "t1"

    async def site(key: str) -> str | None:
        return "t2" if key == "reddit" else None

    router = EgressRouter({}, read_default=default, read_site=site, site_of=_site_of)
    assert await router.route_of("https://youtu.be/abc") == "t1"
    assert await router.route_of(_URL) == "t2"


async def test_a_known_site_with_no_route_reader_follows_the_global_default() -> None:
    """A catalog that knows the Site but no per-Site routes handed in: no Site has a route of its
    own, so a known Site goes the default's way like any other address."""

    async def default() -> str:
        return "t1"

    router = EgressRouter({}, read_default=default, site_of=_site_of)
    assert await router.route_of(_URL) == "t1"


async def test_the_route_is_read_per_download_not_captured_once() -> None:
    """Changing a setting has to take effect on the next download, with nothing restarted."""
    routes = iter(["t1", DIRECT])

    async def default() -> str:
        return next(routes)

    router = EgressRouter({}, read_default=default)
    assert await router.route_of(_URL) == "t1"
    assert await router.route_of(_URL) == DIRECT


async def test_an_unknown_host_follows_the_global_default() -> None:
    async def default() -> str:
        return DIRECT

    router = EgressRouter({}, read_default=default)
    assert await router.route_of("https://example-host.test/x") == DIRECT


# --- failing closed ---------------------------------------------------------------------------------


async def test_a_site_routed_to_a_tunnel_that_is_gone_fails_rather_than_going_direct() -> None:
    async def default() -> str:
        return "vanished"

    router = EgressRouter({}, read_default=default)
    with pytest.raises(TunnelError):
        async with router.route_for(_URL):
            pytest.fail("the download went ahead without the tunnel it was told to use")


async def test_a_site_routed_to_a_tunnel_that_is_down_fails_and_names_it() -> None:
    """The worst outcome this feature can have is going out of the machine's own address on the one
    site somebody said not to. Nothing about that is visible afterwards, which is why it is refused
    here rather than recovered from."""
    tunnel = TunnelProcess(TunnelSpec(id="t1", name="Sweden"))  # never started

    async def default() -> str:
        return "t1"

    router = EgressRouter({"t1": tunnel}, read_default=default)
    with pytest.raises(TunnelError, match="Sweden"):
        async with router.route_for(_URL):
            pytest.fail("the download went ahead over a tunnel that is not up")


async def test_a_live_tunnel_hands_the_download_its_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    tunnel = TunnelProcess(TunnelSpec(id="t1", name="Sweden"))
    monkeypatch.setattr(tunnel, "running", lambda: True)
    monkeypatch.setattr(tunnel, "_ports", ListenPorts(proxy=45000, status=45001))

    async def default() -> str:
        return "t1"

    router = EgressRouter({"t1": tunnel}, read_default=default)
    async with router.route_for(_URL) as proxy:
        assert proxy == _PROXY


async def test_a_named_route_resolves_the_same_way_a_sites_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`through` is the half a stash-box uses: it stores a tunnel id on its own row rather than
    following a site. The same id gives the same proxy, direct gives none, and a gone tunnel
    refuses rather than going out of the machine's own address."""
    tunnel = TunnelProcess(TunnelSpec(id="t1", name="Sweden"))
    monkeypatch.setattr(tunnel, "running", lambda: True)
    monkeypatch.setattr(tunnel, "_ports", ListenPorts(proxy=45000, status=45001))
    router = EgressRouter({"t1": tunnel})

    async with router.through("t1") as proxy:
        assert proxy == _PROXY
    async with router.through(None) as proxy:
        assert proxy is None
    async with router.through(DIRECT) as proxy:
        assert proxy is None
    with pytest.raises(TunnelError):
        async with router.through("vanished"):
            pytest.fail("a gone tunnel let the question out directly")


# --- what a download records about the route it took ---------------------------------------------


async def test_the_route_a_download_records_is_the_one_it_holds_read_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The label, the tunnel and its server come from the tunnel actually held.

    Read twice (once for the proxy and again for the name), a setting changed in between would
    make a row name a way out its download never took. The default below
    answers the tunnel first and Direct on every later read, so a second read would be caught.
    """
    routes = iter(["t1", DIRECT, DIRECT])

    async def default() -> str:
        return next(routes)

    async def metrics() -> tuple[int | None, str | None]:
        return 1, "192.0.2.7"

    tunnel = TunnelProcess(TunnelSpec(id="t1", name="Sweden"))
    monkeypatch.setattr(tunnel, "running", lambda: True)
    monkeypatch.setattr(tunnel, "_ports", ListenPorts(proxy=45000, status=45001))
    monkeypatch.setattr(tunnel, "_read_metrics", metrics)
    router = EgressRouter({"t1": tunnel}, read_default=default)

    async with router.take(_URL) as taken:
        assert taken.proxy == _PROXY
        assert (taken.label, taken.tunnel_id, taken.address) == ("Sweden", "t1", "192.0.2.7")


async def test_a_tunnel_that_has_not_said_which_server_records_no_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No address rather than a guessed one: the row then shows the name alone."""

    async def silent() -> tuple[int | None, str | None]:
        return None, None

    async def default() -> str:
        return "t1"

    tunnel = TunnelProcess(TunnelSpec(id="t1", name="Sweden"))
    monkeypatch.setattr(tunnel, "running", lambda: True)
    monkeypatch.setattr(tunnel, "_ports", ListenPorts(proxy=45000, status=45001))
    monkeypatch.setattr(tunnel, "_read_metrics", silent)

    async with EgressRouter({"t1": tunnel}, read_default=default).take(_URL) as taken:
        assert (taken.label, taken.address) == ("Sweden", None)


async def test_a_router_told_no_site_follows_the_default_for_every_url() -> None:
    """Built without `site_of` (a bare build, a test), a Site's own route is never read, which is
    the answer a router built without a site reader has always given: the default decides."""

    async def site(_key: str) -> str | None:
        raise AssertionError("no Site was named, so no Site's route is read")

    router = EgressRouter({}, read_site=site)
    assert await router.route_of(_URL) == DIRECT


async def test_a_direct_route_is_labelled_direct_and_names_no_tunnel() -> None:
    async with EgressRouter({}).take(_URL) as taken:
        assert (taken.proxy, taken.label, taken.tunnel_id, taken.address) == (
            None,
            DIRECT_LABEL,
            None,
            None,
        )


async def test_a_route_brings_its_tunnel_up_before_taking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restart leaves every tunnel down until somebody asks; the first request that wants one is
    that ask, and the tunnel is looked up only after it has had the chance to come up."""
    tunnel = TunnelProcess(TunnelSpec(id="t1", name="Sweden"))
    tunnels: dict[str, TunnelProcess] = {}
    started: list[str] = []

    async def ensure_started(tunnel_id: str) -> None:
        started.append(tunnel_id)
        monkeypatch.setattr(tunnel, "running", lambda: True)
        monkeypatch.setattr(tunnel, "_ports", ListenPorts(proxy=45000, status=45001))
        tunnels[tunnel_id] = tunnel

    router = EgressRouter(tunnels, ensure_started=ensure_started)
    async with router.through("t1") as proxy:
        assert proxy == _PROXY
    assert started == ["t1"]
