# SPDX-License-Identifier: AGPL-3.0-or-later
"""A facet's three declarations agree: the registry's `faceted=True` in `records.py`, the access
layer's column to group by (`ENTITY_FACETS`, or the files wall's `FACETS`), and the parameters each
wall's route declares, since FastAPI silently drops undeclared ones and answers over the whole
population.

Every faceted field has somewhere to be counted from; every `ENTITY_FACETS` entry names a faceted
field or is a presence facet (`linked`, `cover`, `mine` and the like, which no record can hold);
and every wall's route reads exactly the parameters its facets are keyed by.
"""

from __future__ import annotations

import inspect

import pytest

from sift.kernel.access.constraints import ENTITY_FACETS
from sift.kernel.access.repository.assets import FACETS
from sift.kernel.records import Subject, every_field, field
from sift.slices.collections.router import CollectionsNarrowing
from sift.slices.people.router import PeopleNarrowing, SitesNarrowing
from sift.slices.photo_sets.router import PhotoSetsNarrowing
from sift.slices.songs.router import SongsNarrowing
from sift.slices.tags_ratings.router import TagsNarrowing

pytestmark = [pytest.mark.gate, pytest.mark.unit]

#: A file facet carries the query language's word, since clicking a row writes that filter; this
#: is the one that differs from its column.
FILE_TOKENS = {"release_date": "released"}

#: The facets that are not fields: questions about a ROW's relationships (a stash-box attached, a
#: cover, whose it is, what is shared, which box made or enriched it) that no form could fill.
#: `disagrees` is not in the database at all: the reconcile slice works its ids out (`_disagrees`).
PRESENCE = frozenset(
    {"linked", "enriched", "cover", "usernames", "mine", "sharing", "created", "disagrees"}
)

#: Tags on a wall whose record declares no tag field, counted from their own tables
#: (`collection_tags`, `photo_set_tags`).
OWN_TAGS = frozenset({("collection", "tags"), ("photo_set", "tags")})

#: The parameter declarations each wall's two routes read, by the noun the wall is of.
NARROWINGS = {
    "person": PeopleNarrowing,
    "site": SitesNarrowing,
    "tag": TagsNarrowing,
    "collection": CollectionsNarrowing,
    "photo_set": PhotoSetsNarrowing,
    "song": SongsNarrowing,
}


def test_every_faceted_field_has_somewhere_to_be_counted_from() -> None:
    """A flag with no column behind it is a dimension the panel asks for and the server refuses."""
    missing = []
    for one in every_field():
        if not one.faceted:
            continue
        if one.subject is Subject.ASSET:
            token = FILE_TOKENS.get(one.key, one.key)
            if token not in FACETS:
                missing.append(f"asset.{one.key} (as {token!r}) is in no FACETS entry")
            continue
        wall = ENTITY_FACETS.get(one.subject.value, {})
        if one.key not in wall:
            missing.append(f"{one.subject.value}.{one.key} is in no ENTITY_FACETS entry")

    assert not missing, (
        "\nThese fields are declared faceted and nothing says how to count them. The panel offers\n"
        "the dimension and the route refuses it.\n\n  " + "\n  ".join(missing) + "\n"
    )


def test_every_entity_facet_names_a_faceted_field_or_is_a_presence() -> None:
    """An entity facet with no faceted field behind it is the drift a rename causes: a facet
    grouping by a key that names nothing."""
    stray = []
    for noun, facets in ENTITY_FACETS.items():
        subject = Subject(noun) if noun in {one.value for one in Subject} else None
        for key in facets:
            if key in PRESENCE or (noun, key) in OWN_TAGS:
                continue
            if subject is None:
                stray.append(f"{noun}.{key} is not a presence facet and {noun} has no record")
                continue
            declared = field(subject, key)
            if declared is None:
                stray.append(f"{noun}.{key} names no declared field")
            elif not declared.faceted:
                stray.append(f"{noun}.{key} names a field that is not declared faceted")

    assert not stray, (
        "\nThese are counted along a dimension the registry does not declare as one. Either the\n"
        "field needs `faceted=True` or the entry is left over from a rename.\n\n  "
        + "\n  ".join(stray)
        + "\n"
    )


def test_every_wall_reads_exactly_the_parameters_its_facets_are_keyed_by() -> None:
    """Each wall reads exactly its facets' parameters, or a chip changes the address and nothing
    else."""
    apart = []
    for noun, narrowing in NARROWINGS.items():
        declared = set(inspect.signature(narrowing.__init__).parameters) - {"self"}
        carried = set(narrowing().picks)
        keyed = set(ENTITY_FACETS[noun])
        if declared != keyed:
            apart.append(f"{noun}: route reads {sorted(declared)}, facets are {sorted(keyed)}")
        if carried != declared:
            apart.append(f"{noun}: route declares {sorted(declared)} and passes {sorted(carried)}")

    assert not apart, (
        "\nThese walls' routes and their facets name different things.\n\n  "
        + "\n  ".join(apart)
        + "\n"
    )
