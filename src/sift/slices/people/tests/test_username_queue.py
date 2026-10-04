# SPDX-License-Identifier: AGPL-3.0-or-later
"""Usernames nobody has said who they belong to, as a panel on the Organize board.

A queue is for judgements a threshold cannot settle, and this is exactly that: no rule can tell
whether `esmewrenfield` on one site and `Neve Arbor` in the library are the same person. What a rule CAN
do is stop asking about the ones already answered, and that is the only filtering here.

Driven against the queue with the real access layer behind it, because what the card draws is a
scoped read, and the scoping is the half that matters on a screen full of ids.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from sift.kernel.access import Repository, Role, Viewer, index_assets
from sift.kernel.access.sentences import username_opens
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.kernel.workbench import ASSET
from sift.slices.people.queue import PREVIEW, UsernameQueue, join_receipt
from sift.slices.people.service import PeopleService
from sift.slices.workbench.store import Store as WorkbenchStore
from sift.testing.fixtures import World, create_user

pytestmark = pytest.mark.anyio


@pytest.fixture
async def admin(temp_db: Database, access: Repository) -> Viewer:
    """A real row in `users`, not a bare `Viewer`.

    What a user may see is stored per user, so a viewer that is not a user is one nothing
    has been made visible to: every cover reads as hidden and the card draws nothing. The queue
    is asked as somebody who exists.
    """
    await temp_db.initialize_schema()
    return await create_user(temp_db, Role.ADMIN)


@pytest.fixture
async def queue(temp_db: Database, access: Repository, admin: Viewer) -> UsernameQueue:
    return UsernameQueue(PeopleService(temp_db, access), access, touched=_nothing)


async def _nothing(asset_ids: Sequence[str]) -> None:
    """The index, for the tests that are not about it."""


async def _a_username(temp_db: Database, name: str = "esmewrenfield") -> str:
    site_id, username_id = new_id(), new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name) VALUES (?, ?) ON CONFLICT(name) DO NOTHING",
        (site_id, "SomeSite"),
    )
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, created_at)"
        " VALUES (?, (SELECT id FROM sites WHERE name = 'SomeSite'), ?, 0)",
        (username_id, name),
    )
    return username_id


async def _a_person(temp_db: Database, name: str = "Neve") -> str:
    person = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person, name)
    )
    return person


async def _already_answered(temp_db: Database, username_id: str) -> None:
    """Take a username out of the queue by saying who it is.

    The shared world fixture brings a username of its own, and it is unanswered, so a test about
    WHICH username the board asks about first has to settle that one, or it is asserting against a
    row it did not write.
    """
    person = await _a_person(temp_db, "Someone Already Here")
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (person, username_id))


async def _visible(
    temp_db: Database, username_id: str, asset_id: str, *, decided_at: int | None = None
) -> None:
    """File one visible asset under a username, decided when `decided_at` says.

    A username nothing visible came from is a username this user is not told exists, so the file has
    to be there for the card to have anything to say, which is the scoping rule, not a fixture
    detail.
    """
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id, decided_at) VALUES (?, ?, ?)"
        " ON CONFLICT(asset_id, username_id) DO NOTHING",
        (asset_id, username_id, decided_at),
    )


# --- whether the panel is drawn at all ------------------------------------------------------------


async def test_a_library_that_was_scanned_rather_than_downloaded_is_offered_no_panel(
    queue: UsernameQueue,
) -> None:
    """A panel offering to sort out something that does not exist reads as a broken feature rather
    than an unused one."""
    assert await queue.available() is False


async def test_a_library_that_has_answered_every_username_keeps_its_panel_at_zero(
    queue: UsernameQueue, temp_db: Database, admin: Viewer
) -> None:
    """The state this screen is trying to reach, and worth showing."""
    username = await _a_username(temp_db)
    person = await _a_person(temp_db)
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (person, username))

    assert await queue.available() is True
    assert (await queue.survey(admin)).count == 0


# --- the card ------------------------------------------------------------------------------------


async def test_the_card_counts_only_the_usernames_nobody_has_answered_for(
    queue: UsernameQueue, temp_db: Database, admin: Viewer, world: World
) -> None:
    """The whole panel is this filter. Without it the queue holds every username in the library,
    including the ones already decided, and it can never be worked to the bottom."""
    await _already_answered(temp_db, str(world.username))
    waiting = await _a_username(temp_db, "esmewrenfield")
    answered = await _a_username(temp_db, "somebodyelse")
    for one in (waiting, answered):
        await _visible(temp_db, one, str(world.solo))
    person = await _a_person(temp_db)
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (person, answered))

    survey = await queue.survey(admin)

    assert survey.name == "usernames"
    assert survey.title == "Usernames to assign"
    assert survey.count == 1


async def test_a_username_with_no_files_under_it_is_not_waiting(
    queue: UsernameQueue, access: Repository, temp_db: Database, admin: Viewer, world: World
) -> None:
    """The queue is files posted under a username nobody is, and a username can exist with none.

    A stash-box's link to a person's page on a site can become a username with nothing under it,
    and listed, one of those would sit on the wall as "nobody goes by this yet" with 0 items. Asked
    "who is this" it
    offers nothing to recognise and nothing that would move if it were answered. Asked as an ADMIN
    on purpose: the ordinary wall lists empty rows for an admin, and an admin is who works this
    queue, so the rule has to be the queue's own, not the wall's. The board's count and the
    panel's wall read one statement, so both are asserted: they cannot disagree.
    """
    await _already_answered(temp_db, str(world.username))
    empty = await _a_username(temp_db, "emptyname")
    held = await _a_username(temp_db, "esmewrenfield")
    await _visible(temp_db, held, str(world.solo))

    survey = await queue.survey(admin)
    wall = await access.list_usernames(admin, limit=10, unattached=True)
    everything = await access.list_usernames(admin, limit=10)

    assert survey.count == 1
    assert [one.id for one in wall.items] == [held]
    assert wall.total == 1
    # Still a username, and still listed wherever nobody is asking who it is.
    assert empty in {one.id for one in everything.items}


async def test_the_picture_is_the_newest_file_filed_under_the_username(
    queue: UsernameQueue, temp_db: Database, admin: Viewer, world: World
) -> None:
    """Not a cover (a username has none since v58): a file POSTED under the username, the one filed
    most recently, and pressing it goes where a press on the username goes."""
    await _already_answered(temp_db, str(world.username))
    username = await _a_username(temp_db, "esmewrenfield")
    await _visible(temp_db, username, str(world.solo), decided_at=100)
    await _visible(temp_db, username, str(world.twin), decided_at=300)
    await _visible(temp_db, username, str(world.loose), decided_at=200)

    survey = await queue.survey(admin)

    assert [(one.kind, one.id, one.href) for one in survey.preview] == [
        (ASSET, str(world.twin), username_opens(username, None))
    ]


async def test_a_card_draws_no_more_stills_than_it_has_room_for(
    queue: UsernameQueue, temp_db: Database, admin: Viewer, world: World
) -> None:
    """One still per username on the strip, read in one statement for the page of usernames."""
    solo = str(world.solo)
    await _visible(temp_db, str(world.username), solo)
    for index in range(PREVIEW + 2):
        await _visible(temp_db, await _a_username(temp_db, f"name{index}"), solo)

    survey = await queue.survey(admin)

    assert len(survey.preview) == PREVIEW
    assert {one.id for one in survey.preview} == {solo}


async def test_no_picture_is_read_for_a_viewer_who_may_see_nothing_under_the_username(
    access: Repository, temp_db: Database, admin: Viewer, world: World
) -> None:
    """Through the wall's own statement, never a raw read of the filings: a user that may
    see none of the username's files is handed no file of it, while an admin is handed the
    newest."""
    username = await _a_username(temp_db, "esmewrenfield")
    await _visible(temp_db, username, str(world.solo), decided_at=100)
    guest = await create_user(temp_db, Role.GUEST)

    assert await access.newest_under_usernames(admin, [username]) == {username: str(world.solo)}
    assert await access.newest_under_usernames(guest, [username]) == {}
    assert await access.newest_under_usernames(admin, ["not an id"]) == {}


async def test_a_queue_with_nothing_waiting_counts_nothing_and_draws_nothing(
    queue: UsernameQueue, temp_db: Database, admin: Viewer
) -> None:
    username = await _a_username(temp_db)
    person = await _a_person(temp_db)
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (person, username))

    survey = await queue.survey(admin)

    assert survey.count == 0
    assert survey.preview == ()


# --- what one decision was about -------------------------------------------------------------------


async def test_one_decision_draws_the_newest_file_under_its_username(
    queue: UsernameQueue, temp_db: Database, admin: Viewer, world: World
) -> None:
    """Read NOW for whoever reads the record, and pressing it goes to the person the username was
    joined to, which is what the decision made of it."""
    username = await _a_username(temp_db, "esmewrenfield")
    await _visible(temp_db, username, str(world.solo), decided_at=100)
    await _visible(temp_db, username, str(world.twin), decided_at=50)
    person = await _a_person(temp_db)
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (person, username))

    pictures = await queue.pictures_of(admin, json.dumps({"username_id": username}))

    assert [(one.id, one.href) for one in pictures] == [(str(world.solo), f"/people/{person}")]


def test_no_link_this_area_builds_names_a_page_of_the_username_s_own() -> None:
    """The two answers, and never `/accounts/<id>`: there is no such page."""
    assert username_opens("acc", None) == "/browse?username=acc"
    assert username_opens("acc", "someone") == "/people/someone"


async def test_a_decision_about_a_username_with_nothing_filed_under_it_draws_nothing(
    queue: UsernameQueue, temp_db: Database, admin: Viewer
) -> None:
    """The username is there, and nothing under it is a file: a username has no picture of its
    own, so the decision is drawn without one rather than with somebody else's."""
    username = await _a_username(temp_db, "esmewrenfield")

    pictures = await queue.pictures_of(admin, json.dumps({"username_id": username}))

    assert pictures == ()


