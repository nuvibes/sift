# SPDX-License-Identifier: AGPL-3.0-or-later
"""What waits for ready: the catch-up, then the workers, held for the first screen, then the
hardware asked again; and the line that tells a desktop shell the server is listening."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

from sift import main as sift_main
from sift.kernel import wiring
from sift.kernel.wiring import provide
from sift.slices import photo_sets, player, semantic
from sift.wiring import lifespan


class _Bus:
    def __init__(self, opens_after: int | None) -> None:
        self.asked = 0
        self.opens_after = opens_after

    def open_connections(self) -> int:
        self.asked += 1
        return int(self.opens_after is not None and self.asked > self.opens_after)


class _Pool:
    def __init__(self, said: list[str]) -> None:
        self.said = said

    async def start(self) -> None:
        self.said.append("pool.start")


def _ready(bus: _Bus) -> FastAPI:
    app = FastAPI()
    for part in (player.SEGMENT_CACHE, photo_sets.SERVICE, semantic.WHOLE_PICTURE):
        provide(app, part, object())
    provide(app, wiring.CHANGES, bus)
    return app


def _parts(said: list[str]) -> dict[str, Any]:
    built = SimpleNamespace(
        queue=None,
        marks=None,
        downloads=SimpleNamespace(service=None),
        hub=None,
        understanding=SimpleNamespace(faces=None),
    )
    return {
        "settings": SimpleNamespace(data_dir=Path(".")),
        "hardware": SimpleNamespace(answers_kept=False),
        "store": SimpleNamespace(content=None),
        "built": built,
        "pool": _Pool(said),
    }


@pytest.fixture
def said(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    heard: list[str] = []

    async def catch_up(*_: object) -> None:
        heard.append("catch_up")

    async def probe_again(*_: object) -> None:
        heard.append("probe_again")

    monkeypatch.setattr(lifespan, "catch_up", catch_up)
    monkeypatch.setattr(lifespan, "probe_again", probe_again)
    monkeypatch.setattr(lifespan, "_FIRST_SCREEN_BEAT", 0.001)
    return heard


@pytest.mark.unit
async def test_the_workers_start_after_the_catch_up_and_before_the_hardware_is_asked_again(
    said: list[str],
) -> None:
    await lifespan._after_ready(_ready(_Bus(None)), **_parts(said), hold=0.0)

    assert said == ["catch_up", "pool.start", "probe_again"]


@pytest.mark.unit
async def test_a_catch_up_that_fails_still_starts_the_workers(
    said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: object) -> None:
        raise RuntimeError("a count could not be read")

    monkeypatch.setattr(lifespan, "catch_up", broken)
    await lifespan._after_ready(_ready(_Bus(None)), **_parts(said), hold=0.0)

    assert said == ["pool.start", "probe_again"]


@pytest.mark.unit
async def test_the_workers_wait_for_the_first_screen_and_no_longer(said: list[str]) -> None:
    bus = _Bus(opens_after=3)
    await lifespan._after_ready(_ready(bus), **_parts(said), hold=5.0)

    assert said == ["catch_up", "pool.start", "probe_again"]
    assert bus.asked == 4


@pytest.mark.unit
async def test_with_no_screen_the_workers_start_when_the_hold_runs_out(said: list[str]) -> None:
    bus = _Bus(opens_after=None)
    began = asyncio.get_running_loop().time()
    await lifespan._after_ready(_ready(bus), **_parts(said), hold=0.05)

    assert said[-2:] == ["pool.start", "probe_again"]
    assert asyncio.get_running_loop().time() - began >= 0.04


@pytest.mark.unit
async def test_a_server_no_shell_started_holds_nothing(said: list[str]) -> None:
    bus = _Bus(opens_after=None)
    await lifespan._after_ready(_ready(bus), **_parts(said), hold=0.0)

    assert bus.asked == 0


@pytest.mark.unit
async def test_the_first_screen_is_a_connection_to_the_change_stream() -> None:
    assert await lifespan.first_screen_or(_Bus(opens_after=0), 1.0, beat=0.001) is True  # type: ignore[arg-type]
    assert await lifespan.first_screen_or(_Bus(opens_after=None), 0.01, beat=0.001) is False  # type: ignore[arg-type]


class _Server:
    def __init__(self, *, starts: bool) -> None:
        self.started = False
        self.starts = starts

    async def startup(self, sockets: Any = None) -> None:
        self.started = self.starts


@pytest.mark.unit
async def test_the_shell_is_told_once_the_socket_listens_and_not_before() -> None:
    told: list[str] = []
    server = _Server(starts=True)
    sift_main.say_when_listening(server, told.append)

    assert told == []
    await server.startup()
    assert told == [sift_main.READY_LINE]


@pytest.mark.unit
async def test_a_start_that_failed_tells_the_shell_nothing() -> None:
    told: list[str] = []
    server = _Server(starts=False)
    sift_main.say_when_listening(server, told.append)

    await server.startup()
    assert told == []


@pytest.mark.unit
def test_the_process_age_is_read_where_the_operating_system_says_it() -> None:
    age = sift_main.since_the_process_began_ms()
    if sys.platform in ("win32", "linux"):
        assert age is not None and 0 <= age < 3_600_000


@pytest.mark.unit
async def test_a_hardware_probe_that_fails_after_ready_is_logged_and_nothing_else(
    said: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: object) -> None:
        raise OSError("a program would not start")

    monkeypatch.setattr(lifespan, "probe_again", broken)
    await lifespan._after_ready(_ready(_Bus(None)), **_parts(said), hold=0.0)

    assert said == ["catch_up", "pool.start"]


@pytest.mark.unit
async def test_the_hardware_is_asked_again_only_after_a_start_that_used_kept_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from sift.wiring import machine

    asked: list[Path] = []

    async def reprobe(_settings: object, _report: object, *, kept: Path) -> bool:
        asked.append(kept)
        return False

    monkeypatch.setattr(machine, "reprobe", reprobe)
    settings = SimpleNamespace(cache_dir=tmp_path)
    await machine.probe_again(settings, SimpleNamespace(answers_kept=False))  # type: ignore[arg-type]
    await machine.probe_again(settings, SimpleNamespace(answers_kept=True))  # type: ignore[arg-type]

    assert asked == [tmp_path / "hardware-probe.json"]


@pytest.mark.unit
def test_the_process_age_is_none_where_the_operating_system_will_not_say(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreadable(*_: object, **__: object) -> str:
        raise OSError("no such file")

    monkeypatch.setattr(sift_main, "_WINDOWS", False)
    monkeypatch.setattr(Path, "read_text", unreadable)
    assert sift_main.since_the_process_began_ms() is None
