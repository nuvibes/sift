# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loading a model and running it, on a device chosen when it is loaded.

The runtime loads only in the model process; a missing device fails, never falls back."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import numpy as np

from sift.kernel.budget import STEP_BACK_SHARE, WHOLE_DEVICE
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger
from sift.kernel.ml.weights import Weight, WeightStore

if TYPE_CHECKING:
    from sift.kernel.config import Settings

log = get_logger(__name__)


class DeviceUnavailable(Exception):
    """A device was asked for and cannot be used. The message names it and says why."""


class DeviceLost(DeviceUnavailable):
    """The device stopped answering under a session; the queue holds work that needs it."""


def _device_errors(onnxruntime: Any) -> tuple[type[BaseException], ...]:
    """The runtime's own failure classes, named as they share no base, plus `RuntimeError`."""
    state = getattr(getattr(onnxruntime, "capi", None), "onnxruntime_pybind11_state", None)
    named = tuple(
        getattr(state, name)
        for name in ("EPFail", "RuntimeException", "Fail", "EngineError")
        if state is not None and hasattr(state, name)
    )
    return (*named, RuntimeError)


#: What each device needs, as the runtime names it; `cpu` is always there, so absent.
_DEVICES: dict[str, tuple[str, str]] = {
    "nvidia": ("CUDAExecutionProvider", "an NVIDIA graphics card"),
}

#: Every device a setting offers and its label, beside the table above so the features share it.
_OFFERED: dict[str, str] = {"cpu": "CPU", "nvidia": "GPU"}
DEVICES: tuple[str, ...] = tuple(_OFFERED)
DEVICE_LABELS: tuple[str, ...] = tuple(_OFFERED.values())

#: Checked against each other at import, so no menu item fails only when picked.
if set(_DEVICES) - set(_OFFERED):  # pragma: no cover - a declaration error, caught at import
    raise RuntimeError(f"devices with no menu entry: {sorted(set(_DEVICES) - set(_OFFERED))}")
if set(_OFFERED) - set(_DEVICES) != {"cpu"}:  # pragma: no cover - same
    raise RuntimeError(
        f"menu entries with nothing driving them: {sorted(set(_OFFERED) - set(_DEVICES) - {'cpu'})}"
    )

#: Vague rather than wrong; every real caller passes its own word.
_ANONYMOUS = "This feature"

#: Where to turn the card on, shared by the disclosure and the refusal so they point alike.
_WHERE_TO_TURN_IT_ON = "under GPU in Settings > Performance"

DEVICE_DISCLOSURE = (
    "Sift ships the CPU-only build of its model runtime, so the GPU can't be used until the "
    f"GPU's own runtime is added. Sift can download it {_WHERE_TO_TURN_IT_ON}. It's about 1.3 GB, "
    "downloaded once and stored next to your libraries, so an update doesn't remove it and a new "
    "library needs nothing. It needs an NVIDIA GPU and a current driver, and nothing else "
    "installed by hand."
)


def _unknown_device(device: str) -> str:
    return (
        f"{device!r} is not a device Sift knows how to use. Choose the processor, or a "
        f"supported card: {', '.join(sorted(_DEVICES))}."
    )


def why_unusable(device: str, available: Sequence[str], *, feature: str = _ANONYMOUS) -> str | None:
    """Why this device cannot be used on this installation, or None; asked when it is chosen."""
    entry = _DEVICES.get(device)
    if entry is None:
        return None if device == "cpu" else _unknown_device(device)
    provider, description = entry
    if provider in available:
        return None
    return (
        f"{feature} cannot use {description} yet: the runtime that drives it is not installed. "
        f"Sift can fetch it for you {_WHERE_TO_TURN_IT_ON}, "
        f"and this setting will take the card once it has."
    )


def resolve_provider(
    device: str,
    hardware: HardwareReport,
    available: Sequence[str],
    *,
    feature: str = _ANONYMOUS,
) -> list[str]:
    """The runtime backends to load on, or a failure: no device, or no driving software."""
    if device == "cpu":
        return ["CPUExecutionProvider"]

    entry = _DEVICES.get(device)
    if entry is None:
        raise DeviceUnavailable(_unknown_device(device))
    provider, description = entry

    present = hardware.cuda if device == "nvidia" else False
    if not present:
        raise DeviceUnavailable(
            f"{feature} was set to use {description}, and none is visible to Sift. If this "
            "machine has one, its driver has to be installed and working. Set the device "
            "back to the processor to carry on without it."
        )
    refusal = why_unusable(device, available, feature=feature)
    if refusal is not None:
        raise DeviceUnavailable(refusal)
    # The processor behind the device covers single operations it cannot do; not a fallback.
    return [provider, "CPUExecutionProvider"]


#: A measured cap; see `session_threads`.
MOST_DEVICE_THREADS = 4


