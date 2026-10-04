# SPDX-License-Identifier: AGPL-3.0-or-later
"""The faces settings routes over HTTP: fetching the models, looking through the library, and
how much of a scan is still to come."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobQueue
from sift.kernel.jobs.tuning import DEFAULT_PRIORITY, WAITED_ON_PRIORITY
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.slices.faces import folder_import
from sift.slices.faces import jobs as faces_jobs
from sift.slices.faces.folder_import import FACE_FOLDER_IMPORT
from sift.slices.faces.router import _drop_the_chosen_folder
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
    priority_of,
    queued_types,
    sign_in,
    turn_on,
    unlock,
    write,
)
from sift.testing.library import seed_face, seed_grant
from sift.testing.settings import set_app_setting

pytestmark = pytest.mark.integration

#: The fixtures this file shares with the files they are defined in, found here by name.
app = test_routes.app
client = test_routes.client
models_counted_as_here = test_routes.models_counted_as_here
scene = test_routes.scene


# --- fetching the models --------------------------------------------------------------------


def test_asking_for_the_models_queues_a_job_rather_than_downloading_in_the_request(
    client: TestClient,
) -> None:
    """Minutes over a domestic connection, so a request holding it open times out somewhere
    between the browser and here. The job id comes back so a screen can watch the same bar the
    dashboard already draws, and cancel it the same way."""
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post("/api/faces/weights/fetch")

    assert answer.status_code == 200
    job_id = answer.json()["job_id"]
    queued = client.get("/api/jobs").json()
    assert job_id in {job["id"] for job in queued["jobs"]}


def test_a_fetch_somebody_pressed_goes_in_front_of_the_library_wide_work(
    client: TestClient,
) -> None:
    """A press goes ahead of arrival order, because somebody is waiting on it.

    At the ordinary priority, which is arrival order, a fetch somebody pressed would wait behind a
    face sweep's worth of queued files, and until that fetch finished, nothing in the library could
    be scanned at all.
    """
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post("/api/faces/weights/fetch")

    assert priority_of(db_path(client), answer.json()["job_id"]) == WAITED_ON_PRIORITY
    assert WAITED_ON_PRIORITY < DEFAULT_PRIORITY, "a press would run LAST"


def test_asking_for_the_models_again_says_so_in_the_job(client: TestClient) -> None:
    """Download the models again carries `again` to the job, which fetches the files already here
    too; the ordinary press carries it as false, so only what is missing is fetched."""
    turn_on(client)
    sign_in(client, "admin")

    again = client.post("/api/faces/weights/fetch", params={"again": "true"}).json()["job_id"]
    plain = client.post("/api/faces/weights/fetch").json()["job_id"]

    async def payloads() -> dict[str, dict[str, object]]:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            rows = await database.fetch_all(
                "SELECT id, payload FROM jobs WHERE id IN (?, ?)", (again, plain)
            )
        finally:
            await database.close()
        return {str(row["id"]): json.loads(str(row["payload"])) for row in rows}

    read = asyncio.run(payloads())
    assert read[again].get("again") is True
    assert read[plain].get("again") is False


#: Every job type this slice can put in the queue.
#:
#: Named rather than asking for an empty queue outright. The queue is shared and carries the
#: application's own housekeeping (the quarantine sweep schedules itself at boot and is waiting
#: before any test runs), so "the queue is empty" is not true for a reason that has nothing to do
#: with faces, and would read as this consent gate having let something through.
#: Read from the module that registers them rather than written out: a job type renamed with this
#: spelled by hand would quietly match nothing, and an empty answer is what this asserts.
_FACE_WORK = frozenset(
    {
        faces_jobs.FACE_SCAN,
        faces_jobs.FACE_REMATCH,
        faces_jobs.FACE_REGROUP,
        faces_jobs.FACE_FETCH_WEIGHTS,
        faces_jobs.FACE_SWEEP,
    }
)


def _face_work(client: TestClient) -> list[dict[str, object]]:
    """Everything in the queue that this slice put there."""
    page = client.get("/api/jobs", params={"limit": 200}).json()
    return [job for job in page["jobs"] if job["type"] in _FACE_WORK]


def test_with_the_feature_off_nothing_is_queued_and_nothing_reaches_the_internet(
    client: TestClient,
) -> None:
    """The consent gate at the one route that can make this machine reach out.

    Fetching a model for a feature nobody has consented to is exactly the network call the gate
    exists to prevent, and this is the route where it would happen.
    """
    sign_in(client, "admin")

    answer = client.post("/api/faces/weights/fetch")

    assert answer.status_code == 409
    assert "switched off" in answer.json()["detail"]
    assert _face_work(client) == [], "and nothing was queued to go and fetch one"


def test_asking_to_scan_the_library_queues_a_sweep_that_names_who_asked(
    client: TestClient, models_counted_as_here: None
) -> None:
    """Switching recognition on examines nothing by itself, so this is the control that covers what
    was already in the library.

    The user is carried in the payload rather than resolved later from nothing: the sweep's work
    list comes from what that user may see, so a sweep with no user named would be work with
    no scope at all.
    """
    turn_on(client)
    user_id = sign_in(client, "admin")

    answer = client.post("/api/faces/scan")

    assert answer.status_code == 200
    job_id = answer.json()["job_id"]
    queued = client.get("/api/jobs").json()["jobs"]
    sweep = next(job for job in queued if job["id"] == job_id)
    assert sweep["type"] == "face_sweep"
    assert user_id


def test_the_library_cannot_be_swept_while_recognition_is_off(client: TestClient) -> None:
    """The consent gate again. A sweep is the largest amount of face work there is, and asking for
    it before anybody has agreed to the feature is the case the gate exists for."""
    sign_in(client, "admin")

    answer = client.post("/api/faces/scan")

    assert answer.status_code == 409
    assert "switched off" in answer.json()["detail"]
    assert _face_work(client) == [], "and no sweep was queued"


def _sweeps(db: Path) -> list[tuple[str, dict[str, object]]]:
    """Every sweep row and its payload, on a connection of this helper's own. See `write`."""

    async def run() -> list[tuple[str, dict[str, object]]]:
        database = Database(db, readers=1)
        await database.connect()
        try:
            rows = await database.fetch_all(
                "SELECT state, payload FROM jobs WHERE type = 'face_sweep' ORDER BY id"
            )
        finally:
            await database.close()
        return [(str(row["state"]), json.loads(str(row["payload"]))) for row in rows]

    return asyncio.run(run())


