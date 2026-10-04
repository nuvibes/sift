# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bringing a Stash library into Sift: its People, Sites and Tags, and what it knew about the files
this library holds. See `service` for the order and the rules, and `reader` for what is read."""

from __future__ import annotations

from sift.slices.stash_migration import schema as schema  # registers the schema component
from sift.slices.stash_migration.router import router
from sift.slices.stash_migration.service import (
    SERVICE,
    STASH_ARRIVED,
    STASH_IMPORT,
    StashMigration,
    StashRefused,
    register_stash_handlers,
)

__all__ = [
    "SERVICE",
    "STASH_ARRIVED",
    "STASH_IMPORT",
    "StashMigration",
    "StashRefused",
    "register_stash_handlers",
    "router",
]
