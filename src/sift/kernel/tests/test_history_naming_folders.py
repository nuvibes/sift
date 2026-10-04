# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happened to one file, in order.

The read joins seven sources never meant to be read together, so most of what can go wrong is a
row in the wrong order, under the wrong actor or in the wrong time unit; each is asserted on a
file that carries one of everything. The face tables keep MILLISECONDS where every other decision
is in seconds, so a history that forgot to divide would sort every face event last.
"""

from __future__ import annotations

import pytest

# Imported for their side effect: registering the tables a feature owns, so a kernel database has
# them. The application always does (every install creates them whether or not the feature is
# switched on), and the reads guarded on those tables have the other case proved by dropping them.
import sift.slices.download.schema
import sift.slices.faces.schema
import sift.slices.media_edit.schema
import sift.slices.organize.schema
import sift.slices.semantic.schema
import sift.slices.stash_boxes.schema
import sift.slices.suggestions.schema
import sift.slices.watermarks.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository

# The wording lives next door: one table for all three histories. See `sentences.py`.
from sift.kernel.db import Database
from sift.kernel.tests.test_history import make_file, name_person
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio

#: A fixed clock, so every assertion below is about order rather than about when the test ran.
ADDED_AT = 1_700_000_000
NAMED_AT = ADDED_AT + 100
TAGGED_AT = ADDED_AT + 200
FILED_AT = ADDED_AT + 300
ENRICHED_AT = ADDED_AT + 400
SCANNED_AT = ADDED_AT + 500
MOVED_AT = ADDED_AT + 600
UNDONE_AT = ADDED_AT + 700
DECIDED_AT = ADDED_AT + 800
REVERSED_AT = ADDED_AT + 900

ASSET = "01HX0000000000000000000501"
PERSON = "01HX0000000000000000000502"
TAG = "01HX0000000000000000000503"
SITE = "01HX0000000000000000000504"
USERNAME = "01HX0000000000000000000505"
BOX = "01HX0000000000000000000506"
ROOT = "01HX0000000000000000000507"
MOVE = "01HX0000000000000000000508"
TRACK = "01HX0000000000000000000509"
DECISION = "01HX0000000000000000000510"
SOURCE = "01HX0000000000000000000511"
#: The library every `make_file` file sits loose in. Its own root rather than `ROOT`, which the move
#: tests write with a plain INSERT and would collide with.
LIBRARY = "01HX0000000000000000000590"


async def _named_from_folders(database: Database) -> None:
    """The file in `Shoots/Neve`, and every folder above it answered as Neve Alder, the library's
    own top included, written deepest first."""
    await make_file(database)
    await database.execute(
        "UPDATE asset_locations SET rel_path = 'Shoots/Neve/clip.mp4' WHERE asset_id = ?", (ASSET,)
    )
    await name_person(database, source="folder", at=NAMED_AT)
    for folder_id, path in (("f-b-neve", "Shoots/Neve"), ("f-a-shoots", "Shoots"), ("f-c", "")):
        await database.execute(
            "INSERT INTO folders (id, root_id, rel_path, name) VALUES (?, ?, ?, ?)",
            (folder_id, LIBRARY, path, path.rsplit("/", 1)[-1]),
        )
        await database.execute(
            "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, ?)",
            (folder_id, PERSON, ADDED_AT),
        )


async def test_a_naming_folder_is_named_only_to_a_reader_who_may_see_it_all_the_way_down(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Handed no folders, the read asks which the reader may see. A reader who may see `Shoots`
    and not `Neve` inside it is told no folder at all, rather than the path's hidden half."""
    from sift.kernel.access.history_sources import naming_folders

    await _named_from_folders(temp_db)

    found = await naming_folders(temp_db, access, actors.admin, ASSET, [PERSON, PERSON])
    assert {key: (one.id, one.text) for key, one in found.items()} == {PERSON: ("f-b-neve", "Neve")}
    partly = {LIBRARY: frozenset({"Shoots"})}
    assert await naming_folders(temp_db, access, actors.admin, ASSET, [PERSON], partly) == {}


async def test_the_nearest_folder_that_named_a_person_is_read_for_a_page_of_files(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The deepest answered folder above each file, as a file's own line names it, across every
    copy of it: a copy in another library under a shallower answered folder, read after the deep
    one, does not take its place. A file no answered folder holds is not in the answer."""
    from sift.kernel.access.history_sources import nearest_naming_folders

    await _named_from_folders(temp_db)
    elsewhere = "01HX0000000000000000000509"
    await make_file(temp_db, elsewhere)
    other_library = "01HX0000000000000000000510"
    await make_file(temp_db, other_library)
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('r2', 'r2', '/r2', 0)"
    )
    await temp_db.execute(
        "UPDATE asset_locations SET root_id = 'r2' WHERE asset_id = ?", (other_library,)
    )
    await temp_db.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES ('copy-2', ?, 'r2', NULL, 'Pool/clip.mp4',"
        " 'clip.mp4', 0, 0)",
        (ASSET,),
    )
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, rel_path, name) VALUES ('f-pool', 'r2', 'Pool', 'Pool')"
    )
    await temp_db.execute(
        "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES ('f-pool', ?, ?)",
        (PERSON, ADDED_AT),
    )

    nearest = await nearest_naming_folders(temp_db, PERSON, [ASSET, elsewhere, other_library])

    assert nearest == {
        ASSET: ("f-b-neve", LIBRARY, "Shoots/Neve", "Neve"),
        elsewhere: ("f-c", LIBRARY, "", ""),
    }
