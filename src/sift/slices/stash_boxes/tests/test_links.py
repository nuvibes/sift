# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agreeing that a stash-box entry is one of this library's subjects: a link is kept until taken
back, and is never written from a stale answer, an unreachable box, or a subject with no table."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from sift.kernel.access import Role, Viewer
from sift.kernel.access.catalog import MADE_BY_A_PERSON, ensure_site, record_enrichment
from sift.kernel.access.history import Actor
from sift.kernel.db import Database
from sift.kernel.records import FoundRecord, Subject, fields_filled
from sift.kernel.secret_store import SecretStore
from sift.slices.stash_boxes.adapter import ANSWER_SHAPE, Box, StashBoxUnreachable, as_json
from sift.slices.stash_boxes.queue import (
    LINKED,
    UNDECIDED,
    LinkedQueue,
    UndecidedQueue,
)
from sift.slices.stash_boxes.service import StashBoxService

A_KEY = b"0" * 32


def _found(name: str, *, subject: Subject = Subject.PERSON, **fields: object) -> FoundRecord:
    return FoundRecord(
        source_id="box",
        remote_id="remote-1",
        subject=subject,
        name=name,
        fields=fields,
        confidence=1.0,
    )


class _Answers:
    """Answers a by-id question with whatever the test put in, and counts every question.

    The state is named `*_record` and the methods are named as the service calls them. One name
    cannot be both a method and the value it returns, and a double where it tries to be is a double
    that fails in a way that says nothing about the code under test.
    """

    def __init__(self) -> None:
        self.person_record: FoundRecord | None = _found("Their Name", birth_date="1991-07-09")
        self.site_record: FoundRecord | None = _found("A Site", subject=Subject.SITE)
        self.tag_record: FoundRecord | None = _found("A Tag", subject=Subject.TAG)
        self.asked = 0
        self.refuse: str | None = None

    def _answer(self, held: FoundRecord | None) -> FoundRecord | None:
        self.asked += 1
        if self.refuse:
            raise StashBoxUnreachable(self.refuse)
        return held

    async def search(self, box: Box, term: str) -> list[FoundRecord]:
        return []

    async def search_sites(self, box: Box, term: str) -> list[FoundRecord]:
        return []

    async def search_tags(self, box: Box, term: str) -> list[FoundRecord]:
        return []

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        return []

    async def person(self, box: Box, remote_id: str) -> FoundRecord | None:
        return self._answer(self.person_record)

    async def site(self, box: Box, remote_id: str) -> FoundRecord | None:
        return self._answer(self.site_record)

    async def tag(self, box: Box, remote_id: str) -> FoundRecord | None:
        return self._answer(self.tag_record)


class _NoAccess:
    """The board hands every panel the access repository; `available` never asks it."""


class _Sees:
    """An access repository that shows these subjects to everybody and hides every other."""

    def __init__(self, *shown: str) -> None:
        self._shown = set(shown)

    async def visible_people(self, _viewer: Viewer, ids: list[str]) -> dict[str, object]:
        return {one: SimpleNamespace(name=one) for one in ids if one in self._shown}

    visible_sites = visible_people
    visible_tags = visible_people


ADMIN = Viewer(id="admin", role=Role.ADMIN)

#: What storage called a Site before catalog version 50, bound once for the migration test.
_THE_OLD_WORD = "platform"  # the old word, spelled once


@pytest.fixture
async def adapter() -> _Answers:
    return _Answers()


@pytest.fixture
async def service(temp_db: Database, adapter: _Answers) -> StashBoxService:
    await temp_db.initialize_schema()
    return StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]


async def _a_box(service: StashBoxService) -> str:
    return await service.add(
        name="StashDB",
        endpoint="https://stashdb.example/graphql",
        api_key="a-real-key",
        master_key=A_KEY,
    )


async def _a_person(temp_db: Database, person_id: str = "person-1") -> str:
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, "Somebody")
    )
    return person_id


