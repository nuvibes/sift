# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding who a face belongs to, by arithmetic over numbers already stored.

No vector database: a few hundred people is one matrix multiply, and adding a person re-reads no
file. Above the attach line (`tuning.bar_for`, per person) a face is named, between it and the
suggest line it is offered, below it nothing; a refused person is never matched again.
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
    """Everybody Sift can recognize, arranged once per re-match for comparing many faces."""

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
    """How to describe one person for matching: one blend of their references, or with `groups`
    one per cluster; the blend measures better at ordinary gallery sizes."""
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
    """The middle of several descriptions, at unit length so it compares as a single face does."""
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
    """The likeliest person for one face, or None; refused people are removed before choosing."""
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
    """Which rows of the gallery describe each person, worked out once for a whole pass."""
    rows: dict[str, list[int]] = {}
    for index, person_id in enumerate(gallery.people):
        rows.setdefault(person_id, []).append(index)
    return {person_id: np.asarray(found, dtype=np.intp) for person_id, found in rows.items()}


def likeness(vector: Vector, gallery: Gallery, rows: np.ndarray | None) -> float | None:
    """How closely one face resembles one person, on the stored confidences' scale, unfloored;
    None without rows or a description."""
    if rows is None or len(rows) == 0 or not vector:
        return None
    scores = gallery.vectors[rows] @ np.asarray(vector, dtype=np.float32)
    return float(scores.max())


def verdict(
    match: Match | None,
    *,
    attach_above: float = tuning.AUTO_APPLY_CONFIDENCE,
) -> Attribution | None:
    """What to do about a match: attach it, offer it, or nothing, against the bar handed in."""
    if match is None:
        return None
    # The top of the scale is never: single precision can put a match a hair over one.
    if attach_above >= tuning.ALWAYS_ASK:
        return Attribution.SUGGESTED
    return Attribution.MATCHED if match.confidence >= attach_above else Attribution.SUGGESTED
