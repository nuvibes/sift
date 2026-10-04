# SPDX-License-Identifier: AGPL-3.0-or-later
"""The door: nothing about a file or a record marked kept local leaves this machine.

The adapter is a stand-in, and that is the whole shape of these tests: what is under test is which
questions REACH it, because reaching it is what sends a request to somebody else's service. A test
that only checked the refusal's wording would pass on a build that refused after asking.

The other half here is the ledger the refusal's sister feature writes: when a box last enriched
something, and whether anybody pressed it. Kept together because they are one column's worth of
menu: a row that refuses, and the line under it saying when it last did not.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

# The ledger's tables: every run of a person, a site or a tag writes its `enriched` event there.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.catalog import kept_local, set_kept_local_on
from sift.kernel.access.viewer import Role
from sift.kernel.db import Database
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.secret_store import SecretStore
from sift.slices.stash_boxes.adapter import Box
from sift.slices.stash_boxes.service import KEPT_LOCAL, KeptLocal, StashBoxService
from sift.testing.fixtures import create_user

A_KEY = b"0" * 32

#: An invented library. Never a real person, a real studio or a real username. See
#: `tests/gates/data/names_cast.txt`.
A_PERSON = "person-lumen-vasco"
A_FILE = "asset-quiet-harbour"


async def _set_kept(database: Database, kind: str, local_id: str, kept: bool) -> bool:
    """The flag alone, in a transaction of its own: what a test of the COLUMN wants.

    The catalog offers no form that opens its own transaction, deliberately: the service writes the
    flag and its ledger event together. A test about what the flag does needs neither the event nor
    a viewer, so it opens the write itself here.
    """
    async with database.write() as connection:
        return await set_kept_local_on(connection, kind, local_id, kept)


class _Adapter:
    """Counts what it was asked. Asking at all is the thing being measured."""

    def __init__(self) -> None:
        self.asked = 0

    async def search(self, box: Box, term: str) -> list[FoundRecord]:
        _ = (box, term)
        self.asked += 1
        return []

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        _ = (box, hashes)
        self.asked += 1
        return []

    async def person(self, box: Box, remote_id: str) -> FoundRecord | None:
        _ = (box, remote_id)
        self.asked += 1
        return FoundRecord(
            source_id="box", remote_id=remote_id, subject=Subject.PERSON, name="Lumen Vasco"
        )


@pytest.fixture
async def adapter() -> _Adapter:
    return _Adapter()


@pytest.fixture
async def service(temp_db: Database, adapter: _Adapter) -> StashBoxService:
    await temp_db.initialize_schema()
    return StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]


async def _a_box(service: StashBoxService) -> str:
    return await service.add(
        name="StashDB",
        endpoint="https://stashdb.example/graphql",
        api_key="a-real-key",
        master_key=A_KEY,
    )


async def _a_person(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
            (A_PERSON, "Lumen Vasco"),
        )


async def test_a_person_kept_local_has_their_name_sent_nowhere(
    service: StashBoxService, temp_db: Database, adapter: _Adapter
) -> None:
    """The refusal, measured at the adapter rather than at the wording."""
    await _a_box(service)
    await _a_person(temp_db)
    assert await _set_kept(temp_db, "person", A_PERSON, True)

    with pytest.raises(KeptLocal):
        await service.search("Lumen Vasco", A_KEY, subject=Subject.PERSON, about=A_PERSON)

    assert adapter.asked == 0


async def test_the_same_person_is_asked_about_once_the_refusal_is_lifted(
    service: StashBoxService, temp_db: Database, adapter: _Adapter
) -> None:
    """The other direction, which is what makes the test above about the FLAG rather than the name."""
    await _a_box(service)
    await _a_person(temp_db)
    await _set_kept(temp_db, "person", A_PERSON, True)
    await _set_kept(temp_db, "person", A_PERSON, False)

    await service.search("Lumen Vasco", A_KEY, subject=Subject.PERSON, about=A_PERSON)

    assert adapter.asked == 1


async def test_a_term_about_no_row_is_still_asked(
    service: StashBoxService, adapter: _Adapter
) -> None:
    """A word somebody typed into the chooser is not a fact about anything in this library."""
    await _a_box(service)

    await service.search("Lumen Vasco", A_KEY, subject=Subject.PERSON)

    assert adapter.asked == 1


async def _a_file(temp_db: Database, asset_id: str, *, kept: bool = False) -> None:
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
            (asset_id, asset_id),
        )
    if kept:
        assert await _set_kept(temp_db, "asset", asset_id, True)


async def test_a_file_kept_local_has_its_fingerprints_sent_nowhere(
    service: StashBoxService, temp_db: Database, adapter: _Adapter
) -> None:
    """The fingerprints are the thing that leaves for a FILE, and this is where they do not."""
    await _a_box(service)
    await _a_file(temp_db, A_FILE, kept=True)

    with pytest.raises(KeptLocal):
        await service.recognise(A_FILE, {"oshash": "0" * 16}, A_KEY)

    assert adapter.asked == 0


async def test_a_file_nobody_marked_is_asked_about(
    service: StashBoxService, temp_db: Database, adapter: _Adapter
) -> None:
    await _a_box(service)
    await _a_file(temp_db, A_FILE)

    await service.recognise(A_FILE, {"oshash": "0" * 16}, A_KEY)

    assert adapter.asked == 1


async def test_linking_a_kept_local_person_is_refused_before_the_box_is_asked(
    service: StashBoxService, temp_db: Database, adapter: _Adapter
) -> None:
    box_id = await _a_box(service)
    await _a_person(temp_db)
    await _set_kept(temp_db, "person", A_PERSON, True)

    with pytest.raises(KeptLocal):
        await service.link(Subject.PERSON, A_PERSON, box_id, "remote-1", A_KEY)

    assert adapter.asked == 0


async def test_the_refusal_says_where_it_stayed(
    service: StashBoxService, temp_db: Database
) -> None:
    """The sentence is the one thing a person reads, so it is pinned rather than left to drift."""
    await _a_box(service)
    await _a_person(temp_db)
    await _set_kept(temp_db, "person", A_PERSON, True)

    with pytest.raises(KeptLocal) as refused:
        await service.search("Lumen Vasco", A_KEY, subject=Subject.PERSON, about=A_PERSON)

    assert str(refused.value) == KEPT_LOCAL
    assert "not sent outside this device" in KEPT_LOCAL


async def test_a_sweeps_work_list_leaves_out_what_is_kept_local(
    service: StashBoxService, temp_db: Database
) -> None:
    """Not the rule (the door is), but the right place to spend nothing on them."""
    await _a_box(service)
    await _a_file(temp_db, A_FILE, kept=True)
    await _a_file(temp_db, "asset-open-water")

    assert await service.unasked([A_FILE, "asset-open-water"]) == ["asset-open-water"]


async def test_the_chosen_box_is_the_only_one_asked(
    service: StashBoxService, temp_db: Database, adapter: _Adapter
) -> None:
    """The flyout's answer, measured where it decides something: how many boxes are asked."""
    await _a_box(service)
    await service.add(
        name="FansDB",
        endpoint="https://fansdb.cc/graphql",
        api_key="another-key",
        master_key=A_KEY,
    )

    await service.search("Lumen Vasco", A_KEY, subject=Subject.PERSON, only="fansdb")

    assert adapter.asked == 1


