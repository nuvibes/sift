# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a kept stash-box record and this library disagree, and what settling one does.

A list of FIELDS and not of subjects, which is the whole screen: "StashDB has something to say
about Jane" is not a question anybody can answer; "StashDB says 1991 and you have 1990" is.

Nothing is stored. A disagreement is the state of two values and either can change under it, so the
list is worked out on every read and is true when it is drawn, rather than being a table
describing a disagreement that ended a week ago.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

# The ledger's tables: taking a box's answer writes a run and its `enriched` event.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Role, Viewer
from sift.kernel.db import Database
from sift.kernel.enrichment import Creating, Enricher, Missing
from sift.kernel.ledger import Actor
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.secret_store import SecretStore
from sift.slices.stash_boxes.adapter import Box, as_json
from sift.slices.stash_boxes.reconcile import Disagreement, Reconciler, ReconcileReceipts
from sift.slices.stash_boxes.service import StashBoxService
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.anyio

A_KEY = b"0" * 32
ADMIN = Viewer(id="admin", role=Role.ADMIN)


class _Adapter:
    async def search(self, box: Box, term: str) -> list[FoundRecord]:  # pragma: no cover
        raise AssertionError("reconcile never reaches the network")

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        raise AssertionError("reconcile never reaches the network")  # pragma: no cover


class _Settings:
    def __init__(self, values: Mapping[str, object] | None = None) -> None:
        self.values = dict(values or {})

    async def get_app(self, key: str) -> object:
        return self.values.get(key)


class _Access:
    """Visibility, stood in for. `hidden` is what this user may not be shown.

    It counts the one-at-a-time reads as well as answering them, because how many there are is
    what the batched form exists to prevent: the People wall costs the same whether it is narrowed
    to one person or to all of them.
    """

    def __init__(self, hidden: set[str] | None = None) -> None:
        self.hidden = hidden or set()
        #: How many times each by-id form was asked. Read by the test that proves the batching.
        self.one_at_a_time = 0
        self.batched = 0

    async def visible_person(self, viewer: Viewer, local_id: str) -> object | None:
        _ = viewer
        self.one_at_a_time += 1
        return None if local_id in self.hidden else object()

    async def visible_people(self, viewer: Viewer, local_ids: list[str]) -> dict[str, object]:
        """The batched form. Absent means "not allowed" and "not there" together, as the real one's
        does, so a caller cannot tell those apart from the answer."""
        _ = viewer
        self.batched += 1
        return {one: object() for one in local_ids if one not in self.hidden}

    async def visible_site(self, viewer: Viewer, local_id: str) -> object | None:
        return await self.visible_person(viewer, local_id)

    async def visible_sites(self, viewer: Viewer, local_ids: list[str]) -> dict[str, object]:
        _ = viewer
        return {one: object() for one in local_ids if one not in self.hidden}

    async def visible_tags(self, viewer: Viewer, local_ids: list[str]) -> dict[str, object]:
        return await self.visible_sites(viewer, local_ids)

    async def visible_tag(self, viewer: Viewer, local_id: str) -> object | None:
        return await self.visible_person(viewer, local_id)


class _Writer:
    """A person, as far as this file is concerned: what is held, and what was written."""

    subject = Subject.PERSON

    def __init__(self, held: Mapping[str, object] | None = None) -> None:
        self.held = dict(held or {})
        self.written: list[tuple[str, dict[str, object]]] = []

    async def current(self, local_id: str) -> Mapping[str, object]:
        _ = local_id
        return self.held

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        _ = values
        return ()

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Takes every field and says so. A writer answers with what it actually wrote, and a
        settle reports "settled" only for a field that landed."""
        _ = creating, actor
        self.written.append((local_id, dict(values)))
        return dict.fromkeys(values, 1)


async def _service(temp_db: Database) -> StashBoxService:
    await temp_db.initialize_schema()
    return StashBoxService(temp_db, SecretStore(temp_db), _Adapter())  # type: ignore[arg-type]


async def _a_box(service: StashBoxService, name: str = "StashDB") -> str:
    return await service.add(
        name=name,
        endpoint=f"https://{name.lower()}.example/graphql",
        api_key=None,
        master_key=A_KEY,
    )


async def _a_person_link(
    temp_db: Database, person_id: str, box_id: str, fields: Mapping[str, object], name: str = "Jane"
) -> None:
    """A kept record, written straight to the table. The route that makes one FETCHES, and what is
    under test here is what is done with what was kept rather than how it was got."""
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name)
    )
    record = FoundRecord(
        source_id=box_id,
        remote_id="r1",
        subject=Subject.PERSON,
        name=name,
        fields=dict(fields),
        confidence=1.0,
    )
    await temp_db.execute(
        "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, ?, 'r1', ?, 0)",
        (person_id, box_id, as_json([record])),
    )


def _reconciler(
    service: StashBoxService, writer: _Writer, access: _Access | None = None
) -> Reconciler:
    enricher = Enricher()
    enricher.register(writer)
    return Reconciler(service, enricher, _Settings(), access or _Access())  # type: ignore[arg-type]


# --- what disagrees ---------------------------------------------------------------------------


async def test_the_disagreements_are_kept_until_the_library_moves(
    temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planning an enrichment per linked subject to COUNT disagreements would be the Organize
    board's long pole, for a number that moves only when something in the library does. Under one mark the
    answer is the same object; a moved mark computes it again."""
    from sift.slices.stash_boxes import reconcile as module

    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"})
    reconciler = _reconciler(service, _Writer({"birth_date": "1990-01-01"}))
    marks = iter(["m1", "m1", "m2"])
    monkeypatch.setattr(module, "current_mark", lambda: next(marks))

    first = await reconciler.disagreements(ADMIN)
    again = await reconciler.disagreements(ADMIN)
    moved = await reconciler.disagreements(ADMIN)

    assert first is again, "the same library, the same answer, not computed twice"
    assert moved is not first and moved == first


