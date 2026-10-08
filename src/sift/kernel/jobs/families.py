# SPDX-License-Identifier: AGPL-3.0-or-later
"""The five long passes a person watches, and which jobs belong to each.

A job type is what the queue runs; a family is what a person waits for. "Scanning" is a walk, a
read of each file and the import that took it in, and nobody watching an import wants those as
three progress bars. The Jobs screen draws one bar per family, the ledger records one run per
family, and an estimate of time left is made per family, so the grouping has to live in one
place, and that place is where each handler is registered, beside its name. A list kept on the
screen drifts, and a job type in no family is invisible on the screen that answers "what is Sift
doing".

A job that is none of these (a backup, a download, a search index rebuild) is `OTHER`: still
on the table, still counted, never drawn as a pass over the library.

The pass a person presses from Importing (one read of each file for every product it lacks
among the ones ticked) runs as TWO of these, `GENERATE` (pictures and fingerprints) and
`IDENTIFY` (faces and meaning), because they are two kinds of work a person waits for separately
and each is counted before it starts: one bar reading "Generate and Identify" over one number
would say nothing about either. The arriving-file jobs land in the same families, one job per
product.
"""

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


#: What each family is called on screen. Declared beside the enum so a label cannot be missing.
FAMILY_LABELS: dict[Family, str] = {
    Family.SCAN: "Scan",
    Family.GENERATE: "Generate",
    Family.FINGERPRINT: "Fingerprint",
    Family.IDENTIFY: "Identify",
    Family.SEMANTIC: "Smart Search",
    Family.OTHER: "Other",
}

#: The families a person runs as a pass and watches, in the order the screen draws them.
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
    """What work that is not priced says of itself while it runs: waiting on somebody, or the
    seconds it has left by its own measure, or neither while it is still measuring."""

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


#: THE WORK THAT IS NOT A PASS OVER THE LIBRARY AND IS STILL WORTH WATCHING.
#:
#: The Activity screen draws the five long passes, and work in the `OTHER` family, which is not a
#: family and is not drawn as one, has nowhere else to appear: a folder pass that never succeeds or
#: a five-minute query could take most of a machine's time and be invisible on the screen that
#: answers "what is Sift doing".
#:
#: Named here rather than gathered from `OTHER`, because `OTHER` is also every backup, prune,
#: reindex and catch-up: two dozen rows nobody opens this screen to read. These are the ones
#: somebody waits for. A type named here that no handler claims is a mistake, and a test holds
#: this list to the registry so a rename is caught where it happens.
HOUSEKEEPING: tuple[Chore, ...] = (
    Chore("dedup_scan", "Near duplicates", task="duplicates"),
    Chore("suggestion_scan", "Folder suggestions", task="suggestions"),
    Chore("shoots_look", "Shoots", task="shoots"),
    Chore("stash_box_scan", "Enrichment", task="enrichment"),
    Chore("download", "Downloads"),
    Chore("transcode", "Transcodes"),
    # A swap with another install: one task per session, running for as long as the transfer
    # does. Not a pass over the library, so not a family; watched like a download.
    Chore("swap_session", "Swaps", priced=False, waiting="Waiting for them"),
)


#: THE WORK SIFT STARTS BY ITSELF, which the queue's list leaves off unless somebody pressed it.
#:
#: A folder counted before its scan, the file details kept after a read, a folder checked for
#: changes, a per-file step a pass hands out: each is a row nobody asked for, and a boot over a
#: library of hundreds of folders draws hundreds of identical Done rows over the work somebody is
#: watching. The family's bar and History's run line say what it did. A pressed one, a failed one
#: and a canceled one stay listed, as `by_itself` rows do. A test holds every name to the registry.
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


#: Which family each PRODUCT's work belongs to, by the key the composition root registers it
#: under. A Build's task is typed by its coordinator (`identify_file`, `generate_file`) whatever it
#: makes, so a run filtered to Smart Search would otherwise count as Identify's work on the
#: Activity screen while Smart Search read "Waiting". This is the map that puts the work where the
#: person looks for it. Declared here, beside the families, because it is the one answer both the
#: registry and the screen read; a product missing from it is counted under its coordinator.
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


#: The job type each PRODUCT's own work is counted under in its family, by the same keys: the kind
#: whose bar on Activity says how many files have it (`thumbnails` is the `thumbnail` row). A task
#: of a coordinator's type that makes a product is that product's work there, so a run somebody
#: pressed over some files is drawn on the product's own line. A product missing here is drawn on
#: a line of its own key.
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


#: THE PAYLOAD KEY A PERSON'S PER-FILE PRESS SETS: "make it again, even where the file has it."
#:
#: A file's work is otherwise asked for by something that only wants what is MISSING (an arriving
#: file, the Build walking the library) and each product's maker skips a file that already has
#: what it makes, which is right for them. "Run now" on one file or a selection is the other
#: question: somebody looked at the file and wants the thing made again. A maker that honours this
#: key re-makes; one that does not is declared so (`Product.again`) and the press offers it only for
#: the files that lack it. Declared here, in the kernel, because the press is the importing
#: feature's and the makers are three other features', and neither may import the other.
AGAIN = "again"
