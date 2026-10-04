# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading what somebody typed into a query, and writing a query back out as the words it came from."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sift.kernel.access import (
    FolderDepth,
)
from sift.kernel.access.constraints import FILING_PARAMETERS, Filing
from sift.slices.search.filter_fields import (
    ALIASES,
    IMPOSSIBLE,
    MAX_DEPTH,
    MAX_TERMS,
    MAX_VALUE,
    PRESENCE_FIELDS,
    Field,
    Group,
    Negated,
    Node,
    Op,
    Presence,
    Query,
    Term,
    _leaves,
    group,
)


def _bounded(op: Op, parts: tuple[Node, ...]) -> Node:
    """A group, or the impossible query if there are more parts than a request may carry."""
    if len(parts) > MAX_TERMS:
        return IMPOSSIBLE
    return group(op, parts)


def _depth(node: Node) -> int:
    if isinstance(node, Term | Presence):
        return 1
    if isinstance(node, Negated):
        return 1 + _depth(node.part)
    return 1 + max((_depth(part) for part in node.parts), default=0)


def over_budget(query: Query) -> bool:
    """Whether a query asks for more than one request may cost."""
    leaves = query.leaves()
    return (
        len(leaves) + len(query.filings) > MAX_TERMS
        or _depth(query.where) > MAX_DEPTH
        or any(len(leaf.value) > MAX_VALUE for leaf in leaves if isinstance(leaf, Term))
    )


# --- the front-ends -------------------------------------------------------------------------


def _scan(text: str) -> list[tuple[int, str]]:
    """Split on whitespace keeping quoted runs together, and say where each piece began."""
    pieces: list[tuple[int, str]] = []
    current: list[str] = []
    quoted = False
    started = False
    begin = 0

    for index, character in enumerate(text):
        if character == '"':
            if not started:
                begin = index
            quoted = not quoted
            started = True
            # The quote is KEPT. A piece has to reach the value splitter with its quoting intact or
            # a quoted comma cannot be told from a bare one: `tags:"a,b"` is one tag and
            # `tags:a,b` is two, and stripping here would make them the same thing. `_unquote` takes
            # them off once the splitting is done.
            current.append(character)
            continue
        if character.isspace() and not quoted:
            if started:
                pieces.append((begin, "".join(current)))
            current = []
            started = False
            continue
        if not started:
            begin = index
        current.append(character)
        started = True

    if started:
        pieces.append((begin, "".join(current)))
    return pieces


def _unquote(piece: str) -> str:
    """A piece with its quoting removed, which is what everything downstream reads."""
    return piece.replace('"', "")


def _as_field(name: str) -> Field | None:
    """The field this name refers to, or None if it refers to none."""
    wanted = name.strip().lower()
    try:
        return Field(wanted)
    except ValueError:
        return ALIASES.get(wanted)


#: The word that joins two filters into "either one". Recognized whatever its case, but ONLY
#: between two filters (see `_connectives`, and the module docstring for why).
_OR = "or"


def _as_filter(piece: str) -> Node | None:
    """The filter this piece writes, or None if it is not one and should be searched for as text."""
    negated = piece.startswith("-")
    body = piece[1:] if negated else piece

    name, colon, value = body.partition(":")
    if not colon:
        # A bare word is a filter only when it is negated and names a dimension that can be asked
        # whether it is there at all. `-tags` is "nothing tagged"; a plain `tags` is a word.
        if not negated:
            return None
        found = _as_field(_unquote(body))
        if found is None or found not in PRESENCE_FIELDS:
            return None
        return Presence(found, False)

    found = _as_field(_unquote(name))
    if found is None:
        return None

    if (
        len(_alternatives(value)) > 1
        and (joined := _listed(found, f"-{value}" if negated else value)) is not None
    ):
        # Either of these, built by the SAME function the named parameters use, with the minus
        # handed to it rather than read off separately. That is what makes `-media:video|gif` and
        # `?media=-video|gif` one query: excluded, both mean "neither of these".
        return joined

    pieces = _values(value)
    if not pieces:
        # A token with nothing after the colon. Kept as a constraint naming nothing, which matches
        # nothing: the compiler refuses it explicitly rather than letting it filter by accident.
        return Negated(Term(found, "")) if negated else Term(found, "")

    parts = tuple(_leaf(found, one) for one in pieces)
    if negated:
        # `-tags:a,b` excludes both, which is "not either of them" rather than "not both of them".
        # The second reading would keep anything carrying only one of the two, which is not what a
        # minus in front of a list looks like it does.
        return group(Op.ALL, tuple(Negated(part) for part in parts))
    # Written side by side, values in a list all apply: `tags:a,b` wants both tags, which is what
    # the modal's comma-separated boxes have always meant.
    return group(Op.ALL, parts)