class _ManyWriter(_Writer):
    """A person writer that reads many people in one go, and counts how it was asked."""

    def __init__(self, held: Mapping[str, object] | None = None) -> None:
        super().__init__(held)
        self.one_at_a_time = 0
        self.batches: list[list[str]] = []

    async def current(self, local_id: str) -> Mapping[str, object]:
        self.one_at_a_time += 1
        return await super().current(local_id)

    async def current_many(self, local_ids: list[str]) -> dict[str, Mapping[str, object]]:
        self.batches.append(list(local_ids))
        return {one: self.held for one in local_ids}


async def test_the_survey_reads_every_linked_person_in_one_ask(temp_db: Database) -> None:
    """The wall filtered to disagreements waits on this survey. Read one person at a time it would
    be five round trips per link, seconds on a thousand links; read per kind it is the same answer
    from one ask."""
    service = await _service(temp_db)
    box = await _a_box(service)
    for n in range(3):
        await _a_person_link(temp_db, f"p{n}", box, {"birth_date": "1991-02-02"}, name=f"P{n}")
    writer = _ManyWriter({"birth_date": "1990-01-01"})

    found = await _reconciler(service, writer).disagreements(ADMIN)

    assert sorted(one.local_id for one in found) == ["p0", "p1", "p2"]
    assert [sorted(one) for one in writer.batches] == [["p0", "p1", "p2"]]
    assert writer.one_at_a_time == 0


async def test_a_field_two_answers_differ_about_is_a_row(temp_db: Database) -> None:
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"})
    writer = _Writer({"birth_date": "1990-01-01"})

    found = await _reconciler(service, writer).disagreements(ADMIN)

    assert [(one.key, one.mine, one.theirs) for one in found] == [
        ("birth_date", "1990-01-01", "1991-02-02")
    ]
    assert found[0].name == "Jane"
    assert found[0].box_name == "StashDB"


async def test_two_people_are_compared_against_one_reading_of_the_rules(
    temp_db: Database,
) -> None:
    """The rules are read per KIND of subject rather than per subject. A library with two hundred
    linked people would otherwise read thirty-six settings two hundred times over."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"}, name="Jane")
    await _a_person_link(temp_db, "p2", box, {"birth_date": "1992-03-03"}, name="Neve")

    found = await _reconciler(service, _Writer({"birth_date": "1990-01-01"})).disagreements(ADMIN)

    assert sorted(one.local_id for one in found) == ["p1", "p2"]


async def test_a_box_is_named_from_the_one_it_is_rather_than_the_first_one_configured(
    temp_db: Database,
) -> None:
    """Two boxes and one row. A name taken from the head of the list would be right exactly as
    often as there is one box."""
    service = await _service(temp_db)
    await _a_box(service, name="StashDB")
    second = await _a_box(service, name="FansDB")
    await _a_person_link(temp_db, "p1", second, {"birth_date": "1991-02-02"})

    found = await _reconciler(service, _Writer({"birth_date": "1990-01-01"})).disagreements(ADMIN)

    assert [one.box_name for one in found] == ["FansDB"]


async def test_a_field_nobody_has_filled_in_is_not_a_disagreement(temp_db: Database) -> None:
    """It is a gap, and filling one in loses nothing, so it is written under the ordinary rules
    rather than made into a question."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"})

    found = await _reconciler(service, _Writer()).disagreements(ADMIN)

    assert found == []