async def test_a_link_keeps_the_whole_record_and_not_just_the_id(
    service: StashBoxService, temp_db: Database
) -> None:
    """The point of keeping the payload: a field Sift has no column for is still readable."""
    box = await _a_box(service)
    person = await _a_person(temp_db)

    held = await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)

    assert held is not None
    assert held.record.fields["birth_date"] == "1991-07-09"
    assert [one.record.name for one in await service.links_of(Subject.PERSON, person)] == [
        "Their Name"
    ]


async def _a_creators_box(service: StashBoxService) -> str:
    """A box whose studios ARE people, which Sift decides from the address, never from a switch.

    The real host, because that is what `known_boxes` matches on and a made-up one would be filed
    under the ordinary rule and prove the opposite of what this is for.
    """
    return await service.add(
        name="PMVStash",
        endpoint="https://pmvstash.org/graphql",
        api_key="a-real-key",
        master_key=A_KEY,
    )


async def _is_creator(temp_db: Database, person_id: str) -> bool:
    row = await temp_db.fetch_one("SELECT pmv_creator FROM people WHERE id = ?", (person_id,))
    assert row is not None
    return bool(row["pmv_creator"])


async def test_a_box_that_files_its_creators_as_people_marks_the_person_it_is_linked_to(
    service: StashBoxService, temp_db: Database
) -> None:
    """The PMV-creator flag's automatic half, and the one place the evidence for it exists.

    The flag is decided by WHICH box answered rather than by anything in the answer, so no field of
    the record could ever carry it, and this is the one path every way of coming to know a creator
    passes through, including the enrichment job the confirm screen queues for the rows it invented.
    """
    box = await _a_creators_box(service)
    person = await _a_person(temp_db)

    assert not await _is_creator(temp_db, person)
    assert await service.link(Subject.PERSON, person, box, "remote-1", A_KEY) is not None
    assert await _is_creator(temp_db, person)


async def test_an_ordinary_box_marks_nobody(service: StashBoxService, temp_db: Database) -> None:
    """The other half, and the one that says the mark means something.

    A performer linked to a box that keeps its studios as Sites is a performer. A rule that
    marked everybody a stash-box has ever heard of would put the badge on most of the library.
    """
    box = await _a_box(service)
    person = await _a_person(temp_db)

    assert await service.link(Subject.PERSON, person, box, "remote-1", A_KEY) is not None
    assert not await _is_creator(temp_db, person)


async def test_the_mark_is_not_taken_back_by_a_later_box(
    service: StashBoxService, temp_db: Database
) -> None:
    """Set and never cleared, which is what `mark_pmv_creator` promises and why it has no opposite.

    The mark is a claim about EVIDENCE (a box that keeps its creators as people has an entry for
    them), and evidence does not go away because a second box has never heard of them. Taking it
    off is somebody's decision, made on the record form.
    """
    creators = await _a_creators_box(service)
    ordinary = await _a_box(service)
    person = await _a_person(temp_db)

    await service.link(Subject.PERSON, person, creators, "remote-1", A_KEY)
    await service.link(Subject.PERSON, person, ordinary, "remote-1", A_KEY)

    assert await _is_creator(temp_db, person)


async def test_a_site_linked_to_such_a_box_marks_nobody(
    service: StashBoxService, temp_db: Database
) -> None:
    """Only a PERSON is marked. A box that files its creators as people still has tags, and the
    subject being linked is what decides, not the box on its own."""
    box = await _a_creators_box(service)
    person = await _a_person(temp_db)
    site = await ensure_site(temp_db, "A Site", made=MADE_BY_A_PERSON)

    assert await service.link(Subject.SITE, site, box, "remote-1", A_KEY) is not None
    assert not await _is_creator(temp_db, person)


async def test_the_record_is_fetched_fresh_rather_than_taken_from_a_search_answer(
    service: StashBoxService, temp_db: Database, adapter: _Answers
) -> None:
    """A search answer is a match on a name and may be a month old.

    A link is somebody saying "this is them", and what is worth keeping under that statement is
    what the service says about them NOW, so the by-id read happens even though a search for the
    same person has just been on screen.
    """
    box = await _a_box(service)
    person = await _a_person(temp_db)

    await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)

    assert adapter.asked == 1


