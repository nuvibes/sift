# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one day adds up to, the vault split, the re-split, and today counted live.

Each rule here has a mutation case: the view threshold, Theater counted once, the split, the
re-split on a moved stamp, a gone file keeping its last state, and today live.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, cast

import pytest

from sift.kernel.db import Database
from sift.slices.insights import store
from sift.slices.insights.metrics import ANYTHING
from sift.slices.insights.store import DayRow, count_day
from sift.slices.insights.tests.conftest import DAY, World, at

pytestmark = pytest.mark.integration


def _by(rows: list[DayRow] | tuple[DayRow, ...], metric: str) -> dict[str, DayRow]:
    return {row.key: row for row in rows if row.metric == metric}


async def _count(world: World) -> list[DayRow]:
    return await count_day(world.db.fetch_all, world.user, DAY)


async def test_a_day_with_nothing_in_it_has_no_rows(world: World) -> None:
    assert await _count(world) == []


async def test_a_day_when_only_files_arrived_is_counted(world: World) -> None:
    """A User who did nothing on a day that brought files still has a day: what arrived is the
    access layer's question, asked when the slice's own sources say nothing."""
    await world.add_file("video")
    await world.add_file("image", hidden=True)

    counted = await _count(world)

    assert _by(counted, "files_added")[""] == DayRow(DAY.isoformat(), "files_added", "", 2, 1)


async def test_a_sitting_is_judged_from_what_it_wrote_down_about_its_file(world: World) -> None:
    """The kind and the length ride with the sitting, so a file gone since is judged as it was:
    a picture opened is a view, and stays one after the picture is deleted."""
    photo = await world.add_file("image", length_ms=None)
    await world.sit(photo, at(9), 0)
    await world.run("DELETE FROM assets WHERE id = ?", (photo,))

    assert _by(await _count(world), "files_viewed:kind")["image"].whole == 1


async def test_a_video_counts_only_past_the_players_threshold(world: World) -> None:
    # A ten-minute video needs a quarter of itself, capped at thirty seconds, to be a view.
    film = await world.add_file("video", length_ms=600_000)
    await world.sit(film, at(20), 29_000)
    assert _by(await _count(world), "sittings") == {}
    await world.sit(film, at(21), 30_000)
    counted = await _count(world)
    assert _by(counted, "sittings")[""].whole == 1
    assert _by(counted, "viewed_ms")[""].whole == 30_000


async def test_a_picture_is_viewed_the_moment_it_is_opened(world: World) -> None:
    photo = await world.add_file("image", length_ms=None)
    await world.sit(photo, at(9), 0)
    assert _by(await _count(world), "files_viewed:kind")["image"].whole == 1


async def test_a_files_sittings_are_keyed_by_the_kind_it_wrote_down(world: World) -> None:
    """`sittings:file` carries the kind in its key, so a period's split by kind is read off the
    rows and a file deleted since still counts as the picture it was (not as a file-day)."""
    photo = await world.add_file("image", length_ms=None)
    await world.sit(photo, at(9), 0)
    await world.sit(photo, at(10), 0)
    await world.run("DELETE FROM assets WHERE id = ?", (photo,))

    assert {key: row.whole for key, row in _by(await _count(world), "sittings:file").items()} == {
        f"image:{photo}": 2
    }


async def test_a_theater_wall_is_one_hour_per_hour_and_one_sitting(world: World) -> None:
    await world.wall("evening", at(20), at(21), arrangement="saved-wall")
    cells = [await world.add_file("video") for _ in range(4)]
    for cell in cells:
        await world.person_on(cell, "person-one")
        await world.sit(cell, at(20), 3_600_000, screen="theater", theater_session="evening")
    counted = await _count(world)
    assert _by(counted, "viewed_ms")[""].whole == 3_600_000
    assert _by(counted, "sittings")[""].whole == 1
    assert {key: row.whole for key, row in _by(counted, "viewed_ms:kind").items()} == {
        "theater": 3_600_000
    }
    assert _by(counted, "theater_ms:wall")["saved-wall"].whole == 3_600_000
    # The things on the wall were each on screen for the hour.
    assert _by(counted, "files_viewed")[""].whole == 4
    assert _by(counted, "viewed_ms:person")["person-one"].whole == 4 * 3_600_000


async def test_a_hidden_file_is_counted_and_split_out(world: World) -> None:
    shown = await world.add_file("video")
    kept = await world.add_file("video", hidden=True)
    await world.sit(shown, at(10), 60_000)
    await world.sit(kept, at(11), 60_000)
    total = _by(await _count(world), "viewed_ms")[""]
    assert (total.whole, total.hidden) == (120_000, 60_000)
    assert total.shown(locked=True) == 60_000 and total.shown(locked=False) == 120_000


