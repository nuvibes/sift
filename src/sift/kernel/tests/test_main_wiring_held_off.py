# SPDX-License-Identifier: AGPL-3.0-or-later
"""A start with the optional features held off reads them as off and stores nothing."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs

from sift.kernel.config import get_settings
from sift.slices import faces, semantic, settings_hub, watermarks
from sift.wiring import preferences
from sift.wiring.built import Storage


class _Stored:
    """Every app setting stored as on."""

    async def fetch_one(self, _sql: str, _params: tuple[Any, ...]) -> dict[str, str]:
        return {"value": "true"}


@pytest.fixture
def held(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SIFT_HOLD_OPTIONAL_FEATURES", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _built() -> settings_hub.SettingsService:
    store = cast(Storage, SimpleNamespace(database=_Stored()))
    return preferences.build_preferences(FastAPI(), store, None)


async def test_the_three_switches_read_off_and_the_rest_as_stored(held: None) -> None:
    with capture_logs() as logs:
        hub = _built()

    assert isinstance(hub, preferences.HoldingOff)
    for key in (faces.ENABLED_KEY, semantic.ENABLED_KEY, watermarks.ENABLED_KEY):
        assert await hub.get_app(key) is False
    assert await hub.get_app(faces.PEOPLE_FROM_FILES_KEY) is True
    said = [one for one in logs if one["event"] == "boot.optional_features_held"]
    assert sorted(said[0]["keys"]) == sorted(preferences.HELD_OFF)


async def test_an_ordinary_start_reads_them_as_stored() -> None:
    get_settings.cache_clear()
    hub = _built()

    assert type(hub) is settings_hub.SettingsService
    assert await hub.get_app(faces.ENABLED_KEY) is True
