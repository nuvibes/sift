# SPDX-License-Identifier: AGPL-3.0-or-later
"""Obtaining a model, proving it is the one described, and loading it on a chosen device.

Two features run models and neither may import the other, so this machinery sits in the kernel and
both ask for it. What is proved here is the part that belongs to no feature:

**A device that was asked for and is not there is a failure, not a fallback.** Quietly running on
the processor instead turns "why is this taking nine hours" into a question with no answer anywhere
on the machine: the work still happens, the result is still right, and nothing says why it was
slow. The two halves fail for different reasons: the card is missing, or the software that drives
it is, and a person can only act on the difference if the message names it.

**Nothing is used until its digest matches.** That is not belt and braces. A truncated model file
usually loads perfectly well and then returns numbers that are quietly wrong, so the check is the
only thing standing between a dropped connection and a library described by a broken model.

**A feature's models are its own.** Each names a corner of the data directory, and one feature
deleting its models can never reach another's.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest
from blake3 import blake3

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.ml import child as ml_child
from sift.kernel.ml import runtime, session
from sift.kernel.ml.child import DeviceQuestion
from sift.kernel.ml.runtime import (
    DEVICE_LABELS,
    DEVICES,
    MOST_DEVICE_THREADS,
    DeviceUnavailable,
    Runner,
    device_refusal,
    resolve_provider,
    session_threads,
    why_unusable,
)
from sift.kernel.ml.weights import Weight, WeightError, WeightStore, digest_of

pytestmark = pytest.mark.unit

CPU = "CPUExecutionProvider"
CUDA = "CUDAExecutionProvider"


@pytest.fixture(autouse=True)
def runtime_in_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Models load here, as they do in the model process."""
    monkeypatch.setattr(runtime, "loader", session)


def answering(*providers: str) -> Any:
    """The device question, answered with these and asked of no child."""
    return patch.object(ml_child, "DEVICES", DeviceQuestion(lambda _settings: providers))


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


def make_weight(payload: bytes, *, member: str | None = None, role: str = "reader") -> Weight:
    return Weight(
        id="test.reader",
        role=role,
        family="test",
        revision="test-1",
        url="https://example.test/model.onnx",
        digest=blake3(payload).hexdigest(),
        size_bytes=len(payload),
        archive_member=member,
        licence="MIT",
    )


@pytest.fixture
def store(settings: Settings) -> WeightStore:
    return WeightStore(settings, "probe")


# --- choosing a device ---------------------------------------------------------------------------


def test_the_processor_is_always_available_and_needs_nothing() -> None:
    """Which is what makes it the guarantee rather than an option."""
    assert resolve_provider("cpu", machine(), [CPU]) == [CPU]


def test_a_graphics_card_that_is_present_and_driveable_is_used() -> None:
    resolved = resolve_provider("nvidia", machine(cuda=True), [CUDA, CPU])

    assert resolved[0] == CUDA
    # The processor stays behind it for any single operation the card cannot do. That is not a
    # silent fallback: the model still runs on the device that was asked for, and if it could not,
    # loading it fails outright.
    assert resolved[-1] == CPU


def test_asking_for_a_card_this_machine_does_not_have_fails_loudly() -> None:
    """No card in the machine, and the message says so rather than naming the software.

    It does not tell somebody to start a CONTAINER with access to the card: Sift is an application
    somebody installs, and on Windows that would be a thing to check that does not exist.
    """
    with pytest.raises(DeviceUnavailable) as failure:
        resolve_provider("nvidia", machine(cuda=False), [CUDA, CPU])

    message = str(failure.value)
    assert "NVIDIA" in message
    assert "driver has to be installed and working" in message
    # The other half's answer must not appear here, or the two failures read as one.
    assert "processor-only version" not in message


def test_a_card_with_no_software_to_drive_it_fails_differently() -> None:
    """The card is there and the software that drives it is not, which is a different answer.

    THE MESSAGE HAS TO CARRY THE WAY OUT: `ml.accel` fetches the card's runtime, so the message
    points at the place that does it.

    What is being held here is not the wording but the property: somebody who reads this must be
    able to act on it without going and finding out. "Not installed" alone would leave them looking
    for a driver they already have.
    """
    with pytest.raises(DeviceUnavailable) as failure:
        resolve_provider("nvidia", machine(cuda=True), [CPU])

    message = str(failure.value)
    assert "not installed" in message
    assert "Sift can fetch it" in message
    assert "Performance" in message


def test_the_card_is_only_refused_for_ONE_reason_at_a_time() -> None:
    """A machine with the card and the software gets neither refusal.

    Both halves are checked in one function and it would be easy for one to shadow the other: a
    `return` in the wrong branch, and every card is refused for the missing driver whether or not
    it is missing. The pass case is what proves the two conditions are separate.
    """
    assert resolve_provider("nvidia", machine(cuda=True), [CUDA, CPU])[0] == CUDA


