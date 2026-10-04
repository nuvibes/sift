# SPDX-License-Identifier: AGPL-3.0-or-later
"""A module's logger, fresh, so a capture in a test sees what it writes.

Sift caches a logger's processors on its first use (`kernel.log`), and `structlog`'s
`capture_logs` swaps the processors of loggers not yet used. A module whose logger an earlier
test already used writes past the capture: a test asserting on its lines reads an empty list, and
one asserting on their absence passes on nothing. Both depend on test order, so they show in a
whole-suite run and in neither file alone. The capture is given a logger that has not been used
yet.
"""

from __future__ import annotations

from types import ModuleType

import pytest

from sift.kernel.log import get_logger


def uncached_log(monkeypatch: pytest.MonkeyPatch, module: ModuleType) -> None:
    """Replace `module.log` for the test with a logger nothing has used yet."""
    monkeypatch.setattr(module, "log", get_logger(module.__name__))
