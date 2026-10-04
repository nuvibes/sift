# SPDX-License-Identifier: AGPL-3.0-or-later
"""The four jobs that turn a file on disk into something the grid can draw.

Nothing in Sift's interface shows an original. A grid of thousands of tiles that reached for the
real files would read gigabytes to draw a screen, so every tile is a derivative: a still, a short
clip that plays under the cursor, a strip of frames for the scrubber. They are built once, in the
background, at import time, which is the only time the cost is invisible.

The order is fixed by what each stage knows. `probe` opens the file and works out what it is; the
other three need its answers (how long it is, how big, whether it decodes at all), so they are
enqueued by it rather than beside it, as children of it. That is also what makes the dashboard
honest: one file is one job with three children, and cancelling it cancels all of it.

Everything here is rebuildable. Delete the cache directory and the library still knows what every
file is, who is in it, and what it was tagged: the only thing lost is CPU time. That is what
lets a backup be the database and nothing else.
"""

from __future__ import annotations

# The module's door: each name lives in the module of its product, and is named here for callers.
from sift.kernel.media import MissingAsset, NoReadableCopy, Source, resolve, resolve_decodable
from sift.slices.media_jobs.corrections import (
    reclassify,
    reidentify,
)
from sift.slices.media_jobs.fingerprints import _grey_frames as _grey_frames
from sift.slices.media_jobs.fingerprints import _video_phash as _video_phash
from sift.slices.media_jobs.fingerprints import (
    fingerprint_arrival,
    fingerprint_one,
    fingerprint_stash_box,
)
from sift.slices.media_jobs.job_types import (
    BECAUSE_CONTAINER,
    BECAUSE_INTERLEAVE,
    FINGERPRINT_FILE,
    FINGERPRINT_FOR_STASH_BOXES,
    KEEP_PROBES,
    LOOP_THUMBNAIL,
    PREVIEW,
    PROBE,
    READ_UNREAD,
    REBUILD_PREVIEWS,
    REBUILD_THUMBNAILS,
    RECLASSIFY,
    REIDENTIFY,
    REMUX,
    SPRITE,
    THUMBNAIL,
    ChosenShape,
    FollowOnJobs,
    FollowOnPayloads,
    SettlingJobs,
    ShouldGenerate,
)
from sift.slices.media_jobs.pictures import (
    PICTURES,
    Picture,
    build_picture,
    picture_lack,
    picture_lacking_among,
    picture_switched_on,
)
from sift.slices.media_jobs.previews import (
    PREVIEW_RECIPE_VERSION,
    from_the_still,
    preview,
    preview_recipe,
    rebuild_previews,
)
from sift.slices.media_jobs.probing import (
    keep_probes,
    probe,
    probe_is_coming,
    read_unread,
)
from sift.slices.media_jobs.registration import (
    register_handlers,
)
from sift.slices.media_jobs.repairs import (
    remux,
)
from sift.slices.media_jobs.shared import (
    Unusable,
    broken_bytes,
    recording_verdicts,
)
from sift.slices.media_jobs.sprites import (
    sprite,
)
from sift.slices.media_jobs.thumbnails import (
    BLACK_BELOW,
    FADE_DIM_BELOW,
    FADE_FLAT_BELOW,
    FLAT_BELOW,
    STILL_FRACTIONS,
    STILL_LEVEL_SIZE,
    StillLevels,
    loop_thumbnail,
    rebuild_thumbnails,
    still_candidates,
    still_level_filter,
    still_levels,
    thumbnail,
)

__all__ = [
    "BECAUSE_CONTAINER",
    "BECAUSE_INTERLEAVE",
    "BLACK_BELOW",
    "FADE_DIM_BELOW",
    "FADE_FLAT_BELOW",
    "FINGERPRINT_FILE",
    "FINGERPRINT_FOR_STASH_BOXES",
    "FLAT_BELOW",
    "KEEP_PROBES",
    "LOOP_THUMBNAIL",
    "PICTURES",
    "PREVIEW",
    "PREVIEW_RECIPE_VERSION",
    "PROBE",
    "READ_UNREAD",
    "REBUILD_PREVIEWS",
    "REBUILD_THUMBNAILS",
    "RECLASSIFY",
    "REIDENTIFY",
    "REMUX",
    "SPRITE",
    "STILL_FRACTIONS",
    "STILL_LEVEL_SIZE",
    "THUMBNAIL",
    "ChosenShape",
    "FollowOnJobs",
    "FollowOnPayloads",
    "MissingAsset",
    "NoReadableCopy",
    "Picture",
    "SettlingJobs",
    "ShouldGenerate",
    "Source",
    "StillLevels",
    "Unusable",
    "broken_bytes",
    "build_picture",
    "fingerprint_arrival",
    "fingerprint_one",
    "fingerprint_stash_box",
    "from_the_still",
    "keep_probes",
    "loop_thumbnail",
    "picture_lack",
    "picture_lacking_among",
    "picture_switched_on",
    "preview",
    "preview_recipe",
    "probe",
    "probe_is_coming",
    "read_unread",
    "rebuild_previews",
    "rebuild_thumbnails",
    "reclassify",
    "recording_verdicts",
    "register_handlers",
    "reidentify",
    "remux",
    "resolve",
    "resolve_decodable",
    "sprite",
    "still_candidates",
    "still_level_filter",
    "still_levels",
    "thumbnail",
]
