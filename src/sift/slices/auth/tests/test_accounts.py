# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making a guest, and everything an admin can do to one afterwards.

Over real HTTP, because the whole point of this surface is that it is the way a second user
comes into being in a running Sift. Everything underneath it (the roles, the grants, the
disabled flag) is tested where it lives, so what these tests
are about is the door: that it opens for an admin, that it does not open for a guest, and that what
comes out the other side is a user somebody can really sign in as.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.slices import auth
from sift.slices.auth import AuthService, Hasher
from sift.slices.auth.crypto import resolve_argon2_params

pytestmark = [pytest.mark.integration]

PASSWORD = "Corr3ct-Horse!staple9"
GUEST_PASSWORD = "An0ther-Secur3!keyword"
GUEST_PASSWORD_TWO = "Third-Passw0rd!phrase"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        app = c.app
        # The floor hashing parameters, so a suite that signs in many times is not paced by a
        # deliberately expensive function. Still a real Argon2id hash.
        app.state.auth = AuthService(  # type: ignore[attr-defined]
            app.state.database,  # type: ignore[attr-defined]
            hasher=Hasher(resolve_argon2_params(None)),
            master_keys=app.state.master_keys,  # type: ignore[attr-defined]
            queue=app.state.queue,  # type: ignore[attr-defined]
            session_ttl_seconds=auth.DEFAULT_SESSION_DAYS * 24 * 60 * 60,
        )
        yield c
    get_settings.cache_clear()


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER_NAME: client.get("/api/auth/me").json()["csrf_token"]}


def _session_of(client: TestClient) -> str:
    """The session handle this client is currently holding, so a test can put it back later."""
    return str(client.cookies.get(SESSION_COOKIE_NAME))


def _become(client: TestClient, session: str) -> None:
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, session)


def _admin(client: TestClient) -> None:
    """Create the one admin and leave the client signed in as them."""
    response = client.post("/api/auth/setup", json={"username": "kate", "password": PASSWORD})
    assert response.status_code == 201, response.text


