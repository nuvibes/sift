# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the access tests share: the truth table, the vault cases, and the small libraries
several of them build."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Imported for their tables: the unnamed-face condition, `same_music`, the `enriched:` predicates
# and the ledger every concealment writes all read one, and the application always has them.
import sift.slices.faces.schema
import sift.slices.music.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    Effect,
    Folder,
    ObjectType,
    Repository,
    Viewer,
    repository,
)
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.testing.fixtures import Actors, World, hide
from sift.testing.library import _INSERT_SONG, _LINK_ASSET_SONG


def _prop_settings(directory: str) -> Settings:
    """Settings for a property test's throwaway database; only path resolution reads them."""
    return Settings(data_dir=Path(directory) / "data", cache_dir=Path(directory) / "cache")


TRUTH_TABLE = json.loads(
    (Path(__file__).parent / "fixtures" / "access_truth_table.json").read_text()
)
CASES: list[dict[str, Any]] = TRUTH_TABLE["cases"]


async def apply_grants(
    access: Repository,
    world: World,
    subject: str,
    grants: list[list[str]],
    database: Database,
) -> None:
    for object_type, object_name, effect in grants:
        if object_type == ObjectType.SONG:
            await carry_the_song(database, world)
        await access.grant(
            ObjectType(object_type),
            world.object_id(object_name),
            subject,
            Effect(effect),
        )


async def carry_the_song(database: Database, world: World) -> None:
    """Put the world's song on `solo`, once, for a case that grants or hides it: `build_world`
    gives it no row, so other tests read no song on `solo`."""
    if await database.fetch_one("SELECT 1 FROM songs WHERE id = ?", (world.song,)) is None:
        await database.execute(_INSERT_SONG, (world.song, "song", sort_key("song")))
        await database.execute(_LINK_ASSET_SONG, (world.solo, world.song))


# --- WHICH shuffle, for Random, the one order drawn from a seed off the address
#
# Asked of two dozen files: three rows have only six orders, too few to tell a shuffle from luck.


async def _a_shelf_of_files(database: Database, world: World, how_many: int) -> None:
    """More files than three, each with a COPY: verdict rows are written when a copy appears, so a
    file with none is in nobody's library."""
    for index in range(how_many):
        asset_id = new_id()
        await database.execute(
            "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
            " VALUES (?, ?, 1, 'video', 0)",
            (asset_id, f"digest-shelf-{index}-{asset_id}"),
        )
        await database.execute(
            "INSERT INTO asset_locations"
            " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, NULL, ?, ?, 0, 0)",
            (new_id(), asset_id, world.root, f"shelf-{index}.mp4", f"shelf-{index}.mp4"),
        )


async def _shuffled(
    access: Repository, actors: Actors, seed: int | None, *, limit: int = 50, offset: int = 0
) -> list[str]:
    listing = await access.visible_assets(
        actors.admin, sort="random", seed=seed, limit=limit, offset=offset
    )
    return [item.asset.id for item in listing.items]


# --- the vault, at every site that has to agree about it
#
# The concealment rule is written out in the asset resolver, the tree's file counts and the subtree
# count before a move. A count disagreeing with the shown files tells the reader how much is kept
# back. Each site is held to what the asset resolver answers, never to hand-written numbers.


@dataclass(frozen=True, slots=True)
class VaultCase:
    """One way to conceal `solo`; `kind` is the per-user table, read by the coverage test."""

    name: str
    kind: str
    target: str
    #: Folders this same act must remove from the tree entirely, rather than merely empty out.
    concealed_folders: frozenset[str]


VAULT_CASES: list[VaultCase] = [
    VaultCase(
        name="a hidden file",
        kind="asset",
        target="solo",
        concealed_folders=frozenset(),
    ),
    VaultCase(
        name="a hidden person",
        kind="person",
        target="person",
        concealed_folders=frozenset(),
    ),
    VaultCase(
        name="a hidden collection",
        kind="collection",
        target="collection",
        concealed_folders=frozenset(),
    ),
    VaultCase(
        name="a hidden tag",
        kind="tag",
        target="tag",
        concealed_folders=frozenset(),
    ),
    VaultCase(
        # Two hops: a site has usernames, and a username is on the file.
        name="a hidden site",
        kind="site",
        target="site",
        concealed_folders=frozenset(),
    ),
    VaultCase(
        # A photo set the file is in, concealed by the rule a collection is.
        name="a photo set the file is in",
        kind="photo_set",
        target="photo_set",
        concealed_folders=frozenset(),
    ),
    VaultCase(
        name="a song the file carries",
        kind="song",
        target="song",
        concealed_folders=frozenset(),
    ),
    VaultCase(
        name="the folder the file sits in",
        kind="folder",
        target="leaf",
        concealed_folders=frozenset({"leaf"}),
    ),
    VaultCase(
        name="a folder above the one the file sits in",
        kind="folder",
        target="mid",
        concealed_folders=frozenset({"leaf"}),
    ),
    VaultCase(
        name="the whole root",
        kind="root",
        target="root",
        concealed_folders=frozenset({"top", "leaf"}),
    ),
    VaultCase(
        # `twin`'s other copy, in another root: hiding that folder drops the count under `top`.
        name="a second copy of the file, in a hidden folder elsewhere",
        kind="folder",
        target="other",
        concealed_folders=frozenset({"other"}),
    ),
]

