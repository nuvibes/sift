# SPDX-License-Identifier: AGPL-3.0-or-later
"""The runtime in a process of its own, proved against a real model, once.

Every other test of a model stands the runtime in; this one starts the worker the application
starts and runs the small model the runtime ships as its own example. What is proved is the part
that belongs to no feature: the child answers what the in-process runtime answers, a child that
dies is replaced on the next ask, and letting go of the models ends the process.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.ml import worker
from sift.kernel.ml.child import ChildRunner, receive, send
from sift.kernel.ml.runtime import DeviceUnavailable, Runner
from sift.kernel.ml.weights import Weight, WeightError, WeightStore, digest_of

pytestmark = pytest.mark.integration


def machine() -> HardwareReport:
    return HardwareReport(
        cpu_count=4,
        total_ram_bytes=8 << 30,
        worker_concurrency=3,
        cuda=False,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )


async def _example_model(store: WeightStore) -> Weight:
    """The runtime's own example, installed as a weight the way a real one is."""
    from onnxruntime.datasets import get_example

    path = Path(get_example("mul_1.onnx"))
    weight = Weight(
        id="example.mul",
        role="reader",
        family="probe",
        revision="1",
        url="",
        digest=digest_of(path),
        size_bytes=path.stat().st_size,
        archive_member=None,
        licence="MIT",
    )
    await store.install_from_file(weight, path)
    return weight


@pytest.fixture
def store(settings: Settings) -> WeightStore:
    return WeightStore(settings, "probe")


async def test_the_child_answers_what_the_runtime_in_this_process_answers(
    store: WeightStore,
) -> None:
    weight = await _example_model(store)
    blob = np.arange(6, dtype=np.float32).reshape(3, 2)
    here = Runner(store, machine(), device="cpu")
    expected = here.run(here.load(weight), blob)

    child = ChildRunner(store, machine(), device="cpu", feature="Probe")
    assert child.device == "cpu"
    try:
        loaded = child.load(weight)
        assert loaded.inputs == here.load(weight).inputs
        assert loaded.outputs == here.load(weight).outputs
        assert loaded.session is None, "the session lives in the child, and nothing here holds it"
        answered = child.run(loaded, blob)
        assert len(answered) == len(expected)
        np.testing.assert_array_equal(answered[0], expected[0])
        # Asking for the outputs by name goes through as well.
        named = child.run(loaded, blob, outputs=list(loaded.outputs))
        np.testing.assert_array_equal(named[0], expected[0])
        # The same handle again is the same handle: loaded once, not once per ask.
        assert child.load(weight) is loaded
        # A fault in the ask itself comes back as the runtime's own error, and the child stays.
        with pytest.raises(RuntimeError, match="INVALID_ARGUMENT"):
            child.run(loaded, np.ones((1, 2), dtype=np.float32))
        assert child._child is not None
    finally:
        child.unload()
    # Letting go twice is nothing.
    child.unload()


async def test_a_child_that_dies_is_replaced_on_the_next_ask(store: WeightStore) -> None:
    """A lost context is dead for its process, and the process is the thing that is disposable:
    the ask that found it dead fails once, and the next one starts a fresh child and loads the
    model again."""
    weight = await _example_model(store)
    child = ChildRunner(store, machine(), device="cpu", feature="Probe")
    try:
        loaded = child.load(weight)
        first = child._child
        assert first is not None
        first.kill()
        first.wait(timeout=10)

        with pytest.raises(DeviceUnavailable, match="stopped"):
            child.run(loaded, np.ones((3, 2), dtype=np.float32))

        answered = child.run(loaded, np.ones((3, 2), dtype=np.float32))
        assert answered[0].shape == (3, 2)
        assert child._child is not None and child._child.pid != first.pid
        assert child.broken is None, "a child that was killed is not a device that was lost"
    finally:
        child.unload()


async def test_letting_go_of_the_models_ends_the_child(store: WeightStore) -> None:
    weight = await _example_model(store)
    child = ChildRunner(store, machine(), device="cpu", feature="Probe")
    child.load(weight)
    running = child._child
    assert running is not None and running.poll() is None

    child.unload()

    assert child._child is None
    assert running.poll() is not None, "the process outlived the models it held"


