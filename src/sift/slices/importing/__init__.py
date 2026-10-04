# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happens to a file when it arrives, and what a person can turn off.

Three groups over one moment. A file lands, and Sift **scans** it to find out what it is,
**generates** the pictures it is drawn with, and **identifies** what is in it. The first is the
floor (everything else reads what it wrote) and the other two are choices, because they are what
a library is waiting on before it can be used.

**Off means later, not never.** Every switch here is read as each file arrives, so turning one off
skips only what lands while it is off. Each group carries a way to go back over what was missed:
the Build is one pass that reads each file once for everything it lacks among the
products ticked on its sheet: without it a rescan would never fill the gaps in, because a scan
skips any file whose path, size and mtime are unchanged.

**A folder may answer differently.** An override is stored per library root, in this feature's own
table, and an absent row means the folder follows the library. See `schema`.
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

#: Whether Sift reads your folders by itself: the walk, the whole-library pass, and the catch-up
#: that finds what moved while Sift was closed.
#:
#: **The third master.** A file that was never probed has no dimensions and cannot be laid out on a
#: wall, but probing is not what this switches. Off, nothing walks a folder looking for new files;
#: a file already taken in is still probed, and a file pasted or downloaded is still imported,
#: because those are not the walk.
#:
#: On out of the box, and it has to be: it is how a library gets its files. The switch is for
#: somebody who wants their folders left alone for a while (a disk being reorganised, a share that
#: is about to go away) and it is the one of the three masters that cannot sensibly start off.
SCAN_KEY = "importing.scan"
#
# RETIRED INTO THE SCAN TASK'S WHEN (`tasks.scan.when`), with the other two below. The key is kept
# as a name because a folder's own answer is stored under it and the import gates ask it by this
# name; it is READ through the When ("anything but Only when I press it"). See
# `settings_registry.retire_setting` and the composition root, which retires it. What the switch
# said is one of three answers: as files arrive, in quiet hours, or only when pressed.


#: Whether the pictures a file is drawn with are built as it arrives.
#:
#: **On out of the box: a new library is usable without pressing anything.** Hover previews,
#: scrubber strips and fingerprints are made as files arrive, and work somebody presses is queued
#: ahead of them. The thumbnail is not under this at all: every file gets one as it arrives,
#: because without it a file cannot be drawn on a wall.
#:
#: A MASTER OVER FOUR SWITCHES RATHER THAN A READING OF THEM. It answers a different question:
#: does this group run at all, so it is a real answer with no mixed state, and the four settings
#: under it keep their own meanings and their own stored values.
GENERATE_KEY = "importing.generate"
# !! RETIRED into `tasks.generate.when`. See SCAN_KEY above. The Generate task starts on "As
# files arrive".


#: Whether Sift works out what is in a file as it arrives.
#:
#: Faces, Smart Search and watermarks each keep their own switch, off out of the box: each
#: downloads models and is the most expensive work Sift does per file. Their Whens start on "As
#: files arrive", so turning one on is the only answer needed.
#:
#: It does not turn recognition or Smart Search on. Each of those has its own switch on its own
#: screen, answering whether the feature exists at all; this answers when it runs.
IDENTIFY_KEY = "importing.identify"
# !! RETIRED into the Whens of the three tasks it was the master over (faces, Smart Search and
# watermarks), read as on while any of them starts on its own. See SCAN_KEY above.


#: The Generate stage, on the Tasks screen and beside the stage on Importing. Its work is the
#: pictures a file is drawn with, made as it arrives (which job types those are is the composition
#: root's to say) or by a Generate run over the library, which is what its press starts.
register_schedule(
    ScheduledTask(
        id="generate",
        title="Generate",
        explain=(
            "Generates thumbnails, hover previews, scrubber strips and fingerprints for new files."
        ),
        job_type=GENERATE,
        needs_starter=True,
        # A value never written reads as this, so an install that chose "Only when I press it"
        # keeps its stored answer; see `ScheduledTask.when_default`.
        when_default=WHEN_WORK,
        set_in="importing",
        press="Generate now",
        unit=PER_FILE,
    )
)

#: The Identify stage: faces, Smart Search and watermarks, three tasks with a When each. Its own
#: When is a READING of theirs (their shared answer, or mixed), and an answer chosen for it is
#: written to all three, so nothing is stored here that could disagree with them. Its press is an
#: Identify run over the library for the three products, the run each of them starts alone.
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
