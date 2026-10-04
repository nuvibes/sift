# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an import is allowed to do to a tag's record.

The whole of this writer is the difference between a FORM's save and an IMPORT's. The form sends
all three fields every time, so the statement behind it replaces all three; a stash-box sends
whatever it happened to carry, so an import that used the same statement would blank the two fields
it said nothing about. Reading first and writing the merge is what stops that, and there is nothing
on any screen that would say it had happened.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import Repository
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.kernel.records import Subject
from sift.slices.tags_ratings.enrich import TagWriter
from sift.slices.tags_ratings.service import TagService

pytestmark = pytest.mark.anyio


@pytest.fixture
async def writer(temp_db: Database, access: Repository) -> TagWriter:
    await temp_db.initialize_schema()
    return TagWriter(TagService(temp_db, access))


async def _a_tag(temp_db: Database, name: str = "beach") -> str:
    tag_id = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (tag_id, name)
    )
    return tag_id


async def test_it_writes_the_subject_the_seam_asks_it_about(writer: TagWriter) -> None:
    """One writer per subject, and which one it is has to be readable without running it."""
    assert writer.subject is Subject.TAG


async def test_a_tag_nobody_has_described_reads_as_an_empty_record(
    writer: TagWriter, temp_db: Database
) -> None:
    """Absent rather than three nulls. A library of four hundred plain tags would otherwise send
    twelve hundred of them, and absent and null read the same to everything that draws a record."""
    tag = await _a_tag(temp_db)

    assert await writer.current(tag) == {}
    # And a tag that is not there at all reads the same way. An import can be applying a decision
    # taken before somebody deleted the tag it names, and "nothing is written down about this" is
    # the honest answer rather than a fault.
    assert await writer.current(new_id()) == {}


async def test_a_tags_words_are_words_so_an_import_invents_nothing(writer: TagWriter) -> None:
    """The count on the confirm button. A person or a site named by a stash-box may have to be
    created; a tag's category is a word, and there is nothing behind it to make."""
    assert await writer.missing({"category": "Location", "aliases": ["seaside"]}) == ()


async def test_an_import_that_mentions_one_field_leaves_the_other_two_alone(
    writer: TagWriter, temp_db: Database
) -> None:
    """The fault this writer exists to prevent, stated as an assertion.

    The statement behind a tag's record replaces all three fields, because the form that edits it
    sends all three. A stash-box sends what it carries, so writing straight through would blank a
    description somebody typed on the strength of an answer that only knew a category.
    """
    tag = await _a_tag(temp_db)
    await writer.write(
        tag,
        {"description": "Sand.", "category": "Location", "aliases": ["seaside"]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    await writer.write(tag, {"category": "Place"}, creating=False, actor=Actor.sift("stash"))

    assert await writer.current(tag) == {
        "description": "Sand.",
        "category": "Place",
        "aliases": ["seaside"],
    }


async def test_the_list_that_arrives_is_the_whole_list(
    writer: TagWriter, temp_db: Database
) -> None:
    """The plan has already merged what is held with what was offered, so what arrives IS the
    answer. Added to what was read a line ago it would be unioned with itself."""
    tag = await _a_tag(temp_db)
    await writer.write(tag, {"aliases": ["seaside"]}, creating=False, actor=Actor.sift("stash"))

    await writer.write(
        tag, {"aliases": ["seaside", "shore"]}, creating=False, actor=Actor.sift("stash")
    )

    assert await writer.current(tag) == {"aliases": ["seaside", "shore"]}


async def test_an_offered_alias_that_is_only_spaces_is_dropped(
    writer: TagWriter, temp_db: Database
) -> None:
    tag = await _a_tag(temp_db)

    await writer.write(
        tag, {"aliases": ["seaside", "   ", ""]}, creating=False, actor=Actor.sift("stash")
    )

    assert await writer.current(tag) == {"aliases": ["seaside"]}


async def test_an_offer_that_is_not_a_list_leaves_the_aliases_where_they_were(
    writer: TagWriter, temp_db: Database
) -> None:
    """An adapter that answered with a bare string is not a reason to lose what is stored."""
    tag = await _a_tag(temp_db)
    await writer.write(tag, {"aliases": ["seaside"]}, creating=False, actor=Actor.sift("stash"))

    await writer.write(tag, {"aliases": "shore"}, creating=False, actor=Actor.sift("stash"))

    assert await writer.current(tag) == {"aliases": ["seaside"]}


async def test_creating_is_part_of_the_shape_and_changes_nothing_here(
    writer: TagWriter, temp_db: Database
) -> None:
    """It is permission to invent a row from a name, and every field here is a word. Naming it in
    the signature is what keeps the seam one shape for every subject."""
    tag = await _a_tag(temp_db)

    await writer.write(tag, {"description": "Sand."}, creating=True, actor=Actor.sift("stash"))

    assert await writer.current(tag) == {"description": "Sand."}


async def test_an_import_writes_no_edited_event_of_its_own(
    writer: TagWriter, temp_db: Database, access: Repository
) -> None:
    """One press is one line: the ask's `enriched` event is the record, not an `edited` one.

    Through `set_record`, whose `edited` event draws "Edited the aliases", the writer would put
    that beside the box's own "FansDB filled in its aliases" for the same press. A hand edit still
    writes its `edited` event: the two writers differ in the ledger and nowhere else.
    """
    tag = await _a_tag(temp_db)
    await writer.write(
        tag,
        {"aliases": ["seaside"], "category": "Location"},
        creating=False,
        actor=Actor.sift("stash"),
    )
    assert await writer.current(tag) == {"category": "Location", "aliases": ["seaside"]}
    assert await _edited(temp_db, tag) == 0

    await TagService(temp_db, access).set_record(
        tag,
        description=None,
        category="Place",
        aliases=["seaside"],
        parent=None,
        actor=Actor.sift("stash"),
    )
    assert await _edited(temp_db, tag) == 1


async def _edited(temp_db: Database, tag_id: str) -> int:
    row = await temp_db.fetch_one(
        "SELECT COUNT(*) AS n FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'edited' AND s.kind = 'tag' AND s.subject_id = ?",
        (tag_id,),
    )
    assert row is not None
    return int(row["n"])
