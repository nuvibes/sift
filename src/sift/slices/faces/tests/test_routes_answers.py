# SPDX-License-Identifier: AGPL-3.0-or-later
"""The answers given on Faces, over HTTP: one pile and part of it, agreeing with proposals, the
scopes of a Yes and a No, merging and splitting, and the group a face is waiting in."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Role, Viewer
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobQueue
from sift.slices.faces import jobs as faces_jobs
from sift.slices.faces import router_answers, tuning
from sift.slices.faces.jobs_agree import FACE_AGREE
from sift.slices.faces.service import FACE_STARTERS, FaceService
from sift.slices.faces.tests import test_routes
from sift.slices.faces.tests.test_routes import (
    _ATTRIBUTE,
    _EPOCH,
    _INSERT_ASSET,
    _INSERT_LOCATION,
    _INSERT_PERSON,
    NEVER_EXISTED,
    Scene,
    data_dir,
    db_path,
    faces_of,
    make_pile,
    queued_types,
    sign_in,
    turn_on,
    write,
)
from sift.slices.faces.weights import pairing
from sift.testing.library import seed_face
from sift.testing.settings import set_app_setting

pytestmark = pytest.mark.integration

#: The fixtures this file shares with the files they are defined in, found here by name.
app = test_routes.app
client = test_routes.client
scene = test_routes.scene


# --- one pile on its own, and answering for part of it -------------------------------------------
#
# The list shows a handful of each pile because it draws hundreds of them, which is enough to
# recognise a pile and not enough to decide about one. The grouping is tuned to split rather than
# merge, so a pile is usually one person and occasionally one person plus a stranger, and an
# answer only about the whole thing would be the wrong shape for that.


def test_a_pile_has_a_page_of_its_own_with_its_faces_on_it(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    pile = make_pile(client, scene)
    sign_in(client)

    answer = client.get(f"/api/faces/groups/{pile}")

    assert answer.status_code == 200
    body = answer.json()
    assert body["total"] == 1
    assert [face["track_id"] for face in body["group"]["faces"]] == [scene.track]


def test_a_pile_nobody_may_see_has_no_page_rather_than_an_empty_one(
    client: TestClient, scene: Scene
) -> None:
    """An empty page for a pile that demonstrably exists is itself an answer about what is being
    kept back.

    Asked as an admin with the file hidden from themselves, which is the only way to reach this
    state on a surface only an admin can open at all."""
    turn_on(client)
    pile = make_pile(client, scene)
    admin = sign_in(client)
    scene.hide(client, "asset", scene.asset, admin)

    assert client.get(f"/api/faces/groups/{pile}").status_code == 404


def test_a_pile_that_does_not_exist_is_the_same_miss_as_one_that_is_hidden(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    sign_in(client)

    assert client.get(f"/api/faces/groups/{NEVER_EXISTED}").status_code == 404


def test_a_piles_page_is_refused_while_recognition_is_off(client: TestClient, scene: Scene) -> None:
    pile = make_pile(client, scene)
    sign_in(client)

    assert client.get(f"/api/faces/groups/{pile}").status_code == 409


def test_naming_some_faces_attributes_exactly_those_faces(client: TestClient, scene: Scene) -> None:
    turn_on(client)
    make_pile(client, scene)
    sign_in(client)

    answer = client.post(
        "/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person}
    )

    assert answer.status_code == 200
    assert answer.json()["changed"] == 1
    assert answer.json()["person_id"] == scene.person


def test_naming_faces_under_a_new_name_creates_that_person_once(
    client: TestClient, scene: Scene
) -> None:
    """A person is a row the whole library is organised by. Pressing this twice must not produce
    two of somebody."""
    turn_on(client)
    make_pile(client, scene)
    sign_in(client)

    first = client.post("/api/faces/name", json={"track_ids": [scene.track], "name": "Grace"})
    assert first.status_code == 200
    made = first.json()["person_id"]

    again = client.post("/api/faces/name", json={"track_ids": [scene.track], "name": "grace"})

    assert again.status_code == 200
    assert again.json()["person_id"] == made
    # And the answer names her in the row's own spelling, which the screen's sentence says.
    assert (first.json()["person_name"], again.json()["person_name"]) == ("Grace", "Grace")


def test_naming_faces_as_somebody_this_account_cannot_see_is_a_404(
    client: TestClient, scene: Scene
) -> None:
    """The lock on the OTHER end of the call, and the one a test of the faces would never reach.

    Every face is checked against the viewer above; this checks the PERSON they are being named as.
    Without it, a user could learn that a concealed person exists by naming a face as them and
    reading which answer came back, and could quietly file faces under somebody they have never
    been told about. A 404 rather than a 403, because a 403 would confirm the row is there.
    """
    turn_on(client)
    make_pile(client, scene)
    sign_in(client)

    answer = client.post(
        "/api/faces/name",
        json={"track_ids": [scene.track], "person_id": "01HX0000000000000000000099"},
    )

    assert answer.status_code == 404


def test_naming_a_face_can_offer_the_rest_of_its_group_as_the_same_person(
    client: TestClient, scene: Scene
) -> None:
    """The other half of the route, reached with `whole_group`.

    Naming one face out of twenty and leaving nineteen in the pile ignores work somebody has
    already done, so the rest of the group is OFFERED as the same person, suggested rather than
    confirmed, because nobody has looked at them. What is asserted here is the route reaching that
    call at all; what the two halves mean is proved against the service next door.
    """
    turn_on(client)
    make_pile(client, scene)
    sign_in(client)

    answer = client.post(
        "/api/faces/name",
        json={"track_ids": [scene.track], "person_id": scene.person, "whole_group": True},
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["changed"] == 1
    assert answer.json()["person_id"] == scene.person


def test_naming_faces_needs_exactly_one_of_a_person_and_a_name(
    client: TestClient, scene: Scene
) -> None:
    """Both, resolved by precedence, would be a rule that surprises somebody whichever way round it
    was written."""
    turn_on(client)
    sign_in(client)

    both = client.post(
        "/api/faces/name",
        json={"track_ids": [scene.track], "person_id": scene.person, "name": "Grace"},
    )
    neither = client.post("/api/faces/name", json={"track_ids": [scene.track]})
    nobody = client.post("/api/faces/name", json={"track_ids": [], "person_id": scene.person})
    blank = client.post("/api/faces/name", json={"track_ids": [scene.track], "name": "   "})

    assert both.status_code == 400
    assert neither.status_code == 400
    assert nobody.status_code == 400
    assert blank.status_code == 400


def test_naming_a_face_from_a_file_this_account_may_not_see_names_nothing_and_says_so(
    client: TestClient, scene: Scene
) -> None:
    """Checked before anything is written, and about every face named.

    A partial success is fine when the reply SAYS which part happened, so the reply is 200 with a
    count rather than a 404 for the whole call: a refusal would leave forty faces of which one sits
    in the vault all unnamed, and explain nothing. The face out of reach is still not named.
    `vault_locked` is what lets the screen offer to unlock, which is the whole reason this case is
    told apart from an id that does not exist.

    Reached as an admin with the file hidden, because a guest never gets this far: curation is
    admin-only and is refused at the door. That refusal is checked separately below.
    """
    turn_on(client)
    admin = sign_in(client, "admin")
    scene.hide(client, "asset", scene.asset, admin)

    answer = client.post(
        "/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person}
    )

    assert answer.status_code == 200
    body = answer.json()
    assert body["changed"] == 0
    assert body["skipped"] == 1
    assert body["vault_locked"] is True, "the screen has no way to offer to unlock"


def test_deciding_about_faces_at_all_is_an_admin_s(client: TestClient, scene: Scene) -> None:
    """Naming and setting aside change what the library says about a person, which is curation,
    so a guest browsing a shared folder is refused at the door rather than scoped inside."""
    turn_on(client)
    sign_in(client, "guest", who="stranger")

    named = client.post(
        "/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person}
    )
    aside = client.post("/api/faces/set-aside", json={"track_ids": [scene.track]})

    assert named.status_code == 403
    assert aside.status_code == 403


def test_naming_faces_as_somebody_this_account_cannot_see_is_refused(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    user = sign_in(client)
    scene.hide(client, "person", scene.person, user)

    answer = client.post(
        "/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person}
    )

    assert answer.status_code == 404


def test_naming_faces_is_refused_while_recognition_is_off(client: TestClient, scene: Scene) -> None:
    sign_in(client)

    answer = client.post(
        "/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person}
    )

    assert answer.status_code == 409


def test_setting_some_faces_aside_moves_them_into_a_pile_of_their_own(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    make_pile(client, scene)
    sign_in(client)

    answer = client.post("/api/faces/set-aside", json={"track_ids": [scene.track]})

    assert answer.status_code == 200
    assert answer.json()["changed"] == 1
    ignored = client.get("/api/faces/groups", params={"status": "ignored"}).json()
    assert ignored["total"] == 1


def test_setting_aside_needs_faces_and_a_viewer_who_may_touch_them(
    client: TestClient, scene: Scene
) -> None:
    """No faces named is a 400; a face out of reach is SKIPPED and counted.

    Not a refusal of the whole call: a partial success saying nothing is worse than none, and one
    that says which part happened is not. What the assertions below keep is the part that matters:
    a face the viewer may not touch is NEVER acted on. What they add is that the reply says so, and that the vault says which of the two reasons it was, because
    only one of them is a lock the person holds the key to.
    """
    turn_on(client)
    admin = sign_in(client)

    assert client.post("/api/faces/set-aside", json={"track_ids": []}).status_code == 400

    unknown = client.post("/api/faces/set-aside", json={"track_ids": [NEVER_EXISTED]})
    assert unknown.status_code == 200
    assert (unknown.json()["changed"], unknown.json()["skipped"]) == (0, 1)
    # A made-up id is not a lock: no PIN would produce it, so no Unlock is offered for it.
    assert unknown.json()["vault_locked"] is False

    scene.hide(client, "asset", scene.asset, admin)
    hidden = client.post("/api/faces/set-aside", json={"track_ids": [scene.track]})
    assert hidden.status_code == 200
    assert (hidden.json()["changed"], hidden.json()["skipped"]) == (0, 1)
    assert hidden.json()["vault_locked"] is True


def test_setting_aside_is_refused_while_recognition_is_off(
    client: TestClient, scene: Scene
) -> None:
    sign_in(client)

    assert client.post("/api/faces/set-aside", json={"track_ids": [scene.track]}).status_code == 409


def test_removing_faces_takes_them_away_for_good(client: TestClient, scene: Scene) -> None:
    """The one decision here with no undo, so it is the only one whose result is a smaller library
    rather than a differently-labelled one."""
    turn_on(client)
    sign_in(client)

    answer = client.post("/api/faces/remove", json={"track_ids": [scene.track]})

    assert answer.status_code == 200
    assert answer.json()["changed"] == 1
    assert client.get(f"/api/assets/{scene.asset}/faces").json() == []


def test_removing_faces_is_an_admin_s(client: TestClient, scene: Scene) -> None:
    """It changes the library for everybody and cannot be taken back, so a guest is refused at the
    door rather than scoped inside."""
    turn_on(client)
    sign_in(client, "guest", who="stranger")

    assert client.post("/api/faces/remove", json={"track_ids": [scene.track]}).status_code == 403


def test_removing_needs_faces_and_a_viewer_who_may_touch_them(
    client: TestClient, scene: Scene
) -> None:
    """No faces named is a 400; a face out of reach is SKIPPED and counted.

    Not a refusal of the whole call: a partial success saying nothing is worse than none, and one
    that says which part happened is not. What the assertions below keep is the part that matters:
    a face the viewer may not touch is NEVER acted on. What they add is that the reply says so, and that the vault says which of the two reasons it was, because
    only one of them is a lock the person holds the key to.
    """
    turn_on(client)
    admin = sign_in(client)

    assert client.post("/api/faces/remove", json={"track_ids": []}).status_code == 400

    unknown = client.post("/api/faces/remove", json={"track_ids": [NEVER_EXISTED]})
    assert unknown.status_code == 200
    assert (unknown.json()["changed"], unknown.json()["skipped"]) == (0, 1)
    # A made-up id is not a lock: no PIN would produce it, so no Unlock is offered for it.
    assert unknown.json()["vault_locked"] is False

    scene.hide(client, "asset", scene.asset, admin)
    hidden = client.post("/api/faces/remove", json={"track_ids": [scene.track]})
    assert hidden.status_code == 200
    assert (hidden.json()["changed"], hidden.json()["skipped"]) == (0, 1)
    assert hidden.json()["vault_locked"] is True


def test_removing_is_refused_while_recognition_is_off(client: TestClient, scene: Scene) -> None:
    sign_in(client)

    assert client.post("/api/faces/remove", json={"track_ids": [scene.track]}).status_code == 409


def test_what_has_been_recognized_lately_is_listed(client: TestClient, scene: Scene) -> None:
    """The counterpart of the piles: those are the questions, this is what has been answered."""
    turn_on(client)
    sign_in(client)
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})

    answer = client.get(f"/api/faces/identified/people/{scene.person}")

    assert answer.status_code == 200
    assert [item["track_id"] for item in answer.json()["items"]] == [scene.track]
    assert answer.json()["total"] == 1


def test_one_persons_waiting_faces_survive_everybody_elses_activity(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person's own numbers must not depend on how busy the rest of the library has been.

    The list behind a person's page is narrowed by person before it is bounded. Taken as the
    library-wide recency slice with everybody else dropped afterwards, a bound meant for "what has
    Sift recognized lately" becomes a bound on how many OTHER people can be attributed before
    somebody's own page goes quiet: on a busy library one person can hold most of the slice, and
    nearly everybody else with attributed faces is undercounted, some of them to nothing at all.

    The cap is patched to one rather than seeded past, because the number is not what is being
    tested: what is being tested is what the cap is a cap ON. At one, the two arrangements give
    opposite answers: narrowed by person, hers is the only row there is and it survives; sliced
    from the library, the newer decision about somebody else is the only row and hers is gone.


    Ordered by `id DESC` here, which is what the statement falls back to when nothing has recorded
    WHEN a decision was made, so the later-built scene is the newer one, exactly as a later
    attribution would be.
    """
    turn_on(client)
    sign_in(client)
    monkeypatch.setattr(tuning, "PERSON_FACES_AT_MOST", 1)

    hers = Scene(client, tmp_path / "hers", name="hers")
    hers.attribute(client, how="suggested")
    somebody_else = Scene(client, tmp_path / "theirs", name="theirs")
    somebody_else.attribute(client, how="matched")

    answer = client.get(f"/api/faces/identified/people/{hers.person}?waiting=true")

    assert answer.status_code == 200
    assert answer.json()["total"] == 1
    assert [item["track_id"] for item in answer.json()["items"]] == [hers.track]


