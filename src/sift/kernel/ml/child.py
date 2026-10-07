# SPDX-License-Identifier: AGPL-3.0-or-later
"""The inference runtime in a process of its own, below normal priority, and replaceable.

Inference in the backend's own process, at normal priority, would be two faults together. The
processor half: a face pass on the processor would take the machine's attention from a person
watching a video, because a thread cannot be put below the process it is in and the runtime's
threads are threads. The card half: a CUDA context, once lost, is dead for that process for ever
(every later call fails and nothing exits), so the only recovery would be restarting Sift, and
the runtime's in-process guard could do no better than say so loudly.

A child process answers both. It is created at the class the seam gives every background tool
(`Priority.BACKGROUND`), so the decode and the model share the machine the way a scrub strip
and a thumbnail already do. And when its device dies underneath it, the child exits and is
started again: one process is restarted rather than the application, and the models are loaded
again on the next ask.

The protocol is a length-prefixed JSON frame on the child's own pipes, each array's bytes after
it: JSON rather than pickle, so nothing read can run code. Nothing here listens on a port.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import struct
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import Callable, Sequence
from typing import IO, Any

import numpy as np

from sift.kernel import device_load
from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger, level_name, redacts_personal
from sift.kernel.ml.runtime import _ANONYMOUS, _DEVICES, DeviceLost, DeviceUnavailable, Loaded
from sift.kernel.ml.weights import Weight, WeightError, WeightStore
from sift.kernel.subprocess import Priority, creation_flags, launch_prefix, step_aside
from sift.kernel.threads import waits_on_storage

log = get_logger(__name__)

#: The module the child runs. Named here and nowhere else, so the parent and the worker cannot
#: come to disagree about which program is on the other end of the pipe.
WORKER_MODULE = "sift.kernel.ml.worker"

#: The worker's argument for a child that answers the device question and exits.
DEVICES_FLAG = "--devices"

#: How long that child has to answer. Loading the runtime takes seconds on a slow disk.
DEVICES_TIMEOUT_SECONDS = 60.0

#: How long a lost device is held against, before the next ask starts a child again. A card that
#: is genuinely dead would otherwise be asked again by every job, each paying a process start to
#: find out; a minute is long enough for the driver to settle and short enough to notice.
LOST_DEVICE_HOLD_SECONDS = 60.0

#: How long the child is given to answer a load or a run. A cold first CUDA context is seconds
#: and a large batch on the processor is tens of them; a model that has genuinely hung is the one
#: thing this cannot tell from one that is slow, so the limit is generous. Past it the child is
#: ended, because the alternative is a job thread that never comes back.
ANSWER_TIMEOUT_SECONDS = 600.0

_HEADER = struct.Struct(">I")

#: The two records a frame can carry besides the plain values JSON has, each as a marked object.
_RECORDS: dict[str, type[Weight | HardwareReport]] = {
    "$weight": Weight,
    "$hardware": HardwareReport,
}


class _Frame:
    """A frame being written: the JSON-able shape of a value, with its arrays set aside."""

    def __init__(self) -> None:
        self.arrays: list[np.ndarray] = []

    def plain(self, value: Any) -> Any:
        if isinstance(value, np.ndarray):
            contiguous = np.ascontiguousarray(value)
            self.arrays.append(contiguous)
            return {"$array": len(self.arrays) - 1}
        for mark, kind in _RECORDS.items():
            if isinstance(value, kind):
                return {mark: self.plain(dataclasses.asdict(value))}
        if isinstance(value, dict):
            return {str(key): self.plain(one) for key, one in value.items()}
        if isinstance(value, list | tuple):
            return [self.plain(one) for one in value]
        return value


def _rich(value: Any, arrays: list[np.ndarray]) -> Any:
    """The inverse of `_Frame.plain`, with the arrays read back in."""
    if isinstance(value, dict):
        if len(value) == 1:
            (mark, inner), *_ = value.items()
            if mark == "$array":
                return arrays[int(inner)]
            if mark in _RECORDS:
                # A record's sequences are tuples; JSON made them lists.
                facts: dict[str, Any] = {
                    k: tuple(v) if isinstance(v, list) else v
                    for k, v in _rich(inner, arrays).items()
                }
                return _RECORDS[mark](**facts)
        return {key: _rich(one, arrays) for key, one in value.items()}
    if isinstance(value, list):
        return [_rich(one, arrays) for one in value]
    return value


def send(pipe: IO[bytes], frame: dict[str, Any]) -> None:
    """One frame down the pipe: the length of its JSON, the JSON, then each array's bytes."""
    shape = _Frame()
    plain = shape.plain(frame)
    header = json.dumps(
        {
            "frame": plain,
            "arrays": [
                {"dtype": one.dtype.str, "shape": list(one.shape), "bytes": one.nbytes}
                for one in shape.arrays
            ],
        }
    ).encode()
    pipe.write(_HEADER.pack(len(header)))
    pipe.write(header)
    for one in shape.arrays:
        pipe.write(one.tobytes())
    pipe.flush()


