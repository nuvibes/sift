# SPDX-License-Identifier: AGPL-3.0-or-later
"""The switches, declared once at import so the settings screen can draw itself from them.

Every one of these is global to the install rather than per-user. Recognizing faces is a
capability the whole instance either has or does not, not a personal preference, and it is
admin-only for the same reason the curation screens are.

The first switch is different from the rest and is written that way on purpose. Nothing in this
feature runs, fetches, or writes anything until somebody turns it on: no model is loaded, no file
is downloaded, no row is written, on any path. It is a consent gate rather than a default that
happens to start off, because the thing it gates is a measurement of people's faces.
"""

from __future__ import annotations

from sift.kernel import sampling
from sift.kernel.ml.runtime import DEVICE_DISCLOSURE, DEVICE_LABELS, DEVICES, device_refusal
from sift.kernel.settings_registry import register_setting, remove_setting
from sift.slices.faces import tuning
from sift.slices.faces.runner import FEATURE
from sift.slices.faces.weights import download_bytes, pairing

ENABLED_KEY = "faces.enabled"
MODEL_KEY = "faces.model"
DEVICE_KEY = "faces.device"
EFFORT_KEY = "faces.effort"
QUALITY_KEY = "faces.minimum_quality"
#: Three removed settings: how sure a match must be to ask, how sure to add the name without
#: asking, and how many samples describe a person. They are `tuning.SUGGEST_CONFIDENCE`,
#: `tuning.AUTO_APPLY_CONFIDENCE` and `tuning.MATCH_GROUPS` now, fixed where they were measured. A
#: stored row for any of them is inert; each is declared removed so History still names it.
REMOVED_SUGGEST_KEY = "faces.match_confidence"
REMOVED_ATTACH_KEY = "faces.attach_confidence"
REMOVED_REFERENCE_GROUPS_KEY = "faces.reference_groups"
FILE_BUDGET_KEY = "faces.file_budget_seconds"
#: A removed setting, "Match again when People change" (`faces.rematch_on_change`): a re-match opens
#: no file and costs a fraction of a second, so every change to the people Sift knows re-matches
#: once. Declared removed so History still names a change made to it.
RETIRED_REMATCH_KEY = "faces.rematch_on_change"
BUDGET_KEY = "faces.machine_budget"
CORE_SHARE_KEY = "faces.core_share"
THREAD_COUNT_KEY = "faces.thread_count"
#: Whether a held entry of facial fingerprints that faces in the library match, and whose faces
#: are nobody's here, becomes a person on its own; off, the group is asked instead. Stored rather
#: than held by the pane, because turning it on also runs the pass for the entries already held
#: (`FACE_PEOPLE_FROM_FILES`, asked by `wiring/reactions.py`). The key is the one the earlier
#: "a person for each name" switch was stored under: on meant "make people from a file" then and
#: means it now, so a stored answer keeps its meaning and no step moves it.
PEOPLE_FROM_FILES_KEY = "faces.people_from_files"

#: Which pair of models to use. `accurate` is measurably better and is licensed for non-commercial
#: research by whoever publishes it; `permissive` is licensed so that anybody may use it for
#: anything. Sift ships neither: both are fetched by the person running it, on their terms.
MODELS = ("accurate", "permissive")

#: What the default family's models cost to download, in the megabytes a person reads: the
#: archives that come down, not the files kept out of them. See `weights.download_bytes`.
_DEFAULT_DOWNLOAD_MB = round(download_bytes(pairing("accurate")) / 1_000_000)

#: Named rather than numeric, because the numbers behind them are meaningless without the
#: measurements that produced them.
EFFORTS = ("fast", "balanced", "deep")
QUALITY_LEVELS = ("strict", "balanced", "lenient")
#: No "idle": "while you are not using Sift" would need the browser to define using it. A stored
#: "idle" reads back as the default, and the hub logs it. No overnight budget either: when
#: recognition runs is its task's When (`tasks.faces.when`, quiet hours).
BUDGETS = ("share", "threads")

#: 0 = let Sift decide, everywhere it appears. The same convention the rest of the app's
#: performance settings use.
AUTOMATIC = 0


