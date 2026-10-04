# SPDX-License-Identifier: AGPL-3.0-or-later
"""The switches, declared once at import so the settings screen can draw itself from them.

Both are global to the install rather than per-user: reading the library's watermarks is a
capability the whole instance either has or does not, not a personal preference.

The first switch is different from the second. Nothing in this feature runs, fetches, or writes
anything until it is on: no model is loaded, no file is downloaded, no row is written, on any
path. It starts off because it is a pass over every file in the library and nobody should pay for
one they did not ask for.

**The device is this feature's own**, deliberately mirroring the two that already exist rather
than sharing them. A machine with one graphics card may well want one of the three long passes on
it and the others left on the processor, and a single shared setting cannot express that.

**When new files are read is the watermark task's When** (`tasks.watermarks.when`), as for the
other two passes, and it reads them as they arrive unless somebody chose otherwise. Without that,
an install with the feature on and the models fetched would read nothing until somebody pressed
Run now, and a library that only tells the truth after a button is pressed is what the When exists
to stop. The cost per file is real (the pass opens the file and looks at the picture), which is why
the switch in front of it starts off.
"""

from __future__ import annotations

from sift.kernel.ml.runtime import DEVICE_DISCLOSURE, DEVICE_LABELS, DEVICES, device_refusal
from sift.kernel.settings_registry import register_setting
from sift.slices.watermarks.reader import FEATURE
from sift.slices.watermarks.weights import download_bytes

ENABLED_KEY = "watermarks.enabled"

#: Whether a file is read for a mark as it arrives, rather than only when the library is swept.
#:
#: Retired into the task's When, like its counterparts for the other two passes. It answers WHEN
#: rather than WHETHER: `ENABLED_KEY` in front of it is what decides whether this feature exists at
#: all.
READ_ON_IMPORT_KEY = "watermarks.read_on_import"
DEVICE_KEY = "watermarks.device"

#: The download, in whole megabytes, for the sentence somebody reads before agreeing to it.
_MEGABYTES = round(download_bytes() / 1_000_000)


def register() -> None:
    register_setting(
        key=ENABLED_KEY,
        scope="app",
        default=False,
        section="Watermarks",
        label="Scan files for Site watermarks",
        help=(
            "Finds a Site's watermark printed on a file and adds that Site to the file. A "
            "watermark shows where a copy came from, never who is in it."
        ),
        # NAMES THE DOWNLOAD, the way the other two passes do. The figure is the sum of the sizes
        # in `weights.py`, rounded, so it cannot drift away from what is actually fetched.
        disclosure=(
            "Sift ships no watermark models. When you choose Download the models, Sift downloads "
            "the PP-OCRv4 mobile pair, published by Baidu as part of PaddleOCR, from Hugging Face. "
            f"The download is about {_MEGABYTES} MB. The models recognize the Latin alphabet only, "
            "so a watermark in another script isn't found."
        ),
    )
    # RETIRED into the When of the watermarks task (`tasks.watermarks.when`). The key stays as the
    # name the import gate and a folder's own answer use; the composition root retires it.
    register_setting(
        key=DEVICE_KEY,
        scope="app",
        default="cpu",
        section="Watermarks",
        label="Run watermark scans on",
        choices=DEVICES,
        choice_labels=DEVICE_LABELS,
        # Refused HERE, in front of whoever picks it, rather than hours later inside the job that
        # finally tried to load a model on it. A `refuse` and not a `validator`: a validator also
        # runs when the stored value is read back, and this one would rewrite it.
        refuse=device_refusal(FEATURE),
        help=(
            "A GPU makes a scan about a fifth faster at most, because most of the time goes to "
            "opening files."
        ),
        disclosure=DEVICE_DISCLOSURE,
    )
