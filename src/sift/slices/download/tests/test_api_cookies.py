# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cookies endpoints, driven against the real application: saving, reading back, checking."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sift.kernel.db import Database
from sift.kernel.tunnels import TunnelError
from sift.slices.auth.crypto import generate_master_key
from sift.slices.download.tests.api_support import (
    _ask,
    _block_the_job_of,
    _Direct,
    _exported,
    _on_the_apps_loop,
    _Site,
    _unlock,
    download_api,
    sign_in,
)

pytestmark = [pytest.mark.integration]


def test_saving_a_cookie_needs_a_password_login(client: TestClient) -> None:
    sign_in(client, "admin")
    # A session with no master key in memory: a cookie-only resume, or before a password login.
    response = client.post("/api/site-connections", json={"site": "TikTok", "cookie": "c=1"})
    assert response.status_code == 409


def test_saving_cookies_puts_a_waiting_download_back_in_the_line(client: TestClient) -> None:
    """The other half of waiting for cookies: something has to end the wait.

    A blocked job is waiting on a condition it cannot see change, so whatever changes it has to
    say so: the same release signing in makes when the master key arrives. Without it, saving
    the cookies somebody was just asked for would leave the row sitting at "Waiting for cookies"
    until they found Try again, which is being asked twice for one thing.
    """
    user_id = sign_in(client, "admin")
    client.app.state.master_keys.store(user_id, generate_master_key())  # type: ignore[attr-defined]
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}
    ).json()["id"]
    database = client.app.state.database  # type: ignore[attr-defined]
    _on_the_apps_loop(client, _block_the_job_of, database, download_id)
    assert client.get("/api/downloads").json()["downloads"][0]["status"] == "blocked"

    saved = client.post("/api/site-connections", json={"site": "TikTok", "cookie": "sessionid=x"})
    assert saved.status_code == 201

    assert client.get("/api/downloads").json()["downloads"][0]["status"] == "queued"


def test_a_saved_cookie_is_never_handed_back(client: TestClient) -> None:
    user_id = sign_in(client, "admin")
    client.app.state.master_keys.store(user_id, generate_master_key())  # type: ignore[attr-defined]

    saved = client.post(
        "/api/site-connections", json={"site": "TikTok", "cookie": "sessionid=top-secret"}
    )
    assert saved.status_code == 201

    listing = client.get("/api/site-connections")
    assert listing.status_code == 200
    assert listing.json()[0]["site"] == "TikTok"
    # The cookie is nowhere in the response, under any key.
    assert "top-secret" not in listing.text
    assert "cookie" not in listing.json()[0]


def test_a_login_that_cannot_be_read_is_refused_where_it_is_pasted(client: TestClient) -> None:
    """Not at the next download. The tools disagree about a malformed login and both end up
    reporting it as the SITE wanting one, which sends a person to replace something that was fine.
    """
    user_id = sign_in(client, "admin")
    client.app.state.master_keys.store(user_id, generate_master_key())  # type: ignore[attr-defined]

    refused = client.post(
        "/api/site-connections", json={"site": "TikTok", "cookie": "not a login at all"}
    )
    assert refused.status_code == 400
    assert "cookie" in refused.json()["detail"].lower()  # says what was expected
    assert client.get("/api/site-connections").json() == []  # and nothing was stored


def test_what_was_understood_comes_back_without_any_of_the_login(client: TestClient) -> None:
    user_id = sign_in(client, "admin")
    client.app.state.master_keys.store(user_id, generate_master_key())  # type: ignore[attr-defined]

    saved = client.post(
        "/api/site-connections",
        json={"site": "TikTok", "cookie": "sessionid=top-secret; csrftoken=also-secret"},
    )

    assert saved.status_code == 201
    body = saved.json()
    assert body["cookies"] == 2
    assert body["domains"] == ["tiktok.com"]  # the site was worked out from the site
    assert body["expired"] is False
    assert "top-secret" not in saved.text  # facts about the login, never the login


