# SPDX-License-Identifier: AGPL-3.0-or-later
"""A decision's line, worded when it is shown, for whoever is reading it.

The area that wrote a decision says what it did; this reader says who did it and what each thing
it names is called now. So every case here is one of those two questions: the doer as this reader
may be told it, and a thing that is still there, renamed, merged away, deleted, or never had a name.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import pytest

# Imported for their side effect: registering the tables the record and the stash-boxes live in,
# so a kernel database has them.
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Viewer
from sift.kernel.access.worded import Line, StoredRow, lines_of, worded_or_stored
from sift.kernel.db import Database
from sift.kernel.sorting import sort_key
from sift.kernel.workbench import DOER, Named, Preview, Recorded, Worded, Workbench
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio

AT = 1_700_000_000

STILL_HERE = "01HX0000000000000000000401"
RENAMED = "01HX0000000000000000000402"
MERGED_AWAY = "01HX0000000000000000000403"
DELETED = "01HX0000000000000000000404"
NAMELESS_GONE = "01HX0000000000000000000405"
# A chain of four merges, every link of it gone: the reader follows as far as it may and names the
# last one it reached.
CHAIN = [f"01HX000000000000000000041{n}" for n in range(5)]
BOX = "01HX0000000000000000000420"
SHOOT = "01HX0000000000000000000421"
FILE = "01HX0000000000000000000422"
GONE_USER = "01HX0000000000000000000423"


@dataclass
class Area:
    """A reverser that words its own decisions from a table of answers, one per receipt."""

    name: str
    answers: Mapping[str, Callable[[Recorded], Worded | None]] = field(default_factory=dict)
    reversible: bool = True

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        return self.answers[recorded.id](recorded)


@dataclass
class Unworded:
    """A reverser that cannot word anything: its receipts keep their stored titles."""

    name: str
    reversible: bool = True

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        return False


async def decide(
    database: Database,
    decision_id: str,
    *,
    queue: str = "words",
    verb: str = "decided",
    actor_kind: str | None = None,
    actor_id: str | None = None,
    user_id: str | None = None,
    object_kind: str | None = None,
    object_id: str | None = None,
    object_name: str | None = None,
    subjects: tuple[tuple[str, str, str | None], ...] = (),
) -> StoredRow:
    """One receipt in the record, with the ledger's columns beside it, as a page reads it back."""
    await database.execute(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at,"
        " verb, actor_kind, actor_id, object_kind, object_id, object_name)"
        " VALUES (?, ?, ?, 'stored title', '', '{}', ?, ?, ?, ?, ?, ?, ?)",
        (
            decision_id,
            queue,
            user_id,
            AT,
            verb,
            actor_kind,
            actor_id,
            object_kind,
            object_id,
            object_name,
        ),
    )
    for kind, subject_id, name in subjects:
        await database.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
            " VALUES (?, ?, ?, ?)",
            (decision_id, kind, subject_id, name),
        )
    return StoredRow(
        id=decision_id,
        queue=queue,
        user_id=user_id,
        title="stored title",
        detail="",
        payload="{}",
        decided_at=AT,
    )


async def person(database: Database, person_id: str, name: str) -> None:
    await database.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (person_id, name, sort_key(name), AT),
    )


async def merge(database: Database, decision_id: str, gone: str, into: str, name: str) -> None:
    """The ledger's `merged` event: the one going as its subject, the one kept as its object."""
    await decide(
        database,
        decision_id,
        queue="people",
        verb="merged",
        object_kind="person",
        object_id=into,
        object_name=name,
        subjects=(("person", gone, None),),
    )


@pytest.fixture
async def library(temp_db: Database, actors: Actors) -> Database:
    """People still here, renamed, merged away and deleted; one stash-box; the record of merges."""
    await person(temp_db, STILL_HERE, "Jane Roe")
    await person(temp_db, RENAMED, "Ada Lovelace")
    await merge(temp_db, "01HX0000000000000000000431", MERGED_AWAY, STILL_HERE, "Jane Roe")
    for at, (gone, into) in enumerate(pairwise(CHAIN)):
        await merge(temp_db, f"01HX000000000000000000044{at}", gone, into, f"Chain {at}")
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)",
        (BOX, "StashDB", "https://stash-box.invalid/graphql"),
    )
    return temp_db


