# SPDX-License-Identifier: AGPL-3.0-or-later
"""The whole-install record over HTTP: who may read it, what a line carries, and what it does not.

Run against a real application rather than against the kernel read next door, because what is
asserted here is the ROUTE's business: who is refused, what the wire shape says, that a name comes
with the way to the thing it names, and that a name whose thing is gone comes with nothing. The
read's own rules (the vault, the order, the filtering) are the kernel's and are proved there.

The events are seeded as rows rather than written through `record_event`, deliberately: this file
is about what a page of the record LOOKS like from outside, and going through the door would make
every case here fail when the door's rules change for reasons that have nothing to do with the feed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.settings_registry import registered_settings
from sift.main import create_app
from sift.testing.auth import establish_session
from sift.testing.library import seed_folder, seed_person, seed_root, seed_site, seed_username

pytestmark = pytest.mark.integration

PASSWORD = "A-Ledger-Test-Passw0rd!"

#: A person nobody ever seeded, so an event naming them is an event whose subject is gone.
NEVER_THERE = "01HX00000000000000000GONE1"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as running:
        yield running


#: The boot's own lines. The scheduler places the two clean-ups' first runs at the same time
#: (`sift.wiring.tasks.build_tasks`), and each of those tasks records its run, so a feed these tests
#: read can carry a "Sift ran Delete old search history" line the test did not seed (the update
#: check the start queues writes none: its declaration says why): newest, since the seeded events are dated, and present or
#: not by how fast the worker got to it. They are
#: taken out here rather than switched off, because the switch is a setting in the database the
#: boot creates, and because the lines are the app's real behaviour: what these tests are about is
#: the events they seed, and a page that has one more line than they wrote is still that page.
#: A SET, because more than one clean-up records its run, and a one-task filter would let the
#: other through as a line nobody seeded.
THE_BOOT_S_OWN_RUNS = frozenset(
    ("run", task) for task in ("quarantine-prune", "search-records-prune")
)


def ledger(client: TestClient, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """The feed as the route answers it, less the boot's own run lines (see above)."""
    body: dict[str, Any] = client.get("/api/ledger", params=params).json()
    kept = [
        one
        for one in body["items"]
        if not any((piece["kind"], piece["id"]) in THE_BOOT_S_OWN_RUNS for piece in one["subjects"])
    ]
    return {**body, "items": kept, "total": body["total"] - (len(body["items"]) - len(kept))}


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def write(path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    """Seed through a connection of the test's own.

    The client drives the application on its own event loop, and a write issued from this loop
    would meet a lock held on that one.
    """

    async def run() -> None:
        database = Database(path, readers=1)
        await database.connect()
        try:
            for sql, params in statements:
                await database.execute(sql, params)
        finally:
            await database.close()

    asyncio.run(run())


_EVENT = (
    "INSERT INTO workbench_decisions"
    " (id, queue, user_id, title, detail, payload, decided_at, reversed_at,"
    " verb, actor_kind, actor_id, object_kind, object_id, object_name, touched)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_SUBJECT = (
    "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
    " VALUES (?, ?, ?, ?)"
)


def seed_event(
    client: TestClient,
    *,
    verb: str = "added",
    at: int = 1_760_000_000,
    queue: str = "ledger",
    actor_kind: str = "sift",
    actor_id: str | None = None,
    user_id: str | None = None,
    subjects: tuple[tuple[str, str, str | None], ...] = (),
    object_kind: str | None = None,
    object_id: str | None = None,
    object_name: str | None = None,
    count: int | None = None,
    title: str = "",
    reversed_at: int | None = None,
    payload: str = "{}",
) -> str:
    """One event in the record, exactly as the door would have written it."""
    event_id = new_id()
    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            _EVENT,
            (
                event_id,
                queue,
                user_id,
                title,
                "",
                payload,
                at,
                reversed_at,
                verb,
                actor_kind,
                actor_id,
                object_kind,
                object_id,
                object_name,
                count,
            ),
        )
    ]
    statements += [
        (_SUBJECT, (event_id, kind, subject_id, name)) for kind, subject_id, name in subjects
    ]
    write(db_path(client), statements)
    return event_id


def sign_in(client: TestClient, role: str) -> str:
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"ledger-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


# --- who may read it --------------------------------------------------------------------------


def test_a_guest_is_refused_the_whole_install_record(client: TestClient) -> None:
    """Refused outright rather than filtered. This is every user's acts, every pass and every
    file in one list, and no filter of it would be a guest's own record: what a guest may be
    told about their own files is a file's history, at its own address."""
    sign_in(client, "guest")

    assert client.get("/api/ledger").status_code == 403


