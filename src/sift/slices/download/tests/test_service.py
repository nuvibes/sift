# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ledger and the site logins: recording a download, reading it back, and the saved cookies."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from sift.kernel.access.sites import site_address
from sift.kernel.db import Database
from sift.kernel.jobs import Workspaces, register_handler
from sift.kernel.jobs.tuning import DEFAULT_PRIORITY, WAITED_ON_PRIORITY
from sift.kernel.sql_splice import splice
from sift.slices.download.service import (
    DownloadService,
    DownloadView,
    PasteChoices,
    _creator_scope_of,
    _shown_url,
    site_home_of,
    site_name_of,
)
from sift.slices.download.sources import url_hash

_URL = "https://www.tiktok.com/@creator/video/1"


async def _view(service: DownloadService, download_id: str) -> DownloadView:
    view = await service.get(download_id)
    assert view is not None
    return view


async def _secret_id(service: DownloadService, site: str) -> str:
    secret = await service.connection_for_site(site)
    assert secret is not None and secret.secret_id is not None
    return secret.secret_id


@pytest.fixture
def registered_download() -> Iterator[None]:
    """A no-op download handler, so `submit_url` can enqueue without a real one wired."""

    async def noop(_ctx: object) -> None:
        return None

    register_handler("download", noop, name="Test job")
    yield


async def _insert_download(
    db: Database,
    download_id: str,
    *,
    state: str,
    url: str = _URL,
    asset_id: str | None = None,
) -> None:
    await db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, asset_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (download_id, url, url_hash(url), state, asset_id),
    )


async def _insert_asset(db: Database, asset_id: str) -> None:
    """The file a finished download produced, so a ledger row can point at something.

    A settled row whose asset is gone is a URL whose file has been deleted, and the ledger stops
    calling that one already-downloaded, so a test about recognizing a finished URL has to give
    it a file, or it is quietly testing the other case.
    """
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        (asset_id, f"hash-{asset_id}"),
    )


async def _insert_job(db: Database, download_id: str, *, state: str) -> None:
    """A job for the row, and the row told about it: what queueing one through the service does."""
    await db.execute(
        "INSERT INTO jobs (id, type, state, payload, created_at, updated_at) "
        "VALUES (?, 'download', ?, ?, 0, 0)",
        (f"job-{download_id}", state, json.dumps({"download_id": download_id})),
    )
    await db.execute(
        "UPDATE downloads SET job_id = ? WHERE id = ?", (f"job-{download_id}", download_id)
    )


async def _named_job(db: Database, download_id: str) -> str | None:
    row = await db.fetch_one("SELECT job_id FROM downloads WHERE id = ?", (download_id,))
    assert row is not None
    return None if row["job_id"] is None else str(row["job_id"])


async def test_submit_records_a_row_and_queues_a_job(
    download_service: DownloadService, temp_db: Database, registered_download: None
) -> None:
    download_id = await download_service.submit_url(url=_URL, dest_folder_id=None)

    job = await download_service.job_input(download_id)
    assert job is not None
    assert job.url == _URL
    assert job.dest_folder_id is None

    queued = await temp_db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE type = 'download'")
    assert queued is not None
    assert queued["n"] == 1
    # And the row names the job that runs it: the screen reads the job's state off that name
    # rather than by searching every job's payload for the row.
    the_job = await temp_db.fetch_one("SELECT id FROM jobs WHERE type = 'download'")
    assert the_job is not None
    assert await _named_job(temp_db, download_id) == str(the_job["id"])


async def test_what_a_paste_chose_for_itself_is_on_the_row_and_outlives_a_retry(
    download_service: DownloadService, temp_db: Database, registered_download: None
) -> None:
    """The Downloads page decides for THIS download: its choice travels with the paste and is
    read back by the job, including after Try again, which queues a new job for the same row. A
    choice carried only in the first job's payload would be dropped by exactly that retry."""
    chosen = await download_service.submit_url(url=_URL, choices=PasteChoices(remember=False))
    await temp_db.execute("UPDATE downloads SET state = 'failed' WHERE id = ?", (chosen,))
    assert await download_service.retry(chosen) is True

    job = await download_service.job_input(chosen)
    assert job is not None
    assert job.choices == PasteChoices(remember=False)

    kept = await download_service.submit_url(
        url="https://www.tiktok.com/@a/video/3", choices=PasteChoices(remember=True)
    )
    kept_job = await download_service.job_input(kept)
    assert kept_job is not None
    assert kept_job.choices == PasteChoices(remember=True)

    # Nothing chosen (a drop, the extension) is NULL, which the job reads as "follow the setting".
    plain = await download_service.submit_url(url="https://www.tiktok.com/@a/video/2")
    plain_job = await download_service.job_input(plain)
    assert plain_job is not None
    assert plain_job.choices == PasteChoices(remember=None)


async def test_a_row_run_again_names_its_newest_job(
    download_service: DownloadService, temp_db: Database, registered_download: None
) -> None:
    """A retry and a fetch-anyway each queue a second job for the same row, and the row has to
    name the new one: the old job's state is the ending being retried, and a cancel that reached
    the old job would stop nothing."""
    download_id = await download_service.submit_url(url=_URL, dest_folder_id=None)
    first = await _named_job(temp_db, download_id)
    assert first is not None
    await temp_db.execute("UPDATE downloads SET state = 'failed' WHERE id = ?", (download_id,))

    assert await download_service.retry(download_id) is True
    second = await _named_job(temp_db, download_id)
    assert second is not None and second != first

    await temp_db.execute("UPDATE downloads SET state = 'skipped' WHERE id = ?", (download_id,))
    assert await download_service.fetch_anyway(download_id) is True
    third = await _named_job(temp_db, download_id)
    assert third is not None and third not in {first, second}


async def test_job_input_is_none_for_a_row_that_is_gone(
    download_service: DownloadService,
) -> None:
    assert await download_service.job_input("nope") is None


async def test_the_ledger_recognizes_a_finished_url(
    download_service: DownloadService, temp_db: Database
) -> None:
    await _insert_asset(temp_db, "a1")
    await _insert_download(temp_db, "d1", state="done", asset_id="a1")
    assert await download_service.is_already_done(url_hash(_URL)) is True
    assert await download_service.is_already_done(url_hash("https://other.example/x")) is False


