# SPDX-License-Identifier: AGPL-3.0-or-later
"""A swap that sends and receives, on loopback: a real host and a real guest, each offering, each
answering the other's offer, each landing what it took, at the same time.

Every rule of a swap one way, held in the direction from the guest to the host as well: a file
with a place arrives stripped, Do not swap keeps a file out of the guest's offer, a side asks only
for what it does not already hold, each arrival names the device it came from, a cut part-way is
joined again and both directions finish, and End ends both sides. And the shape both devices must
know: an older guest is turned away in words before anything is named, and a host's hello in a swap
one way carries only the fields it always has.

Nothing leaves this machine: the session tests' own fakes (a host "tunnel" nobody dials and a
CONNECT proxy on loopback), as `test_session.py` uses them.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel import places
from sift.kernel.access import Repository
from sift.kernel.access import sentences as say
from sift.kernel.vocabulary import VIA_SWAP
from sift.slices.swap import guest as guest_side
from sift.slices.swap import session as swap
from sift.slices.swap import transfer
from sift.slices.swap.ingest import LandingSession
from sift.slices.swap.models import Chosen, Diff, Offer, OfferedFile, OfferScreen
from sift.slices.swap.offer import offer_for
from sift.slices.swap.router import _directions
from sift.slices.swap.session import LiveFacts, SwapRefused, SwapSessions, Taken
from sift.slices.swap.store import SessionRow, SessionStore, count_landed_on
from sift.slices.swap.tests.test_offer import Library, _NoFilters, library  # noqa: F401
from sift.slices.swap.tests.test_sending import _SETTINGS, _picture
from sift.slices.swap.tests.test_session import (
    _MASTER,
    _Context,
    _database,
    _device,
    _Egress,
    _host_rig,
    _Hoster,
    _needs_psk,
    _Proxy,
    _sessions,
    _until,
)
from sift.slices.swap.transfer import CHUNK_SIZE, Prepared

pytestmark = pytest.mark.integration

#: Who pressed on each side. The host's is the row the session's start goes on the ledger under.
HOST_VIEWER: Any = SimpleNamespace(id="viewer")
GUEST_VIEWER: Any = SimpleNamespace(id="admin")


async def _as_it_is(source: Path, workdir: Path) -> Prepared:
    """A copy, unstripped: the strip is proven in `test_transfer.py`, so the bytes are known."""

    def run() -> Prepared:
        workdir.mkdir(parents=True, exist_ok=True)
        target = workdir / (source.name + ".strip")
        shutil.copyfile(source, target)
        return Prepared(target, target.stat().st_size, transfer.digest_file(target))

    return await asyncio.to_thread(run)


def _offer_of(files: dict[str, Path]) -> Offer:
    return Offer(
        files=[
            OfferedFile(
                key=key, size=path.stat().st_size, identity=f"id-{key}", kind="image", title=key
            )
            for key, path in files.items()
        ]
    )


@dataclass
class _Side:
    """One device: its sessions, what it landed, and the landing handed each file."""

    sessions: SwapSessions
    landed: dict[str, bytes] = field(default_factory=dict)
    landings: list[LandingSession] = field(default_factory=list)
    offers_made: list[tuple[Any, list[Chosen], str | None]] = field(default_factory=list)
    id: str = ""
    task: asyncio.Task[None] | None = None

    def facts(self) -> LiveFacts:
        return self.sessions.facts(self.id) or LiveFacts(None, None, 0, 0, None, None, 0)

    async def row(self) -> SessionRow:
        row = await self.sessions.row(self.id)
        assert row is not None
        return row


@dataclass
class _Rig:
    host: _Side
    guest: _Side
    proxy: _Proxy
    host_device: str = ""

    async def connected(self) -> str:
        """Both sides past the hello, showing one code."""
        await _until(lambda: self.host.facts().code and self.guest.facts().code)
        assert self.host.facts().code == self.guest.facts().code
        return str(self.host.facts().code)

    async def both_match(self, chosen: list[Chosen]) -> None:
        """They match on both sides, the guest's with what it sends; and both offer screens."""
        await self.host.sessions.answer_code(self.host.id, True)
        await self.guest.sessions.answer_code(
            self.guest.id, True, viewer=GUEST_VIEWER, chosen=chosen
        )
        await _until(lambda: self.host.facts().screen and self.guest.facts().screen)

    async def finished(self) -> None:
        assert self.host.task is not None and self.guest.task is not None
        await asyncio.wait_for(asyncio.gather(self.host.task, self.guest.task), 30)