async def test_a_decision_naming_a_username_that_is_gone_draws_nothing(
    queue: UsernameQueue,
    admin: Viewer,
) -> None:
    pictures = await queue.pictures_of(admin, json.dumps({"username_id": new_id()}))

    assert pictures == ()


async def test_a_record_that_cannot_be_read_draws_nothing_rather_than_failing(
    queue: UsernameQueue,
    admin: Viewer,
) -> None:
    for payload in ("not json", json.dumps(["a list"]), json.dumps({})):
        assert await queue.pictures_of(admin, payload) == ()


# --- taking a decision back ------------------------------------------------------------------------


async def test_an_undo_takes_the_join_off_and_leaves_the_person(
    queue: UsernameQueue, temp_db: Database, admin: Viewer
) -> None:
    """A person this decision CREATED is left: somebody may have edited that person since, and an
    undo that deleted a row somebody has been working on is worse than a person with nothing filed
    under them."""
    username = await _a_username(temp_db)
    person = await _a_person(temp_db)
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (person, username))

    assert await queue.reverse(admin, "r1", json.dumps({"username_id": username})) is True

    row = await temp_db.fetch_one("SELECT person_id FROM usernames WHERE id = ?", (username,))
    assert row is not None and row["person_id"] is None
    assert await temp_db.fetch_one("SELECT id FROM people WHERE id = ?", (person,)) is not None


