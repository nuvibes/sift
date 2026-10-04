# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a list of PAIRS into the groups a person actually decides about.

## Why a group and not a pair

Three copies of one clip are three pairs, so a pair screen asks three questions about one thing,
and the three answers could contradict each other. Every serious tool reviews a GROUP: N files, one
keeper, the rest go, the way the reclaim view next door works (one asset, several locations).

## Why nothing here is stored

A group is not a fact about the library, it is a fact about the library AT A SETTING. Both dials
(how close is close enough, and how far apart two may run) are applied when the queue is read, so
widening one is a different question asked of rows that already exist. Store the groups and every
dial change has to invalidate them; compute them on the way out and there is nothing to invalidate.

Some tens of thousands of pending pairs fold in a few hundred milliseconds with the files' figures
in hand: longer than the watchdog's quarter-second threshold, on a path somebody is waiting on,
and growing with the pair count. So the service hands this fold to a thread
(`DedupService._grouped`): half a second of one worker rather than of every screen. The memo makes
the SECOND reader cheap and does nothing for the first, who is exactly who opens the Duplicates
screen at a dial nobody has asked about yet.

This module is pure (it is handed everything it reads), and a pure function is a function a
thread cannot race.

## The two rules that stop this being wrong

**Never chain across methods.** On a large library `phash` alone can reach a component of hundreds
of files at the low dial where `video_phash` alone stays near a hundred. A still and a clip are
compared on different scales (bits out of 63 against frames out of 30), so a pair from each
meeting at a shared file would join two populations that were never compared to one another.

**A component past the cap is not a group.** This is the classic fault of clustering by connected
components: A is near B, B is near C, and A is nothing like C. At the widest dial one component can
hold hundreds of files; even at *exact* some exceed the cap. Past the cap the component is handed
back marked `too_big`, with its size, and NOTHING in it is ever pre-marked: it is work for a
person, and the one thing that must not happen is a rule proposing to delete hundreds of files it
cannot vouch for.

This module is pure on purpose. No database, no settings, no service: everything it needs is
passed in, so every rule above can be tested exhaustively and mutation-proved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple

# What a rule is allowed to look at, and it is the content layer's OWN record rather than a copy of
# it. Two records for one set of five numbers is two places for a column to be added to, and the
# mapping between them is a line that can only ever be wrong. What keeps it honest is what `Measure`
# deliberately leaves out (five columns and not the asset), so a rule cannot be written against a
# title or a rating, and the answer is the same for everybody.
#
# There is deliberately nothing on it about who may SEE the file. Whether a file is hidden is a fact
# about a session rather than about the file, and these groups are computed once for the whole
# library and kept under the change mark, so a viewer's vault cannot be an input to them without
# making the answer un-keepable. The screen's boundary is where that belongs: a group holding
# something this session may not be shown is handed over with no keeper marked, and a request to act
# on one is refused there.
from sift.kernel.content.duplicates import Measure

#: How many files a component may hold and still be offered as one question.
#:
#: Eight. Most of what sits below it is a plain pair, and the components above it are the long
#: chains. So the cap keeps the long chains out of the automatic
#: path without taking the ordinary case with them.
#:
#: A number rather than a setting, deliberately. It is not a preference: it is the point at which
#: "these are the same thing" stops being a claim anybody can check by looking, and offering it as a
#: dial would invite somebody to raise it until the screen was fast again.
DEFAULT_MAX_GROUP = 8

#: Which file a rule keeps. The five the screen has always offered.
Rule = Literal["larger", "smaller", "higher_res", "newer", "older"]

