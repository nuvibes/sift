# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making a smaller copy of a file, or one that plays anywhere.

Sift indexes files where they are and, apart from deleting and renaming them, does not change
one. This feature produces a file, and it produces a NEW one, always, beside the original it came
from. Nothing here writes over anything, and that is a property of the write
seam it goes through rather than of care taken here: the seam claims a destination name with a
create that fails if anything is already there, so there is no version of this that lands on top of
somebody's file.

Two kinds of target, and they combine. A **size** (a named preset whose number is a setting, or
one typed in), for the ordinary case of a file being too big for wherever it is going. And
**"plays anywhere"**, which is not about size at all: a container or a codec the far end will not
decode stops a file being usable just as completely, and being small does not help. Where the file
is already in a form that plays anywhere, that one costs a rewrap rather than an encode.

Three rules shape everything else:

**Never below 720p.** A target only reachable by going under it is reported as unreachable rather
than met: the point of a smaller copy is that it is still worth watching. A file already smaller
than that is neither refused nor scaled up.

**The sound is never touched.** It is copied through, at whatever it already was. The one exception
is a file whose sound the widely-supported container physically cannot carry, and only when
compatibility was asked for.

**Say it will not fit before spending four minutes proving it.** From the running time, the picture
size and the current weight it is arithmetic to know when a target is out of reach, so that is
worked out first, per file, with the smallest target that IS reachable offered instead, and it
can still be overridden, because the warning informs rather than refuses.
"""

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