def test_a_second_press_while_the_library_is_being_looked_through_is_refused(
    client: TestClient, models_counted_as_here: None
) -> None:
    """One walk of the library at a time, and a press from scratch still means from scratch.

    A sweep is a chain of pages, so a second press while one is going starts a second walk from the
    top and queues every file both reach twice: the whole library decoded twice, for a doubled
    from-scratch press. The screen hides the button while it follows a run; a second window does
    not know about that, so the refusal is the route's.
    """
    turn_on(client)
    sign_in(client, "admin")

    # The first walk stays queued rather than running and finishing on an empty library: no model
    # is installed under a test's data directory, so the queue does not hand Identify work out.
    first = client.post("/api/faces/scan", params={"force": "true"})
    second = client.post("/api/faces/scan")

    assert first.status_code == 200
    assert second.status_code == 409
    assert "already being looked through" in second.json()["detail"]
    sweeps = _sweeps(db_path(client))
    assert len(sweeps) == 1, "and no second walk was queued"
    assert sweeps[0][1]["force"] is True, "the press from scratch still carries force"


def test_two_identical_presses_in_the_same_instant_are_one_walk(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, models_counted_as_here: None
) -> None:
    """The one gap the count of running sweeps leaves: two presses that both ask before either has
    queued anything. Made here by having the count find nothing both times; the queue's own
    dedupe, in its write transaction, is what still makes them one walk."""
    turn_on(client)
    sign_in(client, "admin")

    async def nothing_running(self: JobQueue, *job_types: str) -> int:
        return 0

    monkeypatch.setattr(JobQueue, "outstanding", nothing_running)

    first = client.post("/api/faces/scan")
    second = client.post("/api/faces/scan")

    assert first.status_code == second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]
    assert len(_sweeps(db_path(client))) == 1


def _job_ids(client: TestClient) -> set[str]:
    return {str(job["id"]) for job in client.get("/api/jobs", params={"limit": 200}).json()["jobs"]}


@pytest.mark.parametrize(
    ("press", "body"),
    [("/api/faces/scan", None), ("/api/tasks/faces/run", {"at": "now"})],
)
def test_a_press_with_the_chosen_models_missing_is_refused_and_queues_nothing(
    client: TestClient, press: str, body: dict[str, str] | None
) -> None:
    """With Permissive chosen and its models not downloaded, the Faces screen says not ready, and
    the library route and the Tasks screen's Run now must not answer with a started pass whose
    every file would then wait on a download nobody asked for. Both doors ask the one question
    (`cannot_scan`), and answer it before anything is queued."""
    turn_on(client)
    sign_in(client, "admin")
    set_app_setting(db_path(client), "faces.model", '"permissive"')
    before = _job_ids(client)

    answer = client.post(press, json=body) if body is not None else client.post(press)

    assert answer.status_code == 409
    assert answer.json()["detail"].startswith("Sift hasn't downloaded the Permissive recognition")
    assert _job_ids(client) == before, "and nothing was queued"


def test_the_settings_route_says_how_many_files_want_a_look_and_why(client: TestClient) -> None:
    """The backlog, as the two reasons the Faces pane gives: never looked at, and looked at under
    an older rule, so a library that was never scanned does not read like a finished one."""
    turn_on(client)
    sign_in(client, "admin")
    asset, root, folder = new_id(), new_id(), new_id()
    write(
        db_path(client),
        [
            (_INSERT_ASSET, (asset, f"identity-{asset}", 10, "never.mp4", _EPOCH)),
            ("UPDATE assets SET probed_at = ? WHERE id = ?", (_EPOCH, asset)),
            # A copy that is THERE: a file with no present copy is not work waiting, so the
            # backlog counts only files Sift can read.
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root, "never", "C:\\never", _EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                "VALUES (?, ?, NULL, ?, ?)",
                (folder, root, "", "never"),
            ),
            (
                _INSERT_LOCATION,
                (new_id(), asset, root, folder, "never.mp4", "never.mp4", _EPOCH, _EPOCH),
            ),
        ],
    )

    body = client.get("/api/faces/settings").json()

    assert body["never_scanned"] == 1
    assert body["scanned_under_older_rules"] == 0


def test_a_pack_cannot_be_built_before_the_models_are_installed(client: TestClient) -> None:
    """Refused in words rather than raised.

    A pack records which model produced its numbers, so building one needs that model present.
    Switching recognition on installs nothing (Sift ships no models), so "none yet" is an
    ordinary state on a fresh install, and a stack trace is the wrong way to say so.
    """
    turn_on(client)
    sign_in(client, "admin")

    refused = client.post(
        "/api/faces/packs/export",
        json={"name": "My People", "person_ids": [], "include_pictures": False},
    )

    assert refused.status_code == 409
    assert "installed" in refused.json()["detail"]


def test_an_empty_pack_is_still_refused_rather_than_crashing(client: TestClient) -> None:
    """Nothing to export is not a reason to answer differently: the model stamp is needed either
    way, and the answer has to be the same sentence rather than a different failure."""
    turn_on(client)
    sign_in(client, "admin")

    assert (
        client.post("/api/faces/packs/export", json={"name": "x", "person_ids": []}).status_code
        == 409
    )


def test_a_pack_that_is_not_a_pack_is_refused(client: TestClient) -> None:
    """Somebody else's file, or a truncated download. Refused, not half-imported."""
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/packs/import",
        files={"file": ("pack.zip", b"not a pack at all", "application/zip")},
    )

    # 400 once the models are there and the file itself is judged; 409 before that, because the
    # model has to be present to read a pack against at all. Never a 500: what is asserted here
    # is that a file somebody chose by mistake is answered rather than crashed on.
    assert answer.status_code in (400, 409), answer.text
    assert answer.status_code != 500


def test_a_pack_taken_in_before_the_models_are_there_is_a_conflict_said_in_words(
    client: TestClient,
) -> None:
    """A pack is read against the face model; without one the file is not at fault, so the answer
    is the model's own sentence as a conflict, never a refusal of the file."""
    from sift.slices.faces.weights import WeightError

    async def no_models(raw: bytes, *, while_off: bool = False) -> object:
        raise WeightError("The face models aren't downloaded yet.")

    sign_in(client, "admin")
    client.app.state.faces.import_pack = no_models  # type: ignore[attr-defined]

    answer = client.post(
        "/api/faces/packs/import",
        files={"file": ("pack.zip", b"pretend-pack", "application/zip")},
    )

    assert answer.status_code == 409
    assert answer.json()["detail"] == "The face models aren't downloaded yet."


def test_packs_cannot_be_shared_while_recognition_is_off(client: TestClient) -> None:
    """The consent gate on the way out. A pack is faces, and sharing them is using the feature.
    The way in is open (see the next test), and a file that is not a pack is still refused as that."""
    sign_in(client, "admin")

    assert (
        client.post("/api/faces/packs/export", json={"name": "x", "person_ids": []}).status_code
        == 409
    )
    assert (
        client.post(
            "/api/faces/packs/import",
            files={"file": ("pack.zip", b"not-a-pack", "application/zip")},
        ).status_code
        == 400
    )


