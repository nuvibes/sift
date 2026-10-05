# SPDX-License-Identifier: AGPL-3.0-or-later
"""The feature object itself: which models it holds, what it fetches, and one file read end to end.

No model is run. The reader is stood in for with one that answers lines a test chose, and the file
is stood in for at the one seam that opens it, so what is checked is everything the service decides
around those two: when the models are rebuilt, what is fetched, what a file that cannot be opened
leaves behind, and what one reading writes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

# Imported for its side effect: the workbench slice registers the ledger's table, which a username
# arriving is written to. `initialize_schema()` creates only what is registered.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.slices.watermarks import frames, read, weights
from sift.slices.watermarks import settings as watermark_settings
from sift.slices.watermarks.reader import Reader
from sift.slices.watermarks.service import FROM_WATERMARK, WatermarkService
from sift.slices.watermarks.store import Store

pytestmark = [pytest.mark.anyio]

A_STILL = "01HX0000000000000000000SV1"
A_CLIP = "01HX0000000000000000000SV2"


class Preferences:
    def __init__(self, **values: Any) -> None:
        self.values = values

    async def get_app(self, key: str) -> Any:
        return self.values.get(key)


class Models:
    """The loaded models as the service holds them. Answers the lines a test hands it."""

    def __init__(self, lines: list[read.Line] | None = None) -> None:
        self.lines = lines or []
        self.broken: str | None = None
        self.unloaded = 0
        self.asked: list[list[Any]] = []

    def installed(self) -> bool:
        return True

    def unload(self) -> None:
        self.unloaded += 1

    async def read_crops(self, pieces: list[Any]) -> list[read.Line]:
        self.asked.append(list(pieces))
        return self.lines


class Visibility:
    """The read that decides what a user may be shown, answering from a set a test chose."""

    def __init__(self, visible: set[str]) -> None:
        self.visible = visible

    async def can_view(self, viewer: Any, asset_id: str) -> bool:
        return asset_id in self.visible


def _hardware() -> HardwareReport:
    return HardwareReport(
        cpu_count=4,
        total_ram_bytes=8 << 30,
        worker_concurrency=3,
        cuda=False,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )


def _service(
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    models: Any = None,
    on: bool = True,
    repository: Any = None,
) -> WatermarkService:
    return WatermarkService(
        store=Store(cast(Any, database)),
        content=cast(Any, None),
        repository=cast(Any, repository),
        settings=cast(Any, settings),
        hardware=_hardware(),
        preferences=Preferences(
            **{watermark_settings.ENABLED_KEY: on, watermark_settings.DEVICE_KEY: "cpu"}
        ),
        reader=cast(Any, models),
    )


async def _library(tmp_path: Path) -> Database:
    database = Database(tmp_path / "service.sqlite3", readers=1)
    await database.connect()
    await database.initialize_schema()
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('r1', 'r', '/r', 0)"
    )
    for asset_id in (A_STILL, A_CLIP):
        await database.execute(
            "INSERT INTO assets (id, identity, media_type, width, height, added_at)"
            " VALUES (?, ?, 'image', 1920, 1080, 0)",
            (asset_id, f"identity-{asset_id}"),
        )
    return database


# --- which models are held ----------------------------------------------------------------


async def test_the_same_models_are_kept_while_the_device_stays_the_same() -> None:
    stand_in = Models()
    held: Any = stand_in
    service = _service(models=held)
    assert await service.models() is held
    assert await service.models() is held
    assert stand_in.unloaded == 0


async def test_changing_the_device_rebuilds_the_models_rather_than_keeping_the_old_ones(
    settings: Settings,
) -> None:
    """The device is baked into a prepared session, so keeping the old one would make the change
    take effect for everything except the thing already running."""
    held = Models()
    service = _service(models=held, settings=settings)
    await service.models()
    cast(Preferences, service._preferences).values[watermark_settings.DEVICE_KEY] = "cuda"

    rebuilt = await service.models()

    assert held.unloaded == 1
    assert isinstance(rebuilt, Reader)
    assert cast(Any, rebuilt)._runner.device == "cuda"


async def test_switching_off_gives_the_memory_back_once(settings: Settings) -> None:
    held = Models()
    service = _service(models=held, settings=settings)
    service.release()
    service.release()
    assert held.unloaded == 1
    # And the next use builds afresh rather than handing back what was released.
    assert isinstance(await service.models(), Reader)


async def test_a_pass_is_ready_with_the_models_on_disk_and_the_device_answering() -> None:
    assert await _service(models=Models()).ready() == (True, None)


# --- fetching the models ------------------------------------------------------------------


class WeightFiles:
    """The feature's model store: which files are on disk, and what was fetched."""

    def __init__(self, present: set[str]) -> None:
        self.present = present
        self.fetched: list[tuple[str, Any]] = []
        self.afresh: list[bool] = []

    def installed(self, weight: weights.Weight) -> bool:
        return weight.id in self.present

    async def fetch(
        self, weight: weights.Weight, *, progress: Any = None, fresh: bool = False
    ) -> None:
        self.fetched.append((weight.id, progress))
        self.afresh.append(fresh)
        self.present.add(weight.id)


