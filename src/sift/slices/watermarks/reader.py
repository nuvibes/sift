# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two models, held open, and one file's worth of reading put through them.

**Nothing here touches the event loop.** Running a model is one call into compiled code that holds
the interpreter for its whole duration, and during it nothing else in the process runs at all:
not a request, not the job feed, not a video somebody is watching. Every call into a model is made
from a worker thread, and `read` is async for that reason and no other.

**The models run in a process of their own.** That is the kernel's `ChildRunner`, and it is what
search-by-meaning does. Two reasons, and the second is the one that matters here: a graphics card
that dies underneath a session poisons the process it was loaded in, for good and silently, so the
process it is loaded in must not be the one serving the library; and a child can be run below
normal priority, which a thread cannot.

**The finder is asked twice and the reader once.** Each crop gets its own pass of the finder,
because the two crops are different sizes and the finder takes one picture at a time. Everything
both crops turned up is read in ONE batch: the reader takes a stack of strips, and a batch of
nine costs a fraction more than a batch of one.
"""

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

#: How this feature names itself when a device it was set to use is not there.
FEATURE = "Reading watermarks"

#: The narrowest and shortest a found strip may be before it is handed to the reader. Below this
#: the reader's own downsampling leaves it nothing, and it answers with an empty line.
_TINY = 4


class Reader:
    """The finder and the reader, loaded once and kept.

    Constructing one loads nothing. The first file that needs a model loads it, verifies it against
    its digest first, and keeps the prepared session: preparing one costs a hundred times what
    running it does.
    """

    def __init__(
        self, settings: Settings, hardware: HardwareReport, *, device: str = "cpu"
    ) -> None:
        self._settings = settings
        self._runner: Runner | ChildRunner = ChildRunner(
            weights.store(settings), hardware, device=device, feature=FEATURE
        )

    @property
    def broken(self) -> str | None:
        """Why the device stopped answering, or None while it has not. See `Runner.broken`."""
        return self._runner.broken

    def installed(self) -> bool:
        """Whether both files are on disk. Says nothing about whether they are the right ones:
        loading verifies that, and it reads every byte."""
        store = weights.store(self._settings)
        return all(store.installed(weight) for weight in weights.working_set())

    def unload(self) -> None:
        """Give the memory back. What switching the feature off does."""
        self._runner.unload()

    async def read_crops(self, pieces: Sequence[Piece]) -> list[read.Line]:
        """Every line of text in one file's crops, off the event loop."""
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
        # Loaded here rather than beside the finder, so a file whose crops held no text at all
        # never loads it. On a library where half the files carry no mark that is half the
        # loads saved, and the reader is the larger of the two files.
        spoken = self._runner.run(self._runner.load(reader), read.for_reader(strips))[0]
        return [
            read.Line(text=text, confidence=confidence, crop=crop_name)
            for (text, confidence), crop_name in zip(
                read.decode(np.asarray(spoken)), whose, strict=True
            )
            if text.strip()
        ]
