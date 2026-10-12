# SPDX-License-Identifier: AGPL-3.0-or-later
"""The manifest's and the receipt's routes over HTTP: a page of a group, a token read before Join,
Try now, the receipt and its Undo, each with its refusals. Who may call them is the authz matrix's;
here an admitted press is what is asserted (`test_router`'s world)."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel import wiring
from sift.kernel.tunnels import TUNNELS
from sift.kernel.wiring import provide
from sift.slices.swap import receipt, token
from sift.slices.swap.device import device_id_of
from sift.slices.swap.tests.test_router import world  # noqa: F401 (the routes' world)
from sift.slices.swap.tests.test_session import VIEWER

pytestmark = [pytest.mark.integration]


class _Folders:
    """A library with the swap's one folder in it, holding nothing any more."""

    async def get_folder(self, folder_id: str) -> object | None:
        if folder_id != "f-swap":
            return None
        return SimpleNamespace(
            id="f-swap", root_id="r1", rel_path="Received/Swap-00000001", name="Swap-00000001"
        )

    async def folders_in_subtree(self, root_id: str, under: str) -> list[object]:
        return []

    async def locations_in_folder(self, folder_id: str) -> list[object]:
        return []


class _Remover:
    def __init__(self) -> None:
        self.folders: list[str] = []

    async def remove(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover (no files)
        return None

    async def remove_many(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        return None

    async def remove_folder(self, folder_id: str, **_kwargs: Any) -> None:
        self.folders.append(folder_id)


class _OneTunnel:
    async def list(self) -> list[object]:
        return [SimpleNamespace(id="t1", name="Tunnel one", can_host=True, up=False, endpoint=None)]


async def test_a_token_is_read_before_join_without_a_dial(world: Any) -> None:  # noqa: F811
    client, sessions, _hoster, _route = world
    device = device_id_of(os.urandom(32))
    made = token.mint("8.8.4.4", 40000, device, sessions.now())
    read = await client.post("/api/swap/token/read", json={"token": made.text})
    assert read.status_code == 200, read.text
    assert read.json() == {
        "device_id": device,
        "device_name": None,
        "expires": made.expires,
        "swapped_before": 0,
    }
    bad = await client.post("/api/swap/token/read", json={"token": "not a token"})
    assert bad.status_code == 409 and bad.headers["Sift-Field"]


async def test_a_page_try_now_and_the_answer_refuse_what_is_not_there(world: Any) -> None:  # noqa: F811
    client, _sessions, _hoster, _route = world
    assert (await client.get("/api/swap/sessions/nope/groups/loose/files")).status_code == 404
    assert (await client.post("/api/swap/sessions/nope/retry")).status_code == 404
    started = await client.post(
        "/api/swap/start",
        json={"chosen": [{"kind": "person", "id": "p1"}], "tunnel_id": "tunnel-host"},
    )
    session_id = started.json()["session_id"]
    page = await client.get(f"/api/swap/sessions/{session_id}/groups/loose/files?limit=10")
    assert page.status_code == 409
    assert (
        await client.get(f"/api/swap/sessions/{session_id}/groups/x/files?limit=999")
    ).status_code == 422
    retry = await client.post(f"/api/swap/sessions/{session_id}/retry")
    assert retry.status_code == 409
    # A waiting host: the token's expiry, and nothing of a manifest yet.
    view = (await client.get(f"/api/swap/sessions/{session_id}")).json()
    assert view["token_expires"] == started.json()["expires"] and view["manifest"] is None
    # A group the offer does not have is said so; an answer with no offer, as before.
    taken = await client.post(
        f"/api/swap/sessions/{session_id}/take", json={"skipped_groups": ["loose"]}
    )
    assert taken.status_code == 409
    no_receipt = await client.get(f"/api/swap/receipts/{session_id}")
    assert no_receipt.status_code == 404
    await client.post(f"/api/swap/sessions/{session_id}/end")


async def test_a_group_not_in_the_offer_is_refused_by_name(
    world: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sessions, _hoster, _route = world
    started = await client.post(
        "/api/swap/start",
        json={"chosen": [{"kind": "person", "id": "p1"}], "tunnel_id": "tunnel-host"},
    )
    session_id = started.json()["session_id"]

    def no_such_group(*_args: Any) -> Any:
        raise ValueError("That group isn't in the offer.")

    def no_page(*_args: Any, **_kwargs: Any) -> Any:
        raise KeyError("site:9")

    monkeypatch.setattr(sessions, "with_groups", no_such_group)
    monkeypatch.setattr(sessions, "group_files", no_page)
    for route in ("take", "weigh"):
        answer = await client.post(
            f"/api/swap/sessions/{session_id}/{route}", json={"skipped_groups": ["site:9"]}
        )
        assert answer.status_code == 422 and "group" in answer.text
    page = await client.get(f"/api/swap/sessions/{session_id}/groups/site:9/files")
    assert page.status_code == 404
    await client.post(f"/api/swap/sessions/{session_id}/end")


async def test_the_receipt_reads_back_and_its_undo_runs_once(world: Any) -> None:  # noqa: F811
    client, sessions, _hoster, _route = world
    app = client._transport.app
    remover = _Remover()
    provide(app, wiring.LIBRARY, _Folders())
    provide(app, wiring.ACCESS, SimpleNamespace())
    provide(app, receipt.REMOVER, remover)
    provide(app, TUNNELS, _OneTunnel())
    store = sessions.store
    session_id = "01HRECEIPT" + "ROUTES00000001"
    await store.create(session_id, role="guest", started_at=1, started_by=VIEWER, tunnel_id="t1")
    await store.add_sent(session_id, files=1, sent_bytes=10)
    await store.set_rate(session_id, 8000)
    await sessions.store.database.execute(
        "UPDATE swap_sessions SET folder_id = 'f-swap' WHERE id = ?", (session_id,)
    )
    await store.end(session_id, state="done", reason="done", now=int(time.time()))

    shown = await client.get(f"/api/swap/receipts/{session_id}")
    assert shown.status_code == 200, shown.text
    said = shown.json()
    assert said["can_undo"] and said["tunnel"] == "Tunnel one"
    assert said["folder"] == {"id": "f-swap", "path": "Received/Swap-00000001", "own": True}
    # The last rate through a tunnel is the one a swap through it measured.
    tunnels = (await client.get("/api/swap/tunnels")).json()
    assert tunnels[0]["rate_bps"] == 8000

    sessions.undoing.add(session_id)
    assert (await client.post(f"/api/swap/receipts/{session_id}/undo")).status_code == 409
    sessions.undoing.discard(session_id)
    undone = await client.post(f"/api/swap/receipts/{session_id}/undo")
    assert undone.status_code == 200, undone.text
    body = undone.json()
    assert body["undo"]["files"] == 0 and body["undo"]["from_disk"] and not body["can_undo"]
    assert remover.folders == ["f-swap"]
    again = await client.post(f"/api/swap/receipts/{session_id}/undo")
    assert again.status_code == 409 and "already" in again.text

    other = "01HRECEIPTROUTES00000002"
    await store.create(other, role="guest", started_at=1, started_by=VIEWER)
    await store.end(other, state="ended", reason="ended_by_you", now=2)
    nothing = await client.post(f"/api/swap/receipts/{other}/undo")
    assert nothing.status_code == 409 and "Nothing" in nothing.text


class _Refusing(_Remover):
    async def remove_folder(self, folder_id: str, **_kwargs: Any) -> None:
        raise PermissionError("read-only")


async def test_a_folder_the_remover_refuses_stays_and_is_said() -> None:
    folder = await _Folders().get_folder("f-swap")
    left = await receipt._clear_emptied(
        _Folders(),  # type: ignore[arg-type]
        _Refusing(),
        [folder],  # type: ignore[list-item]
        actor=SimpleNamespace(),  # type: ignore[arg-type]
        short_id="00000001",
    )
    assert left == 0


async def test_a_page_and_try_now_answer_with_what_the_session_says(
    world: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sessions, _hoster, _route = world
    started = await client.post(
        "/api/swap/start",
        json={"chosen": [{"kind": "person", "id": "p1"}], "tunnel_id": "tunnel-host"},
    )
    session_id = started.json()["session_id"]
    monkeypatch.setattr(sessions, "group_files", lambda *_args, **_kwargs: ([], 3))
    monkeypatch.setattr(sessions, "try_now", lambda *_args: None)
    page = await client.get(f"/api/swap/sessions/{session_id}/groups/loose/files?offset=5")
    assert page.json() == {"files": [], "total": 3}
    assert (await client.post(f"/api/swap/sessions/{session_id}/retry")).status_code == 200
    await client.post(f"/api/swap/sessions/{session_id}/end")
