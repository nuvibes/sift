# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-box endpoints, over HTTP, against a real application: the scan, the batch and the pile of answers.

Nothing here reaches a network: the adapter on the running application is replaced, so what is
asserted is which questions the routes would ask and what they hand back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.reach import OUT_OF_REACH, VAULT_LOCKED
from sift.slices.stash_boxes.tests.api_support import (
    AN_ASSET,
    ANOTHER_ASSET,
    _a_known_box,
    _a_library,
    _a_site,
    _a_tag,
    _decision,
    _keep_local,
    _linked_person,
    _matching_on,
    _payloads,
    _read,
    _write,
    a_creators_box,
    a_library,
    a_match,
    a_person,
    add_a_box,
    db_path,
    sign_in,
    stand_in,
)
from sift.testing.library import hide_for

pytestmark = pytest.mark.integration


def test_the_pile_carries_what_applying_each_row_would_change(
    client: TestClient, tmp_path: Path
) -> None:
    """The whole point of the screen. A bulk confirm nobody can see the consequences of is a leap
    of faith with a progress bar on it."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Clip", "people": ["Jane"]})

    body = client.get("/api/stash-boxes/matches").json()

    assert body["total"] == 1
    row = body["matches"][0]
    assert row["asset_id"] == asset
    assert row["grade"] == "certain"
    assert {one["key"] for one in row["changes"]} == {"title", "people"}
    assert row["creates"] == [{"name": "Jane", "kind": "person"}], (
        "the rows it would have to invent, each saying WHICH it is, listed before the press"
    )


def test_the_preview_says_which_fields_hang_on_a_tick_and_the_press_agrees(
    client: TestClient, tmp_path: Path
) -> None:
    """A field naming only new rows is written only when one of them is ticked. The count above the
    button reads that from the preview; the press must land exactly the fields that count said."""
    sign_in(client, "admin")
    box = add_a_box(client)
    first, second = a_library(client, tmp_path, "one", "two")
    for asset in (first, second):
        a_match(client, asset, box, {"title": "A Clip", "tags": ["Beach", "Outdoor"]})

    row = client.get("/api/stash-boxes/matches", params={"asset": first}).json()["matches"][0]
    by_key = {one["key"]: one for one in row["changes"]}
    assert (by_key["title"]["needs"], by_key["title"]["stands"]) == ([], True)
    assert by_key["tags"]["stands"] is False, "the tags write nothing unless one is made"
    assert sorted(one["name"] for one in by_key["tags"]["needs"]) == ["Beach", "Outdoor"]

    def press(asset: str, create: list[dict[str, str]]) -> int:
        answer = client.post(
            "/api/stash-boxes/matches/apply",
            json={"matches": [{"asset_id": asset, "box_id": box}], "create": create},
        )
        assert answer.status_code == 200, answer.text
        return int(answer.json()["fields"])

    assert press(first, []) == 1, "nothing ticked: the title and not the tags"
    assert press(second, [{"name": "Beach", "kind": "tag"}]) == 2, "one tag ticked: both"


def test_a_row_about_a_file_this_account_may_not_open_is_not_drawn(
    client: TestClient, tmp_path: Path
) -> None:
    """A match is a statement about a file, and a screen that drew one for a file it would refuse
    to open is the kind of disagreement nobody notices until it matters."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Clip"})
    sign_in(client, "guest")

    assert client.get("/api/stash-boxes/matches").status_code == 403


def test_one_press_settles_a_page_and_signs_for_it_once(client: TestClient, tmp_path: Path) -> None:
    """A run that signed for itself a thousand times could not be undone by anybody."""
    sign_in(client, "admin")
    box = add_a_box(client)
    first, second = a_library(client, tmp_path, "one", "two")
    a_match(client, first, box, {"title": "First"})
    a_match(client, second, box, {"title": "Second"})

    answer = client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [
                {"asset_id": first, "box_id": box},
                {"asset_id": second, "box_id": box},
            ],
        },
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["files"], body["fields"]) == (2, 2)
    assert body["decision_id"], "one receipt for the whole press"
    # WHOSE answers and WHAT they wrote, not "what the stash-boxes said about 2 files".
    said = _decision(client, body["decision_id"])
    assert said["title"] == "Applied StashDB's answers to 2 files: title"
    pile = client.get("/api/stash-boxes/matches").json()
    # Nothing waiting, and the pile says the two were answered, so an empty tab can tell "all
    # agreed to" from "never switched on".
    assert (pile["total"], pile["answered"]) == (0, 2)
    # And the answered two are one press away, as the pile's other state, newest answer first,
    # each saying how it was answered and when.
    settled = client.get("/api/stash-boxes/matches", params={"state": "answered"}).json()
    assert settled["total"] == 2
    assert {one["state"] for one in settled["matches"]} == {"applied"}
    assert all(one["decided_at"] for one in settled["matches"])
    # And one file's share of that half, which the chooser on the file's own menu reads.
    mine = client.get(
        "/api/stash-boxes/matches", params={"state": "answered", "asset": first}
    ).json()
    assert [one["asset_id"] for one in mine["matches"]] == [first]
    assert client.get("/api/stash-boxes/matches", params={"state": "later"}).status_code == 422


