# SPDX-License-Identifier: AGPL-3.0-or-later
r"""Asking this backend to stop so that whatever started it will start it again.

Some changes (turning the graphics card on loads a second build of a loaded library) finish only in
a new process. The ask goes to the server because the backend is the process to restart, and the
person may be at a browser on another machine. Nothing here restarts anything: it arranges the clean
stop Ctrl-C performs and leaves an exit code saying the stop was asked for, which the supervising
desktop shell reads. It refuses where nothing supervises the process, or the library would go off
the air under a screen saying "restarting".
"""

from __future__ import annotations

from collections.abc import Callable

from sift.kernel.ids import new_id

#: Which RUN of the backend this is, fresh at every start: the only honest way for a screen to tell
#: the restart happened, since the server answers the ask before it goes. Served on `/health`, as
#: public as "is it up", so the desktop shell and the browser wait the same way.
BOOT_ID = new_id()

#: The exit code of a stop that was ASKED FOR: distinct from 0 (do not start me again) and from any
#: crash, so the supervisor never reads repeated restarts as a crash loop it must stop restarting.
RESTART_EXIT_CODE = 86

_stop: Callable[[], None] | None = None
_asked = False


def stops_with(stop: Callable[[], None]) -> None:
    """Record how this process is asked to shut down cleanly. Called once, by `main`."""
    global _stop
    _stop = stop


def can_restart() -> bool:
    """Whether asking would achieve anything.

    False for a backend nothing is supervising: it would stop and stay stopped. Answered before the
    ask so a screen can say why rather than taking the library off the air to find out.
    """
    return _stop is not None


def ask_to_restart() -> bool:
    """Begin a clean shutdown, to be followed by a start. False when nobody would start it again.

    Returns once the shutdown is ARRANGED: this process still answers the request that asked, and
    stops after serving what it is serving.
    """
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
    """Put this back to how it starts. For tests, which share a process."""
    global _stop, _asked
    _stop = None
    _asked = False
