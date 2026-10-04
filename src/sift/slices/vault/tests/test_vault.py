# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the vault does, asked at the API.

The vault's promise is "it is not there", so the routes a screen asks are what is tested. Almost
everything is asked as an admin, because the vault conceals from admins too.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.auth.unlocks import VaultUnlockStore
from sift.slices.vault.tests.conftest import (
    NEVER_EXISTED,
    Library,
    db_path,
    demote,
    grid_ids,
    grid_total,
    lock,
    set_asset_vault,
    set_folder_vault,
    share,
    sign_in,
    sign_in_with_pin,
    unlock,
    unlock_key,
)
from sift.testing.auth import TEST_PIN, give_pin

pytestmark = pytest.mark.integration


def _placeholder_mode(client: TestClient) -> None:
    assert (
        client.put("/api/settings", json={"values": {"vault.concealment": "placeholder"}})
    ).status_code == 204


# --- the vault needs a PIN --------------------------------------------------------------------


def test_nothing_can_be_put_in_the_vault_without_a_pin(
    client: TestClient, library: Library
) -> None:
    """Nothing of any kind can be put in the vault without a PIN."""
    sign_in(client)

    assert set_asset_vault(client, library.first, True).status_code == 409
    assert set_folder_vault(client, library.clips, True).status_code == 409

    person = client.post("/api/people", json={"name": "Someone", "vault": True})
    assert person.status_code == 409

    collection = client.post("/api/collections", json={"name": "Private"}).json()["id"]
    assert client.put(f"/api/collections/{collection}/vault", json={"vault": True}).status_code == (
        409
    )

    assert grid_ids(client) == [library.elsewhere, library.second, library.first]


def test_a_person_already_here_cannot_be_moved_into_the_vault_without_a_pin(
    client: TestClient,
) -> None:
    """The vault flag on a person's edit is refused without a PIN; the edit alone is not."""
    sign_in(client)
    person = client.post("/api/people", json={"name": "Someone"}).json()["id"]

    assert client.put(
        f"/api/people/{person}", json={"name": "Someone", "vault": True}
    ).status_code == (409)
    assert client.put(f"/api/people/{person}", json={"name": "Renamed"}).status_code == 200


def test_a_pin_is_what_lifts_the_refusal(client: TestClient, library: Library) -> None:
    """A PIN lifts the refusal."""
    user_id = sign_in(client)
    assert set_asset_vault(client, library.first, True).status_code == 409

    give_pin(db_path(client), user_id)
    assert set_asset_vault(client, library.first, True).status_code == 204


def test_taking_something_back_out_does_not_need_a_pin_of_its_own(
    client: TestClient, library: Library
) -> None:
    """Taking something out needs no PIN of its own: reaching it already took one."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)
    unlock(client)

    assert set_asset_vault(client, library.first, False).status_code == 204
    assert library.first in grid_ids(client)


# --- opening it always costs the PIN ----------------------------------------------------------


def test_showing_hidden_things_always_asks_for_the_pin_again(
    client: TestClient, library: Library
) -> None:
    """Unlocking always asks for the PIN, even when already open: revealing is deliberate."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)
    assert unlock(client) == 200

    assert client.post("/api/vault/unlock", json={}).status_code == 422
    assert unlock(client, "000000") == 401
    # The refusal did not shut what was open.
    assert client.get("/api/vault").json()["unlocked"] is True


def test_a_wrong_pin_never_reveals_anything(client: TestClient, library: Library) -> None:
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)

    assert unlock(client, "000000") == 401
    assert client.get("/api/vault").json()["unlocked"] is False
    assert library.first not in grid_ids(client)


def test_guessing_at_the_vault_is_throttled_by_the_same_counter_as_the_lock_screen(
    client: TestClient,
) -> None:
    """The vault shares the lock screen's attempt counter."""
    sign_in_with_pin(client)
    for _ in range(5):
        assert unlock(client, "000000") == 401
    assert unlock(client, "000000") == 429
    # Even the correct PIN waits out the lockout.
    assert unlock(client) == 429


def test_a_guest_opens_their_own_hidden_and_never_the_admins(
    client: TestClient, library: Library
) -> None:
    """Each user opens only what they hid; an admin's hidden file was never hidden from a guest."""
    admin_id = sign_in_with_pin(client, "admin")
    set_asset_vault(client, library.first, True)
    share(client, library.first, sign_in(client, "guest", who="visitor"))

    guest_id = sign_in(client, "guest", who="visitor")
    give_pin(db_path(client), guest_id)
    assert unlock(client) == 200

    assert library.first in grid_ids(client)
    assert guest_id != admin_id


