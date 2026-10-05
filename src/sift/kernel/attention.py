# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether somebody is using this computer, or other programs are keeping it busy, so background
work can step back to a share of the device meanwhile.

The pool asks `Attention.workers` on its reconfigure timer. Input is read with Windows'
`GetLastInputInfo`; other programs' load is `kernel.device_load`'s. Neither can be read off Windows,
where the pool runs its full count. A press for the full amount (`Attention.press`) overrules both
causes until Sift stops or the next press; it is held in memory because it answers a moment, not a
standing choice.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import cache
from typing import Any, Literal

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.budget import STEP_BACK_SHARE, WHOLE_DEVICE, stepped_workers
from sift.kernel.changes import About, announce_now
from sift.kernel.config import get_settings
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: Why the work steps back: somebody at the keyboard, or other programs keeping the device busy.
Cause = Literal["input", "others"]

#: How recent the last key press or mouse movement must be for the computer to count as in use:
#: a minute, because a person reading a page touches nothing for tens of seconds, and handing
#: the workers back mid-read brings the lag back; longer keeps the pool stepped back after they go.
ATTENTION_SECONDS = 60.0

#: The tick counter Windows keeps is 32 bits of milliseconds, so it wraps after about 49.7 days.
_TICK_WRAP = 1 << 32


def elapsed_seconds(now_ticks: int, last_input_ticks: int) -> float:
    """Seconds between two readings of the millisecond tick counter, across its wrap.

    Both readings are the low 32 bits of the same counter, so the difference taken modulo its
    range is the true one for any gap shorter than the wrap itself.
    """
    return ((now_ticks - last_input_ticks) % _TICK_WRAP) / 1000.0


@cache
def _input_api() -> Any:
    """user32 and kernel32 with the two calls typed, or None where there is no such thing."""
    if sys.platform != "win32":
        return None  # pragma: no cover (the other operating system's branch)
    import ctypes
    from ctypes import wintypes

    class LastInputInfo(ctypes.Structure):
        _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))

    try:
        user32 = ctypes.WinDLL("user32")  # type: ignore[attr-defined, unused-ignore]
        kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined, unused-ignore]
    except (OSError, AttributeError):
        return None
    user32.GetLastInputInfo.restype = wintypes.BOOL
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInputInfo)]
    kernel32.GetTickCount.restype = wintypes.DWORD
    kernel32.GetTickCount.argtypes = []
    return user32, kernel32, LastInputInfo


@cache
def _warn_stand_in(milliseconds: int) -> None:
    """Said once per process: every reading from here on is the stand-in, not the keyboard."""
    log.warning("attention.stand_in", since_input_ms=milliseconds)


def stand_in_seconds() -> float | None:
    """`SIFT_ATTENTION_FAKE_INPUT_MS` in seconds, or None when it is unset (the usual case).

    A measurement of the step-back lever sets it to hold the pool at its share (0) or at its full count
    (a number past `ATTENTION_SECONDS`) without anybody at the keyboard. See the setting.
    """
    milliseconds = get_settings().attention_fake_input_ms
    if milliseconds is None:
        return None
    _warn_stand_in(milliseconds)
    return milliseconds / 1000.0


@cache
def _warn_stand_in_file(path: str) -> None:
    """Said once per process: every reading from here on is the file's number, not the keyboard."""
    log.warning("attention.stand_in_file", path=path)


def stand_in_file_seconds() -> float | None:
    """The number in `SIFT_ATTENTION_FAKE_INPUT_FILE`, in seconds, read afresh on every reading.

    None when the setting is unset (the usual case) or the file holds no whole number of
    milliseconds. Read each time, unlike `stand_in_seconds`, so a measurement can move it while
    Sift runs: 0 written into it is somebody arriving at the keyboard, 600000 is them leaving.
    """
    path = get_settings().attention_fake_input_file
    if path is None:
        return None
    _warn_stand_in_file(str(path))
    try:
        milliseconds = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return max(0, milliseconds) / 1000.0


