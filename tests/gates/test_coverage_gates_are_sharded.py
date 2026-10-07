# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every coverage gate runs, and in exactly one shard of the suite workflow's coverage matrix.

The gates are declared twice in `scripts/coverage_gates.sh`, a label list and a function list, and
the workflow runs them in shards (`--shard K/N`), dealt by how long each gate takes so the shards
end together. Three ways a gate stops running without anything going red, each held here:

- a function defined and in neither list, which no run ever calls;
- the two lists different lengths, which the script refuses at its start and so fails a whole
  shard on a mistake in one line;
- a shard rule that drops or doubles a gate, or a matrix that does not cover every shard it names.

And the two ways the dealing stops being worth having: a recorded time naming a gate that is gone,
and shards that no longer end together.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.gates import posix_bash

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
GATES = REPO / "scripts" / "coverage_gates.sh"
SECONDS = REPO / "scripts" / "coverage_gate_seconds.tsv"
SUITE = REPO / ".github" / "workflows" / "suite.yml"

#: Defined in the script and not gates: the runner every gate calls, and the fan-out's worker.
_NOT_GATES = {"_cov", "_cov_gate_worker"}


def _bash(script: str) -> list[str]:
    done = subprocess.run(
        [posix_bash(), "-c", ". scripts/coverage_gates.sh; " + script],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    return done.stdout.splitlines()


def _shard(k: int, n: int, before: str = "") -> list[str]:
    return _bash(f"{before} cov_take_shard {k} {n} && printf '%s\\n' \"${{COV_LABELS[@]}}\"")


def _recorded() -> dict[str, int]:
    """Each gate's recorded seconds, by its label."""
    lines = [line for line in SECONDS.read_text(encoding="utf-8").splitlines() if line[:1] != "#"]
    return {label: int(seconds) for seconds, label in (line.split("\t") for line in lines)}


def _matrix_width() -> int:
    """The shard count the workflow runs, read from its matrix and held to its `/N`."""
    text = SUITE.read_text(encoding="utf-8")
    job = text[text.index("\n  coverage:") :]
    listed = re.search(r"shard: \[([0-9, ]+)\]", job)
    said = re.search(r'--shard "\$\{\{ matrix\.shard \}\}/(\d+)"', job)
    assert listed is not None and said is not None, "the coverage job is not a shard matrix"
    shards = [int(one) for one in listed.group(1).split(",")]
    assert shards == list(range(1, len(shards) + 1)), shards
    assert int(said.group(1)) == len(shards), "the matrix and its `/N` disagree"
    return len(shards)


def test_every_defined_gate_is_listed_once_and_the_lists_agree() -> None:
    defined = set(re.findall(r"^(_cov_[a-z0-9_]+)\(\)", GATES.read_text(encoding="utf-8"), re.M))
    labels = _bash("printf '%s\\n' \"${COV_LABELS[@]}\"")
    functions = _bash("printf '%s\\n' \"${COV_FNS[@]}\"")
    assert len(labels) == len(functions)
    assert len(set(labels)) == len(labels) and len(set(functions)) == len(functions)
    assert set(functions) == defined - _NOT_GATES


@pytest.mark.parametrize("n", [1, 2, 5])
def test_every_gate_is_in_exactly_one_shard(n: int) -> None:
    everything = _bash("printf '%s\\n' \"${COV_LABELS[@]}\"")
    dealt = [label for k in range(1, n + 1) for label in _shard(k, n)]
    assert sorted(dealt) == sorted(everything)


def test_the_workflows_shards_deal_every_gate_once() -> None:
    n = _matrix_width()
    everything = _bash("printf '%s\\n' \"${COV_LABELS[@]}\"")
    dealt = [label for k in range(1, n + 1) for label in _shard(k, n)]
    assert sorted(dealt) == sorted(everything)


def test_the_workflows_shards_end_together() -> None:
    """A run is as long as its longest shard. No shard is longer than the even share by more
    than a sixth, unless one gate alone is: a gate cannot be split, so it is the floor."""
    n = _matrix_width()
    recorded = _recorded()
    middle = sorted(recorded.values())[len(recorded) // 2]
    loads = [sum(recorded.get(label, middle) for label in _shard(k, n)) for k in range(1, n + 1)]
    even = sum(loads) / n
    assert max(loads) <= max(max(recorded.values()), even * 7 / 6), loads


def test_every_recorded_time_names_a_gate_there_is() -> None:
    """A time kept for a gate that was renamed or removed weighs nothing and says nothing."""
    everything = set(_bash("printf '%s\\n' \"${COV_LABELS[@]}\""))
    assert set(_recorded()) <= everything, sorted(set(_recorded()) - everything)


def test_a_gate_nobody_has_timed_is_still_dealt_once() -> None:
    """With no times at all, every gate counts the same and each is still in one shard."""
    everything = _bash("printf '%s\\n' \"${COV_LABELS[@]}\"")
    untimed = "COV_SECONDS_FILE=/nowhere/at/all;"
    dealt = [label for k in range(1, 5) for label in _shard(k, 4, untimed)]
    assert sorted(dealt) == sorted(everything)
    sizes = {len(_shard(k, 4, untimed)) for k in range(1, 5)}
    assert max(sizes) - min(sizes) <= 1


def test_a_shard_that_does_not_exist_is_refused() -> None:
    for spec in ("0 6", "7 6", "a 6", "1 0"):
        refused = subprocess.run(
            [posix_bash(), "-c", f". scripts/coverage_gates.sh; cov_take_shard {spec}"],
            cwd=REPO,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert refused.returncode == 2, spec


def _cov_with_a_stub_uv(before: str) -> list[str]:
    """Run `_cov` with uv replaced by a stub that prints the data file it was handed."""
    return _bash(
        'export TMPDIR="$PWD"; uvstub() { echo "$COVERAGE_FILE"; }; export UV=uvstub; '
        f'{before} _cov a; echo "after=${{COVERAGE_FILE:-unset}}"'
    )


def test_a_gate_sourced_alone_measures_into_a_file_of_its_own_and_clears_it_up() -> None:
    """Two gates run by hand together would otherwise share the working directory's one data file
    and overwrite each other's numbers into a green figure that measured nothing."""
    handed, after = _cov_with_a_stub_uv("")
    assert "sift-cov." in handed, handed
    assert not Path(handed).exists(), "the lone call's data file was not cleared up"
    assert after == "after=unset"


def test_a_data_file_already_chosen_is_the_one_a_gate_measures_into() -> None:
    handed, after = _cov_with_a_stub_uv("export COVERAGE_FILE=chosen/data.cov;")
    assert handed == "chosen/data.cov"
    assert after == "after=chosen/data.cov"
