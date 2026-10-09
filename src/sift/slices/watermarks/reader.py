# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two watermark models, run in a child process, and one file's crops read through them."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import numpy as np

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.ml.child import ChildRunner
from sift.kernel.ml.runtime import Runner
from sift.slices.watermarks import read, weights
from sift.slices.watermarks.frames import Piece

FEATURE = "Reading watermarks"

#: Smaller and the reader answers with an empty line.
_TINY = 4


class Reader:
    """The finder and the reader, each loaded and digest-checked on first use, then kept."""

    def __init__(
        self, settings: Settings, hardware: HardwareReport, *, device: str = "cpu"
    ) -> None:
        self._settings = settings
        self._runner: Runner | ChildRunner = ChildRunner(
            weights.store(settings), hardware, device=device, feature=FEATURE
        )

    @property
    def broken(self) -> str | None:
        return self._runner.broken

    def installed(self) -> bool:
        """Whether both files are on disk; loading is what verifies them."""
        store = weights.store(self._settings)
        return all(store.installed(weight) for weight in weights.working_set())

    def unload(self) -> None:
        """Give the memory back, as switching the feature off does."""
        self._runner.unload()

    async def read_crops(self, pieces: Sequence[Piece]) -> list[read.Line]:
        if not pieces:
            return []
        return await asyncio.to_thread(self._read_crops, list(pieces))

    def _read_crops(self, pieces: list[Piece]) -> list[read.Line]:
        finder, reader = weights.working_set()
        found = self._runner.load(finder)
        strips: list[np.ndarray] = []
        whose: list[str] = []
        for piece in pieces:
            crop = read.stretch(piece.pixels)
            answer = np.asarray(self._runner.run(found, read.for_detector(crop))[0])[0, 0]
            tall, wide = crop.shape[0], crop.shape[1]
            down, across = answer.shape
            for left, top, right, bottom in read.strips(answer):
                cut = crop[
                    round(top * tall / down) : round(bottom * tall / down),
                    round(left * wide / across) : round(right * wide / across),
                ]
                if cut.shape[0] < _TINY or cut.shape[1] < _TINY:
                    continue
                strips.append(cut)
                whose.append(piece.name)
        if not strips:
            return []
        # Loaded only once a file has text, so files with no mark never load it.
        spoken = self._runner.run(self._runner.load(reader), read.for_reader(strips))[0]
        return [
            read.Line(text=text, confidence=confidence, crop=crop_name)
            for (text, confidence), crop_name in zip(
                read.decode(np.asarray(spoken)), whose, strict=True
            )
            if text.strip()
        ]
