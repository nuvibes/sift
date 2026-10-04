# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the search endpoints send back.

No result shape: search is another way to arrive at the library, so results come from the grid's
own address in the grid's page shape, one description of an asset.
"""

from __future__ import annotations

from pydantic import Field

from sift.kernel.cover_frame import CoverFrame
from sift.kernel.wire import Wire


class SuggestionOut(Wire):
    """One row of the dropdown."""

    value: str
    detail: str | None = None
    count: int | None = None
    #: The field this row completes to, when the dropdown mixes fields (a bare word matches People,
    #: Tags and more); None when every row shares the one `token`.
    field: str | None = None
    #: What picking this row opens rather than completing a token: `file`, which has no filter.
    opens: str | None = None
    #: Which thing this is, so picking goes straight to it: a name is not an identity, and looking
    #: it back up would be a second query that can disagree. None for a folder, whose path is it.
    id: str | None = None
    #: What a cover address carries to be KEPT by the browser (the user's token, which cover, or a
    #: Site's shipped logo token `icon`), spelled as every entity view spells them so a chip draws
    #: like a card (`entityPicture` in `lib/entity/entity-picture.ts`). Only on kinds with a cover.
    art: str | None = None
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    #: The window of the picture it is drawn as, or None for all of it; withheld with the file
    #: (`kernel/cover_frame.py CoverFrame`).
    cover_frame: CoverFrame | None = None
    icon: str | None = None


class FilterOut(Wire):
    """One filter the query language understands, described for somebody meeting it.

    The language documenting itself where people look: an empty dropdown cannot be told from a box
    that only does free text.
    """

    #: The token, without the colon. What gets typed into the box when this row is chosen.
    field: str
    #: Its name in the interface, which is not always the token (People, `people`).
    label: str
    hint: str
    example: str


class Suggestions(Wire):
    """What the dropdown should show: the filters, matches for what is being typed, and recents.

    One response because the dropdown is one list. Inside a `people:ja` token only `matches`
    answers; elsewhere a bare word may start a filter's name or name a thing, so filters, matches
    and recent searches are offered together, in that order.
    """

    token: str | None = None
    filters: list[FilterOut] = Field(default=[])
    matches: list[SuggestionOut] = Field(default=[])
    recent: list[RecentOut] = Field(default=[])
    #: Where the token being completed starts, so the client replaces exactly it without a second
    #: tokenizer that would disagree about quoting.
    replace_from: int | None = None
    #: Where a picked NAME's span starts, which differs from a filter's: names have spaces, so
    #: `reya so` matched as a phrase is replaced whole by Reya Solberg. Else `replace_from`.
    matched_from: int | None = None
    #: The query these were computed for: the dropdown stays clickable over a round trip, and a
    #: stale row picked would delete what was typed since.
    for_query: str = ""


class Named(Wire):
    """What a plain word names in this library: the people, sites, tags, collections and folders.

    The other half of a search: the files come from the ordinary read, the things from here, each
    carrying the field it filters on.
    """

    items: list[SuggestionOut]


class ParsedClause(Wire):
    """One of the things a query asks for, described so the client can draw it.

    One row of the Filters screen and one chip in the box. `query` is its exact text spelled by
    the SERVER, so a removed chip or an edited row rebuilds the rest character for character; a
    client spelling it would be a second writer.
    """

    #: This clause, written the way it would be typed. The client never composes this.
    query: str
    #: The field it filters on; None for a choice across dimensions.
    field: str | None = None
    #: The values it names. Empty when the clause asks whether the dimension is there at all.
    values: list[str] = Field(default=[])
    #: Whether it excludes rather than includes.
    negated: bool = False
    #: Whether the dimension must be there at all, when that is asked; None for a values clause.
    present: bool | None = None
    #: How the values combine: every one of them, or any one of them.
    match: str = "all"


class ParsedProblem(Wire):
    """A filter whose value nothing could act on, and why, so the chip can say so.

    By field and value, since one value of a multi-value clause can be the unreadable one, and
    that is how the screen finds its chip.
    """

    field: str
    value: str
    reason: str


class ParsedQuery(Wire):
    """What a typed query turns out to mean, told to the client by the one parser.

    So the Filters screen shows what is already in force, including what was typed in the box,
    without a second parser in the client that would disagree about quoting.
    """

    #: The free text, filters taken out: what the box keeps when the Filters screen owns the rest.
    text: str = ""
    #: What the query asks for, one clause per thing it asks. All of them have to hold.
    clauses: list[ParsedClause] = Field(default=[])
    #: Clauses whose value could not be read: each matches nothing and is drawn as an error chip.
    problems: list[ParsedProblem] = Field(default=[])
    #: Each field's values wherever they sit, for one-field controls; `clauses` is the faithful one.
    terms: dict[str, list[str]] = Field(default={})


class RecentOut(Wire):
    """One thing this user's box remembers, and what picking it again does.

    A typed search runs again; a thing picked from the dropdown is opened again, as picking it did.
    """

    #: `query` for a search that was typed and run. Otherwise what kind of thing was picked, by the
    #: same name its row carried: the field it completed to, or `file`.
    kind: str
    #: What identifies it: the query, or the picked thing's id or value.
    subject: str
    #: What the row shows: the query, or the thing's name when picked, kept beside the subject
    #: because a name is not an identity.
    label: str


class KeptNameOut(Wire):
    """One value in a saved search that its chip cannot read from the query alone.

    `field` is the parameter as the query spells it; `value` is what the query holds there (an id,
    or a name nothing answers to any more); `name` is what the thing is called now, or null where
    it is gone.
    """

    field: str
    value: str
    name: str | None = None


class SavedSearchOut(Wire):
    """One saved search, as the client sees it.

    `query` reads as it would be written today: each thing it names by id is given back by the
    name it has now, so the filter applies and reads under today's names without being rewritten
    when something is renamed. `named` carries what the query cannot say for itself.
    """

    id: str
    name: str
    query: str
    #: The wall this filter is about: `asset` for the library, else that wall's noun. Offered only
    #: on its own wall, whose vocabulary it is spelled in.
    kind: str = "asset"
    named: list[KeptNameOut] = Field(default_factory=list)


class NameOut(Wire):
    """One thing kept by id elsewhere, and the name it goes by now."""

    id: str
    name: str


class NamesNow(Wire):
    """What each of a list of ids is called now, for the ones this viewer may be shown.

    An id missing from the answer is a thing that is gone or one this viewer may not see; the two
    are one answer, as they are on every read by id.
    """

    items: list[NameOut]


class SavedSearches(Wire):
    """This user's saved searches, newest first. Nobody else's, ever."""

    items: list[SavedSearchOut]


class RenameSearchRequest(Wire):
    """A new name for a saved search. The query is not in it, and that is the whole point.

    Saving already replaces a name's query, and a rename that could rewrite it is one an empty field
    turns into a wipe.
    """

    name: str = Field(min_length=1, max_length=120)


class SaveSearchRequest(Wire):
    """Keep a query under a name. Both are trimmed of surrounding whitespace server-side."""

    name: str = Field(min_length=1, max_length=120)
    query: str = Field(max_length=1000)
    #: Which wall it was kept on, defaulting to files for a client that knows no walls; the word is
    #: checked at the route, the length here only bounds what is sent.
    kind: str = Field(default="asset", max_length=40)


class RememberSearchRequest(Wire):
    """One thing to put at the top of this user's own memory.

    A posted body because it is a write: recorded from the results GET, another site could point a
    browser at Sift and land an entry in somebody's history. The memory's own three fields, one way
    to add a row (see `RecentOut`).
    """

    #: `query`, or the kind picked: the query language's fields plus the two non-field kinds.
    kind: str = Field(default="query", max_length=40)
    subject: str = Field(max_length=1000)
    label: str = Field(max_length=1000)
    #: How many results the search found, for the record only (never drawn or returned): a search
    #: that found nothing is the interesting kind. None, not zero, when the client did not say.
    results: int | None = Field(default=None, ge=0)


class SearchOpenedRequest(Wire):
    """A file opened from the wall a typed search narrowed: the words, and the file.

    A posted body for the reason `RememberSearchRequest` is one.
    """

    query: str = Field(min_length=1, max_length=1000)
    asset_id: str = Field(min_length=1, max_length=64)
