# SPDX-License-Identifier: AGPL-3.0-or-later
"""The desktop routes, over HTTP, against a real application and a fake desktop app.

The fake answers on 127.0.0.1 exactly where the real app's link would, with the same secret check,
so what is proved is the whole ask: an admin's request reaches the app of the computer running
Sift, a guest's never does, and a backend no app started says so rather than drawing a switch off.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.testing.auth import establish_session

pytestmark = pytest.mark.integration

PASSWORD = "A-Desktop-Test-Passw0rd!"
SECRET = "launch-secret-for-the-test"


class FakeShell:
    """What the desktop app's link answers, and every ask it heard."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str, Any]] = []
        self.starting = False
        self.firewall: dict[str, Any] = {"state": "closed", "networks": ["Private"], "scope": None}
        self.garbled = False
        self.refusal: str | None = None
        self.update: dict[str, Any] = {"ok": True, "version": "0.1.300"}

    def _taken(self) -> dict[str, Any]:
        if self.refusal is None:
            return {"ok": True, "refusal": None}
        return {"ok": False, "refusal": self.refusal}

    def answer(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        self.asked.append((method, path, body))
        if self.garbled:
            return 200, {"sharing": "not a sharing"}
        if method == "GET" and path == "/facts":
            return 200, {
                "machine": "DESK-ONE",
                "startsWithWindows": self.starting,
                "sharing": {
                    "enabled": True,
                    "live": True,
                    "address": "http://192.168.1.20:5171",
                    "port": 5171,
                },
            }
        if method == "PUT" and path == "/start-with-windows":
            self.starting = body.get("on") is True
            return 200, {"startsWithWindows": self.starting}
        if method == "GET" and path == "/firewall":
            return 200, self.firewall
        if method == "POST" and path == "/firewall":
            self.firewall = {"state": "open", "networks": ["Private"], "scope": body["scope"]}
            return 200, self.firewall
        if method == "PUT" and path == "/sharing":
            return 200, self._taken()
        if method == "GET" and path == "/storage":
            return 200, {
                "dataDir": "C:\\Lib\\data",
                "cacheDir": "C:\\Lib\\cache",
                "dataBytes": 10,
                "cacheBytes": 20,
                "lastMove": {"ok": False, "refusal": "The drive filled up."},
            }
        if method == "POST" and path in ("/storage/move", "/libraries/open"):
            return 200, self._taken()
        if method == "POST" and path == "/update":
            return 200, self.update
        if method == "GET" and path == "/log?lines=5":
            return 200, {
                "lines": ["one"],
                "path": "C:\\Sift\\shell.log",
                "size": 4,
                "present": True,
            }
        if method == "GET" and path == "/libraries":
            return 200, {
                "current": "C:\\Lib\\data",
                "libraries": [
                    {
                        "dataDir": "D:\\Other\\data",
                        "cacheDir": "D:\\Other\\cache",
                        "name": "Other",
                        "lastOpened": 1,
                    }
                ],
            }
        return 404, {"detail": "No such act."}


@pytest.fixture
def shell() -> Iterator[tuple[FakeShell, str]]:
    fake = FakeShell()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            if self.headers.get("authorization") != f"Bearer {SECRET}":
                self.send_response(401)
                self.end_headers()
                return
            size = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(size)) if size else None
            status, answer = fake.answer(self.command, self.path, body)
            data = json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_PUT = do_POST = _serve

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link: str | None) -> FastAPI:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    if link is not None:
        monkeypatch.setenv("SIFT_SHELL_URL", link)
        monkeypatch.setenv("SIFT_SHELL_TOKEN", SECRET)
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: tuple[FakeShell, str]
) -> Iterator[TestClient]:
    with TestClient(_app(tmp_path, monkeypatch, shell[1])) as running:
        yield running
    get_settings.cache_clear()


