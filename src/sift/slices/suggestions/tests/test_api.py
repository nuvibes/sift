# SPDX-License-Identifier: AGPL-3.0-or-later
"""The four endpoints, over HTTP, against a real application.

Run this way rather than against the service because what is being asserted lives in the router and
its dependencies: who is refused, and what a caller is told when there is nothing to answer. A
service-level test would exercise the half that was never in question and would pass whether or not
the routes were guarded at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.main import create_app
from sift.testing.auth import establish_session

pytestmark = pytest.mark.integration

_EPOCH = 1_700_000_000
PASSWORD = "A-Suggestion-Test-Passw0rd!"

#: Well-formed and never minted, so a refusal for it cannot be mistaken for a lucky miss.
NEVER_EXISTED = "01HX0000000000000000000099"


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


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def write(path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    """Seed through a connection of the test's own.

    The client drives the application on its own event loop, and a write issued from this loop
    would meet a lock held on that one, which fails for a reason that has nothing to do with what
    is being tested.
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


def sign_in(client: TestClient, role: str) -> str:
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"suggest-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def _folder_named(client: TestClient, name: str) -> str:
    """The id of a seeded folder, read through the kernel's own handle.

    Not `sqlite3` directly, and a rule refuses that here rather than leaving it to review: a
    connection opened by hand misses the pragmas (foreign keys off, so cascades silently stop
    working) and the single-writer lock with them. On its own loop, for the reason `write` above
    gives.
    """
    found: list[str] = []

    async def run() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            row = await database.fetch_one("SELECT id FROM folders WHERE name = ?", (name,))
            assert row is not None
            found.append(str(row["id"]))
        finally:
            await database.close()

    asyncio.run(run())
    return found[0]


def read(path: Path, sql: str, *params: object) -> list[dict[str, object]]:
    """Read through a connection of the test's own, for the same reason `write` opens one."""

    async def run() -> list[dict[str, object]]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            rows = await database.fetch_all(sql, params)
            return [dict(row) for row in rows]
        finally:
            await database.close()

    return asyncio.run(run())


def seed_folder_with_a_claim(client: TestClient, *, who: str = "Nadia Vance") -> tuple[str, str]:
    """A folder holding one file, and a pending claim about it. Returns `(claim_id, asset_id)`.

    `who` names the folder and therefore the person it proposes. It is a parameter because a root's
    path is unique, so a test needing two questions in the queue cannot simply call this twice.
    """
    root_id, top_id, folder_id = new_id(), new_id(), new_id()
    asset_id, claim_id = new_id(), new_id()
    key = who.casefold()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root_id, who, f"/library/{key.replace(' ', '-')}", _EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                "VALUES (?, ?, NULL, '', ?)",
                (top_id, root_id, who),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                "VALUES (?, ?, ?, ?, ?)",
                (folder_id, root_id, top_id, who, who),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, size_bytes, added_at) "
                "VALUES (?, ?, 'image', 1000, ?)",
                (asset_id, f"digest-{asset_id}", _EPOCH),
            ),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                "filename, size_bytes, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1000, ?, ?)",
                (
                    new_id(),
                    asset_id,
                    root_id,
                    folder_id,
                    f"{who}/one.jpg",
                    "one.jpg",
                    _EPOCH,
                    _EPOCH,
                ),
            ),
            (
                "INSERT INTO folder_claims (id, folder_id, kind, name_key, proposed, evidence, "
                "state, created_at) VALUES (?, ?, 'person', ?, ?, 'name_only', 'pending', ?)",
                (claim_id, folder_id, key, key, _EPOCH),
            ),
        ],
    )
    return claim_id, asset_id


class TestWhoIsRefused:
    def test_a_guest_may_not_read_the_list(self, client: TestClient) -> None:
        sign_in(client, "guest")
        assert client.get("/api/suggestions").status_code == 403

    def test_a_guest_may_not_answer_one(self, client: TestClient) -> None:
        sign_in(client, "guest")
        assert (
            client.post(f"/api/suggestions/{NEVER_EXISTED}/confirm", json={"skip": []}).status_code
            == 403
        )
        assert client.post(f"/api/suggestions/{NEVER_EXISTED}/reject").status_code == 403

    def test_nobody_signed_out_reaches_any_of_it(self, client: TestClient) -> None:
        assert client.get("/api/suggestions").status_code == 401