def test_a_guest_may_still_shut_it(client: TestClient) -> None:
    """Anyone may shut it: every lock trigger comes through this route."""
    sign_in(client, "guest", who="visitor")
    assert lock(client) == 204


# --- fully gone -------------------------------------------------------------------------------


def test_a_hidden_asset_is_absent_from_the_grid_and_the_count_for_the_admin(
    client: TestClient, library: Library
) -> None:
    """A hidden asset is absent from an admin's grid and from the count."""
    sign_in_with_pin(client)
    assert grid_total(client) == 3

    assert set_asset_vault(client, library.first, True).status_code == 204

    assert library.first not in grid_ids(client)
    assert grid_total(client) == 2
    assert client.get(f"/api/assets/{library.first}").status_code == 404
    assert client.get(f"/api/assets/{library.first}/thumb").status_code == 404
    assert client.get(f"/api/assets/{library.first}/preview").status_code == 404
    assert client.get(f"/api/assets/{library.first}/stream").status_code == 404


def test_unlocking_brings_it_back_everywhere_and_locking_takes_it_away_again(
    client: TestClient, library: Library
) -> None:
    """Unlocking brings it back everywhere, and locking takes it away again."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)

    assert unlock(client) == 200
    assert library.first in grid_ids(client)
    assert grid_total(client) == 3
    assert client.get(f"/api/assets/{library.first}").status_code == 200

    assert lock(client) == 204
    assert library.first not in grid_ids(client)
    assert grid_total(client) == 2


def test_a_hidden_folder_conceals_everything_in_it(client: TestClient, library: Library) -> None:
    """A hidden folder conceals everything in it."""
    sign_in_with_pin(client)

    assert set_folder_vault(client, library.clips, True).status_code == 204

    assert grid_ids(client) == [library.elsewhere]
    assert grid_total(client) == 1
    for asset_id in library.in_clips:
        assert client.get(f"/api/assets/{asset_id}").status_code == 404

    assert unlock(client) == 200
    assert grid_total(client) == 3


# --- locked placeholders ----------------------------------------------------------------------


def test_in_placeholder_mode_the_tile_stays_and_the_bytes_do_not(
    client: TestClient, library: Library
) -> None:
    """In placeholder mode the tile stays and every route that hands over bytes refuses."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)
    _placeholder_mode(client)

    assert library.first in grid_ids(client)
    assert grid_total(client) == 3

    tile = next(
        item for item in client.get("/api/assets").json()["items"] if item["id"] == library.first
    )
    assert tile["concealed"] is True
    # The tile carries nothing that says what it stands for.
    assert not tile.get("original_filename")
    assert not tile.get("duration_ms")

    assert client.get(f"/api/assets/{library.first}/thumb").status_code == 404
    assert client.get(f"/api/assets/{library.first}/preview").status_code == 404
    assert client.get(f"/api/assets/{library.first}/stream").status_code == 404


def test_a_guests_placeholder_mode_shows_their_own_gaps_and_nobody_elses(
    client: TestClient, library: Library
) -> None:
    """Placeholder mode shows a user their own gaps; an admin's hidden file is an ordinary tile to
    a guest."""
    guest_id = sign_in(client, "guest", who="visitor")
    share(client, library.first, guest_id)
    share(client, library.second, guest_id)
    give_pin(db_path(client), guest_id)

    sign_in_with_pin(client, "admin")
    assert set_asset_vault(client, library.first, True).status_code == 204

    sign_in(client, "guest", who="visitor")
    _placeholder_mode(client)
    assert set_asset_vault(client, library.second, True).status_code == 204

    body = client.get("/api/assets").json()
    concealed = {item["id"] for item in body["items"] if item["concealed"]}
    assert concealed == {library.second}
    assert library.first in {item["id"] for item in body["items"]}
    assert client.get(f"/api/assets/{library.second}/stream").status_code == 404


