# SPDX-License-Identifier: AGPL-3.0-or-later
"""The docs site's API reference, written from the OpenAPI file the server publishes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

METHODS = ("get", "post", "put", "patch", "delete")


def _operations(spec: dict[str, Any]) -> dict[str, list[tuple[str, str, str]]]:
    by_tag: dict[str, list[tuple[str, str, str]]] = {}
    for path, item in sorted(spec["paths"].items()):
        for method in METHODS:
            operation = item.get(method)
            if operation is None:
                continue
            tag = (operation.get("tags") or ["other"])[0]
            by_tag.setdefault(tag, []).append((method.upper(), path, operation.get("summary", "")))
    return by_tag


def page(source: Path, header: str) -> str:
    spec = json.loads(source.read_text(encoding="utf-8"))
    lines = [
        "---",
        "title: API reference",
        "description: Every route Sift answers, grouped by what it is about.",
        "---",
        "",
        header.format(source="frontend/openapi.json"),
        "",
        "This reference lists every route Sift answers, grouped by what it's about. "
        "The full schema is the [OpenAPI file](https://github.com/nuvibes/sift/blob/main/frontend/openapi.json) in the source tree. "
        "The routes serve Sift's own browser client and desktop app, and they change between releases without notice.",
        "",
    ]
    for tag, operations in sorted(_operations(spec).items()):
        lines += [f"## {tag.capitalize()}", ""]
        lines += [f"- `{method} {path}`: {summary}" for method, path, summary in operations]
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
