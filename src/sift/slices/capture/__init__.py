# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture: the front door for bytes, routing every drop, paste, upload and link.

One import pipeline serves every path, so the magic-byte gate fires in exactly one place.
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
