# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the stash-box endpoint tests share: a signed-in client, a stand-in adapter, a library."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from fastapi.testclient import TestClient

from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.records import FoundRecord, Subject
from sift.slices.auth.crypto import generate_master_key
from sift.slices.stash_boxes.adapter import Box, StashBoxUnreachable
from sift.testing.auth import establish_session
from sift.testing.library import seed_asset, seed_root

PASSWORD = "A-Stash-Box-Test-Passw0rd!"


#: A library on disk for the routes that resolve an asset id. The two asset ids differ before their
#: LAST character, because `seed_asset` derives the location's id from the asset's.
A_ROOT = "01HX0000000000000000000301"


A_FOLDER = "01HX0000000000000000000302"


AN_ASSET = "01HX0000000000000000000303"


ANOTHER_ASSET = "01HX0000000000000000000403"


AN_ADDRESS = "https://stashdb.example/graphql"


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def sign_in(client: TestClient, role: str) -> str:
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"stashbox-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    # A key is sealed under the master key, which exists in memory only while somebody is signed in
    # with their password. `establish_session` writes a session without going through a password
    # login, so the store is filled here. Otherwise every write refuses with "your saved keys are
    # locked", which is the right answer to a different question.
    client.app.state.master_keys.store(user_id, generate_master_key())  # type: ignore[attr-defined]
    return user_id


class _Adapter:
    def __init__(self) -> None:
        self.records: list[FoundRecord] = []
        self.refuse: str | None = None
        self.asked = 0

    async def search(self, box: Box, term: str) -> list[FoundRecord]:
        self.asked += 1
        if self.refuse:
            raise StashBoxUnreachable(self.refuse)
        return self.records

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        self.asked += 1
        if self.refuse:
            raise StashBoxUnreachable(self.refuse)
        return self.records

    async def _by_id(self, remote_id: str) -> FoundRecord | None:
        """One entry by the id this box files it under, as the three by-id reads answer.

        A link is written from a FRESH read rather than from a search answer, so a stand-in that
        only knew how to search would leave every link route unreachable.
        """
        self.asked += 1
        if self.refuse:
            raise StashBoxUnreachable(self.refuse)
        return next((one for one in self.records if one.remote_id == remote_id), None)

    async def person(self, box: Box, remote_id: str) -> FoundRecord | None:
        return await self._by_id(remote_id)

    async def site(self, box: Box, remote_id: str) -> FoundRecord | None:
        return await self._by_id(remote_id)

    async def tag(self, box: Box, remote_id: str) -> FoundRecord | None:
        return await self._by_id(remote_id)


def stand_in(client: TestClient) -> _Adapter:
    """Replace the adapter on the running application. Nothing here reaches a network."""
    adapter = _Adapter()
    client.app.state.stash_boxes._adapter = adapter  # type: ignore[attr-defined]
    return adapter


def add_a_box(client: TestClient, *, api_key: str | None = "a-key") -> str:
    answer = client.post(
        "/api/stash-boxes",
        json={"name": "StashDB", "endpoint": AN_ADDRESS, "api_key": api_key},
    )
    assert answer.status_code == 201, answer.text
    return str(answer.json()["id"])


# --- the pile, over HTTP ------------------------------------------------------------------------
#
# What the Tagger screen reads and what its one press does. Driven over HTTP rather than against the
# service, because what is being claimed here is a permission surface and a promise about receipts,
# and both live in the router, the dependencies and the access layer together.

_EPOCH = 1_700_000_000


