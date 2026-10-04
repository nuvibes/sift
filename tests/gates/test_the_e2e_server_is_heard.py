# SPDX-License-Identifier: AGPL-3.0-or-later
"""The end-to-end run can hear the application it is testing.

Sift logs to STDOUT (`kernel/log.py`), and Playwright forwards a web server's stdout only when
`webServer.stdout` is `pipe`, dropping it silently otherwise. And at INFO every request is a line,
so `SIFT_LOG_LEVEL=WARNING` in `e2e/serve.mjs` keeps a refusal visible and a request not. Both are
one line nothing else reads, so both are pinned.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
CONFIG = FRONTEND / "playwright.config.ts"
SERVER = FRONTEND / "e2e" / "serve.mjs"


def test_the_run_is_handed_the_servers_stdout() -> None:
    """The run is handed the server's stdout."""
    source = CONFIG.read_text(encoding="utf-8")

    assert re.search(r"^\s*stdout:\s*'pipe'", source, re.MULTILINE), (
        "playwright.config.ts no longer pipes the web server's stdout. Sift logs to stdout and "
        "Playwright drops it by default, so every warning the application makes during a run "
        "would go nowhere, and a failure the server had already explained in one line would "
        "be chased blind."
    )


def test_the_server_is_started_at_a_level_worth_reading() -> None:
    """The server starts at WARNING: at INFO every request is a line."""
    source = SERVER.read_text(encoding="utf-8")

    assert re.search(r"SIFT_LOG_LEVEL:\s*'WARNING'", source), (
        "e2e/serve.mjs no longer starts the server at WARNING. Its stdout is piped into the run's "
        "output, and at the default INFO that is one line for every HTTP request the suite makes, "
        "which buries the refusals the pipe exists to show."
    )


def test_the_application_still_logs_where_the_pipe_is_pointed() -> None:
    """Sift still logs to stdout, the premise of both: on stderr the pipe would be unneeded."""
    log_setup = (
        Path(__file__).resolve().parents[2] / "src" / "sift" / "kernel" / "log.py"
    ).read_text(encoding="utf-8")

    assert "logging.StreamHandler(sys.stdout)" in log_setup, (
        "the application no longer logs to stdout, so the reasoning behind piping the web "
        "server's stdout in playwright.config.ts no longer holds. Re-read both."
    )
