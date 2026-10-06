# SPDX-License-Identifier: AGPL-3.0-or-later
"""The work list, and the one property that makes a sweep finish.

**Rows leave the work list as they are described.** So walking it by counting past what has been
done skips files: the offset that pointed at the next unfinished one points past several by the
time it is used, and those are never offered again. The cursor here only ever moves forward
through ids, which cannot skip anything, and a file that fails is stepped over rather than retried
for ever.

The other property proved here is that a file described by an older model is **waiting, not done**.
Numbers from two models are not comparable and nothing about them says so, so a model change has to
put every file back on the list.
"""

from __future__ import annotations

import pytest

from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.slices.semantic.records import Records

pytestmark = pytest.mark.integration

OLD = "siglip2-old"
NEW = "siglip2-new"


@pytest.fixture
async def records(temp_db: Database) -> Records:
    await temp_db.initialize_schema()
    return Records(temp_db)


async def seed(database: Database, *ids: str) -> None:
    """Files in the library, each with a copy that is there to read.

    The copy is part of the file here, not decoration: the library's count of work still to do
    leaves out a file whose every copy is missing (`kernel.content.presence`), so a file with no
    copy at all is one the count and the page could never agree about.
    """
    await database.execute(
        "INSERT OR IGNORE INTO library_roots (id, name, abs_path, created_at) "
        "VALUES ('root', 'Pictures', '/library/pictures', 1700000000)"
    )
    for asset_id in ids:
        await database.execute(
            "INSERT INTO assets (id, identity, media_type, size_bytes, added_at) "
            "VALUES (?, ?, 'image', 1, 1700000000)",
            (asset_id, f"digest-{asset_id}"),
        )
        await database.execute(
            "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename, "
            "first_seen_at, last_seen_at) VALUES (?, ?, 'root', NULL, ?, ?, 1700000000, 1700000000)",
            (f"loc-{asset_id}", asset_id, f"{asset_id}.jpg", f"{asset_id}.jpg"),
        )


async def test_a_file_nothing_has_described_is_not_settled(
    temp_db: Database, records: Records
) -> None:
    await seed(temp_db, "a", "b")

    assert await records.settled_ids(NEW) == set()


async def test_a_described_file_is_settled(temp_db: Database, records: Records) -> None:
    await seed(temp_db, "a", "b")

    await records.mark("a", revision=NEW, frames=3, at_ms=1)

    assert await records.settled_ids(NEW) == {"a"}


async def test_which_of_these_files_the_model_has_not_described(
    temp_db: Database, records: Records, content_store: ContentStore
) -> None:
    """The page-at-a-time form the Build asks: the same rule as `settled_ids`, over the ids given
    and no others, so a page of a thousand is one short query rather than the whole table.

    And the same rule as the term the Build's sheet counts the library by, asked the same
    question, so the two cannot drift apart unnoticed."""
    await seed(temp_db, "a", "b", "c")
    await temp_db.execute("UPDATE assets SET probed_at = 1")
    await records.mark("a", revision=NEW, frames=3, at_ms=1)
    await records.mark("b", revision=OLD, frames=3, at_ms=1)

    assert await records.unsettled_among(["a", "b", "c"], NEW) == {"b", "c"}
    assert await records.unsettled_among(["a"], NEW) == set()
    assert await records.unsettled_among([], NEW) == set()

    for revision in (NEW, OLD):
        page = await records.unsettled_among(["a", "b", "c"], revision)
        counted = await content_store.count_lacking([records.lack(revision)])
        assert counted.each == (len(page),), revision


async def test_a_file_described_by_an_older_model_is_not_settled(
    temp_db: Database, records: Records
) -> None:
    """Numbers from two models are not comparable, and mixing them does not fail: it returns
    wrong neighbours. So a model change is not a preference, it is a re-index."""
    await seed(temp_db, "a")
    await records.mark("a", revision=OLD, frames=3, at_ms=1)

    assert await records.settled_ids(NEW) == set()
    assert await records.settled_ids(OLD) == {"a"}


