# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a reading does to the library, and how it is taken back.

The models are not run here either. What is checked is the half that writes: an exact address files
the file under a site, a name the site already answers to attributes the username, a name it does
not answers nothing and is recorded instead, and every one of those is one receipt on one file that
undoes cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

# Imported for its side effect: the download slice registers `download_sites`, the table
# that remembers which Site each site files under, which a reading reads and writes through the
# kernel (`kernel.access.sites`). Without it a reading files by the word it read.
import sift.slices.download.schema

# Imported for its side effect: the workbench slice registers the ledger's table, which a username
# arriving is written to (`catalog._seed_username_on`). `temp_db.initialize_schema()` creates
# only what is registered, so without this the table would depend on import order in the worker.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import catalog
from sift.kernel.access.catalog import MADE_BY_A_PERSON
from sift.kernel.access.sites import host_key
from sift.kernel.audience import Audience
from sift.kernel.changes import About
from sift.kernel.db import Database
from sift.slices.download.sources.registry import site_key
from sift.slices.watermarks import service as service_module
from sift.slices.watermarks import signatures, weights
from sift.slices.watermarks.queue import WatermarkFilings
from sift.slices.watermarks.service import FROM_WATERMARK, QUEUE, WatermarkService
from sift.slices.watermarks.store import Read, Store

pytestmark = [pytest.mark.anyio]

AN_ASSET = "01HX00000000000000000000W1"
ANOTHER = "01HX00000000000000000000W2"


class Preferences:
    """The settings this feature reads, answered from values a test set."""

    def __init__(self, **values: Any) -> None:
        self._values = values

    async def get_app(self, key: str) -> Any:
        return self._values.get(key)


class Receipts:
    """The board, as this feature writes to it. Keeps what it was handed."""

    def __init__(self) -> None:
        self.written: list[dict[str, Any]] = []

    async def record_on(self, connection: Any, **fields: Any) -> str:
        self.written.append(fields)
        return f"receipt-{len(self.written)}"


async def _library(tmp_path: Path) -> Database:
    database = Database(tmp_path / "marks.sqlite3", readers=1)
    await database.connect()
    await database.initialize_schema()
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('r1', 'r', '/r', 0)"
    )
    for asset_id in (AN_ASSET, ANOTHER):
        # Width and height as well as a present copy: the sweep asks for files it could actually
        # plan crops in, so a file nothing has probed is not a candidate.
        await database.execute(
            "INSERT INTO assets (id, identity, media_type, width, height, added_at)"
            " VALUES (?, ?, 'image', 1920, 1080, 0)",
            (asset_id, f"identity-{asset_id}"),
        )
        await database.execute(
            "INSERT INTO asset_locations"
            " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
            " VALUES (?, ?, 'r1', ?, ?, 'present', 0, 0)",
            (f"loc-{asset_id}", asset_id, f"{asset_id}.jpg", f"{asset_id}.jpg"),
        )
    return database


def _service(database: Database, receipts: Receipts) -> WatermarkService:
    """The feature with only the halves this file exercises wired.

    The content store and the read that decides visibility are genuinely not reached by anything
    here (nothing opens a file and nothing draws one), so they are absent rather than stood in
    for. A stand-in would be a claim that they were used.
    """
    return WatermarkService(
        store=Store(database),
        content=cast(Any, None),
        repository=cast(Any, None),
        settings=cast(Any, None),
        hardware=cast(Any, None),
        preferences=Preferences(**{"watermarks.enabled": True, "watermarks.device": "cpu"}),
        recorder=cast(Any, receipts),
    )


async def _filed_under(database: Database) -> list[tuple[str, str, str | None]]:
    rows = await database.fetch_all(
        "SELECT aa.asset_id AS asset_id, p.name AS site, ac.name AS name"
        " FROM asset_usernames aa JOIN usernames ac ON ac.id = aa.username_id"
        " LEFT JOIN sites p ON p.id = ac.site_id"
        " WHERE aa.source = ? ORDER BY aa.asset_id",
        (FROM_WATERMARK,),
    )
    return [(str(r["asset_id"]), str(r["site"]), str(r["name"])) for r in rows]


async def _kept(database: Database, asset_id: str) -> tuple[str, str, str] | None:
    """What was recorded as read off one file (its letters, kind and site), or None.

    Asked of the table here rather than of the store: nothing in the application reads one file's
    reading back through the feature any more. The file's History reads the table itself.
    """
    row = await database.fetch_one(
        "SELECT text, kind, site FROM watermark_reads WHERE asset_id = ?", (asset_id,)
    )
    return None if row is None else (str(row["text"]), str(row["kind"]), str(row["site"]))


