# SPDX-License-Identifier: AGPL-3.0-or-later
"""The switches, declared once at import so the settings screen can draw itself from them.

Every one is global to the install rather than per-user: describing a library is a capability
the whole instance either has or does not, not a personal preference.

The first switch is different from the rest. Nothing in this feature runs, fetches, or writes
anything until it is on: no model is loaded, no file is downloaded, no row is written, on any
path. It starts off because most libraries do not need it and nobody should pay several hundred
megabytes and a pass over every file for a feature they did not ask for.

**The device is this feature's own**, deliberately mirroring the one recognition has rather than
sharing it. A machine with one graphics card may well want the expensive one-off job on it and the
other feature left on the processor, and a single shared setting cannot express that.
"""

from __future__ import annotations

from sift.kernel.ml.runtime import DEVICE_DISCLOSURE, DEVICE_LABELS, DEVICES, device_refusal
from sift.kernel.settings_registry import register_setting
from sift.slices.semantic.embed import FEATURE
from sift.slices.semantic.weights import SETS

ENABLED_KEY = "semantic.enabled"

#: Whether a file is described as it arrives, rather than only when the library is swept.
#:
#: The counterpart of recognition's `performance.scan_faces_on_import`. Without it describing would
#: be queued for every import with nothing checking whether the feature was on at all: the handler
#: asks and returns, so no work is done, but the job is still written, claimed, run and recorded,
#: once per file, for ever.
#:
#: Retired into the task's When, which reads "As files arrive" unless somebody chose otherwise: it
#: answers WHEN rather than WHETHER, and somebody who has turned Smart Search on wants a library
#: that stays described. The feature switch is what is off out of the box.
DESCRIBE_ON_IMPORT_KEY = "semantic.describe_on_import"
MODEL_KEY = "semantic.model"
DEVICE_KEY = "semantic.device"


def describes_together(*, workers: int, faces_together: int, faces_running: bool) -> int:
    """How many files may be described at the same time.

    **The two expensive passes over a library share one budget, and this is where describing gives
    way.** Recognition has a control (what share of the machine it may use) and describing has
    none, so without this describing would take every worker that was free. On a machine doing
    both, the setting for one of them would quietly decide nothing: recognition would keep to its
    share and describing would fill the rest whether or not anybody had asked it to.

    Describing is the one that yields, and not arbitrarily. Recognition has a share an admin chose
    and can watch working; describing is a single pass that finishes and then never runs again, so
    it is the one where taking longer costs the least. What is left after recognition's share is
    what it gets.

    Never below one. A budget that rounded down to nothing would stop describing rather than slow
    it, and a library part-way through being described would sit unfinished with nothing on any
    screen explaining why.

    When recognition is not running there is nothing to share with and describing gets the whole
    pool: reserving workers for a feature that is switched off would make the machine slower for
    no one.
    """
    if not faces_running:
        return max(1, workers)
    return max(1, workers - faces_together)


def register() -> None:
    register_setting(
        key=ENABLED_KEY,
        scope="app",
        default=False,
        section="Smart Search",
        label="Search using natural language and similarity",
        help=(
            "Search your library by describing what you want, and see files that look alike. A "
            "small AI model runs on this device."
        ),
        # NAMES THE DOWNLOAD. Figures are the sizes in `semantic/weights.py`, rounded; the default
        # set is the compact one, which is what an untouched install fetches. SHORT, the same way
        # recognition's is and for the same reason: the number is what somebody wants before
        # agreeing to a download, and the breakdown, the licence and the alternative set belong on
        # the Smart Search pane where the models are chosen.
        disclosure=(
            "Sift ships no search models. When you choose Download the models, Sift downloads the "
            "default set, SigLIP 2 (base, patch16-224), from the onnx-community repository on "
            "Hugging Face. The download is about 382 MB."
        ),
        # OFF until somebody says yes, here on the Smart Search pane. There is no first-run
        # question for it. See the note in the settings registry.
    )
    # RETIRED into the When of the Smart Search task (`tasks.smart-search.when`). The key stays as
    # the name the import gate and a folder's own answer use; the composition root retires it.
    register_setting(
        key=MODEL_KEY,
        scope="app",
        default="compact",
        section="Smart Search",
        label="Description models",
        choices=SETS,
        choice_labels=("Compact", "Full"),
        help=(
            "Compact is quicker and smaller. Full is more precise on a large library of "
            "similar-looking files."
        ),
        disclosure=(
            "Compact is a 380 MB download and describes a picture in about a tenth of a second. "
            "Full is 1.5 GB and about half again as slow. Both found the right answer in every "
            "test; Full ranks it further ahead, which matters on a large library. Changing models "
            "describes every file again, because the two models' results can't be compared."
        ),
    )
    register_setting(
        key=DEVICE_KEY,
        scope="app",
        default="cpu",
        section="Smart Search",
        label="Run descriptions on",
        choices=DEVICES,
        choice_labels=DEVICE_LABELS,
        # Refused HERE, in front of whoever picks it, rather than hours later inside the job
        # that finally tried to load a model on it. A `refuse` and not a `validator`: a
        # validator also runs when the stored value is read back, and this one would rewrite
        # it. See `device_refusal`.
        refuse=device_refusal(FEATURE),
        help="Sift uses the CPU unless you choose a GPU. You can't choose a GPU Sift can't reach.",
        disclosure=DEVICE_DISCLOSURE,
    )