def test_applying_writes_what_the_answer_says_onto_the_file(
    client: TestClient, tmp_path: Path
) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Better Name"})

    client.post(
        "/api/stash-boxes/matches/apply",
        json={"matches": [{"asset_id": asset, "box_id": box}]},
    )

    assert client.get(f"/api/assets/{asset}").json()["title"] == "A Better Name"


def test_applying_an_answer_naming_TAGS_AND_A_SITE_queues_them_for_enrichment_too(
    client: TestClient, tmp_path: Path
) -> None:
    """All three kinds, not people alone.

    An answer names people, tags and a site, and every row behind a name no box is linked to is
    handed to the enrichment job. The site and the tag arms are exercised here as well as the
    person one, and they are the arms that decide WHICH lookup a name goes through.
    A name routed to the wrong one finds nothing and the row is quietly never enriched.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(
        client,
        asset,
        box,
        {"people": ["Jane"], "tags": ["Beach", "Sunset"], "site": "Somewhere"},
    )

    answer = client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [{"asset_id": asset, "box_id": box}],
            "create": [
                {"name": "Jane", "kind": "person"},
                {"name": "Beach", "kind": "tag"},
                {"name": "Sunset", "kind": "tag"},
                {"name": "Somewhere", "kind": "site"},
            ],
        },
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    # The rows were made, which is what gives the enrichment something to look up.
    assert body["created"] >= 1
    assert {one["name"] for one in client.get("/api/tags").json()["items"]} >= {"Beach", "Sunset"}
    assert {one["name"] for one in client.get("/api/sites").json()["items"]} >= {"Somewhere"}


def test_an_old_answer_from_a_creators_box_offers_its_studio_as_the_person_who_made_it(
    client: TestClient, tmp_path: Path
) -> None:
    """The oldest kept shape, read the way the box reads today, all the way to the wall.

    A match kept by an older mapper carries this box's creator under `site`. Read now, that studio
    is the creator: the confirm screen offers to make them, pressing it makes them, the file
    carries them, and they wear the mark that says they make the edits. No Site is created named
    after a person.
    """
    sign_in(client, "admin")
    box = a_creators_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"site": "quillmoss", "people": ["Orla Tennant"]})

    row = client.get("/api/stash-boxes/matches").json()["matches"][0]
    assert [one["name"] for one in row["creates"]] == ["quillmoss", "Orla Tennant"]

    answer = client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [{"asset_id": asset, "box_id": box}],
            "create": [
                {"name": "quillmoss", "kind": "person"},
                {"name": "Orla Tennant", "kind": "person"},
            ],
        },
    )

    assert answer.status_code == 200, answer.text
    made = {one["name"]: one for one in client.get("/api/people").json()["items"]}
    assert sorted(made) == ["Orla Tennant", "quillmoss"]
    assert made["quillmoss"]["pmv_creator"] is True
    assert made["Orla Tennant"]["pmv_creator"] is False
    assert client.get("/api/sites").json()["items"] == []


def test_creating_is_per_press_and_is_counted_in_the_answer(
    client: TestClient, tmp_path: Path
) -> None:
    """A stored "always make the people" would be the automatic creation this feature refuses."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"people": ["Jane"]})

    without = client.post(
        "/api/stash-boxes/matches/apply",
        json={"matches": [{"asset_id": asset, "box_id": box}]},
    ).json()

    assert without["created"] == 0
    assert client.get("/api/people").json()["items"] == []


def test_creating_when_it_is_asked_for_makes_the_rows_and_counts_them(
    client: TestClient, tmp_path: Path
) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"people": ["Jane"]})

    answer = client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [{"asset_id": asset, "box_id": box}],
            "create": [{"name": "Jane", "kind": "person"}],
        },
    ).json()

    assert answer["created"] == 1
    assert [one["name"] for one in client.get("/api/people").json()["items"]] == ["Jane"]


def test_a_disagreement_on_a_file_is_answered_where_it_is_shown(
    client: TestClient, tmp_path: Path
) -> None:
    """A conflict on a file is settled on the row it was shown on.

    The screen that settles disagreements walks linked people, sites and tags (never files), so
    this is the only place a file's conflict can be answered.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    client.put(f"/api/assets/{asset}", json={"title": "What I Called It"})
    a_match(client, asset, box, {"title": "What They Call It"})

    row = client.get("/api/stash-boxes/matches").json()["matches"][0]
    (change,) = [one for one in row["changes"] if one["key"] == "title"]
    assert change["outcome"] == "conflict"
    # No `can_keep_both` on the wire: it could only ever be False, and not because a title holds
    # one value: because a conflict ALWAYS does: a field holding many is merged and both answers
    # are kept without anybody being asked, so it is never among the conflicts. The refusal of
    # `take="both"` below is a different thing, because it guards an untrusted body.
    assert "can_keep_both" not in change

    client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [{"asset_id": asset, "box_id": box}],
            "settle": [{"asset_id": asset, "box_id": box, "key": "title", "take": "theirs"}],
        },
    )

    assert client.get(f"/api/assets/{asset}").json()["title"] == "What They Call It"


def test_a_disagreement_nobody_answered_keeps_what_is_already_there(
    client: TestClient, tmp_path: Path
) -> None:
    """Keeping your own is the default, and it is the commonest answer."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    client.put(f"/api/assets/{asset}", json={"title": "What I Called It"})
    a_match(client, asset, box, {"title": "What They Call It"})

    client.post(
        "/api/stash-boxes/matches/apply",
        json={"matches": [{"asset_id": asset, "box_id": box}]},
    )

    assert client.get(f"/api/assets/{asset}").json()["title"] == "What I Called It"


