# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every job type is claimed with a name a person can read, so no stored id reaches a screen.

Activity lists the queue's rows and offers a Type choice built from them, each kind by the name
its handler declares. A kind with no name would be offered by its id, an identifier among the
words of the list. The list names only what a handler claims and folds the rest (rows left by a
type whose handler has gone) under "Older tasks"; this holds the other half, that everything a
handler claims has a name, and one that reads as words rather than as an id: not blank, not the
type itself, no underscore, a capital first.

Two checks, because each sees what the other cannot. The source is read for the call itself, so a
registration with no `name=` fails here before anything is built; the application is started and
what it claimed is read, so a name built at run time (an f-string, a table) is held too. And no
two kinds share a name: the Type list offers each by its name, and two choices that read "Checking
the library" (Generate's and Identify's) could not be told apart.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.jobs.worker_pool import registered_job_names
from sift.main import create_app

pytestmark = [pytest.mark.gate, pytest.mark.unit]

_SOURCE = Path(__file__).resolve().parents[2] / "src" / "sift"


def unnamed_registrations(source: str) -> list[int]:
    """The lines of every `register_handler(...)` call in this source that passes no `name=`."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        called = node.func.id if isinstance(node.func, ast.Name) else None
        if isinstance(node.func, ast.Attribute):
            called = node.func.attr
        if called != "register_handler":
            continue
        if not any(keyword.arg == "name" for keyword in node.keywords):
            lines.append(node.lineno)
    return lines


def unreadable_names(names: dict[str, str]) -> list[str]:
    """The job types whose name reads as an id rather than as words, each with its name."""
    return sorted(
        f"{job_type}: {name!r}"
        for job_type, name in names.items()
        if not name.strip()
        or name.strip() == job_type
        or "_" in name
        or not name.strip()[0].isupper()
    )


def shared_names(names: dict[str, str]) -> list[str]:
    """Every name more than one job type claims, each with the types that claim it.

    The Type list offers each kind by its name, so two kinds under one name are two choices that
    read the same and cannot be told apart.
    """
    claimed: dict[str, list[str]] = {}
    for job_type, name in names.items():
        claimed.setdefault(name.strip(), []).append(job_type)
    return sorted(
        f"{name!r}: {', '.join(sorted(types))}" for name, types in claimed.items() if len(types) > 1
    )


@pytest.mark.regression
def test_every_registration_in_the_source_passes_a_name() -> None:
    missing = [
        f"{path.relative_to(_SOURCE)}:{line}"
        for path in sorted(_SOURCE.rglob("*.py"))
        if "tests" not in path.parts
        for line in unnamed_registrations(path.read_text(encoding="utf-8"))
    ]
    assert not missing, "a job type is claimed with no name to show: " + ", ".join(missing)


@pytest.mark.regression
def test_every_type_the_started_application_claims_has_a_readable_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    try:
        # Started, not merely built: every build step that registers a handler runs in the lifespan.
        with TestClient(create_app()):
            names = registered_job_names()
    finally:
        get_settings.cache_clear()

    assert names, "the started application claimed no job type at all"
    bad = unreadable_names(names)
    assert not bad, "these job types would show an id where a name belongs: " + ", ".join(bad)
    shared = shared_names(names)
    assert not shared, "these job types share a name the Type list cannot tell apart: " + "; ".join(
        shared
    )


def test_the_checks_see_a_planted_fault() -> None:
    """Each check refuses the shape it is for, so a green run is a run that looked."""
    planted = textwrap.dedent("""
        register_handler(SCAN, scan, name="Scanning folder")
        register_handler(PRUNE, prune)
        worker_pool.register_handler(ASK, ask, family=Family.OTHER)
    """)
    assert unnamed_registrations(planted) == [3, 4]
    assert unreadable_names(
        {
            "scan": "Scanning folder",
            "face_asked_only": "face_asked_only",
            "prune": "  ",
            "tidy": "tidy_names",
            "ask": "asking a stash-box",
        }
    ) == [
        "ask: 'asking a stash-box'",
        "face_asked_only: 'face_asked_only'",
        "prune: '  '",
        "tidy: 'tidy_names'",
    ]
    assert shared_names(
        {
            "generate": "Checking the library",
            "identify": "Checking the library ",
            "scan": "Scanning folder",
        }
    ) == ["'Checking the library': generate, identify"]