def test_a_device_this_installation_cannot_drive_says_so_without_a_machine() -> None:
    """`why_unusable` is the half that needs no hardware report, and it answers on its own.

    This is what the settings screen calls: it has no machine to ask about and does not need one,
    because "the software was never shipped" is true before anything is plugged in.
    """
    assert why_unusable("nvidia", [CPU], feature="Smart Search") is not None
    assert "Smart Search" in str(why_unusable("nvidia", [CPU], feature="Smart Search"))
    assert why_unusable("nvidia", [CUDA, CPU]) is None
    # The processor is the guarantee, and is never anybody's reason to refuse.
    assert why_unusable("cpu", []) is None
    assert why_unusable("quantum", [CPU]) is not None


def test_the_device_menu_and_the_table_of_what_drives_them_agree() -> None:
    """Every device a setting may offer has a label and something that drives it.

    Two hand-written tables describing one set, which is the shape that drifts silently: a device
    added to one and not the other is a menu item that fails only when somebody picks it, or a
    driver nothing can ask for. The import-time check in `runtime` is the real guard; this is what
    makes it visible as a rule rather than as a line nobody reads.
    """
    assert len(DEVICES) == len(DEVICE_LABELS)
    assert DEVICES[0] == "cpu"
    for device in DEVICES:
        assert device == "cpu" or why_unusable(device, [CUDA, CPU]) is None


def test_choosing_a_device_this_installation_cannot_drive_is_REFUSED_AT_THE_SETTING() -> None:
    """The refusal happens where the choice is made, not hours later inside a job.

    Accepted at the setting, a graphics card the installation cannot drive would store, and the
    screen would show it. The only disagreement would come from the job that eventually tried to
    load a model, by which time the settings screen had been claiming the card was in use for as
    long as anybody had left it there.
    """
    refuse = device_refusal("Recognition")

    assert refuse("cpu") is None

    with answering(CPU):
        # Refused, and the refusal says where to go rather than only what is wrong.
        assert "Performance" in str(refuse("nvidia"))
    with answering(CUDA, CPU):
        assert refuse("nvidia") is None


def test_a_card_is_refused_in_words_when_the_runtime_cannot_start() -> None:
    def crashed(_settings: Settings) -> tuple[str, ...]:
        raise DeviceUnavailable("it stopped with code 0xC0000005")

    with patch.object(ml_child, "DEVICES", DeviceQuestion(crashed)):
        refused = device_refusal("Recognition")("nvidia")

    assert refused == (
        "Recognition can't run on this device: the model runtime couldn't start "
        "(it stopped with code 0xC0000005). Restart Sift to try again."
    )


def test_the_refusal_is_NOT_the_validator_so_a_stored_choice_is_never_rewritten() -> None:
    """It answers with a reason rather than raising, and that shape is load-bearing.

    The registry runs a setting's VALIDATOR on the way out of the database as well as on the way
    in, and quietly falls back to the default when it rejects what is stored. Written as a
    validator, this check would turn a stored "nvidia" into "cpu" on every read, so recognition,
    which refuses to run on a device that is not there precisely so that nobody is left wondering
    why a sweep took nine hours, would read its own setting as the processor and run on it.

    Returning a string keeps it usable only where it is asked for: the write path.
    """
    with answering(CPU):
        answer = device_refusal("Recognition")("nvidia")

    assert isinstance(answer, str)


def test_the_processor_is_accepted_WITHOUT_importing_the_inference_runtime() -> None:
    """The default must not drag a hundred megabytes of runtime in at boot.

    The registry runs every setting's validator over its own default at import time, and the
    default here is the processor. If that reached the device question, every start would pay
    for a child that loads the runtime.
    """
    called = False

    def watch(_settings: Settings) -> tuple[str, ...]:
        nonlocal called
        called = True
        return (CPU,)

    with patch.object(ml_child, "DEVICES", DeviceQuestion(watch)):
        assert device_refusal("Recognition")("cpu") is None

    assert not called


def test_a_device_sift_does_not_know_about_is_refused_with_the_list_of_ones_it_does() -> None:
    with pytest.raises(DeviceUnavailable, match="not a device Sift knows"):
        resolve_provider("quantum", machine(), [CPU])


def test_the_feature_that_asked_is_what_the_message_names() -> None:
    """A machine can run more than one of these, so "a device is unavailable" with no subject is
    not something anybody can act on. The caller supplies the word."""
    with pytest.raises(DeviceUnavailable, match=r"^Recognition was set"):
        resolve_provider("nvidia", machine(), [CUDA, CPU], feature="Recognition")


def test_a_caller_that_names_nothing_still_produces_a_readable_sentence() -> None:
    with pytest.raises(DeviceUnavailable, match=r"^This feature was set"):
        resolve_provider("nvidia", machine(), [CUDA, CPU])


# --- where a feature's models live ---------------------------------------------------------------


