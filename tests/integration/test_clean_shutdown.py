# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stopping cleanly when whoever started Sift lets go of its stdin.

On Windows, Node turns every kill into TerminateProcess, so the desktop shell signals a stop by
closing the pipe instead, and the backend closes its database and folds the write-ahead log.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from contextlib import suppress

import pytest

from sift.kernel.config import Settings
from sift.main import stop_when_the_parent_lets_go

pytestmark = pytest.mark.integration


class _Server:
    """Only the flag uvicorn actually reads. `should_exit` is what Ctrl-C sets."""

    def __init__(self) -> None:
        self.should_exit = False


class _Stdin:
    """A real operating-system pipe, which the watch reads by descriptor: only a real pipe can be
    open and empty, which is what "the parent still holds it" looks like."""

    def __init__(self, read_end: int) -> None:
        self._read_end = read_end

    def fileno(self) -> int:
        return self._read_end


def _wait_for_exit(server: _Server, seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.should_exit:
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def pipe() -> Iterator[tuple[int, int]]:
    read_end, write_end = os.pipe()
    try:
        yield read_end, write_end
    finally:
        for descriptor in (read_end, write_end):
            with suppress(OSError):
                os.close(descriptor)


def test_letting_go_of_stdin_asks_the_server_to_stop(
    monkeypatch: pytest.MonkeyPatch, pipe: tuple[int, int]
) -> None:
    read_end, write_end = pipe
    server = _Server()
    monkeypatch.setattr("sift.main.sys.stdin", _Stdin(read_end))

    stop_when_the_parent_lets_go(server)
    os.close(write_end)

    assert _wait_for_exit(server), "end of file on stdin did not ask the server to stop"


def test_a_pipe_THAT_IS_STILL_HELD_does_not_stop_anything(
    monkeypatch: pytest.MonkeyPatch, pipe: tuple[int, int]
) -> None:
    """While the parent holds the pipe nothing stops, bytes or no bytes: only its end lets go."""
    read_end, write_end = pipe
    monkeypatch.setattr("sift.main.sys.stdin", _Stdin(read_end))
    server = _Server()

    stop_when_the_parent_lets_go(server)
    os.write(write_end, b"still here")
    time.sleep(0.3)

    assert server.should_exit is False

    os.close(write_end)
    assert _wait_for_exit(server)


def test_a_descriptor_that_is_not_there_reads_as_the_parent_being_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A descriptor already closed reads as the parent gone. Closed before the watch starts, since
    closing it under a blocked `os.read` does not wake the thread on Windows."""
    read_end, write_end = os.pipe()
    os.close(read_end)
    os.close(write_end)
    monkeypatch.setattr("sift.main.sys.stdin", _Stdin(read_end))
    server = _Server()

    stop_when_the_parent_lets_go(server)

    assert _wait_for_exit(server)


def test_a_process_with_no_stdin_at_all_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sys.stdin` is None under pythonw and in some service hosts. Nothing to watch, and asking
    a None for its descriptor would be an exception at start-up rather than a missing convenience."""
    monkeypatch.setattr("sift.main.sys.stdin", None)
    server = _Server()

    stop_when_the_parent_lets_go(server)
    time.sleep(0.1)

    assert server.should_exit is False


def test_the_watch_does_not_make_the_interpreter_ABORT_ON_ITS_WAY_OUT() -> None:
    """The watch does not make the interpreter abort at shutdown, which a buffered stdin reader does
    ("could not acquire lock for <_io.BufferedReader name='<stdin>'>"). Only a child process can
    show it."""
    program = textwrap.dedent(
        """
        import sys, threading, time
        from sift.main import stop_when_the_parent_lets_go

        class Server:
            should_exit = False

        stop_when_the_parent_lets_go(Server())
        # Let the watch reach its blocking read before the interpreter starts to finalize.
        time.sleep(0.4)
        """
    )
    finished = subprocess.run(
        [sys.executable, "-c", program],
        stdin=subprocess.PIPE,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert "Fatal Python error" not in finished.stderr, finished.stderr
    assert finished.returncode == 0, f"exited {finished.returncode}: {finished.stderr}"


def test_the_setting_is_off_unless_somebody_asks() -> None:
    """The watch is off unless asked for: a container started without `-i` has stdin at end of
    file."""
    assert Settings().stop_on_stdin_eof is False