def scans_at_once(
    *,
    budget: str,
    core_share: int,
    threads: int,
    workers: int,
) -> int:
    """How many files may be examined for faces at the same time. Zero means not at present.

    **This is what "what share of the processor to use" actually does.** A share of the workers,
    since one scan is single-threaded; rounded down and never below one, since a small share asks
    for slow, not stopped. When it runs is the task's When. Never more than `workers`, the pool
    running NOW, so this share multiplies with the pool's step back.
    """
    if budget == "threads" and threads > 0:
        return max(1, min(threads, workers))
    share = core_share if core_share > 0 else 50
    return max(1, min(workers, (workers * share) // 100))


def register() -> None:
    """Declare the face preferences. Called once, at import, by the slice package. The order is
    the order the screens draw them in."""
    _register_the_switch()
    _register_how_to_look()
    _register_removed_tuning()
    _register_limits_and_names()
    _register_device_use()


def _register_the_switch() -> None:
    """The consent switch and the models it downloads."""
    register_setting(
        key=ENABLED_KEY,
        scope="app",
        default=False,
        section="Identify",
        label="Recognize faces in your library",
        # Long on purpose: the one switch that starts measuring faces names what it downloads
        # and how much, the figure computed from the catalog (the archives that come down).
        disclosure=(
            "Sift ships no face models. When you choose Download the models, Sift downloads the "
            "default set, InsightFace buffalo, from the insightface project on GitHub. The download "
            f"is about {_DEFAULT_DOWNLOAD_MB} MB."
        ),
        help=(
            "Sift finds the faces in your library and groups the ones that look alike, so you name "
            "a person once. Faces never leave this device."
        ),
        # Off until somebody says yes, here on the Identify pane: never being asked decides nothing.
    )
    register_setting(
        key=MODEL_KEY,
        scope="app",
        default="accurate",
        section="Identify",
        label="Recognition models",
        choices=MODELS,
        choice_labels=("Accurate", "Permissive"),
        help=(
            "Accurate finds more faces and suits almost everyone. Choose Permissive only when the "
            "license matters, for example when Sift is part of something you sell."
        ),
        disclosure=(
            "Accurate finds about 98 faces in 100, and its makers license it for research only, "
            "so you decide whether to download it. Permissive finds about 90 in 100, and anyone may "
            "use it for anything. Changing models measures every face again from the crops Sift "
            "kept, and new faces appear after Identify all files again. Facial fingerprints from "
            "one model can't be read by the other, so a swap sends face crops instead."
        ),
    )


def _register_how_to_look() -> None:
    """Where recognition runs, how thoroughly it looks, and the faces it accepts."""
    register_setting(
        key=DEVICE_KEY,
        scope="app",
        default="cpu",
        section="Identify",
        label="Run recognition on",
        choices=DEVICES,
        choice_labels=DEVICE_LABELS,
        # Refused in front of whoever picks it; a `refuse`, not a `validator`, which would also
        # rewrite the stored value on reading it back. See `device_refusal`.
        refuse=device_refusal(FEATURE),
        help="Sift uses the CPU unless you choose a GPU. You can't choose a GPU Sift can't reach.",
        disclosure=DEVICE_DISCLOSURE,
    )
    register_setting(
        key=EFFORT_KEY,
        scope="app",
        default="balanced",
        section="Identify",
        label="How thorough",
        choices=EFFORTS,
        choice_labels=("Fast", "Balanced", "Deep"),
        # Every figure is the sampler's own. "Half" and "three times" are `EFFORT_LEVELS` and
        # `DEEP_FACTOR` below; a test holds both.
        disclosure=(
            "Balanced looks once a second through a clip up to "
            f"{int(sampling.FACE_DENSE_SECONDS)} seconds long. It looks {sampling.FACE_DENSE_MOMENTS} "
            f"to {sampling.MAX_FRAMES} times through a video up to "
            f"{int(sampling.FACE_SPREAD_SECONDS // 60)} minutes long, and {sampling.MAX_FRAMES} "
            "times through anything longer. Fast looks half as often and finishes sooner. Deep "
            f"looks three times as often, up to {sampling.MAX_FACE_FRAMES} times, and finds people "
            "who are on screen only briefly."
        ),
        help="How closely Sift looks through each video for faces. Sift checks a photo one time.",
    )
    register_setting(
        key=QUALITY_KEY,
        scope="app",
        default="balanced",
        section="Identify",
        label="Lowest face quality to accept",
        choices=QUALITY_LEVELS,
        choice_labels=("Strict", "Balanced", "Lenient"),
        disclosure=(
            "Strict skips more blurred and turned faces, and faces under "
            f"{tuning.MIN_PIXELS} pixels across. Balanced and Lenient skip faces under "
            f"{tuning.MIN_PIXELS_ACCEPTED} pixels across. Lenient finds more people and makes more "
            "mistakes."
        ),
        help=(
            "How sharp and how directly facing the camera a face must be. A blurred or turned face "
            "is easily mistaken for someone else."
        ),
    )


def _register_removed_tuning() -> None:
    """The three match settings fixed in `tuning`, declared removed so History names them."""
    remove_setting(
        REMOVED_SUGGEST_KEY,
        label="Ask about a match above",
        why="Where a match is worth asking about is measured, not a taste; nobody could tune it.",
    )
    remove_setting(
        REMOVED_ATTACH_KEY,
        label="Add the name without asking above",
        why="Where a name is added without asking is measured, not a taste; nobody could tune it.",
    )
    remove_setting(
        REMOVED_REFERENCE_GROUPS_KEY,
        label="Face samples per person",
        why="The measured default is right for every library it was tried on.",
    )


def _register_limits_and_names() -> None:
    """The time one file may take, and whether names from files become People."""
    register_setting(
        key=FILE_BUDGET_KEY,
        scope="app",
        default=AUTOMATIC,
        section="Identify",
        minimum=AUTOMATIC,
        maximum=600,
        unit="sec",
        label="Longest time on one file",
        automatic_label="Automatic",
        disclosure="When the time runs out, Sift saves its place and finishes the file later.",
        help="Stops one very long file from holding up the rest.",
    )
    remove_setting(
        RETIRED_REMATCH_KEY,
        label="Match again when People change",
        why="Every change to the named people re-matches once; nothing was left for it to decide.",
    )
    register_setting(
        key=PEOPLE_FROM_FILES_KEY,
        scope="app",
        default=True,
        section="Identify",
        label="Create people from these fingerprints as their faces are recognized",
        help=(
            "When faces in your library match someone in a facial fingerprints file or an "
            "imported folder, Sift adds that person. Turning it on does the same now for the "
            "fingerprints you added before."
        ),
        disclosure=(
            "Sift never goes by the name alone. If the faces already belong to someone here, the "
            "fingerprints are added to that person. When off, each group that looks like someone "
            "in the file waits for your answer. People from a swap wait for you."
        ),
        default_since="0.1.218",
    )


def _register_device_use() -> None:
    """How much of the device recognition uses, filed under Performance on Concurrency's page,
    beside the other answers about how hard Sift may push this device."""
    register_setting(
        key=BUDGET_KEY,
        scope="app",
        default="share",
        section="Performance",
        label="How much recognition uses",
        choices=BUDGETS,
        choice_labels=("Share", "Fixed number"),
        disclosure=(
            "Share uses the share below. Fixed number checks as many files at the same time as you "
            "choose. When recognition runs is set under Tasks."
        ),
        help="How much of this device face recognition uses.",
    )
    register_setting(
        key=CORE_SHARE_KEY,
        scope="app",
        default=50,
        section="Performance",
        minimum=10,
        maximum=100,
        unit="%",
        label="Share of this device to use",
        disclosure=(
            "In eco mode, this is a share of the amount Sift uses then. That amount is set in "
            "Settings > Performance > System resource usage in eco mode."
        ),
        help="Using all of it slows other apps.",
    )
    register_setting(
        key=THREAD_COUNT_KEY,
        scope="app",
        default=AUTOMATIC,
        section="Performance",
        minimum=AUTOMATIC,
        maximum=64,
        label="Files searched at the same time",
        automatic_label="Automatic",
        disclosure="Choose a number only if you have measured this device and want a different one from Sift's.",
        help="Used with Fixed number, whatever else this device is doing.",
    )


# --- Turning the named choices into the numbers the pipeline uses --------------------------------
#
# One place, so a preset means the same thing to the sampler, the quality bar and the tests.


#: What each effort means: the depth a pass runs at, and the multiplier on the moment count. Depth
#: is kept underneath because it has an order: a deeper look at ONE file changes it, and a file
#: scanned deeply is left alone by a lesser sweep. The effort `fast` and the depth `fast` differ:
#: `balanced` also runs at depth `fast`, on all the moments.
EFFORT_LEVELS: dict[str, tuple[str, float]] = {
    "fast": ("fast", 0.5),
    "balanced": ("fast", 1.0),
    "deep": ("deep", 1.0),
}

#: What a deep pass multiplies the moment count by: three, because the moment cap swallows two on
#: the longest files and anything past three on every clip over twenty seconds.
DEEP_FACTOR = 3.0

#: What each quality level does to the three measurements a face is judged on: the fewest pixels
#: across, the least sharpness, and how square-on it has to be (`frontality`).
#:
#: **The pixel floor is 112 on strict and 96 on balanced and lenient**: 112 is the recognizer's own
#: input square, and a sharp, square-on face 96 across survives the stretch (see `tuning`). The real
#: trade is blur and angle: strict keeps only what is sharp and square-on, lenient finds more people
#: and makes more mistakes. A floor costs nothing at scan time, being a rejection taken first.
QUALITY_BARS: dict[str, tuple[int, float, float]] = {
    "strict": (tuning.MIN_PIXELS, 80.0, 0.45),
    "balanced": (tuning.MIN_PIXELS_ACCEPTED, 40.0, 0.30),
    "lenient": (tuning.MIN_PIXELS_ACCEPTED, 15.0, 0.15),
}
