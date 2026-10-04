# SPDX-License-Identifier: AGPL-3.0-or-later
"""A pass somebody pressed says who on the file's History, for as long as its line stands.

No pass's own row records a press: the job that ran it did, and a job row is gone a week after it
settles. So the worker pool records the press beside the work (`kernel.presses`), in the
transaction that marks the job done, and each pass line reads it beside its own row: the line is
the presser's where its row was written inside the stretch the pressed job ran in, and Sift's
otherwise. Who is named by the rule every History line names a user by: "You", the user's name to
an admin, "Another user" to anybody else and for a user removed since.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

# The feature tables these lines read, registered so a kernel database has them.
import sift.slices.faces.schema
import sift.slices.music.schema
import sift.slices.semantic.schema
import sift.slices.stash_boxes.schema
import sift.slices.watermarks.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Role, schema
from sift.kernel.access.history import Actor, Event, history_of_asset
from sift.kernel.access.history_actors import _Who
from sift.kernel.access.history_events import LedgerEvent
from sift.kernel.access.history_faces import FACE_RUN_SAID
from sift.kernel.access.history_presses import Press, Pressers, press_of
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobContext, JobQueue, WorkerPool, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.ledger import Actor as ActorOf
from sift.kernel.ledger import record_event
from sift.kernel.presses import Pressed, carry_into_the_ledger, pressed_job
from sift.kernel.vocabulary import LEDGER_QUEUE, VIA_FACES, VIA_FINGERPRINT, Subject
from sift.testing.fixtures import Actors, World, create_user

pytestmark = pytest.mark.anyio

#: The fixture library's arrival moment (`testing.fixtures._EPOCH`); every pass here is an hour on.
ARRIVED = 1_700_000_000
LATER = ARRIVED + 3_600
BOX = "01HX0000000000000000000952"
#: The kinds the pass lines are drawn as: the fixture library's namings and taggings are not these.
_KINDS = frozenset({"face_run", "watermark", "ready", "scanned", "asked", "left_out", "pressed"})


def _words(event: Event) -> str:
    return "".join(f"{one.lead}{one.text}" for one in event.pieces)


async def _press(
    database: Database, asset_id: str, pass_key: str, user_id: str, *, at: int = LATER
) -> None:
    """A press of one pass on one file, its work running a minute either side of `at`: the act
    `kernel.presses` writes, with its moment set rather than read off the clock."""
    act = new_id()
    await database.execute(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at,"
        " verb, actor_kind, actor_id) VALUES (?, ?, NULL, '', '', ?, ?, 'pressed', 'user', ?)",
        (act, LEDGER_QUEUE, json.dumps({"passes": [pass_key], "began": at - 30}), at + 30, user_id),
    )
    await database.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id) VALUES (?, 'asset', ?)",
        (act, asset_id),
    )


async def _share(database: Database, asset_id: str, user_id: str) -> None:
    """The file shared with a guest: the one way a guest reaches a file's History at all."""
    await database.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'item', ?, ?, 'share', 0)",
        (new_id(), asset_id, user_id),
    )


async def _scan_faces(database: Database, asset_id: str, at: int = LATER) -> None:
    await database.execute(
        "INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count,"
        " identified_count, detector, recognizer, settings_digest, scanned_at)"
        " VALUES (?, 'no_faces', 'fast', 1.0, 10, 0, 0, 'd', 'r', 'x', ?)",
        (asset_id, at * 1000),
    )


async def _lines(
    database: Database, access: Repository, reader: object, asset_id: str
) -> list[Event]:
    drawn = await history_of_asset(database, access, reader, asset_id)  # type: ignore[arg-type]
    return [one for one in drawn if one.kind in _KINDS]


async def _name(database: Database, user_id: str) -> str:
    row = await database.fetch_one("SELECT username FROM users WHERE id = ?", (user_id,))
    assert row is not None
    return str(row["username"])