def test_a_connection_can_be_deleted(client: TestClient) -> None:
    user_id = sign_in(client, "admin")
    client.app.state.master_keys.store(user_id, generate_master_key())  # type: ignore[attr-defined]
    saved = client.post("/api/site-connections", json={"site": "X", "cookie": "sessionid=abc"})
    connection_id = saved.json()["id"]

    assert client.delete(f"/api/site-connections/{connection_id}").status_code == 204
    # Idempotent: a second delete is still fine.
    assert client.delete(f"/api/site-connections/{connection_id}").status_code == 204
    assert client.get("/api/site-connections").json() == []


def test_reading_cookies_back_saves_nothing(client: TestClient) -> None:
    """The read-back before Save. It describes the jar and leaves the database exactly as it was.

    Asserted as an ABSENCE of a row rather than by counting writes, because the failure this guards
    against is the route quietly becoming the save route: a preview that stored what it read would
    look identical on screen and would seal a jar nobody had agreed to keep.
    """
    _unlock(client)

    answer = client.post(
        "/api/site-connections/preview",
        json={"site": "TikTok", "cookie": _exported(4_102_444_800)},
    )

    assert answer.status_code == 200, answer.text
    read = answer.json()
    assert read["cookies"] == 1
    assert read["domains"] == ["tiktok.com"]
    assert read["expires_last"] == 4_102_444_800
    assert read["expired"] is False
    assert "kept-secret" not in answer.text
    assert client.get("/api/site-connections").json() == []


def test_reading_cookies_back_needs_no_key_and_refuses_what_it_cannot_read(
    client: TestClient,
) -> None:
    """No master key, because there is nothing to seal: pasting the wrong file and being locked
    are two separate problems and meeting both at the same time is how a form becomes a wall."""
    sign_in(client, "admin")

    assert (
        client.post(
            "/api/site-connections/preview",
            json={"site": "TikTok", "cookie": _exported(4_102_444_800)},
        ).status_code
        == 200
    )
    refused = client.post(
        "/api/site-connections/preview", json={"site": "TikTok", "cookie": "nothing like it"}
    )
    assert refused.status_code == 400


def test_a_saved_jar_is_shown_with_its_dates_and_its_state(client: TestClient) -> None:
    """The row the cookies screen draws: the word, the last expiry, and nothing that was pasted.

    Two saves rather than one, because `state` is the thing being tested and a single row could
    pass by accident: a jar good for years and a jar that ran out last decade have to come back as
    different words from the same list.
    """
    _unlock(client)
    assert (
        client.post(
            "/api/site-connections",
            json={"site": "TikTok", "cookie": _exported(4_102_444_800)},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/site-connections",
            json={"site": "Reddit", "cookie": _exported(1_000_000_000, host=".reddit.com")},
        ).status_code
        == 201
    )

    rows = {one["site"]: one for one in client.get("/api/site-connections").json()}
    assert rows["TikTok"]["state"] == "saved"
    assert rows["TikTok"]["expires_last"] == 4_102_444_800
    assert rows["TikTok"]["last_used_at"] is None, "saving a jar is not using it"
    assert rows["Reddit"]["state"] == "expired"
    assert "kept-secret" not in client.get("/api/site-connections").text


