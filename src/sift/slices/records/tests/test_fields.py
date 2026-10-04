# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one route: every described field, grouped, in reading order."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.kernel.records import Shown, Subject, fields_of
from sift.slices.records.tests.conftest import sign_in


def test_the_registry_comes_back_grouped_and_in_reading_order(client: TestClient) -> None:
    """The order is the design, not an accident of how a dictionary happened to iterate."""
    sign_in(client)

    answer = client.get("/api/records/fields")

    assert answer.status_code == 200
    subjects = answer.json()["subjects"]
    assert subjects, "nothing described, so nothing below is being tested"
    assert [one["key"] for one in subjects["asset"]] == [
        one.key for one in fields_of(Subject.ASSET)
    ]
    assert [one["key"] for one in subjects["person"]] == [
        one.key for one in fields_of(Subject.PERSON)
    ]


def test_every_described_field_carries_what_a_screen_needs(client: TestClient) -> None:
    """Every described field carries a label and a type; where it is drawn is read from the enum."""
    sign_in(client)

    subjects = client.get("/api/records/fields").json()["subjects"]
    places = {one.value for one in Shown}

    for group in subjects.values():
        for one in group:
            assert one["label"].strip(), one["key"]
            assert one["kind"], one["key"]
            assert one["shown"] in places, one["key"]
            assert isinstance(one["editable"], bool), one["key"]
            assert one["help"] is None or one["help"].strip(), one["key"]
            if one["kind"] in ("names", "links") and one["editable"]:
                assert one["entry"] and one["entry"].strip(), one["key"]
            assert isinstance(one["ordered"], bool), one["key"]
    # The one list whose order is stored says so on the wire, where the form reads it.
    artists = next(one for one in subjects["song"] if one["key"] == "artists")
    assert artists["ordered"] is True


def test_a_guest_is_described_the_same_fields_as_an_admin(client: TestClient) -> None:
    """This says what a field IS and never what anything's value is, so there is nothing to scope.

    Asserted rather than assumed: the day a value creeps into this answer, the two stop matching.
    """
    sign_in(client, "admin")
    as_admin = client.get("/api/records/fields").json()

    sign_in(client, "guest")
    as_guest = client.get("/api/records/fields").json()

    assert as_guest == as_admin


def test_an_anonymous_caller_is_turned_away(client: TestClient) -> None:
    """The shape of a record is this library's own vocabulary; a session is the price of reading it."""
    assert client.get("/api/records/fields").status_code == 401


def test_what_a_file_measures_is_not_offered_for_editing(client: TestClient) -> None:
    """A box around a file's size invites an edit nothing can honour."""
    sign_in(client)

    asset = client.get("/api/records/fields").json()["subjects"]["asset"]
    by_key = {one["key"]: one for one in asset}

    assert by_key["title"]["editable"] is True
    for measured in ("size_bytes", "container", "vcodec", "fps", "dimensions"):
        assert by_key[measured]["editable"] is False, measured
