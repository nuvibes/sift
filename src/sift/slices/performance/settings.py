# SPDX-License-Identifier: AGPL-3.0-or-later
"""How hard Sift is allowed to work the machine, as preferences rather than only as hardware.

Sift sizes itself: it counts the cores, leaves one for the interface, and caps the expensive jobs
so a folder of videos does not fill every worker with encoding and leave the grid empty. These
settings are for a different answer: a NAS worked gently, a workstation that can take more, a
scan that keeps the disk too busy to browse.

Every value here is 0 = automatic ("let Sift decide"), so an install that never touches them
behaves like a fresh one. The resolvers below are the one place that sentinel is spent.

Nothing here reads a job type or a queue: the composition root (`sift/wiring/workers.py`) maps an
effective number onto the job types it governs, since a slice does not reach into another slice to
find out which types those are.
"""

from __future__ import annotations

from sift.kernel.budget import STEP_BACK_SHARE
from sift.kernel.hardware import HardwareReport
from sift.kernel.lanes import MAX_READS_AT_ONCE
from sift.kernel.sampling import (
    DEFAULT_PREVIEW_SHAPE,
    PREVIEW_SHAPE_SETTING,
    PREVIEW_SHAPES,
)
from sift.kernel.settings_registry import register_setting, remove_setting

#: The most a person may set by hand. Far above the automatic cap (`MAX_WORKERS`), because that cap
#: is about where more workers stop helping *on a typical box*: a deliberate override on a big
#: machine is allowed past it, while an absurd value that would just thrash the disk is still
#: refused at the door.
MAX_MANUAL_WORKERS = 64

#: The polling interval bounds, in seconds. A poll asks the filesystem what changed, so it costs a
#: directory walk each time: too fast hammers a slow share, too slow makes a dropped-in file feel
#: lost. The default is a middle a person does not have to think about.
MIN_POLL_SECONDS = 1

#: 0 = automatic on every one of these. See the module docstring.
AUTOMATIC = 0

WORKER_COUNT_KEY = "performance.worker_count"
GENERATION_LIMIT_KEY = "performance.generation_limit"
SCAN_LIMIT_KEY = "performance.scan_limit"

#: Per-derivative on/off switches. A scan probes every file for its identity (never optional: that
#: is what makes it an asset), and then builds these on top. Turning one off means a scan does that
#: much less work; it can be turned back on and a rescan fills in what was skipped.
GENERATE_THUMBNAILS_KEY = "performance.generate_thumbnails"
GENERATE_PREVIEWS_KEY = "performance.generate_previews"

#: Which shape a hover preview has. Both the shapes and this preference's name are the kernel's,
#: because three features have to agree about them. See `kernel.sampling`. Re-exported here so
#: this module still reads as the list of what Performance owns.
PREVIEW_SHAPE_KEY = PREVIEW_SHAPE_SETTING
GENERATE_SPRITES_KEY = "performance.generate_sprites"
GENERATE_FINGERPRINTS_KEY = "performance.generate_fingerprints"
SCAN_FACES_ON_IMPORT_KEY = "performance.scan_faces_on_import"

#: How many files may be read at once from every network share, instead of each share's measured
#: number; 0 = as measured for each storage. See kernel.lanes.
SHARE_READS_KEY = "performance.share_reads_at_once"

#: A removed setting ("Offer to measure this device"). A stored row for it is inert; it is
#: declared removed below so History lines about it still name it (`settings_registry.Removed`).
TUNE_PROMPT_REMOVED_KEY = "performance.tune_prompt"
REPAIR_PLAYBACK_KEY = "performance.repair_playback"

#: Whether background work steps back while somebody is using the computer (`kernel.attention`).
STEP_BACK_KEY = "performance.step_back_while_used"

#: Whether background work steps back while other programs keep this device busy. Off until the
#: reading (`kernel.device_load`) is measured accurate; it logs what it would do either way.
BUSY_STEP_BACK_KEY = "performance.step_back_while_busy"

#: How much of the device background work uses while it steps back, in percent: a quarter unless
#: somebody chose otherwise. Bounds the worker count and each tool's threads together (see
#: `kernel.budget`); read by the same reconfigure.
STEP_BACK_SHARE_KEY = "performance.step_back_share"

#: The least share a person may choose. Below a tenth the pool is one worker on nearly every
#: machine, which the step back already never goes under.
MIN_STEP_BACK_SHARE = 10


