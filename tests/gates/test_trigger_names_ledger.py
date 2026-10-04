# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every trigger name Sift has shipped is written down, and each one is built today or retired.

A trigger can be dropped only by its NAME. `visibility.keep_true` drops one this build does not make
only if it is in `visibility.RETIRED_TRIGGERS`; otherwise it is left running, and every boot finds
the triggers out of step and rebuilds every stored answer. `data/trigger_names.json` records the
past (APPEND-ONLY, kept so by review): a name that stops being built fails here until retired.

The waiting tables' triggers (`waiting.triggers`) have no retirement list, and `RETIRED_TRIGGERS`
is dropped only on a visibility rebuild, so a name without the `vis_` prefix must stay built.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

import sift.main  # noqa: F401 (imported for its side effect: every component registers itself)
from sift.kernel.access import visibility, waiting

pytestmark = [pytest.mark.gate, pytest.mark.unit]

LEDGER = Path(__file__).parent / "data" / "trigger_names.json"

#: The prefix of every visibility trigger name, the only names its retirement list can drop.
RETIRABLE = "vis_"


def _ledger() -> list[str]:
    names = json.loads(LEDGER.read_text(encoding="utf-8"))["names"]
    assert isinstance(names, list)
    return [str(name) for name in names]


def built_today() -> set[str]:
    """Every trigger name this build composes: visibility's and every waiting table's."""
    names = {name for name, _table, _ddl in visibility.triggers()}
    for spec in waiting.registered():
        names |= set(waiting.triggers(spec))
    return names


def unaccounted(ledger: Iterable[str], built: set[str], retired: Iterable[str]) -> list[str]:
    """The names once shipped that this build neither makes nor can drop."""
    dropped = set(retired)
    return sorted(
        name
        for name in ledger
        if name not in built and not (name.startswith(RETIRABLE) and name in dropped)
    )


def unrecorded(ledger: Iterable[str], names: Iterable[str]) -> list[str]:
    """The names this build knows about that the ledger does not."""
    recorded = set(ledger)
    return sorted(name for name in names if name not in recorded)


def test_every_trigger_ever_shipped_is_built_or_retired() -> None:
    missing = unaccounted(_ledger(), built_today(), visibility.RETIRED_TRIGGERS)
    assert not missing, (
        "these trigger names were shipped and are no longer built. A database that has one keeps "
        "running it: add each `vis_` name to visibility.RETIRED_TRIGGERS; any other name belongs "
        "to a waiting table, which cannot retire one yet (see this gate's docstring):\n"
        + "\n".join(missing)
    )


def test_every_trigger_built_today_is_in_the_ledger() -> None:
    missing = unrecorded(_ledger(), built_today())
    assert not missing, (
        "add these to tests/gates/data/trigger_names.json. A name goes in when it is first "
        "built and never comes out:\n" + "\n".join(missing)
    )


def test_every_retired_trigger_is_in_the_ledger() -> None:
    """Every retired name is in the ledger, so it cannot be pruned from the list unnoticed."""
    missing = unrecorded(_ledger(), visibility.RETIRED_TRIGGERS)
    assert not missing, "add these to tests/gates/data/trigger_names.json:\n" + "\n".join(missing)


def test_the_ledger_names_each_trigger_once() -> None:
    names = _ledger()
    assert len(names) == len(set(names)), "a name is listed twice"


def test_the_gate_catches_a_name_nobody_retired() -> None:
    """Planted names are caught: one shipped and no longer built, one built and unrecorded, and a
    waiting table's name put on a list that would never drop it."""
    built = {"vis_kept_insert", "music_waiting_done"}
    ledger = ["vis_kept_insert", "music_waiting_done", "vis_planted_unknown"]
    assert unaccounted(ledger, built, retired=()) == ["vis_planted_unknown"]
    assert unaccounted(ledger, built, retired=("vis_planted_unknown",)) == []
    assert unrecorded(["vis_kept_insert"], built) == ["music_waiting_done"]
    gone_waiting = ["music_waiting_gone"]
    assert unaccounted(gone_waiting, built, retired=gone_waiting) == gone_waiting
