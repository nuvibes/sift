# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configuring boxes, and the cache that keeps Sift from asking the same question twice.

The adapter is a stand-in here: what is being tested is which questions reach it and which do not,
which is the half that decides how often a third-party service hears from this installation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from sift.kernel.db import Database
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.secret_store import SecretStore
from sift.slices.stash_boxes import configured as configured_module
from sift.slices.stash_boxes.adapter import Box, StashBoxUnreachable
from sift.slices.stash_boxes.service import CACHE_DAYS, StashBoxService

A_KEY = b"0" * 32


class _Adapter:
    """Counts what it was asked, and can be told to refuse."""

    def __init__(self, records: list[FoundRecord] | None = None) -> None:
        self.records = records if records is not None else []
        self.asked = 0
        self.refuse: str | None = None
        self.keys_seen: list[str | None] = []

    async def search(self, box: Box, term: str) -> list[FoundRecord]:
        self.asked += 1
        self.keys_seen.append(box.api_key)
        if self.refuse:
            raise StashBoxUnreachable(self.refuse)
        return self.records

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        self.asked += 1
        if self.refuse:
            raise StashBoxUnreachable(self.refuse)
        return self.records


def _found(name: str) -> FoundRecord:
    return FoundRecord(
        source_id="box", remote_id="1", subject=Subject.PERSON, name=name, confidence=0.5
    )


@pytest.fixture
async def service(temp_db: Database) -> StashBoxService:
    await temp_db.initialize_schema()
    return StashBoxService(temp_db, SecretStore(temp_db), _Adapter())  # type: ignore[arg-type]


async def _a_box(service: StashBoxService, **kwargs: object) -> str:
    return await service.add(
        name=str(kwargs.get("name", "StashDB")),
        endpoint=str(kwargs.get("endpoint", "https://stashdb.example/graphql")),
        api_key=kwargs.get("api_key", "a-real-key"),  # type: ignore[arg-type]
        master_key=A_KEY,
    )


async def test_a_key_is_sealed_and_never_comes_back_out(service: StashBoxService) -> None:
    """`has_key` and nothing else. There is no shape in this API that carries a key outward."""
    await _a_box(service)

    boxes = await service.boxes()

    assert [one.has_key for one in boxes] == [True]
    # Every field of what a screen is handed, checked against the key itself. `has_key` is a
    # boolean; nothing here is the key, and there is no shape in this API that could carry it.
    assert "a-real-key" not in repr(boxes)


async def test_a_box_added_with_no_key_says_so_rather_than_pretending(
    service: StashBoxService,
) -> None:
    await _a_box(service, api_key=None)

    assert [one.has_key for one in await service.boxes()] == [False]


async def test_the_second_identical_question_is_not_asked_again(
    service: StashBoxService, temp_db: Database
) -> None:
    """The cache is why. Asking a public service the same thing twice in an afternoon is exactly
    the behaviour the throttle exists to prevent Sift from having."""
    adapter = _Adapter([_found("A Name")])
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await _a_box(service)

    first = await service.search("a name", A_KEY)
    second = await service.search("A NAME", A_KEY)

    assert adapter.asked == 1
    assert first[0].fresh is True
    assert second[0].fresh is False
    assert [one.name for one in second[0].records] == ["A Name"]


async def test_an_answer_older_than_the_window_is_asked_again(
    service: StashBoxService, temp_db: Database
) -> None:
    adapter = _Adapter([_found("A Name")])
    now = [1_000_000.0]
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter, clock=lambda: now[0])  # type: ignore[arg-type]
    await _a_box(service)

    await service.search("a name", A_KEY)
    now[0] += (CACHE_DAYS + 1) * 86400
    await service.search("a name", A_KEY)

    assert adapter.asked == 2


