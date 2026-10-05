# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's history, over HTTP.

The read itself is proved in the kernel, beside the tables it joins. What is proved here is the
three things only the route decides: that a file this user may not have answers the same 404 a
made-up id gets, that the limit is a bounded query parameter rather than anything a caller can ask
for, and that what comes back on the wire is the shape a client was promised.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.browse.tests.conftest import Library, db_path, share, sign_in, write

pytestmark = [pytest.mark.integration]

_EPOCH = 1_700_000_000


def test_a_file_says_it_arrived_even_when_nothing_else_has_happened_to_it(
    client: TestClient, library: Library
) -> None:
    """One line, in the app's own voice, with a time on it and the name it arrived under.

    The arrival is read off the asset's own row, so it is the one event every file in the library
    has, which makes an empty history impossible for a file that exists, and an empty list a
    reliable sign that something went wrong rather than a file nobody has touched.

    The name is on the line, not a row on the RECORD: what a file arrived as is not a property of
    it, it is the first thing that happened to it.
    """
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/history")

    assert answer.status_code == 200
    assert answer.json()["total"] == 1
    assert answer.json()["items"] == [
        {
            "at": _EPOCH,
            "actor": "sift",
            "actor_name": "Sift",
            "kind": "added",
            # THE LINE AS PIECES: the actor first, plain words, then the folder it was found in as
            # the one thing named. Asserted rather than left out, because this is the one test in
            # the file that spells the whole reply: a field that stopped being sent would be a line
            # nothing draws and no failure anywhere.
            "pieces": [
                {
                    "text": "Sift added this file to the library as shared.mp4 from ",
                    "kind": None,
                    "id": None,
                    "href": None,
                    "gone": False,
                    "rest": [],
                    "lead": "",
                },
                {
                    "text": "clips",
                    "kind": "folder",
                    "id": library.folder,
                    "href": f"/browse?in={library.folder}",
                    "gone": False,
                    "rest": [],
                    "lead": "",
                },
            ],
            "what": "Sift added this file to the library as shared.mp4 from clips",
            "means": "Added to the library",
            "undo": None,
            "reversed": False,
            "detail": [],
            "via": None,
            "how": None,
            # Null rather than absent: an arrival is nothing anybody pressed, so there is no receipt
            # for it, and a client folds two lines into one act by comparing this id, so a field
            # that stopped being sent would silently stop every fold rather than fail anywhere.
            "receipt": None,
            "since": None,
            "away": None,
            # What else a decision wrote that its line does not say: empty on an arrival.
            "more": "",
        }
    ]


def test_a_tag_put_on_by_hand_becomes_a_line_with_the_moment_it_was_decided(
    client: TestClient, library: Library
) -> None:
    """Who decided a row, and when.

    `source` alone says who and never when, so a screen could say "a stash-box put this here" and
    never "on the ninth of September". Both halves are on the wire here.
    """
    sign_in(client, "admin")
    tag = new_id()
    write(
        db_path(client),
        [
            ("INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (tag, "poolside", _EPOCH)),
            (
                "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at)"
                " VALUES (?, ?, NULL, ?)",
                (library.shared, tag, _EPOCH + 60),
            ),
        ],
    )

    events = client.get(f"/api/assets/{library.shared}/history").json()["items"]

    assert [event["kind"] for event in events] == ["added", "tagged"]
    assert events[1]["what"] == "The tag poolside was added here"
    assert events[1]["at"] == _EPOCH + 60
    assert events[1]["actor"] == "somebody"


def test_a_file_this_account_may_not_have_is_a_404_and_not_an_empty_list(
    client: TestClient, library: Library
) -> None:
    """An empty list would say the file exists and nothing has happened to it.

    That is a different answer, and it is the one bit of information the whole permission model is
    trying not to give away. The shared file answers normally to the same user in the same
    breath, which is what proves the refusal is about the file rather than about the session.
    """
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    assert client.get(f"/api/assets/{library.shared}/history").status_code == 200
    assert client.get(f"/api/assets/{library.private}/history").status_code == 404
    assert client.get("/api/assets/nothing-like-an-id/history").status_code == 404


def test_a_move_that_can_still_be_taken_back_says_which_door_undoes_it(
    client: TestClient, library: Library
) -> None:
    """The kind travels with the id because there are two doors, and a client must not guess.

    A move is undone through the organizer and a workbench decision through the workbench, so a
    client holding only an id would have to work out which from the event's kind: a second
    mapping to keep in step with the server's, in a language that cannot check it.
    """
    sign_in(client, "admin")
    move = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO file_moves (id, kind, location_id, asset_id, root_id, from_rel_path,"
                " to_rel_path, moved_by, moved_at, undone_at)"
                " VALUES (?, 'rename', NULL, ?, ?, ?, ?, NULL, ?, NULL)",
                (move, library.shared, library.root, "clips/old.mp4", "clips/new.mp4", _EPOCH + 90),
            )
        ],
    )

    events = client.get(f"/api/assets/{library.shared}/history").json()["items"]

    assert events[-1]["kind"] == "renamed"
    assert events[-1]["what"] == "This file was renamed from old.mp4 to new.mp4"
    assert events[-1]["undo"] == {"kind": "move", "id": move}


