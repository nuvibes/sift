# SPDX-License-Identifier: AGPL-3.0-or-later
"""The downloader adapters: one interface over several external tools.

Each tool (yt-dlp for most video hosts, gallery-dl for image galleries) is run as a separate
process and never imported. Which tool handles a given address is decided by a small registry of
per-host rules with a catch-all behind it, so adding a site is one entry rather than a change to
any of the code that runs the tools.

Running the tools out-of-process is not only a robustness choice. The tools are copyleft, and a
program that imported them would take on their licence; a program that runs them as subprocesses
does not. The registry and the argument builders are Sift's own; the tools stay at arm's length.
"""

from __future__ import annotations

from sift.slices.download.sources.downloader import Downloader
from sift.slices.download.sources.errors import (
    CookiesNeeded,
    DownloadError,
    LoginRequired,
    NothingFound,
    UnsupportedURL,
)
from sift.slices.download.sources.normalize import normalize_url, url_hash
from sift.slices.download.sources.registry import (
    SITES,
    Attribution,
    Backend,
    SiteRecord,
    classify,
    match_site,
)

__all__ = [
    "SITES",
    "Attribution",
    "Backend",
    "CookiesNeeded",
    "DownloadError",
    "Downloader",
    "LoginRequired",
    "NothingFound",
    "SiteRecord",
    "UnsupportedURL",
    "classify",
    "match_site",
    "normalize_url",
    "url_hash",
]