async def test_a_hidden_person_hides_their_whole_row(world: World) -> None:
    clip = await world.add_file("video")
    await world.person_on(clip, "person-two", hidden=True)
    await world.set_hidden(clip, False)
    await world.sit(clip, at(10), 60_000)
    row = _by(await _count(world), "viewed_ms:person")["person-two"]
    assert (row.whole, row.hidden) == (60_000, 60_000)


async def test_faces_named_counts_the_faces_this_user_named(world: World) -> None:
    async def receipt(act: str, tracks: list[str], actor: str = "user") -> None:
        await world.run(
            "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
            " decided_at, verb, actor_kind, actor_id) VALUES (?, 'identified', NULL, 't', 'd', ?,"
            " ?, 'decided', ?, ?)",
            (
                f"r-{act}-{actor}",
                json.dumps({"act": act, "track_ids": tracks}),
                at(15),
                actor,
                world.user if actor == "user" else "recognition",
            ),
        )

    await receipt("named-groups", ["t1", "t2", "t3"])
    await receipt("agreed-with-proposals", ["t4"])
    await receipt("refused-groups", ["t5", "t6"])
    await receipt("agreed-with-matches", ["t7", "t8"], actor="sift")
    assert _by(await _count(world), "faces_named")[""].whole == 4


async def _added_up(world: World) -> None:
    """File the day the way the helper does, split at the User's stamp now."""
    stamp = await store.stamp_of(world.db, world.user)
    counted = await _count(world)
    async with world.db.write() as connection:
        await store.write_day(connection, world.user, DAY, counted, stamp)


async def test_a_moved_stamp_splits_a_stored_day_again_before_it_is_read(world: World) -> None:
    clip = await world.add_file("video")
    await world.sit(clip, at(10), 60_000)
    await _added_up(world)
    await world.set_hidden(clip, True)
    await world.bump_stamp()
    read = await store.rows(
        world.db, world.user, DAY, DAY, ["viewed_ms"], now=at(12, day=DAY + timedelta(days=30))
    )
    assert [(row.whole, row.hidden) for row in read.rows] == [(60_000, 60_000)]
    await world.set_hidden(clip, False)
    await world.bump_stamp()
    read = await store.rows(
        world.db, world.user, DAY, DAY, ["viewed_ms"], now=at(12, day=DAY + timedelta(days=30))
    )
    assert [(row.whole, row.hidden) for row in read.rows] == [(60_000, 0)]


