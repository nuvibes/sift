# SPDX-License-Identifier: AGPL-3.0-or-later
"""What runs for a file the moment it lands in staging, before anything reads it from the library.

Everything a person hands Sift waits in Sift's own staging directory before it is copied into a
library folder, which is often a network share. For work that reads a file END TO END this is
the one affordable moment: local disk instead of the share's one lane. Features register work here
at import (no slice imports another), fired once per landing from the function every origin uses.

A hook gets the file's identity, not an asset id: the bytes may be a file already held, an asset
written a moment later, or nothing if the copy fails, and the identity is true in every case. It
also gets the destination root (None if unknown), the only folder there is to ask about per-folder
switches while the file has no place yet. A hook that raises is logged and the rest run: the
landing is what was asked for, and anything left undone is picked up later by its own pass.

The handle is installed once by the composition root (as `kernel/lanes.py` does), since the
import pipeline holds no database; nothing installed means nothing runs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.log import get_logger

log = get_logger(__name__)


class Landing(Protocol):
    """One thing to do for a file that has just landed, while its bytes are still local."""

    @property
    def name(self) -> str: ...

    async def landed(
        self, path: Path, identity: str, settings: Settings, *, root_id: str | None
    ) -> None:
        """Do it. Raising is allowed and is logged; it never fails the landing.

        `root_id` is the library root the file is being placed in, None where none is known.
        See the module docstring.
        """
        ...


#: How to build one, given the handle it needs: registration happens at import, before a database.
Builder = Callable[[Database], Landing]

_REGISTERED: dict[str, Builder] = {}

#: The handle the composition root installed, and the landings built against it; None until boot.
_DATABASE: Database | None = None
_BUILT: dict[str, Landing] = {}


@dataclass(frozen=True, slots=True)
class KnownShape:
    """What Sift already holds about a landed file's bytes: its kind, and the probe's answer
    where the probe has run. Read for a landing, which has no viewer to scope a read to."""

    media_type: str
    probed_at: int | None
    acodec: str | None


async def known_shape(database: Database, identity: str, settings: Settings) -> KnownShape | None:
    """The kept shape of the file these bytes belong to, or None when nothing is recorded yet.

    The one library read a landing may make, the file's own row by identity; in the kernel because
    a feature never reads the content store itself.
    """
    from sift.kernel.content.identity import ContentStore

    asset = await ContentStore(database, settings).resolve_by_identity(identity)
    if asset is None:
        return None
    return KnownShape(
        media_type=str(asset.media_type), probed_at=asset.probed_at, acodec=asset.acodec
    )


def register_landing(name: str, builder: Builder) -> None:
    """Claim a name. Registering the same one twice is a bug, not an override."""
    if name in _REGISTERED:
        raise ValueError(f"a landing named {name!r} is already registered")
    _REGISTERED[name] = builder


def registered_landings() -> dict[str, Builder]:
    """The registry, copied. Nothing mutates it through here."""
    return dict(_REGISTERED)


def install(database: Database | None) -> None:
    """Give the registry the handle its landings are built against, or take it away.

    Once at boot, or None from a test. Built landings are dropped with it, so none stays bound to a
    database that has gone.
    """
    global _DATABASE
    _DATABASE = database
    _BUILT.clear()


async def landed(path: Path, identity: str, *, settings: Settings, root_id: str | None) -> None:
    """Run every registered landing for a file that has just arrived in staging.

    Awaited, not launched: the caller removes staging as soon as the import returns. `root_id` is
    required, None included, so a forgetful caller cannot silently become "no destination".
    """
    if _DATABASE is None:
        return
    for name, builder in _REGISTERED.items():
        try:
            landing = _BUILT.get(name)
            if landing is None:
                landing = builder(_DATABASE)
                _BUILT[name] = landing
            await landing.landed(path, identity, settings, root_id=root_id)
        except Exception:
            # Never fail the landing over side work (see the module docstring).
            log.exception("landing.failed", landing=name, path=str(path))
