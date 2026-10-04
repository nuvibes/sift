# SPDX-License-Identifier: AGPL-3.0-or-later
"""A kept filter names each thing by its id, so a rename can never leave it stale.

The address a filter is saved from names a tag, a person, a Site, a collection, a Photo Set or a
folder by its NAME, so a filter kept that way would, after a rename, ask for a name nobody has: it
would match no file, and its chip would go on showing the old name. So every such value is kept as
the id of the one thing it names and read back under today's name, and a one-time step rewrites
the filters kept by name.

This gate holds the property rather than the code. It builds a library with one of each thing,
keeps filters naming them the way the panel writes them (before the step, and through the save
after it), and reads every kept filter back through the PARSER, the same reading the wall compiles.
Every entity value the parser finds must be an id, unless it is a name that names several things
(kept as the name, because it meant all of them) or nothing (kept, and shown as gone). A new
place that stores a filter by name, or a step that stops rewriting one, fails here.

The same holds for every other place that kept a thing by its name (the second test): a saved
Theater wall's cells (typed filter text), a download's Site and person, a watermark reading's Site
and a creator picture's username. Each keeps the id beside or instead of the word, filled once by
its step from the names and written by id from then on. Two more places read names live and store
none, so they are held by their own tests beside them: a proposed shoot's creator name
(`shoots/tests/test_store.py`) and the picker's and the swap drawer's picks (`PickMenu`,
`SwapDrawer`, through `/search/names-now`).
"""

from __future__ import annotations

import pytest
from starlette.datastructures import QueryParams

# Imported for their side effect: each registers the tables the second test fills, so the fixture
# database carries them whatever else this worker happened to import first.
import sift.slices.download.schema
import sift.slices.theater.schema
import sift.slices.watermarks.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.db import Database
from sift.kernel.ids import is_id, new_id
from sift.kernel.sorting import sort_key
from sift.slices.download.schema import _fill_art, _fill_by_id
from sift.slices.search.filters import ENTITY_FIELDS, FilterCompiler, Term, parse
from sift.slices.search.schema import _cells_by_id, initialize
from sift.slices.search.service import SearchService
from sift.slices.watermarks.schema import _fill_site_ids
from sift.testing.fixtures import Actors

pytestmark = [pytest.mark.gate, pytest.mark.integration]

_EPOCH = 1_700_000_000

#: The filters kept before the step, as the panel wrote them: every entity field, the older
#: spellings of two of them, an excluded list, a typed clause, a shared name and a name gone.
_BEFORE = (
    "tags=Harbour|Dusk&people=Wren+Halloway&platforms=Lanternfish&from=01ABC",
    "collections=Kept+Best&photo_sets=Pier+Shoot&songs=Pier+Tune&folder=reels/pier&media=video",
    "q=tags%3AHarbour+sites%3ALanternfish+words&in=-reels",
    "people=Ada+Twin&tags=renamed-before-the-step",
)


async def _library(database: Database) -> dict[str, str]:
    """One of each thing a filter names, plus two people who share a name."""
    ids = {name: new_id() for name in ("root", "reels", "pier", "twin_a", "twin_b")}

    async def row(sql: str, params: tuple[object, ...]) -> None:
        await database.execute(sql, params)

    await row(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (ids["root"], "roll", "/library/roll", _EPOCH),
    )
    for key, parent, path, name in (
        ("reels", None, "reels", "reels"),
        ("pier", "reels", "reels/pier", "pier"),
    ):
        await row(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (ids[key], ids["root"], ids[parent] if parent else None, path, name),
        )
    for name in ("Harbour", "Dusk"):
        ids[name] = new_id()
        await row(
            "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (ids[name], name, _EPOCH)
        )
    for key, name in (("Wren Halloway", "Wren Halloway"), ("twin_a", "Ada Twin")):
        ids.setdefault(key, new_id())
        await row(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)", (ids[key], name, _EPOCH)
        )
    await row(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
        (ids["twin_b"], "Ada Twin", _EPOCH),
    )
    ids["Lanternfish"] = new_id()
    await row(
        "INSERT INTO sites (id, name, name_sort) VALUES (?, ?, ?)",
        (ids["Lanternfish"], "Lanternfish", sort_key("Lanternfish")),
    )
    ids["Kept Best"] = new_id()
    await row(
        "INSERT INTO collections (id, name, created_at) VALUES (?, ?, ?)",
        (ids["Kept Best"], "Kept Best", _EPOCH),
    )
    ids["Pier Shoot"] = new_id()
    await row(
        "INSERT INTO photo_sets (id, name, origin, created_at) VALUES (?, ?, 'manual', ?)",
        (ids["Pier Shoot"], "Pier Shoot", _EPOCH),
    )
    ids["Pier Tune"] = new_id()
    await row(
        "INSERT INTO songs (id, name, created_at) VALUES (?, ?, ?)",
        (ids["Pier Tune"], "Pier Tune", _EPOCH),
    )
    return ids