async def test_a_model_that_is_not_installed_is_refused_by_name(store: WeightStore) -> None:
    """The child's refusal arrives as the same error the runtime raises here, not as a dead
    process: a missing file is the caller's fact to act on."""
    weight = await _example_model(store)
    missing = Weight(
        id="example.absent",
        role="reader",
        family="probe",
        revision="1",
        url="",
        digest=weight.digest,
        size_bytes=weight.size_bytes,
        archive_member=None,
        licence="MIT",
    )
    child = ChildRunner(store, machine(), device="cpu", feature="Probe")
    try:
        with pytest.raises(WeightError, match="not been installed"):
            child.load(missing)
        # And the child is still there to answer for the model that is.
        assert child.run(child.load(weight), np.ones((3, 2), dtype=np.float32))
    finally:
        child.unload()


def test_a_lost_device_is_held_against_for_a_while_and_then_tried_again(
    store: WeightStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the worker says when its device dies is read here as a hold rather than as a
    verdict for the life of the process. Driven without a card: the answer is written onto the
    pipe by a stand-in child."""
    from sift.kernel.ml import child as module

    class DeadChild:
        pid = 1
        stdin: Any
        stdout: Any

        def __init__(self) -> None:
            import io

            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO()
            send(self.stdout, {"ok": True})
            send(self.stdout, {"error": "CUDA failure 999", "kind": "device"})
            self.stdout.seek(0)

        def wait(self, timeout: float | None = None) -> int:
            return 3

        def kill(self) -> None:
            pass

        def poll(self) -> int:
            return 3

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: DeadChild())
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runner = ChildRunner(store, machine(), device="nvidia", feature="Probe")
    weight = Weight(
        id="x", role="reader", family="probe", revision="1", url="", digest="", size_bytes=0,
        archive_member=None, licence="MIT",
    )  # fmt: skip

    with pytest.raises(DeviceUnavailable, match="stopped answering"):
        runner.load(weight)
    assert runner.broken is not None and "tried again in a minute" in runner.broken
    # Held: the next ask is refused at once, with the same sentence, and starts nothing.
    with pytest.raises(DeviceUnavailable, match="tried again"):
        runner.load(weight)
    # And after the hold the sentence is gone, and the next ask would start a fresh child.
    clock[0] += module.LOST_DEVICE_HOLD_SECONDS
    assert runner.broken is None


def test_the_frames_carry_an_array_and_end_cleanly() -> None:
    import io

    weight = Weight(
        id="x", role="reader", family="probe", revision="1", url="", digest="", size_bytes=0,
        archive_member=None, licence="MIT",
    )  # fmt: skip
    pipe = io.BytesIO()
    send(pipe, {"op": "run", "blob": np.arange(4, dtype=np.float32), "outputs": None})
    # Two arrays, one of them a strided view, and both records the frames know.
    picture = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
    send(pipe, {"outputs": [picture[:, ::2], np.zeros((0, 3))], "who": [weight, machine()]})
    send(pipe, {"op": "stop"})
    pipe.seek(0)
    first = receive(pipe)
    assert first is not None and first["op"] == "run" and first["outputs"] is None
    np.testing.assert_array_equal(first["blob"], np.arange(4, dtype=np.float32))
    second = receive(pipe)
    assert second is not None
    np.testing.assert_array_equal(second["outputs"][0], picture[:, ::2])
    assert second["outputs"][1].shape == (0, 3) and second["outputs"][1].dtype == np.float64
    assert second["who"] == [weight, machine()]
    assert isinstance(second["who"][1].warnings, tuple)
    assert receive(pipe) == {"op": "stop"}
    assert receive(pipe) is None, "the end of the pipe is None, not an exception"
    # A frame cut off in its header, in its JSON, or in an array's bytes is the end as well.
    assert receive(io.BytesIO(b"\x00\x00")) is None
    assert receive(io.BytesIO(b"\x00\x00\x00\x09abc")) is None
    whole = io.BytesIO()
    send(whole, {"blob": np.ones(8)})
    assert receive(io.BytesIO(whole.getvalue()[:-1])) is None
    # A frame that is not a record is not a frame.
    bare = io.BytesIO()
    body = b'{"frame": [1, 2], "arrays": []}'
    bare.write(len(body).to_bytes(4, "big") + body)
    bare.seek(0)
    assert receive(bare) is None


@pytest.mark.parametrize("greets", [False, True])
def test_a_child_that_never_answers_is_stopped_at_the_deadline(
    store: WeightStore, monkeypatch: pytest.MonkeyPatch, greets: bool
) -> None:
    """A model that hangs is the one thing the parent cannot tell from one that is slow, so a
    deadline stands in: past it the child is ended and the ask fails, rather than a job thread
    that never comes back. Driven by a stand-in whose pipe is never written: at all, or after
    the greeting."""
    import os

    from sift.kernel.ml import child as module

    read_end, write_end = os.pipe()
    if greets:
        with open(os.dup(write_end), "wb") as greeting:
            send(greeting, {"ok": True})

    class SilentChild:
        pid = 1

        def __init__(self) -> None:
            self.stdin = open(os.pipe()[1], "wb")  # noqa: SIM115 (closed by `_end`)
            self.stdout = open(read_end, "rb")  # noqa: SIM115 (closed by `_end`)

        dead = False

        def wait(self, timeout: float | None = None) -> int:
            # A process that hangs does not stop for being waited on.
            if not self.dead:
                raise subprocess.TimeoutExpired("silent", timeout or 0)
            return 1

        def kill(self) -> None:
            self.dead = True
            os.close(write_end)

        def poll(self) -> int:
            return 1

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: SilentChild())
    monkeypatch.setattr(module, "ANSWER_TIMEOUT_SECONDS", 0.2)
    runner = ChildRunner(store, machine(), device="cpu", feature="Probe")
    weight = Weight(
        id="x", role="reader", family="probe", revision="1", url="", digest="", size_bytes=0,
        archive_member=None, licence="MIT",
    )  # fmt: skip
    expected = (
        r"did not answer Probe within 0 seconds" if greets else r"did not answer \(TimeoutError"
    )
    with pytest.raises(DeviceUnavailable, match=expected):
        runner.load(weight)
    assert runner._child is None
    assert runner.broken is None, "a child that hung is not a device that was lost"


def test_a_child_that_cannot_start_or_answer_says_so(
    store: WeightStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each way a child can fail short of a lost device, as the sentence the ask raises: it
    could not be started, it exited before greeting, it greeted with an error, its pipe broke
    under an ask, and it answered an ask with a fault of its own."""
    import io

    weight = Weight(
        id="x", role="reader", family="probe", revision="1", url="", digest="", size_bytes=0,
        archive_member=None, licence="MIT",
    )  # fmt: skip

    class Gone(io.BytesIO):
        """A pipe that breaks once what was written is read."""

        def read(self, size: int | None = -1) -> bytes:
            if self.tell() == len(self.getvalue()):
                raise ValueError("gone")
            return super().read(size)

    class StandIn:
        pid = 1

        def __init__(self, *frames: dict[str, Any], breaks: bool = False) -> None:
            self.stdin = io.BytesIO()
            self.stdout = Gone() if breaks else io.BytesIO()
            for frame in frames:
                send(self.stdout, frame)
            self.stdout.seek(0)

        def wait(self, timeout: float | None = None) -> int:
            return 1

        def kill(self) -> None:
            pass

        def poll(self) -> int:
            return 1

    def starts(*frames: dict[str, Any], **kw: Any) -> None:
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: StandIn(*frames, **kw))

    runner = ChildRunner(store, machine(), device="cpu", feature="Probe")

    def refuses() -> None:
        monkeypatch.setattr(
            subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("no"))
        )

    refuses()
    with pytest.raises(DeviceUnavailable, match="could not be started"):
        runner.load(weight)
    starts()
    with pytest.raises(DeviceUnavailable, match=r"could not start \(it exited\)"):
        runner.load(weight)
    starts({"error": "no such device"})
    with pytest.raises(DeviceUnavailable, match=r"could not start \(no such device\)"):
        runner.load(weight)
    starts({"ok": True}, breaks=True)
    with pytest.raises(DeviceUnavailable, match=r"stopped while Probe was using it \(gone\)"):
        runner.load(weight)
    starts({"ok": True}, {"error": "ValueError: bad shape", "kind": "other"})
    with pytest.raises(RuntimeError, match="bad shape"):
        runner.load(weight)
    assert runner._child is not None, "a fault of the model's is not the end of the child"
    assert runner.broken is None


