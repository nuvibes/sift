# SPDX-License-Identifier: AGPL-3.0-or-later
"""A creating write says who is making the row, or it does not run.

`people`, `sites`, `tags`, `collections` and `photo_sets` record their maker in `created_by_kind`,
`created_by_via` and `created_by_user_id`, telling apart a pass reading a folder name, a stash-box
answer and somebody typing the name. A default maker would write that answer for a call site never
asked, indistinguishable from one that meant it. So: no default for `made`; no `Made` built as
'sift' with nothing beside it (only `MADE_UNSAID`, named so it is visibly chosen); and SQL binding
'sift' into `created_by_kind` names `created_by_via` too. Read from source text: a wrong `Made` is
the slice suites' business. `EXEMPT` is empty.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

#: Nothing is exempt; the set stays, with its self-test, so a future exemption has a shape.
EXEMPT: frozenset[str] = frozenset()

#: `made: Made = ...` and `made: Made | None = ...`, on one line or ending a wrapped signature; a
#: `*made` varargs name is refused by the leading marker.
_DEFAULTED = re.compile(r"(?<![*\w])made\s*:\s*Made\s*(?:\|\s*None\s*)?=")

#: `Made('sift')` however spelled; `by_sift(VIA_X)`, the intended shape, is not matched.
_BARE_SIFT = re.compile(r"""Made\(\s*(?:kind\s*=\s*)?["']sift["']\s*(?:,\s*None\s*)?\)""")

#: Adjacent Python string literals joined, so a split statement reads as one.
_JOINED = re.compile(r'"\s*\n\s*"')

_INSERT = re.compile(r"INSERT (?:OR IGNORE )?INTO \w+\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", re.S)


def _files() -> list[Path]:
    """Every shipped module, `src/sift/testing/` included: every suite builds its world from it."""
    return [path for path in sorted(SOURCE.rglob("*.py")) if "tests" not in path.parts]


def _named(path: Path) -> str:
    return path.relative_to(SOURCE).as_posix()


def test_no_creation_write_defaults_its_maker() -> None:
    """No creation write defaults its maker, so a forgetful call site is a type error."""
    guilty = [
        f"{_named(path)}:{number}  {line.strip()[:90]}"
        for path in _files()
        if _named(path) not in EXEMPT
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _DEFAULTED.search(line)
    ]
    assert not guilty, (
        "these writes answer 'who made this row' on the caller's behalf, so a creation path that\n"
        "never says records the one answer the column exists to make explicit:\n\n  "
        + "\n  ".join(guilty)
        + "\n\nTake the default off and ask for `made` by keyword."
    )


def test_nothing_rebuilds_the_default_by_hand() -> None:
    """Nothing rebuilds `MADE_UNSAID` by hand at a call site."""
    guilty: list[str] = []
    for path in _files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _BARE_SIFT.search(line) or line.lstrip().startswith("MADE_UNSAID"):
                continue
            guilty.append(f"{_named(path)}:{number}  {line.strip()[:90]}")
    assert not guilty, (
        "these build a maker that says Sift and names no pass, which is what a default maker\n"
        "would write:\n\n  "
        + "\n  ".join(guilty)
        + "\n\nCall `by_sift(VIA_...)` with the pass that is running, or pass `MADE_UNSAID` where\n"
        "'Sift, and which pass is not worth a word' is genuinely meant."
    )


def test_a_statement_that_writes_sift_writes_the_pass_beside_it() -> None:
    """A statement writing 'sift' as the maker names the pass beside it."""
    guilty: list[str] = []
    for path in _files():
        text = _JOINED.sub("", path.read_text(encoding="utf-8", errors="replace"))
        for match in _INSERT.finditer(text):
            columns, values = match.group(1), match.group(2)
            if "created_by_kind" not in columns or "'sift'" not in values:
                continue
            if "created_by_via" in columns:
                continue
            guilty.append(f"{_named(path)}  ({columns.strip()[:70]})")
    assert not guilty, (
        "these statements write 'sift' into `created_by_kind` and name no pass, so the rows they\n"
        "make are indistinguishable from the ones written before anything recorded a pass:\n\n  "
        + "\n  ".join(guilty)
        + "\n\nName `created_by_via` in the statement, or take a `Made` from the caller."
    )


def test_the_exemption_is_real() -> None:
    """A named exemption's file exists and still carries the default."""
    for name in EXEMPT:
        path = SOURCE / name
        assert path.exists(), f"{name} is exempted from this gate and is not there any more"
        assert _DEFAULTED.search(path.read_text(encoding="utf-8")), (
            f"{name} no longer defaults `made`, so its line in EXEMPT is stale. Delete it."
        )


def test_the_gate_can_see_each_fault() -> None:
    """Known positives: silence and success look the same in a text search."""
    assert _DEFAULTED.search("    async def create_person(self, name: str, made: Made = X) -> str:")
    assert _DEFAULTED.search("    made: Made | None = None,")
    assert not _DEFAULTED.search("def registry_of(*made: Made, machine: int | None = None):")
    assert not _DEFAULTED.search("    made: Made,")

    assert _BARE_SIFT.search('    return Made("sift", None)')
    assert _BARE_SIFT.search("    who = Made(kind='sift')")
    assert not _BARE_SIFT.search('    return Made("sift", via)')
    assert not _BARE_SIFT.search("    return by_sift(VIA_FOLDER)")

    # nosemgrep: sift-no-string-built-sql
    forgot = "INSERT INTO tags (id, created_by_kind) VALUES (?, 'sift')"
    match = _INSERT.search(forgot)
    assert match is not None
    assert "created_by_kind" in match.group(1)
    assert "'sift'" in match.group(2)
    assert "created_by_via" not in match.group(1)