async def test_a_word_naming_no_configured_box_asks_nobody(
    service: StashBoxService, adapter: _Adapter
) -> None:
    """Never every box. "Ask only FansDB" where FansDB has gone must ask nobody: falling back to
    all of them would send out exactly what somebody narrowed the pass to stop sending."""
    await _a_box(service)

    assert await service.search("Lumen Vasco", A_KEY, subject=Subject.PERSON, only="fansdb") == []
    assert adapter.asked == 0


async def test_an_enrichment_is_remembered_with_who_set_it_going(
    service: StashBoxService, temp_db: Database
) -> None:
    box_id = await _a_box(service)
    await _a_person(temp_db)

    await service.record_enrichment(Subject.PERSON, A_PERSON, box_id, automatic=True)
    runs = await service.enrichment_of(Subject.PERSON, A_PERSON)

    assert [(one.box_id, one.box_name, one.automatic) for one in runs] == [
        (box_id, "StashDB", True)
    ]


async def test_a_second_run_is_kept_and_the_reader_still_answers_with_the_last(
    service: StashBoxService, temp_db: Database
) -> None:
    """Every ask is kept (catalog version 51), so a History can say when each one happened.

    What this asserts is that keeping them costs the readers nothing. They still get one row per box
    and it is still the newest: the reader groups by the box and takes the largest moment, which
    is a seek on the index that replaced the primary key."""
    box_id = await _a_box(service)
    await _a_person(temp_db)

    await service.record_enrichment(Subject.PERSON, A_PERSON, box_id, automatic=True)
    await service.record_enrichment(Subject.PERSON, A_PERSON, box_id, automatic=False)

    runs = await service.enrichment_of(Subject.PERSON, A_PERSON)
    assert [one.automatic for one in runs] == [False]

    kept = await temp_db.fetch_all(
        "SELECT automatic FROM enrichment_runs WHERE subject = 'person' AND local_id = ?"
        " ORDER BY at, id",
        (A_PERSON,),
    )
    assert [int(row["automatic"]) for row in kept] == [1, 0]