def test_checking_cookies_says_what_the_site_said_and_writes_the_health(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepted, then refused, with the stored health following each answer.

    The request itself is stood in for. What is under test is everything around it: that the saved
    jar is unsealed and turned into a header, that the two answers produce the two sentences, and
    that a refusal is written down where the cookies screen reads it.
    """
    _unlock(client)
    made = client.post(
        "/api/site-connections", json={"site": "TikTok", "cookie": _exported(4_102_444_800)}
    )
    connection_id = made.json()["id"]

    asked: list[str] = []

    async def took_them(_router: object, url: str, header: str) -> bool | None:
        asked.append(header)
        return True

    monkeypatch.setattr(download_api, "_still_accepted", took_them)
    answer = client.post(f"/api/site-connections/{connection_id}/check")

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"accepted": True, "said": "TikTok still accepts these cookies"}
    assert asked == ["sessionid=kept-secret"]
    checked = client.get("/api/site-connections").json()[0]
    assert checked["status"] == "saved"
    # Unsealing the jar to ask the site is a use of it, and the screen says when it was last used.
    assert checked["last_used_at"] is not None

    async def turned_them_away(_router: object, url: str, header: str) -> bool | None:
        return False

    download_api.forget_checks()
    monkeypatch.setattr(download_api, "_still_accepted", turned_them_away)
    refused = client.post(f"/api/site-connections/{connection_id}/check")

    assert refused.status_code == 200, refused.text
    assert refused.json()["accepted"] is False
    assert refused.json()["said"].startswith("TikTok turned these cookies away")
    shown = client.get("/api/site-connections").json()[0]
    assert shown["status"] == "needs_cookies"
    # And the word the screen draws follows the health, whatever the dates say.
    assert shown["state"] == "expired"


def test_checking_the_same_site_twice_in_a_minute_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One request per site per minute. The answer cannot change faster than that, and the button
    is easy to lean on: the site on the other end is the one that would pay for it."""
    _unlock(client)
    made = client.post(
        "/api/site-connections", json={"site": "TikTok", "cookie": _exported(4_102_444_800)}
    )
    connection_id = made.json()["id"]

    how_many = 0

    async def took_them(_router: object, url: str, header: str) -> bool | None:
        nonlocal how_many
        how_many += 1
        return True

    download_api.forget_checks()
    monkeypatch.setattr(download_api, "_still_accepted", took_them)

    assert client.post(f"/api/site-connections/{connection_id}/check").status_code == 200
    again = client.post(f"/api/site-connections/{connection_id}/check")

    assert again.status_code == 429
    assert again.json()["detail"] == "Checked a moment ago. Try again in a minute."
    assert how_many == 1, "the site was asked a second time anyway"


def test_a_site_that_cannot_be_reached_is_not_a_verdict(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A network that was down for a second must never condemn a working jar of cookies. The health
    written down is left exactly as it was, and the answer is a refusal rather than "turned away"."""
    _unlock(client)
    made = client.post(
        "/api/site-connections", json={"site": "TikTok", "cookie": _exported(4_102_444_800)}
    )
    connection_id = made.json()["id"]

    async def nothing_answered(_router: object, url: str, header: str) -> bool | None:
        return None

    download_api.forget_checks()
    monkeypatch.setattr(download_api, "_still_accepted", nothing_answered)
    answer = client.post(f"/api/site-connections/{connection_id}/check")

    assert answer.status_code == 502
    assert client.get("/api/site-connections").json()[0]["status"] == "saved"


def test_checking_cookies_while_locked_asks_for_the_password_first(client: TestClient) -> None:
    """The jar is sealed, and without the key there is nothing to send: the answer says how to get
    the key, before anything is looked up or asked of a site."""
    sign_in(client, "admin")

    answer = client.post("/api/site-connections/01HXsomeid/check")

    assert answer.status_code == 409
    assert "locked" in answer.json()["detail"]


def test_checking_cookies_nobody_saved_is_not_found(client: TestClient) -> None:
    _unlock(client)

    answer = client.post("/api/site-connections/01HX000000000000000000NONE/check")

    assert answer.status_code == 404
    assert answer.json()["detail"] == "There are no saved cookies to check."


def test_cookies_for_a_site_sift_has_no_record_of_cannot_be_checked(client: TestClient) -> None:
    """Cookies can be saved for any Site, and the check needs an address to ask, which only the
    catalog knows. Refused by name rather than guessing a host from the Site's name."""
    _unlock(client)
    made = client.post(
        "/api/site-connections",
        json={
            "site": "Quillmoss",
            "cookie": _exported(4_102_444_800, host=".quillmoss.test"),
        },
    )
    assert made.status_code == 201, made.text
    download_api.forget_checks()

    answer = client.post(f"/api/site-connections/{made.json()['id']}/check")

    assert answer.status_code == 400
    assert answer.json()["detail"] == (
        "Sift has no record of Quillmoss, so it has no address to ask."
    )


def test_cookies_sealed_under_another_key_are_reported_unreadable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A jar that will not open is said to be unreadable, and the site is not asked anything."""
    user_id = _unlock(client)
    made = client.post(
        "/api/site-connections", json={"site": "TikTok", "cookie": _exported(4_102_444_800)}
    )
    client.app.state.master_keys.store(user_id, generate_master_key())  # type: ignore[attr-defined]
    download_api.forget_checks()

    async def never(_router: object, url: str, header: str) -> bool | None:
        raise AssertionError("a site was asked with cookies that did not open")

    monkeypatch.setattr(download_api, "_still_accepted", never)
    answer = client.post(f"/api/site-connections/{made.json()['id']}/check")

    assert answer.status_code == 400
    assert answer.json()["detail"] == (
        "Sift could not read the cookies saved for TikTok. Add them again."
    )


def test_a_saved_jar_this_sift_cannot_read_is_refused_with_the_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A jar that opens and is not a cookie file (sealed by a Sift that read jars differently)
    is answered with the sentence a paste of it would get, and nothing is sent."""
    from sift.slices.download.secrets import SecretStore

    user_id = sign_in(client, "admin")
    key = generate_master_key()
    client.app.state.master_keys.store(user_id, key)  # type: ignore[attr-defined]
    made = client.post(
        "/api/site-connections", json={"site": "TikTok", "cookie": _exported(4_102_444_800)}
    )
    connection_id = made.json()["id"]
    database = client.app.state.database  # type: ignore[attr-defined]

    async def reseal(database: Database, connection_id: str) -> None:
        secret_id = await SecretStore(database).seal(b"not a cookie file at all", key)
        await database.execute(
            "UPDATE site_connections SET secret_id = ? WHERE id = ?", (secret_id, connection_id)
        )

    _on_the_apps_loop(client, reseal, database, connection_id)
    download_api.forget_checks()

    async def never(_router: object, url: str, header: str) -> bool | None:
        raise AssertionError("a site was asked with a jar that could not be read")

    monkeypatch.setattr(download_api, "_still_accepted", never)
    answer = client.post(f"/api/site-connections/{connection_id}/check")

    assert answer.status_code == 400
    assert answer.json()["detail"].startswith("Sift could not read that as cookies.")


def test_cookies_saved_for_another_host_are_not_sent_to_the_site(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A jar with nothing for the site's own host would ask with no cookies at all, which proves
    only that the site serves its home page. Refused, naming the fix."""
    _unlock(client)
    made = client.post(
        "/api/site-connections",
        json={"site": "TikTok", "cookie": _exported(4_102_444_800, host=".reddit.com")},
    )
    download_api.forget_checks()

    async def never(_router: object, url: str, header: str) -> bool | None:
        raise AssertionError("a site was asked with no cookies of its own")

    monkeypatch.setattr(download_api, "_still_accepted", never)
    answer = client.post(f"/api/site-connections/{made.json()['id']}/check")

    assert answer.status_code == 400
    assert answer.json()["detail"] == (
        "None of the saved cookies are for TikTok. Export them again from that site."
    )


@pytest.mark.parametrize(
    ("status", "accepted"), [(200, True), (302, True), (401, False), (403, False)]
)
def test_a_site_turning_cookies_away_is_a_401_or_403_and_anything_else_is_a_yes(
    monkeypatch: pytest.MonkeyPatch, status: int, accepted: bool
) -> None:
    """Only the two refusals condemn a jar. Any other answer is the site serving these cookies,
    a redirect included, which sites send a good request constantly."""
    site = _Site(status)

    assert _ask(monkeypatch, _Direct(), site) is accepted
    assert site.sent == [{"Cookie": "sessionid=kept"}]


def test_a_site_that_does_not_answer_is_no_verdict_about_the_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _ask(monkeypatch, _Direct(), _Site(None)) is None


def test_a_check_through_a_tunnel_that_is_down_is_refused_by_the_tunnels_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking the site directly instead would be the leak the route exists to prevent."""

    class _DownTunnel(_Direct):
        async def __aenter__(self) -> str | None:
            raise TunnelError("Sweden is not connected.")

    site = _Site(200)
    with pytest.raises(HTTPException) as refused:
        _ask(monkeypatch, _DownTunnel(), site)

    assert refused.value.status_code == 400
    assert refused.value.detail == "Sweden is not connected."
    assert site.sent == []
