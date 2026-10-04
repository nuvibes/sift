# SPDX-License-Identifier: AGPL-3.0-or-later
"""One field of a file's record that a save moved, said with what it was and what it became."""

from __future__ import annotations

from collections.abc import Mapping

from sift.kernel.text import non_empty_str

#: The fields whose values read as words in a line. The details are paragraphs and the links a
#: list, so a save of those names the field alone.
PLAIN = frozenset({"title", "release_date", "production_date", "site_code", "music"})


def field_changed(
    payload: Mapping[str, object], words: Mapping[str, str]
) -> tuple[str, str] | None:
    """The two halves around the thing edited: ("changed the title", " from A to B"), or None.

    Only a save that moved ONE plain field and recorded both values (`browse.service`'s edit).
    """
    listed = payload.get("fields")
    if not isinstance(listed, list) or len(listed) != 1 or not isinstance(listed[0], Mapping):
        return None
    one = listed[0]
    field = one.get("field")
    if not isinstance(field, str) or field not in PLAIN or not {"before", "after"} <= one.keys():
        return None
    before, after = one["before"], one["after"]
    if not all(value is None or isinstance(value, str) for value in (before, after)):
        return None
    what = words.get(field, field)
    was, now = non_empty_str(before), non_empty_str(after)
    if now is None:
        return f"cleared {what}", ""
    if was is None:
        return f"set {what}", f" to {now}"
    return f"changed {what}", f" from {was} to {now}"
