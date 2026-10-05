# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a release proves beyond its build: the hosted suite passed on its commit, and every library
inside a shipped program is named with its licence and where its source is."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

SUITE = "suite.yml"
NOTICE = "THIRD-PARTY-NOTICES.txt"
_GROUPS = ("tools", "built", "libraries", "wheels")


def runs_path(repository: str, commit: str) -> str:
    """The API read listing the suite's runs on one commit."""
    return f"repos/{repository}/actions/workflows/{SUITE}/runs?head_sha={commit}&per_page=100"


def suite_verdict(answer: dict[str, object] | None, commit: str) -> str | None:
    """None when the newest suite run on `commit` passed, else what was found, in one sentence."""
    short = commit[:12]
    if answer is None:
        return f"GitHub shows no workflow {SUITE}, so nothing says the suite passed on {short}."
    listed = answer.get("workflow_runs")
    runs = [one for one in listed if isinstance(one, dict)] if isinstance(listed, list) else []
    runs = [one for one in runs if one.get("head_sha") == commit]
    if not runs:
        return f"the hosted suite has no run on {short}: start {SUITE} on it, then publish."
    # A run started again keeps its id, so the highest id is the newest verdict.
    newest = max(runs, key=lambda one: one["id"] if isinstance(one.get("id"), int) else 0)
    where = newest.get("html_url", "no address given")
    if newest.get("status") != "completed":
        return f"the hosted suite on {short} is still running ({where}): publish once it passes."
    if newest.get("conclusion") != "success":
        found = newest.get("conclusion")
        return f"the hosted suite on {short} concluded {found} ({where}), and a release needs it green."
    return None


def read_manifest(path: Path) -> dict[str, object]:
    found = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(found, dict):
        raise ValueError(f"{path} is not a JSON object.")
    return found


def _programs(manifest: dict[str, object]) -> Iterator[dict[str, object]]:
    for group in _GROUPS:
        entries = manifest.get(group)
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                yield entry


def _inside(program: dict[str, object]) -> list[dict[str, object]]:
    listed = program.get("inside")
    return [one for one in listed if isinstance(one, dict)] if isinstance(listed, list) else []


def _title(program: dict[str, object]) -> str:
    return f"{program.get('name') or program.get('package')} {program.get('version', '')}".strip()


def unrecorded(manifest: dict[str, object]) -> list[str]:
    """`program: library` for each library inside a program with no licence or no source given."""
    return [
        f"{_title(program)}: {one.get('name')}"
        for program in _programs(manifest)
        for one in _inside(program)
        if not one.get("licence") or not one.get("source")
    ]


def recipe_files(wheels: list[dict[str, object]]) -> set[str]:
    found: set[str] = set()
    for wheel in wheels:
        inputs = wheel.get("recipe_inputs")
        for one in [wheel.get("recipe", ""), *(inputs if isinstance(inputs, list) else [])]:
            found.add(f"sources/{Path(str(one)).name}")
    return found


def _source_of(program: dict[str, object]) -> str:
    source = program.get("source")
    if isinstance(source, dict):
        return f"{source.get('repository')} at {source.get('commit')}"
    if isinstance(source, str):
        return source
    extras = program.get("extra_files")
    version = str(program.get("version", ""))
    for extra in extras if isinstance(extras, list) else []:
        dest = str(extra.get("dest", "")) if isinstance(extra, dict) else ""
        if dest.startswith("bin/sources/") and version and version in dest:
            return f"{dest.removeprefix('bin/')} (beside this file)"
    return "not given"


def notice(manifest: dict[str, object]) -> str:
    """The notice the installer carries beside the programs, written from the manifest."""
    lines = [
        "Libraries inside the programs Sift ships",
        "",
        "Every library compiled or bundled into each program is listed with its licence and where",
        "its source is published. Licence texts and source archives that ship are in this folder.",
    ]
    for program in _programs(manifest):
        lines += ["", _title(program), f"  licence: {program.get('licence', 'not given')}"]
        lines.append(f"  source:  {_source_of(program)}")
        for one in _inside(program):
            lines.append(f"  - {one.get('name')}, {one.get('version')}")
            lines.append(f"      licence: {one.get('licence') or 'not recorded'}")
            lines.append(f"      source:  {one.get('source') or 'not recorded'}")
    return "\n".join(lines) + "\n"


def write_notice(manifest_path: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    written = folder / NOTICE
    written.write_text(notice(read_manifest(manifest_path)), encoding="utf-8", newline="\n")
    return written