async def test_a_duplicate_url_also_counts_as_finished(
    download_service: DownloadService, temp_db: Database
) -> None:
    # A download that found everything already in the library settles as 'duplicate', but the URL has
    # still been through: a re-paste of it is skipped just as a 'done' one is. (mark_done's done vs
    # duplicate choice is exercised end to end, with real assets, in the job tests.)
    await _insert_asset(temp_db, "a1")
    await _insert_download(temp_db, "d1", state="duplicate", asset_id="a1")
    assert await download_service.is_already_done(url_hash(_URL)) is True


async def test_a_finished_url_whose_file_was_deleted_can_be_fetched_again(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The ledger keeps the row as history, and the database clears its asset when the file goes.

    Read without that, the row would go on answering "you already have this" about a file nobody
    has any more, and the only way to get it back would be the "download it anyway" override.
    """
    await _insert_asset(temp_db, "a1")
    await _insert_download(temp_db, "d1", state="done", asset_id="a1")
    assert await download_service.is_already_done(url_hash(_URL)) is True

    await temp_db.execute("DELETE FROM assets WHERE id = ?", ("a1",))

    assert await download_service.is_already_done(url_hash(_URL)) is False


async def test_state_transitions(download_service: DownloadService, temp_db: Database) -> None:
    await _insert_download(temp_db, "d1", state="queued")

    await download_service.mark_running("d1")
    assert (await _view(download_service, "d1")).status == "running"

    await download_service.mark_failed("d1", error="a plain message")
    view = await _view(download_service, "d1")
    assert view.status == "failed"
    assert view.error == "a plain message"

    await _insert_download(temp_db, "d2", state="queued")
    await download_service.mark_skipped("d2")
    assert (await _view(download_service, "d2")).status == "skipped"


async def test_a_blocked_job_shows_the_download_as_waiting(
    download_service: DownloadService, temp_db: Database
) -> None:
    await _insert_download(temp_db, "d1", state="running")
    await _insert_job(temp_db, "d1", state="blocked")

    view = await _view(download_service, "d1")
    assert view.status == "blocked"

    page = await download_service.list_downloads(limit=10)
    assert page.downloads[0].status == "blocked"


async def test_an_exhausted_job_shows_the_download_as_failed(
    download_service: DownloadService, temp_db: Database
) -> None:
    await _insert_download(temp_db, "d1", state="running")
    await _insert_job(temp_db, "d1", state="failed")
    assert (await _view(download_service, "d1")).status == "failed"


async def test_list_is_newest_first_and_counts_the_whole_ledger(
    download_service: DownloadService, temp_db: Database
) -> None:
    for index in range(3):
        await temp_db.execute(
            "INSERT INTO downloads (id, url, url_hash, state, created_at) VALUES (?, ?, ?, 'done', ?)",
            (f"d{index}", f"https://x.example/{index}", f"h{index}", index),
        )
    page = await download_service.list_downloads(limit=2)
    assert page.total == 3
    assert [view.id for view in page.downloads] == ["d2", "d1"]


async def test_a_download_with_two_jobs_is_still_one_row(
    download_service: DownloadService, temp_db: Database
) -> None:
    """A second job against one download must not make the ledger report it twice.

    "Download anyway" enqueues another job for a download that already has one. A join onto every
    job would fan the row out: the total would count the copies, and the screen draws this list
    keyed by id, where a repeated key abandons the whole list.

    The row names its newest job, so the join is on one id and cannot fan out; the older job is
    still in the table and must be ignored.
    """
    await _insert_download(temp_db, "d1", state="running")
    await temp_db.execute(
        "INSERT INTO jobs (id, type, state, payload, created_at, updated_at) "
        "VALUES ('job-old', 'download', 'failed', ?, 1, 1)",
        (json.dumps({"download_id": "d1"}),),
    )
    await temp_db.execute(
        "INSERT INTO jobs (id, type, state, payload, created_at, updated_at) "
        "VALUES ('job-new', 'download', 'running', ?, 2, 2)",
        (json.dumps({"download_id": "d1"}),),
    )
    await temp_db.execute("UPDATE downloads SET job_id = 'job-new' WHERE id = 'd1'")

    page = await download_service.list_downloads(limit=10)

    assert [view.id for view in page.downloads] == ["d1"]
    assert page.total == 1
    # And the status comes from the job the row NAMES, which is the one describing what is
    # happening now.
    assert page.downloads[0].status == "running"
    assert (await _view(download_service, "d1")).status == "running"


async def test_get_is_none_for_a_missing_download(download_service: DownloadService) -> None:
    assert await download_service.get("nope") is None


async def test_an_empty_ledger_lists_nothing(download_service: DownloadService) -> None:
    page = await download_service.list_downloads(limit=10)
    assert page.downloads == []
    assert page.total == 0


# --- site logins ------------------------------------------------------------------------------


async def test_saving_a_login_seals_the_cookie_and_lists_the_connection(
    download_service: DownloadService, master_key: bytes, admin_id: str
) -> None:
    await download_service.save_connection(
        site="TikTok", cookie="c=1", master_key=master_key, by=admin_id
    )

    connections = await download_service.list_connections()
    assert len(connections) == 1
    assert connections[0].site == "TikTok"

    secret = await download_service.connection_for_site("TikTok")
    assert secret is not None and secret.secret_id is not None
    assert await download_service.open_cookie(secret.secret_id, master_key) == "c=1"


async def test_replacing_a_login_forgets_the_old_cookie(
    download_service: DownloadService, master_key: bytes, admin_id: str
) -> None:
    first_id = await download_service.save_connection(
        site="TikTok", cookie="old", master_key=master_key, by=admin_id
    )
    old_secret = await _secret_id(download_service, "TikTok")

    second_id = await download_service.save_connection(
        site="TikTok", cookie="new", master_key=master_key, by=admin_id
    )
    assert second_id == first_id  # the connection is replaced, not duplicated

    new_secret = await _secret_id(download_service, "TikTok")
    assert new_secret != old_secret
    assert await download_service.open_cookie(old_secret, master_key) is None
    assert await download_service.open_cookie(new_secret, master_key) == "new"


async def test_deleting_a_login_removes_it(
    download_service: DownloadService, master_key: bytes, admin_id: str
) -> None:
    connection_id = await download_service.save_connection(
        site="TikTok", cookie="c", master_key=master_key, by=admin_id
    )
    assert await download_service.delete_connection(connection_id, by=admin_id) is True
    assert await download_service.list_connections() == []
    assert await download_service.delete_connection(connection_id, by=admin_id) is False


async def test_no_connection_for_an_unknown_site(download_service: DownloadService) -> None:
    assert await download_service.connection_for_site("Nowhere") is None


@dataclass(frozen=True, slots=True)
class _Event:
    """One row of the record, read back the way a history reader would."""

    verb: str
    actor: tuple[str, str | None]
    payload: str
    about: tuple[tuple[str, str | None], ...]


async def _events(db: Database) -> list[_Event]:
    """Every event the record holds, oldest first, with the things each one is about."""
    rows = await db.fetch_all(
        "SELECT id, verb, actor_kind, actor_id, payload FROM workbench_decisions ORDER BY id", ()
    )
    events = []
    for row in rows:
        about = await db.fetch_all(
            "SELECT kind, subject_id, name FROM workbench_decision_subjects WHERE decision_id = ?",
            (row["id"],),
        )
        events.append(
            _Event(
                verb=str(row["verb"]),
                actor=(str(row["actor_kind"]), row["actor_id"]),
                payload=str(row["payload"]),
                about=tuple((str(one["kind"]), one["name"]) for one in about),
            )
        )
    return events


async def test_saving_cookies_is_on_the_record_against_the_site(
    download_service: DownloadService, temp_db: Database, master_key: bytes, admin_id: str
) -> None:
    """A saved jar is an act somebody took with a consequence they can feel, and it wrote nothing.

    The Site is what the event is about, because a Site's page is where somebody goes to ask why it
    stopped fetching. Nothing of the jar is in the event: not a cookie, not a domain, not a count.
    """
    await download_service.save_connection(
        site="TikTok", cookie="c=1", master_key=master_key, by=admin_id
    )

    events = await _events(temp_db)
    assert [one.verb for one in events] == ["cookies_saved"]
    assert events[0].actor == ("user", admin_id)
    assert events[0].about == (("site", "TikTok"),)


async def test_replacing_cookies_says_it_replaced_them(
    download_service: DownloadService, temp_db: Database, master_key: bytes, admin_id: str
) -> None:
    """Two acts and two rows: a Site that has been given cookies twice says so twice."""
    await download_service.save_connection(
        site="TikTok", cookie="old", master_key=master_key, by=admin_id
    )
    await download_service.save_connection(
        site="TikTok", cookie="new", master_key=master_key, by=admin_id
    )

    events = await _events(temp_db)
    assert [one.verb for one in events] == ["cookies_saved", "cookies_replaced"]


async def test_forgetting_cookies_outlives_the_row_it_deleted(
    download_service: DownloadService, temp_db: Database, master_key: bytes, admin_id: str
) -> None:
    """The one write here that deletes its own row, which is why this event matters most.

    Without it, forgetting a Site's cookies leaves nothing anywhere saying they were ever there,
    and the Site's name is gone with the row, so the event carries the name it had.
    """
    connection_id = await download_service.save_connection(
        site="TikTok", cookie="c", master_key=master_key, by=admin_id
    )

    assert await download_service.delete_connection(connection_id, by=admin_id) is True

    events = await _events(temp_db)
    assert events[-1].verb == "cookies_forgotten"
    assert events[-1].about == (("site", "TikTok"),)
    assert await download_service.list_connections() == []


async def test_a_forget_that_finds_no_row_records_nothing(
    download_service: DownloadService, temp_db: Database, admin_id: str
) -> None:
    """The known negative. Nothing happened, so nothing is on the record."""
    assert await download_service.delete_connection("no-such-connection", by=admin_id) is False
    assert await _events(temp_db) == []


async def test_a_site_the_cookies_form_makes_names_who_saved_them(
    download_service: DownloadService, temp_db: Database, master_key: bytes, admin_id: str
) -> None:
    await download_service.save_connection(
        site="Northlight Group", cookie="c=1", master_key=master_key, by=admin_id
    )
    row = await temp_db.fetch_one(
        "SELECT created_by_kind, created_by_user_id FROM sites WHERE name = ?",
        ("Northlight Group",),
    )
    assert row is not None
    assert (row["created_by_kind"], row["created_by_user_id"]) == ("user", admin_id)


async def test_a_site_the_cookies_list_makes_wears_the_catalogs_spelling(
    download_service: DownloadService, temp_db: Database, master_key: bytes, admin_id: str
) -> None:
    """The list hands over a lowercase key; the Site it makes is named the way the list showed it."""
    await download_service.save_connection(
        site="saint", cookie="c=1", master_key=master_key, by=admin_id
    )
    names = [str(row["name"]) for row in await temp_db.fetch_all("SELECT name FROM sites", ())]
    assert names == ["Saint"]
    events = await _events(temp_db)
    assert events[-1].about == (("site", "Saint"),)


async def _connection_without_secret(db: Database, connection_id: str, site: str) -> None:
    from sift.kernel.access import MADE_BY_A_PERSON, ensure_site

    site_id = await ensure_site(db, site, made=MADE_BY_A_PERSON)
    await db.execute(
        "INSERT INTO site_connections (id, site_id, secret_id, status, updated_at) "
        "VALUES (?, ?, NULL, 'saved', 0)",
        (connection_id, site_id),
    )


async def test_replacing_a_connection_that_had_no_secret(
    download_service: DownloadService, temp_db: Database, master_key: bytes, admin_id: str
) -> None:
    await _connection_without_secret(temp_db, "sc1", "TikTok")
    assert (
        await download_service.save_connection(
            site="TikTok", cookie="c", master_key=master_key, by=admin_id
        )
        == "sc1"
    )


async def test_deleting_a_connection_that_had_no_secret(
    download_service: DownloadService, temp_db: Database, admin_id: str
) -> None:
    await _connection_without_secret(temp_db, "sc2", "X")
    assert await download_service.delete_connection("sc2", by=admin_id) is True


async def test_the_schema_is_a_noop_once_it_is_current(temp_db: Database) -> None:
    from sift.slices.download.schema import DOWNLOAD_VERSION, initialize_download

    # The declared version rather than a number typed here: a step added later moves the version,
    # and a hard-coded one turns this from "nothing runs" into "the newest step runs, twice".
    async with temp_db.write() as connection:
        await initialize_download(connection, DOWNLOAD_VERSION)


async def test_cancel_stops_a_queued_download_and_its_job(
    download_service: DownloadService,
    temp_db: Database,
    registered_download: None,
    admin_id: str,
) -> None:
    download_id = await download_service.submit_url(url=_URL, dest_folder_id=None)

    assert await download_service.cancel(download_id, by=admin_id) is True
    assert (await _view(download_service, download_id)).status == "canceled"

    job = await temp_db.fetch_one(
        "SELECT state FROM jobs WHERE json_extract(payload, '$.download_id') = ?", (download_id,)
    )
    assert job is not None and job["state"] == "canceled"


async def test_cancel_is_false_for_a_finished_download(
    download_service: DownloadService, temp_db: Database, admin_id: str
) -> None:
    await _insert_download(temp_db, "d1", state="done")
    assert await download_service.cancel("d1", by=admin_id) is False
    assert (await _view(download_service, "d1")).status == "done"  # unchanged


async def test_cancel_marks_a_download_that_has_no_job_row(
    download_service: DownloadService, temp_db: Database, admin_id: str
) -> None:
    await _insert_download(temp_db, "d1", state="queued")  # a row with no job behind it
    assert await download_service.cancel("d1", by=admin_id) is True
    assert (await _view(download_service, "d1")).status == "canceled"


async def test_cancel_is_false_for_a_download_that_is_gone(
    download_service: DownloadService, admin_id: str
) -> None:
    assert await download_service.cancel("nope", by=admin_id) is False


async def test_cancelling_is_on_the_record_with_who_pressed_it(
    download_service: DownloadService, temp_db: Database, admin_id: str
) -> None:
    """Stopping a download for good is on the record, as pausing one for a minute is.

    The subject is the row, because a download that never landed has no file for the event to hang
    on, and the name it carries is the address as a person reads it.
    """
    await _insert_download(temp_db, "d1", state="queued")

    assert await download_service.cancel("d1", by=admin_id) is True

    events = await _events(temp_db)
    assert [one.verb for one in events] == ["canceled"]
    assert events[0].actor == ("user", admin_id)
    assert [kind for kind, _ in events[0].about] == ["download"]


async def test_a_cancel_that_changes_nothing_records_nothing(
    download_service: DownloadService, temp_db: Database, admin_id: str
) -> None:
    """The known negative beside it: a download that had already finished is not stopped twice."""
    await _insert_download(temp_db, "d1", state="done")

    assert await download_service.cancel("d1", by=admin_id) is False
    assert await _events(temp_db) == []


# --- fetching one the ledger already knew about --------------------------------------------------


async def test_fetching_anyway_re_queues_the_same_row_and_says_to_pass_the_ledger(
    download_service: DownloadService, temp_db: Database, registered_download: None
) -> None:
    """The same row, not a second one, and the job carries the one instruction that matters.

    Two rows for one link would leave two records of it with two different endings, which is the
    thing this route exists to avoid.
    """
    await _insert_download(temp_db, "d1", state="skipped")

    assert await download_service.fetch_anyway("d1") is True

    assert (await _view(download_service, "d1")).status == "queued"
    rows = await temp_db.fetch_all("SELECT id FROM downloads")
    assert len(rows) == 1  # the same row, re-queued
    job = await temp_db.fetch_one(
        "SELECT payload FROM jobs WHERE json_extract(payload, '$.download_id') = ?", ("d1",)
    )
    assert job is not None
    assert json.loads(str(job["payload"]))["ignore_ledger"] is True


async def test_fetching_anyway_a_download_that_is_gone_is_false_and_queues_nothing(
    download_service: DownloadService, temp_db: Database
) -> None:
    """Answering differently for a row that is not there would make this a way of asking which
    download ids exist."""
    assert await download_service.fetch_anyway("nope") is False
    assert await temp_db.fetch_all("SELECT id FROM jobs") == []


async def test_a_username_that_is_not_a_person_is_recorded_without_inventing_one(
    download_service: DownloadService, temp_db: Database
) -> None:
    """A subreddit is a username and is nobody.

    The site registry decides which sites name creators, because this cannot tell a creator's
    username from the name of a board, and a rule that could not tell them apart would file
    `r/pics` in the People list beside actual people. The username is still recorded: it is where
    the file came from, and that is worth keeping whether or not it is somebody.
    """
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at) "
        "VALUES ('a1', 'digest-a1', 'image', 4, 'x.png', 0)"
    )

    await download_service.attribute(
        asset_id="a1", site="Reddit", username="pics", username_is_a_person=False
    )

    username = await temp_db.fetch_one("SELECT name FROM usernames")
    assert username is not None and username["name"] == "pics"
    assert await temp_db.fetch_all("SELECT id FROM people") == []
    assert await temp_db.fetch_all("SELECT asset_id FROM asset_people") == []


async def test_a_settled_row_records_when_it_stopped(
    download_service: DownloadService, registered_download: None
) -> None:
    """How long a download took, which is a different question from how long ago it started.

    Both are needed: a row that carried its start and nothing else could say a finished download
    was queued an hour ago and never that it ran for nine seconds.
    """
    download_id = await download_service.submit_url(url=_URL, dest_folder_id=None)
    assert (await _view(download_service, download_id)).finished_at is None

    await download_service.mark_running(download_id)
    assert (await _view(download_service, download_id)).finished_at is None, (
        "a download that is still going has not finished"
    )

    await download_service.mark_done(download_id, asset_id=None, site="TikTok", username="creator")
    settled = await _view(download_service, download_id)
    assert settled.finished_at is not None
    assert settled.finished_at >= settled.created_at


async def test_going_again_clears_the_stop_time_of_the_attempt_before(
    download_service: DownloadService, registered_download: None
) -> None:
    """A retry is a new attempt. Left alone, the previous attempt's stop time would have a running
    row reporting a duration that ended before this attempt began."""
    download_id = await download_service.submit_url(url=_URL, dest_folder_id=None)
    await download_service.mark_failed(download_id, error="the site refused")
    assert (await _view(download_service, download_id)).finished_at is not None

    await download_service.mark_running(download_id)
    assert (await _view(download_service, download_id)).finished_at is None


# --- what a row says about its address, before anything has been fetched ------------------------
#
# Three small readers that turn the address into what a row draws. Their empty-address branches are
# here rather than driven through the API deliberately: the ledger's own column cannot be null, so
# the only way to reach them is to call them, and a guard nothing can reach is a guard nobody has
# checked.


def test_no_address_is_no_second_form_and_no_name() -> None:
    assert _shown_url(None) is None
    assert site_name_of(None) is None
    assert _creator_scope_of(None, "somebody") is None


def test_a_site_that_files_uploads_under_a_channel_names_no_creator() -> None:
    """A username is not always a person.

    On a recognised site whose addresses name a channel, a board or a room rather than somebody, a
    creator picture filed under that name would put one face on everything the room ever posted,
    so there is no scope for it and the card keeps its monogram.
    """
    assert (
        _creator_scope_of("https://cdn.discordapp.com/attachments/1/2/clip.mp4", "somebody") is None
    )


# --- a download nothing will ever settle -------------------------------------------------------
#
# A download job records its own failure on its last attempt, which covers every failure the handler
# is present to see. It is not present for the one below: a worker killed mid-download runs no code
# at all, so the queue expires the claim, the attempts run out, the reaper fails the job, and the
# ledger row says `running` for the rest of the database's life.


async def test_a_row_whose_job_gave_up_is_not_counted_as_still_downloading(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The count and the list answer about the same population.

    A count that read the ledger alone beside a list that folds the job's state in would say
    `1 downloading` in the corner of a page whose list says `Nothing downloading`.
    """
    await _insert_download(temp_db, "orphan", state="running")
    await _insert_job(temp_db, "orphan", state="failed")

    page = await download_service.list_downloads(limit=10)

    assert page.running == 0, "the count says nothing is downloading"
    assert [one.status for one in page.downloads] == ["failed"], "and so does the row"


async def test_a_row_whose_job_is_still_going_is_counted(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The known positive. Without it the check above passes on a count that is always zero."""
    await _insert_download(temp_db, "live", state="running")
    await _insert_job(temp_db, "live", state="running")

    page = await download_service.list_downloads(limit=10)

    assert page.running == 1
    assert [one.status for one in page.downloads] == ["running"]


async def test_state_counts_agree_with_the_list(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The chips' numbers are the queue's, folded the way each row is.

    Three copies of one rule (`_display_status`, `_ACTIVE_COUNTS`, `_STATE_COUNTS`), and this
    is what holds the third to the first: every state a row on the list shows is a key here, with
    the same count, over a queue that has one of each kind of fold in it. A chip counting the
    ledger's word alone would say "running 2" over a list showing one failed and one running.
    """
    await _insert_download(temp_db, "orphan", state="running")
    await _insert_job(temp_db, "orphan", state="failed")
    await _insert_download(temp_db, "live", state="running")
    await _insert_job(temp_db, "live", state="running")
    await _insert_download(temp_db, "asking", state="queued")
    await _insert_job(temp_db, "asking", state="blocked")
    await _insert_download(temp_db, "landed", state="done")
    await _insert_download(temp_db, "held", state="paused")
    await _insert_job(temp_db, "held", state="paused")

    page = await download_service.list_downloads(limit=10)

    shown: dict[str, int] = {}
    for one in page.downloads:
        shown[one.status] = shown.get(one.status, 0) + 1
    assert page.by_state == shown
    assert page.by_state == {"failed": 1, "running": 1, "blocked": 1, "done": 1, "paused": 1}
    # And the strip's own two figures, which count a different population: a paused download is
    # neither downloading nor waiting its turn, so it appears in neither. The waiting one is the
    # blocked row, which the ledger still holds as queued (it IS waiting, on somebody rather than
    # on a worker), and that is what this figure counts.
    # The strip's figures are the SHOWN states: the row whose job is blocked is waiting for
    # cookies, not waiting in the line, so it is neither downloading nor queued here.
    assert (page.running, page.queued) == (1, 0)


async def test_a_paused_job_under_a_row_that_was_not_told_reads_as_paused(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The window between the two writes a pause makes, which is the reason the job's state is
    folded in at all: the queue is asked to stop first and the row is written after, so a process
    that stops in between comes back with a paused job under a row that still says running."""
    await _insert_download(temp_db, "mid", state="running")
    await _insert_job(temp_db, "mid", state="paused")

    page = await download_service.list_downloads(limit=10)

    assert [one.status for one in page.downloads] == ["paused"]
    assert page.running == 0, "and it is not counted as downloading either"


async def test_the_line_is_numbered_in_the_order_the_queue_will_take_them(
    download_service: DownloadService, temp_db: Database
) -> None:
    """Where each waiting download is, counting from 1, in the order the queue claims.

    Ranked over the JOBS rather than over the ledger rows, which is what the promoted row proves:
    moving a download to the front writes the queue's own priority onto its job and touches the
    ledger row not at all, so a position worked out from when the row was made would have said it
    was still third.
    """
    for name in ("first", "second", "third"):
        await _insert_download(temp_db, name, state="queued")
        await _insert_job(temp_db, name, state="queued")
    # The queue takes a priority's jobs in the order of their ids, never by the clock, and these
    # ids sort in the order the jobs were made: job-first, job-second, job-third.

    page = await download_service.list_downloads(limit=10)
    assert {one.id: one.position for one in page.downloads} == {
        "first": 1,
        "second": 2,
        "third": 3,
    }

    await temp_db.execute("UPDATE jobs SET priority = ? WHERE id = ?", (0, "job-third"))
    page = await download_service.list_downloads(limit=10)
    assert {one.id: one.position for one in page.downloads} == {
        "third": 1,
        "first": 2,
        "second": 3,
    }


async def test_nothing_that_is_not_waiting_has_a_place_in_the_line(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The known negative. A running, paused or finished download is not in the line at all, and a
    number on it would be a fact about nothing, so it is absent rather than zero."""
    await _insert_download(temp_db, "going", state="running")
    await _insert_job(temp_db, "going", state="running")
    await _insert_download(temp_db, "held", state="paused")
    await _insert_job(temp_db, "held", state="paused")
    await _insert_download(temp_db, "landed", state="done")

    page = await download_service.list_downloads(limit=10)

    assert {one.position for one in page.downloads} == {None}


async def test_a_pause_is_refused_for_anything_that_is_not_going(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The refusals, which are the half that does not need a queue to answer.

    Everything but queued and running is either already stopped or already over, and a row nothing
    knows about is the same answer: there is nothing running. Neither writes anything, so a refused
    pause cannot move a row it was not allowed to touch.
    """
    await _insert_download(temp_db, "landed", state="done")
    await _insert_download(temp_db, "held", state="paused")

    assert await download_service.pause("landed", by="user-1") is False
    assert await download_service.pause("no-such-row", by="user-1") is False
    # And the mirror image: resuming something that was never paused.
    assert await download_service.resume("landed", by="user-1") is False
    assert await download_service.resume("no-such-row", by="user-1") is False

    rows = {
        one.id: one.status for one in (await download_service.list_downloads(limit=10)).downloads
    }
    assert rows == {"landed": "done", "held": "paused"}


async def test_a_row_put_away_comes_back_and_only_the_once(
    download_service: DownloadService, temp_db: Database
) -> None:
    """Remove's undo. The answer has to tell a row that was never removed from one that was: the
    only way to this is the message saying one just was, and "fine" to a press that restored
    nothing leaves somebody looking at a list for a row that is not coming back."""
    await _insert_download(temp_db, "tidied", state="done")
    assert await download_service.hide("tidied") is None
    assert [one.id for one in (await download_service.list_downloads(limit=10)).downloads] == []

    assert await download_service.restore("tidied") is True
    assert [one.id for one in (await download_service.list_downloads(limit=10)).downloads] == [
        "tidied"
    ]

    assert await download_service.restore("tidied") is False
    assert await download_service.restore("no-such-row") is False


async def test_the_sweep_settles_an_orphan_and_says_how_many(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The screens are honest about these rows either way; this is what stops the DATABASE holding
    a state nothing can reach, which every later reader would have to know to correct."""
    await _insert_download(temp_db, "orphan", state="running")
    await _insert_job(temp_db, "orphan", state="failed")

    assert await download_service.settle_orphans() == 1

    row = await temp_db.fetch_one("SELECT state, error FROM downloads WHERE id = 'orphan'")
    assert row is not None
    assert row["state"] == "failed", "the ROW, not merely how it is displayed"
    assert "never finished" in str(row["error"])


async def test_the_sweep_leaves_a_download_that_is_really_running(
    download_service: DownloadService, temp_db: Database
) -> None:
    """Settling a live download would be worse than the fault being fixed: it would report a
    failure for something that is about to succeed, and take it off the screen while it ran."""
    await _insert_download(temp_db, "live", state="running")
    await _insert_job(temp_db, "live", state="running")

    assert await download_service.settle_orphans() == 0
    row = await temp_db.fetch_one("SELECT state FROM downloads WHERE id = 'live'")
    assert row is not None and row["state"] == "running"


async def test_the_sweep_leaves_a_row_with_no_job_at_all(
    download_service: DownloadService, temp_db: Database
) -> None:
    """Deliberately narrow: there is a moment during submission when a row has no job yet, and that
    is exactly what a healthy row looks like. Only a job that has GIVEN UP settles one."""
    await _insert_download(temp_db, "fresh", state="queued")

    assert await download_service.settle_orphans() == 0
    row = await temp_db.fetch_one("SELECT state FROM downloads WHERE id = 'fresh'")
    assert row is not None and row["state"] == "queued"


async def test_a_finished_download_tells_the_file_where_it_came_from(
    download_service: DownloadService, registered_download: None, temp_db: Database
) -> None:
    """Seeded here rather than left to the screen to ask the ledger, because it is a field on the
    record: a guest sees it, it can be corrected, and it survives the ledger row being tidied
    away."""
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a1', 'a1', 'video', 0)"
    )
    download_id = await download_service.submit_url(url=_URL, dest_folder_id=None)

    await download_service.mark_done(download_id, asset_id="a1", site="TikTok", username="creator")

    row = await temp_db.fetch_one("SELECT download_url FROM assets WHERE id = 'a1'")
    assert row is not None
    assert row["download_url"] == _URL


async def test_an_album_that_lost_a_file_keeps_how_many_on_its_row(
    download_service: DownloadService, registered_download: None, temp_db: Database
) -> None:
    """One dead file does not fail the album, and the row says so, not only the log. It keeps how
    many the link offered and how many were left out, the page and the single read both carry them, and
    a row that lost none keeps neither."""
    await _insert_asset(temp_db, "a1")
    album = await download_service.submit_url(url=_URL, dest_folder_id=None)
    whole = await download_service.submit_url(
        url="https://www.tiktok.com/@creator/video/2", dest_folder_id=None
    )

    await download_service.mark_done(
        album, asset_id="a1", site="TikTok", username="creator", offered=219, left_out=1
    )
    await download_service.mark_done(
        whole, asset_id="a1", site="TikTok", username="creator", offered=40, left_out=0
    )

    lost = await _view(download_service, album)
    assert (lost.files_offered, lost.files_left_out) == (219, 1)
    kept = await _view(download_service, whole)
    assert (kept.files_offered, kept.files_left_out) == (None, None)
    page = await download_service.list_downloads(limit=10)
    listed = {one.id: (one.files_offered, one.files_left_out) for one in page.downloads}
    assert listed == {album: (219, 1), whole: (None, None)}


async def test_a_read_its_tunnel_refused_stays_on_a_row_that_landed(
    download_service: DownloadService, registered_download: None, temp_db: Database
) -> None:
    from sift.slices.download.router import _item

    await _insert_asset(temp_db, "a1")
    one = await download_service.submit_url(url=_URL, dest_folder_id=None)
    said = "Sift didn't read the music. The tunnel Sweden is turned off, so nothing was sent."

    await download_service.mark_done(
        one, asset_id="a1", site="TikTok", username=None, reads_refused=said
    )

    view = await _view(download_service, one)
    assert view.status == "done" and view.reads_refused == said
    assert _item(view).reads_refused == said
    page = await download_service.list_downloads(limit=10)
    assert [row.reads_refused for row in page.downloads] == [said]
    await download_service.mark_done(one, asset_id="a1", site="TikTok", username=None)
    assert (await _view(download_service, one)).reads_refused is None


async def test_a_ledger_row_with_no_address_seeds_nothing(
    download_service: DownloadService, registered_download: None, temp_db: Database
) -> None:
    """A row whose address has been tidied away is not an address of "". Nothing is written rather
    than a blank, which the record would then draw as a link to nowhere."""
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a1', 'a1', 'video', 0)"
    )
    download_id = await download_service.submit_url(url=_URL, dest_folder_id=None)
    await temp_db.execute("UPDATE downloads SET url = '' WHERE id = ?", (download_id,))

    await download_service.mark_done(download_id, asset_id="a1", site="TikTok", username="creator")

    row = await temp_db.fetch_one("SELECT download_url FROM assets WHERE id = 'a1'")
    assert row is not None
    assert row["download_url"] is None


async def test_a_site_with_no_username_on_it_is_still_recorded(
    download_service: DownloadService, temp_db: Database
) -> None:
    """A download from a site off the built-in list has a site and no username, and the site is
    still recorded rather than thrown away for want of a second fact it never had. Nothing
    suppresses the address's answer.
    """
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at) "
        "VALUES ('a1', 'digest-a1', 'image', 4, 'x.png', 0)"
    )

    await download_service.attribute(asset_id="a1", site="Pmvhaven", username=None)

    # A file reaches a site through a username, which is why the row is written with no username
    # rather than not written: every question about a site joins through `asset_usernames`.
    filed = await temp_db.fetch_all(
        "SELECT p.name, a.name AS username FROM asset_usernames aa"
        " JOIN usernames a ON a.id = aa.username_id"
        " JOIN sites p ON p.id = a.site_id"
        " WHERE aa.asset_id = 'a1'"
    )
    # The username is the empty string and not null: "from here, poster unknown" is a value rather
    # than an absence, which is what keeps one such row per site instead of one per file.
    assert [(str(row["name"]), str(row["username"])) for row in filed] == [("Pmvhaven", "")]


