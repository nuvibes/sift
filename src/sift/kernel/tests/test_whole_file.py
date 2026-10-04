# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small file written whole or not at all."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sift.kernel.whole_file import write_json_whole


def test_it_writes_the_value_as_json_and_leaves_no_partial_behind(tmp_path: Path) -> None:
    target = tmp_path / "note.json"

    write_json_whole(target, {"b": 1, "a": [1, 2]})

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": [1, 2], "b": 1}
    assert sorted(each.name for each in tmp_path.iterdir()) == ["note.json"]


def test_it_replaces_an_existing_file_whole(tmp_path: Path) -> None:
    target = tmp_path / "note.json"
    target.write_text('{"old": true}', encoding="utf-8")

    write_json_whole(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}


def test_a_value_that_cannot_be_written_leaves_the_old_file_untouched(tmp_path: Path) -> None:
    """The reader sees the old note or the new one, never a torn or an empty one."""
    target = tmp_path / "note.json"
    target.write_text('{"old": true}', encoding="utf-8")

    with pytest.raises(TypeError):
        write_json_whole(target, {"when": object()})

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert sorted(each.name for each in tmp_path.iterdir()) == ["note.json"]
