# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one import site of the inference runtime and of the vocabulary, used only in the model
process.

The server never imports this module, so a native library that crashes as it loads takes a child
with it and never the server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sift.kernel.config import Settings
from sift.kernel.ml import accel


def load_runtime(settings: Settings) -> Any:
    """The runtime, with an installed graphics-card build put in front first: the first one in wins
    for the life of the process."""
    accel.enable(settings)
    import onnxruntime

    return onnxruntime


def load_vocabulary(path: Path) -> Any:
    """A sentencepiece vocabulary, read from its file."""
    import sentencepiece

    return sentencepiece.SentencePieceProcessor(model_file=str(path))


def providers(settings: Settings) -> tuple[str, ...]:
    """Which backends the runtime offers in this process."""
    return tuple(load_runtime(settings).get_available_providers())
