# SPDX-License-Identifier: AGPL-3.0-or-later
"""The receipt's numbers agree with each other, and the progress screen's rate is each direction's.

One clock: the time on the card runs from the first byte to the last, less the time cut off, and
the average is over that time. One size: the headline's is the files', the same as its rows'. The
cases are the shapes swaps have ended in: a long wait at the code, a run cut off at its end, an
exchange whose two directions moved for very different lengths, an exchange ended with files
received and not yet filed, and an Undo that met files in use or already in the library.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sift.slices.swap import receipt
from sift.slices.swap.models import ReceiptUndo
from sift.slices.swap.router import _directions, _rate
from sift.slices.swap.session_extras import LiveFacts
from sift.slices.swap.store import SessionRow, SessionStore
from sift.slices.swap.tests.test_session import VIEWER, _database

MB = 1_000_000
GB = 1_000_000_000


def _row(**changes: Any) -> SessionRow:
    base = SessionRow(
        id="01HRECEIPTNUMBERS0000001",
        role="guest",
        state="done",
        tunnel_id=None,
        peer_device="DESK-2F9A",
        token_expires=None,
        chosen=[],
        offered_files=0,
        wanted_files=0,
        sent_files=0,
        sent_bytes=0,
        rate_bps=None,
        dest_folder_id=None,
        started_at=1000,
        ended_at=None,
        end_reason="done",
        folder_id="f1",
    )
    return dataclasses.replace(base, **changes)


def _group(way: str, files: int, size: int) -> dict[str, Any]:
    return {"way": way, "kind": "person", "name": "Bryn Calloway", "files": files, "bytes": size}


def test_the_time_and_the_average_count_from_the_first_byte_and_the_wait_is_said_apart() -> None:
    # 42 files landed after three minutes at the code, then 45 seconds of moving.
    row = _row(sent_files=42, sent_bytes=543 * MB, ended_at=1000 + 180 + 240)
    body = {
        "groups": [_group("landed", 42, 543 * MB)],
        "moving_since": 1180,
        "moving_seconds": 45,
    }
    drawn = receipt.view(row, receipt.Kept(body=body), folder=None, tunnel=None)
    assert (drawn.waited, drawn.moving, drawn.seconds) == (180, 45, 420)
    assert drawn.rate == round(543 * MB / 45, 1)
    assert drawn.bytes == 543 * MB


def test_a_run_cut_off_at_its_end_says_one_size_and_averages_only_the_minutes_it_moved() -> None:
    # 199 files, 7.2 GB of them, 8.8 GB on the wire; 7 minutes moving, 12 more cut off.
    row = _row(sent_files=199, sent_bytes=8_800 * MB, ended_at=2200, cut_off_at=1480)
    body = {
        "groups": [_group("landed", 120, 4_000 * MB), _group("landed", 79, 3_200 * MB)],
        "moving_since": 1060,
        "moving_seconds": 420,
        "cut_seconds": 720,
        "cut_offs": 0,
    }
    drawn = receipt.view(row, receipt.Kept(body=body), folder=None, tunnel=None)
    assert drawn.bytes == sum(one.bytes for one in drawn.groups) == 7_200 * MB
    assert drawn.rate == round(8_800 * MB / 420, 1)
    # Ended while cut off: the store's mark counts the cut the body missed.
    assert (drawn.cut_offs, drawn.cut_seconds) == (1, 720)


def test_an_exchange_gives_each_direction_its_own_average() -> None:
    # 13 files (60 MB) landed in a minute; 971 (8.7 GB) went the other way over nine.
    row = _row(
        two_way=True,
        sent_files=13,
        sent_bytes=60 * MB,
        back_files=971,
        back_bytes=8_700 * MB,
        ended_at=1600,
    )
    body = {
        "groups": [_group("landed", 13, 60 * MB), _group("sent", 971, 8_700 * MB)],
        "moving_since": 1005,
        "moving_seconds": 60,
        "back_moving_seconds": 540,
    }
    drawn = receipt.view(row, receipt.Kept(body=body), folder=None, tunnel=None)
    assert (drawn.bytes, drawn.back_bytes) == (60 * MB, 8_700 * MB)
    assert drawn.rate == 1_000_000.0
    assert drawn.back_rate == round(8_700 * MB / 540, 1)


def test_a_host_reads_its_sent_rows_as_the_headline_and_its_landed_ones_as_the_other_way() -> None:
    row = _row(role="host", two_way=True, sent_files=2, sent_bytes=9, back_files=1, back_bytes=9)
    body = {"groups": [_group("sent", 2, 300), _group("landed", 1, 40)]}
    drawn = receipt.view(row, receipt.Kept(body=body), folder=None, tunnel=None)
    assert (drawn.bytes, drawn.back_bytes, drawn.moving, drawn.back_rate) == (300, 40, None, None)


def test_an_ended_exchange_says_the_received_files_it_did_not_keep() -> None:
    row = _row(sent_files=178, ended_at=1500)
    drawn = receipt.view(row, receipt.Kept(body={"not_kept": 976}), folder=None, tunnel=None)
    assert drawn.not_kept == 976


def _flow(back: bool, moved: int, first: int | None, last: int | None) -> SimpleNamespace:
    return SimpleNamespace(back=back, moved_bytes=moved, first_moved_at=first, last_moved_at=last)


def _live(*flows: SimpleNamespace, **changes: Any) -> SimpleNamespace:
    live = SimpleNamespace(
        owner=SimpleNamespace(now=lambda: 2000),
        cut_spans=[],
        cut_at=None,
        moving_since=1000,
        cut_offs=0,
        receiving_half=lambda: None,
        sending_half=lambda: None,
        flows=lambda: list(flows),
    )
    for key, value in changes.items():
        setattr(live, key, value)
    return live


def test_each_direction_moves_from_its_first_byte_to_its_last_less_the_cuts_inside() -> None:
    small = _flow(False, 60 * MB, 1000, 1060)
    large = _flow(True, 8_700 * MB, 1000, 1900)
    live = _live(small, large, cut_spans=[(1200, 1500)])
    body = receipt.body_of(live)  # type: ignore[arg-type]
    # The cut came after the small direction was done: only the large one loses it.
    assert (body["moving_seconds"], body["back_moving_seconds"]) == (60, 600)
    assert body["cut_seconds"] == 300


def test_a_session_ended_while_cut_off_stopped_moving_when_it_was_cut() -> None:
    flow = _flow(False, 7 * GB, None, None)
    body = receipt.body_of(_live(flow, cut_at=1420))  # type: ignore[arg-type]
    # No marks on the direction: from the first byte to the cut, and the cut to the end said.
    assert (body["moving_seconds"], body["cut_seconds"]) == (420, 580)


def test_a_direction_that_never_moved_has_no_time_and_a_blink_has_one_second() -> None:
    still = _flow(False, 0, None, None)
    blink = _flow(True, 10, 1500, 1500)
    body = receipt.body_of(_live(still, blink))  # type: ignore[arg-type]
    assert (body["moving_seconds"], body["back_moving_seconds"]) == (None, 1)
    nothing = receipt.body_of(_live(_flow(False, 10, None, None), moving_since=None))  # type: ignore[arg-type]
    assert nothing["moving_seconds"] is None


def test_the_receiver_counts_what_it_received_whole_and_never_filed() -> None:
    receiving = SimpleNamespace(
        verified={1, 2, 3, 4, 5},
        done_files={1},
        failed_files={2},
        refusals={3: "bytes"},
        offer=None,
        groups=None,
    )
    live = _live(receiving_half=lambda: receiving)
    assert receipt.body_of(live)["not_kept"] == 2  # type: ignore[arg-type]


def test_an_undo_that_met_files_in_use_counts_them_and_the_next_one_adds_up() -> None:
    row = _row(sent_files=42)
    first = receipt.undone_of(row, None, receipt.Undone(30, 1, 0, 0, in_use=11), 5)
    assert (first.files, first.in_use, first.kept, first.people) == (30, 11, 1, 1)
    again = receipt.undone_of(row, first, receipt.Undone(11, 0, 1, 0), 9)
    assert (again.files, again.in_use, again.kept, again.sites, again.at) == (41, 0, 1, 1, 9)
    # A host's landing is the other way's.
    host = _row(role="host", sent_files=0, back_files=3)
    assert receipt.undone_of(host, None, receipt.Undone(3, 0, 0, 0), 1).kept == 0


def test_a_receipt_with_files_in_use_offers_the_undo_again() -> None:
    row = _row(sent_files=42, ended_at=1100)
    held = ReceiptUndo(at=1, files=30, from_disk=True, in_use=11, kept=1)
    assert receipt.view(row, receipt.Kept(body={}, undo=held), folder=None, tunnel=None).can_undo
    whole = held.model_copy(update={"in_use": 0, "files": 41})
    assert not receipt.view(
        row, receipt.Kept(body={}, undo=whole), folder=None, tunnel=None
    ).can_undo


@pytest.mark.integration
async def test_a_second_undo_is_written_only_over_one_that_left_files_in_use(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path / "swap.sqlite3")
    session_id = "01HUNDOAGAIN" + "0000000000001"
    try:
        store = SessionStore(database)
        await store.create(session_id, role="guest", started_at=1, started_by=VIEWER)
        held = ReceiptUndo(at=5, files=30, from_disk=True, in_use=11, kept=1)
        assert await receipt.mark_undone(database, session_id, held)
        kept = await receipt.read(database, session_id)
        assert kept is not None and kept.undo == held
        whole = ReceiptUndo(at=9, files=41, from_disk=True, kept=1)
        assert await receipt.mark_undone(database, session_id, whole)
        assert not await receipt.mark_undone(database, session_id, whole)
        kept = await receipt.read(database, session_id)
        assert kept is not None and kept.undo == whole
    finally:
        await database.close()


# --- the progress screen: each direction its own rate ------------------------------------------


def _facts(**changes: Any) -> LiveFacts:
    base = LiveFacts(
        code=None,
        token=None,
        wanted_bytes=60 * MB,
        moved_bytes=60 * MB,
        rate_bps=8_024_820,
        screen=None,
        unwanted=0,
        two_way=True,
        back_wanted_bytes=26 * GB,
        back_moved_bytes=3 * GB,
        back_rate_bps=139_000_000,
        back_moving=True,
        moving=True,
        received_files=13,
        streams=0,
        back_streams=14,
    )
    return dataclasses.replace(base, **changes)


def test_a_finished_direction_is_done_and_the_session_rate_is_the_moving_ones() -> None:
    # The host's 13 files have all landed; the other way still moves.
    row = _row(two_way=True, wanted_files=13, sent_files=13, back_wanted=3261, back_files=900)
    facts = _facts()
    sending, receiving = _directions(row, facts)
    assert sending is not None and receiving is not None
    assert receiving.done and receiving.streams == 0
    assert not sending.done and sending.streams == 14
    assert _rate(row, facts, (sending, receiving)) == 139_000_000
    # Both moving: the sum.
    early = dataclasses.replace(facts, received_files=4)
    ways = _directions(row, early)
    assert _rate(row, early, ways) == 139_000_000 + 8_024_820
    # Neither moving: no rate.
    idle = dataclasses.replace(facts, moving=False, back_moving=False)
    assert _rate(row, idle, _directions(row, idle)) is None


def test_a_swap_one_way_keeps_its_own_rate() -> None:
    row = _row(rate_bps=800)
    assert _directions(row, None) == (None, None)
    assert _rate(row, None, (None, None)) == 800
    assert _rate(row, _facts(two_way=False), (None, None)) == 8_024_820
