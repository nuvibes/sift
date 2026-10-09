# SPDX-License-Identifier: AGPL-3.0-or-later
"""How hard Sift may work the machine, as preferences; 0 is automatic throughout."""

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

#: Past the automatic cap for a big machine, while an absurd value is still refused at the door.
MAX_MANUAL_WORKERS = 64

#: Each poll walks the folder: too fast hammers a slow share, too slow makes a new file feel lost.
MIN_POLL_SECONDS = 1

AUTOMATIC = 0

WORKER_COUNT_KEY = "performance.worker_count"
GENERATION_LIMIT_KEY = "performance.generation_limit"
SCAN_LIMIT_KEY = "performance.scan_limit"

#: Per-derivative switches; a rescan fills in what was skipped while one was off.
GENERATE_THUMBNAILS_KEY = "performance.generate_thumbnails"
GENERATE_PREVIEWS_KEY = "performance.generate_previews"

#: The kernel's, since three features must agree (`kernel.sampling`); re-exported as Performance's.
PREVIEW_SHAPE_KEY = PREVIEW_SHAPE_SETTING
GENERATE_SPRITES_KEY = "performance.generate_sprites"
GENERATE_FINGERPRINTS_KEY = "performance.generate_fingerprints"
SCAN_FACES_ON_IMPORT_KEY = "performance.scan_faces_on_import"

SHARE_READS_KEY = "performance.share_reads_at_once"

#: Removed, and declared so, so History lines about it still name it.
TUNE_PROMPT_REMOVED_KEY = "performance.tune_prompt"
REPAIR_PLAYBACK_KEY = "performance.repair_playback"

STEP_BACK_KEY = "performance.step_back_while_used"

#: Off until the device-load reading is measured accurate; it logs what it would do either way.
BUSY_STEP_BACK_KEY = "performance.step_back_while_busy"

#: Bounds workers and tool threads together (`kernel.budget`).
STEP_BACK_SHARE_KEY = "performance.step_back_share"

#: Below a tenth the pool is one worker, which the step back never goes under anyway.
MIN_STEP_BACK_SHARE = 10


def register() -> None:
    """Declare the performance preferences, once, at import, in the package's order."""
    remove_setting(
        TUNE_PROMPT_REMOVED_KEY,
        label="Offer to measure this device",
        why="It decided one field of the self-test's answer that no screen reads.",
    )
    _register_counts()
    _register_import_work()
    _register_step_back()
    # Retired into `tasks.faces.when`; the key stays as the name the import gate reads.


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
        # A limit, never topped up with an idle share (`kernel.budget`).
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
    # Drawn on Importing rather than here, but this module owns the numbers.
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
        why="A thumbnail is created for every file as it's imported; there's nothing left to switch.",
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
            "Sift checks every few seconds. After a minute with no keyboard, mouse or video playing in "
            "Sift, it goes back to its usual number of tasks. Reading the keyboard and mouse works "
            "only on Windows."
        ),
        help=(
            "While you're typing or moving the mouse, or a video is playing in Sift, Sift is in eco mode. "
            "Background tasks then use "
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
            "you're working, a video is playing or other programs are busy, as the two settings above choose."
        ),
    )


# Resolvers: the 0 sentinel is spent here only, guarded against a wrong-typed restored value.


def resolve_worker_count(raw: object, hardware: HardwareReport) -> int:
    """The number of workers to run, the automatic answer for 0, never below one."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return hardware.worker_concurrency
    return raw


def resolve_step_back_share(raw: object) -> int:
    """The share of the device the step back keeps to, in percent; a quarter by default."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < MIN_STEP_BACK_SHARE:
        return STEP_BACK_SHARE
    return min(100, raw)


def resolve_generation_limit(raw: object, concurrency: int) -> int:
    """The cap on preview and sprite building: the stored value, or half the effective workers."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return max(1, concurrency // 2)
    return raw


def resolve_share_reads(raw: object) -> int:
    """The number read together from every network share, or 0 for each share as measured."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return AUTOMATIC
    return min(raw, MAX_READS_AT_ONCE)


def resolve_scan_limit(raw: object) -> int | None:
    """The cap on concurrent folder scans, or None for no separate cap (zero would mean never)."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return None
    return raw


# Entitlements: shares for `kernel.budget` to divide, not caps; a typed scan limit is fixed.

#: A folder scan mostly hands work out as child jobs, so a quarter of the workers.
SCAN_SHARE = 4

#: Describing is a peer of recognition: half the workers each.
DESCRIBE_SHARE = 2

#: Half: until a probe finishes an imported file has no thumbnail, size or duration.
PROBE_SHARE = 2

#: Fingerprints are the heaviest per-file work an import has: half, cut while a thumbnail waits.
FINGERPRINT_SHARE = 2

#: Thumbnails are in the division, so a waiting one cuts the long passes to their share.
THUMBNAIL_SHARE = 2


def scan_limit_is_fixed(raw: object) -> bool:
    """Whether "Folder scans at the same time" holds a typed number, and so is a limit."""
    return resolve_scan_limit(raw) is not None


def resolve_scan_share(raw: object, workers: int) -> int:
    """A folder scan's share of the machine: the typed number, or a quarter of the workers."""
    explicit = resolve_scan_limit(raw)
    if explicit is not None:
        return min(explicit, workers)
    return max(1, workers // SCAN_SHARE)


#: Compression: half, a peer of the other long passes, though a person is waiting on it.
COMPRESS_SHARE = 2


def resolve_compress_share(workers: int) -> int:
    """How much of the machine compressing files may take when everything is competing."""
    return max(1, workers // COMPRESS_SHARE)


def resolve_probe_share(workers: int) -> int:
    """How much of the machine probing files may take when everything is competing."""
    return max(1, workers // PROBE_SHARE)


def resolve_fingerprint_share(workers: int) -> int:
    """An arriving file's fingerprints' share of the machine. See `FINGERPRINT_SHARE`."""
    return max(1, workers // FINGERPRINT_SHARE)


def resolve_thumbnail_share(workers: int) -> int:
    """The room the long passes make for thumbnails while any are waiting. See `THUMBNAIL_SHARE`."""
    return max(1, workers // THUMBNAIL_SHARE)


def resolve_describe_share(workers: int) -> int:
    """Describing a library's share of the machine; recognition's control already answers it."""
    return max(1, workers // DESCRIBE_SHARE)
