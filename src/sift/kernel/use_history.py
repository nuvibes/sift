# SPDX-License-Identifier: AGPL-3.0-or-later
"""A User's own history of use: whether Sift keeps it, and clearing it in one press.

What somebody watched, which pages they opened, how long they stayed and what they searched for is
kept so Insights can add it up, and it is theirs: on this computer, read back only to them, gone
when they are. Two answers belong to them as well, and both live here because several features
write that history and none of them may import another:

- **A pause.** One switch per User (`RECORD_KEY`, `Settings > Privacy > Your history`). While it is
  off, a feature that writes a sitting, a page visit or a search's record writes nothing for them.
  Each writer asks `keeps_history` at the moment it would write; nothing is held, so turning it off
  takes effect on the next request.
- **A clear.** Each feature that keeps part of the history says how to clear its part for one User
  (`register_clearing`), and `clear_history_of` runs every one of them in ONE write, so a press
  clears all of it or none of it. The figures Insights has already added up from what was cleared
  are cleared with it and added up again from what is left.

Removing a User removes all of it without this: every table that holds a part names its User with
a key that cascades.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from sift.kernel.audience import Audience
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database
from sift.kernel.log import get_logger
from sift.kernel.seams import SettingsSeam

log = get_logger(__name__)

#: The per-User switch: True (the default) keeps the history, False pauses it. Registered by
#: Insights, which is what the history is kept for; read here so every writer reads it one way.
RECORD_KEY: Final = "history.keep"

#: How a feature clears its part of one User's history: on the write it is handed, answering how
#: many rows went.
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
    """Clear every part of this User's history, in one write, and tell their screens.

    Answers how many rows each part lost. The log line carries those counts and nothing of what
    was in them.
    """
    cleared: dict[str, int] = {}
    async with telling(database, Audience.of_user(user_id), About.MINE) as connection:
        for name, clearing in sorted(_CLEARINGS.items()):
            cleared[name] = await clearing(connection, user_id)
    log.info("history.cleared", user_id=user_id, **cleared)
    return cleared