_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, width, height, duration_ms, size_bytes, original_filename, added_at)
VALUES (?, ?, 'video', 1920, 1080, 60000, 14, ?, ?)
"""


_INSERT_LOCATION = """
INSERT INTO asset_locations
    (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def a_library(client: TestClient, tmp_path: Path, *names: str) -> list[str]:
    """One root, one folder and a file per name, with real bytes on disk."""
    from sift.kernel.ids import new_id
    from sift.slices.stash_boxes.tests.test_scan import _files  # noqa: F401

    media = tmp_path / "media" / "clips"
    media.mkdir(parents=True, exist_ok=True)
    root, folder = new_id(), new_id()
    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (root, "library", str(tmp_path / "media"), _EPOCH),
        ),
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (folder, root, None, "clips", "clips"),
        ),
    ]
    ids: list[str] = []
    for name in names:
        asset_id = new_id()
        ids.append(asset_id)
        (media / f"{name}.mp4").write_bytes(f"bytes of {name}".encode())
        statements.append((_INSERT_ASSET, (asset_id, f"digest-{name}", f"{name}.mp4", _EPOCH)))
        statements.append(
            (
                _INSERT_LOCATION,
                (
                    new_id(),
                    asset_id,
                    root,
                    folder,
                    f"clips/{name}.mp4",
                    f"{name}.mp4",
                    _EPOCH,
                    _EPOCH,
                ),
            )
        )
    _write(db_path(client), statements)
    return ids


