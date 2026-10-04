# SPDX-License-Identifier: AGPL-3.0-or-later
"""One SQL statement from a template and the fixed fragments it names.

How a rule written once reaches every statement that needs it. In the kernel on its own because
it is a text helper any slice needs, nothing to do with who may see what. Only module constants
go through it: run-time values are bound as parameters. Every check fires at import, so a marker
left in a statement or a fragment never named stops the application starting.
"""

from __future__ import annotations

import re

#: A fragment marker: `{{NAME}}`, capitals and underscores only.
_MARKER = re.compile(r"\{\{[A-Z_]+\}\}")


def _refuse_a_marker_in_a_comment(template: str) -> None:
    """Refuse a template that names a marker inside a `--` comment.

    Substitution is plain text, so a marker in a comment is replaced too, and the comment then
    swallows the fragment's first line and leaves the rest as live SQL: a syntax error on every
    request. Checked at import, like `splice`'s own checks.
    """
    for line in template.splitlines():
        comment = line.find("--")
        if comment >= 0 and _MARKER.search(line[comment:]) is not None:
            raise RuntimeError(f"a fragment marker is written inside a comment: {line.strip()}")


def splice(template: str, **fragments: str) -> str:
    """One statement from a template and the fragments it names as `{{NAME}}`.

    Each fragment is a module constant. A template naming a fragment it was not given, or given one
    it never names, fails at import rather than running with a marker left in it.
    """
    _refuse_a_marker_in_a_comment(template)
    text = template
    for name, fragment in fragments.items():
        marker = "{{" + name + "}}"
        if marker not in text:
            raise RuntimeError(f"the statement never names {marker}")
        text = text.replace(marker, fragment)
    left = _MARKER.search(text)
    if left is not None:
        raise RuntimeError(f"the statement still names {left.group(0)} and nothing filled it")
    return text