async def test_the_worker_serves_frames_in_this_process(
    store: WeightStore, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker's loop, driven without a process: every kind of frame and every kind of
    answer, including the one that ends it."""
    import io

    weight = await _example_model(store)
    absent = Weight(
        id="example.absent", role="reader", family="probe", revision="1", url="",
        digest=weight.digest, size_bytes=1, archive_member=None, licence="MIT",
    )  # fmt: skip
    configured: list[dict[str, Any]] = []

    def hello() -> dict[str, Any]:
        return {
            "op": "hello",
            "data_dir": str(settings.data_dir),
            "cache_dir": str(settings.cache_dir),
            "namespace": "probe",
            "hardware": machine(),
            "device": "cpu",
            "feature": "Probe",
            "log_level": "WARNING",
            "redact_personal": False,
        }

    # No greeting, or the wrong one: the worker declines to start.
    assert worker.serve(io.BytesIO(), io.BytesIO(), configure=configured.append) == 2
    wrong = io.BytesIO()
    send(wrong, {"op": "run"})
    wrong.seek(0)
    assert worker.serve(wrong, io.BytesIO(), configure=configured.append) == 2
    assert configured == []

    stdin = io.BytesIO()
    send(stdin, hello())
    send(stdin, {"op": "load", "weight": weight})
    send(stdin, {"op": "run", "weight_id": weight.id, "blob": np.ones((3, 2), dtype=np.float32)})
    send(stdin, {"op": "run", "weight_id": "never.loaded", "blob": np.ones((1, 2))})
    send(stdin, {"op": "load", "weight": absent})
    send(stdin, {"op": "dance"})
    send(stdin, {"op": "unload"})
    stdin.seek(0)
    stdout = io.BytesIO()
    assert worker.serve(stdin, stdout, configure=configured.append) == 0
    assert configured == [hello()]
    stdout.seek(0)
    answers = [receive(stdout) for _ in range(7)]
    assert answers[0] == {"ok": True}
    assert answers[1] is not None and answers[1]["inputs"] == ["X"]
    assert answers[2] is not None and answers[2]["outputs"][0].shape == (3, 2)
    assert answers[3] == {"error": "KeyError: 'never.loaded'", "kind": "other"}
    assert answers[4] is not None and answers[4]["kind"] == "weight"
    assert answers[5] == {"error": "unknown request 'dance'", "kind": "other"}
    assert answers[6] == {"ok": True}
    assert receive(stdout) is None

    # A lost device ends the worker with the exit code that names it.
    def lost(self: Runner, *a: Any, **k: Any) -> Any:
        raise DeviceUnavailable("CUDA failure 999")

    monkeypatch.setattr(Runner, "run", lost)
    stdin = io.BytesIO()
    send(stdin, hello())
    send(stdin, {"op": "load", "weight": weight})
    send(stdin, {"op": "run", "weight_id": weight.id, "blob": np.ones((3, 2), dtype=np.float32)})
    stdin.seek(0)
    stdout = io.BytesIO()
    assert worker.serve(stdin, stdout, configure=configured.append) == worker.DEVICE_LOST
    stdout.seek(0)
    receive(stdout), receive(stdout)
    assert receive(stdout) == {"error": "CUDA failure 999", "kind": "device"}


def test_the_worker_keeps_its_pipe_for_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """What `main` does before anything can print: the pipe is taken for frames and standard
    output is standard error, so a log line cannot be read as the length of a frame."""
    import io
    import sys
    from types import SimpleNamespace

    frames = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO()))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=frames))
    assert worker.main() == 2
    assert sys.stdout is sys.stderr


def test_the_worker_is_configured_the_way_the_parent_is(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        worker,
        "configure_logging",
        lambda level, *, redact_personal: seen.append((level, redact_personal)),
    )
    worker.configure_from({"log_level": "DEBUG", "redact_personal": False})
    worker.configure_from({})
    assert seen == [("DEBUG", False), ("INFO", True)]