def _write(path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    """Run writes on a connection of this helper's own, on its own loop."""
    import asyncio

    from sift.kernel.db import Database

    async def run() -> None:
        database = Database(path, readers=1)
        await database.connect()
        try:
            for sql, params in statements:
                await database.execute(sql, params)
        finally:
            await database.close()

    asyncio.run(run())


def _read(path: Path, sql: str, params: tuple[object, ...]) -> list[dict[str, object]]:
    """Read rows the same way `_write` writes them: through the kernel's own database, never the
    driver: the one rule every test in the tree is held to."""
    import asyncio

    from sift.kernel.db import Database

    async def run() -> list[dict[str, object]]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return [dict(row) for row in await database.fetch_all(sql, params)]
        finally:
            await database.close()

    return asyncio.run(run())


def a_match(
    client: TestClient,
    asset_id: str,
    box_id: str,
    fields: dict[str, object],
    *,
    refs: dict[str, dict[str, str]] | None = None,
) -> None:
    """One kept answer about one file, written straight to the table.

    The pass that makes one asks three public services. What is under test here is what the screen
    does with an answer, so the answer is put there rather than fetched.
    """
    from sift.kernel.records import Subject
    from sift.slices.stash_boxes.adapter import as_json

    record = FoundRecord(
        source_id=box_id,
        remote_id="r1",
        subject=Subject.ASSET,
        name="A Clip",
        fields=dict(fields),
        confidence=1.0,
        refs=refs or {},
    )
    _write(
        db_path(client),
        [
            (
                "INSERT INTO asset_stash_box_matches"
                " (asset_id, box_id, remote_id, payload, grade, state, found_at)"
                " VALUES (?, ?, 'r1', ?, 'certain', 'waiting', 0)",
                (asset_id, box_id, as_json([record])),
            )
        ],
    )


def a_creators_box(client: TestClient) -> str:
    """A box whose studios ARE its creators, which is the one property the reading below turns on.

    Decided from the address rather than passed in, so this names the address instead of setting a
    column: what a box files as a studio is a fact about somebody else's service.
    """
    answer = client.post(
        "/api/stash-boxes",
        json={
            "name": "A creators box",
            "endpoint": "https://pmvstash.org/graphql",
            "api_key": "a-key",
        },
    )
    assert answer.status_code == 201, answer.text
    return str(answer.json()["id"])


# --- what has been agreed about a subject -------------------------------------------------------


def a_person(client: TestClient, name: str = "Jane") -> str:
    answer = client.post("/api/people", json={"name": name})
    assert answer.status_code == 201, answer.text
    return str(answer.json()["id"])


# --- where two answers disagree -----------------------------------------------------------------


def a_person_link(client: TestClient, person: str, box: str, fields: dict[str, object]) -> None:
    """A kept record for one person, written straight to the table. The route that makes one
    FETCHES; what is under test here is what is done with what was kept."""
    from sift.slices.stash_boxes.adapter import as_json

    record = FoundRecord(
        source_id=box,
        remote_id="r1",
        subject=Subject.PERSON,
        name="Jane",
        fields=dict(fields),
        confidence=1.0,
    )
    _write(
        db_path(client),
        [
            (
                "INSERT INTO person_stash_box_links"
                " (person_id, box_id, remote_id, payload, fetched_at)"
                " VALUES (?, ?, 'r1', ?, 0)",
                (person, box, as_json([record])),
            )
        ],
    )


def _a_second_box(client: TestClient) -> str:
    answer = client.post(
        "/api/stash-boxes",
        json={"name": "FansDB", "endpoint": "https://fansdb.example/graphql", "api_key": "k"},
    )
    assert answer.status_code == 201, answer.text
    return str(answer.json()["id"])


def _settle(client: TestClient, person: str, box: str, *, take: bool) -> dict[str, object]:
    answer = client.post(
        "/api/stash-boxes/disagreements/settle",
        json={
            "subject": "person",
            "local_id": person,
            "box_id": box,
            "key": "birth_date",
            "take_theirs": take,
        },
    )
    assert answer.status_code == 200, answer.text
    body: dict[str, object] = answer.json()
    return body


def _waiting(client: TestClient, person: str) -> list[tuple[str, object, object]]:
    rows = client.get(f"/api/stash-boxes/disagreements/person/{person}").json()["disagreements"]
    return sorted((one["box_id"], one["mine"], one["theirs"]) for one in rows)


def _decision(client: TestClient, decision_id: str) -> dict[str, str]:
    """A receipt's own words, read straight off the table."""
    (row,) = _read(
        db_path(client),
        "SELECT title, detail FROM workbench_decisions WHERE id = ?",
        (decision_id,),
    )
    return {"title": str(row["title"]), "detail": str(row["detail"])}


# --- keeping a stash-box's picture, and the batch that asks about several subjects ---------------


def write_undecided(client: TestClient, subject: str, local_id: str, *, candidates: int) -> None:
    """A name the unattended enrichment could not choose for, written the way the enricher writes it.

    Straight to the row rather than through a run: what these tests are about is the LIST, and
    arranging a real decline needs two boxes each answering twice.
    """
    _write(
        db_path(client),
        [
            (
                "INSERT INTO stash_box_undecided (subject, local_id, candidates, seen_at)"
                " VALUES (?, ?, ?, 0)",
                (subject, local_id, candidates),
            )
        ],
    )


def _linked_person(client: TestClient, box: str, *, image_url: str | None) -> str:
    """A person this library has, linked to a box, with what that box said kept beside them."""
    person = a_person(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id="box",
            remote_id="r1",
            subject=Subject.PERSON,
            name="Jane",
            image_url=image_url,
        )
    ]
    linked = client.put(f"/api/stash-boxes/links/person/{person}/{box}", json={"remote_id": "r1"})
    assert linked.status_code == 200, linked.text
    return person


def _ledger_line(client: TestClient) -> str:
    """The one line the ledger tab draws for its first row, as the server built it."""
    row = client.get("/api/stash-boxes/linked").json()["items"][0]
    return "".join(piece["lead"] + piece["text"] for piece in row["said"])


# --- asking about named FILES rather than the whole library --------------------------------------


def _matching_on(client: TestClient) -> None:
    """Matching switched on, with a box to ask: a press that would ask nobody is refused."""
    from sift.slices.stash_boxes.settings import SCAN_KEY

    turned_on = client.put("/api/settings", json={"values": {SCAN_KEY: True}})
    assert turned_on.status_code == 204, turned_on.text
    add_a_box(client)


def _a_known_box(client: TestClient, name: str, endpoint: str) -> str:
    """A box at an address Sift knows by a word, so a press can name it."""
    answer = client.post(
        "/api/stash-boxes", json={"name": name, "endpoint": endpoint, "api_key": "k"}
    )
    assert answer.status_code == 201, answer.text
    return str(answer.json()["id"])