async def test_a_download_s_filing_says_it_was_a_download(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The filing under the username a download read off the page names its source: the row is
    what every History reader reads to fold it into the download's own line,
    in place of guessing from the clock. A person's filing keeps no source."""
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at) "
        "VALUES ('a1', 'digest-a1', 'image', 4, 'x.png', 0)"
    )

    await download_service.attribute(asset_id="a1", site="TikTok", username="creator")

    rows = await temp_db.fetch_all(
        "SELECT aa.source AS source FROM asset_usernames aa"
        " JOIN usernames a ON a.id = aa.username_id WHERE aa.asset_id = 'a1' AND a.name = 'creator'"
    )
    assert [row["source"] for row in rows] == ["download"]


async def test_a_site_a_download_makes_is_made_knowing_where_it_lives(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The download is the creator that holds the site's host, so the Site it invents carries its
    address from the first moment, drawn by its own logo by address, not only by a name that
    happens to match. Both paths: a site with no username, and a site with one. Only the host,
    never the fetched address with its path and signature."""
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at) "
        "VALUES ('a1', 'digest-a1', 'image', 4, 'x.png', 0)"
    )

    await download_service.attribute(
        asset_id="a1",
        site="Quillhouse",
        username=None,
        address="https://www.quillhouse.example/v/1.mp4?sig=abc",
    )
    await download_service.attribute(
        asset_id="a1",
        site="TikTok",
        username="creator",
        address="https://m.tiktok.com/@creator/video/1",
    )

    # The address is the Site's first link (`sites.SITE_ADDRESS`); the column went in catalog v66.
    rows = await temp_db.fetch_all(
        splice(
            "SELECT s.name AS name, {{SITE_ADDRESS}} AS site_url FROM sites s ORDER BY s.name",
            SITE_ADDRESS=site_address("s"),
        )
    )
    assert [(str(row["name"]), row["site_url"]) for row in rows] == [
        ("Quillhouse", "https://quillhouse.example"),
        ("TikTok", "https://tiktok.com"),
    ]


def test_a_sites_home_is_the_catalogs_own_host_or_the_one_fetched_from() -> None:
    """A site Sift knows is at its front door however deep or odd the host fetched from; any other
    is at the host that was fetched, `www.` taken off. Nothing from the path comes along."""
    assert site_home_of("https://vm.tiktok.com/abc") == "https://tiktok.com"
    assert site_home_of("https://www.quillhouse.example/a?b=c") == "https://quillhouse.example"
    assert site_home_of("https://localhost/x") is None
    assert site_home_of(None) is None


async def test_asking_where_a_download_that_does_not_exist_was_aimed(
    download_service: DownloadService,
) -> None:
    """A retry can read a row that has since been deleted. None rather than a raise, for the reason
    every other read of a missing ledger row gives: the download is gone and there is nothing to
    file, which is not an error condition."""
    assert await download_service.aim_of("01HX0000000000000000000099") is None


async def test_a_download_is_queued_ahead_of_a_library_wide_pass(
    download_service: DownloadService, temp_db: Database, registered_download: None
) -> None:
    """Somebody pasted a link and is watching for it. A face sweep queues two hundred files at a
    time and takes hours, so on arrival order alone a download pasted seconds after a sweep started
    would sit behind every file it had queued.

    A download competes for nothing here anyway: it waits on somebody else's server, which is why
    it is already outside the share-of-machine division.
    """
    download_id = await download_service.submit_url(url=_URL, dest_folder_id=None)

    rows = await temp_db.fetch_all(
        "SELECT priority FROM jobs WHERE type = 'download' AND payload LIKE ?",
        (f"%{download_id}%",),
    )

    assert [row["priority"] for row in rows] == [WAITED_ON_PRIORITY]
    assert WAITED_ON_PRIORITY < DEFAULT_PRIORITY


# --- What a paused row kept, after a restart ----------------------------------------------------


def _paused(download_id: str, job_id: str | None, status: str = "paused") -> DownloadView:
    return DownloadView(
        id=download_id,
        status=status,
        dest_folder_id=None,
        site=None,
        username=None,
        asset_id=None,
        error=None,
        created_at=0,
        job_id=job_id,
    )


async def test_a_paused_row_the_registry_lost_is_measured_on_disk(
    download_service: DownloadService, tmp_path: Path
) -> None:
    """The registry is memory and goes with the process; the workspace does not. What a paused row
    kept is the bytes in its job's directory, nested wherever the tool wrote them."""
    workspaces = Workspaces(tmp_path / "jobs")
    media = workspaces.of("job-a") / "media"
    media.mkdir()
    (media / "clip.mp4.part").write_bytes(b"x" * 4096)
    (media / "clip.info").write_bytes(b"y" * 100)

    kept = await download_service.kept_on_disk([_paused("one", "job-a")], workspaces, measured=())

    assert kept == {"one": 4196}


async def test_only_a_paused_row_with_no_figure_and_something_kept_is_measured(
    download_service: DownloadService, tmp_path: Path
) -> None:
    """A row the registry still measures is not read twice; a running row is the registry's; a
    job with no directory, or an empty one, kept nothing and says nothing, and asking never makes
    the directory it asked about."""
    workspaces = Workspaces(tmp_path / "jobs")
    for job in ("job-live", "job-running"):
        (workspaces.of(job) / "part").write_bytes(b"x" * 10)
    workspaces.of("job-empty")

    kept = await download_service.kept_on_disk(
        [
            _paused("live", "job-live"),
            _paused("running", "job-running", status="running"),
            _paused("empty", "job-empty"),
            _paused("none", "job-none"),
            _paused("pruned", None),
        ],
        workspaces,
        measured={"live"},
    )

    assert kept == {}
    assert not (tmp_path / "jobs" / "job-none").exists()


async def test_no_workspaces_is_no_figure_rather_than_a_fault(
    download_service: DownloadService,
) -> None:
    assert await download_service.kept_on_disk([_paused("one", "job-a")], None, measured=()) == {}


# --- the edges of pause, resume, remove and failure ------------------------------------------------


async def test_a_download_whose_job_was_pruned_still_pauses_and_resumes_on_a_fresh_one(
    download_service: DownloadService,
    temp_db: Database,
    admin_id: str,
    registered_download: None,
) -> None:
    """The row is the record and the job is how it is run: a row with no job left is paused all
    the same, and its Resume queues it a new job rather than answering that nothing can run it."""
    await _insert_download(temp_db, "d1", state="queued")

    assert await download_service.pause("d1", by=admin_id) is True
    assert (await _view(download_service, "d1")).status == "paused"

    assert await download_service.resume("d1", by=admin_id) is True
    job_id = await _named_job(temp_db, "d1")
    assert job_id is not None, "the row names the fresh job it was given"
    job = await temp_db.fetch_one("SELECT state, payload FROM jobs WHERE id = ?", (job_id,))
    assert job is not None and job["state"] == "queued"
    assert json.loads(str(job["payload"])) == {"download_id": "d1"}
    assert [one.verb for one in await _events(temp_db)] == ["paused", "resumed"]


async def test_pausing_a_landed_link_fetched_again_is_filed_against_its_file(
    download_service: DownloadService,
    temp_db: Database,
    admin_id: str,
    registered_download: None,
) -> None:
    """A landed row fetched again keeps the file it landed. Its pause is about that file, which is
    where somebody goes looking for what happened to it, not about the queue row."""
    await _insert_asset(temp_db, "a1")
    await _insert_download(temp_db, "d1", state="done", asset_id="a1")
    assert await download_service.fetch_anyway("d1") is True

    assert await download_service.pause("d1", by=admin_id) is True

    events = await _events(temp_db)
    assert [one.verb for one in events] == ["paused"]
    assert [kind for kind, _ in events[0].about] == ["asset"]


async def test_removing_a_row_that_is_not_there_is_a_quiet_success(
    download_service: DownloadService, temp_db: Database
) -> None:
    """Removing twice, or racing another window, answers as a removal did: nothing to refuse."""
    assert await download_service.hide("no-such-row") is None
    assert await temp_db.fetch_all("SELECT id FROM downloads") == []


async def test_a_failure_for_a_row_that_has_gone_records_no_event(
    download_service: DownloadService, temp_db: Database
) -> None:
    """An event about a queue row that has gone is an event nobody can reach from anywhere."""
    await download_service.mark_failed("no-such-row", error="It failed.")

    assert await _events(temp_db) == []
    assert await temp_db.fetch_all("SELECT id FROM downloads") == []


async def test_the_site_list_leaves_out_an_address_no_site_can_be_named_from(
    download_service: DownloadService, temp_db: Database
) -> None:
    """A host with no name to offer as a choice (a single label, or no host at all) is not a
    Site in the panel; its rows are still in the list under every other narrowing."""
    await _insert_download(temp_db, "d1", state="done", url="https://www.youtube.com/watch?v=abc")
    await _insert_download(temp_db, "d2", state="done", url="https://intranet/clip.mp4")
    await _insert_download(temp_db, "d3", state="done", url="clip.mp4")

    assert [(one.name, one.count) for one in await download_service.site_counts()] == [
        ("YouTube", 1)
    ]
    assert len((await download_service.list_downloads(limit=10)).downloads) == 3


async def test_a_song_named_from_a_page_the_library_has_no_site_for_names_no_site(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The song is written and the act recorded either way; only a Site the library has a row for
    is named as the page it came from. A host no Site can be named from names none either."""
    await _insert_asset(temp_db, "a1")
    await _insert_asset(temp_db, "a2")
    await _insert_asset(temp_db, "a3")
    await temp_db.execute("INSERT INTO sites (id, name) VALUES ('s-yt', 'YouTube')")

    assert await download_service.seed_music(
        "a1", "A Song", url="https://www.youtube.com/watch?v=abc"
    )
    assert await download_service.seed_music("a2", "A Song", url="https://vimeo.com/123")
    assert await download_service.seed_music("a3", "A Song", url="https://intranet/clip.mp4")

    named = await temp_db.fetch_all(
        "SELECT verb, object_kind, object_id FROM workbench_decisions ORDER BY id"
    )
    assert [tuple(row) for row in named] == [
        ("song_named", "site", "s-yt"),
        ("song_named", None, None),
        ("song_named", None, None),
    ]
    songs = await temp_db.fetch_all("SELECT music FROM assets ORDER BY id")
    assert [row["music"] for row in songs] == ["A Song"] * 3


async def test_a_landing_is_the_act_of_whoever_asked_for_it(
    download_service: DownloadService, registered_download: None, temp_db: Database, admin_id: str
) -> None:
    """Who pasted a download is recorded, so a landing is not always Sift's. The row keeps the user
    who asked (download v33) and the landing is theirs; a download nobody pressed for, and a
    failure, stay Sift's."""
    await _insert_asset(temp_db, "a1")
    await _insert_asset(temp_db, "a2")
    asked = await download_service.submit_url(url=_URL, dest_folder_id=None, requested_by=admin_id)
    unasked = await download_service.submit_url(
        url="https://www.tiktok.com/@creator/video/2", dest_folder_id=None
    )

    await download_service.mark_done(asked, asset_id="a1", site="TikTok", username="creator")
    await download_service.mark_done(unasked, asset_id="a2", site="TikTok", username="creator")

    landed = [one.actor for one in await _events(temp_db) if one.verb == "downloaded"]
    assert landed == [("user", admin_id), ("sift", "download")]


async def test_a_deleted_user_s_download_is_sift_s_again(
    download_service: DownloadService, registered_download: None, temp_db: Database, admin_id: str
) -> None:
    """The column's key sets it NULL when the user goes, so the landing names nobody who is gone."""
    await _insert_asset(temp_db, "a1")
    asked = await download_service.submit_url(url=_URL, dest_folder_id=None, requested_by=admin_id)
    await temp_db.execute("PRAGMA foreign_keys = ON")
    await temp_db.execute("DELETE FROM users WHERE id = ?", (admin_id,))

    await download_service.mark_done(asked, asset_id="a1", site="TikTok", username="creator")

    landed = [one.actor for one in await _events(temp_db) if one.verb == "downloaded"]
    assert landed == [("sift", "download")]
