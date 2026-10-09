# SPDX-License-Identifier: AGPL-3.0-or-later
"""The watermark switches, global to the install; off by default, since it is a pass over every
file."""

from __future__ import annotations

from sift.kernel.ml.runtime import DEVICE_DISCLOSURE, DEVICE_LABELS, DEVICES, device_refusal
from sift.kernel.settings_registry import register_setting
from sift.slices.watermarks.reader import FEATURE
from sift.slices.watermarks.weights import download_bytes

ENABLED_KEY = "watermarks.enabled"

#: Retired into the task's When; `ENABLED_KEY` decides whether the feature exists.
READ_ON_IMPORT_KEY = "watermarks.read_on_import"
DEVICE_KEY = "watermarks.device"

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
        # Summed from `weights.py`, so it cannot drift from what is fetched.
        disclosure=(
            "Sift ships no watermark models. When you choose Download the models, Sift downloads "
            "the PP-OCRv4 mobile pair, published by Baidu as part of PaddleOCR, from Hugging Face. "
            f"The download is about {_MEGABYTES} MB. The models recognize the Latin alphabet only, "
            "so a watermark in another script isn't found."
        ),
    )
    register_setting(
        key=DEVICE_KEY,
        scope="app",
        default="cpu",
        section="Watermarks",
        label="Run watermark scans on",
        choices=DEVICES,
        choice_labels=DEVICE_LABELS,
        # Refused when picked, not inside a job; a validator would also rewrite the stored value.
        refuse=device_refusal(FEATURE),
        help=(
            "A GPU makes a scan about a fifth faster at most, because most of the time goes to "
            "opening files."
        ),
        disclosure=DEVICE_DISCLOSURE,
    )
