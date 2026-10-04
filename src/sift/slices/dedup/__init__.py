# SPDX-License-Identifier: AGPL-3.0-or-later
"""Duplicate-finding: a review queue for what might be the same, and a view of what already is.

Two surfaces, two different problems, and conflating them is the mistake this feature is shaped to
avoid.

**Exact duplicates** are identical bytes. They are not a review problem at all: the content model
resolved them at import, into one asset with several locations. They are a *space* problem. They
appear in the reclaim view, which says how much dropping the extras would free, and they never
appear in the queue because there is nothing to judge.

**Near duplicates** are different bytes that look alike: a re-encode, a crop, a screenshot of a
scene. Those are a judgement problem, and no threshold settles them: a re-encode, a crop and a
genuinely different shot from the same scene all measure as "close". So they go in a queue and a
person decides.

The purpose is reclaiming space, not hoarding redundancy. Sift does not present three copies of a
clip as a good thing; RAID and backups own safety, and this owns the disk filling up.

Both surfaces are admin-only, refused on the server. And nothing in here ever deletes: it proposes,
a person disposes, and the disposing goes out through the one feature allowed to touch a file.
"""

from __future__ import annotations

from sift.kernel.jobs.schedules import ScheduledTask, register_schedule
from sift.kernel.settings_registry import register_setting
from sift.slices.dedup import schema
from sift.slices.dedup.grouping import DEFAULT_MAX_GROUP, Group, Rule
from sift.slices.dedup.jobs import DEDUP_SCAN, register_handlers
from sift.slices.dedup.matcher import (
    DEFAULT_ACCURACY,
    DEFAULT_MAX_DURATION_GAP_MS,
    LEVELS,
    NEAR_FRAME_BITS,
    NEAR_VIDEO_FRAMES,
    VIDEO_FRAMES,
    WIDEST,
    Accuracy,
    Matcher,
    Pair,
)
from sift.slices.dedup.queue import CarriedAttributions, DedupQueue, ReclaimQueue
from sift.slices.dedup.router import router
from sift.slices.dedup.service import (
    CARRY_QUEUE,
    DEFAULT_RULE,
    RULE_LABELS,
    RULES,
    SERVICE,
    Candidate,
    CarryOffer,
    DedupError,
    DedupService,
    Dials,
    Method,
    NotAllowed,
    NotFound,
    RemovalRefused,
    Remover,
    Settled,
    Status,
    Verdict,
)

#: Whether the duplicate sweep runs at all.
#:
#: The pass is asked for once a batch of imports has settled and from the button on the duplicates
#: card, and this is the way to say no to either. It is about a second of work per settled batch, so
#: this is not a switch anybody needs for speed: it is for a library where near-copies are wanted
#: and being asked about them is noise.
#:
#: **It stops the pass and undoes nothing.** Pairs already filed stay on the review queue, and a
#: decision already made stays made. A switch that also swept them away would be a preference
#: deleting somebody's work.
SCAN_KEY = "dedup.scan"

# RETIRED into the When of the duplicates task (`tasks.duplicates.when`), read as "anything but Only
# when I press it". The key stays as a name the switchboard and the old callers ask by; the
# composition root retires it (`settings_registry.retire_setting`).

#: The three dials on the review queue.
#:
#: All three are read when the queue is READ, never when it is scanned. See `matcher.WIDEST`.
#: That is what lets any of them be moved and answered instantly on a library of any size, and it is
#: the reason they are ordinary settings rather than a control that starts an hour of work.
LEVEL_KEY = "dedup.level"
MAX_DURATION_GAP_KEY = "dedup.max_duration_gap_seconds"
KEEP_KEY = "dedup.keep"

