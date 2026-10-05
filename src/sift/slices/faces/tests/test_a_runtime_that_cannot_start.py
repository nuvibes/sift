# SPDX-License-Identifier: AGPL-3.0-or-later
"""A model runtime that dies as it loads costs one child, and the Faces screen says so."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import Settings
from sift.kernel.ml import child as ml_child
from sift.kernel.ml.runtime import DeviceUnavailable
from sift.kernel.tests.test_child import CRASH, a_runtime
from sift.slices.faces import FaceService
from sift.slices.faces.tests import test_routes
from sift.slices.faces.tests.conftest import FakePreferences
from sift.slices.faces.tests.test_routes import sign_in, turn_on
from sift.wiring.readiness import recognition_can_run

pytestmark = pytest.mark.integration

app = test_routes.app
client = test_routes.client


def test_a_runtime_that_kills_its_process_leaves_the_server_answering_in_words(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a_runtime(tmp_path, monkeypatch, CRASH)
    started: list[int] = []

    def counted(settings: Settings) -> tuple[str, ...]:
        started.append(1)
        return ml_child._one_shot(settings)

    monkeypatch.setattr(ml_child, "DEVICES", ml_child.DeviceQuestion(counted))
    monkeypatch.setattr(ml_child, "_RUNNING", set())
    turn_on(client)
    sign_in(client, "admin")

    first = client.get("/api/faces/settings")
    again = client.get("/api/faces/settings")

    assert first.status_code == again.status_code == 200
    problem = first.json()["device_problem"]
    assert problem.startswith("Recognition can't run on this device: the model runtime couldn't")
    assert problem.endswith("Restart Sift to try again.")
    assert again.json()["device_problem"] == problem
    assert started == [1], "a runtime that crashed is not started again on every ask"


async def test_the_identify_readiness_says_the_same_words_and_never_raises(
    service: FaceService, preferences: FakePreferences, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked by the task queue moments after every start: a crash here was a crash per start."""

    def crashed(_settings: Settings) -> tuple[str, ...]:
        raise DeviceUnavailable("it stopped with code 0xC0000005")

    monkeypatch.setattr(ml_child, "DEVICES", ml_child.DeviceQuestion(crashed))
    preferences.set("faces.enabled", True)

    answer = await recognition_can_run(service)

    assert answer.ready is False
    assert answer.problem == (
        "Recognition can't run on this device: the model runtime couldn't start "
        "(it stopped with code 0xC0000005). Restart Sift to try again."
    )