def register() -> None:
    """Declare the performance preferences. Called once, at import, by the slice package.

    Kept as a function rather than run at module import so the order is the package's to state and
    so a test can read the resolvers without the registry being touched as a side effect.
    """
    remove_setting(
        TUNE_PROMPT_REMOVED_KEY,
        label="Offer to measure this device",
        why="It decided one field of the self-test's answer that no screen reads.",
    )
    _register_counts()
    _register_import_work()
    _register_step_back()
    # Scanning faces on import is the faces task's When (`tasks.faces.when`). The key stays as the
    # name the import gate and a folder's own answer use; the composition root retires it.


def _register_counts() -> None:
    """The counts behind Concurrency's Edit on Performance, beside the step back."""
    register_setting(
        key=WORKER_COUNT_KEY,
        scope="app",
        default=AUTOMATIC,
        section="Performance",
        label="Tasks at the same time",
        automatic_label="Automatic",
        disclosure=(
            "Automatic chooses from your CPU, which is usually right. A change takes effect within "
            "seconds, with no restart."
        ),
        help=(
            "How many background tasks Sift runs at the same time, such as scans and thumbnails."
        ),
        minimum=AUTOMATIC,
        maximum=MAX_MANUAL_WORKERS,
    )
    register_setting(
        key=GENERATION_LIMIT_KEY,
        scope="app",
        default=AUTOMATIC,
        section="Performance",
        label="Previews at the same time",
        automatic_label="Automatic",
        disclosure="Automatic is half the tasks above, so previews never take every task.",
        help=("Generating hover previews and scrubber strips uses the most CPU."),
        minimum=AUTOMATIC,
        maximum=MAX_MANUAL_WORKERS,
    )
    register_setting(
        key=SCAN_LIMIT_KEY,
        scope="app",
        default=AUTOMATIC,
        section="Performance",
        label="Folder scans at the same time",
        automatic_label="Automatic",
        # A LIMIT, and the words say so because the arithmetic does: a typed number is never
        # topped up with an idle share. See `kernel.budget`.
        disclosure=(
            "Automatic lets scans share the tasks above, and use all of them when nothing else is "
            "running. A number is a limit: Sift never runs more scans at the same time than that."
        ),
        help=("Limits scans when they keep a slow disk or a network share too busy to browse."),
        minimum=AUTOMATIC,
        maximum=MAX_MANUAL_WORKERS,
    )
    register_setting(
        key=SHARE_READS_KEY,
        scope="app",
        default=AUTOMATIC,
        section="Performance",
        label="Files read per share",
        automatic_label="Automatic",
        disclosure=(
            "Automatic reads each share at the number the benchmark measured for it, and two from "
            "a share not measured yet. A number here is used for every share instead. A local "
            "drive is never limited by this."
        ),
        help=(
            "Limits how many files Sift opens at the same time from each network share. A share "
            "slows down when too many are read together."
        ),
        minimum=AUTOMATIC,
        maximum=MAX_READS_AT_ONCE,
    )


def _register_import_work() -> None:
    # The two below and the preview shape are drawn on Importing rather than here: they are about
    # what happens to a file on the way in and how new files are noticed, not about how hard the
    # machine works. Registered here because this module owns the numbers behind them.
    register_setting(
        key=GENERATE_FINGERPRINTS_KEY,
        scope="app",
        default=True,
        section="Importing",
        label="Generate fingerprints",
        disclosure=(
            "When off, new files aren't fingerprinted, so they can't be matched on the "
            "stash-boxes or found as duplicates. To catch up, turn this on and choose Generate now."
        ),
        help="Generates the fingerprint that finds duplicates and matches a file on the stash-boxes.",
    )
    remove_setting(
        GENERATE_THUMBNAILS_KEY,
        label="Generate thumbnails",
        why="A thumbnail is made for every file as it arrives; there's nothing left to switch.",
    )
    register_setting(
        key=GENERATE_PREVIEWS_KEY,
        scope="app",
        default=True,
        section="Importing",
        label="Generate hover previews",
        disclosure=(
            "Hover previews use the most CPU, so turning them off saves the most on a busy scan. "
            "Videos still play in full when you open them."
        ),
        help=("Generates the short clip that plays when you hover over a video."),
    )
    register_setting(
        key=PREVIEW_SHAPE_KEY,
        scope="app",
        default=DEFAULT_PREVIEW_SHAPE,
        section="Importing",
        label="Hover preview length",
        choices=[shape.key for shape in PREVIEW_SHAPES],
        choice_labels=tuple(shape.label for shape in PREVIEW_SHAPES),
        disclosure=(
            "Both cut to a new moment every 1.5 seconds. Changing this generates every preview "
            "again in the background."
        ),
        help=(
            "A long video is sampled across its whole length. A short one plays from the beginning."
        ),
    )
    register_setting(
        key=GENERATE_SPRITES_KEY,
        scope="app",
        default=True,
        section="Importing",
        label="Generate scrubber strips",
        disclosure="When off, the scrubber still works but can't show where you are dragging to.",
        help=("Generates the row of frames you see while dragging along a video."),
    )
    register_setting(
        key=REPAIR_PLAYBACK_KEY,
        scope="app",
        default=True,
        section="Importing",
        label="Repair videos that stutter when skipping",
        disclosure=(
            "Sift keeps a corrected copy and plays that instead. Your own file is never changed. "
            "Each copy is a whole second file, so turning this off stops new ones and keeps the "
            "ones already made. To free that space, delete them in Settings > Maintenance."
        ),
        help=(
            "Some files store their sound far from their picture, which makes skipping around "
            "stutter."
        ),
    )


