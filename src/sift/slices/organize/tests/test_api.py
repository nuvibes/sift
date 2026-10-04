# SPDX-License-Identifier: AGPL-3.0-or-later
"""The organize endpoints, driven against the real application.

The rules themselves are proven where the service is tested. What is proven here is the part only a
real request can show: that the refusals arrive as the right status with their own sentence intact,
that a guest is turned away by the server rather than by a hidden button, and that the answers a
guest gets do not tell them whether the file exists.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.testing.auth import establish_session
from sift.testing.library import hide_for, seed_asset, seed_folder, seed_root, share_folder

pytestmark = [pytest.mark.integration]

ROOT_ID = "01HX0000000000000000000101"
TOP_FOLDER = "01HX0000000000000000000102"
ASSET_ID = "01HX0000000000000000000103"
LEFT_FOLDER = "01HX0000000000000000000104"
RIGHT_FOLDER = "01HX0000000000000000000105"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def library(tmp_path: Path) -> Path:
    directory = tmp_path / "media"
    directory.mkdir()
    return directory


def sign_in(client: TestClient, role: str) -> str:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role=role, username=f"organize-{role}", password="Organize-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def seed(
    client: TestClient,
    library: Path,
    tmp_path: Path,
    *,
    rel_path: str = "clip.mp4",
    folder_id: str = TOP_FOLDER,
) -> None:
    """A library, a folder and a real file, written straight into the running database.

    Synchronous, and that is not incidental. The test client runs the application on a loop of its
    own; seeding through the application's own stores from a test would touch the same database
    handle from a second loop, and the lock guarding the single writer belongs to the first one.
    """
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_root(db_path, ROOT_ID, folder_id=TOP_FOLDER, path=library)
    if folder_id != TOP_FOLDER:
        seed_folder(
            db_path,
            folder_id,
            root_id=ROOT_ID,
            parent_id=TOP_FOLDER,
            rel_path=rel_path.rsplit("/", 1)[0],
        )
    seed_asset(
        db_path,
        ASSET_ID,
        root_id=ROOT_ID,
        folder_id=folder_id,
        root_path=library,
        cache_dir=tmp_path / "cache",
        rel_path=rel_path,
    )


# --- renaming -------------------------------------------------------------------------------


def test_an_admin_can_rename_a_file(client: TestClient, library: Path, tmp_path: Path) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    response = client.post(f"/api/assets/{ASSET_ID}/rename", json={"name": "holiday.mp4"})

    assert response.status_code == 200, response.text
    assert response.json()["filename"] == "holiday.mp4"
    assert (library / "holiday.mp4").exists()
    assert not (library / "clip.mp4").exists()


def test_renaming_in_a_read_only_library_is_a_409_with_a_sentence(
    client: TestClient, library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """409, not 500 and not a stack trace: the request was fine, the state of the disk says no."""
    seed(client, library, tmp_path)
    monkeypatch.setattr("sift.kernel.content.library.is_writable", lambda _path: False)
    sign_in(client, "admin")

    response = client.post(f"/api/assets/{ASSET_ID}/rename", json={"name": "holiday.mp4"})

    assert response.status_code == 409
    assert "not allowed to write" in response.json()["detail"]
    assert (library / "clip.mp4").exists()


def test_a_name_that_is_taken_is_a_409(client: TestClient, library: Path, tmp_path: Path) -> None:
    seed(client, library, tmp_path)
    (library / "taken.mp4").write_bytes(b"already here")
    sign_in(client, "admin")

    response = client.post(f"/api/assets/{ASSET_ID}/rename", json={"name": "taken.mp4"})

    assert response.status_code == 409
    assert (library / "taken.mp4").read_bytes() == b"already here"
    # And the file that was being renamed is still where it was. A rename that destroyed its source
    # while failing to take the new name would satisfy the assertion above on its own.
    assert (library / "clip.mp4").exists()


def test_a_guest_renaming_what_they_can_see_is_a_403(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """Hiding the button is not the control. The server refuses the request itself."""
    seed(client, library, tmp_path)
    user_id = sign_in(client, "guest")
    share_folder(
        client.app.state.database.path,  # type: ignore[attr-defined]
        TOP_FOLDER,
        user_id,
        grant_id="01HX0000000000000000000107",
    )

    response = client.post(f"/api/assets/{ASSET_ID}/rename", json={"name": "holiday.mp4"})

    assert response.status_code == 403
    assert (library / "clip.mp4").exists()


def test_a_guest_moving_a_selection_is_refused_at_the_door(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """The bulk route counts a per-file refusal instead of raising it, so without a door a
    guest would be answered 200 with everything skipped: a success that did nothing."""
    seed(client, library, tmp_path)
    user_id = sign_in(client, "guest")
    share_folder(
        client.app.state.database.path,  # type: ignore[attr-defined]
        TOP_FOLDER,
        user_id,
        grant_id="01HX0000000000000000000108",
    )

    response = client.post(
        "/api/assets/move", json={"asset_ids": [ASSET_ID], "folder_id": TOP_FOLDER}
    )

    assert response.status_code == 403
    assert (library / "clip.mp4").exists()


def test_a_guest_renaming_what_they_cannot_see_is_a_404(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """Not 403, which would confirm the file is there."""
    seed(client, library, tmp_path)
    sign_in(client, "guest")

    response = client.post(f"/api/assets/{ASSET_ID}/rename", json={"name": "holiday.mp4"})

    assert response.status_code == 404
    assert (library / "clip.mp4").exists()


def test_a_guest_learns_nothing_from_a_malformed_request(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """The same answer whether or not the body was valid.

    A route that validated the body first would answer "your JSON is wrong" for a well-formed
    request and "you may not" for a broken one: two different answers from a route this caller is
    not allowed to use at all.
    """
    seed(client, library, tmp_path)
    sign_in(client, "guest")

    with_body = client.post(f"/api/assets/{ASSET_ID}/rename", json={"name": "holiday.mp4"})
    without = client.post(f"/api/assets/{ASSET_ID}/rename")

    # The same answer, and the answer is the one that says nothing: this guest cannot see the file,
    # so both are "there is no such file". Asserting only that the two agree would be satisfied by
    # both being a 500.
    assert with_body.status_code == 404
    assert without.status_code == 404


# --- moving ---------------------------------------------------------------------------------


def test_an_admin_can_move_a_file_to_another_folder(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    (library / "sorted").mkdir()
    seed_folder(
        client.app.state.database.path,  # type: ignore[attr-defined]
        RIGHT_FOLDER,
        root_id=ROOT_ID,
        parent_id=TOP_FOLDER,
        rel_path="sorted",
    )
    sign_in(client, "admin")

    response = client.post(
        "/api/assets/move", json={"asset_ids": [ASSET_ID], "folder_id": RIGHT_FOLDER}
    )

    assert response.status_code == 200, response.text
    assert response.json()["changed"] == 1
    assert (library / "sorted" / "clip.mp4").exists()
    assert not (library / "clip.mp4").exists()


def test_moving_to_a_folder_that_is_not_there_is_skipped_and_says_so(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """A bulk write reports rather than refuses: the file stays, the count says so, and the
    reason is the service's own sentence."""
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    response = client.post(
        "/api/assets/move",
        json={"asset_ids": [ASSET_ID], "folder_id": "01HX0000000000000000000009"},
    )

    assert response.status_code == 200, response.text
    done = response.json()
    assert (done["changed"], done["skipped"]) == (0, 1)
    assert done["reason"]
    assert (library / "clip.mp4").exists()