def _exactly(pipe: IO[bytes], length: int) -> bytes | None:
    """`length` bytes, or None if the pipe ended first."""
    body = pipe.read(length)
    return body if len(body) == length else None


def receive(pipe: IO[bytes]) -> dict[str, Any] | None:
    """One frame off the pipe, or None at the end of it."""
    head = _exactly(pipe, _HEADER.size)
    if head is None:
        return None
    (length,) = _HEADER.unpack(head)
    body = _exactly(pipe, length)
    if body is None:
        return None
    header = json.loads(body)
    arrays: list[np.ndarray] = []
    for facts in header["arrays"]:
        raw = _exactly(pipe, int(facts["bytes"]))
        if raw is None:
            return None
        arrays.append(np.frombuffer(raw, dtype=np.dtype(facts["dtype"])).reshape(facts["shape"]))
    frame = _rich(header["frame"], arrays)
    return frame if isinstance(frame, dict) else None


class ChildStopped(DeviceUnavailable):
    """The model process ended while it was asked something; `how` says how it ended."""

    def __init__(self, feature: str, how: str) -> None:
        super().__init__(
            f"The model process stopped while {feature} was using it ({how}). "
            "It is started again on the next ask."
        )
        self.how = how


class ChildRunner:
    """The runtime, driven in a child process. The same surface `Runner` has, so a feature can
    hold either without knowing which."""

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
        self._lock = threading.Lock()
        self._child: subprocess.Popen[bytes] | None = None
        self._loaded: dict[str, Loaded] = {}
        #: When the device last died underneath the child, for the hold above. None while it
        #: has not, or once the hold has passed.
        self._lost_at: float | None = None
        self._lost_why: str | None = None

    @property
    def device(self) -> str:
        return self._device

    @property
    def broken(self) -> str | None:
        """Why the device cannot be used just now, or None. Clears itself once the hold passes:
        the next ask starts a fresh child on a fresh context."""
        if self._lost_at is None:
            return None
        if time.monotonic() - self._lost_at >= LOST_DEVICE_HOLD_SECONDS:
            self._lost_at = None
            self._lost_why = None
            return None
        return self._lost_why

    def load(self, weight: Weight) -> Loaded:
        """Load one model in the child, verifying the file first. Repeated calls hand back the same
        handle."""
        with self._lock:
            held = self.broken
            if held is not None:
                raise DeviceUnavailable(held)
            existing = self._loaded.get(weight.id)
            if existing is not None:
                return existing
            answer = self._ask({"op": "load", "weight": weight})
            loaded = Loaded(
                weight=weight,
                # There is no session on this side of the pipe. What a feature does with one
                # (run it) goes through `run`, which is the point of the handle.
                session=None,
                inputs=tuple(answer["inputs"]),
                outputs=tuple(answer["outputs"]),
                device=str(answer["device"]),
            )
            self._loaded[weight.id] = loaded
            return loaded

    def run(
        self, loaded: Loaded, blob: np.ndarray, *, outputs: Sequence[str] | None = None
    ) -> list[np.ndarray]:
        """Run one input through a loaded model and hand back its outputs, in declared order or
        in the order asked for. One input per ask: the pipe's cost is the bytes, so inputs worth
        batching are stacked into one array by the caller.
        """
        with self._lock:
            if loaded.weight.id not in self._loaded:
                # The child that loaded it has gone (a lost device, a crash), and this handle
                # is from before. Loaded again, in the child there is now.
                self._loaded[loaded.weight.id] = loaded
                self._ask({"op": "load", "weight": loaded.weight})
            answer = self._ask(
                {
                    "op": "run",
                    "weight_id": loaded.weight.id,
                    "blob": blob,
                    "outputs": None if outputs is None else list(outputs),
                }
            )
            return [np.asarray(one) for one in answer["outputs"]]

    def encode(self, weight: Weight, text: str) -> tuple[list[int], int]:
        """Text as the symbols of a vocabulary file, read in the child, and its end symbol."""
        with self._lock:
            answer = self._ask({"op": "encode", "weight": weight, "text": text})
        return [int(one) for one in answer["ids"]], int(answer["eos"])

    def unload(self) -> None:
        """Drop every session and end the child, so the memory goes back to the machine."""
        with self._lock:
            self._loaded.clear()
            self._end()

    # --- the child ----------------------------------------------------------------------------

    def _ask(self, frame: dict[str, Any]) -> dict[str, Any]:
        """One request to the child, and its answer. Holds `_lock`; the caller took it."""
        child = self._child if self._child is not None else self._start()
        stdin, stdout = _pipes(child)
        try:
            send(stdin, frame)
            answer = self._answer(stdout)
        except TimeoutError:
            self._end(patience=0)
            raise DeviceUnavailable(
                f"The model process did not answer {self._feature} within "
                f"{ANSWER_TIMEOUT_SECONDS:.0f} seconds and was stopped. "
                "It is started again on the next ask."
            ) from None
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._end()
            raise DeviceUnavailable(
                f"The model process stopped while {self._feature} was using it ({error}). "
                "It is started again on the next ask."
            ) from error
        if answer is None:
            self._end()
            raise ChildStopped(self._feature, _how_it_ended(child.returncode))
        if "error" not in answer:
            return answer
        why = str(answer["error"])
        if answer.get("kind") == "weight":
            raise WeightError(why)
        if answer.get("kind") == "device":
            # The child has already exited: a lost context is dead for that process, and the
            # process is the thing this design makes disposable. Held against for a while, then
            # tried again with a fresh one.
            self._end()
            self._lost_at = time.monotonic()
            self._lost_why = (
                f"{_DEVICES.get(self._device, ('', self._device))[1]} stopped answering "
                f"({why}). It is tried again in a minute; until then nothing runs on it."
            )
            log.error("ml.device.lost", device=self._device, feature=self._feature, detail=why)
            raise DeviceLost(self._lost_why)
        raise RuntimeError(why)

    def _start(self) -> subprocess.Popen[bytes]:
        """A fresh child, told what it is running for. Below normal priority, like every tool
        that works for nobody who is waiting."""
        argv = [*launch_prefix(Priority.BACKGROUND), sys.executable, "-m", WORKER_MODULE]
        try:
            child = subprocess.Popen(  # noqa: S603 (a list, never a shell; our own interpreter)
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # The child's own log lines go where the backend's do.
                stderr=None,
                env=os.environ.copy(),
                creationflags=creation_flags(Priority.BACKGROUND),
            )
        except OSError as error:
            raise DeviceUnavailable(f"The model process could not be started ({error}).") from error
        _RUNNING.add(self)
        # Behind everything else on the disk and in memory too, like any tool's reads.
        step_aside(child)
        device_load.own_child(child)
        self._child = child
        stdin, stdout = _pipes(child)
        try:
            send(
                stdin,
                {
                    "op": "hello",
                    "data_dir": str(self._store.settings.data_dir),
                    "cache_dir": str(self._store.settings.cache_dir),
                    "namespace": self._store.namespace,
                    "hardware": self._hardware,
                    "device": self._device,
                    "feature": self._feature,
                    # The child logs the way this process does: same level, same redaction.
                    "log_level": level_name(),
                    "redact_personal": redacts_personal(),
                },
            )
            answer = self._answer(stdout)
        except (TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
            self._end(patience=0)
            why = str(error) or type(error).__name__
            raise DeviceUnavailable(f"The model process did not answer ({why}).") from error
        if answer is None or "error" in answer:
            self._end()
            why = "it exited" if answer is None else str(answer["error"])
            raise DeviceUnavailable(f"The model process could not start ({why}).")
        log.info("ml.child.started", pid=child.pid, device=self._device, feature=self._feature)
        return child

    def devices(self) -> tuple[str, ...] | None:
        """What the runtime in this runner's child offers, or None with no idle child to ask."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            if self._child is None:
                return None
            return tuple(self._ask({"op": "devices"})["devices"])
        finally:
            self._lock.release()

    @staticmethod
    def _answer(pipe: IO[bytes]) -> dict[str, Any] | None:
        """One frame off the child's pipe, or TimeoutError after `ANSWER_TIMEOUT_SECONDS`.

        The read is a thread so the wait for it can have a deadline; `_end` closes a late one.
        """
        box: list[dict[str, Any] | BaseException | None] = []

        def read() -> None:
            try:
                box.append(receive(pipe))
            except BaseException as error:  # carried across the thread, raised below
                box.append(error)

        reader = threading.Thread(target=read, name="ml-child-answer", daemon=True)
        reader.start()
        reader.join(ANSWER_TIMEOUT_SECONDS)
        if reader.is_alive():
            raise TimeoutError
        got = box[0]
        if isinstance(got, BaseException):
            raise got
        return got

    def _end(self, *, patience: float = 5.0) -> None:
        """Stop the child, if there is one, and forget everything it held.

        Closing its input is the ask to stop, and `patience` is how long it gets before it is
        killed. Its output is closed last, once the process is gone: a reader still blocked on
        that pipe (the one `_answer` gave up waiting for) holds the pipe's lock until the
        child's end of it closes, and closing it from here first would wait on that for ever.
        """
        child, self._child = self._child, None
        self._loaded.clear()
        if child is None:
            return
        stdin, stdout = _pipes(child)
        with contextlib.suppress(OSError):
            stdin.close()
        try:
            child.wait(timeout=patience)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        device_load.child_ended(child)
        with contextlib.suppress(OSError):
            stdout.close()
        log.info("ml.child.stopped", pid=child.pid, feature=self._feature)


def _pipes(child: subprocess.Popen[bytes]) -> tuple[IO[bytes], IO[bytes]]:
    """The child's two pipes, which `_start` asked for; typed as optional by the library."""
    assert child.stdin is not None and child.stdout is not None  # noqa: S101 (pipes were asked for)
    return child.stdin, child.stdout


# --- which devices the runtime can drive ------------------------------------------------------

#: Every runner with a child started, so the device question can go to a runtime already loaded.
_RUNNING: weakref.WeakSet[ChildRunner] = weakref.WeakSet()


def runtime_wont_start(feature: str, why: str) -> str:
    """What a screen says when the runtime can't load on this device."""
    return (
        f"{feature} can't run on this device: the model runtime couldn't start ({why}). "
        "Restart Sift to try again."
    )


def _how_it_ended(code: int) -> str:
    if code < 0:
        return f"it was ended by signal {-code}"
    # A Windows crash code reads as the hex its documentation uses.
    return f"it stopped with code 0x{code:08X}" if code > 0xFFFF else f"it stopped with code {code}"


def _one_shot(settings: Settings) -> tuple[str, ...]:
    """Ask a new child, which exits once it has answered. Raises `DeviceUnavailable` saying why."""
    argv = [
        *launch_prefix(Priority.BACKGROUND),
        sys.executable,
        "-m",
        WORKER_MODULE,
        DEVICES_FLAG,
        str(settings.data_dir),
        str(settings.cache_dir),
    ]
    try:
        done = subprocess.run(  # noqa: S603 (a list, never a shell; our own interpreter)
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=None,
            timeout=DEVICES_TIMEOUT_SECONDS,
            check=False,
            creationflags=creation_flags(Priority.BACKGROUND),
        )
    except subprocess.TimeoutExpired:
        raise DeviceUnavailable(
            f"it didn't answer within {DEVICES_TIMEOUT_SECONDS:.0f} seconds"
        ) from None
    except OSError as error:
        raise DeviceUnavailable(str(error)) from error
    try:
        answer = receive(io.BytesIO(done.stdout)) or {}
    except ValueError:
        answer = {}
    if done.returncode == 0 and "devices" in answer:
        return tuple(answer["devices"])
    raise DeviceUnavailable(str(answer.get("error") or _how_it_ended(done.returncode)))


class DeviceQuestion:
    """Which backends the runtime offers, asked of a child and kept until `forget`.

    A failure is kept too: a runtime that crashed as it loaded crashes the same way again.
    """

    def __init__(self, one_shot: Callable[[Settings], tuple[str, ...]] = _one_shot) -> None:
        self._one_shot = one_shot
        self._lock = threading.Lock()
        self._answer: tuple[str, ...] | None = None
        self._failed: str | None = None

    def ask(self, settings: Settings, feature: str) -> tuple[str, ...]:
        with self._lock:
            if self._answer is None and self._failed is None:
                try:
                    self._answer = _from_a_running_child() or self._one_shot(settings)
                except (DeviceUnavailable, RuntimeError) as error:
                    self._failed = str(error)
                    log.error("ml.runtime.wont_start", detail=self._failed)
            if self._answer is None:
                raise DeviceUnavailable(runtime_wont_start(feature, str(self._failed)))
            return self._answer

    def forget(self) -> None:
        """Ask again next time, of a fresh child: a runtime was installed or removed."""
        with self._lock:
            self._answer = None
            self._failed = None
            _RUNNING.clear()


def _from_a_running_child() -> tuple[str, ...] | None:
    for runner in list(_RUNNING):
        # A child that stopped for its own reasons says nothing about the runtime: a new one does.
        with contextlib.suppress(DeviceUnavailable, RuntimeError):
            answer = runner.devices()
            if answer is not None:
                return answer
    return None


#: The one door to the device question, for every feature.
DEVICES = DeviceQuestion()


@waits_on_storage
def devices_here(settings: Settings, feature: str = _ANONYMOUS) -> tuple[str, ...]:
    """Which backends the runtime offers on this device, or `DeviceUnavailable` saying it can't
    start here, in the feature's words."""
    return DEVICES.ask(settings, feature)


def forget_devices() -> None:
    DEVICES.forget()