def test_a_fingerprints_file_is_taken_in_while_recognition_is_off_and_says_so(
    client: TestClient,
) -> None:
    """A new library brings its People first. The file lands switched off, the answer says
    recognition is off so the screen can say nothing is recognized yet, and no re-match is queued
    for a feature that would only skip it: the library pass asks for one once it is on."""
    from sift.slices.faces.service import PackOutcome

    asked: list[bool] = []

    async def canned(raw: bytes, *, while_off: bool = False) -> PackOutcome:
        asked.append(while_off)
        return PackOutcome(added=4, held=["Bryn Calloway"], alias_clashes=[])

    sign_in(client, "admin")
    client.app.state.faces.import_pack = canned  # type: ignore[attr-defined]

    answer = client.post(
        "/api/faces/packs/import",
        files={"file": ("pack.zip", b"pretend-pack", "application/zip")},
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["added"] == 4
    assert answer.json()["recognizing"] is False
    assert asked == [True]
    assert "face_rematch" not in queued_types(client)

    turn_on(client)
    assert (
        client.post(
            "/api/faces/packs/import",
            files={"file": ("pack.zip", b"pretend-pack", "application/zip")},
        ).json()["recognizing"]
        is True
    )
    assert "face_people_from_files" in queued_types(client)


def test_a_pack_name_cannot_write_its_own_response_header(client: TestClient) -> None:
    """The filename goes into a header, and a header is one of the few places where quoting wrongly
    is an injection rather than a cosmetic bug. Reduced rather than escaped, so nothing that could
    need quoting survives, checked on the helper, since building a pack needs models installed."""
    from sift.slices.faces.router import _pack_filename

    made = _pack_filename('evil"\r\nX-Injected: yes')

    assert "\r" not in made and "\n" not in made and '"' not in made
    assert made.endswith("-faces.zip")


def test_who_sift_can_recognize_is_listed_and_searchable(client: TestClient) -> None:
    """The question is "do I have them already", asked before adding somebody.

    Keyed on having reference faces rather than on being a Person: somebody with none is not
    recognizable, and listing them would answer yes to a question whose real answer is no.
    """
    turn_on(client)
    sign_in(client, "admin")

    body = client.get("/api/faces/known").json()

    assert body == {"items": [], "total": 0}
    assert client.get("/api/faces/known", params={"q": "nobody"}).json()["total"] == 0


def test_the_roster_carries_the_picture_each_person_is_drawn_by(
    client: TestClient, scene: Scene
) -> None:
    """A picker of these people draws each by their picture, as the People picker does.

    The cover columns come through the People wall's own reading for this viewer, so the whole
    file a named face gave is here (never the face), with the crops' version.
    """
    turn_on(client)
    sign_in(client, "admin")
    client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})

    (known,) = client.get("/api/faces/known").json()["items"]

    assert known["id"] == scene.person
    assert known["cover_track_id"] is None
    assert known["cover_asset_id"] == scene.asset
    assert known["cover_upload_id"] is None
    assert known["art"]
    # One confirmed face: the band her page draws it in, which the choosers mark her with.
    assert (known["faces"], known["verdict"]) == (1, "weak")


def test_the_roster_carries_the_marks_that_keep_somebody_out_of_a_swap(
    client: TestClient, scene: Scene
) -> None:
    """A swap's chooser of these people draws the refused ones refused, so the marks ride along."""
    turn_on(client)
    sign_in(client, "admin")
    client.post(f"/api/faces/{scene.track}/confirm", json={"person_id": scene.person})

    (before,) = client.get("/api/faces/known").json()["items"]
    marked = client.put(f"/api/swap/keep-out/person/{scene.person}", json={"kept_out": True})
    (after,) = client.get("/api/faces/known").json()["items"]

    assert marked.status_code == 200
    assert (before["keep_from_swaps"], after["keep_from_swaps"]) == (False, True)
    assert after["keep_local"] is False


def test_the_roster_is_refused_while_recognition_is_off(client: TestClient) -> None:
    sign_in(client, "admin")

    assert client.get("/api/faces/known").status_code == 409


def test_a_successful_import_reports_all_four_answers(client: TestClient) -> None:
    """What the route does with a result, which is the part a route is for: how many faces the
    file brought and how many people it names. The work itself is the service's."""
    from sift.slices.faces.service import PackOutcome

    turn_on(client)
    sign_in(client, "admin")

    async def canned(raw: bytes, *, while_off: bool = False) -> PackOutcome:
        assert raw, "the route did not read the uploaded file"
        return PackOutcome(added=7, held=["Ada", "Grace"], alias_clashes=[])

    client.app.state.faces.import_pack = canned  # type: ignore[attr-defined]

    body = client.post(
        "/api/faces/packs/import",
        files={"file": ("pack.zip", b"pretend-pack", "application/zip")},
    ).json()

    assert body == {"added": 7, "people": 2, "recognizing": True}


def test_a_pack_that_brought_faces_asks_for_the_pass_over_fingerprints(
    client: TestClient,
) -> None:
    """The pass places what the file holds by face and matches the library again after it; the
    route asks for it once and matches nothing itself."""
    from sift.slices.faces.service import PackOutcome

    turn_on(client)
    sign_in(client, "admin")

    async def canned(raw: bytes, *, while_off: bool = False) -> PackOutcome:
        return PackOutcome(added=3, held=["Ada"], alias_clashes=[])

    client.app.state.faces.import_pack = canned  # type: ignore[attr-defined]

    answer = client.post(
        "/api/faces/packs/import",
        files={"file": ("pack.zip", b"pretend-pack", "application/zip")},
    )

    assert answer.status_code == 200
    assert "face_people_from_files" in queued_types(client)
    assert "face_rematch" not in queued_types(client)


def test_a_pack_that_cannot_be_read_is_refused_rather_than_half_imported(
    client: TestClient,
) -> None:
    """Somebody else's file, or a truncated download. The message does not quote it back."""
    turn_on(client)
    sign_in(client, "admin")

    async def broken(raw: bytes, **_: object) -> object:
        raise ValueError("the central directory is missing")

    client.app.state.faces.import_pack = broken  # type: ignore[attr-defined]

    answer = client.post(
        "/api/faces/packs/import",
        files={"file": ("pack.zip", b"junk", "application/zip")},
    )

    assert answer.status_code == 400
    assert "central directory" not in answer.text, "the refusal quoted the file's own error back"


def test_a_pack_made_with_the_other_model_is_refused_with_its_own_sentence(
    client: TestClient,
) -> None:
    """The pack's refusal is written to be shown, and it is a 400 with those words: never a
    server error."""
    turn_on(client)
    sign_in(client, "admin")

    async def other_model(raw: bytes, **_: object) -> object:
        from sift.slices.faces.packs import PackError

        raise PackError("This file's facial fingerprints were made with the other face model.")

    client.app.state.faces.import_pack = other_model  # type: ignore[attr-defined]

    answer = client.post(
        "/api/faces/packs/import",
        files={"file": ("faces.zip", b"pretend-pack", "application/zip")},
    )

    assert answer.status_code == 400
    assert answer.json()["detail"] == (
        "This file's facial fingerprints were made with the other face model."
    )


