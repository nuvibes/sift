# SPDX-License-Identifier: AGPL-3.0-or-later
"""The passes' counts kept as rows say what a walk of the library says, after every kind of write,
and a read after a write evaluates the files that moved, not the library."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    DerivativeKind,
    Lack,
    VerdictProduct,
    backlog,
    lacks_derivative,
    lacks_fingerprint,
    wanted_outside,
)
from sift.kernel.content.backlog import Term, Totals
from sift.kernel.content.identity import RECIPE_VERSIONS
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import LibraryRoot

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000


class _Walks:
    """Kept counts that are never there, so every count walks the library as it always did."""

    async def totals(self, terms: list[Term]) -> list[Totals] | None:
        return None


async def _file(
    database: Database, root: LibraryRoot, media_type: str = "video", *, read: bool = True
) -> str:
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, duration_ms, added_at,"
        " probed_at) VALUES (?, ?, 1, ?, 5000, ?, ?)",
        (asset_id, f"digest-{asset_id}", media_type, _EPOCH, _EPOCH if read else None),
    )
    await database.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, 'present', ?, ?)",
        (new_id(), asset_id, root.id, f"{asset_id}.bin", f"{asset_id}.bin", _EPOCH, _EPOCH),
    )
    return asset_id


async def _picture(database: Database, asset_id: str, kind: DerivativeKind) -> None:
    await database.execute(
        "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, created_at, recipe_version)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), asset_id, kind.value, f"{kind.value}/{asset_id}", _EPOCH, RECIPE_VERSIONS[kind]),
    )


def _terms(root: LibraryRoot) -> list[Lack]:
    thumbs = lacks_derivative([DerivativeKind.THUMB])
    previews = lacks_derivative([DerivativeKind.PREVIEW])
    assert thumbs is not None and previews is not None
    return [
        replace(thumbs, product=VerdictProduct.THUMBNAILS.value),
        replace(previews, product=VerdictProduct.PREVIEWS.value),
        lacks_fingerprint(),
    ]


async def _every_count(store: ContentStore, root: LibraryRoot) -> list[object]:
    terms = _terms(root)
    refusing = wanted_outside([[root.id]])
    return [
        await store.count_lacking(terms),
        await store.count_lacking(terms, ticked=[True, False, True]),
        await store.count_lacking(terms, roots=[root.id]),
        await store.count_lacking_by_kind(terms),
        await store.wanting_count(VerdictProduct.PREVIEWS.value),
        await store.wanting_count(VerdictProduct.THUMBNAILS.value, refusing),
        await store.asset_count(),
        await store.asset_count(refusing),
    ]


@pytest.fixture
def walker(temp_db: Database, settings: Settings, content_store: ContentStore) -> ContentStore:
    walks = ContentStore(temp_db, settings)
    walks._kept_counts = _Walks()  # type: ignore[assignment]
    return walks


async def _agree(kept: ContentStore, walks: ContentStore, root: LibraryRoot) -> None:
    """The kept counts, once any term new to them is built, against the walk."""
    await _every_count(kept, root)
    await kept._kept.settled()
    assert await _every_count(kept, root) == await _every_count(walks, root)


async def test_every_kind_of_write_leaves_the_kept_counts_as_the_walk_counts(
    temp_db: Database, content_store: ContentStore, walker: ContentStore, library_root: LibraryRoot
) -> None:
    first = await _file(temp_db, library_root)
    photo = await _file(temp_db, library_root, "image")
    await _file(temp_db, library_root, read=False)
    await _agree(content_store, walker, library_root)
    assert await temp_db.fetch_all("SELECT bit FROM backlog_terms"), "nothing was kept"

    writes: list[tuple[str, Callable[[], Awaitable[object]]]] = [
        # A product landing, and going.
        ("INSERT", lambda: _picture(temp_db, first, DerivativeKind.THUMB)),
        ("DELETE", lambda: temp_db.execute("DELETE FROM derivatives WHERE asset_id = ?", (first,))),
        # A pass giving up on a file, and the verdict cleared.
        (
            "verdict",
            lambda: temp_db.execute(
                "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
                " VALUES (?, 'previews', 'unreadable', 'test', 0, ?)",
                (first, _EPOCH),
            ),
        ),
        (
            "cleared",
            lambda: temp_db.execute("DELETE FROM file_verdicts WHERE asset_id = ?", (first,)),
        ),
        # Its only copy going missing, and coming back.
        (
            "missing",
            lambda: temp_db.execute(
                "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (first,)
            ),
        ),
        (
            "back",
            lambda: temp_db.execute(
                "UPDATE asset_locations SET status = 'present' WHERE asset_id = ?", (first,)
            ),
        ),
        # Read, fingerprinted, its kind read again.
        (
            "read",
            lambda: temp_db.execute("UPDATE assets SET probed_at = 1 WHERE probed_at IS NULL"),
        ),
        (
            "fingerprinted",
            lambda: temp_db.execute(
                "UPDATE assets SET phash = 'p', fingerprint_version = 99 WHERE id = ?", (photo,)
            ),
        ),
        (
            "kind",
            lambda: temp_db.execute("UPDATE assets SET media_type = 'gif' WHERE id = ?", (photo,)),
        ),
        # A file arriving and a file leaving.
        ("arrives", lambda: _file(temp_db, library_root, "image")),
        ("leaves", lambda: temp_db.execute("DELETE FROM assets WHERE id = ?", (photo,))),
    ]
    for what, write in writes:
        await write()
        await _agree(content_store, walker, library_root)
        assert not await temp_db.fetch_all("SELECT asset_id FROM backlog_moved"), what


async def test_a_read_after_a_write_evaluates_only_the_file_that_moved(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    files = [await _file(temp_db, library_root) for _ in range(5)]
    terms = _terms(library_root)
    await content_store.count_lacking(terms)
    await content_store._kept.settled()
    await content_store.count_lacking(terms)
    await _picture(temp_db, files[0], DerivativeKind.THUMB)

    marked = await temp_db.fetch_all("SELECT asset_id FROM backlog_moved")
    assert [row["asset_id"] for row in marked] == [files[0]]
    counted = await content_store.count_lacking(terms)
    assert counted.each[0] == 4


async def test_a_count_that_disagrees_with_the_walk_is_built_again(
    temp_db: Database, content_store: ContentStore, walker: ContentStore, library_root: LibraryRoot
) -> None:
    await _file(temp_db, library_root)
    await _agree(content_store, walker, library_root)
    await temp_db.execute("UPDATE backlog_counts SET n = n + 5")
    await temp_db.execute("UPDATE backlog_terms SET checked_at = 0")

    terms = await temp_db.fetch_all("SELECT bit FROM backlog_terms")
    for _ in range(len(terms) + 1):
        await _agree(content_store, walker, library_root)
    walked = await temp_db.fetch_all(
        "SELECT bit FROM backlog_terms WHERE checked_at = 0 AND building = 0"
    )
    assert not walked, "a term was never checked"


async def test_a_build_a_stopped_process_left_half_done_is_started_again(
    temp_db: Database, content_store: ContentStore, walker: ContentStore, library_root: LibraryRoot
) -> None:
    await _file(temp_db, library_root)
    await _agree(content_store, walker, library_root)
    await temp_db.execute("UPDATE backlog_terms SET building = 1")
    await temp_db.execute("UPDATE backlog_counts SET n = 0")

    await _agree(content_store, walker, library_root)


async def test_the_least_recently_asked_term_gives_its_bit_up(
    temp_db: Database, content_store: ContentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backlog, "TERMS_KEPT", 3)
    kept = content_store._kept
    for n in range(5):
        term = Term(f"{n} = {n}")
        assert await kept.totals([term]) is None
        await kept.settled()
        assert await kept.totals([term]) == [{}]
    assert len(await temp_db.fetch_all("SELECT bit FROM backlog_terms")) == 3


async def test_a_term_another_reader_is_building_is_walked(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    await _file(temp_db, library_root)
    kept = content_store._kept
    term = Term("1")
    await kept.totals([term])
    await kept.settled()
    assert await kept.totals([term]) == [{"video": 1}]
    kept._building.add(term.signature)
    assert await kept.totals([term]) is None


async def test_a_library_at_content_version_30_gets_the_kept_counts(temp_db: Database) -> None:
    from sift.kernel.content import schema

    async with temp_db.write() as connection:
        await schema.initialize_library(connection, on_disk=0)
        await schema.initialize_content(connection, on_disk=0)
        for statement in (
            "DROP TABLE backlog_clock",
            "DROP TABLE backlog_moved",
            "DROP TABLE backlog_terms",
            "DROP TABLE backlog_files",
            "DROP TRIGGER backlog_assets_added",
        ):
            await connection.execute(statement)
        await schema.initialize_content(connection, on_disk=30)
        tables = {
            str(row[0])
            for row in await connection.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE name LIKE 'backlog%'"
            )
        }
    assert {"backlog_clock", "backlog_terms", "backlog_assets_added"} <= tables


async def test_a_file_written_twice_before_a_read_under_an_upsert_is_marked_once(
    temp_db: Database, content_store: ContentStore, walker: ContentStore, library_root: LibraryRoot
) -> None:
    asset_id = await _file(temp_db, library_root)
    await _agree(content_store, walker, library_root)
    for code in ("unreadable", "too_short"):
        await temp_db.execute(
            "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
            " VALUES (?, 'previews', ?, 'test', 0, 1) ON CONFLICT(asset_id, product)"
            " DO UPDATE SET code = excluded.code",
            (asset_id, code),
        )
    await temp_db.execute(
        "INSERT OR IGNORE INTO derivatives (id, asset_id, kind, rel_cache_path, created_at)"
        " VALUES (?, ?, 'thumb', 'thumb/x', 1)",
        (new_id(), asset_id),
    )
    assert len(await temp_db.fetch_all("SELECT asset_id FROM backlog_moved")) == 1
    await _agree(content_store, walker, library_root)


async def test_a_build_that_fails_is_started_again_by_the_next_asker(
    temp_db: Database,
    content_store: ContentStore,
    walker: ContentStore,
    library_root: LibraryRoot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _file(temp_db, library_root)
    applied = backlog._apply

    async def broken(*args: object) -> None:
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(backlog, "_apply", broken)
    term = Term("1")
    assert await content_store._kept.totals([term]) is None
    await content_store._kept.settled()
    monkeypatch.setattr(backlog, "_apply", applied)
    assert await content_store._kept.totals([term]) is None, "started again, walked meanwhile"
    await content_store._kept.settled()
    assert await content_store._kept.totals([term]) == [{"video": 1}]


async def test_more_terms_than_are_kept_are_walked(
    temp_db: Database,
    content_store: ContentStore,
    walker: ContentStore,
    library_root: LibraryRoot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backlog, "TERMS_KEPT", 2)
    await _file(temp_db, library_root)
    terms = _terms(library_root)
    assert await content_store._kept.totals([Term("1"), Term("2"), Term("3")]) is None
    assert await content_store.count_lacking(terms) == await walker.count_lacking(terms)


async def test_a_term_given_up_meanwhile_is_not_folded(
    temp_db: Database, content_store: ContentStore
) -> None:
    async with temp_db.write() as connection:
        assert not await content_store._kept._fold(connection, [Term("1")])


async def test_a_build_over_several_pages_counts_every_file(
    temp_db: Database,
    content_store: ContentStore,
    walker: ContentStore,
    library_root: LibraryRoot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backlog, "_BUILD_AT_ONCE", 2)
    for _ in range(5):
        await _file(temp_db, library_root)
    await _agree(content_store, walker, library_root)
    assert await content_store.asset_count() == 5


async def test_a_term_given_up_to_another_reader_before_its_fold_is_walked(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    await _file(temp_db, library_root)
    kept = content_store._kept
    term = Term("1")
    await kept.totals([term])
    await kept.settled()
    ensured = kept._ensure

    async def taken_meanwhile(terms: Sequence[Term]) -> dict[str, int] | None:
        bits = await ensured(terms)
        async with temp_db.write() as connection:
            await backlog._forget(connection, bits[term.signature] if bits else 0)
        return bits

    kept._ensure = taken_meanwhile  # type: ignore[method-assign]
    assert await kept.totals([term]) is None


async def test_a_term_still_asked_keeps_its_bit_when_an_older_one_gives_way(
    temp_db: Database, content_store: ContentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backlog, "TERMS_KEPT", 2)
    kept = content_store._kept
    first, second, third = Term("1 = 1"), Term("2 = 2"), Term("3 = 3")
    for term in (first, second):
        await kept.totals([term])
        await kept.settled()
    await kept.totals([first, third])
    await kept.settled()
    assert await kept.totals([first, third]) == [{}, {}]
    signatures = {
        str(row[0]) for row in await temp_db.fetch_all("SELECT signature FROM backlog_terms")
    }
    assert signatures == {first.signature, third.signature}
