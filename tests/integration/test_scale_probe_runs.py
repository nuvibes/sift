# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scale probe builds a library on today's schema and asks every question it has a budget for.

It is run by hand, rarely, against a library of half a million files, so nothing else would notice
the day a column it writes moves or a statement it times binds a new parameter. A few hundred files
are enough to prove it still runs end to end; whether each answer fits its budget is the hand run's
question, so it is not asked here.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scale_probe.py"


def _load() -> ModuleType:
    """The script, imported by path: an operator tool rather than part of the package."""
    spec = importlib.util.spec_from_file_location("sift_scale_probe_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


async def test_a_small_library_is_built_and_every_question_is_timed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kept = tmp_path / "probe"

    verdict = await probe.run(300, kept, 7)

    printed = capsys.readouterr().out
    assert "built 300 files" in printed
    timed = {
        name
        for name in probe.BUDGETS
        if re.search(rf"^{re.escape(name)}\s+\d+\.\d+ ms", printed, re.MULTILINE)
    }
    assert timed == set(probe.BUDGETS), f"not timed: {sorted(set(probe.BUDGETS) - timed)}"
    assert verdict in (0, 1)
    assert (kept / "probe.sqlite3").is_file(), "--keep keeps the database it built"


async def test_the_walls_it_times_have_rows_to_draw(tmp_path: Path) -> None:
    """A wall timed while it answers nothing measures nothing: the built library has people and
    tags an admin may see."""
    database = probe.Database(tmp_path / "probe.sqlite3", readers=1)
    await database.connect()
    try:
        await database.initialize_schema()
        ids = await probe.build(database, 300, 7)
        settings = probe.Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
        access = probe.Repository(database, probe.ContentStore(database, settings))
        admin = probe.Viewer(id=ids["admin"], role=probe.Role.ADMIN)
        assert (await access.suggest_people(admin, limit=50)).items
        assert (await access.list_tags(admin, limit=50)).items
    finally:
        await database.close()