async def test_two_answers_that_agree_are_not_a_disagreement(temp_db: Database) -> None:
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1990-01-01"})

    found = await _reconciler(service, _Writer({"birth_date": "1990-01-01"})).disagreements(ADMIN)

    assert found == []


async def test_a_subject_this_account_may_not_be_shown_produces_no_row(
    temp_db: Database,
) -> None:
    """It matters more here than on most screens: a row carries a person's NAME and their birth
    date side by side, which is the most identifying pair in the database."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"})
    writer = _Writer({"birth_date": "1990-01-01"})

    found = await _reconciler(service, writer, _Access(hidden={"p1"})).disagreements(ADMIN)

    assert found == []


async def test_every_linked_person_is_resolved_in_one_read_rather_than_one_each(
    temp_db: Database,
) -> None:
    """The cost of this screen, and the reason it is worth a test of its own.

    Narrowing the People wall to one id does not make it cheaper (the by-id condition sits after
    the counts), so one read per linked person would resolve the whole wall once per person. So
    this asserts the shape of the asking and not a duration: one batched read, and no by-id read.
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    for local_id, name in (("p1", "Ada"), ("p2", "Bea"), ("p3", "Cyd")):
        await _a_person_link(temp_db, local_id, box, {"birth_date": "1991-02-02"}, name=name)
    access = _Access(hidden={"p2"})

    found = await _reconciler(service, _Writer({"birth_date": "1990-01-01"}), access).disagreements(
        ADMIN
    )

    assert access.batched == 1
    assert access.one_at_a_time == 0
    # And the scoping still holds through the batched form: the hidden person has no row.
    assert sorted(one.local_id for one in found) == ["p1", "p3"]


async def test_every_linked_site_is_resolved_in_one_read_rather_than_one_each(
    temp_db: Database,
) -> None:
    """A Site's by-id read is the Sites wall asked for one row, so the survey asks once per kind."""
    service = await _service(temp_db)
    box = await _a_box(service)
    for local_id in ("s1", "s2", "s3"):
        await temp_db.execute(
            "INSERT INTO sites (id, name) VALUES (?, ?)", (local_id, f"Studio {local_id}")
        )
        record = FoundRecord(
            source_id=box,
            remote_id=f"r{local_id}",
            subject=Subject.SITE,
            name=local_id,
            fields={"details": "theirs"},
            confidence=1.0,
        )
        await temp_db.execute(
            "INSERT INTO site_stash_box_links (site_id, box_id, remote_id, payload, fetched_at)"
            " VALUES (?, ?, ?, ?, 0)",
            (local_id, box, f"r{local_id}", as_json([record])),
        )

    class _SiteWriter(_Writer):
        subject = Subject.SITE

    access = _Access(hidden={"s2"})
    found = await _reconciler(service, _SiteWriter({"details": "mine"}), access).disagreements(
        ADMIN
    )

    assert access.one_at_a_time == 0
    assert sorted(one.local_id for one in found) == ["s1", "s3"]


async def test_every_kind_of_subject_is_put_to_the_same_visibility_rule(
    temp_db: Database,
) -> None:
    """A site and a tag are scoped exactly as a person is. Three subjects and one rule, and each
    reads a different lister, so a copy that stopped agreeing would say so here."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await temp_db.execute("INSERT INTO sites (id, name) VALUES ('s1', 'A Studio')")
    await temp_db.execute("INSERT INTO tags (id, name, created_at) VALUES ('t1', 'beach', 0)")
    for table, column, local_id in (
        ("site_stash_box_links", "site_id", "s1"),
        ("tag_stash_box_links", "tag_id", "t1"),
    ):
        record = FoundRecord(
            source_id=box,
            remote_id="r1",
            subject=Subject.SITE if column == "site_id" else Subject.TAG,
            name=local_id,
            fields={"description": "theirs"},
            confidence=1.0,
        )
        statement = (
            "INSERT INTO site_stash_box_links"
            " (site_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, 'r1', ?, 0)"
            if table == "site_stash_box_links"
            else "INSERT INTO tag_stash_box_links"
            " (tag_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, 'r1', ?, 0)"
        )
        await temp_db.execute(statement, (local_id, box, as_json([record])))

    hidden = _Access(hidden={"s1", "t1"})
    assert await _reconciler(service, _Writer(), hidden).disagreements(ADMIN) == []


async def test_a_subject_nothing_can_write_produces_no_row_rather_than_failing(
    temp_db: Database,
) -> None:
    """An older build meeting a newer one. A kind of subject this version cannot write has nothing
    to compare against, which is not the same as a disagreement."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await temp_db.execute("INSERT INTO tags (id, name, created_at) VALUES ('t1', 'beach', 0)")
    record = FoundRecord(
        source_id=box,
        remote_id="r1",
        subject=Subject.TAG,
        name="beach",
        fields={"description": "Sand."},
        confidence=1.0,
    )
    await temp_db.execute(
        "INSERT INTO tag_stash_box_links (tag_id, box_id, remote_id, payload, fetched_at)"
        " VALUES ('t1', ?, 'r1', ?, 0)",
        (box, as_json([record])),
    )

    found = await _reconciler(service, _Writer()).disagreements(ADMIN)

    assert found == []


