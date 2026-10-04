# SPDX-License-Identifier: AGPL-3.0-or-later
"""Swap: two installs of Sift trade what each chose to offer, directly, through their own tunnels.

Nothing is relayed and nothing goes through a server of anybody's: the host's tunnel forwards one
port to a listener on this device, the guest dials it through its own tunnel, and a connection
locked by a one-time key carries an offer, the guest's answer, and the files the guest lacks.
Neither side learns the other's home address (only a VPN's), and a drop, a crash or a lie
costs time, never data or privacy.

The parts, each in its own module:

* `device.py`: this install's Ed25519 key, sealed, and the device id made from it.
* `token.py`: what the host pastes to the guest, checked field by field.
* `lock.py`: TLS 1.3 keyed by the token's secret, with no certificate on either side.
* `session.py` (`handshake.py`, `live.py`, `sending.py`, `receiving.py`, `host.py`, `guest.py`):
  a session by its phases, from the hello to the end, and the task it runs as on both sides.
* `transfer.py`: the strip on the way out, the chunks, the receiver's staging.
* `offer.py` / `diff.py`: what the host offers and what the guest wants.
* `ingest.py`: a received file landed through the one import path.
* `schema.py` / `store.py`: the rows.
"""

from __future__ import annotations

import re

from sift.kernel.settings_registry import SettingError, register_setting
from sift.slices.swap import schema as schema  # registers the schema component
from sift.slices.swap.router import router
from sift.slices.swap.session import (
    SESSIONS,
    SWAP_SESSION,
    SwapRefused,
    SwapSessions,
    Taken,
    register_handlers,
)
from sift.slices.swap.store import SessionStore

#: The tunnel this library's side of a swap goes through when it JOINS one: a tunnel's id, or ""
#: until somebody chooses. Chosen on the swap screens, in the open, beside the join, and remembered
#: for the next one. Never the downloads' default route: a swap dialled through whatever route
#: downloads happen to use would go out through a tunnel nobody had chosen for it.
GUEST_TUNNEL_KEY = "swap.guest_tunnel"

_TUNNEL_ID = re.compile(r"\A[A-Za-z0-9_-]{0,64}\Z")


def _tunnel_id(value: object) -> str:
    """A tunnel's id as stored, or "" for none. Its shape only: whether it is still there is asked
    at the join, where a tunnel removed since is said in words."""
    if not isinstance(value, str) or not _TUNNEL_ID.match(value):
        raise SettingError("choose one of your tunnels")
    return value


register_setting(
    key=GUEST_TUNNEL_KEY,
    scope="app",
    default="",
    validator=_tunnel_id,
    section="Sites and Tunnels",
    label="Tunnel for joining a swap",
    help="The tunnel your side of a swap goes through when you join one.",
)

__all__ = [
    "GUEST_TUNNEL_KEY",
    "SESSIONS",
    "SWAP_SESSION",
    "SessionStore",
    "SwapRefused",
    "SwapSessions",
    "Taken",
    "register_handlers",
    "router",
]