def test_keeping_both_is_refused_on_a_field_that_holds_one_value(
    client: TestClient, tmp_path: Path
) -> None:
    """The screen does not offer it there, and the server does not take its word for that.

    Writing a list into a single value would put "a, b" where one name goes, worse than either
    answer, and arrived at by agreeing to something nobody was shown.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    client.put(f"/api/assets/{asset}", json={"title": "What I Called It"})
    a_match(client, asset, box, {"title": "What They Call It"})

    client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [{"asset_id": asset, "box_id": box}],
            "settle": [{"asset_id": asset, "box_id": box, "key": "title", "take": "both"}],
        },
    )

    assert client.get(f"/api/assets/{asset}").json()["title"] == "What I Called It"


def test_an_answer_naming_a_field_that_is_not_in_disagreement_writes_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    """Only fields the plan itself calls a CONFLICT.

    `title` here is a plain write: there was nothing in it, so nothing disagreed and no question
    was asked about it. An answer naming it is a request to write a field that was never offered as
    a choice, and writing it a second time would report two fields for one value.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Clip"})

    row = client.get("/api/stash-boxes/matches").json()["matches"][0]
    assert [one["outcome"] for one in row["changes"]] == ["write"], "no question was asked"

    answer = client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [{"asset_id": asset, "box_id": box}],
            "settle": [{"asset_id": asset, "box_id": box, "key": "title", "take": "theirs"}],
        },
    ).json()

    assert answer["fields"] == 1, "the title the plan decided, once, and nothing the answer added"


def test_a_row_that_is_already_settled_is_not_settled_a_second_time(
    client: TestClient, tmp_path: Path
) -> None:
    """What makes a bulk confirm safe to press again after it half-failed."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Clip"})
    press = {"matches": [{"asset_id": asset, "box_id": box}]}
    client.post("/api/stash-boxes/matches/apply", json=press)

    again = client.post("/api/stash-boxes/matches/apply", json=press).json()

    assert (again["files"], again["decision_id"]) == (0, "")


def test_a_press_naming_a_file_nobody_may_see_settles_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)

    answer = client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [{"asset_id": "01HX0000000000000000000009", "box_id": box}],
        },
    ).json()

    assert answer["files"] == 0


def test_saying_no_stops_them_being_asked_about_and_writes_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    """A refusal is remembered rather than deleted: a row that is gone comes straight back the next
    time the pass runs, and the same wrong answer is then offered for ever."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Clip"})

    answer = client.post(
        "/api/stash-boxes/matches/refuse",
        json={"matches": [{"asset_id": asset, "box_id": box}]},
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["files"] == 1
    assert client.get("/api/stash-boxes/matches").json()["total"] == 0
    assert client.get(f"/api/assets/{asset}").json()["title"] is None


def test_a_refusal_naming_a_file_nobody_may_see_refuses_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)

    answer = client.post(
        "/api/stash-boxes/matches/refuse",
        json={
            "matches": [{"asset_id": "01HX0000000000000000000009", "box_id": box}],
        },
    ).json()

    assert answer["files"] == 0


# --- starting a pass ----------------------------------------------------------------------------


def test_a_scan_is_refused_while_matching_is_switched_off(client: TestClient) -> None:
    """Refused rather than silently ignored: a button that queues a job which then declines to do
    anything reads as a broken button."""
    from sift.slices.stash_boxes.settings import ENRICHING_OFF

    sign_in(client, "admin")

    answer = client.post("/api/stash-boxes/scan")

    # The one sentence every press gives for this state, naming where to turn it on.
    assert (answer.status_code, answer.json()["detail"]) == (409, ENRICHING_OFF)


def test_a_scan_queues_a_sweep_and_comes_back_at_once(client: TestClient) -> None:
    """The sweep asks nothing itself (it works out which files have not been asked about and
    queues one question each), so this returns immediately and the work shows up in the job list."""
    from sift.slices.stash_boxes.settings import SCAN_KEY

    user_id = sign_in(client, "admin")
    add_a_box(client)  # a press that would ask nobody is refused
    turned_on = client.put("/api/settings", json={"values": {SCAN_KEY: True}})
    assert turned_on.status_code == 204, turned_on.text

    answer = client.post("/api/stash-boxes/scan")

    assert answer.status_code == 200, answer.text
    job_id = answer.json()["job_id"]
    assert job_id
    # A press, and whose: its per-file questions follow it as the same press (`enqueue_child`).
    # Marked as one on the row, so the lookups' When, "Only when I press it" by default, which
    # refuses the run nobody pressed, never refuses this one.
    (row,) = _read(db_path(client), "SELECT requested_by, timing FROM jobs WHERE id = ?", (job_id,))
    assert row["requested_by"] == user_id
    assert row["timing"] == "now"