def _a_library(client: TestClient, tmp_path: Path, *how_many: str) -> None:
    """A root, a folder and real asset rows, so a route that resolves an id finds one.

    The scan route resolves every id it is handed, so these tests need real files to answer 200.
    """
    root = tmp_path / "library"
    seed_root(db_path(client), A_ROOT, folder_id=A_FOLDER, path=root)
    for index, asset_id in enumerate(how_many):
        seed_asset(
            db_path(client),
            asset_id,
            root_id=A_ROOT,
            folder_id=A_FOLDER,
            root_path=root,
            cache_dir=tmp_path / "cache",
            filename=f"clip{index}.mp4",
        )


def _a_site(client: TestClient, name: str = "Northlight") -> str:
    answer = client.post("/api/sites", json={"name": name, "kind": None})
    assert answer.status_code == 201, answer.text
    return str(answer.json()["id"])


def _a_tag(client: TestClient, name: str = "Beach") -> str:
    answer = client.post("/api/tags", json={"name": name})
    assert answer.status_code == 201, answer.text
    return str(answer.json()["id"])


# --- what a decision says it was about -----------------------------------------------------------


def _subjects_of(client: TestClient, decision_id: str) -> set[tuple[str, str]]:
    """The link rows one decision wrote, read straight from the file the application is using.

    Through the database rather than through a route, because there is no route that answers it:
    what the links are FOR is the history read, and the two tests below assert one of them each way
    round: a file through its own history, a person through the row, because a person's history
    has nothing drawing it yet.
    """
    rows = _read(
        db_path(client),
        "SELECT kind, subject_id FROM workbench_decision_subjects WHERE decision_id = ?",
        (decision_id,),
    )
    return {(str(row["kind"]), str(row["subject_id"])) for row in rows}


# --- "Do not enrich" reaches what is already held, not only what would be sent -------------------
#
# The door refuses every question that would LEAVE. These are the answers Sift already holds (a
# match waiting in the pile, a record kept with a link), which need nothing sent to be written,
# so the door never sees them. Each is refused in the door's own words.


def _keep_local(client: TestClient, subject: str, local_id: str, kept: bool = True) -> None:
    answer = client.put(
        f"/api/stash-boxes/enrichment/{subject}/{local_id}/keep-local",
        json={"kept_local": kept},
    )
    assert answer.status_code == 200, answer.text


def _payloads(client: TestClient, job_type: str) -> list[dict[str, object]]:
    rows = _read(
        db_path(client), "SELECT payload FROM jobs WHERE type = ? ORDER BY id", (job_type,)
    )
    return [json.loads(str(row["payload"])) for row in rows]


# --- a FILE's disagreements, on the file's own page ------------------------------------------------


def _a_file_disagreeing_about_its_title(client: TestClient, tmp_path: Path) -> tuple[str, str]:
    """A file titled by hand, then an answer with a different title agreed to and left alone."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    client.put(f"/api/assets/{asset}", json={"title": "What I Called It"})
    a_match(client, asset, box, {"title": "What They Call It"})
    applied = client.post(
        "/api/stash-boxes/matches/apply",
        json={"matches": [{"asset_id": asset, "box_id": box}]},
    )
    assert applied.status_code == 200, applied.text
    return asset, box


def _file_rows(client: TestClient, asset: str) -> list[dict[str, object]]:
    answer = client.get(f"/api/stash-boxes/disagreements/asset/{asset}")
    assert answer.status_code == 200, answer.text
    return list(answer.json()["disagreements"])


def _history_of(client: TestClient, asset: str) -> list[str]:
    answer = client.get(f"/api/assets/{asset}/history")
    assert answer.status_code == 200, answer.text
    return [str(one["what"]) for one in answer.json()["items"]]
