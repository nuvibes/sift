# SPDX-License-Identifier: AGPL-3.0-or-later
"""A library in the shape the last release left it comes forward in one boot, every component at once.

Each component's own migration tests build ITS old shape alone, so a step that writes into another
component's table is only ever run against that table at today's shape. A step that records a
History line can then run before the ledger's table has the columns the ledger's door writes, and
an update stops at boot while every per-component test is green.

`data/library-<version>.sql.gz` is a new library made by the released code itself and dumped whole:
every table, index and trigger as that release creates them, every component at its released
version. Its SQL comments are taken out (they are text nothing reads, and the tree carries no
double-hyphen); nothing else is changed. It is kept gzipped, under the size a commit may add: a
release's triggers can be megabytes of text that packs to a fraction. Each release writes its own
and deletes the one before (`scripts/release.py`, `write_upgrade_fixture`), and this reads the
newest by its version, so the folder's one file is always the release an update starts from.

What it does not cover: a library carried through older releases, whose tables were ALTERed rather
than created (a column added at the end, a constraint written later). The seeded rows are the few
each one-time step of this build needs to have something to do; a step whose input is not seeded
here runs over nothing.
"""

from __future__ import annotations

import gzip
import json
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
import time
from pathlib import Path
from typing import Any

import pytest

import sift.main  # noqa: F401 (imported for its side effect: every component registers its schema)
from sift.kernel.access import visibility
from sift.kernel.content.songs import ARTISTS_JOINED_BY
from sift.kernel.db import Database, _ordered_components, registered_components
from sift.slices.stash_boxes.taken_back import RECEIPTS, left_by_a_refusal

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

DATA = Path(__file__).parent / "data"


def _version_of(fixture: Path) -> tuple[int, ...]:
    """A fixture's version as numbers, so 0.1.9 is older than 0.1.10 as it is meant."""
    version = fixture.name.removeprefix("library-").removesuffix(".sql.gz")
    return tuple(int(one) for one in version.split("-")[0].split("."))


def newest_release(folder: Path) -> Path:
    """The last release's library: the fixture with the highest version in `folder`."""
    return max(folder.glob("library-*.sql.gz"), key=_version_of)


RELEASED = newest_release(DATA)

#: The rows every step below reads: a box, a file it filed a person and a username on, and a song
#: on a second file.
_SEEDS: tuple[tuple[str, tuple[object, ...]], ...] = (
    (
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)",
        ("box", "FansDB", "https://box.example/graphql"),
    ),
    (
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        ("f", "f"),
    ),
    (
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        ("g", "g"),
    ),
    (
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        ("s", "Storefront", "s"),
    ),
    (
        "INSERT INTO people (id, name, name_sort, created_at, created_by_kind, created_by_box_id)"
        " VALUES (?, ?, ?, 0, 'box', 'box')",
        ("p", "Wren Halloway", "wren halloway"),
    ),
    (
        "INSERT INTO usernames (id, site_id, name, name_sort, person_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, 0)",
        ("u", "s", "wrenclips", "wrenclips", "p"),
    ),
    (
        "INSERT INTO asset_people (asset_id, person_id, source) VALUES (?, ?, 'stash_box')",
        ("f", "p"),
    ),
    (
        "INSERT INTO asset_usernames (asset_id, username_id, source) VALUES (?, ?, 'stash_box')",
        ("f", "u"),
    ),
    (
        "INSERT INTO songs (id, name, name_sort, created_at, recording_id) VALUES (?, ?, ?, 0, ?)",
        ("song", "Harbour Lights", "harbour lights", "rec-1"),
    ),
    ("INSERT INTO song_files (song_id, asset_id) VALUES (?, ?)", ("song", "g")),
)

_ANSWER = json.dumps([{"fields": {"people": ["Wren Halloway"]}}])