def test_the_limit_is_bounded_at_the_door(client: TestClient, library: Library) -> None:
    """A page ceiling that only the read honoured would be a ceiling a caller could argue with.

    Refused here rather than clamped, unlike the kernel's own floor and ceiling: a query parameter
    is something a caller wrote down, and answering 422 tells them what they asked for is not a
    thing to ask for. The kernel clamps because it is called from code that has already been
    checked.
    """
    sign_in(client, "admin")

    assert client.get(f"/api/assets/{library.shared}/history?limit=0").status_code == 422
    assert client.get(f"/api/assets/{library.shared}/history?limit=501").status_code == 422
    assert client.get(f"/api/assets/{library.shared}/history?limit=1").status_code == 200


def _decide(client: TestClient, asset_id: str, *, queue: str, decision_id: str) -> None:
    """One workbench decision, and the link saying it named this file.

    Written straight into the two tables rather than taken through a queue's own route: what is
    being proved here is the ROUTE: that it asks the registry which kinds of decision are final
    and hands the answer to the read, and arranging a real decision of each kind would be proving
    two other features on the way past.
    """
    write(
        db_path(client),
        [
            (
                "INSERT INTO workbench_decisions"
                " (id, queue, user_id, title, detail, payload, decided_at, reversed_at)"
                " VALUES (?, ?, NULL, 'Kept one of a group', 'It did something.', '{}', ?, NULL)",
                (decision_id, queue, _EPOCH + 120),
            ),
            (
                "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
                " VALUES (?, 'asset', ?)",
                (decision_id, asset_id),
            ),
        ],
    )


def test_a_decision_that_named_this_file_says_the_workbench_undoes_it(
    client: TestClient, library: Library
) -> None:
    """The second door. `duplicates` is a queue whose decisions can be taken back."""
    sign_in(client, "admin")
    decision = new_id()
    _decide(client, library.shared, queue="duplicates", decision_id=decision)

    events = client.get(f"/api/assets/{library.shared}/history").json()["items"]

    assert events[-1]["kind"] == "decided"
    assert events[-1]["what"] == "Kept one of a group"
    assert events[-1]["undo"] == {"kind": "decision", "id": decision}


def test_a_decision_of_a_kind_that_can_never_be_undone_offers_no_button(
    client: TestClient, library: Library
) -> None:
    """`copies` releases a copy from a disk, which is gone. Only the registry knows that, and only
    this route can ask it, so a route that forgot to would draw a button whose one way of saying
    no is being pressed.

    Both halves in one test on purpose: the same decision under a reversible queue MUST offer the
    button, or this would pass with the whole read broken.
    """
    sign_in(client, "admin")
    final, ordinary = new_id(), new_id()
    _decide(client, library.shared, queue="copies", decision_id=final)
    _decide(client, library.shared, queue="duplicates", decision_id=ordinary)

    offered = {
        one["undo"]["id"] if one["undo"] else None
        for one in client.get(f"/api/assets/{library.shared}/history").json()["items"]
        if one["kind"] == "decided"
    }

    assert offered == {None, ordinary}