def test_a_card_says_which_faces_sift_attached_and_which_somebody_agreed_to(
    client: TestClient, scene: Scene
) -> None:
    """ "28 faces" reads the same whether somebody agreed to every one or to none.

    The screen exists because a feature that puts people's names on files without being asked owes
    somebody a place to see what it concluded, down to which of the faces on a card are the ones it
    concluded rather than ones a person agreed to.

    The percentage belongs to the MATCHED ones alone. A confirmed face was decided by a person
    rather than by arithmetic, so a confidence beside it would be describing the wrong thing.
    """
    turn_on(client)
    sign_in(client)
    agreed = new_id()
    seed_face(db_path(client), agreed, scene.asset, data_dir=data_dir(client))
    scene.attribute(client, how="matched")
    write(db_path(client), [(_ATTRIBUTE, (scene.person, "confirmed", agreed))])

    (card,) = client.get("/api/faces/identified/people").json()["people"]

    assert card["size"] == 2
    assert card["matched"] == 1
    assert card["confirmed"] == 1
    assert card["surest"] == pytest.approx(0.9)


def test_the_identified_wall_narrows_to_one_kind_of_decision(
    client: TestClient, tmp_path: Path
) -> None:
    """The filter narrows on the SERVER, so the count and the contents describe one set.

    Narrowed after the page was taken it would read the wrong rows, and a person with nothing of
    the wanted kind would still be counted: a wall saying two people over one card.
    """
    turn_on(client)
    sign_in(client)
    hers = Scene(client, tmp_path / "hers", name="hers")
    hers.attribute(client, how="matched")
    theirs = Scene(client, tmp_path / "theirs", name="theirs")
    theirs.attribute(client, how="confirmed")

    both = client.get("/api/faces/identified/people").json()
    only = client.get("/api/faces/identified/people", params={"attribution": "matched"}).json()

    assert both["total"] == 2
    assert only["total"] == 1
    assert [card["person_id"] for card in only["people"]] == [hers.person]
    assert only["people"][0]["confirmed"] == 0