def test_nobody_at_all_is_turned_away_before_the_read(client: TestClient) -> None:
    assert client.get("/api/ledger").status_code == 401


# --- what a line carries ----------------------------------------------------------------------


def test_an_event_carries_the_verb_the_names_and_the_way_to_them(client: TestClient) -> None:
    seed_person(db_path(client), "01HX000000000000000PERSON1", "Ilva Brennan")
    seed_event(
        client,
        verb="removed",
        subjects=(("asset", "01HX0000000000000000FILE01", "beach-day.mp4"),),
        object_kind="person",
        object_id="01HX000000000000000PERSON1",
        object_name="Ilva Brennan",
    )
    sign_in(client, "admin")

    body: dict[str, Any] = ledger(client)

    assert body["total"] == 1
    assert body["offset"] == 0
    one = body["items"][0]
    assert one["verb"] == "removed"
    assert one["object"] == {
        "kind": "person",
        "id": "01HX000000000000000PERSON1",
        "name": "Ilva Brennan",
        "href": "/people/01HX000000000000000PERSON1",
        # Named on the row, so nothing was looked up and there is nothing to say about it having
        # gone: that word is for a subject with no snapshot at all. See `_thing`.
        "gone": False,
    }
    # The NAME at the time, off the row. A file that has since been deleted still says what it was
    # called, which is the whole reason the snapshot is stored.
    assert one["subjects"][0]["name"] == "beach-day.mp4"


def test_a_subject_that_is_gone_has_a_name_and_no_address(client: TestClient) -> None:
    """The line still says who it was. It does not offer a link that lands on "no such person",
    which reads as a broken screen rather than as a library that has moved on."""
    seed_event(
        client,
        verb="deleted",
        subjects=(("person", NEVER_THERE, "Ilva Brennan"),),
    )
    sign_in(client, "admin")

    one = ledger(client)["items"][0]

    assert one["subjects"][0]["name"] == "Ilva Brennan"
    assert one["subjects"][0]["href"] is None


def test_a_nameless_subject_is_named_by_what_it_is_called_now(client: TestClient) -> None:
    """A row from before names were snapshotted carries none, and drawn bare it would say "Linked
    a file to Ilva Brennan": a category where a name belongs. A nameless subject that is still in
    the library is named live, which is the one thing the row cannot answer for itself."""
    seed_person(db_path(client), "01HX000000000000000PERSON2", "Ilva Brennan")
    seed_event(
        client,
        verb="linked",
        subjects=(("person", "01HX000000000000000PERSON2", None),),
    )
    sign_in(client, "admin")

    one = ledger(client)["items"][0]

    assert one["subjects"][0]["name"] == "Ilva Brennan"
    assert one["subjects"][0]["href"] == "/people/01HX000000000000000PERSON2"
    assert one["subjects"][0]["gone"] is False


def test_a_nameless_subject_that_is_gone_says_so_rather_than_naming_its_kind(
    client: TestClient,
) -> None:
    """The other silence, and the other sentence. Nothing was written down, Sift can look this kind
    up, and it is not there, so the line says it has gone rather than "a file", which reads as
    though one were still on a shelf somewhere."""
    seed_event(client, verb="deleted", subjects=(("asset", NEVER_THERE, None),))
    sign_in(client, "admin")

    one = ledger(client)["items"][0]

    assert one["subjects"][0]["name"] is None
    assert one["subjects"][0]["gone"] is True


def test_a_delete_that_named_no_file_still_names_what_it_did_name(client: TestClient) -> None:
    """A Photo Set that Sift dissolves writes a delete whose only subject is the grouping. The feed
    prefers the file a delete ended (naming the people and tags it was on would say they were
    deleted too), but that is a preference and not a filter: otherwise these read "Deleted
    something", which is the record refusing to say what it had just thrown away."""
    seed_event(
        client,
        verb="deleted",
        subjects=(("photo_set", NEVER_THERE, "Harbour lights"),),
    )
    sign_in(client, "admin")

    one = ledger(client)["items"][0]

    assert [thing["name"] for thing in one["subjects"]] == ["Harbour lights"]


def test_a_setting_opens_its_own_row_and_a_key_with_no_row_gets_no_link(client: TestClient) -> None:
    """A registered setting has a place (its row in Settings); a key the registry no longer knows
    has none, and is plain words rather than a link to a pane that would not ring anything."""
    seed_event(
        client,
        verb="edited",
        subjects=(
            ("setting", "logs.detail", "Detail"),
            ("setting", "gone.forever", "gone.forever"),
        ),
    )
    sign_in(client, "admin")

    one = ledger(client)["items"][0]

    # A set: the line orders its things its own way, and the order is not the subject here.
    assert {thing["href"] for thing in one["subjects"]} == {"/settings/logs#logs.detail", None}


