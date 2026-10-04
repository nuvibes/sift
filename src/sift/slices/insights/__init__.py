# SPDX-License-Identifier: AGPL-3.0-or-later
"""Insights: a User's library, viewing and organizing, in numbers, kept on this device.

The engine is three pieces. `schema` holds the rows: each finished day of each User added up once,
as a figure per metric and key, with the part of it that came from hidden things beside it.
`metrics` is the closed list of what is counted and the one statement that counts each. `rollup`
is the quiet adder-up (a loop of the process, not a task) that adds up the next finished day
for one User at a time, in pieces small enough that nothing a person is waiting for waits on it.
`store` is how everything else reads them, today counted live by the same statements.
"""

from __future__ import annotations

# The four route modules (the page's answer, the recaps, the learning paths of Get to know Sift,
# and the pages a client reports it showed) as MODULES: the composition root mounts each one's `router`, and their tests reach into
# them by module name.
from sift.kernel.settings_registry import register_setting
from sift.kernel.use_history import RECORD_KEY
from sift.slices.insights import (
    capture,  # noqa: F401 (imported so its part of clearing a history registers itself)
    capture_router,
    path,
    path_router,
    recaps,
    recaps_router,
    router,
    schema,  # noqa: F401 (imported so the tables register themselves)
    store,
)
from sift.slices.insights.metrics import ADMIN_ONLY, METRICS
from sift.slices.insights.rollup import keep_the_days_added_up
from sift.slices.insights.store import DayRow, Figures, RecapRow

# Whether Sift keeps this person's history of use: what they watch, the pages they open, what they
# search for. Theirs to pause, on their own Privacy pane, and read by every feature that writes a
# part of it (`kernel/use_history.py`). Registered here because Insights is what it is kept for.
register_setting(
    key=RECORD_KEY,
    scope="user",
    default=True,
    section="Privacy and Security",
    label="Keep your history",
    help=(
        "Sift notes what you watch, open and search for, so Insights can "
        "show how you use your library. It stays on the device Sift runs on and only you can see "
        "it."
    ),
    disclosure="Turn it off to pause: nothing new is noted until you turn it on again.",
)

__all__ = [
    "ADMIN_ONLY",
    "METRICS",
    "DayRow",
    "Figures",
    "RecapRow",
    "capture_router",
    "keep_the_days_added_up",
    "path",
    "path_router",
    "recaps",
    "recaps_router",
    "router",
    "store",
]