def test_the_review_list_puts_a_persons_proposals_before_the_groups(
    client: TestClient, scene: Scene
) -> None:
    """One list, ordered by how much one press settles.

    A proposal settles every face standing for that person and teaches Sift what they look like; a
    group settles itself and teaches nothing until it is named. Three walls could not say which
    held the answer worth giving first.
    """
    turn_on(client)
    sign_in(client, "admin")
    scene.attribute(client, how="suggested")
    pile = make_pile(client, scene)
    # A group big enough to be listed at all: five is the floor.
    for _extra in range(5):
        track = new_id()
        seed_face(db_path(client), track, scene.asset, data_dir=data_dir(client))
        write(db_path(client), [("UPDATE face_tracks SET pile_id = ? WHERE id = ?", (pile, track))])

    answer = client.get("/api/faces/to-check").json()

    assert [item["kind"] for item in answer["items"]] == ["person", "group"]
    assert answer["total"] == 2
    assert answer["items"][0]["id"] == scene.person


def test_the_review_list_holds_back_the_small_groups_and_says_how_many(
    client: TestClient, scene: Scene
) -> None:
    """The stranger floor, over the wire.

    A group of fewer than five faces is not listed and not counted; how many there are is the one
    line at the foot of the list, and it is answered here rather than by a second request so the
    line and the list are one reading of the library.
    """
    turn_on(client)
    sign_in(client, "admin")
    make_pile(client, scene)

    answer = client.get("/api/faces/to-check").json()
    opened = client.get("/api/faces/to-check", params={"show": "small"}).json()

    assert answer["items"] == []
    assert answer["total"] == 0
    assert answer["small_groups"] == 1
    # And nothing is unreachable: the line opens them, as the same cards.
    assert [item["kind"] for item in opened["items"]] == ["group"]
    assert opened["total"] == 1


def _a_group_of(client: TestClient, scene: Scene, faces: int) -> str:
    """A group of `faces` faces in the scene's file, open and waiting for a name."""
    pile = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
                "VALUES (?, 'open', X'0000803F', ?, 0, 0)",
                (pile, faces),
            )
        ],
    )
    for _one in range(faces):
        track = new_id()
        seed_face(db_path(client), track, scene.asset, data_dir=data_dir(client))
        write(db_path(client), [("UPDATE face_tracks SET pile_id = ? WHERE id = ?", (pile, track))])
    return pile


def test_the_review_list_opens_from_a_group_at_the_page_that_group_is_on(
    client: TestClient, scene: Scene
) -> None:
    """`from` on Unnamed faces: the crumb and the browser's Back come back to the page that was
    being worked. The group is found in the tab's own list (groups above the floor, largest
    first) and the page starts at it, saying which offset that is so the pager can say so too."""
    turn_on(client)
    sign_in(client, "admin")
    _a_group_of(client, scene, 7)
    second = _a_group_of(client, scene, 6)

    answer = client.get(
        "/api/faces/to-check", params={"kind": "group", "limit": 1, "from": second}
    ).json()

    assert answer["offset"] == 1
    assert [item["id"] for item in answer["items"]] == [second]
    assert answer["total"] == 2


def test_the_review_list_serves_the_top_for_a_from_it_cannot_place(
    client: TestClient, scene: Scene
) -> None:
    """A group answered since, or an id nobody minted: the top, never a refusal, so `from` is
    not a way of asking whether something is there. And a small group is not on the list above
    the floor, while `?show=small` finds it in its own list."""
    turn_on(client)
    sign_in(client, "admin")
    largest = _a_group_of(client, scene, 7)
    small = make_pile(client, scene)

    unknown = client.get("/api/faces/to-check", params={"kind": "group", "from": new_id()}).json()
    below = client.get("/api/faces/to-check", params={"kind": "group", "from": small}).json()
    among = client.get("/api/faces/to-check", params={"show": "small", "from": small}).json()

    assert unknown["offset"] == 0
    assert [item["id"] for item in unknown["items"]] == [largest]
    assert below["offset"] == 0
    assert among["offset"] == 0
    assert [item["id"] for item in among["items"]] == [small]


def test_the_suggestions_tab_opens_from_a_person_at_the_page_that_person_is_on(
    client: TestClient, scene: Scene
) -> None:
    """`from` on Suggestions names a PERSON (the row's own id), and the page starts at them in
    the tab's order, surest proposal first, so the tab does not come back at the top after every
    look at a person. A person asked for on the groups tab is nowhere there: the top."""
    turn_on(client)
    sign_in(client, "admin")
    surer, surer_face = new_id(), new_id()
    write(db_path(client), [(_INSERT_PERSON, (surer, "Esme Wrenfield"))])
    seed_face(db_path(client), surer_face, scene.asset, data_dir=data_dir(client))
    write(
        db_path(client),
        [
            (
                "UPDATE face_tracks SET person_id = ?, confidence = ?, attribution = 'suggested' "
                "WHERE id = ?",
                (person, sure, track),
            )
            for person, sure, track in ((surer, 0.95, surer_face), (scene.person, 0.6, scene.track))
        ],
    )

    answer = client.get(
        "/api/faces/to-check", params={"kind": "person", "limit": 1, "from": scene.person}
    ).json()
    elsewhere = client.get(
        "/api/faces/to-check", params={"kind": "group", "from": scene.person}
    ).json()

    assert answer["offset"] == 1
    assert [item["id"] for item in answer["items"]] == [scene.person]
    assert answer["total"] == 2
    assert elsewhere["offset"] == 0


