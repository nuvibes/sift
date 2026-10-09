# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the end of an asset takes with it in stores no foreign key reaches; a gate checks it."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from sift.kernel.db import Database
from sift.kernel.log import get_logger

log = get_logger(__name__)


class Forgetting(Protocol):
    """One store that holds asset ids the database cannot cascade, and how to clear them."""

    @property
    def name(self) -> str: ...

    async def forget(self, asset_ids: Sequence[str]) -> int:
        """Drop everything held about these assets; returns a count for the log."""
        ...


#: Registration happens at import, before a database exists.
Builder = Callable[[Database], Forgetting]


@dataclass(frozen=True, slots=True)
class Registered:
    #: Stated so the gate can check every uncascaded asset-id column against them.
    tables: tuple[str, ...]
    build: Builder


_REGISTERED: dict[str, Registered] = {}


def register_forgetting(name: str, tables: Sequence[str], builder: Builder) -> None:
    """Claim a name and its tables; a repeated name or a table claimed twice is a bug."""
    if name in _REGISTERED:
        raise ValueError(f"a forgetting named {name!r} is already registered")
    if not tables:
        raise ValueError(f"the forgetting named {name!r} names no table, so nothing checks it")
    for table in tables:
        owner = declared_tables().get(table)
        if owner is not None:
            raise ValueError(
                f"{table!r} is already cleared by {owner!r}, so {name!r} cannot own it"
            )
    _REGISTERED[name] = Registered(tables=tuple(tables), build=builder)


def registered_forgettings() -> dict[str, Registered]:
    return dict(_REGISTERED)


def declared_tables() -> dict[str, str]:
    """Every table somebody has taken responsibility for, and which store did."""
    return {table: name for name, entry in _REGISTERED.items() for table in entry.tables}


async def forget_everywhere(database: Database, asset_ids: Sequence[str]) -> dict[str, int]:
    """Clear these assets from every declared store, after the removing write commits."""
    if not asset_ids:
        return {}
    cleared: dict[str, int] = {}
    for name, entry in _REGISTERED.items():
        try:
            cleared[name] = await entry.build(database).forget(asset_ids)
        except Exception:
            # The delete is durable; a loud line and the next store, never a failed delete.
            log.exception("forgetting.failed", store=name, assets=len(asset_ids))
    return cleared
