# SPDX-License-Identifier: AGPL-3.0-or-later
"""The faces as the workbench sees them: one list of work, and one record of who Sift knows.

Separate piles (awaiting review, proposed people, ignored, identified) could not say between
them which held the answer worth giving first. What is left to check is one list, ordered by how
many faces one press settles, with what was set aside as a filter on it; the people
Sift knows is the record beside it.

Both are absent from the board unless recognition is switched on. That is not a count of zero: an
install that has never turned it on has no groups because nothing has looked, and a permanently
empty panel reads as a feature that is broken rather than one that is off.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.access.viewer import Concealment
from sift.kernel.db import Database
from sift.kernel.vocabulary import Subject
from sift.kernel.workbench import FACE, Band, Recorded
from sift.slices.faces import recognize
from sift.slices.faces import settings as face_settings
from sift.slices.faces.models import PileStatus, ToCheckKind, ToCheckShow
from sift.slices.faces.queue import (
    FACES_GROUP,
    DisagreementsQueue,
    IdentifiedRecords,
    IgnoredRecords,
    PeopleKnownQueue,
    SetAsideQueue,
    SuggestionsQueue,
    ToNameQueue,
    _picture,
)
from sift.slices.faces.service import (
    AGREED_WITH_MATCHES,
    REFUSED_FACES,
    FaceService,
    Sighting,
    ToCheckView,
)
from sift.slices.faces.tests.conftest import FakePreferences, person_vector
from sift.slices.faces.worded import identified_said, ignored_said
from sift.slices.workbench.store import Store as WorkbenchStore
from sift.testing.fixtures import create_user
from sift.testing.library import hidden_row

pytestmark = pytest.mark.integration

PILE = "01HX00000000000000000PILE1"
TRACK = "01HX0000000000000000TRACK1"
ASSET = "01HX0000000000000000ASSET1"
ROOT = "01HX00000000000000000ROOT1"
FOLDER = "01HX0000000000000000FLDR1"
#: The group of five the card tests are drawn from, and its first face. See the `seeded` fixture.
BIG_PILE = "01HX0000000000000000PIL5"
BIG_FACE = "01HX00000000000000TR500"


@pytest.fixture
async def admin(temp_db: Database, access: Repository) -> Viewer:
    return await create_user(temp_db, Role.ADMIN)


@pytest.fixture
async def seeded_pile(temp_db: Database, service: FaceService) -> str:
    """One group of one face, in one file an admin can see.

    The file needs a place in a real library folder, not just a row in `assets`: every count on
    this surface is recounted through the resolver, and a file in no folder is a file nobody
    (including an admin) can see.
    """
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, 'Media', ?, 0)",
        (ROOT, "/library/media"),
    )
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
        "VALUES (?, ?, NULL, '', 'Media')",
        (FOLDER, ROOT),
    )
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        (ASSET, f"digest-{ASSET}"),
    )
    await temp_db.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, 'one.mp4', 'one.mp4', 0, 0)",
        (f"loc-{ASSET}", ASSET, ROOT, FOLDER),
    )
    await temp_db.execute(
        "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
        "VALUES (?, 'open', x'00', 1, 0, 0)",
        (PILE,),
    )
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, person_id, created_at) VALUES (?, ?, 0, 0, 1, 1.0, ?, NULL, 0)",
        (TRACK, ASSET, PILE),
    )
    return PILE


@pytest.fixture
async def seeded(temp_db: Database, seeded_pile: str) -> str:
    """A group big enough to be asked about, and the first face in it.

    The fixture above is a group of ONE, which is under the stranger floor and deliberately not on
    the list at all, so the tests about what a card draws need a group the list actually holds.
    Five is the floor, so five is the smallest honest fixture for them.
    """
    await seed_group(temp_db, "5", 5)
    return BIG_FACE


async def seed_group(temp_db: Database, number: str, faces: int) -> str:
    """A second group, of however many faces, on the file the fixture above already made.

    The faces are separate appearances of one file, which is what a group of a stranger who turns
    up several times in one video really is, and it is the count of APPEARANCES that the floor
    is measured against, so this is the shape the floor has to be proved on.
    """
    pile = f"01HX0000000000000000PIL{number}"
    await temp_db.execute(
        "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
        "VALUES (?, 'open', x'00', ?, 0, 0)",
        (pile, faces),
    )
    for at in range(faces):
        await temp_db.execute(
            "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
            "pile_id, person_id, created_at) VALUES (?, ?, ?, ?, 1, 1.0, ?, NULL, 0)",
            (f"01HX00000000000000TR{number}{at:02d}", ASSET, at, at, pile),
        )
    return pile


#: The five tabs of the Faces page, which are five queues. See the slice's `queue.py`.
EVERY_TAB: list[Any] = [
    SuggestionsQueue,
    DisagreementsQueue,
    ToNameQueue,
    SetAsideQueue,
    PeopleKnownQueue,
]


@pytest.mark.parametrize("build", EVERY_TAB)
async def test_both_are_absent_when_recognition_is_off(
    service: FaceService, preferences: FakePreferences, build: type, admin: Viewer
) -> None:
    preferences.values[face_settings.ENABLED_KEY] = False

    assert await build(service).available() is False


@pytest.mark.parametrize("build", EVERY_TAB)
async def test_and_present_when_it_is_on(service: FaceService, build: type, admin: Viewer) -> None:
    assert await build(service).available() is True


def test_only_the_list_of_work_is_work(service: FaceService) -> None:
    """The people Sift knows is a record of what has been settled. Counting it as work would put a
    number on a screen whose whole promise is that it empties and that can never reach zero.

    Asked of the QUEUE rather than of its survey, and the difference is the point: a survey no
    longer carries a band at all: the board stamps each queue's own declaration onto it on the
    way out, so there is one answer and it lives here. See `kernel.workbench.Summary.band`.
    """
    assert SuggestionsQueue(service).band is Band.DECISION
    assert DisagreementsQueue(service).band is Band.DECISION
    assert ToNameQueue(service).band is Band.DECISION
    # And the two that can never be worked down to nothing are records, not work.
    assert SetAsideQueue(service).band is Band.RECORD
    assert PeopleKnownQueue(service).band is Band.RECORD


def test_the_five_share_one_page(service: FaceService) -> None:
    """Five answers to one question (who is this), so they are tabs of one screen rather than
    five destinations. The tab row is the group's own membership; nothing on the client holds a
    list of these names."""
    assert {build(service).group for build in EVERY_TAB} == {FACES_GROUP}


def test_the_card_is_titled_for_the_whole_page(service: FaceService) -> None:
    """The first queue names the group, so the board draws one card called Faces rather than one
    card per queue. The record declares nothing, which is what keeps it a tab."""
    assert SuggestionsQueue(service).group_title == "Faces"
    assert [build(service).group_title for build in EVERY_TAB[1:]] == [None, None, None, None]


def _a_sighting() -> Sighting:
    """One appearance, with only the fields a card reads filled in."""
    return Sighting(
        track_id=TRACK,
        asset_id=ASSET,
        started_ms=0,
        ended_ms=0,
        picture_ms=0,
        person_id=None,
        person_name=None,
        confidence=None,
        attribution=None,
    )


def test_a_card_draws_the_first_face_the_shut_vault_does_not_hold_back() -> None:
    """A face on a file in the vault is a padlock on its own screen and has no crop to serve, so a
    card skips it for the next face in the row, and a row of nothing but locked faces draws none."""
    locked = replace(_a_sighting(), track_id="01KZF0VNDL0CKED00000000000", locked=True)
    open_face = replace(_a_sighting(), track_id="01KZF0VND0PENFACE000000000")
    group = ToCheckView(
        kind=ToCheckKind.GROUP,
        id=PILE,
        size=2,
        faces=(locked, open_face),
        status=PileStatus.OPEN,
    )

    shown = _picture(group, "/somewhere")
    assert shown is not None and shown.id == open_face.track_id
    assert _picture(replace(group, faces=(locked,)), "/somewhere") is None


@pytest.mark.parametrize("build", EVERY_TAB)
async def test_each_says_what_it_is_and_what_deciding_one_means(
    service: FaceService, build: type, admin: Viewer
) -> None:
    found = await build(service).survey(admin)

    assert found.title.strip()
    assert found.decision.strip()
    assert found.icon.strip()


async def test_the_record_counts_people_in_people(service: FaceService, admin: Viewer) -> None:
    """The words the board puts after the number: "18 people", and the singular is a different
    phrase rather than the plural again."""
    found = await PeopleKnownQueue(service).survey(admin)

    assert (found.verb, found.verb_one) == ("people", "person")


async def test_an_empty_install_counts_nothing_rather_than_failing(
    service: FaceService, admin: Viewer
) -> None:
    for build in EVERY_TAB:
        found = await build(service).survey(admin)
        assert found.count == 0
        assert found.preview == ()


async def test_the_pictures_are_faces(service: FaceService, admin: Viewer, seeded: str) -> None:
    """A crop rather than a still. The kind is declared so the client does not have to guess it
    from an id, which says nothing about what sort of thing it names."""
    found = await ToNameQueue(service).survey(admin)

    assert [one.kind for one in found.preview] == [FACE]
    assert [one.id for one in found.preview] == [seeded]  # The largest group's first face.


async def test_each_crop_on_the_card_leads_to_the_group_it_was_cut_from(
    service: FaceService, admin: Viewer, seeded: str
) -> None:
    """A still on a board card leads somewhere.

    The group rather than the face: what the card is about is the group waiting for a name, and an
    address for one appearance in it would lead away from the decision rather than into it.
    """
    found = await ToNameQueue(service).survey(admin)

    assert [one.href for one in found.preview] == [f"/organize/faces-to-name/{BIG_PILE}"]


# --- the order, the floor and the filter ---------------------------------------------------------


async def test_a_persons_proposals_come_before_every_group(
    service: FaceService, temp_db: Database, admin: Viewer, proposed: str, seeded_pile: str
) -> None:
    """The order is the answer rather than a presentation choice: agreeing to a person's proposals
    settles every face standing for them in one go, and each one becomes a reference picture that
    improves the next pass. A group settles itself and teaches nothing until it is named."""
    await seed_group(temp_db, "9", 6)

    items, _total, _small = await service.to_check(admin)

    kinds = [one.kind for one in items]
    assert kinds == [ToCheckKind.PERSON, ToCheckKind.GROUP]


async def test_the_groups_are_largest_first(
    service: FaceService, temp_db: Database, admin: Viewer, seeded_pile: str
) -> None:
    """Naming a large group gives that person a reference, and the smaller groups of the same face
    join them on their own, so answered largest first, the same faces are settled in fewer
    presses. Answered smallest first they are settled one group at a time."""
    await seed_group(temp_db, "8", 8)
    await seed_group(temp_db, "6", 6)

    items, _total, _small = await service.to_check(admin)

    assert [one.size for one in items] == [8, 6]


async def test_a_group_of_four_is_neither_listed_nor_counted(
    service: FaceService, temp_db: Database, admin: Viewer, seeded_pile: str
) -> None:
    """The stranger floor. Most of a swept library is groups of one or two people who walked past a
    camera once; listed among the real questions they bury them.

    The count and the list agree, which is the half that matters: a card counting what its own
    screen does not show is the disagreement between a pager and a page that this read exists to
    prevent.
    """
    await seed_group(temp_db, "4", 4)

    items, total, small = await service.to_check(admin)

    assert items == []
    assert total == 0
    # Two: the group of four, and the fixture's group of one.
    assert small == 2


async def test_a_group_of_five_is_listed(
    service: FaceService, temp_db: Database, admin: Viewer, seeded_pile: str
) -> None:
    """The known positive beside the rule above. Five is the floor, so five is in."""
    pile = await seed_group(temp_db, "5", 5)

    items, total, small = await service.to_check(admin)

    assert [one.id for one in items] == [pile]
    assert total == 1
    # The fixture's group of one is still under the floor and still counted apart.
    assert small == 1


async def test_the_small_groups_can_be_opened(
    service: FaceService, temp_db: Database, admin: Viewer, seeded_pile: str
) -> None:
    """Nothing is hidden and nothing is deleted: the line at the foot of the list opens the groups
    the floor holds back, as the same cards. They are people in the library and they are there for
    a reason."""
    small_pile = await seed_group(temp_db, "3", 3)
    big = await seed_group(temp_db, "7", 7)

    items, total, _small = await service.to_check(admin, show=ToCheckShow.SMALL)

    assert {one.id for one in items} == {small_pile, PILE}
    assert total == 2
    assert big not in {one.id for one in items}


async def test_what_was_set_aside_is_a_filter_rather_than_a_tab(
    service: FaceService, temp_db: Database, admin: Viewer, seeded_pile: str
) -> None:
    """Setting a group aside is the same question answered no, so it belongs beside the question.
    It is not in the list of work and it is one press away from it."""
    put_away = await seed_group(temp_db, "2", 6)
    await service.ignore(put_away, admin)

    waiting, _total, _small = await service.to_check(admin)
    aside, total, _none = await service.to_check(admin, show=ToCheckShow.IGNORED)

    assert put_away not in {one.id for one in waiting}
    assert [one.id for one in aside] == [put_away]
    assert total == 1


async def test_the_list_pages_across_both_kinds_as_one(
    service: FaceService, temp_db: Database, admin: Viewer, proposed: str, seeded_pile: str
) -> None:
    """One offset over one list, which is what makes it one list rather than two on one screen. A
    page that starts past the proposals starts that far into the groups; a pager each would be two
    populations under one count."""
    await seed_group(temp_db, "7", 7)

    first, total, _small = await service.to_check(admin, limit=1, offset=0)
    second, _total, _none = await service.to_check(admin, limit=1, offset=1)

    assert total == 2
    assert [one.kind for one in first] == [ToCheckKind.PERSON]
    assert [one.kind for one in second] == [ToCheckKind.GROUP]


async def test_each_tab_reads_its_own_tier_of_the_one_list(
    service: FaceService, temp_db: Database, admin: Viewer, proposed: str, seeded_pile: str
) -> None:
    """The three questions are three tabs, and each asks the one read for its own tier.

    Narrowed in the READ rather than over a page already fetched, which is the whole reason `kind`
    exists as a parameter: a tab that took the whole list and filtered it would ask for the wrong
    rows the moment somebody had more of one kind than a page holds, and its pager would then be
    counting a population its screen was not drawing.
    """
    await seed_group(temp_db, "7", 7)

    people, people_total, _small = await service.to_check(admin, kind=ToCheckKind.PERSON)
    groups, groups_total, _none = await service.to_check(admin, kind=ToCheckKind.GROUP)

    assert [one.kind for one in people] == [ToCheckKind.PERSON]
    assert people_total == 1
    assert [one.kind for one in groups] == [ToCheckKind.GROUP]
    assert groups_total == 1


async def test_a_narrowed_tab_pages_its_own_tier_from_the_top(
    service: FaceService, temp_db: Database, admin: Viewer, proposed: str, seeded_pile: str
) -> None:
    """The offset means what it says on a tab: the first group is the first group.

    Pinned because the whole list subtracts the tiers above each one from the offset, and a tier
    that is not read has no total to subtract, so the arithmetic that makes one list page as one
    has to come out right for one tier on its own as well. With the proposals unread, offset 0 on
    the groups is the largest group and not the second.
    """
    biggest = await seed_group(temp_db, "7", 7)
    await seed_group(temp_db, "6", 6)

    groups, total, _small = await service.to_check(admin, kind=ToCheckKind.GROUP, limit=1, offset=0)

    assert [one.id for one in groups] == [biggest]
    assert total == 2


async def test_the_position_of_a_group_is_where_the_groups_tab_pages_it(
    service: FaceService, temp_db: Database, admin: Viewer, proposed: str, seeded_pile: str
) -> None:
    """`from` on Unnamed faces resolves in that tab's own list: the groups above the floor, largest
    first. The position is checked against the page `to_check` actually serves at that offset, so
    the read and the list cannot drift apart without this failing."""
    await seed_group(temp_db, "8", 8)
    middle = await seed_group(temp_db, "7", 7)
    await seed_group(temp_db, "6", 6)

    at = await service.position_in_to_check(admin, middle, kind=ToCheckKind.GROUP)
    page, _total, _small = await service.to_check(
        admin, kind=ToCheckKind.GROUP, limit=1, offset=at or 0
    )

    assert at == 1
    assert [one.id for one in page] == [middle]


async def test_the_position_on_the_whole_list_counts_the_proposals_above_the_groups(
    service: FaceService, temp_db: Database, admin: Viewer, proposed: str, seeded_pile: str
) -> None:
    """On the whole list the person's proposals lead, so a group's position is behind them: the
    same arithmetic `to_check` subtracts to find where a page starts in the groups."""
    largest = await seed_group(temp_db, "8", 8)

    at = await service.position_in_to_check(admin, largest)
    page, _total, _small = await service.to_check(admin, limit=1, offset=at or 0)

    assert at == 1
    assert [one.id for one in page] == [largest]


async def test_the_position_of_a_small_group_is_taken_among_the_small_groups(
    service: FaceService, temp_db: Database, admin: Viewer, seeded_pile: str
) -> None:
    """`?show=small` is another list, so the position is taken in it: under the floor, largest
    first. And a small group is nowhere on the list above the floor: the top is served."""
    await seed_group(temp_db, "7", 7)
    three = await seed_group(temp_db, "3", 3)

    among_small = await service.position_in_to_check(admin, PILE, show=ToCheckShow.SMALL)
    above_floor = await service.position_in_to_check(admin, three, kind=ToCheckKind.GROUP)

    assert await service.position_in_to_check(admin, three, show=ToCheckShow.SMALL) == 0
    assert among_small == 1
    assert above_floor is None


async def test_the_position_of_a_group_set_aside_is_taken_among_what_was_set_aside(
    service: FaceService, temp_db: Database, admin: Viewer, seeded_pile: str
) -> None:
    """Set aside is the third narrowing and has no floor: a group of one put aside is listed."""
    put_away = await seed_group(temp_db, "2", 6)
    await service.ignore(put_away, admin)
    await service.ignore(PILE, admin)

    assert await service.position_in_to_check(admin, put_away, show=ToCheckShow.IGNORED) == 0
    assert await service.position_in_to_check(admin, PILE, show=ToCheckShow.IGNORED) == 1
    assert await service.position_in_to_check(admin, put_away, kind=ToCheckKind.GROUP) is None


async def test_the_position_of_what_is_not_a_listed_group_is_nothing(
    service: FaceService, temp_db: Database, admin: Viewer, proposed: str, seeded_pile: str
) -> None:
    """A face's id, an id nobody minted, a group asked for on the proposals tab and a person asked
    for on the groups tab all answer None, which the route turns into the top: one answer, so a
    position is not an existence probe. A person is a row of the Suggestions list, and is found in
    it (see the tests below).
    """
    group = await seed_group(temp_db, "7", 7)

    assert await service.position_in_to_check(admin, PERSON, kind=ToCheckKind.GROUP) is None
    assert await service.position_in_to_check(admin, proposed) is None
    assert await service.position_in_to_check(admin, "01HX0000000000000000NOPE0") is None
    assert await service.position_in_to_check(admin, group, kind=ToCheckKind.PERSON) is None


def test_the_tabs_are_the_faces_page_in_the_order_they_are_drawn(service: FaceService) -> None:
    """The row on screen, and the address it lives at.

    The first queue is named for the group, so the board's Faces card opens `/organize/faces`
    rather than a name for one of the states inside it. The rest are named for what they hold.
    Pinned here because the tab row is built from these names and from nothing else.
    """
    assert [build(service).name for build in EVERY_TAB] == [
        "faces",
        "disagreements",
        "faces-to-name",
        "discarded-faces",
        "known-people",
    ]


async def test_each_tab_is_titled_for_what_it_holds(service: FaceService, admin: Viewer) -> None:
    """The words on the tabs, which are the server's and not the client's."""
    titles = [(await build(service).survey(admin)).title for build in EVERY_TAB]

    assert titles == [
        "Faces to confirm",
        "Disagreements",
        "Unnamed faces",
        "Discarded",
        "People Sift can recognize",
    ]


async def test_the_groups_put_aside_are_a_tab_of_their_own(
    service: FaceService, temp_db: Database, admin: Viewer, seeded_pile: str
) -> None:
    """A tab rather than a dropdown on the work (see `SetAsideQueue`). A record: its count is of
    questions already answered, so it can never be worked down to nothing."""
    put_away = await seed_group(temp_db, "2", 6)
    await service.ignore(put_away, admin)

    found = await SetAsideQueue(service).survey(admin)

    assert found.count == 1
    assert [one.id for one in found.preview] != []


# --- putting decisions back ----------------------------------------------------------------------


async def test_taking_back_a_set_aside_puts_the_group_back(
    service: FaceService, admin: Viewer, seeded_pile: str
) -> None:
    await service.ignore(seeded_pile, admin)

    put = await IgnoredRecords(service).reverse(
        admin, "decision-1", json.dumps({"pile_id": seeded_pile})
    )

    assert put is True


async def test_the_name_a_receipt_was_written_under_outlives_the_card_that_wrote_it(
    service: FaceService,
) -> None:
    """A decision is on disk for as long as the library is, and the
    receipts written under these names are reversible because the names are registered as reversers, which
    is exactly what that half of the registry exists for."""
    assert IgnoredRecords(service).name == "ignored"
    assert IdentifiedRecords(service).name == "identified"


async def test_each_record_is_worded_by_the_line_its_decision_says(service: FaceService) -> None:
    """The records wall words a receipt when it is shown, and each queue hands it to the one line
    for its decision: a group set aside reads as discarded, a match names the person and the file."""
    discarded = Recorded(
        id="01R",
        queue="ignored",
        payload=json.dumps({"pile_id": PILE}),
        title="A group of 3 faces set aside",
        detail="",
        decided_at=0,
    )
    matched = Recorded(
        id="01S",
        queue="identified",
        payload=json.dumps({"person_id": FILED_PERSON, "track_ids": [TRACK]}),
        title="Sift named Rasha Emberlin here, 75% sure",
        detail="",
        decided_at=0,
        subjects=(Subject(kind="asset", id=ASSET, name="one.mp4"),),
    )

    ignored = IgnoredRecords(service).worded(discarded)
    identified = IdentifiedRecords(service).worded(matched)

    assert ignored is not None and ignored == ignored_said(discarded)
    assert " discarded " in ignored.said
    assert identified is not None and identified == identified_said(matched)
    assert " recognized " in identified.said


async def test_a_record_this_build_cannot_read_is_not_one_it_can_put_back(
    service: FaceService, admin: Viewer
) -> None:
    """A payload naming neither a person nor any appearances is not a decision this knows how to
    reverse, and saying so is the truth rather than a failure."""
    assert await IdentifiedRecords(service).reverse(admin, "decision-1", "{}") is False


async def test_a_record_shows_the_faces_the_decision_was_taken_on(
    service: FaceService, admin: Viewer, seeded_pile: str
) -> None:
    """A decision that says only "12 faces set aside" cannot be checked, only read.

    The group survives being set aside (that is what makes it reversible), so the faces are
    still there to look at, and the address goes to the group's own screen, which is where somebody
    who disagrees with the decision goes to change it.
    """
    await service.ignore(seeded_pile, admin)

    shown = await IgnoredRecords(service).pictures_of(admin, json.dumps({"pile_id": seeded_pile}))

    assert [one.kind for one in shown] == [FACE]
    assert [one.id for one in shown] == [TRACK]
    assert [one.href for one in shown] == [f"/organize/faces-to-name/{seeded_pile}"]


@pytest.mark.parametrize("payload", ["not json at all", "{}", "[]", '{"pile_id": null}'])
async def test_a_record_written_in_a_shape_this_version_cannot_read_shows_no_pictures(
    service: FaceService, admin: Viewer, payload: str
) -> None:
    """Records outlive the code that wrote them. A payload from an older version, or one this
    version simply does not recognise, is a record with nothing to draw, never an error on a
    screen whose whole job is to list what has already been done."""
    assert await IgnoredRecords(service).pictures_of(admin, payload) == ()


async def test_a_group_that_has_since_gone_shows_no_pictures(
    service: FaceService, admin: Viewer
) -> None:
    """And the same is true of one this user may not be told about: the group is asked for
    through the service, which scopes it, so an answer of nothing covers both cases without this
    having to know which one it is."""
    assert (
        await IgnoredRecords(service).pictures_of(
            admin, json.dumps({"pile_id": "01HX0000000000000000GONE1"})
        )
        == ()
    )


# --- what a decision says it was about -----------------------------------------------------------


@pytest.fixture
async def recorded(temp_db: Database, service: FaceService) -> WorkbenchStore:
    """The service wired to a real decision record.

    Put in place directly: the ordinary fixture has no recorder because most of this file is about
    counting and reversing rather than about writing receipts, and building a second service to
    hold one would be a second arrangement to keep in step.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    return written


