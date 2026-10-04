# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numeric thread pools are held to one thread, before any of them is loaded.

A process holding a numeric thread pool cannot reliably start another program: the pools install
handlers that run across a fork and can deadlock, leaving a launch that never returns and a server
that answers nothing. The libraries read their setting once, on load, so it must be in place first.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import sift

_INIT = Path(sift.__file__)


def test_every_numeric_pool_is_pinned_by_importing_the_package() -> None:
    """Importing the package pins every pool."""
    import os

    for name in sift.NUMERIC_THREAD_LIMITS:
        assert os.environ[name] == "1", name


def test_all_four_libraries_are_named() -> None:
    """All four libraries are named."""
    assert set(sift.NUMERIC_THREAD_LIMITS) == {
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    }


def test_a_larger_value_already_set_is_replaced_and_reported() -> None:
    """A larger value already set is replaced and reported, in a child process so the real
    import-time path runs."""
    proof = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sift, os;"
            "print(os.environ['OPENBLAS_NUM_THREADS']);"
            "print(sift.NUMERIC_THREADS_REPLACED.get('OPENBLAS_NUM_THREADS'))",
        ],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "OPENBLAS_NUM_THREADS": "8",
            "PYTHONPATH": str(_INIT.parents[1]),
        },
        check=True,
    )
    held, replaced = proof.stdout.split()
    assert held == "1"
    assert replaced == "8"


@pytest.mark.parametrize(
    ("already", "reported"),
    [
        # A machine tuned for numeric work carries a bigger number.
        ("8", True),
        # Already right: nothing to report.
        ("1", False),
        # Nothing set, as on every ordinary install.
        (None, False),
    ],
)
def test_what_was_overridden_is_reported_and_what_was_not_is_silent(
    already: str | None, reported: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinning called directly: its return is the boot line, worth saying only when somebody's
    value was replaced."""
    name = "OPENBLAS_NUM_THREADS"
    if already is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, already)
    for other in sift.NUMERIC_THREAD_LIMITS:
        if other != name:
            monkeypatch.setenv(other, "1")

    replaced = sift._hold_numeric_libraries_to_one_thread()

    import os

    assert os.environ[name] == "1", "the pin did not take"
    assert (name in replaced) is reported
    if reported:
        assert replaced[name] == already


def test_the_pin_happens_before_numpy_can_be_loaded() -> None:
    """Importing the package pulls in nothing that starts a pool first, read from source since the
    package is already imported."""
    tree = ast.parse(_INIT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    allowed = {"os", "sys", "__future__"}
    assert imported <= allowed, (
        f"{_INIT.name} imports {sorted(imported - allowed)}. Anything beyond {sorted(allowed)} risks "
        "loading a numeric library before the pools are pinned, which silently undoes the pinning."
    )


@pytest.mark.parametrize("library", ["numpy"])
def test_the_pool_really_is_one_thread_in_a_fresh_process(library: str) -> None:
    """The numeric library itself reports one thread in a fresh process."""
    pytest.importorskip(library)
    proof = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sift;"
            "import numpy;"
            "import os;"
            "print(os.environ['OPENBLAS_NUM_THREADS'], os.environ['OMP_NUM_THREADS'])",
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(_INIT.parents[1])},
        check=True,
    )
    assert proof.stdout.split() == ["1", "1"]


def test_the_interpreter_hands_its_turn_over_quickly() -> None:
    """The interpreter hands its turn over quickly: `sqlite3` gives the turn up once per row, so at
    the default switch interval a wide read queues behind busy threads per row. A ceiling, so it can
    be tuned down and never silently back to the default."""
    assert sift.GIL_SWITCH_SECONDS <= 0.0005
    assert sys.getswitchinterval() <= 0.0005


def test_the_setting_is_actually_applied_and_not_merely_declared() -> None:
    """The switch interval is applied, read in a fresh process."""
    proof = subprocess.run(
        [sys.executable, "-c", "import sift, sys; print(sys.getswitchinterval())"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(_INIT.parents[1])},
        check=True,
    )
    assert float(proof.stdout.strip()) == sift.GIL_SWITCH_SECONDS
