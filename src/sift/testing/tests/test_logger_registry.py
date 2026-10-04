# SPDX-License-Identifier: AGPL-3.0-or-later
"""The logger registry can be walked while another thread adds a logger to it.

pytest's log capture walks the registry as every test phase starts, and a booted app creates
loggers on the test client's thread at the same moment. The walk here is pytest's own, so what
passes is the capture the suite actually runs under.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator

import pytest
from _pytest.logging import LogCaptureHandler, catching_logs

from sift.kernel.ids import new_id

#: Long enough for a thread on a loaded machine; the interleaving never waits this long.
_PATIENCE_SECONDS = 10.0


class _Midway(logging.Logger):
    """A logger that, when a walker reads whether it propagates, waits for another thread's write.

    That read is the body of pytest's walk, so the write lands between two steps of it: the
    interleaving a race would need luck for, made certain.
    """

    def __init__(self, name: str, writer_may_go: threading.Event, written: threading.Event) -> None:
        self._armed = False
        self._stored = True
        super().__init__(name)
        self._writer_may_go = writer_may_go
        self._written = written
        self.landed_mid_walk: bool | None = None
        self._armed = True

    @property
    def propagate(self) -> bool:
        if self._armed:
            self._armed = False
            self._writer_may_go.set()
            self.landed_mid_walk = self._written.wait(_PATIENCE_SECONDS)
        return self._stored

    @propagate.setter
    def propagate(self, value: bool) -> None:
        self._stored = value


@pytest.fixture
def names() -> Iterator[list[str]]:
    """Logger names a test makes, taken back out of the process-wide registry afterwards."""
    made: list[str] = []
    yield made
    registry = logging.Logger.manager.loggerDict
    for name in made:
        registry.pop(name, None)


def test_a_logger_made_mid_walk_does_not_break_the_walk(names: list[str]) -> None:
    writer_may_go, written = threading.Event(), threading.Event()
    midway = _Midway(f"sift-walked-{new_id()}", writer_may_go, written)
    fresh = f"sift-made-mid-walk-{new_id()}"
    names.extend([midway.name, fresh])
    logging.Logger.manager.loggerDict[midway.name] = midway

    def write() -> None:
        if writer_may_go.wait(_PATIENCE_SECONDS):
            logging.getLogger(fresh)  # nosemgrep: sift-no-print-or-raw-logger
            written.set()

    writer = threading.Thread(target=write, name="logger-writer")
    writer.start()
    try:
        with catching_logs(LogCaptureHandler()):
            pass
    finally:
        writer_may_go.set()
        writer.join(_PATIENCE_SECONDS)

    # The write has to have happened DURING the walk, or this proved nothing.
    assert midway.landed_mid_walk is True
    assert fresh in logging.Logger.manager.loggerDict


def test_the_registry_still_hands_out_one_logger_per_name(names: list[str]) -> None:
    parent = f"sift-parent-{new_id()}"
    names.extend([parent, f"{parent}.child"])

    child = logging.getLogger(f"{parent}.child")  # nosemgrep: sift-no-print-or-raw-logger
    assert logging.getLogger(f"{parent}.child") is child  # nosemgrep: sift-no-print-or-raw-logger
    # Made after its child, the parent takes the placeholder's place in the hierarchy.
    made = logging.getLogger(parent)  # nosemgrep: sift-no-print-or-raw-logger
    assert child.parent is made
    assert {parent, f"{parent}.child"} <= set(logging.Logger.manager.loggerDict)
