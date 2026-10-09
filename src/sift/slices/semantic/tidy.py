# SPDX-License-Identifier: AGPL-3.0-or-later
"""Descriptions of files no longer in the library, which no foreign key can reach."""

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
        """Which described files are not in the library any more, asked through the kernel."""
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
            # Rows in a table, not files on disk, so no space is quoted.
            frees_bytes=None,
        )

    async def run(self) -> int:
        removed = await self._store.prune(await self._gone())
        log.info("semantic.tidy.orphaned_descriptions", removed=removed)
        return removed


class SupersededDescriptions:
    """Descriptions from a Smart Search model no longer in use, unreachable by any search."""

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
        """The revision the chosen model writes, or None, which removes nothing."""
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