def _a_file_filed_under(client: TestClient, scene: Scene, revision: str) -> tuple[str, str]:
    """A file a folder filed under the scene's person, with one face Sift recognized as somebody
    else: what makes a disagreement."""
    asset, track, elsewhere = new_id(), new_id(), new_id()
    write(
        db_path(client),
        [
            (_INSERT_ASSET, (asset, f"digest-{asset}", 1, f"{asset}.mp4", _EPOCH)),
            (
                _INSERT_LOCATION,
                (
                    new_id(),
                    asset,
                    scene.root,
                    scene.folder,
                    f"clips/{asset}.mp4",
                    f"{asset}.mp4",
                    _EPOCH,
                    _EPOCH,
                ),
            ),
            (
                "INSERT INTO asset_people (asset_id, person_id, source, decided_at) "
                "VALUES (?, ?, 'folder', 0)",
                (asset, scene.person),
            ),
        ],
    )
    seed_face(db_path(client), track, asset, data_dir=data_dir(client))
    write(
        db_path(client),
        [
            ("UPDATE face_scans SET recognizer = ? WHERE asset_id = ?", (revision, asset)),
            (_INSERT_PERSON, (elsewhere, "Wren Hale")),
            (
                "UPDATE face_tracks SET person_id = ?, confidence = 0.8, attribution = 'matched' "
                "WHERE id = ?",
                (elsewhere, track),
            ),
        ],
    )
    return asset, track


def test_the_disagreements_tab_opens_from_a_file_at_the_page_that_file_is_on(
    client: TestClient, scene: Scene
) -> None:
    """`from` on Disagreements names the FILE (the row's own id) in the tier's order."""
    turn_on(client)
    sign_in(client, "admin")
    revision = pairing("accurate")[1].revision
    write(
        db_path(client),
        [
            (
                # The embedding is one float32, so the reference can be unpacked by the may-be
                # cards' read as well (a lone byte cannot be). Kept above the literals: the
                # statement gate joins ADJACENT string literals, and a comment between two of them
                # splits this insert in its eyes.
                "INSERT INTO face_references "
                "(id, person_id, crop_path, crop_digest, embedding, quality, origin, recognizer, "
                "created_at) VALUES (?, ?, 'crop.jpg', 'digest', x'00000000', 1.0, 'confirmed', ?, 0)",
                (new_id(), scene.person, revision),
            )
        ],
    )
    _first, _face = _a_file_filed_under(client, scene, revision)
    second, _later = _a_file_filed_under(client, scene, revision)

    answer = client.get(
        "/api/faces/to-check", params={"kind": "mismatch", "limit": 1, "from": second}
    ).json()

    assert answer["offset"] == 1
    assert [item["id"] for item in answer["items"]] == [second]
    assert answer["total"] == 2


def test_what_was_set_aside_is_a_filter_on_the_review_list(
    client: TestClient, scene: Scene
) -> None:
    """Setting a group aside is the same question answered no, so it is a narrowing of the list
    rather than a tab of its own, and it is absent from the work until it is asked for."""
    turn_on(client)
    sign_in(client, "admin")
    pile = make_pile(client, scene, status="ignored")

    waiting = client.get("/api/faces/to-check").json()
    aside = client.get("/api/faces/to-check", params={"show": "ignored"}).json()

    assert waiting["items"] == []
    assert [item["id"] for item in aside["items"]] == [pile]


def test_the_review_list_is_empty_with_recognition_off(client: TestClient, scene: Scene) -> None:
    """An install that never turned this on has nothing to check because nothing has looked."""
    sign_in(client, "admin")
    make_pile(client, scene)

    assert client.get("/api/faces/to-check").json() == {
        "items": [],
        "total": 0,
        "offset": 0,
        "small_groups": 0,
    }


def test_a_face_already_settled_is_not_a_proposal(client: TestClient, scene: Scene) -> None:
    """Otherwise the wall of questions would be the record of answers again, with a button on it."""
    turn_on(client)
    sign_in(client)
    scene.attribute(client, how="matched")

    assert client.get("/api/faces/to-check").json()["total"] == 0


def test_a_face_nobody_is_attached_to_is_not_listed_as_identified(
    client: TestClient, scene: Scene
) -> None:
    """A screen of answers that included the questions would be the Unidentified list twice."""
    turn_on(client)
    sign_in(client)

    assert client.get("/api/faces/identified/people").json()["people"] == []


def test_a_recognized_face_in_a_file_this_account_may_not_see_is_absent(
    client: TestClient, scene: Scene
) -> None:
    """Both the row and the count. A total that included it would say how many decisions are being
    kept back, which is what the concealment was for."""
    turn_on(client)
    admin = sign_in(client)
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})
    scene.hide(client, "asset", scene.asset, admin)

    answer = client.get("/api/faces/identified/people").json()

    assert answer["people"] == []
    assert answer["total"] == 0


def test_what_has_been_recognized_is_empty_while_recognition_is_off(
    client: TestClient, scene: Scene
) -> None:
    """A section of a screen somebody is already on, so it says "nothing" rather than raising."""
    sign_in(client)

    assert client.get("/api/faces/identified/people").json()["people"] == []


# --- agreeing with what Sift proposed -------------------------------------------------------------


def test_agreeing_with_a_suggestion_settles_it_and_asks_for_a_rematch(
    client: TestClient, scene: Scene
) -> None:
    """Each agreement makes that face one of the person's references, which changes the gallery
    everything else is matched against, so the rest are compared again afterwards."""
    turn_on(client)
    sign_in(client)
    scene.attribute(client, how="suggested")

    answer = client.post("/api/faces/accept", json={"track_ids": [scene.track]})

    assert answer.status_code == 200
    assert answer.json()["changed"] == 1
    assert "face_rematch" in queued_types(client)
    listed = client.get(f"/api/faces/identified/people/{scene.person}").json()["items"]
    assert [item["attribution"] for item in listed] == ["confirmed"]


def test_agreeing_with_a_face_nobody_proposed_anybody_for_changes_nothing(
    client: TestClient, scene: Scene
) -> None:
    """The screen offers this over a selection, and a selection dragged across a page picks up
    faces that were already settled. Skipped rather than failing the whole call."""
    turn_on(client)
    sign_in(client)

    answer = client.post("/api/faces/accept", json={"track_ids": [scene.track]})

    assert answer.status_code == 200
    assert answer.json()["changed"] == 0
    assert "face_rematch" not in queued_types(client)