#: Which files have a copy under each folder; `loose` sits directly in the root.
SUBTREE: dict[str, tuple[str, ...]] = {
    "top": ("solo", "twin"),
    "leaf": ("solo", "twin"),
    "other": ("twin",),
}


async def conceal(temp_db: Database, world: World, case: VaultCase, actors: Actors) -> None:
    """Hide one thing for an admin, and something else, always, for somebody who is not them.

    With one user's hidden rows, "anybody hid this" and "this viewer hid this" answer alike; the
    second user's rows make a copy of the rule that lost its `:viewer` disagree with the rest.
    """
    target = world.object_id(case.target)
    assert target is not None
    if case.kind == "song":
        await carry_the_song(temp_db, world)
    await hide(temp_db, case.kind, target, actors.admin.id)
    await hide(temp_db, "asset", world.twin, actors.guest.id)
    await hide(temp_db, "person", world.person, actors.guest.id)


async def folder_named(
    access: Repository, viewer: Viewer, world: World, name: str
) -> Folder | None:
    folder_id = world.object_id(name)
    assert folder_id is not None
    return await access.get_folder(viewer, folder_id)


def _access_source() -> str:
    """Every line of the access package, as one string, read from disk so the comments count."""
    package = Path(repository.__file__).parent
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py")))


#: Every named CTE in the access package: opened by `name(columns) AS (` at column zero and closed
#: by the next line that is only `),` or `)`.
_CTE_HEAD = re.compile(r"^(\w+)\([^)]*\) AS \($", re.M)


def _access_ctes() -> list[tuple[str, str]]:
    source = _access_source()
    found: list[tuple[str, str]] = []
    for match in _CTE_HEAD.finditer(source):
        rest = source[match.end() :]
        end = rest.find("\n),")
        stop = rest.find("\n)\n")
        if end == -1 or (stop != -1 and stop < end):
            end = stop
        found.append((match.group(1), rest[: end if end != -1 else len(rest)]))
    return found


# --- the people suggesters and the one alias resolver, the only place a typed word becomes a person


async def _person(temp_db: Database, name: str, *, hidden_by: Viewer | None = None) -> str:
    """One person, optionally hidden by a viewer: hiding means nothing without one."""
    person_id = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name)
    )
    if hidden_by is not None:
        await hide(temp_db, "person", person_id, hidden_by.id)
    return person_id


# --- the catalog writer: a site and a username, linked to an asset


async def _migrated(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    return temp_db


# --- filing a download under the person it is of, the refusals above all: a wrong filing is silent


async def _somebody(db: Database, name: str, *aliases: str) -> str:
    person_id = new_id()
    await db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name)
    )
    for alias in aliases:
        await db.execute(
            "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)",
            (new_id(), person_id, alias),
        )
    return person_id


async def _an_asset(db: Database) -> str:
    asset_id = new_id()
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at) "
        "VALUES (?, ?, 'video', 1, 'x.mp4', 0)",
        (asset_id, new_id()),
    )
    return asset_id


# --- ordering a page

#: The instant the sortable fixtures are anchored to.
_EPOCH = 1_700_000_000


async def _seed_asset(db: Database) -> str:
    asset_id = new_id()
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at) "
        "VALUES (?, ?, 'video', 1, 'x.mp4', 0)",
        (asset_id, new_id()),
    )
    return asset_id


async def _a_file_called(temp_db: Database, root_id: str, filename: str) -> None:
    """One asset with one location, which is all the username-number search reads."""
    asset_id = new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'image', 0)",
        (asset_id, new_id()),
    )
    await temp_db.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, NULL, ?, ?, 0, 0)",
        (new_id(), asset_id, root_id, filename, filename),
    )