async def test_a_box_that_has_gone_between_the_two_reads_is_named_by_its_id(
    temp_db: Database,
) -> None:
    """The links and the names are two reads, and a box can be removed between them. A row with no
    name on it says nothing about which of two answers it is, so the id is used rather than a blank.
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"})

    class _Forgotten:
        """The service, with the box removed after the links were read."""

        def __init__(self, real: StashBoxService) -> None:
            self._real = real

        async def linked_subjects(self) -> Any:
            return await self._real.linked_subjects()

        async def boxes(self) -> list[Any]:
            return []

        async def kept_answers(self) -> dict[Any, Any]:
            return await self._real.kept_answers()

        async def kept_local(self, subject: Subject, local_id: str) -> bool:
            return await self._real.kept_local(subject, local_id)

    reconciler = _reconciler(_Forgotten(service), _Writer({"birth_date": "1990-01-01"}))  # type: ignore[arg-type]

    found = await reconciler.disagreements(ADMIN)

    assert [one.box_name for one in found] == [box]


# --- settling one -----------------------------------------------------------------------------


def _a_disagreement(box_id: str) -> Disagreement:
    """One row as the panel hands it back, about a person the `_Writer` above holds."""
    return Disagreement(
        subject=Subject.PERSON,
        local_id="p1",
        name="Jane",
        box_id=box_id,
        box_name="StashDB",
        key="birth_date",
        mine="1990-01-01",
        theirs="1991-02-02",
    )


async def test_taking_their_answer_writes_it_through_the_subjects_own_writer(
    temp_db: Database,
) -> None:
    """One place a person's birth date is ever written, whether it was typed or agreed to."""
    service = await _service(temp_db)
    # A real person: the event names the person it was about, by the name their row holds.
    await temp_db.execute("INSERT INTO people (id, name, created_at) VALUES ('p1', 'Jane', 0)")
    writer = _Writer({"birth_date": "1990-01-01"})
    reconciler = _reconciler(service, writer)
    # A real user: the event names who pressed it, and that is a key into `users`.
    pressed = Viewer(id=(await create_user(temp_db, Role.ADMIN)).id, role=Role.ADMIN)
    one = Disagreement(
        subject=Subject.PERSON,
        local_id="p1",
        name="Jane",
        box_id="box",
        box_name="StashDB",
        key="birth_date",
        mine="1990-01-01",
        theirs="1991-02-02",
    )

    assert await reconciler.settle(pressed, one, take_theirs=True) is True
    assert writer.written == [("p1", {"birth_date": "1991-02-02"})]
    # And it is a run of that box, pressed by this user, which filled in the one field, written by
    # the run's own writer with the box.
    runs = await temp_db.fetch_all(
        "SELECT box_id, automatic, applied FROM enrichment_runs WHERE local_id = 'p1'"
    )
    assert [(row["box_id"], row["automatic"], row["applied"]) for row in runs] == [
        ("box", 0, json.dumps({"birth_date": 1}))
    ]
    said = await temp_db.fetch_all(
        "SELECT actor_kind, actor_id, object_kind, object_id FROM workbench_decisions"
        " WHERE verb = 'enriched'"
    )
    assert [tuple(row) for row in said] == [("user", pressed.id, "box", "box")]