def test_a_setting_is_named_by_what_it_is_called_now(client: TestClient) -> None:
    """The one kind read live rather than off the row: a setting cannot be renamed or deleted, its
    label is copy, and older rows snapshotted the key. A key the registry does not know is drawn as
    its snapshot: the key itself on those old rows, and the writer's words on a setting that was
    never a registered one (a Site's name template)."""
    key, setting = next(iter(registered_settings().items()))
    seed_event(
        client,
        verb="edited",
        subjects=(
            ("setting", key, key),
            ("setting", "gone.forever", "gone.forever"),
            ("setting", "site_options.instagram.naming", "Instagram's name template"),
        ),
    )
    sign_in(client, "admin")

    one = ledger(client)["items"][0]

    assert sorted(thing["name"] for thing in one["subjects"]) == sorted(
        [setting.label, "gone.forever", "Instagram's name template"]
    )


def test_a_removed_setting_is_named_as_it_was_last_called_and_its_values_are_not_read(
    client: TestClient,
) -> None:
    """A key taken out with nothing in its place (`settings_registry.Removed`) is named by the
    label it last had and said to be gone, never by a snapshot holding an older word ("You changed
    Offer to measure this machine 2 times and left it at done"). Its values meant something to a
    control that is gone, so none is read back."""
    admin = sign_in(client, "admin")
    seed_event(
        client,
        verb="edited",
        actor_kind="user",
        actor_id=admin,
        user_id=admin,
        subjects=(("setting", "performance.tune_prompt", "Offer to measure this machine"),),
        payload=json.dumps(
            {"key": "performance.tune_prompt", "before": '"ask"', "after": '"done"'}
        ),
    )

    one = ledger(client)["items"][0]

    assert "".join(piece["text"] for piece in one["pieces"]) == (
        "You changed Offer to measure this device (since removed)"
    )


def test_a_pass_that_ran_is_named_by_its_family_and_has_no_address(client: TestClient) -> None:
    """A `ran` event is about the run (`work_runs`), which has no page: the feed names it by the
    family's word the writer snapshotted, and links it nowhere."""
    seed_event(client, verb="ran", subjects=(("run", "01HX00000000000000000RUN01", "Scan"),))
    sign_in(client, "admin")

    one = ledger(client, params={"verb": "ran"})["items"][0]

    assert one["actor"]["kind"] == "sift"
    assert one["subjects"] == [
        {
            "kind": "run",
            "id": "01HX00000000000000000RUN01",
            "name": "Scan",
            "href": None,
            "gone": False,
        }
    ]


def test_only_a_ran_line_about_a_run_on_record_offers_its_report(client: TestClient) -> None:
    """A pass over the library is a run on record and its line opens the run's report. A task's own
    run line (a backup) names the TASK under the same subject kind and has no report, so it
    offers none."""
    write(
        db_path(client),
        [
            (
                "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at)"
                " VALUES (?, 'scan', 1760000000, 1760000000, 1760000060)",
                ("01HX00000000000000000RUN02",),
            )
        ],
    )
    seed_event(client, verb="ran", subjects=(("run", "01HX00000000000000000RUN02", "Scan"),))
    seed_event(client, verb="ran", at=1_760_000_100, subjects=(("run", "backup", "Backup"),))
    sign_in(client, "admin")

    reports = {
        one["subjects"][0]["id"]: one["report"]
        for one in ledger(client, params={"verb": "ran"})["items"]
    }

    assert reports == {"01HX00000000000000000RUN02": "01HX00000000000000000RUN02", "backup": None}


def test_a_folder_is_addressed_by_its_id_as_the_folder_browser_opens_it(
    client: TestClient, tmp_path: Path
) -> None:
    """A folder is the Files wall filtered to it, and `in:` takes its id: the one value that names
    exactly one folder, and what the folder browser writes. Never the PATH: a path is relative to
    its library folder, and two library folders each holding "Shoots/2025" both answer to it."""
    root_id, top_id, folder_id = new_id(), new_id(), new_id()
    seed_root(db_path(client), root_id, folder_id=top_id, path=tmp_path / "ledger-root")
    seed_folder(
        db_path(client), folder_id, root_id=root_id, parent_id=top_id, rel_path="Shoots/2025"
    )
    seed_event(client, verb="scanned", count=1203, subjects=(("folder", folder_id, "2025"),))
    sign_in(client, "admin")

    one = ledger(client)["items"][0]

    assert one["count"] == 1203
    assert one["subjects"][0]["href"] == f"/browse?in={folder_id}"


