# SPDX-License-Identifier: AGPL-3.0-or-later
"""How the host sends: a file with no place in it goes as it is, a file with one never leaves
unstripped, every read goes to the front of its storage's lane, the climb judged over several
windows, and the estimate's pace held through a window that moved nothing.

The ladder is replayed against the 63 windows of one recorded swap, its rate per window as the
session wrote it, in MB/s.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from sift.kernel import lanes, places
from sift.slices.swap import session as swap
from sift.slices.swap import transfer
from sift.slices.swap.models import (
    Diff,
    Offer,
    OfferedFaces,
    OfferedFile,
    OfferedPerson,
    OfferedSong,
    OfferScreen,
)
from sift.slices.swap.session import Ladder, Taken, hello, read_hello
from sift.slices.swap.tests.test_session import (
    _MASTER,
    _Context,
    _database,
    _device,
    _Egress,
    _host_rig,
    _Hoster,
    _HostRig,
    _keep_pinging,
    _needs_psk,
    _Proxy,
    _sessions,
    _start_host,
    _until,
)
from sift.slices.swap.transfer import CHUNK_SIZE, Changed, Prepared

pytestmark = pytest.mark.integration

#: The recorded swap's rate per ten-second window, in MB/s, first to last.
WATCHED = (
    0.0, 5.5, 25.6, 22.6, 0.4, 17.6, 27.7, 23.1, 28.0, 27.7, 2.0, 8.8, 23.9, 26.2, 18.6, 23.1,
    5.0, 26.7, 17.0, 20.8, 22.0, 14.9, 13.4, 13.5, 17.3, 13.8, 14.9, 13.6, 9.9, 0.0, 21.4, 22.2,
    19.5, 24.7, 23.8, 0.0, 17.2, 16.4, 20.6, 24.7, 25.3, 24.2, 26.4, 24.1, 27.5, 29.4, 8.7, 24.7,
    19.3, 28.1, 22.4, 24.3, 23.7, 27.5, 16.5, 16.9, 0.0, 15.9, 31.1, 20.0, 17.6, 16.4, 7.8,
)  # fmt: skip


def _bits(rate: float) -> float:
    return rate * 8_000_000


def _judged_window_by_window(rates: tuple[float, ...]) -> tuple[int, int | None]:
    """A rule judged window by window, for contrast: one window under nine tenths of the best so
    far ends the climb for the session. The streams it ends on, and the window it stopped at."""
    streams, climbing, best, stopped = swap.STREAMS_START, True, None, None
    for number, rate in enumerate(rates, 1):
        if climbing and best is not None:
            if rate >= best * 0.9 and streams < swap.STREAMS_CAP:
                streams += 1
            else:
                climbing, stopped = False, number
        best = rate if best is None else max(best, rate)
    return streams, stopped


# --- the climb --------------------------------------------------------------------------------


@pytest.mark.unit
def test_the_watched_swap_replayed_climbs_past_its_first_dip_where_the_old_rule_stopped() -> None:
    old, stopped = _judged_window_by_window(WATCHED)
    assert (old, stopped) == (10, 4), "window by window: ten streams, stopped at the fourth window"

    ladder = Ladder()
    counts = [ladder.window(_bits(rate)) for rate in WATCHED]

    # Through the dip at window 4 (22.6 against 25.6) and the stall at window 5 (0.4).
    assert counts[:6] == [8, 9, 10, 11, 12, 12]
    assert counts[-1] == 18 and not ladder.climbing
    # It stopped when four more streams carried nothing more, and took up the climb again when
    # the trend rose a tenth past its best (windows 43 to 45).
    assert counts[15:42] == [16] * 27
    assert counts[42:45] == [17, 17, 18]


@pytest.mark.unit
def test_one_slow_window_holds_the_count_and_the_climb_goes_on() -> None:
    ladder = Ladder()
    rates = [100, 110, 121, 30, 133, 146, 160]
    assert [ladder.window(rate) for rate in rates] == [8, 9, 10, 11, 12, 13, 14]
    assert ladder.climbing


@pytest.mark.unit
def test_streams_that_add_nothing_end_the_climb_and_a_better_line_starts_it_again() -> None:
    ladder = Ladder()
    flat = [ladder.window(100) for _ in range(10)]
    assert flat == [8, 9, 10, 11, 12, 12, 12, 12, 12, 12]
    assert not ladder.climbing
    # A trend a twentieth over the best is not enough to start again; a tenth over it is.
    assert [ladder.window(rate) for rate in (106, 106, 106)] == [12, 12, 12]
    assert [ladder.window(rate) for rate in (120, 120)] == [12, 13]
    assert ladder.climbing


@pytest.mark.unit
def test_a_low_trend_holds_and_never_adds_a_stream() -> None:
    ladder = Ladder()
    assert [ladder.window(rate) for rate in (100, 50, 40, 45)] == [8, 9, 9, 9]


# --- the look: as it is, or stripped ---------------------------------------------------------


def _picture(path: Path, *, place: bool) -> Path:
    exif = Image.Exif()
    exif[0x010F] = "a camera maker"
    if place:
        exif.get_ifd(0x8825)[2] = (48.0, 51.0, 29.17)
    Image.new("RGB", (32, 24), (40, 90, 160)).save(path, exif=exif)
    return path


_SETTINGS: Any = SimpleNamespace(ffmpeg_path="ffmpeg")


async def test_a_file_with_no_place_is_sent_as_it_is_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    source = _picture(tmp_path / "plain.jpg", place=False)
    work = tmp_path / "out"

    ready = await transfer.prepare(source, work, settings=_SETTINGS)

    assert ready.path == source and not ready.copy
    assert ready.size == source.stat().st_size
    assert ready.digest is None, "nothing is read whole to make it ready"
    assert (await transfer.with_digest(ready)).digest == transfer.digest_file(source)
    assert ready.stamp == (source.stat().st_size, source.stat().st_mtime_ns)
    assert not work.exists() or list(work.iterdir()) == [], "no copy"
    whole = b"".join(
        [await transfer.read_ready_chunk(ready, n) for n in range(transfer.chunk_count(ready.size))]
    )
    assert whole == source.read_bytes()


async def test_a_file_with_a_place_never_leaves_unstripped(tmp_path: Path) -> None:
    source = _picture(tmp_path / "trip.jpg", place=True)
    assert places.places_in(source) > 0

    ready = await transfer.prepare(source, tmp_path / "out", settings=_SETTINGS)

    assert ready.copy and ready.path != source and ready.path.parent == tmp_path / "out"
    sent = await transfer.read_ready_chunk(ready, 0)
    assert sent == ready.path.read_bytes() and sent != source.read_bytes()
    assert places.places_in(ready.path) == 0
    assert source.exists(), "the original is read, never touched"


async def test_a_strip_holds_its_place_as_a_whole_file_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A strip keeps its storage's place for the whole file, so it goes through the gate that
    leaves a share's streams a place of their own."""
    source = _picture(tmp_path / "trip.jpg", place=True)
    held: list[str] = []
    real = lanes.whole_file

    @asynccontextmanager
    async def recorded(path: Path) -> AsyncIterator[None]:
        held.append(path.name)
        async with real(path):
            yield

    monkeypatch.setattr(lanes, "whole_file", recorded)

    await transfer.prepare(source, tmp_path / "out", settings=_SETTINGS)

    assert held == ["trip.jpg"]


