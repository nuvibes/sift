# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a picture, and a typed sentence, into numbers in one shared space."""

from __future__ import annotations

import asyncio
import string
from collections.abc import Sequence

import numpy as np

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.ml.child import ChildRunner, ChildStopped, runtime_wont_start
from sift.kernel.ml.runtime import DeviceUnavailable, Loaded, Runner
from sift.slices.semantic import weights

FEATURE = "Search by meaning"

FRAME_SIZE = 224

MAX_SYMBOLS = 64

_PADDING = 0

#: ASCII punctuation only, exactly what the publisher's preparation removes.
_PUNCTUATION = str.maketrans("", "", string.punctuation)

#: Named, not by position: the FIRST output is per patch or per word, a confidently wrong shape.
_POOLED = "pooler_output"


def canonical(text: str) -> str:
    """A typed query as the word model was trained on: lower case, no punctuation, one space."""
    return " ".join(text.translate(_PUNCTUATION).lower().split())


def prepare(frame: np.ndarray) -> np.ndarray:
    """One decoded picture (height by width by colour bytes) as the model's batch of one."""
    if frame.shape != (FRAME_SIZE, FRAME_SIZE, 3):
        raise ValueError(
            f"a picture for this model has to be {FRAME_SIZE} by {FRAME_SIZE} in colour, "
            f"and this one is {frame.shape}"
        )
    pixels = frame.astype(np.float32) / 255.0
    pixels = (pixels - 0.5) / 0.5
    return np.transpose(pixels, (2, 0, 1))[None, ...].copy()


def to_unit_length(values: np.ndarray) -> list[float]:
    """Scaled to unit length so two vectors compare by direction; all zeros come back as is."""
    length = float(np.linalg.norm(values))
    if length == 0.0:
        return [float(value) for value in values]
    return [float(value) for value in values / length]


class Embedder:
    """The two models and the vocabulary, loaded on first use and kept."""

    def __init__(
        self,
        settings: Settings,
        hardware: HardwareReport,
        *,
        family: str = "compact",
        device: str = "cpu",
    ) -> None:
        self._settings = settings
        self._family = family
        self._runner: Runner | ChildRunner = ChildRunner(
            weights.store(settings), hardware, device=device, feature=FEATURE
        )
        #: Kept: a vocabulary that crashed its process crashes the next one the same way.
        self._words_refused: str | None = None

    @property
    def family(self) -> str:
        return self._family

    @property
    def device_name(self) -> str:
        """What the models are loaded on, read back so a changed setting rebuilds the sessions."""
        return self._runner.device

    @property
    def broken(self) -> str | None:
        """Why the device stopped answering, or None while it has not."""
        return self._runner.broken

    @property
    def words_refused(self) -> str | None:
        """Why a typed query can't be read on this device, or None while it can."""
        return self._words_refused

    @property
    def revision(self) -> str:
        """What described a file; numbers from two revisions are not comparable."""
        return weights.working_set(self._family)[0].revision

    def installed(self) -> bool:
        """Whether all three files of the chosen set are on disk (loading checks their content)."""
        store = weights.store(self._settings)
        return all(store.installed(weight) for weight in weights.working_set(self._family))

    def unload(self) -> None:
        """Give the memory back. What switching the feature off does."""
        self._runner.unload()

    # --- pictures ---------------------------------------------------------------------------

    async def describe_pictures(self, frames: Sequence[np.ndarray]) -> list[list[float]]:
        """Describe several decoded pictures, off the event loop."""
        if not frames:
            return []
        return await asyncio.to_thread(self._describe_pictures, list(frames))

    def _describe_pictures(self, frames: list[np.ndarray]) -> list[list[float]]:
        """One frame at a time: a stacked batch normalises across frames, changing every vector."""
        pictures, _, _ = weights.working_set(self._family)
        loaded = self._runner.load(pictures)
        described = []
        for frame in frames:
            output = self._runner.run(loaded, prepare(frame), outputs=[_POOLED])
            described.append(to_unit_length(np.asarray(output[0]).reshape(-1)))
        return described

    # --- words ------------------------------------------------------------------------------

    async def describe_words(self, text: str) -> list[float]:
        """Describe a typed query, off the event loop."""
        return await asyncio.to_thread(self._describe_words, text)

    def _describe_words(self, text: str) -> list[float]:
        symbols = np.array([self._symbols(text)], dtype=np.int64)
        _, words, _ = weights.working_set(self._family)
        loaded = self._runner.load(words)
        output = self._runner.run(loaded, symbols, outputs=[_POOLED])
        return to_unit_length(np.asarray(output[0]).reshape(-1))

    def _symbols(self, text: str) -> list[int]:
        """A sentence as the model's symbols, padded to the length it reads, the end marker kept."""
        if self._words_refused is not None:
            raise DeviceUnavailable(self._words_refused)
        _, _, vocabulary = weights.working_set(self._family)
        try:
            encoded, end = self._runner.encode(vocabulary, canonical(text))
        except (DeviceUnavailable, RuntimeError) as failure:
            why = failure.how if isinstance(failure, ChildStopped) else str(failure)
            self._words_refused = runtime_wont_start(FEATURE, why)
            raise DeviceUnavailable(self._words_refused) from failure
        symbols = [*encoded[: MAX_SYMBOLS - 1], end]
        return symbols + [_PADDING] * (MAX_SYMBOLS - len(symbols))

    # --- for tests and for the settings screen ------------------------------------------------

    def loaded_picture_model(self) -> Loaded:
        """The picture model, loading it if it is not loaded. Raises if it is not installed."""
        return self._runner.load(weights.working_set(self._family)[0])
