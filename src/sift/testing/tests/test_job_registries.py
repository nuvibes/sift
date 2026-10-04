# SPDX-License-Identifier: AGPL-3.0-or-later
"""A test module's handlers never reach the next module, however wide the fixture that claimed them.

The per-test restore only hands back what changed inside a test. A module-scoped fixture that
boots the application claims its handlers before any test begins, so without a restore at the
module's own edge they stay claimed, and every later boot in that process refuses its first
handler as already registered, in a module that did nothing wrong.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sift.kernel.jobs import worker_pool
from sift.testing.fixtures import JOB_REGISTRIES, job_registries_kept

#: A module whose module-scoped fixture claims a job type, as a booted application does.
_CLAIMS = """
import pytest
from sift.kernel.jobs import worker_pool

async def _nothing(job, context):
    return None

@pytest.fixture(scope="module")
def claimed():
    worker_pool.register_handler("kept_apart", _nothing, name="Kept apart", exclusive=True)
    yield

def test_claims(claimed):
    assert "kept_apart" in worker_pool._HANDLERS
"""

#: The module run after it, which claims the same type as its own boot would.
_CLAIMS_AGAIN = """
from sift.kernel.jobs import worker_pool

async def _nothing(job, context):
    return None

def test_claims_again():
    assert "kept_apart" not in worker_pool._EXCLUSIVE
    worker_pool.register_handler("kept_apart", _nothing, name="Kept apart")
"""

#: Long enough for a cold interpreter importing the kernel on a loaded machine.
_PATIENCE_SECONDS = 120


def test_a_module_scoped_claim_is_handed_back_before_the_next_module(tmp_path: Path) -> None:
    (tmp_path / "test_a_claims.py").write_text(_CLAIMS, encoding="utf-8")
    (tmp_path / "test_b_claims_again.py").write_text(_CLAIMS_AGAIN, encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "sift.testing.fixtures",
            "-p",
            "no:cacheprovider",
            "-q",
            "--rootdir",
            str(tmp_path),
            "test_a_claims.py",
            "test_b_claims_again.py",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=_PATIENCE_SECONDS,
        check=False,
    )
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-3000:]
    assert "2 passed" in run.stdout


def test_every_registry_the_pool_keeps_is_handed_back() -> None:
    """A registry added to the pool and not to the list would leak past both restores."""
    kept = {
        name
        for name, value in vars(worker_pool).items()
        if name.startswith("_") and name.isupper() and isinstance(value, dict | set)
    }
    # A hold is declared by key and declared again by every boot, so a second boot never refuses it.
    kept.discard("_HOLDS")
    assert kept == set(JOB_REGISTRIES)


def test_what_a_boot_claims_inside_the_keeping_is_gone_after_it() -> None:
    async def nothing(*_: object) -> None:
        return None

    with job_registries_kept():
        worker_pool.register_handler("kept_apart", nothing, name="Kept apart", exclusive=True)
    assert "kept_apart" not in worker_pool._HANDLERS
    assert "kept_apart" not in worker_pool._EXCLUSIVE