def test_models_live_in_the_devices_store_and_never_in_the_cache(
    settings: Settings, store: WeightStore
) -> None:
    """Once per device, beside the libraries, not inside this library, and never in a cache."""
    assert store.directory() == settings.models_dir / "probe"
    assert not store.directory().is_relative_to(settings.data_dir)
    assert not store.directory().is_relative_to(settings.cache_dir)


def test_two_features_never_share_a_corner(settings: Settings) -> None:
    """So switching one off and deleting its models cannot reach another's."""
    first = WeightStore(settings, "probe")
    second = WeightStore(settings, "other")

    assert first.namespace == "probe"
    assert first.directory() != second.directory()


def test_the_digest_of_a_file_is_read_in_pieces(tmp_path: Path) -> None:
    """A model is hundreds of megabytes; it does not have to fit in memory to be checked."""
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (3 << 20))

    assert digest_of(big) == blake3(b"x" * (3 << 20)).hexdigest()


# --- installing from a file ----------------------------------------------------------------------


async def test_a_model_can_be_installed_from_a_file_the_operator_already_has(
    tmp_path: Path, store: WeightStore
) -> None:
    """The offline answer, and the only one on a machine with no route out."""
    payload = b"a model, of sorts" * 100
    source = tmp_path / "model.onnx"
    source.write_bytes(payload)
    weight = make_weight(payload)

    assert store.installed(weight) is False
    await store.install_from_file(weight, source)

    assert store.installed(weight) is True
    store.verify(weight)


async def test_a_file_that_is_not_the_model_described_is_refused(
    tmp_path: Path, store: WeightStore
) -> None:
    payload = b"the real model" * 100
    weight = make_weight(payload)
    wrong = tmp_path / "wrong.onnx"
    wrong.write_bytes(b"something else entirely")

    with pytest.raises(WeightError, match=r"not the .* model"):
        await store.install_from_file(weight, wrong)

    assert store.installed(weight) is False


async def test_a_file_that_is_not_there_is_reported(tmp_path: Path, store: WeightStore) -> None:
    with pytest.raises(WeightError, match="no file at"):
        await store.install_from_file(make_weight(b"payload"), tmp_path / "absent.onnx")


async def test_a_model_distributed_inside_an_archive_is_taken_out_of_it(
    tmp_path: Path, store: WeightStore
) -> None:
    payload = b"the model inside" * 50
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("wanted.onnx", payload)
        bundle.writestr("not-wanted.onnx", b"a much larger thing nobody asked for")
    weight = make_weight(payload, member="wanted.onnx")

    await store.install_from_file(weight, archive)

    assert store.path_of(weight).read_bytes() == payload


async def test_an_archive_without_the_named_model_in_it_is_refused(
    tmp_path: Path, store: WeightStore
) -> None:
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("something-else.onnx", b"nope")
    weight = make_weight(b"the model inside", member="wanted.onnx")

    with pytest.raises(WeightError, match="does not contain"):
        await store.install_from_file(weight, archive)


async def test_a_file_named_as_an_archive_that_is_not_one_is_copied_whole(
    tmp_path: Path, store: WeightStore
) -> None:
    """A publisher can change how it distributes a file. The member is what to take IF there is an
    archive, not an assertion that there is one."""
    payload = b"loose after all" * 40
    source = tmp_path / "model.onnx"
    source.write_bytes(payload)
    weight = make_weight(payload, member="wanted.onnx")

    await store.install_from_file(weight, source)

    assert store.path_of(weight).read_bytes() == payload


# --- proving what is on disk ---------------------------------------------------------------------


def test_a_model_that_has_not_been_installed_is_reported_as_such(store: WeightStore) -> None:
    with pytest.raises(WeightError, match="not been installed"):
        store.verify(make_weight(b"payload"))


async def test_a_model_that_changed_on_disk_is_refused_rather_than_loaded(
    tmp_path: Path, store: WeightStore
) -> None:
    """A truncated model usually loads and then returns numbers that are quietly wrong, so this is
    checked before every load rather than only when it was installed."""
    payload = b"a model, of sorts" * 100
    source = tmp_path / "model.onnx"
    source.write_bytes(payload)
    weight = make_weight(payload)
    await store.install_from_file(weight, source)

    store.path_of(weight).write_bytes(payload[:-20])

    with pytest.raises(WeightError, match="not the one Sift expects"):
        store.verify(weight)


