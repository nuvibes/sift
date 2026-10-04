# SPDX-License-Identifier: AGPL-3.0-or-later
"""One place decides whether Sift may change a file in somebody's library.

Sift runs as the logged-in user with write access to every drive, so the only thing between a
mistake and somebody's library is the code that decides; a granted folder is a permission, not a
read-only mount. So there is one door, `kernel/library_write.py`, and this proves it: nobody else
reads `root.managed` (reading it is deciding) or calls `check_can_be_managed` (the filesystem half
of the same decision). Tests and the screens' models are exempt: describing and drawing are not
deciding, and the server refuses regardless.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SOURCE = Path(__file__).resolve().parents[2] / "src" / "sift"

#: The one module allowed to decide.
DOOR = SOURCE / "kernel" / "library_write.py"

#: What the flag is called on a root, and what the filesystem half is called.
THE_FLAG = "managed"
THE_FILESYSTEM_HALF = "check_can_be_managed"

#: Where the flag may be read without deciding anything, each with its reason; it stays short.
ALLOWED = {
    "kernel/library_write.py",
    # The store that owns the column writes and reads the flag, and decides no write.
    "kernel/content/library.py",
    # The models and routers carry the flag to a screen's switch.
    "slices/library_roots/models.py",
    "slices/library_roots/router.py",
    "slices/library_roots/service.py",
    # Backup and restore carry it with the row; a row without one is restored closed.
    "slices/backup/service.py",
}


def _python_files() -> list[Path]:
    return [
        path
        for path in SOURCE.rglob("*.py")
        if "tests" not in path.parts and "testing" not in path.parts
    ]


def _relative(path: Path) -> str:
    return path.relative_to(SOURCE).as_posix()


def _reads_the_flag(tree: ast.AST) -> bool:
    """Whether this module reads `something.managed`; a `managed=True` keyword passes it along."""
    return any(isinstance(node, ast.Attribute) and node.attr == THE_FLAG for node in ast.walk(tree))


def _calls_the_filesystem_half(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == THE_FILESYSTEM_HALF:
            return True
        if isinstance(node, ast.Attribute) and node.attr == THE_FILESYSTEM_HALF:
            return True
    return False


def test_only_the_door_decides_whether_a_library_may_be_changed() -> None:
    scanned = _python_files()
    # A walk that read a handful of modules is pointed somewhere wrong.
    assert len(scanned) > 200, f"only {len(scanned)} modules scanned: the walk is wrong"

    offenders: list[str] = []
    for path in scanned:
        where = _relative(path)
        if where in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _reads_the_flag(tree):
            offenders.append(f"{where} reads the `{THE_FLAG}` flag")
        if _calls_the_filesystem_half(tree):
            offenders.append(f"{where} calls `{THE_FILESYSTEM_HALF}` itself")

    assert not offenders, (
        "\nThe permission to change somebody's library is decided outside the one place that may "
        "decide it.\n\n"
        + "\n".join(f"  {one}" for one in offenders)
        + "\n\nAsk `sift.kernel.library_write` instead: `check_may_change` for one folder, or "
        "`check_root_may_change` and `check_folder_may_change` where a move has two.\n"
        "No read-only mount stands behind this check to make a mistake here impossible, "
        "which is why there is exactly one door.\n"
    )


def test_the_door_is_where_this_gate_says_it_is() -> None:
    """The door is where this gate says it is, or every check would measure a moved file."""
    assert DOOR.is_file()
    body = DOOR.read_text(encoding="utf-8")
    # The door asks the FILESYSTEM, per folder, at the moment of the write.
    for named in ("check_may_change", "check_folder_may_change"):
        assert f"def {named}" in body, named


@pytest.mark.parametrize(
    "planted",
    [
        "def sneak(root):\n    return root.managed\n",
        "from sift.kernel.content import check_can_be_managed\n\n"
        "def sneak(folder):\n    check_can_be_managed(folder)\n",
    ],
)
def test_a_second_door_is_caught(planted: str, tmp_path: Path) -> None:
    """Both fenced shapes, planted, are caught."""
    module = tmp_path / "sneaky.py"
    module.write_text(planted, encoding="utf-8")
    tree = ast.parse(module.read_text(encoding="utf-8"))

    assert _reads_the_flag(tree) or _calls_the_filesystem_half(tree)


def test_the_allowed_list_is_all_still_real() -> None:
    """Every exemption names a file that exists."""
    missing = [one for one in sorted(ALLOWED) if not (SOURCE / one).is_file()]
    assert not missing, f"exempted files that are not there any more: {missing}"