def _wants(holds: set[str]) -> Callable[[Offer], Any]:
    """An offer screen's reading: every file offered but those this side already holds (the
    dedup rule, which `test_diff.py` proves) and those unticked."""

    async def assess(arrived: Offer) -> tuple[OfferScreen, Callable[[Taken], Diff]]:
        def answer(taken: Taken) -> Diff:
            keys = [
                one.key
                for one in arrived.files
                if one.key not in holds and one.key not in taken.unticked
            ]
            return Diff(wanted=keys, people={})

        return OfferScreen(layout="rows"), answer

    return assess


@asynccontextmanager
async def _both_ways(
    tmp_path: Path,
    host_files: dict[str, Path],
    guest_files: dict[str, Path],
    *,
    host_holds: set[str] | None = None,
    guest_offer: Callable[..., Any] | None = None,
    guest_path: Callable[..., Any] | None = None,
    guest_prepare: Callable[..., Any] = _as_it_is,
    proxy_of: Callable[[_Hoster], _Proxy] = _Proxy,
    **kwargs: Any,
) -> AsyncIterator[_Rig]:
    """A host that pressed Start under Send and receive, and a guest that joined it, both running.
    Each lands what it takes as the real landing does: counted on its own row in a transaction."""
    host_db = await _database(tmp_path / "host.sqlite3")
    guest_db = await _database(tmp_path / "guest.sqlite3")
    hoster = _Hoster()
    proxy = proxy_of(hoster)
    await proxy.start()
    sides: list[_Side] = []

    def lands_on(index: int) -> Callable[..., Any]:
        async def land(session: LandingSession, received: Any, *, ctx: Any) -> None:
            side = sides[index]
            assert received.key in session.wanted
            side.landings.append(session)
            side.landed[received.key] = await asyncio.to_thread(received.staged.read_bytes)
            async with side.sessions.store.database.write() as connection:
                await count_landed_on(connection, session.id)

        return land

    def offers(files: dict[str, Path], index: int) -> Callable[..., Any]:
        async def make_offer(
            viewer: Any, chosen: Any, share_boxes: bool, model: str | None = None
        ) -> Offer:
            sides[index].offers_made.append((viewer, list(chosen), model))
            return _offer_of(files)

        return make_offer

    def paths(files: dict[str, Path]) -> Callable[..., Any]:
        async def path_of(_viewer: object, key: str) -> Path | None:
            return files.get(key)

        return path_of

    async def face_model() -> str:
        return "model-a"

    host = _Side(
        _sessions(
            host_db,
            tmp_path / "h",
            hoster=hoster,
            make_offer=offers(host_files, 0),
            path_of=paths(host_files),
            assess=_wants(host_holds or set()),
            land=lands_on(0),
            face_model=face_model,
            **kwargs,
        )
    )
    guest = _Side(
        _sessions(
            guest_db,
            tmp_path / "g",
            egress=_Egress(proxy.url),
            make_offer=guest_offer or offers(guest_files, 1),
            path_of=guest_path or paths(guest_files),
            prepare=guest_prepare,
            assess=_wants(set()),
            land=lands_on(1),
            **kwargs,
        )
    )
    sides.extend([host, guest])
    rig = _Rig(host, guest, proxy)
    try:
        started = await host.sessions.start(
            viewer=HOST_VIEWER,
            chosen=[Chosen(kind="person", id="host-person")],
            tunnel_id="tunnel-host",
            share_boxes=True,
            master_key=_MASTER,
            two_way=True,
            dest_folder_id="host-folder",
        )
        rig.host_device = started.token.host_device
        host.id = started.session_id
        guest.id = await guest.sessions.join(
            viewer_id="admin",
            token_text=started.token.text,
            dest_folder_id="guest-folder",
            master_key=_MASTER,
        )
        host.task = asyncio.create_task(host.sessions.run(_Context(host.id, tmp_path / "hw")))  # type: ignore[arg-type]
        guest.task = asyncio.create_task(guest.sessions.run(_Context(guest.id, tmp_path / "gw")))  # type: ignore[arg-type]
        yield rig
    finally:
        for side in sides:
            if side.id and side.sessions.live(side.id) is not None:
                await side.sessions.end(side.id)
            if side.task is not None:
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(side.task, 5)
        if proxy.server is not None:
            proxy.server.close()
        await host_db.close()
        await guest_db.close()


