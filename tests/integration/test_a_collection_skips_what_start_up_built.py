# SPDX-License-Identifier: AGPL-3.0-or-later
"""The running app keeps what start-up built out of every later collection.

A full collection stops everything else for as long as it takes to walk the tracked objects, and
nearly all of them were built at start-up and live until the process ends. Held to the app itself:
the helper works wherever it is called, and nothing else says the server calls it.
"""

from __future__ import annotations

import gc
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.main import create_app

pytestmark = pytest.mark.integration


def test_a_serving_app_has_set_its_start_up_aside_and_gives_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert gc.get_freeze_count() > 100_000, "start-up's objects are still walked"
            assert not any(one is app for one in gc.get_objects())
        assert gc.get_freeze_count() == 0
    finally:
        get_settings.cache_clear()