def test_a_row_about_a_file_the_reader_may_not_open_is_left_out_of_the_pile(
    client: TestClient, tmp_path: Path
) -> None:
    """The pass runs as an admin and this is read by one, so in practice they agree, but a match
    is a statement about a file, and a screen that drew one for a file it would refuse to open is
    the kind of disagreement nobody notices until it matters."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Clip"})
    # The file goes, and the match row stays: exactly the state this line is about.
    _write(db_path(client), [("DELETE FROM asset_locations WHERE asset_id = ?", (asset,))])

    body = client.get("/api/stash-boxes/matches").json()

    assert body["matches"] == []
    assert body["total"] == 1, "the count is what is waiting, not what this reader may see"


def test_a_row_whose_plan_cannot_be_made_is_drawn_with_no_changes_on_it(
    client: TestClient, tmp_path: Path
) -> None:
    """An older build meeting a newer one: nothing can write a file, so there is nothing to say the
    row would change, and drawing the row anyway beats an empty pile with no explanation."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Clip"})
    client.app.state.enricher.writers.clear()  # type: ignore[attr-defined]

    body = client.get("/api/stash-boxes/matches").json()

    assert [one["changes"] for one in body["matches"]] == [[]]
    assert [one["creates"] for one in body["matches"]] == [[]]


def test_a_press_whose_plan_cannot_be_made_settles_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Clip"})
    client.app.state.enricher.writers.clear()  # type: ignore[attr-defined]

    answer = client.post(
        "/api/stash-boxes/matches/apply",
        json={"matches": [{"asset_id": asset, "box_id": box}]},
    ).json()

    assert (answer["files"], answer["decision_id"]) == (0, "")


def test_saying_no_to_a_row_already_settled_refuses_nothing_a_second_time(
    client: TestClient, tmp_path: Path
) -> None:
    """The same property the apply has, and for the same reason: a bulk press that half-failed has
    to be safe to press again."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Clip"})
    press = {"matches": [{"asset_id": asset, "box_id": box}]}
    client.post("/api/stash-boxes/matches/refuse", json=press)

    again = client.post("/api/stash-boxes/matches/refuse", json=press).json()

    assert again["files"] == 0


def test_saying_no_to_an_answer_already_applied_takes_back_what_it_wrote(
    client: TestClient, tmp_path: Path
) -> None:
    """A refusal of an applied answer is whole: its title, its person and its filings come off the
    file, and the person it made, holding nothing else, goes too. Refused again, it is nothing."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "A Better Name", "people": ["Nell Quarry"]})
    press = {"matches": [{"asset_id": asset, "box_id": box}]}
    client.post(
        "/api/stash-boxes/matches/apply",
        json={**press, "create": [{"name": "Nell Quarry", "kind": "person"}]},
    )
    assert client.get(f"/api/assets/{asset}").json()["title"] == "A Better Name"

    refused = client.post("/api/stash-boxes/matches/refuse", json=press).json()

    assert refused["files"] == 1
    assert refused["decision_id"], "the take-back's receipt was not handed back for its Undo"
    assert client.get(f"/api/assets/{asset}").json()["title"] is None
    people = client.get("/api/people").json()["items"]
    assert "Nell Quarry" not in {one["name"] for one in people}
    again = client.post("/api/stash-boxes/matches/refuse", json=press).json()
    assert again["files"] == 0
    undone = client.post(f"/api/workbench/decisions/{refused['decision_id']}/undo", json={})
    assert undone.status_code == 200, undone.text
    assert client.get(f"/api/assets/{asset}").json()["title"] == "A Better Name"


def test_saying_no_where_the_box_said_nothing_about_the_file_changes_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")

    refused = client.post(
        "/api/stash-boxes/matches/refuse", json={"matches": [{"asset_id": asset, "box_id": box}]}
    )

    assert refused.status_code == 200, refused.text
    assert refused.json()["files"] == 0
    assert not refused.json()["decision_id"]


def test_the_pile_and_the_ledger_open_at_their_row_or_where_the_page_was(
    client: TestClient,
) -> None:
    """Both states of the pile and the ledger keep their page in the tab's address. A row still on
    the list is where the page starts, wherever `near` says it was; a row that is gone (answered,
    or its link taken off) is answered with where the page was, never the top."""
    sign_in(client, "admin")
    box = add_a_box(client)
    person = _linked_person(client, box, image_url=None)

    for address, params in (
        ("/api/stash-boxes/matches", {}),
        ("/api/stash-boxes/matches", {"state": "answered"}),
        ("/api/stash-boxes/linked", {}),
        ("/api/stash-boxes/linked", {"subject": "tag"}),
    ):
        answer = client.get(address, params={**params, "from": "gone:gone", "near": 2})
        assert answer.status_code == 200, answer.text
        assert answer.json()["offset"] == 2, f"{address} served the top for a row that is gone"

    found = client.get("/api/stash-boxes/linked", params={"from": f"{person}:{box}", "near": 5})
    assert found.json()["offset"] == 0, "the row itself wins over where the page was"


def test_a_guest_is_refused_the_picture_and_the_batch(client: TestClient) -> None:
    sign_in(client, "admin")
    box = add_a_box(client)
    person = a_person(client)
    sign_in(client, "guest")

    assert (
        client.post(
            f"/api/stash-boxes/{box}/picture/keep",
            json={"subject": "person", "local_id": person},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/stash-boxes/enrich", json={"subject": "person", "ids": [person]}
        ).status_code
        == 403
    )