#: What each one-time step this test can feed needs, by the component and the version that brings
#: it. A step the released library already took is neither fed nor asserted (that release ran it),
#: so a new release's fixture never turns this red.
_STEPS: dict[tuple[str, int], tuple[tuple[str, tuple[object, ...]], ...]] = {
    # A stash-box answer refused with its filings left behind.
    ("stash_boxes", 19): (
        (
            "INSERT INTO asset_stash_box_matches"
            " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
            " VALUES (?, ?, 'scene', ?, 'unsure', 'refused', 0, 1)",
            ("f", "box", _ANSWER),
        ),
    ),
    # An applied answer a box asked again turned back into a question, its filings left behind:
    # the answer waits, and the box has found the file twice.
    ("stash_boxes", 20): (
        (
            "INSERT INTO asset_stash_box_matches"
            " (asset_id, box_id, remote_id, payload, grade, state, found_at)"
            " VALUES (?, ?, 'scene', ?, 'unsure', 'waiting', 0)",
            ("f", "box", _ANSWER),
        ),
        (
            "INSERT INTO stash_box_scans (id, asset_id, box_id, scanned_at, found)"
            " VALUES (?, ?, ?, ?, 1)",
            ("scan-1", "f", "box", 0),
        ),
        (
            "INSERT INTO stash_box_scans (id, asset_id, box_id, scanned_at, found)"
            " VALUES (?, ?, ?, ?, 1)",
            ("scan-2", "f", "box", 1),
        ),
    ),
    # An AcoustID answer naming artists for a song of that recording.
    ("music", 5): (
        (
            "INSERT INTO music_lookups"
            " (asset_id, looked_up_at, lengths_sent, status, recording_id, title, artists, score)"
            " VALUES (?, 0, '[]', 'named', ?, ?, ?, 0.9)",
            (
                "g",
                "rec-1",
                "Harbour Lights",
                ARTISTS_JOINED_BY.join(["Example Band", "Low Tide"]),
            ),
        ),
    ),
    # Rows whose parent went while foreign keys were off, beside rows that must stay as they are.
    ("catalog", 90): (
        ("INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", ("gone", "u")),
        (
            "INSERT INTO collections (id, name, owner_id, created_at) VALUES (?, ?, ?, 0)",
            ("c-gone", "Gone", "nobody"),
        ),
        ("INSERT INTO collections (id, name, created_at) VALUES (?, ?, 0)", ("c-shared", "Shared")),
        (
            "INSERT INTO tags (id, name, cover_asset_id, parent_id, created_at)"
            " VALUES (?, ?, ?, ?, 0)",
            ("t-left", "Harbour", "gone", "t-gone"),
        ),
        (
            "INSERT INTO tags (id, name, cover_asset_id, parent_id, created_at)"
            " VALUES (?, ?, ?, ?, 0)",
            ("t-kept", "Lights", "f", "t-left"),
        ),
    ),
}