def test_a_library_folders_own_folder_links_to_itself_and_never_to_an_empty_in(
    client: TestClient, tmp_path: Path
) -> None:
    """Sharing a whole library folder names its own folder, whose path is EMPTY, so a path-built
    link would read `/browse?in=`, which names nothing. By its id it opens that library folder."""
    root_id, top_id = new_id(), new_id()
    seed_root(db_path(client), root_id, folder_id=top_id, path=tmp_path / "shared-root")
    seed_event(client, verb="scanned", count=3, subjects=(("folder", top_id, "Videos"),))
    sign_in(client, "admin")

    href = ledger(client)["items"][0]["subjects"][0]["href"]

    assert href == f"/browse?in={top_id}"
    assert href != "/browse?in="


def test_a_username_opens_its_person_or_the_files_under_it_and_never_a_page_of_its_own(
    client: TestClient,
) -> None:
    """A username has no page. A line naming one goes to whoever it belongs to, and where
    nobody has been said for it, to Browse filtered to its files: the address the Browse route
    reads as `username`."""
    site, alone, claimed, person = new_id(), new_id(), new_id(), new_id()
    seed_site(db_path(client), site, "Seeded Site")
    seed_username(db_path(client), alone, site, "nobodys_handle")
    seed_username(db_path(client), claimed, site, "somebodys_handle")
    seed_person(db_path(client), person, "Ilva Brennan")
    write(
        db_path(client),
        [("UPDATE usernames SET person_id = ? WHERE id = ?", (person, claimed))],
    )
    seed_event(client, verb="added", subjects=(("username", alone, "nobodys_handle"),))
    seed_event(
        client,
        verb="added",
        at=1_760_000_100,
        subjects=(("username", claimed, "somebodys_handle"),),
    )
    sign_in(client, "admin")

    items = ledger(client)["items"]
    hrefs = {one["subjects"][0]["id"]: one["subjects"][0]["href"] for one in items}

    assert hrefs == {alone: f"/browse?username={alone}", claimed: f"/people/{person}"}


def seed_download(client: TestClient, download_id: str, *, hidden: bool) -> None:
    """One row on the downloads queue, removed from the list or not."""
    write(
        db_path(client),
        [
            (
                "INSERT INTO downloads (id, url, url_hash, state, created_at, hidden_at)"
                " VALUES (?, 'https://example.invalid/x', ?, 'failed', 1760000000, ?)",
                (download_id, download_id, 1_760_000_100 if hidden else None),
            )
        ],
    )


def test_a_download_is_addressed_by_the_queue_that_holds_it(client: TestClient) -> None:
    """The second kind with no page of its own, and the only one that is not in the library.

    A download that failed produced no file, so the queue row IS the thing, and the address is
    the Downloads screen with that row picked out, the same shape a folder's is.
    """
    download_id = new_id()
    seed_download(client, download_id, hidden=False)
    seed_event(
        client,
        verb="download_failed",
        subjects=(("download", download_id, "example.invalid/x"),),
    )
    sign_in(client, "admin")

    one = ledger(client)["items"][0]

    assert one["subjects"][0]["href"] == f"/downloads?row={download_id}"


def test_a_removed_download_is_named_and_never_linked(client: TestClient) -> None:
    """Remove from the list keeps the row (it is what stops a re-pasted link being fetched twice),
    and the queue has no way to show it again. A link to it would land on a page that does not
    draw it, which reads as a broken screen; the name is drawn as the plain words it already is."""
    download_id = new_id()
    seed_download(client, download_id, hidden=True)
    seed_event(
        client,
        verb="download_failed",
        subjects=(("download", download_id, "example.invalid/x"),),
    )
    sign_in(client, "admin")

    one = ledger(client)["items"][0]

    assert one["subjects"][0]["name"] == "example.invalid/x"
    assert one["subjects"][0]["href"] is None


def test_who_did_it_is_named_now_rather_than_snapshotted(client: TestClient) -> None:
    """The opposite rule from a subject's, and deliberately: a user renamed last week is the
    same user, and a feed calling them by an old name answers "who did this" with a name nobody
    recognises."""
    admin = sign_in(client, "admin")
    seed_event(
        client,
        verb="added",
        actor_kind="user",
        actor_id=admin,
        user_id=admin,
        subjects=(("asset", "01HX0000000000000000FILE01", "beach-day.mp4"),),
    )

    one = ledger(client)["items"][0]

    assert one["actor"] == {"kind": "user", "id": admin, "name": "ledger-admin"}