def _named_things(query: str) -> list[str]:
    """Every entity value the parser reads out of a kept filter."""
    return [
        leaf.value
        for leaf in parse(QueryParams(query)).leaves()
        if isinstance(leaf, Term) and leaf.field in ENTITY_FIELDS
    ]


async def test_every_kept_filter_names_things_by_id(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    ids = await _library(temp_db)
    for position, query in enumerate(_BEFORE):
        await temp_db.execute(
            "INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)"
            " VALUES (?, ?, 'asset', ?, ?, 0)",
            (new_id(), actors.admin.id, f"Before {position}", query),
        )
    async with temp_db.write() as connection:
        await initialize(connection, 7)

    service = SearchService(temp_db, access, FilterCompiler(access))
    await service.save_search(actors.admin, "After", "tags=Dusk&people=Wren+Halloway&in=pier")

    # The two a step cannot turn into one id, and that is the whole of what may stay a name.
    allowed_names = {"Ada Twin", "renamed-before-the-step"}
    kept = await temp_db.fetch_all("SELECT name, query FROM saved_searches ORDER BY name")
    assert len(kept) == len(_BEFORE) + 1
    for row in kept:
        values = _named_things(str(row["query"]))
        assert values, row["name"]
        stale = [value for value in values if not is_id(value) and value not in allowed_names]
        assert stale == [], f"{row['name']} keeps a name: {stale}"

    every_id = {value for row in kept for value in _named_things(str(row["query"]))}
    for name in (
        "Harbour",
        "Dusk",
        "Wren Halloway",
        "Lanternfish",
        "Kept Best",
        "Pier Shoot",
        "Pier Tune",
    ):
        assert ids[name] in every_id, name
    assert {ids["pier"], ids["reels"]} <= every_id


async def test_every_other_kept_name_is_kept_by_id(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    ids = await _library(temp_db)
    wall = new_id()
    await temp_db.execute(
        "INSERT INTO theater_arrangements (id, user_id, name, layout, created_at, updated_at)"
        " VALUES (?, ?, 'Wall', 'side_by_side', 0, 0)",
        (wall, actors.admin.id),
    )
    for position, source in enumerate(
        ('tags:Harbour people:"Wren Halloway" dusk', "sites:Lanternfish in:reels/pier")
    ):
        await temp_db.execute(
            "INSERT INTO theater_cells (arrangement_id, position, source, media_kind, ordering,"
            " end_behaviour, volume) VALUES (?, ?, ?, 'all', 'in_order', 'once', 100)",
            (wall, position, source),
        )
    asset = new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, 'one', 'video', 0)",
        (asset,),
    )
    username = new_id()
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, person_id, created_at) VALUES (?, ?, ?, ?, 0)",
        (username, ids["Lanternfish"], "wrenh", ids["Wren Halloway"]),
    )
    await temp_db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, site, username, asset_id, created_at)"
        " VALUES (?, 'https://lanternfish.example/wrenh/1', 'h', 'done', 'Lanternfish', 'wrenh',"
        " ?, 0)",
        (new_id(), asset),
    )
    await temp_db.execute(
        "INSERT INTO watermark_reads (asset_id, text, kind, site, confidence, read_at)"
        " VALUES (?, 'lanternfish.example/wrenh', 'site', 'Lanternfish', 0.9, 0)",
        (asset,),
    )
    await temp_db.execute(
        "INSERT INTO site_art (scope, path, stored_at) VALUES ('@lanternfish.example:wrenh',"
        " 'covers/x.jpg', 0)"
    )
    # As the rows stood before the steps: named, and nothing kept by id.
    for statement in (
        "UPDATE downloads SET site_id = NULL, person_id = NULL",
        "UPDATE watermark_reads SET site_id = NULL",
        "UPDATE site_art SET username_id = NULL",
        "DELETE FROM download_sites",
    ):
        await temp_db.execute(statement)
    # Each step's fill, as the step runs it: the columns and tables are already the fixture's.
    async with temp_db.write() as connection:
        await _cells_by_id(connection)
        await _fill_by_id(connection)
        await _fill_art(connection)
        await _fill_site_ids(connection)

    cells = await temp_db.fetch_all("SELECT source FROM theater_cells ORDER BY position")
    values = [
        value
        for row in cells
        for value in _named_things(str(QueryParams({"q": str(row["source"])})))
    ]
    assert values and all(is_id(value) for value in values), values
    assert {ids["Harbour"], ids["Wren Halloway"], ids["Lanternfish"], ids["pier"]} <= set(values)

    download = await temp_db.fetch_one("SELECT site_id, person_id FROM downloads")
    assert download is not None
    assert (download["site_id"], download["person_id"]) == (
        ids["Lanternfish"],
        ids["Wren Halloway"],
    )
    reading = await temp_db.fetch_one("SELECT site_id FROM watermark_reads")
    assert reading is not None and reading["site_id"] == ids["Lanternfish"]
    art = await temp_db.fetch_one("SELECT username_id FROM site_art")
    assert art is not None and art["username_id"] == username
