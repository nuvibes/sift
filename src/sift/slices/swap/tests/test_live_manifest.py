# SPDX-License-Identifier: AGPL-3.0-or-later
"""The manifest of a running swap, on both sides, and the receipt each side keeps at its end.

Real sessions against a hand-written other side (`test_session`'s rigs): the screen's rows fill in
as the offer is read, the answer is given, the files cross and land, and what was kept at the end
is read back as the receipt."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sift.slices.swap import receipt
from sift.slices.swap.ingest import DIGEST, STRIP, Landed, Refused
from sift.slices.swap.models import Diff, Offer, OfferedFile, OfferedPerson, OfferedSite
from sift.slices.swap.receiving import _refused_why
from sift.slices.swap.router import _session_view
from sift.slices.swap.session import SwapRefused, Taken
from sift.slices.swap.tests.test_session import (
    _guest_rig,
    _host_rig,
    _needs_psk,
    _send_whole,
    _until,
)

FILES = {"a": b"the first small file", "b": b"the second small file", "c": b"a third"}


def _filed_offer() -> Offer:
    """The rig's three files, one under a person, one under a Site, one loose."""
    return Offer(
        people=[OfferedPerson(name="Bryn Calloway")],
        sites=[OfferedSite(name="Northlight Media")],
        files=[
            OfferedFile(
                key="a",
                size=len(FILES["a"]),
                identity="id-a",
                kind="video",
                title="clip a",
                people=[0],
            ),
            OfferedFile(
                key="b",
                size=len(FILES["b"]),
                identity="id-b",
                kind="video",
                title="clip b",
                site="Northlight Media",
                username="northlight",
            ),
            OfferedFile(key="c", size=len(FILES["c"]), identity="id-c", kind="image", title="c"),
        ],
    )


def test_a_refused_landing_is_said_in_three_words() -> None:
    assert _refused_why(Refused(DIGEST)) == "bytes"
    assert _refused_why(Refused(STRIP)) == "strip"
    assert _refused_why(RuntimeError("anything else")) == "landing"


@pytest.mark.integration
@_needs_psk
async def test_the_guest_manifest_fills_in_and_its_receipt_is_kept(tmp_path: Any) -> None:
    async def landing(session: Any, received: Any, *, ctx: Any) -> object:
        if received.key == "c":
            raise Refused(STRIP)
        if received.key == "a":
            return Landed(
                asset_id="x-a",
                location_id="l-a",
                was_duplicate=False,
                folder_id="f",
                named=(("Bryn Calloway", "p-bryn", True), ("Cassia Lynn", "p-cassia", False)),
                faces_by_person=(("p-bryn", 2),),
                site_id="s-nl",
            )
        return Landed(
            asset_id="x-b", location_id="l-b", was_duplicate=False, folder_id="f", site_id="s-nl"
        )

    async with _guest_rig(tmp_path, FILES, land=landing) as rig:
        rig.offer = _filed_offer()
        control = await rig.connected()
        sessions = rig.sessions
        # Before the offer there is nothing to draw, nor a page to read.
        extras = sessions.extras(rig.id)
        assert extras is not None and extras.manifest is None and extras.streams is None
        with pytest.raises(SwapRefused):
            sessions.group_files(rig.id, "loose", offset=0, limit=5)
        with pytest.raises(SwapRefused):
            sessions.with_groups(rig.id, Taken(), ["loose"])
        assert sessions.with_groups(rig.id, Taken(), []) == Taken()

        await rig.offer_to(control)
        extras = sessions.extras(rig.id)
        assert extras is not None and extras.manifest is not None
        rows = {row.key: row for row in extras.manifest}
        assert set(rows) == {"person:0", "site:0", "loose"}
        assert rows["person:0"].to_take == 1 and rows["person:0"].state == "offered"
        page, total = sessions.group_files(rig.id, "site:0", offset=0, limit=5)
        assert total == 1 and page[0].title == "clip b"
        skipping = sessions.with_groups(rig.id, Taken(), ["loose"])
        assert skipping.unticked == frozenset({"c"})
        with pytest.raises(ValueError, match="group"):
            sessions.with_groups(rig.id, Taken(), ["photo_set:1"])

        await rig.take(control)
        stream, _ = await rig.stream()
        await _send_whole(stream, 0, FILES["a"])
        await _until(lambda: 0 in rig_live(rig).done_files)
        moving = sessions.extras(rig.id)
        assert moving is not None and moving.manifest is not None
        assert moving.moving_since is not None and moving.streams is not None
        assert moving.kept_files == 1 and moving.kept_bytes == len(FILES["a"])
        landed = {row.key: row for row in moving.manifest}
        assert landed["person:0"].state == "landed" and landed["person:0"].local_id == "p-bryn"
        assert landed["site:0"].state == "waiting"
        view = await _session_view(sessions, rig.id)
        assert view.manifest is not None and view.kept_files == 1
        # The device's earlier swaps are read once a session and kept while it runs.
        assert sessions.said[rig.id] == (0, None)
        assert (await _session_view(sessions, rig.id)).swapped_before == 0
        await _send_whole(stream, 1, FILES["b"])
        await _send_whole(stream, 2, FILES["c"])
        await _until(lambda: len(rig_live(rig).refusals) == 1)
        refused = sessions.extras(rig.id)
        assert refused is not None
        assert [(one.title, one.reason) for one in refused.refused] == [("c", "strip")]
        assert refused.refused_count == 1
        await rig.ended()

        kept = await receipt.read(sessions.store.database, rig.id)
        assert kept is not None
        groups = {one["kind"]: one for one in kept.body["groups"]}
        assert groups["person"]["filed"] == "created" and groups["person"]["faces"] == 2
        assert groups["site"]["id"] == "s-nl" and groups["site"]["usernames"] == 1
        assert kept.body["refused"] == 1


