# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sharing, from the panel that makes a grant to the screen that changes because of it.

The engine underneath these tests has its own exhaustive suite. So what is checked here is
deliberately not the resolution rules again. It is the join: that a grant made through this
endpoint is the same grant the resolver reads, that it applies to the very next request rather
than the next sign-in, and that the two regressions worth naming hold once grants can actually be
produced.

Those two are default-deny (a guest sees what was shared and nothing else) and restrict-wins (a
restrict inside a shared scope removes access). Both are the ones that would silently stop being
true the moment a share could be made for real, which is exactly what this slice makes possible.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.testing.auth import TEST_PIN, give_pin

from .conftest import Library, Reach, db_path, sign_in, visible_ids, write

pytestmark = [pytest.mark.integration]


def _share(client: TestClient, **body: object) -> int:
    return int(client.put("/api/sharing", json=body).status_code)


def _revoke(client: TestClient, **body: object) -> int:
    return int(client.post("/api/sharing/revoke", json=body).status_code)


# --- default-deny, and the first grant that has ever undone it -----------------------------------


def test_a_guest_starts_seeing_nothing_at_all(client: TestClient, library: Library) -> None:
    """The default, and the reason the model is three-valued. Files arrive on their own (scans,
    watched folders, downloads), so anything but fail-closed would show a guest a new file before
    an admin had seen it themselves."""
    sign_in(client, "guest")

    assert visible_ids(client) == []


def test_sharing_one_item_shows_that_item_and_nothing_else(
    client: TestClient, library: Library
) -> None:
    """The whole feature, and the regression that matters most: sharing must reveal exactly what
    was shared. A share that leaked its neighbours would be indistinguishable on the sharer's own
    screen, where everything is visible anyway."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")

    assert (
        _share(
            client,
            object_type="item",
            object_id=library.first,
            subject_user_id=guest_id,
            effect="share",
        )
        == 200
    )

    sign_in(client, "guest")
    assert visible_ids(client) == [library.first]


def test_sharing_a_folder_reaches_what_is_inside_it(client: TestClient, library: Library) -> None:
    """The physical axis. A folder is the shape a library already has on disk, so sharing one has
    to carry its contents rather than being a label on a directory."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")

    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest_id,
        effect="share",
    )

    sign_in(client, "guest")
    assert sorted(visible_ids(client)) == sorted([library.first, library.second])


def test_sharing_a_person_reaches_only_what_they_are_in(
    client: TestClient, library: Library
) -> None:
    """The logical axis, and the one that only works because silence is not a no. Under a
    two-valued model every logical share would be a no-op: it could only reveal things already
    visible, and to a guest nothing is."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")

    _share(
        client,
        object_type="person",
        object_id=library.person,
        subject_user_id=guest_id,
        effect="share",
    )

    sign_in(client, "guest")
    assert visible_ids(client) == [library.first]


def test_a_restrict_inside_a_share_takes_it_back(client: TestClient, library: Library) -> None:
    """Fail-closed, and the reason Restrict exists at all.

    Default-deny already keeps a guest out of everything, so Restrict's one job is to be a
    guarantee: never this, whatever else gets shared later, and no share made anywhere reaches in.
    Here the folder is shared and one clip inside it is restricted, and the restricted one is gone
    while its neighbour stays.
    """
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest_id,
        effect="share",
    )

    _share(
        client,
        object_type="item",
        object_id=library.first,
        subject_user_id=guest_id,
        effect="restrict",
    )

    sign_in(client, "guest")
    assert visible_ids(client) == [library.second]


def test_revoking_applies_to_the_very_next_request(client: TestClient, library: Library) -> None:
    """Not at the next sign-in, and not when a month-long session expires. Nothing about a
    permission is carried in the session, so the guest's next call is already answered by the
    absence of the row."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")
    _share(
        client,
        object_type="item",
        object_id=library.first,
        subject_user_id=guest_id,
        effect="share",
    )
    sign_in(client, "guest")
    assert visible_ids(client) == [library.first]

    sign_in(client, "admin")
    assert (
        _revoke(
            client,
            object_type="item",
            object_id=library.first,
            subject_user_id=guest_id,
            effect="share",
        )
        == 200
    )

    sign_in(client, "guest")
    assert visible_ids(client) == []


