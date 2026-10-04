# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Insights captures because nothing else keeps it: the pages opened, the sittings they made,
the files that left, and a User's own history paused and cleared."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from sift.kernel.access import Role, Viewer, arrivals
from sift.kernel.access.sentences import departures_kept, text_of
from sift.kernel.client import Client
from sift.kernel.db import Database
from sift.kernel.ids import new_id, timestamp_ms
from sift.kernel.use_history import RECORD_KEY, clear_history_of
from sift.slices.insights import capture, metrics, schema
from sift.slices.insights.capture import SESSION_GAP_MS, Visit, record_visits
from sift.slices.insights.tests.conftest import END, START, World, at

pytestmark = pytest.mark.integration

PHONE = Client("phone", "a-phone-of-sixteen")
DESK = Client("app", "a-desk-of-sixteen-x")
NOW = 10_000_000_000


@dataclass
class _Access:
    """The access layer, as far as a visit asks it: who may be shown what, Hidden open or shut."""

    seen: set[str] = field(default_factory=set)
    hidden: set[str] = field(default_factory=set)

    async def _one(self, viewer: Viewer, ref: str) -> object | None:
        if ref in self.seen or (ref in self.hidden and viewer.show_hidden):
            return object()
        return None

    visible_person = visible_tag = visible_site = visible_collection = _one
    visible_photo_set = visible_song = get_folder = _one


@dataclass
class _Preferences:
    keep: bool = True

    async def get_app(self, key: str) -> Any:
        raise AssertionError(key)

    async def get_user(self, user_id: str, key: str) -> Any:
        assert key == RECORD_KEY
        return self.keep


def _visit(place: str = "wall", ref: str = "library", **times: int) -> Visit:
    return Visit(
        id=new_id().lower() + "x",
        place=place,
        ref=ref,
        opened_ago_ms=times.get("opened", 60_000),
        last_ago_ms=times.get("last", 0),
        front_ms=times.get("front", 30_000),
    )


async def _record(
    world: World,
    visits: list[Visit],
    *,
    client: Client = PHONE,
    access: _Access | None = None,
    keep: bool = True,
    now_ms: int = NOW,
    viewer: Viewer | None = None,
) -> int:
    return await record_visits(
        world.db,
        access or _Access(),  # type: ignore[arg-type]
        _Preferences(keep),
        viewer or Viewer(id=world.user, role=Role.ADMIN),
        client,
        visits,
        now_ms=now_ms,
    )


async def _rows(world: World, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in await world.db.fetch_all(sql, params)]


async def test_a_visit_is_kept_with_its_time_in_front_and_device(world: World) -> None:
    one = _visit(opened=90_000, last=10_000, front=50_000)
    assert await _record(world, [one]) == 1
    (row,) = await _rows(world, "SELECT * FROM page_visits")
    assert row["opened_at_ms"] == NOW - 90_000 and row["last_at_ms"] == NOW - 10_000
    assert (row["front_ms"], row["device_id"], row["client_kind"]) == (
        50_000,
        PHONE.device,
        "phone",
    )
    assert (row["place"], row["ref"], row["hidden"]) == ("wall", "library", 0)


async def test_a_visit_reported_again_is_one_row_brought_forward(world: World) -> None:
    one = _visit(opened=90_000, front=20_000)
    await _record(world, [one])
    later = Visit(one.id, one.place, one.ref, 150_000, 0, 80_000)
    await _record(world, [later], now_ms=NOW + 60_000)
    (row,) = await _rows(world, "SELECT front_ms, last_at_ms FROM page_visits")
    assert row == {"front_ms": 80_000, "last_at_ms": NOW + 60_000}
    (sitting,) = await _rows(world, "SELECT last_at_ms FROM app_sessions")
    assert sitting["last_at_ms"] == NOW + 60_000


async def test_time_in_front_is_never_more_than_the_visit_lasted(world: World) -> None:
    await _record(world, [_visit(opened=10_000, last=0, front=999_999_999)])
    (row,) = await _rows(world, "SELECT front_ms FROM page_visits")
    assert row["front_ms"] == 10_000


async def test_visits_within_the_gap_are_one_sitting_and_a_longer_quiet_starts_another(
    world: World,
) -> None:
    await _record(world, [_visit(opened=60_000), _visit(opened=30_000)])
    assert len(await _rows(world, "SELECT id FROM app_sessions")) == 1
    await _record(world, [_visit(opened=0)], now_ms=NOW + SESSION_GAP_MS + 1)
    assert len(await _rows(world, "SELECT id FROM app_sessions")) == 2


async def test_each_device_has_its_own_sitting(world: World) -> None:
    await _record(world, [_visit()], client=PHONE)
    await _record(world, [_visit()], client=DESK)
    rows = await _rows(world, "SELECT device_id FROM app_sessions ORDER BY device_id")
    assert PHONE.device is not None and DESK.device is not None
    assert [row["device_id"] for row in rows] == sorted([PHONE.device, DESK.device])


async def test_a_paused_history_writes_nothing(world: World) -> None:
    assert await _record(world, [_visit()], keep=False) == 0
    assert await _rows(world, "SELECT id FROM page_visits") == []


