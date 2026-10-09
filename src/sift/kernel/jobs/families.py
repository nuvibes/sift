# SPDX-License-Identifier: AGPL-3.0-or-later
"""The long passes a person watches, and which jobs belong to each.

Declared beside each handler, as a job type in no family is invisible on the Activity screen."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class Family(StrEnum):
    SCAN = "scan"
    GENERATE = "generate"
    FINGERPRINT = "fingerprint"
    IDENTIFY = "identify"
    SEMANTIC = "semantic"
    OTHER = "other"


#: What each family is called on screen.
FAMILY_LABELS: dict[Family, str] = {
    Family.SCAN: "Scan",
    Family.GENERATE: "Generate",
    Family.FINGERPRINT: "Fingerprint",
    Family.IDENTIFY: "Identify",
    Family.SEMANTIC: "Smart Search",
    Family.OTHER: "Other",
}

#: The families drawn as passes, in screen order.
LONG_PASSES: tuple[Family, ...] = (
    Family.SCAN,
    Family.GENERATE,
    Family.FINGERPRINT,
    Family.IDENTIFY,
    Family.SEMANTIC,
)


@dataclass(frozen=True, slots=True)
class Chore:
    """One piece of housekeeping the Activity screen draws beside the passes."""

    job_type: str
    label: str
    """What it is called on screen. Not the job's own name, which describes the act a row is
    doing ("Looking for duplicates"); this names the thing, so it can head a column."""
    task: str | None = None
    """The task this chore IS on the Tasks screen, by its id (where it is run and scheduled),
    or None for work that is no task (a download, a transcode). Activity's row links to that task's
    row rather than being one more door that starts it. Held to the task registry by a test."""
    priced: bool = True
    """False for work whose length is a person's rather than the machine's: a swap waits for the
    other side before a byte moves, so the runs before it say nothing about this one. Its row takes
    no estimate from them; the running work says where it stands itself (`estimate_itself`)."""
    waiting: str | None = None
    """What the row of work that is not priced says while it is waiting on somebody."""


@dataclass(frozen=True, slots=True)
class OwnEstimate:
    """What work that is not priced says of itself while it runs: waiting, or seconds left."""

    waiting: bool = False
    seconds: int | None = None


_OWN_ESTIMATES: dict[str, Callable[[], OwnEstimate]] = {}


def estimate_itself(job_type: str, read: Callable[[], OwnEstimate]) -> None:
    """Say how a job type that is not priced reads its own time left, beside its handler."""
    _OWN_ESTIMATES[job_type] = read


def own_estimate(job_type: str) -> OwnEstimate | None:
    """What that job type says of itself now, or None where nothing was said how to ask."""
    read = _OWN_ESTIMATES.get(job_type)
    return None if read is None else read()


#: Work outside the passes still worth watching on Activity; a test holds it to the registry.
HOUSEKEEPING: tuple[Chore, ...] = (
    Chore("dedup_scan", "Near duplicates", task="duplicates"),
    Chore("suggestion_scan", "Folder suggestions", task="suggestions"),
    Chore("shoots_look", "Shoots", task="shoots"),
    Chore("stash_box_scan", "Enrichment", task="enrichment"),
    Chore("download", "Downloads"),
    Chore("transcode", "Transcodes"),
    # A swap, watched like a download.
    Chore("swap_session", "Swaps", priced=False, waiting="Waiting for them"),
)


#: Work that Sift starts by itself, left off the queue's list unless pressed, failed or canceled.
BACKGROUND: frozenset[str] = frozenset(
    {
        "scan",
        "scan_count",
        "library_reconcile",
        "keep_probes",
        "identify_file",
        "generate_file",
        "reidentify",
        "face_regroup",
        "face_rematch",
        "face_starters",
        "face_people_from_files",
        "face_box_questions",
        "face_floor",
        "face_whole_picture",
        "semantic_whole_picture",
        "stash_arrived",
        "stash_box_creator_picture",
        "stash_box_link_invented",
        "music_lookup",
        "loop_thumbnail",
    }
)


#: Which family each product's work is counted under, as its coordinator's type says nothing.
PRODUCT_FAMILIES: dict[str, Family] = {
    "thumbnails": Family.GENERATE,
    "previews": Family.GENERATE,
    "sprites": Family.GENERATE,
    "fingerprints": Family.FINGERPRINT,
    "music": Family.FINGERPRINT,
    "faces": Family.IDENTIFY,
    "watermarks": Family.IDENTIFY,
    "meaning": Family.SEMANTIC,
}


def products_of(family: Family | None = None, job_type: str | None = None) -> list[str]:
    """The products a pass makes, or the one a sub-task's type is the work of."""
    if job_type is not None:
        return [key for key, made in PRODUCT_TYPES.items() if made == job_type]
    return sorted(key for key, whose in PRODUCT_FAMILIES.items() if whose is family)


#: The job type each product's work is counted under in its family, by the same keys.
PRODUCT_TYPES: dict[str, str] = {
    "thumbnails": "thumbnail",
    "previews": "preview",
    "sprites": "sprite",
    "fingerprints": "fingerprint_file",
    "music": "audio_fingerprint",
    "faces": "face_scan",
    "watermarks": "watermark_read",
    "meaning": "semantic_describe",
}


#: The payload key a person's per-file press sets: make it again, even where the file has it.
AGAIN = "again"