register_setting(
    key=LEVEL_KEY,
    scope="app",
    default=DEFAULT_ACCURACY.value,
    choices=tuple(level.value for level in Accuracy),
    # Short. The control is a drop-down about 30 characters wide, and a label longer than that is
    # truncated to an ellipsis, which takes the meaning out of exactly the words that were chosen
    # over a number BECAUSE they carry meaning. The sentence each one is shorthand for is in the
    # help below and the numbers behind them are in the disclosure.
    # The words are the SCREEN'S four rungs, in order, so the dial and the chip on every card
    # under it say the same thing: four descriptions of a threshold in different registers, next
    # to a list whose every card was labelled on a different scale, would read as two scales.
    #
    # The mapping is exact rather than approximate, and it holds for all three fingerprints:
    # `LEVELS` is 0, 2, 4 and the widest for each of them, and the screen's rungs turn over at the
    # same figures. So a queue read at High shows nothing worse than "Almost identical", and the
    # dial says so before it is moved.
    choice_labels=(
        "Exact \u2014 identical",
        "High \u2014 almost identical",
        "Medium \u2014 very similar",
        "Low \u2014 similar",
    ),
    section="Maintenance",
    label="How similar duplicates must be",
    help=("How close two files must be before Sift shows them as duplicates."),
    disclosure=(
        "Each level is a count of how much of the fingerprint differs, which means something "
        "different for each kind of file. For a video, exact is 0, high is 2, medium is 4 and low "
        "is 6, where unrelated videos start to look alike. For a photograph the loosest level "
        "reaches 11, because two different pictures measure at least 26 apart. For a GIF it "
        "counts frames, and the loosest level lets 6 of its 30 frames differ."
    ),
)

register_setting(
    key=KEEP_KEY,
    scope="app",
    default=DEFAULT_RULE,
    choices=RULES,
    choice_labels=RULE_LABELS,
    section="Maintenance",
    label="Which copy to keep",
    help=(
        "The copy Sift suggests keeping in each group of duplicates. Nothing is deleted until you "
        "confirm it."
    ),
    disclosure=(
        "Each rule is tried first, not alone. Two re-encodes of one clip often have the same size "
        "and resolution, so a tie goes to the next rule. Only a group that no rule can separate "
        "is left for you to decide. Higher resolution is the default because it best "
        "survives a re-encode: a re-compressed copy is smaller and newer but no better to look at."
    ),
)

register_setting(
    key=MAX_DURATION_GAP_KEY,
    scope="app",
    default=DEFAULT_MAX_DURATION_GAP_MS // 1000,
    minimum=0,
    maximum=3600,
    unit="sec",
    section="Maintenance",
    label="Largest difference in length",
    automatic_label="No limit",
    disclosure=(
        "Leave it empty to ignore length. Videos Sift hasn't measured yet are always shown."
    ),
    help=(
        "Two copies of one video run about the same length, so a bigger gap is usually a coincidence."
    ),
)


#: Looking for duplicates, as a task: the sweep a scan asks for once it settles, and Run now.
register_schedule(
    ScheduledTask(
        id="duplicates",
        title="Find duplicate files",
        explain="Compares the fingerprints of your files to find exact and near duplicates.",
        job_type=DEDUP_SCAN,
        set_in="importing",
    )
)

__all__ = [
    "CARRY_QUEUE",
    "DEDUP_SCAN",
    "DEFAULT_ACCURACY",
    "DEFAULT_MAX_DURATION_GAP_MS",
    "DEFAULT_MAX_GROUP",
    "DEFAULT_RULE",
    "KEEP_KEY",
    "LEVELS",
    "LEVEL_KEY",
    "MAX_DURATION_GAP_KEY",
    "NEAR_FRAME_BITS",
    "NEAR_VIDEO_FRAMES",
    "RULES",
    "RULE_LABELS",
    "SCAN_KEY",
    "SERVICE",
    "VIDEO_FRAMES",
    "WIDEST",
    "Accuracy",
    "Candidate",
    "CarriedAttributions",
    "CarryOffer",
    "DedupError",
    "DedupQueue",
    "DedupService",
    "Dials",
    "Group",
    "Matcher",
    "Method",
    "NotAllowed",
    "NotFound",
    "Pair",
    "ReclaimQueue",
    "RemovalRefused",
    "Remover",
    "Rule",
    "Settled",
    "Status",
    "Verdict",
    "register_handlers",
    "router",
    "schema",
]
