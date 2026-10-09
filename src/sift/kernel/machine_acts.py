# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an act on the computer running Sift leaves in History: who asked, and from which device."""

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

#: A computer's name is at most 63 characters on every system Sift runs on.
DEVICE_LONGEST: Final = 63

UNNAMED: Final = "unnamed"


def device_words(asked: str | None) -> str | None:
    """The window's own name for its computer, or None; no brace, as a line's slots use them."""
    if asked is None:
        return None
    words = "".join(one for one in asked if one.isprintable()).strip()[:DEVICE_LONGEST].strip()
    if not words or "{" in words or "}" in words:
        return None
    return words


def act_payload(asked: str | None, machine: str | None, **more: str) -> str:
    """The payload: the device, whether it was this computer, and whatever the act names."""
    device = device_words(asked)
    here = device is not None and machine is not None and device.casefold() == machine.casefold()
    return json.dumps({"from": None if here else device, "here": here, **more})


async def record_act(
    database: Database, viewer: Viewer, verb: str, device: str | None, **more: str
) -> None:
    """Write one act on the computer running Sift into History, told to every admin."""
    machine = machine_name()
    async with telling(database, EVERY_ADMIN, About.LIBRARY) as connection:
        await record_event(
            connection,
            actor=Actor.user(viewer.id),
            verb=verb,
            subject=Subject(kind="computer", id=machine or UNNAMED, name=machine),
            payload=act_payload(device, machine, **more),
        )
