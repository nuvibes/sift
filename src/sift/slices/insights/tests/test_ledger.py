# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every metric's figure on one day whose rows are written here, against a hand count.

The day (Wednesday 11 March 2026, local clock) holds one of each thing that has made a figure
wrong: a sitting from the night before that runs past midnight, a picture panel left open for ten
hours, a video on Repeat for two hours, a Theater wall whose two cells overlap, a wall left open
with nothing playing, a file deleted, and a Username filed after the day was added up. A metric
with no line in `EXPECTED` fails the walk, so a new metric cannot land without its hand count.

All files are shown to the User (nothing hidden) except one page visit, so each `hidden` is 0 but
that one. Times are minutes and milliseconds: MINUTE = 60,000 ms.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from sift.slices.insights import metrics, rollup, store
from sift.slices.insights.metrics import METRICS
from sift.slices.insights.store import DayRow, count_day
from sift.slices.insights.tests.conftest import DAY, World, at

pytestmark = pytest.mark.integration

MINUTE = 60_000
PREV = DAY - timedelta(days=1)
LONG_AGO = DAY - timedelta(days=200)

# The files, by name: V0 the late film, P the picture left open, V1 the clip viewed three times,
# R the file come back to, V2 the clip on Repeat, F1 F2 F3 the wall's cells, D the file deleted.
NAMES = ("V0", "P", "V1", "R", "V2", "F1", "F2", "F3", "D")


async def _the_day(world: World) -> dict[str, str]:
    files = {
        "V0": await world.add_file("video", length_ms=60 * MINUTE),
        "P": await world.add_file("image", length_ms=None),
        "V1": await world.add_file("video", length_ms=10 * MINUTE),
        "R": await world.add_file("video", length_ms=10 * MINUTE),
        "V2": await world.add_file("video", length_ms=2 * MINUTE),
        "F1": await world.add_file("video", length_ms=120 * MINUTE),
        "F2": await world.add_file("video", length_ms=120 * MINUTE),
        "F3": await world.add_file("video", length_ms=120 * MINUTE),
        "D": await world.add_file("video"),
    }
    f = files
    # 23:50 the night before, 30 minutes: 10 on the 10th, 20 on this day.
    await world.sit(f["V0"], at(23, 50, PREV), 30 * MINUTE)
    # 08:00 a picture left open in the panel for ten hours: counts 15 minutes.
    await world.sit(f["P"], at(8), 600 * MINUTE, screen="panel")
    # 09:00, 09:10, 09:20 five minutes each: a file never sat with before, three times.
    for minute in (0, 10, 20):
        await world.sit(f["V1"], at(9, minute), 5 * MINUTE, screen="panel")
    # 12:00 one minute of a file last sat with 200 days ago.
    await world.sit(f["R"], at(12, 0, LONG_AGO), 2 * MINUTE, screen="panel")
    await world.sit(f["R"], at(12), 1 * MINUTE, screen="panel")
    # 20:00 to 21:00 a wall: F1 20:00 to 20:40, F2 20:20 to 21:00 (overlapping), F3 ten seconds
    # at 20:30 (no view). One hour as a union, never 80 minutes.
    await world.wall("evening", at(20), at(21), arrangement="wall-one")
    for name, minute, length in (
        ("F1", 0, 40 * MINUTE),
        ("F2", 20, 40 * MINUTE),
        ("F3", 30, 10_000),
    ):
        await world.sit(
            f[name], at(20, minute), length, screen="theater", theater_session="evening"
        )
    # 21:10 to 23:00 a wall with nothing on it.
    await world.wall("idle", at(21, 10), at(23))
    # 22:00 a two-minute clip on Repeat for two hours in the corner: counts 15 minutes.
    await world.sit(f["V2"], at(22), 120 * MINUTE, screen="corner")

    # What the files carry.
    await world.person_on(f["V1"], "p-1")
    await world.run("INSERT INTO asset_people (asset_id, person_id) VALUES (?, 'p-1')", (f["R"],))
    await world.run("INSERT INTO sites (id, name, created_at) VALUES ('s-1', 'Another Studio', 1)")
    await world.run(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES ('u-1', 's-1', 'handle', 1)"
    )
    await world.run(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, 'u-1')", (f["V1"],)
    )
    await world.run("INSERT INTO tags (id, name, created_at) VALUES ('t-1', 'runway', 1)")
    await world.run("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, 't-1')", (f["R"],))
    await world.run("INSERT INTO collections (id, name, created_at) VALUES ('c-1', 'evening', 1)")
    await world.run(
        "INSERT INTO collection_items (collection_id, asset_id) VALUES ('c-1', ?)", (f["V2"],)
    )
    await world.run("INSERT INTO photo_sets (id, name, created_at) VALUES ('ps-1', 'shoot', 1)")
    await world.run(
        "INSERT INTO photo_set_items (photo_set_id, asset_id) VALUES ('ps-1', ?)", (f["V1"],)
    )
    await world.run(
        "INSERT INTO songs (id, name, name_sort, created_at) VALUES ('so-1', 'tune', 'tune', 1)"
    )
    await world.run(
        "INSERT INTO song_files (asset_id, song_id, added_at) VALUES (?, 'so-1', 1)", (f["P"],)
    )
    await world.run("UPDATE assets SET size_bytes = 1000 WHERE id = ?", (f["V1"],))
    # A membership re-decides a file's verdict: every file is said shown again after them.
    for asset_id in files.values():
        await world.set_hidden(asset_id, False)

    # The opinions, the organizing, what Sift did.
    for kind, before, after in (("rating", None, 4), ("favorite", 0, 1), ("o", 0, 1)):
        await world.run(
            "INSERT INTO opinions (id, user_id, subject_kind, subject_id, kind, before, after, at)"
            " VALUES (?, ?, 'asset', ?, ?, ?, ?, ?)",
            (world._id("o"), world.user, f["V1"], kind, before, after, at(10)),
        )
    for queue, verb, payload in (
        ("identified", "decided", {"act": "named-groups", "track_ids": ["a", "b"]}),
        ("ledger", "filed", {}),
    ):
        decision = world._id("d")
        await world.run(
            "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
            " decided_at, verb, actor_kind, actor_id) VALUES (?, ?, ?, 't', '', ?, ?, ?, 'user', ?)",
            (decision, queue, world.user, json.dumps(payload), at(15), verb, world.user),
        )
        if verb == "filed":
            await world.run(
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
                " VALUES (?, 'asset', ?)",
                (decision, f["V1"]),
            )
    await world.run(
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, worker_ms, files)"
        " VALUES ('r-1', 'fingerprint', ?, ?, ?, 60000, ?)",
        (at(3), at(3), at(4), json.dumps({"video": {"n": 4}})),
    )
    await world.run(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality,"
        " created_at) VALUES ('ft-1', ?, 0, 1, 1, 1.0, ?)",
        (f["P"], at(3) * 1000),
    )

    # A sitting with Sift from 07:55 to 09:30, three pages in it, one about something hidden.
    await world.run(
        "INSERT INTO app_sessions (id, user_id, client_kind, started_at_ms, last_at_ms)"
        " VALUES ('as-1', ?, 'computer', ?, ?)",
        (world.user, at(7, 55) * 1000, at(9, 30) * 1000),
    )
    for n, hidden in enumerate((0, 0, 1)):
        await world.run(
            "INSERT INTO page_visits (id, user_id, app_session_id, place, hidden, opened_at_ms,"
            " last_at_ms, front_ms, client_kind) VALUES (?, ?, 'as-1', 'person', ?, ?, ?, 1, 'computer')",
            (f"pv-{n}", world.user, hidden, at(8, n) * 1000, at(8, n) * 1000),
        )
    # A download of V1 that finished at 10:00.
    await world.run(
        "INSERT INTO downloads (id, url, url_hash, state, created_at, finished_at, asset_id,"
        " requested_by) VALUES ('dl-1', 'https://example.invalid/a', 'h', 'done', ?, ?, ?, ?)",
        (at(9), at(10), f["V1"], world.user),
    )
    # D deleted (the departure keeps who could see it), its departure filed at 13:00.
    await world.run("DELETE FROM assets WHERE id = ?", (f["D"],))
    await world.run("UPDATE file_departures SET ended_at = ? WHERE asset_id = ?", (at(13), f["D"]))
    return files


