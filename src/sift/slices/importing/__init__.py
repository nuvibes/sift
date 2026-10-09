# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happens to a file when it arrives: scan, generate, identify, and what a person can turn off.

Off means later, not never: the Build goes back over what was missed.
"""

from __future__ import annotations

from sift.kernel.jobs.quiet_hours import WHEN_WORK
from sift.kernel.jobs.schedules import PER_FILE, ScheduledTask, register_schedule
from sift.slices.importing import schema, tuning
from sift.slices.importing.jobs import (
    GENERATE,
    GENERATE_FILE,
    IDENTIFY,
    IDENTIFY_FILE,
    RUNS,
    register_handlers,
)
from sift.slices.importing.products import (
    PRODUCTS,
    Machine,
    OnePass,
    Product,
    ProductRegistry,
    Reading,
    files_lacking,
    lacking_by_kind,
)
from sift.slices.importing.router import JOBS_AT_ONCE, files_router, router, start_runs
from sift.slices.importing.service import SERVICE, ImportPolicy
from sift.slices.importing.store import ROOT_PREFS, RootPreferences

SECTION = "Importing"

#: Whether Sift reads your folders by itself; on out of the box, since it is how a library fills.
SCAN_KEY = "importing.scan"
# !! RETIRED into `tasks.scan.when`; kept as a name because folder answers are stored under it.


#: Whether the pictures a file is drawn with are built as it arrives: a master over four switches.
GENERATE_KEY = "importing.generate"
# !! RETIRED into `tasks.generate.when`. See SCAN_KEY above.


#: Whether Sift works out what is in a file as it arrives; each feature keeps its own switch.
IDENTIFY_KEY = "importing.identify"
# !! RETIRED into the Whens of faces, Smart Search and watermarks. See SCAN_KEY above.


#: The Generate stage, on the Tasks screen and beside the stage on Importing.
register_schedule(
    ScheduledTask(
        id="generate",
        title="Generate",
        explain=(
            "Generates thumbnails, hover previews, scrubber strips and fingerprints for new files."
        ),
        job_type=GENERATE,
        needs_starter=True,
        # A value never written reads as this (`ScheduledTask.when_default`).
        when_default=WHEN_WORK,
        set_in="importing",
        press="Generate now",
        unit=PER_FILE,
    )
)

#: The Identify stage: its When is a reading of its three tasks' Whens, written to all three.
IDENTIFY_READS: tuple[str, ...] = ("faces", "smart-search", "watermarks")
register_schedule(
    ScheduledTask(
        id="identify",
        title="Identify",
        explain="Recognizes faces, describes files for Smart Search and reads watermarks.",
        job_type=IDENTIFY,
        needs_starter=True,
        reads=IDENTIFY_READS,
        set_in="importing",
        press="Identify now",
        unit=PER_FILE,
    )
)

__all__ = [
    "GENERATE",
    "GENERATE_FILE",
    "GENERATE_KEY",
    "IDENTIFY",
    "IDENTIFY_FILE",
    "IDENTIFY_KEY",
    "IDENTIFY_READS",
    "JOBS_AT_ONCE",
    "PRODUCTS",
    "ROOT_PREFS",
    "RUNS",
    "SCAN_KEY",
    "SECTION",
    "SERVICE",
    "ImportPolicy",
    "Machine",
    "OnePass",
    "Product",
    "ProductRegistry",
    "Reading",
    "RootPreferences",
    "files_lacking",
    "files_router",
    "lacking_by_kind",
    "register_handlers",
    "router",
    "schema",
    "start_runs",
    "tuning",
]