class _Counting:
    """The database, counting how many times a day was counted from the raw tables."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.counted = 0

    async def fetch_one(self, sql: str, params: Any = ()) -> Any:
        return await self.db.fetch_one(sql, params)

    async def fetch_all(self, sql: str, params: Any = ()) -> Any:
        if sql == ANYTHING:
            self.counted += 1
        return await self.db.fetch_all(sql, params)


async def test_a_stale_split_is_worked_out_once_per_stamp(world: World) -> None:
    """A reader re-splits a stale day once and keeps it until the stamp moves: the page is not the
    one paying for the helper's backlog on every visit. Right after the next move, it counts again
    and says the new split."""
    clip = await world.add_file("video")
    await world.sit(clip, at(10), 60_000)
    await _added_up(world)
    await world.set_hidden(clip, True)
    await world.bump_stamp()
    later = at(12, day=DAY + timedelta(days=30))
    counting = _Counting(world.db)
    as_db = cast(Database, counting)

    async def read(metric: str = "viewed_ms") -> list[tuple[int, int]]:
        found = await store.rows(as_db, world.user, DAY, DAY, [metric], now=later)
        return [(row.whole, row.hidden) for row in found.rows]

    assert await read() == [(60_000, 60_000)]
    assert await read() == [(60_000, 60_000)]
    assert counting.counted == 1
    # Another metric of the same day and stamp is its own answer, not the first one's.
    assert await read("sittings") == [(1, 1)]
    assert counting.counted == 2
    await world.set_hidden(clip, False)
    await world.bump_stamp()
    assert await read() == [(60_000, 0)]
    assert counting.counted == 3


async def test_a_gone_file_keeps_the_state_it_last_had(world: World) -> None:
    clip = await world.add_file("video")
    await world.sit(clip, at(10), 60_000)
    await _added_up(world)
    await world.run("DELETE FROM assets WHERE id = ?", (clip,))
    await world.bump_stamp()
    read = await store.rows(
        world.db, world.user, DAY, DAY, ["viewed_ms"], now=at(12, day=DAY + timedelta(days=30))
    )
    assert [(row.whole, row.hidden) for row in read.rows] == [(60_000, 0)]


async def test_today_is_counted_live_by_the_same_statements(world: World) -> None:
    clip = await world.add_file("video")
    await world.sit(clip, at(10), 60_000)
    noon = at(12)
    live = await store.today(world.db, world.user, ["viewed_ms"], now=noon)
    assert [(row.day, row.whole) for row in live] == [(DAY.isoformat(), 60_000)]
    read = await store.rows(world.db, world.user, DAY, DAY, ["viewed_ms"], now=noon)
    assert read.live_days == (DAY.isoformat(),) and read.rows == tuple(live)


async def test_an_old_day_the_helper_has_not_reached_is_named_missing(world: World) -> None:
    later = at(12, day=DAY + timedelta(days=30))
    read = await store.rows(world.db, world.user, DAY, DAY, ["viewed_ms"], now=later)
    assert read.missing_days == (DAY.isoformat(),) and read.rows == ()


async def test_a_reader_cannot_ask_for_a_word_that_is_not_a_metric(world: World) -> None:
    with pytest.raises(ValueError, match="not a metric"):
        await store.rows(world.db, world.user, DAY, DAY, ["watched_ms"])


async def test_a_recap_is_frozen_and_seen_once(world: World) -> None:
    first = await store.write_recap(world.db, world.user, "month:2026-03", "[]", made_at=5)
    again = await store.write_recap(world.db, world.user, "month:2026-03", "[1]", made_at=6)
    assert again == first
    assert [one.id for one in await store.recaps_of(world.db, world.user)] == [first.id]
    assert await store.recap(world.db, "somebody-else", first.id) is None
    assert await store.mark_seen(world.db, world.user, first.id, at=9) is True
    assert await store.mark_seen(world.db, world.user, first.id, at=10) is False
    seen = await store.recap(world.db, world.user, first.id)
    assert seen is not None and seen.seen_at == 9


async def test_a_recap_filed_tells_its_user_s_open_screens(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The quiet helper files a recap when its period closes. An Insights tab left open learned of
    it only when it was opened again; the shelf re-reads on `MINE`, so the filing says so, once."""
    from sift.kernel import changes
    from sift.kernel.audience import Audience
    from sift.kernel.changes import About

    told: list[tuple[object, object]] = []
    monkeypatch.setattr(changes, "announce", lambda who, about: told.append((who, about)))

    await store.write_recap(world.db, world.user, "month:2026-04", "[]", made_at=5)
    await store.write_recap(world.db, world.user, "month:2026-04", "[1]", made_at=6)

    assert told == [(Audience.of_user(world.user), About.MINE)]


