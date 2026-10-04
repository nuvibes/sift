# SPDX-License-Identifier: AGPL-3.0-or-later
"""What History says about a swap: its two ends on the feed, a file it imported, one swap's
arrivals folded apart from another's, and that no line can say where the other side was.

The words are `kernel/access/sentences.py`'s (`swap_started`, `swap_ended`, `added_by`, the fold's
tail); the fold key is `kernel/access/history_events.py`'s. They live in the kernel and read only
the ledger's payload, so the one thing this slice owns about them is the vocabulary they are keyed
by (the reasons a session ends and the two sides), which is why the table is held to it here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

import sift.slices.workbench.schema  # noqa: F401 (registers the ledger's tables)
from sift.kernel.access import Repository
from sift.kernel.access import sentences as say
from sift.kernel.access.history import history_of_asset
from sift.kernel.access.history_feed import presses_recent
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import VIA_SWAP, Subject
from sift.kernel.workbench import Workbench
from sift.slices.swap.session import REASONS
from sift.slices.swap.store import SessionStore
from sift.slices.workbench.router import ledger
from sift.testing.fixtures import Actors, World

#: The other install, as its device id is shown. Two, for the fold.
DEVICE = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"
OTHER_DEVICE = "WXYZ-2345-6734-5673-4567-ABCD-EFGH-IJKL"

FILE = say.thing("asset", "a1", "holiday.mp4")


def _feed(verb: str, payload: Mapping[str, object], **rest: object) -> str:
    by = str(rest.pop("by", "Sift"))
    return say.text_of(say.feed_line(verb, by=by, payload=payload, **rest).pieces)  # type: ignore[arg-type]


def _ended(role: str, reason: str, files: int | None = None, device: str | None = DEVICE) -> str:
    payload: dict[str, object] = {"role": role, "reason": reason}
    if files is not None:
        payload["files"] = files
    if device is not None:
        payload["device"] = device
    return _feed("swap_ended", payload, task=VIA_SWAP)


def _both(role: str, reason: str, files: int, back: int) -> str:
    """The end of a swap that sends and receives: `files` crossed to the guest, `back` to the host."""
    payload: dict[str, object] = {
        "role": role,
        "reason": reason,
        "files": files,
        "back": back,
        "device": DEVICE,
    }
    return _feed("swap_ended", payload, task=VIA_SWAP)


# --- the words ------------------------------------------------------------------------------------


def test_every_way_a_swap_ends_has_its_words() -> None:
    """Both ways: a reason the session can write and this table cannot say reads only "ended"; a
    reason here the session never writes is a sentence nothing can reach."""
    assert set(say.SWAP_ENDED) == set(REASONS)


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        (_feed("swap_started", {"role": "host"}, by="You"), "You started a swap"),
        (
            _feed("swap_started", {"role": "guest", "device": DEVICE}, by="You"),
            f"You joined a swap from device {DEVICE}",
        ),
        (_ended("guest", "done", 38), f"The swap with device {DEVICE} ended: 38 files received"),
        (_ended("host", "done", 38), f"The swap with device {DEVICE} ended: 38 files sent"),
        (_ended("host", "done", 1), f"The swap with device {DEVICE} ended: 1 file sent"),
        (_ended("guest", "done", 0), f"The swap with device {DEVICE} ended: nothing received"),
        (
            _ended("guest", "ended by you", 12),
            f"This device ended the swap with device {DEVICE}: 12 files received",
        ),
        (_ended("host", "ended by them", 12), f"Device {DEVICE} ended the swap: 12 files sent"),
        (
            _ended("guest", "lost", 12),
            f"The swap with device {DEVICE} lost the connection after 12 files",
        ),
        (
            _ended("guest", "lost", 0),
            f"The swap with device {DEVICE} lost the connection with nothing received",
        ),
        # The host's session before anybody joined has no other device to name.
        (_ended("host", "lost", None, None), "The swap lost the connection with nothing sent"),
        (
            _ended("guest", "refused", 0),
            f"The swap with device {DEVICE} ended with nothing sent: the codes didn't match",
        ),
        (
            _ended("guest", "wrong device", 0),
            f"The swap with device {DEVICE} ended with nothing sent: a different device answered",
        ),
        # Nobody joined in time, and a guest's token had been used: never "lost".
        (
            _ended("host", "expired", None, None),
            "The swap ended with nothing sent: nobody joined in time",
        ),
        (
            _ended("guest", "used", 0),
            f"The swap with device {DEVICE} ended with nothing sent: that swap had been joined already",
        ),
        (_ended("host", "a reason from a later build", 3), f"The swap with device {DEVICE} ended"),
        # A swap that sends and receives is an exchange, and says both counts, from the side whose
        # record it is.
        (
            _both("host", "done", 38, 4),
            f"The exchange with device {DEVICE} ended: 38 files sent and 4 received",
        ),
        (
            _both("guest", "done", 38, 4),
            f"The exchange with device {DEVICE} ended: 4 files sent and 38 received",
        ),
        (
            _both("host", "ended by them", 0, 1),
            f"Device {DEVICE} ended the exchange: nothing sent and 1 file received",
        ),
        (
            _both("guest", "ended by you", 0, 2),
            f"This device ended the exchange with device {DEVICE}: 2 files sent and nothing received",
        ),
        (
            _both("host", "lost", 0, 0),
            f"The exchange with device {DEVICE} lost the connection with nothing sent or received",
        ),
        (
            _both("guest", "lost", 5, 0),
            f"The exchange with device {DEVICE} lost the connection with nothing sent and 5 files received",
        ),
        (
            _feed("swap_started", {"role": "host", "two_way": True}, by="You"),
            "You started an exchange",
        ),
        (
            _both("host", "older", 0, 0),
            f"The exchange with device {DEVICE} ended with nothing sent: their Sift can't send files back",
        ),
        (
            say.text_of(say.added_by(VIA_SWAP, {"device": DEVICE, "session": "7Q4K2M9X"})),
            f"Sift added this file to the library by swap from device {DEVICE}",
        ),
        (
            _feed(
                "added",
                {"device": DEVICE, "session": "7Q4K2M9X"},
                task=VIA_SWAP,
                subjects=[("asset", FILE)],
            ),
            f"Sift added holiday.mp4 to the library by swap from device {DEVICE}",
        ),
        (
            say.text_of(
                say.feed_folded(
                    "added",
                    by="Sift",
                    task=VIA_SWAP,
                    acts=38,
                    subjects=[("asset", FILE)],
                    subject_counts={"asset": 38},
                    payload={"device": DEVICE, "session": "7Q4K2M9X"},
                ).pieces
            ),
            f"Sift added 38 files to the library by swap from device {DEVICE}",
        ),
    ],
)
def test_the_swap_lines_read_exactly(said: str, expected: str) -> None:
    assert said == expected


def test_the_device_is_said_only_on_an_arrival_by_swap() -> None:
    """The tail belongs to the swap's own arrival: a file another task added keeps its own words,
    whatever its payload happens to hold."""
    payload = {"device": DEVICE}
    assert _feed("added", payload, task="folder", subjects=[("asset", FILE)]) == (
        "Sift added holiday.mp4 to the library from a folder name"
    )
    assert _feed("added", payload, by="You", subjects=[("asset", FILE)]) == (
        "You added holiday.mp4 to the library"
    )


# --- nothing about where the other side is ---------------------------------------------------------

#: What a swap knows and must never say on History: the address the other side was reached at, the
#: port, the token, its secret, the six-character code and the session's own id. A writer that put
#: any of them in a payload by mistake must still not get it onto a line.
_NEVER = {
    "address": "203.0.113.77",
    "public_ipv4": "198.51.100.23",
    "port": "51820",
    "external_port": "40771",
    "token": "AEAQ-CAIB-AEAQ-CAIB-AEAQ-CAIB-AEAQ",
    "secret": "a-secret-value-nobody-may-log",
    "code": "K7QZPX",
    "session": "01K5ZQ7Q4K2M9X",
    "session_id": "01K5ZQ7Q4K2M9XAAAAAAAAAAAA",
}


def _every_swap_line(payload: Mapping[str, object]) -> list[str]:
    session = say.Piece(_NEVER["session_id"])
    lines = [
        say.feed_line(
            verb, by=by, task=VIA_SWAP, subjects=[("swap", session)], payload=payload
        ).pieces
        for verb in ("swap_started", "swap_ended")
        for by in ("You", "Sift")
    ]
    lines += [
        say.event_said(verb, by="You", here=say.VANTAGE_FILE, payload=payload).pieces
        for verb in ("swap_started", "swap_ended")
    ]
    lines += [
        say.feed_folded(verb, by="Sift", task=VIA_SWAP, acts=2, payload=payload).pieces
        for verb in ("swap_started", "swap_ended", "added")
    ]
    lines.append(say.added_by(VIA_SWAP, payload))
    lines.append(
        say.feed_line(
            "added", by="Sift", task=VIA_SWAP, subjects=[("asset", FILE)], payload=payload
        ).pieces
    )
    return [say.text_of(line) for line in lines]


@pytest.mark.parametrize("reason", [*REASONS, None])
@pytest.mark.parametrize("role", ["host", "guest"])
def test_no_swap_line_says_an_address_a_port_the_token_the_code_or_the_session(
    role: str, reason: str | None
) -> None:
    payload = {**_NEVER, "role": role, "device": DEVICE, "files": 3}
    if reason is not None:
        payload["reason"] = reason
    for words in _every_swap_line(payload):
        leaked = [key for key, value in _NEVER.items() if value in words]
        assert not leaked, f"{words!r} says the {leaked}"
        assert "address" not in words.lower() and "token" not in words.lower(), words
    # The known positive: the device the lines exist to name is said.
    assert any(DEVICE in words for words in _every_swap_line(payload))


# --- the fold, and the file's own page ------------------------------------------------------------


async def _arrived(database: Database, asset_id: str, device: str, session: str) -> str:
    async with database.write() as connection:
        return await record_event(
            connection,
            actor=Actor.sift(VIA_SWAP),
            verb="added",
            subject=Subject(kind="asset", id=asset_id),
            payload=json.dumps({"device": device, "session": session}),
        )


async def _swap_event(database: Database, verb: str, user_id: str, payload: object) -> str:
    async with database.write() as connection:
        return await record_event(
            connection,
            actor=Actor.user(user_id) if verb == "swap_started" else Actor.sift(VIA_SWAP),
            verb=verb,
            subject=Subject(kind="swap", id=new_id()),
            payload=json.dumps(payload),
        )


@pytest.mark.anyio
@pytest.mark.integration
async def test_one_swap_s_arrivals_fold_apart_from_another_s(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """Two swaps landing in the same minute are two lines, each naming its own device: the fold's
    key holds the session. And each end of a swap is its own line, never folded with the next."""
    for asset in (world.solo, world.twin):
        await _arrived(temp_db, asset, DEVICE, "7Q4K2M9X")
    await _arrived(temp_db, world.loose, OTHER_DEVICE, "3HV8C1RT")
    await _arrived(temp_db, world.solo, DEVICE, "7Q4K2M9X")
    for _ in range(2):
        await _swap_event(temp_db, "swap_started", actors.admin.id, {"role": "host"})

    presses, total = await presses_recent(temp_db, actors.admin)

    assert total == 4
    arrivals = sorted(one.folded for one in presses if one.event.verb == "added")
    assert arrivals == [1, 3]
    assert [one.folded for one in presses if one.event.verb == "swap_started"] == [1, 1]

    # Through the feed's own route, which hands the builders the task and the newest payload.
    page = await ledger(
        database=temp_db, bench=Workbench(), runs=Ledger(temp_db), viewer=actors.admin
    )
    said = ["".join(piece.lead + piece.text for piece in one.pieces) for one in page.items]
    assert said.count("You started a swap") == 2
    assert f"Sift added 2 files to the library by swap from device {DEVICE}" in said
    alone = [one for one in said if one.endswith(f"by swap from device {OTHER_DEVICE}")]
    assert len(alone) == 1 and alone[0].startswith("Sift added "), said


@pytest.mark.anyio
@pytest.mark.integration
async def test_a_file_that_arrived_by_swap_says_the_device_on_its_own_page(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _arrived(temp_db, world.solo, DEVICE, "7Q4K2M9X")

    thread = await history_of_asset(temp_db, access, actors.admin, world.solo)

    # The `added` event is the arrival line's, and is not drawn a second time.
    arrivals = [say.text_of(one.pieces) for one in thread if one.kind == "added"]
    assert arrivals == [f"Sift added this file to the library by swap from device {DEVICE}"]


@pytest.mark.anyio
@pytest.mark.integration
async def test_a_file_no_task_recorded_arriving_keeps_its_name_line(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    thread = await history_of_asset(temp_db, access, actors.admin, world.solo)

    arrivals = [say.text_of(one.pieces) for one in thread if one.kind == "added"]
    # An arrival says its folder, as every arrival line does.
    assert arrivals == ["Sift added this file to the library from top/mid/leaf"]


@pytest.mark.anyio
@pytest.mark.integration
async def test_an_exchange_started_says_so_in_history_and_a_swap_one_way_does_not(
    temp_db: Database, actors: Actors
) -> None:
    """The row's start writes the History line in its own transaction: an exchange's says so."""
    store = SessionStore(temp_db)
    await store.create(
        "01HEXCHANGESTARTED0000001",
        role="host",
        started_at=1,
        started_by=actors.admin.id,
        dest_folder_id="folder",
        two_way=True,
    )
    await store.create(
        "01HONEWAYSTARTED000000001", role="host", started_at=2, started_by=actors.admin.id
    )

    page = await ledger(
        database=temp_db, bench=Workbench(), runs=Ledger(temp_db), viewer=actors.admin
    )
    said = ["".join(piece.lead + piece.text for piece in one.pieces) for one in page.items]
    assert sorted(said) == ["You started a swap", "You started an exchange"]
