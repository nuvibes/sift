# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work run for a file the moment it lands in staging, while reading it end to end is cheap.

A hook gets the identity and destination root; one that raises is logged and never fails it."""

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
        """Do it; raising is logged and never fails the landing; `root_id` may be None."""
        ...


#: Registration happens at import, before a database exists.
Builder = Callable[[Database], Landing]

_REGISTERED: dict[str, Builder] = {}

_DATABASE: Database | None = None
_BUILT: dict[str, Landing] = {}


@dataclass(frozen=True, slots=True)
class KnownShape:
    """What Sift holds about a landed file's bytes: its kind and the probe's answer, if run."""

    media_type: str
    probed_at: int | None
    acodec: str | None


async def known_shape(database: Database, identity: str, settings: Settings) -> KnownShape | None:
    """The kept shape of these bytes' file, read by identity, or None when nothing is recorded."""
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
    """Give the registry its database at boot, or None in a test; built landings are dropped."""
    global _DATABASE
    _DATABASE = database
    _BUILT.clear()


async def landed(path: Path, identity: str, *, settings: Settings, root_id: str | None) -> None:
    """Run every landing, awaited, as the caller removes staging once the import returns."""
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
