# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an act on the computer running Sift leaves in History: who asked, and from which device.

The desktop routes (`slices/desktop`) and the restart (`slices/performance`) change a machine rather
than a library (network sharing, where Sift keeps its data, an installer, which library is open, a
restart, Windows' own settings), and asked from a window on another computer they happen with
nobody at that machine. Here in the kernel because two slices write it and neither may import the
other. So each one that CHANGES it writes an
event, in its own transaction, naming the admin who asked and the device the window runs on. A read
writes nothing: looking at a log or a folder size changes nothing there.

## The device

The window says what it is called, when it can: the Sift app knows its computer's name and sends it
(`device` on the ask); a browser knows no such thing, and is said as "another computer". The words
are the window's own and are kept as words, never as an address: cut to a name's length, printable,
and never anything a sentence could read as one of its slots. Where the window names the computer
running Sift itself, the line says the act came from that computer's own screen.

## The computer

The event's subject is the computer running Sift, by the name this process reads off the machine it
runs on (`hardware.machine_name`), which is the same machine the app there answers for. History is
an admin's to read, and so is the name: the one audience `machine_name` is already shown to.
"""

from __future__ import annotations

import json
from typing import Final

from sift.kernel.access import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Database
from sift.kernel.hardware import machine_name
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject

#: The most characters of a device's own name a line keeps. A computer's name is at most 63 on
#: every system Sift runs on; anything longer is not a name.
DEVICE_LONGEST: Final = 63

#: The subject's id where this machine gives no name worth saying (`machine_name` is None).
UNNAMED: Final = "unnamed"


def device_words(asked: str | None) -> str | None:
    """The window's own name for its computer, as a line may say it, or None where it said none.

    Only printable characters, trimmed, at most `DEVICE_LONGEST`; a brace is refused outright
    because a line's slots are written in braces and a name must never read as one.
    """
    if asked is None:
        return None
    words = "".join(one for one in asked if one.isprintable()).strip()[:DEVICE_LONGEST].strip()
    if not words or "{" in words or "}" in words:
        return None
    return words


def act_payload(asked: str | None, machine: str | None, **more: str) -> str:
    """The event's payload: the device (`from`), whether it was this computer itself (`here`), and
    whatever the act names (a version, a library)."""
    device = device_words(asked)
    here = device is not None and machine is not None and device.casefold() == machine.casefold()
    return json.dumps({"from": None if here else device, "here": here, **more})


async def record_act(
    database: Database, viewer: Viewer, verb: str, device: str | None, **more: str
) -> None:
    """Write one act on the computer running Sift into History, in its own transaction.

    Told to every admin inside the write (`telling`), so the Settings feed open on any screen reads
    the line as it lands. The act itself has already happened by the time this runs: the app there
    answered for it, and only an act it took is written.
    """
    machine = machine_name()
    async with telling(database, EVERY_ADMIN, About.LIBRARY) as connection:
        await record_event(
            connection,
            actor=Actor.user(viewer.id),
            verb=verb,
            subject=Subject(kind="computer", id=machine or UNNAMED, name=machine),
            payload=act_payload(device, machine, **more),
        )
