# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding who a face belongs to, by arithmetic over numbers already stored.

**There is no database of vectors here and there should not be.** A few hundred people at a handful
of descriptions each is a few megabytes; comparing one face against all of them is a single matrix
multiply and takes microseconds. A vector database earns its place when the question is "find the
nearest of ten million", which is a different feature. Adding one here would be a dependency, a
schema, a rebuild step and a failure mode, in exchange for making a fast thing fast.

The consequence is the single biggest improvement over how this is usually done: because every
face that was ever found is stored with its numbers, **adding a person does not re-read a single
file**. It is a comparison against data already held: seconds, no decoding, no queue.

Three thresholds, and the gap between the first two is where a person's judgement goes:

- above **attach**, Sift attributes the face itself
- between **suggest** and **attach**, it offers the match and waits
- below **suggest**, it says nothing

**The attach line is not one number for everybody**, and nothing here knows that: it arrives as a
parameter because it depends on the person the comparison landed on rather than on the comparison.
`tuning.bar_for` is the one place that decides it, and the callers hand in what it answered.

A face that somebody has explicitly said is *not* a particular person is never matched to them
again. That rejection is stored and consulted here, because a suggestion that comes back a week
after being turned down is how a review queue gets abandoned.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from sift.slices.faces import tuning
from sift.slices.faces.clustering import agglomerate
from sift.slices.faces.models import Attribution, Match, Origin, Reference, Vector


@dataclass(frozen=True, slots=True)
class Gallery:
    """Everybody Sift can recognize, arranged for comparison.

    Built once and used for many faces: the cost of arranging it is paid per re-match, not per
    face, which is what makes re-matching a whole library after adding one person quick.
    """

    people: tuple[str, ...]
    """Who each row of `vectors` belongs to. Several rows may name the same person."""
    vectors: np.ndarray
    starters_only: frozenset[str] = frozenset()
    """Who is described by STARTER pictures alone (`Origin.SEED`): the people a match may only be
    asked about. Worked out from the same rows the vectors are, so the gallery and the rule about
    it cannot come from two reads that disagree. See `FaceService._attach_bar`."""

    def __len__(self) -> int:
        return len(self.people)


def describe(references: Sequence[Reference], *, groups: int = tuning.MATCH_GROUPS) -> list[Vector]:
    """How to describe one person for matching, from their reference faces.

    With `groups` at one, all of a person's references are blended into a single description. That
    is measurably the better answer at ordinary gallery sizes: keeping several descriptions (one
    per era, hair colour or camera) is the obvious idea and it makes matching very slightly *worse*, including for the people whose references genuinely fall into
    distinct groups. With ten or twenty references the blend is a better estimate of a person than
    any subset of them is.

    Raising it clusters their references and keeps one description per cluster, for a gallery large
    enough that a cluster is well described by its own members.
    """
    if not references:
        return []
    vectors = [reference.vector for reference in references]
    if groups <= 1:
        return [_blend(vectors)]
    clustered = agglomerate(
        vectors, join_above=tuning.PILE_JOIN, most=min(groups, tuning.MAX_MATCH_GROUPS)
    )
    return [_blend([vectors[index] for index in members]) for members in clustered]


def _blend(vectors: Sequence[Vector]) -> Vector:
    """The middle of several descriptions, scaled back to unit length so it compares on the same
    scale as a single face does."""
    stacked = np.asarray(vectors, dtype=np.float32)
    middle = stacked.mean(axis=0)
    length = float(np.linalg.norm(middle))
    if length > 0:
        middle = middle / length
    return tuple(middle.tolist())