async def test_a_look_for_faces_somebody_pressed_says_who_to_each_reader(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The admin is told the guest's name, the guest is told it was them, and the line is theirs."""
    await _share(temp_db, world.solo, actors.guest.id)
    await _scan_faces(temp_db, world.solo)
    await _press(temp_db, world.solo, "faces", actors.guest.id)
    name = await _name(temp_db, actors.guest.id)

    [admin_reads] = await _lines(temp_db, access, actors.admin, world.solo)
    assert _words(admin_reads) == f"{name} had Sift look for faces here, and it found none"
    assert (admin_reads.actor, admin_reads.actor_name) == (Actor.ANOTHER_USER, name)
    [guest_reads] = await _lines(temp_db, access, actors.guest, world.solo)
    assert _words(guest_reads) == "You had Sift look for faces here, and it found none"
    assert guest_reads.actor is Actor.YOU


async def test_a_guest_is_never_told_who_else_pressed(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Who else uses this install is the sharing screens' to tell, and they tell an admin only."""
    await _share(temp_db, world.solo, actors.guest.id)
    await _scan_faces(temp_db, world.solo)
    await _press(temp_db, world.solo, "face_scan", actors.admin.id)

    [line] = await _lines(temp_db, access, actors.guest, world.solo)
    assert _words(line) == "Another user had Sift look for faces here, and it found none"
    assert (line.actor, line.actor_name) == (Actor.ANOTHER_USER, None)


async def test_a_user_removed_since_reads_as_another_user(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    gone = await create_user(temp_db, Role.GUEST)
    await _scan_faces(temp_db, world.solo)
    await _press(temp_db, world.solo, "faces", gone.id)
    await temp_db.execute("DELETE FROM users WHERE id = ?", (gone.id,))

    [line] = await _lines(temp_db, access, actors.admin, world.solo)
    assert _words(line) == "Another user had Sift look for faces here, and it found none"


async def test_a_look_sift_ran_by_itself_after_the_press_is_sift_s(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The press stays on the record and the newer scan is outside its stretch: the line is Sift's.
    And a press whose stretch the file's scan was written before is no claim on that scan."""
    await _scan_faces(temp_db, world.solo, at=LATER + 3_600)
    await _press(temp_db, world.solo, "faces", actors.guest.id)
    await _scan_faces(temp_db, world.twin, at=LATER - 3_600)
    await _press(temp_db, world.twin, "faces", actors.guest.id)

    name = await _name(temp_db, actors.guest.id)
    pressed = f"{name} had Sift look for faces in this file"
    sift_s = "Sift looked for faces here and found none"
    for asset_id, said in ((world.solo, [pressed, sift_s]), (world.twin, [sift_s, pressed])):
        lines = await _lines(temp_db, access, actors.admin, asset_id)
        assert [_words(one) for one in lines] == said
        assert next(one for one in lines if one.kind == "face_run").actor is Actor.SIFT


async def test_a_press_of_another_pass_says_nothing_about_this_one(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _scan_faces(temp_db, world.solo)
    await _press(temp_db, world.solo, "meaning", actors.guest.id)

    name = await _name(temp_db, actors.guest.id)
    lines = await _lines(temp_db, access, actors.admin, world.solo)
    assert [_words(one) for one in lines] == [
        "Sift looked for faces here and found none",
        f"{name} had Sift index this file for Smart Search",
    ]


async def test_every_other_pressed_pass_says_who_in_its_own_words(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """One file put through every other pass by one press each, and each line said as the press."""
    who = actors.guest.id
    name = await _name(temp_db, who)
    await temp_db.execute(
        "INSERT INTO derivatives (id, asset_id, kind, params, rel_cache_path, created_at)"
        " VALUES ('d1', ?, 'thumb', '{}', 'x.jpg', ?)",
        (world.solo, LATER),
    )
    await temp_db.execute(
        "INSERT INTO watermark_scans (asset_id, revision, identity, found, scanned_at)"
        " VALUES (?, 'r', 'i', 0, ?)",
        (world.solo, LATER + 600),
    )
    await temp_db.execute(
        "INSERT INTO semantic_indexed (asset_id, revision, frames, indexed_at)"
        " VALUES (?, 'r', 1, ?)",
        (world.solo, (LATER + 1_200) * 1000),
    )
    await temp_db.execute(
        "INSERT INTO audio_fingerprints"
        " (asset_id, algorithm, tool, duration_ms, offset_ms, fingerprint, computed_at)"
        " VALUES (?, 2, 'fpcalc', 61000, 0, ?, ?)",
        (world.solo, b"\x01", LATER + 1_800),
    )
    await temp_db.execute(
        "INSERT INTO music_lookups (asset_id, looked_up_at, lengths_sent, status, title)"
        " VALUES (?, ?, '[61]', 'nothing', NULL)",
        (world.solo, LATER + 2_400),
    )
    await temp_db.execute(
        "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
        " VALUES (?, 'previews', 'x', 'the tool said something', 1, ?)",
        (world.solo, LATER + 3_000),
    )
    await temp_db.execute(
        "INSERT OR REPLACE INTO asset_probes (asset_id, probe_version, tool, body, probed_at)"
        " VALUES (?, 1, 'ffprobe', x'00', ?)",
        (world.solo, LATER + 3_600),
    )
    for key, at in (
        ("thumbnails", LATER),
        ("watermark_read", LATER + 600),
        ("semantic_describe", LATER + 1_200),
        ("music", LATER + 1_800),
        ("music_lookup", LATER + 2_400),
        ("previews", LATER + 3_000),
        ("details", LATER + 3_600),
    ):
        await _press(temp_db, world.solo, key, who, at=at)

    lines = await _lines(temp_db, access, actors.admin, world.solo)
    assert [_words(one) for one in lines] == [
        f"{name} had Sift generate a thumbnail for this file",
        f"{name} had Sift look for a watermark, and it found none",
        f"{name} had Sift index this file for Smart Search",
        f"{name} had Sift generate a music fingerprint for this file",
        f"{name} had Sift ask AcoustID, and it didn't know the song",
        f"{name} had Sift generate a hover preview for this file, and it could not yet",
        f"{name} had Sift read this file's details again",
    ]
    # A press is never folded into a sitting of Sift's housekeeping, which would lose who.
    assert not any(one.routine for one in lines)


async def test_a_stash_box_ask_somebody_pressed_says_who(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'Quillbox', ?, ?)",
        (BOX, "https://quillbox.example/graphql", ARRIVED),
    )
    await temp_db.execute(
        "INSERT INTO stash_box_scans (id, asset_id, box_id, found, scanned_at)"
        " VALUES ('s1', ?, ?, 0, ?)",
        (world.solo, BOX, LATER),
    )
    await _share(temp_db, world.solo, actors.guest.id)
    await _press(temp_db, world.solo, "stash_box_scan", actors.guest.id)

    [line] = await _lines(temp_db, access, actors.guest, world.solo)
    assert _words(line) == "You had Sift ask Quillbox, and nothing matched"


async def test_fingerprints_somebody_pressed_are_theirs_on_the_ledger_s_line(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The one pass whose line is the ledger's act, said again as the presser's."""
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=ActorOf.sift(VIA_FINGERPRINT),
            verb="scanned",
            subject=Subject(kind="asset", id=world.solo, name="solo.mp4"),
        )
    row = await temp_db.fetch_one(
        "SELECT decided_at FROM workbench_decisions WHERE verb = 'scanned'", ()
    )
    assert row is not None
    await _press(temp_db, world.solo, "fingerprints", actors.guest.id, at=int(row["decided_at"]))

    name = await _name(temp_db, actors.guest.id)

    [line] = await _lines(temp_db, access, actors.admin, world.solo)
    assert _words(line) == f"{name} had Sift generate fingerprints for this file"
    assert (line.actor, line.actor_name) == (Actor.ANOTHER_USER, name)


def test_a_job_records_its_file_and_every_pass_it_carried() -> None:
    assert pressed_job("generate_file", {"asset_id": "a", "products": ["thumbnails"]}, "u", 5) == (
        Pressed(asset_id="a", passes=("generate_file", "thumbnails"), user_id="u", started_at=5)
    )
    # Nobody pressed it, or it is not about one file: nothing is recorded.
    assert pressed_job("face_scan", {"asset_id": "a"}, None, 5) is None
    assert pressed_job("generate", {"products": ["thumbnails"]}, "u", 5) is None


async def test_a_pressed_run_records_its_own_family_s_files_and_no_other(
    temp_db: Database, job_queue: JobQueue, world: World, actors: Actors
) -> None:
    """A Generate run somebody pressed hands its files to Generate tasks, and those are the press;
    the Identify work one of them hands on follows its own When and is Sift's, as `requested_by`
    and `timing` stop at the edge of the family."""

    async def page(context: JobContext) -> None:
        await context.enqueue_child(
            "generate_one", {"asset_id": world.solo, "products": ["thumbnails"]}
        )

    async def one(context: JobContext) -> None:
        await context.enqueue_child("identify_one", {"asset_id": world.twin})
        # Marked to run now, and still not the press: a press reaches its own family only.
        await context.enqueue_child("identify_one", {"asset_id": world.loose}, at="now")

    async def nothing(context: JobContext) -> None:
        return None

    register_handler(
        "generate_page", page, name="Test page", family=Family.GENERATE, needs_ready=False
    )
    register_handler(
        "generate_one", one, name="Test task", family=Family.GENERATE, needs_ready=False
    )
    register_handler(
        "identify_one", nothing, name="Test look", family=Family.IDENTIFY, needs_ready=False
    )
    await job_queue.enqueue(
        "generate_page", {"products": ["thumbnails"]}, requested_by=actors.admin.id
    )
    pool = WorkerPool(job_queue, concurrency=1, poll_interval=0.01, watchdog=False)
    await pool.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            counts = await job_queue.counts()
            if not counts.get("queued") and not counts.get("running"):
                break
            await asyncio.sleep(0.01)
    finally:
        await pool.stop()

    rows = await temp_db.fetch_all(
        "SELECT s.subject_id AS asset_id, d.payload AS payload, d.actor_id AS user_id"
        " FROM workbench_decisions d JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'pressed'",
        (),
    )
    assert [
        (row["asset_id"], json.loads(row["payload"])["passes"], row["user_id"]) for row in rows
    ] == [(world.solo, ["generate_one", "thumbnails"], actors.admin.id)]


async def test_every_press_of_a_pass_is_kept_and_folded_where_it_ran_again_and_again(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A second look replaces the first one's row, never the first press: each press no line says
    is a line of its own, and presses of one pass a moment apart are one line that counts them."""
    who = actors.guest.id
    name = await _name(temp_db, who)
    await _share(temp_db, world.solo, who)
    await _press(temp_db, world.solo, "faces", who, at=LATER - 7_200)
    await _press(temp_db, world.solo, "faces", who, at=LATER - 3_600)
    for gap in (0, 20, 40):
        await _press(temp_db, world.solo, "face_scan", who, at=LATER - 1_800 + gap)
    await _scan_faces(temp_db, world.solo)
    await _press(temp_db, world.solo, "faces", who)

    lines = await _lines(temp_db, access, actors.admin, world.solo)
    assert [_words(one) for one in lines] == [
        f"{name} had Sift look for faces in this file",
        f"{name} had Sift look for faces in this file",
        f"{name} had Sift look for faces in this file 3 times",
        f"{name} had Sift look for faces here, and it found none",
    ]
    assert [one.kind for one in lines] == ["pressed", "pressed", "pressed", "face_run"]
    assert not any(one.routine for one in lines)
    # The reader's own presses are said as theirs.
    [*_, last] = await _lines(temp_db, access, actors.guest, world.solo)
    assert _words(last) == "You had Sift look for faces here, and it found none"


async def test_a_press_that_found_the_work_done_is_said_and_the_older_line_stays_its_own(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _share(temp_db, world.solo, actors.guest.id)
    await _press(temp_db, world.solo, "faces", actors.admin.id, at=LATER - 3_600)
    await _scan_faces(temp_db, world.solo, at=LATER - 3_600)
    await _press(temp_db, world.solo, "faces", actors.guest.id)

    lines = await _lines(temp_db, access, actors.guest, world.solo)
    assert [_words(one) for one in lines] == [
        "Another user had Sift look for faces here, and it found none",
        "You had Sift look for faces in this file",
    ]


async def test_a_job_s_press_is_one_act_and_a_gone_file_or_user_writes_none(
    temp_db: Database, world: World, actors: Actors
) -> None:
    from sift.kernel.presses import record_pressed

    async with temp_db.write() as connection:
        for asset_id, user_id in (
            (world.solo, actors.admin.id),
            ("no-such-file", actors.admin.id),
            (world.twin, "no-such-user"),
        ):
            pressed = Pressed(asset_id=asset_id, passes=("p",), user_id=user_id, started_at=0)
            await record_pressed(connection, pressed, finished_at=LATER)
    rows = await temp_db.fetch_all(
        "SELECT d.actor_id AS who, d.payload AS payload, s.subject_id AS file"
        " FROM workbench_decisions d JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'pressed'",
        (),
    )
    assert [(row["who"], row["file"]) for row in rows] == [(actors.admin.id, world.solo)]
    # Begun when it ended where the job never said when it began.
    assert json.loads(rows[0]["payload"]) == {"passes": ["p"], "began": LATER}


async def test_the_presses_a_library_kept_before_are_carried_into_the_ledger_once(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Catalog 85: each press the old record held is an act, the rows one job wrote are one press,
    a user removed since keeps the id, and the old record goes. A second run changes nothing."""
    gone = await create_user(temp_db, Role.GUEST)
    await temp_db.execute(
        "CREATE TABLE pass_presses (asset_id TEXT NOT NULL, pass TEXT NOT NULL,"
        " user_id TEXT NOT NULL, started_at INTEGER NOT NULL, finished_at INTEGER NOT NULL,"
        " PRIMARY KEY (asset_id, pass))"
    )
    for asset_id, pass_key, user_id in (
        (world.solo, "generate_file", actors.guest.id),
        (world.solo, "thumbnails", actors.guest.id),
        (world.twin, "faces", gone.id),
    ):
        await temp_db.execute(
            "INSERT INTO pass_presses VALUES (?, ?, ?, ?, ?)",
            (asset_id, pass_key, user_id, LATER - 30, LATER + 30),
        )
    await temp_db.execute("DELETE FROM users WHERE id = ?", (gone.id,))
    async with temp_db.write() as connection:
        await schema.initialize_catalog(connection, 84)
    async with temp_db.write() as connection:
        assert await carry_into_the_ledger(connection) == 0
    rows = await temp_db.fetch_all(
        "SELECT d.actor_id AS who, d.user_id AS user_id, d.payload AS payload,"
        " d.decided_at AS at, s.subject_id AS file"
        " FROM workbench_decisions d JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'pressed' ORDER BY s.subject_id = ?",
        (world.twin,),
    )
    assert [
        (row["file"], row["who"], row["user_id"], json.loads(row["payload"]), row["at"])
        for row in rows
    ] == [
        (
            world.solo,
            actors.guest.id,
            actors.guest.id,
            {"passes": ["generate_file", "thumbnails"], "began": LATER - 30},
            LATER + 30,
        ),
        (world.twin, gone.id, None, {"passes": ["faces"], "began": LATER - 30}, LATER + 30),
    ]
    present = await temp_db.fetch_all("SELECT 1 FROM sqlite_master WHERE name = 'pass_presses'", ())
    assert present == []
    name = await _name(temp_db, actors.guest.id)
    [line] = await _lines(temp_db, access, actors.admin, world.solo)
    assert _words(line) == f"{name} had Sift generate a thumbnail for this file"


async def test_a_catalog_with_no_ledger_beside_it_still_lets_the_old_record_go(
    temp_db: Database, world: World
) -> None:
    """The kernel brought up alone, as its own tests bring it up, has no ledger to carry a press
    into: the step writes no act and still takes the old record away, so it finishes."""
    _ = world
    await temp_db.execute(
        "CREATE TABLE pass_presses (asset_id TEXT NOT NULL, pass TEXT NOT NULL,"
        " user_id TEXT NOT NULL, started_at INTEGER NOT NULL, finished_at INTEGER NOT NULL,"
        " PRIMARY KEY (asset_id, pass))"
    )
    await temp_db.execute(
        "INSERT INTO pass_presses VALUES (?, 'faces', 'someone', 1, 2)", (world.solo,)
    )
    await temp_db.execute("DROP TABLE workbench_decision_subjects")
    await temp_db.execute("DROP TABLE workbench_decisions")

    async with temp_db.write() as connection:
        assert await carry_into_the_ledger(connection) == 0
    present = await temp_db.fetch_all("SELECT 1 FROM sqlite_master WHERE name = 'pass_presses'", ())
    assert present == []


def test_a_press_is_said_in_the_feed_and_on_a_page_by_its_passes() -> None:
    from sift.kernel.access import sentences as say

    file = say.thing("asset", "a1", "holiday.mp4")
    payload = {"passes": ["generate_file", "thumbnails", "previews", "faces"]}
    feed = say.feed_line("pressed", by=say.YOU, subjects=[("asset", file)], payload=payload)
    assert say.text_of(feed.pieces) == (
        "You had Sift generate a thumbnail and a hover preview for holiday.mp4"
        " and look for faces in holiday.mp4"
    )
    page = say.event_said("pressed", by=say.YOU, here=say.VANTAGE_FILE, payload=payload)
    assert say.text_of(page.pieces).startswith("You had Sift generate a thumbnail and a hover")
    # A press naming no pass this build knows is the task it was.
    alone = say.event_said("pressed", by=say.YOU, here=say.VANTAGE_FILE, payload={"passes": ["x"]})
    assert say.text_of(alone.pieces) == "You had Sift run a task on this file"
    twice = say.text_of(say.pressed_here("You", ["faces"], 2))
    assert twice == "You had Sift look for faces in this file twice"


def test_presses_folded_in_the_feed_say_their_passes() -> None:
    from sift.kernel.access import sentences as say

    file = say.thing("asset", "a1", "holiday.mp4")
    payload = {"passes": ["identify_file", "faces"]}
    again = say.feed_folded(
        "pressed",
        by=say.YOU,
        acts=3,
        subjects=[("asset", file)],
        subject_counts={"asset": 1},
        payload=payload,
    )
    assert say.text_of(again.pieces) == "You had Sift look for faces in holiday.mp4 3 times"
    run = say.feed_folded(
        "pressed",
        by=say.YOU,
        acts=40,
        subjects=[("asset", file)],
        subject_counts={"asset": 40},
        payload=payload,
    )
    assert say.text_of(run.pieces) == "You had Sift look for faces in 40 files"
    assert [group.words for group in run.groups] == ["The newest 1 of 40 files"]


async def test_fingerprints_sift_made_with_nobody_pressing_are_sift_s(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The ledger's fingerprints line keeps Sift as who did it where no press of that pass covers
    its moment, even beside a press of another pass at the same moment."""
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=ActorOf.sift(VIA_FINGERPRINT),
            verb="scanned",
            subject=Subject(kind="asset", id=world.solo, name="solo.mp4"),
        )
    row = await temp_db.fetch_one(
        "SELECT decided_at FROM workbench_decisions WHERE verb = 'scanned'", ()
    )
    assert row is not None
    await _press(temp_db, world.solo, "meaning", actors.guest.id, at=int(row["decided_at"]))

    lines = await _lines(temp_db, access, actors.admin, world.solo)

    scanned = next(one for one in lines if one.kind == "scanned")
    assert scanned.actor is Actor.SIFT
    assert "had Sift" not in _words(scanned)


async def test_a_look_for_faces_whose_record_will_not_read_still_says_it_looked(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A look's counts are its act's payload; one that will not read says the look happened and
    nothing it cannot vouch for."""
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=ActorOf.sift(VIA_FACES),
            verb="face_run",
            subject=Subject(kind="asset", id=world.solo, name="solo.mp4"),
            payload="not json",
        )

    [line] = [one for one in await _lines(temp_db, access, actors.admin, world.solo)]

    assert (line.kind, _words(line)) == ("face_run", "Sift looked for faces here and found none")


def _act(payload: object, *, actor_id: str | None = "u-1") -> LedgerEvent:
    return LedgerEvent(
        id="p-1",
        at=ARRIVED + 100,
        verb="pressed",
        actor_kind="user",
        actor_id=actor_id,
        user_id=actor_id,
        object=None,
        count=None,
        queue=LEDGER_QUEUE,
        payload=json.dumps(payload),
        title="",
        detail="",
        reversed_at=None,
    )


def test_a_pressed_act_that_names_no_pass_or_no_presser_is_no_press() -> None:
    """A press is a presser and the passes the job ran. An act missing either (an empty or
    malformed list of passes, or no presser) claims no pass line, so the line stays Sift's."""
    assert press_of(_act({"passes": ["faces"], "began": ARRIVED})) == Press(
        id="p-1", user_id="u-1", passes=("faces",), began=ARRIVED, ended=ARRIVED + 100
    )
    malformed: tuple[dict[str, object], ...] = (
        {},
        {"passes": "faces"},
        {"passes": []},
        {"passes": [3]},
    )
    for payload in malformed:
        assert press_of(_act(payload)) is None, payload
    assert press_of(_act({"passes": ["faces"]}, actor_id=None)) is None


def test_of_two_presses_covering_one_moment_the_one_that_ended_last_is_who_pressed() -> None:
    admin = Viewer(id="admin", role=Role.ADMIN)
    later = Press(id="p-later", user_id="u-2", passes=("faces",), began=100, ended=300)
    earlier = Press(id="p-earlier", user_id="u-1", passes=("faces",), began=100, ended=200)
    pressers = Pressers(
        presses=(later, earlier), who=_Who(viewer=admin, names={"u-1": "one", "u-2": "two"})
    )

    assert pressers.of(FACE_RUN_SAID, 150) == (Actor.ANOTHER_USER, "two")
    assert pressers.claimed == {"p-later"}