def _alternatives(raw: str) -> list[str]:
    """One value string, split on pipes, respecting quotes."""
    pieces: list[str] = []
    current: list[str] = []
    quoted = False

    for char in raw:
        if char == '"':
            quoted = not quoted
            current.append(char)
        elif char == "|" and not quoted:
            pieces.append("".join(current))
            current = []
        else:
            current.append(char)
    pieces.append("".join(current))
    return [piece for piece in (one.strip() for one in pieces) if piece]


def _listed(found: Field, value: str) -> Node | None:
    """A parameter's whole value as one node: pipes are choices, commas inside them are demands."""
    if value.startswith("-") and len(value) > 1:
        # The minus belongs to the WHOLE parameter, not to its first piece. `?media=-video|gif` means
        # "neither of these", which is what pressing exclude on a chip carrying two values means; read
        # per-piece it would have said "not video, or gif", which is nearly everything.
        leaves = _flattened(found, value[1:])
        if not leaves:
            return None
        return _bounded(Op.ALL, tuple(Negated(leaf) for leaf in leaves))

    choices = _alternatives(value)
    if len(choices) > 1:
        return _bounded(Op.ANY, tuple(_listed_all(found, one) for one in choices))
    pieces = _values(value)
    if not pieces:
        return None
    # `_bounded` rather than `group`, and it matters: the cap sees every value of every parameter
    # in one flat list, and folding a parameter's values into a node of their own would hide a
    # twenty-thousand-comma value from it: the exact work the cap exists to refuse.
    return _bounded(Op.ALL, tuple(_leaf(found, one) for one in pieces))


def _flattened(found: Field, value: str) -> tuple[Node, ...]:
    """Every value in a parameter as one flat run of leaves, pipes and commas alike."""
    return tuple(
        _leaf(found, piece) for one in _alternatives(value) for piece in (_values(one) or [one])
    )


def _listed_all(found: Field, value: str) -> Node:
    """One alternative: still a comma list, still all of them."""
    pieces = _values(value) or [value]
    return _bounded(Op.ALL, tuple(_leaf(found, one) for one in pieces))


def _leaf(found: Field, value: str) -> Node:
    """One value as a filter: the presence question if it asks one, otherwise the value itself."""
    present = _presence(found, value)
    if present is None:
        return Term(found, value)
    return Presence(found, present)


def parse_tokens(text: str) -> Query:
    """The typed front-end: free text with `field:value` tokens mixed in."""
    pieces = _scan(text)
    kinds = _connectives(pieces)

    buckets: list[list[Node]] = []
    words: list[str] = []
    join = False

    for (_, raw), kind in zip(pieces, kinds, strict=True):
        if kind is None:
            words.append(_unquote(raw))
            join = False
            continue
        if kind is _JOIN:
            join = True
            continue
        if join and buckets:
            buckets[-1].append(kind)
        else:
            buckets.append([kind])
        join = False

    clauses = tuple(group(Op.ANY, tuple(bucket)) for bucket in buckets)
    return Query(text=" ".join(words) or None, where=_bounded(Op.ALL, clauses))


#: Marks the piece that joins two filters. A sentinel rather than a string, so it can never be
#: confused with a filter or with a word.
_JOIN = Negated(Term(Field.TAGS, "\x00join"))


def _connectives(pieces: list[tuple[int, str]]) -> list[Node | None]:
    """What each piece is: a filter, the joining word, or free text."""
    read: list[Node | None] = []
    maybe: list[int] = []
    for index, (_, raw) in enumerate(pieces):
        # Read with its quoting intact, both here and in `_as_filter`. A quoted `"or"` is the word
        # somebody meant literally, not the connective, and a value has to reach the comma
        # splitter still quoted or `tags:"a,b"` (one tag) cannot be told from `tags:a,b` (two).
        if raw.strip().lower() == _OR:
            read.append(None)
            maybe.append(index)
            continue
        read.append(_as_filter(raw))

    for index in maybe:
        before = read[index - 1] if index > 0 else None
        after = read[index + 1] if index + 1 < len(read) else None
        # Not `is not None` on the neighbours alone: another `or` reads as None here too, and
        # `tags:a or or tags:b` is a query with a stray word in it rather than two connectives.
        if before is not None and after is not None and index + 1 not in maybe:
            read[index] = _JOIN

    return read


