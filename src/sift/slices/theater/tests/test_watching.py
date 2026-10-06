# SPDX-License-Identifier: AGPL-3.0-or-later
"""An open wall's beat: the clip clock eco mode reads, marked from any device, never written down."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from sift.kernel import attention
from sift.kernel.http import CSRF_HEADER_NAME
from sift.slices.theater.tests.conftest import sign_in

# The module, not the package's `router`, which is the APIRouter of the same name.
routes = importlib.import_module("sift.slices.theater.router")

pytestmark = pytest.mark.unit


@pytest.fixture
def played(monkeypatch: pytest.MonkeyPatch) -> attention.Played:
    clock = attention.Played(clock=lambda: 50.0)
    monkeypatch.setattr(routes, "PLAYED", clock)
    return clock


def test_a_beat_from_an_open_wall_marks_the_clock(
    client: TestClient, played: attention.Played
) -> None:
    sign_in(client, "guest")

    assert client.post("/api/theater/watching").status_code == 204
    assert played.seconds_since() == 0.0


def test_a_beat_from_nobody_or_without_the_token_marks_nothing(
    client: TestClient, played: attention.Played
) -> None:
    assert client.post("/api/theater/watching").status_code == 401
    sign_in(client)
    del client.headers[CSRF_HEADER_NAME]
    assert client.post("/api/theater/watching").status_code == 403

    assert played.seconds_since() is None