# --- asking about a batch of subjects ------------------------------------------------------------


def test_asking_about_a_batch_queues_one_job_and_says_how_many_it_holds(
    client: TestClient,
) -> None:
    """One job for the batch, not one per subject: the pacing is per box, so forty jobs would spend
    their lives waiting on the same limiter."""
    sign_in(client, "admin")
    add_a_box(client)  # a press that would ask nobody is refused
    one = a_person(client, "Jane")
    two = a_person(client, "Doe")

    answer = client.post("/api/stash-boxes/enrich", json={"subject": "person", "ids": [one, two]})

    assert answer.status_code == 200, answer.text
    assert answer.json()["asked"] == 2
    assert answer.json()["job_id"]


def test_the_count_is_of_what_can_really_be_asked_about(client: TestClient) -> None:
    """An id that has gone since the wall drew it is dropped here rather than counted. Saying
    "asking about 2" over a job that asks about one is a small lie that makes the job list wrong."""
    sign_in(client, "admin")
    add_a_box(client)  # a press that would ask nobody is refused
    person = a_person(client)

    answer = client.post(
        "/api/stash-boxes/enrich",
        json={"subject": "person", "ids": [person, "01HX0000000000000000000009"]},
    )

    assert answer.json()["asked"] == 1


def test_a_batch_naming_nobody_who_can_be_asked_about_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")
    add_a_box(client)  # a press that would ask nobody is refused

    answer = client.post(
        "/api/stash-boxes/enrich",
        json={"subject": "person", "ids": ["01HX0000000000000000000009"]},
    )

    assert answer.status_code == 404


def test_a_press_that_would_ask_nobody_is_refused_in_the_panes_words(client: TestClient) -> None:
    """With matching on and no stash-box to ask, the Enrich press on the library, on files and on
    people is refused rather than answering "started" for work that would ask nobody. Each is
    refused with the sentence the Tasks pane's Run now gives, from the one rule they share; and a
    press naming a box that is off while another is on says that box is the one."""
    from sift.slices.stash_boxes.settings import CHOSEN_BOX_OFF, EVERY_BOX_OFF, SCAN_KEY

    sign_in(client, "admin")
    turned_on = client.put("/api/settings", json={"values": {SCAN_KEY: True}})
    assert turned_on.status_code == 204, turned_on.text
    person = a_person(client)

    presses = [
        client.post("/api/stash-boxes/scan"),
        client.post("/api/stash-boxes/scan", json={"assets": [AN_ASSET]}),
        client.post("/api/stash-boxes/enrich", json={"subject": "person", "ids": [person]}),
    ]
    assert [(one.status_code, one.json()["detail"]) for one in presses] == [
        (409, EVERY_BOX_OFF)
    ] * 3

    add_a_box(client)
    named = client.post("/api/stash-boxes/scan", json={"box": "fansdb"})
    assert (named.status_code, named.json()["detail"]) == (409, CHOSEN_BOX_OFF)


def test_naming_files_queues_one_question_each_and_answers_with_the_first(
    client: TestClient, tmp_path: Path
) -> None:
    """Separate jobs on purpose: each is one third-party request, they are paced by the adapter's
    own limiter, and one that fails takes only its own file down rather than the selection.

    Named files skip the sweep entirely. The sweep's whole job is working out WHICH files have not
    been asked about; when somebody has just pointed at them, running it would be a walk of the
    library to rediscover an answer it was handed.
    """
    sign_in(client, "admin")
    _matching_on(client)
    _a_library(client, tmp_path, AN_ASSET, ANOTHER_ASSET)

    answer = client.post("/api/stash-boxes/scan", json={"assets": [AN_ASSET, ANOTHER_ASSET]})

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["job_id"]
    assert body["asked"] == 2
    assert body["skipped"] == 0
    assert body["reason"] is None


def test_a_file_in_a_locked_vault_is_refused_rather_than_quietly_not_asked_about(
    client: TestClient, tmp_path: Path
) -> None:
    """A concealed file is refused out loud, not queued like any other.

    The job reads it with `open_asset`, which refuses a placeholder, so nothing is sent. A route
    that queued the job and answered 200 would look exactly like a file that was sent and matched
    nothing.

    423 with the vault's own sentence, which is what every other write on a concealed file already
    answers and what the padlock toast with its Unlock button is built on.
    """
    user_id = sign_in(client, "admin")
    _matching_on(client)
    _a_library(client, tmp_path, AN_ASSET)
    hide_for(db_path(client), "asset", AN_ASSET, user_id)

    answer = client.post("/api/stash-boxes/scan", json={"assets": [AN_ASSET]})

    assert answer.status_code == 423, answer.text
    assert answer.json()["detail"] == VAULT_LOCKED


def test_a_selection_asks_about_what_it_can_and_says_what_it_left_out(
    client: TestClient, tmp_path: Path
) -> None:
    """`reach`'s own rule: a partial success is not a reason to throw away the part that worked.

    Four files asked about and one held back is not a failure; it is a fact the screen has to be
    able to state. What must never happen is the silent version of it.
    """
    user_id = sign_in(client, "admin")
    _matching_on(client)
    _a_library(client, tmp_path, AN_ASSET, ANOTHER_ASSET)
    hide_for(db_path(client), "asset", ANOTHER_ASSET, user_id)

    answer = client.post("/api/stash-boxes/scan", json={"assets": [AN_ASSET, ANOTHER_ASSET]})

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["asked"] == 1
    assert body["skipped"] == 1
    assert body["reason"] == VAULT_LOCKED
    assert body["vault_locked"] is True


