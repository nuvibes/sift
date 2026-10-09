# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the search endpoints send back; results come from the grid's own address and page shape."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.cover_frame import CoverFrame
from sift.kernel.wire import Wire


class SuggestionOut(Wire):
    """One row of the dropdown."""

    value: str
    detail: str | None = None
    count: int | None = None
    field: str | None = None
    opens: str | None = None
    #: Which thing this is: a name is not an identity, and looking it up again could disagree.
    id: str | None = None
    art: str | None = None
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    icon: str | None = None


class FilterOut(Wire):
    """One filter the query language understands, described for somebody meeting it."""

    field: str
    label: str
    hint: str
    example: str
    set_from: str | None = None


class Suggestions(Wire):
    """What the dropdown should show: the filters, matches for what is being typed, and recents."""

    token: str | None = None
    filters: list[FilterOut] = Field(default=[])
    matches: list[SuggestionOut] = Field(default=[])
    recent: list[RecentOut] = Field(default=[])
    replace_from: int | None = None
    matched_from: int | None = None
    #: The query these were computed for, so a stale row cannot delete what was typed since.
    for_query: str = ""


class Named(Wire):
    """What a plain word names in this library: people, sites, tags, collections and folders."""

    items: list[SuggestionOut]


class ParsedClause(Wire):
    """One clause of a query, its exact text spelled by the server so no client composes it."""

    query: str
    field: str | None = None
    values: list[str] = Field(default=[])
    negated: bool = False
    present: bool | None = None
    match: str = "all"


class ParsedProblem(Wire):
    """A filter whose value nothing could act on, and why, so the chip can say so."""

    field: str
    value: str
    reason: str


class ParsedQuery(Wire):
    """What a typed query means, told by the one parser so the client needs no second one."""

    text: str = ""
    clauses: list[ParsedClause] = Field(default=[])
    problems: list[ParsedProblem] = Field(default=[])
    terms: dict[str, list[str]] = Field(default={})


class RecentOut(Wire):
    """One thing this user's box remembers, and what picking it again does."""

    kind: str
    subject: str
    label: str


class KeptNameOut(Wire):
    """One value in a saved search that its chip cannot read from the query alone."""

    field: str
    value: str
    name: str | None = None


class SavedSearchOut(Wire):
    """One saved search, its query read back under today's names."""

    id: str
    name: str
    query: str
    kind: str = "asset"
    named: list[KeptNameOut] = Field(default_factory=list)


class NameOut(Wire):
    """One thing kept by id elsewhere, and the name it goes by now."""

    id: str
    name: str


class NamesNow(Wire):
    """What each of a list of ids is called now, for the ones this viewer may be shown."""

    items: list[NameOut]


class SavedSearches(Wire):
    """This user's saved searches, newest first. Nobody else's, ever."""

    items: list[SavedSearchOut]


class RenameSearchRequest(Wire):
    """A new name for a saved search; the query is not in it, so a rename can never wipe it."""

    name: str = Field(min_length=1, max_length=120)


class SaveSearchRequest(Wire):
    """Keep a query under a name, trimmed server-side."""

    name: str = Field(min_length=1, max_length=120)
    query: str = Field(max_length=1000)
    kind: str = Field(default="asset", max_length=40)


class RememberSearchRequest(Wire):
    """One entry for the top of this user's own memory; posted, so no other site can plant one."""

    kind: str = Field(default="query", max_length=40)
    subject: str = Field(max_length=1000)
    label: str = Field(max_length=1000)
    results: int | None = Field(default=None, ge=0)


class SearchOpenedRequest(Wire):
    """A file opened from the wall a typed search narrowed: the words, and the file."""

    query: str = Field(min_length=1, max_length=1000)
    asset_id: str = Field(min_length=1, max_length=64)