def says(*pieces: object) -> Callable[[Recorded], Worded | None]:
    return lambda _recorded: Worded(said=tuple(pieces))  # type: ignore[arg-type]


async def test_nothing_to_word_is_nothing_worded(library: Database, actors: Actors) -> None:
    assert await lines_of(library, Workbench(), actors.admin, []) == {}


async def test_the_doer_is_you_to_whoever_did_it_and_named_to_anybody_else(
    library: Database, actors: Actors
) -> None:
    receipts = [
        await decide(
            library, "01HX0000000000000000000451", actor_kind="user", actor_id=actors.admin.id
        ),
        await decide(
            library, "01HX0000000000000000000452", actor_kind="user", actor_id=actors.guest.id
        ),
        await decide(library, "01HX0000000000000000000453", actor_kind="user", actor_id=GONE_USER),
        await decide(library, "01HX0000000000000000000454", actor_kind="box", actor_id=BOX),
        await decide(library, "01HX0000000000000000000455", actor_kind="sift", actor_id="faces"),
    ]
    area = Area(
        "words",
        {one.id: says(DOER, " kept ", Named("person", STILL_HERE)) for one in receipts},
    )
    bench = Workbench()
    bench.register_reverser(area)

    lines = await lines_of(library, bench, actors.admin, [(one, 1) for one in receipts])

    guest_name = f"guest-{actors.guest.id}"
    assert [lines[one.id].said for one in receipts] == [
        "You kept Jane Roe",
        f"{guest_name} kept Jane Roe",
        "A user that is gone kept Jane Roe",
        "StashDB kept Jane Roe",
        "Sift kept Jane Roe",
    ]
    assert lines[receipts[0].id].links[0].id == STILL_HERE


async def test_each_thing_is_said_as_it_is_now(library: Database, actors: Actors) -> None:
    cases: dict[str, tuple[Named, str]] = {
        "01HX0000000000000000000461": (
            Named("person", RENAMED, recorded="Ada Byron", as_recorded=True),
            "Ada Byron (now Ada Lovelace)",
        ),
        "01HX0000000000000000000462": (
            Named("login", actors.guest.id, recorded="old-name", as_recorded=True),
            f"old-name (now guest-{actors.guest.id})",
        ),
        "01HX0000000000000000000463": (
            Named("login", actors.guest.id),
            f"guest-{actors.guest.id}",
        ),
        "01HX0000000000000000000464": (
            Named("person", MERGED_AWAY, recorded="Mary Roe"),
            "Mary Roe (since merged into Jane Roe)",
        ),
        "01HX0000000000000000000465": (
            Named("person", CHAIN[0], recorded="Linnea Ross"),
            "Linnea Ross (since merged into Chain 3)",
        ),
        "01HX0000000000000000000466": (
            Named("person", DELETED, recorded="Mary Roe"),
            "Mary Roe (since deleted)",
        ),
        "01HX0000000000000000000467": (
            Named("person", NAMELESS_GONE),
            "a person that is gone",
        ),
        "01HX0000000000000000000468": (Named("person", "", recorded="Rowan Pike"), "Rowan Pike"),
        "01HX0000000000000000000469": (Named("shoot", SHOOT), "a shoot"),
        "01HX0000000000000000000470": (Named("box", BOX), "StashDB"),
    }
    receipts = [await decide(library, key, actor_kind="sift") for key in cases]
    area = Area("words", {key: says(DOER, " chose ", piece) for key, (piece, _) in cases.items()})
    bench = Workbench()
    bench.register_reverser(area)

    lines = await lines_of(library, bench, actors.admin, [(one, 1) for one in receipts])

    for key, (_piece, words) in cases.items():
        assert lines[key].said == f"Sift chose {words}", key
    renamed = lines["01HX0000000000000000000461"]
    assert [(one.id, one.name) for one in renamed.links] == [(RENAMED, "Ada Lovelace")]
    merged = lines["01HX0000000000000000000464"]
    assert [one.id for one in merged.links] == [STILL_HERE]
    # A merge whose last step is gone too is said in words, with nothing to open.
    assert lines["01HX0000000000000000000465"].links == ()


