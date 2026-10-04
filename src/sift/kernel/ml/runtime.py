# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loading a model and running it, on a device chosen when it is loaded.

Two things about this module are deliberate and neither is obvious from the code.

**The runtime is imported inside the function that needs it, not at the top of the file.** The
inference runtime costs a couple of hundred milliseconds and a hundred megabytes of memory to
import, and the overwhelming majority of installs will never turn any of this on. An import at
module scope would charge every one of them for a capability they are not using, at boot, before
anything is served. This is the one place in Sift where a late import is the correct shape rather
than a smell, so it is written down here rather than argued about in review.

**A device that was asked for and is not there is a failure, not a fallback.** Falling back to the
processor when a graphics card was requested turns "why is this taking nine hours" into a question
with no answer anywhere on the machine: the work still happens, the result is still correct, and
nothing anywhere says the reason. So it stops and says which device, and why not.

**This is the kernel's copy, and it belongs to no feature.** It knows how to choose a device and
how to hold a loaded model; it knows nothing about what any model is for. The words a person reads
when a device is missing name the feature that asked, because "a device is unavailable" with no
subject is not something anybody can act on, so the caller supplies that word and nothing else
about itself.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from sift.kernel.budget import STEP_BACK_SHARE, WHOLE_DEVICE
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger
from sift.kernel.ml.weights import Weight, WeightStore
from sift.kernel.threads import waits_on_storage

log = get_logger(__name__)


class DeviceUnavailable(Exception):
    """A device was asked for and cannot be used. The message names it and says why."""


class DeviceLost(DeviceUnavailable):
    """The device stopped answering underneath a session. Work that needs it waits rather than
    spending attempts; the job queue holds this error for a while."""


def _device_errors(onnxruntime: Any) -> tuple[type[BaseException], ...]:
    """The runtime's own failure classes, resolved from the module that is loaded.

    They derive from `Exception` directly rather than from a common base, so they are named. A
    backend that fails to open, a context that has died underneath a running session, and the
    engine giving up all arrive as one of these, or as a plain `RuntimeError` from the runtime's
    Python layer.
    """
    state = getattr(getattr(onnxruntime, "capi", None), "onnxruntime_pybind11_state", None)
    named = tuple(
        getattr(state, name)
        for name in ("EPFail", "RuntimeException", "Fail", "EngineError")
        if state is not None and hasattr(state, name)
    )
    return (*named, RuntimeError)


#: What each choice needs from the machine, and what it is called to the runtime. `cpu` is absent
#: because it is always available: that is what makes it the guarantee rather than an option.
_DEVICES: dict[str, tuple[str, str]] = {
    "nvidia": ("CUDAExecutionProvider", "an NVIDIA graphics card"),
}

#: Every device a setting may offer, in the order a menu shows them, and what each is called on
#: screen. Declared HERE, beside the table above that says what each one needs, because the two
#: features that offer the choice would otherwise each hold their own copy of this pair: two lists
#: that have to agree with a third, with nothing checking that they do.
#:
#: ONE mapping, and the two tuples the registry wants are read off it. Written as two tuples they
#: would be a fourth and fifth list to keep in step, in the one place whose whole job is to stop
#: that: a name added in one and not the other silently shifts every label by one.
_OFFERED: dict[str, str] = {"cpu": "CPU", "nvidia": "GPU"}
DEVICES: tuple[str, ...] = tuple(_OFFERED)
DEVICE_LABELS: tuple[str, ...] = tuple(_OFFERED.values())

#: The two tables are written by hand and describe the same set, so they are checked against each
#: other at import: a device that can be offered and has no entry saying what drives it would be a
#: menu item that fails only when somebody picks it.
if set(_DEVICES) - set(_OFFERED):  # pragma: no cover - a declaration error, caught at import
    raise RuntimeError(f"devices with no menu entry: {sorted(set(_DEVICES) - set(_OFFERED))}")
if set(_OFFERED) - set(_DEVICES) != {"cpu"}:  # pragma: no cover - same
    raise RuntimeError(
        f"menu entries with nothing driving them: {sorted(set(_OFFERED) - set(_DEVICES) - {'cpu'})}"
    )

#: How a feature is named in a message when the caller did not say. Deliberately vague rather than
#: wrong: every real caller passes its own word.
_ANONYMOUS = "This feature"

