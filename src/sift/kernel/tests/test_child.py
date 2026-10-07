# SPDX-License-Identifier: AGPL-3.0-or-later
"""The runtime in a process of its own, proved against a real model, once.

Every other test of a model stands the runtime in; this one starts the worker the application
starts and runs the small model the runtime ships as its own example. What is proved is the part
that belongs to no feature: the child answers what the in-process runtime answers, a child that
dies is replaced on the next ask, and letting go of the models ends the process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import weakref
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.ml import child as ml_child
from sift.kernel.ml import runtime, session, worker
from sift.kernel.ml.child import DEVICES_FLAG, ChildRunner, DeviceQuestion, receive, send
from sift.kernel.ml.runtime import DeviceUnavailable, Runner
from sift.kernel.ml.weights import Weight, WeightError, WeightStore, digest_of

pytestmark = pytest.mark.integration

CPU = "CPUExecutionProvider"


@pytest.fixture(autouse=True)
def runtime_in_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Models load here too, as they do in the model process; no other test's child is asked."""
    monkeypatch.setattr(runtime, "loader", session)
    monkeypatch.setattr(ml_child, "_RUNNING", weakref.WeakSet())


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
    # Held: the next ask is refused immediately, with the same sentence, and starts nothing.
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
        returncode = 1

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
    starts({"ok": True})  # the pipe ends where the answer should be
    with pytest.raises(
        DeviceUnavailable,
        match=r"stopped while Probe was using it \(it stopped with code 1\)\. It is",
    ):
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
    monkeypatch.setattr(sys, "argv", ["worker"])
    monkeypatch.setattr(runtime, "loader", None)
    assert worker.main() == 2
    assert sys.stdout is sys.stderr
    assert runtime.loader is session


def test_the_worker_answers_the_device_question_alone_and_exits(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import sys
    from types import SimpleNamespace

    frames = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=frames))
    args = [DEVICES_FLAG, str(settings.data_dir), str(settings.cache_dir)]

    assert worker.main(args) == 0
    frames.seek(0)
    answer = receive(frames)
    assert answer is not None and CPU in answer["devices"]

    def broken(_settings: Settings) -> tuple[str, ...]:
        raise ImportError("DLL load failed while importing onnxruntime_pybind11_state")

    monkeypatch.setattr(session, "providers", broken)
    out = io.BytesIO()
    assert worker.answer_devices(out, settings) == 1
    out.seek(0)
    assert receive(out) == {
        "error": "ImportError: DLL load failed while importing onnxruntime_pybind11_state"
    }


def test_a_running_worker_answers_the_device_question(settings: Settings) -> None:
    import io

    stdin = io.BytesIO()
    send(
        stdin,
        {
            "op": "hello",
            "data_dir": str(settings.data_dir),
            "cache_dir": str(settings.cache_dir),
            "namespace": "probe",
            "hardware": machine(),
            "device": "cpu",
            "feature": "Probe",
        },
    )
    send(stdin, {"op": "devices"})
    stdin.seek(0)
    stdout = io.BytesIO()

    assert worker.serve(stdin, stdout, configure=lambda _hello: None) == 0
    stdout.seek(0)
    assert receive(stdout) == {"ok": True}
    answer = receive(stdout)
    assert answer is not None and CPU in answer["devices"]


# --- the device question, asked of real children ----------------------------------------------


async def test_a_running_child_answers_the_device_question_and_a_busy_one_is_passed_over(
    store: WeightStore, settings: Settings
) -> None:
    weight = await _example_model(store)
    child = ChildRunner(store, machine(), device="cpu", feature="Probe")
    assert child.devices() is None, "no child to ask"
    try:
        child.load(weight)
        assert child in ml_child._RUNNING

        def never(_settings: Settings) -> tuple[str, ...]:
            raise AssertionError("a new child was started beside a running one")

        assert CPU in DeviceQuestion(never).ask(settings, "Probe")
        with child._lock:
            assert child.devices() is None
    finally:
        child.unload()


def a_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """An `onnxruntime` that does `body` as it loads, found first by every child started now."""
    a_library(tmp_path, monkeypatch, "onnxruntime", body)


def a_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, body: str) -> None:
    """A library `name` that does `body` as it loads, found first by every child started now."""
    package = tmp_path / "standin" / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(body, encoding="utf-8")
    already = os.environ.get("PYTHONPATH")
    found = str(package.parent) + (os.pathsep + already if already else "")
    monkeypatch.setenv("PYTHONPATH", found)


