# SPDX-License-Identifier: AGPL-3.0-or-later
"""A User's own history of use: whether Sift keeps it, and clearing it in one press."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from sift.kernel.audience import Audience
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database
from sift.kernel.log import get_logger
from sift.kernel.seams import SettingsSeam

log = get_logger(__name__)

#: Registered by Insights; read here so every writer reads it one way.
RECORD_KEY: Final = "history.keep"

Clearing = Callable[[Connection, str], Awaitable[int]]

_CLEARINGS: dict[str, Clearing] = {}


async def keeps_history(preferences: SettingsSeam, user_id: str) -> bool:
    """Whether Sift keeps this User's history of use now. Read at each write, never held."""
    return bool(await preferences.get_user(user_id, RECORD_KEY))


def register_clearing(name: str, clearing: Clearing) -> None:
    """Say how this feature clears its part of a User's history. Once per name, at import."""
    if name in _CLEARINGS:
        raise ValueError(f"the history clearing {name!r} is registered twice")
    _CLEARINGS[name] = clearing


def registered_clearings() -> dict[str, Clearing]:
    """The registry, copied. Tests read it; nothing mutates it through here."""
    return dict(_CLEARINGS)


async def clear_history_of(database: Database, user_id: str) -> dict[str, int]:
    """Clear every part of this User's history in one write, and tell their screens."""
    cleared: dict[str, int] = {}
    async with telling(database, Audience.of_user(user_id), About.MINE) as connection:
        for name, clearing in sorted(_CLEARINGS.items()):
            cleared[name] = await clearing(connection, user_id)
    log.info("history.cleared", user_id=user_id, **cleared)
    return cleared