async def test_only_the_missing_model_is_fetched_unless_asked_again(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installed means the file exists; a damaged one is replaced by asking again."""
    files = WeightFiles({"finder"})
    monkeypatch.setattr(weights, "store", lambda _settings: files)
    service = _service(settings=settings)

    def note(written: int, total: int) -> bool:
        return True

    assert await service.install_models(progress=note) == ["reader"]
    assert files.fetched == [("reader", note)]
    assert await service.install_models(force=True) == ["finder", "reader"]
    assert files.afresh == [False, True, True], "asked again, nothing left from before is resumed"


# --- what a user may be shown -------------------------------------------------------------


async def test_only_the_files_this_user_may_see_are_answered() -> None:
    service = _service(repository=Visibility({A_STILL}))
    assert await service.visible_of(cast(Any, None), [A_STILL, A_CLIP]) == {A_STILL}


# --- one file, end to end -----------------------------------------------------------------


def _open_as(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, media_type: str, duration_ms: int | None
) -> list[dict[str, Any]]:
    """Stand in for opening the file and for cutting its crops. Hands back what the crops were
    asked for with, so a test can say which frame was read."""
    asked: list[dict[str, Any]] = []

    async def resolve(_content: object, asset_id: str, **_: object) -> object:
        asset = SimpleNamespace(
            width=1920,
            height=1080,
            probed_at=1,
            media_type=media_type,
            duration_ms=duration_ms,
            identity=f"identity-{asset_id}",
        )
        return SimpleNamespace(asset=asset, path=tmp_path / f"{asset_id}.bin")

    async def crops(path: Path, **rest: Any) -> list[frames.Piece]:
        asked.append(rest)
        return []

    monkeypatch.setattr(media, "resolve_decodable", resolve)
    monkeypatch.setattr(frames, "read", crops)
    return asked


async def test_a_file_read_end_to_end_is_filed_under_the_site_its_mark_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confidence kept is that of the line the mark was in, not of the best line on the frame:
    a caption read perfectly beside a mark read badly would otherwise flatter it."""
    database = await _library(tmp_path)
    _open_as(monkeypatch, tmp_path, media_type="image", duration_ms=None)
    models = Models(
        [
            read.Line(text="onlyfans.com/riverbend", confidence=0.7, crop="corner"),
            read.Line(text="a caption read perfectly", confidence=0.99, crop="band"),
        ]
    )
    service = _service(database=database, models=models)
    try:
        found = await service.read_asset(A_STILL)
        assert found is not None
        assert (found.site, found.username, found.confidence) == ("OnlyFans", "riverbend", 0.7)
        assert found.frame_ms == 0
        filed = await database.fetch_all(
            "SELECT aa.asset_id AS asset_id FROM asset_usernames aa WHERE aa.source = ?",
            (FROM_WATERMARK,),
        )
        assert [str(row["asset_id"]) for row in filed] == [A_STILL]
        assert await service.read_files() == 1
        assert await service.marks_found() == 1
    finally:
        await database.close()


async def test_a_clip_is_read_a_quarter_of_the_way_in_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = await _library(tmp_path)
    asked = _open_as(monkeypatch, tmp_path, media_type="video", duration_ms=40000)
    models = Models([read.Line(text="fansly.com/riverbend", confidence=0.9, crop="band")])
    try:
        found = await _service(database=database, models=models).read_asset(A_CLIP)
        assert found is not None
        assert found.frame_ms == int(40000 * frames.FRACTION)
        assert asked[0]["duration_ms"] == 40000
    finally:
        await database.close()


async def test_a_file_with_no_mark_is_written_down_as_looked_at_and_answers_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pass costs the same on an unmarked file, and much of a library is unmarked: without
    the record every sweep would open them all again."""
    database = await _library(tmp_path)
    _open_as(monkeypatch, tmp_path, media_type="image", duration_ms=None)
    service = _service(database=database, models=Models([]))
    try:
        assert await service.read_asset(A_STILL) is None
        assert await service.read_files() == 1
        assert await service.marks_found() == 0
    finally:
        await database.close()


async def test_a_file_whose_copies_are_all_out_of_reach_is_left_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An offline share is not a file with no mark. Writing it down as read is how a library comes
    to be marked done by a pass that never ran."""
    database = await _library(tmp_path)

    async def unreachable(_content: object, asset_id: str, **_: object) -> object:
        raise media.NoReadableCopy(asset_id)

    monkeypatch.setattr(media, "resolve_decodable", unreachable)
    models = Models()
    service = _service(database=database, models=models)
    try:
        assert await service.read_asset(A_STILL) is None
        assert models.asked == []
        assert await service.read_files() == 0
    finally:
        await database.close()


async def test_nothing_is_opened_while_the_feature_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    async def resolve(_content: object, asset_id: str, **_: object) -> object:
        opened.append(asset_id)
        raise AssertionError("opened a file with the feature off")

    monkeypatch.setattr(media, "resolve_decodable", resolve)
    assert await _service(models=Models(), on=False).read_asset(A_STILL) is None
    assert opened == []