async def test_an_unreachable_box_writes_no_link_at_all(
    service: StashBoxService, temp_db: Database, adapter: _Answers
) -> None:
    """A link written from nothing is worse than no link: it claims a service said something.

    And the refusal carries the box's own sentence out, rather than collapsing into the same None
    as "the box does not know that id", so the screen can say which it was.
    """
    box = await _a_box(service)
    person = await _a_person(temp_db)
    adapter.refuse = "It did not answer."

    with pytest.raises(StashBoxUnreachable, match="It did not answer"):
        await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)
    assert await service.links_of(Subject.PERSON, person) == []


async def test_a_box_that_does_not_know_the_id_writes_no_link(
    service: StashBoxService, temp_db: Database, adapter: _Answers
) -> None:
    box = await _a_box(service)
    person = await _a_person(temp_db)
    adapter.person_record = None

    assert await service.link(Subject.PERSON, person, box, "gone", A_KEY) is None
    assert await service.links_of(Subject.PERSON, person) == []


async def test_a_keyed_box_is_never_asked_without_its_key(
    service: StashBoxService, temp_db: Database, adapter: _Answers
) -> None:
    """No master key is a session resumed after a restart, not a reason to ask unauthenticated.

    An unauthenticated query answers 200 with an `errors` array, which reads as "that entry is not
    there": the one wrong answer nobody would go looking for.
    """
    box = await _a_box(service)
    person = await _a_person(temp_db)

    with pytest.raises(StashBoxUnreachable, match="could not be asked"):
        await service.link(Subject.PERSON, person, box, "remote-1", None)
    assert adapter.asked == 0


async def test_linking_the_same_subject_twice_replaces_rather_than_doubling(
    service: StashBoxService, temp_db: Database
) -> None:
    """Saying it again is the same statement, not a second one."""
    box = await _a_box(service)
    person = await _a_person(temp_db)

    await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)
    await service.link(Subject.PERSON, person, box, "remote-2", A_KEY)

    links = await service.links_of(Subject.PERSON, person)
    assert len(links) == 1
    assert links[0].remote_id == "remote-1", "the id kept is the one the SERVICE answered with"


async def test_a_refresh_asks_again_under_the_id_already_agreed(
    service: StashBoxService, temp_db: Database, adapter: _Answers
) -> None:
    box = await _a_box(service)
    person = await _a_person(temp_db)
    await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)
    adapter.person_record = _found("A Newer Name", birth_date="1991-07-09")

    held = await service.refresh(Subject.PERSON, person, box, A_KEY)

    assert held is not None
    assert held.record.name == "A Newer Name"


async def test_a_refresh_of_something_never_linked_is_not_a_link(
    service: StashBoxService, temp_db: Database, adapter: _Answers
) -> None:
    """Otherwise refresh would be a second, quieter way to create one."""
    box = await _a_box(service)
    person = await _a_person(temp_db)

    assert await service.refresh(Subject.PERSON, person, box, A_KEY) is None
    assert adapter.asked == 0


async def test_unlinking_forgets_the_link_and_says_whether_there_was_one(
    service: StashBoxService, temp_db: Database
) -> None:
    box = await _a_box(service)
    person = await _a_person(temp_db)
    await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)

    assert await service.forget_link(Subject.PERSON, person, box) is True
    assert await service.forget_link(Subject.PERSON, person, box) is False
    assert await service.links_of(Subject.PERSON, person) == []


async def test_deleting_the_person_takes_the_link_with_them(
    service: StashBoxService, temp_db: Database
) -> None:
    """The whole reason there are three tables rather than one with a `subject` word in it.

    A foreign key cannot point at "whichever kind of thing this is", so a shared table could only
    be kept tidy by remembering to sweep it from a slice that may not import this one. The cascade
    is what makes remembering unnecessary.
    """
    box = await _a_box(service)
    person = await _a_person(temp_db)
    await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)

    await temp_db.execute("DELETE FROM people WHERE id = ?", (person,))

    assert await service.links_of(Subject.PERSON, person) == []


