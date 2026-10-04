# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the catch-up pass over the library asks this feature, and the two answers agreeing.

The Identify stage has one button that goes over the files already here for each of its switches,
and this pass is one of them: without it a library with the feature on would tell the truth only
about files that had ARRIVED since it was turned on. The two answers below are what a stage's run
needs from this feature: how many files lack a reading, and which of THESE files lack one. The
third thing a run asks, whether the work is wanted at all, is the import policy's and is
deliberately not answered twice.

**The last two are one rule written twice and that is what these tests are really for.** The count
is a condition the content store evaluates over the whole library; the page question is a statement
in the access layer. A screen that counted a different population from the one the pass claims would
report work that never arrives, and nothing else in the tree would notice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from sift.kernel import media
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.slices.watermarks import settings as watermark_settings
from sift.slices.watermarks import weights
from sift.slices.watermarks.service import WatermarkService
from sift.slices.watermarks.store import Store

pytestmark = [pytest.mark.anyio]

READ_ALREADY = "01HX0000000000000000000CU1"
NEVER_READ = "01HX0000000000000000000CU2"
READ_BY_AN_OLDER_MODEL = "01HX0000000000000000000CU3"
REFUSED = "01HX0000000000000000000CU4"
COPY_HAS_GONE = "01HX0000000000000000000CU5"

EVERY_FILE = (READ_ALREADY, NEVER_READ, READ_BY_AN_OLDER_MODEL, REFUSED, COPY_HAS_GONE)


class Preferences:
    """The settings this feature reads, answered from values a test set."""

    def __init__(self, **values: Any) -> None:
        self._values = values

    async def get_app(self, key: str) -> Any:
        return self._values.get(key)


class ModelsOnDisk:
    """The models as `ready()` reads them. Nothing is loaded and nothing is run.

    A stand-in rather than the real `Reader` because none of this opens a file: what is being
    checked is which files a pass would be given, which is decided entirely in SQL.
    """

    def __init__(self, *, installed: bool = True, broken: str | None = None) -> None:
        self._installed = installed
        self.broken = broken

    def installed(self) -> bool:
        return self._installed

    def unload(self) -> None:
        return None


async def _library(tmp_path: Path) -> Database:
    """Five files, one of each state the two answers have to agree about."""
    database = Database(tmp_path / "catchup.sqlite3", readers=1)
    await database.connect()
    await database.initialize_schema()
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('r1', 'r', '/r', 0)"
    )
    for asset_id in EVERY_FILE:
        await database.execute(
            "INSERT INTO assets (id, identity, media_type, width, height, added_at, probed_at)"
            " VALUES (?, ?, 'image', 1920, 1080, 0, 1)",
            (asset_id, f"identity-{asset_id}"),
        )
        # The one whose copy has gone still has a row: a location that is not `present` is what a
        # file on a share nobody has plugged in looks like.
        await database.execute(
            "INSERT INTO asset_locations"
            " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
            " VALUES (?, ?, 'r1', ?, ?, ?, 0, 0)",
            (
                f"loc-{asset_id}",
                asset_id,
                f"{asset_id}.jpg",
                f"{asset_id}.jpg",
                "missing" if asset_id == COPY_HAS_GONE else "present",
            ),
        )
    await database.execute(
        "INSERT INTO watermark_scans (asset_id, revision, identity, found, scanned_at)"
        " VALUES (?, ?, ?, 0, 0)",
        (READ_ALREADY, weights.REVISION, f"identity-{READ_ALREADY}"),
    )
    await database.execute(
        "INSERT INTO watermark_scans (asset_id, revision, identity, found, scanned_at)"
        " VALUES (?, 'an-older-model', ?, 0, 0)",
        (READ_BY_AN_OLDER_MODEL, f"identity-{READ_BY_AN_OLDER_MODEL}"),
    )
    await database.execute(
        "INSERT INTO watermark_refusals (asset_id, created_at) VALUES (?, 0)", (REFUSED,)
    )
    return database


def _service(
    database: Database, *, on: bool = True, read_on_import: bool = True, models: Any = None
) -> WatermarkService:
    return WatermarkService(
        store=Store(database),
        content=cast(Any, None),
        repository=cast(Any, None),
        settings=cast(Any, None),
        hardware=cast(Any, None),
        preferences=Preferences(
            **{
                watermark_settings.ENABLED_KEY: on,
                watermark_settings.DEVICE_KEY: "cpu",
                watermark_settings.READ_ON_IMPORT_KEY: read_on_import,
            }
        ),
        reader=cast(Any, models if models is not None else ModelsOnDisk()),
    )


async def _counted_by_the_term(database: Database, service: WatermarkService) -> int:
    """How many files the count's own term is true of, asked of the real content store.

    Through `count_lacking` rather than by pasting the condition into a statement of the test's own:
    a test that builds its own SQL around a term is asserting against its own evaluator, so a term
    that the content store would wrap differently, or refuse, would still pass here. The
    settings are genuinely not reached: this one method touches no path.
    """
    lack = await service.lack()
    assert lack is not None
    counted = await ContentStore(database, cast(Any, None)).count_lacking([lack])
    return counted.files


