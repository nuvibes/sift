# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one folder answers differently from the rest of the library: overrides only.

An absent row follows the library's setting, keyed by the same setting key.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "importing"
VERSION = 1

# Cascades from the root, so a later folder with the same id inherits no old answer.
_CREATE_ROOT_PREFS = """
CREATE TABLE IF NOT EXISTS root_import_prefs (
  root_id    TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(root_id, key)
)
"""


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_ROOT_PREFS)


register_schema_initializer(COMPONENT, VERSION, initialize, baseline=1)
