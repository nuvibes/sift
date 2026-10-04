# SPDX-License-Identifier: AGPL-3.0-or-later
"""What song is in a file.

A clip's sound is usually one track, unmixed, for the length of the clip, so the sound is the
one part of a file that can be matched against something the person already owns. This slice keeps
the evidence for that: a Chromaprint fingerprint of every file's audio, whole, computed once.

Three decisions shape it.

**The whole track, never a window.** Chromaprint's own tool reads the first two minutes and the
public service is built around whole music files. Thirty seconds taken from five minutes into a
track match a whole-track fingerprint at a bit error rate of 0.011 and score as random against a
fingerprint of the same track's first two minutes, so a window is a different answer, not a
cheaper one. See `kernel/chromaprint.py`.

**The cost is the DECODE, and it is a few seconds a file.** On MP4, with the video dropped, the
demuxer skips it, so the read is a small part of the file's bytes, and a three-minute video takes
a few seconds, the same again when its bytes are already cached, so the time is the audio decode
rather than the share. MKV, WebM and AVI,
where sound and picture share blocks, may read nearly whole. A few seconds across a whole library is
still hours nobody asked for, so nothing reads a library on its own: a file that arrives through
Sift is fingerprinted while it is still on the local disk, and only when the folder it is going to
says so (`slices/music/landing.py`); a library that was already here waits for the music task's
Run now (`slices/music/queue.py`), priced as every pass is (`Ledger.estimate`).

**Nothing here identifies a song yet.** What is gathered is the thing that cannot be gathered
later without reading everything again; matching it (against a folder of the person's own music,
or against the rest of the library to find the clips that share a track) reads these rows and
opens no file.
"""

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

#: Music fingerprints, as a task. It is also under Generate's own When on the way in (the import
#: gate asks both), so a file arriving is fingerprinted only when both start on their own.
#:
#: ONLY WHEN PRESSED OUT OF THE BOX: a library is never read for its music on its own. A folder
#: can say yes for itself, and then a file arriving in it is fingerprinted at staging; everything
#: else waits for Run now, the Build or a file's Run task. An install that
#: had this at "As soon as there is work" is moved here once by the settings step at v11.
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

#: Naming songs with AcoustID, as a task of its own, beside the fingerprints and never inside them.
#:
#: Making a fingerprint sends nothing anywhere; this sends each file's music fingerprint and its
#: length to AcoustID, a service somebody else runs, so it is a decision of its own with a When of
#: its own, and pressing Generate music fingerprints asks AcoustID about nothing (see
#: `slices/music/lookup.py`). ONLY WHEN PRESSED OUT OF THE BOX: nothing is stored under its When
#: until somebody chooses one, so every install reads the default, and a library's fingerprints
#: leave this device only on a press or on a When
#: somebody chose. Its press refuses in words while the switch is off or there is no key, and its
#: row says the switch is off (`switch`) and names the Music pane where it is turned on.
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
