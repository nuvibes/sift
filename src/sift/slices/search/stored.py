# SPDX-License-Identifier: AGPL-3.0-or-later
"""A kept filter names each thing by its ID, and reads back with the name it has today.

## Why ids

A filter stored as the address it is saved from (`tags=harbour&in=Sift+Downloads`) names a tag
the way a facet row writes it, by its NAME. Renaming the tag `harbour` to `quayside` then leaves
the filter asking for a tag nobody has: it matches no file, and its chip goes on reading
`tags: harbour`, a name nothing in the library carries any more. The search index is not involved
(it follows a rename); the stored filter is the stale copy.

## What is kept, and what is shown

KEPT: every value of an entity field (a tag, a person, a Site, a collection, a Photo Set, a
folder) is written as the id of the one thing it names, when it names exactly one. A value that
names several things (two people who share a name, two library folders each holding a "2024")
is kept as the name, because that is what it has always meant and choosing one of them would
narrow somebody's filter behind their back. A value that names nothing is kept as it was.

SHOWN: when the list is read, each id is put back as the name it goes by NOW, so the address the
filter applies and the chips that describe it say today's name, and a rename needs no rewrite
anywhere. The swap happens only where that name leads back to exactly the same id; otherwise the
id stays in the address (it filters exactly as the id did) and a note carries the name for the
chip to read. An id that names nothing any more is the note's other case: a thing deleted since
the filter was kept, drawn as such and matching no file, as a name nobody has already does.

Nothing here builds SQL or reads a table: every lookup goes through the compiler's scoped
resolvers, so a name becomes an id, and an id a name, only for a viewer who may be shown it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode

from starlette.datastructures import QueryParams

from sift.kernel.access import ENTITY_FACETS, Viewer
from sift.kernel.ids import is_id
from sift.slices.search.filters import (
    ALIASES,
    ENTITY_FIELDS,
    Field,
    FilterCompiler,
    Group,
    Negated,
    Node,
    Presence,
    Term,
    parse,
    parse_tokens,
    presence_word,
    write,
)

#: Every parameter name that spells an entity field, the older spellings included, because a
#: filter kept under `platforms=` is a filter over Sites exactly as one kept under `sites=` is.
ENTITY_PARAMETERS: dict[str, Field] = {one.value: one for one in ENTITY_FIELDS} | {
    old: one for old, one in ALIASES.items() if one in ENTITY_FIELDS
}

#: The parameter that holds typed text. Its filters are read by the tokenizer, not split here.
TYPED = "q"

#: What to put in place of a value, per field: `{Field.TAGS: {"harbour": "01K..."}}`.
Swaps = Mapping[Field, Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class Noted:
    """One value a chip cannot read from the address alone, and what it goes by now.

    `spelled` is the parameter name as it is written in the filter (`platforms`, not `sites`),
    because that is the name the chip is drawn under. `name` is None where the thing is gone: an
    id nothing answers to, or a name nothing is called any more.
    """

    spelled: str
    value: str
    name: str | None


#: The facets of a wall of things that keep a thing by its id, and the field each one names.
_WALL_IDS: Mapping[str, Mapping[str, Field]] = {
    "person": {"tags": Field.TAGS},
    "site": {"tags": Field.TAGS, "parent": Field.SITES},
    "tag": {"parent": Field.TAGS},
    "collection": {"tags": Field.TAGS},
    "photo_set": {"tags": Field.TAGS},
}


def _split(raw: str) -> list[str]:
    """A parameter's value cut at its separators, the separators kept as pieces of their own.

    Quote-aware, as the parser is; kept as written, so only the values swapped change spelling.
    """
    pieces: list[str] = []
    current: list[str] = []
    quoted = False
    for character in raw:
        if character == '"':
            quoted = not quoted
        if character in "|," and not quoted:
            pieces.append("".join(current))
            pieces.append(character)
            current = []
            continue
        current.append(character)
    pieces.append("".join(current))
    return pieces


def _plain(piece: str) -> str:
    return piece.replace('"', "").strip()


def _spelled_value(value: str) -> str:
    """A value as a parameter holds it: quoted where a separator or a minus would misread it."""
    if value.startswith("-") or any(character in value for character in ",|"):
        return '"' + value.replace('"', "") + '"'
    return value


def _is_a_value(found: Field, value: str) -> bool:
    """Whether this piece names a thing, rather than asking whether the dimension is there."""
    return bool(value) and presence_word(found, value) is None


def _parameter_values(found: Field, raw: str) -> list[str]:
    body = raw[1:] if raw.startswith("-") else raw
    return [
        _plain(piece)
        for piece in _split(body)
        if piece not in ("|", ",") and _is_a_value(found, _plain(piece))
    ]


def _typed_leaves(node: Node) -> list[Term]:
    if isinstance(node, Term):
        return [node] if node.field in ENTITY_FIELDS else []
    if isinstance(node, Presence):
        return []
    if isinstance(node, Negated):
        return _typed_leaves(node.part)
    return [leaf for part in node.parts for leaf in _typed_leaves(part)]


def entity_values(query: str) -> dict[Field, dict[str, set[str]]]:
    """Every value of an entity field in a kept filter, with the spellings it is written under.

    Both halves of a filter: the named parameters a panel writes and the tokens left in `q` that
    a parameter cannot spell. What the gate reads, and what the two directions below look up.
    """
    found: dict[Field, dict[str, set[str]]] = {}
    for name, raw in parse_qsl(query, keep_blank_values=True):
        if name == TYPED:
            for leaf in _typed_leaves(parse_tokens(raw).where):
                value = leaf.value.strip()
                if _is_a_value(leaf.field, value):
                    found.setdefault(leaf.field, {}).setdefault(value, set()).add(leaf.field.value)
            continue
        field_of = ENTITY_PARAMETERS.get(name)
        if field_of is None:
            continue
        for value in _parameter_values(field_of, raw):
            found.setdefault(field_of, {}).setdefault(value, set()).add(name)
    return found


def _swapped_parameter(found: Field, raw: str, swaps: Swaps) -> str:
    table = swaps.get(found, {})
    sign = "-" if raw.startswith("-") else ""
    body = raw[len(sign) :]
    out: list[str] = []
    for piece in _split(body):
        value = _plain(piece)
        if piece in ("|", ",") or value not in table:
            out.append(piece)
        else:
            out.append(_spelled_value(table[value]))
    return sign + "".join(out)


def _swapped_node(node: Node, swaps: Swaps) -> Node:
    if isinstance(node, Term):
        replacement = swaps.get(node.field, {}).get(node.value.strip())
        return node if replacement is None else Term(node.field, replacement)
    if isinstance(node, Presence):
        return node
    if isinstance(node, Negated):
        return Negated(_swapped_node(node.part, swaps))
    return Group(node.op, tuple(_swapped_node(part, swaps) for part in node.parts))


def _swapped_typed(raw: str, swaps: Swaps) -> str:
    """Typed text with its entity values swapped, or exactly as it was where none is.

    Written back through the parser's own writer, which is the one spelling of a filter there is;
    the free words follow the filters, and the two are independent of each other, so the order is
    not a change in meaning. Untouched text is returned untouched, so a filter with nothing to
    swap is never respelled.
    """
    parsed = parse_tokens(raw)
    swapped = _swapped_node(parsed.where, swaps)
    if swapped == parsed.where:
        return raw
    return " ".join(part for part in (write(swapped), parsed.text or "") if part)


def swapped(query: str, swaps: Swaps) -> str:
    """A kept filter with these values put in place of those, and nothing else changed."""
    if not any(swaps.values()):
        return query
    pairs: list[tuple[str, str]] = []
    for name, raw in parse_qsl(query, keep_blank_values=True):
        field_of = ENTITY_PARAMETERS.get(name)
        if name == TYPED:
            pairs.append((name, _swapped_typed(raw, swaps)))
        elif field_of is not None:
            pairs.append((name, _swapped_parameter(field_of, raw, swaps)))
        else:
            pairs.append((name, raw))
    return urlencode(pairs)


async def as_kept(compiler: FilterCompiler, viewer: Viewer, query: str) -> str:
    """The filter as it is stored: each name that names exactly one thing, as that thing's id.

    Resolved as the wall would resolve it for this viewer, so the stored filter matches what the
    one on screen matched. See the head of this module for the two cases kept as names.
    """
    swaps: dict[Field, dict[str, str]] = {}
    for found, values in entity_values(query).items():
        names = sorted(value for value in values if not is_id(value))
        if not names:
            continue
        answered = await compiler.resolve(viewer, found, names)
        swaps[found] = {value: ids[0] for value, ids in answered.items() if len(ids) == 1}
    return swapped(query, swaps)


async def as_shown(
    compiler: FilterCompiler, viewer: Viewer, query: str
) -> tuple[str, tuple[Noted, ...]]:
    """The filter as it reads today, and the notes a chip needs for what the address cannot say.

    An id becomes today's name where that name leads straight back to it. Everything the address
    cannot say for itself is noted: an id kept because its name is shared (with the name), an id
    that answers to nothing (gone), and a name nothing answers to any more (gone, by that name).
    """
    swaps: dict[Field, dict[str, str]] = {}
    notes: list[Noted] = []
    for found, values in entity_values(query).items():
        ids = sorted(value for value in values if is_id(value))
        named = await compiler.names_of(viewer, found, ids)
        back = await compiler.resolve(viewer, found, sorted(set(named.values())))
        for key in ids:
            name = named.get(key)
            if name is not None and back.get(name) == (key,):
                swaps.setdefault(found, {})[key] = name
                continue
            notes.extend(Noted(spelled, key, name) for spelled in sorted(values[key]))
        names = sorted(value for value in values if not is_id(value))
        answered = await compiler.resolve(viewer, found, names) if names else {}
        for value in names:
            if not answered.get(value):
                notes.extend(Noted(spelled, value, None) for spelled in sorted(values[value]))
    return swapped(query, swaps), tuple(notes)


def _typed_query(texts: Sequence[str]) -> str:
    """Several pieces of typed filter text as ONE kept filter, one `q` apiece, in order.

    So a whole list of them (every cell of every saved Theater wall a user keeps) is resolved in one
    lookup per field rather than one per piece, and each comes back in its own place.
    """
    return urlencode([(TYPED, text) for text in texts])


def _typed_texts(query: str, count: int) -> list[str]:
    texts = [value for name, value in parse_qsl(query, keep_blank_values=True) if name == TYPED]
    if len(texts) != count:  # pragma: no cover - every `q` written above is read back
        raise ValueError("a typed filter came back with a different number of pieces")
    return texts


async def typed_as_kept(
    compiler: FilterCompiler, viewer: Viewer, texts: Sequence[str]
) -> list[str]:
    """Typed filter text (`tags:harbour people:"Wren Halloway"`) as it is stored: by id.

    The typed half of `as_kept`, for a filter kept as the words a search box would hold rather than
    as an address: a saved Theater wall keeps each cell that way. The same rule, so a name shared by
    several things or naming nothing stays as it was typed.
    """
    if not texts:
        return []
    return _typed_texts(await as_kept(compiler, viewer, _typed_query(texts)), len(texts))


async def typed_as_shown(
    compiler: FilterCompiler, viewer: Viewer, texts: Sequence[str]
) -> list[str]:
    """Typed filter text kept by id, as it reads today: each id as the name it goes by now.

    Where the name would not lead back to exactly that id (a shared name, or a thing gone), the id
    stays in the text: it filters exactly as it did, and it is what the text has to say about it.
    """
    if not texts:
        return []
    shown, _ = await as_shown(compiler, viewer, _typed_query(texts))
    return _typed_texts(shown, len(texts))


async def names_only_gone(compiler: FilterCompiler, viewer: Viewer, query: str) -> bool:
    """Whether a kept filter over files filters by named things alone, and every one is gone."""
    asked = parse(QueryParams(query))
    if asked.text or asked.filings:
        return False
    if not all(isinstance(one, Term) and one.field in ENTITY_FIELDS for one in asked.leaves()):
        return False
    named = {value for values in entity_values(query).values() for value in values}
    _, notes = await as_shown(compiler, viewer, query)
    return bool(named) and named <= {one.value for one in notes if one.name is None}


async def wall_names_only_gone(
    compiler: FilterCompiler, viewer: Viewer, wall: str, query: str
) -> bool:
    """The same for a kept filter on a wall of things, whose facets keep ids."""
    held = _WALL_IDS.get(wall, {})
    named: dict[Field, set[str]] = {}
    for key, value in parse_qsl(query):
        if key in held:
            named.setdefault(held[key], set()).add(value.removeprefix("-"))
        elif key == TYPED or key in ENTITY_FACETS.get(wall, {}):
            return False
    for found, ids in named.items():
        if await compiler.names_of(viewer, found, sorted(ids)):
            return False
    return bool(named)
