# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-box endpoints, over HTTP, against a real application: configuring the boxes and asking them by hand.

Nothing here reaches a network: the adapter on the running application is replaced, so what is
asserted is which questions the routes would ask and what they hand back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.records import FoundRecord, Subject
from sift.slices.stash_boxes.tests.api_support import (
    AN_ADDRESS,
    _keep_local,
    _write,
    a_library,
    add_a_box,
    db_path,
    sign_in,
    stand_in,
)
from sift.testing.library import a_png

pytestmark = pytest.mark.integration


def test_a_configured_box_reports_that_it_has_a_key_and_never_the_key(client: TestClient) -> None:
    """A key is write-only in this interface: typed in, sealed, and never rendered back."""
    sign_in(client, "admin")
    add_a_box(client, api_key="the-real-key")

    body = client.get("/api/stash-boxes").text

    assert '"has_key":true' in body.replace(" ", "")
    assert "the-real-key" not in body


def test_a_guest_is_refused_every_stash_box_endpoint(client: TestClient) -> None:
    """Reads included. These are the routes that make Sift send a request to somebody else's
    service with a stored key."""
    sign_in(client, "admin")
    box_id = add_a_box(client)
    sign_in(client, "guest")

    assert client.get("/api/stash-boxes").status_code == 403
    assert client.get("/api/stash-boxes/look-up?term=anybody").status_code == 403
    assert client.post(f"/api/stash-boxes/{box_id}/check").status_code == 403


def test_a_box_that_is_not_there_is_a_404_rather_than_a_quiet_success(client: TestClient) -> None:
    sign_in(client, "admin")
    missing = "01HX0000000000000000000009"

    assert client.put(f"/api/stash-boxes/{missing}", json={"enabled": False}).status_code == 404
    assert client.delete(f"/api/stash-boxes/{missing}").status_code == 404
    assert client.post(f"/api/stash-boxes/{missing}/check").status_code == 404


def test_mentioning_one_field_does_not_blank_the_others(client: TestClient) -> None:
    """The trap this route is written against: a model of optional fields read attribute by
    attribute is a full-row writer, and would switch a box off for mentioning its pace."""
    sign_in(client, "admin")
    box_id = add_a_box(client)
    client.put(f"/api/stash-boxes/{box_id}", json={"route": "a-tunnel"})

    client.put(f"/api/stash-boxes/{box_id}", json={"requests_per_minute": 60})

    box = client.get("/api/stash-boxes").json()["boxes"][0]
    assert box["enabled"] is True
    assert box["route"] == "a-tunnel"
    assert box["requests_per_minute"] == 60


def test_the_look_up_shows_what_came_back_and_says_which_box_said_it(client: TestClient) -> None:
    sign_in(client, "admin")
    add_a_box(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id="box",
            remote_id="1",
            subject=Subject.PERSON,
            name="A Name",
            fields={"country": "US"},
            confidence=0.5,
        )
    ]

    body = client.get("/api/stash-boxes/look-up?term=a name").json()

    assert len(body["answers"]) == 1
    said = body["answers"][0]
    assert said["box_name"] == "StashDB"
    assert [one["name"] for one in said["records"]] == ["A Name"]
    assert said["records"][0]["source_name"] == "StashDB"


def test_a_box_that_cannot_be_reached_is_a_sentence_beside_the_others(client: TestClient) -> None:
    """A third-party service being down is a fact to show, not a failure of this request. A 502
    here would read as Sift being broken."""
    sign_in(client, "admin")
    add_a_box(client)
    stand_in(client).refuse = "StashDB did not answer in time."

    answer = client.get("/api/stash-boxes/look-up?term=a name")

    assert answer.status_code == 200
    assert answer.json()["answers"][0]["problem"] == "StashDB did not answer in time."


def test_the_check_answers_200_whether_or_not_the_box_did(client: TestClient) -> None:
    sign_in(client, "admin")
    box_id = add_a_box(client)
    adapter = stand_in(client)

    good = client.post(f"/api/stash-boxes/{box_id}/check")
    adapter.refuse = "not authorized"
    bad = client.post(f"/api/stash-boxes/{box_id}/check")

    assert good.status_code == 200 and good.json()["ok"] is True
    assert bad.status_code == 200 and bad.json() == {"ok": False, "problem": "not authorized"}


def test_with_nothing_configured_a_look_up_asks_nobody_and_answers_empty(
    client: TestClient,
) -> None:
    """Sift works with no network and with nothing set up. This is not an error state."""
    sign_in(client, "admin")

    answer = client.get("/api/stash-boxes/look-up?term=anybody")

    assert answer.status_code == 200
    assert answer.json() == {"answers": []}


