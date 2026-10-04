# SPDX-License-Identifier: AGPL-3.0-or-later
"""A decision's line on History's Decisions, worded when it is shown from what it recorded.

The rules this holds, each against the fault it closes: the doer is "You" to the person who did it;
a thing is said by the name it has NOW (a stored title kept "Ada Byron" for good about a person called
something else); one merged away is said as merged into the one kept, linked; one deleted keeps its
name as plain words and says so; and an area that cannot word its decisions (or fails to) keeps
the stored title, because an old row that recorded nothing else has nothing better.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from sift.kernel.access import Role, Viewer
from sift.kernel.access import sentences as say
from sift.kernel.access.history_entity import history_of_tag
from sift.kernel.access.history_person import history_of_person
from sift.kernel.access.sentences import A_GONE, today_words
from sift.kernel.access.worded import Line, worded_or_stored
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.vocabulary import VIA_FACES, Subject
from sift.kernel.workbench import DOER, Named, Recorded, Worded, Workbench
from sift.slices.workbench.router import ledger
from sift.slices.workbench.store import Decision, Store
from sift.slices.workbench.tests.conftest import FakeQueue
from sift.testing.fixtures import create_user


@dataclass
class WordingQueue(FakeQueue):
    """A queue that words its decisions with whatever a test hands it."""

    words: Callable[[Recorded], Worded | None] | None = None
    seen: list[Recorded] | None = None

    def worded(self, recorded: Recorded) -> Worded | None:
        if self.seen is not None:
            self.seen.append(recorded)
        assert self.words is not None
        return self.words(recorded)


@dataclass(frozen=True)
class _Row:
    """One receipt and the line it is worded with."""

    receipt: Decision
    line: Line | None


async def _rows(store: Store, workbench: Workbench, admin: Viewer) -> list[_Row]:
    """Every receipt, each worded through the reader History's Decisions asks
    (`kernel.access.worded.worded_or_stored`): one decision, one sentence, on every screen."""
    found, _ = await store.recent(limit=50, offset=0)
    lines = await worded_or_stored(store.database, workbench, admin, [(one, 1) for one in found])
    # The stored title in today's words, as every screen says a saved title (`today_words`).
    return [
        _Row(receipt=replace(one, title=today_words(one.title)), line=lines.get(one.id))
        for one in found
    ]


async def _person(database: Database, name: str) -> str:
    person_id = new_id()
    async with database.write() as connection:
        await connection.execute(
            "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
            (person_id, name, name.lower()),
        )
    return person_id


async def _decide(store: Store, user_id: str | None, *, title: str = "Kept your answer") -> str:
    async with store.database.write() as connection:
        return await store.record_on(
            connection,
            queue="things",
            user_id=user_id,
            # Sift says which pass acted; a person needs no word for it.
            via=None if user_id else VIA_FACES,
            title=title,
            detail="A detail stored at the press.",
            payload="{}",
        )


def _about(person_id: str, *, recorded: str | None = None, as_recorded: bool = False) -> Worded:
    return Worded(
        said=(
            DOER,
            " kept the name ",
            Named(kind="person", id=person_id, recorded=recorded, as_recorded=as_recorded),
            " over the box's offer",
        ),
        more=("Nothing else changed",),
    )


async def test_a_worded_decision_names_its_doer_and_its_thing_as_they_are_now(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    person_id = await _person(store.database, "Esme Wrenfield")
    workbench.register(WordingQueue(words=lambda _r: _about(person_id, recorded="Old Name")))
    await _decide(store, admin.id)

    row = (await _rows(store, workbench, admin))[0]

    assert row.line is not None
    assert row.line.said == "You kept the name Esme Wrenfield over the box's offer"
    assert [(one.kind, one.id, one.name) for one in row.line.links] == [
        ("person", person_id, "Esme Wrenfield")
    ]
    assert row.line.more == "Nothing else changed"


async def test_a_name_that_is_the_fact_is_said_as_recorded_with_the_name_now_after_it(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    person_id = await _person(store.database, "Esme Wrenfield")
    workbench.register(
        WordingQueue(words=lambda _r: _about(person_id, recorded="Ada Byron", as_recorded=True))
    )
    await _decide(store, admin.id)

    line = (await _rows(store, workbench, admin))[0].line

    assert line is not None
    assert line.said == "You kept the name Ada Byron (now Esme Wrenfield) over the box's offer"
    assert [one.name for one in line.links] == ["Esme Wrenfield"]


async def test_a_person_merged_away_is_said_as_merged_into_the_one_kept(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    kept = await _person(store.database, "Esme Wrenfield")
    going = new_id()
    async with store.database.write() as connection:
        await record_event(
            connection,
            actor=Actor.user(admin.id),
            verb="merged",
            subject=Subject(kind="person", id=going, name="Ada Byron"),
            object=Object(kind="person", id=kept, name="Esme Wrenfield"),
        )
    workbench.register(WordingQueue(words=lambda _r: _about(going, recorded="Ada Byron")))
    await _decide(store, admin.id)

    line = (await _rows(store, workbench, admin))[0].line

    assert line is not None
    assert line.said == (
        "You kept the name Ada Byron (since merged into Esme Wrenfield) over the box's offer"
    )
    assert [(one.id, one.name) for one in line.links] == [(kept, "Esme Wrenfield")]


async def test_a_person_deleted_since_keeps_the_name_it_had_as_plain_words(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(WordingQueue(words=lambda _r: _about(new_id(), recorded="Ada Byron")))
    await _decide(store, admin.id)

    line = (await _rows(store, workbench, admin))[0].line

    assert line is not None
    assert line.said == "You kept the name Ada Byron (since deleted) over the box's offer"
    assert line.links == ()


async def test_a_thing_gone_with_no_name_recorded_is_said_as_gone(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(WordingQueue(words=lambda _r: _about(new_id())))
    await _decide(store, admin.id)

    line = (await _rows(store, workbench, admin))[0].line

    assert line is not None
    assert A_GONE["person"] in line.said
    assert line.links == ()


def _created(photo_set_id: str, *, recorded: str | None = None) -> Worded:
    set_named = Named(kind="photo_set", id=photo_set_id, recorded=recorded, kind_said=True)
    return Worded(said=(DOER, " created ", set_named, " from 3 photos posted together"))


async def test_the_kind_is_said_before_a_name_and_never_before_the_words_for_a_kind(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """Never "Sift created the Photo Set a Photo Set that is gone from 3 photos": an area writing
    "the Photo Set " before a thing the reader then says by its kind doubles it. The reader says the
    kind, before a name only."""
    workbench.register(WordingQueue(words=lambda _r: _created(new_id(), recorded="Cassia Lynn")))
    await _decide(store, admin.id)
    workbench.register(WordingQueue(name="others", words=lambda _r: _created(new_id())))
    async with store.database.write() as connection:
        await store.record_on(
            connection, queue="others", user_id=admin.id, title="t", detail="", payload="{}"
        )

    lines = [one.line for one in await _rows(store, workbench, admin)]

    said = sorted(line.said for line in lines if line is not None)
    assert said == [
        "You created a Photo Set that is gone from 3 photos posted together",
        "You created the Photo Set Cassia Lynn (since deleted) from 3 photos posted together",
    ]


async def test_the_doer_is_named_to_anybody_else_and_sift_is_sift(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    other = await create_user(store.database, Role.ADMIN)
    person_id = await _person(store.database, "Esme Wrenfield")
    workbench.register(WordingQueue(words=lambda _r: _about(person_id)))
    await _decide(store, other.id, title="first")
    await _decide(store, None, title="second")

    called = await store.database.fetch_one("SELECT username FROM users WHERE id = ?", (other.id,))
    assert called is not None

    lines = [one.line for one in await _rows(store, workbench, admin)]

    assert lines[0] is not None and lines[0].said.startswith("Sift kept")
    assert lines[1] is not None and lines[1].said.startswith(f"{called['username']} kept")


async def test_an_area_that_cannot_word_keeps_the_stored_title(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(FakeQueue(name="things"))
    await _decide(store, admin.id, title="A group set aside")

    row = (await _rows(store, workbench, admin))[0]

    assert row.line is None
    # The stored title, in TODAY'S words: "set aside" is a word the History word table retired
    # (`vocabulary.json`, `history_screens`), and `sentences.STALE_IN_A_TITLE` swaps a retired
    # phrase wherever a stored title is shown: here, the feed and a page alike. The title is kept;
    # only the word that is no longer said changes.
    assert row.receipt.title == "A group discarded"


async def test_an_old_row_that_recorded_nothing_else_keeps_the_stored_title(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(WordingQueue(words=lambda _r: None))
    await _decide(store, admin.id)

    assert (await _rows(store, workbench, admin))[0].line is None


async def test_an_area_whose_words_fail_costs_only_its_own_rows(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """One area failing to word a row leaves that row in its stored words, and every other area's
    rows worded, which is what tells this apart from the page giving up on words altogether."""

    def broken(_recorded: Recorded) -> Worded | None:
        raise ValueError("a payload this build cannot read")

    person_id = await _person(store.database, "Esme Wrenfield")
    workbench.register(WordingQueue(name="broken", words=broken))
    workbench.register(WordingQueue(name="things", words=lambda _r: _about(person_id)))
    async with store.database.write() as connection:
        await store.record_on(
            connection,
            queue="broken",
            user_id=admin.id,
            title="Stored words",
            detail="",
            payload="{}",
        )
    await _decide(store, admin.id)

    rows = {one.receipt.queue: one for one in await _rows(store, workbench, admin)}

    assert rows["broken"].line is None
    assert rows["broken"].receipt.title == "Stored words"
    assert rows["things"].line is not None


async def test_the_area_is_handed_what_the_row_recorded_and_how_many_it_stands_for(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    seen: list[Recorded] = []
    workbench.register(WordingQueue(words=lambda _r: None, seen=seen))
    for _ in range(3):
        await _decide(store, admin.id, title="the same sentence")

    # Through History's Decisions, which folds the three into one line and says so to the area.
    await ledger(
        database=store.database,
        bench=workbench,
        runs=Ledger(store.database),
        viewer=admin,
        decisions=True,
    )

    assert [(one.run, one.user_id, one.verb, one.title) for one in seen] == [
        (3, admin.id, "decided", "the same sentence")
    ]


async def test_a_worded_line_is_history_pieces_each_thing_placed_where_it_sits(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """The decision record draws the one shape every History screen draws: the doer and the words
    plain, the person a piece of its own with its kind and id, placed where it sits: nothing for
    the client to search for in a sentence."""
    person_id = await _person(store.database, "Esme Wrenfield")
    workbench.register(WordingQueue(words=lambda _r: _about(person_id, recorded="Old Name")))
    await _decide(store, admin.id)

    line = (await _rows(store, workbench, admin))[0].line

    assert line is not None
    assert [(one.text, one.kind, one.id) for one in line.pieces] == [
        ("You kept the name ", None, None),
        ("Esme Wrenfield", "person", person_id),
        (" over the box's offer", None, None),
    ]


async def test_a_name_that_is_the_fact_places_the_name_now_as_the_thing(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """ "Ada Byron (now Esme Wrenfield)": the recorded name is words, and only the name it has now
    is the piece that goes somewhere."""
    person_id = await _person(store.database, "Esme Wrenfield")
    workbench.register(
        WordingQueue(words=lambda _r: _about(person_id, recorded="Ada Byron", as_recorded=True))
    )
    await _decide(store, admin.id)

    line = (await _rows(store, workbench, admin))[0].line

    assert line is not None
    assert [(one.text, one.kind) for one in line.pieces] == [
        ("You kept the name Ada Byron (now ", None),
        ("Esme Wrenfield", "person"),
        (") over the box's offer", None),
    ]


async def test_a_face_match_receipt_says_the_one_face_match_sentence(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """A face match reads one sentence on the file, the person's page, the feed and here: "Sift
    recognized <person> in <file>" (`sentences.recognized_face`), with the person and the file as
    pieces, never the words its receipt was stored with."""
    person_id = await _person(store.database, "Esme Wrenfield")
    file_id = new_id()
    workbench.register(WordingQueue(words=lambda _r: None))
    async with store.database.write() as connection:
        await store.record_on(
            connection,
            queue="things",
            user_id=None,
            via=VIA_FACES,
            title="Sift named Esme Wrenfield here, 75% sure",
            detail="",
            payload="{}",
            verb="linked",
            subjects=(Subject(kind="asset", id=file_id, name="holiday.mp4"),),
            object=Object(kind="person", id=person_id, name="Esme Wrenfield"),
        )

    line = (await _rows(store, workbench, admin))[0].line

    assert line is not None
    assert line.said.startswith("Sift recognized Esme Wrenfield in holiday.mp4")
    assert [(one.kind, one.id) for one in line.pieces if one.kind] == [("person", person_id)]


async def test_a_history_page_says_a_decision_in_the_card_s_words_with_its_own_vantage(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """A person's page and the decision record word a decision from what it recorded, never one from
    its STORED title ("Took FansDB's answer for Ada Byron") beside the other. One reader for both,
    the page's own thing said by its vantage word, and a name that IS the fact keeps its name even
    there."""
    person_id = await _person(store.database, "Esme Wrenfield")
    chose = Worded(said=(DOER, " chose the box's height for ", Named(kind="person", id=person_id)))
    workbench.register(WordingQueue(words=lambda _r: chose))
    async with store.database.write() as connection:
        await store.record_on(
            connection,
            queue="things",
            user_id=admin.id,
            title="Took the box's answer for Esme Wrenfield",
            detail="",
            payload="{}",
            subjects=[Subject(kind="person", id=person_id)],
        )

    card = (await _rows(store, workbench, admin))[0].line
    page = await history_of_person(store.database, admin, person_id, bench=workbench)
    without = await history_of_person(store.database, admin, person_id)

    assert card is not None and card.said == "You chose the box's height for Esme Wrenfield"
    said = [say.text_of(one.pieces) for one in page if one.kind == "decided"]
    assert said == ["You chose the box's height for them"]
    # Drawn with no registry to ask, a page keeps the stored title: nothing is worded on a guess.
    kept = [say.text_of(one.pieces) for one in without if one.kind == "decided"]
    assert kept == ["Took the box's answer for Esme Wrenfield"]


async def test_a_history_page_hands_the_area_the_detail_the_receipt_stored(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """A settled disagreement from before its payload carried both values kept them only in its
    detail, and a statement reading every column but that one would have the decision record say "You
    chose FansDB's height for Ada Byron, 157 cm, over your 177 cm" while her own page said the
    stored "Took FansDB's answer for Ada Byron". A person's page and a tag's hand the area the same
    row the card does."""
    person_id = await _person(store.database, "Esme Wrenfield")
    tag_id = new_id()
    async with store.database.write() as connection:
        await connection.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?, 'Loop', 0)", (tag_id,)
        )
    seen: list[Recorded] = []
    workbench.register(WordingQueue(words=lambda _r: None, seen=seen))
    for kind, thing in (("person", person_id), ("tag", tag_id)):
        async with store.database.write() as connection:
            await store.record_on(
                connection,
                queue="things",
                user_id=admin.id,
                title="Took the box's answer",
                detail=f"height_cm: 177 became 157 ({kind})",
                payload="{}",
                subjects=[Subject(kind=kind, id=thing)],  # type: ignore[arg-type]
            )

    await history_of_person(store.database, admin, person_id, bench=workbench)
    await history_of_tag(store.database, admin, tag_id, bench=workbench)

    assert [one.detail for one in seen] == [
        "height_cm: 177 became 157 (person)",
        "height_cm: 177 became 157 (tag)",
    ]
