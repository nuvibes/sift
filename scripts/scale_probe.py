# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build a library of any size on the real schema, and time the reads that must not grow with it.

    python scripts/scale_probe.py --files 500000
    python scripts/scale_probe.py --files 50000 --keep /tmp/probe   # keep the database

Every listing in Sift reads the stored verdict (`viewer_assets`) instead of resolving permission
per statement, and the point of that design is that a page, a count, a check behind a thumbnail
and an entity wall cost the page and not the library. A test suite proves the rules on a handful
of rows; only a library of the size somebody actually has can prove the cost. This builds the
synthetic one (`sift.testing.fixture_library`) and times each question against a budget in
milliseconds, exiting non-zero when one is over. The same questions are held in SQLite's steps at
two sizes by the statement ledger's gate; the milliseconds are for a big run on a real machine.

The budgets are per question, not per library: they are what the question costs when it costs the
page. A budget that scaled with the library would pass the thing this exists to catch.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from sift.kernel.access import Repository
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.testing.fixture_library import FixtureLibrary, files_under, fixture_library, questions

#: What one of a person's files costs the page that lists them (see the budget below).
PER_MEMBER_MS = 0.012

#: What one copy under a folder costs the share that reaches it (see the budget below).
PER_SHARED_FILE_MS = 0.06

BUDGETS = {
    "admin page, newest": 25.0,
    "admin page, by name": 25.0,
    "admin page continued (keyset)": 25.0,
    "admin count, un-narrowed": 5.0,
    "guest page": 25.0,
    "point check (thumbnail)": 2.0,
    # Bounded by the person's files, never by the library: the page walks their membership rows
    # and sorts what it finds, which is the only walk whose cost does not depend on where in the
    # order their files happen to sit. So this one is a per-member price, on top of the number here.
    "one person's files": 10.0,
    "people wall": 400.0,
    "tags wall": 400.0,
    "hide one file (trigger)": 20.0,
    "tag one file (trigger)": 20.0,
    # Bounded by the copies under the folder, never by the library: every pair the share reaches
    # is re-decided and its rows written, the cost of an answer that is stored. A per-copy price
    # too, with the write lock held for the whole of it.
    "share a folder (trigger)": 200.0,
}

#: The questions priced per member, and what one member costs.
PER_MEMBER = {
    "one person's files": PER_MEMBER_MS,
    "share a folder (trigger)": PER_SHARED_FILE_MS,
}


async def timed_read(read: Callable[[], Awaitable[object]], *, runs: int = 5) -> float:
    """The best of `runs` goes at one read, in milliseconds."""
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        await read()
        best = min(best, time.perf_counter() - started)
    return best * 1000


async def measure(database: Database, access: Repository, lib: FixtureLibrary) -> dict[str, float]:
    """Each question in milliseconds: a read the best of five, a write once, cold."""
    asked = await questions(database, access, lib)
    found: dict[str, float] = {}
    for name, question in asked.items():
        found[name] = await timed_read(question, runs=1 if "(trigger)" in name else 5)
    return found


async def run(files: int, keep: Path | None, seed: int) -> int:
    directory = keep if keep is not None else Path(tempfile.mkdtemp(prefix="sift-scale-"))
    directory.mkdir(parents=True, exist_ok=True)
    lib = await fixture_library(files, seed, directory / "probe.sqlite3")
    print(f"built {files:,} files in {lib.build_seconds:.1f}s")
    database = Database(lib.path, readers=2)
    await database.connect()
    try:
        settings = Settings(data_dir=directory / "data", cache_dir=directory / "cache")
        access = Repository(database, ContentStore(database, settings))
        members = await files_under(database, lib)
        found = await measure(database, access, lib)
    finally:
        await database.close()
        if keep is None:
            shutil.rmtree(directory, ignore_errors=True)
    over = 0
    for name, limit in BUDGETS.items():
        limit += PER_MEMBER.get(name, 0.0) * members.get(name, 0)
        cost = found[name]
        verdict = "ok" if cost <= limit else "OVER"
        if cost > limit:
            over += 1
        print(f"{name:34s} {cost:9.2f} ms  budget {limit:8.1f}  {verdict}")
    print(f"{over} over budget at {files:,} files")
    return 1 if over else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--files", type=int, default=500_000)
    parser.add_argument("--keep", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    return asyncio.run(run(args.files, args.keep, args.seed))


if __name__ == "__main__":
    sys.exit(main())