async def test_a_recap_opened_or_dismissed_tells_its_user_s_other_tabs_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The announcement stays on every other open tab until one of them hears that it ended."""
    from sift.kernel.audience import Audience
    from sift.kernel.changes import About

    made = await store.write_recap(world.db, world.user, "month:2026-05", "[]", made_at=5)
    told: list[tuple[object, object]] = []
    monkeypatch.setattr(store, "announce", lambda who, about: told.append((who, about)))

    await store.mark_seen(world.db, world.user, made.id, at=9)
    await store.mark_seen(world.db, world.user, made.id, at=10)

    assert told == [(Audience.of_user(world.user), About.MINE)]


# --- when somebody sat down, and what they rated -----------------------------------------------

NEXT = DAY + timedelta(days=1)


def _figure(counted: list[DayRow], metric: str) -> tuple[int, int] | None:
    found = _by(counted, metric).get("")
    return None if found is None else (found.whole, found.hidden)


async def test_the_earliest_start_is_a_time_somebody_sat_down(world: World) -> None:
    """An evening that ran on past midnight opens files after it; the next day's earliest start is
    when somebody next SAT DOWN (19:00), not the middle of last night (00:05)."""
    film = await world.add_file("video", length_ms=120_000)
    await world.sit(film, at(23, 40, day=DAY - timedelta(days=1)), 60_000)
    await world.sit(film, at(23, 50, day=DAY - timedelta(days=1)), 60_000)
    await world.sit(film, at(0, 5), 60_000)
    await world.sit(film, at(19), 60_000)
    assert _figure(await _count(world), "earliest_start") == (19 * 60, 0)


async def test_a_day_that_only_ran_on_from_the_night_before_began_nothing(world: World) -> None:
    film = await world.add_file("video", length_ms=120_000)
    await world.sit(film, at(23, 50, day=DAY - timedelta(days=1)), 60_000)
    await world.sit(film, at(0, 5), 60_000)
    counted = await _count(world)
    assert _figure(counted, "earliest_start") is None
    assert _figure(counted, "latest_finish") is None


async def test_the_latest_finish_follows_the_evening_past_midnight(world: World) -> None:
    """The evening that began today ended at 00:40 tomorrow (after a file opened past midnight)
    and the next time somebody sat down (09:00 tomorrow) is not part of it."""
    film = await world.add_file("video", length_ms=120_000)
    await world.sit(film, at(22), 60_000)
    await world.sit(film, at(23, 54), 60_000)
    await world.sit(film, at(0, 10, day=NEXT), 60_000)
    await world.sit(film, at(0, 39, day=NEXT), 60_000)
    await world.sit(film, at(9, day=NEXT), 60_000)
    assert _figure(await _count(world), "latest_finish") == (24 * 60 + 40, 0)


async def test_a_hidden_file_in_the_run_on_is_hidden_from_the_finish(world: World) -> None:
    """Locked, the finish is the last minute over what is not hidden: a hidden file opened past
    midnight moves the whole figure and none of the locked one."""
    film = await world.add_file("video", length_ms=120_000)
    secret = await world.add_file("video", length_ms=120_000, hidden=True)
    await world.sit(film, at(23, 54), 60_000)
    await world.sit(secret, at(0, 20, day=NEXT), 60_000)
    whole, hidden = _figure(await _count(world), "latest_finish") or (0, 0)
    assert (whole, whole - hidden) == (24 * 60 + 21, 23 * 60 + 55)


async def test_a_sitting_down_that_began_with_a_skip_is_counted(world: World) -> None:
    """A two-second skip past a ten-minute video is no view, but it is somebody sitting down: the
    view a minute later is part of that sitting-down, which is counted and starts at 20:00."""
    long_one = await world.add_file("video", length_ms=600_000)
    photo = await world.add_file("image", length_ms=None)
    await world.sit(long_one, at(20), 2_000)
    await world.sit(photo, at(20, 1), 5_000)
    counted = await _count(world)
    assert _figure(counted, "pickups") == (1, 0)
    assert _figure(counted, "earliest_start") == (20 * 60, 0)


async def _opinion(world: World, kind: str, subject: str, what: str, after: int) -> None:
    await world.run(
        "INSERT INTO opinions (id, user_id, subject_kind, subject_id, kind, before, after, at)"
        " VALUES (lower(hex(randomblob(8))), ?, ?, ?, ?, NULL, ?, ?)",
        (world.user, kind, subject, what, after, at(12)),
    )


async def test_rated_and_starred_count_files_each_once_a_day(world: World) -> None:
    """ "You rated N files and starred M": a file re-rated is one file, and a person rated or
    starred is no file at all."""
    clip = await world.add_file("video")
    await _opinion(world, "asset", clip, "rating", 3)
    await _opinion(world, "asset", clip, "rating", 4)
    await _opinion(world, "person", "p-one", "rating", 5)
    await _opinion(world, "person", "p-one", "favorite", 1)
    await _opinion(world, "asset", clip, "favorite", 1)
    await _opinion(world, "asset", clip, "favorite", 1)
    counted = await _count(world)
    assert _figure(counted, "rated") == (1, 0)
    assert _figure(counted, "starred") == (1, 0)


async def test_a_sitting_with_no_file_is_still_judged_and_names_no_file(world: World) -> None:
    """A sitting whose file row is gone keeps what it wrote down about the file, so it is judged
    by the player's rule all the same, and there is no file to ask the vault about."""
    await world.run(
        "INSERT INTO plays (id, user_id, asset_id, started_at, duration_ms, made_at, kind)"
        " VALUES ('p-none', ?, NULL, ?, 1000, ?, 'image')",
        (world.user, at(10), at(10)),
    )
    assert await store.views_of_the_day(world.db.fetch_all, world.user, DAY) == (["p-none"], set())


async def test_a_day_cannot_be_counted_for_a_word_that_is_not_a_metric(world: World) -> None:
    with pytest.raises(ValueError, match="not a metric"):
        await count_day(world.db.fetch_all, world.user, DAY, ["watched_ms"])


async def test_a_span_that_ends_before_it_begins_has_nothing_in_it(world: World) -> None:
    read = await store.rows(world.db, world.user, DAY, DAY - timedelta(days=1), ["viewed_ms"])
    assert (read.rows, read.live_days, read.missing_days) == ((), (), ())