async def _enriched(temp_db: Database) -> list[dict[str, object]]:
    rows = await temp_db.fetch_all(
        "SELECT actor_kind, actor_id, object_kind, object_id, object_name, payload"
        " FROM workbench_decisions WHERE verb = 'enriched' ORDER BY id"
    )
    return [dict(row) for row in rows]


async def test_each_run_writes_its_event_naming_the_box_and_what_landed(
    service: StashBoxService, temp_db: Database
) -> None:
    """One press, one `enriched` event, in the run's own transaction: the box as the object and
    what landed as the payload, in the run column's own shape. The user where somebody pressed
    it, the box where nobody did, so a thread draws every press, not only each box's latest run."""
    box_id = await _a_box(service)
    await _a_person(temp_db)
    pressed = await create_user(temp_db, Role.ADMIN)

    await service.record_enrichment(
        Subject.PERSON,
        A_PERSON,
        box_id,
        automatic=False,
        applied={"birth_date": 1, "links": 3},
        pressed_by=pressed.id,
    )
    await service.record_enrichment(Subject.PERSON, A_PERSON, box_id, automatic=True, applied={})
    await service.record_enrichment(Subject.PERSON, A_PERSON, box_id, automatic=True)

    said = await _enriched(temp_db)
    assert [(one["actor_kind"], one["actor_id"]) for one in said] == [
        ("user", pressed.id),
        ("box", box_id),
        ("box", box_id),
    ]
    assert {(one["object_kind"], one["object_id"], one["object_name"]) for one in said} == {
        ("box", box_id, "StashDB")
    }
    # The key-to-count object; `{}` for a plan that filled nothing; nothing for a bare link.
    assert [one["payload"] for one in said] == [
        json.dumps({"birth_date": 1, "links": 3}),
        "{}",
        "",
    ]
    subjects = await temp_db.fetch_all(
        "SELECT kind, subject_id, name FROM workbench_decision_subjects"
    )
    assert {(row["kind"], row["subject_id"], row["name"]) for row in subjects} == {
        ("person", A_PERSON, "Lumen Vasco")
    }