#: What somebody has to do to actually use a graphics card, written once and shown under BOTH
#: settings that offer the choice.
#:
#: The refusal alone is not enough. Choosing the card is refused with a sentence saying the
#: software is not installed, which answers "why did that not work" and leaves "then how DO I turn
#: it on" unanswered, so the person who wanted the card would have to find out by trying, and then
#: still not know.
#:
#: `sift.kernel.ml.accel` fetches the graphics-card runtime the same way a model is fetched, and the
#: words below are what points somebody at it. The two have to move together: a disclosure that
#: describes the wrong door is a worse answer than none.
#: Where the person is sent: one string for the disclosure below and the refusal after it, so the
#: two cannot point at different places. The path is the one the settings search takes pasted.
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
    """Why this device cannot be used on this INSTALLATION, or None if it can. No machine involved.

    The half of the check below that is true before anything is plugged in: whether the software
    that drives a device was shipped at all. Separated out because it is the half that can be
    answered the moment somebody CHOOSES a device, rather than hours later inside the job that
    finally tried to use it (see `device_validator`).

    The other half, whether the card is actually in this machine, needs the hardware report and
    stays in `resolve_provider`.
    """
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
    """Which runtime backend to load a model on, or a failure explaining why not.

    Both halves are checked, because they fail for different reasons and only one of them is
    visible from inside the application. The hardware report says whether the device is *there*;
    the runtime's own list says whether the software that drives it was ever installed. A machine
    with a graphics card and no driver libraries, and a machine with the libraries and no card,
    produce the same silence and need different answers.
    """
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
    # The processor stays on the list behind the device, as the runtime's answer for any single
    # operation the device cannot do. That is not a silent fallback: the model still runs on the
    # device that was asked for, and if it could not, loading it fails above.
    return [provider, "CPUExecutionProvider"]


#: The most threads a session on a device is given, however many the machine has.
#:
#: A cap rather than the runtime's own answer, and the number is measured. See `session_threads`.
MOST_DEVICE_THREADS = 4


