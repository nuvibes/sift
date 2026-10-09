# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking this backend to stop with an exit code that tells its supervisor to start it again."""

from __future__ import annotations

from collections.abc import Callable

from sift.kernel.ids import new_id

#: Fresh each start, so a screen can tell the restart happened; public on `/health`.
BOOT_ID = new_id()

#: Distinct from 0 and from a crash, so restarts never read as a crash loop.
RESTART_EXIT_CODE = 86

_stop: Callable[[], None] | None = None
_asked = False


def stops_with(stop: Callable[[], None]) -> None:
    global _stop
    _stop = stop


def can_restart() -> bool:
    """Whether asking would achieve anything: False when nothing supervises this process."""
    return _stop is not None


def ask_to_restart() -> bool:
    """Arrange a clean shutdown to be followed by a start; False when nobody would start it."""
    global _asked
    if _stop is None:
        return False
    _asked = True
    _stop()
    return True


def was_asked_to_restart() -> bool:
    """Whether the shutdown that just happened was asked for. Read by `main` on the way out."""
    return _asked


def forget() -> None:
    global _stop, _asked
    _stop = None
    _asked = False
