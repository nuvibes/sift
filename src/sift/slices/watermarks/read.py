# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding text in a picture and reading it, with nothing but arrays.

Two models do the work (one says WHERE text is, the other says WHAT a strip of it says) and
everything between them is here. That between is not incidental: the first model answers with a
probability per pixel, and turning that into strips a reader can be handed is where an OCR
pipeline is usually made of a computer-vision library.

**There is no computer-vision library here, and that is a decision rather than an omission.** The
usual pair for this (OpenCV for contours and warps, a polygon-offset library for the rest) is
about forty megabytes that every install would carry for a feature almost none of them will switch
on, and Sift already owns the two things that make them unnecessary:

- **the decoder shapes the picture.** Every crop this feature reads is a rectangle of a frame at a
  size chosen before the frame is decoded, so ffmpeg, which is reading the file anyway, hands
  back exactly the pixels wanted, already scaled. Nothing here resizes a frame.
- **the offset is arithmetic on a rectangle.** The published pipeline finds a contour, takes its
  smallest enclosing rectangle, grows the polygon by a distance, and takes the smallest enclosing
  rectangle of THAT. Growing a rectangle by a distance and re-enclosing it is the rectangle grown
  by that distance on each side; the rounded corners the general algorithm makes are thrown away
  by the second enclosure. So the library would compute, expensively, something that is one line.

What IS given up is rotated text. The published pipeline lifts a tilted box upright through a
perspective transform; this one reads upright rectangles. That is not a loss for what this reads:
a site's watermark is drawn horizontally over the bottom of a frame, and the two crops that carry
it are where marks are found (see `frames.py`).

**The strips come from a profile, not from connected blobs.** The first model's answer for a line
of text is one shrunken region per line, so the rows of the crop that contain any of it are the
lines, and within a line the columns that contain any of it are the words. Summing a boolean array
along an axis is one array operation; walking pixels to label blobs is not, and in Python it is
about as expensive as running the model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: What the reader's output positions mean, in the publisher's own order.
#:
#: Ninety-five printable symbols, and the order looks wrong until you see it: it starts at `0`,
#: runs to `~`, and only then wraps round to `!`. That is the publisher's dictionary file, and it
#: is the order the model's outputs are in: sorting it, or writing the obvious ASCII run, moves
#: every symbol and the reader returns confident nonsense.
#:
#: **Written here rather than downloaded beside the model, because the model is pinned by digest.**
#: The alphabet is a property of one exact file; a different file would fail its digest and never
#: load, so there is no version of this that can drift away from the weights it describes. The
#: publisher ships it inside a configuration file in a format Sift has no reader for, and adding
#: one to carry ninety-five characters that cannot change would be a dependency bought with
#: nothing.
ALPHABET = (
    "0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
    "!\"#$%&'()*+,-./ "
)

#: The symbols the reader's output positions map to. Position zero is the separator the decoder
#: uses to mark "nothing here", and the model carries one more output than the alphabet has
#: symbols: the publisher's decoder appends a space to fill it.
SYMBOLS = ("", *ALPHABET, " ")

#: The longest side the detector is given. The model was trained at this scale and reads a larger
#: picture no better, only slower; the published pipeline caps at the same number.
DETECT_LONG_SIDE = 960

#: Both sides of the detector's input are rounded to a multiple of this. Its downsampling steps
#: halve the picture five times, so a side that is not a multiple of thirty-two is padded inside
#: the model and the answer comes back describing a picture a few pixels larger than the one that
#: went in.
DETECT_STEP = 32

#: How tall a strip is when the reader is handed it. Fixed by the model.
LINE_HEIGHT = 48

#: The widest strip the reader is given, after scaling to `LINE_HEIGHT`. A whole band of a very
#: wide frame can otherwise arrive as one strip thousands of pixels long, which costs time in
#: proportion and says nothing more.
MAX_LINE_WIDTH = 1200

#: The narrowest. Below this the model's own downsampling leaves it nothing to read.
MIN_LINE_WIDTH = 16

#: Strip widths are padded up to a multiple of this before a batch is made, because the reader's
#: output is one position per this many input columns.
LINE_STEP = 8

#: Where the detector's answer is cut into ink and background. The publisher's own default.
INK = 0.3

#: How sure the detector has to be, averaged over a whole strip, for that strip to be read. The
#: publisher's own default.
CONFIDENCE = 0.6

#: How far a strip is grown before it is read. The detector is trained to answer with a SHRUNKEN
#: region (that is how two lines of text a few pixels apart come back as two regions rather than
#: one), so a strip has to be grown back or its top and bottom rows of letters are cut off. The
#: publisher's own default.
UNCLIP = 1.5

#: What the detector expects a picture's numbers to be centred and spread on. Not a preference:
#: the model was trained on pictures prepared this way, and one prepared differently comes back as
#: a confident answer about nothing.
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STANDARD_DEVIATION = np.array([0.229, 0.224, 0.225], np.float32)