def _arrival_line(landing: LandingSession) -> str:
    """The History line the landing writes for a file that arrived in this session."""
    payload = {"device": landing.peer_device, "session": landing.short_id}
    return say.text_of(say.feed_line("added", by="Sift", payload=payload, task=VIA_SWAP).pieces)


# --- a whole swap both ways --------------------------------------------------------------------


@_needs_psk
async def test_both_ways_each_side_lands_what_it_took_and_no_place_leaves_either_way(
    tmp_path: Path,
) -> None:
    """The host sends two files and takes two of the guest's three: the third it already holds
    (the dedup rule), the guest's photograph with a place in it arrives stripped, and the one
    without arrives byte for byte. Each arrival names the device it came from, each row counts both
    directions, and both end as done."""
    mine, theirs = tmp_path / "host-files", tmp_path / "guest-files"
    mine.mkdir()
    theirs.mkdir()
    first, second = os.urandom(CHUNK_SIZE + 4321), os.urandom(5000)
    (mine / "h1.bin").write_bytes(first)
    (mine / "h2.bin").write_bytes(second)
    host_files = {"h1": mine / "h1.bin", "h2": mine / "h2.bin"}
    guest_files = {
        "g-plain": _picture(theirs / "plain.jpg", place=False),
        "g-placed": _picture(theirs / "trip.jpg", place=True),
        "g-held": _picture(theirs / "held.jpg", place=False),
    }
    async with _both_ways(
        tmp_path,
        host_files,
        guest_files,
        host_holds={"g-held"},
        guest_prepare=partial(transfer.prepare, settings=_SETTINGS),
    ) as rig:
        await rig.connected()
        assert rig.host.facts().two_way and rig.guest.facts().two_way
        assert (await rig.guest.row()).two_way, "the host's hello said so, and the row says it"
        # The guest's They match says what it sends: with nothing chosen it is refused in words.
        with pytest.raises(SwapRefused, match="Choose what to send them first"):
            await rig.guest.sessions.answer_code(rig.guest.id, True, viewer=GUEST_VIEWER)
        await rig.both_match([Chosen(kind="person", id="guest-person")])
        # Each offer comes from its own side's make_offer, the guest's as whoever pressed They
        # match there, knowing the host's face model.
        assert rig.guest.offers_made == [
            (GUEST_VIEWER, [Chosen(kind="person", id="guest-person")], "model-a")
        ]
        # What taking the guest's offer would bring on the host: not the file it already holds.
        plain, placed = (guest_files[key].stat().st_size for key in ("g-plain", "g-placed"))
        assert rig.host.sessions.weigh_answer(rig.host.id, Taken()) == (2, plain + placed)
        await rig.host.sessions.take(rig.host.id, Taken())
        await rig.guest.sessions.take(rig.guest.id, Taken())
        await rig.finished()

        assert rig.guest.landed == {"h1": first, "h2": second}
        assert set(rig.host.landed) == {"g-plain", "g-placed"}, "never the file already held"
        assert rig.host.landed["g-plain"] == guest_files["g-plain"].read_bytes(), "as it is"
        arrived = tmp_path / "arrived.jpg"
        arrived.write_bytes(rig.host.landed["g-placed"])
        assert places.places_in(arrived) == 0, "NO LOCATION LEAVES, the guest's side too"

        host_row, guest_row = await rig.host.row(), await rig.guest.row()
        guest_device = host_row.peer_device
        assert guest_device is not None and guest_device != rig.host_device
        assert {one.peer_device for one in rig.guest.landings} == {rig.host_device}
        assert {one.peer_device for one in rig.host.landings} == {guest_device}
        assert {one.dest_folder_id for one in rig.host.landings} == {"host-folder"}
        assert _arrival_line(rig.host.landings[0]).endswith(
            f"to the library by swap from device {guest_device}"
        )

        for row in (host_row, guest_row):
            assert (row.state, row.end_reason, row.two_way) == ("done", "done", True)
        assert (host_row.offered_files, host_row.wanted_files, host_row.sent_files) == (2, 2, 2)
        assert (host_row.back_offered, host_row.back_wanted, host_row.back_files) == (3, 2, 2)
        assert (guest_row.offered_files, guest_row.wanted_files, guest_row.sent_files) == (2, 2, 2)
        assert (guest_row.back_offered, guest_row.back_wanted, guest_row.back_files) == (3, 2, 2)
        assert host_row.sent_bytes == guest_row.sent_bytes == len(first) + len(second)
        assert (
            host_row.back_bytes == guest_row.back_bytes == plain + len(rig.host.landed["g-placed"])
        )
        assert guest_row.dest_folder_id == "guest-folder"
        assert host_row.dest_folder_id == "host-folder"