async def test_deleting_the_box_takes_its_links_with_it(
    service: StashBoxService, temp_db: Database
) -> None:
    """A link is a statement about one service, and it means nothing once that one is gone."""
    box = await _a_box(service)
    person = await _a_person(temp_db)
    await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)

    await service.forget(box)

    assert await service.links_of(Subject.PERSON, person) == []


async def test_a_file_cannot_be_linked_by_name(service: StashBoxService, temp_db: Database) -> None:
    """What a stash-box knows about a FILE is answered by fingerprints, which is a different pass.

    Refused at the door rather than writing nowhere: a subject with no table would otherwise take a
    link, report success and have kept nothing.
    """
    box = await _a_box(service)

    with pytest.raises(ValueError, match="asset"):
        await service.link(Subject.ASSET, "asset-1", box, "remote-1", A_KEY)
    with pytest.raises(ValueError, match="asset"):
        await service.search("a name", A_KEY, subject=Subject.ASSET)


async def test_the_subject_is_part_of_the_cache_key(
    service: StashBoxService, temp_db: Database
) -> None:
    """Otherwise a search for a SITE called Northlight is answered out of a search for a PERSON."""
    box = await _a_box(service)

    await service.search("northlight", A_KEY, subject=Subject.PERSON)
    await service.search("northlight", A_KEY, subject=Subject.SITE)

    rows = await temp_db.fetch_all(
        "SELECT kind FROM stash_box_answers WHERE box_id = ? ORDER BY kind", (box,)
    )
    assert [str(row["kind"]) for row in rows] == ["search:person", "search:site"]


# --- the three kinds of subject, and the one that is not a subject ---------------------------------


async def test_a_site_and_a_tag_are_linked_the_same_way_a_person_is(
    service: StashBoxService, temp_db: Database
) -> None:
    """One module for all three, which is what makes ONE confirm screen possible, and the confirm
    screen is the part that would otherwise have been written three times."""
    box = await _a_box(service)
    await temp_db.execute("INSERT INTO sites (id, name) VALUES ('s1', 'A Studio')")
    await temp_db.execute("INSERT INTO tags (id, name, created_at) VALUES ('t1', 'beach', 0)")

    for subject, local_id in ((Subject.SITE, "s1"), (Subject.TAG, "t1")):
        held = await service.link(subject, local_id, box, "r1", A_KEY)
        assert held is not None, subject
        assert [one.source_id for one in await service.links_of(subject, local_id)] == [box]
        assert await service.forget_link(subject, local_id, box) is True


async def test_a_file_has_no_link_table_and_every_call_says_so(
    service: StashBoxService,
) -> None:
    """A file is recognised by its fingerprint, never looked up by name, so there is no such thing
    as a link between a stash-box and one. Reading and forgetting answer with nothing; writing
    raises, because a caller asking to write one has made a mistake worth hearing about."""
    assert await service.links_of(Subject.ASSET, "a1") == []
    assert await service.forget_link(Subject.ASSET, "a1", "box") is False
    assert await service.refresh(Subject.ASSET, "a1", "box", A_KEY) is None


async def test_a_kept_record_that_will_not_read_is_skipped_rather_than_emptying_the_screen(
    service: StashBoxService, temp_db: Database
) -> None:
    """A record can outlive the version that wrote it, and one unreadable row must not empty a
    screen that draws every link in the library."""
    box = await _a_box(service)
    await temp_db.execute("INSERT INTO people (id, name, created_at) VALUES ('p1', 'Jane', 0)")
    await temp_db.execute(
        "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
        " VALUES ('p1', ?, 'r1', '[]', 0)",
        (box,),
    )

    assert await service.links_of(Subject.PERSON, "p1") == []
    assert await service.linked_subjects() == []
    keys = await service.ledger_keys()
    assert (await service.ledger_rows(keys), len(keys)) == ([], 1)
    # A row is a row, readable or not: the ledger is a tab from the first link.
    assert await service.any_link() is True