async def test_keeping_your_own_answer_writes_no_field_and_remembers_the_answer(
    temp_db: Database,
) -> None:
    """The commonest answer, and both halves of what it must do.

    The field is not written: a field re-written with the value it already holds is a change in
    every log that watches for one. The answer is written, or the next read would work the same
    conflict out again and the row would come straight back.
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    writer = _Writer({"birth_date": "1990-01-01"})
    reconciler = _reconciler(service, writer)
    one = _a_disagreement(box)

    assert await reconciler.settle(ADMIN, one, take_theirs=False) is True

    assert writer.written == []
    assert await service.kept_answers() == {
        ("person", "p1", box, "birth_date"): ('"1990-01-01"', '"1991-02-02"')
    }


async def test_a_kept_answer_is_forgotten_when_the_decision_is_taken_back(
    temp_db: Database,
) -> None:
    """The undo. Nothing was written to the record, so what there is to put back is the QUESTION,
    and forgetting the answer is what puts it back on the panel."""
    service = await _service(temp_db)
    box = await _a_box(service)
    reconciler = _reconciler(service, _Writer({"birth_date": "1990-01-01"}))
    one = _a_disagreement(box)
    await reconciler.settle(ADMIN, one, take_theirs=False)

    assert await reconciler.forget_kept(ADMIN, Subject.PERSON, "p1", box, "birth_date") is True
    assert await service.kept_answers() == {}


async def test_an_undo_of_a_record_this_account_may_not_see_is_refused(
    temp_db: Database,
) -> None:
    """An undo is a write, and a write about a record this user may not be shown is one they may
    not make. False rather than a raise: it is what the reverser reports as "could not be undone".
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    reconciler = _reconciler(service, _Writer(), _Access(hidden={"p1"}))

    assert await reconciler.forget_kept(ADMIN, Subject.PERSON, "p1", box, "birth_date") is False


async def test_an_answer_about_a_value_that_cannot_be_written_down_is_not_remembered_wrongly(
    temp_db: Database,
) -> None:
    """A value JSON cannot carry is remembered by its `repr` rather than dropped or guessed at.

    Nothing here has ever produced one (a conflict only happens on a field holding ONE value and
    every one of those comes off a stash-box's JSON), so this is the branch that must not lose the
    row. It is better for the question to come back than for it to be matched by accident.
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    reconciler = _reconciler(service, _Writer())
    unwritable = object()
    one = Disagreement(
        subject=Subject.PERSON,
        local_id="p1",
        name="Jane",
        box_id=box,
        box_name="StashDB",
        key="birth_date",
        mine=unwritable,
        theirs="1991-02-02",
    )

    assert await reconciler.settle(ADMIN, one, take_theirs=False) is True

    held = await service.kept_answers()
    assert held[("person", "p1", box, "birth_date")] == (repr(unwritable), '"1991-02-02"')


async def test_settling_something_this_account_may_not_see_is_refused(temp_db: Database) -> None:
    service = await _service(temp_db)
    writer = _Writer({"birth_date": "1990-01-01"})
    reconciler = _reconciler(service, writer, _Access(hidden={"p1"}))
    one = Disagreement(
        subject=Subject.PERSON,
        local_id="p1",
        name="Jane",
        box_id="box",
        box_name="StashDB",
        key="birth_date",
        mine="1990-01-01",
        theirs="1991-02-02",
    )

    assert await reconciler.settle(ADMIN, one, take_theirs=True) is False
    assert writer.written == []


@pytest.mark.parametrize("subject", [Subject.SITE, Subject.TAG])
async def test_settling_a_site_or_a_tag_this_account_may_not_see_is_refused(
    temp_db: Database, subject: Subject
) -> None:
    """A Site and a tag are scoped as a person is: settling one this account may not be shown is
    refused, and nothing is written."""
    service = await _service(temp_db)
    one = Disagreement(
        subject=subject,
        local_id="s1",
        name="Northlight Media",
        box_id="box",
        box_name="StashDB",
        key="name",
        mine="Northlight",
        theirs="Northlight Media",
    )
    writer = _Writer({"name": "Northlight"})
    refused = _reconciler(service, writer, _Access(hidden={"s1"}))
    assert await refused.settle(ADMIN, one, take_theirs=True) is False
    assert await refused.settle(ADMIN, one, take_theirs=False) is False
    assert writer.written == []


# --- what is left of it once the card is gone ---------------------------------------------------


async def test_a_settled_field_is_drawn_as_no_picture_at_all(temp_db: Database) -> None:
    """A field is not a picture, and a row of stills under "1990 or 1991" would be decoration
    standing in for the thing the decision was actually about."""
    service = await _service(temp_db)
    receipts = ReconcileReceipts(_reconciler(service, _Writer()))

    assert await receipts.pictures_of(ADMIN, "{}") == ()


# --- taking one back ----------------------------------------------------------------------------


def _receipt(**over: Any) -> str:
    recorded = {"subject": "person", "local_id": "p1", "key": "birth_date", "mine": "1990-01-01"}
    recorded.update(over)
    return json.dumps(recorded)


async def test_an_undo_puts_back_exactly_the_value_that_was_there(temp_db: Database) -> None:
    """Reversible where the merge next door is not, and the difference is what was written: this
    wrote ONE field and wrote down what it replaced, so putting it back is exact."""
    service = await _service(temp_db)
    writer = _Writer({"birth_date": "1991-02-02"})
    receipts = ReconcileReceipts(_reconciler(service, writer))

    assert await receipts.reverse(ADMIN, "r1", _receipt()) is True
    assert writer.written == [("p1", {"birth_date": "1990-01-01"})]


async def test_an_undo_of_a_record_that_cannot_be_read_puts_nothing_back(
    temp_db: Database,
) -> None:
    """A record can outlive the version that wrote it. Answering "nothing was put back" is the
    honest reading, where reaching straight in would fail the request and read as a broken undo."""
    service = await _service(temp_db)
    writer = _Writer({"birth_date": "1991-02-02"})
    receipts = ReconcileReceipts(_reconciler(service, writer))

    for payload in (
        "not json at all",
        json.dumps(["a list, not a record"]),
        _receipt(subject="something-else"),
        _receipt(local_id=""),
        _receipt(key=""),
        json.dumps({"subject": "person", "local_id": "p1", "key": "birth_date"}),
    ):
        assert await receipts.reverse(ADMIN, "r1", payload) is False

    assert writer.written == []


async def test_an_undo_of_a_kept_answer_puts_the_question_back_rather_than_a_value(
    temp_db: Database,
) -> None:
    """The second kind of decision that reaches the reverser, and it has nothing to write.

    Taking the box's value replaced a field, so its undo writes the old one back. Keeping your own
    wrote no field at all (what it wrote is the ANSWER), so its undo is forgetting that answer,
    and the row is waiting again the next time the panel is drawn. A payload says which by carrying
    `box_id`, which only this kind has.
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    writer = _Writer({"birth_date": "1990-01-01"})
    reconciler = _reconciler(service, writer)
    await reconciler.settle(ADMIN, _a_disagreement(box), take_theirs=False)
    assert await service.kept_answers() != {}

    receipts = ReconcileReceipts(reconciler)
    kept_receipt = json.dumps(
        {"subject": "person", "local_id": "p1", "key": "birth_date", "box_id": box}
    )

    assert await receipts.reverse(ADMIN, "r1", kept_receipt) is True
    assert await service.kept_answers() == {}
    assert writer.written == [], "forgetting an answer must not write to the record"


