# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keeping this device awake while quiet-hours work runs: the idle timer only, never the display."""

from __future__ import annotations

import sys
from collections.abc import Callable

from sift.kernel.log import get_logger

log = get_logger(__name__)

ES_SYSTEM_REQUIRED = 0x00000001
ES_CONTINUOUS = 0x80000000

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