async def test_the_picture_kept_is_the_one_the_box_named_and_not_one_the_client_sends(
    service: StashBoxService, adapter: _Answers, temp_db: Database
) -> None:
    """The picture kept is the address the link holds from the box, never one the browser sends
    (the browser only ever holds Sift's own proxy address)."""
    adapter.person_record = _found("Their Name", birth_date="1991-07-09")
    object.__setattr__(adapter.person_record, "image_url", "https://cdn.stashdb.example/p/1.jpg")

    box_id = await _a_box(service)
    person_id = await _a_person(temp_db)
    assert await service.link(Subject.PERSON, person_id, box_id, "remote-1", A_KEY) is not None

    kept = [
        one for one in await service.links_of(Subject.PERSON, person_id) if one.source_id == box_id
    ]
    assert kept, "the link this reads from was not written"
    assert kept[0].record.image_url == "https://cdn.stashdb.example/p/1.jpg", (
        "the address kept under the link is not the one the box gave, so a keep would fetch the"
        " wrong thing, or Sift's own proxy address on the wrong host"
    )


async def test_a_cached_search_is_marked_for_certainty_again_on_the_way_out(
    service: StashBoxService, temp_db: Database
) -> None:
    """The mark is not in the cache. A cached row read without it would mark every entry as
    certain (ten "Neve"s for "Neve Arbogast"), and every unattended enrichment would decline the
    name as ambiguous. The cached answer is marked against the term on the way out, as a fresh one is."""
    box = await _a_box(service)
    # The cache is emptied once per reading of the adapter before it is next read; this row is
    # written in the current reading, so the pass is recorded as run first.
    await service.record_catch_up(f"answers_read_as:{ANSWER_SHAPE}")
    records = [
        FoundRecord(source_id=box, remote_id="r1", subject=Subject.PERSON, name="Neve Arbogast"),
        FoundRecord(source_id=box, remote_id="r2", subject=Subject.PERSON, name="Neve Arbor"),
    ]
    await temp_db.execute(
        "INSERT INTO stash_box_answers (box_id, kind, ask, payload, fetched_at)"
        " VALUES (?, 'search:person', 'neve arbogast', ?, ?)",
        (box, as_json(records), int(service._now())),
    )

    (answer,) = await service.search("Neve Arbogast", A_KEY, subject=Subject.PERSON)

    assert answer.fresh is False
    assert [(one.name, one.every_word) for one in answer.records] == [
        ("Neve Arbogast", True),
        ("Neve Arbor", False),
    ]


# --- the two panels the ledger and the undecided list are drawn as -------------------------------
#
# The whole surface the Organize board reaches this feature through.


async def test_the_ledger_panel_is_offered_once_anything_has_been_linked(
    service: StashBoxService, temp_db: Database
) -> None:
    """A library never asked has no ledger worth a tab, and an empty ledger beside a pile that
    exists is the state the pile is trying to reach, so the panel appears on either."""
    queue = LinkedQueue(service, _NoAccess())  # type: ignore[arg-type]
    assert await queue.available() is False

    box = await _a_box(service)
    person = await _a_person(temp_db)
    await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)

    assert await queue.available() is True


async def test_the_ledger_card_counts_only_what_the_viewer_may_see(
    service: StashBoxService, temp_db: Database
) -> None:
    """Counted as the page is: a person a shut vault hides is not in the number, which would
    otherwise say they are there. One scoped read per kind, not one per row."""
    box = await _a_box(service)
    person = await _a_person(temp_db)
    hidden = await _a_person(temp_db, "person-2")
    await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)
    await service.link(Subject.PERSON, hidden, box, "remote-2", A_KEY)

    card = await LinkedQueue(service, _Sees(person)).survey(ADMIN)  # type: ignore[arg-type]

    assert card.name == LINKED
    assert card.count == 1
    assert card.title == "Enriched by a stash-box"


