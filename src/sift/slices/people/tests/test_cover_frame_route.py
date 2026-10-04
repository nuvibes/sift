# SPDX-License-Identifier: AGPL-3.0-or-later
"""A cover can be REFRAMED: the window of its picture is written through the cover's own PUT.

The kernel's own tests (`kernel/tests/test_cover_frame.py`) prove the shape, the binding, the
address and the cut. What these prove is the route: the frame is stored beside the pointers by the
one statement that writes them, read back on the view every screen draws from, refused where it is
not a window of anything, and recorded as a reframe rather than as a new choice.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.testclient import TestClient

from sift.slices.people.tests.conftest import Library, db_path, make_person, read, sign_in

_FRAME = {"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.6}


def _frame_column(client: TestClient, person: str) -> Any:
    rows = read(db_path(client), "SELECT cover_frame FROM people WHERE id = ?", (person,))
    return rows[0]["cover_frame"]


def _cover_lines(client: TestClient, person: str) -> list[tuple[object, object]]:
    rows = read(
        db_path(client),
        "SELECT d.object_id AS object_id, d.payload AS payload FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE s.subject_id = ? AND d.verb = 'edited' ORDER BY d.id",
        (person,),
    )
    return [(row["object_id"], row["payload"]) for row in rows]


def test_the_frame_is_stored_and_read_back_on_the_view(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    person = make_person(client, "Somebody")

    answer = client.put(
        f"/api/people/{person}/cover", json={"asset_id": library.shared, "frame": _FRAME}
    )

    assert answer.status_code == 200
    assert answer.json()["cover_frame"] == _FRAME
    stored = json.loads(_frame_column(client, person))
    assert stored["of"] == f"asset:{library.shared}@"
    assert client.get(f"/api/people/{person}").json()["cover_frame"] == _FRAME


def test_choosing_another_picture_without_a_frame_drops_the_window(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    person = make_person(client, "Somebody")
    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared, "frame": _FRAME})

    answer = client.put(f"/api/people/{person}/cover", json={"asset_id": library.private})

    assert answer.json()["cover_frame"] is None
    assert _frame_column(client, person) is None


def test_moving_the_window_is_recorded_as_a_reframe(client: TestClient, library: Library) -> None:
    """The same picture with a new window says "Cover reframed", not "Cover set to" the file it
    already was: that line would claim a choice nobody made."""
    sign_in(client)
    person = make_person(client, "Somebody")
    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared})

    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared, "frame": _FRAME})

    lines = _cover_lines(client, person)
    assert lines[0][0] == library.shared
    assert lines[-1] == (None, json.dumps({"cover": "reframed"}))


def test_a_frame_outside_the_picture_is_refused(client: TestClient, library: Library) -> None:
    sign_in(client)
    person = make_person(client, "Somebody")

    answer = client.put(
        f"/api/people/{person}/cover",
        json={"asset_id": library.shared, "frame": {"x": 0.7, "y": 0.0, "w": 0.5, "h": 0.5}},
    )

    assert answer.status_code == 422
    assert _frame_column(client, person) is None


def test_a_frame_with_no_picture_is_refused(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Somebody")

    answer = client.put(f"/api/people/{person}/cover", json={"asset_id": None, "frame": _FRAME})

    assert answer.status_code == 422


def test_a_put_cannot_name_an_upload_that_is_not_the_cover(client: TestClient) -> None:
    """Only the picture already the cover may be named, to reframe it. Any other upload id is
    refused as missing: pointing this row at another entity's picture would let the next change
    here delete it from under them."""
    sign_in(client)
    person = make_person(client, "Somebody")

    answer = client.put(
        f"/api/people/{person}/cover",
        json={"upload_id": "01HX00000000000000000000ZZ", "frame": _FRAME},
    )

    assert answer.status_code == 404