def test_a_placeholder_cannot_be_taken_out_of_the_vault(
    client: TestClient, library: Library
) -> None:
    """A placeholder cannot be taken out of the vault without opening it."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)
    set_folder_vault(client, library.other, True)
    _placeholder_mode(client)

    assert set_asset_vault(client, library.first, False).status_code == 404
    assert set_folder_vault(client, library.other, False).status_code == 404

    assert unlock(client) == 200
    assert set_asset_vault(client, library.first, False).status_code == 204
    assert set_folder_vault(client, library.other, False).status_code == 204


# --- the way out, for every kind of thing -----------------------------------------------------


def test_a_vaulted_collection_can_be_taken_back_out_once_the_vault_is_open(
    client: TestClient,
) -> None:
    """A vaulted collection can be taken out once the vault is open."""
    sign_in_with_pin(client)
    collection = client.post("/api/collections", json={"name": "Private"}).json()["id"]
    assert client.put(f"/api/collections/{collection}/vault", json={"vault": True}).status_code == (
        204
    )
    assert client.get("/api/collections").json()["items"] == []

    assert unlock(client) == 200
    assert client.put(
        f"/api/collections/{collection}/vault", json={"vault": False}
    ).status_code == (204)
    assert lock(client) == 204
    assert [entry["id"] for entry in client.get("/api/collections").json()["items"]] == [collection]


def test_renaming_somebody_does_not_take_them_out_of_the_vault(client: TestClient) -> None:
    """A rename without `vault` leaves the person in the vault: an absent field is not `false`."""
    sign_in_with_pin(client)
    person = client.post("/api/people", json={"name": "Someone"}).json()["id"]
    client.put(f"/api/people/{person}", json={"name": "Someone", "vault": True})
    assert unlock(client) == 200

    assert client.put(f"/api/people/{person}", json={"name": "Renamed"}).json()["vault"] is True

    assert lock(client) == 204
    assert client.get(f"/api/people/{person}/aliases").status_code == 404, "still concealed"


def test_a_vaulted_person_can_be_edited_deleted_and_taken_back_out_once_the_vault_is_open(
    client: TestClient,
) -> None:
    """A vaulted person can be edited, deleted and taken out once the vault is open."""
    sign_in_with_pin(client)
    person = client.post("/api/people", json={"name": "Someone"}).json()["id"]
    assert client.put(
        f"/api/people/{person}", json={"name": "Someone", "vault": True}
    ).status_code == (200)
    assert client.get(f"/api/people/{person}/aliases").status_code == 404

    assert unlock(client) == 200
    assert client.get(f"/api/people/{person}/aliases").status_code == 200
    # An absent flag means "leave it", so taking out says so.
    taken_out = client.put(f"/api/people/{person}", json={"name": "Renamed", "vault": False})
    assert taken_out.json()["vault"] is False

    assert client.put(
        f"/api/people/{person}", json={"name": "Renamed", "vault": True}
    ).status_code == (200)
    assert client.delete(f"/api/people/{person}").status_code == 204


# --- what a locked vault is, and is not -------------------------------------------------------


def test_an_unlock_is_this_browser_only(client: TestClient, second_browser: TestClient) -> None:
    """An unlock belongs to one browser."""
    sign_in_with_pin(client)
    assert unlock(client) == 200

    sign_in_with_pin(second_browser, who="second-device")
    assert second_browser.get("/api/vault").json()["unlocked"] is False


def test_nothing_about_an_unlock_survives_the_process(client: TestClient, library: Library) -> None:
    """Nothing about an unlock survives the process, which is why the PIN may be short; the
    session itself survives, with the vault shut."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)
    assert unlock(client) == 200
    assert library.first in grid_ids(client)

    running = client.app.state.auth.vault_unlocks  # type: ignore[attr-defined]
    assert running.is_unlocked(unlock_key(client)) is True
    assert VaultUnlockStore().is_unlocked(unlock_key(client)) is False

    assert client.get("/api/auth/me").status_code == 200, (
        "the session outlives what the vault knows"
    )


def test_signing_out_drops_the_unlock_rather_than_leaving_it_behind(
    client: TestClient, second_browser: TestClient
) -> None:
    """Signing out drops the session's unlock from the store; another browser keeps its own. Asked
    of the store, since no route can reach a revoked session."""
    sign_in_with_pin(client)
    sign_in_with_pin(second_browser, who="second-device")
    ending = unlock_key(client)
    assert unlock(client) == 200
    assert unlock(second_browser) == 200

    unlocks = client.app.state.auth.vault_unlocks  # type: ignore[attr-defined]
    assert unlocks.is_unlocked(ending) is True

    assert client.post("/api/auth/logout").status_code == 204

    assert unlocks.is_unlocked(ending) is False
    assert second_browser.get("/api/vault").json()["unlocked"] is True


def test_hidden_survives_an_account_being_demoted_because_it_was_never_the_role_s(
    client: TestClient, library: Library
) -> None:
    """Hidden survives a demotion: it belongs to the user, not the role."""
    user_id = sign_in_with_pin(client)
    share(client, library.first, user_id)
    assert set_asset_vault(client, library.first, True).status_code == 204
    assert unlock(client) == 200
    assert library.first in grid_ids(client)

    demote(client, user_id)

    # Still theirs and still openable.
    assert lock(client) == 204
    assert client.get("/api/vault").json()["unlocked"] is False
    assert library.first not in grid_ids(client)
    assert unlock(client) == 200
    assert library.first in grid_ids(client)