# --- one record's own, which is what its page asks ---------------------------------------------


async def test_one_record_is_asked_about_on_its_own(temp_db: Database) -> None:
    """The narrow question, and the whole reason it exists.

    The wide one surveys everything ever linked, which grows with the library, for a page that
    draws at most three rows. Both come out of `_conflicts`, so what is asserted here is that the
    narrow one finds the same field.
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"})
    await _a_person_link(temp_db, "p2", box, {"birth_date": "1970-03-03"}, name="Marisol")
    reconciler = _reconciler(service, _Writer({"birth_date": "1990-01-01"}))

    mine = await reconciler.for_subject(ADMIN, "person", "p1")

    assert mine is not None
    assert [(one.local_id, one.key, one.theirs) for one in mine] == [
        ("p1", "birth_date", "1991-02-02")
    ]
    assert await reconciler.disagreement_count(ADMIN, "person", "p1") == 1


async def test_the_mark_names_each_box_that_disagrees_once(temp_db: Database) -> None:
    """The mark beside the tab says which box it means, so it never reads "a stash-box" where the
    links name one. Two fields from one box name it once, and the boxes come in the panel's order,
    the newest link first."""
    service = await _service(temp_db)
    first = await _a_box(service)
    second = await _a_box(service, "BoxOfQuinces")
    await _a_person_link(
        temp_db, "p1", first, {"birth_date": "1991-02-02", "career_start_year": 2014}
    )
    await temp_db.execute(
        "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
        " VALUES ('p1', ?, 'r2', ?, 1)",
        (
            second,
            as_json(
                [
                    FoundRecord(
                        source_id=second,
                        remote_id="r2",
                        subject=Subject.PERSON,
                        name="Jane",
                        fields={"birth_date": "1992-03-03"},
                        confidence=1.0,
                    )
                ]
            ),
        ),
    )
    held = {"birth_date": "1990-01-01", "career_start_year": 2009}
    reconciler = _reconciler(service, _Writer(held))

    assert await reconciler.disagreement_mark(ADMIN, "person", "p1") == (
        3,
        ["BoxOfQuinces", "StashDB"],
    )
    guest = Viewer(id="guest", role=Role.GUEST)
    assert await reconciler.disagreement_mark(guest, "person", "p1") == (None, [])


async def test_a_record_nothing_is_linked_to_has_nothing_waiting(temp_db: Database) -> None:
    """Nought, not None. It was asked about and nothing disagrees, which is the ordinary answer for
    almost every record in a library."""
    service = await _service(temp_db)
    await temp_db.execute("INSERT INTO people (id, name, created_at) VALUES ('p9', 'Nobody', 0)")
    reconciler = _reconciler(service, _Writer())

    assert await reconciler.for_subject(ADMIN, "person", "p9") == []
    assert await reconciler.disagreement_count(ADMIN, "person", "p9") == 0


async def test_a_field_already_settled_is_not_counted_again(temp_db: Database) -> None:
    """The same rule the wide list keeps, asked the narrow way.

    "Keep yours" writes the ANSWER and not the field, so the two values are unchanged and the
    conflict is worked out afresh on every read. What stops the row coming straight back is the
    answer, remembered against the pair it was an answer TO, and the mark on the tab has to obey
    that as exactly as the panel under it does, or it stands over an empty panel.
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"})
    reconciler = _reconciler(service, _Writer({"birth_date": "1990-01-01"}))
    waiting = await reconciler.for_subject(ADMIN, "person", "p1")
    assert waiting is not None

    assert await reconciler.settle(ADMIN, waiting[0], take_theirs=False) is True

    assert await reconciler.disagreement_count(ADMIN, "person", "p1") == 0