def test_a_thousand_file_selection_is_asked_about_whole_and_every_file_is_counted(
    client: TestClient, tmp_path: Path
) -> None:
    """The count on screen is the count asked: asked plus skipped is the whole selection.

    A selection is never cut to a page size. The two real files sit at the front and the back of
    the selection, so a cut anywhere loses the last one and the sum comes up short.
    """
    from sift.slices.stash_boxes.router import MOST_NAMED_FILES

    sign_in(client, "admin")
    _matching_on(client)
    _a_library(client, tmp_path, AN_ASSET, ANOTHER_ASSET)
    nowhere = [f"01HY{index:022d}" for index in range(MOST_NAMED_FILES - 2)]
    selection = [AN_ASSET, *nowhere, ANOTHER_ASSET]

    answer = client.post("/api/stash-boxes/scan", json={"assets": selection})

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["asked"] == 2
    assert body["asked"] + body["skipped"] == len(selection) == MOST_NAMED_FILES
    assert body["reason"] == OUT_OF_REACH


def test_a_selection_is_queued_as_the_press_of_whoever_made_it(
    client: TestClient, tmp_path: Path
) -> None:
    """Enrich on a selection is a PRESS, and the queue is told whose.

    Queued as work nobody asked for, the enrichment task's When would govern it: held to quiet
    hours when that is its When, refused when it only runs when pressed. It is also one write for
    the whole selection (`JobQueue.enqueue_many`), so every file's job is there.
    """
    user_id = sign_in(client, "admin")
    _matching_on(client)
    _a_library(client, tmp_path, AN_ASSET, ANOTHER_ASSET)

    answer = client.post("/api/stash-boxes/scan", json={"assets": [AN_ASSET, ANOTHER_ASSET]})

    assert answer.status_code == 200, answer.text
    rows = _read(
        db_path(client),
        "SELECT requested_by, timing FROM jobs WHERE type = 'stash_box_scan'",
        (),
    )
    assert [(row["requested_by"], row["timing"]) for row in rows] == [(user_id, "now")] * 2


def test_a_selection_past_the_ceiling_is_refused_out_loud_rather_than_cut(
    client: TestClient,
) -> None:
    """One file over is a sentence, not a quiet trim to a number nobody chose."""
    from sift.slices.stash_boxes.router import MOST_NAMED_FILES

    sign_in(client, "admin")
    _matching_on(client)
    selection = [f"01HY{index:022d}" for index in range(MOST_NAMED_FILES + 1)]

    answer = client.post("/api/stash-boxes/scan", json={"assets": selection})

    assert answer.status_code == 422, answer.text
    assert "1,000 files at a time" in answer.json()["detail"]


def test_a_file_that_never_existed_is_a_plain_miss(client: TestClient) -> None:
    """Not 423. The vault answer is relaxed for the user who locked their own file, and nothing
    else: an id that names nothing must not be told apart from one belonging to somebody else."""
    sign_in(client, "admin")
    _matching_on(client)

    answer = client.post("/api/stash-boxes/scan", json={"assets": [AN_ASSET]})

    assert answer.status_code == 404, answer.text
    assert answer.json()["detail"] == OUT_OF_REACH


def test_naming_files_is_refused_as_plainly_as_the_whole_library_is(client: TestClient) -> None:
    """The setting is about matching against the stash-boxes at all, not about the unattended pass.

    Asserted because a screen depends on it: the chooser on one file's own menu has a whole panel
    to say it is turned off in, and it can only say so if the route refuses rather than queueing
    a job that then declines to do anything.
    """
    from sift.slices.stash_boxes.settings import ENRICHING_OFF

    sign_in(client, "admin")

    answer = client.post("/api/stash-boxes/scan", json={"assets": ["01HX0000000000000000000001"]})

    assert (answer.status_code, answer.json()["detail"]) == (409, ENRICHING_OFF)


def test_the_batch_takes_a_site_and_a_tag_as_readily_as_a_person(client: TestClient) -> None:
    """Three kinds, one route. A Site resolved through the people read would be invisible to
    it, and the batch would report that none of them could be asked about."""
    sign_in(client, "admin")
    add_a_box(client)  # a press that would ask nobody is refused

    for subject, local_id in (
        ("site", _a_site(client)),
        ("tag", _a_tag(client)),
    ):
        answer = client.post(
            "/api/stash-boxes/enrich", json={"subject": subject, "ids": [local_id]}
        )
        assert answer.status_code == 200, answer.text
        assert answer.json()["asked"] == 1


