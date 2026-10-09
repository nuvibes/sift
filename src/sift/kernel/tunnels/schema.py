# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tunnels' two tables, the kernel's as two features read them.

The configuration is sealed in `secrets`; running is never stored, as a process can stop."""

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


register_schema_initializer(
    COMPONENT, VERSION, initialize, depends_on=[IDENTITY_COMPONENT], baseline=1
)


__all__ = ["COMPONENT", "VERSION", "initialize"]