def quoted(value: str) -> str:
    """A value written so the parser reads it back as the same value."""
    if any(character.isspace() or character in ',"' for character in value):
        return '"' + value.replace('"', "") + '"'
    return value


def write(node: Node) -> str:
    """A filter written the way it would have been typed."""
    if isinstance(node, Term):
        return f"{node.field.value}:{quoted(node.value)}"
    if isinstance(node, Presence):
        return f"{node.field.value}:any" if node.present else f"-{node.field.value}"
    if isinstance(node, Negated):
        return _write_negated(node.part)
    parts = tuple(write(part) for part in node.parts)
    if node.op is Op.ANY:
        # An empty choice is the query nothing satisfies. There is no way to type that on purpose,
        # so it is written as a filter naming nothing, which is what the parser turns into it.
        return " OR ".join(parts) if parts else f"{Field.TAGS.value}:"
    if _same_field(node.parts):
        field_name = next(iter(node.parts))
        assert isinstance(field_name, Term)  # noqa: S101 (`_same_field` has just checked it)
        values = ",".join(quoted(part.value) for part in node.parts if isinstance(part, Term))
        return f"{field_name.field.value}:{values}"
    return " ".join(parts)


def _write_negated(part: Node) -> str:
    """The opposite of a filter, written."""
    if isinstance(part, Term):
        return f"-{part.field.value}:{quoted(part.value)}"
    if isinstance(part, Presence):
        return write(Presence(part.field, not part.present))
    if isinstance(part, Negated):
        return write(part.part)
    joiner = " " if part.op is Op.ANY else " OR "
    return joiner.join(_write_negated(inner) for inner in part.parts)


def _same_field(parts: tuple[Node, ...]) -> bool:
    """Whether every part is a plain value of one field, which is what a comma list spells."""
    fields = {part.field for part in parts if isinstance(part, Term)}
    return len(fields) == 1 and len(parts) == sum(1 for part in parts if isinstance(part, Term))


@dataclass(frozen=True, slots=True)
class Clause:
    """One of the things a query asks for, described rather than parsed."""

    query: str
    field: Field | None
    values: tuple[str, ...]
    negated: bool
    present: bool | None
    match: Op


def clauses(query: Query) -> tuple[Clause, ...]:
    """A query as the rows it is made of. Every one of them has to hold."""
    root = query.where
    if isinstance(root, Group) and root.op is Op.ALL:
        return tuple(_describe(part) for part in root.parts)
    return (_describe(root),)


def _describe(node: Node) -> Clause:
    if isinstance(node, Term):
        return Clause(write(node), node.field, (node.value,), False, None, Op.ALL)
    if isinstance(node, Presence):
        return Clause(write(node), node.field, (), not node.present, node.present, Op.ALL)
    if isinstance(node, Negated):
        inside = _describe(node.part)
        return Clause(
            write(node), inside.field, inside.values, not inside.negated, None, inside.match
        )

    # Through any negations, not just the plain parts: `-tags:a OR -tags:b` is about tags, and a
    # clause that could not name its own field is one a rule builder has to leave alone.
    fields = {leaf.field for part in node.parts for leaf in _leaves(part)}
    inner = tuple(_describe(part) for part in node.parts)
    negated = bool(inner) and all(part.negated for part in inner)
    # Every part excluded is the whole clause excluded, and the way the rest of them combine turns
    # over with it: "not a and not b" is "not either of a, b", which is the row somebody built.
    match = node.op
    if negated:
        match = Op.ANY if node.op is Op.ALL else Op.ALL
    return Clause(
        write(node),
        next(iter(fields)) if len(fields) == 1 else None,
        tuple(value for part in inner for value in part.values),
        negated,
        None,
        match,
    )


def _values(raw: str) -> list[str]:
    """One value string, split on commas, respecting quotes."""
    pieces: list[str] = []
    current: list[str] = []
    quoted = False

    for character in raw:
        if character == '"':
            quoted = not quoted
            continue
        if character == "," and not quoted:
            pieces.append("".join(current).strip())
            current = []
            continue
        current.append(character)

    pieces.append("".join(current).strip())
    return [piece for piece in pieces if piece]


def _given(raw: Mapping[str, str], name: str) -> list[str]:
    """Every value a parameter was given, not just the last one."""
    everything = getattr(raw, "getlist", None)
    if callable(everything):
        return [value for value in everything(name) if value is not None]
    value = raw.get(name)
    return [] if value is None else [value]