#: What a runtime does when it dies in native code, without the Windows crash dialog.
CRASH = """
import ctypes, faulthandler, sys
if sys.platform == "win32":
    ctypes.windll.kernel32.SetErrorMode(0x0002)
faulthandler._read_null()
"""


async def test_a_child_reads_text_with_a_real_vocabulary(
    store: WeightStore, tmp_path: Path
) -> None:
    """Typed words are read in the model process: the server never loads the vocabulary."""
    import sentencepiece

    corpus = tmp_path / "corpus.txt"
    corpus.write_text("a red car on a road\na woman at the beach\n" * 20, encoding="utf-8")
    sentencepiece.SentencePieceTrainer.train(
        input=str(corpus),
        model_prefix=str(tmp_path / "words"),
        vocab_size=24,
        model_type="char",
        minloglevel=2,
    )
    path = tmp_path / "words.model"
    weight = Weight(
        id="probe.vocabulary",
        role="vocabulary",
        family="probe",
        revision="1",
        url="",
        digest=digest_of(path),
        size_bytes=path.stat().st_size,
        archive_member=None,
        licence="MIT",
    )
    await store.install_from_file(weight, path)
    runner = Runner(store, machine())
    here = runner.encode(weight, "a red car")
    assert runner.encode(weight, "a red car") == here, "the vocabulary is loaded once and kept"
    child = ChildRunner(store, machine(), feature="Probe")
    try:
        assert child.encode(weight, "a red car") == here
        assert here[0] and isinstance(here[1], int)
    finally:
        child.unload()


def test_a_new_child_answers_which_devices_the_runtime_offers(settings: Settings) -> None:
    assert CPU in ml_child._one_shot(settings)


def test_a_runtime_that_raises_as_it_loads_costs_one_child_and_says_why(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a_runtime(tmp_path, monkeypatch, 'raise ImportError("DLL load failed: the specified module")')

    with pytest.raises(DeviceUnavailable) as refused:
        DeviceQuestion().ask(settings, "Recognition")

    assert str(refused.value) == (
        "Recognition can't run on this device: the model runtime couldn't start "
        "(ImportError: DLL load failed: the specified module). Restart Sift to try again."
    )


def test_a_runtime_that_kills_its_process_costs_one_child_and_leaves_a_record(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The way an access violation ends a process: no handler runs, and only the crash recorder
    says where it was."""
    a_runtime(tmp_path, monkeypatch, CRASH)

    with pytest.raises(DeviceUnavailable) as refused:
        DeviceQuestion().ask(settings, "Search by meaning")

    said = str(refused.value)
    assert said.startswith("Search by meaning can't run on this device: ")
    if sys.platform == "win32":
        assert "(it stopped with code 0xC0000005)" in said
    else:
        assert "(it was ended by signal 11)" in said
    record = capfd.readouterr().err
    assert "most recent call first" in record
    assert str(Path("onnxruntime") / "__init__.py") in record


def test_a_child_that_never_answers_is_ended(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "pid"
    a_runtime(
        tmp_path,
        monkeypatch,
        f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n",
    )
    monkeypatch.setattr(ml_child, "DEVICES_TIMEOUT_SECONDS", 15.0)

    with pytest.raises(DeviceUnavailable, match="didn't answer within 15 seconds"):
        ml_child._one_shot(settings)

    assert not _alive(int(pid_file.read_text(encoding="utf-8")))


def _alive(pid: int) -> bool:
    """Whether this test's own child is still running five seconds on: ending takes a moment."""
    if os.name == "nt":
        import ctypes

        kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009 (Windows only)
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return bool(kernel32.WaitForSingleObject(handle, 5000) != 0)
        finally:
            kernel32.CloseHandle(handle)
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        time.sleep(0.1)
    return True


def test_a_child_that_cannot_be_started_or_answers_nonsense_is_said(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a_runtime(
        tmp_path,
        monkeypatch,
        "import os\nos.write(1, bytes([0, 0, 0, 2]) + b'{]')\nraise SystemExit(4)\n",
    )
    with pytest.raises(DeviceUnavailable, match=r"^it stopped with code 4$"):
        ml_child._one_shot(settings)

    monkeypatch.setattr(ml_child, "launch_prefix", lambda _priority: [])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "no-such-python"))
    with pytest.raises(DeviceUnavailable):
        ml_child._one_shot(settings)


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
