# SPDX-License-Identifier: AGPL-3.0-or-later
"""One file's crops put through the two models, with the models stood in for.

No model ships with Sift, so nothing here runs one. What is checked is everything `Reader` decides
around them: where the finder's answer is cut out of the crop, which cuts are too small to be worth
reading, that the reader is only loaded when there is something to read, and that a strip read as
nothing is not a line.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.slices.watermarks import read, weights
from sift.slices.watermarks.frames import Piece
from sift.slices.watermarks.reader import Reader

pytestmark = [pytest.mark.anyio]


class Models:
    """The runtime, answering with arrays a test chose, and counting what it was asked."""

    def __init__(self, finder: np.ndarray, spoken: np.ndarray | None = None) -> None:
        self._finder = finder
        self._spoken = spoken
        self.loaded: list[str] = []
        self.batches: list[tuple[int, ...]] = []
        self.broken: str | None = None
        self.device = "cpu"
        self.unloaded = False

    def load(self, weight: weights.Weight) -> str:
        self.loaded.append(weight.id)
        return weight.id

    def run(self, loaded: str, blob: np.ndarray) -> list[np.ndarray]:
        if loaded == "finder":
            return [self._finder[None, None, ...]]
        self.batches.append(blob.shape)
        assert self._spoken is not None
        return [self._spoken]

    def unload(self) -> None:
        self.unloaded = True


def _hardware() -> HardwareReport:
    return HardwareReport(
        cpu_count=4,
        total_ram_bytes=8 << 30,
        worker_concurrency=3,
        cuda=False,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )


def _reader(settings: Settings, models: Models) -> Reader:
    built = Reader(settings, _hardware())
    built._runner = models  # type: ignore[assignment]
    return built


def _spoken(*words: str) -> np.ndarray:
    """The reader's answer for one strip per word: one position per letter, sure of each."""
    positions = max(len(word) for word in words) or 1
    scores = np.zeros((len(words), positions, len(read.SYMBOLS)), np.float32)
    for row, word in enumerate(words):
        for at in range(positions):
            symbol = 1 + read.ALPHABET.index(word[at]) if at < len(word) else 0
            scores[row, at, symbol] = 1.0
    return scores


def _two_lines() -> np.ndarray:
    answer = np.zeros((64, 200), np.float32)
    answer[10:20, 40:160] = 0.9
    answer[40:50, 40:160] = 0.9
    return answer


async def test_every_line_found_in_a_files_crops_is_read_in_one_batch(settings: Settings) -> None:
    """Both strips of the band go to the reader together, and a strip it read as nothing at all is
    not a line of text."""
    models = Models(_two_lines(), _spoken("abc", ""))
    band = Piece(name="band", pixels=np.full((64, 200, 3), 120, np.uint8))

    lines = await _reader(settings, models).read_crops([band])

    assert lines == [read.Line(text="abc", confidence=1.0, crop="band")]
    assert models.loaded == ["finder", "reader"]
    assert len(models.batches) == 1 and models.batches[0][0] == 2


async def test_a_strip_that_shrinks_to_a_few_pixels_in_its_crop_is_not_read(
    settings: Settings,
) -> None:
    """Below a few pixels the reader's own downsampling leaves it nothing, and it answers with an
    empty line, so the cut is dropped before it costs a place in the batch."""
    models = Models(_two_lines(), _spoken("abc", "de"))
    band = Piece(name="band", pixels=np.full((64, 200, 3), 120, np.uint8))
    corner = Piece(name="corner", pixels=np.full((8, 8, 3), 120, np.uint8))

    lines = await _reader(settings, models).read_crops([band, corner])

    assert [line.crop for line in lines] == ["band", "band"]
    assert models.batches[0][0] == 2


async def test_crops_holding_no_text_never_load_the_reader(settings: Settings) -> None:
    """The reader is the larger of the two files, and much of a library carries no mark."""
    models = Models(np.zeros((64, 200), np.float32))
    band = Piece(name="band", pixels=np.full((64, 200, 3), 120, np.uint8))

    assert await _reader(settings, models).read_crops([band]) == []
    assert models.loaded == ["finder"]


async def test_a_file_with_no_crops_loads_nothing_at_all(settings: Settings) -> None:
    models = Models(np.zeros((64, 200), np.float32))
    assert await _reader(settings, models).read_crops([]) == []
    assert models.loaded == []


def test_the_models_count_as_installed_only_when_both_files_are_on_disk(
    settings: Settings,
) -> None:
    """Existence only: whether they are the RIGHT files is what loading one checks."""
    reader = Reader(settings, _hardware())
    store = weights.store(settings)
    finder, second = weights.working_set()
    assert not reader.installed()
    for count, weight in enumerate((finder, second)):
        path = store.path_of(weight)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not really a model")
        assert reader.installed() is (count == 1)


def test_the_device_stopping_and_switching_off_are_the_runtimes(settings: Settings) -> None:
    models = Models(np.zeros((4, 4), np.float32))
    reader = _reader(settings, models)
    before = reader.broken
    models.broken = "the card stopped answering"
    assert (before, reader.broken) == (None, "the card stopped answering")
    reader.unload()
    assert models.unloaded


def test_a_new_reader_is_driven_in_a_process_of_its_own(settings: Settings) -> None:
    """A device that dies poisons the process it was loaded in, so the one serving the library must
    not be it. Constructing one loads nothing and starts nothing."""
    from sift.kernel.ml.child import ChildRunner

    reader = Reader(settings, _hardware(), device="cpu")
    runner: Any = reader._runner
    assert isinstance(runner, ChildRunner)
    assert runner._child is None