async def test_setting_a_group_aside_says_it_was_about_the_group_and_its_files(
    temp_db: Database,
    service: FaceService,
    admin: Viewer,
    seeded_pile: str,
    recorded: WorkbenchStore,
) -> None:
    """Both, and both are free: the tracks were already read to count the faces, and each one
    carries the file it was found in.

    The group is what the decision was taken ON; the files are where somebody will be standing when
    they wonder why a face stopped being offered.
    """
    await service.ignore(seeded_pile, admin)

    (written, _total) = await recorded.recent(limit=1, offset=0)
    rows = await temp_db.fetch_all(
        "SELECT kind, subject_id FROM workbench_decision_subjects WHERE decision_id = ?",
        (written[0].id,),
    )
    assert {(str(row["kind"]), str(row["subject_id"])) for row in rows} == {
        ("pile", seeded_pile),
        ("asset", ASSET),
    }


# --- the proposals: what Sift has a name for and will not act on alone ----------------------------

PERSON = "01HX000000000000000PERSON1"
PROPOSED = "01HX00000000000000TRACK99"


@pytest.fixture
async def proposed(temp_db: Database, seeded_pile: str) -> str:
    """One face Sift has proposed somebody for, in a file an admin can see.

    Between the two lines: attributed to a person, and `suggested` rather than `matched`. It keeps
    its group, because an offered face is one that
    was grouped with a face somebody named. See `name_with_their_group`.
    """
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Wren Halloway', 0)", (PERSON,)
    )
    # The pass that found the face. Agreeing to one reads the scan it came out of (the model that
    # described it is what a new reference is stamped with), so a track with no scan behind it
    # cannot be confirmed at all, which is right and is why this row is here.
    await temp_db.execute(
        "INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count, "
        "identified_count, detector, recognizer, settings_digest, scanned_at) "
        "VALUES (?, 'none_identified', 'fast', 1.0, 1, 1, 0, 'det', 'rec', 'digest', 0)",
        (ASSET,),
    )
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, person_id, confidence, attribution, attributed_at, created_at) "
        "VALUES (?, ?, 0, 0, 1, 1.0, NULL, ?, 0.58, 'suggested', 1000, 0)",
        (PROPOSED, ASSET, PERSON),
    )
    return PROPOSED