def test_sift_needs_no_lookup_and_says_which_pass_on_the_row(client: TestClient) -> None:
    seed_event(client, actor_kind="sift", actor_id="folder", subjects=(("asset", "a1", "one.mp4"),))
    sign_in(client, "admin")

    one = ledger(client)["items"][0]

    assert one["actor"]["kind"] == "sift"
    assert one["actor"]["id"] == "folder"
    assert one["actor"]["name"] is None


# --- a receipt, where there was one -------------------------------------------------------------


def test_only_a_judgement_carries_a_receipt(client: TestClient) -> None:
    """Its presence is what says a row can be taken back. Everything else in the record is an act
    that has happened and has no card on the Organize board."""
    seed_event(client, queue="ledger", subjects=(("asset", "a1", "one.mp4"),))
    seed_event(
        client,
        queue="folders",
        verb="decided",
        title="Reya Solberg - 47 files",
        subjects=(("asset", "a2", "two.mp4"),),
    )
    sign_in(client, "admin")

    body: dict[str, Any] = ledger(client)
    receipts = {one["subjects"][0]["id"]: one["receipt"] for one in body["items"]}

    assert receipts["a1"] is None
    assert receipts["a2"]["queue"] == "folders"
    assert receipts["a2"]["title"] == "Reya Solberg - 47 files"


def test_a_decision_says_the_words_its_card_says_and_a_fold_keeps_its_count(
    client: TestClient,
) -> None:
    """A decision in the feed is worded the way its decision card words it: doer first, its thing
    by the name it has now, rather than by its stored title, which has no doer ("Created a Photo
    Set for Cassia Lynn") and may say names that have changed since. A fold of them keeps its count
    after the line; a receipt its area cannot word keeps its title."""
    admin = sign_in(client, "admin")
    shoot = json.dumps(
        {"kind": "shoot", "photo_set_id": NEVER_THERE, "name": "Cassia Lynn", "assets": ["a", "b"]}
    )
    for at in (1_760_000_000, 1_760_000_001):
        seed_event(
            client,
            queue="shoots",
            verb="decided",
            at=at,
            actor_kind="user",
            actor_id=admin,
            user_id=admin,
            title="Created a Photo Set for Cassia Lynn",
            payload=shoot,
            subjects=(("photo_set", NEVER_THERE, "Cassia Lynn"),),
        )
    seed_event(
        client,
        queue="folders",
        verb="decided",
        at=1_750_000_000,
        actor_kind="user",
        actor_id=admin,
        user_id=admin,
        title="Reya Solberg - 47 files",
        subjects=(("asset", "a2", "two.mp4"),),
    )

    said = {
        one["receipt"]["queue"]: "".join(piece["text"] for piece in one["pieces"])
        for one in ledger(client)["items"]
    }

    assert said == {
        "shoots": "You created the Photo Set Cassia Lynn (since deleted) from 2 photos, 2 times",
        "folders": "Reya Solberg - 47 files",
    }


def test_a_decision_recorded_under_another_verb_says_its_cards_words_and_its_fold_its_own(
    client: TestClient,
) -> None:
    """A filing is recorded as `filed` and a face match as `linked`, each a receipt with a decision
    card. One of them alone says the card's words, never the feed's own line beside a
    card that says it another way; a task's FOLD of them spans different things, so it keeps the
    feed's own line rather than one receipt's words with a count."""
    sign_in(client, "admin")
    filed: dict[str, Any] = {
        "queue": "filenames",
        "verb": "filed",
        "actor_kind": "sift",
        "actor_id": "filename",
        "object_kind": "username",
    }
    alone = seed_event(
        client,
        **filed,
        at=1_700_000_000,
        object_id="u-ada",
        object_name="ada_lumen",
        title="Filed under ada_lumen on Instagram from the file's name",
        payload=json.dumps({"kind": "filed", "username_id": "u-ada", "assets": ["a1"]}),
        subjects=(("asset", "a1", "one.jpg"),),
    )
    for at, username, asset in ((1_760_000_000, "u-reya", "a2"), (1_760_000_001, "u-cass", "a3")):
        seed_event(
            client,
            **filed,
            at=at,
            object_id=username,
            object_name=username,
            title=f"Filed under {username} on Instagram from the file's name",
            payload=json.dumps({"kind": "filed", "username_id": username, "assets": [asset]}),
            subjects=(("asset", asset, f"{asset}.jpg"),),
        )

    feed = {
        one["id"]: (one["folded"], "".join(piece["text"] for piece in one["pieces"]))
        for one in ledger(client)["items"]
    }
    assert feed[alone][0] == 1
    assert feed[alone][1].endswith(" on Instagram from its file name")
    ((folded, line),) = [said for key, said in feed.items() if key != alone]
    assert folded == 2
    assert "2 times" not in line