async def test_a_merge_into_a_thing_the_line_also_names_ends_at_that_thing(
    library: Database, actors: Actors
) -> None:
    receipt = await decide(library, "01HX0000000000000000000472", actor_kind="sift")
    area = Area(
        "words",
        {
            receipt.id: says(
                DOER,
                " chose ",
                Named("person", MERGED_AWAY, recorded="Mary Roe"),
                " over ",
                Named("person", STILL_HERE),
            )
        },
    )
    bench = Workbench()
    bench.register_reverser(area)

    lines = await lines_of(library, bench, actors.admin, [(receipt, 1)])

    said = lines[receipt.id]
    assert said.said == "Sift chose Mary Roe (since merged into Jane Roe) over Jane Roe"
    assert [one.id for one in said.links] == [STILL_HERE, STILL_HERE]


async def test_a_thing_said_with_its_kind_leads_the_line_with_its_link(
    library: Database, actors: Actors
) -> None:
    receipt = await decide(library, "01HX0000000000000000000471", actor_kind="sift")
    area = Area(
        "words", {receipt.id: says(Named("person", STILL_HERE, kind_said=True), " was kept")}
    )
    bench = Workbench()
    bench.register_reverser(area)

    lines = await lines_of(library, bench, actors.admin, [(receipt, 1)])

    # The line opens on a name, so no capital is forced on it.
    assert lines[receipt.id].said == "the person Jane Roe was kept"


async def test_a_receipt_nobody_can_word_keeps_its_stored_title(
    library: Database, actors: Actors
) -> None:
    unworded = await decide(library, "01HX0000000000000000000481", queue="plain")
    failing = await decide(library, "01HX0000000000000000000482")
    silent = await decide(library, "01HX0000000000000000000483")

    def fails(_recorded: Recorded) -> Worded | None:
        raise RuntimeError("an area that could not word this")

    bench = Workbench()
    bench.register_reverser(Unworded("plain"))
    bench.register_reverser(Area("words", {failing.id: fails, silent.id: lambda _r: None}))

    receipts = [(unworded, 1), (failing, 1), (silent, 1)]
    assert await lines_of(library, bench, actors.admin, receipts) == {}


async def test_a_face_match_is_said_as_the_one_face_sentence(
    library: Database, actors: Actors
) -> None:
    await library.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at, title)"
        " VALUES (?, ?, 1, 'image', ?, ?)",
        (FILE, f"digest-{FILE}", AT, "clip.png"),
    )
    receipt = await decide(
        library,
        "01HX0000000000000000000491",
        verb="linked",
        actor_kind="sift",
        actor_id="faces",
        object_kind="person",
        object_id=STILL_HERE,
        object_name="Jane Roe",
        subjects=(("asset", FILE, "clip.png"),),
    )

    lines: dict[str, Line] = await lines_of(library, Workbench(), actors.admin, [(receipt, 1)])

    said = lines[receipt.id].said
    assert said.startswith("Sift recognized Jane Roe in clip.png")
    assert {one.id for one in lines[receipt.id].links} == {STILL_HERE, FILE}


async def test_a_page_that_cannot_be_worded_draws_its_stored_titles(
    tmp_path: Path, actors: Actors
) -> None:
    receipt = StoredRow(
        id="01HX0000000000000000000499",
        queue="words",
        user_id=None,
        title="stored title",
        detail="",
        payload="{}",
        decided_at=AT,
    )
    never_opened = Database(tmp_path / "never-opened.sqlite3")

    assert await worded_or_stored(never_opened, Workbench(), actors.admin, [(receipt, 1)]) == {}
