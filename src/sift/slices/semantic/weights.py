# SPDX-License-Identifier: AGPL-3.0-or-later
"""The model sets that describe pictures and words, fetched by the user and never shipped."""

from __future__ import annotations

from sift.kernel.config import Settings
from sift.kernel.ml.weights import Weight, WeightError, WeightStore

__all__ = [
    "CATALOG",
    "NAMESPACE",
    "SETS",
    "Weight",
    "WeightError",
    "store",
    "working_set",
]

#: Its own corner, so deleting these models cannot reach another feature's.
NAMESPACE = "semantic"

#: Every file is pinned by digest, so a moved repository fails loudly.
_BASE = "https://huggingface.co/onnx-community/siglip2-base-patch16-224-ONNX/resolve/main"

#: Both sets read the same vocabulary, fetched once and verified like everything else.
_VOCABULARY = Weight(
    id="vocabulary",
    role="vocabulary",
    family="shared",
    revision="siglip2-base-patch16-224",
    url=f"{_BASE}/tokenizer.model",
    digest="83e9cb746b1847bab2a6295b8024404ad6593b60968740ae3a7cccb53386d678",
    size_bytes=4241003,
    archive_member=None,
    licence="Apache-2.0",
    suffix=".model",
)

CATALOG: dict[str, Weight] = {
    "vocabulary": _VOCABULARY,
    "compact.pictures": Weight(
        id="compact.pictures",
        role="picture reader",
        family="compact",
        revision="siglip2-base-patch16-224-quantized",
        url=f"{_BASE}/onnx/vision_model_quantized.onnx",
        digest="b4bbf7e25fdeca342a1b72d77fb5fdba324d3065a3d1a2f6d0782e5a1c1652bb",
        size_bytes=94553333,
        archive_member=None,
        licence="Apache-2.0",
        dimension=768,
    ),
    "compact.words": Weight(
        id="compact.words",
        role="word reader",
        family="compact",
        revision="siglip2-base-patch16-224-quantized",
        url=f"{_BASE}/onnx/text_model_quantized.onnx",
        digest="a5c20b92a1217d4001c0f499e48e57c7f1f95eec59510dea87d62953b4008833",
        size_bytes=283438275,
        archive_member=None,
        licence="Apache-2.0",
        dimension=768,
    ),
    "full.pictures": Weight(
        id="full.pictures",
        role="picture reader",
        family="full",
        revision="siglip2-base-patch16-224",
        url=f"{_BASE}/onnx/vision_model.onnx",
        digest="2bc84d1b801ca6796f8a050445eb251a3c4c87a3231803342834250e6131856b",
        size_bytes=371807752,
        archive_member=None,
        licence="Apache-2.0",
        dimension=768,
    ),
    "full.words": Weight(
        id="full.words",
        role="word reader",
        family="full",
        revision="siglip2-base-patch16-224",
        url=f"{_BASE}/onnx/text_model.onnx",
        digest="e06ed429969c27fcf5ab8771e69949be340fe10e004c9eea9e2735d8d06ed1bc",
        size_bytes=1129469657,
        archive_member=None,
        licence="Apache-2.0",
        dimension=768,
    ),
}

#: `compact` is preselected: as accurate on everything compared, a third of the download.
SETS = ("compact", "full")


def working_set(family: str) -> tuple[Weight, Weight, Weight]:
    """The picture reader, the word reader and the vocabulary of one set, in that order."""
    try:
        return CATALOG[f"{family}.pictures"], CATALOG[f"{family}.words"], _VOCABULARY
    except KeyError:
        raise WeightError(f"there is no set of models called {family!r}") from None


def store(settings: Settings) -> WeightStore:
    """This feature's model files, and everything done to them."""
    return WeightStore(settings, NAMESPACE)
