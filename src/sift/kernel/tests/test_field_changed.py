# SPDX-License-Identifier: AGPL-3.0-or-later
"""One plain field a save moved, said with what it was and what it became, or not said at all.

Anything else (several fields, a field that reads as a paragraph, a value that is not words) is
answered with None, so the line falls back to naming what was edited without a value.
"""

from __future__ import annotations

import pytest

from sift.kernel.access.field_changed import field_changed

WORDS = {"title": "the title"}


def _one(field: str, before: object, after: object) -> dict[str, object]:
    return {"fields": [{"field": field, "before": before, "after": after}]}


@pytest.mark.parametrize(
    ("payload", "said"),
    [
        (_one("title", "Dawn", "Dusk"), ("changed the title", " from Dawn to Dusk")),
        (_one("title", None, "Dusk"), ("set the title", " to Dusk")),
        (_one("title", "Dawn", None), ("cleared the title", "")),
        # A plain field with no words of its own is named by its key.
        (_one("site_code", "A1", "B2"), ("changed site_code", " from A1 to B2")),
    ],
)
def test_one_plain_field_is_said_with_both_values(
    payload: dict[str, object], said: tuple[str, str]
) -> None:
    assert field_changed(payload, WORDS) == said


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"fields": "title"},
        {"fields": [_one("title", "a", "b")["fields"], _one("music", "c", "d")["fields"]]},
        {"fields": ["title"]},
        _one("details", "Dawn", "Dusk"),
        {"fields": [{"field": "title", "after": "Dusk"}]},
        # A value that is not words (a number a client sent) cannot be read out as one.
        _one("title", 1999, "Dusk"),
        _one("title", "Dawn", ["Dusk"]),
    ],
)
def test_anything_but_one_plain_field_with_both_words_is_not_said(
    payload: dict[str, object],
) -> None:
    assert field_changed(payload, WORDS) is None
