# SPDX-License-Identifier: AGPL-3.0-or-later
"""A start that fails part way ends the process, with uvicorn's start-up failure code.

The database's connections each run on a thread the interpreter waits for at exit, so a start that
fails after the database opened, and does not close it, logs "Application startup failed" and
then never ends. The parent's pipe cannot end it either: it is watched only once the server is
up. So the failure is planted here in a real process, at the first step after the database opens
and at the last step before the server answers, and the process must end on its own while its
parent still holds its stdin.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.regression]

#: uvicorn's own exit code for a start-up that failed (`uvicorn.config.STARTUP_FAILURE`).
STARTUP_FAILURE = 3

#: Far longer than a failed start takes; a process still running after this is the fault.
SECONDS_TO_END = 60

#: Each planted failure: the name patched in the running process, and what it becomes.
PLANTED = {
    # The schema, which is applied after the connections are open.
    "the schema": (
        "import sift.kernel.db as target\n"
        "async def refuse(self):\n"
        "    raise RuntimeError('planted: the schema did not apply')\n"
        "target.Database.initialize_schema = refuse\n"
    ),
    # The last step before the server answers: the workers, the watcher and every loop are up.
    "the last step": (
        "import sift.wiring.lifespan as target\n"
        "def refuse():\n"
        "    raise RuntimeError('planted: the last step failed')\n"
        "target.retired_variables_in_use = refuse\n"
    ),
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.mark.parametrize("where", sorted(PLANTED))
def test_a_start_that_fails_ends_the_process(tmp_path: Path, where: str) -> None:
    data = tmp_path / "data"
    variables = {
        **os.environ,
        "SIFT_DATA_DIR": str(data),
        "SIFT_CACHE_DIR": str(tmp_path / "cache"),
        "SIFT_HOST": "127.0.0.1",
        "SIFT_PORT": str(_free_port()),
        "SIFT_LOG_LEVEL": "WARNING",
        "SIFT_STOP_ON_STDIN_EOF": "true",
    }
    # The module run the way `python -m sift.main` runs it, with one step made to fail first.
    program = PLANTED[where] + textwrap.dedent(
        """
        import runpy
        runpy.run_module("sift.main", run_name="__main__")
        """
    )
    said = tmp_path / "server.log"
    with said.open("wb") as sink:
        process = subprocess.Popen(
            # Said aloud, so a connection left for the collector to stop is seen, not forgiven.
            [sys.executable, "-W", "always::ResourceWarning", "-c", program],
            env=variables,
            stdin=subprocess.PIPE,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    try:
        # Stdin stays open throughout: the process has to end without its parent letting go.
        code = process.wait(timeout=SECONDS_TO_END)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        pytest.fail(
            f"a failed start at {where} did not end the process:\n"
            + said.read_text(encoding="utf-8", errors="replace")
        )
    finally:
        if process.stdin is not None:
            process.stdin.close()
    log = said.read_text(encoding="utf-8", errors="replace")
    assert "planted:" in log, log
    assert code == STARTUP_FAILURE, log
    # Closed, not abandoned: the last connection to close takes the write-ahead log with it.
    assert (data / "sift.sqlite3").exists(), log
    assert not (data / "sift.sqlite3-wal").exists(), log
    assert "deleted before being closed" not in log, log
