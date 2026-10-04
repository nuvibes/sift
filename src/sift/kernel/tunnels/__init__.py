# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tunnels: named WireGuard ways out, the routes sites take through them, and hosting a swap on one.

In the kernel because more than one feature runs through the same client. Downloads, stash-boxes
and song lookups go OUT through a tunnel (`EgressRouter.through`); a swap is HOSTED on one
(`TunnelStore.host_on`). One VPN key cannot run twice, so both ride the one client process, and the
thing that owns that process belongs to no single feature.

- `process`: one running client, its configuration checks, and the listener a hosting adds.
- `client`: whether the program is where the pack put it, and is the file it shipped.
- `store`: the tunnels table, the routes, and the processes behind both; hosting.
- `egress`: which way out a request takes, and the refusal that keeps that promise.
- `natpmp`: asking the provider for a public port through the tunnel, and keeping it.
- `schema`: the two tables, as the kernel component `tunnels`.
"""

from __future__ import annotations

from sift.kernel.tunnels import schema
from sift.kernel.tunnels.client import CLIENT_CHANGED, CLIENT_REMOVED, client_fault
from sift.kernel.tunnels.egress import (
    DIRECT,
    DIRECT_LABEL,
    EGRESS,
    DefaultRouteReader,
    EgressRouter,
    Route,
    SiteOf,
    SiteRouteReader,
    TunnelStarter,
)
from sift.kernel.tunnels.natpmp import Mapped, NatPmp, NatPmpError, Renewal
from sift.kernel.tunnels.process import (
    Listener,
    ListenPorts,
    TunnelClientLost,
    TunnelConfigInvalid,
    TunnelError,
    TunnelHealth,
    TunnelProcess,
    TunnelSpec,
    validate_config,
)
from sift.kernel.tunnels.store import (
    CANNOT_HOST,
    DEFAULT_SCOPE,
    END_THE_SWAP_FIRST,
    TUNNELS,
    Hosting,
    HostingLost,
    HostingMoved,
    TunnelHosting,
    TunnelStore,
    TunnelView,
)

__all__ = [
    "CANNOT_HOST",
    "CLIENT_CHANGED",
    "CLIENT_REMOVED",
    "DEFAULT_SCOPE",
    "DIRECT",
    "DIRECT_LABEL",
    "EGRESS",
    "END_THE_SWAP_FIRST",
    "TUNNELS",
    "DefaultRouteReader",
    "EgressRouter",
    "Hosting",
    "HostingLost",
    "HostingMoved",
    "ListenPorts",
    "Listener",
    "Mapped",
    "NatPmp",
    "NatPmpError",
    "Renewal",
    "Route",
    "SiteOf",
    "SiteRouteReader",
    "TunnelClientLost",
    "TunnelConfigInvalid",
    "TunnelError",
    "TunnelHealth",
    "TunnelHosting",
    "TunnelProcess",
    "TunnelSpec",
    "TunnelStarter",
    "TunnelStore",
    "TunnelView",
    "client_fault",
    "schema",
    "validate_config",
]