class TestTheList:
    def test_an_empty_library_has_nothing_to_answer(self, client: TestClient) -> None:
        sign_in(client, "admin")
        response = client.get("/api/suggestions")
        assert response.status_code == 200
        assert response.json() == {"proposals": [], "total": 0, "offset": 0}

    def test_a_pending_claim_comes_back_with_its_folder_and_its_count(
        self, client: TestClient
    ) -> None:
        sign_in(client, "admin")
        claim_id, _ = seed_folder_with_a_claim(client)
        body = client.get("/api/suggestions").json()
        assert body["total"] == 1
        (row,) = body["proposals"]
        assert row["id"] == claim_id
        assert row["proposed"] == "nadia vance"
        assert row["folder"] == "Nadia Vance"
        assert row["files"] == 1
        assert row["evidence"] == "name_only"

    def test_the_page_can_be_asked_for_in_parts(self, client: TestClient) -> None:
        sign_in(client, "admin")
        seed_folder_with_a_claim(client)
        body = client.get("/api/suggestions", params={"limit": 1, "offset": 1}).json()
        assert body["proposals"] == []
        # The total still describes what this user may see, not what is left after the offset.
        assert body["total"] == 1

    def test_the_queue_can_be_opened_where_it_was_left(self, client: TestClient) -> None:
        """`?from=` names a question to start at, instead of an offset.

        The queue pages by whole rows, so how many cards fit depends on the size of the screen and
        a page NUMBER means nothing in a link. Two questions, so a position that is not zero exists
        to be asked for.
        """
        sign_in(client, "admin")
        seed_folder_with_a_claim(client, who="Ada Lovelace")
        seed_folder_with_a_claim(client, who="Grace Hopper")

        every = client.get("/api/suggestions", params={"limit": 50}).json()
        ordered = [one["id"] for one in every["proposals"]]
        assert len(ordered) == 2

        anchored = client.get("/api/suggestions", params={"limit": 1, "from": ordered[1]}).json()

        assert [one["id"] for one in anchored["proposals"]] == [ordered[1]]
        assert anchored["offset"] == 1, "the answer says where it landed"
        assert anchored["total"] == every["total"], "anchoring narrows nothing"

    def test_an_anchor_that_has_been_answered_opens_the_top(self, client: TestClient) -> None:
        """The ordinary case on a queue rather than an error: answering a question is exactly what
        takes it off the list, so a link somebody sends is usually stale by the time it is opened.

        And a claim that never existed gets the same answer as one this user may not be told
        about, which is what stops the address being a way to ask which.
        """
        sign_in(client, "admin")
        answered, _asset = seed_folder_with_a_claim(client, who="Ada Lovelace")
        seed_folder_with_a_claim(client, who="Grace Hopper")
        assert client.post(f"/api/suggestions/{answered}/reject").status_code == 200

        top = client.get("/api/suggestions", params={"limit": 5}).json()
        stale = client.get("/api/suggestions", params={"limit": 5, "from": answered}).json()
        never = client.get("/api/suggestions", params={"limit": 5, "from": NEVER_EXISTED}).json()

        assert len(top["proposals"]) == 1, "one question left, and it is not the anchor"
        assert stale == top
        assert never == top