#: The percentiles a crop's colours are stretched between before the detector sees it. A watermark
#: is usually a few percent of white laid over a picture, and the detector finds it far more often
#: when that few percent is spread over the whole range.
_LOW_PERCENTILE = 2.0
_HIGH_PERCENTILE = 98.0

#: How much of a spread there has to be before stretching it is worth anything. Below this the crop
#: is flat (a black bar, a blown-out sky) and stretching it only amplifies the noise.
_FLAT = 8.0

#: How far apart the percentiles are sampled. The stretch reads every fourth row and column rather
#: than the whole crop: the answer is a percentile of a few thousand samples either way, and the
#: full read was measurably the more expensive half of preparing a crop.
_SAMPLE_STRIDE = 4


@dataclass(frozen=True, slots=True)
class Line:
    """One strip of text that was found and read, and how sure the reader was of it."""

    text: str
    confidence: float
    #: Which crop it was found in, for the record kept against the file.
    crop: str


def detect_size(width: int, height: int) -> tuple[int, int]:
    """The size the detector is handed a crop at.

    Never larger than the crop: a picture enlarged on the way in gives the model nothing it did not
    already have, and costs in proportion to the area.
    """
    scale = min(DETECT_LONG_SIDE / max(width, height), 1.0)
    return (
        max(DETECT_STEP, round(width * scale / DETECT_STEP) * DETECT_STEP),
        max(DETECT_STEP, round(height * scale / DETECT_STEP) * DETECT_STEP),
    )


def stretch(crop: np.ndarray) -> np.ndarray:
    """One crop with each colour spread over the whole range it has room for.

    Per channel rather than over the picture as a whole, because a watermark drawn in white over a
    warm-toned frame is a few percent of one channel and most of another, and a single stretch
    keeps the ratio between them exactly as unreadable as it was.

    **Through a table of 256 answers rather than as arithmetic on the picture.** A crop is about a
    million pixels and the arithmetic is the same for every pixel holding the same value (there
    are only 256 of those), so working it out per pixel does the same sum thousands of times over.
    Doing it once per value and looking the answer up takes about a quarter off the array stage,
    and about five per cent off the pass, which is what it is worth and no more.
    """
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
    """One picture at another size, sampled between its neighbours.

    The one resize in this module, and it is here because the two places that need it are both
    mid-pipeline: a crop on its way into the detector, and a strip on its way into the reader.
    Everything the DECODER can be asked for is asked of the decoder instead.

    Bilinear, and read as a gather rather than a loop: the four neighbours of every output pixel
    are addressed at once, so the cost is the size of the output and not the size of the input.
    """
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
    """One crop as the batch of one the detector reads: colour first, centred, spread."""
    width, height = detect_size(crop.shape[1], crop.shape[0])
    small = resample(crop, width, height).astype(np.float32) / 255.0
    return np.transpose((small - _MEAN) / _STANDARD_DEVIATION, (2, 0, 1))[None, ...].copy()


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Where every run of True starts and stops. The one primitive the strip finder is built on."""
    padded = np.concatenate(([False], flags, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[0::2].tolist(), edges[1::2].tolist(), strict=True))


def _joined(runs: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    """Runs with the short gaps between them closed up.

    The gap allowed is the height of the line, which is about the width of one letter: the spaces
    between words in a line close, and the space between two separate marks on the same row does
    not. A whole line read as one strip is what the reader wants in any case: it reads a strip of
    text, and the match that follows slides a window along whatever comes back.
    """
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
    """Where the text is in the detector's answer, as left, top, right, bottom.

    The answer is one probability per pixel that the pixel is inside a line of text. The rows that
    hold any of it are the lines; within a line, the columns that hold any of it are the words;
    and a strip is kept when the detector is sure enough of it AVERAGED OVER THE WHOLE RECTANGLE,
    which is what makes a scattering of confident specks fail while a solid line passes.
    """
    mask = answer > ink
    height, width = mask.shape
    found: list[tuple[int, int, int, int]] = []
    for top, bottom in _runs(mask.any(axis=1)):
        tall = bottom - top
        if tall < 3:
            # Two rows of ink is a speck or a border, never a line of text. The published pipeline
            # refuses the same thing by the shorter side of its rectangle.
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
    """Several strips as the one batch the reader takes.

    Every strip is scaled to the height the model reads and then padded out to the width of the
    longest, because a batch is one rectangle. The padding is black and sits to the RIGHT of the
    text, where the decoder reads it as nothing.

    **The colours are reversed on the way in.** The publisher's reader was trained on pictures in
    the order the usual imaging library hands them over, which is the reverse of the order a
    decoder hands them over. Feeding it the other way round is not an error and does not look like
    one: it returns letters, confidently, and some of them are wrong.
    """
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
    """What the reader said, one line per strip, with how sure it was.

    The model answers with one row of scores per horizontal position, and a letter is usually wide
    enough to cover several positions. So a run of the same symbol is one letter, and the separator
    at position zero is what tells two genuine doubles apart from one wide letter, which is why
    the separator is dropped only after the repeats are collapsed and never before.
    """
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
