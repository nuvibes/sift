# SPDX-License-Identifier: AGPL-3.0-or-later
"""The people a facial fingerprints file or a folder brought whom no face matches yet, over HTTP:
the list `Settings > Faces` draws, removing one, and the mark on a person they created."""

from __future__ import annotations

import json
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.slices.faces.receipts import FINGERPRINTS_QUEUE, FINGERPRINTS_REMOVED_QUEUE
from sift.slices.faces.tests import test_routes
from sift.slices.faces.tests.test_routes import (
    _INSERT_PERSON,
    db_path,
    sign_in,
    turn_on,
    write,
)

pytestmark = pytest.mark.integration

app = test_routes.app
client = test_routes.client

_PACK = (
    "INSERT INTO face_packs (id, name, version, recognizer, dimension, digest, installed_at)"
    " VALUES (?, ?, '1', 'r', 2, 'd', 0)"
)
_ENTRY = (
    "INSERT INTO pack_entries (id, pack_id, name, claimed_person_id, created_at)"
    " VALUES (?, ?, ?, ?, ?)"
)
_FACE = (
    "INSERT INTO pack_entry_faces (id, entry_id, crop_path, crop_digest, embedding, quality,"
    " recognizer) VALUES (?, ?, NULL, ?, X'0000803F', 0.9, 'r')"
)


def read_rows(path: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        return list(connection.execute(sql, params))


def _held(client: TestClient) -> None:
    write(
        db_path(client),
        [
            (_PACK, ("pk", "Studio Faces")),
            (_PACK, ("sw", "swap one two")),
            (_INSERT_PERSON, ("p1", "Wren Halloway")),
            (_ENTRY, ("e1", "pk", "Neve Arbor", None, 1000)),
            (_ENTRY, ("e2", "pk", "Wren Halloway", "p1", 2000)),
            (_ENTRY, ("e3", "sw", "Cass Ivory", None, 3000)),
            (_FACE, ("f1", "e1", "a")),
            (_FACE, ("f2", "e1", "b")),
        ],
    )


def test_the_waiting_list_holds_who_no_face_matches_with_their_file_and_count(
    client: TestClient,
) -> None:
    """A claimed entry is somebody already; a swap's entry waits on the person's own page."""
    sign_in(client, "admin")
    _held(client)

    answer = client.get("/api/faces/fingerprints/waiting")

    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "items": [
            {
                "entry_id": "e1",
                "name": "Neve Arbor",
                "faces": 2,
                "confirmed": None,
                "source": "Studio Faces",
                "added_at": 1000,
            }
        ]
    }


def test_removing_a_waiting_entry_forgets_it_and_says_so_in_history(client: TestClient) -> None:
    sign_in(client, "admin")
    _held(client)

    assert client.delete("/api/faces/fingerprints/waiting/e1").status_code == 204

    assert client.get("/api/faces/fingerprints/waiting").json() == {"items": []}
    assert read_rows(db_path(client), "SELECT id FROM pack_entry_faces") == []
    said = read_rows(
        db_path(client),
        "SELECT title FROM workbench_decisions WHERE queue = ?",
        (FINGERPRINTS_REMOVED_QUEUE,),
    )
    assert said == [("Removed the facial fingerprints of Neve Arbor from Studio Faces",)]
    # Gone, or somebody's already: nothing to remove and nothing written.
    assert client.delete("/api/faces/fingerprints/waiting/e1").status_code == 404
    assert client.delete("/api/faces/fingerprints/waiting/e2").status_code == 404


def test_a_guest_is_not_shown_the_waiting_list(client: TestClient) -> None:
    sign_in(client, "guest")
    assert client.get("/api/faces/fingerprints/waiting").status_code == 403


def test_the_known_list_marks_who_facial_fingerprints_created_until_it_is_undone(
    client: TestClient,
) -> None:
    turn_on(client)
    sign_in(client, "admin")
    made = json.dumps({"act": "made", "person_id": "p1", "from": "Studio Faces"})
    write(
        db_path(client),
        [
            (_INSERT_PERSON, ("p1", "Wren Halloway")),
            (_INSERT_PERSON, ("p2", "Neve Arbor")),
            *(
                (
                    "INSERT INTO face_references (id, person_id, crop_path, crop_digest,"
                    " embedding, quality, origin, recognizer, created_at)"
                    " VALUES (?, ?, NULL, ?, X'0000803F', 0.9, 'pack', 'r', 0)",
                    (f"r-{person}", person, person),
                )
                for person in ("p1", "p2")
            ),
            (
                "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
                " decided_at) VALUES ('d1', ?, NULL, 't', '', ?, 1)",
                (FINGERPRINTS_QUEUE, made),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
                " VALUES ('d1', 'person', 'p1')",
                (),
            ),
        ],
    )

    def marks() -> dict[str, object]:
        listed = client.get("/api/faces/known").json()["items"]
        return {one["name"]: one["from_fingerprints"] for one in listed}

    assert marks() == {"Neve Arbor": None, "Wren Halloway": "Studio Faces"}
    write(db_path(client), [("UPDATE workbench_decisions SET reversed_at = 2", ())])
    assert marks() == {"Neve Arbor": None, "Wren Halloway": None}