class TestWhatWasFiledWithoutAsking:
    """Its own route rather than a field on the list, because the two are different things.

    The list is outstanding work. This is work already done, and an empty list of questions with
    a record of what was answered silently beside it is the whole point of having both.
    """

    def test_a_library_nothing_was_filed_in_answers_with_an_empty_list(
        self, client: TestClient
    ) -> None:
        sign_in(client, "admin")

        response = client.get("/api/suggestions/filed")

        assert response.status_code == 200
        assert response.json() == {"filed": []}

    def test_what_a_pass_filed_comes_back_with_the_person_the_folder_and_the_count(
        self, client: TestClient
    ) -> None:
        """The state a silent filing leaves behind, seeded the way this file seeds everything else.

        Written as state rather than by driving a pass: a pass needs faces, and what this test is
        about is the trip through HTTP: whether the row the service resolved arrives with its
        three fields intact.
        """
        sign_in(client, "admin")
        _, asset_id = seed_folder_with_a_claim(client)
        person_id, folder_id = new_id(), _folder_named(client, "Nadia Vance")
        write(
            db_path(client),
            [
                (
                    "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
                    (person_id, "Nadia Vance", _EPOCH),
                ),
                (
                    "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, ?)",
                    (folder_id, person_id, _EPOCH),
                ),
                (
                    "INSERT INTO asset_people (asset_id, person_id, source) VALUES (?, ?, 'folder')",
                    (asset_id, person_id),
                ),
            ],
        )

        (row,) = client.get("/api/suggestions/filed").json()["filed"]

        assert row["person"] == "Nadia Vance"
        assert row["folder"] == "Nadia Vance"
        assert row["files"] == 1

    def test_the_count_on_a_folders_row_is_that_folders_and_not_the_persons_whole_library(
        self, client: TestClient
    ) -> None:
        """The row is a folder; a count grouped by person alone would carry the person's
        library-wide total (a thousand on a folder of a hundred). A second folder filed under
        the same person does not add to the first folder's number."""
        sign_in(client, "admin")
        _, here = seed_folder_with_a_claim(client)
        _, elsewhere = seed_folder_with_a_claim(client, who="Other Shoot")
        person_id, folder_id = new_id(), _folder_named(client, "Nadia Vance")
        write(
            db_path(client),
            [
                (
                    "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
                    (person_id, "Nadia Vance", _EPOCH),
                ),
                (
                    "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, ?)",
                    (folder_id, person_id, _EPOCH),
                ),
                (
                    "INSERT INTO asset_people (asset_id, person_id, source) VALUES (?, ?, 'folder')",
                    (here, person_id),
                ),
                (
                    "INSERT INTO asset_people (asset_id, person_id, source) VALUES (?, ?, 'folder')",
                    (elsewhere, person_id),
                ),
            ],
        )
        (row,) = client.get("/api/suggestions/filed").json()["filed"]
        assert row["files"] == 1, "the other folder's file was counted onto this folder's row"

    def test_a_guest_is_refused_it(self, client: TestClient) -> None:
        sign_in(client, "guest")

        assert client.get("/api/suggestions/filed").status_code == 403

    def test_a_row_takes_its_folder_back_and_says_what_it_did(self, client: TestClient) -> None:
        """A folder added before any record of it was written: its row carries the folder, and
        Take back takes the person off the file the folder pass put them on, once."""
        sign_in(client, "admin")
        _, asset_id = seed_folder_with_a_claim(client)
        person_id, folder_id = new_id(), _folder_named(client, "Nadia Vance")
        write(
            db_path(client),
            [
                (
                    "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
                    (person_id, "Nadia Vance", _EPOCH),
                ),
                (
                    "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, ?)",
                    (folder_id, person_id, _EPOCH),
                ),
                (
                    "INSERT INTO asset_people (asset_id, person_id, source) VALUES (?, ?, 'folder')",
                    (asset_id, person_id),
                ),
            ],
        )
        (row,) = client.get("/api/suggestions/filed").json()["filed"]
        address = f"/api/suggestions/filed/{row['folder_id']}/people/{person_id}/undo"

        sign_in(client, "guest")
        assert client.post(address).status_code == 403
        sign_in(client, "admin")
        answer = client.post(address)

        assert answer.status_code == 200, answer.text
        assert answer.json()["files"] == 1
        assert answer.json()["decision_id"]
        assert client.get("/api/suggestions/filed").json() == {"filed": []}
        assert client.post(address).json() == {"files": 0, "decision_id": ""}


class TestAnswering:
    def test_confirming_names_somebody_and_attributes_the_folder(self, client: TestClient) -> None:
        sign_in(client, "admin")
        claim_id, _asset_id = seed_folder_with_a_claim(client)

        answer = client.post(f"/api/suggestions/{claim_id}/confirm", json={"skip": []})
        assert answer.status_code == 200
        applied = answer.json()
        assert applied["created"] is True
        assert applied["files"] == 1
        assert applied["alias"] is True
        # There was no site above the folder, so no username was made and none was linked.
        assert applied["username_linked"] is False

        # And it is gone from the list.
        assert client.get("/api/suggestions").json()["proposals"] == []

    def test_a_file_left_out_is_left_out(self, client: TestClient) -> None:
        sign_in(client, "admin")
        claim_id, asset_id = seed_folder_with_a_claim(client)
        applied = client.post(
            f"/api/suggestions/{claim_id}/confirm", json={"skip": [asset_id]}
        ).json()
        assert applied["files"] == 0

    def test_rejecting_settles_it(self, client: TestClient) -> None:
        sign_in(client, "admin")
        claim_id, _ = seed_folder_with_a_claim(client)
        # Answers with what it did and the record it wrote, rather than "queued": setting a
        # folder aside starts no job, and the record is what the toast offers to take back.
        answered = client.post(f"/api/suggestions/{claim_id}/reject").json()
        assert answered["settled"] is True
        assert answered["decision_id"]
        assert client.get("/api/suggestions").json()["proposals"] == []

    def test_answering_something_that_is_not_there_is_a_404(self, client: TestClient) -> None:
        sign_in(client, "admin")
        assert (
            client.post(f"/api/suggestions/{NEVER_EXISTED}/confirm", json={"skip": []}).status_code
            == 404
        )
        assert client.post(f"/api/suggestions/{NEVER_EXISTED}/reject").status_code == 404

    def test_an_ambiguous_name_is_refused_out_loud(self, client: TestClient) -> None:
        """Two people answer to it, so confirming would have to guess, and it says so instead."""
        sign_in(client, "admin")
        claim_id, _ = seed_folder_with_a_claim(client)
        write(
            db_path(client),
            [
                (
                    "INSERT INTO people (id, name, created_at) VALUES (?, 'nadia vance', ?)",
                    (new_id(), _EPOCH),
                )
                for _ in range(2)
            ],
        )
        answer = client.post(f"/api/suggestions/{claim_id}/confirm", json={"skip": []})
        assert answer.status_code == 400
        assert "more than one person" in answer.json()["detail"]