def build_gallery(
    by_person: Mapping[str, Sequence[Reference]], *, groups: int = tuning.MATCH_GROUPS
) -> Gallery:
    """Arrange everybody's references into one block of numbers."""
    people: list[str] = []
    rows: list[Vector] = []
    starters: set[str] = set()
    for person_id, references in sorted(by_person.items()):
        if references and all(one.origin is Origin.SEED for one in references):
            starters.add(person_id)
        for description in describe(references, groups=groups):
            people.append(person_id)
            rows.append(description)
    if not rows:
        return Gallery(people=(), vectors=np.zeros((0, 0), dtype=np.float32))
    return Gallery(
        people=tuple(people),
        vectors=np.asarray(rows, dtype=np.float32),
        starters_only=frozenset(starters),
    )


def best_match(
    vector: Vector,
    gallery: Gallery,
    *,
    rejected: Iterable[str] = (),
    floor: float = tuning.SUGGEST_CONFIDENCE,
) -> Match | None:
    """The likeliest person for one face, or nothing if none is likely enough.

    People this face has been explicitly rejected for are removed before the best is chosen, not
    after. Removing them afterwards would let a rejected person hide the real answer: the second
    best is the one that should be offered, and it never would be.
    """
    if len(gallery) == 0 or not vector:
        return None
    scores = gallery.vectors @ np.asarray(vector, dtype=np.float32)

    excluded = set(rejected)
    if excluded:
        keep = np.array([person not in excluded for person in gallery.people], dtype=bool)
        if not keep.any():
            return None
        scores = np.where(keep, scores, -2.0)

    best = int(scores.argmax())
    confidence = float(scores[best])
    if confidence < floor:
        return None
    return Match(person_id=gallery.people[best], confidence=confidence)


def rows_by_person(gallery: Gallery) -> dict[str, np.ndarray]:
    """Which rows of the gallery describe each person, worked out once for a whole pass.

    For a pass that asks many faces about one NAMED person each (a re-match re-reading the
    questions standing for people) rather than about whoever is closest. Looked up per face it
    would be a walk of the whole gallery per face, on the loop.
    """
    rows: dict[str, list[int]] = {}
    for index, person_id in enumerate(gallery.people):
        rows.setdefault(person_id, []).append(index)
    return {person_id: np.asarray(found, dtype=np.intp) for person_id, found in rows.items()}


def likeness(vector: Vector, gallery: Gallery, rows: np.ndarray | None) -> float | None:
    """How closely one face resembles one person: the best of that person's rows. None when the
    person has no rows (nothing to compare with) or the face has no description.

    The same product `best_match` takes, narrowed to one person's rows, so the number is on the same
    scale as every confidence stored beside it. Not floored: a question about somebody is re-scored
    whatever the number is, and what to do about a low one is the caller's rule, not this one's.
    """
    if rows is None or len(rows) == 0 or not vector:
        return None
    scores = gallery.vectors[rows] @ np.asarray(vector, dtype=np.float32)
    return float(scores.max())


def verdict(
    match: Match | None,
    *,
    attach_above: float = tuning.AUTO_APPLY_CONFIDENCE,
) -> Attribution | None:
    """What to do about a match: attach it, offer it, or nothing.

    Attaching without asking is held to a deliberately higher bar than being right on average. An
    attribution nobody was asked about is the one nobody checks, so the only ones made silently
    are the ones there is least room to doubt.

    **The bar is handed in, and the default is the one for a person Sift has little to go on
    about.** Who the match is FOR decides it (a well-described person earns a lower one), and
    that is a fact about the gallery rather than about this comparison, so it is settled by
    `tuning.bar_for` where the reference counts are to hand and passed down. Reading it from a
    constant here would be a second answer to the same question, in the one place that cannot see
    the evidence for it.
    """
    if match is None:
        return None
    # A bar at the top of the scale is NEVER, and the comparison alone does not keep that promise:
    # a product of two unit vectors in single precision can come out a hair over one: the same
    # picture found in the library and held as somebody's only reference is exactly that case, and
    # it is the case a starter picture from a stash-box makes likely. Said here, where the decision
    # is, rather than trusted to the arithmetic.
    if attach_above >= tuning.ALWAYS_ASK:
        return Attribution.SUGGESTED
    return Attribution.MATCHED if match.confidence >= attach_above else Attribution.SUGGESTED