async def test_an_answer_read_by_an_older_adapter_is_asked_again_once(
    temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache keeps records already read, so a new reading must not be served the old one.

    Once per reading: a second service over the same library keeps what the new reading cached.
    """
    await temp_db.initialize_schema()
    adapter = _Adapter([_found("A Name")])
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await _a_box(service)
    await service.search("a name", A_KEY)

    monkeypatch.setattr(configured_module, "ANSWER_SHAPE", "read-another-way")
    later = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await later.search("a name", A_KEY)
    again = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await again.search("a name", A_KEY)

    assert adapter.asked == 2


async def test_a_switched_off_box_is_not_asked_at_all(
    service: StashBoxService, temp_db: Database
) -> None:
    """Off is absent, not broken. Nothing asks it and nothing complains about it."""
    adapter = _Adapter([_found("A Name")])
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    box_id = await _a_box(service)
    await service.set_enabled(box_id, False)

    assert await service.search("a name", A_KEY) == []
    assert adapter.asked == 0


async def test_a_press_asks_anyone_only_while_a_box_it_would_ask_is_on(
    service: StashBoxService,
) -> None:
    """Run now with every stash-box turned off would walk the library asking nobody, so the press
    is refused while this answers False."""
    assert not await service.asks_anyone()
    box_id = await _a_box(service)
    assert await service.asks_anyone()
    # A word naming no switched-on box asks nobody, rather than falling back to every box.
    assert not await service.asks_anyone("fansdb")
    # And the dry run names exactly the boxes the press would ask.
    assert await service.names_asked() == ["StashDB"]
    assert await service.names_asked("fansdb") == []
    await service.set_enabled(box_id, False)
    assert not await service.asks_anyone()
    assert await service.names_asked() == []


async def test_a_box_that_cannot_be_reached_answers_with_its_sentence_not_an_exception(
    service: StashBoxService, temp_db: Database
) -> None:
    """One box failing must not hide what the others said, so the failure travels as data."""
    adapter = _Adapter()
    adapter.refuse = "StashDB did not answer in time."
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await _a_box(service)

    answers = await service.search("a name", A_KEY)

    assert answers[0].problem == "StashDB did not answer in time."
    assert answers[0].records == []


async def test_a_fresh_answer_is_cached_without_ringing_the_library(
    service: StashBoxService, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No screen draws the answer cache, so keeping an answer tells no screen to read again."""
    rung: list[object] = []
    monkeypatch.setattr(configured_module, "announce_now", lambda _who, about: rung.append(about))
    adapter = _Adapter([_found("A Name")])
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await _a_box(service)
    rung.clear()

    await service.search("a name", A_KEY)
    again = await service.search("a name", A_KEY)

    assert adapter.asked == 1, "the answer was not kept"
    assert [one.name for one in again[0].records] == ["A Name"]
    assert rung == []


async def test_a_failure_is_not_cached(service: StashBoxService, temp_db: Database) -> None:
    """A cached failure is a box that stays broken until somebody notices a stale row."""
    adapter = _Adapter([_found("A Name")])
    adapter.refuse = "nope"
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await _a_box(service)

    await service.search("a name", A_KEY)
    adapter.refuse = None
    again = await service.search("a name", A_KEY)

    assert [one.name for one in again[0].records] == ["A Name"]


async def test_with_no_master_key_the_box_is_absent_rather_than_asked_unkeyed(
    service: StashBoxService, temp_db: Database
) -> None:
    """A session resumed from a cookie after a restart has no master key yet. Asking anyway would
    send an unauthenticated query and read the refusal as "this person is not in there"."""
    adapter = _Adapter([_found("A Name")])
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await _a_box(service)

    answers = await service.search("a name", None)

    assert adapter.asked == 0
    assert answers[0].problem is not None


async def test_replacing_a_key_throws_away_what_the_old_one_was_told(
    service: StashBoxService, temp_db: Database
) -> None:
    """A new key can mean a different account, and an answer given to the old one is not evidence
    about the new one."""
    adapter = _Adapter([_found("A Name")])
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    box_id = await _a_box(service)
    await service.search("a name", A_KEY)

    await service.set_key(box_id, "a-different-key", A_KEY)
    await service.search("a name", A_KEY)

    assert adapter.asked == 2
    assert adapter.keys_seen[-1] == "a-different-key"


async def test_forgetting_a_box_takes_what_it_said_with_it(
    service: StashBoxService, temp_db: Database
) -> None:
    """A cached answer outliving its source is a fact with no provenance."""
    adapter = _Adapter([_found("A Name")])
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    box_id = await _a_box(service)
    await service.search("a name", A_KEY)

    await service.forget(box_id)

    rows = await temp_db.fetch_all("SELECT * FROM stash_box_answers")
    assert rows == []


async def test_the_connection_check_never_asks_for_the_key_back(
    service: StashBoxService, temp_db: Database
) -> None:
    """`me { api_key }` returns the key itself, so the check is a search for nothing instead."""
    adapter = _Adapter()
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    box_id = await _a_box(service)

    assert await service.check(box_id, A_KEY) is None
    assert adapter.asked == 1


async def test_the_check_reports_a_refusal_as_a_sentence(
    service: StashBoxService, temp_db: Database
) -> None:
    adapter = _Adapter()
    adapter.refuse = "StashDB refused the question: not authorized."
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    box_id = await _a_box(service)

    assert await service.check(box_id, A_KEY) == "StashDB refused the question: not authorized."


async def test_checking_a_box_that_is_not_there_says_so(service: StashBoxService) -> None:
    assert await service.check("01HX0000000000000000000009", A_KEY) is not None


async def test_a_key_that_will_not_unseal_is_a_box_that_cannot_be_asked(
    temp_db: Database,
) -> None:
    """A sealed value that does not open is not a box with no key.

    Both refuse, and both are better than sending an unauthenticated query, whose refusal comes
    back as a 200 with an `errors` array, which reads as "this person is not in there". But they
    are not the same SENTENCE, and that is what this holds. A box with a key that will not open
    saying "this one has no key" would be untrue on a screen showing a green "Key saved" beside
    it, and would send somebody looking for a key they never lost.
    """
    await temp_db.initialize_schema()
    adapter = _Adapter([_found("A Name")])
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await _a_box(service)

    answers = await service.search("a name", b"1" * 32)

    assert [one.problem for one in answers] == [
        "This stash-box's key is locked because Sift restarted. Enter your password under "
        "Unlock in Settings > Stash-boxes."
    ]
    assert adapter.asked == 0


def test_an_answer_naming_no_site_is_handed_back_as_it_stands() -> None:
    """A kept answer is read the way the box reads NOW, and for a box whose studios are people
    that means the site comes out of it.

    Both halves, because they are different acts. An answer that names a site is REBUILT without
    it. One that names none is handed straight back (the same object, not a copy of it), and
    that is what keeps every match read off a creators box from rebuilding a record with nothing
    to change in it.
    """
    from sift.slices.stash_boxes.matches import _creator_not_a_site

    plain = FoundRecord(
        source_id="box",
        remote_id="1",
        subject=Subject.ASSET,
        name="A Clip",
        fields={"details": "What it is about."},
    )
    named = FoundRecord(
        source_id="box",
        remote_id="1",
        subject=Subject.ASSET,
        name="A Clip",
        fields={"details": "What it is about.", "site": "Northlight Media"},
    )

    assert _creator_not_a_site(plain) is plain

    read = _creator_not_a_site(named)
    assert read is not named
    assert "site" not in read.fields


def test_the_site_an_old_answer_names_becomes_the_creator_at_the_head_of_the_people() -> None:
    """The older shape: the creator under `site`.

    Taking the name out is half the reading. Today's mapper names that studio as the creator and
    leads `people` with them, so a kept answer in the older shape has to come out of here saying
    both. Otherwise the one name the box is surest about is dropped, the writer never resolves a
    row for it, and `_mark_creator` has nobody to mark.

    The performers already on the answer keep their order behind the creator, and a creator who is
    ALSO listed among them is named once: two rows under one name is what the writer's own fold is
    for, and handing it a list with a repeat in it would be relying on that fold instead of saying
    the thing.
    """
    from sift.slices.stash_boxes.matches import _creator_not_a_site

    old = FoundRecord(
        source_id="box",
        remote_id="1",
        subject=Subject.ASSET,
        name="A Clip",
        fields={
            "details": "What it is about.",
            "site": "quillmoss",
            "people": ["Orla Tennant", "QuillMoss", "Bryn Calloway"],
        },
    )

    read = _creator_not_a_site(old)

    assert read.fields["creator"] == "quillmoss"
    assert read.fields["people"] == ["quillmoss", "Orla Tennant", "Bryn Calloway"]
    assert "site" not in read.fields

    # An answer naming a studio and nobody else, which is the ordinary shape on this kind of box:
    # the creator is still the whole of the list, so the writer has somebody to resolve.
    alone = replace(old, fields={"site": "quillmoss"})

    assert _creator_not_a_site(alone).fields == {
        "creator": "quillmoss",
        "people": ["quillmoss"],
    }


def test_an_answer_that_says_who_made_it_keeps_what_it_says() -> None:
    """The NEW shape, and the one case where both could be on one payload.

    `creator` said in words is the mapper's own statement and it wins, which is the precedence the
    back-fill over these same rows uses. The site still comes out. A `site` holding nothing at
    all is taken out and promotes nobody, which is the other way this can be handed a name that is
    not one.
    """
    from sift.slices.stash_boxes.matches import _creator_not_a_site

    both = FoundRecord(
        source_id="box",
        remote_id="1",
        subject=Subject.ASSET,
        name="A Clip",
        fields={"site": "Northlight Media", "creator": "quillmoss", "people": ["quillmoss"]},
    )
    blank = FoundRecord(
        source_id="box",
        remote_id="1",
        subject=Subject.ASSET,
        name="A Clip",
        fields={"site": "   ", "people": ["Orla Tennant"]},
    )

    said = _creator_not_a_site(both)
    assert said.fields == {"creator": "quillmoss", "people": ["quillmoss"]}

    nothing = _creator_not_a_site(blank)
    assert nothing.fields == {"people": ["Orla Tennant"]}


# --- the reads and refusals around a pass ---------------------------------------------------------


async def test_a_row_that_is_not_there_is_not_kept_local_and_nothing_is_recorded(
    service: StashBoxService, temp_db: Database
) -> None:
    import sift.slices.workbench.schema  # noqa: F401 (the ledger's table registers itself)
    from sift.kernel.access import Role, Viewer

    await temp_db.initialize_schema()
    admin = Viewer(id="admin", role=Role.ADMIN)

    kept = await service.set_kept_local(admin, Subject.PERSON, "no-such-person", True, name=None)

    assert kept is False
    rows = await temp_db.fetch_all("SELECT id FROM workbench_decisions")
    assert rows == [], "a decision about nothing is not written down"


async def test_a_sweep_that_asked_no_switched_on_box_writes_nothing_down(
    service: StashBoxService, temp_db: Database
) -> None:
    import sift.slices.workbench.schema  # noqa: F401 (the ledger's table registers itself)
    from sift.kernel.ledger import Actor

    await temp_db.initialize_schema()
    await _a_box(service)

    await service.record_asking(actor=Actor.user("admin"), count=3, only="no-such-box")

    assert await temp_db.fetch_all("SELECT id FROM workbench_decisions") == []


async def test_a_list_of_files_all_kept_local_has_nothing_left_to_ask_about(
    service: StashBoxService, temp_db: Database
) -> None:
    from sift.kernel.access.catalog import set_kept_local_on

    await _a_box(service)
    for asset_id in ("a1", "a2"):
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
            (asset_id, asset_id),
        )
        async with temp_db.write() as connection:
            await set_kept_local_on(connection, "asset", asset_id, True)

    assert await service.unasked(["a1", "a2"]) == []