class TestNamingAFolderByHand:
    """The only way a MISS is ever written down.

    Every other row in the claims table records something the reader produced: confirmed says it
    was right, rejected says it was wrong. A folder the reader misread or walked straight past
    writes no row at all, so without this the record fills with "you proposed X, wrong" and holds
    not one "you missed Y". It cannot be added later: the corrections only exist if something wrote
    them down as they were made.
    """

    def _a_folder(self, client: TestClient) -> str:
        claim_id, _ = seed_folder_with_a_claim(client, who="Nadia Vance")
        found = read(db_path(client), "SELECT folder_id FROM folder_claims WHERE id = ?", claim_id)
        return str(found[0]["folder_id"])

    def test_an_admin_can_name_a_folder_the_reader_did_not(self, client: TestClient) -> None:
        sign_in(client, "admin")
        folder_id = self._a_folder(client)
        answer = client.post(f"/api/suggestions/folder/{folder_id}", json={"name": "Somebody Else"})
        assert answer.status_code == 200
        assert answer.json()["person_id"]
        assert answer.json()["files"] == 1

    def test_a_folder_can_be_named_as_a_SITE_rather_than_as_a_person(
        self, client: TestClient
    ) -> None:
        """Naming a folder as a Site, and it is a different answer rather than a variation on the
        first one: a folder of one site's downloads holds many people, one per file, so treating
        the site's name as a person's would make a person called "RedGIFs" and file everybody's
        clips under them.

        Nothing is looked up for a site. `people_named` asks who a word already names, which is the
        right question for a person and the wrong one for a Site, so the claim is written with
        no person on it and handed to the same confirmation every other claim goes through, which is
        what reads the people out of the filenames instead.
        """
        sign_in(client, "admin")
        folder_id = self._a_folder(client)

        answer = client.post(
            f"/api/suggestions/folder/{folder_id}", json={"name": "Northlight", "kind": "site"}
        )

        assert answer.status_code == 200, answer.text
        assert not answer.json()["person_id"], "a site was filed as a person"
        rows = read(
            db_path(client),
            "SELECT kind, evidence, state, site FROM folder_claims"
            " WHERE folder_id = ? AND name_key = ?",
            folder_id,
            "northlight",
        )
        assert [(row["kind"], row["evidence"], row["state"], row["site"]) for row in rows] == [
            ("site", "by_hand", "confirmed", "Northlight")
        ]

    def test_the_correction_is_recorded_as_one_a_person_made(self, client: TestClient) -> None:
        """The reason matters as much as the row. A learned layer needs to tell the cases the
        reader got right from the cases it never saw, and only this reason says the latter."""
        sign_in(client, "admin")
        folder_id = self._a_folder(client)
        client.post(f"/api/suggestions/folder/{folder_id}", json={"name": "Somebody Else"})
        rows = read(
            db_path(client),
            "SELECT evidence, state FROM folder_claims WHERE folder_id = ? AND name_key = ?",
            folder_id,
            "somebody else",
        )
        assert [(row["evidence"], row["state"]) for row in rows] == [("by_hand", "confirmed")]

    def test_a_name_that_is_no_name_is_refused(self, client: TestClient) -> None:
        """`---` is a hundred and twenty characters short of the limit and is nobody.

        The field only refuses an empty string, so anything made entirely of punctuation arrives
        here looking like a name and is one once it is cleaned up. Written, it would be a person
        with a blank name and a folder filed under them.
        """
        sign_in(client, "admin")
        folder_id = self._a_folder(client)

        assert (
            client.post(f"/api/suggestions/folder/{folder_id}", json={"name": "---"}).status_code
            == 404
        )

    def test_a_name_two_people_answer_to_is_a_question_rather_than_an_answer(
        self, client: TestClient
    ) -> None:
        """Two people can share a spelling deliberately, and which was meant is not something this
        may decide. It is the same refusal a confirmation gives, because it IS one."""
        sign_in(client, "admin")
        folder_id = self._a_folder(client)
        write(
            db_path(client),
            [
                (
                    "INSERT INTO people (id, name, created_at) VALUES (?, 'Jane Doe', ?)",
                    (new_id(), _EPOCH),
                ),
                (
                    "INSERT INTO people (id, name, created_at) VALUES (?, 'jane doe', ?)",
                    (new_id(), _EPOCH),
                ),
            ],
        )

        answer = client.post(f"/api/suggestions/folder/{folder_id}", json={"name": "Jane Doe"})

        assert answer.status_code == 400
        assert "one person" in answer.json()["detail"]

    def test_saying_yes_to_a_name_already_said_no_to_reopens_the_same_row(
        self, client: TestClient
    ) -> None:
        """A correction of a correction. The folder has already made this claim in some state, so
        there is nothing to add: the row is reopened rather than filed beside itself, or the
        record would hold two answers to one question and no way to tell which is current."""
        sign_in(client, "admin")
        claim_id, _ = seed_folder_with_a_claim(client, who="Nadia Vance")
        folder_id = str(
            read(db_path(client), "SELECT folder_id FROM folder_claims WHERE id = ?", claim_id)[0][
                "folder_id"
            ]
        )
        assert client.post(f"/api/suggestions/{claim_id}/reject").status_code == 200

        answer = client.post(f"/api/suggestions/folder/{folder_id}", json={"name": "Nadia Vance"})

        assert answer.status_code == 200
        rows = read(
            db_path(client),
            "SELECT id, state FROM folder_claims WHERE folder_id = ?",
            folder_id,
        )
        assert [(str(row["id"]), row["state"]) for row in rows] == [(claim_id, "confirmed")]
        assert read(db_path(client), "SELECT name_key FROM claim_rejections") == []

    def test_a_guest_may_not_name_one(self, client: TestClient) -> None:
        sign_in(client, "guest")
        folder_id = self._a_folder(client)
        assert (
            client.post(f"/api/suggestions/folder/{folder_id}", json={"name": "X"}).status_code
            == 403
        )

    def test_a_folder_nobody_may_see_is_not_there_to_name(self, client: TestClient) -> None:
        sign_in(client, "admin")
        assert client.post("/api/suggestions/folder/nope", json={"name": "X"}).status_code == 404


