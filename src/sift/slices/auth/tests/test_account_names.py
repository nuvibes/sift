# SPDX-License-Identifier: AGPL-3.0-or-later
"""Renaming a user, and inventing one: a name is a label, and a generated password exists in the
clear once."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.slices import auth
from sift.slices.auth import AuthService, Hasher
from sift.slices.auth import service as service_module
from sift.slices.auth.crypto import resolve_argon2_params
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]

PASSWORD = "Corr3ct-Horse!staple9"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.app.state.auth = AuthService(  # type: ignore[attr-defined]
            c.app.state.database,  # type: ignore[attr-defined]
            hasher=Hasher(resolve_argon2_params(None)),
            master_keys=c.app.state.master_keys,  # type: ignore[attr-defined]
            queue=c.app.state.queue,  # type: ignore[attr-defined]
            session_ttl_seconds=auth.DEFAULT_SESSION_DAYS * 24 * 60 * 60,
        )
        yield c
    get_settings.cache_clear()


def db_path(client: TestClient) -> Path:
    return Path(client.app.state.database.path)  # type: ignore[attr-defined]


def sign_in(client: TestClient, role: str = "admin", *, who: str = "one") -> str:
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"names-{role}-{who}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def rename(client: TestClient, user_id: str, username: str):  # type: ignore[no-untyped-def]
    return client.post(f"/api/auth/users/{user_id}/username", json={"username": username})


# --- renaming ------------------------------------------------------------------------------------


def test_an_account_can_rename_itself(client: TestClient) -> None:
    user_id = sign_in(client, "guest", who="visitor")

    assert rename(client, user_id, "Jules").status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "Jules"


def test_an_admin_can_rename_somebody_else(client: TestClient) -> None:
    guest_id = sign_in(client, "guest", who="visitor")
    sign_in(client, "admin")

    assert rename(client, guest_id, "Sam").status_code == 200
    names = {account["id"]: account["username"] for account in client.get("/api/auth/users").json()}
    assert names[guest_id] == "Sam"


def test_a_guest_cannot_rename_anybody_else(client: TestClient) -> None:
    """A guest may rename only themselves."""
    admin_id = sign_in(client, "admin")
    sign_in(client, "guest", who="visitor")

    assert rename(client, admin_id, "not-yours").status_code == 403
    assert client.get("/api/auth/me").json()["username"] != "not-yours"


def test_a_name_that_is_taken_is_refused_the_same_way_creating_one_is(client: TestClient) -> None:
    """A taken name is refused as creating one is, from the same read inside the write."""
    guest_id = sign_in(client, "guest", who="visitor")
    sign_in(client, "admin")

    refused = rename(client, guest_id, "names-admin-one")
    assert refused.status_code == 409

    also_refused = client.post(
        "/api/auth/users", json={"username": "names-admin-one", "password": PASSWORD}
    )
    assert also_refused.status_code == 409
    assert refused.json()["detail"] == also_refused.json()["detail"]


def test_renaming_yourself_to_your_own_name_is_allowed(client: TestClient) -> None:
    """So correcting the capitalisation works. The column folds case, so this is the same row."""
    user_id = sign_in(client, "guest", who="visitor")

    assert rename(client, user_id, "NAMES-GUEST-VISITOR").status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "NAMES-GUEST-VISITOR"


def test_a_rename_signs_nobody_out(client: TestClient) -> None:
    """A rename signs nobody out."""
    guest_id = sign_in(client, "guest", who="visitor")
    guest_cookie = client.cookies.get(SESSION_COOKIE_NAME)
    guest_csrf = client.headers[CSRF_HEADER_NAME]

    sign_in(client, "admin")
    assert rename(client, guest_id, "Renamed").status_code == 200
    # An admin renamed themselves too, and is still here.
    admin_id = client.get("/api/auth/me").json()["id"]
    assert rename(client, admin_id, "Boss").status_code == 200
    assert client.get("/api/auth/me").status_code == 200

    client.cookies.set(SESSION_COOKIE_NAME, guest_cookie or "")
    client.headers[CSRF_HEADER_NAME] = guest_csrf
    assert client.get("/api/auth/me").json()["username"] == "Renamed"


def test_the_saved_site_logins_still_open_after_a_rename(client: TestClient) -> None:
    """Saved site logins still open after a rename: the master key is wrapped by the password, not
    the name, and is the same object afterwards."""
    setup = client.post("/api/auth/setup", json={"username": "kate", "password": PASSWORD})
    assert setup.status_code == 201
    client.headers[CSRF_HEADER_NAME] = client.get("/api/auth/me").json()["csrf_token"]
    user_id = client.get("/api/auth/me").json()["id"]
    keys = client.app.state.master_keys  # type: ignore[attr-defined]
    before = keys.get(user_id)
    assert before is not None, "an admin holds a wrapped key, or this proves nothing"

    assert rename(client, user_id, "katherine").status_code == 200
    assert keys.get(user_id) == before

    # And it still unwraps from the row, under the same password and the new name.
    client.post("/api/auth/logout")
    client.cookies.clear()
    again = client.post("/api/auth/login", json={"username": "katherine", "password": PASSWORD})
    assert again.status_code == 200
    assert keys.get(user_id) == before


# --- inventing one -------------------------------------------------------------------------------


def test_a_generated_guest_is_shown_once_and_can_sign_in(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A generated guest's password is shown once, never again, not in the log, and works."""
    sign_in(client, "admin")

    with caplog.at_level(logging.DEBUG, logger="sift"):
        made = client.post("/api/auth/users/generate")
    assert made.status_code == 201
    body = made.json()
    username = body["user"]["username"]
    password = body["password"]
    assert body["user"]["role"] == "guest"

    # Not in any later read. The listing is the shape that carries a user everywhere else, and
    # it is the one that must never grow a password field.
    listed = client.get("/api/auth/users").json()
    assert password not in json.dumps(listed)
    assert all("password" not in account for account in listed)

    # Not in Sift's own log records; the driver's DEBUG parameters are its own.
    written = "\n".join(
        record.getMessage() for record in caplog.records if record.name.startswith("sift")
    )
    assert password not in written
    assert username not in written

    # And it works, which is the half that stops the rest passing on a password nobody can use.
    client.cookies.clear()
    assert (
        client.post(
            "/api/auth/login", json={"username": username, "password": password}
        ).status_code
        == 200
    )