async def test_a_page_not_theirs_to_be_shown_leaves_no_row(world: World) -> None:
    access = _Access(seen={"P-SEEN"})
    kept = await _record(
        world, [_visit("person", "P-SEEN"), _visit("person", "P-NOT-THEIRS")], access=access
    )
    assert kept == 1
    assert [row["ref"] for row in await _rows(world, "SELECT ref FROM page_visits")] == ["P-SEEN"]


async def test_a_thing_in_hidden_is_written_only_once_unlocked_and_marked_hidden(
    world: World,
) -> None:
    access = _Access(hidden={"T-HIDDEN"})
    shut = Viewer(id=world.user, role=Role.ADMIN)
    assert await _record(world, [_visit("tag", "T-HIDDEN")], access=access, viewer=shut) == 0
    opened = Viewer(id=world.user, role=Role.ADMIN, show_hidden=True)
    assert await _record(world, [_visit("tag", "T-HIDDEN")], access=access, viewer=opened) == 1
    (row,) = await _rows(world, "SELECT hidden FROM page_visits")
    assert row["hidden"] == 1


async def test_somebody_elses_visit_id_is_never_written_over(world: World) -> None:
    other = await world.add_user("u-other")
    one = _visit()
    await _record(world, [one], viewer=Viewer(id=other, role=Role.ADMIN))
    assert await _record(world, [one]) == 0
    (row,) = await _rows(world, "SELECT user_id FROM page_visits")
    assert row["user_id"] == other


# --- a file that leaves -----------------------------------------------------------------------


async def _site_on(world: World, asset_id: str, site_id: str) -> None:
    await world.run(
        "INSERT OR IGNORE INTO sites (id, name, created_at) VALUES (?, ?, 1)", (site_id, site_id)
    )
    await world.run(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, 1)",
        ("u-" + asset_id, site_id, "name-" + asset_id),
    )
    await world.run(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
        (asset_id, "u-" + asset_id),
    )


async def test_a_file_that_leaves_keeps_what_it_was_and_who_could_see_it(world: World) -> None:
    asset = await world.add_file()
    await world.run("UPDATE assets SET size_bytes = 2048 WHERE id = ?", (asset,))
    await _site_on(world, asset, "site-1")
    # The verdict last: a link written after it recomputes it, to nothing for a file with no folder.
    await world.set_hidden(asset, True)
    await world.run("DELETE FROM assets WHERE id = ?", (asset,))
    (row,) = await _rows(world, "SELECT * FROM file_departures")
    assert (row["arrived_at"], row["size_bytes"], row["media_type"]) == (at(1), 2048, "video")
    assert (row["duration_ms"], json.loads(row["site_ids"]), row["viewers_known"]) == (
        600_000,
        ["site-1"],
        1,
    )
    (seen,) = await _rows(world, "SELECT user_id, concealed FROM file_departure_viewers")
    assert seen == {"user_id": world.user, "concealed": 1}


async def test_a_day_that_lost_its_files_still_counts_what_arrived(world: World) -> None:
    kept = await world.add_file()
    gone = await world.add_file()
    # Inside the day as the server's clock bounds it, and the verdicts written after.
    await world.run("UPDATE assets SET added_at = ?", (START + 60,))
    await _site_on(world, gone, "site-1")
    await world.set_hidden(kept, False)
    await world.set_hidden(gone, True)
    await world.run("DELETE FROM assets WHERE id = ?", (gone,))
    (row,) = await arrivals.files_added(
        world.db.fetch_all, user_id=world.user, start=START, end=END
    )
    assert (row["whole"], row["hidden"]) == (2, 1)
    by_site = await arrivals.files_added_by_site(
        world.db.fetch_all, user_id=world.user, start=START, end=END
    )
    assert [(one["key"], one["whole"], one["hidden"]) for one in by_site] == [("site-1", 1, 1)]
    del kept


async def test_a_departed_file_counts_only_for_who_could_see_it(world: World) -> None:
    other = await world.add_user("u-other")
    gone = await world.add_file()
    await world.run("UPDATE assets SET added_at = ?", (START + 60,))
    await world.set_hidden(gone, False)
    await world.run("DELETE FROM assets WHERE id = ?", (gone,))
    assert await arrivals.files_added(world.db.fetch_all, user_id=other, start=START, end=END) == []
    (mine,) = await arrivals.files_added(
        world.db.fetch_all, user_id=world.user, start=START, end=END
    )
    assert (mine["whole"], mine["hidden"]) == (1, 0)


async def test_removals_are_counted_from_what_was_kept_as_files_went(world: World) -> None:
    gone = await world.add_file(hidden=True)
    await world.run("DELETE FROM assets WHERE id = ?", (gone,))
    (ended,) = await _rows(world, "SELECT ended_at FROM file_departures")
    params = {"user": world.user, "start": ended["ended_at"], "end": ended["ended_at"] + 1}
    (row,) = await metrics.rows_of("files_removed", world.db.fetch_all, params)
    assert (row["whole"], row["hidden"]) == (1, 1)
    other = await world.add_user("u-other")
    assert (
        await metrics.rows_of("files_removed", world.db.fetch_all, {**params, "user": other}) == []
    )