async def test_an_undo_of_a_record_that_cannot_be_read_takes_nothing_off(
    queue: UsernameQueue,
    admin: Viewer,
) -> None:
    for payload in ("not json", json.dumps({}), json.dumps({"username_id": new_id()})):
        assert await queue.reverse(admin, "r1", payload) is False


async def _joined(
    temp_db: Database, access: Repository, admin: Viewer, world: World
) -> tuple[UsernameQueue, str, str, list[list[str]]]:
    """A username with a file under it, joined to a person through the queue's own answer.

    The person is on a second file that is NOT under the username, because that is where an alias
    reaches further than the join: it is indexed on every file its person is on. The index is the
    real one, told through the same seam the route and the undo tell it through; what it was told
    is kept, so a test can say which files the undo named.
    """
    username = await _a_username(temp_db, "esmewrenfield")
    person = await _a_person(temp_db, "Neve Arbor")
    await _visible(temp_db, username, str(world.solo))
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (str(world.twin), person)
    )
    told: list[list[str]] = []

    async def reindex(asset_ids: Sequence[str]) -> None:
        told.append(sorted(asset_ids))
        await index_assets(temp_db, asset_ids=list(asset_ids))

    service = PeopleService(temp_db, access)
    queue = UsernameQueue(service, access, touched=reindex)
    await service.attach_username(
        username,
        person_id=person,
        actor=Actor.user(admin.id),
        receipt=join_receipt(WorkbenchStore(temp_db), admin.id),
    )
    # What the route does once the join has landed (`merge_username_into_person`), deduplicated
    # the way it is there.
    joined = [
        *await service.assets_of_username(username),
        *await service.assets_of_person(person),
    ]
    await reindex(list(dict.fromkeys(joined)))
    return queue, username, person, told


