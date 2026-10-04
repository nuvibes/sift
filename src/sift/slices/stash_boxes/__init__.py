# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stash-boxes: the stash-boxes Sift can ask about a person, a site or a file.

Their own name, used literally. There are three Sift is known to work with (StashDB, FansDB and
PMVStash), and they are named individually wherever they are listed. There is no invented
collective noun for them anywhere in the interface.

What this feature owns: the list of configured boxes, the sealed key for each, the per-instance
cache of what they have said, and the one adapter that speaks to them. What it deliberately does
not own: any decision about what to do with an answer. Nothing here writes to the library.
"""

from __future__ import annotations

from sift.kernel.jobs.quiet_hours import WHEN_PRESS
from sift.kernel.jobs.schedules import PER_FILE, ScheduledTask, register_schedule
from sift.slices.stash_boxes.adapter import (
    DEFAULT_REQUESTS_PER_MINUTE,
    REFUSAL_BACKOFF_SECONDS,
    Box,
    SessionFactory,
    StashBoxAdapter,
    StashBoxRefused,
    StashBoxUnreachable,
)
from sift.slices.stash_boxes.enrich import AssetWriter, file_writer, filing_as
from sift.slices.stash_boxes.entities import (
    ENTITIES,
    EntityEnricher,
    StarterPictures,
)
from sift.slices.stash_boxes.jobs import (
    STASH_SCAN,
    STASH_SWEEP,
    ScanDeps,
    register_handlers,
    register_picture_handler,
    strategies_for,
)
from sift.slices.stash_boxes.known import KnownScene, link_known_scene
from sift.slices.stash_boxes.queue import LinkedQueue, StudioQueue, TaggerQueue, UndecidedQueue
from sift.slices.stash_boxes.reconcile import RECONCILER, Reconciler, ReconcileReceipts
from sift.slices.stash_boxes.router import router
from sift.slices.stash_boxes.schema import STASH_BOX_COMPONENT, STASH_BOX_VERSION
from sift.slices.stash_boxes.service import CACHE_DAYS, SERVICE, Answer, BoxView, StashBoxService
from sift.slices.stash_boxes.settings import (
    ASK_NEW_FILES_KEY,
    AUTO_APPLY_KEY,
    DURATION_KEY,
    ENRICHED,
    ENRICHING_OFF,
    SCAN_KEY,
    box_for,
)
from sift.slices.stash_boxes.studios import CreatorStudios

#: Looking up new files on the stash-boxes, as a task: the sweep the fingerprints ask for once they
#: are ready, and Run now (Auto-enrich over the whole library). Works only while enriching is on.
#: Only when pressed until somebody chooses otherwise: every lookup sends fingerprints to a service
#: somebody else runs, so the first one is a person's decision.
register_schedule(
    ScheduledTask(
        id="enrichment",
        title="Look up new files on stash-boxes",
        explain="Looks up new files on the stash-boxes you added, once their fingerprints are ready.",
        job_type=STASH_SWEEP,
        needs_starter=True,
        when_default=WHEN_PRESS,
        set_in="stash-boxes",
        unit=PER_FILE,
    )
)

__all__ = [
    "ASK_NEW_FILES_KEY",
    "AUTO_APPLY_KEY",
    "CACHE_DAYS",
    "DEFAULT_REQUESTS_PER_MINUTE",
    "DURATION_KEY",
    "ENRICHED",
    "ENRICHING_OFF",
    "ENTITIES",
    "RECONCILER",
    "REFUSAL_BACKOFF_SECONDS",
    "SCAN_KEY",
    "SERVICE",
    "STASH_BOX_COMPONENT",
    "STASH_BOX_VERSION",
    "STASH_SCAN",
    "STASH_SWEEP",
    "Answer",
    "AssetWriter",
    "Box",
    "BoxView",
    "CreatorStudios",
    "EntityEnricher",
    "KnownScene",
    "LinkedQueue",
    "ReconcileReceipts",
    "Reconciler",
    "ScanDeps",
    "SessionFactory",
    "StarterPictures",
    "StashBoxAdapter",
    "StashBoxRefused",
    "StashBoxService",
    "StashBoxUnreachable",
    "StudioQueue",
    "TaggerQueue",
    "UndecidedQueue",
    "box_for",
    "file_writer",
    "filing_as",
    "link_known_scene",
    "register_handlers",
    "register_picture_handler",
    "router",
    "strategies_for",
]