async def test_a_match_this_pass_made_can_be_taken_back_from_its_record(
    temp_db: Database, service: FaceService, admin: Viewer, proposed: str
) -> None:
    """The half of a match that needs a receipt of its own.

    Reversed from what the decision wrote down rather than from the state, so a face somebody has
    since agreed to is left where it is: here, the same face moved to `confirmed` before the undo.
    """
    await temp_db.execute(
        "UPDATE face_tracks SET attribution = 'matched' WHERE id = ?", (PROPOSED,)
    )
    payload = json.dumps({"person_id": PERSON, "track_ids": [PROPOSED]})

    assert await IdentifiedRecords(service).reverse(admin, "decision-1", payload) is True
    row = await temp_db.fetch_one(
        "SELECT person_id, attribution, asked_by FROM face_tracks WHERE id = ?", (PROPOSED,)
    )
    # The name comes off and the face is asked about instead, as a question no re-match will
    # recognize again (`FaceService.unmatch`), or the next re-match would name it again at the same
    # score.
    assert row is not None
    assert (str(row["person_id"]), row["attribution"], row["asked_by"]) == (
        PERSON,
        "suggested",
        "undone",
    )


async def test_an_undo_leaves_a_face_somebody_agreed_to_exactly_where_it_is(
    temp_db: Database, service: FaceService, admin: Viewer, proposed: str
) -> None:
    """Somebody's own answer is not this pass's to take back. Only the appearances still carrying
    the match the decision made come off."""
    await temp_db.execute(
        "UPDATE face_tracks SET attribution = 'confirmed' WHERE id = ?", (PROPOSED,)
    )
    payload = json.dumps({"person_id": PERSON, "track_ids": [PROPOSED]})

    assert await IdentifiedRecords(service).reverse(admin, "decision-1", payload) is False
    row = await temp_db.fetch_one("SELECT person_id FROM face_tracks WHERE id = ?", (PROPOSED,))
    assert row is not None and str(row["person_id"]) == PERSON


