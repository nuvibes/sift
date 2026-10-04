# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether somebody is using this computer right now, so background work can step back while they are.

Lowering a child's priority (see `kernel.subprocess`) decides who waits when the processor, the disk
and the memory are contended. It does not stop them being contended: eight decodes at the back of
every line still keep the disk's queue full and the memory's pages moving, and a person scrolling a
browser beside them feels that as lag. What removes the contention is running fewer of them while
somebody is at the keyboard, and all of them again once nobody is.

So the worker pool asks this, on the timer it already reconfigures itself on, how many workers to
run: the full count while the computer has been left alone for `ATTENTION_SECONDS`, and a share of
it (a quarter by default, rounded up, never fewer than one) while there has been a key press or a
mouse movement inside that window. A worker told to go finishes the task in its hand first
(`WorkerPool.reconcile`), so stepping back never throws work away, and the count comes back within
a few seconds of the person leaving.

The count alone does not make a share of the device: each tool may take more threads than its
flags say, and a quarter of the workers can still keep every core busy. So the share in force
(`Attention.share_now`) also sizes each background tool's threads and holds it to them
(`media.set_share`, `kernel.budget`): the workers together stay inside the share.

The reading is Windows' own: `GetLastInputInfo`, the moment of the last input in the session Sift
runs in. There is nothing equivalent to ask on Linux or a Mac without a desktop library Sift does
not carry, and a server there usually has nobody at its keyboard anyway, so off Windows the lever
is simply off and the pool runs its full count, as it always has.

A person can overrule the step back for a while: the leaf on the sidebar, pressed, runs the full
count although somebody is at the keyboard, and pressed again steps back once more
(`Attention.press`). The press is held in memory, not stored, because it answers a moment
("I am here, and I want this done now") rather than a standing choice: the standing choice is the
setting, and a press that outlived a restart would turn the setting off without saying so.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import cache
from typing import Any

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.budget import STEP_BACK_SHARE, WHOLE_DEVICE, stepped_workers
from sift.kernel.changes import About, announce_now
from sift.kernel.config import get_settings
from sift.kernel.log import get_logger

log = get_logger(__name__)

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

    `since_input` is how the time since the last input is read: the real reading by default, a
    number a test chooses otherwise. None from it means the lever cannot be read and stays off.

    Two facts are kept between readings. Whether the last reading found somebody at the keyboard
    with a pool big enough to step back and the setting on (`_in_use`: the step back is in play), and
    whether a person has pressed for the full amount (`_full_by_hand`). What the screens are told
    is the pair of them: stepping back is in play and nobody pressed; or it is in play and somebody
    did. Neither is said while the step back is not in play, since nothing is held back then.
    """

    def __init__(self, since_input: Callable[[], float | None] = seconds_since_input) -> None:
        self._since_input = since_input
        self._in_use = False
        self._full_by_hand = False
        self._share = STEP_BACK_SHARE

    @property
    def holding(self) -> bool:
        """Whether background work runs fewer workers than the full count because of input.

        Follows a press at once, while the pool itself follows it at its next reconfigure, a few
        seconds later: the answer says what was asked for, and the pool catches up with it.
        """
        return self._in_use and not self._full_by_hand

    @property
    def share(self) -> int:
        """The share of the device the step back keeps to, in percent: the setting's last reading."""
        return self._share

    @property
    def share_now(self) -> int:
        """The share of the device background work may use right now, in percent: the step back's
        share while it holds, the whole device otherwise (nobody here, or the full amount pressed).
        """
        return self._share if self.holding else WHOLE_DEVICE

    @property
    def full_amount(self) -> bool:
        """Whether background work uses the full count by a press although somebody is here."""
        return self._in_use and self._full_by_hand

    @property
    def pressed(self) -> bool:
        """Whether the press for the full amount is on, in play or not."""
        return self._full_by_hand

    def press(self, *, full: bool) -> None:
        """Use the full amount although somebody is here (`full`), or step back again.

        Held until Sift stops or the next press. Every window is told, since every window draws
        the control, and the pool takes it at its next reconfigure.
        """
        if full == self._full_by_hand:
            return
        self._full_by_hand = full
        log.info(
            "attention.full_amount_pressed" if full else "attention.step_back_pressed",
            in_use=self._in_use,
        )
        announce_now(EVERY_ADMIN, About.JOBS)

    def workers(self, full: int, *, step_back: bool, share: int = STEP_BACK_SHARE) -> int:
        """How many workers to run now, out of `full`.

        `share` percent of `full`, rounded up and never below one, while `step_back` is on and the
        last input was inside `ATTENTION_SECONDS`, unless the full amount was pressed for; `full`
        otherwise. A pool too small to give anything back (one worker, or a share that rounds up
        to the whole pool) never puts the step back in play.
        """
        self._share = min(WHOLE_DEVICE, max(1, share))
        since = self._since_input() if step_back else None
        recent = since is not None and since < ATTENTION_SECONDS
        stepped = stepped_workers(full, self._share)
        in_use = recent and stepped < full
        effective = stepped if in_use and not self._full_by_hand else full
        if in_use != self._in_use:
            # Three names, so a log read later says what the pool did: stepped back, ran the full
            # count with somebody here because of a press, or ran it because nobody is here.
            if in_use:
                event = "attention.stepping_back" if effective < full else "attention.full_amount"
            else:
                event = "attention.full_count"
            log.info(event, workers=effective, full=full, share=self._share)
            self._in_use = in_use
            # The leaf on the sidebar comes and goes with this, and nothing in the queue moves to
            # make a window read it again.
            announce_now(EVERY_ADMIN, About.JOBS)
        return effective


#: The one reading the pool and the Activity screen share.
ATTENTION = Attention()


def stepping_back() -> bool:
    """Whether the worker pool is running fewer tasks right now because the computer is in use."""
    return ATTENTION.holding


def full_amount() -> bool:
    """Whether the worker pool runs its full count by a press although the computer is in use."""
    return ATTENTION.full_amount


__all__ = [
    "ATTENTION",
    "ATTENTION_SECONDS",
    "Attention",
    "elapsed_seconds",
    "full_amount",
    "seconds_since_input",
    "stand_in_file_seconds",
    "stand_in_seconds",
    "stepping_back",
]
