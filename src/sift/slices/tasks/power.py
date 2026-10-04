# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keeping this device awake while quiet-hours work is running, and only then.

Windows does not count a busy processor as somebody using the machine, so a PC left to do its
quiet-hours work sleeps on its timer in the middle of it: the work simply stops until morning and
nothing says why. The answer is a POWER REQUEST: `SetThreadExecutionState(ES_SYSTEM_REQUIRED |
ES_CONTINUOUS)` asks the system not to sleep on its idle timer while the request stands, and
`ES_CONTINUOUS` alone withdraws it.

Three limits, each deliberate:

* **Never the display.** `ES_DISPLAY_REQUIRED` is not asked for: the screen may turn off, and a
  locked session is fine: Sift keeps running through both.
* **Never against a person.** A system-required request only stops the IDLE timer. Closing a lid,
  pressing Sleep or choosing it from the Start menu still sleeps the device, and nothing here can or
  should stand in the way of that.
* **Held only while there is quiet-hours work.** Taken when the range is open and work held to it is
  waiting or running, withdrawn the moment either stops, so an idle device sleeps as it always did.

The request belongs to the THREAD that made it, so it is taken and withdrawn on the same one: the
event loop's, where the scheduler that decides it runs. The call is a single system call with no
waiting in it. Anywhere but Windows there is no such call and this does nothing: a Linux or container
install is not a desktop that sleeps on a timer.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from sift.kernel.log import get_logger

log = get_logger(__name__)

#: The system is required: do not sleep on the idle timer.
ES_SYSTEM_REQUIRED = 0x00000001
#: The state stands until it is changed, rather than resetting the idle timer once.
ES_CONTINUOUS = 0x80000000

#: Sets the thread's execution state and returns the previous one, or 0 on failure.
SetState = Callable[[int], int]


def _windows_call() -> SetState | None:
    """The real call, or None off Windows."""
    if sys.platform != "win32":
        return None
    import ctypes

    # Reached by name: `WinDLL` exists only on Windows, and the type checker runs on both.
    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
    function = kernel32.SetThreadExecutionState
    function.argtypes = [ctypes.c_uint32]
    function.restype = ctypes.c_uint32

    def call(flags: int) -> int:
        return int(function(flags))

    return call


class KeepAwake:
    """One power request, taken and withdrawn. Idempotent both ways."""

    def __init__(self, call: SetState | None = None) -> None:
        """`call` is the system call, handed in by a test; left out, the real one on Windows."""
        self._call = call if call is not None else _windows_call()
        self._held = False

    @property
    def held(self) -> bool:
        """Whether the request stands right now."""
        return self._held

    def hold(self, *, why: str) -> None:
        """Take the request, if it is not already held. Logged with the reason it was taken."""
        if self._held or self._call is None:
            return
        if self._call(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) == 0:
            log.warning("power.keep_awake_refused")
            return
        self._held = True
        log.info("power.keep_awake_taken", why=why)

    def release(self, *, why: str) -> None:
        """Withdraw the request, if it is held. Logged with the reason it was withdrawn."""
        if not self._held or self._call is None:
            return
        self._call(ES_CONTINUOUS)
        self._held = False
        log.info("power.keep_awake_released", why=why)
