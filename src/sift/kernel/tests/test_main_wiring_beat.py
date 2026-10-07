# SPDX-License-Identifier: AGPL-3.0-or-later
"""The composition root's settings beat, and the two closures it hands out that nothing else calls."""

from __future__ import annotations

import asyncio

# The NAME only: `sqlite3.Error` is what the settings converger catches, raised below to prove that
# arm; nothing here opens a database.
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from sift import main
from sift.kernel import log_settings, wiring
from sift.kernel.config import get_settings
from sift.kernel.content import lacks_derivative, lacks_fingerprint
from sift.kernel.db import DatabaseError
from sift.kernel.jobs.quiet_hours import next_opening
from sift.slices import (
    faces,
    importing,
    media_jobs,
    music,
    performance,
    player,
    semantic,
    tasks,
    watermarks,
)
from sift.slices.media_jobs.jobs import PICTURES
from sift.wiring import (
    downloads,
    lifespan,
)

# --- the settings beat
#
# `keep_the_settings_applied` pushes two numbers on a beat to objects that cannot read the database
# where it matters (the segment cache inside a synchronous lock, and the log). A background task's
# faults are silent, so a raised read must not end it. Driven by beats, never by a sleep: a wall
# clock can step backwards.


class _Cache:
    """The segment cache, narrowed to the one method the beat calls."""

    def __init__(self) -> None:
        self.caps: list[int] = []

    def resize(self, max_bytes: int) -> bool:
        self.caps.append(max_bytes)
        return len(self.caps) == 1


DEFAULT_ANSWERS: dict[str, Any] = {
    player.CACHE_MAX_GB_KEY: 4,
    log_settings.DETAIL_KEY: log_settings.NORMAL,
    log_settings.KEEP_MB_KEY: 512,
    log_settings.HIDE_PERSONAL_KEY: False,
}


async def _beat(
    cache: _Cache,
    *,
    beats: int = 1,
    answers: Callable[[int, str], Any] | None = None,
) -> None:
    """Run the loop for exactly `beats` passes: `stop` is set inside the read that opens a beat and
    looked at only at the top of the next, so the count is exact without a timer."""
    stop = asyncio.Event()
    pass_number = 0

    async def get_app(key: str) -> Any:
        nonlocal pass_number
        if key == player.CACHE_MAX_GB_KEY:
            pass_number += 1
            if pass_number >= beats:
                stop.set()
        if answers is not None:
            return answers(pass_number, key)
        return DEFAULT_ANSWERS[key]

    await asyncio.wait_for(
        lifespan.keep_the_settings_applied(get_app, cache, stop, backups=5, interval=0.001),  # type: ignore[arg-type]
        timeout=10,
    )


async def test_the_beat_pushes_the_stored_cap_onto_the_running_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In bytes. The setting is in gigabytes because that is the unit somebody thinks in, and the
    cache counts bytes because bytes are what fill a disk."""
    applied: list[dict[str, Any]] = []
    monkeypatch.setattr(lifespan, "apply_log_preferences", lambda **kw: applied.append(kw))
    cache = _Cache()

    await _beat(cache)

    assert cache.caps == [4 * 1024**3]
    assert applied[0]["detailed"] is False
    assert applied[0]["hide_personal"] is False, "the log is written whole unless asked"
    # PER FILE, not the total: the setting says what the WHOLE log may take and there are
    # `backups + 1` files, so handing the number straight to the handler would take six times it.
    assert applied[0]["per_file_bytes"] == log_settings.per_file_bytes(512 * 1024 * 1024, 5)


async def test_a_beat_that_lands_while_the_database_is_swapped_is_skipped_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restore closes the database and opens the restored one; a beat in that window is refused
    by the kernel, and the loop must still be there for the next beat."""
    applied: list[dict[str, Any]] = []
    monkeypatch.setattr(lifespan, "apply_log_preferences", lambda **kw: applied.append(kw))
    cache = _Cache()

    def answers(pass_number: int, key: str) -> Any:
        if pass_number == 1:
            raise DatabaseError("the database is not open: call connect() first")
        return DEFAULT_ANSWERS[key]

    await _beat(cache, beats=2, answers=answers)

    assert cache.caps == [4 * 1024**3], "the second beat applied; the first was skipped"
    assert len(applied) == 1