@_needs_psk
async def test_both_ways_do_not_swap_and_hidden_hold_on_the_guests_offer(
    tmp_path: Path,
    library: Library,  # noqa: F811
    access: Repository,
) -> None:
    """The guest's offer is read as the host's is (`offer_for`, the vault shut): a file marked
    Do not swap, a file in Hidden and a file kept local are never offered, and only the file
    beside them crosses, so an empty answer is not the proof."""
    ids = library.ids
    await library.db.execute("UPDATE assets SET keep_from_swaps = 1 WHERE id = ?", (ids["p2"],))
    files = tmp_path / "guest-library"
    files.mkdir()
    (files / "p1.mp4").write_bytes(os.urandom(3000))
    on_disk = {ids["p1"]: files / "p1.mp4"}

    async def guest_offer(
        viewer: Any, chosen: Any, share_boxes: bool, model: str | None = None
    ) -> Offer:
        assert viewer is GUEST_VIEWER
        return await offer_for(
            access, library.db, _NoFilters(), library.admin, chosen, share_boxes=share_boxes
        )

    async def guest_path(_viewer: object, key: str) -> Path | None:
        return on_disk.get(key)

    mine = tmp_path / "host-files"
    mine.mkdir()
    (mine / "h1.bin").write_bytes(b"host file")
    async with _both_ways(
        tmp_path,
        {"h1": mine / "h1.bin"},
        {},
        guest_offer=guest_offer,
        guest_path=guest_path,
    ) as rig:
        await rig.connected()
        await rig.both_match([Chosen(kind="person", id=ids["person"])])
        guest_live = rig.guest.sessions.live(rig.guest.id)
        assert guest_live is not None
        sending = guest_live.sending_half()
        assert sending is not None and sending.offer is not None
        offered = {one.key for one in sending.offer.files}
        assert offered == {ids["p1"]}, "Do not swap, Hidden and kept local stay home"
        await rig.host.sessions.take(rig.host.id, Taken())
        await rig.guest.sessions.take(rig.guest.id, Taken())
        await rig.finished()
        assert set(rig.host.landed) == {ids["p1"]}
        assert rig.guest.landed == {"h1": b"host file"}