def test_an_exported_pack_comes_back_as_a_file_to_save(
    client: TestClient, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn_on(client)
    sign_in(client, "admin")
    scene.attribute(client)

    async def everybody() -> list[str]:
        return [scene.person]

    async def canned(**_: object) -> bytes:
        return b"PK\x03\x04pretend"

    monkeypatch.setattr(faces_of(client), "shareable_people", everybody)
    monkeypatch.setattr(faces_of(client), "export_pack", canned)

    built = client.post("/api/faces/packs/export", json={"name": "My People", "person_ids": []})

    assert built.status_code == 200
    assert built.content == b"PK\x03\x04pretend"
    assert built.headers["content-type"] == "application/zip"
    assert 'filename="My-People-faces.zip"' in built.headers["content-disposition"]


def test_share_everyone_leaves_out_whoever_a_shut_hidden_holds_back(
    client: TestClient, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The file must say what the pane says.** The pane lists the people Sift can recognize held
    to the People wall, so a person Hidden holds back is not on it while Hidden is shut, and "Share
    everyone" must not carry her name, other names and facial fingerprints in a file meant for
    somebody else. Held back while shut, named once it is open, and an empty list refused in words
    rather than read as everybody."""
    turn_on(client)
    admin = sign_in(client, "admin")
    scene.attribute(client)
    asked: list[list[str]] = []

    async def everybody() -> list[str]:
        return [scene.person]

    async def canned(*, person_ids: Sequence[str], **_: object) -> bytes:
        asked.append(list(person_ids))
        return b"PK\x03\x04pretend"

    monkeypatch.setattr(faces_of(client), "shareable_people", everybody)
    monkeypatch.setattr(faces_of(client), "export_pack", canned)

    assert client.post("/api/faces/packs/export", json={"name": "x"}).status_code == 200
    assert asked == [[scene.person]]

    scene.hide(client, "person", scene.person, admin)
    for sent in ([], [scene.person]):
        refused = client.post("/api/faces/packs/export", json={"name": "x", "person_ids": sent})
        assert refused.status_code == 409
        assert "nobody here" in refused.json()["detail"]
    assert asked == [[scene.person]], "a shut Hidden's person reached the file"

    assert unlock(client) == 200
    assert client.post("/api/faces/packs/export", json={"name": "x"}).status_code == 200
    assert asked == [[scene.person], [scene.person]]


def queued_import(client: TestClient) -> dict[str, object] | None:
    """The payload of the folder import a route queued, or None when it queued none."""
    path = db_path(client)

    async def run() -> dict[str, object] | None:
        database = Database(path, readers=1)
        await database.connect()
        try:
            row = await database.fetch_one(
                "SELECT payload FROM jobs WHERE type = ?", (FACE_FOLDER_IMPORT,)
            )
            return None if row is None else dict(json.loads(str(row["payload"])))
        finally:
            await database.close()

    return asyncio.run(run())


def staged_files(client: TestClient) -> list[str]:
    """The files the upload door copied for its task, relative to the copy."""
    payload = queued_import(client)
    assert payload is not None
    root = faces_of(client).scratch_root() / str(payload["staged"])  # type: ignore[attr-defined]
    return sorted(one.relative_to(root).as_posix() for one in root.rglob("*") if one.is_file())


@pytest.fixture
def no_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """An application whose workers never start, so a queued task and its files stay as queued."""

    async def idle(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", idle)


def test_an_upload_answers_at_once_with_the_task_that_reads_it(
    no_workers: None, client: TestClient, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request copies the files and queues the task; no face is read inside it."""

    async def never(folder: Path) -> object:
        raise AssertionError("a picture was read inside the request")

    turn_on(client)
    sign_in(client, "admin")
    monkeypatch.setattr(faces_of(client), "import_person_folder", never)

    answer = client.post(
        "/api/faces/references/folder",
        files=[("files", ("Gallery/Wren Halloway/one.jpg", b"stand-in", "image/jpeg"))],
    )

    assert answer.status_code == 202
    payload = queued_import(client)
    assert payload is not None and answer.json()["job_id"]
    assert staged_files(client) == ["Wren Halloway/one.jpg"]
    # The chosen folder's own name goes with the task: what its people say they came from.
    assert payload["named"] == "Gallery"


def test_a_folder_import_with_recognition_off_is_refused(client: TestClient, scene: Scene) -> None:
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/references/folder",
        files=[("files", ("Gallery/Ada/one.jpg", b"stand-in", "image/jpeg"))],
    )

    assert answer.status_code == 409
    assert queued_import(client) is None


def test_a_folder_import_judges_the_session_before_it_reads_the_form(client: TestClient) -> None:
    """A locked session gets the lock's answer, and a guest the refusal, with no body sent.

    The body is read by a dependency of the route, and one declared ahead of the admin check
    would answer both of them 422 about the missing folder, the door asked second.
    """
    turn_on(client)
    sign_in(client, "guest")
    assert client.post("/api/faces/references/folder").status_code == 403

    sign_in(client, "admin")
    allowed = client.put("/api/settings", json={"values": {"vault.app_lock_enabled": True}})
    assert allowed.status_code == 204, allowed.text
    assert client.post("/api/auth/lock").json()["outcome"] == "locked"
    assert client.post("/api/faces/references/folder").status_code == 423


def test_a_folder_import_with_no_folder_asks_an_admin_to_choose_one(client: TestClient) -> None:
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post("/api/faces/references/folder")

    assert answer.status_code == 422
    assert answer.json()["detail"] == "choose a folder to import"


def test_a_loose_file_at_the_top_belongs_to_nobody_and_is_not_copied(
    client: TestClient, scene: Scene
) -> None:
    """Skipped rather than refused: a Mac's `.DS_Store` beside the people is an ordinary folder."""
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/references/folder",
        files=[
            ("files", ("notes.txt", b"loose", "text/plain")),
            ("files", ("Ada/one.jpg", b"stand-in", "image/jpeg")),
        ],
    )

    assert answer.status_code == 202
    assert staged_files(client) == ["Ada/one.jpg"], "the loose file was written anyway"


def test_more_files_than_one_import_will_take_is_refused_before_anything_is_queued(
    client: TestClient, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both caps are here because either alone is bypassable: a file cap lets through one enormous
    file, and a byte cap lets through a hundred thousand tiny ones, each costing a round of
    detection."""
    monkeypatch.setattr(folder_import, "MAX_FOLDER_FILES", 1)
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/references/folder",
        files=[
            ("files", ("Gallery/Ada/one.jpg", b"a", "image/jpeg")),
            ("files", ("Gallery/Ada/two.jpg", b"b", "image/jpeg")),
        ],
    )

    assert answer.status_code == 413
    assert queued_import(client) is None


def test_a_folder_of_more_than_a_thousand_pictures_reaches_the_task(
    client: TestClient, scene: Scene
) -> None:
    """The form parser's own default refuses a thousand and one files before the route runs, which
    is every real gallery. The route's cap is the only one that applies."""
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/references/folder",
        files=[
            ("files", (f"Gallery/Ada Lumen/{index}.jpg", b"x", "image/jpeg"))
            for index in range(1001)
        ],
    )

    assert answer.status_code == 202
    assert len(staged_files(client)) == 1001


