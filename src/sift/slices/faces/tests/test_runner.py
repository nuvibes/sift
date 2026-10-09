# SPDX-License-Identifier: AGPL-3.0-or-later
"""Choosing a device when a model is loaded: one asked for and missing is a failure naming the
missing half (the hardware or its software), never a quiet fallback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.slices.faces.runner import (
    ChildRunner,
    DeviceUnavailable,
    Runner,
    resolve_provider,
)
from sift.slices.faces.weights import CATALOG, WeightError

pytestmark = pytest.mark.unit

CPU = "CPUExecutionProvider"
CUDA = "CUDAExecutionProvider"


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


def test_the_processor_is_always_available_and_needs_nothing() -> None:
    """Which is what makes it the guarantee rather than an option."""
    assert resolve_provider("cpu", machine(), [CPU]) == [CPU]


def test_a_graphics_card_that_is_present_and_driveable_is_used() -> None:
    resolved = resolve_provider("nvidia", machine(cuda=True), [CUDA, CPU])

    assert resolved[0] == CUDA
    # The processor backs single operations the card cannot do; the model still runs on the card.
    assert resolved[-1] == CPU


def test_asking_for_a_card_this_machine_does_not_have_fails_loudly() -> None:
    with pytest.raises(DeviceUnavailable) as failure:
        resolve_provider("nvidia", machine(cuda=False), [CUDA, CPU])

    message = str(failure.value)
    assert "NVIDIA" in message
    # No card: nothing to fetch, so no install is offered.
    assert "Set the device back to the processor" in message


def test_a_card_with_no_software_to_drive_it_fails_differently() -> None:
    """The card is there and its software is not: the one refusal that offers a fetch, and says
    where."""
    with pytest.raises(DeviceUnavailable) as failure:
        resolve_provider("nvidia", machine(cuda=True), [CPU])

    message = str(failure.value)
    assert "not installed" in message
    assert "Sift can fetch it for you" in message
    assert "Set the device back to the processor" not in message
    assert "under GPU in Settings > Performance" in message


def test_a_device_sift_does_not_know_about_is_refused_with_the_list_of_ones_it_does() -> None:
    with pytest.raises(DeviceUnavailable, match="not a device Sift knows"):
        resolve_provider("quantum", machine(), [CPU])


def test_a_model_cannot_be_loaded_before_it_has_been_obtained(
    settings: Settings,
) -> None:
    """No model ships with Sift, so this is the ordinary state of a fresh install."""
    runner = Runner(settings, machine())

    with pytest.raises(WeightError, match="not been installed"):
        runner.load(CATALOG["accurate.detector"])


def test_a_runner_starts_on_the_processor_unless_told_otherwise(settings: Settings) -> None:
    assert Runner(settings, machine()).device == "cpu"
    assert Runner(settings, machine(cuda=True), device="nvidia").device == "nvidia"


def test_the_runner_in_a_process_of_its_own_is_built_the_same_way(settings: Settings) -> None:
    """The runner the service actually runs on, built here so something does; it starts no child
    until a model loads."""
    child = ChildRunner(settings, machine())
    assert child.device == "cpu"
    assert ChildRunner(settings, machine(cuda=True), device="nvidia").device == "nvidia"


def test_dropping_the_models_gives_the_memory_back(settings: Settings) -> None:
    """What switching the feature off does."""
    runner = Runner(settings, machine())
    runner._loaded["something"] = object()  # type: ignore[assignment]

    runner.unload()

    assert runner._loaded == {}


@pytest.mark.parametrize("in_a_process_of_its_own", [True, False])
def test_a_lost_card_leaves_the_model_the_shape_and_the_input_it_was_handed(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, in_a_process_of_its_own: bool
) -> None:
    """The face pass names the picture the models were working on, from either runner."""
    import numpy as np
    from structlog.testing import capture_logs

    from sift.kernel.ml.child import ChildRunner as KernelChildRunner
    from sift.kernel.ml.runtime import DeviceLost
    from sift.kernel.ml.runtime import Runner as KernelRunner
    from sift.slices.faces import runner as face_runner
    from sift.testing.logs import uncached_log

    def lost(*_args: object, **_kwargs: object) -> list[np.ndarray]:
        raise DeviceLost("an NVIDIA graphics card stopped answering")

    kernel_runner = KernelChildRunner if in_a_process_of_its_own else KernelRunner
    face_runner_class = ChildRunner if in_a_process_of_its_own else Runner
    monkeypatch.setattr(kernel_runner, "run", lost)
    uncached_log(monkeypatch, face_runner)
    loaded = SimpleNamespace(weight=SimpleNamespace(id="accurate.detector"))
    blob = np.zeros((1, 3, 640, 640), dtype=np.float32)
    token = face_runner.IN_FLIGHT.set("file asset-1")
    try:
        with capture_logs() as logs, pytest.raises(DeviceLost):
            face_runner_class(settings, machine(cuda=True), device="nvidia").run(loaded, blob)  # type: ignore[arg-type]
    finally:
        face_runner.IN_FLIGHT.reset(token)

    [said] = [one for one in logs if one["event"] == "faces.device.lost_on"]
    assert said["weight"] == "accurate.detector"
    assert said["shape"] == [1, 3, 640, 640]
    assert said["input"] == "file asset-1"