def test_a_yes_on_a_folder_says_what_else_it_wrote_under_its_line(
    client: TestClient, tmp_path: Path
) -> None:
    """The line names the person and the folder; what else the press wrote (a person added, the
    other folders of that name answered) is the line under it, which the feed carries."""
    admin = sign_in(client, "admin")
    person_id, root_id, top_id, folder_id = new_id(), new_id(), new_id(), new_id()
    seed_person(db_path(client), person_id, "Reya Solberg")
    seed_root(db_path(client), root_id, folder_id=top_id, path=tmp_path / "yes-root")
    seed_folder(
        db_path(client), folder_id, root_id=root_id, parent_id=top_id, rel_path="Reya Solberg"
    )
    written = {
        "attributed": ["a1", "a2"],
        "remembered": [[folder_id, person_id]],
        "created_people": [person_id],
        "namesakes": [["f-other"]],
    }
    seed_event(
        client,
        queue="folders",
        verb="decided",
        actor_kind="user",
        actor_id=admin,
        user_id=admin,
        object_kind="person",
        object_id=person_id,
        object_name="Reya Solberg",
        title="Reya Solberg - 2 files",
        payload=json.dumps({"kind": "confirmed", "written": written}),
        subjects=(("asset", "a1", "one.mp4"), ("asset", "a2", "two.mp4")),
    )

    (one,) = ledger(client)["items"]

    assert "".join(piece["text"] for piece in one["pieces"]) == (
        "You filed 2 files under Reya Solberg from the folder Reya Solberg"
    )
    assert one["more"] == "A new person added, 1 other folder with that name answered."


def test_a_thing_gone_since_is_said_so_except_by_the_act_that_ended_it(client: TestClient) -> None:
    """A cover chosen for a Photo Set deleted since must not say "... as the cover of the Photo
    Set Cassia Lynn" as if it were there, beside worded lines saying "(since deleted)" about the
    same set. One rule for both; the delete itself names it bare."""
    seed_event(
        client,
        verb="edited",
        at=1_760_000_000,
        object_kind="asset",
        object_id="a1",
        object_name="beach.jpg",
        payload="",
        subjects=(("photo_set", NEVER_THERE, "Cassia Lynn"),),
    )
    seed_event(
        client,
        verb="deleted",
        at=1_750_000_000,
        subjects=(("photo_set", NEVER_THERE, "Cassia Lynn"),),
    )
    sign_in(client, "admin")

    cover, deleted = ("".join(p["text"] for p in one["pieces"]) for one in ledger(client)["items"])

    assert cover.endswith(" as the cover of the Photo Set Cassia Lynn (since deleted)")
    assert "(since" not in deleted


# --- paging and filtering -----------------------------------------------------------------------


def test_the_record_is_paged_and_says_how_much_there_is(client: TestClient) -> None:
    """Three presses, spaced wider than the fold's gap so none of them folds into another, and
    filtered to their verb: the boot's own run lines land on the first page, where `ledger` can
    take them out, but they would still be counted in the total of a later one."""
    for each in range(3):
        seed_event(
            client, at=1_760_000_000 + each * 120, subjects=(("asset", f"a{each}", "one.mp4"),)
        )
    sign_in(client, "admin")

    body: dict[str, Any] = ledger(client, params={"limit": 2, "offset": 2, "verb": "added"})

    assert body["total"] == 3
    assert body["offset"] == 2
    assert [one["subjects"][0]["id"] for one in body["items"]] == ["a0"]


def test_it_narrows_to_one_act(client: TestClient) -> None:
    seed_event(client, verb="added", subjects=(("asset", "a1", "one.mp4"),))
    seed_event(client, verb="deleted", subjects=(("asset", "a2", "two.mp4"),))
    sign_in(client, "admin")

    body: dict[str, Any] = ledger(client, params={"verb": "deleted"})

    assert body["total"] == 1
    assert [one["verb"] for one in body["items"]] == ["deleted"]


def test_it_narrows_to_one_kind_of_thing(client: TestClient) -> None:
    seed_event(client, subjects=(("asset", "a1", "one.mp4"),))
    seed_event(client, subjects=(("person", NEVER_THERE, "Ilva Brennan"),))
    sign_in(client, "admin")

    body: dict[str, Any] = ledger(client, params={"kind": "person"})

    assert body["total"] == 1
    assert body["items"][0]["subjects"][0]["kind"] == "person"


