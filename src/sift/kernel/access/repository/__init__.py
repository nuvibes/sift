# SPDX-License-Identifier: AGPL-3.0-or-later
"""The only sanctioned way to read an asset or a folder.

Everything else in Sift asks this module for rows, and it never gets a row the person asking is
not allowed to see. A feature that wrote its own `SELECT ... FROM assets` would get rows that
look exactly like rows it was allowed to have, which is why a lint rule refuses to let it.

The resolve is compiled into the SQL rather than applied to the rows afterwards. That is not an
optimisation. A page of fifty that gets filtered down to thirty in Python is a broken paginator
and a wrong count, and the moment the count is wrong the enforcement has moved out of the one
place that cannot be forgotten and into every caller that has to remember to reapply it.

There is one query. The grid, the single-asset fetch and the permission check are the same
statement with different parameters, because the way an unauthorized read gets served is that
the detail endpoint and the list endpoint disagree about who may see what.

## The rule

Fail-closed throughout: anything the resolver cannot work out is a no.

    0. Hiding conceals, and it conceals from the user who did it and nobody else. An asset is
       concealed for a viewer if that viewer hid it, hid any folder or root it sits under, or hid a
       person, collection, tag or site it belongs to.
    1. An admin sees everything else.
    2. A restrict on a person, collection, tag or site is final. Nothing outranks it.
    3. A grant on the item itself is then final, whichever way it points.
    4. A restrict on a folder or root then wins, and any explicit share then grants visibility.
    5. Nothing at all means no.

Steps 2 and 4 are both restrict, and the split between them is deliberate.

A folder is WHERE a file is; a person, tag, site or collection is WHAT IT IS OF. Sharing one file
out of an otherwise closed folder is an ordinary thing to want, so an item grant outranks a folder
restrict. Sharing one file OF a restricted person is not: it hands over the very thing the restrict
was protecting, and it does it silently, one file at a time. So a logical restrict outranks
everything below an admin, including a share on the file itself.

Read the other way: restrict on a person means that person is off-limits to this user, and no
grant made anywhere else quietly reopens them.

Either way, restrict is never undone by inheritance. Without that, and with sharing built on
inheritance, "never show anything in this folder" could always be undone by someone sharing a
collection that happened to contain one of its files. One restricted copy of a file restricts the
file.

Silence is not restrict, though, and that distinction is the whole design. No row means private:
it keeps a guest out by default, but a share made deliberately somewhere else may still reach the
file. If absence outranked a share, then sharing a collection could never reveal anything at all
(every file sits in some folder that nobody thought to share), and the entire logical overlay
would be decoration.

Within the folder chain, nearest wins: a share on a subfolder overrides a restrict on its parent,
which is how one folder is opened up inside an otherwise closed tree. Across chains it does not:
a file with two copies is only as visible as its least visible copy.

Step 0 and the rest are two different questions and are kept apart deliberately. Steps 1-5 decide
whether somebody may see a thing at all; step 0 decides whether the user asking wants it on
their own screen right now. So hiding is not a grant, nothing can share around it, and it never
takes anything away from anybody else: keeping something off another user's screen is what
restrict is for. That is also what makes hiding safe to leave in everybody's hands, guests
included, and it is why a thing one user hid is plainly visible to the next.
"""

from __future__ import annotations

from sift.kernel.access.repository.assets import (
    ADMIN_FACETS,
    DEFAULT_SORT,
    FACET_LABELS,
    FACETS,
    FACETS_RENAMED,
    RELEVANCE,
    SEEKABLE_SORTS,
    SHUFFLE_MODULUS,
    SIMILARITY,
    SORT_KEYS,
)
from sift.kernel.access.repository.entities import (
    ENTITY_SORT_KEYS,
    ENTITY_SORT_SEEN,
    SONG_SORT_KEYS,
)
from sift.kernel.access.repository.store import (
    MAX_PAGE_SIZE,
    Named,
    Recording,
    Repository,
)
from sift.kernel.access.repository.views import (
    AccessError,
    Actionable,
    AliasMatch,
    AssetPage,
    AssetView,
    CollectionPage,
    CollectionView,
    Enrichment,
    FacetCount,
    Folder,
    FolderContents,
    Grant,
    GrantMark,
    GrantSource,
    LoopPage,
    LoopTag,
    LoopView,
    Memberships,
    PeoplePage,
    PersonSuggestion,
    PhotoSetPage,
    PhotoSetView,
    Reaches,
    ServedDerivative,
    SitePage,
    SiteSuggestion,
    SongPage,
    SongView,
    TagPage,
    TagSuggestion,
    UsernamePage,
    UsernameSuggestion,
    VaultSource,
)
from sift.kernel.access.viewer import ConcealerType

__all__ = [
    "ADMIN_FACETS",
    "DEFAULT_SORT",
    "ENTITY_SORT_KEYS",
    "ENTITY_SORT_SEEN",
    "FACETS",
    "FACETS_RENAMED",
    "FACET_LABELS",
    "MAX_PAGE_SIZE",
    "RELEVANCE",
    "SEEKABLE_SORTS",
    "SHUFFLE_MODULUS",
    "SIMILARITY",
    "SONG_SORT_KEYS",
    "SORT_KEYS",
    "AccessError",
    "Actionable",
    "AliasMatch",
    "AssetPage",
    "AssetView",
    "CollectionPage",
    "CollectionView",
    "ConcealerType",
    "Enrichment",
    "FacetCount",
    "Folder",
    "FolderContents",
    "Grant",
    "GrantMark",
    "GrantSource",
    "LoopPage",
    "LoopTag",
    "LoopView",
    "Memberships",
    "Named",
    "PeoplePage",
    "PersonSuggestion",
    "PhotoSetPage",
    "PhotoSetView",
    "Reaches",
    "Recording",
    "Repository",
    "ServedDerivative",
    "SitePage",
    "SiteSuggestion",
    "SongPage",
    "SongView",
    "TagPage",
    "TagSuggestion",
    "UsernamePage",
    "UsernameSuggestion",
    "VaultSource",
]
