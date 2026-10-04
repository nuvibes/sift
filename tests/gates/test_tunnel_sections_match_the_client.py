# SPDX-License-Identifier: AGPL-3.0-or-later
"""The refusal list and the tunnel client are talking about the same sections.

`kernel/tunnels/process._LISTENER_SECTIONS` lists the configuration sections Sift refuses in a
pasted WireGuard file because each makes the client open a port; a section the client parses and
the list misses goes straight through. So the client's own sections are read out of the shipped
binary (Go writes each parser's qualified name into it, `wireproxy.parseUDPProxyTunnelConfig`),
every one is accounted for, every refused name is one the client has, and the refusal fires on a
whole configuration.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.tunnels.process import (
    _LISTENER_SECTIONS,
    TunnelConfigInvalid,
    _check_no_listeners,
)

pytestmark = [pytest.mark.gate, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]

#: The client Sift ships beside itself, placed by `scripts/vendor_manifest.json`.
CLIENT = ROOT / "vendor" / "bin" / "wireproxy.exe"

#: The three sections that describe the WireGuard link itself and open nothing.
NOT_A_LISTENER: dict[str, str] = {
    "interface": "this end of the link: its address, its private key, its DNS. No socket.",
    # `ParsePeers` reads `[Peer]`, once per peer.
    "peers": "the far end: its key, its endpoint, its allowed IPs. It dials out and never listens.",
    "resolve": "which address family to prefer when a name is looked up. A strategy, not a socket.",
}

#: Names that come back from the walk and are not sections at all.
NOT_A_SECTION: dict[str, str] = {
    "config": "`ParseConfig` reads the whole file and hands the sections to the parsers below it.",
    "routines": "`parseRoutinesConfig` is the switch over the tunnel sections, not one of them.",
}

#: A configuration with every part the client needs; the key is not a real one, and
#: `_check_no_listeners` runs before the key check.
WHOLE = (
    "[Interface]\n"
    "PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
    "Address = 10.2.0.2/32\n"
    "\n"
    "[Peer]\n"
    "PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=\n"
    "Endpoint = 198.51.100.10:51820\n"
    "AllowedIPs = 0.0.0.0/0\n"
)


def _sections_the_client_parses() -> set[str]:
    """Every section name lifted out of the vendored client's parser symbols, lower case."""
    binary = CLIENT.read_bytes()
    found = {one.decode().lower() for one in re.findall(rb"wireproxy\.parse(\w+)Config\b", binary)}
    found |= {one.decode().lower() for one in re.findall(rb"wireproxy\.Parse(\w+)\b", binary)}
    return found


def _client_or_skip() -> set[str]:
    if not CLIENT.is_file():  # pragma: no cover (the vendored client is committed)
        pytest.skip(f"no vendored tunnel client at {CLIENT}; nothing to compare the list against")
    return _sections_the_client_parses()


def test_the_walk_finds_the_clients_parsers() -> None:
    """A walk finding nothing would pass every rule below."""
    found = _client_or_skip()

    assert "socks5" in found and "udpproxytunnel" in found, (
        "the parser symbols were not found in the vendored client. The walk is broken, not the "
        f"client. What it did find: {sorted(found)}"
    )
    assert len(found) >= 8, f"only {len(found)} parsers found; the walk is broken: {sorted(found)}"


def test_every_section_the_client_parses_is_accounted_for() -> None:
    """A section in neither list reaches the client unread."""
    found = _client_or_skip()
    known = (
        {one.strip("[]") for one in _LISTENER_SECTIONS} | set(NOT_A_LISTENER) | set(NOT_A_SECTION)
    )
    stranger = sorted(found - known)

    assert not stranger, (
        "\nThe vendored tunnel client understands sections this build has never heard of.\n"
        "A pasted configuration carrying one goes to the client unread. Decide for each whether\n"
        "it opens a port: a listener goes in `_LISTENER_SECTIONS`, anything else in\n"
        "NOT_A_LISTENER here with the line that says why.\n\n  " + "\n  ".join(stranger) + "\n"
    )


def test_every_refused_section_is_one_the_client_has() -> None:
    """Refusing a section nothing can produce is not protection."""
    found = _client_or_skip()
    invented = sorted(one for one in _LISTENER_SECTIONS if one.strip("[]") not in found)

    assert not invented, (
        "\nThese are refused and the vendored client has no parser for them, so nothing a\n"
        "provider can write would ever be turned away by these lines, and the list reads as\n"
        "complete while real sections are missing from it.\n\n  " + "\n  ".join(invented) + "\n"
    )


def test_every_reason_is_a_reason() -> None:
    """An empty excuse is not one, and neither is a name repeated back."""
    for name, reason in {**NOT_A_LISTENER, **NOT_A_SECTION}.items():
        assert len(reason.split()) >= 5, f"{name} is declared harmless without a reason"


@pytest.mark.parametrize(
    ("section", "body"),
    [
        ("[UDPProxyTunnel]", "BindAddress = 0.0.0.0:53\nTarget = 192.0.2.1:53\n"),
        ("[STDIOTunnel]", "Target = 192.0.2.1:22\n"),
        ("[SNI]", "BindAddress = 0.0.0.0:443\n"),
    ],
)
def test_the_listener_sections_are_refused(section: str, body: str) -> None:
    """The listener sections are refused through the real check:
    `[UDPProxyTunnel]` on `0.0.0.0:53` would forward LAN traffic out of the tunnel's account."""
    carrying = f"{WHOLE}\n{section}\n{body}"

    with pytest.raises(TunnelConfigInvalid, match="does more than connect"):
        _check_no_listeners(carrying.lower(), carrying)


def test_a_provider_file_with_no_extra_section_is_left_alone() -> None:
    """The file people actually paste passes."""
    _check_no_listeners(WHOLE.lower(), WHOLE)