def test_sharing_the_same_thing_twice_is_one_grant(client: TestClient, library: Library) -> None:
    """The control says what the state should be, not what to change, so pressing it twice is one
    decision made twice rather than an error or a second row."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")
    body = {
        "object_type": "item",
        "object_id": library.first,
        "subject_user_id": guest_id,
        "effect": "share",
    }

    client.put("/api/sharing", json=body)
    second = client.put("/api/sharing", json=body)

    assert second.status_code == 200
    assert len(second.json()) == 1


def test_revoking_something_never_granted_is_not_an_error(
    client: TestClient, library: Library
) -> None:
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")

    assert (
        _revoke(
            client,
            object_type="item",
            object_id=library.first,
            subject_user_id=guest_id,
            effect="share",
        )
        == 200
    )


# --- who this is shared with ---------------------------------------------------------------------


def test_the_panel_lists_both_effects_with_the_names_beside_them(
    client: TestClient, library: Library
) -> None:
    """A share is easy to make and easy to forget, and nobody revokes what they were never shown.
    The name rides along because a user id is not something anybody recognizes."""
    first_guest = sign_in(client, "guest", username="sharing-first")
    second_guest = sign_in(client, "guest", username="sharing-second")
    sign_in(client, "admin")
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=first_guest,
        effect="share",
    )
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=second_guest,
        effect="restrict",
    )

    panel = client.get(
        "/api/sharing", params={"object_type": "folder", "object_id": library.folder}
    )

    assert panel.status_code == 200
    assert {(row["username"], row["effect"]) for row in panel.json()} == {
        ("sharing-first", "share"),
        ("sharing-second", "restrict"),
    }


def test_something_nobody_was_given_reads_back_empty(client: TestClient, library: Library) -> None:
    """Private is the default for everything in a library. An empty panel is the ordinary answer,
    not a missing object and not an error."""
    sign_in(client, "admin")

    panel = client.get("/api/sharing", params={"object_type": "item", "object_id": library.second})

    assert panel.status_code == 200
    assert panel.json() == []


def test_the_share_control_is_offered_every_account(client: TestClient) -> None:
    """Admins included, and marked as such. The picker greys them out rather than leaving them off,
    so an admin looking for somebody can tell "not a user here" from "a user this control
    cannot do anything about"."""
    sign_in(client, "guest")
    sign_in(client, "admin")

    listed = client.get("/api/sharing/users")

    assert listed.status_code == 200
    assert {(row["username"], row["role"]) for row in listed.json()} == {
        ("sharing-admin", "admin"),
        ("sharing-guest", "guest"),
    }


# --- grants that could never mean anything -------------------------------------------------------


def test_sharing_with_an_admin_is_refused_rather_than_stored(
    client: TestClient, library: Library
) -> None:
    """The row would do nothing: an admin is let past the access rules entirely, so neither effect
    reaches them. A stored restrict would be the worse half: the panel would report something
    walled off from an admin that was not."""
    admin_id = sign_in(client, "admin")

    assert (
        _share(
            client,
            object_type="item",
            object_id=library.first,
            subject_user_id=admin_id,
            effect="restrict",
        )
        == 409
    )


def test_sharing_with_an_account_that_does_not_exist_is_refused(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")

    assert (
        _share(
            client,
            object_type="item",
            object_id=library.first,
            subject_user_id="01HX0000000000000000000404",
            effect="share",
        )
        == 404
    )


def test_a_grant_stored_the_wrong_way_round_is_refused(
    client: TestClient, library: Library
) -> None:
    """The global object names nothing; everything else names something. Either mistake produces a
    row that sits in the table doing nothing while the panel reports it as in force, and an inert
    restrict is a promise that was never kept."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")

    assert (
        _share(
            client,
            object_type="global",
            object_id=library.first,
            subject_user_id=guest_id,
            effect="share",
        )
        == 422
    )
    assert (
        _share(
            client,
            object_type="folder",
            object_id=None,
            subject_user_id=guest_id,
            effect="share",
        )
        == 422
    )


def test_asking_the_panel_about_a_shape_that_cannot_exist_is_refused(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")

    asked = client.get("/api/sharing", params={"object_type": "folder"})

    assert asked.status_code == 422


def test_revoking_a_shape_that_cannot_exist_is_refused(
    client: TestClient, library: Library
) -> None:
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")

    assert (
        _revoke(
            client,
            object_type="global",
            object_id=library.first,
            subject_user_id=guest_id,
            effect="share",
        )
        == 422
    )


def test_a_kind_of_thing_that_is_not_shareable_is_refused_at_the_edge(
    client: TestClient, library: Library
) -> None:
    """The list of things a grant can name is closed. A typo becomes a 422 here rather than a row
    naming a kind of object nothing will ever look for."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")

    assert (
        _share(
            client,
            object_type="playlist",
            object_id=library.first,
            subject_user_id=guest_id,
            effect="share",
        )
        == 422
    )


