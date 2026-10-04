# SPDX-License-Identifier: AGPL-3.0-or-later
"""The delete endpoints, driven against the real application.

The rules must survive HTTP, and the three refusals are distinct: 404 nothing there for you, 403
you may not, 409 the disk says no. A guest told "admins only" about a file they cannot see has
learned it exists, so 404 comes before 403.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.testing.auth import establish_session
from sift.testing.library import (
    hide_for,
    seed_asset,
    seed_folder,
    seed_root,
    share_folder,
    write_rows,
)

pytestmark = [pytest.mark.integration]

A_ROOT = "01HX0000000000000000000011"
A_FOLDER = "01HX0000000000000000000012"
AN_ASSET = "01HX0000000000000000000013"

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def library(tmp_path: Path) -> Path:
    directory = tmp_path / "media"
    directory.mkdir()
    return directory


@pytest.fixture
def client(app: FastAPI, library: Path) -> Iterator[TestClient]:
    with TestClient(app) as c:
        db_path = c.app.state.database.path  # type: ignore[attr-defined]
        seed_root(db_path, A_ROOT, folder_id=A_FOLDER, path=library)
        seed_asset(
            db_path,
            AN_ASSET,
            root_id=A_ROOT,
            folder_id=A_FOLDER,
            root_path=library,
            cache_dir=Path(get_settings().cache_dir),
        )
        # Real media: restoring goes through the ingress gate, which refuses placeholder bytes.
        (library / "clip.mp4").write_bytes((CORPUS / "accepted.mp4").read_bytes())
        yield c


def sign_in(client: TestClient, role: str) -> str:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role=role, username=f"trash-{role}", password="Trash-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


# --- the two tiers over HTTP ------------------------------------------------------------------


def test_the_default_mode_forgets_the_entry_and_keeps_the_file(
    client: TestClient, library: Path
) -> None:
    """With no `mode` the entry is forgotten and the file kept."""
    sign_in(client, "admin")

    response = client.delete(f"/api/assets/{AN_ASSET}")

    assert response.status_code == 204
    assert (library / "clip.mp4").exists()


def test_a_guest_cannot_forget_even_an_entry_they_can_see(
    client: TestClient, library: Path
) -> None:
    """Forget is admin-only: it drops the shared index entry and cascades away its curation. Shared
    with the guest first, so the answer is the 403 an operation they may not do gets, not the 404 an
    invisible asset gets, and the file stays where it is."""
    sign_in(client, "guest")
    # The seeded folder is not shared, so first make it visible to this user.
    _share_with_current_guest(client)

    response = client.delete(f"/api/assets/{AN_ASSET}?mode=sift")

    assert response.status_code == 403
    assert (library / "clip.mp4").exists()


def _share_with_current_guest(client: TestClient) -> None:
    """Write the grant straight to the database file, as the fixtures seed everything: the app's
    write lock is bound to the test client's loop."""
    share_folder(
        client.app.state.database.path,  # type: ignore[attr-defined]
        A_FOLDER,
        _current_user(client),
        grant_id="01HX0000000000000000000014",
    )


def _current_user(client: TestClient) -> str:
    return str(client.get("/api/auth/me").json()["id"])


# --- who may delete from disk -----------------------------------------------------------------


def test_a_guest_who_can_see_the_file_is_refused_the_destructive_mode(
    client: TestClient, library: Path
) -> None:
    """403, because they may see it and may not do this to it."""
    sign_in(client, "guest")
    _share_with_current_guest(client)

    response = client.delete(f"/api/assets/{AN_ASSET}?mode=disk")

    assert response.status_code == 403
    assert (library / "clip.mp4").exists()


def test_a_guest_who_cannot_see_the_file_is_told_it_is_not_there(
    client: TestClient, library: Path
) -> None:
    """A guest who cannot see the file gets 404, so the endpoint is no existence oracle."""
    sign_in(client, "guest")

    response = client.delete(f"/api/assets/{AN_ASSET}?mode=disk")

    assert response.status_code == 404
    assert (library / "clip.mp4").exists()