def _the_last_release(path: Path) -> set[tuple[str, int]]:
    """The released library, made from its dump, with the seeds written into it. Answers which of
    `_STEPS` it was fed for: the ones the release had not taken. Of two steps that read one row
    (the stash-box answer), the earliest is fed, as the boot then runs it first."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(gzip.decompress(RELEASED.read_bytes()).decode("utf-8"))
        released = dict(connection.execute("SELECT component, version FROM schema_version"))
        fed: set[tuple[str, int]] = set()
        for component, version in sorted(_STEPS):
            if released.get(component, 0) >= version or any(c == component for c, _ in fed):
                continue
            fed.add((component, version))
        for statement, values in _SEEDS:
            connection.execute(statement, values)
        for step in fed:
            for statement, values in _STEPS[step]:
                connection.execute(statement, values)
        connection.commit()
    finally:
        connection.close()
    return fed


async def test_a_library_from_the_last_release_comes_forward_in_one_boot(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    fed = {component for component, _version in _the_last_release(path)}
    database = Database(path)
    await database.connect()
    try:
        await database.initialize_schema()

        recorded = {
            str(row["component"]): int(row["version"])
            for row in await database.fetch_all("SELECT component, version FROM schema_version")
        }
        expected = {name: one.version for name, one in registered_components().items()}
        assert {name: recorded.get(name) for name in expected} == expected
        # The triggers come forward in this build's shape, whatever shape the release wrote them in:
        # every connection parses their text before its first statement.
        written = await database.fetch_all(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'vis_%'"
        )
        assert {str(row["name"]): str(row["sql"]) for row in written} == {
            name: ddl.replace("CREATE TRIGGER IF NOT EXISTS", "CREATE TRIGGER", 1)
            for name, _table, ddl in visibility.triggers()
        }
        if "stash_boxes" in fed:
            # The stash-box repair ran, and said so on the ledger in this build's shape.
            assert await left_by_a_refusal(database) == 0
            line = await database.fetch_one(
                "SELECT payload, client_kind FROM workbench_decisions WHERE queue = ?",
                (RECEIPTS,),
            )
            assert line is not None and json.loads(str(line["payload"]))["files"] == 1
        if "music" in fed:
            # The artists AcoustID named are credited on the song of that recording.
            credited = await database.fetch_all(
                "SELECT a.name AS name FROM song_artists c JOIN artists a ON a.id = c.artist_id"
                " WHERE c.song_id = 'song' ORDER BY c.position"
            )
            assert [str(one["name"]) for one in credited] == ["Example Band", "Low Tide"]
        assert await database.fetch_all("SELECT * FROM pragma_foreign_key_check") == []
        if "catalog" in fed:
            filed = await database.fetch_all("SELECT asset_id, username_id FROM asset_usernames")
            assert [tuple(one) for one in filed] == [("f", "u")]
            kept = await database.fetch_all("SELECT id FROM collections ORDER BY id")
            assert [str(one["id"]) for one in kept] == ["c-shared"]
            tags = await database.fetch_all(
                "SELECT id, cover_asset_id, parent_id FROM tags WHERE id LIKE 't-%' ORDER BY id"
            )
            assert [tuple(one) for one in tags] == [
                ("t-kept", "f", "t-left"),
                ("t-left", None, None),
            ]
    finally:
        await database.close()


def test_the_ledgers_owner_comes_up_before_every_component_not_beneath_it() -> None:
    """The rule the boot above depends on, said of the order itself: the component that leads (the
    owner of the table the ledger's door writes) comes before everything it does not depend on."""
    ordered = [one.name for one in _ordered_components()]
    leader = next(one for one in registered_components().values() if one.leads)
    beneath: set[str] = set()
    waiting = list(leader.depends_on)
    while waiting:
        name = waiting.pop()
        if name not in beneath:
            beneath.add(name)
            waiting.extend(registered_components()[name].depends_on)
    at = ordered.index(leader.name)
    assert set(ordered[:at]) == beneath


async def test_bringing_the_last_release_forward_reads_the_versions_without_a_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every step's DDL moves the schema cookie, and a reader asked anything next parses the whole
    schema again first: this library's trigger text, once per component. The versions are read on
    the writer, which made the change, so what the bring-up spends reading them stays under a bound
    a single parse of this schema is over."""
    path = tmp_path / "library.sqlite3"
    _the_last_release(path)
    database = Database(path)
    await database.connect()
    spent: list[float] = []
    fetch_one, fetch_all = database.fetch_one, database.fetch_all

    async def timed(read: Any, statement: Any, params: Any = ()) -> Any:
        started = time.perf_counter()
        try:
            return await read(statement, params)
        finally:
            if "schema_version" in str(getattr(statement, "sql", statement)):
                spent.append(time.perf_counter() - started)

    monkeypatch.setattr(database, "fetch_one", lambda s, p=(): timed(fetch_one, s, p))
    monkeypatch.setattr(database, "fetch_all", lambda s, p=(): timed(fetch_all, s, p))
    try:
        await database.initialize_schema()
    finally:
        await database.close()

    assert sum(spent) < 0.05, f"{len(spent)} reads of the versions took {sum(spent):.3f} s"
