# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a list of pairs into the groups a person decides about.

Pure, and computed on the way out rather than stored, since a group depends on the dials. Pairs of
different methods never chain, and a component past the cap is handed back `too_big` with nothing in
it pre-marked.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple

# What a rule may look at: the content layer's own record, which carries nothing about who may see
# the file, so the groups stay one answer for the whole library.
from sift.kernel.content.duplicates import Measure

#: How many files a component may hold and still be one question; past it, "the same thing" is not
#: something anybody can check by looking, so it is not a setting.
DEFAULT_MAX_GROUP = 8

#: Which file a rule keeps. The five the screen has always offered.
Rule = Literal["larger", "smaller", "higher_res", "newer", "older"]

#: The order every rule falls back through: each rule leads its own chain and the rest follow, as a
#: single key ties too often. Resolution leads, as it best survives a re-encode.
_CHAIN: Mapping[Rule, tuple[Rule, ...]] = {
    "higher_res": ("higher_res", "larger", "newer"),
    "larger": ("larger", "higher_res", "newer"),
    "smaller": ("smaller", "higher_res", "newer"),
    "newer": ("newer", "higher_res", "larger"),
    "older": ("older", "higher_res", "larger"),
}

#: Which way each rule points. True keeps the bigger number.
_KEEPS_THE_LARGER: Mapping[Rule, bool] = {
    "larger": True,
    "smaller": False,
    "higher_res": True,
    "newer": True,
    "older": False,
}


class PairRow(NamedTuple):
    """One pending pair, as this module needs to read it; a record, so a swapped id cannot pass."""

    id: str
    asset_a: str
    asset_b: str
    method: str
    distance: int


@dataclass(frozen=True, slots=True)
class Group:
    """One question: these files, and which of them a rule would keep."""

    #: The files, in a stable order. The smallest id first, which is also how groups are paged.
    ids: tuple[str, ...]
    #: Which method's pairs made this group; never mixed.
    method: str
    #: The closest any pair in it measured, so a screen can say how alike these are.
    distance: int
    #: The pending pairs this group was made of, by queue id: dismissing has to mark every one.
    pairs: tuple[str, ...] = ()
    #: The file the rule would keep, or None when it could not decide, which the card counts.
    keeper: str | None = None
    #: True when this component was too big to be one question; `keeper` is then None.
    too_big: bool = False

    @property
    def needs_a_person(self) -> bool:
        """Whether a rule settled this or somebody has to."""
        return self.keeper is None


def _metric(facts: Measure, rule: Rule) -> int | None:
    """The one number this rule compares, or None when the file does not carry it."""
    if rule in ("larger", "smaller"):
        return facts.size_bytes
    if rule == "higher_res":
        return None if facts.width is None or facts.height is None else facts.width * facts.height
    return facts.added_at


def keeper_of(files: Sequence[Measure], rule: Rule) -> str | None:
    """Which file to keep, or None when a figure is missing or the whole chain ties."""
    if len(files) < 2:
        return None

    # Each step narrows the ones still in the running rather than starting over, so a tie-break
    # never overrules the leaders.
    running = list(files)
    for step in _CHAIN[rule]:
        scores = [(_metric(one, step), one) for one in running]
        ranked = [(value, one) for value, one in scores if value is not None]
        # A figure nothing carries skips this step rather than dropping the files that lack it.
        if len(ranked) != len(scores):
            continue
        best = (
            max(value for value, _ in ranked)
            if _KEEPS_THE_LARGER[step]
            else min(value for value, _ in ranked)
        )
        running = [one for value, one in ranked if value == best]
        if len(running) == 1:
            return running[0].asset_id
    return None


def _components(
    pairs: Iterable[PairRow],
) -> tuple[dict[tuple[str, str], list[str]], dict[tuple[str, str], list[PairRow]]]:
    """The files and the pairs of each connected component, keyed by its root."""
    # Union-find keyed by (method, asset), so pairs of different methods never meet.
    parent: dict[tuple[str, str], tuple[str, str]] = {}
    #: The pairs and closest distance under each node, folded onto whichever node ends as the root.
    filed: dict[tuple[str, str], list[PairRow]] = {}

    def find(node: tuple[str, str]) -> tuple[str, str]:
        parent.setdefault(node, node)
        # Iterative, not recursive: a chain can be hundreds of files.
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(one: tuple[str, str], other: tuple[str, str]) -> None:
        left, right = find(one), find(other)
        if left != right:
            parent[right] = left

    for pair in pairs:
        first, second = (pair.method, pair.asset_a), (pair.method, pair.asset_b)
        union(first, second)
        filed.setdefault(first, []).append(pair)

    members: dict[tuple[str, str], list[str]] = {}
    for node in list(parent):
        members.setdefault(find(node), []).append(node[1])

    held: dict[tuple[str, str], list[PairRow]] = {}
    for node, rows in filed.items():
        held.setdefault(find(node), []).extend(rows)
    return members, held


def group_pairs(
    pairs: Iterable[PairRow],
    *,
    facts: Mapping[str, Measure],
    rule: Rule,
    cap: int = DEFAULT_MAX_GROUP,
) -> list[Group]:
    """Every group these pairs make, most alike first; a file with no measure stays in its group."""
    members, held = _components(pairs)

    groups: list[Group] = []
    for root, ids in members.items():
        method = root[0]
        # Sorted, so the smallest id is a stable paging key.
        ordered = tuple(sorted(set(ids)))
        rows = held.get(root, [])
        # The closest pair anywhere in the group, taken over every pair since the root moves.
        distance = min((row.distance for row in rows), default=0)
        settling = tuple(sorted({row.id for row in rows}))
        if len(ordered) > cap:
            groups.append(
                Group(
                    ids=ordered,
                    method=method,
                    distance=distance,
                    pairs=settling,
                    keeper=None,
                    too_big=True,
                )
            )
            continue
        known = [facts[one] for one in ordered if one in facts]
        keeper = keeper_of(known, rule) if len(known) == len(ordered) else None
        groups.append(
            Group(ids=ordered, method=method, distance=distance, pairs=settling, keeper=keeper)
        )

    # Closest first; a chain last among groups equally alike, since nothing can be done with it;
    # then the smallest id, so the order is total for paging.
    groups.sort(key=lambda one: (one.distance, one.too_big, one.ids[0]))
    return groups


__all__ = ["DEFAULT_MAX_GROUP", "Group", "PairRow", "Rule", "group_pairs", "keeper_of"]