def _register_step_back() -> None:
    register_setting(
        key=STEP_BACK_KEY,
        scope="app",
        default=True,
        section="Performance",
        label="Use less system resources while you're working",
        disclosure=(
            "Sift checks every few seconds. Once nobody has touched the keyboard or mouse for a "
            "minute, it goes back to its usual number of tasks. This works only on Windows."
        ),
        help=(
            "While you're typing or moving the mouse, Sift is in eco mode. Background tasks then use "
            "only a share of this device, so the computer stays quick."
        ),
    )
    register_setting(
        key=BUSY_STEP_BACK_KEY,
        scope="app",
        default=False,
        section="Performance",
        label="Use less system resources while other programs are busy",
        disclosure=(
            "Sift checks every few seconds how busy the CPU and GPU are with other programs, "
            "not counting its own work. Once they have been quiet for a minute, it goes back "
            "to its usual number of tasks. This works only on Windows."
        ),
        help=(
            "While other programs keep this device busy, Sift is in eco mode. Background tasks then "
            "use only a share of this device."
        ),
    )
    register_setting(
        key=STEP_BACK_SHARE_KEY,
        scope="app",
        default=STEP_BACK_SHARE,
        section="Performance",
        minimum=MIN_STEP_BACK_SHARE,
        maximum=100,
        unit="%",
        label="System resource usage in eco mode",
        disclosure=(
            "Every kind of task shares this amount. A task's own share, such as the one for "
            "recognizing faces, is a share of it."
        ),
        help=(
            "How much of this device background tasks use in eco mode. Sift is in eco mode while "
            "you're working or other programs are busy, as the two settings above choose."
        ),
    )


# --- resolvers: the stored number turned into the effective one ------------------------------
# The sentinel is spent here and nowhere else. Each takes the raw stored value (already validated
# and bounded by the registry, so it is a whole number in range) and returns what the machinery
# should actually use. A guard against a wrong-typed value is kept anyway: these are called from
# the pool's live reconfigure, and a value from a restored-from-old-version row that slipped the
# decoder is a wrong number of workers, not a crash.


def resolve_worker_count(raw: object, hardware: HardwareReport) -> int:
    """The number of workers to run: the stored value, or the automatic answer when it is 0.

    Never returns below one. Zero means automatic; a machine with one usable worker still gets one.
    """
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return hardware.worker_concurrency
    return raw


def resolve_step_back_share(raw: object) -> int:
    """The share of the device the step back keeps to, in percent: the stored value, or a quarter
    when there is none that can be read."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < MIN_STEP_BACK_SHARE:
        return STEP_BACK_SHARE
    return min(100, raw)


def resolve_generation_limit(raw: object, concurrency: int) -> int:
    """The cap on preview/sprite building: the stored value, or half the workers when automatic.

    Half the EFFECTIVE workers, not half the hardware default, so raising the worker count raises
    this with it, which is what "about half the jobs above" on the screen promises and what "half the
    workers" has always meant. Never below one, so a single-worker box still builds previews.
    """
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return max(1, concurrency // 2)
    return raw


def resolve_share_reads(raw: object) -> int:
    """The number read at once from every network share, or 0 where each share reads as
    measured (`kernel.lanes`)."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return AUTOMATIC
    return min(raw, MAX_READS_AT_ONCE)


def resolve_scan_limit(raw: object) -> int | None:
    """The cap on concurrent folder scans, or None for no separate cap at all.

    None is not zero (which would mean "never scan"): the limits carry no entry for a scan, so scans
    are bounded only by the worker count.
    """
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return None
    return raw


# --- entitlements: a claimant's share of the machine when everything wants it --------------------
#
# These feed `kernel.budget`, which divides them against whoever actually has work. They are shares
# rather than caps, so they may add up to more than the worker count: the worker count is the real
# ceiling and these only decide who gets what when several long passes are competing for it.
#
# The one exception is a typed scan limit: `scan_limit_is_fixed` says so, and the composition root
# names the scan fixed to the division, which then gives it exactly that number and never hands it
# an idle share. Automatic stays a share.

