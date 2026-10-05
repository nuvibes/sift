# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every test of the device's measurements shares."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from sift.kernel import device_load
from sift.slices.performance.tests.test_runner import close_the_stores


@pytest.fixture(autouse=True)
async def _stores_closed() -> AsyncIterator[None]:
    """The rates databases a test opened are closed when it ends, passed or failed."""
    yield
    await close_the_stores()


@pytest.fixture(autouse=True)
def _no_level_taken_twice_for_this_machines_own_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_load.READER, "latest", None)
