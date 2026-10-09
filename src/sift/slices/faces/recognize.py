# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning one aligned face into unit-length numbers compared by one dot product; each family
wants its pixels arranged its own way, and the wrong way quietly matches nothing."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from sift.slices.faces.models import Description, Vector
from sift.slices.faces.runner import Loaded, RunnerLike

#: How each family wants its pixels: whether to centre them around zero, and by how much.
_CENTRED = {"accurate": True, "permissive": False}

#: The ramp turning a family's raw output length into a 0-to-1 term; it ranks, never refuses.
_STRENGTH_RAMP = {"accurate": (14.0, 21.0)}


class Recognizer:
    """One loaded recognizer, and the arrangement its family expects."""

    def __init__(self, runner: RunnerLike, loaded: Loaded) -> None:
        self._runner = runner
        self._loaded = loaded
        try:
            self._centre = _CENTRED[loaded.weight.family]
        except KeyError:
            raise ValueError(f"no recognizer reader for {loaded.weight.family!r}") from None

    @property
    def revision(self) -> str:
        return self._loaded.weight.revision

    @property
    def dimension(self) -> int:
        return self._loaded.weight.dimension

    def embed(self, chip: np.ndarray) -> Description:
        """The numbers describing one aligned face, and how firmly the model answered."""
        return self.embed_many([chip])[0]

    def embed_many(self, chips: Sequence[np.ndarray]) -> list[Description]:
        """Every aligned face of one file, described in one batched run of the model."""
        if not chips:
            return []
        pixels = np.stack([np.asarray(chip, dtype=np.float32) for chip in chips])
        if self._centre:
            pixels = (pixels - 127.5) / 127.5
        blob = np.ascontiguousarray(pixels.transpose(0, 3, 1, 2))
        answered = np.asarray(self._runner.run(self._loaded, blob)[0])
        if answered.shape[0] != len(chips):
            raise ValueError(
                f"the {self._loaded.weight.family} recognizer answered {answered.shape[0]} "
                f"descriptions for {len(chips)} faces, so it does not read a batch"
            )
        raw = answered.reshape(len(chips), -1)
        return [
            Description(vector=tuple(normalise(one).tolist()), strength=float(np.linalg.norm(one)))
            for one in raw
        ]

    def recognisability(self, strength: float) -> float:
        """One raw length as a 0-to-1 term for the quality score; 1.0 for an uncalibrated family."""
        return recognisability(strength, _STRENGTH_RAMP.get(self._loaded.weight.family))


def recognisability(strength: float, ramp: tuple[float, float] | None) -> float:
    """The ramp itself, apart from the class so a stand-in recognizer applies the same rule."""
    if ramp is None or strength <= 0:
        return 1.0
    floor, ceiling = ramp
    return float(min(1.0, max(0.0, (strength - floor) / (ceiling - floor))))


def normalise(vector: np.ndarray) -> np.ndarray:
    """Scale to unit length; all zeros is handed back unchanged, matching nothing."""
    length = float(np.linalg.norm(vector))
    return vector / length if length > 0 else vector


def similarity(first: Vector, second: Vector) -> float:
    """How alike two faces are, from -1 to 1. Both are unit length, so this is their dot product."""
    if len(first) != len(second):
        raise ValueError("two faces described by different numbers of values cannot be compared")
    return float(np.dot(np.asarray(first, dtype=np.float32), np.asarray(second, dtype=np.float32)))


def pack(vector: Vector) -> bytes:
    """A vector as bytes for storage: little-endian four-byte floats."""
    return np.asarray(vector, dtype="<f4").tobytes()


def unpack(raw: bytes) -> Vector:
    """A stored vector, back."""
    return tuple(np.frombuffer(raw, dtype="<f4").tolist())
