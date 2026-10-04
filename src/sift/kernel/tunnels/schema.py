# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tunnels' two tables: the tunnels themselves, and the routes sites take through them.

Two features read them (a download goes out through a tunnel, and a swap is hosted on one), and a
table two features read belongs to neither of them, so they are the kernel's.

`tunnels` holds a named way out: a name, the id of its sealed configuration in `secrets`, whether it
is meant to be running, and whether it can host a swap. Nothing readable about the provider, its key
or its endpoint is stored: the configuration is a private key, sealed exactly as a site login is,
so a tunnel cannot start until somebody has signed in. Running is a fact about a process and is never
written down, because a stored one is wrong the moment the process stops.

`can_host` is NULL until somebody tries to host a swap on the tunnel, then 1 or 0 as that attempt
found: whether the provider forwards a port to this configuration is written nowhere in it, and only
asking the provider says.

`tunnel_routes` says which way out a site takes. One row per site given a route of its own, plus one
row under a reserved scope for the default every other site follows. One table, because it is the
same question asked at two levels. `route` is the word for the device's own address or a tunnel's
id, and a route naming a deleted tunnel is left alone rather than repointed at the address somebody
routed the site away from: the download refuses and says so.
"""

from __future__ import annotations

from sift.kernel.access.schema import IDENTITY_COMPONENT
from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "tunnels"
VERSION = 1

_CREATE_TUNNELS = """
CREATE TABLE IF NOT EXISTS tunnels (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  secret_id  TEXT REFERENCES secrets(id) ON DELETE SET NULL,
  enabled    INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  can_host   INTEGER
)
"""

_CREATE_ROUTES = """
CREATE TABLE IF NOT EXISTS tunnel_routes (
  scope      TEXT PRIMARY KEY,
  route      TEXT NOT NULL,
  updated_at INTEGER NOT NULL
)
"""

#: Two tunnels with one name are one row a screen cannot tell apart from the other.
_INDEXES = ("CREATE UNIQUE INDEX IF NOT EXISTS ux_tunnels_name ON tunnels(name COLLATE NOCASE)",)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (_CREATE_TUNNELS, _CREATE_ROUTES, *_INDEXES):
            await connection.execute(statement)


# `secrets` is the kernel identity component's; `tunnels.secret_id` points into it.
register_schema_initializer(
    COMPONENT, VERSION, initialize, depends_on=[IDENTITY_COMPONENT], baseline=1
)


__all__ = ["COMPONENT", "VERSION", "initialize"]
