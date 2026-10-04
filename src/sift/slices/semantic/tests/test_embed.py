# SPDX-License-Identifier: AGPL-3.0-or-later
"""Preparing a picture, reading a sentence, and getting both off the event loop.

**The preparation IS the property the feature rests on.** A model fed pictures prepared the wrong
way does not fail: it returns numbers, quickly, and ranks them confidently. It is ranking noise. So
the square, the scaling and which output is read are all checked here rather than trusted.

**The pooled output is taken by name.** Both models offer two, the wrong one is FIRST, and it is
one row per patch rather than one per picture. Taking output zero produces a confident answer of
entirely the wrong shape, and this is what stops it happening.

**Everything crosses to a worker thread.** Running a model holds the interpreter for its whole
duration, so a call made on the event loop stops the whole application for a tenth of a second per
frame. Proved by watching the loop stay answerable while a description is in progress.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from typing import Any, ClassVar

import numpy as np
import pytest
from blake3 import blake3

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.slices.semantic import weights as semantic_weights
from sift.slices.semantic.embed import (
    FRAME_SIZE,
    MAX_SYMBOLS,
    Embedder,
    canonical,
    prepare,
    to_unit_length,
)

pytestmark = pytest.mark.unit


def machine(*, cuda: bool = False) -> HardwareReport:
    return HardwareReport(
        cpu_count=4,
        total_ram_bytes=8 << 30,
        worker_concurrency=3,
        cuda=cuda,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )


def picture(value: int = 255) -> np.ndarray:
    return np.full((FRAME_SIZE, FRAME_SIZE, 3), value, dtype=np.uint8)


# --- preparing a picture -----------------------------------------------------------------------


def test_a_picture_becomes_a_batch_of_one_in_the_order_the_model_reads() -> None:
    prepared = prepare(picture())

    assert prepared.shape == (1, 3, FRAME_SIZE, FRAME_SIZE)
    assert prepared.dtype == np.float32


def test_the_scaling_runs_from_minus_one_to_one() -> None:
    """Not from zero to one. Half a step wrong in either direction is not an error, it is a slow
    drift in the answers."""
    assert prepare(picture(255))[0, 0, 0, 0] == pytest.approx(1.0)
    assert prepare(picture(0))[0, 0, 0, 0] == pytest.approx(-1.0)
    assert prepare(picture(128))[0, 0, 0, 0] == pytest.approx(0.00392, abs=1e-4)


def test_a_picture_of_the_wrong_size_is_refused_rather_than_reshaped() -> None:
    """Quietly resizing here would hide a decoder that stopped producing what it was asked for."""
    with pytest.raises(ValueError, match="224 by 224"):
        prepare(np.zeros((10, 10, 3), dtype=np.uint8))


def test_numbers_are_scaled_to_unit_length_so_two_can_be_compared() -> None:
    scaled = to_unit_length(np.array([3.0, 4.0]))

    assert scaled == pytest.approx([0.6, 0.8])


def test_a_vector_of_nothing_comes_back_unchanged_rather_than_as_nonsense() -> None:
    """It cannot come out of a real model, and dividing by its length is a division by zero. A
    wrong answer is recoverable; a page of NaN is not readable at all."""
    assert to_unit_length(np.zeros(3)) == [0.0, 0.0, 0.0]


# --- the models ----------------------------------------------------------------------------------


class StubSession:
    """Stands in for the inference runtime, which needs real model files and there are none."""

    def __init__(self, path: str, options: Any, providers: list[str], **kwargs: Any) -> None:
        self.path = path
        self.options = options
        self.providers = providers
        self.kwargs = kwargs
        self.asked: list[list[str]] = []

    def get_providers(self) -> list[str]:
        return self.providers

    def get_inputs(self) -> list[Any]:
        return [type("Input", (), {"name": "input"})()]

    def get_outputs(self) -> list[Any]:
        return [
            type("Output", (), {"name": "last_hidden_state"})(),
            type("Output", (), {"name": "pooler_output"})(),
        ]

    #: How long one call blocks for. Zero by default; the loop test sets it, because a model call
    #: that returns instantly cannot show whether it was made on the event loop or off it.
    delay = 0.0

    def run(self, names: list[str], feed: dict[str, Any]) -> list[np.ndarray]:
        self.asked.append(names)
        if StubSession.delay:
            # Synchronous on purpose. This is what a real model call is: compiled code holding the
            # interpreter for its whole duration.
            time.sleep(StubSession.delay)
        # Deliberately different shapes, so a test that read the wrong one could not pass.
        if names == ["pooler_output"]:
            return [np.array([[3.0, 4.0]], dtype=np.float32)]
        return [np.zeros((1, 196, 768), dtype=np.float32)]


class StubVocabulary:
    #: Every string the vocabulary was asked to read, in order. Class-level because the embedder
    #: builds its own, so there is no instance here for a test to hold.
    handed: ClassVar[list[str]] = []

    def __init__(self, model_file: str) -> None:
        self.model_file = model_file

    def encode(self, text: str) -> list[int]:
        StubVocabulary.handed.append(text)
        return [7] * len(text.split())

    def eos_id(self) -> int:
        return 1


@pytest.fixture
def installed(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every file of the compact set, on disk, with the digests the catalog expects."""
    StubVocabulary.handed = []
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", StubSession)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: ["CPUExecutionProvider"])
    import sentencepiece

    monkeypatch.setattr(sentencepiece, "SentencePieceProcessor", StubVocabulary)

    store = semantic_weights.store(settings)
    store.directory().mkdir(parents=True, exist_ok=True)
    catalog = dict(semantic_weights.CATALOG)
    for weight_id in ("compact.pictures", "compact.words", "vocabulary"):
        weight = catalog[weight_id]
        payload = f"stand-in for {weight_id}".encode()
        replaced = type(weight)(
            **{
                **{field: getattr(weight, field) for field in weight.__slots__},
                "digest": blake3(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        )
        catalog[weight_id] = replaced
        (store.directory() / f"{replaced.id}{replaced.suffix}").write_bytes(payload)
    monkeypatch.setattr(semantic_weights, "CATALOG", catalog)
    monkeypatch.setattr(
        semantic_weights,
        "_VOCABULARY",
        catalog["vocabulary"],
        raising=False,
    )


def test_a_set_of_models_that_does_not_exist_is_refused() -> None:
    with pytest.raises(semantic_weights.WeightError, match="no set of models"):
        semantic_weights.working_set("imaginary")


def test_nothing_is_installed_on_a_fresh_machine(settings: Settings) -> None:
    assert Embedder(settings, machine()).installed() is False


def test_every_file_of_the_set_present_reads_as_installed(
    settings: Settings, installed: None
) -> None:
    assert Embedder(settings, machine()).installed() is True


def test_a_fresh_embedder_has_nothing_broken_to_report(settings: Settings) -> None:
    """The device's state is read off the runner, never assumed: nothing has died yet."""
    assert Embedder(settings, machine()).broken is None


async def test_a_picture_is_described_from_the_pooled_output(
    settings: Settings, installed: None
) -> None:
    """By name. The other output is first and is one row per patch, so taking output zero returns
    a confident answer of entirely the wrong shape."""
    embedder = Embedder(settings, machine())

    described = await embedder.describe_pictures([picture()])

    assert described == [pytest.approx([0.6, 0.8])]
    assert embedder.loaded_picture_model().session.asked == [["pooler_output"]]


async def test_describing_no_pictures_costs_nothing(settings: Settings) -> None:
    assert await Embedder(settings, machine()).describe_pictures([]) == []


async def test_a_typed_query_is_described(settings: Settings, installed: None) -> None:
    described = await Embedder(settings, machine()).describe_words("a cat outdoors")

    assert described == pytest.approx([0.6, 0.8])


def test_a_short_query_is_padded_to_the_length_the_model_reads(
    settings: Settings, installed: None
) -> None:
    """The model has a fixed input length, and what fills a short one is the vocabulary's own
    padding symbol, which the model was trained to expect in exactly that place."""
    embedder = Embedder(settings, machine())

    symbols = embedder._symbols("two words")

    assert len(symbols) == MAX_SYMBOLS
    assert symbols[:3] == [7, 7, 1]
    assert set(symbols[3:]) == {0}


def test_a_query_longer_than_the_model_reads_is_cut_rather_than_refused(
    settings: Settings, installed: None
) -> None:
    """Somebody pasting a paragraph gets an answer about the beginning of it, which is better than
    an error."""
    embedder = Embedder(settings, machine())

    symbols = embedder._symbols(" ".join(["word"] * 200))

    assert len(symbols) == MAX_SYMBOLS


def test_a_query_that_reaches_the_limit_still_ENDS_with_the_end_marker(
    settings: Settings, installed: None
) -> None:
    """The cut is taken before the marker is added, so the one input long enough to need it is not
    the one input that loses it.

    Cutting after the marker (`[*encode(text), eos()][:MAX_SYMBOLS]`) drops it the moment the text
    alone fills the length, and the model reads the last position, so the query it is handed ends
    where the sentence happened to stop.
    """
    embedder = Embedder(settings, machine())

    symbols = embedder._symbols(" ".join(["word"] * 200))

    assert symbols[-1] == 1, symbols[-5:]
    assert symbols.count(1) == 1, symbols


@pytest.mark.parametrize(
    ("typed", "prepared"),
    [
        ("Red Sports Car", "red sports car"),
        ("A Woman At The Beach", "a woman at the beach"),
        ("SNOW", "snow"),
        ("blonde hair?", "blonde hair"),
        ("a  dog   in the snow", "a dog in the snow"),
        ("  leading and trailing  ", "leading and trailing"),
        ("well-lit, indoors.", "welllit indoors"),
        ("already plain", "already plain"),
    ],
)
def test_a_typed_query_is_canonicalised_the_way_the_publisher_says(
    typed: str, prepared: str
) -> None:
    """Lower case, punctuation out, runs of whitespace collapsed: what the model was trained on.

    `tokenizer_config.json`, which ships beside the weights Sift pins by digest, carries
    `do_lower_case: True`. A capitalised query does not merely rank differently: it collapses.
    `Red Sports Car` can share NONE of its top twelve with `red sports car`, and unrelated
    capitalised queries share most of their top twelve with each other: the words stop saying
    anything.
    """
    assert canonical(typed) == prepared


def test_the_vocabulary_is_handed_the_canonical_text_and_not_what_was_typed(
    settings: Settings, installed: None
) -> None:
    """The property that matters, asserted where it takes effect rather than on the helper.

    `canonical` being right is not the same as its being CALLED, and a search box is not the place
    for it either: this is a fact about what the model reads, so every caller has to pass through
    it whether it knows the rule or not.
    """
    embedder = Embedder(settings, machine())

    embedder._symbols("A Woman At The Beach!")

    assert StubVocabulary.handed[-1] == "a woman at the beach"


def test_the_vocabulary_is_read_once_and_kept(settings: Settings, installed: None) -> None:
    embedder = Embedder(settings, machine())

    first = embedder._load_vocabulary()
    again = embedder._load_vocabulary()

    assert first is again


def test_switching_off_drops_the_models_and_the_vocabulary(
    settings: Settings, installed: None
) -> None:
    embedder = Embedder(settings, machine())
    embedder._load_vocabulary()

    embedder.unload()

    assert embedder._vocabulary is None


def test_the_device_and_the_set_are_readable_so_a_change_can_be_noticed(
    settings: Settings,
) -> None:
    embedder = Embedder(settings, machine(cuda=True), family="full", device="nvidia")

    assert embedder.family == "full"
    assert embedder.device_name == "nvidia"
    assert embedder.revision == semantic_weights.CATALOG["full.pictures"].revision


async def test_describing_a_picture_leaves_the_event_loop_answerable(
    settings: Settings, installed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call that holds the event loop shows up as a frozen screen rather than as anything a
    check reports, and a model call is the likeliest one to do it.

    The model here **really blocks**, for a fifth of a second, in the same synchronous way a real
    one does. Something else on the loop counts how often it gets a turn during that. Made on the
    loop, the count would be zero and this test would fail; made off it, the loop keeps running the
    whole time.
    """
    monkeypatch.setattr(StubSession, "delay", 0.05)
    embedder = Embedder(settings, machine())

    async def count_turns_during(work: Awaitable[object]) -> int:
        """How many turns something else on the loop gets while `work` runs."""
        turns = 0

        async def heartbeat() -> None:
            nonlocal turns
            while True:
                await asyncio.sleep(0.001)
                turns += 1

        beating = asyncio.create_task(heartbeat())
        try:
            await work
        finally:
            beating.cancel()
        return turns

    # What this machine's clock can deliver over the same stretch with nothing blocking at all.
    #
    # MEASURED RATHER THAN ASSUMED: a fixed count would be a number about a machine and not about
    # the code. `asyncio.sleep(0.001)` really is a millisecond on Linux and is about fifteen on
    # Windows, whose timer does not go finer, so the same correct behaviour counts two hundred-odd
    # turns on one and a dozen on the other.
    baseline = await count_turns_during(asyncio.sleep(0.2))
    ticks = await count_turns_during(embedder.describe_pictures([picture() for _ in range(4)]))

    # Four calls of 50 ms each is 200 ms of blocked thread. A loop that was free the whole time gets
    # as many turns as an idle one; a held one gets none. Half is the line between the two, and it is
    # nowhere near either: made on the loop this is zero.
    assert ticks >= baseline // 2, f"{ticks} turns against {baseline} with nothing blocking"
