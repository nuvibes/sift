# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the related-counts endpoint answers with."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class RelatedCounts(Wire):
    """How many things of each kind one entity's files reach.

    Absent means "this page has no such tab" and zero is a real answer, so an empty wall and an
    absent one never look the same.
    """

    files: int | None = None
    #: The bytes of the files `files` counts, off the same read, so the size shown is of what this
    #: viewer may see. Not a tab; None exactly when `files` is.
    files_bytes: int | None = None
    photo_sets: int | None = None
    loops: int | None = None
    #: The sites PART OF this one, on a Site's page only (see `TABS_FOR`); `sites` already answers
    #: which Sites the files came from.
    sites_within: int | None = None
    #: The tags filed directly under this one. A tag's page only; the twin of `sites_within`.
    tags_within: int | None = None
    tags: int | None = None
    people: int | None = None
    sites: int | None = None
    collections: int | None = None
    #: The songs these files carry: the Music tab. See `TABS_FOR` for the pages that have it.
    songs: int | None = None
    #: Fields a linked stash-box disagrees with about this record: not a tab but the mark on the
    #: History tab, whose top panel settles them. None (no mark) for a non-admin, a kind no box can
    #: know, or a record this user may not see.
    disagreements: int | None = None
    #: The boxes those fields are about, by name, so the mark says which box it means.
    disagreement_boxes: list[str] = Field(default=[])