def test_agreeing_again_with_a_face_already_settled_changes_nothing(
    client: TestClient, scene: Scene
) -> None:
    """The other half of the same forgiveness, and the one that can undo work.

    A selection dragged across a page picks up faces somebody has already decided. Putting those
    through again would replace a decision a person made with whatever the machine currently
    guesses, so a settled face is skipped rather than re-decided.
    """
    turn_on(client)
    sign_in(client)
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})

    answer = client.post("/api/faces/accept", json={"track_ids": [scene.track]})

    assert answer.status_code == 200
    assert answer.json()["changed"] == 0
    # And it is still theirs, on the decision they made rather than on a fresh guess.
    known = client.get("/api/faces/identified/people").json()["people"]
    assert [one["person_id"] for one in known] == [scene.person]


def test_agreeing_with_every_match_for_one_person_answers_both_numbers(
    client: TestClient, scene: Scene
) -> None:
    """ "These matches are right", from the person's own page.

    Two numbers come back because they answer different questions: what now carries somebody's own
    answer, and how much Sift learned from it. Each agreement changes the gallery everything is
    matched against (and can lower the bar Sift attaches to that person at), so the rest are
    compared again afterwards.
    """
    turn_on(client)
    sign_in(client)
    scene.attribute(client, how="matched")

    answer = client.post(f"/api/faces/people/{scene.person}/confirm-matches")

    assert answer.status_code == 200
    assert answer.json() == {"confirmed": 1, "references": 1}
    assert "face_rematch" in queued_types(client)
    listed = client.get(f"/api/faces/identified/people/{scene.person}").json()["items"]
    assert [item["attribution"] for item in listed] == ["confirmed"]


def test_a_person_with_nothing_matched_is_agreed_with_and_nothing_happens(
    client: TestClient, scene: Scene
) -> None:
    """Including a person this user may not be told about, which answers the same as one with
    nothing standing: a refusal here would say whether somebody exists."""
    turn_on(client)
    sign_in(client)

    answer = client.post(f"/api/faces/people/{scene.person}/confirm-matches")
    unknown = client.post(f"/api/faces/people/{NEVER_EXISTED}/confirm-matches")

    assert answer.json() == unknown.json() == {"confirmed": 0, "references": 0}
    assert "face_rematch" not in queued_types(client)


def test_agreeing_with_every_match_is_refused_while_recognition_is_off(
    client: TestClient, scene: Scene
) -> None:
    """The switch is checked at the route, like every other decision here: with it off there is
    nothing to agree with and the answer says so rather than writing nothing quietly."""
    sign_in(client)

    assert client.post(f"/api/faces/people/{scene.person}/confirm-matches").status_code == 409


# --- the three scopes a person's own screen offers under its Yes and its No ------------------------
#
# The row on a person's screen offers "All N on this page", "The N picked" and "All N in this
# tab", the same three the group screen's control offers. What is pinned here is that the scope
# is a declared, validated part of the request and that the ids it carries are NARROWED by the
# server rather than trusted: the narrowing itself over two faces is pinned in the service tests
# beside the write.

_BULK_DOORS = (
    "/api/faces/people/{person}/confirm-matches",
    "/api/faces/people/{person}/reject-matches",
    "/api/faces/look-alikes/{person}/confirm",
    "/api/faces/look-alikes/{person}/reject",
)


@pytest.mark.parametrize("door", _BULK_DOORS)
@pytest.mark.parametrize(
    "body",
    [
        {"scope": "page"},
        {"scope": "picked", "track_ids": []},
        {"scope": "all", "track_ids": [NEVER_EXISTED]},
        {"scope": "everything"},
    ],
    ids=["page-without-faces", "picked-without-faces", "all-with-faces", "no-such-scope"],
)
def test_a_scope_that_does_not_say_what_it_is_about_is_refused(
    client: TestClient, scene: Scene, door: str, body: dict[str, object]
) -> None:
    """A page or a pick names its faces, the whole tab names none, and nothing else is a scope.

    Refused rather than read generously: a page with no faces read as "all" would turn an empty
    page into the widest press there is, and it would do it silently.
    """
    turn_on(client)
    sign_in(client)
    scene.attribute(client, how="matched")

    answer = client.post(door.format(person=scene.person), json=body)

    assert answer.status_code == 422
    listed = client.get(f"/api/faces/identified/people/{scene.person}").json()["items"]
    assert [item["attribution"] for item in listed] == ["matched"], "a refused press wrote"


@pytest.mark.parametrize("scope", ["page", "picked"])
def test_a_scoped_yes_acts_on_the_faces_it_names_and_on_nothing_else(
    client: TestClient, scene: Scene, scope: str
) -> None:
    """The ids are narrowed against what is standing for this person, never trusted.

    A face that is not theirs (here one that never existed) is not part of the press, so naming
    only that one confirms nothing; naming the face that IS theirs confirms exactly it.
    """
    turn_on(client)
    sign_in(client)
    scene.attribute(client, how="matched")
    door = f"/api/faces/people/{scene.person}/confirm-matches"

    stranger = client.post(door, json={"scope": scope, "track_ids": [NEVER_EXISTED]})
    theirs = client.post(door, json={"scope": scope, "track_ids": [scene.track]})

    assert stranger.status_code == 200
    assert stranger.json() == {"confirmed": 0, "references": 0}
    assert theirs.json()["confirmed"] == 1
    listed = client.get(f"/api/faces/identified/people/{scene.person}").json()["items"]
    assert [item["attribution"] for item in listed] == ["confirmed"]


def test_a_scoped_no_takes_the_name_off_the_faces_it_names(
    client: TestClient, scene: Scene
) -> None:
    """The refusal reads the same scope the agreement does, through the same narrowing."""
    turn_on(client)
    sign_in(client)
    scene.attribute(client, how="suggested")
    door = f"/api/faces/look-alikes/{scene.person}/reject"

    stranger = client.post(door, json={"scope": "picked", "track_ids": [NEVER_EXISTED]})
    theirs = client.post(door, json={"scope": "page", "track_ids": [scene.track]})

    assert stranger.json()["changed"] == 0
    assert theirs.json()["changed"] == 1
    assert client.get(f"/api/faces/identified/people/{scene.person}").json()["items"] == []
    # The receipt rides on the reply, so the card that pressed can offer Undo; a press that changed
    # nothing wrote none and says so.
    assert stranger.json()["decision_id"] is None
    assert isinstance(theirs.json()["decision_id"], str) and theirs.json()["decision_id"]


@pytest.mark.parametrize("body", [None, {}, {"scope": "all"}])
def test_the_whole_tab_is_what_no_scope_means(
    client: TestClient, scene: Scene, body: dict[str, object] | None
) -> None:
    """No body, an empty one and `all` are one press: every face standing on the tab.

    The card on the People Sift can recognize wall sends `{}` and must keep meaning what it meant.
    """
    turn_on(client)
    sign_in(client)
    scene.attribute(client, how="matched")
    door = f"/api/faces/people/{scene.person}/confirm-matches"

    answer = client.post(door) if body is None else client.post(door, json=body)

    assert answer.status_code == 200
    assert answer.json()["confirmed"] == 1