def test_applying_a_page_puts_the_decision_in_each_files_own_history(
    client: TestClient, tmp_path: Path
) -> None:
    """The whole chain, end to end: the press records what it touched, and the file can be asked.

    Through the history route rather than the link table, because that is the question the table
    exists to answer and a test reading the row back would pass with the read still broken.
    """
    sign_in(client, "admin")
    box = add_a_box(client)
    first, second = a_library(client, tmp_path, "one", "two")
    a_match(client, first, box, {"title": "First"})
    a_match(client, second, box, {"title": "Second"})

    answer = client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [{"asset_id": first, "box_id": box}, {"asset_id": second, "box_id": box}],
        },
    )
    assert answer.status_code == 200, answer.text

    for asset in (first, second):
        history = client.get(f"/api/assets/{asset}/history").json()["items"]
        decided = [one for one in history if one["kind"] == "decided"]
        assert len(decided) == 1, asset
        assert decided[0]["undo"] == {"kind": "decision", "id": answer.json()["decision_id"]}


def test_a_waiting_answer_on_a_file_kept_local_since_is_neither_listed_nor_applied_kept_local(
    client: TestClient, tmp_path: Path
) -> None:
    """A match found last week, then "Do not enrich" on the file: the answer is not a question any
    more, so it leaves the pile and its count, and a press naming it anyway is refused rather than
    written. Allowing enrichment again brings it back untouched, because nothing was settled."""
    from sift.slices.stash_boxes.service import KEPT_LOCAL

    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_match(client, asset, box, {"title": "Their Name"})
    _keep_local(client, "asset", asset)

    pile = client.get("/api/stash-boxes/matches").json()
    assert (pile["total"], pile["matches"]) == (0, [])
    assert client.get("/api/stash-boxes/matches", params={"asset": asset}).json()["total"] == 0

    pressed = client.post(
        "/api/stash-boxes/matches/apply", json={"matches": [{"asset_id": asset, "box_id": box}]}
    )
    assert pressed.status_code == 409, pressed.text
    assert pressed.json()["detail"] == KEPT_LOCAL
    assert client.get(f"/api/assets/{asset}").json()["title"] != "Their Name"

    _keep_local(client, "asset", asset, kept=False)
    assert client.get("/api/stash-boxes/matches").json()["total"] == 1


def test_a_file_under_a_kept_local_person_leaves_the_pile_too_kept_local(
    client: TestClient, tmp_path: Path
) -> None:
    """The file's own switch is off; the PERSON it is filed under is kept local. The pile reads the
    whole rule, the same one the door reads, so the answer is not waiting on anybody either."""
    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    person = a_person(client)
    _write(
        db_path(client),
        [("INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (asset, person))],
    )
    a_match(client, asset, box, {"title": "Their Name"})
    _keep_local(client, "person", person)

    assert client.get("/api/stash-boxes/matches").json()["total"] == 0


def test_a_selection_says_a_kept_local_file_is_kept_local_not_missing_kept_local(
    client: TestClient, tmp_path: Path
) -> None:
    """One of two files is kept local, and the reason says so rather than "could not find"."""
    from sift.kernel.reach import KEPT_LOCAL_LEFT_OUT, KEPT_LOCAL_LEFT_OUT_MANY

    sign_in(client, "admin")
    _matching_on(client)
    _a_library(client, tmp_path, AN_ASSET, ANOTHER_ASSET)
    _keep_local(client, "asset", ANOTHER_ASSET)

    answer = client.post("/api/stash-boxes/scan", json={"assets": [AN_ASSET, ANOTHER_ASSET]})

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["asked"], body["skipped"]) == (1, 1)
    assert (body["reason"], body["reason_many"]) == (KEPT_LOCAL_LEFT_OUT, KEPT_LOCAL_LEFT_OUT_MANY)
    assert (body["kept_local"], body["vault_locked"]) == (True, False)
    assert body["reason"] != OUT_OF_REACH


def test_a_press_naming_no_box_asks_what_is_set_up_and_all_asks_every_box_box_passed(
    client: TestClient, tmp_path: Path
) -> None:
    """Nothing is the setting and `all` is every box: the flyout's "All stash-boxes" and a press
    naming nothing are different asks. A named box is carried as named, and Auto-enrich on files
    carries the press's yes to a certain match."""
    from sift.slices.stash_boxes.jobs import STASH_ENRICH, STASH_SCAN
    from sift.slices.stash_boxes.settings import AUTO_BOX_KEY

    sign_in(client, "admin")
    _matching_on(client)
    # The boxes the presses below name, so each would ask somebody.
    _a_known_box(client, "StashDB", "https://stashdb.org/graphql")
    _a_known_box(client, "FansDB", "https://fansdb.cc/graphql")
    set_up = client.put("/api/settings", json={"values": {AUTO_BOX_KEY: "fansdb"}})
    assert set_up.status_code == 204, set_up.text
    person = a_person(client)
    for box in ("", "all", "stashdb"):
        answer = client.post(
            "/api/stash-boxes/enrich", json={"subject": "person", "ids": [person], "box": box}
        )
        assert answer.status_code == 200, answer.text
    assert [one["box"] for one in _payloads(client, STASH_ENRICH)] == ["fansdb", "", "stashdb"]

    _a_library(client, tmp_path, AN_ASSET)
    client.post("/api/stash-boxes/scan", json={"assets": [AN_ASSET], "auto": True})
    client.post("/api/stash-boxes/scan", json={"assets": [AN_ASSET], "box": "all"})
    assert [(one["box"], one["apply"]) for one in _payloads(client, STASH_SCAN)] == [
        ("fansdb", True),
        ("", False),
    ]


def test_a_person_a_confirmed_page_invents_is_queued_to_be_linked_by_the_boxs_own_id(
    client: TestClient, tmp_path: Path
) -> None:
    """The follow-up a confirmed page queues carries the box and its id for each row the page
    INVENTED, so the job links them by it and their cover comes with the link. A row that was
    already here is not given one: a credit sharing its name is not the box saying who they are."""
    from sift.slices.stash_boxes.jobs import STASH_ENRICH

    sign_in(client, "admin")
    box = add_a_box(client)
    (asset,) = a_library(client, tmp_path, "one")
    a_person(client, "Jane Roe")
    a_match(
        client,
        asset,
        box,
        {"people": ["Jane", "Jane Roe"]},
        refs={"person": {"Jane": "pf-1", "Jane Roe": "pf-2"}},
    )

    answer = client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [{"asset_id": asset, "box_id": box}],
            "create": [{"name": "Jane", "kind": "person"}],
        },
    )

    assert answer.status_code == 200, answer.text
    (queued,) = _payloads(client, STASH_ENRICH)
    subjects = queued["subjects"]
    assert isinstance(subjects, list)
    by_name = {one["name"]: one for one in subjects}
    assert (by_name["Jane"]["box"], by_name["Jane"]["remote_id"]) == (box, "pf-1")
    assert "remote_id" not in by_name["Jane Roe"]