#: What a folder scan is entitled to when everything is competing: a quarter of the workers.
#:
#: Smaller than the others because a scan walks directories and hands the real work out as child
#: jobs (probing, thumbnailing, describing), so a wide scan mostly means a longer queue sooner.
#: Probing is in the division (`PROBE_SHARE`), and so is thumbnailing (`THUMBNAIL_SHARE`), as
#: work that is counted and never capped.
SCAN_SHARE = 4

#: What describing a library is entitled to: half the workers, the same as recognition's own default.
#: The two are peers: both run a model over every file, both take hours, and neither has a claim
#: on the machine the other lacks.
DESCRIBE_SHARE = 2

#: What probing a file is entitled to: half the workers. A read is one ffprobe and a check of the
#: file's index; the fingerprints are their own job (`FINGERPRINT_SHARE`).
#: Without a share a library's worth of reads could still hold every worker.
#:
#: Half rather than a quarter because until a probe finishes an imported file has no thumbnail,
#: size or duration. A share nobody else is using still goes to whoever wants it, so an
#: import alone on the machine is not slowed, which is why this is a share and not a cap.
PROBE_SHARE = 2

#: What an arriving file's fingerprints are entitled to: half the workers. About 55 ffmpeg decodes
#: per video (thirty frames for the near-duplicate fingerprint, twenty-five stills for the
#: stash-box one), typically tens of seconds and minutes at worst: the heaviest per-file work an
#: import has, which is why a thumbnail waiting cuts it to its share.
FINGERPRINT_SHARE = 2

#: How much room the long passes make for thumbnails while any are waiting: half the workers.
#:
#: Inside the division because a claimant left out of it is nobody's, and the queue is claimed
#: oldest first: a scan hands out every file's probe before any probe has handed out a thumbnail.
#: In the division, a thumbnail waiting cuts probing to its share and the workers it gives up take
#: the pictures of the files already read, in the order they were read. The thumbnail itself is
#: named `waited_on` there, so it is given no cap: nothing is ever held back from it.
THUMBNAIL_SHARE = 2


def scan_limit_is_fixed(raw: object) -> bool:
    """Whether "Folder scans at once" holds a typed number, and so is a limit.

    The label and its help say "never more than this", so a typed number must not be topped up
    with an idle share. The division takes a fixed claimant's number as it stands
    (`kernel.budget.divide`, `fixed=`). Automatic is not fixed: it is the share.
    """
    return resolve_scan_limit(raw) is not None


def resolve_scan_share(raw: object, workers: int) -> int:
    """A folder scan's share of the machine: the typed number, or a quarter of the workers.

    Distinct from `resolve_scan_limit` above, which answers a different question: that one is a
    hard cap and may be absent altogether. This one always answers, because a claimant with no share
    could not take part in a division at all.
    """
    explicit = resolve_scan_limit(raw)
    if explicit is not None:
        return min(explicit, workers)
    return max(1, workers // SCAN_SHARE)


#: What compressing files is entitled to: half the workers, the same as describing a library.
#:
#: A peer of the other long passes rather than a privileged one, and it earns that on the same
#: grounds they do: it is processor-bound and it runs for as long as the files it was given take.
#: It is not the smallest share on the machine because, unlike the others, it is started
#: deliberately by a person who is waiting for the result, and a share nobody else is using goes
#: to whoever is using theirs, so on an idle machine a compression may have most of it and gives it
#: back within a few seconds of a scan arriving.
COMPRESS_SHARE = 2


def resolve_compress_share(workers: int) -> int:
    """How much of the machine compressing files may take when everything is competing.

    No setting of its own, for the reason describing has none (see `resolve_describe_share`).
    """
    return max(1, workers // COMPRESS_SHARE)


def resolve_probe_share(workers: int) -> int:
    """How much of the machine probing files may take when everything is competing.

    No setting of its own, for the reason describing has none (see `resolve_describe_share`).
    """
    return max(1, workers // PROBE_SHARE)


def resolve_fingerprint_share(workers: int) -> int:
    """An arriving file's fingerprints' share of the machine. See `FINGERPRINT_SHARE`."""
    return max(1, workers // FINGERPRINT_SHARE)


def resolve_thumbnail_share(workers: int) -> int:
    """The room the long passes make for thumbnails while any are waiting. See `THUMBNAIL_SHARE`."""
    return max(1, workers // THUMBNAIL_SHARE)


def resolve_describe_share(workers: int) -> int:
    """Describing a library's share of the machine.

    No setting of its own: recognition's share control already answers "how much of this machine
    may the long passes have", and a second slider asking nearly the same question would only
    confuse. If one is ever needed, it goes here and nothing else changes.
    """
    return max(1, workers // DESCRIBE_SHARE)
