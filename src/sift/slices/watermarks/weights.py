# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two watermark models (a finder and an English-only reader), where to get them, and their
size; none ships."""

from __future__ import annotations

from sift.kernel.config import Settings
from sift.kernel.ml.weights import Weight, WeightError, WeightStore

__all__ = [
    "CATALOG",
    "NAMESPACE",
    "REVISION",
    "Weight",
    "WeightError",
    "download_bytes",
    "store",
    "working_set",
]

#: Its own, so deleting these cannot reach another feature's models.
NAMESPACE = "watermarks"

#: A file read by anything else is stale, so a model change sweeps the library again.
REVISION = "pp-ocrv4-mobile-en"

_BASE = "https://huggingface.co"

CATALOG: dict[str, Weight] = {
    "finder": Weight(
        id="finder",
        role="text finder",
        family="mobile",
        revision=REVISION,
        url=f"{_BASE}/tobiichioriguchi/PP-OCRv4_mobile_det_onnx/resolve/main/inference.onnx",
        digest="d7815a6fad714407a52965555fd2f608e2430df01bff2296bcf0dea1fbf2641b",
        size_bytes=4767934,
        archive_member=None,
        licence="Apache-2.0",
    ),
    "reader": Weight(
        id="reader",
        role="text reader",
        family="mobile",
        revision=REVISION,
        url=f"{_BASE}/tobiichioriguchi/en_PP-OCRv4_mobile_rec_onnx/resolve/main/inference.onnx",
        digest="34b4b0b2eb7466076575eab5bb5ebb66db3387cd589063ec208c402e74324f58",
        size_bytes=7685668,
        archive_member=None,
        licence="Apache-2.0",
    ),
}


def download_bytes() -> int:
    return sum(weight.size_bytes for weight in CATALOG.values())


def working_set() -> tuple[Weight, Weight]:
    try:
        return CATALOG["finder"], CATALOG["reader"]
    except KeyError:  # pragma: no cover - a declaration error, not a runtime one
        raise WeightError("the watermark models are not declared") from None


def store(settings: Settings) -> WeightStore:
    return WeightStore(settings, NAMESPACE)