async def test_the_ledger_records_no_decisions_so_it_draws_and_reverses_nothing(
    service: StashBoxService,
) -> None:
    """A RECORD rather than a question. Nothing ever asks it for pictures, and there is nothing to
    put back: the links were agreed to one at a time and come off the record itself the same way.
    Both answers are stated rather than inherited, because the board asks every panel."""
    queue = LinkedQueue(service, _NoAccess())  # type: ignore[arg-type]

    assert await queue.pictures_of(ADMIN, "{}") == ()
    assert await queue.reverse(ADMIN, "receipt", "{}") is False
    assert queue.reversible is False


async def test_the_undecided_panel_counts_the_names_nobody_could_choose_for(
    service: StashBoxService, temp_db: Database
) -> None:
    """One box holding two certain entries for a name is a judgement about which of two people this
    is, and the enrichment declines it onto a list somebody can work from."""
    person = await _a_person(temp_db)
    hidden = await _a_person(temp_db, "person-2")
    queue = UndecidedQueue(service, _Sees(person))  # type: ignore[arg-type]

    await service.note_undecided(Subject.PERSON, person, candidates=2)
    await service.note_undecided(Subject.PERSON, hidden, candidates=2)

    card = await queue.survey(ADMIN)
    assert card.name == UNDECIDED
    assert card.count == 1, "a name the viewer may not see is not counted"
    assert card.title == "Names with more than one entry"
    # The same record, and nothing to draw or put back.
    assert await queue.pictures_of(ADMIN, "{}") == ()
    assert await queue.reverse(ADMIN, "receipt", "{}") is False


async def test_the_undecided_panel_is_offered_on_the_same_terms_as_the_ledger(
    service: StashBoxService, temp_db: Database
) -> None:
    """It sits beside the ledger and appears with it: a name nobody could choose for is only
    reachable on a library that has been asked at all."""
    queue = UndecidedQueue(service, _NoAccess())  # type: ignore[arg-type]
    assert await queue.available() is False

    box = await _a_box(service)
    person = await _a_person(temp_db)
    await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)

    assert await queue.available() is True


async def test_a_box_is_stored_under_the_word_sift_knows_its_address_by(
    service: StashBoxService,
) -> None:
    """The slug, derived from the endpoint and written down beside `sites_are`.

    Stored rather than worked out where it is needed, because SQL has to group and filter by it:
    a facet counting files per box and a `created:` narrowing both read the column. Null for a box
    at an address Sift has never heard of, which is a real answer: a self-hosted box enriches and
    creates like any other, and it is drawn in the ordinary accent under its own name.

    Matched on the exact host, never on a substring, so a mirror on another domain does not inherit
    a service's word, the same rule `sites_are` follows and for the same reason.
    """
    known = await service.add(
        name="Anything At All",
        endpoint="https://www.fansdb.cc/graphql",
        api_key=None,
        master_key=None,
    )
    unknown = await service.add(
        name="Mine", endpoint="https://boxes.example.test/graphql", api_key=None, master_key=None
    )
    nearly = await service.add(
        name="Not Them",
        endpoint="https://notfansdb.example.test/graphql",
        api_key=None,
        master_key=None,
    )

    slugs = {box.id: box.slug for box in await service.boxes()}
    # The NAME is ignored entirely: what decides the word is where the requests go.
    assert slugs[known] == "fansdb"
    assert slugs[unknown] is None
    assert slugs[nearly] is None