def session_threads(device: str, cpu_count: int) -> int:
    """Threads per loaded model: one on the processor, half the machine up to four on a card.

    On a card they run what the card cannot, and one throttles it; never past the step back."""
    if device == "cpu":
        return 1
    stepped = cpu_count * STEP_BACK_SHARE // WHOLE_DEVICE
    return max(1, min(MOST_DEVICE_THREADS, cpu_count // 2, stepped))


#: Loads the native libraries; set only in the model process, so `Runner` refuses elsewhere.
loader: ModuleType | None = None


def _native() -> ModuleType:
    if loader is None:
        raise DeviceUnavailable("The model runtime is loaded only in the model process.")
    return loader


def _runtime(settings: Settings) -> Any:
    return _native().load_runtime(settings)


def device_refusal(feature: str) -> Callable[[Any], str | None]:
    """Why a `*.device` setting may not take this value, or None: the `refuse` hook.

    Not a validator, which would rewrite a stored card to the processor on every read."""

    def check(value: Any) -> str | None:
        # The processor needs no lookup, which keeps the runtime unimported for most installs.
        if value == "cpu":
            return None
        from sift.kernel.config import get_settings
        from sift.kernel.ml.child import devices_here

        try:
            available = devices_here(get_settings(), feature)
        except DeviceUnavailable as refused:
            return str(refused)
        return why_unusable(value, available, feature=feature)

    return check


@dataclass(frozen=True, slots=True)
class Loaded:
    """A model that is ready to be run, and what it was loaded from."""

    weight: Weight
    session: Any
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    device: str


class Runner:
    """Holds the loaded models, kept per feature per process, one thread per session."""

    def __init__(
        self,
        store: WeightStore,
        hardware: HardwareReport,
        *,
        device: str = "cpu",
        feature: str = _ANONYMOUS,
    ) -> None:
        self._store = store
        self._hardware = hardware
        self._device = device
        self._feature = feature
        self._loaded: dict[str, Loaded] = {}
        self._vocabularies: dict[str, Any] = {}
        self._lock = threading.Lock()
        #: Why the device is dead, for the life of the process: a lost context never comes back.
        self._broken: str | None = None

    @property
    def device(self) -> str:
        return self._device

    @property
    def broken(self) -> str | None:
        """Why the device stopped answering, or None while it has not."""
        return self._broken

    def load(self, weight: Weight) -> Loaded:
        """Load one verified model once; refused after the device has died."""
        with self._lock:
            if self._broken is not None:
                raise DeviceUnavailable(self._broken)
            existing = self._loaded.get(weight.id)
            if existing is not None:
                return existing

            self._store.verify(weight)
            path = self._store.path_of(weight)
            loaded = self._open(weight, path)
            self._loaded[weight.id] = loaded
            log.info(
                "ml.model.loaded",
                namespace=self._store.namespace,
                weight=weight.id,
                revision=weight.revision,
                device=self._device,
            )
            return loaded

    def _open(self, weight: Weight, path: Path) -> Loaded:
        onnxruntime = _runtime(self._store.settings)

        providers = resolve_provider(
            self._device,
            self._hardware,
            onnxruntime.get_available_providers(),
            feature=self._feature,
        )
        options = onnxruntime.SessionOptions()
        threads = session_threads(self._device, self._hardware.cpu_count)
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = threads
        # Startup chatter nobody running Sift can act on.
        options.log_severity_level = 3
        # No fallback at either moment the runtime would make one; a missing device fails.
        try:
            session = onnxruntime.InferenceSession(
                str(path), options, providers=providers, enable_fallback=False
            )
        except _device_errors(onnxruntime) as error:
            if self._device == "cpu":
                raise
            raise DeviceUnavailable(
                f"{_DEVICES[self._device][1]} could not open a model ({error}). Set the device "
                "to the processor, or check the card's driver."
            ) from error
        # Read back, never assumed: the first backend must be the one asked for.
        opened = tuple(session.get_providers())
        if not opened or opened[0] != providers[0]:
            raise DeviceUnavailable(
                f"{_DEVICES.get(self._device, ('', self._device))[1]} was asked for and the model "
                f"opened on {opened[0] if opened else 'nothing'} instead. Set the device to the "
                "processor, or check the card's driver."
            )
        return Loaded(
            weight=weight,
            session=session,
            inputs=tuple(item.name for item in session.get_inputs()),
            outputs=tuple(item.name for item in session.get_outputs()),
            device=self._device,
        )

    def run(
        self, loaded: Loaded, blob: np.ndarray, *, outputs: Sequence[str] | None = None
    ) -> list[np.ndarray]:
        """Run one input through a model; a device failure marks it dead and drops every session."""
        try:
            names = list(loaded.outputs) if outputs is None else list(outputs)
            return list(loaded.session.run(names, {loaded.inputs[0]: blob}))
        except _device_errors(_runtime(self._store.settings)) as error:
            if self._device == "cpu":
                raise
            self._broken = (
                f"{_DEVICES.get(self._device, ('', self._device))[1]} stopped answering "
                f"({error}). Restart Sift to use it again; until then nothing runs on it."
            )
            log.error(
                "ml.device.lost", device=self._device, feature=self._feature, detail=str(error)
            )
            self.unload()
            raise DeviceLost(self._broken) from error

    def encode(self, weight: Weight, text: str) -> tuple[list[int], int]:
        """Text as the symbols of a vocabulary file, and that vocabulary's end symbol."""
        with self._lock:
            vocabulary = self._vocabularies.get(weight.id)
            if vocabulary is None:
                self._store.verify(weight)
                vocabulary = _native().load_vocabulary(self._store.path_of(weight))
                self._vocabularies[weight.id] = vocabulary
                log.info("ml.vocabulary.loaded", weight=weight.id, revision=weight.revision)
        return [int(one) for one in vocabulary.encode(text)], int(vocabulary.eos_id())

    def unload(self) -> None:
        """Drop every session. Called when a feature is switched off, so the memory goes back."""
        with self._lock:
            self._loaded.clear()
            self._vocabularies.clear()