# --- cut off part-way, and joined again ------------------------------------------------------------


class _CuttingProxy(_Proxy):
    """The guest's tunnel, on loopback, able to drop every connection it carries in one go: a cut,
    the way a tunnel makes one."""

    def __init__(self, hoster: _Hoster) -> None:
        super().__init__(hoster)
        self.writers: list[asyncio.StreamWriter] = []

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        await super()._handle(reader, writer)

    def cut(self) -> None:
        for writer in self.writers:
            with suppress(Exception):
                writer.transport.abort()
        self.writers.clear()


@_needs_psk
async def test_both_ways_cut_part_way_is_joined_again_and_both_directions_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every connection drops while files go both ways: neither side ends, the guest dials again,
    each side says again what the other may not have heard, and both directions carry on from the
    chunks already verified to the end."""
    mine, theirs = tmp_path / "host-files", tmp_path / "guest-files"
    mine.mkdir()
    theirs.mkdir()
    host_data = {f"h{n}": os.urandom(CHUNK_SIZE * 2 + 99) for n in range(2)}
    guest_data = {f"g{n}": os.urandom(CHUNK_SIZE * 2 + 77) for n in range(2)}
    for key, data in host_data.items():
        (mine / key).write_bytes(data)
    for key, data in guest_data.items():
        (theirs / key).write_bytes(data)

    # A chunk is known by a short digest of its bytes, the same on both sides, so what a receiver
    # wrote down and what a sender sent again can be compared.
    sent: dict[str, int] = {"cuts": 0}
    cutting: list[_CuttingProxy] = []
    written: dict[tuple[str, str, int], str] = {}
    verified: set[str] = set()
    sent_after_cut: list[str] = []
    original = swap.Conn.send_chunk
    real_write = transfer.write_chunk

    async def counting(self: swap.Conn, file_index: int, chunk_index: int, data: bytes) -> None:
        if sent["cuts"]:
            sent_after_cut.append(transfer.chunk_digest(data).hex()[:16])
        await original(self, file_index, chunk_index, data)

    def writing(path: Path, index: int, data: bytes, chunk_size: int = CHUNK_SIZE) -> None:
        real_write(path, index, data, chunk_size)
        written[(path.parent.name, path.name, index)] = transfer.chunk_digest(data).hex()[:16]

    monkeypatch.setattr(swap.Conn, "send_chunk", counting)
    monkeypatch.setattr(transfer, "write_chunk", writing)
    async with _both_ways(
        tmp_path,
        {key: mine / key for key in host_data},
        {key: theirs / key for key in guest_data},
        proxy_of=_CuttingProxy,
        retry_seconds=0.2,
    ) as rig:
        assert isinstance(rig.proxy, _CuttingProxy)
        cutting.append(rig.proxy)
        rejoined: list[str] = []
        for side, name in ((rig.host, "host"), (rig.guest, "guest")):
            store = side.sessions.store
            real = store.rejoined

            async def counted(session_id: str, real: Any = real, name: str = name) -> bool:
                rejoined.append(name)
                return bool(await real(session_id))

            monkeypatch.setattr(store, "rejoined", counted)
            real_mark = store.mark_done

            # The cut comes once the receivers have written two chunks down as verified, so what
            # carries on afterwards is measured against a known point, however far the senders had
            # run ahead of them.
            async def marked(
                session_id: str, file_key: str, chunk: int, now: int, real: Any = real_mark
            ) -> None:
                await real(session_id, file_key, chunk, now)
                if sent["cuts"]:
                    return
                staged = transfer.staged_path(Path(), session_id, file_key)
                verified.add(written[(session_id, staged.name, chunk)])
                if len(verified) == 2:
                    sent["cuts"] += 1
                    cutting[0].cut()

            monkeypatch.setattr(store, "mark_done", marked)
        await rig.connected()
        await rig.both_match([Chosen(kind="person", id="guest-person")])
        await rig.host.sessions.take(rig.host.id, Taken())
        await rig.guest.sessions.take(rig.guest.id, Taken())
        await rig.finished()

        assert sent["cuts"] == 1
        assert sorted(rejoined) == ["guest", "host"], "both sides joined the same session again"
        assert rig.guest.landed == host_data
        assert rig.host.landed == guest_data
        for row in (await rig.host.row(), await rig.guest.row()):
            assert (row.state, row.end_reason, row.cut_off_at) == ("done", "done", None)
            assert (row.sent_files, row.back_files) == (2, 2)
        assert len(verified) == 2
        again = sorted(verified.intersection(sent_after_cut))
        assert not again, f"carried on from what is verified, not sent again: {again}"


# --- End, and the shape both devices must know ---------------------------------------------------


@_needs_psk
async def test_both_ways_end_on_either_side_ends_both(tmp_path: Path) -> None:
    mine = tmp_path / "host-files"
    mine.mkdir()
    (mine / "h1.bin").write_bytes(b"x" * 10)
    async with _both_ways(tmp_path, {"h1": mine / "h1.bin"}, {"g1": mine / "h1.bin"}) as rig:
        await rig.connected()
        await rig.both_match([Chosen(kind="person", id="guest-person")])
        await rig.guest.sessions.end(rig.guest.id)
        await rig.finished()
        host_row, guest_row = await rig.host.row(), await rig.guest.row()
        assert (guest_row.state, guest_row.end_reason) == ("ended", swap.ENDED_BY_YOU)
        assert (host_row.state, host_row.end_reason) == ("ended", swap.ENDED_BY_THEM)


@_needs_psk
async def test_both_ways_an_older_guest_is_turned_away_before_anything_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guest whose hello does not say it can send back (a Sift from before) is refused at the
    hello: the host ends as `older`, which its screen says in words, and the older guest reads the
    refusal as it reads any (a token already used) and ends too. No offer is made on either side,
    and no code is ever shown."""
    real_hello = swap.hello

    def older(device: Any, role: str, secret: bytes, nonce: bytes, **extra: Any) -> dict[str, Any]:
        if role == "guest":
            extra.pop("both", None)
        return real_hello(device, role, secret, nonce, **extra)

    # The guest reads `hello` from its own module, so the older guest is made there.
    monkeypatch.setattr(guest_side, "hello", older)
    async with _both_ways(tmp_path, {}, {}) as rig:
        await rig.finished()
        host_row, guest_row = await rig.host.row(), await rig.guest.row()
        assert (host_row.state, host_row.end_reason) == ("ended", swap.OLDER)
        assert (guest_row.state, guest_row.end_reason) == ("ended", swap.USED)
        assert rig.host.offers_made == [] and rig.guest.offers_made == []
        assert host_row.peer_device is None, "never connected: no code is shown"
    assert say.SWAP_ENDED[swap.OLDER].endswith("their Sift can't send files back")