def test_unlocking_the_vault_does_not_hand_over_the_master_key(client: TestClient) -> None:
    """Unlocking the vault does not hand over the saved-login key: the PIN is a screen lock."""
    user_id = sign_in_with_pin(client)
    keys = client.app.state.master_keys  # type: ignore[attr-defined]
    keys.forget(user_id)

    assert unlock(client) == 200
    assert keys.get(user_id) is None


# --- state, and the shape of a refusal --------------------------------------------------------


def test_the_state_route_says_whether_there_is_a_pin_at_all(client: TestClient) -> None:
    """The state route says whether there is a PIN, so screens can prompt before hiding."""
    user_id = sign_in(client)
    assert client.get("/api/vault").json() == {"unlocked": False, "pin_set": False}

    give_pin(db_path(client), user_id)
    assert client.get("/api/vault").json() == {"unlocked": False, "pin_set": True}

    assert unlock(client, TEST_PIN) == 200
    assert client.get("/api/vault").json() == {"unlocked": True, "pin_set": True}


def test_a_concealed_thing_answers_a_vault_write_the_way_an_unknown_id_does(
    client: TestClient, library: Library
) -> None:
    """A concealed id answers a vault write as an unknown id does."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)
    set_folder_vault(client, library.clips, True)

    for asset_id in (library.first, NEVER_EXISTED):
        assert set_asset_vault(client, asset_id, True).status_code == 404, asset_id
        assert set_asset_vault(client, asset_id, False).status_code == 404, asset_id
    for folder_id in (library.clips, NEVER_EXISTED):
        assert set_folder_vault(client, folder_id, True).status_code == 404, folder_id
        assert set_folder_vault(client, folder_id, False).status_code == 404, folder_id


def test_locking_a_vault_that_was_never_open_is_not_an_error(client: TestClient) -> None:
    """Locking a vault that was never open is not an error."""
    sign_in_with_pin(client)
    assert lock(client) == 204
    assert lock(client) == 204


# --- what a hidden file is TOLD, which is not what an absent one is told -----------------------
#
# To the one user who hid a file, the vault says what is wrong and how to undo it
# (`sift.kernel.reach`): never "no such file", never a sign-in prompt.


def test_a_write_to_a_hidden_file_says_so_and_never_ends_the_session(
    client: TestClient, library: Library
) -> None:
    """Every write naming one hidden file answers 423 with the vault's sentence and without
    `Sift-Locked`, which only the app lock carries and which sends the browser to the lock screen."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)
    _placeholder_mode(client)

    writes = (
        client.put(f"/api/assets/{library.first}/favorite", json={"favorite": True}),
        client.put(f"/api/assets/{library.first}/rating", json={"rating": 4}),
        client.put(f"/api/assets/{library.first}/pin", json={"pinned": True}),
    )
    for answer in writes:
        assert answer.status_code == 423, answer.request.url
        # Compared on content, so the wording can improve.
        assert "vault" in answer.json()["detail"].lower()
        assert "sift-locked" not in {name.lower() for name in answer.headers}, answer.request.url


def test_the_app_lock_is_the_only_423_that_carries_the_mark(client: TestClient) -> None:
    """The app lock is the only 423 carrying the mark, so neither side can drift alone."""
    sign_in_with_pin(client)
    # It answers a body, so 200.
    assert client.post("/api/auth/lock").status_code == 200

    shut = client.get("/api/assets")
    assert shut.status_code == 423
    assert shut.headers.get("sift-locked") == "session"


def test_moving_or_renaming_a_hidden_file_says_the_vault_rather_than_no_such_file(
    client: TestClient, library: Library
) -> None:
    """Moving or renaming a hidden file says the vault, not "no such file"."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)
    _placeholder_mode(client)

    renamed = client.post(f"/api/assets/{library.first}/rename", json={"name": "new.mp4"})
    assert renamed.status_code == 423
    assert "vault" in renamed.json()["detail"].lower()

    asked = client.get(f"/api/assets/{library.first}/organize")
    assert asked.status_code == 423
    assert "vault" in asked.json()["detail"].lower()

    # An id naming nothing keeps the plain 404, so this is no existence oracle.
    missing = client.post(f"/api/assets/{NEVER_EXISTED}/rename", json={"name": "new.mp4"})
    assert missing.status_code == 404


def test_deleting_a_hidden_file_offers_the_unlock_rather_than_denying_it_exists(
    client: TestClient, library: Library
) -> None:
    """Both delete routes offer the unlock; the bulk route's `vault_locked` draws the button."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)
    _placeholder_mode(client)

    one = client.request("DELETE", f"/api/assets/{library.first}")
    assert one.status_code == 423
    assert "vault" in one.json()["detail"].lower()

    many = client.post("/api/assets/delete", json={"asset_ids": [library.first], "mode": "sift"})
    assert many.status_code == 200
    body = many.json()
    assert body["changed"] == 0
    assert body["skipped"] == 1
    assert "vault" in (body["reason"] or "").lower()
    assert body["vault_locked"] is True


