# SPDX-License-Identifier: AGPL-3.0-or-later
"""Get to know Sift, judged from a library written by hand on a fixed clock.

Every table is the application's own (the whole schema is built), and the Organize board is a real
`Workbench` with stand-in piles.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import cast

import pytest

import sift.main  # noqa: F401 (every component registers its tables on import)
from sift.kernel.access import Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Lacking, LibraryStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs.families import Family
from sift.kernel.workbench import Band, Preview, Summary, Workbench
from sift.slices.insights import path
from sift.slices.insights.path import (
    GOALS,
    PATHS,
    PathService,
    achievement_head,
)
from sift.slices.insights.path_models import Goal
from sift.slices.insights.recaps_models import RecapHead
from sift.slices.insights.store import day_bounds, recaps_of, write_recap
from sift.testing.fixtures import create_user

#: A Wednesday, at noon on this device's clock.
TODAY = date(2026, 9, 23)
NOW = datetime(2026, 9, 23, 12, 0).timestamp()


def noon(day: date) -> int:
    return day_bounds(day)[0] + 12 * 3600


@dataclass
class Pile:
    """One Organize pile, answering from values the test set."""

    name: str
    count: int
    band: Band = Band.DECISION
    group: str | None = None
    group_title: str | None = None
    purpose: str | None = None
    title: str = "Things"
    verb: str = "things to answer"
    verb_one: str = "thing to answer"

    @property
    def reversible(self) -> bool:
        return True

    async def available(self) -> bool:
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        return Summary(
            name=self.name,
            title=self.title,
            decision="Say yes or no.",
            verb=self.verb,
            verb_one=self.verb_one,
            icon="inbox",
            count=self.count,
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        return False


@dataclass
class Library:
    db: Database
    admin: Viewer
    guest: Viewer
    board: Workbench
    path: PathService

    async def run(self, sql: str, params: tuple[object, ...] = ()) -> None:
        await self.db.execute(sql, params)

    async def decision(
        self,
        queue: str,
        at: int,
        *,
        verb: str = "decided",
        user: Viewer | None = None,
        undone: bool = False,
    ) -> None:
        who = user or self.admin
        await self.run(
            "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
            " decided_at, reversed_at, verb, actor_kind, actor_id)"
            " VALUES (?, ?, ?, 'A decision', '', '{}', ?, ?, ?, 'user', ?)",
            (new_id(), queue, who.id, at, at + 1 if undone else None, verb, who.id),
        )

    async def asset(self) -> str:
        asset_id = new_id()
        await self.run(
            "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
            " VALUES (?, ?, 1, 'video', 1)",
            (asset_id, f"digest-{asset_id}"),
        )
        return asset_id

    async def play(self, at: int, *, user: Viewer | None = None) -> None:
        who = user or self.admin
        await self.run(
            "INSERT INTO plays (id, user_id, asset_id, started_at, duration_ms, made_at)"
            " VALUES (?, ?, ?, ?, 600000, ?)",
            (new_id(), who.id, await self.asset(), at, at),
        )


@pytest.fixture
async def library(temp_db: Database, settings: Settings) -> Library:
    await temp_db.initialize_schema()
    board = Workbench()
    roots = LibraryStore(temp_db, settings)
    return Library(
        db=temp_db,
        admin=await create_user(temp_db, Role.ADMIN),
        guest=await create_user(temp_db, Role.GUEST),
        board=board,
        path=PathService(temp_db, board, library=roots, now=lambda: NOW),
    )


async def goals(library: Library, viewer: Viewer | None = None) -> dict[str, Goal]:
    """Every goal the paths hold for this User, read the way the section reads them."""
    answer = await library.path.summary(viewer or library.admin)
    return {goal.id: goal for one in answer.paths for goal in one.goals}


async def achieved(library: Library, viewer: Viewer | None = None) -> list[RecapHead]:
    """Read the paths (which freezes what a page may judge), then the achievements now filed."""
    who = viewer or library.admin
    await library.path.summary(who)
    heads = [achievement_head(row) for row in await recaps_of(library.db, who.id)]
    return [head for head in heads if head is not None]


# --- the paths ------------------------------------------------------------------------------------


def test_every_goal_is_on_exactly_one_path_and_every_path_names_real_goals() -> None:
    placed = [name for one in PATHS for name in one.goals]
    assert sorted(placed) == sorted(rule.id for rule in GOALS)
    assert len(placed) == len(set(placed))


def test_the_paths_are_drawn_from_the_whole_app_and_never_only_from_organize() -> None:
    """Organize is one path of several: most goals are done somewhere else in Sift."""
    by_id = {rule.id: rule for rule in GOALS}
    elsewhere = [
        name for one in PATHS for name in one.goals if not by_id[name].href.startswith("/organize")
    ]
    assert len(elsewhere) > len(GOALS) // 2
    assert {by_id[name].href.split("/")[1] for name in elsewhere} >= {
        "settings",
        "browse",
        "loops",
        "collections",
        "theater",
        "downloads",
    }


async def test_a_path_lists_its_goals_in_order_and_a_frozen_milestone_is_done_on_its_day(
    library: Library,
) -> None:
    await library.play(noon(date(2025, 9, 1)))
    answer = await library.path.summary(library.admin)
    assert [one.id for one in answer.paths] == ["set_up", "watch", "organize"]
    watch = answer.paths[1]
    assert watch.title == "Watch and keep"
    assert watch.sentence
    assert [goal.id for goal in watch.goals] == list(PATHS[1].goals)
    year = {goal.id: goal for goal in watch.goals}["year_of_sift"]
    assert year.done
    assert year.done_at == day_bounds(date(2026, 9, 1))[0]
    assert not {goal.id: goal for goal in watch.goals}["make_loop"].done


async def test_a_guest_walks_only_the_paths_and_goals_a_guest_can_reach(library: Library) -> None:
    answer = await library.path.summary(library.guest)
    assert [(one.id, [goal.id for goal in one.goals]) for one in answer.paths] == [
        (
            "watch",
            [
                "view_something",
                "star_or_rate",
                "make_loop",
                "keep_filter",
                "open_theater",
                "year_of_sift",
            ],
        )
    ]


# --- the goals that are a first time -----------------------------------------------------------------


async def test_every_first_time_goal_is_judged_from_the_record(library: Library) -> None:
    fresh = await goals(library)
    first_times = [rule.id for rule in GOALS if not rule.milestone]
    assert [name for name in fresh if name in first_times] == [
        name for one in PATHS for name in one.goals if name in first_times
    ]
    assert not any(fresh[name].done for name in first_times)

    day = TODAY - timedelta(days=40)
    at = noon(day)
    admin = library.admin.id
    await library.run(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, 'Library', ?, ?)",
        (new_id(), "/library", at),
    )
    await library.run(
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at)"
        " VALUES (?, 'scan', ?, ?, ?)",
        (new_id(), at, at, at + 60),
    )
    await library.decision("identified", at)
    await library.decision("folders", at)
    viewed = await library.asset()
    await library.run(
        "INSERT INTO asset_user_state (asset_id, user_id, view_count, last_viewed_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (viewed, admin, at, at),
    )
    await library.run(
        "INSERT INTO opinions (id, user_id, subject_kind, subject_id, kind, before, after, at)"
        " VALUES (?, ?, 'asset', ?, 'favorite', 0, 1, ?)",
        (new_id(), admin, viewed, at),
    )
    await library.run(
        "INSERT INTO collections (id, name, owner_id, created_at, created_by_kind,"
        " created_by_user_id) VALUES (?, 'Mine', ?, ?, 'user', ?)",
        (new_id(), admin, at, admin),
    )
    await library.decision("ledger", at, verb="enriched")
    await library.run(
        "INSERT INTO saved_searches (id, user_id, name, query, created_at)"
        " VALUES (?, ?, 'Kept', '{}', ?)",
        (new_id(), admin, at),
    )
    await library.run(
        "INSERT INTO loops (id, asset_id, start_ms, end_ms, created_by, created_at)"
        " VALUES (?, ?, 0, 20000, ?, ?)",
        (new_id(), viewed, admin, at),
    )
    await library.run(
        "INSERT INTO theater_sessions (id, user_id, session, started_at, made_at)"
        " VALUES (?, ?, 'one', ?, ?)",
        (new_id(), admin, at, at),
    )
    await library.run(
        "INSERT INTO downloads (id, url, url_hash, state, asset_id, created_at, finished_at)"
        " VALUES (?, 'https://example.invalid/a', 'hash-a', 'done', ?, ?, ?)",
        (new_id(), viewed, at - 60, at),
    )

    done = await goals(library)
    assert [name for name in first_times if not done[name].done] == []
    assert {done[name].done_at for name in first_times} == {at, at + 60}
    assert all(goal.help and goal.href.startswith("/") for goal in done.values())


async def test_a_decision_somebody_else_made_does_not_tick_this_users_goal(
    library: Library,
) -> None:
    other = await create_user(library.db, Role.ADMIN)
    await library.decision("identified", noon(TODAY), user=other)
    assert not (await goals(library))["name_face"].done


# --- the achievements -----------------------------------------------------------------------------


async def test_an_achievement_is_frozen_once_on_the_day_it_happened(library: Library) -> None:
    first = date(2025, 9, 1)
    await library.play(noon(first))

    once = await achieved(library)
    again = await achieved(library)
    assert [head.period for head in once] == ["achievement:year_of_sift"]
    assert [head.id for head in again] == [head.id for head in once]
    assert once[0].made_at == day_bounds(date(2026, 9, 1))[0]
    assert once[0].span == "September 1, 2026"
    rows = await library.db.fetch_all(
        "SELECT COUNT(*) AS n FROM recaps WHERE user_id = ?", (library.admin.id,)
    )
    assert int(rows[0]["n"]) == 1


async def test_a_card_this_user_emptied_is_an_achievement(library: Library) -> None:
    library.board.register(Pile("folders", 0, title="Folders to review"))
    library.board.register(Pile("shoots", 0))
    await library.decision("folders", noon(TODAY - timedelta(days=2)))

    heads = await achieved(library)
    assert [(head.period, head.span) for head in heads] == [
        ("achievement:queue_emptied", "September 21, 2026")
    ]
    # Frozen: a later decision on another emptied card does not make a second one.
    await library.decision("shoots", noon(TODAY))
    again = await achieved(library)
    assert [head.id for head in again] == [head.id for head in heads]


# --- the board's cards, and the milestones in full ----------------------------------------------


async def test_a_record_of_a_group_with_no_card_rides_on_nothing(library: Library) -> None:
    library.board.register(Pile("folders", 5))
    library.board.register(Pile("discarded", 9, band=Band.RECORD, group="nobody"))
    cards = path.cards_of(await library.board.board(library.admin))
    assert [(card.lead.name, card.names) for card in cards] == [("folders", ["folders"])]


async def test_a_groups_waiting_piles_join_the_first_ones_card_and_its_records_ride_on_it(
    library: Library,
) -> None:
    """The board's own joining: the waiting piles of one group are one card under the first, their
    counts added; a record of the group rides on that card uncounted; a pile with no group stands
    alone however many others have none."""
    library.board.register(Pile("folders", 2, group="sorting", group_title="Sorting"))
    library.board.register(Pile("tags", 5))
    library.board.register(Pile("shoots", 3, group="sorting"))
    library.board.register(Pile("moved", 7, band=Band.RECORD, group="sorting"))
    library.board.register(Pile("names", 1))

    cards = path.cards_of(await library.board.board(library.admin))

    assert [(card.lead.name, card.names, card.count, card.title) for card in cards] == [
        ("folders", ["folders", "shoots", "moved"], 5, "Sorting"),
        ("tags", ["tags"], 5, "Things"),
        ("names", ["names"], 1, "Things"),
    ]


@dataclass
class ReceiptsOnly:
    """A reverser with no card of its own, declaring the group whose card it records for."""

    name: str
    group: str | None

    @property
    def reversible(self) -> bool:
        return True

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        return False


async def test_a_decision_recorded_under_a_receipt_only_name_credits_its_groups_card(
    library: Library,
) -> None:
    """Naming a group on the Faces card is recorded under a name that is no card: the card carries
    that name once, a reverser of another group adds nothing, and emptying the card is credited."""
    library.board.register(Pile("faces", 0, group="faces"))
    library.board.register_reverser(ReceiptsOnly("identified", "faces"))
    library.board.register_reverser(ReceiptsOnly("elsewhere", "other"))
    library.board.register_reverser(ReceiptsOnly("loose", None))
    await library.decision("identified", noon(TODAY - timedelta(days=1)))

    cards = await library.path._cards(library.admin)
    assert [(card.lead.name, card.names) for card in cards] == [("faces", ["faces", "identified"])]
    assert await library.path._cards(library.guest) == []

    heads = await achieved(library)
    assert [(head.period, head.span) for head in heads] == [
        ("achievement:queue_emptied", "September 22, 2026")
    ]


async def test_only_the_achievements_the_paths_know_are_goals(library: Library) -> None:
    """A period's recap and an achievement no longer awarded are in the same table; neither is
    a goal, and neither marks one done."""
    await write_recap(library.db, library.admin.id, "month:2026-08", "[]")
    await write_recap(library.db, library.admin.id, "achievement:retired", "[]")
    assert await achieved(library) == []
    assert not any(goal.done for goal in (await goals(library)).values())


async def named_faces(library: Library, faces: int, at: int, *, act: str = "named-groups") -> None:
    """One press on the faces board that named `faces` faces, as the board writes it."""
    payload = json.dumps({"act": act, "track_ids": [f"t{one}" for one in range(faces)]})
    await library.run(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
        " decided_at, verb, actor_kind, actor_id)"
        " VALUES (?, 'identified', ?, 'Named', '', ?, ?, 'decided', 'user', ?)",
        (new_id(), library.admin.id, payload, at, library.admin.id),
    )


async def test_a_faces_milestone_is_dated_the_day_the_count_crossed_it(library: Library) -> None:
    crossed = TODAY - timedelta(days=40)
    await named_faces(library, 60, noon(crossed - timedelta(days=9)))
    await named_faces(library, 50, noon(crossed))
    await named_faces(library, 900, noon(TODAY - timedelta(days=2)))

    heads = await achieved(library)
    assert {(head.period, head.span) for head in heads} == {
        ("achievement:faces_100", "August 14, 2026"),
        ("achievement:faces_1000", "September 21, 2026"),
    }
    # Both frozen: the next read judges nothing and files nothing.
    await library.path.summary(library.admin)
    rows = await library.db.fetch_all(
        "SELECT COUNT(*) AS n FROM recaps WHERE user_id = ?", (library.admin.id,)
    )
    assert int(rows[0]["n"]) == 2


async def test_a_first_sitting_on_the_29th_of_february_comes_round_on_the_1st_of_march(
    library: Library,
) -> None:
    await library.play(noon(date(2024, 2, 29)))
    heads = await achieved(library)
    assert [(head.period, head.span) for head in heads] == [
        ("achievement:year_of_sift", "March 1, 2025")
    ]


async def test_a_year_not_yet_come_round_is_no_achievement(library: Library) -> None:
    """A first sitting a day short of a year ago is not a year of Sift yet: nothing is filed, and
    the goal is not done."""
    await library.play(noon(TODAY.replace(year=TODAY.year - 1) + timedelta(days=1)))
    assert await achieved(library) == []
    answer = await library.path.summary(library.admin)
    assert not {goal.id: goal for goal in answer.paths[1].goals}["year_of_sift"].done


async def test_a_card_emptied_by_somebody_else_is_not_this_users(library: Library) -> None:
    library.board.register(Pile("folders", 0))
    other = await create_user(library.db, Role.ADMIN)
    await library.decision("folders", noon(TODAY - timedelta(days=2)), user=other)
    assert await achieved(library) == []


@dataclass
class Counted:
    """The two library counts the fingerprinted milestone reads, as the test sets them. The counts
    themselves are the content model's, proved by its own suite; what is under test here is the
    judgement made from them."""

    files: int
    lacking: int

    async def asset_count(self) -> int:
        return self.files

    async def count_lacking(self, lacks: Sequence[object]) -> Lacking:
        return Lacking(each=(self.lacking,), files=self.lacking)


def counted(files: int, lacking: int) -> ContentStore:
    return cast(ContentStore, Counted(files, lacking))


async def test_the_helper_freezes_what_it_may_judge_once(library: Library) -> None:
    await library.play(noon(date(2025, 9, 1)))
    finished = noon(TODAY - timedelta(days=3))
    await library.run(
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (new_id(), Family.FINGERPRINT.value, finished - 60, finished, finished),
    )
    made = await path.make_due(library.db, library.admin.id, TODAY, content=counted(3, 0))
    assert sorted(made) == ["library_fingerprinted", "year_of_sift"]
    assert await path.make_due(library.db, library.admin.id, TODAY, content=counted(3, 0)) == []
    heads = {head.period: head.span for head in await achieved(library)}
    assert heads["achievement:library_fingerprinted"] == "September 20, 2026"


async def test_every_file_fingerprinted_is_an_admins_and_only_when_it_is_true(
    library: Library,
) -> None:
    for files, lacking in ((0, 0), (3, 1)):
        made = await path.make_due(
            library.db, library.admin.id, TODAY, content=counted(files, lacking)
        )
        assert made == [], (files, lacking)
    assert await path.make_due(library.db, library.guest.id, TODAY, content=counted(3, 0)) == []
    # No fingerprinting run on record: dated the day it was noticed.
    whole = await path.library_fingerprinted(library.db, counted(3, 0), TODAY)
    assert whole is not None and whole.day == TODAY