def test_agreeing_needs_faces_and_a_viewer_who_may_touch_them(
    client: TestClient, scene: Scene
) -> None:
    """No faces named is a 400; a face out of reach is SKIPPED and counted.

    Not a refusal of the whole call: a partial success saying nothing is worse than none, and one
    that says which part happened is not. What the assertions below keep is the part that matters:
    a face the viewer may not touch is NEVER acted on. What they add is that the reply says so, and that the vault says which of the two reasons it was, because
    only one of them is a lock the person holds the key to.
    """
    turn_on(client)
    admin = sign_in(client)

    assert client.post("/api/faces/accept", json={"track_ids": []}).status_code == 400

    unknown = client.post("/api/faces/accept", json={"track_ids": [NEVER_EXISTED]})
    assert unknown.status_code == 200
    assert (unknown.json()["changed"], unknown.json()["skipped"]) == (0, 1)
    # A made-up id is not a lock: no PIN would produce it, so no Unlock is offered for it.
    assert unknown.json()["vault_locked"] is False

    scene.hide(client, "asset", scene.asset, admin)
    hidden = client.post("/api/faces/accept", json={"track_ids": [scene.track]})
    assert hidden.status_code == 200
    assert (hidden.json()["changed"], hidden.json()["skipped"]) == (0, 1)
    assert hidden.json()["vault_locked"] is True


def test_agreeing_is_an_admins(client: TestClient, scene: Scene) -> None:
    turn_on(client)
    guest = sign_in(client, "guest", who="agreeable")
    scene.share_with(client, guest)

    assert client.post("/api/faces/accept", json={"track_ids": [scene.track]}).status_code == 403


def test_agreeing_is_refused_while_recognition_is_off(client: TestClient, scene: Scene) -> None:
    sign_in(client)

    assert client.post("/api/faces/accept", json={"track_ids": [scene.track]}).status_code == 409


# --- merging and splitting groups -----------------------------------------------------------------


def test_moving_faces_with_no_destination_splits_them_into_a_group_of_their_own(
    client: TestClient, scene: Scene
) -> None:
    """The grouping deliberately over-splits, so one pile that is mostly one person plus a
    stranger is the ordinary case, and moving the stranger out needs no name at all."""
    turn_on(client)
    sign_in(client)

    answer = client.post("/api/faces/move", json={"track_ids": [scene.track], "pile_id": None})

    assert answer.status_code == 200
    body = answer.json()
    assert body["changed"] == 1
    assert body["pile_id"]
    landed = client.get(f"/api/faces/groups/{body['pile_id']}")
    assert [face["track_id"] for face in landed.json()["group"]["faces"]] == [scene.track]


def test_moving_faces_into_a_group_that_is_not_there_is_a_miss(
    client: TestClient, scene: Scene
) -> None:
    """The destination is picked off a list, and a group can be named or set aside between the
    list being drawn and the press."""
    turn_on(client)
    sign_in(client)

    answer = client.post(
        "/api/faces/move", json={"track_ids": [scene.track], "pile_id": NEVER_EXISTED}
    )

    assert answer.status_code == 404


def test_moving_needs_faces_and_a_viewer_who_may_touch_them(
    client: TestClient, scene: Scene
) -> None:
    """No faces named is a 400; a face out of reach is SKIPPED and counted.

    Not a refusal of the whole call: a partial success saying nothing is worse than none, and one
    that says which part happened is not. What the assertions below keep is the part that matters:
    a face the viewer may not touch is NEVER acted on. What they add is that the reply says so, and that the vault says which of the two reasons it was, because
    only one of them is a lock the person holds the key to.
    """
    turn_on(client)
    admin = sign_in(client)

    assert client.post("/api/faces/move", json={"track_ids": []}).status_code == 400

    unknown = client.post("/api/faces/move", json={"track_ids": [NEVER_EXISTED]})
    assert unknown.status_code == 200
    assert (unknown.json()["changed"], unknown.json()["skipped"]) == (0, 1)
    # A made-up id is not a lock: no PIN would produce it, so no Unlock is offered for it.
    assert unknown.json()["vault_locked"] is False

    scene.hide(client, "asset", scene.asset, admin)
    hidden = client.post("/api/faces/move", json={"track_ids": [scene.track]})
    assert hidden.status_code == 200
    assert (hidden.json()["changed"], hidden.json()["skipped"]) == (0, 1)
    assert hidden.json()["vault_locked"] is True


def test_moving_faces_is_an_admins(client: TestClient, scene: Scene) -> None:
    """It rearranges the review queue everybody sees, and it overrides the grouping."""
    turn_on(client)
    guest = sign_in(client, "guest", who="rearranger")
    scene.share_with(client, guest)

    assert client.post("/api/faces/move", json={"track_ids": [scene.track]}).status_code == 403


def test_moving_faces_is_refused_while_recognition_is_off(client: TestClient, scene: Scene) -> None:
    sign_in(client)

    assert client.post("/api/faces/move", json={"track_ids": [scene.track]}).status_code == 409


def test_one_persons_page_leaves_out_faces_that_are_somebody_elses(
    client: TestClient, scene: Scene, tmp_path: Path
) -> None:
    """The half that stops the page above passing on a route that lists every decided face.

    Two people, both recognized, both in files this user may see. Opening one card must show
    one of them: the scoping is per viewer and the filtering is per person, and a page that only
    did the first would put the whole of Identified under whichever name was pressed.
    """
    turn_on(client)
    sign_in(client)
    # Its own directory: a Scene makes a library root, and two roots cannot share a path.
    other = Scene(client, tmp_path / "second", name="second")
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})
    client.post("/api/faces/name", json={"track_ids": [other.track], "person_id": other.person})

    answer = client.get(f"/api/faces/identified/people/{scene.person}").json()

    assert [item["track_id"] for item in answer["items"]] == [scene.track]
    assert answer["total"] == 1


