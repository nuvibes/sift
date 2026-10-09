# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fingerprint a file's music from its staged local copy, where its destination folder says yes."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from sift.kernel import chromaprint
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.ingress import Kind
from sift.kernel.landing import known_shape, register_landing
from sift.kernel.log import get_logger
from sift.slices.music.service import as_kept
from sift.slices.music.store import MusicStore

log = get_logger(__name__)

LANDING = "music"

#: The import policy's `allows_for_root(job type, root id)`.
RootAllowed = Callable[[str, str], Awaitable[bool]]

#: Installed at boot; None does nothing, so a build missing the wiring reads no file.
_GATE: RootAllowed | None = None

#: The product's own switch, so the landing and the pass answer to one setting.
_JOB_TYPE = "audio_fingerprint"


def install_gate(allowed: RootAllowed | None) -> None:
    global _GATE
    _GATE = allowed


class MusicLanding:
    name = LANDING

    def __init__(self, database: Database) -> None:
        self._db = database
        self._store = MusicStore(database)

    async def landed(
        self, path: Path, identity: str, settings: Settings, *, root_id: str | None
    ) -> None:
        if _GATE is None:
            return
        if root_id is None:
            # Nowhere to ask, so off.
            log.info("music.landing_skipped", identity=identity, because="no destination")
            return
        if not await _GATE(_JOB_TYPE, root_id):
            return
        silent = await self._known_silent(identity, settings)
        if silent is not None:
            log.info("music.landing_skipped", identity=identity, because=silent)
            return
        fingerprint = await chromaprint.read_whole_track(path, settings=settings)
        await self._store.keep_pending(identity, as_kept(fingerprint))
        log.info(
            "music.fingerprinted_at_landing",
            identity=identity,
            values=len(fingerprint.values),
            covers_ms=fingerprint.duration_ms,
            at=int(time.time()),
        )

    async def _known_silent(self, identity: str, settings: Settings) -> str | None:
        """Why these bytes certainly carry no sound, so ffmpeg is not started; None when unknown."""
        shape = await known_shape(self._db, identity, settings)
        if shape is None:
            return None
        if shape.media_type != Kind.VIDEO:
            return f"a {shape.media_type} carries no sound"
        if shape.probed_at is not None and shape.acodec is None:
            return "the probe found no sound track"
        return None


register_landing(LANDING, MusicLanding)