def _make_guest(client: TestClient, username: str = "sam", password: str = GUEST_PASSWORD) -> str:
    response = client.post(
        "/api/auth/users",
        json={"username": username, "password": password},
        headers=_csrf(client),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _sign_in(client: TestClient, username: str, password: str) -> int:
    """Sign the client in as somebody, dropping whoever it was before. Returns the status."""
    client.cookies.clear()
    return int(
        client.post(
            "/api/auth/login", json={"username": username, "password": password}
        ).status_code
    )


# --- the door itself ----------------------------------------------------------------------------


def test_an_admin_creates_a_guest_who_can_then_sign_in(client: TestClient) -> None:
    """The whole feature in one test: without this endpoint there is no way for a second user to
    come into being, so guest would be a role nobody could ever hold."""
    _admin(client)

    created = client.post(
        "/api/auth/users",
        json={"username": "sam", "password": GUEST_PASSWORD},
        headers=_csrf(client),
    )

    assert created.status_code == 201
    assert created.json()["role"] == "guest"
    assert created.json()["disabled"] is False

    assert _sign_in(client, "sam", GUEST_PASSWORD) == 200
    assert client.get("/api/auth/me").json()["role"] == "guest"


def test_a_guest_changes_the_password_the_admin_chose(client: TestClient) -> None:
    """Not forced, offered. An admin picks a first password and says it out loud; changing it is
    the guest's own decision, made with the password they already have."""
    _admin(client)
    _make_guest(client)
    _sign_in(client, "sam", GUEST_PASSWORD)

    changed = client.post(
        "/api/auth/password",
        json={"old_password": GUEST_PASSWORD, "new_password": GUEST_PASSWORD_TWO},
        headers=_csrf(client),
    )

    assert changed.status_code == 204
    assert _sign_in(client, "sam", GUEST_PASSWORD) == 401
    assert _sign_in(client, "sam", GUEST_PASSWORD_TWO) == 200


def test_a_name_that_is_already_an_account_is_refused_by_name(client: TestClient) -> None:
    """Said plainly rather than fudged. This is not a login screen: an admin can see the list of
    users on the same page, so pretending the name might be free helps nobody."""
    _admin(client)
    _make_guest(client)

    again = client.post(
        "/api/auth/users",
        json={"username": "sam", "password": GUEST_PASSWORD_TWO},
        headers=_csrf(client),
    )

    assert again.status_code == 409


def test_a_name_that_differs_only_in_case_is_the_same_account(client: TestClient) -> None:
    """The column folds case, so Sam and sam are one person. Refusing here is what stops two rows
    being made that the login could then not tell apart."""
    _admin(client)
    _make_guest(client)

    again = client.post(
        "/api/auth/users",
        json={"username": "SAM", "password": GUEST_PASSWORD_TWO},
        headers=_csrf(client),
    )

    assert again.status_code == 409


def test_a_blank_username_is_refused(client: TestClient) -> None:
    """Spaces are not a name, and a user whose name cannot be typed cannot be signed in to."""
    _admin(client)

    response = client.post(
        "/api/auth/users",
        json={"username": "   ", "password": GUEST_PASSWORD},
        headers=_csrf(client),
    )

    assert response.status_code == 409


def test_a_guests_first_password_meets_the_same_policy_as_anybody_elses(
    client: TestClient,
) -> None:
    """An admin choosing somebody else's password is exactly the case where a weak one gets
    chosen: it is not the chooser who has to live with it."""
    _admin(client)

    response = client.post(
        "/api/auth/users",
        json={"username": "sam", "password": "weak"},
        headers=_csrf(client),
    )

    assert response.status_code == 422


# --- what an admin can do to a guest ------------------------------------------------------------


def test_the_account_list_names_everybody_including_the_admin(client: TestClient) -> None:
    """An admin is listed even though nothing here can act on them. Leaving them out would make
    this list disagree with the sharing screen, which has to name every user there is."""
    _admin(client)
    _make_guest(client)

    listed = client.get("/api/auth/users")

    assert listed.status_code == 200
    assert [(row["username"], row["role"]) for row in listed.json()] == [
        ("kate", "admin"),
        ("sam", "guest"),
    ]


def test_disabling_a_guest_stops_them_signing_in(client: TestClient) -> None:
    _admin(client)
    guest_id = _make_guest(client)

    disabled = client.put(
        f"/api/auth/users/{guest_id}/disabled",
        json={"disabled": True},
        headers=_csrf(client),
    )

    assert disabled.status_code == 200
    assert disabled.json()["disabled"] is True
    assert _sign_in(client, "sam", GUEST_PASSWORD) == 401


def test_disabling_ends_a_session_the_guest_already_had(client: TestClient) -> None:
    """The dangerous half. Refusing the next sign-in does nothing for somebody who is signed in
    right now, on a browser that will not ask again for a month."""
    _admin(client)
    guest_id = _make_guest(client)
    admin_csrf = _csrf(client)
    admin_session = _session_of(client)

    _sign_in(client, "sam", GUEST_PASSWORD)
    guest_session = _session_of(client)
    assert client.get("/api/auth/me").status_code == 200

    _become(client, admin_session)
    client.put(
        f"/api/auth/users/{guest_id}/disabled",
        json={"disabled": True},
        headers=admin_csrf,
    )

    _become(client, guest_session)
    assert client.get("/api/auth/me").status_code == 401


def test_a_session_disabling_ended_does_not_come_back_when_the_account_does(
    client: TestClient,
) -> None:
    """The half that the live re-read does not give for free, and the reason the sessions are
    dropped rather than merely refused.

    Refusing every request while the flag is set would look identical on screen. The difference
    shows up on the way back: without dropping the rows, turning the user on again silently
    restores whatever browser was left open and still holding the old cookie, so somebody
    disabled on Friday and re-enabled on Monday is signed in on a machine nobody thought about.
    Signing in again gets them back; the abandoned cookie does not.
    """
    _admin(client)
    guest_id = _make_guest(client)
    admin_csrf = _csrf(client)
    admin_session = _session_of(client)

    _sign_in(client, "sam", GUEST_PASSWORD)
    stale_session = _session_of(client)

    _become(client, admin_session)
    for state in (True, False):
        client.put(
            f"/api/auth/users/{guest_id}/disabled",
            json={"disabled": state},
            headers=admin_csrf,
        )

    _become(client, stale_session)
    assert client.get("/api/auth/me").status_code == 401
    assert _sign_in(client, "sam", GUEST_PASSWORD) == 200


def test_enabling_a_guest_again_lets_them_back_in(client: TestClient) -> None:
    _admin(client)
    guest_id = _make_guest(client)
    client.put(
        f"/api/auth/users/{guest_id}/disabled",
        json={"disabled": True},
        headers=_csrf(client),
    )

    back = client.put(
        f"/api/auth/users/{guest_id}/disabled",
        json={"disabled": False},
        headers=_csrf(client),
    )

    assert back.status_code == 200
    assert back.json()["disabled"] is False
    assert _sign_in(client, "sam", GUEST_PASSWORD) == 200


def test_an_admin_resets_a_forgotten_guest_password(client: TestClient) -> None:
    _admin(client)
    guest_id = _make_guest(client)

    reset = client.post(
        f"/api/auth/users/{guest_id}/password",
        json={"new_password": GUEST_PASSWORD_TWO},
        headers=_csrf(client),
    )

    assert reset.status_code == 204
    assert _sign_in(client, "sam", GUEST_PASSWORD) == 401
    assert _sign_in(client, "sam", GUEST_PASSWORD_TWO) == 200


def test_a_reset_ends_the_sessions_the_old_password_opened(client: TestClient) -> None:
    """A reset is what an admin does when they think somebody else has the password. A session
    that password already opened surviving it would make the reset a gesture."""
    _admin(client)
    guest_id = _make_guest(client)
    admin_csrf = _csrf(client)
    admin_session = _session_of(client)

    _sign_in(client, "sam", GUEST_PASSWORD)
    guest_session = _session_of(client)

    _become(client, admin_session)
    client.post(
        f"/api/auth/users/{guest_id}/password",
        json={"new_password": GUEST_PASSWORD_TWO},
        headers=admin_csrf,
    )

    _become(client, guest_session)
    assert client.get("/api/auth/me").status_code == 401


def test_a_reset_password_still_has_to_meet_the_policy(client: TestClient) -> None:
    _admin(client)
    guest_id = _make_guest(client)

    response = client.post(
        f"/api/auth/users/{guest_id}/password",
        json={"new_password": "weak"},
        headers=_csrf(client),
    )

    assert response.status_code == 422


def test_deleting_a_guest_removes_the_account(client: TestClient) -> None:
    _admin(client)
    guest_id = _make_guest(client)

    removed = client.delete(f"/api/auth/users/{guest_id}", headers=_csrf(client))

    assert removed.status_code == 204
    assert [row["username"] for row in client.get("/api/auth/users").json()] == ["kate"]
    assert _sign_in(client, "sam", GUEST_PASSWORD) == 401


# --- what an admin cannot do --------------------------------------------------------------------


def test_an_admin_account_cannot_be_disabled_from_here(client: TestClient) -> None:
    """The rule that stands in for three. There is one admin, so refusing every admin refuses
    locking yourself out, deleting yourself, and removing the last way into the instance."""
    _admin(client)
    admin_id = client.get("/api/auth/me").json()["id"]

    response = client.put(
        f"/api/auth/users/{admin_id}/disabled",
        json={"disabled": True},
        headers=_csrf(client),
    )

    assert response.status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_an_admin_account_cannot_be_deleted_from_here(client: TestClient) -> None:
    _admin(client)
    admin_id = client.get("/api/auth/me").json()["id"]

    response = client.delete(f"/api/auth/users/{admin_id}", headers=_csrf(client))

    assert response.status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_an_admin_password_cannot_be_reset_from_here(client: TestClient) -> None:
    """Resetting a password without the old one is only safe for a user who holds no wrapped
    key. An admin holds one, and replacing it blind would throw away their saved site logins,
    which the console tool does only after saying so in as many words."""
    _admin(client)
    admin_id = client.get("/api/auth/me").json()["id"]

    response = client.post(
        f"/api/auth/users/{admin_id}/password",
        json={"new_password": GUEST_PASSWORD_TWO},
        headers=_csrf(client),
    )

    assert response.status_code == 403
    assert _sign_in(client, "kate", PASSWORD) == 200


def test_an_account_that_does_not_exist_is_missing_rather_than_refused(
    client: TestClient,
) -> None:
    """A 404 and a 403 are different answers on purpose, and there is no oracle in telling them
    apart here: only an admin can ask, and they can list every user on the instance."""
    _admin(client)
    nowhere = "01HX0000000000000000000404"
    csrf = _csrf(client)

    assert (
        client.put(
            f"/api/auth/users/{nowhere}/disabled", json={"disabled": True}, headers=csrf
        ).status_code
        == 404
    )
    assert client.delete(f"/api/auth/users/{nowhere}", headers=csrf).status_code == 404
    assert (
        client.post(
            f"/api/auth/users/{nowhere}/password",
            json={"new_password": GUEST_PASSWORD_TWO},
            headers=csrf,
        ).status_code
        == 404
    )


def test_a_guest_cannot_reach_any_of_it(client: TestClient) -> None:
    """The control is here, not in the screen that does not draw the buttons.

    Every one of these is a request a guest would have to make deliberately, which is the shape
    the refusal has to hold against.
    """
    _admin(client)
    guest_id = _make_guest(client)
    _sign_in(client, "sam", GUEST_PASSWORD)
    csrf = _csrf(client)

    assert client.get("/api/auth/users").status_code == 403
    assert (
        client.post(
            "/api/auth/users",
            json={"username": "smuggled", "password": GUEST_PASSWORD_TWO},
            headers=csrf,
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/auth/users/{guest_id}/disabled", json={"disabled": False}, headers=csrf
        ).status_code
        == 403
    )
    assert client.delete(f"/api/auth/users/{guest_id}", headers=csrf).status_code == 403
    assert (
        client.post(
            f"/api/auth/users/{guest_id}/password",
            json={"new_password": GUEST_PASSWORD_TWO},
            headers=csrf,
        ).status_code
        == 403
    )


def test_me_names_the_run_of_the_server_to_an_admin_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unlock bar's Not now holds across a reload and not across a restart, and the page tells
    the two apart by `boot`: the same word in one process, a different one in the next. An admin's
    bar reads it, so only an admin is told, as `/health` tells only an admin."""
    from sift.kernel import lifecycle

    _admin(client)
    first = client.get("/api/auth/me").json()["boot"]
    assert first == lifecycle.BOOT_ID
    assert client.get("/api/auth/me").json()["boot"] == first
    monkeypatch.setattr(lifecycle, "BOOT_ID", "the next run")
    assert client.get("/api/auth/me").json()["boot"] == "the next run"

    _make_guest(client)
    assert _sign_in(client, "sam", GUEST_PASSWORD) == 200
    assert client.get("/api/auth/me").json().get("boot") is None
