# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture: the front door for bytes.

Everything a person hands Sift arrives here (a dropped file, a clipboard paste, an upload, a
dragged-in link) and this decides what it is and routes it. It owns no tables: it is a router, and
a router that owned state would start duplicating the things it routes to. It writes through the
content store (assets and where they sit) and hands links to the downloader.

One import pipeline serves every path, so the magic-byte gate fires in exactly one place on the way
in and cannot be reached around. The pipeline is published for the downloader to call directly with
what it fetched, and the downloader's URL entry point is consumed through a structural seam wired by
the application: neither feature imports the other.
"""

from __future__ import annotations

from sift.kernel.ingress import NoDestination
from sift.slices.capture.jobs import IMPORT, register_handlers, sweep_staging
from sift.slices.capture.pipeline import (
    ImportOutcome,
    Route,
    import_file,
    route_capture,
    usable_source_url,
)
from sift.slices.capture.router import router
from sift.slices.capture.service import SERVICE, CaptureService

__all__ = [
    "IMPORT",
    "SERVICE",
    "CaptureService",
    "ImportOutcome",
    "NoDestination",
    "Route",
    "import_file",
    "register_handlers",
    "route_capture",
    "router",
    "sweep_staging",
    "usable_source_url",
]