async def test_a_files_run_writes_no_event(service: StashBoxService, temp_db: Database) -> None:
    """A file's thread draws the box's line off the match and the run; an event per file would be
    a second copy there and an unattended pass listed file by file on the feed."""
    box_id = await _a_box(service)

    await service.record_enrichment(
        Subject.ASSET,
        A_FILE,
        box_id,
        automatic=False,
        applied={"title": 1},
        pressed_by=(await create_user(temp_db, Role.ADMIN)).id,
    )

    assert await _enriched(temp_db) == []


async def test_nothing_is_remembered_for_a_kind_no_box_is_told_about(
    service: StashBoxService, temp_db: Database
) -> None:
    box_id = await _a_box(service)

    assert await service.enrichment_of(Subject.USERNAME, "account-1") == []
    assert not await kept_local(temp_db, "collection", "collection-1")
    assert box_id


async def test_a_site_can_be_kept_local_at_all(temp_db: Database) -> None:
    """A Site is named by the word the wire uses, and that is the only word these take.

    The statements are keyed `asset`, `person`, `site`, `tag`; the SQL underneath says `sites`
    because a statement names its own table. A reader that put the kind through `stored_word`
    would miss the table for every Site: `kept_local` would answer False for one that is kept
    local, and `set_kept_local` "no such row".
    """
    await temp_db.initialize_schema()
    await temp_db.execute("INSERT INTO sites (id, name) VALUES ('site-1', 'Quillhouse')")

    assert await _set_kept(temp_db, "site", "site-1", True) is True
    assert await kept_local(temp_db, "site", "site-1") is True
    # A kind this map does not name describes nothing here, and answers so rather than failing:
    # a collection cannot be kept local because nothing is ever sent outside the LAN about one.
    assert await kept_local(temp_db, "collection", "site-1") is False
    assert await _set_kept(temp_db, "collection", "site-1", False) is False
    # And the switch really does come back off, which is what the menu's other half presses.
    assert await _set_kept(temp_db, "site", "site-1", False) is True
    assert await kept_local(temp_db, "site", "site-1") is False


# --- The inheritance: a file is kept local by what it is filed under -----------------------------
#
# Not enriching a person, Site or tag applies to every file under it, the way sharing inherits.
#
# Measured at the adapter, like every other test in this file, because the property is that the
# question never goes out, not that a sentence comes back. A build that asked and then refused
# would pass a wording test and would have sent the fingerprints.

A_SITE = "site-quillhouse"
A_TAG = "tag-lantern-work"


async def _filed_under_a_site(temp_db: Database, asset_id: str) -> None:
    """One file, one username, one Site: the two hops a file takes to reach the Site it came
    off."""
    async with temp_db.write() as connection:
        await connection.execute("INSERT INTO sites (id, name) VALUES (?, 'Quillhouse')", (A_SITE,))
        await connection.execute(
            "INSERT INTO usernames (id, site_id, name, created_at)"
            " VALUES ('username-1', ?, 'quillwright', 0)",
            (A_SITE,),
        )
        await connection.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, 'username-1')",
            (asset_id,),
        )


async def test_a_file_under_a_kept_local_site_is_refused(
    service: StashBoxService, temp_db: Database, adapter: _Adapter
) -> None:
    """The Site says no, so the file's fingerprints stay here, and its own switch says nothing."""
    await _a_box(service)
    await _a_file(temp_db, A_FILE)
    await _filed_under_a_site(temp_db, A_FILE)
    assert await _set_kept(temp_db, "site", A_SITE, True)

    with pytest.raises(KeptLocal):
        await service.recognise(A_FILE, {"oshash": "abc"}, A_KEY)

    assert adapter.asked == 0
    # The FILE's own row still says nothing, which is what the menu's switch reads: pressing
    # "Allow enrichment" on the file would change that row and change nothing about what leaves,
    # so the two answers have to stay tellable apart. See `kept_local_here`.
    assert await service.kept_local_here(Subject.ASSET, A_FILE) is False
    assert await service.kept_local(Subject.ASSET, A_FILE) is True


