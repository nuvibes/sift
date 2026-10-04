# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fingerprinting a file's music while its bytes are still on the local disk.

A downloaded, dropped, pasted or uploaded file waits in staging before it is copied into a library
folder, and a library folder is very often somewhere else on the network. Reading the staged copy
costs the local disk; reading the same file back out of the library costs the share as well as the
decode (a few seconds for a three-minute MP4, most of it the decode). So where a file's
music is wanted, the read happens here, once, and the answer waits under the file's identity until
the product claims it.

Nothing about the landing depends on this working. A refusal is logged by the registry and the file
lands exactly as it would have; the product then reads it later, from the library, at the library's
price. Lateness rather than loss.

## The switch is the DESTINATION folder's

Off unless the folder the file is going into says yes. The music task is "Only when I press it"
out of the box, and a folder can answer "Fingerprint music as files arrive" for itself on the
Importing pane; a file landing there is read here, and a file landing anywhere else is not read at
all: no pending row, because the read is the cost the switch exists to avoid. A file whose
destination is not known is treated as off.

It asks the folder rather than the library. The file has not been placed yet, but the landing is
told the destination root (`kernel/landing.py`), which is exactly the folder whose answer applies;
asking the library would read every arriving file into a folder that had said no and throw the
answer away thirty days later.

## Why the gate is installed rather than asked for

Whether this is wanted is the import policy's answer, and the policy is built by the composition
root out of things this slice may not import. A landing registers at import, when none of that
exists, so the gate is installed once at boot, and with nothing installed this does nothing at
all. That is the same arrangement `kernel/lanes.py` and `kernel/landing.py` use, and it means a
build that forgets the wiring is inert rather than quietly reading every file that arrives.
"""

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

#: Whether this work starts for a file arriving in one library root: the import policy's
#: `allows_for_root`, asked with the job type and the destination root's id.
RootAllowed = Callable[[str, str], Awaitable[bool]]

#: The import policy's answer, installed at boot. None means this does nothing. See the docstring.
_GATE: RootAllowed | None = None

#: The job type the gate is asked about. The same one the product's switch is mapped to, so the
#: landing and the file's own pass cannot be governed by two different answers.
_JOB_TYPE = "audio_fingerprint"


def install_gate(allowed: RootAllowed | None) -> None:
    """Tell the landing which switch decides whether it runs. Called once, by the boot."""
    global _GATE
    _GATE = allowed


class MusicLanding:
    """Reads the music out of a file that has just landed, and keeps it under its identity."""

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
            # Nowhere to ask, so off: see the docstring.
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
        """Why these bytes certainly carry no sound, from what Sift already knows, or None.

        Asked BEFORE ffmpeg is started, because the start is the whole cost of a file with no
        sound: the read fails at once, and without this it would fail for every still that
        arrived, each one an ffmpeg launch and a `chromaprint.no_audio` line saying a picture had
        no audio track.

        Two facts are read, both kept by the file's own row, which exists by now: the landing
        fires after the file is recorded (`capture.pipeline`). What KIND of file it is, which the
        ingress gate decided from its leading bytes: a still or a GIF never has a sound track.
        And, where the file has already been probed, probing's own answer: no audio stream, no
        read. For a video that has just arrived probing has not run yet (it is queued after the
        landing), so that one is still read, and a silent one still fails fast; there is nothing
        kept to ask, and probing it here would be a second launch for every file that DOES carry
        sound to save one for the few that do not.
        """
        shape = await known_shape(self._db, identity, settings)
        if shape is None:
            return None
        if shape.media_type != Kind.VIDEO:
            return f"a {shape.media_type} carries no sound"
        if shape.probed_at is not None and shape.acodec is None:
            return "the probe found no sound track"
        return None


register_landing(LANDING, MusicLanding)
