# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which test files one shard of the suite runs.

    python .github/scripts/shard_tests.py <index> <count>   the files of shard <index> of <count>
    python .github/scripts/shard_tests.py --alone           the files that run in a job of their own

Round-robin over the sorted list of every test file under pytest's `testpaths`, so every file lands
in exactly one shard with nothing to keep up to date: a new test file or a new slice is in a shard
the moment it exists, and the files of one large directory are spread across all of them rather
than handed to one. Balanced by file count, not by duration; a file slow enough to dominate a
shard goes in ALONE and gets a job of its own.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Files run in a job of their own rather than in a shard, each because it alone is a large part of
#: the suite's wall clock.
ALONE = ("tests/gates/test_authz_matrix.py",)


def declared_testpaths(root: Path = ROOT) -> tuple[str, ...]:
    """The directories pytest collects from, as `pyproject.toml` declares them."""
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return tuple(config["tool"]["pytest"]["ini_options"]["testpaths"])


def collected_files(root: Path = ROOT) -> list[str]:
    """Every test module pytest would collect, as sorted POSIX paths relative to the root."""
    found = {
        path.relative_to(root).as_posix()
        for where in declared_testpaths(root)
        for pattern in ("test_*.py", "*_test.py")
        for path in (root / where).rglob(pattern)
        if "node_modules" not in path.parts
    }
    return sorted(found)


def shard(files: list[str], index: int, count: int) -> list[str]:
    """Shard `index` of `count`: every `count`-th file, starting at `index`, less the ALONE ones."""
    if count < 1 or not 0 <= index < count:
        raise ValueError(f"shard {index} of {count} does not exist")
    return [name for name in files if name not in ALONE][index::count]


def main(argv: list[str]) -> int:
    if argv == ["--alone"]:
        chosen = list(ALONE)
    elif len(argv) == 2 and all(one.isdigit() for one in argv):
        chosen = shard(collected_files(), int(argv[0]), int(argv[1]))
    else:
        sys.stderr.write(__doc__ or "")
        return 2
    missing = [name for name in chosen if not (ROOT / name).is_file()]
    if missing or not chosen:
        # A shard that runs nothing passes; say so instead.
        sys.stderr.write(f"shard_tests: nothing to run, or missing files: {missing}\n")
        return 1
    # Bytes, so the lines end in a bare newline on Windows too: the shell that reads them keeps a
    # carriage return as part of the name, and pytest then finds no such file and runs nothing.
    sys.stdout.buffer.write(("\n".join(chosen) + "\n").encode())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
