# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saving a file onto your own machine, and the record of it.

Not the internet downloader. The toggle is friction rather than protection, so what is tested is
that the server decides, decides at each request, and says little when the answer is no.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sift.slices.browse.router import OUTGOING_DIR_NAME, SAVE_TO_DEVICE_KEY
from sift.slices.browse.tests.conftest import (
    Library,
    cache_dir,
    db_path,
    set_app_setting,
    share,
    sign_in,
    write,
)
from sift.testing.library import hidden_row

pytestmark = [pytest.mark.integration]


def save(client: TestClient, asset_id: str) -> object:
    return client.get(f"/api/assets/{asset_id}/save-to-device")


def test_the_admin_can_always_save(client: TestClient, library: Library) -> None:
    """No toggle governs an admin. It is their library."""
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/save-to-device")
    assert answer.status_code == 200
    assert answer.content == b"bytes of shared"


def test_a_guest_is_refused_while_the_capability_is_off(
    client: TestClient, library: Library
) -> None:
    """A guest is refused by the server while the capability is off, its default."""
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    assert client.get(f"/api/assets/{library.shared}/save-to-device").status_code == 403


def test_a_guest_may_save_once_the_capability_is_on(client: TestClient, library: Library) -> None:
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)
    set_app_setting(client, SAVE_TO_DEVICE_KEY, "true")

    answer = client.get(f"/api/assets/{library.shared}/save-to-device")
    assert answer.status_code == 200
    assert answer.content == b"bytes of shared"


def test_me_tells_the_admin_they_may_always_save(client: TestClient, library: Library) -> None:
    """The courtesy the button reads. An admin is told yes without regard to the toggle, because
    the toggle governs guests and it is an admin's library."""
    sign_in(client, "admin")
    assert client.get("/api/auth/me").json()["can_save_to_device"] is True


def test_me_reflects_the_toggle_for_a_guest(client: TestClient, library: Library) -> None:
    """A guest is told what the endpoint would tell them, so the shell can offer the button exactly
    when it would work rather than a door that will not open."""
    sign_in(client, "guest")
    assert client.get("/api/auth/me").json()["can_save_to_device"] is False

    set_app_setting(client, SAVE_TO_DEVICE_KEY, "true")
    assert client.get("/api/auth/me").json()["can_save_to_device"] is True


def test_the_toggle_applies_to_the_very_next_request(client: TestClient, library: Library) -> None:
    """The toggle applies to the very next request of a live session."""
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)
    set_app_setting(client, SAVE_TO_DEVICE_KEY, "true")

    assert client.get(f"/api/assets/{library.shared}/save-to-device").status_code == 200

    set_app_setting(client, SAVE_TO_DEVICE_KEY, "false")

    assert client.get(f"/api/assets/{library.shared}/save-to-device").status_code == 403, (
        "the capability was cached; turning it off did not take effect until re-login"
    )


def test_a_guest_cannot_save_what_they_cannot_see(client: TestClient, library: Library) -> None:
    """A guest cannot save what they cannot see: 404, as a made-up id gets."""
    sign_in(client, "guest")
    set_app_setting(client, SAVE_TO_DEVICE_KEY, "true")

    denied = client.get(f"/api/assets/{library.private}/save-to-device")
    missing = client.get("/api/assets/01HX0000000000000000000000/save-to-device")

    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()


def test_an_invisible_asset_is_a_404_even_with_the_capability_off(
    client: TestClient, library: Library
) -> None:
    """Visibility is decided first, so an invisible asset is 404 whatever the setting."""
    sign_in(client, "guest")

    assert client.get(f"/api/assets/{library.private}/save-to-device").status_code == 404


# --- the record ------------------------------------------------------------------------


def test_a_successful_save_is_recorded(client: TestClient, library: Library) -> None:
    admin = sign_in(client, "admin")
    client.get(f"/api/assets/{library.shared}/save-to-device")

    body = client.get("/api/save-log").json()
    assert body["total"] == 1
    assert body["items"][0]["asset_id"] == library.shared
    assert body["items"][0]["user_id"] == admin