async def test_a_file_whose_tables_cannot_be_read_is_stripped_rather_than_sent_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _picture(tmp_path / "odd.jpg", place=False)

    def unsure(_path: Path) -> int:
        raise places.CannotRemovePlaces("a structure runs past the end of the file")

    monkeypatch.setattr(places, "places_in", unsure)
    monkeypatch.setattr(transfer, "_holds_a_place", lambda _path: False)

    ready = await transfer.prepare(source, tmp_path / "out", settings=_SETTINGS)

    assert ready.copy and ready.path != source


async def test_an_original_changed_after_it_was_measured_is_not_read(tmp_path: Path) -> None:
    source = _picture(tmp_path / "plain.jpg", place=False)
    ready = await transfer.prepare(source, tmp_path / "out", settings=_SETTINGS)
    source.write_bytes(source.read_bytes() + b"more")

    with pytest.raises(Changed, match="changed since it was measured"):
        await transfer.read_ready_chunk(ready, 0)


@pytest.mark.unit
def test_the_remover_takes_only_what_a_swap_wrote(tmp_path: Path) -> None:
    library_file = tmp_path / "clip.mp4"
    library_file.write_bytes(b"theirs")

    with pytest.raises(ValueError, match="not a file a swap wrote"):
        transfer.remove(library_file)

    assert library_file.read_bytes() == b"theirs"
    copy = tmp_path / "x.strip"
    copy.write_bytes(b"ours")
    transfer.remove(copy)
    assert not copy.exists()


