# SPDX-License-Identifier: AGPL-3.0-or-later
"""The switches, declared once at import; install-wide, and nothing runs until the first is on."""

from __future__ import annotations

from sift.kernel.ml.runtime import DEVICE_DISCLOSURE, DEVICE_LABELS, DEVICES, device_refusal
from sift.kernel.settings_registry import register_setting
from sift.slices.semantic.embed import FEATURE
from sift.slices.semantic.weights import SETS

ENABLED_KEY = "semantic.enabled"

#: Retired into the task's When; without the check every import would queue a no-op job.
DESCRIBE_ON_IMPORT_KEY = "semantic.describe_on_import"
MODEL_KEY = "semantic.model"
DEVICE_KEY = "semantic.device"


def describes_together(*, workers: int, faces_together: int, faces_running: bool) -> int:
    """How many files may be described together: what recognition's share leaves, at least one."""
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
        # Names the download size, short: the breakdown and licence belong on the Smart Search pane.
        disclosure=(
            "Sift ships no search models. When you choose Download the models, Sift downloads the "
            "default set, SigLIP 2 (base, patch16-224), from the onnx-community repository on "
            "Hugging Face. The download is about 382 MB."
        ),
        # Off until somebody says yes on the Smart Search pane; there is no first-run question.
    )
    # Retired into `tasks.smart-search.when`; the key stays as the name the import gate reads.
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
        # Refused here, not hours later in the job: a validator would rewrite the stored value.
        refuse=device_refusal(FEATURE),
        help="Sift uses the CPU unless you choose a GPU. You can't choose a GPU Sift can't reach.",
        disclosure=DEVICE_DISCLOSURE,
    )
