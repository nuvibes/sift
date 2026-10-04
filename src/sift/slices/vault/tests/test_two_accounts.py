# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two users, each with their own Hidden, asked at the API: every test asserts the boundary."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.vault.tests.conftest import (
    Library,
    db_path,
    grid_ids,
    lock,
    set_asset_vault,
    share,
    sign_in,
    sign_in_with_pin,
    unlock,
)
from sift.testing.auth import TEST_PIN, give_pin

pytestmark = pytest.mark.integration

#: A PIN that is nobody's. Used where the point is that the wrong secret is being offered.
OTHER_PIN = "135791"


def test_each_account_opens_its_own_and_neither_pin_opens_the_other(
    client: TestClient, library: Library
) -> None:
    """Each PIN opens only its own user's hidden set, in both directions."""
    guest_id = sign_in(client, "guest", who="visitor")
    give_pin(db_path(client), guest_id, OTHER_PIN)
    share(client, library.first, guest_id)
    share(client, library.second, guest_id)

    admin_id = sign_in_with_pin(client, "admin")
    assert set_asset_vault(client, library.first, True).status_code == 204
    assert admin_id != guest_id

    sign_in(client, "guest", who="visitor")
    assert set_asset_vault(client, library.second, True).status_code == 204

    # An admin's PIN does not open the guest's Hidden.
    assert unlock(client, TEST_PIN) == 401
    assert library.second not in grid_ids(client)
    # Their own does.
    assert unlock(client, OTHER_PIN) == 200
    assert library.second in grid_ids(client)

    sign_in_with_pin(client, "admin")
    assert unlock(client, OTHER_PIN) == 401
    assert library.first not in grid_ids(client)
    assert unlock(client, TEST_PIN) == 200
    assert library.first in grid_ids(client)


def test_neither_accounts_hidden_things_appear_in_the_others_reads(
    client: TestClient, library: Library
) -> None:
    """Hidden by one is ordinary to the other, even with Hidden open."""
    guest_id = sign_in(client, "guest", who="visitor")
    give_pin(db_path(client), guest_id, OTHER_PIN)
    share(client, library.first, guest_id)
    share(client, library.second, guest_id)

    sign_in_with_pin(client, "admin")
    set_asset_vault(client, library.first, True)

    sign_in(client, "guest", who="visitor")
    set_asset_vault(client, library.second, True)
    unlock(client, OTHER_PIN)

    # With their own open, the guest sees both: theirs because it is revealed, the other account's
    # file because it was never hidden from them.
    assert set(grid_ids(client)) >= {library.first, library.second}
    hidden_for_guest = client.get(
        "/api/sharing/hidden-by", params={"object_type": "item", "object_id": library.first}
    )
    assert hidden_for_guest.json() == [], "the admin's reasons were handed to the guest"

    # Shut again, and only their own goes.
    lock(client)
    ids = grid_ids(client)
    assert library.first in ids
    assert library.second not in ids


def test_a_failed_pin_on_one_account_does_not_spend_the_others_budget(
    client: TestClient, library: Library
) -> None:
    """A failed PIN on one account does not spend the other's budget."""
    guest_id = sign_in(client, "guest", who="visitor")
    give_pin(db_path(client), guest_id, OTHER_PIN)

    # The guest spends their whole budget and is locked out.
    statuses = [unlock(client, "000000") for _ in range(12)]
    assert 429 in statuses, "the guest was not locked out, so this proves nothing about the admin"
    assert unlock(client, OTHER_PIN) == 429

    # The other account's is untouched, and its correct PIN still works first time.
    sign_in_with_pin(client, "admin")
    assert unlock(client, TEST_PIN) == 200


def test_and_a_failed_pin_still_spends_the_accounts_own_budget(
    client: TestClient, library: Library
) -> None:
    """A failed PIN still spends its own account's budget."""
    sign_in_with_pin(client, "admin")

    statuses = [unlock(client, "000000") for _ in range(12)]

    assert 401 in statuses, "the first wrong guesses are simply wrong"
    assert 429 in statuses, "a run of them locks out"
    assert unlock(client, TEST_PIN) == 429, "the correct PIN is refused while the lockout stands"


def test_a_guest_with_no_pin_cannot_hide_anything_either(
    client: TestClient, library: Library
) -> None:
    """The refusal in front of hiding applies to everybody, not only the user who had it first.

    A guest allowed to hide something with no PIN has not hidden it: they have lost it, with
    nothing that opens it again.
    """
    guest_id = sign_in(client, "guest", who="visitor")
    share(client, library.first, guest_id)

    assert set_asset_vault(client, library.first, True).status_code == 409

    give_pin(db_path(client), guest_id, OTHER_PIN)
    assert set_asset_vault(client, library.first, True).status_code == 204
