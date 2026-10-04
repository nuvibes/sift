# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build a library of any size on the real schema, and time the reads that must not grow with it.

    python scripts/scale_probe.py --files 500000
    python scripts/scale_probe.py --files 50000 --keep /tmp/probe   # keep the database

Every listing in Sift reads the stored verdict (`viewer_assets`) instead of resolving permission
per statement, and the point of that design is that a page, a count, a check behind a thumbnail
and an entity wall cost the page and not the library. A test suite proves the rules on a handful
of rows; only a library of the size somebody actually has can prove the cost. This makes one
(roots, a folder tree, files with copies, people, tags, a few users with grants and a few things
hidden) and measures each question against a budget. It exits non-zero when one is over.

The budgets are per question, not per library: they are what the question costs when it costs the
page. A budget that scaled with the library would pass the thing this exists to catch.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import shutil
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sift.main  # noqa: F401 (every component registers itself)
from sift.kernel.access import Repository, Role, Viewer, visibility
from sift.kernel.access.constraints import NO_FILTER, AssetFilter, Where
from sift.kernel.access.repository.assets import (
    assets_query,
    drive_for,
    point_query,
    seek_anchor,
)
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key

ROOTS = 3
FOLDER_DEPTH = 4
FOLDERS_PER_LEVEL = 12
PEOPLE = 400
TAGS = 60
GUESTS = 3


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
    # order their files happen to sit. Walking the sort order and probing membership per row is
    # O(page) when their files are spread through it and O(library) when they are all old. So
    # this one is a per-member price; the number below is what a member costs here at 500k and
    # at 2M with headroom, and a plan that walks the library instead is fifty times over it.
    "one person's files": 10.0,
    "people wall": 400.0,
    "tags wall": 400.0,
    "hide one file (trigger)": 20.0,
    "tag one file (trigger)": 20.0,
    # Bounded by the copies under the folder, never by the library: every pair the share reaches
    # is re-decided and its rows written, which is the cost of an answer that is stored. Measured
    # at 36 us a file at 500k and 40 us at 2M, with the write lock held for the whole of it. Like
    # the person's page above this is a per-file price; this script's tree puts a thirty-sixth of
    # the library under the folder it shares.
    "share a folder (trigger)": 200.0,
}


def _sample(n: int) -> list[str]:
    return [new_id() for _ in range(n)]


async def _users(connection: Any, now: int) -> tuple[str, list[str]]:
    admin = new_id()
    guests = _sample(GUESTS)
    await connection.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at)"
        " VALUES (?, 'admin', 'x', 'admin', ?)",
        (admin, now),
    )
    for index, guest in enumerate(guests):
        await connection.execute(
            "INSERT INTO users (id, username, password_hash, role, created_at)"
            " VALUES (?, ?, 'x', 'guest', ?)",
            (guest, f"guest{index}", now),
        )
    return admin, guests


def _folder_tree(
    roots: list[str],
) -> tuple[list[tuple[str, str, str | None, str, str]], list[tuple[str, str]]]:
    # A tree per root: FOLDERS_PER_LEVEL children at each level, FOLDER_DEPTH deep.
    folders: list[tuple[str, str, str | None, str, str]] = []
    leaves: list[tuple[str, str]] = []
    for root in roots:
        level: list[tuple[str, str, str | None, str, str]] = [
            (new_id(), root, None, f"top{k}", f"top{k}") for k in range(FOLDERS_PER_LEVEL)
        ]
        folders.extend(level)
        for depth in range(1, FOLDER_DEPTH):
            nxt: list[tuple[str, str, str | None, str, str]] = []
            for parent_id, _root, _parent, rel, _name in level:
                for k in range(FOLDERS_PER_LEVEL if depth < 2 else 2):
                    fid = new_id()
                    nxt.append((fid, root, parent_id, f"{rel}/d{depth}_{k}", f"d{depth}_{k}"))
            folders.extend(nxt)
            level = nxt
        leaves.extend((fid, root) for fid, _r, _p, _rel, _n in level)
    return folders, leaves


