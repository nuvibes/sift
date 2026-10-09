# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the capture endpoints send and receive: a destination by folder id, never a path."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire

#: The same bound the download slice puts on a submitted link.
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
    """A clipboard item taken as a download or an import; exactly one id says which."""

    job_id: str | None = None
    download_id: str | None = None
