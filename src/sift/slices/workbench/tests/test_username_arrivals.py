# SPDX-License-Identifier: AGPL-3.0-or-later
"""A username says how it arrived: recorded as it is made, and backfilled for the ones made before.

The row carries a `created_at` and no provenance, so on its own nothing could tell a username
read off a file name from one a stash-box answered with or one a watermark was read as. The one
body that makes a username (`catalog._seed_username_on`) writes an `added` event in the same
transaction, with the pass as the actor, and the workbench's v14 step writes the same event for
every username made before, where the how can be read.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# The ledger's table. See `kernel/tests/test_ledger_door.py` for why this import is needed.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    Repository,
    by_sift,
    seed_site_username,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key

pytestmark = pytest.mark.unit

SOURCE = Path(__file__).resolve().parents[3]

_ARRIVALS = (
    "SELECT d.actor_kind, d.actor_id, d.object_kind, d.object_id, d.object_name, d.payload,"
    " d.decided_at, s.subject_id, s.name"
    " FROM workbench_decisions d JOIN workbench_decision_subjects s ON s.decision_id = d.id"
    " WHERE d.verb = 'added' AND s.kind = 'username' ORDER BY s.name"
)


async def _arrivals(database: Database) -> list[dict[str, object]]:
    return [dict(row) for row in await database.fetch_all(_ARRIVALS, ())]


# --- as it happens ----------------------------------------------------------------------------


async def test_a_username_a_pass_makes_says_which_pass_and_which_site(
    temp_db: Database, access: Repository
) -> None:
    site_id, username_id = await seed_site_username(
        temp_db, site="Instagram", name="wrenly", made=by_sift("filename")
    )

    [arrival] = await _arrivals(temp_db)
    assert (arrival["actor_kind"], arrival["actor_id"]) == ("sift", "filename")
    assert (arrival["subject_id"], arrival["name"]) == (username_id, "wrenly")
    assert (arrival["object_kind"], arrival["object_id"]) == ("site", site_id)
    # Named by the ledger's door from the Site's own row.
    assert arrival["object_name"] == "Instagram"


async def test_a_second_sighting_is_not_an_arrival(temp_db: Database, access: Repository) -> None:
    await seed_site_username(temp_db, site="Instagram", name="wrenly", made=by_sift("filename"))
    await seed_site_username(temp_db, site="Instagram", name="wrenly", made=by_sift("stash"))

    assert [one["actor_id"] for one in await _arrivals(temp_db)] == ["filename"]


def test_nothing_in_the_application_makes_a_username_with_nobody_to_name() -> None:
    """The one hole: `MADE_BY_A_PERSON` has no user id, so the ledger cannot say whose it was and
    the arrival goes unrecorded (`catalog._actor_of`). It is kept out of `src` here: every maker of
    a username in the running application is a pass, and one that is a person must name them."""
    makers = {"seed_site_username", "seed_site_username_on", "_seed_username_on"}
    makers |= {"remember_username_number"}
    offenders: list[str] = []
    for path in (SOURCE / "sift").rglob("*.py"):
        if "tests" in path.parts or "testing" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
            if name not in makers:
                continue
            for keyword in node.keywords:
                said = ast.unparse(keyword.value) if keyword.arg == "made" else ""
                if said.endswith("MADE_BY_A_PERSON") or said in ("by_user()", "by_user(None)"):
                    offenders.append(f"{path.relative_to(SOURCE)}:{node.lineno}")
    assert offenders == [], (
        "A username made by a person with no id records no arrival. Name the person"
        f" (by_user(viewer.id)), or make it as the pass that it is: {offenders}"
    )


def test_the_guard_above_can_see_a_person_with_nobody_named() -> None:
    """A known positive, so a walk that stopped reading cannot pass by finding nothing."""
    tree = ast.parse("seed_site_username(db, site='x', name='y', made=MADE_BY_A_PERSON)")
    [call] = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert isinstance(call, ast.Call)
    assert ast.unparse(call.keywords[-1].value).endswith("MADE_BY_A_PERSON")


# --- the ones made before -------------------------------------------------------------------


async def _username(database: Database, site_id: str, name: str, *, at: int) -> str:
    username_id = new_id()
    await database.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, ?, ?, ?)",
        (username_id, site_id, name, sort_key(name), at),
    )
    return username_id