async def test_an_exact_address_files_the_file_under_a_site_that_did_not_exist(
    tmp_path: Path,
) -> None:
    """The site is created the way the file-name reader creates one, and the filing lands on the
    row that means "from here, poster unknown": a filing needs a username to exist at all, and
    inventing one out of a name nothing corroborates is what this feature refuses to do."""
    database = await _library(tmp_path)
    receipts = Receipts()
    service = _service(database, receipts)
    async with database.write() as connection:
        await service._file(connection, asset_id=AN_ASSET, site="Fansly", username="nobody")
    assert await _filed_under(database) == [(AN_ASSET, "Fansly", "")]
    await database.close()


async def test_a_name_the_site_already_answers_to_attributes_that_account(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    async with database.write() as connection:
        await catalog.seed_site_username_on(
            connection, site="OnlyFans", name="riverbend", made=MADE_BY_A_PERSON
        )
    receipts = Receipts()
    service = _service(database, receipts)
    async with database.write() as connection:
        # One letter out, and long enough for one letter to mean something.
        await service._file(connection, asset_id=AN_ASSET, site="OnlyFans", username="riverbcnd")
    assert await _filed_under(database) == [(AN_ASSET, "OnlyFans", "riverbend")]
    assert receipts.written[0]["title"] == "Filed under riverbend on OnlyFans from a watermark"
    await database.close()


async def test_a_name_nothing_on_the_site_answers_to_files_the_site_and_no_account(
    tmp_path: Path,
) -> None:
    """Allowing a name known on some OTHER site to count would file a file under the site its mark
    named while attributing it to a username on a different one."""
    database = await _library(tmp_path)
    async with database.write() as connection:
        await catalog.seed_site_username_on(
            connection, site="Instagram", name="riverbend", made=MADE_BY_A_PERSON
        )
    service = _service(database, Receipts())
    async with database.write() as connection:
        await service._file(connection, asset_id=AN_ASSET, site="OnlyFans", username="riverbend")
    assert await _filed_under(database) == [(AN_ASSET, "OnlyFans", "")]
    await database.close()


async def test_one_receipt_per_file_and_undo_puts_that_one_file_back(tmp_path: Path) -> None:
    """One per file and not one per pass, so the file somebody thinks was misread is the only one
    that goes back. It is also what leaves the SITE and the USERNAME standing: both may hold files
    this pass never touched."""
    database = await _library(tmp_path)
    async with database.write() as connection:
        await catalog.seed_site_username_on(
            connection, site="OnlyFans", name="riverbend", made=MADE_BY_A_PERSON
        )
    receipts = Receipts()
    service = _service(database, receipts)
    for asset_id in (AN_ASSET, ANOTHER):
        async with database.write() as connection:
            await service._file(
                connection, asset_id=asset_id, site="OnlyFans", username="riverbend"
            )
    assert len(receipts.written) == 2
    assert {one["queue"] for one in receipts.written} == {QUEUE}

    reverser = WatermarkFilings(service)
    assert reverser.reversible
    put_back = await reverser.reverse(cast(Any, None), "receipt-1", receipts.written[0]["payload"])
    assert put_back
    assert await _filed_under(database) == [(ANOTHER, "OnlyFans", "riverbend")]
    # The username itself is left standing: it may be somebody's own, and it holds another file.
    assert await database.fetch_one("SELECT id FROM usernames WHERE name = 'riverbend'")
    await database.close()


async def test_a_filing_taken_back_is_not_made_again_by_the_next_pass(tmp_path: Path) -> None:
    """Without the standing refusal the sweep puts back precisely what somebody just removed, with
    nothing on any screen saying why. The file-name reader keeps the same rule."""
    database = await _library(tmp_path)
    receipts = Receipts()
    service = _service(database, receipts)
    async with database.write() as connection:
        await service.store.remember_on(
            connection, asset_id=AN_ASSET, revision="r", identity="identity-" + AN_ASSET, found=1
        )
        await service._file(connection, asset_id=AN_ASSET, site="Fansly", username="nobody")
    assert await reverse_one(service, receipts)
    # Read again from scratch: the file is still not offered, because the refusal outlives the
    # record of having looked.
    assert await service.store.forget_everything() >= 0
    assert await service.store.unread("r", limit=50) == [ANOTHER]
    await database.close()


async def reverse_one(service: WatermarkService, receipts: Receipts) -> bool:
    return await WatermarkFilings(service).reverse(
        cast(Any, None), "receipt-1", receipts.written[0]["payload"]
    )


async def test_an_unreadable_record_puts_nothing_back_rather_than_failing(tmp_path: Path) -> None:
    """A record can outlive the version that wrote it. "Nothing was put back" is the honest
    reading; raising would read as Undo being broken rather than as the record being unreadable."""
    database = await _library(tmp_path)
    reverser = WatermarkFilings(_service(database, Receipts()))
    assert not await reverser.reverse(cast(Any, None), "r", "not json at all")
    assert not await reverser.reverse(cast(Any, None), "r", '{"kind": "something else"}')
    assert not await reverser.reverse(cast(Any, None), "r", '{"kind": "filed", "assets": []}')
    await database.close()


async def test_a_file_is_remembered_as_looked_at_even_when_it_carried_nothing(
    tmp_path: Path,
) -> None:
    """The whole reason there is a record at all: a pass costs the same on an unmarked file, and
    much of a library is unmarked. Without this the sweep opens them all again, for ever."""
    database = await _library(tmp_path)
    store = Store(database)
    assert sorted(await store.unread("r", limit=50)) == [AN_ASSET, ANOTHER]
    async with database.write() as connection:
        await store.remember_on(
            connection, asset_id=AN_ASSET, revision="r", identity="identity-" + AN_ASSET, found=0
        )
    assert await store.unread("r", limit=50) == [ANOTHER]
    # A different set of models has not read it, so it is stale rather than done.
    assert sorted(await store.unread("r2", limit=50)) == [AN_ASSET, ANOTHER]
    await database.close()


async def test_a_file_whose_contents_changed_is_read_again(tmp_path: Path) -> None:
    """A mark is a property of the copy. A file whose bytes changed is a different copy."""
    database = await _library(tmp_path)
    store = Store(database)
    async with database.write() as connection:
        await store.remember_on(
            connection, asset_id=AN_ASSET, revision="r", identity="something else", found=1
        )
    assert AN_ASSET in await store.unread("r", limit=50)
    await database.close()


async def test_what_was_read_is_kept_and_says_whether_it_reached_an_account(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    store = Store(database)
    async with database.write() as connection:
        await store.record_on(
            connection,
            asset_id=AN_ASSET,
            read=Read(
                text="onlyfans.com/riverbend",
                kind=signatures.SITE,
                site="OnlyFans",
                username="riverbend",
                confidence=0.94,
                frame_ms=0,
            ),
        )
    kept = await _kept(database, AN_ASSET)
    assert kept is not None and kept[0] == "onlyfans.com/riverbend"
    # Recorded, and nothing more: writing a reading down files nothing on its own.
    assert await _filed_under(database) == []
    await database.close()


async def test_a_reading_that_filed_nothing_does_not_say_the_file_was_filed(
    tmp_path: Path,
) -> None:
    """THE FILE'S HISTORY SAYS "FILED NOTHING" FROM THIS.

    Only an exact address files anything. A reading a letter or two out, or one whose front a crop
    cut off, is recorded and decides nothing, and it reaches the screen with `matched` false,
    exactly as a reading that DID file under a site with nobody named, so the screen must tell them
    apart or it says the file was filed under a site it has never been near. Such readings are not
    rare, and one can name Fansly for a mark that plainly reads OnlyFans.
    """
    database = await _library(tmp_path)
    receipts = Receipts()
    service = _service(database, receipts)
    lines = ["oniyfans.com/riverbend"]
    mark = signatures.mark_in(lines[0])
    assert mark is not None and not mark.exact
    await service._settle(
        AN_ASSET,
        "identity-" + AN_ASSET,
        mark,
        Read(
            text=mark.text,
            kind=mark.kind,
            site=mark.site or "",
            username=mark.username,
            confidence=0.9,
            frame_ms=0,
        ),
        signatures.tags_in(lines),
    )
    assert await _filed_under(database) == []
    assert await _kept(database, AN_ASSET) == (
        "oniyfans.com/riverbend",
        signatures.SITE,
        "OnlyFans",
    )
    await database.close()


async def test_a_reading_that_filed_under_a_site_with_nobody_named_says_so(tmp_path: Path) -> None:
    """The other half of the pair: an exact address with a name nothing answers to files the file
    under the site itself."""
    database = await _library(tmp_path)
    receipts = Receipts()
    service = _service(database, receipts)
    lines = ["onlyfans.com/riverbend"]
    mark = signatures.mark_in(lines[0])
    assert mark is not None and mark.exact
    await service._settle(
        AN_ASSET,
        "identity-" + AN_ASSET,
        mark,
        Read(
            text=mark.text,
            kind=mark.kind,
            site=mark.site or "",
            username=mark.username,
            confidence=0.99,
            frame_ms=0,
        ),
        signatures.tags_in(lines),
    )
    # Under the site itself, through its "poster unknown" row (the empty name), and nobody.
    assert await _filed_under(database) == [(AN_ASSET, "OnlyFans", "")]
    await database.close()


# --- what each kind of mark may write ---------------------------------------------------------


async def _tags_on(database: Database, asset_id: str) -> list[tuple[str, str | None]]:
    rows = await database.fetch_all(
        "SELECT t.name AS name, at.source AS source"
        " FROM asset_tags at JOIN tags t ON t.id = at.tag_id"
        " WHERE at.asset_id = ? ORDER BY t.name",
        (asset_id,),
    )
    return [(str(r["name"]), None if r["source"] is None else str(r["source"])) for r in rows]


async def test_a_distributors_band_files_the_file_under_onlyfans_and_tags_nothing(
    tmp_path: Path,
) -> None:
    """A distributor's band files the file, driven through the write that makes it. The band names
    nobody, so the filing lands on the row that means "from here, poster unknown": the same path
    an exact `onlyfans.com` with no readable username takes, and it is one receipt on one file, so
    it undoes like any other. It writes no tag."""
    database = await _library(tmp_path)
    receipts = Receipts()
    service = _service(database, receipts)
    lines = ["DMCA PROTECTED CONTENT"]
    mark = signatures.mark_in(lines[0])
    assert mark is not None
    await service._settle(
        AN_ASSET,
        "identity-" + AN_ASSET,
        mark,
        Read(
            text=mark.text,
            kind=mark.kind,
            site=mark.site or "",
            username=None,
            confidence=0.9,
            frame_ms=0,
        ),
        signatures.tags_in(lines),
    )
    assert await _tags_on(database, AN_ASSET) == []
    assert await _filed_under(database) == [(AN_ASSET, "OnlyFans", "")]
    assert receipts.written[0]["title"] == "Filed under OnlyFans from a watermark"
    assert receipts.written[0]["queue"] == QUEUE
    # And it is recorded as read, under the site it was filed under rather than an empty column.
    kept = await _kept(database, AN_ASSET)
    assert kept is not None and (kept[1], kept[2]) == (signatures.NOTICE, "OnlyFans")
    await database.close()


async def test_a_band_filing_is_taken_back_one_file_at_a_time(tmp_path: Path) -> None:
    """The Undo the receipt above carries, exercised. A band files under a site with no username, so
    the take-back has only the site to go on and finds the "poster unknown" row from it: the case
    the receipt carries a `site` for."""
    database = await _library(tmp_path)
    service = _service(database, Receipts())
    lines = ["DMCA PROTECTED CONTENT"]
    mark = signatures.mark_in(lines[0])
    assert mark is not None
    await service._settle(
        AN_ASSET,
        "identity-" + AN_ASSET,
        mark,
        Read(
            text=mark.text,
            kind=mark.kind,
            site=mark.site or "",
            username=None,
            confidence=0.9,
            frame_ms=0,
        ),
        signatures.tags_in(lines),
    )
    assert await service.take_back(username_id=None, site="OnlyFans", asset_ids=[AN_ASSET])
    assert await _filed_under(database) == []
    assert await _kept(database, AN_ASSET) is None
    await database.close()


async def test_a_telegram_address_tags_the_file_as_a_mirror_and_writes_no_site(
    tmp_path: Path,
) -> None:
    """A channel is not a site Sift files under and its name is not a person's."""
    database = await _library(tmp_path)
    service = _service(database, Receipts())
    lines = ["t.me/quietharbour"]
    await service._settle(
        AN_ASSET,
        "identity-" + AN_ASSET,
        signatures.best_mark(lines),
        Read(
            text="t.me/quietharbour",
            kind=signatures.CHANNEL,
            site="",
            username="quietharbour",
            confidence=0.9,
            frame_ms=0,
        ),
        signatures.tags_in(lines),
    )
    assert await _tags_on(database, AN_ASSET) == [("Telegram mirror", FROM_WATERMARK)]
    assert await _filed_under(database) == []
    assert await database.fetch_all("SELECT id FROM sites") == []
    await database.close()


async def test_a_band_beside_an_address_is_filed_once_under_the_address(tmp_path: Path) -> None:
    """A band that loses the ranking writes nothing at all.

    A band files, and one copy is filed under one site, so on a frame carrying both, the address
    decides and the band writes nothing at all. Here the two agree, which is the ordinary case;
    where they did not, the address is the one read off THIS copy.

    The case is rare, so it is constructed rather than waited for, and that is exactly why it is
    written down."""
    database = await _library(tmp_path)
    service = _service(database, Receipts())
    lines = ["DMCAPROTECTEDCONTENT", "onlyfans.com/quietharbour"]
    mark = signatures.best_mark(lines)
    assert mark is not None and mark.kind == signatures.SITE
    await service._settle(
        AN_ASSET,
        "identity-" + AN_ASSET,
        mark,
        Read(
            text=mark.text,
            kind=mark.kind,
            site=mark.site or "",
            username=mark.username,
            confidence=0.9,
            frame_ms=0,
        ),
        signatures.tags_in(lines),
    )
    assert await _tags_on(database, AN_ASSET) == []
    assert await _filed_under(database) == [(AN_ASSET, "OnlyFans", "")]
    await database.close()


async def test_a_channel_beside_an_address_still_tags_the_file(tmp_path: Path) -> None:
    """The case the separate read of the tags is FOR, a band being no tag. A mirror's address is a
    fact about the copy standing beside the filing rather than a rival claim about where it came
    from, so the file truthfully carries both."""
    database = await _library(tmp_path)
    service = _service(database, Receipts())
    lines = ["t.me/quietharbour", "onlyfans.com/quietharbour"]
    mark = signatures.best_mark(lines)
    assert mark is not None and mark.kind == signatures.SITE
    await service._settle(
        AN_ASSET,
        "identity-" + AN_ASSET,
        mark,
        Read(
            text=mark.text,
            kind=mark.kind,
            site=mark.site or "",
            username=mark.username,
            confidence=0.9,
            frame_ms=0,
        ),
        signatures.tags_in(lines),
    )
    assert await _tags_on(database, AN_ASSET) == [("Telegram mirror", FROM_WATERMARK)]
    assert await _filed_under(database) == [(AN_ASSET, "OnlyFans", "")]
    await database.close()


async def test_a_bare_handle_is_attributed_when_exactly_one_site_answers_to_it(
    tmp_path: Path,
) -> None:
    """The mark says who and not where, so the library supplies the where, and only where it
    supplies one answer. No site is invented and no username is: those are the one fact the mark
    does not carry."""
    database = await _library(tmp_path)
    async with database.write() as connection:
        await catalog.seed_site_username_on(
            connection, site="Fansly", name="quietharbour", made=MADE_BY_A_PERSON
        )
    receipts = Receipts()
    service = _service(database, receipts)
    async with database.write() as connection:
        await service._attribute_bare_username(
            connection, asset_id=AN_ASSET, username="quietharbour"
        )
    assert await _filed_under(database) == [(AN_ASSET, "Fansly", "quietharbour")]
    assert receipts.written[0]["title"] == "Filed under quietharbour on Fansly from a watermark"
    await database.close()


async def test_a_bare_handle_known_on_two_sites_attributes_neither(tmp_path: Path) -> None:
    """A coin toss is not an attribution. It is the same answer `nearest_username` gives to two
    usernames one edit apart, and it is given for the same reason."""
    database = await _library(tmp_path)
    async with database.write() as connection:
        for site in ("Fansly", "OnlyFans"):
            await catalog.seed_site_username_on(
                connection, site=site, name="quietharbour", made=MADE_BY_A_PERSON
            )
    receipts = Receipts()
    service = _service(database, receipts)
    async with database.write() as connection:
        await service._attribute_bare_username(
            connection, asset_id=AN_ASSET, username="quietharbour"
        )
    assert await _filed_under(database) == []
    assert receipts.written == []
    await database.close()


async def test_a_bare_handle_nothing_answers_to_invents_nothing(tmp_path: Path) -> None:
    """The filing above reads an address, so it knows the site even when the name is new and can
    create both. This one knows neither."""
    database = await _library(tmp_path)
    service = _service(database, Receipts())
    async with database.write() as connection:
        await service._attribute_bare_username(
            connection, asset_id=AN_ASSET, username="quietharbour"
        )
    assert await _filed_under(database) == []
    assert await database.fetch_all("SELECT id FROM sites") == []
    await database.close()


async def test_a_bare_handle_one_letter_out_is_not_attributed(tmp_path: Path) -> None:
    """None of the one-edit tolerance the marks that carry an address get: those earn it from the
    address beside them, and a name a letter out beside nothing at all is a different name."""
    database = await _library(tmp_path)
    async with database.write() as connection:
        await catalog.seed_site_username_on(
            connection, site="Fansly", name="quietharbour", made=MADE_BY_A_PERSON
        )
    service = _service(database, Receipts())
    async with database.write() as connection:
        await service._attribute_bare_username(
            connection, asset_id=AN_ASSET, username="quietharbourn"
        )
    assert await _filed_under(database) == []
    await database.close()


async def test_a_file_already_wearing_the_tag_does_not_gain_it_twice(tmp_path: Path) -> None:
    """A second look at the same file reads the same mirror. `OR IGNORE` is what makes that a no-op
    rather than an error, and it keeps the FIRST answer to when it was decided."""
    database = await _library(tmp_path)
    service = _service(database, Receipts())
    async with database.write() as connection:
        first = await service.store.tag_on(
            connection,
            asset_id=AN_ASSET,
            tag="Telegram mirror",
            tag_id="tag-one",
            source=FROM_WATERMARK,
        )
        again = await service.store.tag_on(
            connection,
            asset_id=AN_ASSET,
            tag="Telegram mirror",
            tag_id="tag-two",
            source=FROM_WATERMARK,
        )
    assert first and not again
    assert await _tags_on(database, AN_ASSET) == [("Telegram mirror", FROM_WATERMARK)]
    rows = await database.fetch_all("SELECT id, created_by_via FROM tags")
    assert [(str(r["id"]), str(r["created_by_via"])) for r in rows] == [("tag-one", "watermark")]
    await database.close()


async def test_a_file_with_no_mark_on_it_is_settled_as_looked_at(tmp_path: Path) -> None:
    """The whole reason there is a record at all, driven through the write that makes it rather
    than through the store underneath: a pass costs the same on an unmarked file, and much of a
    library is unmarked. Recording only the files that FOUND something is what would have the sweep
    open every unmarked file again on every pass, for ever."""
    database = await _library(tmp_path)
    service = _service(database, Receipts())
    await service._settle(AN_ASSET, "identity-" + AN_ASSET, None, None, [])
    rows = await database.fetch_all("SELECT asset_id, found FROM watermark_scans")
    assert [(str(r["asset_id"]), int(r["found"])) for r in rows] == [(AN_ASSET, 0)]
    assert await service.store.unread(weights.REVISION, limit=50) == [ANOTHER]
    await database.close()


async def test_a_filing_with_no_poster_named_says_what_it_was_filed_under(tmp_path: Path) -> None:
    """The receipt's object is the site's "poster unknown" row, the one the filing's own line is
    keyed by, so a file's pane folds the two into one line. Without an object the file would say
    "Filed under OnlyFans from a watermark" twice."""
    database = await _library(tmp_path)
    try:
        receipts = Receipts()
        service = _service(database, receipts)
        async with database.write() as connection:
            await service._file(connection, asset_id=AN_ASSET, site="Fansly", username="nobody")
        unknown = await database.fetch_one(
            "SELECT ac.id AS id FROM usernames ac JOIN sites p ON p.id = ac.site_id"
            " WHERE p.name = 'Fansly' AND ac.name = ''"
        )
        assert unknown is not None
        (written,) = receipts.written
        assert written["object"] is not None
        assert (written["object"].kind, written["object"].id, written["object"].name) == (
            "username",
            str(unknown["id"]),
            "Fansly",
        )
    finally:
        await database.close()


async def test_a_file_somebody_already_filed_under_that_name_gets_no_receipt(
    tmp_path: Path,
) -> None:
    """The insert leaves a person's own filing alone, and a receipt for a row this pass did not
    write would let an Undo delete somebody else's filing."""
    database = await _library(tmp_path)
    try:
        async with database.write() as connection:
            _, username_id = await catalog.seed_site_username_on(
                connection, site="OnlyFans", name="riverbend", made=MADE_BY_A_PERSON
            )
            await catalog.link_username_to_asset_on(
                connection, asset_id=AN_ASSET, username_id=username_id
            )
        receipts = Receipts()
        service = _service(database, receipts)
        async with database.write() as connection:
            await service._file(
                connection, asset_id=AN_ASSET, site="OnlyFans", username="riverbend"
            )
        assert receipts.written == []
        assert await _filed_under(database) == []
    finally:
        await database.close()


async def test_a_bare_username_already_filed_by_a_person_gets_no_receipt(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        async with database.write() as connection:
            _, username_id = await catalog.seed_site_username_on(
                connection, site="Fansly", name="quietharbour", made=MADE_BY_A_PERSON
            )
            await catalog.link_username_to_asset_on(
                connection, asset_id=AN_ASSET, username_id=username_id
            )
        receipts = Receipts()
        service = _service(database, receipts)
        async with database.write() as connection:
            await service._attribute_bare_username(
                connection, asset_id=AN_ASSET, username="quietharbour"
            )
        assert receipts.written == []
    finally:
        await database.close()


async def test_a_reading_that_is_only_a_username_is_attributed_through_the_library(
    tmp_path: Path,
) -> None:
    """Settled as a whole: the reading is kept, and the one site that knows the name supplies the
    where the mark did not carry."""
    database = await _library(tmp_path)
    try:
        async with database.write() as connection:
            await catalog.seed_site_username_on(
                connection, site="Fansly", name="quietharbour", made=MADE_BY_A_PERSON
            )
        service = _service(database, Receipts())
        mark = signatures.mark_in("@quietharbour")
        assert mark is not None and mark.kind == signatures.USERNAME
        found = Read(
            text=mark.text,
            kind=mark.kind,
            site="",
            username=mark.username,
            confidence=0.9,
            frame_ms=0,
        )
        await service._settle(AN_ASSET, "identity-" + AN_ASSET, mark, found, [])
        assert await _filed_under(database) == [(AN_ASSET, "Fansly", "quietharbour")]
        assert await _kept(database, AN_ASSET) == ("@quietharbour", signatures.USERNAME, "")
    finally:
        await database.close()


async def test_taking_back_names_nothing_puts_nothing_back(tmp_path: Path) -> None:
    """No files, or no username and no site to find one by: nothing to reverse, and saying so."""
    database = await _library(tmp_path)
    try:
        service = _service(database, Receipts())
        assert not await service.take_back(username_id=None, site="Fansly", asset_ids=[])
        assert not await service.take_back(username_id=None, site=None, asset_ids=[AN_ASSET])
    finally:
        await database.close()


async def test_taking_back_a_filing_that_is_not_there_refuses_nothing(tmp_path: Path) -> None:
    """The standing refusal is what stops the next pass putting a filing back. Writing one for a
    file this pass never filed would stop it ever being read for a mark again."""
    database = await _library(tmp_path)
    try:
        async with database.write() as connection:
            _, username_id = await catalog.seed_site_username_on(
                connection, site="Fansly", name="quietharbour", made=MADE_BY_A_PERSON
            )
        service = _service(database, Receipts())
        assert not await service.take_back(
            username_id=username_id, site="Fansly", asset_ids=[AN_ASSET]
        )
        assert await database.fetch_all("SELECT asset_id FROM watermark_refusals") == []
    finally:
        await database.close()


class Visibility:
    """The read that decides what a user may be shown, answering from a set a test chose."""

    def __init__(self, visible: set[str]) -> None:
        self.visible = visible
        self.asked: list[str] = []

    async def can_view(self, viewer: Any, asset_id: str) -> bool:
        self.asked.append(asset_id)
        return asset_id in self.visible


async def test_a_record_draws_only_the_files_its_reader_may_still_see(tmp_path: Path) -> None:
    """A file restricted since the decision is one this user may no longer be shown, and a record
    of having filed it is not a licence to draw it."""
    database = await _library(tmp_path)
    try:
        service = _service(database, Receipts())
        service._repository = cast(Any, Visibility({ANOTHER}))
        payload = json.dumps({"kind": "filed", "site": "Fansly", "assets": [AN_ASSET, ANOTHER]})
        drawn = await WatermarkFilings(service).pictures_of(cast(Any, None), payload)
        assert [(one.kind, one.id, one.href) for one in drawn] == [
            ("asset", ANOTHER, f"/asset/{ANOTHER}")
        ]
    finally:
        await database.close()


async def test_a_record_naming_no_files_draws_nothing_and_asks_nothing(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        service = _service(database, Receipts())
        visibility = Visibility(set())
        service._repository = cast(Any, visibility)
        assert await WatermarkFilings(service).pictures_of(cast(Any, None), "not json") == ()
        assert visibility.asked == []
    finally:
        await database.close()


async def test_a_filing_taken_back_tells_every_admin_and_whoever_the_site_is_shared_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A username's wall and its Site draw the files an Undo takes off, so the take-back tells
    every admin and the guest the Site is shared with, whose cache stamp moves in the same
    write."""
    database = await _library(tmp_path)
    service = _service(database, Receipts())
    lines = ["DMCA PROTECTED CONTENT"]
    mark = signatures.mark_in(lines[0])
    assert mark is not None
    await service._settle(
        AN_ASSET,
        "identity-" + AN_ASSET,
        mark,
        Read(
            text=mark.text,
            kind=mark.kind,
            site=mark.site or "",
            username=None,
            confidence=0.9,
            frame_ms=0,
        ),
        signatures.tags_in(lines),
    )
    site = await database.fetch_one("SELECT id FROM sites WHERE name = 'OnlyFans'")
    assert site is not None
    await database.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at)"
        " VALUES ('guest-1', 'orla', 'x', 'guest', 0)",
        (),
    )
    await database.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES ('grant-1', 'site', ?, 'guest-1', 'share', 0)",
        (str(site["id"]),),
    )
    told: list[tuple[Audience, object]] = []
    monkeypatch.setattr(service_module, "announce", lambda who, about: told.append((who, about)))

    assert await service.take_back(username_id=None, site="OnlyFans", asset_ids=[AN_ASSET])

    assert len(told) == 1
    who, about = told[0]
    assert about == About.LIBRARY
    assert who.every_admin and who.users == frozenset({"guest-1"})
    stamp = await database.fetch_one("SELECT cache_stamp FROM users WHERE id = 'guest-1'")
    assert stamp is not None and int(stamp["cache_stamp"]) == 1
    await database.close()


async def test_a_reading_keeps_the_site_it_filed_under_by_id(tmp_path: Path) -> None:
    """The reading's way to its Site is the Site's id, so renaming the Site keeps the link.

    Joined by the word the reading kept, a Site renamed from `OnlyFans` would stop being the Site
    the file's History line led to. The id is written with the filing, in the same transaction.
    """
    database = await _library(tmp_path)
    service = _service(database, Receipts())
    mark = signatures.mark_in("onlyfans.com/riverbend")
    assert mark is not None and mark.exact
    read = Read(
        text=mark.text,
        kind=mark.kind,
        site=mark.site or "",
        username=mark.username,
        confidence=0.99,
        frame_ms=0,
    )
    await service._settle(AN_ASSET, "identity-" + AN_ASSET, mark, read, [])
    site = await database.fetch_one("SELECT id FROM sites WHERE name = 'OnlyFans'")
    assert site is not None
    await database.execute("UPDATE sites SET name = 'Riverbend Home' WHERE id = ?", (site["id"],))
    kept = await database.fetch_one(
        "SELECT site, site_id FROM watermark_reads WHERE asset_id = ?", (AN_ASSET,)
    )
    assert kept is not None
    assert kept["site_id"] == site["id"]
    # The word read stays what was read.
    assert kept["site"] == "OnlyFans"
    await database.close()


async def _settle_reading(service: WatermarkService, asset_id: str, line: str) -> None:
    mark = signatures.mark_in(line)
    assert mark is not None and mark.exact
    read = Read(
        text=mark.text,
        kind=mark.kind,
        site=mark.site or "",
        username=mark.username,
        confidence=0.99,
        frame_ms=0,
    )
    await service._settle(asset_id, "identity-" + asset_id, mark, read, [])


async def test_a_renamed_site_takes_the_next_reading_and_no_second_site_is_made(
    tmp_path: Path,
) -> None:
    """The Site a reading filed under is remembered by the site's key, so after a rename the next
    reading files under the renamed Site. Looked up by the word the mark reads as, it would make a
    second `OnlyFans` beside the renamed one."""
    database = await _library(tmp_path)
    try:
        service = _service(database, Receipts())
        await _settle_reading(service, AN_ASSET, "onlyfans.com/riverbend")
        site = await database.fetch_one("SELECT id FROM sites WHERE name = 'OnlyFans'")
        assert site is not None
        await database.execute(
            "UPDATE sites SET name = 'Riverbend Home' WHERE id = ?", (site["id"],)
        )

        await _settle_reading(service, ANOTHER, "onlyfans.com/riverbend")
        sites = await database.fetch_all("SELECT id, name FROM sites")
        assert [(row["id"], row["name"]) for row in sites] == [(site["id"], "Riverbend Home")]
        assert await _filed_under(database) == [
            (AN_ASSET, "Riverbend Home", ""),
            (ANOTHER, "Riverbend Home", ""),
        ]
        kept = await database.fetch_one(
            "SELECT site, site_id FROM watermark_reads WHERE asset_id = ?", (ANOTHER,)
        )
        assert kept is not None and (kept["site"], kept["site_id"]) == ("OnlyFans", site["id"])
    finally:
        # Closed whatever the outcome: an open reader thread keeps the run from ending.
        await database.close()


@pytest.mark.parametrize(("site", "host"), signatures.SIGNATURES)
def test_a_marks_site_is_keyed_as_a_download_from_its_address_is(site: str, host: str) -> None:
    """One key per site, whichever pass met it first: a download from the address and a reading of
    it find the same Site."""
    assert service_module._HOSTS[site] == host
    assert host_key(host) == site_key(f"https://{host}/someone")


async def test_a_site_with_no_address_of_its_own_is_filed_by_its_word_and_keeps_no_key(
    tmp_path: Path,
) -> None:
    """A band's site is data, and a band can name a site no address is known for. Such a site is
    filed under by the word it is named with, whether or not a username is read beside it, and no
    key is kept for it: there is no address for a later download or reading to find it by."""
    database = await _library(tmp_path)
    try:
        async with database.write() as connection:
            await catalog.seed_site_username_on(
                connection, site="Elsewhere", name="riverbend", made=MADE_BY_A_PERSON
            )
        service = _service(database, Receipts())
        assert "Elsewhere" not in service_module._HOSTS

        async with database.write() as connection:
            await service._file(connection, asset_id=AN_ASSET, site="Elsewhere", username=None)
            await service._file(
                connection, asset_id=ANOTHER, site="Elsewhere", username="riverbend"
            )

        assert await _filed_under(database) == [
            (AN_ASSET, "Elsewhere", ""),
            (ANOTHER, "Elsewhere", "riverbend"),
        ]
        assert await database.fetch_all("SELECT key FROM download_sites") == []
    finally:
        await database.close()


async def test_a_filing_taken_back_from_a_username_whose_site_is_gone_tells_every_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Site was deleted after the filing, which leaves its usernames on no Site. The take-back
    still works and tells every admin; there is no Site whose sharing could widen who is told."""
    database = await _library(tmp_path)
    try:
        service = _service(database, Receipts())
        async with database.write() as connection:
            _, username_id = await catalog.seed_site_username_on(
                connection, site="OnlyFans", name="riverbend", made=MADE_BY_A_PERSON
            )
            await catalog.link_username_to_asset_on(
                connection, asset_id=ANOTHER, username_id=username_id, source=FROM_WATERMARK
            )
        await database.execute("DELETE FROM sites WHERE name = 'OnlyFans'")
        told: list[tuple[Audience, object]] = []
        monkeypatch.setattr(
            service_module, "announce", lambda who, about: told.append((who, about))
        )

        assert await service.take_back(username_id=username_id, site=None, asset_ids=[ANOTHER])

        assert len(told) == 1
        who, about = told[0]
        assert about == About.LIBRARY
        assert who.every_admin and who.users == frozenset()
    finally:
        await database.close()