def _expected(f: dict[str, str]) -> dict[str, dict[str, tuple[int, int]]]:
    """The hand count, metric by metric: `{key: (whole, hidden)}`."""
    m = MINUTE
    return {
        # V0's 20 after midnight + P 15 (capped) + V1 3 x 5 + R 1 + V2 15 (capped) + the wall's
        # union of 60 = 126 minutes. The idle wall is nothing.
        "viewed_ms": {"": (126 * m, 0)},
        "viewed_ms:kind": {"video": (51 * m, 0), "image": (15 * m, 0), "theater": (60 * m, 0)},
        # P, V1 three times, R, V2, and the wall that played; V0 began the day before.
        "sittings": {"": (7, 0)},
        "sittings:kind": {"image": (1, 0), "video": (5, 0), "theater": (1, 0)},
        "files_viewed": {"": (4, 0)},
        "files_viewed:kind": {"image": (1, 0), "video": (3, 0)},
        "theater_files": {"wall-one": (2, 0)},
        "viewed_ms:person": {"p-1": (16 * m, 0)},
        "files_viewed:person": {"p-1": (2, 0)},
        "viewed_ms:site": {"s-1": (15 * m, 0)},
        "viewed_ms:tag": {"t-1": (1 * m, 0)},
        "viewed_ms:collection": {"c-1": (15 * m, 0)},
        "viewed_ms:photo_set": {"ps-1": (15 * m, 0)},
        "photo_sets_viewed": {"": (1, 0)},
        "viewed_ms:song": {"so-1": (15 * m, 0)},
        "sittings:file": {
            f"image:{f['P']}": (1, 0),
            f"video:{f['V1']}": (3, 0),
            f"video:{f['R']}": (1, 0),
            f"video:{f['V2']}": (1, 0),
        },
        "viewed_ms:hour": {
            "00": (20 * m, 0),
            "08": (15 * m, 0),
            "09": (15 * m, 0),
            "12": (1 * m, 0),
            "20": (60 * m, 0),
            "22": (15 * m, 0),
        },
        "viewed_ms:weekday": {"2": (126 * m, 0)},
        "theater_ms:wall": {"wall-one": (60 * m, 0)},
        # 08:00 (V0 stopped at 00:20), 09:00 (P stopped at 08:15), 12:00, 20:00 (the wall begins
        # to play), 22:00 (the wall stopped at 21:00). 09:10 and 09:20 follow 09:00; the cells
        # after 20:00 follow the first.
        "pickups": {"": (5, 0)},
        "first_opened:person": {"p-1": (2, 0)},
        "first_opened:kind": {"image": (1, 0), "video": (3, 0)},
        "earliest_start": {"": (8 * 60, 0)},
        # V2 stopped accruing at 22:15.
        "latest_finish": {"": (22 * 60 + 15, 0)},
        "rated": {"": (1, 0)},
        "rated:file": {f["V1"]: (1, 0)},
        "starred": {"": (1, 0)},
        "starred:file": {f["V1"]: (1, 0)},
        "o": {"": (1, 0)},
        "o:file": {f["V1"]: (1, 0)},
        "decided": {"": (1, 0)},
        "decided:queue": {"identified": (1, 0)},
        "faces_named": {"": (2, 0)},
        "files_filed": {"": (1, 0)},
        # All nine arrived at 01:00, D among them through what was kept as it went.
        "files_added": {"": (9, 0)},
        "files_added:site": {"s-1": (1, 0)},
        "files_removed": {"": (1, 0)},
        "work_ms:family": {"fingerprint": (60_000, 0)},
        "faces_found": {"": (1, 0)},
        "fingerprints_made": {"": (4, 0)},
        "first_file": {f["P"]: (at(8), 0)},
        "last_file": {f["V2"]: (at(22, 15), 0)},
        "new_favourites:file": {f["V1"]: (3, 0)},
        "rediscovered:file": {f["R"]: ((at(12) - at(12, 0, LONG_AGO)) // 86400, 0)},
        "session_ms:session": {"as-1": (95 * m, 0)},
        "session_pages:session": {"as-1": (3, 1)},
        "downloads": {"": (1, 0)},
        "download_bytes": {"": (1000, 0)},
    }


def _by(rows: list[DayRow], metric: str) -> dict[str, tuple[int, int]]:
    return {row.key: (row.whole, row.hidden) for row in rows if row.metric == metric}


async def test_every_metric_is_its_hand_count(world: World) -> None:
    files = await _the_day(world)
    expected = _expected(files)
    assert set(expected) == set(METRICS), "a metric with no hand count"
    counted = await count_day(world.db.fetch_all, world.user, DAY)
    wrong = {
        metric: (_by(counted, metric), want)
        for metric, want in expected.items()
        if _by(counted, metric) != want
    }
    assert wrong == {}
    assert expected["rediscovered:file"][files["R"]][0] >= metrics.REDISCOVERED_AFTER_DAYS


async def test_a_username_filed_after_the_cut_adds_the_day_up_again(world: World) -> None:
    """The day is added up; V2 is then filed under the Site's Username. Its arrival and its
    sitting are on that day, so the day is marked and added up again: the Site's time is V1's 15
    minutes and V2's 15, and two of its files arrived. Nothing is marked twice."""
    files = await _the_day(world)
    stamp = await store.stamp_of(world.db, world.user)
    async with world.db.write() as connection:
        await store.write_day(
            connection,
            world.user,
            DAY,
            await count_day(world.db.fetch_all, world.user, DAY),
            stamp,
        )
    await world.run(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, 'u-1')", (files["V2"],)
    )
    await world.set_hidden(files["V2"], False)
    (progress,) = await world.db.fetch_all(
        "SELECT dirty_from FROM insight_progress WHERE user_id = ?", (world.user,)
    )
    assert progress["dirty_from"] == DAY.isoformat()
    assert await rollup.add_up_again(world.db)
    stored = await store.rows(
        world.db,
        world.user,
        DAY,
        DAY,
        ["viewed_ms:site", "files_added:site"],
        now=at(12) + 30 * 86400,
    )
    assert _by(list(stored.rows), "viewed_ms:site") == {"s-1": (30 * MINUTE, 0)}
    assert _by(list(stored.rows), "files_added:site") == {"s-1": (2, 0)}
    assert not await rollup.add_up_again(world.db)