def test_two_generated_guests_get_different_credentials(client: TestClient) -> None:
    """Two generated guests get different credentials."""
    sign_in(client, "admin")

    first = client.post("/api/auth/users/generate").json()
    second = client.post("/api/auth/users/generate").json()

    assert first["password"] != second["password"]
    assert first["user"]["username"] != second["user"]["username"]


def test_a_guest_cannot_invent_an_account(client: TestClient) -> None:
    """The sharpest route in the section: a user a guest could mint themselves is a second way
    in that survives their own being blocked."""
    sign_in(client, "guest", who="visitor")

    assert client.post("/api/auth/users/generate").status_code == 403


# --- the refusals nothing else reaches -----------------------------------------------------------


def test_renaming_to_nothing_but_spaces_is_refused(client: TestClient) -> None:
    """A name of spaces is blank after the trim and refused."""
    user_id = sign_in(client, "guest", who="visitor")

    assert rename(client, user_id, "   ").status_code == 409


def test_renaming_an_account_that_is_not_there_is_a_404(client: TestClient) -> None:
    """The id names nothing. Different from a name being taken, and a different answer."""
    sign_in(client, "admin")

    assert rename(client, "01HX0000000000000000000999", "Whoever").status_code == 404


def test_running_out_of_invented_names_is_refused_rather_than_worked_around(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running out of invented names is refused rather than inventing a longer one."""
    sign_in(client, "admin")
    monkeypatch.setattr(service_module, "generated_username", lambda: "guest-0001")

    assert client.post("/api/auth/users/generate").status_code == 201
    assert client.post("/api/auth/users/generate").status_code == 409


def test_a_guest_is_not_told_which_names_are_taken(client: TestClient) -> None:
    """A guest is not told which names are taken; an admin is."""
    sign_in(client, "admin", who="owner")
    taken = "names-admin-owner"
    guest_id = sign_in(client, "guest", who="visitor")

    refused = rename(client, guest_id, taken)
    assert refused.status_code == 409
    assert taken not in refused.text, "the refusal named the account it was asked about"

    sign_in(client, "admin", who="owner")
    told = rename(client, guest_id, taken)
    assert told.status_code == 409
    assert taken in told.text


def test_a_guest_runs_out_of_name_changes(client: TestClient) -> None:
    """A guest's three renames a day are spent before the name is checked."""
    guest_id = sign_in(client, "guest", who="visitor")

    for attempt, name in enumerate(("One", "Two", "Three"), start=1):
        assert rename(client, guest_id, name).status_code == 200, f"change {attempt} was refused"

    spent = rename(client, guest_id, "Four")
    assert spent.status_code == 429
    assert client.get("/api/auth/me").json()["username"] == "Three"


def test_an_admin_has_no_such_limit(client: TestClient) -> None:
    """They can read the user list anyway, so a cap would only be in the way."""
    guest_id = sign_in(client, "guest", who="visitor")
    sign_in(client, "admin")

    for name in ("One", "Two", "Three", "Four", "Five"):
        assert rename(client, guest_id, name).status_code == 200
