# SPDX-License-Identifier: AGPL-3.0-or-later
"""The icon codepoints written into `app.css` still name the glyphs the font actually ships.

Almost every icon in Sift is drawn by `Icon`, which reads the generated codepoint map, so it
cannot be wrong about a glyph. A stylesheet cannot read JSON, so the handful of icons a GLOBAL rule
needs are written into `app.css` by hand, and that hand-written copy is a second copy of a generated
fact. This is what stops the two drifting.

Two ways it drifts, and neither one says anything at the time:

* the name is dropped from `src/lib/design/icons.ts`, so the subsetter stops shipping the glyph and the
  rule draws a blank box on every disclosure in the app;
* the codepoint is mistyped, which draws a different glyph or nothing at all.

Both are silent. A missing glyph in an icon font is not an error: it is a space, or whatever the
fallback font has at that codepoint.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
_APP_CSS = _FRONTEND / "src" / "app.css"
_CODEPOINTS = _FRONTEND / "src" / "lib" / "generated" / "icon-codepoints.json"

#: `--icon-keyboard-arrow-right: '\e315';` -> ("keyboard-arrow-right", "e315").
_TOKEN = re.compile(r"--icon-([a-z0-9-]+)\s*:\s*'\\([0-9a-f]{4,6})'")


def test_every_icon_token_in_the_stylesheet_is_a_glyph_the_font_ships() -> None:
    written = _TOKEN.findall(_APP_CSS.read_text(encoding="utf-8"))
    # A KNOWN POSITIVE. A regex that has quietly stopped matching agrees with everything.
    assert written, "no --icon-* tokens were found in app.css; the pattern has stopped matching"

    shipped = json.loads(_CODEPOINTS.read_text(encoding="utf-8"))
    for dashed, codepoint in written:
        name = dashed.replace("-", "_")
        assert name in shipped, (
            f"app.css draws --icon-{dashed}, but {name!r} is not in the icon font. "
            "Add it to frontend/src/lib/design/icons.ts and run `npm run fonts`."
        )
        assert shipped[name] == codepoint, (
            f"--icon-{dashed} is written as {codepoint!r} and the font ships {shipped[name]!r}."
        )
