# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a picture into numbers, and a typed sentence into numbers that mean the same thing.

The whole feature rests on one property: the two models put a picture and a description of that
picture in the *same* space, so the distance between them is meaningful. Nothing about a model
announces whether that property holds: one that has been fed pictures prepared the wrong way
still returns numbers, still returns them fast, and still ranks them confidently. It is simply
ranking noise.

So the preparation here is not incidental detail, it is the property:

- **Squashed to a square, not cropped to one.** The publisher's own preparation resizes to 224x224
  outright. Cropping would be a reasonable-looking choice that quietly shows the model something
  different from what it was trained on.
- **Scaled to run from minus one to one**, not from zero to one. Half a step wrong in either
  direction is not an error, it is a slow drift in the answers.
- **The pooled output, taken by name.** Both models offer two outputs and the *first* one is not
  the one wanted: it is one row per patch or per word rather than one per picture. Taking output
  zero gets a confident, wrongly shaped answer, and nothing fails to say so.
- **Scaled to unit length afterwards**, so that comparing two of them measures direction and not
  magnitude.
- **A typed query is canonicalised before the vocabulary reads it**: lower case, no punctuation,
  runs of whitespace collapsed. Same rule, the other tower: text prepared differently from the way
  the model was trained still returns numbers and still ranks them confidently. See `canonical`,
  which carries what a capital letter does to a query.