def test_an_id_that_never_existed_answers_a_guest_identically(client: TestClient) -> None:
    """The same 404, so the two cannot be told apart from outside."""
    sign_in(client, "guest")

    invisible = client.delete(f"/api/assets/{AN_ASSET}?mode=disk")
    imaginary = client.delete("/api/assets/01HX0000000000000000000404?mode=disk")

    assert invisible.status_code == imaginary.status_code == 404
    assert invisible.json() == imaginary.json()


def test_deleting_from_disk_on_a_read_only_root_is_a_conflict_not_a_refusal(
    client: TestClient, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting on a read-only root is a 409 saying what to check: the folder refuses."""
    sign_in(client, "admin")
    monkeypatch.setattr("sift.kernel.content.library.is_writable", lambda _path: False)

    response = client.delete(f"/api/assets/{AN_ASSET}?mode=disk")

    assert response.status_code == 409
    assert "not allowed to write" in response.json()["detail"]
    assert (library / "clip.mp4").exists()


def test_deleting_from_disk_on_a_managed_root_removes_the_file(
    client: TestClient, library: Path
) -> None:
    """Deleting from disk on a managed root removes the file for good; there is no bin."""
    sign_in(client, "admin")

    deleted = client.delete(f"/api/assets/{AN_ASSET}?mode=disk")

    assert deleted.status_code == 204
    assert not (library / "clip.mp4").exists()


def test_the_bin_endpoints_are_gone(client: TestClient) -> None:
    """There are no bin endpoints (404 or 405), asked as an admin so it is the routing table."""
    sign_in(client, "admin")

    assert client.get("/api/trash").status_code in (404, 405)
    assert client.post("/api/trash/01HX0000000000000000000404/restore").status_code in (404, 405)
    assert client.post("/api/trash/purge").status_code in (404, 405)


def test_a_state_changing_call_without_the_token_is_refused(client: TestClient) -> None:
    """The delete is a state change, so it carries the same cross-site protection as the rest."""
    sign_in(client, "admin")
    del client.headers[CSRF_HEADER_NAME]

    assert client.delete(f"/api/assets/{AN_ASSET}").status_code == 403


# --- a selection, in one request ----------------------------------------------------------------


def test_a_selection_is_ONE_request_and_the_answer_counts_what_went(
    client: TestClient, library: Path
) -> None:
    """A selection is one request, answered with a count."""
    sign_in(client, "admin")

    answer = client.post("/api/assets/delete", json={"asset_ids": [AN_ASSET]})

    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "changed": 1,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    # `sift` is still the default here as well as on the single route: a caller who forgets the
    # mode forgets a file, they do not delete one.
    assert (library / "clip.mp4").exists()


def test_the_same_id_twice_is_one_file_and_not_one_refusal(
    client: TestClient, library: Path
) -> None:
    """The same id twice is one file, not a refusal."""
    sign_in(client, "admin")

    answer = client.post("/api/assets/delete", json={"asset_ids": [AN_ASSET, AN_ASSET]})

    assert answer.json() == {
        "changed": 1,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }


def test_one_refusal_does_not_stop_the_rest(client: TestClient, library: Path) -> None:
    """A read-only folder in a selection of two hundred must not refuse the other hundred and
    ninety-nine. The count says how many were refused and the sentence says why, once."""
    sign_in(client, "admin")

    answer = client.post(
        "/api/assets/delete",
        json={"asset_ids": ["01HX0000000000000000000404", AN_ASSET]},
    )

    body = answer.json()
    assert (body["changed"], body["skipped"]) == (1, 1)
    assert body["reason"], "a refusal with no sentence leaves the screen nothing to say"


def test_a_guest_is_refused_the_whole_selection(client: TestClient, library: Path) -> None:
    """The bulk route refuses a guest with a 403, not a 200 counting a refusal."""
    sign_in(client, "guest")
    _share_with_current_guest(client)

    answer = client.post("/api/assets/delete", json={"asset_ids": [AN_ASSET], "mode": "sift"})

    assert answer.status_code == 403, answer.text
    assert (library / "clip.mp4").exists()


# --- a whole folder ------------------------------------------------------------------------------
#
# A folder delete can do part of what was asked; these cover what it takes, refuses and leaves.

A_SUBFOLDER = "01HX0000000000000000000020"
A_DEEPER_FOLDER = "01HX0000000000000000000021"
# The asset ids differ before their last character: `seed_asset` derives the location id from it.
AN_ASSET_INSIDE = "01HX0000000000000000000022"
A_DEEPER_ASSET = "01HX0000000000000000000032"

FOLDERS = "/api/folders"


def _seed_a_subtree(client: TestClient, library: Path) -> None:
    """`sets/` with a file and `sets/more/` with another. Directories first: a scan may run between
    writes, and a row with no directory is one it rightly forgets."""
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    (library / "sets" / "more").mkdir(parents=True, exist_ok=True)
    seed_folder(db_path, A_SUBFOLDER, root_id=A_ROOT, parent_id=A_FOLDER, rel_path="sets")
    seed_folder(
        db_path, A_DEEPER_FOLDER, root_id=A_ROOT, parent_id=A_SUBFOLDER, rel_path="sets/more"
    )
    seed_asset(
        db_path,
        AN_ASSET_INSIDE,
        root_id=A_ROOT,
        folder_id=A_SUBFOLDER,
        root_path=library,
        cache_dir=Path(get_settings().cache_dir),
        filename="inside.mp4",
        rel_path="sets/inside.mp4",
    )
    seed_asset(
        db_path,
        A_DEEPER_ASSET,
        root_id=A_ROOT,
        folder_id=A_DEEPER_FOLDER,
        root_path=library,
        cache_dir=Path(get_settings().cache_dir),
        filename="deeper.mp4",
        rel_path="sets/more/deeper.mp4",
    )


def test_a_folder_takes_its_subfolders_and_all_their_files_with_it(
    client: TestClient, library: Path
) -> None:
    """A folder takes its whole subtree, deepest directory first, or `sets` would stand."""
    sign_in(client, "admin")
    _seed_a_subtree(client, library)

    response = client.delete(f"{FOLDERS}/{A_SUBFOLDER}")

    assert response.status_code == 200, response.text
    said = response.json()
    assert said == {"files": 2, "directories": 2, "left_behind": False}
    assert not (library / "sets").exists(), "the folder itself came off the disk"
    assert (library / "clip.mp4").exists(), "and nothing outside it was touched"
    # And the rows went with the bytes, so the folder list does not offer a folder that is gone.
    names = [folder["id"] for folder in client.get("/api/library/folders").json()["folders"]]
    assert A_SUBFOLDER not in names
    assert A_DEEPER_FOLDER not in names


def test_a_folder_holding_something_sift_does_not_index_is_left_standing(
    client: TestClient, library: Path
) -> None:
    """The indexed files go; a directory holding something unindexed stays, reported."""
    sign_in(client, "admin")
    _seed_a_subtree(client, library)
    notes = library / "sets" / "notes.txt"
    notes.write_text("not Sift's", encoding="utf-8")

    response = client.delete(f"{FOLDERS}/{A_SUBFOLDER}")

    assert response.status_code == 200, response.text
    said = response.json()
    assert said["files"] == 2, "every file Sift indexed still went"
    assert said["left_behind"] is True
    assert notes.read_text(encoding="utf-8") == "not Sift's", "and what it did not index is intact"
    assert (library / "sets").exists()
    # The row stays with the directory, so what Sift lists and what is on the disk still agree.
    names = [folder["id"] for folder in client.get("/api/library/folders").json()["folders"]]
    assert A_SUBFOLDER in names
    assert A_DEEPER_FOLDER not in names, "the empty one below it was removed and its row with it"


def test_a_library_folder_is_refused_and_nothing_is_touched(
    client: TestClient, library: Path
) -> None:
    """409. "Delete" on a library folder means "stop reading this library", which is a different
    act with its own confirmation and which touches no file at all."""
    sign_in(client, "admin")

    response = client.delete(f"{FOLDERS}/{A_FOLDER}")

    assert response.status_code == 409, response.text
    assert (library / "clip.mp4").exists()


def test_a_folder_sift_may_not_change_is_refused_before_any_file_goes(
    client: TestClient, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 409 for a folder Sift may not change, checked for the whole subtree before a file goes."""
    sign_in(client, "admin")
    _seed_a_subtree(client, library)
    monkeypatch.setattr("sift.kernel.content.library.is_writable", lambda _path: False)

    response = client.delete(f"{FOLDERS}/{A_SUBFOLDER}")

    assert response.status_code == 409, response.text
    assert (library / "sets" / "inside.mp4").exists()
    assert (library / "sets" / "more" / "deeper.mp4").exists()


def test_a_guest_who_can_see_the_folder_is_told_they_may_not(
    client: TestClient, library: Path
) -> None:
    """403 and not 404, because they can see it. The other way round is the leak this is about."""
    sign_in(client, "guest")
    _share_with_current_guest(client)
    _seed_a_subtree(client, library)

    response = client.delete(f"{FOLDERS}/{A_FOLDER}")

    assert response.status_code == 403, response.text
    assert (library / "clip.mp4").exists()


def test_a_guest_who_cannot_see_the_folder_is_told_it_is_not_there(
    client: TestClient, library: Path
) -> None:
    """A guest who cannot see the folder gets 404."""
    sign_in(client, "guest")
    _seed_a_subtree(client, library)

    response = client.delete(f"{FOLDERS}/{A_SUBFOLDER}")

    assert response.status_code == 404, response.text
    assert (library / "sets" / "inside.mp4").exists()


def test_the_vault_wins_over_an_ordinary_refusal_in_one_selection(
    client: TestClient, library: Path
) -> None:
    """In one selection the vault refusal replaces an ordinary one: only it can be acted on."""
    user_id = sign_in(client, "admin")
    hide_for(client.app.state.database.path, "asset", AN_ASSET, user_id)  # type: ignore[attr-defined]

    answer = client.post(
        "/api/assets/delete",
        json={"asset_ids": ["01HX0000000000000000000404", AN_ASSET]},
    )

    body = answer.json()
    assert (body["changed"], body["skipped"]) == (0, 2)
    assert body["vault_locked"] is True
    assert body["reason"], "a locked selection with no sentence leaves the screen nothing to say"


def test_a_second_ordinary_refusal_does_not_overwrite_the_first_sentence(
    client: TestClient, library: Path
) -> None:
    """An ordinary reason is said once, not replaced by the next file's."""
    sign_in(client, "admin")

    answer = client.post(
        "/api/assets/delete",
        json={
            "asset_ids": [
                "01HX0000000000000000000404",
                "01HX0000000000000000000405",
                AN_ASSET,
            ]
        },
    )

    body = answer.json()
    assert (body["changed"], body["skipped"]) == (1, 2)
    assert body["vault_locked"] is False
    assert body["reason"]


def test_deleting_a_folder_writes_one_event_for_each_file_that_went(
    client: TestClient, library: Path
) -> None:
    """Deleting a folder writes one `deleted` event per file, with the folder as its object."""
    sign_in(client, "admin")
    _seed_a_subtree(client, library)

    assert client.delete(f"{FOLDERS}/{A_SUBFOLDER}").status_code == 200

    rows = _read(
        client.app.state.database.path,  # type: ignore[attr-defined]
        "SELECT s.subject_id, s.name, d.object_kind, d.object_id FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'deleted' AND s.kind = 'asset' ORDER BY s.name",
    )
    assert rows == [
        (A_DEEPER_ASSET, "deeper.mp4", "folder", A_SUBFOLDER),
        (AN_ASSET_INSIDE, "inside.mp4", "folder", A_SUBFOLDER),
    ]


@pytest.mark.parametrize(("mode", "left_on_disk"), [("disk", False), ("sift", True)])
def test_a_delete_records_whether_the_file_left_the_disk(
    client: TestClient, library: Path, mode: str, left_on_disk: bool
) -> None:
    """One verb for both, and they are not the same act to the person reading about it:
    one removed bytes nobody can put back, the other only stopped Sift listing the file. The event
    says which, so History can."""
    sign_in(client, "admin")

    assert client.delete(f"/api/assets/{AN_ASSET}?mode={mode}").status_code == 204

    assert (library / "clip.mp4").exists() is left_on_disk
    rows = _read(
        client.app.state.database.path,  # type: ignore[attr-defined]
        "SELECT d.payload FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'deleted' AND s.kind = 'asset'",
    )
    assert [json.loads(str(payload))["from"] for (payload,) in rows] == [mode]


def test_a_selection_delete_records_the_same_kind(client: TestClient, library: Path) -> None:
    """The bulk path is a second writer of the same act, and it says the same thing."""
    sign_in(client, "admin")

    answer = client.post("/api/assets/delete", json={"asset_ids": [AN_ASSET], "mode": "sift"})

    assert answer.status_code == 200
    rows = _read(
        client.app.state.database.path,  # type: ignore[attr-defined]
        "SELECT payload FROM workbench_decisions WHERE verb = 'deleted'",
    )
    assert [json.loads(str(payload))["from"] for (payload,) in rows] == ["sift"]


def _read(path: Path, sql: str) -> list[tuple[object, ...]]:
    """Read rows through the kernel's own database, never the driver: the rule every test is held to."""
    from sift.kernel.db import Database

    async def run() -> list[tuple[object, ...]]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return [tuple(row) for row in await database.fetch_all(sql, ())]
        finally:
            await database.close()

    return asyncio.run(run())


def _inside_a_zip(client: TestClient) -> None:
    """Make the seeded file a picture inside an archive, as a scan of a ZIP records one."""
    write_rows(
        client.app.state.database.path,  # type: ignore[attr-defined]
        [
            (
                "UPDATE asset_locations SET archive_rel_path = ?, member_path = ? "
                "WHERE asset_id = ?",
                ("shoot.zip", "01.png", AN_ASSET),
            )
        ],
    )


def test_a_picture_inside_an_archive_is_refused_from_disk_in_the_servers_words(
    client: TestClient,
) -> None:
    """A 409 with the sentence the screen shows, never a 204 for a cache copy, and the sheet's
    question answers before anything is pressed."""
    sign_in(client, "admin")
    _inside_a_zip(client)

    asked = client.post("/api/assets/delete/check", json={"asset_ids": [AN_ASSET]})
    refused = client.delete(f"/api/assets/{AN_ASSET}?mode=disk")
    many = client.post("/api/assets/delete", json={"asset_ids": [AN_ASSET], "mode": "disk"})

    assert asked.json()["inside_archives"] == 1
    assert asked.json()["why"] == refused.json()["detail"]
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("That picture is inside a ZIP file")
    assert (many.json()["changed"], many.json()["skipped"]) == (0, 1)
    assert many.json()["reason_many"].startswith("Those pictures are inside a ZIP file")


def test_the_sheet_is_told_nothing_is_refused_for_an_ordinary_file(client: TestClient) -> None:
    sign_in(client, "admin")

    asked = client.post("/api/assets/delete/check", json={"asset_ids": [AN_ASSET]})

    assert asked.json() == {"inside_archives": 0, "why": None}