async def test_the_beat_stops_when_it_is_told_to_and_does_no_further_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set before the first beat, so the loop must return without touching anything at all,
    which is what makes shutdown immediate rather than one interval long."""
    monkeypatch.setattr(lifespan, "apply_log_preferences", lambda **kw: None)
    cache = _Cache()
    stop = asyncio.Event()
    stop.set()
    asked: list[str] = []

    async def get_app(key: str) -> Any:
        asked.append(key)
        return 1

    await asyncio.wait_for(
        lifespan.keep_the_settings_applied(get_app, cache, stop, interval=10),  # type: ignore[arg-type]
        timeout=10,
    )

    assert asked == []
    assert cache.caps == []


async def test_being_told_to_stop_MID_WAIT_returns_without_one_last_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown during the three-second wait ends it immediately (the wait is on the stop event), and
    the check after it skips one more round of reads against a closing database."""
    monkeypatch.setattr(lifespan, "apply_log_preferences", lambda **kw: None)
    cache = _Cache()
    stop = asyncio.Event()
    asked: list[str] = []

    async def get_app(key: str) -> Any:
        asked.append(key)
        return DEFAULT_ANSWERS[key]

    task = asyncio.create_task(
        lifespan.keep_the_settings_applied(get_app, cache, stop, interval=3600)  # type: ignore[arg-type]
    )
    # As far as the wait, with nothing read.
    for _ in range(5):
        await asyncio.sleep(0)
    assert asked == [], "the beat read something before its first interval had passed"

    stop.set()
    await asyncio.wait_for(task, timeout=10)

    assert asked == []
    assert cache.caps == []


