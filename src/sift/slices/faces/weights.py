# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face models Sift can use, where to get them, and what their licences say.

No model file ships with Sift: the most accurate are for non-commercial research only, so the
person running it fetches their own (`docs/model-licences.md`). Fetching, resuming and verifying
are the kernel's; this names the models and where they are kept.
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

#: This feature's corner of the data directory, so deleting its models reaches nothing else.
NAMESPACE = "faces"


#: Everything Sift can fetch for a face pass; each row's licence is read and recorded first.
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
        # The whole buffalo_s.zip comes down for its one file: the release's Content-Length.
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


#: An older spelling of the recognizer's id, whose file would linger as a second copy.
MISSPELT_RECOGNIZER = "accurate.recogniser"


def retire_misspelt(settings: Settings) -> bool:
    """Remove the recognizer file left under its old spelling, once the current one verifies.
    Returns whether a file went."""
    current = CATALOG["accurate.recognizer"]
    stale = store(settings).directory() / f"{MISSPELT_RECOGNIZER}{current.suffix}"
    if not stale.is_file():
        return False
    try:
        verify(settings, current)
    except WeightError:
        return False
    stale.unlink()  # nosemgrep: sift-no-file-removal-outside-delete-trash
    return True


def download_bytes(wanted: Iterable[Weight]) -> int:
    """How much comes down to fetch these: the archive where there is one, else the file."""
    return sum(weight.archive_bytes or weight.size_bytes for weight in wanted)


def pairing(family: str) -> tuple[Weight, Weight]:
    """The detector and recognizer of one family, in that order: mixed, matching degrades."""
    try:
        return CATALOG[f"{family}.detector"], CATALOG[f"{family}.recognizer"]
    except KeyError:
        raise WeightError(f"there is no set of models called {family!r}") from None


def store(settings: Settings) -> WeightStore:
    """This feature's model files, and everything done to them."""
    return WeightStore(settings, NAMESPACE)


def directory(settings: Settings) -> Path:
    """This feature's corner of the device's model store (`WeightStore.directory`)."""
    return store(settings).directory()


def path_of(settings: Settings, weight: Weight) -> Path:
    return store(settings).path_of(weight)


def installed(settings: Settings, weight: Weight) -> bool:
    """Whether the file is there; `verify` says whether it is the right one."""
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