def test_a_batch_leaves_out_the_ones_kept_local_and_a_selection_of_only_those_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    """Forty with three kept local is a batch of thirty-seven; a batch of NOTHING but kept-local
    rows is a refusal, because there is nothing left to watch."""
    from sift.slices.stash_boxes.service import KEPT_LOCAL

    sign_in(client, "admin")
    _matching_on(client)
    kept, asked = a_person(client, "Jane"), a_person(client, "Orla")
    _keep_local(client, "person", kept)
    _a_library(client, tmp_path, AN_ASSET)
    _keep_local(client, "asset", AN_ASSET)

    batch = client.post("/api/stash-boxes/enrich", json={"subject": "person", "ids": [kept, asked]})
    only_kept = client.post("/api/stash-boxes/scan", json={"assets": [AN_ASSET]})

    assert batch.status_code == 200, batch.text
    assert batch.json()["asked"] == 1
    assert only_kept.status_code == 409, only_kept.text
    assert only_kept.json()["detail"] == KEPT_LOCAL


def test_where_a_thing_stands_with_enrichment_is_read_for_the_four_kinds_and_no_other(
    client: TestClient,
) -> None:
    from sift.slices.stash_boxes.service import KEPT_LOCAL_WHY

    sign_in(client, "admin")
    person = a_person(client)
    _keep_local(client, "person", person)
    unseen = "01HX0000000000000000000999"

    state = client.get(f"/api/stash-boxes/enrichment/person/{person}")

    assert state.status_code == 200, state.text
    body = state.json()
    assert (body["kept_local"], body["refused"], body["why"]) == (True, True, KEPT_LOCAL_WHY)
    for asked in ("not-a-kind", "photo_set", "person"):
        missing = client.get(f"/api/stash-boxes/enrichment/{asked}/{unseen}")
        assert missing.status_code == 404, asked
    refused = client.put(
        f"/api/stash-boxes/enrichment/person/{unseen}/keep-local", json={"kept_local": True}
    )
    assert refused.status_code == 404


def test_a_row_that_goes_before_keep_local_is_written_is_a_404_and_not_a_decision(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seen by the scoped read, gone by the write: the reply must not describe a decision that
    could not be stored."""
    sign_in(client, "admin")
    person = a_person(client)
    service = client.app.state.stash_boxes  # type: ignore[attr-defined]

    async def gone(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(service, "set_kept_local", gone)

    answer = client.put(
        f"/api/stash-boxes/enrichment/person/{person}/keep-local", json={"kept_local": True}
    )

    assert answer.status_code == 404


# --- a creator's picture, after a confirm files their username -------------------------------------


def test_a_confirm_that_files_a_creator_queues_the_boxs_picture_of_them(
    client: TestClient, tmp_path: Path
) -> None:
    """Queued, never fetched in the request: the confirm answers without waiting on a box."""
    from sift.slices.stash_boxes.jobs import STASH_CREATOR_PICTURE

    sign_in(client, "admin")
    box = add_a_box(client)
    adapter = stand_in(client)
    (asset,) = a_library(client, tmp_path, "one")
    account = {"site": "OnlyFans", "handle": "quillmoss", "url": "https://onlyfans.com/quillmoss"}
    a_match(
        client, asset, box, {"accounts": [account]}, refs={"username": {"quillmoss": "studio-9"}}
    )

    answer = client.post(
        "/api/stash-boxes/matches/apply",
        json={
            "matches": [{"asset_id": asset, "box_id": box}],
            "create": [{"name": "OnlyFans", "kind": "site"}],
        },
    )

    assert answer.status_code == 200, answer.text
    assert adapter.asked == 0, "the request itself asks the box nothing"
    assert _payloads(client, STASH_CREATOR_PICTURE) == [
        {
            "box": box,
            "studio": "studio-9",
            "asset_id": asset,
            "site": "OnlyFans",
            "username": "quillmoss",
            "address": "https://onlyfans.com/quillmoss",
        }
    ]