async def test_a_whole_wall_can_be_asked_which_of_its_records_disagree(temp_db: Database) -> None:
    """The facet on the People wall, and the filter a row of it writes.

    A record disagreeing about two fields is ONE row of a wall, so the ids are deduplicated, and
    the one whose kept record says exactly what the library says is not on the list at all, which is
    the ordinary case and the whole reason the column is worth having.

    Asked of the kind, so a wall of tags is not narrowed by what a person disagrees about.
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(
        temp_db, "p1", box, {"birth_date": "1991-02-02", "career_start_year": 2014}
    )
    await _a_person_link(temp_db, "p2", box, {"birth_date": "1992-03-03"}, name="Marisol")
    await _a_person_link(temp_db, "p3", box, {"birth_date": "1990-01-01"}, name="Wren")
    held = {"birth_date": "1990-01-01", "career_start_year": 2009}
    reconciler = _reconciler(service, _Writer(held))

    assert await reconciler.subjects_with_disagreements(ADMIN, "person") == ("p1", "p2")
    assert await reconciler.subjects_with_disagreements(ADMIN, "tag") == ()


async def test_a_wall_has_no_such_question_where_a_record_would_not_either(
    temp_db: Database,
) -> None:
    """None rather than an empty list, and it is the same two cases the narrow read answers None on.

    An empty list is "somebody looked and nothing in the library disagrees", which would narrow a
    wall to nothing and count every row under the second value. None is "there is no such question",
    and the facet is not offered at all: a user that is not an admin cannot settle one of
    these, and no stash-box has ever heard of a Photo Set.
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"})
    reconciler = _reconciler(service, _Writer({"birth_date": "1990-01-01"}))

    guest = Viewer(id="guest", role=Role.GUEST)
    assert await reconciler.subjects_with_disagreements(guest, "person") is None
    assert await reconciler.subjects_with_disagreements(ADMIN, "photo_set") is None


async def test_there_is_no_question_to_ask_about_some_things(temp_db: Database) -> None:
    """None rather than nought, three times over, and the strip draws no mark for any of them.

    A kind no stash-box has ever heard of; a record this user may not be shown, which must answer
    the same way an id that was never minted does; and a user that is not an admin, because
    every control that settles one of these is behind that door.
    """
    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"})
    hidden = _Access({"p1"})
    writer = _Writer({"birth_date": "1990-01-01"})

    assert await _reconciler(service, writer).for_subject(ADMIN, "photo_set", "s1") is None
    assert await _reconciler(service, writer, hidden).for_subject(ADMIN, "person", "p1") is None
    guest = Viewer(id="guest", role=Role.GUEST)
    assert await _reconciler(service, writer).for_subject(guest, "person", "p1") is None
    assert await _reconciler(service, writer).disagreement_count(guest, "person", "p1") is None


# --- a FILE, whose link is the answer it was agreed to -----------------------------------------------


class _FileWriter(_Writer):
    """A file, as far as this file is concerned."""

    subject = Subject.ASSET


class _Where:
    filename = "one.mp4"


class _FileAccess(_Access):
    """Adds the two reads a file is put to: whether it may be OPENED, and where it sits."""

    async def open_asset(self, viewer: Viewer, local_id: str) -> object | None:
        _ = viewer
        return None if local_id in self.hidden else object()

    async def locations(self, viewer: Viewer, local_id: str) -> list[_Where]:
        _ = (viewer, local_id)
        return [_Where()]


async def _an_agreed_file_answer(
    temp_db: Database, box_id: str, fields: Mapping[str, object]
) -> None:
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a1', 'a1', 'video', 0)"
    )
    record = FoundRecord(
        source_id=box_id,
        remote_id="r1",
        subject=Subject.ASSET,
        name="What They Call It",
        fields=dict(fields),
        confidence=1.0,
    )
    await temp_db.execute(
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at)"
        " VALUES ('a1', ?, 'r1', ?, 'certain', 'applied', 1)",
        (box_id, as_json([record])),
    )


