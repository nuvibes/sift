# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every test of the device's measurements shares."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from sift.slices.performance.tests.test_runner import close_the_stores


@pytest.fixture(autouse=True)
async def _stores_closed() -> AsyncIterator[None]:
    """The rates databases a test opened are closed when it ends, passed or failed."""
    yield
    await close_the_stores()
