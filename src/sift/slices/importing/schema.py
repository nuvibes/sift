# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one folder answers differently from the rest of the library.

One table, and it holds overrides and nothing else. An absent row means "whatever the app setting
says", which is what makes a folder nobody has touched follow the global answer, and go on
following it when that answer changes. Storing every folder's copy of every switch would freeze
each folder at whatever the default was on the day it was added.

**Why this is not `register_setting`.** A declared setting is per-user or per-instance and
nothing else; there is no per-folder scope and inventing one would put a row in the settings screen
for every folder times every switch. What a folder overrides is stored here, read by this feature,
and shown on the folder's own row.

The key is the ordinary setting key, so the two sides cannot drift: a folder saying no to
`performance.generate_previews` is answering the same question the settings screen asks, and there
is one name for it.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "importing"
VERSION = 1

# Cascades from the root. An override for a folder that has been removed from the library is not an
# override of anything, and leaving it would apply somebody's old answer to a folder that happened
# to be given the same id later.
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


# `library_roots` is named in a foreign key without a declared dependency on the component that
# creates it, the same way the sibling slices name `folders` and `people`: SQLite resolves a foreign
# key's parent by name when a row is written rather than when the table is made.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=1)
