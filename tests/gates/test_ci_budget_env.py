# SPDX-License-Identifier: AGPL-3.0-or-later
"""The concurrency budget is read from the environment, and must not be left in it.

The knobs are named SIFT_CI_JOBS / SIFT_CI_RESERVE / SIFT_CI_CHECKOUTS, and the ordinary way to pass
one (`SIFT_CI_CHECKOUTS=1 scripts/ci-local.sh`) exports it to every process the run starts. Some
of those processes are the application: the integration gates build it and the end-to-end run serves
it, and it refuses to start on a SIFT_ variable it does not recognize. That guard exists to catch a
typo in a real setting and cannot tell one of these from one of those, so the entire run fails with
the same configuration error and none of the messages names the variable that caused it.

Both halves are asserted here: the value is still honoured, and the variable is gone afterwards.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.gates import posix_bash

pytestmark = pytest.mark.gate

REPO = Path(__file__).resolve().parents[2]

KNOBS = ("SIFT_CI_JOBS", "SIFT_CI_RESERVE", "SIFT_CI_CHECKOUTS")


def _bash(script: str, **env: str) -> str:
    done = subprocess.run(
        [posix_bash(), "-c", script],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", **env},
    )
    return done.stdout.strip()


@pytest.mark.parametrize("knob", KNOBS)
def test_the_budget_knobs_do_not_survive_into_the_environment(knob: str) -> None:
    left = _bash(
        ". scripts/coverage_gates.sh; sift_ci_take_env; env | grep '^SIFT_CI' || true",
        **{knob: "1"},
    )
    assert left == "", f"{knob} is still in the environment; the application will refuse to boot"


#: A machine of 24 threads, whatever this one has. On a small one both answers sit at the floor of
#: two and the comparison below would prove nothing; a function named `nproc` wins over the program.
ON_24_THREADS = "nproc() { echo 24; }; "


def test_the_number_of_checkouts_still_changes_the_answer() -> None:
    """The unsetting is worth nothing if it happens before the value is read."""
    script = ON_24_THREADS + ". scripts/coverage_gates.sh; sift_ci_take_env; sift_ci_jobs"
    one = _bash(script, SIFT_CI_CHECKOUTS="1")
    three = _bash(script, SIFT_CI_CHECKOUTS="3")
    assert int(one) > int(three)


def test_an_explicit_job_count_wins_outright() -> None:
    assert (
        _bash(". scripts/coverage_gates.sh; sift_ci_take_env; sift_ci_jobs", SIFT_CI_JOBS="7")
        == "7"
    )


def test_the_resolved_budget_survives_a_second_question() -> None:
    """The fan-out asks again on its own, after the knobs are gone. It has to get the same answer."""
    answers = _bash(
        ". scripts/coverage_gates.sh; sift_ci_take_env; sift_ci_jobs; sift_ci_jobs",
        SIFT_CI_CHECKOUTS="1",
    ).split()
    assert answers[0] == answers[1]
