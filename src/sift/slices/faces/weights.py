# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face models Sift can use, where to get them, and what their licences say.

**No model file ships with Sift**, in the source or in the image, and that is a licence
requirement rather than a size decision: the most accurate face models available are published
for non-commercial research only, and putting them inside a public image would be redistributing
them under terms nobody granted. So Sift describes them, and the person running it obtains their
own copy when they switch the feature on. `docs/model-licences.md` records the terms of each, read
from the publisher's own files.

**What is left here is the part that is about faces**: which models exist, which of them belong
together, and where this feature keeps them. Obtaining a file, resuming a download that dropped,
proving what arrived is what was described, and taking one file out of an archive are not about
faces at all: another feature needs the same machinery and may not import this one, so it is
the kernel's and this asks the kernel for it. The functions below keep their names for every
caller and test.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.ml.weights import (
    Progress,
    SessionFactory,
    Weight,
    WeightError,
    WeightStore,
    digest_of,
)

__all__ = [
    "CATALOG",
    "Progress",
    "SessionFactory",
    "Weight",
    "WeightError",
    "digest_of",
    "directory",
    "fetch",
    "install_from_file",
    "installed",
    "pairing",
    "path_of",
    "store",
    "verify",
]

#: The corner of the data directory this feature keeps its models in. Naming it here, once, is
#: what makes "switch faces off and delete its models" a thing that cannot reach anything else.
NAMESPACE = "faces"


#: Everything Sift can fetch for a face pass. Adding a row here means opening that file's actual
#: licence, reading it, and recording it in `docs/model-licences.md`: vendor documentation is not
#: a licence.
CATALOG: dict[str, Weight] = {
    "accurate.detector": Weight(
        id="accurate.detector",
        role="detector",
        family="accurate",
        revision="scrfd-500m-bnkps",
        url="https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip",
        digest="395b30439ed1773f1fde53b7ffe1e9e5d13119811c89be0cfe3241305ea03604",
        size_bytes=2524817,
        archive_member="det_500m.onnx",
        # The whole of buffalo_s.zip comes down for the one file in it; the release's
        # Content-Length.
        archive_bytes=127607557,
        licence="Non-commercial research only",
    ),
    "accurate.recognizer": Weight(
        id="accurate.recognizer",
        role="recognizer",
        family="accurate",
        revision="w600k-r50",
        url="https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
        digest="c9cc033a308d5cbe0006b8f2d695f13fe716c985cc4b676ad5c0a20a497a07cc",
        size_bytes=174383860,
        archive_member="w600k_r50.onnx",
        # buffalo_l.zip, likewise.
        archive_bytes=288621354,
        licence="Non-commercial research only",
        dimension=512,
    ),
    "permissive.detector": Weight(
        id="permissive.detector",
        role="detector",
        family="permissive",
        revision="yunet-2026may",
        url=(
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
            "face_detection_yunet_2026may.onnx"
        ),
        digest="226e749f6dba5390a1b1415d8177c54d2d74328824b111cdc4a136416c6de365",
        size_bytes=229738,
        archive_member=None,
        licence="MIT",
    ),
    "permissive.recognizer": Weight(
        id="permissive.recognizer",
        role="recognizer",
        family="permissive",
        revision="arcface-resnet100-8",
        url=(
            "https://media.githubusercontent.com/media/onnx/models/main/validated/vision/"
            "body_analysis/arcface/model/arcfaceresnet100-8.onnx"
        ),
        digest="5e998cd7df2e911f9b170bb1b199af6bc9125bd1ac72f7c7fe95326d0adf00ac",
        size_bytes=261036388,
        archive_member=None,
        licence="Apache-2.0",
        dimension=512,
    ),
}


#: An older spelling of the recognizer's id. A rename of it moves every column but neither the
#: file nor the row that named it, so an install carrying it holds a second 174 MB copy nothing
#: reads.
MISSPELT_RECOGNIZER = "accurate.recogniser"


def retire_misspelt(settings: Settings) -> bool:
    """Remove the recognizer file left under its old spelling, once the current one verifies.

    Only then: the old file is the same bytes, and until the new name's copy is proved intact it
    is the one thing that could put the library back. Returns whether a file went.
    """
    current = CATALOG["accurate.recognizer"]
    stale = store(settings).directory() / f"{MISSPELT_RECOGNIZER}{current.suffix}"
    if not stale.is_file():
        return False
    try:
        verify(settings, current)
    except WeightError:
        return False
    # Sift's own model file, in Sift's own data directory, replaced by the same bytes under the
    # spelling the code uses; nothing anybody made is lost.
    stale.unlink()  # nosemgrep: sift-no-file-removal-outside-delete-trash
    return True


def download_bytes(wanted: Iterable[Weight]) -> int:
    """How much comes down to fetch these: the archive where there is one, else the file.

    The number the consent switch states and the number a progress bar is drawn against. Not
    `size_bytes`: for the default family that is 177 MB of models inside 416 MB of archives, so a
    bar drawn against it would reach 100% with 165 MB still to come and the switch would understate
    the download by more than half.
    """
    return sum(weight.archive_bytes or weight.size_bytes for weight in wanted)


def pairing(family: str) -> tuple[Weight, Weight]:
    """The detector and recognizer of one family, in that order.

    They have to come from the same one: their idea of where a face's features sit has to agree,
    and mixing them measurably degrades matching even though nothing errors.
    """
    try:
        return CATALOG[f"{family}.detector"], CATALOG[f"{family}.recognizer"]
    except KeyError:
        raise WeightError(f"there is no set of models called {family!r}") from None


def store(settings: Settings) -> WeightStore:
    """This feature's model files, and everything done to them."""
    return WeightStore(settings, NAMESPACE)


def directory(settings: Settings) -> Path:
    """Where face model files live: this feature's corner of the device's model store, which a
    cache clean never touches (`WeightStore.directory`)."""
    return store(settings).directory()


def path_of(settings: Settings, weight: Weight) -> Path:
    return store(settings).path_of(weight)


def installed(settings: Settings, weight: Weight) -> bool:
    """Whether the file is there. Says nothing about whether it is the right one: `verify` does
    that, and it reads the whole file, so the two questions are kept apart."""
    return store(settings).installed(weight)


def verify(settings: Settings, weight: Weight) -> None:
    """Prove an installed file is the model it claims to be. Raises with a readable reason."""
    store(settings).verify(weight)


async def install_from_file(settings: Settings, weight: Weight, source: Path) -> None:
    """Take a face model from a file the operator already has."""
    await store(settings).install_from_file(weight, source)


async def fetch(
    settings: Settings,
    weight: Weight,
    *,
    session_factory: SessionFactory | None = None,
    progress: Progress | None = None,
    fresh: bool = False,
) -> None:
    """Download a face model, resuming a previous attempt unless `fresh` says start again."""
    await store(settings).fetch(
        weight, session_factory=session_factory, progress=progress, fresh=fresh
    )