def test_the_file_itself_carries_how_many_lines_its_history_has(
    client: TestClient, library: Library
) -> None:
    """The number the History tab wears before anybody opens the pane.

    Asserted against the length of the PANE'S OWN reply rather than against a literal, because the
    property that matters is that the two agree: a tab saying two over a pane drawing one is the
    fault this field exists to end, and it is the only way this can go wrong that nobody would
    notice until they pressed it.
    """
    sign_in(client, "admin")
    tag = new_id()
    write(
        db_path(client),
        [
            ("INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (tag, "poolside", _EPOCH)),
            (
                "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at)"
                " VALUES (?, ?, NULL, ?)",
                (library.shared, tag, _EPOCH + 60),
            ),
        ],
    )

    detail = client.get(f"/api/assets/{library.shared}").json()
    events = client.get(f"/api/assets/{library.shared}/history").json()["items"]

    assert detail["history_count"] == len(events)
    assert detail["history_count"] == 2


def test_a_read_shorter_than_the_history_says_the_whole_total_and_so_does_the_tab(
    client: TestClient, library: Library
) -> None:
    """The newest lines of a long history and how many there are in all: the tab's number is the
    total, never the length of one page, and Show earlier can say how many more."""
    sign_in(client, "admin")
    tag = new_id()
    write(
        db_path(client),
        [
            ("INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (tag, "poolside", _EPOCH)),
            (
                "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at)"
                " VALUES (?, ?, NULL, ?)",
                (library.shared, tag, _EPOCH + 60),
            ),
        ],
    )

    page = client.get(f"/api/assets/{library.shared}/history?limit=1").json()
    detail = client.get(f"/api/assets/{library.shared}").json()

    assert [one["kind"] for one in page["items"]] == ["tagged"]
    assert page["total"] == 2
    assert detail["history_count"] == 2


def test_the_file_carries_its_disagreement_count_for_the_history_tabs_mark(
    client: TestClient, library: Library
) -> None:
    """The mark on the History tab: nought for an admin asked about a file no box disagrees with,
    which draws no mark. (A user who may not be told gets the seam's own None, `reconcile`'s.)"""
    sign_in(client, "admin")
    detail = client.get(f"/api/assets/{library.shared}").json()
    assert detail["disagreements"] == 0
    assert detail["disagreement_boxes"] == []


def test_a_line_naming_a_site_or_folder_a_guest_may_not_be_shown_leaves_it_nameless(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel.access import sentences as say
    from sift.kernel.access.history_line import Actor, Event

    module = importlib.import_module("sift.slices.browse.router")

    site = new_id()
    write(
        db_path(client), [("INSERT INTO sites (id, name) VALUES (?, 'Pier Nine Media')", (site,))]
    )
    line = say.said(
        "Sift filed this under ",
        say.thing("site", site, "Pier Nine Media"),
        " from the folder ",
        say.folder_named(library.folder, "clips"),
    )
    real = module.history_of_asset

    async def with_a_line(*args: object, **kwargs: object) -> list[Event]:
        every = await real(*args, **kwargs)
        return [
            *every,
            Event(at=_EPOCH, actor=Actor.SIFT, actor_name=None, kind="filed", pieces=line),
        ]

    monkeypatch.setattr(module, "history_of_asset", with_a_line)
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)
    said = client.get(f"/api/assets/{library.shared}/history").json()["items"][-1]
    assert said["what"] == "Sift filed this under a Site from a folder"
    assert [one["id"] for one in said["pieces"]] == [None]

    sign_in(client, "admin")
    said = client.get(f"/api/assets/{library.shared}/history").json()["items"][-1]
    assert said["what"] == "Sift filed this under Pier Nine Media from the folder clips"
    assert [one["id"] for one in said["pieces"] if one["id"]] == [site, library.folder]
