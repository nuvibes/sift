# SPDX-License-Identifier: AGPL-3.0-or-later
"""Creating a smaller copy of a file, or one that plays anywhere, always as a NEW file beside it.
Never below 720p, the sound is copied, and an unreachable target is said before any encode."""

from __future__ import annotations

from sift.slices.media_edit import schema, settings, tidy
from sift.slices.media_edit.editor import EDIT, EDITED_TAG, EDITOR, EditService
from sift.slices.media_edit.jobs import register_handlers
from sift.slices.media_edit.refusals import Refused
from sift.slices.media_edit.router import router
from sift.slices.media_edit.service import (
    COMPRESS,
    COMPRESS_SAMPLE,
    COMPRESSOR,
    PRODUCED_TAG,
    CompressService,
)
from sift.slices.media_edit.settings import Preset

settings.register()
tidy.register()

__all__ = [
    "COMPRESS",
    "COMPRESSOR",
    "COMPRESS_SAMPLE",
    "EDIT",
    "EDITED_TAG",
    "EDITOR",
    "PRODUCED_TAG",
    "CompressService",
    "EditService",
    "Preset",
    "Refused",
    "register_handlers",
    "router",
    "schema",
]
