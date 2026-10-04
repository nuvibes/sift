# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every service lookup a download makes is timed, retried and paced by the download's settings.

The Downloads settings promise one meaning on every Site, and a download reaches a Site's service
through the impersonating client (`curl`) as well as through the tools and Sift's own fetch. A
lookup handed no policy keeps a fixed timeout and never asks again, whatever the settings say, and
nothing on screen shows the difference. So this reads the slice's source: every call to the client
passes `policy=`, or is named below with the reason it is not part of a download's traffic.
"""

from __future__ import annotations

import ast
from pathlib import Path

SLICE = Path(__file__).resolve().parents[1]
SEAMS = frozenset({"guarded_get", "guarded_post"})

#: The functions whose lookups are not a download's own requests, with the reason.
EXEMPT: dict[str, str] = {
    "creator_of": "reads a name for a file that has already landed, best effort, on a short "
    "ceiling of its own; a slow page must not hold the finished download",
    "music_of": "reads a song name for a file that has already landed, for the same reason",
}


def _misses(source: str, where: str) -> list[str]:
    """Calls to the client inside `source` that pass no policy, outside the exempt functions."""
    missing: list[str] = []
    tree = ast.parse(source)
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if function.name in EXEMPT:
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if not (isinstance(owner, ast.Name) and owner.id == "curl"):
                continue
            if node.func.attr not in SEAMS:
                continue
            if "policy" not in {keyword.arg for keyword in node.keywords}:
                missing.append(f"{where}:{node.lineno} {function.name} calls {node.func.attr}()")
    return missing


def test_every_service_lookup_is_given_the_downloads_policy() -> None:
    missing: list[str] = []
    for path in sorted(SLICE.rglob("*.py")):
        if "tests" in path.parts:
            continue
        missing += _misses(path.read_text(encoding="utf-8"), str(path.relative_to(SLICE)))
    assert not missing, (
        "these lookups keep a fixed timeout and no retries whatever the Downloads settings say:\n  "
        + "\n  ".join(missing)
    )


def test_the_gate_names_a_lookup_given_no_policy() -> None:
    """A gate that cannot fail proves nothing: a planted call with no policy is named."""
    planted = (
        "async def resolve_x(url, proxy, policy):\n"
        "    return await curl.guarded_get(url, proxy=proxy)\n"
    )
    assert _misses(planted, "planted.py") == ["planted.py:2 resolve_x calls guarded_get()"]
    exempt = "async def creator_of(url, proxy):\n    return await curl.guarded_get(url)\n"
    assert _misses(exempt, "planted.py") == []
