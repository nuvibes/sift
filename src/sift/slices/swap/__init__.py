# SPDX-License-Identifier: AGPL-3.0-or-later
"""Swap: two installs of Sift trade what each chose to offer, directly, through their tunnels."""

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

#: The tunnel this side joins a swap through, chosen on the swap screens, never the downloads'.
GUEST_TUNNEL_KEY = "swap.guest_tunnel"

_TUNNEL_ID = re.compile(r"\A[A-Za-z0-9_-]{0,64}\Z")


def _tunnel_id(value: object) -> str:
    """A tunnel's id as stored, or "" for none; checked for shape only."""
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
    names_a_tunnel=True,
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
