# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find text in a picture and read it with plain arrays: no vision library, upright rectangles
only."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: The publisher's order (`0` to `~`, then `!`); sorting it makes the reader return nonsense.
ALPHABET = (
    "0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
    "!\"#$%&'()*+,-./ "
)

#: Position zero is the decoder's separator; the last output is a space, as the publisher's.
SYMBOLS = ("", *ALPHABET, " ")

#: The model's training scale; larger reads no better, only slower.
DETECT_LONG_SIDE = 960

#: The detector halves five times; another size is padded and the answer shifts.
DETECT_STEP = 32

#: Fixed by the model.
LINE_HEIGHT = 48

#: A wider strip costs time in proportion and says nothing more.
MAX_LINE_WIDTH = 1200

#: Below this the model's downsampling leaves nothing to read.
MIN_LINE_WIDTH = 16

#: The reader answers one position per this many columns.
LINE_STEP = 8

#: The publisher's defaults, as are `CONFIDENCE` and `UNCLIP`.
INK = 0.3

#: Averaged over a whole strip.
CONFIDENCE = 0.6

#: The detector answers with shrunken regions, so a strip is grown back before reading.
UNCLIP = 1.5

#: The model's training normalisation; anything else gives a confident answer about nothing.
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STANDARD_DEVIATION = np.array([0.229, 0.224, 0.225], np.float32)

#: A faint white mark is found far more often once stretched over the whole range.
_LOW_PERCENTILE = 2.0
_HIGH_PERCENTILE = 98.0

#: A flat crop (a black bar) would only have its noise amplified.
_FLAT = 8.0

#: A percentile of a few thousand samples is as good and much cheaper.
_SAMPLE_STRIDE = 4


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    confidence: float
    crop: str


def detect_size(width: int, height: int) -> tuple[int, int]:
    """The size the detector is handed a crop at, never larger than the crop."""
    scale = min(DETECT_LONG_SIDE / max(width, height), 1.0)
    return (
        max(DETECT_STEP, round(width * scale / DETECT_STEP) * DETECT_STEP),
        max(DETECT_STEP, round(height * scale / DETECT_STEP) * DETECT_STEP),
    )


def stretch(crop: np.ndarray) -> np.ndarray:
    """One crop with each channel stretched over its full range, through a 256-entry table."""
    out = np.empty_like(crop)
    sample = crop[::_SAMPLE_STRIDE, ::_SAMPLE_STRIDE]
    values = np.arange(256, dtype=np.float32)
    for channel in range(crop.shape[2]):
        low, high = np.percentile(sample[:, :, channel], (_LOW_PERCENTILE, _HIGH_PERCENTILE))
        if high - low < _FLAT:
            out[:, :, channel] = crop[:, :, channel]
            continue
        table = np.clip((values - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)
        out[:, :, channel] = table[crop[:, :, channel]]
    return out


def resample(picture: np.ndarray, width: int, height: int) -> np.ndarray:
    """One picture at another size, bilinear, gathered so the cost is the output's size."""
    source_height, source_width = picture.shape[:2]
    if (source_width, source_height) == (width, height):
        return picture
    columns = (np.arange(width, dtype=np.float32) + 0.5) * source_width / width - 0.5
    rows = (np.arange(height, dtype=np.float32) + 0.5) * source_height / height - 0.5
    columns = np.clip(columns, 0, source_width - 1)
    rows = np.clip(rows, 0, source_height - 1)
    left = np.floor(columns).astype(np.int64)
    top = np.floor(rows).astype(np.int64)
    right = np.minimum(left + 1, source_width - 1)
    bottom = np.minimum(top + 1, source_height - 1)
    across = (columns - left)[None, :, None]
    down = (rows - top)[:, None, None]
    values = picture.astype(np.float32)
    upper = values[top[:, None], left[None, :]] * (1 - across) + (
        values[top[:, None], right[None, :]] * across
    )
    lower = values[bottom[:, None], left[None, :]] * (1 - across) + (
        values[bottom[:, None], right[None, :]] * across
    )
    blended = np.clip(upper * (1 - down) + lower * down, 0, 255)
    return np.asarray(blended, dtype=picture.dtype)


def for_detector(crop: np.ndarray) -> np.ndarray:
    width, height = detect_size(crop.shape[1], crop.shape[0])
    small = resample(crop, width, height).astype(np.float32) / 255.0
    return np.transpose((small - _MEAN) / _STANDARD_DEVIATION, (2, 0, 1))[None, ...].copy()


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Where every run of True starts and stops."""
    padded = np.concatenate(([False], flags, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[0::2].tolist(), edges[1::2].tolist(), strict=True))


def _joined(runs: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    """Runs with gaps under `gap` closed: about a letter wide, so words join and marks do not."""
    joined: list[tuple[int, int]] = []
    for start, stop in runs:
        if joined and start - joined[-1][1] <= gap:
            joined[-1] = (joined[-1][0], stop)
        else:
            joined.append((start, stop))
    return joined


def strips(
    answer: np.ndarray, *, ink: float = INK, confidence: float = CONFIDENCE, unclip: float = UNCLIP
) -> list[tuple[int, int, int, int]]:
    """Where the text is in the detector's answer, as left, top, right, bottom."""
    mask = answer > ink
    height, width = mask.shape
    found: list[tuple[int, int, int, int]] = []
    for top, bottom in _runs(mask.any(axis=1)):
        tall = bottom - top
        if tall < 3:
            # Two rows of ink is a speck or a border, never a line of text.
            continue
        for left, right in _joined(_runs(mask[top:bottom].any(axis=0)), tall):
            wide = right - left
            if wide < 3:
                continue
            if float(answer[top:bottom, left:right].mean()) < confidence:
                continue
            grown = round(wide * tall * unclip / (2 * (wide + tall)))
            found.append(
                (
                    max(0, left - grown),
                    max(0, top - grown),
                    min(width, right + grown),
                    min(height, bottom + grown),
                )
            )
    return found


def for_reader(pieces: list[np.ndarray]) -> np.ndarray:
    """Several strips as one batch, scaled, padded right, with the channel order reversed."""
    scaled = []
    widest = MIN_LINE_WIDTH
    for piece in pieces:
        tall, wide = piece.shape[:2]
        width = max(MIN_LINE_WIDTH, min(MAX_LINE_WIDTH, -(-wide * LINE_HEIGHT // tall)))
        scaled.append(resample(piece, width, LINE_HEIGHT))
        widest = max(widest, width)
    widest = -(-widest // LINE_STEP) * LINE_STEP
    batch = np.zeros((len(scaled), 3, LINE_HEIGHT, widest), np.float32)
    for at, piece in enumerate(scaled):
        centred = (piece[:, :, ::-1].astype(np.float32) / 255.0 - 0.5) / 0.5
        batch[at, :, :, : piece.shape[1]] = np.transpose(centred, (2, 0, 1))
    return batch


def decode(answer: np.ndarray) -> list[tuple[str, float]]:
    """What the reader said per strip, with its confidence; repeats collapse before gaps drop."""
    lines = []
    for row in answer:
        best = row.argmax(1)
        strength = row.max(1)
        symbols: list[str] = []
        sureness: list[float] = []
        previous = -1
        for at, score in zip(best.tolist(), strength.tolist(), strict=True):
            if at != 0 and at != previous:
                symbols.append(SYMBOLS[at])
                sureness.append(float(score))
            previous = at
        lines.append(("".join(symbols), float(np.mean(sureness)) if sureness else 0.0))
    return lines