# --- downloading ---------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: bytes, *, total: int | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = {"Content-Length": str(total if total is not None else len(body))}
        self.content = self

    async def iter_chunked(self, size: int):  # type: ignore[no-untyped-def]
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    """Answers one request from memory, and records what was asked for."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.headers: dict[str, str] = {}

    def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self.headers = dict(headers or {})
        return self.response

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


async def test_a_model_is_downloaded_verified_and_put_in_place(store: WeightStore) -> None:
    payload = b"downloaded model" * 200
    weight = make_weight(payload)

    await store.fetch(weight, session_factory=lambda: FakeSession(FakeResponse(200, payload)))

    assert store.path_of(weight).read_bytes() == payload
    store.verify(weight)


async def test_an_interrupted_download_continues_from_where_it_stopped(
    store: WeightStore,
) -> None:
    """Hundreds of megabytes: a connection dropping at ninety per cent must not mean starting
    again."""
    payload = b"downloaded model" * 200
    weight = make_weight(payload)
    partial = store.path_of(weight).with_suffix(".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(payload[:100])
    session = FakeSession(FakeResponse(206, payload[100:], total=len(payload) - 100))

    await store.fetch(weight, session_factory=lambda: session)

    assert session.headers["Range"] == "bytes=100-"
    assert store.path_of(weight).read_bytes() == payload


async def test_a_server_that_ignores_the_range_starts_the_file_again(store: WeightStore) -> None:
    """What is arriving is then not a continuation of what was already written, so keeping the
    partial file would splice two copies of the beginning together."""
    payload = b"downloaded model" * 200
    weight = make_weight(payload)
    partial = store.path_of(weight).with_suffix(".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(payload[:100])

    await store.fetch(weight, session_factory=lambda: FakeSession(FakeResponse(200, payload)))

    assert store.path_of(weight).read_bytes() == payload


async def test_a_server_saying_there_is_nothing_left_to_send_uses_what_is_already_there(
    store: WeightStore,
) -> None:
    payload = b"downloaded model" * 200
    weight = make_weight(payload)
    partial = store.path_of(weight).with_suffix(".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(payload)

    await store.fetch(weight, session_factory=lambda: FakeSession(FakeResponse(416, b"")))

    assert store.path_of(weight).read_bytes() == payload


async def test_a_download_that_fails_says_so_in_words_somebody_can_act_on(
    store: WeightStore,
) -> None:
    weight = make_weight(b"never arrives")

    with pytest.raises(WeightError, match="answered 503"):
        await store.fetch(weight, session_factory=lambda: FakeSession(FakeResponse(503, b"")))


async def test_a_download_reports_progress_and_stopping_leaves_what_arrived(
    store: WeightStore,
) -> None:
    """Cancelling is not an interruption: the reader simply stops asking, and the next attempt
    continues from the bytes already on disk."""
    payload = b"downloaded model" * 200
    weight = make_weight(payload)
    seen: list[tuple[int, int]] = []

    def progress(written: int, total: int) -> bool:
        seen.append((written, total))
        return False

    await store.fetch(
        weight,
        session_factory=lambda: FakeSession(FakeResponse(200, payload)),
        progress=progress,
    )

    assert seen == [(len(payload), len(payload))]
    assert store.installed(weight) is False
    assert store.path_of(weight).with_suffix(".part").read_bytes() == payload


async def test_a_download_that_runs_to_the_end_reports_the_whole_way(store: WeightStore) -> None:
    payload = b"downloaded model" * 200
    weight = make_weight(payload)
    seen: list[tuple[int, int]] = []

    def progress(written: int, total: int) -> bool:
        seen.append((written, total))
        return True

    await store.fetch(
        weight,
        session_factory=lambda: FakeSession(FakeResponse(200, payload)),
        progress=progress,
    )

    assert seen == [(len(payload), len(payload))]
    assert store.installed(weight) is True


# --- loading a model -----------------------------------------------------------------------------


class StubSession:
    """Stands in for the inference runtime, which needs a real model file and there is none."""

    def __init__(self, path: str, options: Any, providers: list[str], **kwargs: Any) -> None:
        self.path = path
        self.options = options
        self.providers = providers
        self.kwargs = kwargs

    def get_providers(self) -> list[str]:
        return self.providers

    def get_inputs(self) -> list[Any]:
        return [type("Input", (), {"name": "input"})()]

    def get_outputs(self) -> list[Any]:
        return [type("Output", (), {"name": "output"})()]

    def run(self, names: list[str], feed: dict[str, Any]) -> list[np.ndarray]:
        return [np.zeros((1, 2), dtype=np.float32)]


class FallenSession(StubSession):
    """A session the runtime quietly opened on the processor when the card was asked for."""

    def get_providers(self) -> list[str]:
        return [CPU]


class DeadSession(StubSession):
    """A session whose device has died underneath it, the way a lost CUDA context does."""

    def run(self, names: list[str], feed: dict[str, Any]) -> list[np.ndarray]:
        raise RuntimeError("CUDA failure 999: unknown error")


class UnopenableSession(StubSession):
    """A session the runtime could not build at all: the card's driver is missing or too old."""

    def __init__(self, path: str, options: Any, providers: list[str], **kwargs: Any) -> None:
        raise RuntimeError("CUDA driver version is insufficient")


@pytest.fixture
def stub_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", StubSession)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: [CPU])