# --- a selection, in one request ----------------------------------------------------------------
#
# One request hides a selection: every id written, the PIN still required to conceal, and an
# unreachable file skipped with a reason.


def set_assets_vault(client: TestClient, asset_ids: list[str], vault: bool):  # type: ignore[no-untyped-def]
    return client.post("/api/assets/vault", json={"asset_ids": asset_ids, "vault": vault})


def test_one_call_hides_a_whole_selection(client: TestClient, library: Library) -> None:
    sign_in_with_pin(client)

    done = set_assets_vault(client, library.in_clips, True)

    assert done.status_code == 200, done.text
    assert done.json() == {
        "changed": 2,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert grid_ids(client) == [library.elsewhere]


def test_one_call_brings_a_whole_selection_back(client: TestClient, library: Library) -> None:
    """Bringing a selection back needs only an open vault."""
    sign_in_with_pin(client)
    set_assets_vault(client, library.in_clips, True)
    unlock(client)

    assert set_assets_vault(client, library.in_clips, False).json()["changed"] == 2

    lock(client)
    assert sorted(grid_ids(client)) == sorted([library.first, library.second, library.elsewhere])


def test_a_selection_cannot_be_hidden_without_a_pin(client: TestClient, library: Library) -> None:
    """A selection cannot be hidden without a PIN."""
    sign_in(client)

    assert set_assets_vault(client, library.in_clips, True).status_code == 409
    assert grid_ids(client) == [library.elsewhere, library.second, library.first]


def test_a_file_the_caller_cannot_reach_is_skipped_rather_than_failing_the_selection(
    client: TestClient, library: Library
) -> None:
    """A file the caller cannot reach is skipped and counted, without saying whether it exists."""
    guest = sign_in_with_pin(client, role="guest")
    share(client, library.first, guest)

    done = set_assets_vault(client, [library.first, library.second], True)

    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["changed"], body["skipped"]) == (1, 1)
    assert body["vault_locked"] is False
    assert body["reason"] and "could not find" in body["reason"].lower()
    assert library.first not in grid_ids(client)


def test_a_file_already_in_the_locked_vault_is_skipped_with_the_unlock_offer(
    client: TestClient, library: Library
) -> None:
    """A file in the locked vault is skipped with the unlock offer as a flag."""
    sign_in_with_pin(client)
    set_asset_vault(client, library.first, True)

    done = set_assets_vault(client, [library.first, library.second], True)

    body = done.json()
    assert (body["changed"], body["skipped"]) == (1, 1)
    assert body["vault_locked"] is True
    assert body["reason"] and "vault" in body["reason"].lower()


def test_the_single_route_still_answers_with_no_body_at_all(
    client: TestClient, library: Library
) -> None:
    """The single route still answers with no body."""
    sign_in_with_pin(client)

    assert set_asset_vault(client, library.first, True).status_code == 204
    assert library.first not in grid_ids(client)


def test_a_hidden_tag_site_or_photo_set_deletes_only_with_the_vault_open(
    client: TestClient,
) -> None:
    """A hidden tag, site or Photo Set is a 404 behind a locked vault, deletable when open."""
    sign_in_with_pin(client)
    made = {
        "tags": client.post("/api/tags", json={"name": "Kept aside"}).json()["id"],
        "sites": client.post("/api/sites", json={"name": "Kept Aside Site"}).json()["id"],
        "photo-sets": client.post("/api/photo-sets", json={"name": "Kept aside set"}).json()["id"],
    }
    for kind, one in made.items():
        assert client.put(f"/api/{kind}/{one}/vault", json={"vault": True}).status_code == 204

    for kind, one in made.items():
        assert client.delete(f"/api/{kind}/{one}").status_code == 404, kind

    assert unlock(client) == 200
    for kind, one in made.items():
        assert client.delete(f"/api/{kind}/{one}").status_code == 204, kind