def rig_live(rig: Any) -> Any:
    live = rig.sessions.live(rig.id)
    assert live is not None
    return live


@pytest.mark.integration
@_needs_psk
async def test_a_skipped_group_is_kept_on_the_guests_receipt(tmp_path: Any) -> None:
    async def landing(session: Any, received: Any, *, ctx: Any) -> object:
        # Filed under nobody here: no person, no Site.
        return Landed(
            asset_id=f"x-{received.key}", location_id="l", was_duplicate=False, folder_id="f"
        )

    async with _guest_rig(
        tmp_path,
        FILES,
        land=landing,
        answer=lambda taken: Diff(wanted=[key for key in "ab" if key not in taken.unticked]),
    ) as rig:
        rig.offer = _filed_offer()
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control, rig.sessions.with_groups(rig.id, Taken(), ["loose"]))
        stream, _ = await rig.stream()
        await _send_whole(stream, 0, FILES["a"])
        await _send_whole(stream, 1, FILES["b"])
        await rig.ended()
        kept = await receipt.read(rig.sessions.store.database, rig.id)
        assert kept is not None
        assert kept.body["skipped"] == [{"kind": "loose", "id": None, "name": None, "files": 1}]


@pytest.mark.integration
@_needs_psk
async def test_the_host_manifest_reads_taking_and_sent_and_its_receipt_says_sent(
    tmp_path: Any,
) -> None:
    async with _host_rig(tmp_path, {"a": b"one small file", "b": b"another"}) as rig:
        sessions = rig.sessions
        await rig.join()
        extras = sessions.extras(rig.id)
        assert extras is not None and extras.manifest is None
        assert extras.token_expires == rig.started.token.expires
        await rig.offer()
        offered = sessions.extras(rig.id)
        assert offered is not None and offered.manifest is not None
        assert {row.state for row in offered.manifest} == {"offered"}
        page, total = sessions.group_files(rig.id, "loose", offset=1, limit=5)
        assert total == 2 and page[0].key == "b" and page[0].held is None
        with pytest.raises(SwapRefused):
            sessions.try_now(rig.id)
        assert rig.control is not None
        await rig.control.send({"diff": Diff(wanted=["a"]).model_dump(mode="json")})
        await _until(lambda: rig.live.transferring)
        taking = sessions.extras(rig.id)
        assert taking is not None and taking.manifest is not None
        assert (
            taking.manifest[0].state in ("taking", "crossing") and taking.manifest[0].to_take == 1
        )
        # A file open for sending, on one stream: what the screen names as crossing now.
        live = rig.live
        live.outgoing[0] = object()  # type: ignore[assignment]
        carrying = asyncio.ensure_future(asyncio.sleep(30))
        live.stream_tasks.add(carrying)
        crossing = sessions.extras(rig.id)
        live.outgoing.clear()
        live.stream_tasks.discard(carrying)
        carrying.cancel()
        assert crossing is not None and crossing.now is not None
        assert (crossing.now.title, crossing.now.group) == ("clip a", "loose")
        assert crossing.streams == 1
        await sessions.end(rig.id)
        kept = await receipt.read(sessions.store.database, rig.id)
        assert kept is not None
        assert kept.body["groups"] == []
        assert kept.body["skipped"] == []


@pytest.mark.integration
@_needs_psk
async def test_try_now_dials_a_guest_cut_off_and_nothing_else(tmp_path: Any) -> None:
    async with _guest_rig(tmp_path, FILES) as rig:
        await rig.connected()
        with pytest.raises(SwapRefused):
            rig.sessions.try_now(rig.id)
        live = rig_live(rig)
        live.cut_at, live.retry_at = 5, 20
        rig.sessions.try_now(rig.id)
        assert live.nudged.is_set()
        extras = rig.sessions.extras(rig.id)
        assert extras is not None and extras.retry_at == 20
        live.cut_at = None
        await asyncio.sleep(0)
    with pytest.raises(SwapRefused):
        rig.sessions.try_now("no such swap")
    with pytest.raises(SwapRefused):
        rig.sessions.group_files("no such swap", "loose", offset=0, limit=5)