async def test_how_many_files_another_model_described(temp_db: Database, records: Records) -> None:
    """The number the settings screen says results are partial with."""
    await seed(temp_db, "a", "b", "c")
    await records.mark("a", revision=NEW, frames=3, at_ms=1)
    await records.mark("b", revision=OLD, frames=3, at_ms=1)

    assert await records.described_by_others(NEW) == 1
    assert await records.described_by_others(OLD) == 1
    assert await records.described_by_others("siglip2-newest") == 2
    assert await records.any_described_by_others(NEW)
    assert await records.any_described_by_others("siglip2-a")
    await records.mark("b", revision=NEW, frames=3, at_ms=1)
    assert not await records.any_described_by_others(NEW)
    assert await records.described_by_others(NEW) == 0


async def test_asking_about_another_models_files_seeks_rather_than_reads_them(
    temp_db: Database, records: Records
) -> None:
    """Asked by every readiness check: `!=` walks every file ever described."""
    from sift.slices.semantic import records as module

    for statement in (module._COUNT_BY_OTHERS, module._ANY_BY_OTHERS):
        plan = await temp_db.fetch_all(
            "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
            (NEW, NEW),
        )
        assert not [row["detail"] for row in plan if str(row["detail"]).startswith("SCAN sem")]


async def test_describing_a_file_twice_replaces_the_record(
    temp_db: Database, records: Records
) -> None:
    await seed(temp_db, "a")
    await records.mark("a", revision=OLD, frames=3, at_ms=1)

    await records.mark("a", revision=NEW, frames=7, at_ms=2)

    described = await records.described("a")
    assert described is not None
    assert (described.revision, described.frames) == (NEW, 7)


async def test_a_file_nothing_knows_about_has_no_record(records: Records) -> None:
    assert await records.described("never-seen") is None


async def test_the_count_is_of_files_this_model_described(
    temp_db: Database, records: Records
) -> None:
    await seed(temp_db, "a", "b")
    await records.mark("a", revision=NEW, frames=1, at_ms=1)
    await records.mark("b", revision=OLD, frames=1, at_ms=1)

    assert await records.described_count(NEW) == 1


async def test_forgetting_one_file_puts_it_back_on_the_list(
    temp_db: Database, records: Records
) -> None:
    await seed(temp_db, "a")
    await records.mark("a", revision=NEW, frames=1, at_ms=1)

    await records.forget("a")

    assert await records.settled_ids(NEW) == set()


async def test_forgetting_everything_puts_the_whole_library_back(
    temp_db: Database, records: Records
) -> None:
    """What removing the index does to this half of it. Dropping only the numbers would leave a
    work list saying every file is done and an index with nothing in it."""
    await seed(temp_db, "a", "b")
    await records.mark("a", revision=NEW, frames=1, at_ms=1)
    await records.mark("b", revision=NEW, frames=1, at_ms=1)

    await records.forget_all()

    assert await records.settled_ids(NEW) == set()


async def test_removing_the_index_is_written_down_once_with_who_did_it(temp_db: Database) -> None:
    """Remove the index deletes every file's description, and Faces' own removal was the one of
    the two that History said. Written in the transaction that empties the table, with the
    feature's switch as the subject; a sweep nobody pressed (`by` absent) writes nothing."""
    import sift.main  # noqa: F401 (the ledger's table is the workbench's)
    from sift.kernel.access import Role
    from sift.kernel.ledger import Actor
    from sift.testing.fixtures import create_user

    await temp_db.initialize_schema()
    records = Records(temp_db)
    await seed(temp_db, "a")
    await records.mark("a", revision=NEW, frames=1, at_ms=1)
    admin = await create_user(temp_db, Role.ADMIN)

    await records.forget_all()
    await records.forget_all(Actor.user(admin.id))

    rows = await temp_db.fetch_all(
        "SELECT d.verb, s.kind, s.subject_id, s.name FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
    )
    assert [tuple(row) for row in rows] == [
        ("forgot", "setting", "semantic.enabled", "Smart Search")
    ]


# --- whether this model has described anything at all ---------------------------------------


async def test_nothing_described_by_this_model_is_nothing_to_compare_against(
    temp_db: Database, records: Records
) -> None:
    """The read a pass makes ONCE, before asking about a file at a time. An index full of the
    previous model's numbers is an index this model can answer nothing out of."""
    await seed(temp_db, "a")
    await records.mark("a", revision=OLD, frames=3, at_ms=1)

    assert await records.describes_anything(OLD) is True
    assert await records.describes_anything(NEW) is False


async def test_an_empty_work_list_has_described_nothing(records: Records) -> None:
    assert await records.describes_anything(NEW) is False
