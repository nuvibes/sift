# SPDX-License-Identifier: AGPL-3.0-or-later
"""The receiver's two figures: files received, and files filed.

A file is RECEIVED when its last piece is in and the whole file's digest matches; it is FILED when
the landing (the strip, the gate, the import, the filing) has put it in the library, one file at a
time after that. The row counts a file only once it is filed, so on its own the receiver's screen
lagged the sender's for the length of the landing queue. The session's answer says both, and at the
end the two meet.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sift.slices.swap.router import _session_view
from sift.slices.swap.session import LiveFacts
from sift.slices.swap.store import SessionRow
from sift.slices.swap.tests.test_session import _guest_rig, _needs_psk, _send_whole


@pytest.mark.integration
@_needs_psk
async def test_a_file_is_received_before_it_is_filed_and_the_two_meet_at_the_end(
    tmp_path: Any,
) -> None:
    files = {"a": b"the first small file", "b": b"the second small file"}
    let_land = asyncio.Event()
    seen_at_filing: list[int] = []

    async def slow_landing(session: Any, received: Any, *, ctx: Any) -> None:
        # The landing waits, as a slow import does, while the pieces keep arriving.
        await let_land.wait()
        facts = rig.sessions.facts(rig.id)
        assert facts is not None
        seen_at_filing.append(facts.received_files)

    async with _guest_rig(tmp_path, files, land=slow_landing) as rig:
        try:
            control = await rig.connected()
            await rig.offer_to(control)
            await rig.take(control)
            stream, _ = await rig.stream()
            await _send_whole(stream, 0, files["a"])
            await _send_whole(stream, 1, files["b"])

            # Both received, neither filed: the answer says both figures.
            view = await _session_view(rig.sessions, rig.id)
            assert (view.received_files, view.sent_files) == (2, 0)
            facts = rig.sessions.facts(rig.id)
            assert facts is not None and facts.back_received_files == 0
        finally:
            # Never left waiting: a failure above still lets the landing and the rig's end go.
            let_land.set()
        row = await rig.ended()
        assert row.state == "done"
        # The two meet: each file received was filed, and each filing saw both already received.
        # (The landing counts a filed file on the row itself, `store.count_landed_on`; this rig's
        # landing is a stand-in, so the filings are counted here.)
        assert seen_at_filing == [2, 2]


def _row(role: str, *, two_way: bool) -> SessionRow:
    return SessionRow(
        id="01HRECEIVEDANDFILED000001",
        role=role,
        state="transferring",
        tunnel_id=None,
        peer_device=None,
        token_expires=None,
        chosen=[],
        offered_files=3,
        wanted_files=3,
        sent_files=1,
        sent_bytes=0,
        rate_bps=None,
        dest_folder_id=None,
        started_at=1,
        ended_at=None,
        end_reason=None,
        two_way=two_way,
    )


class _Sessions:
    def __init__(self, row: SessionRow, facts: LiveFacts | None) -> None:
        self._row, self._facts = row, facts

    async def row(self, session_id: str) -> SessionRow:
        return self._row

    def facts(self, session_id: str) -> LiveFacts | None:
        return self._facts


_FACTS = LiveFacts(None, None, 0, 0, None, None, 0, received_files=2, back_received_files=5)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("role", "two_way", "said", "sending", "receiving"),
    [
        # One way: only the guest receives, and only the guest says a received figure.
        ("guest", False, 2, None, None),
        ("host", False, None, None, None),
        # Both ways: each side says it on the direction it receives, and never on the one it sends.
        ("guest", True, 2, None, 2),
        ("host", True, None, None, 5),
    ],
)
async def test_each_side_says_received_only_where_it_receives(
    role: str, two_way: bool, said: int | None, sending: int | None, receiving: int | None
) -> None:
    sessions = _Sessions(_row(role, two_way=two_way), _FACTS)
    view = await _session_view(sessions, "any")  # type: ignore[arg-type]
    assert view.received_files == said
    assert (view.sending and view.sending.received_files) == (sending if two_way else None)
    assert (view.receiving and view.receiving.received_files) == (receiving if two_way else None)


@pytest.mark.unit
async def test_a_session_no_longer_running_says_no_received_figure() -> None:
    view = await _session_view(_Sessions(_row("guest", two_way=True), None), "any")  # type: ignore[arg-type]
    assert view.received_files is None
    assert view.receiving is not None and view.receiving.received_files is None