def test_recognising_a_file_nobody_may_see_is_a_404_before_anything_is_sent(
    client: TestClient,
) -> None:
    """The scoped read comes first, so a file this user may not be shown does not have its
    fingerprints sent to three third-party services."""
    sign_in(client, "admin")
    add_a_box(client)
    adapter = stand_in(client)

    answer = client.get("/api/stash-boxes/recognise/01HX0000000000000000000009")

    assert answer.status_code == 404
    assert adapter.asked == 0


def test_a_picture_from_a_box_nobody_configured_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")

    answer = client.get(
        "/api/stash-boxes/01HX0000000000000000000009/picture",
        params={"url": "https://stashdb.example/a.png"},
    )

    assert answer.status_code == 404


def test_a_picture_that_will_not_come_is_a_404_rather_than_an_error(client: TestClient) -> None:
    """A missing thumbnail is a missing thumbnail, not an error on a screen somebody is reading.
    The seeded box's address resolves nowhere, so nothing here reaches a network."""
    sign_in(client, "admin")
    box = add_a_box(client)

    answer = client.get(
        f"/api/stash-boxes/{box}/picture", params={"url": "https://stashdb.example/a.png"}
    )

    assert answer.status_code == 404


# --- managing a box -------------------------------------------------------------------------------


def test_a_box_is_switched_off_and_paced_and_routed_without_touching_its_key(
    client: TestClient,
) -> None:
    """Three fields, each optional. A write that says nothing about one leaves it as it was, which
    is what lets a screen edit one control without carrying the whole row."""
    sign_in(client, "admin")
    box = add_a_box(client, api_key="the-real-key")

    edited = client.put(
        f"/api/stash-boxes/{box}",
        json={"enabled": False, "requests_per_minute": 30, "route": "tunnel"},
    )

    assert edited.status_code == 204, edited.text
    held = next(one for one in client.get("/api/stash-boxes").json()["boxes"] if one["id"] == box)
    assert held["enabled"] is False
    assert held["requests_per_minute"] == 30
    assert held["route"] == "tunnel"
    assert held["has_key"] is True, "the key is untouched by an edit that never mentions it"


def test_replacing_a_key_throws_away_what_the_old_one_was_told(client: TestClient) -> None:
    """A new key can mean a different account, and an answer given to the old one is not evidence
    about the new."""
    sign_in(client, "admin")
    box = add_a_box(client)
    adapter = stand_in(client)
    adapter.records = [
        FoundRecord(
            source_id="box", remote_id="r1", subject=Subject.PERSON, name="Jane", confidence=0.5
        )
    ]
    client.get("/api/stash-boxes/look-up?term=jane")
    assert adapter.asked == 1

    replaced = client.put(f"/api/stash-boxes/{box}/key", json={"api_key": "a-different-key"})

    assert replaced.status_code == 204, replaced.text
    client.get("/api/stash-boxes/look-up?term=jane")
    assert adapter.asked == 2, "the cached answer went with the key it was given to"


def test_throwing_away_what_a_box_said_makes_the_next_question_a_real_one(
    client: TestClient,
) -> None:
    """The refresh control."""
    sign_in(client, "admin")
    box = add_a_box(client)
    adapter = stand_in(client)
    client.get("/api/stash-boxes/look-up?term=jane")
    client.get("/api/stash-boxes/look-up?term=jane")
    assert adapter.asked == 1

    cleared = client.delete(f"/api/stash-boxes/{box}/answers")

    assert cleared.status_code == 204, cleared.text
    client.get("/api/stash-boxes/look-up?term=jane")
    assert adapter.asked == 2


def test_removing_a_box_takes_what_it_said_with_it(client: TestClient) -> None:
    """A cached answer outliving its source is a fact with no provenance."""
    sign_in(client, "admin")
    box = add_a_box(client)

    removed = client.delete(f"/api/stash-boxes/{box}")

    assert removed.status_code == 204, removed.text
    assert client.get("/api/stash-boxes").json()["boxes"] == []


def test_every_route_that_takes_a_box_answers_404_for_one_nobody_configured(
    client: TestClient,
) -> None:
    """The same answer to "there is no such box" and "not for you", so none of these becomes a way
    to ask which boxes an install has."""
    sign_in(client, "admin")
    gone = "01HX0000000000000000000009"

    assert client.put(f"/api/stash-boxes/{gone}", json={"enabled": False}).status_code == 404
    assert client.put(f"/api/stash-boxes/{gone}/key", json={"api_key": "k"}).status_code == 404
    assert client.delete(f"/api/stash-boxes/{gone}").status_code == 404
    assert client.delete(f"/api/stash-boxes/{gone}/answers").status_code == 404
    assert client.post(f"/api/stash-boxes/{gone}/check").status_code == 404