#: The order every rule falls back through when it cannot separate two files.
#:
#: **A rule is an ORDER, not one key: higher resolution, then larger file, then newer.** A single key
#: decides far less often than it looks (two re-encodes of one clip at the same resolution tie
#: immediately) and every tie is a group that has to go to a person. So each rule leads its own
#: chain and the rest follow, which is what makes the automatic path reach most of the queue instead
#: of a third of it.
#:
#: Resolution leads by default because it is the measure that best survives a re-encode: a
#: re-compressed copy is smaller and newer and no worse to look at, so "larger" and "newer" both
#: prefer the wrong file about as often as the right one.
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
    """One pending pair, as this module needs to read it.

    A record rather than a bare tuple because four of its five fields are strings and three of them
    are ids: a swapped argument would cluster by the wrong column and produce groups that look
    entirely plausible. Its own type rather than the queue's `Candidate`, because the queue's record
    is defined in the module that imports this one.
    """

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
    #: Which method's pairs made this group. Never mixed. See the module docstring.
    method: str
    #: The closest any pair in it measured, so a screen can say how alike these are.
    distance: int
    #: The pending pairs this group was made of, by their queue ids.
    #:
    #: Carried because settling a group has to reach the rows. Confirming does not need them
    #: (deleting a file takes its pairs with it through the foreign key), but saying "these are not
    #: duplicates" deletes nothing and has to write `dismissed` on every one of them, or the same
    #: group is offered again on the next read.
    pairs: tuple[str, ...] = ()
    #: The file the rule would keep, or None when it could not decide. **None is what "needs you"
    #: means**, and it is the number the card counts.
    keeper: str | None = None
    #: True when this component was too big to be one question. Its files are handed back so the
    #: screen can say how many, and `keeper` is always None.
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
    """Which file to keep, or None when nothing here can say.

    None is a real answer and the commonest reason this is safe: a group nothing separates is a
    group a person decides, and the alternative (picking one anyway) is deleting somebody's file
    on a coin toss.

    Two ways it declines, and each is a case that happens:

    - **A missing figure.** An unprobed file has no dimensions; one filed before a column existed
      has no size. A rule cannot compare a number with nothing.
    - **A tie the whole chain cannot break.** Two byte-identical re-encodes at one resolution added
      in the same second are genuinely indistinguishable to every rule there is.
    """
    if len(files) < 2:
        return None

    # Each step narrows the ones still in the running; it does NOT start again over all of them.
    #
    # That distinction is the whole correctness of a chain: two files tie at 1920x1080, a third is
    # 1280x720 and the largest on disk, and restarting the next step over everybody would keep the
    # 720, so "higher resolution" would delete the higher resolution. A tie-break exists to
    # separate the leaders, never to overrule them.
    running = list(files)
    for step in _CHAIN[rule]:
        scores = [(_metric(one, step), one) for one in running]
        ranked = [(value, one) for value, one in scores if value is not None]
        # A figure nothing carries cannot rank anybody, so this step is skipped and the field is
        # left as it was, rather than dropping the files that lack it, which would let a missing
        # column decide which of the others wins.
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


def group_pairs(
    pairs: Iterable[PairRow],
    *,
    facts: Mapping[str, Measure],
    rule: Rule,
    cap: int = DEFAULT_MAX_GROUP,
) -> list[Group]:
    """Every group these pairs make, most alike first.

    `pairs` is the queue's own pending rows, already narrowed to what the dials show. They arrive
    closest-first and the groups come back in the same order, so the easiest decisions are on the
    first page.

    A file with no measure is still IN its group. Leaving it out would make the group smaller than
    it is and could split one question into two; what it does instead is stop the rule deciding,
    which is `keeper_of`'s own rule about a missing figure and is the honest answer.
    """
    # Union-find, keyed by (method, asset): the method is part of the key, which is the whole of
    # "never chain across methods": two pairs from different methods can share an asset and still
    # never meet, because they are not the same node.
    parent: dict[tuple[str, str], tuple[str, str]] = {}
    #: The pairs read under each node, and the closest distance seen there. Both are folded onto
    #: whichever node ended up being the root, because a later union moves the root of an earlier
    #: one, so neither can be kept against the root as it stood when a pair was read.
    filed: dict[tuple[str, str], list[PairRow]] = {}

    def find(node: tuple[str, str]) -> tuple[str, str]:
        parent.setdefault(node, node)
        # Iterative, not recursive: a chain of hundreds of files would be hundreds of frames, and
        # this runs inside a request.
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

    groups: list[Group] = []
    for root, ids in members.items():
        method = root[0]
        # Sorted, so a group's order does not depend on the order its pairs happened to arrive,
        # which is what makes the smallest id a usable paging key.
        ordered = tuple(sorted(set(ids)))
        rows = held.get(root, [])
        # The closest pair anywhere in the group, which is what "how alike are these" means for a
        # group of more than two. Taken over every pair that ended up here rather than off the root,
        # because the root moves: without this a group's distance would be whichever pair happened
        # to be read last, and the ordering it drives would be arbitrary.
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

    # Closest first; a chain LAST among groups equally alike; then by the smallest id.
    #
    # The middle term is the only one that is a judgement rather than arithmetic: when the
    # closest thing is a long chain (eleven files, say), the
    # first card on the screen is the one card nothing can be done with. Distance does not
    # separate them (a chain is made of pairs, and its closest pair is usually zero), so without
    # this the dead end sits at the top of every page it lands on.
    #
    # It is not a demotion to the end of the queue: a chain still sits with the groups it is as
    # close as, which is where somebody looking for it would expect it. It simply goes after the
    # ones that can be answered.
    #
    # The third term is what makes the order TOTAL, so paging by it cannot skip or repeat a group
    # when two are equally alike.
    groups.sort(key=lambda one: (one.distance, one.too_big, one.ids[0]))
    return groups


__all__ = ["DEFAULT_MAX_GROUP", "Group", "PairRow", "Rule", "group_pairs", "keeper_of"]