def test_it_narrows_to_the_decisions_a_queue_can_take_back(client: TestClient) -> None:
    """Organize's answers and Sift's own filings, each with its receipt, and nothing written
    straight to the record: the whole of what was decided, as one narrowing of the one feed."""
    seed_event(client, queue="ledger", subjects=(("asset", "a1", "one.mp4"),))
    seed_event(
        client,
        queue="folders",
        verb="decided",
        title="Reya Solberg - 47 files",
        subjects=(("asset", "a2", "two.mp4"),),
    )
    seed_event(
        client,
        queue="filenames",
        verb="filed",
        at=1_760_000_500,
        subjects=(("asset", "a3", "three.mp4"),),
    )
    sign_in(client, "admin")

    body: dict[str, Any] = ledger(client, params={"decisions": "true"})

    assert body["total"] == 2
    assert {one["subjects"][0]["id"] for one in body["items"]} == {"a2", "a3"}
    assert all(one["receipt"] is not None for one in body["items"])
    # Unnarrowed, the act with no receipt is there too: the narrowing is what took it out.
    assert ledger(client)["total"] == 3


def test_a_word_neither_vocabulary_knows_matches_nothing_rather_than_being_refused(
    client: TestClient,
) -> None:
    """The two lists live in the kernel, and a copy of them here would be a second opinion about
    what a verb is. An unknown word is an empty answer, which is what it honestly means."""
    seed_event(client, subjects=(("asset", "a1", "one.mp4"),))
    sign_in(client, "admin")

    answer = client.get("/api/ledger", params={"verb": "invented-later"})

    assert answer.status_code == 200
    assert answer.json()["total"] == 0


def test_the_total_counts_the_same_set_the_page_is_cut_from(client: TestClient) -> None:
    """A total counting a different set is worse than none: it is a Show more offered when there is
    nothing more, or withheld when there is."""
    for each in range(3):
        seed_event(
            # Two minutes apart: within a minute the feed folds them into ONE line.
            client,
            verb="added",
            at=1_760_000_000 + each * 120,
            subjects=(("asset", f"a{each}", "x"),),
        )
    seed_event(client, verb="deleted", subjects=(("asset", "a9", "x"),))
    sign_in(client, "admin")

    body: dict[str, Any] = ledger(client, params={"verb": "added", "limit": 1})

    assert body["total"] == 3
    assert len(body["items"]) == 1


def test_a_stash_box_press_names_the_values_it_filled_in(client: TestClient) -> None:
    """The feed says WHAT a box filled in, value by value, as the
    person's own History does, read from the box's kept answer and the record now."""
    person = "01HX000000000000000PERSON7"
    box = "01HX0000000000000000BOX007"
    seed_person(db_path(client), person, "Wren Halloway")
    kept = json.dumps([{"fields": {"gender": "FEMALE", "height_cm": 170}}])
    write(
        db_path(client),
        [
            (
                "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)",
                (box, "FansDB", "https://example.invalid/graphql"),
            ),
            (
                "INSERT INTO person_stash_box_links"
                " (person_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, 'r1', ?, 0)",
                (person, box, kept),
            ),
            ("UPDATE people SET gender = 'FEMALE', height_cm = 165 WHERE id = ?", (person,)),
        ],
    )
    seed_event(
        client,
        verb="enriched",
        actor_kind="box",
        actor_id=box,
        subjects=(("person", person, "Wren Halloway"),),
        object_kind="box",
        object_id=box,
        object_name="FansDB",
        payload=json.dumps({"gender": 1, "height_cm": 1}),
    )
    sign_in(client, "admin")

    (one,) = ledger(client)["items"]

    assert "".join(piece["text"] for piece in one["pieces"]) == (
        "FansDB filled in Wren Halloway's gender (Female) and height (170 cm, since changed)"
    )


def test_a_press_that_links_and_fills_in_is_said_by_its_fill(client: TestClient) -> None:
    """Linking and filling in at one press write a bare link and the fill in the same second. Read
    as two acts the fold would say "filled in 1 person from FansDB", naming nothing filled; the
    press is said by the act that recorded what it filled in."""
    person = "01HX000000000000000PERSON8"
    box = "01HX0000000000000000BOX008"
    seed_person(db_path(client), person, "Wren Halloway")
    kept = json.dumps([{"fields": {"gender": "FEMALE"}}])
    write(
        db_path(client),
        [
            (
                "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)",
                (box, "FansDB", "https://example.invalid/graphql"),
            ),
            (
                "INSERT INTO person_stash_box_links"
                " (person_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, 'r1', ?, 0)",
                (person, box, kept),
            ),
            ("UPDATE people SET gender = 'FEMALE' WHERE id = ?", (person,)),
        ],
    )
    for payload in (json.dumps({"gender": 1}), ""):
        seed_event(
            client,
            verb="enriched",
            actor_kind="box",
            actor_id=box,
            subjects=(("person", person, "Wren Halloway"),),
            object_kind="box",
            object_id=box,
            object_name="FansDB",
            payload=payload,
        )
    sign_in(client, "admin")

    (one,) = ledger(client)["items"]

    assert one["folded"] == 2
    assert "".join(piece["text"] for piece in one["pieces"]) == (
        "FansDB filled in Wren Halloway's gender (Female)"
    )