async def test_a_read_that_fails_is_skipped_rather_than_ending_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A beat whose read raises is skipped, not fatal: a dead task would take both settings for the
    life of the process; the next beat tries again."""
    applied: list[dict[str, Any]] = []
    monkeypatch.setattr(lifespan, "apply_log_preferences", lambda **kw: applied.append(kw))
    cache = _Cache()

    def answers(pass_number: int, key: str) -> Any:
        if pass_number == 1:
            raise sqlite3.OperationalError("the database is locked")
        return {
            player.CACHE_MAX_GB_KEY: 2,
            log_settings.DETAIL_KEY: log_settings.DETAILED,
            log_settings.KEEP_MB_KEY: 64,
            log_settings.HIDE_PERSONAL_KEY: True,
        }[key]

    await _beat(cache, beats=2, answers=answers)

    # The first beat applied nothing and did not end the task; the second one applied everything.
    assert cache.caps == [2 * 1024**3]
    assert applied and applied[-1]["detailed"] is True
    assert applied[-1]["hide_personal"] is True


async def test_a_cap_that_did_not_move_is_not_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    """A beat that moved nothing logs nothing: `resize` says whether the cap moved."""
    monkeypatch.setattr(lifespan, "apply_log_preferences", lambda **kw: None)
    said: list[str] = []
    monkeypatch.setattr(lifespan.log, "info", lambda event, **kw: said.append(event))
    cache = _Cache()

    await _beat(cache, beats=3)

    assert len(cache.caps) == 3, "the beat did not run three times, so this proves nothing"
    assert said.count("player.cache.resized") == 1


# --- two closures the composition root hands out, which nothing in the suite calls


async def test_the_downloader_reader_asks_the_store_rather_than_deciding_itself() -> None:
    """Which tool fetches from a Site, read live when a download starts, through the store's own
    per-Site-then-default `resolve`."""

    class Store:
        def __init__(self) -> None:
            self.asked: list[str | None] = []

        async def resolve(self, site_key: str | None) -> Any:
            self.asked.append(site_key)
            return SimpleNamespace(downloader="ytdlp" if site_key == "tiktok" else None)

    store = Store()
    read = downloads._downloader_for(cast(Any, store))

    assert await read("tiktok") == "ytdlp"
    # A Site with no row of its own: the store's own fallback answers, and this adds no rule.
    assert await read("bunkr") is None
    assert store.asked == ["tiktok", "bunkr"]


@pytest.fixture
def assembled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    with TestClient(main.create_app()) as client:
        yield client
    get_settings.cache_clear()


def test_each_product_asks_the_feature_that_owns_its_answer(
    assembled: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether a product can be made is the feature's answer AS A PRESS (the Build asks as one: the
    arrival When never refuses a press); when tonight starts and which clip
    shape is in force are the preferences'; the fingerprint's build is the media slice's own
    unit. Read inside the application's own loop, which is where its database may be asked."""
    app = cast(Any, assembled.app)
    fingerprinted: list[str] = []

    async def fingerprint_one(_content: Any, asset_id: str, *, settings: Any) -> bool | None:
        fingerprinted.append(asset_id)
        return True

    monkeypatch.setattr(media_jobs, "fingerprint_one", fingerprint_one)

    async def check() -> None:
        registry = wiring.part_of_app(app, importing.PRODUCTS)
        policy = wiring.part_of_app(app, importing.SERVICE)
        hub = wiring.part_of_app(app, wiring.SETTINGS_HUB)
        fingerprints, people, meaning = (
            registry.get(key) for key in ("fingerprints", "faces", "meaning")
        )
        assert fingerprints and people and meaning

        assert await people.switched_on() is await policy.allows(faces.FACE_SCAN, pressed=True)
        assert await meaning.switched_on() is await policy.allows(
            semantic.SEMANTIC_DESCRIBE, pressed=True
        )
        # Fingerprints answer to their own switch now, like every picture does.
        assert await fingerprints.switched_on() is await policy.allows(
            media_jobs.FINGERPRINT_FOR_STASH_BOXES, pressed=True
        )
        # One product per picture, each answering to its own switch.
        for picture in PICTURES:
            product = registry.get(picture.key)
            assert product is not None, picture.key
            wanted = await policy.allows(picture.job_type, pressed=True)
            assert await product.switched_on() is wanted
            assert await product.lack() == (lacks_derivative([picture.kind]) if wanted else None)
        pictures = registry.get("thumbnails")
        assert pictures is not None
        assert await fingerprints.lack() == lacks_fingerprint()

        assert await registry.night_start() == str(await hub.get_app(tasks.FROM_KEY))
        # "Wait for quiet hours" names the moment the range opens next, by the range as set.
        quiet = (str(await hub.get_app(tasks.FROM_KEY)), str(await hub.get_app(tasks.UNTIL_KEY)))
        before = int(time.time())
        opens = await registry.quiet_opens()
        assert next_opening(*quiet, before) <= opens <= next_opening(*quiet, int(time.time()))
        # The watermark read answers to its switch as a press, like every product the Build makes.
        marks = registry.get(watermarks.PRODUCT)
        assert marks is not None
        assert await marks.switched_on() is await policy.allows(
            watermarks.WATERMARK_READ, pressed=True
        )
        # The one product ticked by the ARRIVAL rule: the Build sheet's music row says whether the
        # library fingerprints sound on its own.
        sound = registry.get(music.PRODUCT)
        assert sound is not None
        assert await sound.switched_on() is await policy.allows(music.AUDIO_FINGERPRINT)
        chosen = pictures.build.keywords["chosen_shape"]  # type: ignore[attr-defined]
        assert await chosen() == str(await hub.get_app(performance.PREVIEW_SHAPE_KEY))

        await fingerprints.build(cast(Any, SimpleNamespace(payload={"asset_id": "a-file"})))

    assembled.portal.call(check)  # type: ignore[union-attr]
    assert fingerprinted == ["a-file"]
