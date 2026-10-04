# SPDX-License-Identifier: AGPL-3.0-or-later
"""The job types this feature claims, and the questions it is handed at boot."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence

from sift.kernel.content import DerivativeKind

PROBE = "probe"
THUMBNAIL = "thumbnail"
#: A still of one moment of a video; the moment is half of its cache key (see `loop_thumbnail`).
LOOP_THUMBNAIL = "loop_thumbnail"
PREVIEW = "preview"
SPRITE = "sprite"
REMUX = "remux"

#: Why a repackaged copy was asked for: the work is the same, and only `interleave` is re-measured.
BECAUSE_INTERLEAVE = "interleave"
BECAUSE_CONTAINER = "container"
FINGERPRINT_FOR_STASH_BOXES = "fingerprint_stash_box"
#: An arriving file's fingerprints, handed out by its read after its pictures.
FINGERPRINT_FILE = "fingerprint_file"
REIDENTIFY = "reidentify"
REBUILD_PREVIEWS = "rebuild_previews"
REBUILD_THUMBNAILS = "rebuild_thumbnails"
#: Named for what it leaves behind: every file's kept reading at the current `PROBE_VERSION`.
KEEP_PROBES = "keep_probes"
READ_UNREAD = "read_unread"
RECLASSIFY = "reclassify"

#: What this slice builds for every file; the order they are handed out in is `_ORDER`'s.
_DERIVATIVE_JOBS = (THUMBNAIL, PREVIEW, SPRITE)

#: Whether a derivative is built at all, asked per file (only the composition root knows a file's
#: folder and its settings); the asset is None where the question is about the whole library.
ShouldGenerate = Callable[[str, str | None], Awaitable[bool]]

#: The hover clip shape somebody chose, read per file; the setting belongs to another feature.
ChosenShape = Callable[[], Awaitable[str]]

#: Other features' job types to start once a file is known. Handed down by the composition root,
#: since a feature may not import another; each passes the same switches as the derivatives.
FollowOnJobs = Sequence[str]

#: What a follow-on job carries beyond its file's id, by job type.
FollowOnPayloads = Mapping[str, Mapping[str, object]]

#: Other features' job types worth running once over the whole library after files arrive, rather
#: than once per file: each builds an index over every file, and is queued deduped and delayed.
SettlingJobs = Sequence[str]

_IMAGE = "image"
_GIF = "gif"

#: The picture each of this slice's picture jobs makes.
_PICTURE_KINDS: dict[str, DerivativeKind] = {
    THUMBNAIL: DerivativeKind.THUMB,
    PREVIEW: DerivativeKind.PREVIEW,
    SPRITE: DerivativeKind.SPRITE,
}
