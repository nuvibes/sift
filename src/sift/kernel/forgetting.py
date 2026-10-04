# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the end of an asset has to take with it, in the stores no foreign key reaches.

Ordinary rows pointing at `assets` go with it by cascade. A virtual table (the full-text and vector
indexes) cannot take a foreign key, nor can a map table keyed to one, and left alone they keep a
deleted file findable by name with nothing erroring. So each such store registers here at import
(the kernel may not import a slice), naming the tables it clears, and a gate refuses any asset-id
column no key reaches that nobody declared, so the next store of this shape fails the build.

It is fired from `ContentStore.remove_asset_if_unplaced` alone, the one function that decides an
asset has ended, so no third caller can forget it. It runs after the delete commits, so a store
that fails is logged and the rest still run: a delete that happened is never turned into an error,
and both stores have a catch-up sweep of their own. A store without one must say so where it
registers.

Never reached from here: the ledger, whose events keep a snapshot of their subject so the past
survives tidying, and `plays`, whose watching outlives its file and is declared in the gate's
`KEEPS_ITS_IDS` beside `file_moves`.
"""

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
        """Drop everything held about these assets. Returns how many were cleared out.

        The count is for the log, in the store's own unit: an index of many vectors per file
        reports files.
        """
        ...


#: How to build one, given the handle it needs: registration happens at import, before a database.
Builder = Callable[[Database], Forgetting]


@dataclass(frozen=True, slots=True)
class Registered:
    """One store's entry: what it clears, and how to build the thing that clears it."""

    #: The tables it clears, stated at registration so the gate can check every uncascaded asset-id
    #: column against them. Naming a table and not clearing it is caught by the tests beside this,
    #: which delete a real asset and look.
    tables: tuple[str, ...]
    build: Builder


# Registered by the stores at import; process-global like the schema and preference registries.
_REGISTERED: dict[str, Registered] = {}


def register_forgetting(name: str, tables: Sequence[str], builder: Builder) -> None:
    """Claim a name, and say which tables it answers for.

    The same name twice is a bug, not an override, and so is two stores claiming one table: neither
    could be relied on to clear it.
    """
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
    """The registry, copied. Nothing mutates it through here."""
    return dict(_REGISTERED)


def declared_tables() -> dict[str, str]:
    """Every table somebody has taken responsibility for, and which store did.

    Read by the gate, with no database: what is declared is a fact about the code.
    """
    return {table: name for name, entry in _REGISTERED.items() for table in entry.tables}


async def forget_everywhere(database: Database, asset_ids: Sequence[str]) -> dict[str, int]:
    """Clear these assets out of every store that declared itself. Returns what each cleared.

    Called after the removing transaction commits: the sweeps drop what no longer has an asset, so
    earlier finds nothing, and inside the write would nest a `write()`, which the guard refuses.
    """
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
