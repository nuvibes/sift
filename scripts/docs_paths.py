# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a docs page writes a place in Settings, read by the generator that writes it and the gate.

A path is the breadcrumb the settings search takes pasted, `Settings > Section > Row`, drawn as a
plain link in the body face to the pane and its row:
`[Settings > Privacy > Lock](/settings/privacy#vault.lock_on_blur)`. It is a place to go, not text
to type, so it is never in code ticks: ticks drew it in a monospace face on a grey ground.

The sources the generator reads (CHANGELOG.md, a registration's help) keep the breadcrumb in code
ticks, where the tree's own documents gate it (tests/gates/test_paths_are_breadcrumbs.py); the
generator turns each into this markup.
"""

from __future__ import annotations

import re

#: Where any path starts, whatever it is written in: plain, bold, quoted, ticked or linked.
START = re.compile(r"\bSettings\s*>")
#: A path written right: a plain link whose text is the breadcrumb.
LINKED = re.compile(r"\[(Settings\s*>[^\]`]*)\]\(([^)\s]+)\)")
#: A path in code ticks, as the generator's sources write it.
TICKED = re.compile(r"`(Settings\s*>[^`]*)`")
#: A path in plain prose: it runs to the sentence's punctuation. One that runs past its row names
#: no pane, stays unlinked, and the path gate says so.
PLAIN = re.compile(r"\bSettings(?:\s*>\s*[^.,;:`>()\[\]\n]+)+")
#: The address a path links to: the pane, and the row's key when the path names a row.
DEEP_LINK = re.compile(r"^/settings/([a-z0-9-]+)(?:#([\w.-]+))?$")


def link(path_text: str, address: str) -> str:
    """The one way a page writes a path."""
    return f"[{path_text}]({address})"