async def test_a_file_disagrees_through_the_answer_it_was_agreed_to(temp_db: Database) -> None:
    """The same rule a person's page reads, over the file's agreed answer, named by the file's own
    filename, not by the box's title, which is the value in dispute. A file that may not be OPENED
    (a placeholder is not something to act on) has no question, and the library-wide survey does
    not cover files: that is one plan per matched file, for a kind no wall has a facet for."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await _an_agreed_file_answer(
        temp_db, box, {"title": "What They Call It", "site": "Northlight Media"}
    )
    # A SITE the box disagrees about is not offered: taking it is a filing decision the writer
    # cannot honour with a plain write. See `Reconciler.for_subject`.
    writer = _FileWriter({"title": "What I Called It", "site": "Northlight Group"})

    (row,) = (
        await _reconciler(service, writer, _FileAccess()).for_subject(ADMIN, "asset", "a1") or []
    )

    assert (row.subject, row.name, row.key, row.mine, row.theirs) == (
        Subject.ASSET,
        "one.mp4",
        "title",
        "What I Called It",
        "What They Call It",
    )
    hidden = _FileAccess({"a1"})
    assert await _reconciler(service, writer, hidden).for_subject(ADMIN, "asset", "a1") is None
    reconciler = _reconciler(service, writer, _FileAccess())
    assert await reconciler.subjects_with_disagreements(ADMIN, "asset") is None


async def test_taking_a_boxs_title_for_a_file_writes_it_and_no_enrichment_run(
    temp_db: Database,
) -> None:
    """A file's History draws the box's line off its LATEST run, at the moment the answer was
    agreed, so a run written for one field taken by hand would rewrite that line to say the box
    filled in only the title. The receipt the route writes is the one line; this writes the field
    and no run."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await _an_agreed_file_answer(temp_db, box, {"title": "What They Call It"})
    writer = _FileWriter({"title": "What I Called It"})
    reconciler = _reconciler(service, writer, _FileAccess())
    (row,) = await reconciler.for_subject(ADMIN, "asset", "a1") or []

    assert await reconciler.settle(ADMIN, row, take_theirs=True)

    assert writer.written == [("a1", {"title": "What They Call It"})]
    assert await temp_db.fetch_all("SELECT 1 FROM enrichment_runs WHERE subject = 'asset'") == []


# --- refusals the undo and the survey owe --------------------------------------------------------


async def test_putting_back_an_answer_about_a_record_this_account_may_not_see_is_refused(
    temp_db: Database,
) -> None:
    service = await _service(temp_db)
    box = await _a_box(service)
    reconciler = _reconciler(service, _Writer(), _Access(hidden={"p1"}))

    assert (
        await reconciler.put_back_kept(
            ADMIN, Subject.PERSON, "p1", box, "birth_date", ('"1990-01-01"', '"1991-02-02"')
        )
        is False
    )
    assert await service.kept_answers() == {}, "nothing was written back"


async def test_an_undo_its_own_field_cannot_be_put_back_for_leaves_the_boxes_it_set_aside(
    temp_db: Database,
) -> None:
    """One press, one undo: where the field cannot be put back (here the record is one this
    account may not be shown), the boxes the same press set aside are not touched either."""
    service = await _service(temp_db)
    box = await _a_box(service)
    writer = _Writer({"birth_date": "1991-02-02"})
    receipts = ReconcileReceipts(_reconciler(service, writer, _Access(hidden={"p1"})))

    undone = await receipts.reverse(
        ADMIN, "r1", _receipt(set_aside=[{"box_id": box, "was": ['"x"', '"y"']}])
    )

    assert undone is False
    assert writer.written == []
    assert await service.kept_answers() == {}, "the set-aside box was not put back"


async def test_a_record_kept_local_has_nothing_to_settle_whatever_its_links_say(
    temp_db: Database,
) -> None:
    """Asked about and nothing to settle: no box answer may land on a record kept local, so it has
    no disagreement either: an empty list, not the no-question None."""
    from sift.kernel.access.catalog import set_kept_local_on

    service = await _service(temp_db)
    box = await _a_box(service)
    await _a_person_link(temp_db, "p1", box, {"birth_date": "1991-02-02"})
    async with temp_db.write() as connection:
        await set_kept_local_on(connection, "person", "p1", True)
    reconciler = _reconciler(service, _Writer({"birth_date": "1990-01-01"}))

    assert await reconciler.for_subject(ADMIN, "person", "p1") == []