async def test_an_agreement_with_no_numbers_kept_still_comes_back(
    temp_db: Database, service: FaceService, admin: Viewer, proposed: str
) -> None:
    """The undo walks what the receipt kept, so it has to fall back to the appearances.

    A receipt written by an older release, or by a run where nothing carried a recorded number, has
    an `act` and no map at all, and an undo that walked an empty map would take nothing back and
    report that it had, which is the worst answer a receipt can give.
    """
    await temp_db.execute(
        "UPDATE face_tracks SET attribution = 'confirmed' WHERE id = ?", (PROPOSED,)
    )
    payload = json.dumps({"act": AGREED_WITH_MATCHES, "person_id": PERSON, "track_ids": [PROPOSED]})

    assert await IdentifiedRecords(service).reverse(admin, "decision-1", payload) is True
    row = await temp_db.fetch_one("SELECT attribution FROM face_tracks WHERE id = ?", (PROPOSED,))
    assert row is not None and str(row["attribution"]) == "matched"


async def test_a_records_pictures_open_the_tab_those_faces_are_on(
    temp_db: Database, service: FaceService, admin: Viewer, proposed: str
) -> None:
    """The two acts leave their faces in two different places, so their records open two tabs.

    A run Sift matched on its own is still matched; a run somebody agreed with is confirmed. Sending
    an agreement to the matched tab opens a screen those faces have already left: an empty list
    under a receipt saying how many there were.
    """
    await temp_db.execute(
        "UPDATE face_tracks SET attribution = 'confirmed' WHERE id = ?", (PROPOSED,)
    )
    matched = json.dumps({"person_id": PERSON, "track_ids": [PROPOSED]})
    agreed = json.dumps({"act": AGREED_WITH_MATCHES, "person_id": PERSON, "track_ids": [PROPOSED]})

    from_match = await IdentifiedRecords(service).pictures_of(admin, matched)
    from_agreement = await IdentifiedRecords(service).pictures_of(admin, agreed)

    assert [one.href for one in from_match] == [f"/organize/known-people/{PERSON}?show=matched"]
    assert [one.href for one in from_agreement] == [
        f"/organize/known-people/{PERSON}?show=confirmed"
    ]


