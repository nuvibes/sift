# SPDX-License-Identifier: AGPL-3.0-or-later
"""No source file is hidden from git by an ignore rule meant for something else.

A folder named like a build's output (`build/`, `dist/`) inside the source tree matches the ignore
rule written for the real output, and every file moved into it leaves the repository without a
word: the move reads as deletions, the copies run here and nowhere else, and the repository loses

them. This reads what git ignores under the source folders and refuses anything that is source,
outside the few folders that are ignored on purpose.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]

#: Where source lives.
SOURCE_FOLDERS = ("frontend/src", "frontend/scripts", "src", "tests", "scripts", "desktop/src")

#: What counts as source.
SOURCE_SUFFIXES = (".py", ".ts", ".js", ".mjs", ".cjs", ".svelte")

#: Folders ignored on purpose, each with the reason it is never committed.
IGNORED_ON_PURPOSE = {
    "frontend/src/routes/design/": "an optional design gallery, not part of this tree",
    "frontend/src/lib/generated/": "written by the client's build from files that are committed",
    "src/sift/web/": "the built client the server hands out, made by the build",
}

#: Folder names that are a tool's own output wherever they appear.
TOOL_OUTPUT = ("/.svelte-kit/", "/__pycache__/", "/node_modules/")


def hidden_source(ignored: Iterable[str]) -> list[str]:
    """The ignored paths that are source and are not under a folder ignored on purpose."""
    return sorted(
        path
        for path in ignored
        if path.endswith(SOURCE_SUFFIXES)
        and not path.startswith(tuple(IGNORED_ON_PURPOSE))
        and not any(part in f"/{path}" for part in TOOL_OUTPUT)
    )


def test_the_rule_finds_a_test_hidden_in_a_folder_named_build() -> None:
    """A check that has never failed is indistinguishable from one that cannot."""
    planted = [
        "frontend/src/lib/build/one-veil-gate.test.ts",
        "frontend/src/routes/design/Gallery.svelte",
        "frontend/src/lib/.svelte-kit/generated/root.js",
        "src/sift/web/_app/immutable/entry/app.js",
        "src/sift/__pycache__/main.cpython-313.py",
    ]
    assert hidden_source(planted) == ["frontend/src/lib/build/one-veil-gate.test.ts"]


def test_no_source_file_is_ignored() -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not on this machine")
    # Two listings: files git does not track because a rule hides them, and files it does track
    # that a rule matches anyway (a folder re-hidden after its files were added: git keeps them,
    # but every tool that reads `.gitignore` for itself walks straight past them).
    hidden: list[str] = []
    for which in ("--others", "--cached"):
        listed = subprocess.run(
            [
                git,
                "ls-files",
                which,
                "--ignored",
                "--exclude-standard",
                "-z",
                "--",
                *SOURCE_FOLDERS,
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if listed.returncode != 0:
            pytest.skip("this tree is not a git checkout")
        hidden += [one for one in listed.stdout.decode("utf-8").split("\0") if one]
    found = hidden_source(hidden)
    assert not found, (
        "source files git ignores, so they are in no commit and in nobody else's tree. Move them "
        f"out of the folder an ignore rule matches, or un-ignore the folder: {found[:10]}"
    )