# --- the user going away -------------------------------------------------------------------------


def test_deleting_a_guest_takes_their_grants_with_them(
    client: TestClient, library: Library
) -> None:
    """The one side of the access model a foreign key can carry, and it does: the subject of a
    grant really is a row in the users table. What a grant *points at* is the polymorphic side
    that cannot cascade, which is why deleting an item clears its grants by hand.
    """
    sign_in(client, "admin")
    created = client.post(
        "/api/auth/users",
        json={"username": "temporary", "password": "A-Sharing-Test-Passw0rd!"},
    )
    guest_id = created.json()["id"]
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest_id,
        effect="share",
    )

    client.delete(f"/api/auth/users/{guest_id}")

    panel = client.get(
        "/api/sharing", params={"object_type": "folder", "object_id": library.folder}
    )
    assert panel.json() == []


# --- who may reach any of it ---------------------------------------------------------------------


def test_a_guest_cannot_read_who_else_something_is_shared_with(
    client: TestClient, library: Library
) -> None:
    """Admin-only for the reads as well as the writes. Who else has access is somebody else's
    access, and a guest able to ask it could enumerate the other users one object at a time."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest_id,
        effect="share",
    )

    sign_in(client, "guest")

    assert (
        client.get(
            "/api/sharing", params={"object_type": "folder", "object_id": library.folder}
        ).status_code
        == 403
    )
    assert client.get("/api/sharing/users").status_code == 403


def test_a_guest_cannot_share_anything_with_themselves(
    client: TestClient, library: Library
) -> None:
    """The request the interface never makes, refused by the server anyway. Without this a guest
    who was shared one clip could hand themselves the whole library."""
    guest_id = sign_in(client, "guest")

    assert (
        _share(
            client,
            object_type="global",
            object_id=None,
            subject_user_id=guest_id,
            effect="share",
        )
        == 403
    )
    assert (
        _revoke(
            client,
            object_type="item",
            object_id=library.first,
            subject_user_id=guest_id,
            effect="restrict",
        )
        == 403
    )
    sign_in(client, "guest")
    assert visible_ids(client) == []


def test_sharing_something_restricted_replaces_the_restrict(
    client: TestClient, library: Library
) -> None:
    """The two are mutually exclusive on one object for one user, and the newer one wins.

    Both at once resolves to Restricted (a restrict beats every share), so a share stored
    underneath one does nothing for as long as it sits there, and then quietly takes effect the day
    the restrict is lifted. That is a decision nobody made, arriving much later. Saying "share this
    with them" now means exactly that, whatever was said before.
    """
    guest = sign_in(client, "guest", username="sharing-swap-one")
    sign_in(client, "admin")
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest,
        effect="restrict",
    )

    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest,
        effect="share",
    )

    panel = client.get(
        "/api/sharing", params={"object_type": "folder", "object_id": library.folder}
    )
    assert [row["effect"] for row in panel.json()] == ["share"]


def test_restricting_something_shared_replaces_the_share(
    client: TestClient, library: Library
) -> None:
    """And the other direction, which is the one that matters for a promise: a Restrict must not
    leave a share sitting underneath it, waiting to come back the day it is lifted."""
    guest = sign_in(client, "guest", username="sharing-swap-two")
    sign_in(client, "admin")
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest,
        effect="share",
    )

    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest,
        effect="restrict",
    )

    panel = client.get(
        "/api/sharing", params={"object_type": "folder", "object_id": library.folder}
    )
    assert [row["effect"] for row in panel.json()] == ["restrict"]


def test_the_panel_can_say_where_a_grant_came_from(client: TestClient, library: Library) -> None:
    """The read behind "why is this shared when I never shared it".

    A file inside a folder, carrying a tag, has three places a decision could have been made and no
    way to tell which from the answer alone. This lists every grant that reaches it and names the
    thing each was made on.
    """
    guest = sign_in(client, "guest", username="sharing-why")
    sign_in(client, "admin")
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest,
        effect="share",
    )
    _share(
        client, object_type="tag", object_id=library.tag, subject_user_id=guest, effect="restrict"
    )
    _share(
        client, object_type="item", object_id=library.first, subject_user_id=guest, effect="share"
    )

    found = client.get(
        "/api/sharing/sources", params={"object_type": "item", "object_id": library.first}
    )
    assert found.status_code == 200

    said = {(row["effect"], row["source_type"], row["here"]) for row in found.json()}
    # From the folder it is in, from a tag it carries, and from the file itself.
    assert ("share", "folder", False) in said
    assert ("restrict", "tag", False) in said
    assert ("share", "item", True) in said
    # And each one that is not the file itself is NAMED, or the line cannot be read.
    named = {row["source_type"]: row["source_name"] for row in found.json()}
    assert named["folder"] and named["tag"]


def test_where_it_came_from_names_only_what_reaches_this_file(
    client: TestClient, library: Library
) -> None:
    """The other half: a grant on something this file is not in must not appear.

    Without it the panel would list every decision in the library beside every file, which is the
    same as saying nothing.
    """
    guest = sign_in(client, "guest", username="sharing-elsewhere")
    sign_in(client, "admin")
    _share(
        client,
        object_type="person",
        object_id=library.person,
        subject_user_id=guest,
        effect="share",
    )

    # `second` is the clip with no person on it.
    found = client.get(
        "/api/sharing/sources", params={"object_type": "item", "object_id": library.second}
    ).json()
    assert [row for row in found if row["source_type"] == "person"] == []

    # ...and it does reach the one that IS theirs, so this is not passing on an empty answer.
    reaches = client.get(
        "/api/sharing/sources", params={"object_type": "item", "object_id": library.first}
    ).json()
    assert [row["source_type"] for row in reaches] == ["person"]


def test_where_it_came_from_is_an_admin_question(client: TestClient, library: Library) -> None:
    """A guest is told nothing about grants, including the ones about them, and least of all the
    shape of how their access was arranged."""
    guest = sign_in(client, "guest", username="sharing-nosy")
    sign_in(client, "admin")
    _share(
        client, object_type="item", object_id=library.first, subject_user_id=guest, effect="share"
    )

    sign_in(client, "guest", username="sharing-nosy")
    refused = client.get(
        "/api/sharing/sources", params={"object_type": "item", "object_id": library.first}
    )
    assert refused.status_code == 403


# --- and the other rule: what is CONCEALING this ------------------------------------------------


def _hidden_by(client: TestClient, object_type: str, object_id: str) -> list[dict[str, object]]:
    found = client.get(
        "/api/sharing/hidden-by", params={"object_type": object_type, "object_id": object_id}
    )
    assert found.status_code == 200, found.text
    return list(found.json())


def test_the_panel_can_say_which_folder_is_hiding_a_file(
    client: TestClient, library: Library
) -> None:
    """Hiding a folder hides what is in it, and the file can now say which folder.

    The reason this is worth an endpoint: the flag is on something that is not on the screen.
    Without it, finding out which of a file's folders, people or collections had been put away
    means taking them back out one at a time, while the file itself is invisible.
    """
    user_id = sign_in(client, "admin")
    give_pin(db_path(client), user_id)
    assert client.put(f"/api/folders/{library.folder}/vault", json={"vault": True}).status_code

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    said = _hidden_by(client, "item", library.first)

    assert [(row["source_type"], row["source_name"], row["here"]) for row in said] == [
        ("folder", "clips", False)
    ]


def test_a_file_nobody_has_hidden_says_nothing(client: TestClient, library: Library) -> None:
    """The half that stops the one above passing on a list that is never empty."""
    user_id = sign_in(client, "admin")
    give_pin(db_path(client), user_id)
    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200

    assert _hidden_by(client, "item", library.first) == []


def test_what_is_hiding_a_file_is_not_said_while_the_vault_is_shut(
    client: TestClient, library: Library
) -> None:
    """The names ARE the secret.

    "Hidden by the person Wren Hale" tells somebody who never entered the PIN that Wren Hale
    exists, that she is being kept back, and that this file is hers, which is the whole of what
    concealment is for. Locked, the answer is the one the rest of the app gives: there is nothing
    here.
    """
    user_id = sign_in(client, "admin")
    give_pin(db_path(client), user_id)
    client.put(f"/api/folders/{library.folder}/vault", json={"vault": True})

    # Signed in as an admin, past every access rule there is, and still told nothing.
    assert _hidden_by(client, "item", library.first) == []

    # ...and it is the lock doing that, not an empty library.
    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    assert _hidden_by(client, "item", library.first) != []


def test_what_is_hiding_something_is_asked_and_answered_per_account(
    client: TestClient, library: Library
) -> None:
    """Every user may ask, and each one is told only about what it hid itself.

    The one read in this slice that is not admin-only, because it is not about sharing: it explains
    why a file is off the CALLER's screen. A guest who has hidden nothing is told nothing, even
    where an admin has hidden the very folder the file sits in, answering with another user's
    reasons would hand over which things that user has out of sight.
    """
    sign_in(client, "guest", username="sharing-hidden-nosy")

    answered = client.get(
        "/api/sharing/hidden-by", params={"object_type": "item", "object_id": library.first}
    )
    assert answered.status_code == 200
    assert answered.json() == []


def test_where_it_came_from_answers_about_a_folder_too(
    client: TestClient, library: Library
) -> None:
    """A folder has a chain above it, so it is resolved by the folder read rather than the item one.

    Its own branch in `_reaches`, and the reason the route cannot just ask one question: a file, a
    folder and a tag are three different reads, and answering all three with the item read would
    report the wrong thing for two of them.
    """
    guest = sign_in(client, "guest", username="sharing-folder-why")
    sign_in(client, "admin")
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest,
        effect="share",
    )

    said = client.get(
        "/api/sharing/sources", params={"object_type": "folder", "object_id": library.folder}
    )
    assert said.status_code == 200, said.text
    rows = said.json()
    assert [(row["source_type"], row["here"], row["decides"]) for row in rows] == [
        ("folder", True, True)
    ]


def test_where_it_came_from_answers_about_a_person(client: TestClient, library: Library) -> None:
    """A person has nothing above them, so the whole rule is "a restrict beats a share".

    The third branch, and the one that covers a tag, a collection and a site as well: they are all
    resolved the same way, by reading what this user holds and letting a restrict win.
    """
    guest = sign_in(client, "guest", username="sharing-person-why")
    sign_in(client, "admin")
    _share(
        client,
        object_type="person",
        object_id=library.person,
        subject_user_id=guest,
        effect="share",
    )

    shared = client.get(
        "/api/sharing/sources", params={"object_type": "person", "object_id": library.person}
    ).json()
    assert [(row["effect"], row["decides"]) for row in shared] == [("share", True)]

    # ...and a restrict beats a share made over everything, which is the only way a person can hold
    # two grants at once: a second grant on the SAME object replaces the first, so the losing share
    # has to come from somewhere broader.
    _share(
        client,
        object_type="person",
        object_id=library.person,
        subject_user_id=guest,
        effect="restrict",
    )
    _share(client, object_type="global", object_id=None, subject_user_id=guest, effect="share")

    both = client.get(
        "/api/sharing/sources", params={"object_type": "person", "object_id": library.person}
    ).json()
    assert {(row["source_type"], row["effect"], row["decides"]) for row in both} == {
        ("global", "share", False),
        ("person", "restrict", True),
    }


# --- the reach report: who, other than you, can see this -----------------------------------------


def _reach(client: TestClient, object_type: str, object_id: str | None = None) -> dict[str, Any]:
    found = client.get(
        "/api/sharing/reach",
        params={"object_type": object_type, **({"object_id": object_id} if object_id else {})},
    )
    assert found.status_code == 200, found.text
    return dict(found.json())


def _row(report: dict[str, Any], user_id: str) -> dict[str, Any]:
    rows = [row for row in report["users"] if row["id"] == user_id]
    assert len(rows) == 1, rows
    return dict(rows[0])


def test_the_report_says_who_can_see_a_file_and_leaves_the_asker_out(
    client: TestClient, library: Library
) -> None:
    """The whole question, at its smallest: nobody can, then one user can.

    The asker is absent from their own report. They are reading it, so a row saying they can see
    what they are looking at is the one row that tells them nothing, and on a library with one
    user it would be the entire answer, which reads as a result when it is a mirror.
    """
    guest = sign_in(client, "guest", username="reach-first")
    admin = sign_in(client, "admin")

    before = _reach(client, "item", library.first)
    assert _row(before, guest)["sees"] is False
    assert [row for row in before["users"] if row["id"] == admin] == []

    _share(
        client, object_type="item", object_id=library.first, subject_user_id=guest, effect="share"
    )
    after = _row(_reach(client, "item", library.first), guest)
    assert after["sees"] is True
    assert [(row["how"], row["kind"], row["decides"]) for row in after["through"]] == [
        ("shared", "item", True)
    ]


def test_the_report_explains_a_yes_nobody_wrote_on_the_thing(
    client: TestClient, library: Library
) -> None:
    """The reason this exists rather than the sharing panel being enough.

    Nothing was said about the file. It is reachable because the folder it sits in was handed over,
    and the line has to name that folder. Otherwise the report says "Can see" and cannot say how.
    """
    guest = sign_in(client, "guest", username="reach-inherited")
    sign_in(client, "admin")
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest,
        effect="share",
    )

    row = _row(_reach(client, "item", library.first), guest)
    assert row["sees"] is True
    assert [(line["how"], line["kind"], line["name"]) for line in row["through"]] == [
        ("inherited", "folder", "clips")
    ]


def test_a_restrict_is_named_as_the_line_that_decided(client: TestClient, library: Library) -> None:
    """A share that lost is still listed and must not read as though it is in force.

    It is the answer to the next question every time (what happens when the restrict comes off),
    so it is shown, faded, with `decides` false. Reading both off the stored verdict is what makes
    the two agree: whatever the user can actually see IS the answer.
    """
    guest = sign_in(client, "guest", username="reach-restricted")
    sign_in(client, "admin")
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest,
        effect="share",
    )
    _share(
        client, object_type="tag", object_id=library.tag, subject_user_id=guest, effect="restrict"
    )

    row = _row(_reach(client, "item", library.first), guest)
    assert row["sees"] is False
    assert {(line["how"], line["decides"]) for line in row["through"]} == {
        ("inherited", False),
        ("restricted", True),
    }


def test_a_share_on_a_network_is_reported_on_the_file_its_label_released(
    client: TestClient, library: Library, reach: Reach
) -> None:
    """Sub-studios and networks, the case this report exists for.

    A file is filed under the label that put it out and never under the network above it, so a
    share made on the network reaches it through `sites.parent_id`. The grant chain that EXPLAINS
    a yes has to read it too, or the report says "Can see" with nothing beside it.
    """
    guest = sign_in(client, "guest", username="reach-network")
    sign_in(client, "admin")
    _share(
        client,
        object_type="site",
        object_id=reach.network,
        subject_user_id=guest,
        effect="share",
    )

    row = _row(_reach(client, "item", library.second), guest)
    assert row["sees"] is True
    assert [(line["how"], line["kind"], line["name"]) for line in row["through"]] == [
        ("inherited", "site", "seeded network")
    ]

    # ...and the label's own report says the same thing, which is the other half of the same rule:
    # a label under a shared network is reachable, and the panel on it has to say why.
    on_label = _row(_reach(client, "site", reach.label), guest)
    assert on_label["sees"] is True
    assert [line["name"] for line in on_label["through"]] == ["seeded network"]


def test_a_label_under_a_shared_network_is_shared_but_not_shared_here(
    client: TestClient, reach: Reach
) -> None:
    """The Sites wall's half of the same fact: the label's card says Shared, drawn HOLLOW.

    The repository tells the two apart (`_SITE_MARKS`: the label's own grants against every
    network above it), and the wire has to carry both halves: without the second, the label's card
    reads "this is the thing doing the controlling" over a share made on the network, a switch
    that is not there.
    """
    guest = sign_in(client, "guest", username="mark-network")
    sign_in(client, "admin")
    _share(
        client,
        object_type="site",
        object_id=reach.network,
        subject_user_id=guest,
        effect="share",
    )

    sites = {
        one["id"]: one for one in client.get("/api/sites", params={"limit": 200}).json()["items"]
    }
    assert (sites[reach.label]["shared"], sites[reach.label]["shared_here"]) == (True, False)
    assert (sites[reach.network]["shared"], sites[reach.network]["shared_here"]) == (True, True)


def test_a_share_on_a_photo_set_is_reported_on_the_file_in_it(
    client: TestClient, library: Library, reach: Reach
) -> None:
    """The other arm of the rule the chain explains.

    A Photo Set is a kind of thing that can be shared, so the chain a file's grants are read out of
    has to include it, or a file handed over by its set comes back with a yes and no reason.
    """
    guest = sign_in(client, "guest", username="reach-photo-set")
    sign_in(client, "admin")
    _share(
        client,
        object_type="photo_set",
        object_id=reach.photo_set,
        subject_user_id=guest,
        effect="share",
    )

    row = _row(_reach(client, "item", library.first), guest)
    assert row["sees"] is True
    assert [(line["how"], line["kind"], line["name"]) for line in row["through"]] == [
        ("inherited", "photo_set", "seeded set")
    ]


def test_an_entity_is_seen_when_one_file_under_it_is(client: TestClient, library: Library) -> None:
    """A person is on a guest's wall because a file of theirs is reachable, not because of a grant.

    That is the rule the walls apply, and it is why the yes is read from the stored verdict rather
    than resolved out of the grant rows: a share on the FOLDER puts the person on the wall without
    anything ever having been said about the person.
    """
    guest = sign_in(client, "guest", username="reach-entity")
    sign_in(client, "admin")

    assert _row(_reach(client, "person", library.person), guest)["sees"] is False

    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest,
        effect="share",
    )
    assert _row(_reach(client, "person", library.person), guest)["sees"] is True


def test_an_admin_reads_as_seeing_everything_and_not_as_shared_with(
    client: TestClient, library: Library
) -> None:
    """The role is past every access rule, so the yes is a fact about the user and not a grant.

    Drawn as a share it would be a decision nobody made, and one nobody could revoke.
    """
    other = sign_in(client, "admin", username="reach-second-admin")
    sign_in(client, "admin")

    row = _row(_reach(client, "item", library.first), other)
    assert (row["role"], row["sees"], row["through"]) == ("admin", True, [])


def test_a_disabled_account_cannot_see_what_it_still_holds(
    client: TestClient, library: Library
) -> None:
    """Its verdict rows stay exactly where they were and it can make no request to use them.

    Reported as a no with the flag beside it, because "Can see" about a user that has been
    switched off is the report at its most confidently wrong.
    """
    guest = sign_in(client, "guest", username="reach-disabled")
    sign_in(client, "admin")
    _share(
        client, object_type="item", object_id=library.first, subject_user_id=guest, effect="share"
    )
    assert _row(_reach(client, "item", library.first), guest)["sees"] is True

    switched = client.put(f"/api/auth/users/{guest}/disabled", json={"disabled": True})
    assert switched.status_code == 200, switched.text
    stopped = _row(_reach(client, "item", library.first), guest)
    assert (stopped["sees"], stopped["disabled"]) == (False, True)


def test_the_report_says_what_the_asker_has_hidden_only_once_they_have_unlocked(
    client: TestClient, library: Library
) -> None:
    """Hiding is the caller's own and nobody else's, so it is reported at the top and not per row.

    And it is reported the way everything else names a concealment: not at all while the vault is
    shut, because the names ARE what is being kept back.
    """
    user_id = sign_in(client, "admin")
    give_pin(db_path(client), user_id)
    client.put(f"/api/folders/{library.folder}/vault", json={"vault": True})

    shut = _reach(client, "item", library.first)
    assert (shut["hidden"], shut["concealed"]) == (False, False)

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    open_vault = _reach(client, "item", library.first)
    # Concealed by the folder above it, which is not the file hiding itself.
    assert (open_vault["hidden"], open_vault["concealed"]) == (False, True)

    client.put(f"/api/assets/{library.first}/vault", json={"vault": True})
    itself = _reach(client, "item", library.first)
    assert (itself["hidden"], itself["concealed"]) == (True, True)


def test_the_report_can_name_the_files_behind_an_entity_s_yes(
    client: TestClient, library: Library
) -> None:
    """A yes with no grant behind it, explained over the wire.

    A person is on a guest's wall because ONE file of theirs is reachable, and the thing that let
    that file through was said about the folder it sits in, so the report has a true yes and no
    grant to name beside it. The route asked here is the one that says what did.
    """
    guest = sign_in(client, "guest", username="reach-through-guest")
    sign_in(client, "admin")
    _share(
        client,
        object_type="folder",
        object_id=library.folder,
        subject_user_id=guest,
        effect="share",
    )

    # The state first: a yes with an empty chain, which is what makes the second read worth having.
    row = _row(_reach(client, "person", library.person), guest)
    assert (row["sees"], row["through"]) == (True, [])

    answered = client.get(
        "/api/sharing/reach/through",
        params={"object_type": "person", "object_id": library.person, "user": guest},
    )
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert [(one["kind"], one["id"], one["files"]) for one in body["reasons"]] == [
        ("folder", library.folder, 1)
    ]
    assert (body["files"], body["complete"]) == (1, True)


def test_a_guest_cannot_read_which_files_let_another_account_in(
    client: TestClient, library: Library
) -> None:
    """Admin-only for both of this slice's reasons at once: it is what somebody else can see, and
    it names the folders that let them."""
    guest = sign_in(client, "guest", username="reach-through-nosy")
    refused = client.get(
        "/api/sharing/reach/through",
        params={"object_type": "person", "object_id": library.person, "user": guest},
    )
    assert refused.status_code == 403


def test_asking_which_files_let_an_account_in_about_a_shape_that_cannot_exist_is_refused(
    client: TestClient, library: Library
) -> None:
    """The same refusal the rest of the slice gives, at the same door."""
    sign_in(client, "admin")
    refused = client.get(
        "/api/sharing/reach/through", params={"object_type": "person", "user": "nobody"}
    )
    assert refused.status_code == 422


def test_the_reach_report_says_where_a_thing_stands_outside_this_device(
    client: TestClient, library: Library
) -> None:
    """Do not enrich and Do not swap, each as the switch on the thing and as the whole rule: a file
    under a person kept out of swaps is out without its own switch, and a folder answers for its
    own two marks, which reach every file inside it."""
    sign_in(client, "admin")
    write(
        db_path(client),
        [("UPDATE people SET keep_from_swaps = 1 WHERE id = ?", (library.person,))],
    )

    first = client.get(
        "/api/sharing/reach", params={"object_type": "item", "object_id": library.first}
    )
    second = client.get(
        "/api/sharing/reach", params={"object_type": "item", "object_id": library.second}
    )
    person = client.get(
        "/api/sharing/reach", params={"object_type": "person", "object_id": library.person}
    )
    folder = client.get(
        "/api/sharing/reach", params={"object_type": "folder", "object_id": library.folder}
    )

    assert first.json()["outside"] == {
        "enrich_refused_here": False,
        "enrich_refused": False,
        "enriched_at": None,
        "enriched_by": None,
        "swap_refused_here": False,
        "swap_refused": True,
    }
    assert second.json()["outside"]["swap_refused"] is False
    assert person.json()["outside"]["swap_refused_here"] is True
    assert folder.json()["outside"] == {
        "enrich_refused_here": False,
        "enrich_refused": False,
        "enriched_at": None,
        "enriched_by": None,
        "swap_refused_here": False,
        "swap_refused": False,
    }


def test_the_reach_report_for_a_library_folder_says_nothing_about_outside(
    client: TestClient, library: Library
) -> None:
    """Neither switch is ever asked about a library folder, so there is nothing to say."""
    sign_in(client, "admin")
    answer = client.get(
        "/api/sharing/reach", params={"object_type": "root", "object_id": library.root}
    )
    assert answer.status_code == 200
    assert answer.json()["outside"] is None


def test_a_guest_cannot_read_the_reach_report(client: TestClient, library: Library) -> None:
    """It is a list of what the other users on the instance can see, which is the plainest
    reason anything in this slice is admin-only."""
    sign_in(client, "guest", username="reach-nosy")
    refused = client.get(
        "/api/sharing/reach", params={"object_type": "item", "object_id": library.first}
    )
    assert refused.status_code == 403


def test_asking_the_report_about_a_shape_that_cannot_exist_is_refused(
    client: TestClient, library: Library
) -> None:
    """The same refusal the rest of the slice gives: a tag with no tag names nothing, and an answer
    about it would be an answer about whatever the empty binding happened to match."""
    sign_in(client, "admin")
    assert client.get("/api/sharing/reach", params={"object_type": "tag"}).status_code == 422


# --- a thing that has gone ------------------------------------------------------------------------


@pytest.mark.parametrize("object_type", ["item", "folder", "tag", "person", "collection", "site"])
def test_sharing_something_that_has_gone_is_a_404_not_a_crash(
    client: TestClient, library: Library, object_type: str
) -> None:
    """A grant on a thing that is not there would record an act about nothing: the record refuses
    to write a subject it cannot name, and that refusal must not surface as a 500. The answer is
    that the thing has gone, in the kind's own word, before anything is written."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")
    gone = "01HX00000000000000000000GO"

    answer = client.put(
        "/api/sharing",
        json={
            "object_type": object_type,
            "object_id": gone,
            "subject_user_id": guest_id,
            "effect": "share",
        },
    )
    assert answer.status_code == 404, answer.text
    assert "there's no such" in answer.json()["detail"]
    assert (
        _revoke(
            client,
            object_type=object_type,
            object_id=gone,
            subject_user_id=guest_id,
            effect="share",
        )
        == 404
    )