async def test_taking_back_an_agreement_leaves_the_match_where_it_was(
    temp_db: Database, service: FaceService, admin: Viewer, proposed: str
) -> None:
    """Two acts are recorded under one name and they come back differently.

    What is being taken back is the AGREEMENT, not the match: Sift still says the appearance is
    this person, exactly as it did before the button was pressed. A face that fell all the way to
    nobody would turn "I take my agreement back" into "Sift was wrong".
    """
    await temp_db.execute(
        "UPDATE face_tracks SET attribution = 'confirmed' WHERE id = ?", (PROPOSED,)
    )
    payload = json.dumps(
        {
            "act": AGREED_WITH_MATCHES,
            "person_id": PERSON,
            "track_ids": [PROPOSED],
            "confidence": {PROPOSED: 0.58},
        }
    )

    assert await IdentifiedRecords(service).reverse(admin, "decision-1", payload) is True
    row = await temp_db.fetch_one(
        "SELECT person_id, attribution FROM face_tracks WHERE id = ?", (PROPOSED,)
    )
    assert row is not None
    assert str(row["person_id"]) == PERSON
    assert str(row["attribution"]) == "matched"


# --- a name a pass filed that the one face in the file does not match -----------------------------

#: The person a folder name filed these files under, and the one appearance in each.
FILED_PERSON = "01HX00000000000000PERSON2"
FILED_ASSET = "01HX0000000000000000ASSET2"
LONE_FACE = "01HX00000000000000TRACK77"
SECOND_FACE = "01HX00000000000000TRACK78"
#: Who the lone face is named as: somebody other than the person the file was filed under.
ELSEWHERE = "01HX00000000000000PERSON4"


