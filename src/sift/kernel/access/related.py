# SPDX-License-Identifier: AGPL-3.0-or-later
"""One entity, as the filtering every other entity wall understands.

Cross-association is not a query per pair. "The tags on this person", "the people on this site",
"the photo sets a tag reaches" are all the same thing: an entity wall whose visible set has been
narrowed to the files another entity reaches. This is where a related-list request becomes that
narrowing, and it is one function so that six routes cannot come to disagree about what `person=`
means.

Every value here is an ID, never a name. A name is ambiguous (two people really can be called Jane),
and a related list is always called from a page that already holds the id, so resolving a name
would be guessing at a question nobody asked. An id naming nothing produces a group that matches
nothing, which filters the wall to empty rather than widening it to the library; that is the whole
reason an unresolvable value is kept rather than dropped.
"""

from __future__ import annotations

from sift.kernel.access.constraints import NO_FILTER, AllOf, AssetFilter, Where

#: Which filter leaf each related-list parameter builds. One mapping rather than a branch per
#: parameter, so a parameter added to a route and forgotten here fails loudly instead of silently
#: filtering nothing, which would look like a populated screen and read as correct.
_LEAF: dict[str, str] = {
    "person": "people",
    "tag": "tags",
    "site": "sites",
    "collection": "collections",
    "photo_set": "photo_sets",
    "song": "songs",
    # One FILE: which collections or photo sets hold it, for the chips on its own screen. The
    # `assets` predicate already existed for the walls; this is the same leaf reached by name.
    "asset": "assets",
}


def related_filter(**named: str | None) -> AssetFilter:
    """The filtering for a related list, or no filter at all.

    Composes: two parameters given means both apply, because `AllOf` is what the tree means and the
    seam takes the whole tree. So "the tags on this person's files from this site" costs nothing
    extra and needs no route of its own.

    Returns `NO_FILTER` itself (the same object, not an equal one) when nothing was named. The
    walls check identity to decide whether an admin still sees rows with nothing under them, so an
    equal-but-different empty filter would quietly turn a plain wall into a filtered one.
    """
    parts = tuple(
        Where(_LEAF[name], (value,)) for name, value in named.items() if value and name in _LEAF
    )
    unknown = set(named) - set(_LEAF)
    if unknown:
        raise ValueError(f"not a related-list parameter: {sorted(unknown)}")
    return AssetFilter(where=AllOf(parts)) if parts else NO_FILTER