async def test_nothing_is_lacking_while_the_feature_is_off(tmp_path: Path) -> None:
    """Off means no pass, so no file is waiting for one: the same answer the two beside it give.

    `read_on_import` is a different question and is deliberately not asked here: a person may untick
    the row for one run, and a row that could not be ticked at all is what "off" means.
    """
    database = await _library(tmp_path)
    try:
        service = _service(database, on=False)
        assert await service.lack() is None
        assert await service.unread_among(EVERY_FILE) == set()
    finally:
        await database.close()


async def test_nothing_is_lacking_while_the_models_have_not_been_obtained(tmp_path: Path) -> None:
    """Sift ships no models, so a fresh install has the feature on and nothing to read with.

    Counting the whole library as waiting there would put a number under the button that no press
    could ever bring down.
    """
    database = await _library(tmp_path)
    try:
        service = _service(database, models=ModelsOnDisk(installed=False))
        assert await service.lack() is None
        assert await service.unread_among(EVERY_FILE) == set()
    finally:
        await database.close()


async def test_a_device_that_stopped_answering_leaves_nothing_lacking(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        service = _service(database, models=ModelsOnDisk(broken="the card stopped answering"))
        assert await service.lack() is None
        assert await service.unread_among(EVERY_FILE) == set()
    finally:
        await database.close()


async def test_a_page_holds_exactly_the_files_a_pass_would_open(tmp_path: Path) -> None:
    """Read at this revision: no. Read by an older one: yes, because the models changed under it.
    Refused by an Undo: no, or the next run puts back what somebody just took off. No present
    copy: no, because there is nothing to open."""
    database = await _library(tmp_path)
    try:
        service = _service(database)
        assert await service.unread_among(EVERY_FILE) == {NEVER_READ, READ_BY_AN_OLDER_MODEL}
    finally:
        await database.close()


async def test_a_file_whose_bytes_changed_is_read_again(tmp_path: Path) -> None:
    """The scan row remembers WHAT was read as well as when. A re-encode in place is a different
    picture, so a mark that was not there before can be, and one that was can have gone."""
    database = await _library(tmp_path)
    try:
        service = _service(database)
        assert READ_ALREADY not in await service.unread_among(EVERY_FILE)
        await database.execute(
            "UPDATE assets SET identity = 'something-else' WHERE id = ?", (READ_ALREADY,)
        )
        assert READ_ALREADY in await service.unread_among(EVERY_FILE)
    finally:
        await database.close()


async def test_an_empty_page_asks_the_database_nothing(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        assert await _service(database).unread_among([]) == set()
    finally:
        await database.close()


async def test_the_count_and_the_page_agree_about_every_file_that_can_be_opened(
    tmp_path: Path,
) -> None:
    """THE ONE THIS FILE EXISTS FOR. Two statements, in two layers, for one rule.

    They agree exactly, the file whose copy has gone included: a file with no copy present is not
    work for either of them, and it becomes work again the moment its copy is back.
    """
    database = await _library(tmp_path)
    try:
        service = _service(database)
        counted = await _counted_by_the_term(database, service)
        paged = await service.unread_among(EVERY_FILE)
        assert paged == {NEVER_READ, READ_BY_AN_OLDER_MODEL}
        assert counted == len(paged)
    finally:
        await database.close()


async def test_a_file_read_with_no_picture_in_it_is_given_up_on_and_leaves_the_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read, and the reader found no picture in it: a GIF the decoder cannot open. No
    later pass does better, so it is written down as given up on and the Identify row stops
    saying it is waiting. A file not read yet is left for the scan that reads it."""
    from types import SimpleNamespace

    from sift.slices.watermarks import service as marks

    written: list[tuple[str, str, str, bool]] = []

    class Content:
        async def record_verdict(
            self, asset_id: str, product: str, *, code: str, reason: str, transient: bool = False
        ) -> None:
            written.append((asset_id, str(product), code, transient))

    database = await _library(tmp_path)
    service = _service(database)
    service._content = cast(Any, Content())
    probed: dict[str, int | None] = {NEVER_READ: 1, READ_BY_AN_OLDER_MODEL: None}

    async def resolve(_content: object, asset_id: str, **_: object) -> object:
        asset = SimpleNamespace(width=0, height=0, probed_at=probed[asset_id])
        return SimpleNamespace(asset=asset, path=tmp_path / f"{asset_id}.webp")

    monkeypatch.setattr(media, "resolve_decodable", resolve)
    try:
        assert await service.read_asset(NEVER_READ) is None
        assert await service.read_asset(READ_BY_AN_OLDER_MODEL) is None
    finally:
        await database.close()

    assert written == [(NEVER_READ, marks.PRODUCT, "no_picture", False)]