async def _file_filed_by_a_folder(
    temp_db: Database, service: FaceService, *, source: str | None = "folder"
) -> None:
    """A second file, filed under a person by a pass, with one face Sift named as somebody else.

    Everything the condition needs and nothing it does not: the file has been scanned by this
    model, and the one appearance in it is recognized as a different person.
    """
    recognizer = (await service.configuration()).recognizer
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Rasha Emberlin', 0)",
        (FILED_PERSON,),
    )
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        (FILED_ASSET, f"digest-{FILED_ASSET}"),
    )
    await temp_db.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, 'two.mp4', 'two.mp4', 0, 0)",
        (f"loc-{FILED_ASSET}", FILED_ASSET, ROOT, FOLDER),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, 0)",
        (FILED_ASSET, FILED_PERSON, source),
    )
    await temp_db.execute(
        "INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count, "
        "identified_count, detector, recognizer, settings_digest, scanned_at) "
        "VALUES (?, 'none_identified', 'fast', 1.0, 1, 1, 0, 'det', ?, 'digest', 0)",
        (FILED_ASSET, recognizer),
    )
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Wren Hale', 0)", (ELSEWHERE,)
    )
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, person_id, confidence, attribution, created_at) "
        "VALUES (?, ?, 0, 0, 1, 1.0, NULL, ?, 0.8, 'matched', 0)",
        (LONE_FACE, FILED_ASSET, ELSEWHERE),
    )
    await _give_a_reference(temp_db, service, FILED_PERSON)


async def _give_a_reference(temp_db: Database, service: FaceService, person_id: str) -> None:
    """One reference picture for that person, described by the model in force.

    The comparison has to mean something before "Sift would not even offer this match" is a
    statement about anybody: a person with nothing to be compared against is simply unknown.
    """
    recognizer = (await service.configuration()).recognizer
    # A real description, packed as the store packs one: the list reads every reference back to
    # compare groups with people, and a blob no description could be is a library Sift never wrote.
    await temp_db.execute(
        "INSERT INTO face_references "
        "(id, person_id, crop_path, crop_digest, embedding, quality, origin, recognizer, "
        "created_at) VALUES (?, ?, 'crop.jpg', 'digest', ?, 1.0, 'confirmed', ?, 0)",
        ("01HX0000000000000000REF01", person_id, recognize.pack(person_vector(0)), recognizer),
    )


@pytest.fixture
async def filed(temp_db: Database, service: FaceService, seeded_pile: str) -> str:
    await _file_filed_by_a_folder(temp_db, service)
    return FILED_ASSET


async def test_a_face_named_as_somebody_else_is_asked_about(
    service: FaceService, admin: Viewer, filed: str
) -> None:
    """The question that runs the other way: is the name already on the file the person in it.

    The pass that filed her read a folder name. The face in it is named as somebody else, which
    is evidence against the filing.
    """
    items, total, _small = await service.to_check(admin)

    mismatched = [one for one in items if one.kind is ToCheckKind.MISMATCH]
    assert [one.id for one in mismatched] == [FILED_ASSET]
    assert mismatched[0].person_id == FILED_PERSON
    assert mismatched[0].person_name == "Rasha Emberlin"
    assert mismatched[0].source == "folder"
    assert mismatched[0].size == 1
    # Ahead of every question on the list: a wrong answer already in the library comes before a
    # missing one.
    assert items[0].kind is ToCheckKind.MISMATCH
    # And counted with the rest, because it is one press of the same work.
    assert total == len(items)


async def test_a_face_sift_did_place_with_them_is_not_asked_about(
    temp_db: Database, service: FaceService, admin: Viewer, filed: str
) -> None:
    """Within the bar, so Sift would attach or offer it, which is the opposite of this list."""
    await temp_db.execute(
        "UPDATE face_tracks SET person_id = ?, confidence = 0.7, attribution = 'matched' "
        "WHERE id = ?",
        (FILED_PERSON, LONE_FACE),
    )

    items, _total, _small = await service.to_check(admin)

    assert [one for one in items if one.kind is ToCheckKind.MISMATCH] == []


async def test_a_face_that_matches_nobody_or_is_only_asked_about_is_no_disagreement(
    temp_db: Database, service: FaceService, admin: Viewer, filed: str
) -> None:
    """Only a NAME is evidence against the filing: a face matching nobody (covered, poorly lit)
    says nothing about who it is, and a question about somebody else is not a name. A face a
    person confirmed as somebody else is evidence as much as one Sift recognized."""
    for person_id, attribution in ((None, None), (ELSEWHERE, "suggested")):
        await temp_db.execute(
            "UPDATE face_tracks SET person_id = ?, attribution = ? WHERE id = ?",
            (person_id, attribution, LONE_FACE),
        )
        items, _total, _small = await service.to_check(admin)
        assert [one for one in items if one.kind is ToCheckKind.MISMATCH] == [], attribution

    await temp_db.execute(
        "UPDATE face_tracks SET person_id = ?, attribution = 'confirmed' WHERE id = ?",
        (ELSEWHERE, LONE_FACE),
    )
    items, _total, _small = await service.to_check(admin)
    assert [one.id for one in items if one.kind is ToCheckKind.MISMATCH] == [FILED_ASSET]


