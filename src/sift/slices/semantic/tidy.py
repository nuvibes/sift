# SPDX-License-Identifier: AGPL-3.0-or-later
"""Descriptions of files that are no longer in the library.

Every other table that names an asset carries a foreign key back to it, so removing a file removes
what was recorded about it in the same statement. **The vectors cannot.** They live in a virtual
table (that is what makes a nearest-neighbour search possible at all) and SQLite takes no
foreign key on one. So a deleted file leaves its descriptions behind: invisible, never returned by
any search, and growing.

There are two halves to answering that and both are here for a reason.

**The sweep prunes as it starts**, which bounds how long dead weight can sit there to "until the
next pass over the library" rather than "for ever". That is housekeeping inside the feature's own
work and needs nobody to ask for it.

**This is the button**, and it exists because the sweep only runs when somebody asks it to describe
their library, which on a settled install is never. Registered as a tidying so it obeys the two
rules that hold for all of them: it says what it would remove before removing anything, and it does
not run on its own.

Nothing here is irreversible in the way the other tidyings are. What is removed is a description
that can be computed again by reading the file, except that the file is gone, which is the whole
reason the row is being removed.
"""

from __future__ import annotations

from sift.kernel.log import get_logger
from sift.kernel.ml.weights import WeightError
from sift.kernel.tidy import Leftovers, Resources, existing_asset_ids, register_tidying
from sift.slices.semantic import weights
from sift.slices.semantic.records import Records
from sift.slices.semantic.settings import MODEL_KEY
from sift.slices.semantic.store import VectorStore

log = get_logger(__name__)


class OrphanedDescriptions:
    """Descriptions held for files that have left the library."""

    name = "orphaned-descriptions"
    costly = False
    title = "Descriptions of files you no longer have"
    noun = "description"
    nouns = "descriptions"
    detail = (
        "What the search-by-meaning index remembers about files that have since left your "
        "library. No search can return these files any more. Everything still in "
        "your library keeps its description, so searching is unaffected."
    )

    def __init__(self, resources: Resources) -> None:
        self._store = VectorStore(resources.database)
        self._resources = resources

    async def _gone(self) -> list[str]:
        """Which described files are not in the library any more.

        Asked through the kernel rather than answered here. The assets table carries permissions,
        and both a query against it and a reach for the content store from inside a feature are
        refused by gates, rightly, since that store reads any asset without checking anything.
        """
        held = await self._store.held_ids()
        if not held:
            return []
        alive = await existing_asset_ids(self._resources, held)
        return [asset_id for asset_id in held if asset_id not in alive]

    async def survey(self) -> Leftovers:
        gone = await self._gone()
        return Leftovers(
            name=self.name,
            title=self.title,
            detail=self.detail,
            noun=self.noun,
            nouns=self.nouns,
            count=len(gone),
            # Rows in a table, not files on disk. Quoting a number here would invite somebody to
            # run it to reclaim space, which is not what this is for. See `Leftovers`.
            frees_bytes=None,
        )

    async def run(self) -> int:
        removed = await self._store.prune(await self._gone())
        log.info("semantic.tidy.orphaned_descriptions", removed=removed)
        return removed


class SupersededDescriptions:
    """Descriptions made by a Smart Search model that is no longer the one in use.

    **Nothing in a search can reach them.** Every read of the index is filtered to one model's
    revision, because numbers from two models are the same length and mean nothing to each other,
    so the previous model's descriptions stopped being an answer the moment the model changed, and
    they stay for ever: a file is only described again when a Build reaches it.

    A press and not something a sweep does on its own, for the one cost worth saying out loud: going
    BACK to the previous model would then mean describing the whole library with it again. The
    detail says so, because that is the decision somebody is making when they press it.

    Counted in FILES, from the records beside the index (`semantic_indexed`, one row per file,
    indexed), rather than by grouping the vector table, which is a whole-table read. Both halves
    go together on the run: the frames, and the records that claim them: a record left behind
    would go on counting numbers that are no longer there.
    """

    name = "superseded-descriptions"
    costly = False
    title = "Descriptions from a model you no longer use"
    noun = "description"
    nouns = "descriptions"
    detail = (
        "What Smart Search worked out about your files with a model you have since switched away "
        "from. No search reads them: every search asks the model in use now. Going back to the "
        "earlier model would mean describing your whole library with it again."
    )

    def __init__(self, resources: Resources) -> None:
        self._store = VectorStore(resources.database)
        self._records = Records(resources.database)
        self._preferences = resources.preferences

    async def _in_use(self) -> str | None:
        """The revision the chosen model writes, or None where it cannot be known here.

        None is the honest answer to "which descriptions are stale" when nothing says which model
        is chosen, and it counts nothing and removes nothing, never "all of them".
        """
        if self._preferences is None:
            return None
        family = str(await self._preferences.get_app(MODEL_KEY))
        try:
            return weights.working_set(family)[0].revision
        except WeightError:
            log.warning("semantic.tidy.unknown_model", family=family)
            return None

    async def survey(self) -> Leftovers:
        revision = await self._in_use()
        return Leftovers(
            name=self.name,
            title=self.title,
            detail=self.detail,
            noun=self.noun,
            nouns=self.nouns,
            count=None if revision is None else await self._records.described_by_others(revision),
            # Rows in a table, not files on disk. See `OrphanedDescriptions`.
            frees_bytes=None,
        )

    async def run(self) -> int:
        revision = await self._in_use()
        if revision is None:
            return 0
        frames = await self._store.purge_other_revisions(revision)
        files = await self._records.forget_others(revision)
        log.info("semantic.tidy.superseded_descriptions", files=files, frames=frames)
        return files


def register() -> None:
    register_tidying(OrphanedDescriptions.name, OrphanedDescriptions)
    register_tidying(SupersededDescriptions.name, SupersededDescriptions)
