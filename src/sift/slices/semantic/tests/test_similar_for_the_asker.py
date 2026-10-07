# SPDX-License-Identifier: AGPL-3.0-or-later
"""The fingerprint tier of "Similar to this" ranks only what the asker may see.

A guest whose nearest matches are more hidden files than it ranks is still owed a full strip of
their own, read the same number of times as if those files were not there.
"""

from __future__ import annotations

import importlib
import random
from dataclasses import dataclass, replace
from typing import Any

import pytest

from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.content.duplicates import DuplicateReads
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.semantic.similar import CANDIDATES, Similar, SimilarFinder
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

router = importlib.import_module("sift.slices.semantic.router")

HIDDEN_NEARER = CANDIDATES + 100
VISIBLE = 40
BASES = {"clear": 0, "crowded": 0x7FFFFFFF00000000}
NEAREST = sorted(2 + index % 9 for index in range(VISIBLE))[: router.SIMILAR_PAGE]


def flipped(base: int, bits: int, pick: random.Random) -> str:
    for bit in pick.sample(range(63), bits):
        base ^= 1 << bit
    return f"{base:016x}"


class Counted:
    """The database, counting the reads made through it."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.reads = 0
        self.named: list[str] = []

    def __getattr__(self, name: str) -> Any:
        real = getattr(self.database, name)
        if name not in {"fetch_all", "fetch_one", "sweep_all"}:
            return real

        async def counted(*args: Any, **kwargs: Any) -> Any:
            self.reads += 1
            self.named.append(name)
            return await real(*args, **kwargs)

        return counted


class Service:
    def __init__(self, finder: SimilarFinder) -> None:
        self.finder = finder

    async def similar_to(self, asset_id: str, *, limit: int = CANDIDATES, asker: Viewer) -> Similar:
        return await self.finder.perceptual(asset_id, limit=limit, asker=asker)


@dataclass
class Library:
    counted: Counted
    access: Repository
    finder: SimilarFinder
    guest: Viewer
    admin: Viewer
    keeper: Viewer
    subjects: dict[str, str]
    bits: dict[str, int]
    hidden: list[str]


@pytest.fixture
async def library(temp_db: Database) -> Library:
    """Each subject has visible files near it at 2 to 10 bits; the crowded one also has hidden
    files 1 bit away, more than are ranked. The keeper is an admin whose vault holds those."""
    await temp_db.initialize_schema()
    pick = random.Random(7)
    guest = await create_user(temp_db, Role.GUEST)
    admin = await create_user(temp_db, Role.ADMIN)
    keeper = await create_user(temp_db, Role.ADMIN)
    placed: list[tuple[str, str, str]] = []
    subjects: dict[str, str] = {}
    bits: dict[str, int] = {}
    hidden: list[str] = []
    for name, base in BASES.items():
        subjects[name] = new_id()
        placed.append((subjects[name], f"{base:016x}", "mine"))
        near = [(2 + index % 9, new_id()) for index in range(VISIBLE)]
        placed += [(one, flipped(base, bits, pick), "mine") for bits, one in near]
        bits.update((one, apart) for apart, one in near)
    for _ in range(HIDDEN_NEARER):
        hidden.append(new_id())
        placed.append((hidden[-1], flipped(BASES["crowded"], 1, pick), "theirs"))
    async with temp_db.write() as c:
        for root in ("mine", "theirs"):
            await c.execute(
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, 0)",
                (root, root, f"/library/{root}"),
            )
        await c.execute(
            "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect,"
            " created_at) VALUES ('g', 'root', 'mine', ?, 'share', 0)",
            (guest.id,),
        )
        for asset_id, phash, root in placed:
            await c.execute(
                "INSERT INTO assets (id, identity, identity_version, media_type, added_at, phash)"
                " VALUES (?, ?, 1, 'image', 0, ?)",
                (asset_id, f"digest-{asset_id}", phash),
            )
            await c.execute(
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
                " filename, first_seen_at, last_seen_at) VALUES (?, ?, ?, NULL, ?, ?, 0, 0)",
                (new_id(), asset_id, root, asset_id, asset_id),
            )
        await c.executemany(
            "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
            " VALUES (?, ?, 1, 0, 0)",
            [(one, keeper.id) for one in hidden],
        )
    counted = Counted(temp_db)
    database: Any = counted
    no_content: Any = None
    finder = SimilarFinder(DuplicateReads(database), database)
    access = Repository(temp_db, no_content)
    return Library(counted, access, finder, guest, admin, keeper, subjects, bits, hidden)


async def _strip(library: Library, viewer: Viewer, subject: str) -> tuple[list[str], int]:
    library.counted.reads = 0
    library.counted.named = []
    page = await router.find_similar(
        library.subjects[subject],
        service=Service(library.finder),
        access=library.access,
        viewer=viewer,
    )
    reads = library.counted.reads
    return [item.id for item in page.items], reads


@pytest.mark.parametrize("subject", ["clear", "crowded"])
async def test_a_guests_strip_is_full_whatever_is_hidden_nearer(
    library: Library, subject: str
) -> None:
    shown, _ = await _strip(library, library.guest, subject)

    assert [library.bits[one] for one in shown] == NEAREST


async def test_a_shut_vault_is_not_ranked_and_an_open_one_is(library: Library) -> None:
    shut, _ = await _strip(library, library.keeper, "crowded")
    unlocked, _ = await _strip(library, replace(library.keeper, show_hidden=True), "crowded")

    assert [library.bits[one] for one in shut] == NEAREST
    assert len(unlocked) == router.SIMILAR_PAGE
    assert set(unlocked) <= set(library.hidden)


@pytest.mark.parametrize("who", ["guest", "keeper"])
async def test_the_reads_do_not_follow_what_is_hidden(library: Library, who: str) -> None:
    viewer = getattr(library, who)
    _, clear = await _strip(library, viewer, "clear")
    _, crowded = await _strip(library, viewer, "crowded")

    assert clear == crowded


async def test_an_admin_with_nothing_hidden_keeps_the_whole_read(library: Library) -> None:
    """The fingerprints alone with the vault open, and one seek more with it shut."""
    shut, shut_reads = await _strip(library, library.admin, "crowded")
    shut_named = list(library.counted.named)
    unlocked, open_reads = await _strip(
        library, replace(library.admin, show_hidden=True), "crowded"
    )

    assert set(shut) <= set(library.hidden)
    assert shut == unlocked
    assert (shut_reads, open_reads) == (2, 1)
    # The whole read is the sweep, either way; the seek more is whether this admin hid anything.
    assert (shut_named, library.counted.named) == (["fetch_one", "sweep_all"], ["sweep_all"])