async def test_the_same_file_is_asked_about_once_the_site_lets_it_go(
    service: StashBoxService, temp_db: Database, adapter: _Adapter
) -> None:
    """The other direction, which makes the test above about the SITE rather than about the file."""
    await _a_box(service)
    await _a_file(temp_db, A_FILE)
    await _filed_under_a_site(temp_db, A_FILE)
    await _set_kept(temp_db, "site", A_SITE, True)
    await _set_kept(temp_db, "site", A_SITE, False)

    await service.recognise(A_FILE, {"oshash": "abc"}, A_KEY)

    assert adapter.asked == 1


async def test_a_file_under_a_kept_local_person_or_tag_is_refused(
    service: StashBoxService, temp_db: Database, adapter: _Adapter
) -> None:
    """The other two filings, each on its own file, so neither can be passing on the other's row."""
    await _a_box(service)
    await _a_file(temp_db, "asset-by-person")
    await _a_file(temp_db, "asset-by-tag")
    await _a_person(temp_db)
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?, 'lantern work', 0)", (A_TAG,)
        )
        await connection.execute(
            "INSERT INTO asset_people (asset_id, person_id) VALUES ('asset-by-person', ?)",
            (A_PERSON,),
        )
        await connection.execute(
            "INSERT INTO asset_tags (asset_id, tag_id) VALUES ('asset-by-tag', ?)", (A_TAG,)
        )
    await _set_kept(temp_db, "person", A_PERSON, True)
    await _set_kept(temp_db, "tag", A_TAG, True)

    for asset_id in ("asset-by-person", "asset-by-tag"):
        with pytest.raises(KeptLocal):
            await service.recognise(asset_id, {"oshash": "abc"}, A_KEY)
    assert adapter.asked == 0


async def test_an_ordinary_file_is_asked_about(
    service: StashBoxService, temp_db: Database, adapter: _Adapter
) -> None:
    """The floor under all of it: a file nothing refuses is still sent, filings and all.

    Without this the four above would pass on a door that refused everything, which is the one
    way a rule like this fails silently, because nothing on a screen says a question was not asked.
    """
    await _a_box(service)
    await _a_file(temp_db, A_FILE)
    await _filed_under_a_site(temp_db, A_FILE)

    await service.recognise(A_FILE, {"oshash": "abc"}, A_KEY)

    assert adapter.asked == 1


def test_kept_local_over_carries_the_site_reach_fragment() -> None:
    """The reach fragment is written out inside the statement (the SQL rule refuses a joined
    constant), so this holds the copy to `SITE_REACH` byte for byte."""
    from sift.kernel.access.catalog import _KEPT_LOCAL_OVER
    from sift.kernel.access.sites import SITE_REACH

    assert SITE_REACH in _KEPT_LOCAL_OVER.sql


def test_the_pile_carries_the_kernels_kept_local_rule() -> None:
    """The pile's two statements write the kernel's rule out (the SQL rule refuses a joined
    constant), so this holds both copies to it byte for byte: the file's own switch, and every file
    under a person, a tag or a Site kept local: `constraints.KEPT_LOCAL_FILES`, the same fragment
    the walls read."""
    from sift.kernel.access.constraints import KEPT_LOCAL_FILES
    from sift.slices.stash_boxes.matches import (
        _COUNT_WAITING,
        _MATCH_WAITING_POSITION,
        _MATCHES_WAITING,
    )

    own = "asset_id NOT IN (SELECT id FROM assets WHERE keep_local = 1)"
    # And the third copy: where a waiting match sits on the pile, which must count the same pile.
    for statement in (_MATCHES_WAITING, _COUNT_WAITING, _MATCH_WAITING_POSITION):
        assert own in statement
        assert f"asset_id NOT IN ({KEPT_LOCAL_FILES})" in statement