def sign_in(client: TestClient, role: str) -> None:
    db = client.app.state.database.path  # type: ignore[attr-defined]
    _user, token, csrf = establish_session(
        db, role=role, username=f"desktop-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf


def test_an_admin_reads_the_computer_running_sift(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")

    body = client.get("/api/desktop").json()

    assert body == {
        "has_app": True,
        "machine": "DESK-ONE",
        "starts_with_windows": False,
        "sharing": {
            "enabled": True,
            "live": True,
            "address": "http://192.168.1.20:5171",
            "port": 5171,
        },
    }


def test_start_with_windows_is_changed_by_the_app_there(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")

    body = client.put("/api/desktop/start-with-windows", json={"on": True}).json()

    assert body["starts_with_windows"] is True
    assert ("PUT", "/start-with-windows", {"on": True}) in shell[0].asked


def test_the_firewall_is_read_and_opened_by_the_app_there(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")

    assert client.get("/api/desktop/firewall").json()["state"] == "closed"
    opened = client.post("/api/desktop/firewall", json={"scope": "any"}).json()

    assert opened == {"state": "open", "networks": ["Private"], "scope": "any"}
    assert ("POST", "/firewall", {"scope": "any"}) in shell[0].asked


def test_a_scope_that_is_not_one_of_the_two_is_refused_before_the_app_is_asked(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")

    assert client.post("/api/desktop/firewall", json={"scope": "public"}).status_code == 422
    assert shell[0].asked == []


def test_a_guest_never_reaches_the_app(client: TestClient, shell: tuple[FakeShell, str]) -> None:
    sign_in(client, "guest")

    assert client.get("/api/desktop").status_code == 403
    assert client.put("/api/desktop/start-with-windows", json={"on": True}).status_code == 403
    assert client.post("/api/desktop/firewall", json={"scope": "any"}).status_code == 403
    assert shell[0].asked == []


def test_an_answer_of_the_wrong_shape_is_no_answer(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")
    shell[0].garbled = True

    assert client.get("/api/desktop").status_code == 503
    assert client.get("/api/desktop/firewall").status_code == 503


def test_a_backend_no_app_started_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(_app(tmp_path, monkeypatch, None)) as client:
        sign_in(client, "admin")

        assert client.get("/api/desktop").json() == {
            "has_app": False,
            "machine": None,
            "starts_with_windows": None,
            "sharing": None,
        }
        refused = client.put("/api/desktop/start-with-windows", json={"on": True})
        assert refused.status_code == 409
        assert "isn't running in the Sift app" in refused.json()["detail"]
        assert client.post("/api/desktop/firewall", json={}).status_code == 409
        assert client.get("/api/desktop/firewall").status_code == 409
    get_settings.cache_clear()


def test_an_app_that_does_not_answer_is_said_as_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A port nothing listens on: the app has gone, or its link closed.
    with TestClient(_app(tmp_path, monkeypatch, "http://127.0.0.1:9")) as client:
        sign_in(client, "admin")

        assert client.get("/api/desktop").status_code == 503
        assert client.put("/api/desktop/start-with-windows", json={"on": False}).status_code == 503
        assert client.post("/api/desktop/firewall", json={}).status_code == 503
    get_settings.cache_clear()


def test_an_app_that_refuses_the_secret_is_no_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: tuple[FakeShell, str]
) -> None:
    # Another launch's secret: the app refuses it, and the screen is told the app did not answer.
    monkeypatch.setenv("SIFT_SHELL_TOKEN", "a-secret-from-another-launch")
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SIFT_SHELL_URL", shell[1])
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        sign_in(client, "admin")

        assert client.get("/api/desktop").status_code == 503
        assert shell[0].asked == []
    get_settings.cache_clear()


def test_sharing_is_asked_of_the_app_there_and_answered_before_it_restarts(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")

    body = client.put("/api/desktop/sharing", json={"on": False}).json()

    assert body == {"ok": True, "refusal": None}
    assert ("PUT", "/sharing", {"on": False}) in shell[0].asked


def test_the_storage_folders_are_read_there_with_the_last_move(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")

    assert client.get("/api/desktop/storage").json() == {
        "data_dir": "C:\\Lib\\data",
        "cache_dir": "C:\\Lib\\cache",
        "data_bytes": 10,
        "cache_bytes": 20,
        "last_move": {"ok": False, "refusal": "The drive filled up."},
    }


def test_a_move_the_app_refuses_is_said_in_its_words(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")
    shell[0].refusal = "That folder is not empty. Choose an empty one, or make a new one."

    body = client.post("/api/desktop/storage/move", json={"folder": "E:\\Full"}).json()

    assert body == {"ok": False, "refusal": shell[0].refusal}
    assert ("POST", "/storage/move", {"folder": "E:\\Full"}) in shell[0].asked


def test_an_empty_folder_is_refused_before_the_app_is_asked(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")

    assert client.post("/api/desktop/storage/move", json={"folder": ""}).status_code == 422
    assert client.post("/api/desktop/libraries/open", json={"data_dir": ""}).status_code == 422
    assert shell[0].asked == []


def test_an_update_names_nothing_the_app_would_read(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")

    body = client.post(
        "/api/desktop/update", json={"feed": "https://elsewhere.example/evil.json"}
    ).json()

    assert body == {"ok": True, "version": "0.1.300", "reason": None}
    assert shell[0].asked == [("POST", "/update", {})]


def test_an_update_the_app_refuses_says_why(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")
    shell[0].update = {"ok": False, "reason": "none"}

    body = client.post("/api/desktop/update").json()

    assert body == {"ok": False, "version": None, "reason": "none"}


def test_the_app_log_there_is_read_by_a_count(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")

    assert client.get("/api/desktop/log", params={"lines": 5}).json()["lines"] == ["one"]
    assert client.get("/api/desktop/log", params={"lines": 5000}).status_code == 422


def test_the_libraries_the_app_remembers_are_listed_and_one_is_opened(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")

    listed = client.get("/api/desktop/libraries").json()
    opened = client.post("/api/desktop/libraries/open", json={"data_dir": "D:\\Other\\data"})

    assert listed == {
        "current": "C:\\Lib\\data",
        "libraries": [
            {
                "data_dir": "D:\\Other\\data",
                "cache_dir": "D:\\Other\\cache",
                "name": "Other",
                "last_opened": 1,
            }
        ],
    }
    assert opened.json() == {"ok": True, "refusal": None}
    assert ("POST", "/libraries/open", {"dataDir": "D:\\Other\\data"}) in shell[0].asked


def test_a_guest_never_reaches_the_app_for_any_act(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "guest")

    assert client.put("/api/desktop/sharing", json={"on": False}).status_code == 403
    assert client.get("/api/desktop/storage").status_code == 403
    assert client.post("/api/desktop/storage/move", json={"folder": "E:\\x"}).status_code == 403
    assert client.post("/api/desktop/update").status_code == 403
    assert client.get("/api/desktop/log").status_code == 403
    assert client.get("/api/desktop/libraries").status_code == 403
    opened = client.post("/api/desktop/libraries/open", json={"data_dir": "D:\\x"})
    assert opened.status_code == 403
    assert shell[0].asked == []


def test_every_act_says_so_where_no_app_started_the_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(_app(tmp_path, monkeypatch, None)) as client:
        sign_in(client, "admin")

        for answer in (
            client.put("/api/desktop/sharing", json={"on": True}),
            client.get("/api/desktop/storage"),
            client.post("/api/desktop/storage/move", json={"folder": "E:\\x"}),
            client.post("/api/desktop/update"),
            client.get("/api/desktop/log"),
            client.get("/api/desktop/libraries"),
            client.post("/api/desktop/libraries/open", json={"data_dir": "D:\\x"}),
        ):
            assert answer.status_code == 409
            assert "isn't running in the Sift app" in answer.json()["detail"]
    get_settings.cache_clear()


def test_every_act_is_no_answer_when_the_app_answers_the_wrong_shape(
    client: TestClient, shell: tuple[FakeShell, str]
) -> None:
    sign_in(client, "admin")
    shell[0].garbled = True

    assert client.put("/api/desktop/sharing", json={"on": True}).status_code == 503
    assert client.get("/api/desktop/storage").status_code == 503
    assert client.post("/api/desktop/update").status_code == 503
    assert client.get("/api/desktop/log", params={"lines": 5}).status_code == 503
    assert client.get("/api/desktop/libraries").status_code == 503


def test_every_act_is_no_answer_when_the_app_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(_app(tmp_path, monkeypatch, "http://127.0.0.1:9")) as client:
        sign_in(client, "admin")

        assert client.put("/api/desktop/sharing", json={"on": True}).status_code == 503
        assert client.get("/api/desktop/storage").status_code == 503
        assert client.post("/api/desktop/storage/move", json={"folder": "E:\\x"}).status_code == 503
        assert client.post("/api/desktop/update").status_code == 503
        assert client.get("/api/desktop/log").status_code == 503
        assert client.get("/api/desktop/libraries").status_code == 503
        opened = client.post("/api/desktop/libraries/open", json={"data_dir": "D:\\x"})
        assert opened.status_code == 503
    get_settings.cache_clear()


# --- what an act that changes the computer running Sift leaves in History ---------------------------


def _feed(client: TestClient) -> list[tuple[str | None, str]]:
    """The Settings feed, newest first, as (the act, its line) the admin reads it."""
    items = client.get("/api/ledger").json()["items"]
    return [(one["verb"], "".join(piece["text"] for piece in one["pieces"])) for one in items]


@pytest.fixture
def named(monkeypatch: pytest.MonkeyPatch) -> None:
    """The computer running Sift calls itself DESK-ONE, as the fake app there does."""
    monkeypatch.setattr("sift.kernel.machine_acts.machine_name", lambda: "DESK-ONE")


@pytest.mark.parametrize(
    ("ask", "verb", "said"),
    [
        (
            ("PUT", "/api/desktop/sharing", {"on": True}),
            "sharing_turned_on",
            "turned on network sharing on DESK-ONE",
        ),
        (
            ("PUT", "/api/desktop/sharing", {"on": False}),
            "sharing_turned_off",
            "turned off network sharing on DESK-ONE",
        ),
        (
            ("PUT", "/api/desktop/start-with-windows", {"on": True}),
            "start_with_windows_on",
            "set Sift to start with Windows on DESK-ONE",
        ),
        (
            ("POST", "/api/desktop/firewall", {"scope": "private"}),
            "firewall_opened",
            "opened the firewall port for Sift on DESK-ONE",
        ),
        (
            ("POST", "/api/desktop/storage/move", {"folder": "E:\\Empty"}),
            "storage_moved",
            "moved Sift data to another folder on DESK-ONE",
        ),
        (
            ("POST", "/api/desktop/update", None),
            "update_started",
            "started installing Sift 0.1.300 on DESK-ONE",
        ),
        (
            ("POST", "/api/desktop/libraries/open", {"data_dir": "D:\\Other\\data"}),
            "library_opened",
            "opened the library Other on DESK-ONE",
        ),
    ],
    ids=[
        "sharing on",
        "sharing off",
        "start with windows",
        "firewall",
        "storage",
        "update",
        "library",
    ],
)
def test_each_act_there_writes_who_and_from_which_device_into_history(
    client: TestClient,
    shell: tuple[FakeShell, str],
    named: None,
    ask: tuple[str, str, dict[str, Any] | None],
    verb: str,
    said: str,
) -> None:
    sign_in(client, "admin")
    method, path, body = ask

    answer = client.request(method, path, json=body, params={"device": "LAPTOP-TWO"})

    assert answer.status_code == 200
    ((act, line),) = [one for one in _feed(client) if one[0] == verb]
    assert act == verb
    assert line == f"You {said}, from LAPTOP-TWO"


def test_start_with_windows_off_is_written_as_its_own_act(
    client: TestClient, shell: tuple[FakeShell, str], named: None
) -> None:
    sign_in(client, "admin")
    shell[0].starting = True

    client.put("/api/desktop/start-with-windows", json={"on": False})

    ((_act, line),) = [one for one in _feed(client) if one[0] == "start_with_windows_off"]
    assert line == "You stopped Sift starting with Windows on DESK-ONE, from another computer"


def test_an_act_the_app_refuses_or_a_read_writes_nothing(
    client: TestClient, shell: tuple[FakeShell, str], named: None
) -> None:
    """Nothing changed there, so History has nothing to say: a refusal, an update with nothing
    newer, a firewall nobody approved, and every read."""
    sign_in(client, "admin")
    shell[0].refusal = "That folder is not empty. Choose an empty one, or make a new one."
    shell[0].update = {"ok": False, "reason": "none"}

    client.put("/api/desktop/sharing", json={"on": True})
    client.post("/api/desktop/storage/move", json={"folder": "E:\\Full"})
    client.post("/api/desktop/libraries/open", json={"data_dir": "D:\\Other\\data"})
    client.post("/api/desktop/update")
    client.get("/api/desktop")
    client.get("/api/desktop/firewall")
    client.get("/api/desktop/storage")
    client.get("/api/desktop/libraries")
    client.get("/api/desktop/log", params={"lines": 5})

    assert [one for one in _feed(client) if one[0] in _ACTS] == []


def test_a_firewall_nobody_approved_writes_nothing(
    client: TestClient, shell: tuple[FakeShell, str], named: None
) -> None:
    sign_in(client, "admin")
    original = shell[0].answer

    def refused(method: str, path: str, body: Any) -> tuple[int, Any]:
        if method == "POST" and path == "/firewall":
            shell[0].asked.append((method, path, body))
            return 200, {"state": "closed", "networks": ["Private"], "scope": None}
        return original(method, path, body)

    shell[0].answer = refused  # type: ignore[method-assign]

    assert client.post("/api/desktop/firewall", json={"scope": "any"}).json()["state"] == "closed"
    assert [one for one in _feed(client) if one[0] == "firewall_opened"] == []


def test_a_library_the_app_does_not_list_by_name_is_said_as_another(
    client: TestClient, shell: tuple[FakeShell, str], named: None
) -> None:
    sign_in(client, "admin")

    client.post("/api/desktop/libraries/open", json={"data_dir": "E:\\Unlisted\\data"})

    ((_act, line),) = [one for one in _feed(client) if one[0] == "library_opened"]
    assert line == "You opened another library on DESK-ONE, from another computer"


def test_a_start_with_windows_that_windows_did_not_take_writes_nothing(
    client: TestClient, shell: tuple[FakeShell, str], named: None
) -> None:
    sign_in(client, "admin")
    original = shell[0].answer

    def ignored(method: str, path: str, body: Any) -> tuple[int, Any]:
        if method == "PUT" and path == "/start-with-windows":
            shell[0].asked.append((method, path, body))
            return 200, {"startsWithWindows": shell[0].starting}
        return original(method, path, body)

    shell[0].answer = ignored  # type: ignore[method-assign]

    assert client.put("/api/desktop/start-with-windows", json={"on": True}).status_code == 200
    assert [one for one in _feed(client) if one[0] in _ACTS] == []


#: The verbs an act there writes, for the tests that look for their absence.
_ACTS = {
    "sharing_turned_on",
    "sharing_turned_off",
    "start_with_windows_on",
    "start_with_windows_off",
    "firewall_opened",
    "storage_moved",
    "update_started",
    "library_opened",
    "restarted",
}
