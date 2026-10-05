# SPDX-License-Identifier: AGPL-3.0-or-later
"""What `scripts/check_code_shape.py` refuses and what it records, driven with code made for it.

The check over the real tree is a step of its own in `ci-local.sh`, the pre-commit hook and the
quick workflow; asserting it here too would turn every unrecorded fall into two failures.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_code_shape.py"
_SPEC = importlib.util.spec_from_file_location("_code_shape", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
shape = importlib.util.module_from_spec(_SPEC)
sys.modules["_code_shape"] = shape  # a dataclass looks its module up while it is made
_SPEC.loader.exec_module(shape)


def _function(name: str, body_lines: int) -> str:
    return f"def {name}():\n" + "    x = 1\n" * (body_lines - 1)


def _code(lines: int) -> str:
    return "x = 1\n" * lines


def _measured(files: dict[str, str]) -> Any:
    found = shape.Shape()
    for rel, text in files.items():
        shape.add_file(found, rel, text)
    return found


def _verdict(before: dict[str, str], after: dict[str, str]) -> Any:
    return shape.compare(_measured(before).as_record(), _measured(after))


def test_a_module_over_its_line_is_recorded_and_may_not_grow() -> None:
    long = {"src/sift/a.py": _code(1200)}
    assert _measured(long).module_lines == {"src/sift/a.py": 1200}
    assert _measured({"src/sift/a.py": _code(1000)}).module_lines == {}
    verdict = _verdict(long, {"src/sift/a.py": _code(1201)})
    assert verdict.refused and verdict.rose == ["module_lines src/sift/a.py: 1201, recorded 1200"]


def test_a_module_that_grows_by_entry_is_not_held_to_the_module_line() -> None:
    listed = next(iter(shape.GROWS_BY_ENTRY))
    long = {listed: _code(1200), "src/sift/a.py": _code(1200)}
    assert _measured(long).module_lines == {"src/sift/a.py": 1200}
    for name, why in shape.GROWS_BY_ENTRY.items():
        assert (shape.ROOT / name).is_file(), name
        assert why


def test_a_function_over_its_line_may_not_grow() -> None:
    before = {"src/sift/a.py": _function("run", 90)}
    assert _measured(before).function_lines == {"src/sift/a.py::run": 90}
    verdict = _verdict(before, {"src/sift/a.py": _function("run", 91)})
    assert verdict.rose == ["function_lines src/sift/a.py::run: 91, recorded 90"]


def _branched(name: str, ifs: int) -> str:
    return f"def {name}(x):\n" + "    if x:\n        x -= 1\n" * ifs


def test_a_function_over_its_branches_may_not_gain_one() -> None:
    before = {"src/sift/a.py": _branched("run", 11)}
    assert _measured(before).function_branches == {"src/sift/a.py::run": 12}
    assert _measured({"src/sift/a.py": _branched("run", 9)}).function_branches == {}
    verdict = _verdict(before, {"src/sift/a.py": _branched("run", 12)})
    assert verdict.refused
    assert verdict.rose == ["function_branches src/sift/a.py::run: 13, recorded 12"]
    added = _verdict({}, before).added
    assert added == ["function_branches src/sift/a.py::run: 12, over the line and not recorded"]


def test_a_branch_is_counted_as_the_linter_counts_it() -> None:
    text = (
        "def run(x):\n"
        "    if x:\n        pass\n    elif x > 1:\n        pass\n    else:\n        pass\n"
        "    for y in x:\n        pass\n    else:\n        pass\n"
        "    while x:\n        x -= 1\n"
        "    try:\n        pass\n    except KeyError:\n        pass\n"
        "    except ValueError:\n        pass\n    else:\n        pass\n"
        "    finally:\n        pass\n"
        "    with x:\n        pass\n"
        "    match x:\n        case 1:\n            pass\n"
        "        case [a] if a:\n            pass\n        case _:\n            pass\n"
        "    def inner():\n        return 1 if x else [z for z in x if z and x]\n"
        "    class Held:\n        def method(self):\n            pass\n"
    )
    (run,) = [node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)]
    # if, elif, for, while, two excepts, the try's else, two cases that can fail, and two
    # functions inside; an expression adds none.
    assert shape.branches_of(run) == 1 + 11


def test_a_test_files_functions_are_held_to_no_line() -> None:
    found = _measured({"tests/test_x.py": _branched("test_it", 20) + _function("long", 90)})
    assert found.function_branches == {} and found.function_lines == {}


def test_a_method_is_named_by_its_class_and_a_repeated_name_is_numbered() -> None:
    text = "class Store:\n" + "".join(
        "    " + line + "\n" for line in (_function("put", 85) * 2).splitlines()
    )
    assert sorted(_measured({"src/sift/a.py": text}).function_lines) == [
        "src/sift/a.py::Store.put",
        "src/sift/a.py::Store.put#2",
    ]


def test_a_function_moved_to_another_module_keeps_its_number() -> None:
    before = {"src/sift/a.py": _function("run", 90), "src/sift/b.py": ""}
    moved = _verdict(before, {"src/sift/a.py": "", "src/sift/b.py": _function("run", 90)})
    assert not moved.refused
    assert moved.fell == ["function_lines src/sift/b.py::run: moved from src/sift/a.py::run"]
    longer = _verdict(before, {"src/sift/a.py": "", "src/sift/b.py": _function("run", 91)})
    assert longer.added == ["function_lines src/sift/b.py::run: 91, over the line and not recorded"]
    branched = {"src/sift/a.py": _branched("run", 11), "src/sift/b.py": ""}
    carried = _verdict(branched, {"src/sift/a.py": "", "src/sift/b.py": _branched("run", 11)})
    assert not carried.refused
    assert carried.fell == ["function_branches src/sift/b.py::run: moved from src/sift/a.py::run"]


def test_a_test_file_has_its_own_line() -> None:
    assert shape.is_test_file("src/sift/kernel/tests/helpers.py")
    assert shape.is_test_file("tests/gates/test_x.py")
    assert not shape.is_test_file("src/sift/kernel/db.py")
    before = {"tests/test_x.py": _code(2100)}
    found = _measured(before)
    assert found.test_file_lines == {"tests/test_x.py": 2100} and found.module_lines == {}
    assert _verdict(before, {"tests/test_x.py": _code(2101)}).rose == [
        "test_file_lines tests/test_x.py: 2101, recorded 2100"
    ]


def test_a_test_file_is_held_to_the_prose_line_as_a_module_is() -> None:
    before = {"tests/test_x.py": _prosed(40, 60)}
    assert _measured(before).prose_lines == {"tests/test_x.py": 42}
    verdict = _verdict(before, {"tests/test_x.py": _prosed(45, 60)})
    assert verdict.rose[0].startswith("prose tests/test_x.py: ")
    assert _verdict({}, before).added[0].startswith("prose tests/test_x.py: ")


def test_a_new_entry_over_the_line_is_refused() -> None:
    verdict = _verdict({}, {"src/sift/new.py": _code(1001)})
    assert verdict.refused
    assert verdict.added == ["module_lines src/sift/new.py: 1001, over the line and not recorded"]


def _prosed(prose: int, code: int) -> str:
    return '"""' + "\nwords" * prose + '\n"""\n' + _code(code)


def test_prose_is_docstrings_and_comments_over_non_blank_lines() -> None:
    text = '"""One.\n\nTwo.\n"""\n\n# why\nx = 1  # and why\ny = 2\n'
    assert shape.prose_of(text, ast.parse(text)) == (6, 5)


def test_prose_that_grows_in_share_and_in_lines_is_refused() -> None:
    before = {"src/sift/a.py": _prosed(40, 60)}
    assert _measured(before).prose_lines == {"src/sift/a.py": 42}
    verdict = _verdict(before, {"src/sift/a.py": _prosed(45, 60)})
    assert verdict.refused and verdict.rose[0].startswith("prose src/sift/a.py: ")


def test_prose_that_rises_only_in_share_or_only_in_lines_is_recorded_not_refused() -> None:
    before = {"src/sift/a.py": _prosed(40, 60)}
    code_deleted = _verdict(before, {"src/sift/a.py": _prosed(40, 50)})
    words_with_more_code = _verdict(before, {"src/sift/a.py": _prosed(42, 90)})
    for verdict in (code_deleted, words_with_more_code):
        assert not verdict.refused and verdict.fell[0].startswith("prose src/sift/a.py: ")


def test_a_short_file_is_not_held_to_a_share() -> None:
    assert _measured({"src/sift/__init__.py": _prosed(30, 1)}).prose_share == {}


def test_a_fall_is_asked_to_be_recorded() -> None:
    before = {"src/sift/a.py": _code(1500), "src/sift/b.py": _function("run", 100)}
    after = {"src/sift/a.py": _code(1400), "src/sift/b.py": _function("run", 50)}
    verdict = _verdict(before, after)
    assert not verdict.refused
    assert verdict.fell == [
        "module_lines src/sift/a.py: 1400, recorded 1500",
        "function_lines src/sift/b.py::run: under the line, recorded 100",
    ]


def test_the_record_is_over_every_line_it_names() -> None:
    record = json.loads(shape.RECORD.read_text(encoding="utf-8"))
    assert all(value > shape.MODULE_LINES for value in record["module_lines"].values())
    assert all(value > shape.FUNCTION_LINES for value in record["function_lines"].values())
    assert all(value > shape.FUNCTION_BRANCHES for value in record["function_branches"].values())
    assert all(value > shape.TEST_FILE_LINES for value in record["test_file_lines"].values())
    assert all(value > shape.PROSE_SHARE for value in record["prose_share"].values())
    assert record["prose_share"].keys() == record["prose_lines"].keys()
    assert len(record["module_lines"]) > 20, (
        "a record this short reads as a walk that found nothing"
    )