async def _the_receipt(temp_db: Database) -> tuple[str, str]:
    row = await temp_db.fetch_one(
        "SELECT id, payload FROM workbench_decisions WHERE queue = 'usernames'"
    )
    assert row is not None
    return str(row["id"]), str(row["payload"])


async def _found_by_person_for(temp_db: Database, word: str) -> set[str]:
    """The files whose People words in the search index carry this spelling."""
    rows = await temp_db.fetch_all("SELECT asset_id, people FROM assets_fts")
    return {str(row["asset_id"]) for row in rows if word in str(row["people"])}


async def test_an_undo_takes_off_everything_the_join_wrote(
    temp_db: Database, access: Repository, admin: Viewer, world: World
) -> None:
    """Accept writes five things and an undo takes back all five.

    The pointer, the files filed under the person, the record (an `unlinked` line), the alias the
    join added (a `removed` line on the person), and the search index for the same files the join
    reindexed. With the alias left behind, the search box would go on answering the person for the
    username, and the next download under it would file itself to them again without asking.
    """
    queue, username, person, told = await _joined(temp_db, access, admin, world)
    assert await _found_by_person_for(temp_db, "esmewrenfield") == {
        str(world.solo),
        str(world.twin),
    }
    receipt, payload = await _the_receipt(temp_db)

    assert await queue.reverse(admin, receipt, payload) is True

    row = await temp_db.fetch_one("SELECT person_id FROM usernames WHERE id = ?", (username,))
    assert row is not None and row["person_id"] is None
    filed = await temp_db.fetch_one(
        "SELECT 1 FROM asset_people WHERE asset_id = ? AND person_id = ?", (str(world.solo), person)
    )
    assert filed is None
    aliases = await temp_db.fetch_all(
        "SELECT alias FROM people_aliases WHERE person_id = ?", (person,)
    )
    assert aliases == []
    # The same files the join told the index about, and the index answering nothing for the name.
    assert told[-1] == told[0] == sorted([str(world.solo), str(world.twin)])
    assert await _found_by_person_for(temp_db, "esmewrenfield") == set()
    verbs = await temp_db.fetch_all(
        "SELECT verb FROM workbench_decisions WHERE verb IN ('unlinked', 'removed') ORDER BY verb"
    )
    assert [str(one["verb"]) for one in verbs] == ["removed", "unlinked"]
    # And the card lists it again: it is waiting on somebody, exactly as before the join.
    page = await access.list_usernames(admin, limit=10, unattached=True)
    assert username in {one.id for one in page.items}


