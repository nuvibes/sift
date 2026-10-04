# SPDX-License-Identifier: AGPL-3.0-or-later
"""The endpoints that fetch, test and remove the graphics card's runtime.

Nothing here downloads anything. What is under test is everything AROUND the download: that a
machine with no card is refused rather than made to fetch a gigabyte of libraries for hardware it
does not have, that a guest cannot start one, and that the screen is told the four things it needs
to tell its four states apart.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel import lifecycle
from sift.kernel.config import get_settings
from sift.kernel.hardware import Card
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ml import accel
from sift.main import create_app
from sift.slices.auth.crypto import derive_csrf_token
from sift.testing.auth import establish_session

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    with TestClient(create_app()) as booted:
        yield booted
    get_settings.cache_clear()


def _as(client: TestClient, role: str) -> TestClient:
    """Signed in as one role, carrying the header every writing route wants."""
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    _, token, _ = establish_session(
        db_path, role=role, username=f"accel-{role}", password="Accel-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers.update({CSRF_HEADER_NAME: derive_csrf_token(token)})
    return client


def _pretend_there_is_a_card(client: TestClient, present: bool) -> None:
    """The hardware report is read from the running application, so this is where a card is put."""
    report = client.app.state.hardware  # type: ignore[attr-defined]
    object.__setattr__(report, "cuda", present)
    # The name is a PROPERTY over the list of cards, so the card itself is what is put here,
    # which is closer to the real thing anyway: a machine has cards, not a name.
    object.__setattr__(
        report,
        "gpu_cards",
        (Card(name="NVIDIA Test Card", driver="1.0", can_compute=True),) if present else (),
    )


def test_the_screen_is_told_enough_to_tell_its_states_apart(client: TestClient) -> None:
    _pretend_there_is_a_card(client, True)

    body = _as(client, "admin").get("/api/performance/accelerator").json()

    assert body["card"] == "NVIDIA Test Card"
    assert body["supported"] is True
    assert body["installed"] is False
    # The size is answered BEFORE anybody agrees to anything. Over a gigabyte is not a download to
    # start on somebody's connection and mention afterwards.
    assert body["download_bytes"] == accel.TOTAL_BYTES
    assert body["version"] == accel.PIN


def test_a_machine_with_no_card_is_told_so_rather_than_left_to_infer_it(client: TestClient) -> None:
    """Without this the screen would have to work it out from the two device settings being refused,
    which is how somebody comes to believe their card is broken."""
    _pretend_there_is_a_card(client, False)

    body = _as(client, "admin").get("/api/performance/accelerator").json()

    assert body["supported"] is False
    assert body["card"] is None


def test_a_machine_that_already_has_a_runtime_is_offered_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The answer to "what if I already have these files".

    Somebody running Sift from its source may have installed the graphics-card runtime themselves,
    at a version they chose. Downloading over the top spends a gigabyte replacing something that
    works, and putting a folder in front of theirs on the import path takes the version decision
    away from them without saying so.
    """
    _pretend_there_is_a_card(client, True)
    monkeypatch.setattr(accel, "already_capable", lambda: True)
    admin = _as(client, "admin")

    assert admin.get("/api/performance/accelerator").json()["already_capable"] is True

    refused = admin.post("/api/performance/accelerator")
    assert refused.status_code == 409
    assert "won't replace it" in refused.json()["detail"]