def _kept_before_lists(payload: str) -> str:
    """A kept record as a build before picture lists wrote it: no `pictures` key at all."""
    import json

    records = json.loads(payload)
    for one in records:
        one.pop("pictures", None)
    return json.dumps(records)


async def test_the_people_linked_to_a_box_are_read_oldest_link_first_and_narrowed_to_lists(
    service: StashBoxService, temp_db: Database
) -> None:
    """Everybody with a link and (asked for starter pictures) only the links whose kept record
    carries the whole picture list, which is how a link made before lists existed is told apart:
    its kept record has no `pictures` at all."""
    from sift.slices.stash_boxes.adapter import as_json

    box = await _a_box(service)
    for person, pictures in (
        ("p-late", ("https://b/1",)),
        ("p-early", ()),
        ("p-mid", ("https://b/2",)),
    ):
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person, person)
        )
        record = FoundRecord(
            source_id=box, remote_id=person, subject=Subject.PERSON, name=person, pictures=pictures
        )
        await temp_db.execute(
            "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                person,
                box,
                person,
                as_json([record]) if pictures else _kept_before_lists(as_json([record])),
                {"p-early": 1, "p-mid": 2, "p-late": 3}[person],
            ),
        )

    assert await service.linked_people() == ["p-early", "p-mid", "p-late"]
    assert await service.linked_people(with_picture_lists=True) == ["p-mid", "p-late"]


async def test_a_box_removed_while_a_link_was_being_made_is_said_to_be_gone(
    service: StashBoxService,
) -> None:
    """Not "has no API key": a box with no key is asked without one, so that is never why a box
    cannot be asked. The one other reason than a locked key is that the box is not there."""
    with pytest.raises(StashBoxUnreachable) as refused:
        await service.link(Subject.PERSON, "p1", "01GONEBOX", "r1", A_KEY)

    assert "no longer set up" in str(refused.value)
    assert "API key" not in str(refused.value)


def test_an_old_site_id_filed_under_another_name_links_nobody_as_the_creator() -> None:
    """The id moves to the creator only where it is the creator's: a `site` id kept under some
    other name is not evidence of who made the file, so it goes with the site and nothing more."""
    from sift.slices.stash_boxes.matches import _creator_not_a_site

    old = FoundRecord(
        source_id="box",
        remote_id="1",
        subject=Subject.ASSET,
        name="A Clip",
        fields={"site": "quillmoss"},
        refs={"site": {"Northlight Media": "st-9"}},
    )

    read = _creator_not_a_site(old)

    assert read.fields["creator"] == "quillmoss"
    assert read.refs == {}