#: The named parameter a CONTROL sends to say how far below a named folder it means.
DEPTH = "depth"


def _folder_depth(raw: Mapping[str, str]) -> FolderDepth | None:
    """How far below a named folder this request means. None means it asked for something unreadable."""
    given = _given(raw, DEPTH)
    if not given:
        return FolderDepth.SUBTREE
    asked: list[FolderDepth] = []
    for value in given:
        try:
            asked.append(FolderDepth(value.strip().lower()))
        except ValueError:
            return None
    # The narrowest of them, rather than the last one. A parameter written twice is a link
    # somebody edited or a form that serialised the same control twice; answering with whichever
    # came last could hand back MORE than one of the two asked for, and widening is the one
    # direction this module may not fail in.
    return FolderDepth.DIRECT if FolderDepth.DIRECT in asked else FolderDepth.SUBTREE


def parse_modal(raw: Mapping[str, str]) -> Query:
    """The point-and-click front-end: named parameters using the token field names."""
    parts: list[Node] = []
    for name in Field:
        spellings = [name.value, *(old for old, field in ALIASES.items() if field is name)]
        for value in [given for spelling in spellings for given in _given(raw, spelling)]:
            # An empty parameter is a control the person cleared, not a filter that matches
            # nothing. This is the one place the two front-ends legitimately differ, and it is a
            # property of the medium: a browser sends `tags=` for an emptied box, whereas typing a
            # bare `tags:` into the search bar is a deliberate act.
            listed = _listed(name, value)
            if listed is not None:
                parts.append(listed)
    depth = _folder_depth(raw)
    if depth is None:
        # A depth nobody could read is the condition nothing satisfies, exactly as a query over
        # the term cap is. Falling back to the default would answer a request that asked for LESS
        # with the whole subtree: more than was asked for, silently, on the one screen that
        # cannot tell the difference by looking.
        return Query(text=None, where=IMPOSSIBLE)
    filings = _filings(raw)
    if filings is None:
        # The same rule for a filing: one that could not be read filters to nothing. Dropping it
        # would open the whole Site from a line that counted four files, which is the fault the
        # parameter exists to end.
        return Query(where=IMPOSSIBLE)
    # The modal carries no free text. The box owns that, and `parse` below joins the two.
    return Query(
        text=None, where=_bounded(Op.ALL, tuple(parts)), folder_depth=depth, filings=filings
    )


def _filings(raw: Mapping[str, str]) -> tuple[Filing, ...] | None:
    """Every History line this request names, in one order. None where any could not be read."""
    found: list[Filing] = []
    for parameter in FILING_PARAMETERS:
        for value in _given(raw, parameter):
            if len(found) >= MAX_TERMS:
                return None
            # An empty one is a control somebody cleared, which every modal parameter reads as
            # nothing asked, the rule `parse_modal` states above.
            if not value.strip():
                continue
            filing = Filing.read(parameter, value.strip())
            if filing is None:
                return None
            found.append(filing)
    return tuple(sorted(dict.fromkeys(found), key=lambda one: (one.parameter, one.value)))


def parse(raw: Mapping[str, str]) -> Query:
    """Both front-ends, combined, which is what a real request carries."""
    typed = parse_tokens(raw.get("q") or "")
    clicked = parse_modal(raw)
    # The depth comes from the clicked half and only from there: nothing anybody types can produce
    # one, so the box's answer is always the default and taking it from both would be a merge with
    # one side that can never disagree.
    return Query(
        text=typed.text,
        where=group(Op.ALL, (typed.where, clicked.where)),
        folder_depth=clicked.folder_depth,
        # Clicked only, like the depth: nothing typed can produce one.
        filings=clicked.filings,
    )


#: What `rating:` accepts instead of a number, and what every dimension that can be asked about its
#: presence accepts. `none` is the one people want (a library is mostly unrated and mostly
#: untagged, so "what have I not got to yet" is a real question), and `any` is its opposite.
_UNRATED = frozenset({"none", "unrated", "no"})


_RATED = frozenset({"any", "rated", "yes"})


_ABSENT = frozenset({"none"})


_PRESENT = frozenset({"any"})


def _presence(found: Field, value: str) -> bool | None:
    """Whether this value asks about the PRESENCE of something. None means it does not."""
    if found not in PRESENCE_FIELDS:
        return None
    text = value.strip().lower()
    absent, present = (_UNRATED, _RATED) if found is Field.RATING else (_ABSENT, _PRESENT)
    if text in absent:
        return False
    if text in present:
        return True
    return None