**Nothing here touches the event loop.** Running a model is a single call into compiled code that
holds the interpreter for its whole duration (about a tenth of a second per frame) and during it
nothing else in the process runs at all: not a request, not the job feed, not a video somebody is
watching. The symptom is the screen freezing, not an error. Every call into a model here is made
from a worker thread, and the seam that does it is `describe_*`, which is async for that reason and
no other.
"""

from __future__ import annotations

import asyncio
import string
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger
from sift.kernel.ml.child import ChildRunner
from sift.kernel.ml.runtime import Loaded, Runner
from sift.slices.semantic import weights

log = get_logger(__name__)

#: How this feature names itself when a device it was set to use is not there.
FEATURE = "Search by meaning"

#: The square the picture model reads. Fixed by the model, not a preference.
FRAME_SIZE = 224

#: The most symbols a typed query is read as. Longer text is cut rather than refused: somebody
#: pasting a paragraph gets an answer about the beginning of it, which is better than an error.
MAX_SYMBOLS = 64

#: What fills the unused end of a short query. The vocabulary's own padding symbol.
_PADDING = 0

#: The characters taken out of a typed query. ASCII punctuation and nothing else, which is what the
#: publisher's own preparation removes: matching it exactly is the whole point, so this is not the
#: place to be cleverer than the model.
_PUNCTUATION = str.maketrans("", "", string.punctuation)

#: The output both models are read from. Named rather than taken by position: the other output is
#: one row per patch or per word, it is FIRST, and taking it produces a confidently wrong answer of
#: entirely the wrong shape.
_POOLED = "pooler_output"


def canonical(text: str) -> str:
    """A typed query as the word model was trained to be handed one.

    Lower case, ASCII punctuation removed, runs of whitespace collapsed. This is `prepare` for the
    other tower and it is load-bearing for the same reason: the models only sit in one space if
    both sides are prepared the way the publisher prepared them, and text prepared some other way
    comes back as confident numbers rather than as an error.

    **The publisher says so, in the file that ships beside the weights.** Its
    `tokenizer_config.json` carries `do_lower_case: True`, and Sift pins these weights by digest,
    so that file describes exactly the model being run.

    **A capital letter does not degrade the query: it collapses it.** `Red Sports Car` can share
    NONE of its top twelve with `red sports car`, and three unrelated capitalised
    queries (a car, a beach, a bathroom) shared seven and eight of their top twelve with each
    other. Across eight unrelated queries the mean angle between DIFFERENT questions came out at
    0.802 in lower case, 0.979 in title case and 0.994 in upper case: at that point the words have
    stopped pointing anywhere, so what comes back is whatever sits nearest the middle of the
    library, ranked confidently, with a plausible number beside it.

    Three smaller versions of the same fault, all of which this closes: one leading capital
    comes out at 0.963 against the plainly typed query, a trailing question mark 0.988, and a double
    space 0.930: the vocabulary reads a run of spaces as a symbol of its own. With this applied
    first, every one of those comes back at 1.0000: the same vector, not a nearer one.

    **Nothing stored changes.** The index holds descriptions of PICTURES, and this is the question
    side, so there is nothing to describe again.
    """
    return " ".join(text.translate(_PUNCTUATION).lower().split())


def prepare(frame: np.ndarray) -> np.ndarray:
    """One decoded picture, as the model expects to be handed it.

    Takes a height-by-width-by-colour array of bytes and returns the batch of one the model reads.
    """
    if frame.shape != (FRAME_SIZE, FRAME_SIZE, 3):
        raise ValueError(
            f"a picture for this model has to be {FRAME_SIZE} by {FRAME_SIZE} in colour, "
            f"and this one is {frame.shape}"
        )
    pixels = frame.astype(np.float32) / 255.0
    pixels = (pixels - 0.5) / 0.5
    return np.transpose(pixels, (2, 0, 1))[None, ...].copy()


def to_unit_length(values: np.ndarray) -> list[float]:
    """Scaled so its length is one, which is what makes two of them comparable by direction.

    A vector of all zeros has no direction, and dividing by its length is a division by zero. It
    cannot arise from a real model, and returning it unchanged rather than a page of NaN is the
    difference between a wrong answer and an unreadable one.
    """
    length = float(np.linalg.norm(values))
    if length == 0.0:
        return [float(value) for value in values]
    return [float(value) for value in values / length]


class Embedder:
    """The two models and the vocabulary, loaded once and kept.

    Constructing one loads nothing. The first call that needs a model loads it, verifies it against
    its digest first, and keeps the prepared session: preparing one costs a hundred times what
    running it does.
    """

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
        # In a process of its own, below normal priority, restarted when its device dies. The
        # in-process `Runner` has the same surface and is what a test stands the runtime in for.
        self._runner: Runner | ChildRunner = ChildRunner(
            weights.store(settings), hardware, device=device, feature=FEATURE
        )
        self._vocabulary: Any | None = None

    @property
    def family(self) -> str:
        return self._family

    @property
    def device_name(self) -> str:
        """What the models are loaded on. Read back so that a change to the setting can be noticed
        and the prepared sessions rebuilt rather than quietly kept."""
        return self._runner.device

    @property
    def broken(self) -> str | None:
        """Why the device stopped answering, or None while it has not. See `Runner.broken`."""
        return self._runner.broken

    @property
    def revision(self) -> str:
        """What described a file, recorded against it. Numbers from two different revisions are not
        comparable, and nothing about them says so."""
        return weights.working_set(self._family)[0].revision

    def installed(self) -> bool:
        """Whether all three files of the chosen set are on disk. Says nothing about whether they
        are the right ones: loading verifies that, and it reads every byte."""
        store = weights.store(self._settings)
        return all(store.installed(weight) for weight in weights.working_set(self._family))

    def unload(self) -> None:
        """Give the memory back. What switching the feature off does."""
        self._runner.unload()
        self._vocabulary = None

    # --- pictures ---------------------------------------------------------------------------

    async def describe_pictures(self, frames: Sequence[np.ndarray]) -> list[list[float]]:
        """Describe several decoded pictures, off the event loop.

        A whole file's frames in one call rather than one call per frame: the hop to a worker
        thread is paid once, and the model is loaded once for all of them. What it does NOT do is
        hand all of them to the model at once. See `_describe_pictures`.
        """
        if not frames:
            return []
        return await asyncio.to_thread(self._describe_pictures, list(frames))

    def _describe_pictures(self, frames: list[np.ndarray]) -> list[list[float]]:
        """One frame at a time, deliberately, not stacked into one batch.

        This model declares a free batch dimension, so putting the thirty frames of a file in one
        blob looks like it would let the card do in one pass what it does in thirty. It fails on
        both halves.

        It is not the same answer. Thirty frames described in one blob agree with the same thirty
        described one at a time to a cosine of only 0.956 at worst, while the SAME frames
        described on the processor and on the card, which nobody calls a difference, agree to
        0.977. So batching would move every vector further than changing the device does. The
        cause is known rather than guessed: four identical copies in one blob come back
        identical to the single call to the last bit, reversing the order of a blob changes
        nothing, and the disagreement grows with the number of DIFFERENT pictures in it (0.985 at
        two, 0.956 at thirty). That is a normalisation computed across the batch: order-blind,
        content-dependent, and exactly degenerate when every picture is the same one. The stored
        vectors carry a `revision`, and this would change what the same revision means.

        Nor is it quicker where it matters. Thirty frames on the processor are about a fifth
        SLOWER in one blob than one at a time. On the card the blob is quicker, but most of that
        gap is the thread setting rather than the batch: one at a time, the card's time more than
        halves on the threads alone (`kernel.ml.runtime.session_threads`), which is the setting
        used.

        The face recognizer is the opposite case and is batched: identical answers to the bit, 3.8
        times quicker. A free batch dimension is a promise about shapes, not about arithmetic, and
        the difference has to be measured per model.
        """
        pictures, _, _ = weights.working_set(self._family)
        loaded = self._runner.load(pictures)
        described = []
        for frame in frames:
            output = self._runner.run(loaded, prepare(frame), outputs=[_POOLED])
            described.append(to_unit_length(np.asarray(output[0]).reshape(-1)))
        return described

    # --- words ------------------------------------------------------------------------------

    async def describe_words(self, text: str) -> list[float]:
        """Describe a typed query, off the event loop.

        One pass over a short sentence, a few tens of milliseconds on one core, which is why
        the cost of this feature is the index and never the search. Off the loop regardless: those
        milliseconds of held interpreter are milliseconds in which a video
        somebody is watching gets nothing.
        """
        return await asyncio.to_thread(self._describe_words, text)

    def _describe_words(self, text: str) -> list[float]:
        _, words, _ = weights.working_set(self._family)
        loaded = self._runner.load(words)
        symbols = np.array([self._symbols(text)], dtype=np.int64)
        output = self._runner.run(loaded, symbols, outputs=[_POOLED])
        return to_unit_length(np.asarray(output[0]).reshape(-1))

    def _symbols(self, text: str) -> list[int]:
        """A sentence as the symbols the word model was trained on, padded to the length it reads.

        Canonicalised first. See `canonical`, which is where the figures are. It happens
        here rather than at the search box because it is a fact about what this model reads, and
        every caller that ever describes words goes through this one function.

        The padding is not a formality: the model has a fixed input length, and a short query has
        to be filled out to it. What fills it is the vocabulary's own padding symbol, and an end
        marker closes the real text, both of which the model was trained to expect in exactly
        those places. An UNPADDED query shares between none and four of its top twenty with the
        padded one, and sits further from everything in the library.

        **The cut is taken before the end marker is added, not after.** `[*encode(text),
        eos()][:MAX_SYMBOLS]` would, for a query long enough to reach the limit (about forty-five
        words), throw away the very symbol the paragraph above says closes the text, so the one
        input that most needs the marker would be the one that lost it.
        """
        vocabulary = self._load_vocabulary()
        symbols = [*vocabulary.encode(canonical(text))[: MAX_SYMBOLS - 1], vocabulary.eos_id()]
        return symbols + [_PADDING] * (MAX_SYMBOLS - len(symbols))

    def _load_vocabulary(self) -> Any:
        if self._vocabulary is not None:
            return self._vocabulary
        import sentencepiece

        store = weights.store(self._settings)
        _, _, vocabulary = weights.working_set(self._family)
        store.verify(vocabulary)
        path: Path = store.path_of(vocabulary)
        self._vocabulary = sentencepiece.SentencePieceProcessor(model_file=str(path))
        log.info("semantic.vocabulary.loaded", revision=vocabulary.revision)
        return self._vocabulary

    # --- for tests and for the settings screen ------------------------------------------------

    def loaded_picture_model(self) -> Loaded:
        """The picture model, loading it if it is not loaded. Raises if it is not installed."""
        return self._runner.load(weights.working_set(self._family)[0])
