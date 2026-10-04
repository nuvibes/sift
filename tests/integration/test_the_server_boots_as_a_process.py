# SPDX-License-Identifier: AGPL-3.0-or-later
"""The server started the way the desktop shell starts it: `python -m sift.main`, as a process.

Every other test imports `sift.main` once, and that is the one shape a process start does NOT
have: run as `-m`, the module executes as `__main__`, and if the server then imports itself by
name (`"sift.main:app"`) every line at module level runs a second time. A retirement declared at
module level is then refused as "retired twice" and Sift fails to boot while every unit test is
green, because none of them starts a process. This one does.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.regression]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _up(url: str, deadline: float, process: subprocess.Popen[bytes]) -> bool:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as answer:  # noqa: S310 (our own loopback)
                if answer.status == 200:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


def test_the_module_starts_as_a_process_and_answers(tmp_path: Path) -> None:
    port = _free_port()
    env = {
        **os.environ,
        "SIFT_DATA_DIR": str(tmp_path / "data"),
        "SIFT_CACHE_DIR": str(tmp_path / "cache"),
        "SIFT_HOST": "127.0.0.1",
        "SIFT_PORT": str(port),
        "SIFT_LOG_LEVEL": "WARNING",
        # The shell's own pipe: closing stdin is how the process is asked to stop, and the flag is
        # what makes it listen. See `stop_when_the_parent_lets_go`.
        "SIFT_STOP_ON_STDIN_EOF": "true",
    }
    # Its output goes to a file rather than a pipe nobody drains, so a chatty start cannot block
    # the process on a full pipe and read here as a server that never came up.
    said = tmp_path / "server.log"
    with said.open("wb") as sink:
        process = subprocess.Popen(
            [sys.executable, "-m", "sift.main"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    try:
        answered = _up(f"http://127.0.0.1:{port}/health", time.monotonic() + 90, process)
        if not answered:
            pytest.fail(
                "the server did not answer /health as a process:\n"
                + said.read_text(encoding="utf-8", errors="replace")
            )
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