@_needs_psk
async def test_both_ways_a_hosts_hello_in_a_swap_one_way_keeps_its_fields(tmp_path: Path) -> None:
    """A swap that only sends says nothing new to the guest: its hello carries the fields it
    always did, so an older guest swaps with a newer host as before."""
    async with _host_rig(tmp_path) as rig:
        conn = await rig.dial()
        secret = rig.started.token.secret
        await conn.send(swap.hello(_device(), "guest", secret, os.urandom(32), both=1))
        answer = await conn.read(5)
        assert isinstance(answer, dict)
        assert set(answer) == {"v", "device", "nonce", "key", "sig", "session"}
        live = rig.live
        assert live.other_way is None and live.receiving_half() is None


@_needs_psk
async def test_both_ways_start_names_a_folder_for_what_arrives(tmp_path: Path) -> None:
    database = await _database(tmp_path / "host.sqlite3")
    try:
        sessions = _sessions(database, tmp_path)
        with pytest.raises(SwapRefused, match="Choose a folder") as refused:
            await sessions.start(
                viewer=HOST_VIEWER,
                chosen=[Chosen(kind="person", id="p")],
                tunnel_id="tunnel-host",
                share_boxes=True,
                master_key=_MASTER,
                two_way=True,
            )
        assert refused.value.field == "dest_folder_id"
    finally:
        await database.close()