async def test_a_file_with_two_faces_is_not_asked_about(
    temp_db: Database, service: FaceService, admin: Viewer, filed: str
) -> None:
    """Two faces say nothing about the name: the other one may well be her.

    One face is the whole of what makes the claim safe to make, which is why this is a condition of
    the read rather than a caution in the sentence it draws.
    """
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, person_id, created_at) VALUES (?, ?, 0, 0, 1, 1.0, NULL, NULL, 0)",
        (SECOND_FACE, FILED_ASSET),
    )

    items, _total, _small = await service.to_check(admin)

    assert [one for one in items if one.kind is ToCheckKind.MISMATCH] == []


async def test_a_name_somebody_put_there_themselves_is_not_asked_about(
    temp_db: Database, service: FaceService, admin: Viewer, filed: str
) -> None:
    """A row with no source word was written by a person's own hand, or by this very slice.

    Neither is a pass reading evidence off the file, and neither is something to ask them about
    again, so the source words are a condition of the read.
    """
    await temp_db.execute(
        "UPDATE asset_people SET source = NULL WHERE asset_id = ?", (FILED_ASSET,)
    )

    items, _total, _small = await service.to_check(admin)

    assert [one for one in items if one.kind is ToCheckKind.MISMATCH] == []


async def test_a_person_with_no_pictures_still_disagrees_with_a_face_named_somebody_else(
    temp_db: Database, service: FaceService, admin: Viewer, filed: str
) -> None:
    """The evidence is the name on the face, which her own pictures play no part in."""
    await temp_db.execute("DELETE FROM face_references WHERE person_id = ?", (FILED_PERSON,))

    items, _total, _small = await service.to_check(admin)

    assert [one.id for one in items if one.kind is ToCheckKind.MISMATCH] == [FILED_ASSET]


async def test_a_face_already_refused_as_that_person_is_not_asked_about(
    temp_db: Database, service: FaceService, admin: Viewer, filed: str
) -> None:
    """The question has been asked and answered. Asking again is how a review list is abandoned."""
    await temp_db.execute(
        "INSERT INTO face_rejections (track_id, person_id, created_at) VALUES (?, ?, 0)",
        (LONE_FACE, FILED_PERSON),
    )

    items, _total, _small = await service.to_check(admin)

    assert [one for one in items if one.kind is ToCheckKind.MISMATCH] == []


async def test_a_disagreement_is_asked_only_of_somebody_shown_the_file_and_told_the_person(
    temp_db: Database, service: FaceService, admin: Viewer, filed: str
) -> None:
    """A card here is a sentence naming her about a file, so it needs both: nobody is asked about a
    file hidden from them, nor about a person hidden from them (not even where "leave a locked
    tile" draws that person's file as a padlock), and the row is absent rather than nameless."""
    file_hidden = await create_user(temp_db, Role.ADMIN)
    person_hidden = replace(
        await create_user(temp_db, Role.ADMIN), concealment=Concealment.PLACEHOLDER
    )
    for kind, object_id, viewer in (
        ("asset", FILED_ASSET, file_hidden),
        ("person", FILED_PERSON, person_hidden),
    ):
        statement, parameters = hidden_row(kind, object_id, viewer.id)
        # The statement is one of a fixed set chosen by kind in `testing.library.hidden_row`, and
        # every value binds; nothing typed reaches the text.
        await temp_db.execute(statement, parameters)  # nosemgrep: sift-no-string-built-sql

    told, total = await service.filed_faces_that_do_not_match(admin, limit=10, offset=0)
    assert ([one.id for one in told], total) == ([FILED_ASSET], 1)
    assert FILED_ASSET in await service._shown_of(person_hidden, [FILED_ASSET]), (
        "the locked tile is shown, so what keeps the card away below is the person"
    )
    for viewer in (file_hidden, person_hidden):
        assert await service.filed_faces_that_do_not_match(viewer, limit=10, offset=0) == ([], 0)


async def test_a_page_the_disagreements_fill_still_counts_the_questions_below_it(
    temp_db: Database, service: FaceService, admin: Viewer, filed: str
) -> None:
    """Asked for one row, the one row is the disagreement (a wrong answer comes first), and the
    total still counts the question about her that the next page starts with."""
    await temp_db.execute(
        "UPDATE face_tracks SET person_id = ?, confidence = 0.5, attribution = 'suggested' "
        "WHERE id = ?",
        (FILED_PERSON, TRACK),
    )
    whole, whole_total, _small = await service.to_check(admin)
    assert [one.kind for one in whole] == [ToCheckKind.MISMATCH, ToCheckKind.PERSON]

    page, total, _small = await service.to_check(admin, limit=1)

    assert [(one.kind, one.id) for one in page] == [(ToCheckKind.MISMATCH, FILED_ASSET)]
    assert total == whole_total == 2


# ---------------------------------------------------------------------------------------------
# The anchor on the other two tabs, so Suggestions and Disagreements do not come back at the top
# after every look at a person. A person and a disagreement are ranked by reading the tier
# (`look_alikes`, `filed_faces_that_do_not_match`), and each position is checked against the page
# `to_check` serves at it, so the two cannot drift.
# ---------------------------------------------------------------------------------------------

#: A second person Sift proposes somebody for, surer than the fixture's, so they rank first.
SURER_PERSON = "01HX00000000000000PERSON3"
SURER_FACE = "01HX00000000000000TRACK98"
#: A second file filed under the same person, whose face sits after the first in track order.
LATER_FILED_ASSET = "01HX0000000000000000ASSET3"
LATER_LONE_FACE = "01HX00000000000000TRACK79"


async def _a_surer_proposal(temp_db: Database) -> None:
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Esme Wrenfield', 0)",
        (SURER_PERSON,),
    )
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, person_id, confidence, attribution, attributed_at, created_at) "
        "VALUES (?, ?, 1, 1, 1, 1.0, NULL, ?, 0.91, 'suggested', 1000, 0)",
        (SURER_FACE, ASSET, SURER_PERSON),
    )


async def _a_later_filed_file(temp_db: Database, service: FaceService) -> None:
    recognizer = (await service.configuration()).recognizer
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        (LATER_FILED_ASSET, f"digest-{LATER_FILED_ASSET}"),
    )
    await temp_db.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, 'three.mp4', 'three.mp4', 0, 0)",
        (f"loc-{LATER_FILED_ASSET}", LATER_FILED_ASSET, ROOT, FOLDER),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) "
        "VALUES (?, ?, 'folder', 0)",
        (LATER_FILED_ASSET, FILED_PERSON),
    )
    await temp_db.execute(
        "INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count, "
        "identified_count, detector, recognizer, settings_digest, scanned_at) "
        "VALUES (?, 'none_identified', 'fast', 1.0, 1, 1, 0, 'det', ?, 'digest', 0)",
        (LATER_FILED_ASSET, recognizer),
    )
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, person_id, confidence, attribution, created_at) "
        "VALUES (?, ?, 0, 0, 1, 1.0, NULL, ?, 0.8, 'matched', 0)",
        (LATER_LONE_FACE, LATER_FILED_ASSET, ELSEWHERE),
    )


