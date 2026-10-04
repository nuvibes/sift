# SPDX-License-Identifier: AGPL-3.0-or-later
"""Running the face models, on a device chosen when they are loaded.

The machinery is the kernel's: choosing a device, refusing to pretend when it is missing,
verifying a file before loading it, and holding a prepared session so it is not rebuilt per call.
A second feature needed all of it and a feature may not import another feature, so it lives in one
place and both ask for it.

**What is here is the word "Recognition".** The kernel's copy will not invent a subject for a
sentence a person has to act on: "a graphics card is not available" says nothing about what was
trying to use one, on a machine where two different features might have been. So the caller
supplies the word, and this is where the face pass supplies its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar

import numpy as np

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger
from sift.kernel.ml.child import ChildRunner as _ChildRunner
from sift.kernel.ml.runtime import DeviceLost, DeviceUnavailable, Loaded
from sift.kernel.ml.runtime import Runner as _Runner
from sift.kernel.ml.runtime import resolve_provider as _resolve_provider
from sift.slices.faces import weights

__all__ = [
    "IN_FLIGHT",
    "ChildRunner",
    "DeviceUnavailable",
    "Loaded",
    "Runner",
    "RunnerLike",
    "resolve_provider",
]

log = get_logger(__name__)

#: How this feature names itself in a message about a device it cannot use.
FEATURE = "Recognition"

#: What the models are working on at this moment: the file a scan is reading, or the picture a
#: starter is checked from. Set by the pass, read only when the card is lost under it, so the log
#: names the input a lost device was handed. The kernel's own line says the device and the error,
#: and nothing about what the models were asked; without this a fault that recurs on one input
#: could not be traced back to it.
IN_FLIGHT: ContextVar[str | None] = ContextVar("faces_in_flight", default=None)


def _said_the_input(loaded: Loaded, blob: np.ndarray) -> None:
    """The line a lost device leaves behind: the model, the shape it was handed, and the input."""
    log.error(
        "faces.device.lost_on",
        weight=loaded.weight.id,
        shape=list(blob.shape),
        dtype=str(blob.dtype),
        input=IN_FLIGHT.get(),
    )


def resolve_provider(device: str, hardware: HardwareReport, available: Sequence[str]) -> list[str]:
    """Which runtime backend to load a face model on, or a failure explaining why not."""
    return _resolve_provider(device, hardware, available, feature=FEATURE)


class Runner(_Runner):
    """Holds the loaded face models. One per process, made when the feature is first used."""

    def __init__(
        self, settings: Settings, hardware: HardwareReport, *, device: str = "cpu"
    ) -> None:
        super().__init__(weights.store(settings), hardware, device=device, feature=FEATURE)

    def run(
        self, loaded: Loaded, blob: np.ndarray, *, outputs: Sequence[str] | None = None
    ) -> list[np.ndarray]:
        try:
            return super().run(loaded, blob, outputs=outputs)
        except DeviceLost:
            _said_the_input(loaded, blob)
            raise


class ChildRunner(_ChildRunner):
    """The same models, in a process of their own, below normal priority. What the service runs
    on; `Runner` above is the same surface in this process, for a test that stands the runtime
    in. See `sift.kernel.ml.child` for why the process is the unit."""

    def __init__(
        self, settings: Settings, hardware: HardwareReport, *, device: str = "cpu"
    ) -> None:
        super().__init__(weights.store(settings), hardware, device=device, feature=FEATURE)

    def run(
        self, loaded: Loaded, blob: np.ndarray, *, outputs: Sequence[str] | None = None
    ) -> list[np.ndarray]:
        try:
            return super().run(loaded, blob, outputs=outputs)
        except DeviceLost:
            _said_the_input(loaded, blob)
            raise


#: Either runner: the models are driven the same way whichever process holds them.
RunnerLike = Runner | ChildRunner