async def test_an_alias_the_person_had_before_the_join_survives_its_undo(
    temp_db: Database, access: Repository, admin: Viewer, world: World
) -> None:
    """Only what the join added comes off. A name the person already answered to is theirs, so the
    receipt names no alias and the undo leaves it where it was, still found by the search."""
    username = await _a_username(temp_db, "esmewrenfield")
    person = await _a_person(temp_db, "Neve Arbor")
    await temp_db.execute(
        "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)",
        (new_id(), person, "EsmeWrenfield"),
    )
    await _visible(temp_db, username, str(world.solo))
    service = PeopleService(temp_db, access)
    queue = UsernameQueue(service, access, touched=_nothing)
    await service.attach_username(
        username,
        person_id=person,
        actor=Actor.user(admin.id),
        receipt=join_receipt(WorkbenchStore(temp_db), admin.id),
    )
    receipt, payload = await _the_receipt(temp_db)
    assert json.loads(payload)["alias_id"] is None

    assert await queue.reverse(admin, receipt, payload) is True

    aliases = await temp_db.fetch_all(
        "SELECT alias FROM people_aliases WHERE person_id = ?", (person,)
    )
    assert [str(one["alias"]) for one in aliases] == ["EsmeWrenfield"]


# --- what KIND of waiting a waiting username is -------------------------------------------------


async def test_it_says_how_many_people_already_go_by_each_spelling(
    temp_db: Database, access: Repository, admin: Viewer, world: World
) -> None:
    """The one fact that says whether a row is a judgement or an offer.

    Sift files a download under somebody when EXACTLY ONE person answers to the username, and
    refuses to guess past that. So several answering is that rule declining (nobody but a person
    can settle it), while none answering is an offer to make somebody, which is different work.
    Indistinguishable on the wall, the two would make the pile read as one undifferentiated chore.
    """
    ambiguous = await _a_username(temp_db, "neve")
    await _a_person(temp_db, "Neve")
    await _a_person(temp_db, "neve")
    unknown = await _a_username(temp_db, "nobodyatall")
    await _visible(temp_db, ambiguous, str(world.solo))
    await _visible(temp_db, unknown, str(world.solo))

    page = await access.list_usernames(admin, limit=10, unattached=True, name_candidates=True)
    by_id = {one.id: one for one in page.items}

    assert by_id[ambiguous].name_candidates == 2
    assert by_id[unknown].name_candidates == 0


async def test_an_alias_counts_as_going_by_it(
    temp_db: Database, access: Repository, admin: Viewer, world: World
) -> None:
    """The same rule the downloader applies, which reads names AND also-known-as names. Two
    spellings of "who does this word name" would be two answers, and the one that drifts is the one
    nobody is looking at."""
    username = await _a_username(temp_db, "nev")
    person = await _a_person(temp_db, "Neve Arb")
    await temp_db.execute(
        "INSERT INTO people_aliases (person_id, alias) VALUES (?, ?)", (person, "nev")
    )
    await _visible(temp_db, username, str(world.solo))

    page = await access.list_usernames(admin, limit=10, unattached=True, name_candidates=True)

    # By id: the world fixture has an unattached username of its own, so the first row is not
    # necessarily this one.
    assert {one.id: one.name_candidates for one in page.items}[username] == 1


async def test_nobody_who_did_not_ask_pays_for_the_question(
    temp_db: Database, access: Repository, admin: Viewer, world: World
) -> None:
    """It is a correlated subquery, and this statement is also the People page's username list, a
    site's username list and every by-id read. Off, they bind zero and SQLite skips it entirely.

    Asserted as ZERO rather than as "not run", because zero is what a caller that did not ask
    actually receives, and a screen reading the field without asking would be reading that zero
    as "nobody goes by this". The field is named for what it counts for exactly that reason.
    """
    username = await _a_username(temp_db, "neve")
    await _a_person(temp_db, "Neve")
    await _visible(temp_db, username, str(world.solo))

    default = await access.list_usernames(admin, limit=10, unattached=True)
    by_id = await access.visible_username(admin, username)

    assert {one.id: one.name_candidates for one in default.items}[username] == 0
    assert by_id is not None and by_id.name_candidates == 0
