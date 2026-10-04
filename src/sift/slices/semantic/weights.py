# SPDX-License-Identifier: AGPL-3.0-or-later
"""The models that describe pictures and words, where to get them, and what they cost.

**No model file ships with Sift.** These are obtained by the person running it, when they switch
the feature on, from the publisher. `docs/model-licences.md` records the terms, read from the
publisher's own files rather than from documentation about them.

Three files make one working set, and they are not independent:

- **the picture reader**, which turns a frame into numbers,
- **the word reader**, which turns a typed sentence into numbers *in the same space*,
- **the vocabulary**, which is how a sentence becomes the symbols the word reader expects.

Mixing versions of these does not fail. It returns numbers that mean nothing in relation to each
other, so searching quietly returns wrong answers, which is why they travel as a set, and why the
set's name is recorded against every file that gets described.

**Two sets, and the difference is size against margin.** On ten photographs and ten plain-language
captions, both matched every caption to its own picture, in both directions, and neither was ever
beaten by nonsense text. Where they differ is how far ahead the right answer sat: the compressed
set's lead over the runner-up was roughly half the full set's. On easy pictures that changes
nothing. On a large, similar-looking library it is headroom, and it is the reason the full set is
offered at all.

The word reader is far larger than the picture reader, which is surprising until you see why: it
carries a vocabulary of 256,000 words, and that table is most of the file. It is also the half that
runs once per search rather than once per frame, so its size costs download and memory, never
speed.
"""

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

#: The corner of the data directory this feature keeps its models in. Its own, so that deleting
#: what this feature downloaded cannot reach what another one did.
NAMESPACE = "semantic"

#: The publisher's repository. Named once: every file below comes from the same place, and the one
#: thing worth knowing about it is in `docs/model-licences.md`: the publisher describes it as a
#: temporary home. Sift pins every file by digest, so if it moves the fetch fails loudly rather
#: than quietly installing something else.
_BASE = "https://huggingface.co/onnx-community/siglip2-base-patch16-224-ONNX/resolve/main"

#: Both sets read the same vocabulary, because they are the same model at two precisions. Fetched
#: once and shared, and named as its own entry so it is verified like everything else.
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

#: The sets somebody can choose between. `compact` is preselected: it was as accurate as the other
#: on everything compared, is a third of the download, and describes a frame in two thirds of the
#: time.
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