def test_a_press_of_two_acts_on_two_people_stays_one_folded_line(client: TestClient) -> None:
    """Two acts in one press are one act only where they are about one thing: a box filling in two
    people is two acts, and the line counts both rather than naming one of them."""
    box = "01HX0000000000000000BOX010"
    people = (
        ("01HX00000000000000PERSON10", "Wren Halloway"),
        ("01HX00000000000000PERSON11", "Marla Quist"),
    )
    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)",
            (box, "FansDB", "https://example.invalid/graphql"),
        )
    ]
    for person, name in people:
        seed_person(db_path(client), person, name)
    write(db_path(client), statements)
    for person, name in people:
        seed_event(
            client,
            verb="enriched",
            actor_kind="box",
            actor_id=box,
            subjects=(("person", person, name),),
            object_kind="box",
            object_id=box,
            object_name="FansDB",
            payload="",
        )
    sign_in(client, "admin")

    (one,) = ledger(client)["items"]

    assert one["folded"] == 2
    assert "".join(piece["text"] for piece in one["pieces"]) == "FansDB filled in 2 people"


@pytest.mark.parametrize(
    ("payloads", "line"),
    [
        (
            (json.dumps({"gender": 1}), json.dumps({"gender": 1})),
            "FansDB filled in Wren Halloway's gender (Female)",
        ),
        (("", ""), "FansDB recognized Wren Halloway"),
    ],
    ids=["both-filled", "neither-filled"],
)
def test_a_press_of_two_acts_on_one_thing_names_it(
    client: TestClient, payloads: tuple[str, str], line: str
) -> None:
    """A press whose two acts both recorded what they filled (an answer applied twice over), or
    neither did, must not fold to "filled in 1 person from FansDB" and name nothing. It is one
    act, said by the newer."""
    person = "01HX000000000000000PERSON9"
    box = "01HX0000000000000000BOX009"
    seed_person(db_path(client), person, "Wren Halloway")
    kept = json.dumps([{"fields": {"gender": "FEMALE"}}])
    write(
        db_path(client),
        [
            (
                "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)",
                (box, "FansDB", "https://example.invalid/graphql"),
            ),
            (
                "INSERT INTO person_stash_box_links"
                " (person_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, 'r1', ?, 0)",
                (person, box, kept),
            ),
            ("UPDATE people SET gender = 'FEMALE' WHERE id = ?", (person,)),
        ],
    )
    for payload in payloads:
        seed_event(
            client,
            verb="enriched",
            actor_kind="box",
            actor_id=box,
            subjects=(("person", person, "Wren Halloway"),),
            object_kind="box",
            object_id=box,
            object_name="FansDB",
            payload=payload,
        )
    sign_in(client, "admin")

    (one,) = ledger(client)["items"]

    assert one["folded"] == 2
    assert "".join(piece["text"] for piece in one["pieces"]) == line


def test_a_filing_taken_off_on_a_stash_box_answer_names_the_box(client: TestClient) -> None:
    """The removal names no box; the take-back it was part of does, so the line says which."""
    seed_event(
        client,
        verb="removed",
        actor_id="stash",
        subjects=(("asset", "a1", "one.mp4"),),
        object_kind="box",
        object_id=new_id(),
        object_name="FansDB",
        payload=json.dumps({"took_back": "picture", "boxes": ["FansDB"]}),
    )
    seed_event(
        client,
        verb="unlinked",
        at=1_760_000_001,
        actor_id="stash",
        subjects=(("asset", "a1", "one.mp4"),),
        object_kind="tag",
        object_id=new_id(),
        object_name="poolside",
        payload="",
    )
    sign_in(client, "admin")

    lines = [
        "".join(piece["text"] for piece in one["pieces"])
        for one in ledger(client)["items"]
        if one["verb"] == "unlinked"
    ]

    assert len(lines) == 1
    assert lines[0].startswith("Sift removed the tag poolside")
    assert lines[0].endswith(" from FansDB's answer")
