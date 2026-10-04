# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Stash import files its rows under its own word, never a stash-box's, so nothing that takes
back what a box filed (a refusal, a re-ask, the update's repairs) can reach them."""

from __future__ import annotations

import pytest

from sift.kernel.access.catalog import StillSaid
from sift.kernel.db import Database
from sift.kernel.enrichment import Enricher
from sift.kernel.ledger import Actor
from sift.kernel.vocabulary import VIA_STASH_LIBRARY
from sift.slices.stash_boxes.enrich import IMPORTED, file_writer, filing_as
from sift.slices.stash_boxes.taken_back import TakenBack, left_by_a_refusal, repair, take_off_on
from sift.slices.stash_boxes.tests.test_enrich import _Naming, _writer
from sift.slices.stash_boxes.tests.test_taken_back import _people, _refused_file, _run, db

__all__ = ["db"]

pytestmark = pytest.mark.anyio


async def test_the_import_s_writer_files_under_its_own_word_and_the_box_s_under_its_own() -> None:
    writer, _content, filing, _naming, _reindex = _writer(naming=_Naming({"Known", "Harbor"}))
    enricher = Enricher()
    enricher.register(writer)
    imported = file_writer(filing_as(enricher, VIA_STASH_LIBRARY))
    assert imported is not None and imported is not writer

    values = {"people": ["Known"], "tags": ["Known"], "site": "Harbor"}
    await imported.write("a1", values, creating=False, actor=Actor.sift(VIA_STASH_LIBRARY))
    await writer.write("a2", values, creating=False, actor=Actor.sift("stash"))

    assert filing.attributed == [("id-Known", VIA_STASH_LIBRARY), ("id-Known", IMPORTED)]
    assert filing.tagged == [("id-Known", VIA_STASH_LIBRARY), ("id-Known", IMPORTED)]
    assert filing.filed == [("Harbor", VIA_STASH_LIBRARY), ("Harbor", IMPORTED)]


def test_writers_with_no_file_writer_among_them_are_handed_back_as_they_are() -> None:
    """An import with nothing to file a file by changes nothing about how rows are filed."""
    enricher = Enricher()
    assert filing_as(enricher, VIA_STASH_LIBRARY) is enricher


async def test_a_take_back_and_the_repair_leave_what_a_stash_import_filed(db: Database) -> None:
    await _refused_file(db)
    await _run(
        db,
        "UPDATE asset_people SET source = ? WHERE asset_id = 'file-1' AND person_id = 'p-ilsa'",
        (VIA_STASH_LIBRARY,),
    )

    async with db.write() as connection:
        await take_off_on(connection, "file-1", StillSaid(), TakenBack())
        await repair(connection)

    assert await _people(db, "file-1") == {"p-ilsa", "p-orla"}
    assert await left_by_a_refusal(db) == 0