async def _deleted_line(world: World, asset_id: str, at_moment: int) -> None:
    event = new_id()
    await world.run(
        "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb,"
        " actor_kind, actor_id) VALUES (?, 'ledger', '', '', '{}', ?, 'deleted', 'user', ?)",
        (event, at_moment, world.user),
    )
    await world.run(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
        " VALUES (?, 'asset', ?, 'a file')",
        (event, asset_id),
    )


async def test_the_step_keeps_what_history_can_still_say_and_is_safe_twice(world: World) -> None:
    gone = new_id()
    still_here = await world.add_file()
    await _deleted_line(world, gone, 500)
    await _deleted_line(world, gone, 900)
    await _deleted_line(world, still_here, 500)
    async with world.db.write() as connection:
        assert await schema.keep_what_is_still_known(connection) == 1
        assert await schema.keep_what_is_still_known(connection) == 0
    # One History line for the library, with its count, and none for the run that kept nothing.
    (line,) = await _rows(
        world,
        "SELECT actor_kind, actor_id, touched, payload FROM workbench_decisions WHERE verb = 'added'",
    )
    assert (line["actor_kind"], line["actor_id"], line["touched"]) == ("sift", "update", 1)
    worded = departures_kept("Sift", json.loads(line["payload"]))
    assert worded is not None
    assert text_of(worded) == (
        "Sift kept the 1 file deleted before this update, so its days keep their figures"
    )
    (row,) = await _rows(world, "SELECT * FROM file_departures")
    assert row["asset_id"] == gone and row["ended_at"] == 500 and row["viewers_known"] == 0
    assert row["arrived_at"] == timestamp_ms(gone) // 1000
    # Who could see it is not known: an admin's figures count it, a guest's do not.
    params = {"user": world.user, "start": 500, "end": 501}
    (counted,) = await metrics.rows_of("files_removed", world.db.fetch_all, params)
    assert (counted["whole"], counted["hidden"]) == (1, 0)
    await world.run(
        "INSERT INTO users (id, username, password_hash, role, created_at)"
        " VALUES ('u-guest', 'guest', 'x', 'guest', 1)"
    )
    assert (
        await metrics.rows_of("files_removed", world.db.fetch_all, {**params, "user": "u-guest"})
        == []
    )


async def test_the_step_over_a_library_with_no_history_yet_keeps_nothing(temp_db: Database) -> None:
    """This part's tables do not wait for History's, so on a new library the step can run before
    History's table exists: nothing was deleted yet, and it keeps nothing rather than failing the
    boot."""
    async with temp_db.write() as connection:
        assert await schema.keep_what_is_still_known(connection) == 0


async def test_the_step_brings_an_older_library_forward_and_adds_every_day_up_again(
    world: World,
) -> None:
    async with world.db.write() as connection:
        await connection.execute(
            "INSERT INTO insight_progress (user_id, added_up_to) VALUES (?, '2026-03-01')",
            (world.user,),
        )
        await schema.initialize(connection, 5)
    assert await _rows(world, "SELECT * FROM insight_progress") == []


# --- clearing ---------------------------------------------------------------------------------


async def test_clearing_takes_the_visits_sittings_and_figures_and_nobody_elses(
    world: World,
) -> None:
    other = await world.add_user("u-other")
    await _record(world, [_visit()])
    await _record(world, [_visit()], viewer=Viewer(id=other, role=Role.ADMIN))
    await world.run(
        "INSERT INTO insight_progress (user_id, added_up_to) VALUES (?, '2026-03-01')",
        (world.user,),
    )
    cleared = await clear_history_of(world.db, world.user)
    # One visit, its sitting, and how far the figures were added up.
    assert cleared["insights"] == 3
    left = await _rows(world, "SELECT user_id FROM page_visits")
    assert left == [{"user_id": other}]
    assert await _rows(world, "SELECT * FROM insight_progress") == []


async def test_removing_a_user_removes_their_visits_and_sittings(world: World) -> None:
    await _record(world, [_visit()])
    await world.run("DELETE FROM users WHERE id = ?", (world.user,))
    assert (
        await _rows(world, "SELECT id FROM page_visits UNION ALL SELECT id FROM app_sessions") == []
    )


def test_every_place_a_client_can_name_is_one_the_server_knows() -> None:
    from typing import get_args

    from sift.slices.insights.capture_models import VisitPlace

    assert set(get_args(VisitPlace)) == set(capture.THINGS) | capture.PLACES


def test_the_history_switch_says_the_record_stays_where_sift_runs() -> None:
    """Read on a phone, "this device" is the phone, and the record is never there: it is kept by
    the Sift the phone is talking to. The help names that device instead."""
    import sift.slices.insights  # noqa: F401  registers the switch
    from sift.kernel.settings_registry import get_registered

    switch = get_registered(RECORD_KEY)
    assert switch is not None
    assert "the device Sift runs on" in switch.help
    assert "this device" not in switch.help