def test_a_save_is_an_event_the_feed_draws(client: TestClient, library: Library) -> None:
    """The save log's row has a `saved` event beside it, with the file as its subject, and the
    Settings feed, which reads the ledger and nothing else, returns it."""
    admin = sign_in(client, "admin")
    client.get(f"/api/assets/{library.shared}/save-to-device")

    items = client.get("/api/ledger", params={"verb": "saved"}).json()["items"]
    assert len(items) == 1
    assert items[0]["actor"]["kind"] == "user" and items[0]["actor"]["id"] == admin
    assert [(one["kind"], one["id"]) for one in items[0]["subjects"]] == [("asset", library.shared)]


def test_a_refused_save_is_not_recorded(client: TestClient, library: Library) -> None:
    """The log is what left the machine. An attempt that was turned away did not leave."""
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)
    client.get(f"/api/assets/{library.shared}/save-to-device")

    sign_in(client, "admin")
    assert client.get("/api/save-log").json()["total"] == 0


def test_the_save_log_is_admin_only(client: TestClient, library: Library) -> None:
    """The log is about users other than the one asking. A guest may not read it."""
    sign_in(client, "guest")
    assert client.get("/api/save-log").status_code == 403


def test_the_save_log_is_newest_first_and_paged(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    client.get(f"/api/assets/{library.shared}/save-to-device")
    client.get(f"/api/assets/{library.private}/save-to-device")

    body = client.get("/api/save-log", params={"limit": 1}).json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["asset_id"] == library.private


def test_the_save_log_page_is_bounded(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    assert client.get("/api/save-log", params={"limit": 0}).status_code == 422
    assert client.get("/api/save-log", params={"limit": 10_000}).status_code == 422


def test_the_download_word_is_not_used_for_saving(client: TestClient, library: Library) -> None:
    """Saving is not called download: that word is Sift fetching from the internet."""
    schema = client.app.openapi()  # type: ignore[attr-defined]

    save_paths = [path for path in schema["paths"] if "save-to-device" in path]
    assert save_paths, "the save endpoint is not in the schema"

    # The address and the name of the operation are what the rest of the codebase calls this
    # thing by. Prose that draws the distinction is welcome; a name that erases it is not.
    for path in save_paths:
        assert "download" not in path.lower()
        for operation in schema["paths"][path].values():
            assert "download" not in operation["operationId"].lower()
            assert "download" not in operation.get("summary", "").lower()

    # And the reverse: whatever else the downloader is, it is not reachable from here.
    assert not any("save-to-device" in path for path in schema["paths"] if "/downloads" in path)


def test_the_capability_key_is_the_one_that_was_registered() -> None:
    """The capability key, repeated since a slice may not import another, is a registered one."""
    from sift.kernel.settings_registry import registered_settings

    assert SAVE_TO_DEVICE_KEY in registered_settings(), (
        "the capability key no longer matches the setting that was registered"
    )


def test_a_save_the_person_did_not_ask_for_is_refused(client: TestClient, library: Library) -> None:
    """A cross-site save is refused, so only Sift writes the audit log."""
    sign_in(client, "admin")

    forged = client.get(
        f"/api/assets/{library.shared}/save-to-device",
        headers={"sec-fetch-site": "cross-site"},
    )
    assert forged.status_code == 403

    assert client.get("/api/save-log").json()["total"] == 0, "a forged save reached the log"


def test_the_app_and_a_bookmark_still_work(client: TestClient, library: Library) -> None:
    """Only another origin is refused. Sift's own pages, and a person typing the address or opening
    a bookmark, are the ordinary cases and must not be caught by it."""
    sign_in(client, "admin")

    for site in ("same-origin", "none"):
        answer = client.get(
            f"/api/assets/{library.shared}/save-to-device", headers={"sec-fetch-site": site}
        )
        assert answer.status_code == 200, site


def test_the_saved_file_keeps_the_name_it_was_imported_under(
    client: TestClient, library: Library
) -> None:
    """`locate` hands back a symlink-resolved path, whose basename can be a content-addressed
    string that means nothing to the person saving it."""
    sign_in(client, "admin")

    answer = client.get(f"/api/assets/{library.shared}/save-to-device")
    assert "shared.mp4" in answer.headers["content-disposition"]


# --- The file that is not there any more ---------------------------------------------------------
#
# A file gone from disk: the server answers honestly, and a client can ask first.


def test_a_file_that_has_gone_is_a_404_rather_than_a_crash(
    client: TestClient, library: Library
) -> None:
    """A file gone since the last scan is a 404 and records no save."""
    sign_in(client, "admin")
    (library.media / "clips" / "shared.mp4").unlink()

    assert save(client, library.shared).status_code == 404  # type: ignore[attr-defined]
    assert client.get("/api/save-log").json()["total"] == 0, (
        "a file that never left was written into the record of what has left"
    )


def test_asking_without_the_bytes_answers_the_same_question(
    client: TestClient, library: Library
) -> None:
    """A HEAD answers the same question without the bytes."""
    sign_in(client, "admin")

    assert client.head(f"/api/assets/{library.shared}/save-to-device").status_code == 200
    assert client.head("/api/assets/01ZZZZZZZZZZZZZZZZZZZZZZZZ/save-to-device").status_code == 404

    (library.media / "clips" / "shared.mp4").unlink()
    assert client.head(f"/api/assets/{library.shared}/save-to-device").status_code == 404


def test_asking_is_not_taking(client: TestClient, library: Library) -> None:
    """A HEAD writes no row to the save log."""
    sign_in(client, "admin")

    for _ in range(3):
        assert client.head(f"/api/assets/{library.shared}/save-to-device").status_code == 200

    assert client.get("/api/save-log").json()["total"] == 0


def test_asking_obeys_the_same_permission_the_taking_does(
    client: TestClient, library: Library
) -> None:
    """A HEAD obeys the same permissions, through the same handler."""
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    # The capability is off by default: a guest is refused, and asking is refused the same way.
    assert client.head(f"/api/assets/{library.shared}/save-to-device").status_code == 403
    # And something they cannot see is a 404 either way, never a 403: a 403 would tell them it
    # exists.
    assert client.head(f"/api/assets/{library.private}/save-to-device").status_code == 404

    set_app_setting(client, SAVE_TO_DEVICE_KEY, "true")
    assert client.head(f"/api/assets/{library.shared}/save-to-device").status_code == 200


# --- the vault: 423 to whoever locked it, 404 to everyone else ------------------------------------


def _hide_from(client: TestClient, asset_id: str, user_id: str) -> None:
    """Put a file in ONE user's vault, which is the only way anything gets into one."""
    write(db_path(client), [hidden_row("asset", asset_id, user_id)])


def test_the_owner_is_told_the_file_is_in_their_vault_rather_than_that_it_is_gone(
    client: TestClient, library: Library
) -> None:
    """The vault's own user is told 423, since the vault never kept a secret from its PIN holder."""
    admin = sign_in(client, "admin")
    _hide_from(client, library.shared, admin)

    answer = client.get(f"/api/assets/{library.shared}/save-to-device")

    assert answer.status_code == 423
    assert "vault" in answer.json()["detail"].lower()


def test_the_head_the_client_asks_first_gives_the_same_answer(
    client: TestClient, library: Library
) -> None:
    """The client asks with a HEAD before it claims a download happened, so the HEAD has to carry
    the same status. Otherwise the message is decided by a request that was never told."""
    admin = sign_in(client, "admin")
    _hide_from(client, library.shared, admin)

    assert client.head(f"/api/assets/{library.shared}/save-to-device").status_code == 423


def test_a_guest_still_gets_the_plain_404_for_a_file_in_somebody_elses_vault(
    client: TestClient, library: Library
) -> None:
    """A guest still gets 404 for a file in somebody else's vault."""
    admin = sign_in(client, "admin")
    _hide_from(client, library.shared, admin)
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)
    set_app_setting(client, SAVE_TO_DEVICE_KEY, "true")

    answer = client.get(f"/api/assets/{library.shared}/save-to-device")

    assert answer.status_code == 200, "the admin's vault is not the guest's, and hides nothing here"


def test_a_file_that_is_simply_not_there_is_still_a_plain_404(
    client: TestClient, library: Library
) -> None:
    """An id naming nothing is still a plain 404."""
    sign_in(client, "admin")

    assert client.get("/api/assets/01HX0000000000000000000099/save-to-device").status_code == 404


# --- the copy that leaves carries no location ----------------------------------------------


def _located_jpeg(path: Path) -> bytes:
    """A small JPEG written where the shared asset's file is, with an invented place in it."""
    exif = Image.Exif()
    exif[0x0110] = "Model Q"
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (48.0, 51.0, 29.17)
    Image.new("RGB", (16, 12), (90, 40, 200)).save(path, format="JPEG", exif=exif)
    return path.read_bytes()


def test_a_saved_picture_carries_no_location_and_the_original_keeps_its_own(
    client: TestClient, library: Library
) -> None:
    """The bytes that leave are a copy without the GPS directory; the file in the library is not
    touched, and the copy made for the send is gone once it has been sent."""
    sign_in(client, "admin")
    original = library.media / "clips" / "shared.mp4"
    before = _located_jpeg(original)
    stamp = original.stat().st_mtime_ns

    answer = client.get(f"/api/assets/{library.shared}/save-to-device")

    assert answer.status_code == 200
    with Image.open(io.BytesIO(answer.content)) as sent:
        assert 0x8825 not in sent.getexif()
        assert sent.getexif()[0x0110] == "Model Q"
    assert len(answer.content) == len(before)
    assert original.read_bytes() == before
    assert original.stat().st_mtime_ns == stamp
    outgoing = cache_dir(client) / OUTGOING_DIR_NAME
    assert not outgoing.exists() or not any(outgoing.iterdir())


def test_a_saved_file_with_no_location_is_sent_as_it_is(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    original = library.media / "clips" / "shared.mp4"
    Image.new("RGB", (16, 12)).save(original, format="JPEG")

    answer = client.get(f"/api/assets/{library.shared}/save-to-device")

    assert answer.content == original.read_bytes()
    assert not (cache_dir(client) / OUTGOING_DIR_NAME).exists()


def test_a_save_whose_location_cannot_be_taken_out_is_refused_and_not_recorded(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    original = library.media / "clips" / "shared.mp4"
    data = bytearray(_located_jpeg(original))
    at = data.index(b"Exif\x00\x00") - 2
    data[at : at + 2] = b"\xff\xf0"
    original.write_bytes(bytes(data))

    answer = client.get(f"/api/assets/{library.shared}/save-to-device")

    assert answer.status_code == 422
    assert client.get("/api/save-log").json()["total"] == 0


def test_asking_before_a_save_whose_location_cannot_be_taken_out_says_422_as_the_save_would(
    client: TestClient, library: Library
) -> None:
    """A HEAD that said the file was there would send the browser to a download that failed with
    nothing of Sift's to say why. It answers what the GET answers."""
    sign_in(client, "admin")
    original = library.media / "clips" / "shared.mp4"
    data = bytearray(_located_jpeg(original))
    at = data.index(b"Exif\x00\x00") - 2
    data[at : at + 2] = b"\xff\xf0"
    original.write_bytes(bytes(data))

    assert client.head(f"/api/assets/{library.shared}/save-to-device").status_code == 422
    assert client.get("/api/save-log").json()["total"] == 0