async def test_the_position_of_a_person_is_where_the_suggestions_tab_pages_them(
    service: FaceService, temp_db: Database, admin: Viewer, proposed: str
) -> None:
    """`from` on Suggestions names a PERSON, ranked by their surest proposal: the order
    `look_alikes` serves. Checked against the page the tab actually serves at that offset."""
    await _a_surer_proposal(temp_db)

    first = await service.position_in_to_check(admin, SURER_PERSON, kind=ToCheckKind.PERSON)
    at = await service.position_in_to_check(admin, PERSON, kind=ToCheckKind.PERSON)
    page, total, _small = await service.to_check(
        admin, kind=ToCheckKind.PERSON, limit=1, offset=at or 0
    )

    assert first == 0
    assert at == 1
    assert total == 2
    assert [one.id for one in page] == [PERSON]


async def test_the_first_question_is_the_surest_match_and_the_advice_says_so(
    service: FaceService, temp_db: Database, admin: Viewer, proposed: str
) -> None:
    """Ranked by a person's best match, not by how many faces stand behind them, so the advice at
    the top of the tab names that order: here the first person has one face and the second three."""
    await _a_surer_proposal(temp_db)
    for at in (2, 3):
        await temp_db.execute(
            "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
            "pile_id, person_id, confidence, attribution, attributed_at, created_at) "
            "VALUES (?, ?, ?, ?, 1, 1.0, NULL, ?, 0.52, 'suggested', 1000, 0)",
            (f"01HX00000000000000TRACK9{at}", ASSET, at, at, PERSON),
        )

    found = await SuggestionsQueue(service).survey(admin)
    items, _total, _small = await service.to_check(admin, kind=ToCheckKind.PERSON)

    assert [(one.id, one.size) for one in items] == [(SURER_PERSON, 1), (PERSON, 3)]
    assert found.advice is not None
    assert "surest" in found.advice
    assert "most faces" not in found.advice


async def test_the_position_of_a_disagreement_is_where_its_tab_pages_it(
    service: FaceService, temp_db: Database, admin: Viewer, filed: str
) -> None:
    """`from` on Disagreements names the FILE (the row's own id) in the tier's order."""
    await _a_later_filed_file(temp_db, service)

    first = await service.position_in_to_check(admin, FILED_ASSET, kind=ToCheckKind.MISMATCH)
    at = await service.position_in_to_check(admin, LATER_FILED_ASSET, kind=ToCheckKind.MISMATCH)
    page, total, _small = await service.to_check(
        admin, kind=ToCheckKind.MISMATCH, limit=1, offset=at or 0
    )

    assert first == 0
    assert at == 1
    assert total == 2
    assert [one.id for one in page] == [LATER_FILED_ASSET]


async def test_the_position_of_a_person_on_the_whole_list_counts_the_disagreements_above(
    service: FaceService, temp_db: Database, admin: Viewer, proposed: str, filed: str
) -> None:
    """On the whole list the disagreements lead, so a person's position is behind them, and a
    row asked for on the wrong tab is nowhere, which the route turns into the top."""
    at = await service.position_in_to_check(admin, PERSON)
    page, _total, _small = await service.to_check(admin, limit=1, offset=at or 0)

    assert at == 1
    assert [one.id for one in page] == [PERSON]
    assert await service.position_in_to_check(admin, FILED_ASSET) == 0
    assert await service.position_in_to_check(admin, PERSON, kind=ToCheckKind.MISMATCH) is None
    assert await service.position_in_to_check(admin, FILED_ASSET, kind=ToCheckKind.PERSON) is None


async def test_the_faces_card_counts_questions_not_people(
    service: FaceService, admin: Viewer
) -> None:
    """The board's Faces card draws the first queue's words after the group's number (every
    pending tab added up, people and files and groups together), not "people Sift is proposing",
    which only the Faces to confirm tab counts."""
    found = await SuggestionsQueue(service).survey(admin)

    assert (found.verb, found.verb_one) == ("questions about faces", "question about faces")


# --- what each record answers when asked to be taken back ---------------------------------------


class _Restoring:
    """The feature as a group's Undo reaches it, and as a record's pictures are scoped by it."""

    def __init__(self) -> None:
        self.restored: list[str] = []

    async def restore(self, pile_id: str) -> bool:
        self.restored.append(pile_id)
        return True

    async def touchable_faces(self, viewer: Any, track_ids: list[str]) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(allowed=list(track_ids))


@pytest.mark.parametrize("queue", [SuggestionsQueue, DisagreementsQueue, PeopleKnownQueue])
async def test_a_queue_whose_receipts_belong_to_another_record_takes_nothing_back(
    queue: Any,
) -> None:
    """Its answers are written under another name, which is where each is taken back: an Undo
    here would be a second way to put back one answer."""
    feature = _Restoring()
    assert await queue(feature).reverse(None, "01R", '{"pile_id": "p"}') is False
    assert feature.restored == []


@pytest.mark.parametrize("queue", [ToNameQueue, SetAsideQueue])
async def test_undoing_a_groups_answer_puts_the_group_back_among_the_waiting(queue: Any) -> None:
    feature = _Restoring()
    assert await queue(feature).reverse(None, "01R", json.dumps({"pile_id": "pile-1"}))
    assert feature.restored == ["pile-1"]


async def test_a_re_match_record_this_build_cannot_read_draws_nothing() -> None:
    records = IdentifiedRecords(_Restoring())  # type: ignore[arg-type]
    assert await records.pictures_of(None, "not json") == ()  # type: ignore[arg-type]
    assert await records.pictures_of(None, '{"person_id": "p"}') == ()  # type: ignore[arg-type]


async def test_a_refused_runs_faces_are_drawn_and_lead_nowhere() -> None:
    """The name came off, so the crops are on no tab of hers: a link would open a screen that
    cannot hold them."""
    records = IdentifiedRecords(_Restoring())  # type: ignore[arg-type]
    payload = json.dumps({"person_id": "p", "track_ids": ["t1", "t2"], "act": REFUSED_FACES})
    drawn = await records.pictures_of(None, payload)  # type: ignore[arg-type]
    assert [(one.id, one.href) for one in drawn] == [("t1", None), ("t2", None)]