async def test_a_model_is_verified_before_it_is_loaded_and_loaded_only_once(
    tmp_path: Path, store: WeightStore, stub_runtime: None
) -> None:
    payload = b"a model, of sorts" * 100
    source = tmp_path / "model.onnx"
    source.write_bytes(payload)
    weight = make_weight(payload)
    await store.install_from_file(weight, source)
    runner = Runner(store, machine())

    first = runner.load(weight)
    again = runner.load(weight)

    assert first is again
    assert first.inputs == ("input",)
    assert first.device == "cpu"
    assert runner.run(first, np.zeros((1, 3, 4, 4), dtype=np.float32))[0].shape == (1, 2)


def test_a_model_cannot_be_loaded_before_it_has_been_obtained(store: WeightStore) -> None:
    """No model ships with Sift, so this is the ordinary state of a fresh install."""
    with pytest.raises(WeightError, match="not been installed"):
        Runner(store, machine()).load(make_weight(b"payload"))


async def test_inference_is_kept_to_one_thread_per_model(
    tmp_path: Path, store: WeightStore, stub_runtime: None
) -> None:
    """The work arrives as a queue already running several files together, so a model that spawned a
    thread per core would have every worker fighting every other worker for the same processor."""
    payload = b"a model" * 100
    source = tmp_path / "model.onnx"
    source.write_bytes(payload)
    weight = make_weight(payload)
    await store.install_from_file(weight, source)

    loaded = Runner(store, machine()).load(weight)

    assert loaded.session.options.intra_op_num_threads == 1
    assert loaded.session.options.inter_op_num_threads == 1


def test_a_model_on_a_card_is_not_held_to_the_processors_one_thread() -> None:
    """The setting reaches something different on each device, which is why it depends on one.

    On the processor it is the whole model and one thread is the deliberate price of leaving the
    machine to the person using it. On a card it reaches only the operators the card cannot run,
    and holding THOSE to one thread throttles the card behind them: the picture model describes
    a batch of frames at more than twice the speed with four threads as with one.
    """
    assert session_threads("cpu", 24) == 1
    assert session_threads("nvidia", 24) == MOST_DEVICE_THREADS


def test_a_card_gets_the_step_backs_share_at_most_four_and_never_none() -> None:
    """Capped rather than handed to the runtime: left to choose, it takes every thread a large
    machine has and is SLOWER than four. And never past a quarter of the machine, the share
    background work keeps to while somebody is using it, because a session's threads are fixed
    when it loads."""
    assert session_threads("nvidia", 16) == 4
    assert session_threads("nvidia", 8) == 2
    assert session_threads("nvidia", 4) == 1
    assert session_threads("nvidia", 2) == 1
    assert session_threads("nvidia", 1) == 1


async def _installed(tmp_path: Path, store: WeightStore) -> Any:
    payload = b"a model" * 100
    source = tmp_path / "model.onnx"
    source.write_bytes(payload)
    weight = make_weight(payload)
    await store.install_from_file(weight, source)
    return weight