@dataclass
class _Files:
    """The rows of every file the scale library holds."""

    assets: list[tuple[object, ...]] = field(default_factory=list)
    locations: list[tuple[object, ...]] = field(default_factory=list)
    people: list[tuple[object, ...]] = field(default_factory=list)
    tags: list[tuple[object, ...]] = field(default_factory=list)
    thumbs: list[tuple[object, ...]] = field(default_factory=list)
    first_asset: str | None = None


def _files(
    rng: random.Random,
    files: int,
    now: int,
    leaves: list[tuple[str, str]],
    people: list[str],
    tags: list[str],
) -> _Files:
    rows = _Files()
    for i in range(files):
        aid = new_id()
        if rows.first_asset is None:
            rows.first_asset = aid
        kind = "video" if i % 4 == 0 else "image"
        name = f"file_{rng.randrange(10**9):09d}.{'mp4' if kind == 'video' else 'jpg'}"
        rows.assets.append(
            (
                aid,
                f"b3_{i:012d}",
                kind,
                rng.randrange(400, 4000),
                rng.randrange(400, 4000),
                rng.randrange(1000, 3_600_000) if kind == "video" else None,
                rng.randrange(10_000, 5_000_000_000),
                name,
                now - rng.randrange(0, 90_000_000),
                sort_key(name),
            )
        )
        leaf, root = leaves[rng.randrange(len(leaves))]
        # The path carries the file's number, so two files that drew the same name in the
        # same folder are still two paths: a copy's path is unique within its root.
        rows.locations.append((new_id(), aid, root, leaf, f"{leaf}/{i}/{name}", name, now, now))
        if i % 50 == 0:
            other, other_root = leaves[rng.randrange(len(leaves))]
            rows.locations.append(
                (new_id(), aid, other_root, other, f"{other}/{i}b/{name}", name, now, now)
            )
        # A long tail of people: the first person is on 1 in 100 files, the rest sparse.
        if i % 100 == 0:
            rows.people.append((aid, people[0]))
        elif i % 7 == 0:
            rows.people.append((aid, people[rng.randrange(1, PEOPLE)]))
        if i % 5 == 0:
            rows.tags.append((aid, tags[rng.randrange(TAGS)]))
        rows.thumbs.append(
            (new_id(), aid, "thumb", f"{aid[-4:-2]}/{aid[-2:]}/{aid}/thumb.jpg", now)
        )
    return rows


async def _insert_files(connection: Any, rows: _Files) -> None:
    await connection.executemany(
        "INSERT INTO assets (id, identity, media_type, width, height, duration_ms, size_bytes,"
        " original_filename, added_at, filename_sort) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows.assets,
    )
    await connection.executemany(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows.locations,
    )
    await connection.executemany(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", rows.people
    )
    await connection.executemany(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", rows.tags
    )
    await connection.executemany(
        "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        rows.thumbs,
    )