# --- what the menu is allowed to show ---------------------------------------------------------


def test_a_writable_library_says_the_actions_belong_on_screen(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")

    body = client.get(f"/api/assets/{ASSET_ID}/organize").json()

    assert body["can_organize"] is True
    assert body["reason"] is None
    assert body["undo_move_id"] is None


def test_a_read_only_library_says_they_do_not_and_why(
    client: TestClient, library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason is a sentence, because the one place it is shown is where somebody asked why."""
    seed(client, library, tmp_path)
    monkeypatch.setattr("sift.kernel.content.library.is_writable", lambda _path: False)
    sign_in(client, "admin")

    body = client.get(f"/api/assets/{ASSET_ID}/organize").json()

    assert body["can_organize"] is False
    assert "not allowed to write" in body["reason"]


def test_a_guest_is_told_the_actions_are_not_theirs(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """A guest who can see the file is told no, with a reason. They are not offered the actions."""
    seed(client, library, tmp_path)
    user_id = sign_in(client, "guest")
    share_folder(
        client.app.state.database.path,  # type: ignore[attr-defined]
        TOP_FOLDER,
        user_id,
        grant_id="01HX0000000000000000000106",
    )

    body = client.get(f"/api/assets/{ASSET_ID}/organize").json()

    assert body["can_organize"] is False
    assert "Only an admin" in body["reason"]


def test_a_guest_asking_about_a_file_they_cannot_see_is_told_it_is_not_there(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """Not "you may not", which would confirm the file exists."""
    seed(client, library, tmp_path)
    sign_in(client, "guest")

    assert client.get(f"/api/assets/{ASSET_ID}/organize").status_code == 404


def test_asking_about_a_file_that_is_not_there_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")

    response = client.get("/api/assets/01HX0000000000000000000009/organize")

    assert response.status_code == 404


def test_after_a_rename_the_undo_is_offered(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")
    move_id = client.post(f"/api/assets/{ASSET_ID}/rename", json={"name": "holiday.mp4"}).json()[
        "move_id"
    ]

    assert client.get(f"/api/assets/{ASSET_ID}/organize").json()["undo_move_id"] == move_id

    assert client.post(f"/api/moves/{move_id}/undo").status_code == 200
    assert (library / "clip.mp4").exists()
    # Moved back, not copied back.
    assert not (library / "holiday.mp4").exists()


def test_an_undo_that_cannot_be_carried_out_is_a_409(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """Something has taken the old name back. The refusal arrives as the state-of-the-disk answer."""
    seed(client, library, tmp_path)
    sign_in(client, "admin")
    move_id = client.post(f"/api/assets/{ASSET_ID}/rename", json={"name": "holiday.mp4"}).json()[
        "move_id"
    ]
    (library / "clip.mp4").write_bytes(b"something else")

    response = client.post(f"/api/moves/{move_id}/undo")

    assert response.status_code == 409
    assert (library / "clip.mp4").read_bytes() == b"something else"


def test_a_guest_cannot_undo_over_the_wire(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")
    move_id = client.post(f"/api/assets/{ASSET_ID}/rename", json={"name": "holiday.mp4"}).json()[
        "move_id"
    ]

    sign_in(client, "guest")
    assert client.post(f"/api/moves/{move_id}/undo").status_code == 403
    assert (library / "holiday.mp4").exists()


# --- moving a folder ---------------------------------------------------------------------------


def _with_two_folders(client: TestClient, library: Path, tmp_path: Path) -> None:
    """A library holding `left/a.mp4` and an empty `right`, both known to Sift."""
    seed(
        client,
        library,
        tmp_path,
        rel_path="left/a.mp4",
        folder_id=LEFT_FOLDER,
    )
    (library / "right").mkdir()
    seed_folder(
        client.app.state.database.path,  # type: ignore[attr-defined]
        RIGHT_FOLDER,
        root_id=ROOT_ID,
        parent_id=TOP_FOLDER,
        rel_path="right",
    )


def test_the_vault_wins_over_an_ordinary_refusal_in_one_selection(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """The only one of the two the person can act on. Told a folder or a file refused, they have
    learned nothing to do; told the vault is shut, they have a PIN. The ordinary refusal comes
    FIRST here, so that replacing it is what has to happen rather than merely arriving first."""
    _with_two_folders(client, library, tmp_path)
    user_id = sign_in(client, "admin")
    hide_for(client.app.state.database.path, "asset", ASSET_ID, user_id)  # type: ignore[attr-defined]

    response = client.post(
        "/api/assets/move",
        json={"asset_ids": ["01HX0000000000000000000404", ASSET_ID], "folder_id": RIGHT_FOLDER},
    )

    assert response.status_code == 200, response.text
    done = response.json()
    assert (done["changed"], done["skipped"]) == (0, 2)
    assert done["vault_locked"] is True
    assert done["reason"], "a locked selection with no sentence leaves the screen nothing to say"
    assert (library / "left" / "a.mp4").exists()


def test_a_second_ordinary_refusal_does_not_overwrite_the_first_sentence(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    """The reason is said once. Two refused files with the same cause are one thing to tell
    somebody, and a sentence that kept being replaced would report whichever file happened to be
    last in the selection."""
    _with_two_folders(client, library, tmp_path)
    sign_in(client, "admin")

    response = client.post(
        "/api/assets/move",
        json={
            "asset_ids": ["01HX0000000000000000000404", "01HX0000000000000000000405", ASSET_ID],
            "folder_id": RIGHT_FOLDER,
        },
    )

    assert response.status_code == 200, response.text
    done = response.json()
    assert (done["changed"], done["skipped"]) == (1, 2)
    assert done["vault_locked"] is False
    assert done["reason"] == "Sift could not find the file."
    assert not (library / "left" / "a.mp4").exists()
    assert len(list((library / "right").iterdir())) == 1, "the one real file moved"


# --- renaming a batch -----------------------------------------------------------------------


def test_a_batch_rename_is_previewed_without_a_write_then_applied_with_one_receipt(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "admin")
    body = {"asset_ids": [ASSET_ID], "template": "Trip {n}"}

    preview = client.post("/api/organize/rename/preview", json=body)

    assert preview.status_code == 200, preview.text
    said = preview.json()
    assert [(row["before"], row["after"], row["state"]) for row in said["rows"]] == [
        ("clip.mp4", "Trip 1.mp4", "renamed")
    ]
    assert said["renaming"] == 1 and said["as_task"] is False
    assert (library / "clip.mp4").exists()

    applied = client.post("/api/organize/rename", json=body)

    assert applied.status_code == 200, applied.text
    assert applied.json()["renamed"] == 1
    assert applied.json()["receipt_id"]
    assert (library / "Trip 1.mp4").exists()


def test_a_guest_is_refused_a_batch_rename_at_the_door(
    client: TestClient, library: Path, tmp_path: Path
) -> None:
    seed(client, library, tmp_path)
    sign_in(client, "guest")
    body = {"asset_ids": [ASSET_ID], "template": "Trip {n}"}

    assert client.post("/api/organize/rename/preview", json=body).status_code == 403
    assert client.post("/api/organize/rename", json=body).status_code == 403
    assert (library / "clip.mp4").exists()
