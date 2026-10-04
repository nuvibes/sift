# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the capture endpoints send and receive.

A request here carries a link, some bytes, or both, and always a destination expressed as a folder
id, never a path. What comes back is an id to watch: a job that is importing a file, or a
download row that is fetching a link. Neither ever carries a path back; where a root sits on the
server's disk is not something an interface is told.
"""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire

#: The same bound the download slice puts on a submitted link. Without it this route (which takes
#: whatever was pasted or dragged) would accept a string of any size at all and carry it as far as
#: the resolver. Real URLs are far below this; the number exists to stop a paste being
#: a way to hand the server an arbitrary amount of text.
MAX_URL = 4096


class UrlImport(Wire):
    """A link to fetch, and where to put what comes back."""

    url: str = Field(min_length=1, max_length=MAX_URL)
    #: The drop target. None means the default download folder.
    dest_folder_id: str | None = None


class ImportAccepted(Wire):
    """A file was staged and an import queued. Watch the job to see it settle and appear."""

    job_id: str


class DownloadAccepted(Wire):
    """A link was handed to the downloader. Watch the download row to see it fetch and import."""

    download_id: str


class CaptureAccepted(Wire):
    """A clipboard item was taken in one of two ways, and exactly one id says which.

    A usable link becomes a download; anything else becomes an import of the pasted bytes. The
    client watches whichever id came back.
    """

    job_id: str | None = None
    download_id: str | None = None