async def build(database: Database, files: int, seed: int) -> dict[str, str]:
    """Fill the database. Returns the ids the timings need."""
    # Reproducible rather than secret: the same seed builds the same library on every run.
    rng = random.Random(seed)  # noqa: S311
    now = 1_700_000_000
    async with database.write() as connection:
        # Without the triggers: a bulk build re-decides once at the end, as the backfill does.
        await visibility._drop_triggers(connection)
        admin, guests = await _users(connection, now)
        roots = _sample(ROOTS)
        await connection.executemany(
            "INSERT INTO library_roots (id, name, abs_path, kind, created_at)"
            " VALUES (?, ?, ?, 'local', ?)",
            [(root, f"root{i}", f"/probe/root{i}", now) for i, root in enumerate(roots)],
        )
        folders, leaves = _folder_tree(roots)
        # Parents before children: the ancestry rows are rebuilt afterwards, but the foreign key
        # on the parent is checked as each row lands.
        await connection.executemany(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            folders,
        )
        people = _sample(PEOPLE)
        await connection.executemany(
            "INSERT INTO people (id, name, created_at, name_sort) VALUES (?, ?, ?, ?)",
            [(pid, f"Person {i}", now, sort_key(f"Person {i}")) for i, pid in enumerate(people)],
        )
        tags = _sample(TAGS)
        await connection.executemany(
            "INSERT INTO tags (id, name, created_at, name_sort) VALUES (?, ?, ?, ?)",
            [(tid, f"tag{i}", now, sort_key(f"tag{i}")) for i, tid in enumerate(tags)],
        )
        rows = _files(rng, files, now, leaves, people, tags)
        await _insert_files(connection, rows)
        # Grants: one guest sees a whole root, one sees one top folder, one sees one tag.
        await connection.executemany(
            "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect,"
            " created_at) VALUES (?, ?, ?, ?, 'share', ?)",
            [
                (new_id(), "root", roots[0], guests[0], now),
                (new_id(), "folder", folders[0][0], guests[1], now),
                (new_id(), "tag", tags[0], guests[2], now),
            ],
        )
        await connection.execute(
            "INSERT INTO person_user_state (person_id, user_id, hidden, hidden_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (people[1], admin, now, now),
        )
        started = time.perf_counter()
        await visibility.refresh_everything(connection)
        backfill = time.perf_counter() - started
    await database.fetch_all("SELECT 1")
    return {
        "admin": admin,
        "guest_root": guests[0],
        "guest_folder": guests[1],
        "guest_tag": guests[2],
        "person": people[0],
        "tag": tags[0],
        "folder": folders[1][0],
        "first_asset": rows.first_asset or "",
        "backfill_s": f"{backfill:.1f}",
    }


def _row[T](found: T | None, what: str) -> T:
    """A row that has to be there, or this script is asking about a library it did not build."""
    if found is None:
        raise RuntimeError(f"the probe library has no {what}")
    return found


def _params(viewer: str, **more: object) -> dict[str, object]:
    _where, bound = NO_FILTER.predicate()
    base: dict[str, object] = {
        "viewer": viewer,
        "asset_id": None,
        "reveal": 0,
        "limit": 50,
        "offset": 0,
        "tag_id": None,
        "collection_id": None,
        "photo_set_id": None,
        "hidden_only": 0,
        "pinned_first": 0,
        "reveal_named": 0,
        "is_admin": 0,
        "prefix": "",
        "like": "%",
        "person_id": None,
        "person_ids": None,
        "site_id": None,
        "list_empty": 0,
        "entity_sort": "name",
        **bound,
    }
    base.update(more)
    return base


async def timed(database: Database, sql: str, params: dict[str, object], *, runs: int = 5) -> float:
    return await timed_read(lambda: database.fetch_all(sql, params), runs=runs)


async def timed_read(read: Callable[[], Awaitable[object]], *, runs: int = 5) -> float:
    """The best of `runs` goes at one read, in milliseconds."""
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        await read()
        best = min(best, time.perf_counter() - started)
    return best * 1000


