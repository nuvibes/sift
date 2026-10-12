# SPDX-License-Identifier: AGPL-3.0-or-later
"""The landing takes several verified files together, and a person's first file alone."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel import lanes
from sift.slices.swap.ingest import Landed, LandingSession
from sift.slices.swap.live import Watchdog, _Live
from sift.slices.swap.models import Diff, Offer, OfferedFile, OfferedPerson
from sift.slices.swap.receiving import _Receiving

_SESSION = "01HLANDINGTOGETHER0000001"


class _Library:
    """A destination folder in a root at `abs_path`, or none at all."""

    def __init__(self, abs_path: str | None) -> None:
        self.abs_path = abs_path

    async def get_folder(self, folder_id: str) -> Any:
        if self.abs_path is None:
            return None
        return SimpleNamespace(id=folder_id, root_id="root", rel_path="")

    async def get_root(self, root_id: str) -> Any:
        return SimpleNamespace(id=root_id, abs_path=self.abs_path)


class _Store:
    def __init__(self) -> None:
        self.dropped: list[str] = []

    async def drop_manifest(self, session_id: str, key: str) -> None:
        self.dropped.append(key)


class _Side(_Receiving):
    """The receiving half alone, wanting every file offered; `people[n]` names file n's people."""

    def __init__(self, land: Any, people: list[list[int]], context: Any) -> None:
        self._start_figures()
        self.store = _Store()
        self.live = SimpleNamespace(  # type: ignore[assignment]
            owner=SimpleNamespace(land=land, store=self.store),
            id=_SESSION,
            short_id=_SESSION[-8:],
            watchdog=Watchdog(),
            ended=asyncio.Event(),
            settle=_Live.settle,
            context=context,
            spawn=lambda work, name: asyncio.ensure_future(work),
        )
        self._start_receiving("folder")
        self.offer = Offer(
            files=[
                OfferedFile(
                    key=f"k{n}",
                    size=1,
                    identity=f"id-{n}",
                    kind="image",
                    title=f"photo {n}",
                    people=names,
                )
                for n, names in enumerate(people)
            ],
            people=[OfferedPerson(name="Mara Vell"), OfferedPerson(name="Juno Pike")],
        )
        self.wanted = list(range(len(people)))
        self.wanted_set = set(self.wanted)
        self.diff = Diff(wanted=[f"k{n}" for n in self.wanted], people={})
        self.landing_session = LandingSession(
            id=_SESSION, peer_device="device", dest_folder_id="folder", wanted=frozenset()
        )


class _Landing:
    """A landing that holds each file until it is let go, and keeps what ran beside what."""

    def __init__(self) -> None:
        self.running: set[str] = set()
        self.most = 0
        self.order: list[str] = []
        self.release: dict[str, asyncio.Event] = {}
        self.fail: set[str] = set()

    def let_go(self, key: str) -> None:
        self.release.setdefault(key, asyncio.Event()).set()

    async def __call__(self, session: Any, received: Any, *, ctx: Any) -> Any:
        key = received.key
        self.order.append(key)
        self.running.add(key)
        self.most = max(self.most, len(self.running))
        try:
            await self.release.setdefault(key, asyncio.Event()).wait()
        finally:
            self.running.discard(key)
        if key in self.fail:
            raise RuntimeError("refused")
        return Landed(
            asset_id=f"a-{key}",
            location_id="l",
            was_duplicate=False,
            folder_id="folder",
            people=(),
            created=(),
            faces_held=0,
            named=(),
            site_id=None,
            faces_by_person=(),
        )


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def _started(side: _Side, tmp_path: Path) -> asyncio.Task[None]:
    landing = asyncio.ensure_future(side.land_each())
    for n in side.wanted:
        staged = tmp_path / f"staged-{n}.part"
        staged.write_bytes(b"x")
        await side.landing.put((n, staged, "d" * 64, 1))
    await _settle()
    return landing


async def test_the_landing_takes_as_many_files_together_as_the_destination_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[Path] = []

    def reads_at_once(path: Path) -> int:
        asked.append(path)
        return 3

    monkeypatch.setattr(lanes, "reads_at_once", reads_at_once)
    land = _Landing()
    side = _Side(land, [[]] * 7, SimpleNamespace(library=_Library(str(tmp_path))))
    landing = await _started(side, tmp_path)
    try:
        assert asked == [tmp_path]
        assert len(land.running) == 3, "three together, the destination's number"
        for key in ["k0", "k1", "k2", "k3", "k4", "k5", "k6"]:
            land.let_go(key)
            await _settle()
        await asyncio.wait_for(side.landing.join(), 5)
    finally:
        landing.cancel()
    assert land.most == 3
    assert sorted(land.order) == [f"k{n}" for n in range(7)], "each file landed once"
    assert side.done_files == set(range(7)) and not side.failed_files
    assert sorted(side.store.dropped) == sorted(land.order)
    assert not list(tmp_path.glob("staged-*"))


@pytest.mark.parametrize("library", [None, _Library(None)], ids=["no context", "no destination"])
async def test_with_nowhere_to_read_the_number_from_files_land_one_at_a_time(
    library: _Library | None,
) -> None:
    context = None if library is None else SimpleNamespace(library=library)
    side = _Side(_Landing(), [[]], context)
    assert await side._landing_at_once() == 1


async def test_a_persons_first_file_lands_alone_and_the_rest_of_theirs_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Who a person is here and their faces are settled by their first file, never by two at once."""
    monkeypatch.setattr(lanes, "reads_at_once", lambda path: 4)
    land = _Landing()
    side = _Side(land, [[0], [0], [0, 1], [1]], SimpleNamespace(library=_Library(str(tmp_path))))
    landing = await _started(side, tmp_path)
    try:
        # File 0 settles person 0; file 2 waits on it holding no claim, so file 3 settles person 1.
        assert land.running == {"k0", "k3"}
        land.let_go("k0")
        await _settle()
        assert land.running == {"k1", "k3"}, "file 2 still waits for person 1's first"
        land.let_go("k3")
        await _settle()
        assert land.running == {"k1", "k2"}
        land.let_go("k1")
        land.let_go("k2")
        await asyncio.wait_for(side.landing.join(), 5)
    finally:
        landing.cancel()
    assert side.done_files == {0, 1, 2, 3}


async def test_a_first_file_that_fails_leaves_the_next_of_theirs_to_go_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lanes, "reads_at_once", lambda path: 4)
    land = _Landing()
    land.fail.add("k0")
    side = _Side(land, [[0], [0], [0]], SimpleNamespace(library=_Library(str(tmp_path))))
    landing = await _started(side, tmp_path)
    try:
        assert land.running == {"k0"}
        land.let_go("k0")
        await _settle()
        assert len(land.running) == 1, "the person is not settled yet: one goes first again"
        for key in ["k1", "k2"]:
            land.let_go(key)
        await asyncio.wait_for(side.landing.join(), 5)
    finally:
        landing.cancel()
    assert side.failed_files == {0} and side.done_files == {1, 2}
    assert side.refusals == {0: "landing"}