def session_threads(device: str, cpu_count: int) -> int:
    """How many threads one loaded model may use, which is a question about the DEVICE.

    **One thread is right on the processor and wrong on a card, and the reason is that the
    setting reaches something different in each case.** On the processor it is the whole model:
    Sift runs inference beside decoding, playback and the pages, and a model allowed the machine
    is a person waiting on a video. One thread is a deliberate price: the picture model runs about
    five times slower at one thread than at eight, and the choice buys back the machine.

    On a card the setting reaches only the operators the card cannot run, which the runtime keeps
    on the processor, and capping THOSE at one thread throttles the card behind them: the same
    frames on the card take more than twice as long at one thread as at four, and eight or the
    runtime's own choice (every logical processor) is barely faster than four. So the runtime's
    own answer is past the knee, and the figure is capped rather than handed over.

    Half the machine, capped at four: four is most of what eight gives, and it leaves the decoders
    (which are what the model is waiting on for its next frame) the rest of the box. A model
    whose whole graph is on the card is unaffected either way (the face recognizer runs the same
    at one thread as at eight, within noise).

    And never past the step back's share of the device (`kernel.budget.STEP_BACK_SHARE`): a
    session's threads are fixed when the model loads and cannot follow the share from run to run,
    so they are held to the smaller of the two from the start, and a person at the keyboard never
    meets a runtime sized for an empty machine.
    """
    if device == "cpu":
        return 1
    stepped = cpu_count * STEP_BACK_SHARE // WHOLE_DEVICE
    return max(1, min(MOST_DEVICE_THREADS, cpu_count // 2, stepped))


def _runtime() -> Any:
    """Import the inference runtime, with the graphics-card build in front of it where there is one.

    ONE PLACE for both callers, because a second runtime can be installed beside the first, and
    putting the installed one at the front of the path has to happen BEFORE the first import and
    cannot be undone after. Two import sites means one of them is the first one, and which of the
    two it is depends on what somebody happened to click.
    """
    from sift.kernel.config import get_settings
    from sift.kernel.ml import accel

    accel.enable(get_settings())
    import onnxruntime

    return onnxruntime


@waits_on_storage
def providers_now() -> tuple[str, ...]:
    """What the runtime offers, asked fresh and always through the one import site.

    NOT CACHED, unlike `installed_providers`. This is what a screen asks the moment somebody
    changes a device, and the answer changes when a runtime is installed beside it.

    Through `_runtime`, never a module's own `import onnxruntime`: a second import site would be
    a way for the PROCESSOR build to be the first one loaded, and the first one in wins for the
    life of the process, so a face job running before anybody opened the settings screen would
    decide that the card could not be used, for good, on a machine where it works perfectly.
    """
    return tuple(_runtime().get_available_providers())


@lru_cache(maxsize=1)
def installed_providers() -> tuple[str, ...]:
    """Which runtime backends this installation actually has.

    THE IMPORT IS THE POINT, and it is why this is cached rather than read.

    The note at the top of this module says the inference runtime is never imported at boot, and
    that still holds: nothing calls this while Sift is starting. It is called when an admin picks a
    device on the settings screen, which is a deliberate and rare action, and it costs one import
    (a fraction of a second), once for the life of the process. An installation
    that never touches the choice never pays it.

    Cached, and the cache is CLEARED by an install rather than waiting for a restart (see
    `sift.kernel.ml.accel`). It has to be one or the other: an answer of "no card" that outlives the
    thing that made it true is a person watching a download finish and then being told the download
    did not happen.

    Every failure is the same answer: no providers, which reads as "no device but the processor".
    A settings screen must not fall over because an optional dependency will not import.
    """
    try:
        onnxruntime = _runtime()
    except Exception:  # pragma: no cover - an unimportable runtime is not reproducible in tests
        log.info("ml.runtime_absent")
        return ()
    return tuple(onnxruntime.get_available_providers())


def device_refusal(feature: str) -> Callable[[Any], str | None]:
    """Why a `*.device` setting may not be set to this, or None. Registered as the `refuse` hook.

    WHY THE REFUSAL IS HERE AND NOT IN THE JOB.

    A card is one of the declared choices, so without this it would store, the screen would show
    it, and the refusal would come later and somewhere else (inside the job that eventually loads
    a model), with the settings screen saying the card is in use while every file is done on the
    processor. A choice that cannot work has to be refused at the moment it is made, in front of the
    person making it.

    WHY IT IS A `refuse` AND NOT A `validator`, WHICH IS NOT A DETAIL. A validator also runs when a
    stored value is read back, and a value it rejects is quietly replaced by the default. Written
    as one, this would turn a stored "nvidia" into "cpu" on every read, so recognition, which
    refuses to run on a device that is not there precisely so that nobody wonders why it took nine
    hours, would read its own setting as the processor and run on the processor. See `Refusal` in
    the registry.

    Only the software half is checked. Whether the card is physically present is deliberately left
    to `resolve_provider`: a machine can be given a card while Sift is not looking, and refusing to
    even store the choice would make that unfixable without editing the database.
    """

    def check(value: Any) -> str | None:
        # The processor needs nothing looked up: it is the guarantee. It is also what keeps the
        # inference runtime UNIMPORTED for any installation that never chooses a card: take this
        # line out and the first settings write of any kind pays for a runtime almost nobody uses,
        # which is the one thing the note at the top of this module exists to prevent.
        if value == "cpu":
            return None
        return why_unusable(value, installed_providers(), feature=feature)

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
    """Holds the loaded models. One per feature per process, made when it is first used.

    Sessions are kept rather than made per call: loading a large model reads and prepares hundreds
    of megabytes, which is a hundred times the cost of running it once.

    Inference is single-threaded per session on purpose. The work arrives as a queue of files
    already running several at a time, so a session that spawned a thread per core would have every
    worker fighting every other worker for the same processor, measurably slower than each doing
    its own work on one core.
    """

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
        self._lock = threading.Lock()
        #: Why the device cannot be used any more, once it has failed underneath a session. Set
        #: for the life of the process: a lost context does not come back, and a session opened
        #: after one would be opened on a card the runtime can no longer drive.
        self._broken: str | None = None

    @property
    def device(self) -> str:
        return self._device

    @property
    def broken(self) -> str | None:
        """Why the device stopped answering, or None while it has not. What a screen says."""
        return self._broken

    def load(self, weight: Weight) -> Loaded:
        """Load one model, verifying the file first. Repeated calls hand back the same session.

        Refused outright once the device has died underneath this process (see `run`). Loading
        again would open a session on a card the runtime can no longer drive, and the failure
        would arrive as the next job's rather than as the reason it is.
        """
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
        onnxruntime = _runtime()

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
        # The runtime is chatty at startup about graph shapes it would rather were different. None
        # of it is actionable by whoever is running Sift, and it lands in their log at every boot.
        options.log_severity_level = 3
        # NO FALLBACK, at either moment the runtime would make one. Left at its default, a backend
        # that fails to open is quietly replaced by the processor at construction, and one that
        # fails mid-run is replaced once at run time, with a `print`, which is nowhere. Either
        # way the session, the log line and the settings screen would go on saying the card's name
        # over work the processor was doing. A device that was asked for and is not there is a
        # failure, not a fallback (the note at the top of this module), and this is where it
        # holds.
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
        # And read back, never assumed: the runtime is asked which backend it actually opened
        # the model on, and the first one has to be the one asked for.
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
        """Run one input through a loaded model and hand back its outputs, in declared order or
        in the order asked for.

        A device that fails underneath a session has failed for the life of the process: a lost
        context does not come back, and every later call on it fails the same way while the
        process says nothing. So the first failure marks the device dead, drops every session,
        and is raised as the reason: the job fails loudly, once, and the settings screen says
        what to do. On the processor a failure is the model's or the input's, and is raised as it
        came.
        """
        try:
            names = list(loaded.outputs) if outputs is None else list(outputs)
            return list(loaded.session.run(names, {loaded.inputs[0]: blob}))
        except _device_errors(_runtime()) as error:
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

    def unload(self) -> None:
        """Drop every session. Called when a feature is switched off, so the memory goes back."""
        with self._lock:
            self._loaded.clear()
