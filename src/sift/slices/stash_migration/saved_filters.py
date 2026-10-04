# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stash's saved filters, written as Sift's saved searches where every part of one translates.

A filter comes across whole or not at all. Half a filter is a different question kept under the
old name: "PMV, rated four and up, not organized" without its last part finds a list the person
never asked for, and nothing on the screen would say so. So each filter either becomes one query in
Sift's own words, or it is named in the run's report with the part that could not be said.

Only the two lists Sift keeps as files: Stash's scenes (videos) and images (pictures). Its filters
over performers, studios, tags, groups and galleries ask about another noun, and Sift's People,
Sites and Tags walls keep their own facets; those are reported as not brought.

The words written are the typed query language (`tags:`, `people:`, `sites:`, `rating:`,
`o_count:`, `media:`), because that is what a saved search holds and what its wall reads back.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import unquote

from sift.slices.stash_migration.reader import SavedFilter, stars

#: The lists whose filters can become a search over files, and the media each one means.
_MEDIA = {"SCENES": "video", "IMAGES": "image"}

#: What a filter kept on the pictures list is called here, so it does not replace a scene filter
#: that shares its name (both lists in Stash have a "Default").
PICTURES_SUFFIX = " (pictures)"

#: Stash's criteria that name rows of its own, and the query word each one is in Sift.
_NAMED = {"tags": "tags", "performers": "people", "studios": "sites"}


@dataclass(frozen=True)
class Translated:
    """A filter as a saved search: its name and its query. Or, when `why` is set, the reason not."""

    name: str
    query: str = ""
    why: str = ""


def _quoted(name: str) -> str:
    """A name as one value: quoted, because names hold spaces, commas and pipes."""
    return '"' + name.replace('"', "'") + '"'


class _Untranslatable(Exception):
    """One part of a filter that Sift's words cannot say. The message names it for the report."""


def _items(value: object) -> tuple[list[object], list[object], int]:
    """The chosen ids, the excluded ids and the depth of one of Stash's multi-row criteria."""
    if isinstance(value, list):
        return list(value), [], 0
    if not isinstance(value, Mapping):
        return [], [], 0
    items = value.get("items") or []
    excluded = value.get("excluded") or []
    depth = value.get("depth") or 0
    return (
        list(items) if isinstance(items, list) else [],
        list(excluded) if isinstance(excluded, list) else [],
        int(depth) if isinstance(depth, int) else 0,
    )


def _id_of(item: object) -> int | None:
    raw = item.get("id") if isinstance(item, Mapping) else item
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def _label_of(item: object) -> str:
    return unquote(str(item.get("label") or "")) if isinstance(item, Mapping) else ""


def _named(
    field: str, criterion: Mapping[str, object], names: Callable[[int], str | None]
) -> list[str]:
    """`tags`, `performers` or `studios` as query tokens."""
    word = _NAMED[field]
    modifier = str(criterion.get("modifier") or "")
    if modifier == "IS_NULL":
        return [f"-{word}"]
    if modifier == "NOT_NULL":
        return [f"{word}:any"]
    chosen, excluded, depth = _items(criterion.get("value"))
    if depth != 0:
        # Stash reaching into sub-tags or child studios is a question about a tree Sift reads
        # another way; saying it without the depth would find fewer files than Stash did.
        raise _Untranslatable(f"{field} including those under them")

    def spelled(item: object) -> str:
        found = _id_of(item)
        name = names(found) if found is not None else None
        name = name or _label_of(item)
        if not name:
            raise _Untranslatable(f"{field} Sift cannot name")
        return _quoted(name)

    tokens: list[str] = []
    if chosen:
        values = [spelled(one) for one in chosen]
        if modifier == "INCLUDES":
            tokens.append(f"{word}:" + "|".join(values))
        elif modifier == "INCLUDES_ALL":
            tokens.append(f"{word}:" + ",".join(values))
        elif modifier == "EXCLUDES":
            tokens += [f"-{word}:{one}" for one in values]
        else:
            raise _Untranslatable(f"{field} {modifier.lower()}")
    tokens += [f"-{word}:{spelled(one)}" for one in excluded]
    return tokens


def _number(value: object, key: str) -> int | None:
    found = value.get(key) if isinstance(value, Mapping) else None
    try:
        return None if found is None else int(str(found))
    except ValueError:
        return None


def _scaled(field: str, criterion: Mapping[str, object]) -> list[str]:
    """`rating100` (as stars) or `o_counter` as query tokens."""
    word, point = ("rating", stars) if field == "rating100" else ("o_count", lambda n: n)
    modifier = str(criterion.get("modifier") or "")
    if modifier == "IS_NULL":
        return [f"{word}:none"] if word == "rating" else [f"{word}:0"]
    if modifier == "NOT_NULL":
        return [f"-{word}:none"] if word == "rating" else [f"{word}:1+"]
    value = criterion.get("value")
    low, high = _number(value, "value"), _number(value, "value2")
    if low is None:
        raise _Untranslatable(f"{field} with no number")
    first = point(low)
    if modifier == "EQUALS":
        return [f"{word}:{first}"]
    if modifier == "NOT_EQUALS":
        return [f"-{word}:{first}"]
    if modifier == "GREATER_THAN":
        # Stash's bounds are strict and Sift's are not, so the bound moves by one before it is
        # scaled: above 80 is 81 and up, which is eight stars and up.
        return [f"{word}:{point(low + 1)}+"]
    if modifier == "LESS_THAN":
        return [f"{word}:{point(low - 1)}-"]
    if modifier == "BETWEEN" and high is not None:
        return [f"{word}:{first}..{point(high)}"]
    raise _Untranslatable(f"{field} {modifier.lower()}")


def translate(
    one: SavedFilter,
    *,
    tag: Callable[[int], str | None],
    person: Callable[[int], str | None],
    site: Callable[[int], str | None],
) -> Translated:
    """One of Stash's saved filters as a saved search over files, or the reason it cannot be one.

    `tag`, `person` and `site` read one of Stash's ids back as the name it came across under, so a
    filter names what the library now holds rather than the label Stash stored when it was saved.
    """
    media = _MEDIA.get(one.mode)
    name = one.name + (PICTURES_SUFFIX if one.mode == "IMAGES" else "")
    if media is None:
        return Translated(name, why=f"a filter over {one.mode.lower() or 'another list'}")
    if not one.name:
        return Translated(name, why="a filter with no name")
    names = {"tags": tag, "performers": person, "studios": site}
    tokens = [f"media:{media}"]
    try:
        for field, criterion in sorted(one.criteria.items()):
            if not isinstance(criterion, Mapping):
                raise _Untranslatable(field)
            if field in _NAMED:
                tokens += _named(field, criterion, names[field])
            elif field in ("rating100", "o_counter"):
                tokens += _scaled(field, criterion)
            else:
                raise _Untranslatable(field)
    except _Untranslatable as missing:
        return Translated(name, why=str(missing))
    typed = str(one.find.get("q") or "").strip()
    if typed:
        tokens.append(typed)
    return Translated(name, query=" ".join(tokens))
