# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pack stage's reads: the vendored tools declared, the fuse wire, the shell run."""

from __future__ import annotations

import json
import mmap
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import release_gates
from release_common import MANIFEST, ReleaseFailed
from release_runtime import _pinned_wheels


def _vendored_files() -> tuple[set[str], set[str], set[str]]:
    """What vendor/bin holds by the manifest: executables, program folders, notices under bin/."""
    declared = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # Executables only: a `bin/` destination takes DLLs by pattern and has no one name to look for.
    wanted: set[str] = set()
    folders: set[str] = set()
    notices: set[str] = set()
    for tool_entry in _every_tool(declared):
        _named_in(tool_entry, wanted, folders)
        declared_here = _extras_of(tool_entry, wanted, notices)
        _check_a_gpl_tool_declares_its_source(tool_entry, declared_here)
    # A wheel's recipe and the files it reads are part of the source the LGPL asks for.
    notices.update(release_gates.recipe_files(_pinned_wheels()))
    notices.add(release_gates.NOTICE)
    if not wanted:
        raise ReleaseFailed(
            "no vendored tools were found in scripts/vendor_manifest.json, so this check cannot "
            "tell whether the build packed them. Read the manifest before releasing."
        )
    return wanted, folders, notices


def _named_in(tool_entry: dict[str, object], wanted: set[str], folders: set[str]) -> None:
    """One tool's executables and program folders; a hand-edited manifest can hold any shape."""
    extract = tool_entry.get("extract")
    if isinstance(extract, dict):
        for where in extract:
            if str(where).lower().endswith(".exe"):
                wanted.add(Path(str(where)).name)
    # A program built here names its one file rather than an archive member.
    built = tool_entry.get("file")
    if isinstance(built, str) and built.lower().endswith(".exe"):
        wanted.add(Path(built).name)
    tree = tool_entry.get("extract_tree")
    if isinstance(tree, dict):
        for where in tree:
            folders.add(Path(str(where).rstrip("/")).name)


def _extras_of(tool_entry: dict[str, object], wanted: set[str], notices: set[str]) -> list[str]:
    """One tool's extra files: executables to `wanted`, notices to `notices` and returned."""
    extra_files = tool_entry.get("extra_files")
    declared_here: list[str] = []
    if isinstance(extra_files, list):
        for extra in extra_files:
            dest = extra.get("dest", "") if isinstance(extra, dict) else ""
            if str(dest).lower().endswith(".exe"):
                wanted.add(Path(str(dest)).name)
            elif str(dest).startswith("bin/"):
                notices.add(str(dest)[len("bin/") :])
                declared_here.append(str(dest)[len("bin/") :])
    return declared_here


#: A licence that asks a binary to travel with its source: GPL, LGPL or AGPL, any version. Read from
#: the start of the manifest's `licence` text, which is where each entry names its own.
_SOURCE_LICENCE = re.compile(r"^[A-Z]?GPL-")


def _check_a_gpl_tool_declares_its_source(tool: dict[str, object], shipped: list[str]) -> None:
    """Refuse a GPL tool that ships no source archive named for this very version."""
    licence = str(tool.get("licence", ""))
    if not _SOURCE_LICENCE.match(licence):
        return
    version = str(tool.get("version", ""))
    if version and any(rel.startswith("sources/") and version in Path(rel).name for rel in shipped):
        return
    raise ReleaseFailed(
        f"{tool.get('name', '?')} {version} is {licence.split(' ', 1)[0]} and "
        "scripts/vendor_manifest.json ships no source archive for this version under "
        f"bin/sources/ (it declares {', '.join(shipped) or 'nothing'}).\n"
        "  A GPL program is redistributed WITH its source. Add the source archive of exactly the "
        "pinned build to its extra_files, named with its version, before releasing."
    )


def _every_tool(node: object) -> list[dict[str, object]]:
    """Every entry in the manifest that names a tool, wherever the file happens to nest them."""
    found: list[dict[str, object]] = []
    if isinstance(node, dict):
        if "name" in node and ("extract" in node or "extra_files" in node):
            found.append(node)
        for value in node.values():
            found.extend(_every_tool(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_every_tool(value))
    return found


#: The marker @electron/fuses writes into an Electron binary, with the fuse wire immediately after
#: it: one version byte, one length byte, then one byte per fuse. Copied from the package rather
#: than computed, because it IS the constant: there is nothing to derive it from.
FUSE_SENTINEL = b"dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX"


#: What one byte of the wire means. @electron/fuses' own numbers: the first two are the characters
#: `0` and `1`, which is why a wire reads as a run of digits in a hex editor.
FUSE_OFF = 0x30


FUSE_ON = 0x31


FUSE_REMOVED = 0x72


FUSE_INHERIT = 0x90


def read_fuse_wire(binary: Path) -> dict[int, int]:
    """The fuse states in an Electron binary: wire version, fuse count, one byte per fuse."""
    with binary.open("rb") as handle:
        size = binary.stat().st_size
        if size == 0:
            raise ReleaseFailed(f"{binary} is empty, so it carries no fuses.")
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
            at = view.find(FUSE_SENTINEL)
            if at == -1:
                raise ReleaseFailed(
                    f"{binary} carries no fuse wire. Either it is not an Electron binary, or the "
                    "packer wrote something this script cannot read. Do not ship it until the "
                    "fuses can be read back."
                )
            wire = at + len(FUSE_SENTINEL)
            version = view[wire]
            if version != 1:
                raise ReleaseFailed(
                    f"{binary} carries fuse wire version {version}, and this script reads version "
                    "1. Electron has changed the format; update @electron/fuses and this reader "
                    "together before shipping."
                )
            length = view[wire + 1]
            return {index: view[wire + 2 + index] for index in range(length)}


class ShellRun(NamedTuple):
    """What one run of the packed shell did. `exit_code` is None when it had to be taken down."""

    exit_code: int | None
    output: str


#: How the boot check starts a process. A seam so the check can be tested without a build: every
#: way the launch can go is a return value here, and the fake in the test returns each of them.
Launcher = Callable[[list[str], float], ShellRun]


def run_the_shell(command: list[str], timeout_s: float) -> ShellRun:
    """Start the packed shell; take its whole tree down by PID (`/T`) if it does not stop."""
    started = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        output, _ = started.communicate(timeout=timeout_s)
        return ShellRun(started.returncode, output)
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/PID", str(started.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        output, _ = started.communicate()
        return ShellRun(None, output or "")