async def test_the_box_that_made_a_row_is_read_back_and_forgotten_with_the_box(
    service: StashBoxService, temp_db: Database
) -> None:
    """Who MADE a row, as against who has described it, and what happens when the box goes.

    The column carries no foreign key: the catalog is brought up before this slice's tables exist,
    and SQLite resolves a key's parent when the child is written, so a key here would make every
    write to `people` fail on a database whose stash-box tables are not there yet. What the key
    would have done is done in two other places instead: the read goes through `boxes()`, so a row
    naming a box that is gone cannot be named, and `forget` clears the column so it does not arise.
    """
    box = await _a_box(service)
    person = await _a_person(temp_db)

    # No maker word on the row at all: everybody made before v41 of the catalog. Still the only
    # thing that answers None here.
    assert await service.made_by(Subject.PERSON, person, ADMIN) is None

    await temp_db.execute(
        "UPDATE people SET created_by_box_id = ?, created_by_kind = 'box',"
        " created_by_via = 'stash' WHERE id = ?",
        (box, person),
    )
    made = await service.made_by(Subject.PERSON, person, ADMIN)
    assert made is not None
    assert (made.actor, made.via) == (Actor.STASH_BOX, "stash")
    assert (made.box_id, made.box_name) == (box, "StashDB")

    # A file is not a subject that can be made by a box, so there is nothing to answer about one.
    assert await service.made_by(Subject.ASSET, person, ADMIN) is None

    # The box is gone. The row still says a box made it and still says HOW. What cannot be said
    # any more is WHICH, so the box half is empty and the actor is a stash-box with no name.
    await service.forget(box)
    after = await service.made_by(Subject.PERSON, person, ADMIN)
    assert after is not None
    assert (after.actor, after.via, after.box_id) == (Actor.STASH_BOX, "stash", None)
    row = await temp_db.fetch_one("SELECT created_by_box_id FROM people WHERE id = ?", (person,))
    assert row is not None
    assert row["created_by_box_id"] is None


async def test_a_row_sift_made_says_which_pass_and_one_an_account_made_says_who(
    service: StashBoxService, temp_db: Database
) -> None:
    """The three makers that are not a box, each reaching a screen as a word.

    Each is read back through the one function that decides who did a thing, so the word on a
    maker line and the word on the same row's history cannot come apart, and the user who is
    not the asker is `another_user` with no name, which is the withholding the sharing screens
    already make.
    """
    by_sift = await _a_person(temp_db, "person-sift")
    await temp_db.execute(
        "UPDATE people SET created_by_kind = 'sift', created_by_via = 'folder' WHERE id = ?",
        (by_sift,),
    )
    made = await service.made_by(Subject.PERSON, by_sift, ADMIN)
    assert made is not None
    assert (made.actor, made.via, made.box_id) == (Actor.SIFT, "folder", None)

    mine = await _a_person(temp_db, "person-mine")
    await temp_db.execute(
        "UPDATE people SET created_by_kind = 'user', created_by_user_id = ? WHERE id = ?",
        (ADMIN.id, mine),
    )
    yours = await service.made_by(Subject.PERSON, mine, ADMIN)
    assert yours is not None
    assert (yours.actor, yours.via) == (Actor.YOU, None)

    theirs = await _a_person(temp_db, "person-theirs")
    await temp_db.execute(
        "UPDATE people SET created_by_kind = 'user', created_by_user_id = ? WHERE id = ?",
        ("somebody-else", theirs),
    )
    other = await service.made_by(Subject.PERSON, theirs, ADMIN)
    assert other is not None
    assert (other.actor, other.via) == (Actor.ANOTHER_USER, None)


async def test_the_ledger_row_says_what_the_last_run_filled_in(
    service: StashBoxService, temp_db: Database
) -> None:
    """The tab says which box, when, and what came of it.

    The last run, and the ordering is the point rather than the reading: every ask is kept, so a
    thing enriched twice has two rows and the row on screen must be the later one. The words
    themselves are `fields_filled`'s, which the record's own History already reads: the ledger
    stores the stored list and nothing else, so the two cannot come to different accounts of one
    link.
    """
    box = await _a_box(service)
    person = await _a_person(temp_db)
    await service.link(Subject.PERSON, person, box, "remote-1", A_KEY)

    page = await service.ledger_rows(await service.ledger_keys())
    assert [one.applied for one in page] == [None], "a bare link filled nothing in"

    await record_enrichment(
        temp_db, "person", person, box, automatic=True, at=10, applied=["birth_date"]
    )
    await record_enrichment(
        temp_db, "person", person, box, automatic=True, at=20, applied={"links": 3}
    )

    page = await service.ledger_rows(await service.ledger_keys())
    assert [one.applied for one in page] == ['{"links": 3}']
    assert fields_filled(Subject.PERSON, page[0].applied) == ("3 links",)