def test_far_more_files_than_one_import_will_take_is_answered_in_the_same_words(
    client: TestClient, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the cap the parser stops reading; the answer is still the route's, not the parser's."""
    monkeypatch.setattr(folder_import, "MAX_FOLDER_FILES", 1)
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/references/folder",
        files=[("files", (f"Gallery/Ada/{index}.jpg", b"a", "image/jpeg")) for index in range(3)],
    )

    assert answer.status_code == 413
    assert "Import it in parts" in answer.json()["detail"]


def test_any_other_refusal_of_the_form_reaches_the_page_as_the_parser_said_it(
    client: TestClient, scene: Scene
) -> None:
    """Only too many files is put in this route's words; a form with more fields than an import
    sends is refused by the parser, with its own status, and not as a crash."""
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/references/folder",
        files=[("files", ("Gallery/Ada/one.jpg", b"a", "image/jpeg"))],
        data={f"field{index}": "x" for index in range(9)},
    )

    assert answer.status_code == 400
    assert "fields" in answer.json()["detail"].casefold()


def test_a_folder_bigger_than_one_import_will_take_is_refused_and_its_copy_removed(
    client: TestClient, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_import, "MAX_FOLDER_BYTES", 1)
    turn_on(client)
    sign_in(client, "admin")
    scratch = faces_of(client).scratch_root()  # type: ignore[attr-defined]

    answer = client.post(
        "/api/faces/references/folder",
        files=[("files", ("Gallery/Ada/one.jpg", b"more than one byte", "image/jpeg"))],
    )

    assert answer.status_code == 413
    assert queued_import(client) is None
    assert list(scratch.iterdir()) == [], "a refused upload left its copy behind"


def test_the_wrapper_reduction_survives_being_handed_nothing() -> None:
    """An import of no files at all. Reached before anything is written, so it has to answer."""
    assert _drop_the_chosen_folder([]) == []


# --- the path door ---------------------------------------------------------------------------


def granted_gallery(client: TestClient, tmp_path: Path) -> Path:
    """A folder of people inside a folder Sift has been given."""
    given = tmp_path / "given"
    gallery = given / "Gallery"
    (gallery / "Wren Halloway").mkdir(parents=True)
    seed_grant(db_path(client), "given", path=given.resolve())
    return gallery


def test_a_folder_inside_a_granted_folder_is_queued_by_its_path(
    client: TestClient, scene: Scene, tmp_path: Path
) -> None:
    """Nothing is sent but the path, and the answer is the task: the folder is read by Sift."""
    gallery = granted_gallery(client, tmp_path)
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post("/api/faces/references/folder/path", json={"path": str(gallery)})

    assert answer.status_code == 202, answer.text
    assert queued_import(client) == {"grant": "given", "within": "Gallery"}


@pytest.mark.parametrize("where", ["beside", "parent"], ids=["outside", "above-the-grant"])
def test_a_folder_outside_every_granted_folder_is_refused(
    client: TestClient, scene: Scene, tmp_path: Path, where: str
) -> None:
    """The picker's confinement holds for this door too: a path it could not show is not read."""
    granted_gallery(client, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    turn_on(client)
    sign_in(client, "admin")

    asked = elsewhere if where == "beside" else tmp_path
    answer = client.post("/api/faces/references/folder/path", json={"path": str(asked)})

    assert answer.status_code == 400
    assert "hasn't been given that folder" in answer.json()["detail"]
    assert queued_import(client) is None


def test_a_path_that_is_not_a_folder_is_refused_in_words(
    client: TestClient, scene: Scene, tmp_path: Path
) -> None:
    turn_on(client)
    sign_in(client, "admin")

    answer = client.post(
        "/api/faces/references/folder/path", json={"path": str(tmp_path / "not-there")}
    )

    assert answer.status_code == 400
    assert "cannot find the folder" in answer.json()["detail"]


def test_the_path_door_is_an_admins_and_needs_recognition_on(
    client: TestClient, scene: Scene, tmp_path: Path
) -> None:
    gallery = granted_gallery(client, tmp_path)
    sign_in(client, "admin")
    off = client.post("/api/faces/references/folder/path", json={"path": str(gallery)})
    assert off.status_code == 409

    turn_on(client)
    sign_in(client, "guest")
    guest = client.post("/api/faces/references/folder/path", json={"path": str(gallery)})
    assert guest.status_code == 403
    assert queued_import(client) is None


def test_a_group_asks_about_fingerprints_and_a_yes_makes_the_person_then_matches_again(
    client: TestClient, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The question is keyed by the group, so a screen puts it only on a group it draws; the Yes
    names the presser as the maker and asks for one re-match. A spent entry is a 404."""
    from sift.slices.faces.service_fingerprints import FingerprintOffer

    async def offers() -> list[FingerprintOffer]:
        return [
            FingerprintOffer(
                entry_id="e1",
                name="Wren Halloway",
                pile_id="g1",
                likeness=0.8,
                faces=3,
                confirmed=12,
                source="Studio Faces",
            )
        ]

    made_by: list[str] = []

    async def make(entry_id: str, *, by: str) -> str | None:
        made_by.append(by)
        return "p9" if entry_id == "e1" else None

    turn_on(client)
    sign_in(client, "admin")
    monkeypatch.setattr(faces_of(client), "fingerprint_offers", offers)
    monkeypatch.setattr(faces_of(client), "make_person_from_entry", make)

    assert client.get("/api/faces/fingerprints/offers").json() == {
        "items": [
            {
                "entry_id": "e1",
                "name": "Wren Halloway",
                "pile_id": "g1",
                # What the question says the entry holds and where it came from.
                "faces": 3,
                "confirmed": 12,
                "source": "Studio Faces",
            }
        ]
    }
    assert client.post("/api/faces/fingerprints/e1/person").json() == {"person_id": "p9"}
    assert made_by and made_by[0]
    assert "face_rematch" in queued_types(client)
    assert client.post("/api/faces/fingerprints/gone/person").status_code == 404


def test_a_groups_fingerprints_question_and_its_yes_wait_for_recognition(
    client: TestClient,
) -> None:
    sign_in(client, "admin")

    for answer in (
        client.get("/api/faces/fingerprints/offers"),
        client.post("/api/faces/fingerprints/e1/person"),
    ):
        assert answer.status_code == 409
        assert "switched off" in answer.json()["detail"]


def test_a_pack_that_adds_nothing_asks_for_no_rematching_either(
    client: TestClient, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Importing the same pack a second time. Nothing arrived, so nothing has to be matched again."""
    from sift.slices.faces.service import PackOutcome

    async def stubbed(raw: bytes, *, while_off: bool = False) -> PackOutcome:
        return PackOutcome(added=0, held=[], alias_clashes=[])

    turn_on(client)
    sign_in(client, "admin")
    monkeypatch.setattr(faces_of(client), "import_pack", stubbed)

    client.post(
        "/api/faces/packs/import",
        files=[("file", ("set.zip", b"stand-in", "application/zip"))],
    )

    assert "face_rematch" not in queued_types(client)
    assert "face_people_from_files" not in queued_types(client)


def test_the_groups_screen_is_paged_and_says_how_many_there_are(
    client: TestClient, scene: Scene
) -> None:
    """A swept library has a pile for every face that joined nothing: hundreds of them.

    Each one costs a query for its faces and a question to the resolver, so asking for all of them
    at once is a screen nobody waits for. Asserted with more piles than fit on a page, because a
    limit is only a limit when something exceeds it.

    Every pile carries a real face on a file the viewer may see, and that is load-bearing rather
    than thoroughness. Written as thirty EMPTY piles, a total that counted stored rows would say
    thirty while the groups list, which resolves what the viewer may see, drew nothing: the number
    and the screen describing different sets, which is what the scoped count avoids.
    """
    admin = sign_in(client)
    turn_on(client)
    assert admin
    write(
        db_path(client),
        [
            statement
            for index in range(30)
            for statement in (
                (
                    "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at)"
                    " VALUES (?, 'open', X'0000803F', 1, 0, 0)",
                    (f"pile-{index:03d}",),
                ),
                (
                    "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, "
                    "quality, person_id, confidence, attribution, pile_id, created_at) "
                    "VALUES (?, ?, 0, 0, 1, 0.8, NULL, NULL, NULL, ?, 0)",
                    (f"track-{index:03d}", scene.asset, f"pile-{index:03d}"),
                ),
            )
        ],
    )

    answer = client.get("/api/faces/groups", params={"limit": 10})
    assert answer.status_code == 200, answer.text
    first = answer.json()
    assert first["total"] == 30, "the total is every pile, not the page"
    assert len(first["groups"]) <= 10
    assert first["groups"], "a total of thirty over an empty screen is the fault this guards"

    second = client.get("/api/faces/groups", params={"limit": 10, "offset": 10}).json()
    assert second["total"] == 30

    # No pile appears on both pages. Ordered by size then id, so two of the same size cannot swap
    # places between the two requests and show up twice or not at all.
    assert not ({one["id"] for one in first["groups"]} & {one["id"] for one in second["groups"]})


# --- how much of a scan is still to come -----------------------------------------------------


def test_the_work_left_is_nothing_while_recognition_is_off(client: TestClient) -> None:
    """Zero rather than a refusal, because a screen asks this on every install.

    The bar that reads it is drawn whether or not the feature is on, so answering 409 here would
    mean every install that never switched recognition on shows an error where a number goes.

    A scan is left queued first. The queue is read without asking whether recognition is on, so
    without that there would be nothing to count and the zero would be an accident of the fixture.
    """
    sign_in(client)
    write(
        db_path(client),
        [
            (
                "INSERT INTO jobs (id, type, state, priority, payload, max_attempts, attempts, "
                "progress, created_at, updated_at) "
                "VALUES ('left-over-1', 'face_scan', 'queued', 0, '{}', 3, 0, 0, 0, 0)",
                (),
            )
        ],
    )

    answer = client.get("/api/faces/work-left")

    assert answer.status_code == 200
    assert answer.json() == {"files": 0, "moments": 0}


def test_the_work_left_reports_the_files_queued_and_what_they_amount_to(
    client: TestClient, scene: Scene
) -> None:
    """Two numbers, because the second cannot be got from the first.

    Files left is what a bar counts down; what it costs is the moments those files add up to. A
    long file weighs more than a short one, so moments has to come back larger than files rather
    than tracking it.
    """
    sign_in(client)
    turn_on(client)
    write(
        db_path(client),
        [
            ("UPDATE assets SET duration_ms = 300000 WHERE id = ?", (scene.asset,)),
            (
                "INSERT INTO jobs (id, type, state, priority, payload, max_attempts, attempts, "
                "progress, created_at, updated_at) "
                "VALUES ('work-left-1', 'face_scan', 'queued', 0, ?, 3, 0, 0, 0, 0)",
                (f'{{"asset_id": "{scene.asset}"}}',),
            ),
        ],
    )

    body = client.get("/api/faces/work-left").json()

    assert body["files"] == 1
    assert body["moments"] > body["files"]


def test_the_work_left_is_an_admins_answer(client: TestClient) -> None:
    """It counts what is queued across the whole library, which is a fact about the library."""
    turn_on(client)
    sign_in(client, "guest")

    assert client.get("/api/faces/work-left").status_code == 403


def test_the_pile_total_follows_what_this_viewer_may_see(client: TestClient, scene: Scene) -> None:
    """The number and the screen have to describe the same set.

    Counted as stored rows, concealing the files behind every pile would leave the total exactly
    where it was while the screen emptied, and the badge on the rail would not move when the vault
    opened or shut, though what could be shown changed underneath it. A count that includes what is
    being kept back is the same disclosure a facet count would be, reached from the other side.
    """
    turn_on(client)
    make_pile(client, scene)
    admin = sign_in(client)

    before = client.get("/api/faces/groups").json()
    assert before["total"] == 1, before

    scene.hide(client, "asset", scene.asset, admin)

    after = client.get("/api/faces/groups").json()
    assert after["groups"] == [], "the screen already scoped itself"
    assert after["total"] == 0, "and the total has to agree with it"


def test_a_named_face_says_whether_it_became_a_reference(client: TestClient, scene: Scene) -> None:
    """Agreeing to a face OFFERS it, and several rules can decline it.

    A crop below the quality floor is skipped, so is a near-copy of one the person already holds,
    and so is an appearance that has already given its share. Silent, somebody could name four
    faces, watch the reference count go up by one, and have nowhere at all to find out which one
    it was, which reads as the number being broken rather than as three pictures having been
    declined.

    Matched through the crop's identity, because a reference does not record which appearance it
    came from: the picture a reference holds IS the picture a detection stored.
    """
    turn_on(client)
    scene.attribute(client, how="confirmed")
    sign_in(client)

    before = client.get(f"/api/faces/identified/people/{scene.person}").json()
    assert before["items"], before
    assert all(not face["is_reference"] for face in before["items"]), (
        "nothing has been filed as a reference yet"
    )

    write(
        db_path(client),
        [
            (
                "INSERT INTO face_references (id, person_id, crop_path, crop_digest, embedding, "
                "quality, origin, recognizer, created_at) "
                "VALUES (?, ?, NULL, 'seeded-digest', X'0000803F', 0.8, 'confirmed', 'r', 0)",
                (new_id(), scene.person),
            )
        ],
    )

    after = client.get(f"/api/faces/identified/people/{scene.person}").json()
    assert [face["is_reference"] for face in after["items"]] == [True], after


def test_a_crop_too_poor_to_learn_from_says_so_on_the_face_itself(
    client: TestClient, scene: Scene
) -> None:
    """The judgement is OFFERED rather than applied, and this is the half that offers it.

    A press does what it appears to: Sift files every crop somebody agrees to and marks the ones it
    thinks are poor instead of refusing them. Declining silently moves the reference count by less
    than the number of faces agreed to with nothing on screen saying which were declined, and a
    mark that nothing checks makes a silent rule out of an offered one all over again.
    """
    turn_on(client)
    sign_in(client, "admin")

    good = client.get(f"/api/assets/{scene.asset}/faces").json()
    assert [face["teachable"] for face in good] == [True]

    # The same face, below the floor a reference has to clear.
    write(
        db_path(client),
        [("UPDATE face_tracks SET quality = ? WHERE id = ?", (0.01, scene.track))],
    )

    poor = client.get(f"/api/assets/{scene.asset}/faces").json()
    assert [face["teachable"] for face in poor] == [False]


def test_a_pile_that_was_finished_does_not_take_a_slot_on_the_page(
    client: TestClient, scene: Scene
) -> None:
    """The page and the total count the SAME piles, and this is what proves it.

    The total counts piles with at least one unclaimed face this viewer may see. A page taken in
    SQL over every pile of the status, with the empty ones dropped afterwards, would walk a second
    population with the same offset.

    A pile whose faces have all been named is still a row in `face_piles`, so it would take a slot
    in the page while contributing nothing to the count: twelve finished piles ahead of the real
    ones would bring a first page of ten back with NOTHING on it under a pager saying eight.

    Not a guest-only fault, which is why this signs in as an admin with the vault wide open: it
    would happen to an admin, seeing everything, as soon as anybody finished a pile.
    """
    admin = sign_in(client)
    turn_on(client)
    assert admin

    # Twelve finished piles (every face claimed), then eight with real questions in them. Sorted
    # by stored size descending, the finished ones would lead, because their stored size is 1, ties
    # break by id, and 'done-' sorts before 'open-'.
    write(
        db_path(client),
        [(_INSERT_PERSON, ("somebody", "Somebody"))]
        + [
            statement
            for index in range(12)
            for statement in (
                (
                    "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at)"
                    " VALUES (?, 'open', X'0000803F', 1, 0, 0)",
                    (f"done-{index:03d}",),
                ),
                (
                    "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, "
                    "quality, person_id, confidence, attribution, pile_id, created_at) "
                    "VALUES (?, ?, 0, 0, 1, 0.8, 'somebody', NULL, NULL, ?, 0)",
                    (f"claimed-{index:03d}", scene.asset, f"done-{index:03d}"),
                ),
            )
        ]
        + [
            statement
            for index in range(8)
            for statement in (
                (
                    "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at)"
                    " VALUES (?, 'open', X'0000803F', 1, 0, 0)",
                    (f"open-{index:03d}",),
                ),
                (
                    "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, "
                    "quality, person_id, confidence, attribution, pile_id, created_at) "
                    "VALUES (?, ?, 0, 0, 1, 0.8, NULL, NULL, NULL, ?, 0)",
                    (f"waiting-{index:03d}", scene.asset, f"open-{index:03d}"),
                ),
            )
        ],
    )

    page = client.get("/api/faces/groups", params={"limit": 10}).json()

    assert page["total"] == 8, "only piles with an unanswered question in them are counted"
    assert len(page["groups"]) == 8, (
        "the page holds every counted pile. Short of the total with rows to spare means the page "
        "and the count are describing different sets again"
    )
    assert all(not one["id"].startswith("done-") for one in page["groups"])


def test_the_groups_wall_can_be_opened_where_it_was_left(client: TestClient, scene: Scene) -> None:
    """`?from=` puts the named pile first, so a link opens where the sender was.

    The piles are given ids of the shape the app mints. That is not decoration: an anchor is read
    off an untrusted address, so a value that is not shaped like an id is treated as one that
    matches nothing, and a fixture using `pile-000` asks for the top of the wall while looking
    exactly like a fixture asking for the fourth card.
    """
    admin = sign_in(client)
    turn_on(client)
    assert admin
    piles = [f"01HX{'0' * 20}{index:02d}" for index in range(6)]
    write(
        db_path(client),
        [
            statement
            for index, pile in enumerate(piles)
            for statement in (
                (
                    "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at)"
                    " VALUES (?, 'open', X'0000803F', 1, 0, 0)",
                    (pile,),
                ),
                (
                    "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, "
                    "quality, person_id, confidence, attribution, pile_id, created_at) "
                    "VALUES (?, ?, 0, 0, 1, 0.8, NULL, NULL, NULL, ?, 0)",
                    (f"track-{index:03d}", scene.asset, pile),
                ),
            )
        ],
    )

    every = client.get("/api/faces/groups", params={"limit": 50}).json()
    ordered = [one["id"] for one in every["groups"]]
    assert len(ordered) > 3, "needs enough piles for a position to mean something"
    wanted = ordered[3]

    anchored = client.get("/api/faces/groups", params={"limit": 2, "from": wanted}).json()

    assert [one["id"] for one in anchored["groups"]] == ordered[3:5]
    assert anchored["total"] == every["total"], "anchoring narrows nothing"


def test_an_anchor_that_is_gone_opens_the_top_rather_than_refusing(
    client: TestClient, scene: Scene
) -> None:
    """A stale link is the ordinary case on a queue, not an error.

    And the answer for a pile that never existed has to match the answer for one being kept back,
    or asking by id is a way to find out which.
    """
    sign_in(client)
    turn_on(client)
    make_pile(client, scene)

    top = client.get("/api/faces/groups", params={"limit": 5}).json()
    stale = client.get(
        "/api/faces/groups", params={"limit": 5, "from": "01JQZZZZZZZZZZZZZZZZZZZZZZ"}
    ).json()

    assert stale["groups"] == top["groups"]
    assert stale["total"] == top["total"]


def test_a_discarded_first_card_reopens_the_same_page_not_the_top(
    client: TestClient, scene: Scene
) -> None:
    """Page two of Faces to name, discard the group that was its first card.

    The wall's address names that group (`from`) and the offset it was at (`near`). The group is
    gone from the list, so `from` finds nothing, and the answer is the page it was on, closed up
    over the gap, rather than page one. A `near` past the end of a list that has shrunk is served as
    asked, with no rows; stepping that back to the last page is the wall's one rule for a page past
    the end (`CardPaging`), not a second copy here.
    """
    sign_in(client)
    turn_on(client)
    piles = [f"01HX{'0' * 20}{index:02d}" for index in range(6)]
    write(
        db_path(client),
        [
            statement
            for index, pile in enumerate(piles)
            for statement in (
                (
                    "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at)"
                    " VALUES (?, 'open', X'0000803F', 1, 0, 0)",
                    (pile,),
                ),
                (
                    "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, "
                    "quality, person_id, confidence, attribution, pile_id, created_at) "
                    "VALUES (?, ?, 0, 0, 1, 0.8, NULL, NULL, NULL, ?, 0)",
                    (f"track-{index:03d}", scene.asset, pile),
                ),
            )
        ],
    )
    ordered = [
        one["id"] for one in client.get("/api/faces/groups", params={"limit": 50}).json()["groups"]
    ]
    assert len(ordered) == 6
    first_of_page_two = ordered[2]
    write(
        db_path(client),
        [("UPDATE face_piles SET status = 'ignored' WHERE id = ?", (first_of_page_two,))],
    )

    back = client.get(
        "/api/faces/groups", params={"limit": 2, "from": first_of_page_two, "near": 2}
    ).json()

    assert back["offset"] == 2
    assert [one["id"] for one in back["groups"]] == ordered[3:5], "page two, closed up"

    past = client.get("/api/faces/groups", params={"limit": 2, "from": NEVER_EXISTED, "near": 40})
    assert past.status_code == 200
    assert past.json()["offset"] == 40
    assert past.json()["groups"] == []
    # No total is asserted: this listing counts with a window over the page's own rows, so a page
    # past the end says 0. The wall asks the front once for the real count (`CardPaging`).


def test_the_identified_wall_can_be_opened_where_it_was_left(
    client: TestClient, scene: Scene
) -> None:
    """`?from=` on the wall of people Sift has decided about.

    Two people, so a position that is not zero exists to be asked for: with one, an anchor that
    is honoured and an anchor that is ignored give the same page.
    """
    turn_on(client)
    scene.attribute(client)
    other_person, other_track = new_id(), new_id()
    seed_face(db_path(client), other_track, scene.asset, data_dir=data_dir(client))
    write(
        db_path(client),
        [
            (_INSERT_PERSON, (other_person, "Grace Hopper")),
            (_ATTRIBUTE, (other_person, "matched", other_track)),
        ],
    )
    sign_in(client)

    every = client.get("/api/faces/identified/people", params={"limit": 50}).json()
    ordered = [card["person_id"] for card in every["people"]]
    assert len(ordered) == 2, "needs two cards for a position to mean anything"

    anchored = client.get(
        "/api/faces/identified/people", params={"limit": 1, "from": ordered[1]}
    ).json()

    assert [card["person_id"] for card in anchored["people"]] == [ordered[1]]
    assert anchored["offset"] == 1, "the answer says where it landed, so the pager can say so too"
    assert anchored["total"] == every["total"], "anchoring narrows nothing"


def test_an_identified_anchor_that_is_gone_opens_the_top(client: TestClient, scene: Scene) -> None:
    turn_on(client)
    scene.attribute(client)
    sign_in(client)

    top = client.get("/api/faces/identified/people", params={"limit": 5}).json()
    stale = client.get(
        "/api/faces/identified/people", params={"limit": 5, "from": NEVER_EXISTED}
    ).json()

    assert stale == top


def test_the_nameless_card_cannot_be_anchored_to(client: TestClient, scene: Scene) -> None:
    """Everybody this user may not be told about gathers under one card keyed on nothing.

    An address cannot name it, and asking for the person hidden behind it must give the top rather
    than that card's place. Otherwise a link is a way of asking which row somebody was folded
    into, which is the disclosure the gathering exists to prevent.

    The nameless card is put SECOND on purpose. First, its place and the top are the same page, and
    a lookup that happily returned the nameless card's position would be indistinguishable from one
    that refused.
    """
    turn_on(client)
    scene.attribute(client)
    admin = sign_in(client)
    scene.hide(client, "person", scene.person, admin)
    scene.unname_by_hand(client)

    # Somebody visible, decided about AFTER the hidden one, so they lead the wall.
    seen_person, seen_track = new_id(), new_id()
    seed_face(db_path(client), seen_track, scene.asset, data_dir=data_dir(client))
    write(
        db_path(client),
        [
            (_INSERT_PERSON, (seen_person, "Grace Hopper")),
            (_ATTRIBUTE, (seen_person, "matched", seen_track)),
        ],
    )

    top = client.get("/api/faces/identified/people", params={"limit": 5}).json()
    assert [card["person_id"] for card in top["people"]] == [seen_person, None], (
        "the nameless card has to be second, or its place and the top are one page"
    )

    anchored = client.get(
        "/api/faces/identified/people", params={"limit": 5, "from": scene.person}
    ).json()

    assert anchored == top
    assert anchored["offset"] == 0, "anchoring to a name nobody may be told about starts at the top"


def test_one_persons_appearances_can_be_opened_where_they_were_left(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    sign_in(client)
    second_track = new_id()
    seed_face(db_path(client), second_track, scene.asset, data_dir=data_dir(client))
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})
    client.post("/api/faces/name", json={"track_ids": [second_track], "person_id": scene.person})

    every = client.get(f"/api/faces/identified/people/{scene.person}", params={"limit": 50}).json()
    ordered = [item["track_id"] for item in every["items"]]
    assert len(ordered) == 2, "needs two appearances for a position to mean anything"

    anchored = client.get(
        f"/api/faces/identified/people/{scene.person}",
        params={"limit": 1, "from": ordered[1]},
    ).json()

    assert [item["track_id"] for item in anchored["items"]] == [ordered[1]]
    assert anchored["offset"] == 1
    assert anchored["total"] == every["total"]


def test_an_appearance_anchor_that_is_gone_opens_the_top(client: TestClient, scene: Scene) -> None:
    """The ordinary case on this screen: deciding about a face is what takes it off the list."""
    turn_on(client)
    sign_in(client)
    client.post("/api/faces/name", json={"track_ids": [scene.track], "person_id": scene.person})

    top = client.get(f"/api/faces/identified/people/{scene.person}", params={"limit": 5}).json()
    stale = client.get(
        f"/api/faces/identified/people/{scene.person}",
        params={"limit": 5, "from": NEVER_EXISTED},
    ).json()

    assert stale == top


def test_a_piles_own_page_can_be_opened_where_it_was_left(client: TestClient, scene: Scene) -> None:
    turn_on(client)
    sign_in(client)
    pile_id = make_pile(client, scene)
    second_track = new_id()
    seed_face(db_path(client), second_track, scene.asset, data_dir=data_dir(client))
    write(
        db_path(client),
        [("UPDATE face_tracks SET pile_id = ? WHERE id = ?", (pile_id, second_track))],
    )

    every = client.get(f"/api/faces/groups/{pile_id}", params={"limit": 50}).json()
    ordered = [face["track_id"] for face in every["group"]["faces"]]
    assert len(ordered) == 2, "needs two faces in the pile for a position to mean anything"

    anchored = client.get(
        f"/api/faces/groups/{pile_id}", params={"limit": 1, "from": ordered[1]}
    ).json()

    assert [face["track_id"] for face in anchored["group"]["faces"]] == [ordered[1]]
    assert anchored["offset"] == 1
    assert anchored["total"] == every["total"]


def test_a_face_anchor_that_is_gone_opens_the_top_of_the_pile(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    sign_in(client)
    pile_id = make_pile(client, scene)

    top = client.get(f"/api/faces/groups/{pile_id}", params={"limit": 5}).json()
    stale = client.get(
        f"/api/faces/groups/{pile_id}", params={"limit": 5, "from": NEVER_EXISTED}
    ).json()

    assert stale == top