def test_the_filenames_report_opens_where_the_page_was_for_a_username_that_is_gone(
    client: TestClient,
) -> None:
    """The report keeps its page in its address. A username whose every filing was taken back is off
    the list, and the answer is the page it was on, never the top."""
    sign_in(client, "admin")
    cases: tuple[tuple[str, dict[str, str | bool]], ...] = (("/api/suggestions/filenames", {}),)
    for address, params in cases:
        answer = client.get(address, params={**params, "from": "gone", "near": 2})
        assert answer.status_code == 200, answer.text
        assert answer.json()["offset"] == 2, f"{address} served the top for a row that is gone"


def test_a_no_on_a_username_with_nothing_filed_under_it_answers_no_files_and_no_record(
    client: TestClient,
) -> None:
    """A second press, or a username that is not there, is an answer rather than an error, and the
    press is an admin's: a guest is refused before anything is read."""
    sign_in(client, "guest")
    address = f"/api/suggestions/filenames/{NEVER_EXISTED}/undo"
    assert client.post(address).status_code == 403

    sign_in(client, "admin")
    answer = client.post(address)

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"files": 0, "decision_id": ""}


def test_the_filenames_report_without_a_place_opens_at_the_offset_asked_for(
    client: TestClient,
) -> None:
    """No `from` in the address: the page is the one the offset names, with nothing to resume."""
    sign_in(client, "admin")

    answer = client.get("/api/suggestions/filenames", params={"offset": 3, "limit": 5})

    assert answer.status_code == 200, answer.text
    assert answer.json()["offset"] == 3
    assert answer.json()["groups"] == []
