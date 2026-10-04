# SPDX-License-Identifier: AGPL-3.0-or-later
"""The HTTPS decision that the cookie Secure flag and HSTS both key off."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from sift.kernel.http import (
    chunk_bytes,
    is_https,
    is_local_request,
    origin_is_allowed,
    reached_over_the_local_network,
    read_capped,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.mark.parametrize(
    ("scheme", "forwarded", "expected"),
    [
        ("https", "", True),  # direct TLS, LAN
        ("http", "", False),  # direct plain HTTP, LAN
        ("http", "https", True),  # behind a TLS-terminating proxy
        ("http", "http", False),  # proxy that did not terminate TLS
        ("http", "https,http", True),  # first hop is the browser; later hops are proxies
        ("http", " HTTPS ", True),  # tolerant of spacing and case
        ("http", "http,https", False),  # a later hop claiming https does not count
    ],
)
def test_is_https(scheme: str, forwarded: str, expected: bool) -> None:
    assert is_https(scheme, forwarded) is expected


class _Body:
    """A response body handed over one chunk at a time through `iter_chunked`, the real client's
    shape: `read(n)` does NOT answer with n bytes."""

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks
        self.taken = 0

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.taken += 1
            yield chunk


async def test_a_body_longer_than_one_read_comes_back_whole() -> None:
    """A body longer than one read comes back whole: a truncated picture is still a file, and a
    browser draws as much as it was given."""
    body = _Body(b"first", b"second", b"third")

    assert await read_capped(body, 100) == b"firstsecondthird"


async def test_reading_stops_at_the_cap_rather_than_at_whatever_arrives() -> None:
    """Reading stops at the cap: the body comes from a machine Sift does not control."""
    body = _Body(b"a" * 10, b"b" * 10, b"c" * 10)

    assert await read_capped(body, 15) == b"a" * 10 + b"b" * 5


async def test_reading_stops_taking_chunks_once_the_cap_is_reached() -> None:
    # Not only trimmed after: a body that never ends would otherwise be read in full.
    body = _Body(b"a" * 10, b"b" * 10, b"c" * 10)

    await read_capped(body, 15)

    assert body.taken == 2


async def test_a_body_that_ends_early_is_what_there_was() -> None:
    body = _Body(b"short")

    assert await read_capped(body, 1000) == b"short"


async def test_a_body_with_nothing_in_it_is_nothing_rather_than_an_error() -> None:
    assert await read_capped(_Body(), 1000) == b""


async def test_exactly_the_cap_and_one_over_it_are_told_apart() -> None:
    """Exactly the cap and one over are told apart: a caller asks for `cap + 1`."""
    at_the_cap = await read_capped(_Body(b"a" * 10), 11)
    over_it = await read_capped(_Body(b"a" * 11), 11)

    assert len(at_the_cap) == 10
    assert len(over_it) == 11


def test_how_much_one_read_takes_is_readable_without_reaching_for_a_private_name() -> None:
    """`chunk_bytes` is public, so a test can build a body more than one read long."""
    assert chunk_bytes() > 0


# --- whether a filesystem path is worth answering with
#
# A path is no secret (an admin sees them in the folder picker), but meaningless to another
# machine, where it would be a drag of a file that is not there.


@pytest.mark.parametrize(
    ("client_host", "forwarded_for", "expected"),
    [
        pytest.param("127.0.0.1", "", True, id="loopback_v4"),
        pytest.param("::1", "", True, id="loopback_v6"),
        pytest.param("localhost", "", True, id="by_name"),
        pytest.param("192.168.1.41", "", False, id="another_machine"),
        pytest.param(None, "", False, id="no_client_at_all"),
        # Behind a proxy the loopback address is the proxy's own.
        pytest.param("127.0.0.1", "203.0.113.7", False, id="loopback_behind_a_proxy"),
        pytest.param("127.0.0.1", "   ", True, id="an_empty_header_is_no_proxy"),
    ],
)
def test_whether_a_caller_is_on_this_machine(
    client_host: str | None, forwarded_for: str, expected: bool
) -> None:
    assert is_local_request(client_host, forwarded_for) is expected


@pytest.mark.parametrize(
    ("client_host", "headers", "expected"),
    [
        pytest.param("127.0.0.1", (), True, id="this_machine"),
        pytest.param("::1", (), True, id="this_machine_v6"),
        pytest.param("192.168.1.41", ("host", "cookie"), True, id="the_household_network"),
        pytest.param("10.0.0.8", (), True, id="a_private_range"),
        pytest.param("fe80::1", (), True, id="link_local_v6"),
        pytest.param("fd12:3456::9", (), True, id="a_private_v6_range"),
        pytest.param("::ffff:192.168.1.41", (), True, id="mapped_v4"),
        pytest.param("203.0.113.7", (), False, id="a_public_address"),
        pytest.param("100.64.3.2", (), False, id="an_overlay_vpn_address"),
        pytest.param("198.18.0.1", (), False, id="a_benchmark_range_is_not_a_home"),
        pytest.param("0.0.0.0", (), False, id="unspecified"),  # noqa: S104
        pytest.param("2606:4700::1", (), False, id="a_public_v6_address"),
        pytest.param(None, (), False, id="no_client_at_all"),
        pytest.param("testclient", (), False, id="not_an_address"),
        pytest.param("127.0.0.1", ("X-Forwarded-For",), False, id="through_a_tunnel"),
        pytest.param("192.168.1.41", ("cf-connecting-ip",), False, id="through_a_hosted_tunnel"),
        pytest.param("10.0.0.8", ("forwarded",), False, id="through_a_proxy"),
        pytest.param("10.0.0.8", ("via",), False, id="through_a_relay"),
    ],
)
def test_whether_a_caller_reached_sift_over_the_local_network(
    client_host: str | None, headers: tuple[str, ...], expected: bool
) -> None:
    assert reached_over_the_local_network(client_host, headers) is expected


# --- the socket origin rule, here because two features open sockets and neither may import the
# other


def test_the_origin_rule() -> None:
    allowed = ("https://sift.example",)

    # Same origin.
    assert origin_is_allowed("http://sift.lan:5171", "sift.lan:5171", ())
    # An origin the operator listed, from the setting the HTTP side reads.
    assert origin_is_allowed("https://sift.example", "sift.lan", allowed)
    # Anything else, with a session riding on it, is the attack.
    assert not origin_is_allowed("https://not-sift.example", "sift.lan", allowed)
    # A name that merely starts the same is a different site.
    assert not origin_is_allowed("https://sift.lan.not-sift.example", "sift.lan", ())
    # A scheme change is a different origin: the downgrade.
    assert not origin_is_allowed("http://sift.example", "sift.lan", allowed)


def test_a_caller_with_no_origin_is_not_the_threat() -> None:
    """A caller with no Origin is a script or a tool, with nothing of yours to be tricked into
    sending; browsers always send one."""
    assert origin_is_allowed(None, "sift.lan", ())