# --- recognising one file --------------------------------------------------------------------------


def test_a_file_with_no_fingerprint_asks_nobody_and_answers_empty(
    client: TestClient, tmp_path: Path
) -> None:
    """A photograph has no video fingerprint and a clip that has not been through the catch-up pass
    has none yet. Nothing to ask means nothing was asked."""
    sign_in(client, "admin")
    add_a_box(client)
    adapter = stand_in(client)
    (asset,) = a_library(client, tmp_path, "one")

    answer = client.get(f"/api/stash-boxes/recognise/{asset}")

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"answers": []}
    assert adapter.asked == 0


def test_a_file_with_a_fingerprint_is_asked_about(client: TestClient, tmp_path: Path) -> None:
    sign_in(client, "admin")
    add_a_box(client)
    adapter = stand_in(client)
    (asset,) = a_library(client, tmp_path, "one")
    _write(
        db_path(client),
        [("UPDATE assets SET oshash = 'abc' WHERE id = ?", (asset,))],
    )

    answer = client.get(f"/api/stash-boxes/recognise/{asset}")

    assert answer.status_code == 200, answer.text
    assert adapter.asked == 1


def test_a_write_that_needs_a_key_is_refused_while_the_keys_are_sealed(
    client: TestClient,
) -> None:
    """A key is sealed under the master key, which exists only while somebody is signed in with
    their password. A session resumed from a cookie after a restart has none yet, and 409 with a
    sentence saying what to do is the honest answer, where a 500 is not."""
    user_id = sign_in(client, "admin")
    client.app.state.master_keys.forget(user_id)  # type: ignore[attr-defined]

    refused = client.post(
        "/api/stash-boxes",
        json={"name": "StashDB", "endpoint": AN_ADDRESS, "api_key": "a-key"},
    )

    assert refused.status_code == 409
    assert "password" in refused.json()["detail"]


def test_a_picture_a_box_does_hand_over_is_served_from_sifts_own_address(
    client: TestClient,
) -> None:
    """Sift's pages run under `img-src 'self'`, which allows no remote host: that is what makes
    injected CSS harmless, because there is nowhere for it to send anything. Widening it for
    somebody else's domain would give that away, so the bytes come through here."""
    sign_in(client, "admin")
    box = add_a_box(client)
    adapter = stand_in(client)

    async def picture(box_: object, url: str, *, vector: bool = False) -> tuple[bytes, str]:
        return (a_png(), "image/png")

    adapter.picture = picture  # type: ignore[attr-defined]

    answer = client.get(
        f"/api/stash-boxes/{box}/picture", params={"url": "https://stashdb.example/a.png"}
    )

    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"] == "image/png"
    assert answer.headers["cache-control"] == "private, max-age=604800"
    assert answer.content == a_png()


def test_editing_the_second_of_two_boxes_answers_about_that_one(client: TestClient) -> None:
    """The edited row is found among the others rather than assumed to be the first. A list of one
    is the shape that hides this, and every install starts as one."""
    sign_in(client, "admin")
    first = add_a_box(client)
    second = client.post(
        "/api/stash-boxes",
        json={"name": "FansDB", "endpoint": "https://fansdb.example/graphql", "api_key": None},
    ).json()["id"]

    edited = client.put(f"/api/stash-boxes/{second}", json={"requests_per_minute": 30})

    assert edited.status_code == 204, edited.text
    boxes = {one["id"]: one for one in client.get("/api/stash-boxes").json()["boxes"]}
    assert boxes[second]["requests_per_minute"] == 30
    assert boxes[first]["requests_per_minute"] != 30


def test_recognising_a_file_kept_local_is_a_409_in_the_doors_words_kept_local(
    client: TestClient, tmp_path: Path
) -> None:
    """Not a 500 "Something went wrong": a refusal that worked must not be reported as a crash."""
    from sift.slices.stash_boxes.service import KEPT_LOCAL

    sign_in(client, "admin")
    add_a_box(client)
    adapter = stand_in(client)
    (asset,) = a_library(client, tmp_path, "one")
    _write(db_path(client), [("UPDATE assets SET oshash = 'abc' WHERE id = ?", (asset,))])
    _keep_local(client, "asset", asset)

    answer = client.get(f"/api/stash-boxes/recognise/{asset}")

    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"] == KEPT_LOCAL
    assert adapter.asked == 0
