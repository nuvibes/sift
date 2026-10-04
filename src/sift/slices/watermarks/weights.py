# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two models that read a watermark, where to get them, and what they cost.

**No model file ships with Sift.** These are obtained by the person running it, when they switch
the feature on, from the publisher. `docs/model-licences.md` records the terms.

Two files, and they do different halves of one job:

- **the finder**, which answers, for every pixel of a strip of a frame, whether text is there,
- **the reader**, which is handed one strip of text and says what it says.

They are a pair in the sense that the second is useless without the first, and they are NOT a
matched pair in the sense the search models are: the finder's answer is a rectangle, not a set of
numbers, so a mismatched pair fails visibly rather than quietly. Both are the publisher's mobile
builds, which is what makes this cheap enough to run over a whole library: twelve megabytes
between them against four hundred for the smallest set search-by-meaning offers.

**The reader is English only, and that is a limit rather than a setting.** It was chosen because
what this feature reads is two fixed site addresses written in Latin letters. A mark in another
script comes back as nothing found, which is the right answer for this feature and would be the
wrong one for a general reader of text in pictures, so nothing here should be reused as one
without replacing this file first.

**One set, not two.** Search-by-meaning offers a choice between a compact and a full set because
the difference buys measurable headroom on a large library. Here it goes the other way: the
mobile pair reads the marks there are to read, and what it misses are files carrying no mark at
all rather than files a larger model would have read. A choice nobody can make well is a choice
not worth offering.
"""

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

#: The corner of the data directory this feature keeps its models in. Its own, so that deleting
#: what this feature downloaded cannot reach what another one did.
NAMESPACE = "watermarks"

#: What read a file, recorded against it. A file read by anything else is stale rather than done,
#: which is what lets a model change sweep the library again without a person asking it to.
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


#: How much comes down the wire when somebody presses the button, so a screen can say so first.
def download_bytes() -> int:
    return sum(weight.size_bytes for weight in CATALOG.values())


def working_set() -> tuple[Weight, Weight]:
    """The finder and the reader, in the order the pass uses them."""
    try:
        return CATALOG["finder"], CATALOG["reader"]
    except KeyError:  # pragma: no cover - a declaration error, not a runtime one
        raise WeightError("the watermark models are not declared") from None


def store(settings: Settings) -> WeightStore:
    """This feature's model files, and everything done to them."""
    return WeightStore(settings, NAMESPACE)