async def measure(
    database: Database, access: Repository, ids: dict[str, str], files: int
) -> dict[str, float]:
    where, _bound = NO_FILTER.predicate()
    stats = await database.fetch_one(
        "SELECT permitted, (SELECT MAX(rowid) FROM assets) AS library FROM viewer_stats"
        " WHERE user_id = ?",
        (ids["admin"],),
    )
    stats = _row(stats, "stats")
    drive = drive_for(int(stats["permitted"]), int(stats["library"]))
    found: dict[str, float] = {}
    page = assets_query("newest", where, arranged=False, counted=False, drive=drive)
    found["admin page, newest"] = await timed(database, page, _params(ids["admin"]))
    by_name = assets_query("name_az", where, arranged=False, counted=False, drive=drive)
    found["admin page, by name"] = await timed(database, by_name, _params(ids["admin"]))
    # Continue from the row half way down the library: what a deep offset would cost.
    middle = await database.fetch_one(
        "SELECT id FROM assets ORDER BY added_at DESC, id DESC LIMIT 1 OFFSET ?", (files // 2,)
    )
    middle = _row(middle, "middle")
    anchor = await database.fetch_one(seek_anchor("newest"), (middle["id"],))
    anchor = _row(anchor, "anchor")
    continued = assets_query(
        "newest", where, arranged=False, counted=False, drive=drive, continued=True
    )
    found["admin page continued (keyset)"] = await timed(
        database, continued, _params(ids["admin"], after_key=anchor["key"], after_id=middle["id"])
    )
    found["admin count, un-narrowed"] = await timed(
        database,
        "SELECT permitted, concealed FROM viewer_stats WHERE user_id = ?",
        (ids["admin"],),  # type: ignore[arg-type]
    )
    guest_stats = await database.fetch_one(
        "SELECT permitted FROM viewer_stats WHERE user_id = ?", (ids["guest_folder"],)
    )
    guest_stats = _row(guest_stats, "guest_stats")
    guest_drive = drive_for(int(guest_stats["permitted"]), int(stats["library"]))
    guest_page = assets_query("newest", where, arranged=False, counted=False, drive=guest_drive)
    found["guest page"] = await timed(database, guest_page, _params(ids["guest_folder"]))
    found["point check (thumbnail)"] = await timed(
        database, point_query(where), _params(ids["admin"], asset_id=ids["first_asset"])
    )
    person_where, person_bound = AssetFilter(where=Where("people", (ids["person"],))).predicate()
    person_page = assets_query("newest", person_where, arranged=False, counted=False, drive=drive)
    found["one person's files"] = await timed(
        database, person_page, _params(ids["admin"], **person_bound)
    )
    # The walls through the repository's own reads, which bind their statements in one place: a
    # statement bound here by hand stops running the day the wall binds a new parameter.
    admin = Viewer(id=ids["admin"], role=Role.ADMIN)
    found["people wall"] = await timed_read(lambda: access.suggest_people(admin, limit=50))
    found["tags wall"] = await timed_read(lambda: access.list_tags(admin, limit=50))
    # The write side: what one change costs the triggers.
    for name, sql, params in (
        (
            "hide one file (trigger)",
            "INSERT INTO asset_user_state (asset_id, user_id, hidden, updated_at)"
            " VALUES (?, ?, 1, 0)",
            (ids["first_asset"], ids["admin"]),
        ),
        (
            "tag one file (trigger)",
            "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
            (ids["first_asset"], ids["tag"]),
        ),
        (
            "share a folder (trigger)",
            "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect,"
            " created_at) VALUES (?, 'folder', ?, ?, 'share', 0)",
            (new_id(), ids["folder"], ids["guest_tag"]),
        ),
    ):
        started = time.perf_counter()
        await database.execute(sql, params)
        found[name] = (time.perf_counter() - started) * 1000
    return found


async def run(files: int, keep: Path | None, seed: int) -> int:
    directory = keep if keep is not None else Path(tempfile.mkdtemp(prefix="sift-scale-"))
    directory.mkdir(parents=True, exist_ok=True)
    database = Database(directory / "probe.sqlite3", readers=2)
    await database.connect()
    try:
        await database.initialize_schema()
        started = time.perf_counter()
        ids = await build(database, files, seed)
        print(
            f"built {files:,} files in {time.perf_counter() - started:.1f}s"
            f" (backfill {ids['backfill_s']}s)"
        )
        settings = Settings(data_dir=directory / "data", cache_dir=directory / "cache")
        access = Repository(database, ContentStore(database, settings))
        found = await measure(database, access, ids, files)
    finally:
        await database.close()
        if keep is None:
            shutil.rmtree(directory, ignore_errors=True)
    over = 0
    per_member = {
        "one person's files": PER_MEMBER_MS * (files // 100),
        "share a folder (trigger)": PER_SHARED_FILE_MS * (files // (ROOTS * FOLDERS_PER_LEVEL)),
    }
    for name, limit in BUDGETS.items():
        limit += per_member.get(name, 0.0)
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