# --- a whole swap, with the real look ------------------------------------------------------------


@asynccontextmanager
async def _reading_order() -> AsyncIterator[list[tuple[str, bool]]]:
    """Every read through a storage lane, and whether it asked to go first."""
    seen: list[tuple[str, bool]] = []
    real = lanes.reading

    @asynccontextmanager
    async def recorded(path: Path) -> AsyncIterator[None]:
        seen.append((path.name, lanes._RANK.get() == lanes.FIRST))
        async with real(path):
            yield

    lanes.reading = recorded
    try:
        yield seen
    finally:
        lanes.reading = real


@_needs_psk
async def test_a_whole_swap_sends_the_plain_file_as_it_is_and_the_placed_one_stripped(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    plain = _picture(library / "plain.jpg", place=False)
    placed = _picture(library / "trip.jpg", place=True)
    big = library / "clip.bin"
    big.write_bytes(os.urandom(CHUNK_SIZE + 4321))
    named = {"key-plain": plain, "key-placed": placed}
    offer = Offer(
        files=[
            OfferedFile(
                key=key, size=path.stat().st_size, identity=f"id-{key}", kind="image", title=key
            )
            for key, path in named.items()
        ]
    )

    async def make_offer(*_args: Any, **_kwargs: Any) -> Offer:
        return offer

    async def path_of(_viewer: object, key: str) -> Path:
        return named[key]

    async def assess(arrived: Offer) -> tuple[OfferScreen, Callable[[Taken], Diff]]:
        def answer(taken: Taken) -> Diff:
            keys = [one.key for one in arrived.files if one.key not in taken.unticked]
            return Diff(wanted=keys, people={})

        return OfferScreen(layout="rows"), answer

    landed: dict[str, bytes] = {}

    async def land(session: Any, received: Any, *, ctx: Any) -> None:
        landed[received.key] = await asyncio.to_thread(received.staged.read_bytes)

    host_db = await _database(tmp_path / "host.sqlite3")
    guest_db = await _database(tmp_path / "guest.sqlite3")
    hoster = _Hoster()
    proxy = _Proxy(hoster)
    await proxy.start()
    host = _sessions(
        host_db,
        tmp_path / "h",
        hoster=hoster,
        make_offer=make_offer,
        path_of=path_of,
        prepare=partial(transfer.prepare, settings=_SETTINGS),
    )
    guest = _sessions(guest_db, tmp_path / "g", egress=_Egress(proxy.url), assess=assess, land=land)
    try:
        async with _reading_order() as reads:
            started = await _start_host(host)
            joined = await guest.join(
                viewer_id="viewer",
                token_text=started.token.text,
                dest_folder_id="folder",
                master_key=_MASTER,
            )
            host_work = tmp_path / "hw"
            host_task = asyncio.create_task(host.run(_Context(started.session_id, host_work)))  # type: ignore[arg-type]
            guest_task = asyncio.create_task(guest.run(_Context(joined, tmp_path / "gw")))  # type: ignore[arg-type]

            def code(sessions: Any, session_id: str) -> str | None:
                facts = sessions.facts(session_id)
                return facts.code if facts else None

            await _until(lambda: code(host, started.session_id) and code(guest, joined))
            await host.answer_code(started.session_id, True)
            await _until(lambda: (guest.facts(joined) or SimpleNamespace(screen=None)).screen)

            # What the answer would bring, before Take these: every file, then one unticked.
            both = sum(path.stat().st_size for path in named.values())
            assert guest.weigh_answer(joined, Taken()) == (2, both)
            assert guest.weigh_answer(joined, Taken(unticked=frozenset({"key-plain"}))) == (
                1,
                placed.stat().st_size,
            )
            with pytest.raises(swap.SwapRefused):
                host.weigh_answer(started.session_id, Taken())

            await guest.take(joined, Taken())
            await asyncio.wait_for(asyncio.gather(host_task, guest_task), 30)

        assert landed["key-plain"] == plain.read_bytes(), "sent as it is"
        assert landed["key-placed"] != placed.read_bytes()
        stripped = tmp_path / "landed-trip.jpg"
        stripped.write_bytes(landed["key-placed"])
        assert places.places_in(stripped) == 0, "NO LOCATION LEAVES"
        # The originals are where they were; the one copy made is gone with the swap.
        assert plain.exists() and placed.exists()
        assert not host_work.exists() or not list(host_work.rglob("*.strip"))
        # Every read the host made for the swap asked to go first, and the plain file was read
        # from where it lies.
        assert reads and all(first for _name, first in reads), reads
        assert ("plain.jpg", True) in reads
        row = await host.row(started.session_id)
        assert row is not None and (row.state, row.sent_files) == ("done", 2)
    finally:
        if proxy.server is not None:
            proxy.server.close()
        await host_db.close()
        await guest_db.close()


# --- a file changed during the swap ------------------------------------------------------------


@_needs_psk
async def test_an_original_changed_part_way_fails_and_the_guest_is_told(tmp_path: Path) -> None:
    async def as_it_is(source: Path, _workdir: Path) -> Prepared:
        info = source.stat()
        return Prepared(
            source,
            info.st_size,
            transfer.digest_file(source),
            copy=False,
            stamp=(info.st_size, info.st_mtime_ns),
        )

    data = os.urandom(CHUNK_SIZE * 2)
    async with _host_rig(tmp_path, {"a": data}, prepare=as_it_is) as rig:
        await rig.join()
        await rig.transferring("a")
        stream = await rig.stream()
        header = await stream.read(5)
        assert isinstance(header, dict) and header["file"] == 0
        (tmp_path / "library" / "a").write_bytes(data + b"edited")
        await stream.send({"have": []})
        with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
            while True:
                await stream.read(5)
        again = await rig.stream()
        assert await again.read(5) == {"file": 0, "cannot": True}
        live = rig.live
        assert 0 in live.failed_files
        assert (tmp_path / "library" / "a").exists(), "an original is never removed"


# --- the first pieces: every stream carries what is ready ----------------------------------------


async def _join_in_shares(rig: _HostRig, **says: int) -> None:
    """The rig's guest, saying in its hello that it takes a file in shares over several streams,
    and whatever else `says`."""
    conn = await rig.dial()
    secret = rig.started.token.secret
    await conn.send(hello(_device(), "guest", secret, os.urandom(32), striped=1, **says))
    answer = await conn.read(5)
    assert isinstance(answer, dict)
    rig.session = read_hello(answer, "host", secret).session
    rig.control = conn
    rig.pinger = asyncio.create_task(_keep_pinging(conn))
    await _until(rig.code)


@_needs_psk
async def test_a_stream_carries_a_ready_file_rather_than_waiting_for_one_still_being_read(
    tmp_path: Path,
) -> None:
    """THE SLOW START FROM A SHARE. Every file is read whole (its digest) before its first piece
    leaves, so on a slow storage the files become ready one after another. A stream with nothing
    to carry that took the next file of the queue and waited for THAT file to be read would sit
    idle, however long, while a file already ready went out on one stream at one connection's
    pace: through a tunnel bound per connection, a fraction of the line for the first minute.
    Every stream carries a share of whatever is ready."""
    first_read = asyncio.Event()

    async def slow_share(source: Path, _workdir: Path) -> Prepared:
        if source.name == "a":
            await first_read.wait()
        else:
            await asyncio.Event().wait()  # still being read when the test ends
        info = source.stat()
        stamp = (info.st_size, info.st_mtime_ns)
        return Prepared(source, info.st_size, transfer.digest_file(source), copy=False, stamp=stamp)

    files = {"a": os.urandom(CHUNK_SIZE * 3), "b": b"b" * 1000, "c": b"c" * 1000}
    async with _host_rig(tmp_path, files, prepare=slow_share) as rig:
        await _join_in_shares(rig)
        await rig.transferring("a", "b", "c")
        streams = [await rig.stream() for _ in range(3)]
        # Every stream has asked before anything is ready.
        await _until(lambda: len(rig.live.stream_tasks) == 3)
        await asyncio.sleep(0.1)
        first_read.set()

        headers = [await stream.read(5) for stream in streams]

        assert all(isinstance(one, dict) and one["file"] == 0 for one in headers), headers
        shares = sorted(chunk for one in headers for chunk in one["chunks"])  # type: ignore[index]
        assert shares == [0, 1, 2], "each stream a share of the one ready file"


@_needs_psk
async def test_a_file_whose_reading_failed_goes_back_to_the_front_and_is_read_afresh(
    tmp_path: Path,
) -> None:
    tries: list[str] = []

    async def fails_once(source: Path, _workdir: Path) -> Prepared:
        tries.append(source.name)
        if len(tries) == 1:
            raise OSError("the share went away for a moment")
        info = source.stat()
        stamp = (info.st_size, info.st_mtime_ns)
        return Prepared(source, info.st_size, transfer.digest_file(source), copy=False, stamp=stamp)

    async with _host_rig(tmp_path, {"a": b"a small file"}, prepare=fails_once) as rig:
        await _join_in_shares(rig)
        await rig.transferring("a")
        first = await rig.stream()
        with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
            await first.read(5)

        again = await rig.stream()
        header = await again.read(5)

        assert isinstance(header, dict) and header["file"] == 0
        assert tries == ["a", "a"]


# --- a file's song, to a side that takes songs ------------------------------------------------


@_needs_psk
@pytest.mark.parametrize("takes_songs", [True, False])
async def test_a_files_song_goes_only_to_a_side_whose_hello_says_it_takes_songs(
    tmp_path: Path, takes_songs: bool
) -> None:
    """An install from before songs travelled refuses an offer holding a key it does not declare,
    so it is sent the offer it always was, and the swap goes on."""
    sung = Offer(
        files=[
            OfferedFile(
                key="a",
                size=12,
                identity="id-a",
                kind="video",
                title="clip a",
                song=OfferedSong(name="Harbour Lights", recording="rec-1"),
            )
        ]
    )

    async def make_offer(*_args: object, **_kwargs: object) -> Offer:
        return sung

    async with _host_rig(tmp_path, {"a": b"a small file"}) as rig:
        rig.sessions.make_offer = make_offer
        await _join_in_shares(rig, **({"songs": 1} if takes_songs else {}))

        offered = (await rig.offer())["files"][0]

        if takes_songs:
            assert offered["song"] == {
                "name": "Harbour Lights",
                "artists": [],
                "recording": "rec-1",
            }
        else:
            assert "song" not in offered


# --- a person's confirmed count, to a side that takes it ---------------------------------------


@_needs_psk
@pytest.mark.parametrize("takes_counts", [True, False])
async def test_a_persons_confirmed_count_goes_only_to_a_side_whose_hello_says_it_takes_it(
    tmp_path: Path, takes_counts: bool
) -> None:
    """An install from before the count travelled refuses an offer holding a key it does not
    declare, so it is sent the offer it always was, and the swap goes on."""
    counted = Offer(
        files=[OfferedFile(key="a", size=12, identity="id-a", kind="video", title="clip a")],
        people=[
            OfferedPerson(
                name="Juno Pellerin",
                faces=OfferedFaces(recognizer="model-a", dimension=2, faces=[], confirmed=12),
            )
        ],
    )

    async def make_offer(*_args: object, **_kwargs: object) -> Offer:
        return counted

    async with _host_rig(tmp_path, {"a": b"a small file"}) as rig:
        rig.sessions.make_offer = make_offer
        await _join_in_shares(rig, **({"counts": 1} if takes_counts else {}))

        faces = (await rig.offer())["people"][0]["faces"]

        if takes_counts:
            assert faces["confirmed"] == 12
        else:
            assert "confirmed" not in faces
            assert faces["recognizer"] == "model-a"


# --- the estimate's pace ---------------------------------------------------------------------


def _pace(live: swap.HostSession) -> int | None:
    return live.pace_bps


@_needs_psk
async def test_the_pace_holds_through_a_window_that_moved_nothing(tmp_path: Path) -> None:
    async with _host_rig(tmp_path, {"a": b"x" * 1000}, rate_window=0.05) as rig:
        await rig.join()
        await rig.transferring("a")
        live = rig.live
        assert _pace(live) is None, "no pace before anything moved"
        live.window_bytes = 50_000
        await _until(lambda: _pace(live))
        await _until(lambda: list(live._recent) == [0] * swap.PACE_WINDOWS)
        # The pace is the minute's mean, so the windows that moved nothing lower it, and the last
        # pace that was more than nothing is kept: the estimate never goes back to having none.
        moved = 50_000 * 8 / 0.05
        assert live.pace_bps == int(moved) // swap.PACE_WINDOWS, "a minute of nothing keeps it"
        facts = rig.sessions.facts(rig.id)
        assert facts is not None and facts.rate_bps == live.pace_bps
