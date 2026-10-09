# SPDX-License-Identifier: AGPL-3.0-or-later
"""What song is in a file: a whole-track Chromaprint fingerprint per file, read only when asked."""

from __future__ import annotations

from sift.kernel.jobs.quiet_hours import WHEN_PRESS
from sift.kernel.jobs.schedules import PER_FILE, ScheduledTask, register_schedule
from sift.slices.music import landing as landing  # registers the landing hook
from sift.slices.music import schema as schema  # registers the schema component
from sift.slices.music import settings
from sift.slices.music.acoustid import AcoustIDClient
from sift.slices.music.jobs import AUDIO_FINGERPRINT, fingerprint_one, register_handlers
from sift.slices.music.landing import install_gate
from sift.slices.music.lookup import (
    LOOKUP,
    LOOKUP_STARTER,
    LOOKUP_TASK,
    MUSIC_LOOKUP,
    MUSIC_LOOKUP_CATCH_UP,
    NOT_READY,
    LookupNotReady,
    LookupPlan,
    LookupSettings,
    LookupStarter,
    LookupTask,
)
from sift.slices.music.lookup import register_handlers as register_lookup_handlers
from sift.slices.music.names import SharedNameReceipts, SongNames
from sift.slices.music.queue import PRODUCT, MusicQueue
from sift.slices.music.router import router
from sift.slices.music.service import CLAIM_ONLY, SERVICE, MusicService
from sift.slices.music.settings import FINGERPRINT_KEY, LOOKUP_KEY
from sift.slices.music.store import MusicStore, NameStore

#: Only when pressed by default: a library is never read for its music on its own.
register_schedule(
    ScheduledTask(
        id="music",
        unit=PER_FILE,
        title="Generate music fingerprints",
        explain=(
            "Lets Sift match files that share a song. Generating one reads the file's sound track."
        ),
        job_type=AUDIO_FINGERPRINT,
        needs_starter=True,
        when_default=WHEN_PRESS,
        set_in="music",
    )
)

#: AcoustID lookups get their own When, since they send fingerprints off this device.
register_schedule(
    ScheduledTask(
        id=LOOKUP_TASK,
        unit=PER_FILE,
        title="Name songs with AcoustID",
        explain=(
            "Sends each file's music fingerprint and length to AcoustID, never the file, to name "
            "the song it uses."
        ),
        job_type=MUSIC_LOOKUP_CATCH_UP,
        needs_starter=True,
        when_default=WHEN_PRESS,
        set_in="music",
        switch=LOOKUP_KEY,
    )
)

__all__ = [
    "AUDIO_FINGERPRINT",
    "CLAIM_ONLY",
    "FINGERPRINT_KEY",
    "LOOKUP",
    "LOOKUP_STARTER",
    "LOOKUP_TASK",
    "MUSIC_LOOKUP",
    "MUSIC_LOOKUP_CATCH_UP",
    "NOT_READY",
    "PRODUCT",
    "SERVICE",
    "AcoustIDClient",
    "LookupNotReady",
    "LookupPlan",
    "LookupSettings",
    "LookupStarter",
    "LookupTask",
    "MusicQueue",
    "MusicService",
    "MusicStore",
    "NameStore",
    "SharedNameReceipts",
    "SongNames",
    "fingerprint_one",
    "install_gate",
    "register_handlers",
    "register_lookup_handlers",
    "router",
]

settings.register()
