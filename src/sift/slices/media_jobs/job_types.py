# SPDX-License-Identifier: AGPL-3.0-or-later
"""The job types this feature claims, and the questions it is handed at boot."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence

from sift.kernel.content import DerivativeKind

PROBE = "probe"
THUMBNAIL = "thumbnail"
LOOP_THUMBNAIL = "loop_thumbnail"
PREVIEW = "preview"
SPRITE = "sprite"
REMUX = "remux"

#: Why a repackaged copy was asked for: the work is the same, and only `interleave` is re-measured.
BECAUSE_INTERLEAVE = "interleave"
BECAUSE_CONTAINER = "container"
FINGERPRINT_FOR_STASH_BOXES = "fingerprint_stash_box"
FINGERPRINT_FILE = "fingerprint_file"
REIDENTIFY = "reidentify"
REBUILD_PREVIEWS = "rebuild_previews"
REBUILD_THUMBNAILS = "rebuild_thumbnails"
KEEP_PROBES = "keep_probes"
READ_UNREAD = "read_unread"
RECLASSIFY = "reclassify"

#: What this slice builds for every file; the order they are handed out in is `_ORDER`'s.
_DERIVATIVE_JOBS = (THUMBNAIL, PREVIEW, SPRITE)

#: Whether a derivative is built at all, asked per file; None asks about the whole library.
ShouldGenerate = Callable[[str, str | None], Awaitable[bool]]

#: The hover clip shape somebody chose, read per file; the setting belongs to another feature.
ChosenShape = Callable[[], Awaitable[str]]

#: Other features' job types to start once a file is known, handed down by the composition root.
FollowOnJobs = Sequence[str]

FollowOnPayloads = Mapping[str, Mapping[str, object]]

#: Other features' job types run once over the library after files arrive, not per file.
SettlingJobs = Sequence[str]

_IMAGE = "image"
_GIF = "gif"

#: The picture each of this slice's picture jobs makes.
_PICTURE_KINDS: dict[str, DerivativeKind] = {
    THUMBNAIL: DerivativeKind.THUMB,
    PREVIEW: DerivativeKind.PREVIEW,
    SPRITE: DerivativeKind.SPRITE,
}