@pytest.fixture
def no_card_here(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the graphics card away from the running service, whatever this machine has in it.

    The two tests below assert a REFUSAL, and the refusal is worked out from two facts: whether the
    hardware report sees a card, and whether the runtime that drives one was ever imported. Left
    unarranged, what they prove is a property of the machine the tests run on (and worse, of the
    ORDER the run happens to take).

    The order is what makes it a fault rather than an inconvenience. The inference runtime can only
    be imported once per process, and the build that wins is the one whichever caller got there
    first asked for: under a test's temporary data directory there is no installed card runtime, so
    the plain build is imported and the card is refused; but a test earlier in the same run that
    reached the runtime with the REAL data directory in place pulls in the installed card build
    instead, and it stays for every test after it. On a machine with a card fitted, these two would
    then answer 202 rather than 409, and only in the runs where something else went first.

    So the condition is made here. With no card visible the refusal is settled before the runtime
    is consulted at all, which is the same answer on a machine with a card and on one without.
    """
    service = faces_of(client)
    # The same `type: ignore` the helpers above carry, and for the same reason: the running
    # application is untyped here, so what comes back off it is `object`.
    report = replace(service._hardware, cuda=False, rocm=False)  # type: ignore[attr-defined]
    monkeypatch.setattr(service, "_hardware", report)


def test_a_sweep_is_refused_when_the_device_it_would_run_on_is_not_there(
    client: TestClient,
    no_card_here: None,
) -> None:
    """Not a scan that goes badly: a scan that cannot happen.

    Started in this state it would queue the whole library, fail every file, record nothing, and
    report that it went through the library, while the screen said Ready, the next sweep queued
    nothing because nothing had been recorded, and the only visible sign was a library that never
    gained a face. Refused here rather than only on the screen, so no caller can start one.
    """
    sign_in(client)
    turn_on(client)
    set_app_setting(db_path(client), "faces.device", '"nvidia"')

    answer = client.post("/api/faces/scan")

    assert answer.status_code == 409
    assert "graphics card" in answer.json()["detail"]


def test_a_rebuild_is_refused_when_the_device_it_would_run_on_is_not_there(
    client: TestClient,
    no_card_here: None,
) -> None:
    """The same refusal as a sweep, on the other route that starts real work.

    Grouping loads the recognizer, so it fails exactly the way a scan does on a device that is not
    there, and a caller that can start one of the two is a caller that can start the failure.
    """
    sign_in(client)
    turn_on(client)
    set_app_setting(db_path(client), "faces.device", '"nvidia"')

    answer = client.post("/api/faces/regroup")

    assert answer.status_code == 409
    assert "graphics card" in answer.json()["detail"]


# --- the group a face is waiting in ------------------------------------------------------------


_MAKE_PILE = (
    "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
    "VALUES (?, ?, X'00', 1, 0, 0)"
)
_INTO_PILE = "UPDATE face_tracks SET pile_id = ? WHERE id = ?"


def _pile(scene: Scene, client: TestClient, status: str = "open") -> str:
    pile = new_id()
    write(
        db_path(client),
        [(_MAKE_PILE, (pile, status)), (_INTO_PILE, (pile, scene.track))],
    )
    return pile


def test_an_unnamed_face_says_which_group_it_is_waiting_in(
    client: TestClient, scene: Scene
) -> None:
    """The address of the pile, which the strip under a file cannot build by itself.

    The pile screen is otherwise reachable only from the wall of piles in Organize, so somebody
    looking at a face on a file could not get to the rest of the faces Sift matched it to except by
    recognising the crop on that wall.
    """
    turn_on(client)
    sign_in(client, "admin")
    pile = _pile(scene, client)

    face = client.get(f"/api/assets/{scene.asset}/faces").json()[0]

    assert face["pile_id"] == pile
    # The status too, because open and set-aside piles live at different addresses.
    assert face["pile_status"] == "open"


def test_a_group_that_was_set_aside_says_so_rather_than_reading_as_open(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    sign_in(client, "admin")
    _pile(scene, client, status="ignored")

    face = client.get(f"/api/assets/{scene.asset}/faces").json()[0]

    assert face["pile_status"] == "ignored"


def test_a_face_that_carries_a_name_names_no_group(client: TestClient, scene: Scene) -> None:
    """A named face has left the pile as far as every pile screen is concerned.

    The pile's own reads all carry `person_id IS NULL`, so a group opened from a named face would
    be a screen that does not contain the face that sent you there.
    """
    turn_on(client)
    sign_in(client, "admin")
    _pile(scene, client)
    scene.attribute(client)

    face = client.get(f"/api/assets/{scene.asset}/faces").json()[0]

    assert face["person_name"] == "Ada Lovelace"
    assert face["pile_id"] is None
    assert face["pile_status"] is None


def test_a_face_whose_name_is_withheld_names_no_group_either(
    client: TestClient, scene: Scene
) -> None:
    """The one that matters, and the reason the rule reads the stored column and not the name.

    The same reachable state the withheld-name test above sets up: an admin takes a person off a
    file by hand while a face in it still names them, so to a guest who was given the file that
    person is on no list and the face arrives with no name.

    Every other field about that person is already withheld with the name. The pile is a
    resemblance fact about the same person (it says this face is one of a group Sift matched
    together), so it has to go with them. The rule cannot read the answer's own `person_id`,
    because that reads None here and on a genuinely unnamed face alike; it reads the stored column,
    which still holds somebody.
    """
    turn_on(client)
    scene.attribute(client)
    guest = sign_in(client, "guest", who="invited")
    scene.share_with(client, guest)
    scene.unname_by_hand(client)
    _pile(scene, client)

    face = client.get(f"/api/assets/{scene.asset}/faces").json()[0]

    # Withheld, and so indistinguishable from unnamed on every field that was already withheld.
    assert face["person_name"] is None
    assert face["person_id"] is None
    # And the group goes with them, which is the whole point of this test.
    assert face["pile_id"] is None
    assert face["pile_status"] is None


def test_a_pile_that_has_been_rebuilt_away_leaves_the_status_absent(
    client: TestClient, scene: Scene
) -> None:
    """Grouping replaces piles, so a track can hold the id of one that no longer exists.

    Reported as an id with no status rather than as an id that looks openable. The screen offers
    the row only when both are there, so a group that is gone offers nothing instead of a link
    that lands on a screen saying the group is not there.
    """
    turn_on(client)
    sign_in(client, "admin")
    # FOREIGN KEYS OFF FOR THE ONE WRITE, which is how `test_access.py` arranges the same kind of
    # state. `face_tracks.pile_id` REFERENCES `face_piles(id)`, and `foreign_keys` is ON for every
    # connection, so a track pointing at a pile that was never there cannot be written directly.
    #
    # The state is real all the same, and the migration is what makes it: `initialize` rebuilds
    # `face_piles` by DROP and RENAME and then restores the links, and a link whose pile did not
    # survive the copy is an orphan of precisely this shape. That is what the route is defensive
    # about, so this is the state to arrange.
    write(
        db_path(client),
        [
            ("PRAGMA foreign_keys=OFF", ()),
            (_INTO_PILE, (new_id(), scene.track)),
            ("PRAGMA foreign_keys=ON", ()),
        ],
    )

    face = client.get(f"/api/assets/{scene.asset}/faces").json()[0]

    assert face["pile_id"]
    assert face["pile_status"] is None


def test_the_review_list_says_a_filed_name_does_not_match_the_face(
    client: TestClient, scene: Scene
) -> None:
    """The row that runs backwards, over the wire: a name a pass filed, and the face named as
    somebody else.

    Everything the card is drawn from has to survive the trip: the FILE as the row's id, the
    person the two answers are about, and which pass put the name there. The person is a second
    field precisely because the id is the file; a client reading the person off `id` would press
    both buttons against a file id.
    """
    turn_on(client)
    sign_in(client, "admin")
    revision = pairing("accurate")[1].revision
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_people (asset_id, person_id, source, decided_at) "
                "VALUES (?, ?, 'folder', 0)",
                (scene.asset, scene.person),
            ),
            (
                "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
                ("p-orla", "Orla Tennant", "orla tennant"),
            ),
            (
                "UPDATE face_tracks SET person_id = 'p-orla', attribution = 'matched',"
                " confidence = 0.8 WHERE id = ?",
                (scene.track,),
            ),
            # The scan and the reference both carry the model in force: a description measured by
            # another model means nothing here, so the read leaves those out on purpose.
            (
                "UPDATE face_scans SET recognizer = ? WHERE asset_id = ?",
                (revision, scene.asset),
            ),
            (
                # The embedding is one float32, so the reference can be unpacked by the may-be
                # cards' read as well (a lone byte cannot be). Kept above the literals: the
                # statement gate joins ADJACENT string literals, and a comment between two of them
                # splits this insert in its eyes.
                "INSERT INTO face_references "
                "(id, person_id, crop_path, crop_digest, embedding, quality, origin, recognizer, "
                "created_at) VALUES (?, ?, 'crop.jpg', 'digest', x'00000000', 1.0, 'confirmed', ?, 0)",
                (new_id(), scene.person, revision),
            ),
        ],
    )

    answer = client.get("/api/faces/to-check").json()

    assert [item["kind"] for item in answer["items"]] == ["mismatch"]
    row = answer["items"][0]
    assert row["id"] == scene.asset
    assert row["person_id"] == scene.person
    assert row["person_name"] == "Ada Lovelace"
    assert row["source"] == "folder"
    assert [face["track_id"] for face in row["faces"]] == [scene.track]


def test_the_answer_doors_are_shut_while_faces_is_off_and_each_does_its_one_job_once_on(
    client: TestClient, scene: Scene
) -> None:
    """Every door that answers a question about faces refuses in the switch's words while the
    feature is off, before it reads anything; then, switched on, each does what its card says,
    in one booted application, because what is checked is the wiring, not the rules below it.

    On: the starter count reads the link and the press queues the task for exactly those People;
    a group Yes or No that reaches nothing changes nothing and asks for no re-match; a No on
    Sift's own match takes the name off; a Yes with no proposal standing asks for nothing; and a
    Yes on a proposal names the face and asks for the re-match its new picture makes worth
    running.
    """
    sign_in(client, "admin")
    person = scene.person
    nothing = {"pile_ids": [NEVER_EXISTED], "track_ids": [scene.track]}
    doors = [
        ("GET", "/api/faces/starters", None),
        ("POST", "/api/faces/starters", None),
        ("POST", f"/api/faces/look-alikes/{person}/confirm", None),
        ("POST", f"/api/faces/look-alikes/{person}/reject", None),
        ("POST", f"/api/faces/may-be/{person}/confirm", nothing),
        ("POST", f"/api/faces/may-be/{person}/reject", {"pile_ids": [NEVER_EXISTED]}),
        ("POST", f"/api/faces/people/{person}/reject-matches", None),
    ]
    for method, path, body in doors:
        assert client.request(method, path, json=body).status_code == 409, path
    assert FACE_STARTERS not in queued_types(client)

    turn_on(client)
    box = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO stash_boxes (id, name, endpoint, slug, created_at) "
                "VALUES (?, 'StashDB', 'https://stashdb.invalid/graphql', NULL, 0)",
                (box,),
            ),
            (
                "INSERT INTO person_stash_box_links "
                "(person_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, 'r1', '[]', 0)",
                (person, box),
            ),
        ],
    )
    offered = client.get("/api/faces/starters").json()
    assert offered["people"] == 1
    assert [one["id"] for one in offered["who"]] == [person]
    queued = client.post("/api/faces/starters")
    assert queued.status_code == 200
    assert queued.json()["people"] == 1 and queued.json()["job_id"]
    assert FACE_STARTERS in queued_types(client)

    yes = client.post(f"/api/faces/may-be/{person}/confirm", json=nothing)
    no = client.post(f"/api/faces/may-be/{person}/reject", json={"pile_ids": [NEVER_EXISTED]})
    assert (yes.json()["changed"], yes.json()["decision_id"]) == (0, None)
    assert (no.json()["changed"], no.json()["decision_id"]) == (0, None)
    assert faces_jobs.FACE_REMATCH not in queued_types(client)

    scene.attribute(client, how="matched")
    refused = client.post(f"/api/faces/people/{person}/reject-matches")
    assert refused.status_code == 200 and refused.json()["changed"] == 1
    assert client.get(f"/api/faces/identified/people/{person}").json()["items"] == []
    idle = client.post(f"/api/faces/look-alikes/{person}/confirm")
    assert (idle.json()["changed"], idle.json()["decision_id"]) == (0, None)
    assert faces_jobs.FACE_REMATCH not in queued_types(client)

    scene.attribute(client, how="suggested")
    agreed = client.post(f"/api/faces/look-alikes/{person}/confirm")
    assert agreed.status_code == 200
    assert (agreed.json()["changed"], agreed.json()["person_id"]) == (1, person)
    # The agreement is a task: done once it has run, and it asks for the re-match.
    _until_done(client, FACE_AGREE)
    listed = client.get(f"/api/faces/identified/people/{person}").json()["items"]
    assert [item["attribution"] for item in listed] == ["confirmed"]
    assert faces_jobs.FACE_REMATCH in queued_types(client)


def test_the_starter_press_says_its_count_first_and_refuses_to_do_nothing(
    client: TestClient,
) -> None:
    """The count is read from this library's own tables, before anything is asked of a box, and
    a press with nobody to act on is refused in words rather than queued as a task that does
    nothing. Reached through the stash-box door the app builds (`wiring.BOX_PICTURES`)."""
    turn_on(client)
    sign_in(client, "admin")

    assert client.get("/api/faces/starters").json() == {"people": 0, "who": []}
    refused = client.post("/api/faces/starters")
    assert refused.status_code == 409
    assert "Nobody linked to a stash-box" in refused.json()["detail"]


def _until_done(client: TestClient, job_type: str) -> None:
    """Wait for every task of this type to finish, as the queue reports it; bounded."""
    for _turn in range(400):
        jobs = client.get("/api/jobs", params={"type": job_type, "limit": 20}).json()["jobs"]
        if jobs and all(one["state"] in {"done", "failed", "canceled"} for one in jobs):
            assert {one["state"] for one in jobs} == {"done"}
            return
        time.sleep(0.025)
    raise AssertionError(f"{job_type} did not finish")


class _Agreeing:
    """The face service as the confirm route sees it, recording what it is asked."""

    def __init__(self, named: list[str]) -> None:
        self.named = named
        self.agreed: list[object] = []

    async def enabled(self) -> bool:
        return True

    async def look_alikes_to_agree(
        self, _viewer: object, _person: str, *, only: object
    ) -> list[str]:
        return self.named

    async def confirm_look_alikes(self, *args: object, **kwargs: object) -> None:
        self.agreed.append((args, kwargs))


class _Queue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict[str, object], str | None]] = []

    async def enqueue(
        self, job_type: str, payload: dict[str, object], *, requested_by: str | None = None
    ) -> str:
        self.enqueued.append((job_type, payload, requested_by))
        return "job-1"


async def test_a_yes_to_a_persons_proposals_is_answered_before_the_agreement_runs() -> None:
    """The route hands the faces it reached to a task and answers with their count; nothing is
    agreed to inside the request, and a press reaching nothing queues nothing."""
    service, queue = _Agreeing(["t1", "t2"]), _Queue()
    viewer = Viewer(id="u1", role=Role.ADMIN)

    answer = await router_answers.confirm_look_alikes(
        "p1",
        service=cast(FaceService, service),
        queue=cast(JobQueue, queue),
        viewer=viewer,
    )

    assert (answer.changed, answer.person_id) == (2, "p1")
    assert queue.enqueued == [(FACE_AGREE, {"person_id": "p1", "track_ids": ["t1", "t2"]}, "u1")]
    assert service.agreed == []

    nobody = _Queue()
    await router_answers.confirm_look_alikes(
        "p1",
        service=cast(FaceService, _Agreeing([])),
        queue=cast(JobQueue, nobody),
        viewer=viewer,
    )
    assert nobody.enqueued == []