def seconds_since_input() -> float | None:
    """How long ago the last key press or mouse movement was, or None where this cannot be read.

    The stand-in settings, when one is set, answer instead of Windows, on every operating system:
    the fixed number first, then the file.
    """
    stand_in = stand_in_seconds()
    if stand_in is not None:
        return stand_in
    if get_settings().attention_fake_input_file is not None:
        return stand_in_file_seconds()
    api = _input_api()
    if api is None:
        return None
    import ctypes

    user32, kernel32, last_input_info = api
    info = last_input_info()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return elapsed_seconds(int(kernel32.GetTickCount()), int(info.dwTime))


class Attention:
    """Turns the full worker count into the one to run now, and remembers why.

    `since_input` reads the seconds since the last input; None from it means it cannot be read.
    """

    def __init__(self, since_input: Callable[[], float | None] = seconds_since_input) -> None:
        self._since_input = since_input
        self._cause: Cause | None = None
        self._full_by_hand = False
        self._share = STEP_BACK_SHARE

    @property
    def holding(self) -> bool:
        """Whether background work runs fewer workers than the full count, for either cause."""
        return self._cause is not None and not self._full_by_hand

    @property
    def cause(self) -> Cause | None:
        """Why the work is stepped back while it is: somebody's `input`, or `others` busy."""
        return self._cause if self.holding else None

    @property
    def share(self) -> int:
        """The share of the device the step back keeps to, in percent: the setting's last reading."""
        return self._share

    @property
    def share_now(self) -> int:
        """The share background work may use right now, in percent."""
        return self._share if self.holding else WHOLE_DEVICE

    @property
    def full_amount(self) -> bool:
        """Whether background work uses the full count by a press although a cause holds."""
        return self._cause is not None and self._full_by_hand

    @property
    def pressed(self) -> bool:
        """Whether the press for the full amount is on, in play or not."""
        return self._full_by_hand

    def press(self, *, full: bool) -> None:
        """Use the full amount although a cause holds (`full`), or step back again."""
        if full == self._full_by_hand:
            return
        self._full_by_hand = full
        log.info(
            "attention.full_amount_pressed" if full else "attention.step_back_pressed",
            in_use=self._cause is not None,
        )
        announce_now(EVERY_ADMIN, About.JOBS)

    def workers(
        self,
        full: int,
        *,
        step_back: bool,
        share: int = STEP_BACK_SHARE,
        others_busy: bool = False,
        measuring: bool = False,
    ) -> int:
        """How many workers to run now, out of `full`.

        `share` percent of `full`, rounded up and never below one, while input was inside
        `ATTENTION_SECONDS` (with `step_back` on) or `others_busy`, unless the full amount was
        pressed for. Never while `measuring`: a benchmark reads the whole device.
        """
        self._share = min(WHOLE_DEVICE, max(1, share))
        since = self._since_input() if step_back and not measuring else None
        stepped = stepped_workers(full, self._share)
        cause: Cause | None = None
        if stepped < full and not measuring:
            if since is not None and since < ATTENTION_SECONDS:
                cause = "input"
            elif others_busy:
                cause = "others"
        effective = stepped if cause is not None and not self._full_by_hand else full
        if cause != self._cause:
            if cause is None:
                event = "attention.full_count"
            elif effective < full:
                event = "attention.stepping_back"
            else:
                event = "attention.full_amount"
            log.info(event, workers=effective, full=full, share=self._share, cause=cause)
            self._cause = cause
            # The leaf on the sidebar comes and goes with this, and nothing in the queue moves.
            announce_now(EVERY_ADMIN, About.JOBS)
        return effective


#: The one reading the pool and the Activity screen share.
ATTENTION = Attention()


def stepping_back() -> bool:
    """Whether the worker pool is running fewer tasks right now, for either cause."""
    return ATTENTION.holding


def full_amount() -> bool:
    """Whether the worker pool runs its full count by a press although a cause holds."""
    return ATTENTION.full_amount


__all__ = [
    "ATTENTION",
    "ATTENTION_SECONDS",
    "Attention",
    "Cause",
    "elapsed_seconds",
    "full_amount",
    "seconds_since_input",
    "stand_in_file_seconds",
    "stand_in_seconds",
    "stepping_back",
]