async def test_the_runtime_is_not_allowed_to_fall_back_to_the_processor_quietly(
    tmp_path: Path, store: WeightStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Left to itself the runtime rebuilds the session on the processor when the card fails to
    open, and again once when it fails mid-run (with a `print`, which is nowhere), while the
    session, the log and the settings screen go on naming the card. A card that was asked for and
    is not there is a failure, and it is one that says so."""
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", StubSession)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: [CUDA, CPU])
    weight = await _installed(tmp_path, store)

    loaded = Runner(store, machine(cuda=True), device="nvidia").load(weight)
    assert loaded.session.kwargs == {"enable_fallback": False}
    assert loaded.session.get_providers()[0] == CUDA

    monkeypatch.setattr(onnxruntime, "InferenceSession", FallenSession)
    with pytest.raises(DeviceUnavailable, match="opened on CPUExecutionProvider instead"):
        Runner(store, machine(cuda=True), device="nvidia").load(weight)


async def test_a_card_that_cannot_open_a_model_is_named_and_the_processor_is_not(
    tmp_path: Path, store: WeightStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runtime's own failure to open a model on the card is turned into the one error the
    settings screen reads, naming the device and the cause. On the processor there is no device
    to blame and nothing to fall back to, so the runtime's error is left exactly as it was."""
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", UnopenableSession)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: [CUDA, CPU])
    weight = await _installed(tmp_path, store)

    with pytest.raises(
        DeviceUnavailable, match=r"could not open a model .*driver version"
    ) as named:
        Runner(store, machine(cuda=True), device="nvidia").load(weight)
    assert isinstance(named.value.__cause__, RuntimeError)

    with pytest.raises(RuntimeError, match="driver version is insufficient"):
        Runner(store, machine(cuda=True), device="cpu").load(weight)


async def test_a_device_that_dies_is_dead_for_the_process_and_says_so(
    tmp_path: Path, store: WeightStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost context does not come back. Every later call on the session fails the same way
    while the process says nothing, and the job feed shows the same job failing three times and
    then every job after it. The first failure marks the device dead, drops the sessions, and is
    raised as the reason; loading again is refused with it."""
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", DeadSession)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: [CUDA, CPU])
    weight = await _installed(tmp_path, store)
    runner = Runner(store, machine(cuda=True), device="nvidia")
    loaded = runner.load(weight)
    # Each reading of `broken` goes through a name of its own: mypy narrows a member expression
    # at an assert and does not see the call between two readings change it.
    before = runner.broken
    assert before is None

    with pytest.raises(DeviceUnavailable, match="stopped answering"):
        runner.run(loaded, np.zeros((1, 3, 4, 4), dtype=np.float32))

    reason = runner.broken
    assert reason is not None and "Restart Sift" in reason
    assert runner._loaded == {}, "every session went with the device"
    with pytest.raises(DeviceUnavailable, match="stopped answering"):
        runner.load(weight)


async def test_a_failure_on_the_processor_is_the_models_and_is_raised_as_it_came(
    tmp_path: Path, store: WeightStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", DeadSession)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: [CPU])
    weight = await _installed(tmp_path, store)
    runner = Runner(store, machine())
    loaded = runner.load(weight)

    with pytest.raises(RuntimeError, match="CUDA failure"):
        runner.run(loaded, np.zeros((1, 3, 4, 4), dtype=np.float32))

    assert runner.broken is None, "the processor cannot be lost"


def test_a_runner_starts_on_the_processor_unless_told_otherwise(store: WeightStore) -> None:
    assert Runner(store, machine()).device == "cpu"
    assert Runner(store, machine(cuda=True), device="nvidia").device == "nvidia"


def test_dropping_the_models_gives_the_memory_back(store: WeightStore) -> None:
    """What switching a feature off does."""
    runner = Runner(store, machine())
    runner._loaded["something"] = object()  # type: ignore[assignment]

    runner.unload()

    assert runner._loaded == {}


def test_the_providers_are_read_from_the_runtime_itself(settings: Settings) -> None:
    providers = session.providers(settings)

    # Every build of the runtime carries the processor one; a build without it could run nothing.
    assert "CPUExecutionProvider" in providers


async def test_a_process_without_the_loader_refuses_to_load_a_model(
    tmp_path: Path, store: WeightStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server is such a process: a stray load there is refused, never a runtime loaded."""
    monkeypatch.setattr(runtime, "loader", None)
    source = tmp_path / "model.onnx"
    source.write_bytes(b"a model")
    weight = make_weight(b"a model")
    await store.install_from_file(weight, source)

    with pytest.raises(DeviceUnavailable, match="only in the model process"):
        Runner(store, machine()).load(weight)


class _Child:
    """A runner with a child, idle, busy or gone."""

    def __init__(self, answer: tuple[str, ...] | Exception | None) -> None:
        self.answer = answer

    def devices(self) -> tuple[str, ...] | None:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_the_device_question_is_asked_once_and_again_after_an_install(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ml_child, "_RUNNING", set())
    asked: list[int] = []

    def one_shot(_settings: Settings) -> tuple[str, ...]:
        asked.append(1)
        return (CUDA, CPU) if len(asked) > 1 else (CPU,)

    question = DeviceQuestion(one_shot)

    assert question.ask(settings, "Recognition") == (CPU,)
    assert question.ask(settings, "Recognition") == (CPU,)
    assert len(asked) == 1
    question.forget()
    assert question.ask(settings, "Recognition") == (CUDA, CPU)


def test_a_running_child_answers_before_a_new_one_is_started(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A busy child is passed over rather than waited on, and one that stopped is passed over."""
    gone = _Child(DeviceUnavailable("The model process stopped while Probe was using it."))
    busy, idle = _Child(None), _Child((CUDA, CPU))
    monkeypatch.setattr(ml_child, "_RUNNING", [gone, busy, idle])

    def never(_settings: Settings) -> tuple[str, ...]:
        raise AssertionError("a new child was started beside a running one")

    assert DeviceQuestion(never).ask(settings, "Recognition") == (CUDA, CPU)
    monkeypatch.setattr(ml_child, "_RUNNING", [gone, busy])
    assert DeviceQuestion(lambda _settings: (CPU,)).ask(settings, "Recognition") == (CPU,)


def test_a_runtime_that_cannot_start_is_said_in_each_features_words_and_kept(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kept, because a runtime that crashed as it loaded crashes the same way again."""
    monkeypatch.setattr(ml_child, "_RUNNING", set())
    asked: list[int] = []

    def crashed(_settings: Settings) -> tuple[str, ...]:
        asked.append(1)
        raise RuntimeError("ImportError: DLL load failed")

    question = DeviceQuestion(crashed)

    for feature in ("Recognition", "Search by meaning"):
        with pytest.raises(DeviceUnavailable) as refused:
            question.ask(settings, feature)
        assert str(refused.value).startswith(f"{feature} can't run on this device: ")
        assert "(ImportError: DLL load failed)" in str(refused.value)
    assert len(asked) == 1


@pytest.mark.parametrize(
    ("code", "said"),
    [
        (3221225477, "it stopped with code 0xC0000005"),
        (-11, "it was ended by signal 11"),
        (1, "it stopped with code 1"),
    ],
)
def test_how_a_child_ended_is_said_the_way_its_system_names_it(code: int, said: str) -> None:
    assert ml_child._how_it_ended(code) == said


# --- one decode of a still for every model pass -----------------------------------------------


def _still(target: Path, settings: Settings, size: str = "640x480") -> Path:
    import subprocess as system

    system.run(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}",
            "-frames:v",
            "1",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


def _ask(filters: str, frame_bytes: int) -> Any:
    from sift.kernel import media
    from sift.kernel.ml import pictures

    return media.RawFrames(
        moments=(pictures.STILL,), filters=filters, pixel_format="rgb24", frame_bytes=frame_bytes
    )


def test_a_still_is_asked_for_once_per_shape_and_the_largest_goes_past_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.kernel import media
    from sift.kernel.ml import pictures

    small, large = _ask("scale=4:4", 48), _ask("scale=8:8", 192)
    sought = media.RawFrames(
        moments=(media.Moment(seek=("-ss", "1.000")),),
        filters="scale=2:2",
        pixel_format="rgb24",
        frame_bytes=12,
    )
    files = media.FrameFiles(moments=(pictures.STILL,), filters="x", suffix=".jpg", output=())
    asks = [large, small, _ask("scale=4:4", 48), sought, files]
    assert pictures.still_requests(asks) == [small, large]
    monkeypatch.setattr(pictures, "MOST_PREPARED_BYTES", 100)
    assert pictures.still_requests(asks) == [small]


def test_the_one_decode_splits_the_picture_once_and_maps_every_branch(tmp_path: Path) -> None:
    from sift.kernel.ml import pictures

    asks = [_ask("scale=4:4", 48), _ask("crop=2:2:0:0", 12)]
    settings = Settings(data_dir=tmp_path, cache_dir=tmp_path)
    assert pictures.still_graph(asks) == (
        "[0:v]split=2[s0][s1];\n[s0]scale=4:4[o0];\n[s1]crop=2:2:0:0[o1]\n"
    )
    argv = pictures.still_args(
        Path("in.jpg"), asks, script=tmp_path / "g.txt", workspace=tmp_path, settings=settings
    )
    assert argv[0] == settings.ffmpeg_path
    assert argv[argv.index("-filter_complex_script") + 1] == str(tmp_path / "g.txt")
    assert argv.count("-map") == 2 and argv.count("-frames:v") == 2
    assert argv[-1] == str(tmp_path / "01.raw")


def test_the_decoders_words_keep_the_lines_about_broken_bytes_without_an_address() -> None:
    from sift.kernel.ml import pictures

    said = (
        "'C:\\\\tools\\\\ffmpeg.exe' failed with exit code 69: [mjpeg @ 000001c4ab] "
        "Decode error rate 1 exceeds maximum 0.666667\nConversion failed!"
    )
    assert pictures.damaged(said) == (
        "The decoder refused the picture: [mjpeg] Decode error rate 1 exceeds maximum 0.666667"
    )
    assert pictures.damaged("nothing known") == (
        "The decoder refused the picture: the bytes are damaged"
    )


def test_a_still_nothing_prepared_is_read_as_usual_and_one_refused_is_refused() -> None:
    from sift.kernel import media
    from sift.kernel.ml import pictures

    path = Path("a.jpg")
    assert pictures.held(path, filters="scale=4:4", pixel_format="rgb24") is None
    ready = media.PreparedFrames()
    ready.put_raw(path, _ask("scale=4:4", 3), [b"abc"])
    with media.prepared(ready):
        assert pictures.held(path, filters="scale=4:4", pixel_format="rgb24") == b"abc"
        assert pictures.held(path, filters="scale=9:9", pixel_format="rgb24") is None
    with pictures._refusing(path, "Decode error rate 1"):
        with pytest.raises(media.FFmpegError, match="Decode error rate 1"):
            pictures.held(path, filters="scale=4:4", pixel_format="rgb24")
        assert pictures.held(Path("b.jpg"), filters="scale=4:4", pixel_format="rgb24") is None


async def test_one_decode_gives_every_pass_the_pixels_its_own_read_gives(
    tmp_path: Path, settings: Settings
) -> None:
    """The point of the whole thing: the decoder's own scaler on every branch, so a model reads
    the very bytes it read before, from one read of the file."""
    from sift.kernel import media
    from sift.kernel import subprocess as tools
    from sift.kernel.ml import pictures

    target = _still(tmp_path / "still.png", settings)
    asks = [
        _ask("scale=320:240", 320 * 240 * 3),
        _ask("scale=224:224:flags=bilinear", 224 * 224 * 3),
        _ask(
            "split=2[a][b];[a]crop=640:58:0:422[x];[b]crop=256:96:384:384,scale=640:240[y];"
            "[x]pad=640:58[p];[p][y]vstack=inputs=2",
            640 * 298 * 3,
        ),
    ]
    work = tmp_path / "work"
    work.mkdir()
    prepared = await pictures.decode_still(target, asks, workspace=work, settings=settings)

    for ask in asks:
        alone = await tools.capture(
            media.raw_frame_args(
                target,
                pictures.STILL,
                filters=ask.filters,
                pixel_format="rgb24",
                settings=settings,
            ),
            time_limit=60,
        )
        assert prepared.raw(
            target, [pictures.STILL], filters=ask.filters, pixel_format="rgb24"
        ) == [alone]


async def test_a_still_the_decoder_refuses_is_refused_in_its_words(
    tmp_path: Path, settings: Settings
) -> None:
    from sift.kernel import media
    from sift.kernel.ml import pictures

    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a picture at all")
    with pytest.raises(media.FFmpegError):
        await pictures.decode_still(
            broken, [_ask("scale=4:4", 48)], workspace=tmp_path, settings=settings
        )


class _Source:
    def __init__(self, path: Path, media_type: str, width: int | None) -> None:
        from types import SimpleNamespace

        self.path = path
        self.asset = SimpleNamespace(media_type=media_type, width=width, height=width, size_bytes=1)
        self.location = SimpleNamespace(size_bytes=None)


class _Pass:
    """A pass that would ask for these shapes of a still."""

    def __init__(self, *shapes: int) -> None:
        self.shapes = shapes
        self.facts: list[Any] = []

    @property
    def frames(self) -> Any:
        async def plan(facts: Any) -> list[Any]:
            self.facts.append(facts)
            return [_ask(f"scale={one}:{one}", one * one * 3) for one in self.shapes]

        return plan


class _NoFrames:
    frames = None


async def test_a_task_decodes_a_still_once_where_two_asks_would_read_it(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel import media
    from sift.kernel.ml import pictures

    target = _still(tmp_path / "still.png", settings)
    found: dict[str, Any] = {"a": _Source(target, "image", 640)}

    async def resolve(_content: Any, asset_id: str, **_kwargs: Any) -> Any:
        if asset_id not in found:
            raise media.MissingAsset(asset_id)
        return found[asset_id]

    monkeypatch.setattr(media, "resolve_decodable", resolve)
    reader = pictures.OnePicture(Any, settings=settings)  # type: ignore[arg-type]
    both: list[Any] = [_Pass(8), _NoFrames(), _Pass(16)]

    outer = media.PreparedFrames()
    outer.put_raw(Path("other.png"), _ask("scale=2:2", 12), [b"x" * 12])
    with media.prepared(outer):
        async with reader.prepared("a", both):
            assert pictures.held(target, filters="scale=8:8", pixel_format="rgb24") is not None
            assert pictures.held(target, filters="scale=16:16", pixel_format="rgb24") is not None
            kept = media.prepared_now()
            assert kept is not None
            assert kept.raw(
                Path("other.png"), [pictures.STILL], filters="scale=2:2", pixel_format="rgb24"
            ) == [b"x" * 12]
    assert both[0].facts[0].width == 640 and both[0].facts[0].media_type == "image"

    # One ask, a video, a still of unknown size, or no file: nothing is decoded for the task.
    found["v"] = _Source(target, "video", 640)
    found["u"] = _Source(target, "image", None)
    for asset_id, passes in (("a", [_Pass(8)]), ("v", both), ("u", both), ("gone", both)):
        async with reader.prepared(asset_id, passes):
            assert media.prepared_now() is None


async def test_a_damaged_still_is_refused_to_every_pass_and_any_other_refusal_reads_alone(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel import media
    from sift.kernel import subprocess as tools
    from sift.kernel.ml import pictures

    target = tmp_path / "x.jpg"

    async def resolve(_content: Any, asset_id: str, **_kwargs: Any) -> Any:
        return _Source(target, "image", 640)

    said = ["Decode error rate 1 exceeds maximum"]

    async def refuse(argv: list[str], **_kwargs: Any) -> bytes:
        raise tools.ToolFailed(f"ffmpeg failed: {said[0]}", returncode=69, said=said[0])

    monkeypatch.setattr(media, "resolve_decodable", resolve)
    monkeypatch.setattr(tools, "capture", refuse)
    reader = pictures.OnePicture(Any, settings=settings)  # type: ignore[arg-type]
    async with reader.prepared("a", [_Pass(8), _Pass(16)]):
        with pytest.raises(media.FFmpegError, match="Decode error rate"):
            pictures.held(target, filters="scale=8:8", pixel_format="rgb24")
    assert pictures.held(target, filters="scale=8:8", pixel_format="rgb24") is None

    said[0] = "the share stopped answering"
    async with reader.prepared("a", [_Pass(8), _Pass(16)]):
        assert pictures.held(target, filters="scale=8:8", pixel_format="rgb24") is None