# --- the screen's read, and the row from before ---------------------------------------------------


@pytest.mark.unit
def test_both_ways_each_side_reads_its_own_sending_and_receiving() -> None:
    """The row's first figures are the host's to the guest and its `back_` figures the guest's to
    the host: the host sends the first and receives the second, the guest the other way round."""
    row = SessionRow(
        id="01KZTWOWAYS00000000000001",
        role="host",
        state="transferring",
        tunnel_id=None,
        peer_device=None,
        token_expires=None,
        chosen=[],
        offered_files=4,
        wanted_files=3,
        sent_files=1,
        sent_bytes=100,
        rate_bps=None,
        dest_folder_id=None,
        started_at=1,
        ended_at=None,
        end_reason=None,
        two_way=True,
        back_offered=9,
        back_wanted=8,
        back_files=7,
        back_bytes=700,
    )
    facts = LiveFacts(
        "CODE42",
        None,
        300,
        150,
        800,
        None,
        0,
        two_way=True,
        back_wanted_bytes=900,
        back_moved_bytes=800,
        back_rate_bps=80,
        back_moving=True,
        moving=True,
    )
    sending, receiving = _directions(row, facts)
    assert sending is not None and receiving is not None
    assert (sending.offered_files, sending.files, sending.bytes, sending.wanted_bytes) == (
        4,
        1,
        150,
        300,
    )
    assert (receiving.offered_files, receiving.files, receiving.bytes) == (9, 7, 800)
    assert (receiving.rate_bps, receiving.moving) == (80, True)
    # The guest reads the same two directions the other way round, but for the received count,
    # which each side says only on the direction it receives (`received_files`).
    guest_sending, guest_receiving = _directions(replace(row, role="guest"), facts)
    assert guest_sending is not None and guest_receiving is not None
    assert guest_sending == receiving.model_copy(update={"received_files": None})
    assert guest_receiving == sending.model_copy(update={"received_files": facts.received_files})
    assert receiving.received_files == facts.back_received_files
    assert sending.received_files is None
    assert _directions(replace(row, two_way=False), facts) == (None, None)


async def test_both_ways_a_swap_table_from_before_gains_its_columns_and_keeps_its_rows(
    tmp_path: Path,
) -> None:
    """Version two's table, as somebody who upgrades has it: the columns are added where they
    are missing, nothing is rebuilt, and a row from before reads as a swap one way."""
    from sift.slices.swap import schema

    database = await _database(tmp_path / "old.sqlite3")
    try:
        async with database.write() as connection:
            await connection.execute("DROP TABLE swap_manifests")
            await connection.execute("DROP TABLE swap_sessions")
            await connection.execute(schema._CREATE_SESSIONS)
            await connection.execute(schema._CREATE_MANIFESTS)
            await connection.execute(schema._ADD_CUT_OFF_AT)
            await connection.execute(
                "INSERT INTO swap_sessions (id, role, state, started_at, sent_files) VALUES"
                " ('01KZOLDSESSION00000000002', 'guest', 'done', 1, 4)"
            )
            await schema.initialize(connection, on_disk=2)
            # Run again, as a half-finished step would be: a column already there is left alone.
            await schema.initialize(connection, on_disk=2)
        row = await SessionStore(database).get("01KZOLDSESSION00000000002")
        assert row is not None and row.sent_files == 4
        assert (row.two_way, row.back_offered, row.back_files, row.back_bytes) == (False, 0, 0, 0)
    finally:
        await database.close()