def test_a_disk_without_the_room_is_refused_the_download_and_told_the_figure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wheels and what they unpack to are both on the disk while the last one is unpacked, so
    the room needed is more than twice what comes down. Checked before a byte comes down, and the
    refusal says both figures."""
    import importlib
    import shutil

    _pretend_there_is_a_card(client, True)
    monkeypatch.setattr(accel, "installed", lambda _settings: False)
    monkeypatch.setattr(accel, "already_capable", lambda: False)
    router = importlib.import_module("sift.slices.performance.router")
    monkeypatch.setattr(
        router.shutil, "disk_usage", lambda _path: shutil._ntuple_diskusage(10, 9, 1_000_000)
    )

    answer = _as(client, "admin").post("/api/performance/accelerator")

    assert answer.status_code == 409
    assert "GB free" in answer.json()["detail"]
    assert f"{accel.PEAK_BYTES / 1_000_000_000:.1f} GB" in answer.json()["detail"]
    assert _as(client, "admin").get("/api/performance/accelerator").json()["peak_bytes"] == (
        accel.PEAK_BYTES
    )


def test_a_machine_with_no_card_is_refused_the_download(client: TestClient) -> None:
    """The one outcome nobody would want: a gigabyte of NVIDIA libraries fetched onto a machine
    with no NVIDIA card in it."""
    _pretend_there_is_a_card(client, False)

    answer = _as(client, "admin").post("/api/performance/accelerator")

    assert answer.status_code == 409
    assert "can't see a graphics card" in answer.json()["detail"]


def test_a_download_hands_back_the_job_doing_it(client: TestClient) -> None:
    """Queued rather than done in the request: a request held open for 1.3 GB times out somewhere
    between the browser and here, and then nobody can tell a live download from a dead one."""
    _pretend_there_is_a_card(client, True)

    body = _as(client, "admin").post("/api/performance/accelerator").json()

    assert body["job_id"]


def test_a_guest_may_not_start_a_download_or_read_the_machine(client: TestClient) -> None:
    _pretend_there_is_a_card(client, True)
    guest = _as(client, "guest")

    assert guest.get("/api/performance/accelerator").status_code == 403
    assert guest.post("/api/performance/accelerator").status_code == 403
    assert guest.delete("/api/performance/accelerator").status_code == 403


def test_testing_the_card_says_what_is_wrong_rather_than_only_that_it_failed(
    client: TestClient,
) -> None:
    """Nothing is installed here, so this is the honest answer, and it is a SENTENCE. A boolean
    on its own leaves somebody with a red mark and no next step."""
    _pretend_there_is_a_card(client, True)

    body = _as(client, "admin").post("/api/performance/accelerator/test").json()

    assert body["works"] is False
    assert "not installed" in body["problem"]


def test_removing_it_when_there_is_nothing_to_remove_is_not_an_error(client: TestClient) -> None:
    _pretend_there_is_a_card(client, True)

    body = _as(client, "admin").delete("/api/performance/accelerator").json()

    assert body["installed"] is False


def test_asking_for_a_download_that_is_already_here_answers_with_what_is_here(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pressing it twice, or pressing it on an install that already has the runtime.

    Neither enqueues a second gigabyte: the answer is the state, which is what the screen redraws
    from. A job queued here would download over the top of files that are already correct.
    """
    _pretend_there_is_a_card(client, True)
    monkeypatch.setattr(accel, "installed", lambda _settings: True)

    answered = _as(client, "admin").post("/api/performance/accelerator")

    assert answered.status_code == 200
    assert answered.json()["job_id"] is None


# --- restarting the server -----------------------------------------------------------------------
#
# It restarts the computer running the LIBRARY, wherever the person asking happens to be sitting.
# That is the whole reason it is a route rather than a button in the desktop shell: read from a
# browser or from a second copy of Sift in client mode, restarting the application in front of the
# reader would restart the wrong computer, report success, and change nothing.


def test_a_supervised_backend_is_asked_to_stop(client: TestClient) -> None:
    stopped: list[bool] = []
    lifecycle.forget()
    lifecycle.stops_with(lambda: stopped.append(True))
    try:
        answer = _as(client, "admin").post("/api/performance/restart")
    finally:
        lifecycle.forget()

    assert answer.status_code == 202, answer.text
    assert answer.json() == {"restarting": True}
    assert stopped == [True]


def test_a_restart_writes_who_asked_and_from_which_device_into_history(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one act on the computer running Sift that lives here: History says who restarted it and
    from which device, and a refused restart, which changed nothing, says nothing."""
    monkeypatch.setattr("sift.kernel.machine_acts.machine_name", lambda: "DESK-ONE")
    admin = _as(client, "admin")
    lifecycle.forget()
    assert admin.post("/api/performance/restart").status_code == 409
    lifecycle.stops_with(lambda: None)
    try:
        answer = admin.post("/api/performance/restart", params={"device": "LAPTOP-TWO"})
    finally:
        lifecycle.forget()

    assert answer.status_code == 202, answer.text
    lines = [
        "".join(piece["text"] for piece in one["pieces"])
        for one in admin.get("/api/ledger", params={"verb": "restarted"}).json()["items"]
    ]
    assert lines == ["You restarted Sift on DESK-ONE, from LAPTOP-TWO"]


def test_a_backend_nobody_is_watching_refuses_and_says_so(client: TestClient) -> None:
    """The refusal is the feature. Stopping here would leave the library off the air with a screen
    still saying it was coming back."""
    lifecycle.forget()

    answer = _as(client, "admin").post("/api/performance/restart")

    assert answer.status_code == 409
    assert "Nothing is watching" in answer.text


def test_a_guest_cannot_restart_the_library(client: TestClient) -> None:
    lifecycle.forget()
    lifecycle.stops_with(lambda: pytest.fail("a guest reached the stop"))
    try:
        answer = _as(client, "guest").post("/api/performance/restart")
    finally:
        lifecycle.forget()

    assert answer.status_code == 403
