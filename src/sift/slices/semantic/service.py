# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the feature can do right now and why not, and turning pictures and words into numbers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from sift.kernel import media
from sift.kernel.access import Repository, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Lack, VerdictProduct
from sift.kernel.hardware import HardwareReport
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.memo import PacedAnswer
from sift.kernel.ml import pictures
from sift.kernel.ml.child import devices_here
from sift.kernel.ml.runtime import DeviceUnavailable, resolve_provider
from sift.kernel.ml.weights import Progress, WeightError
from sift.kernel.wiring import Part
from sift.slices.semantic import settings as semantic_settings
from sift.slices.semantic import weights
from sift.slices.semantic.embed import FEATURE, Embedder
from sift.slices.semantic.frames import Reader
from sift.slices.semantic.records import Records
from sift.slices.semantic.similar import CANDIDATES, Similar, SimilarFinder, Tier
from sift.slices.semantic.store import Neighbour, VectorStore, VectorStoreUnavailable

log = get_logger(__name__)


def now_ms() -> int:
    return int(time.time() * 1000)


#: A term true of every counted file, so the same statement gives the denominator (`coverage`).
EVERY_READ_FILE = Lack("1")


class SettingsReader(Protocol):
    """Reading a global preference, handed in at assembly since a feature may not import another."""

    async def get_app(self, key: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class Configured:
    """The settings one piece of work runs under, read once so a file is never half one model."""

    enabled: bool
    family: str
    device: str


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of what one user can see has been described; concealed files are in neither."""

    described: int
    library: int


@dataclass(frozen=True, slots=True)
class Readiness:
    """Whether the feature can do anything, and the sentence to show when it cannot."""

    supported: bool
    enabled: bool
    ready: bool
    family: str
    device: str
    problem: str | None = None
    #: Asked, not counted; the screen that says how many asks `described_by_others`.
    by_another_model: bool = False


class SemanticService:
    """The feature, as one object the routes and the jobs both talk to."""

    def __init__(
        self,
        *,
        store: VectorStore,
        records: Records,
        content: ContentStore,
        repository: Repository,
        preferences: SettingsReader,
        settings: Settings,
        hardware: HardwareReport,
        reader: Reader | None = None,
        similar: SimilarFinder | None = None,
    ) -> None:
        self._store = store
        self._records = records
        self._content = content
        self._unread = PacedAnswer(partial(content.coming_count, VerdictProduct.MEANING.value))
        self._repository = repository
        self._preferences = preferences
        self._settings = settings
        self._hardware = hardware
        self._reader = reader or Reader(settings)
        # The cheap tier; optional so a test of the model half needs no fingerprints.
        self._similar = similar
        self._embedder: Embedder | None = None

    @property
    def settings(self) -> Settings:
        """Where this install keeps things. Read by the routes to say which files are on disk."""
        return self._settings

    # --- what is switched on ------------------------------------------------------------------

    async def enabled(self) -> bool:
        return bool(await self._preferences.get_app(semantic_settings.ENABLED_KEY))

    async def configured(self) -> Configured:
        get = self._preferences.get_app
        return Configured(
            enabled=bool(await get(semantic_settings.ENABLED_KEY)),
            family=str(await get(semantic_settings.MODEL_KEY)),
            device=str(await get(semantic_settings.DEVICE_KEY)),
        )

    async def embedder(self) -> Embedder:
        """The loaded models, made once and rebuilt whenever the settings behind them change."""
        configured = await self.configured()
        existing = self._embedder
        if existing is not None and (
            existing.family != configured.family or existing.device_name != configured.device
        ):
            existing.unload()
            self._embedder = None
        if self._embedder is None:
            self._embedder = Embedder(
                self._settings,
                self._hardware,
                family=configured.family,
                device=configured.device,
            )
        return self._embedder

    def release(self) -> None:
        """Give back the memory the models hold. What switching the feature off does."""
        if self._embedder is not None:
            self._embedder.unload()
            self._embedder = None

    # --- whether it can do anything, and why not ----------------------------------------------

    async def readiness(self) -> Readiness:
        """The four separate ways this cannot run, told apart."""
        configured = await self.configured()
        supported = self._store.available
        # No count: readiness is asked constantly, and the settings screen asks `indexed_frames`.

        problem: str | None = None
        ready = False
        by_another_model = False
        if not supported:
            try:
                self._store.require()
            except VectorStoreUnavailable as failure:
                problem = str(failure)
        elif not configured.enabled:
            problem = None
        else:
            embedder = await self.embedder()
            by_another_model = await self._records.any_described_by_others(embedder.revision)
            if not embedder.installed():
                problem = (
                    "The models have not been obtained yet. Sift does not include them; they are "
                    "fetched once, from their publisher, when you ask for them."
                )
            elif embedder.broken is not None:
                # The card died under a running session; the runner says what to do.
                problem = embedder.broken
            elif embedder.words_refused is not None:
                problem = embedder.words_refused
            else:
                try:
                    await self._check_device(configured.device)
                except DeviceUnavailable as failure:
                    problem = str(failure)
                else:
                    ready = True

        return Readiness(
            supported=supported,
            enabled=configured.enabled,
            ready=ready,
            family=configured.family,
            device=configured.device,
            problem=problem,
            by_another_model=by_another_model,
        )

    async def _check_device(self, device: str) -> None:
        """Whether the chosen device is really there, asked before any work is queued against it."""
        available = await asyncio.to_thread(devices_here, self._settings, FEATURE)
        resolve_provider(device, self._hardware, available, feature=FEATURE)

    # --- turning things into numbers -----------------------------------------------------------

    async def describe_query(self, text: str) -> list[float] | None:
        """A typed query as numbers, or None when this install cannot answer by meaning."""
        if not text.strip():
            return None
        readiness = await self.readiness()
        if not readiness.ready:
            return None
        embedder = await self.embedder()
        try:
            return await embedder.describe_words(text)
        except DeviceUnavailable:
            # A vocabulary that can't load is said by `readiness`; the search takes the ordinary order.
            if embedder.words_refused is None:
                raise
            return None

    async def _reading(self) -> str | None:
        """The revision every index answer passes through, or None while the switch is off."""
        if not await self.enabled():
            return None
        return (await self.embedder()).revision

    async def describes(self, asset_id: str) -> list[float]:
        """What one file looks like as numbers; empty when the model in use has not described it."""
        revision = await self._reading()
        if revision is None:
            return []
        return await self._frames_of(asset_id, revision)

    async def _frames_of(self, asset_id: str, revision: str) -> list[float]:
        """One file's pooled description, asking the record first, as a vec0 read is a scan."""
        described = await self._records.described(asset_id)
        if described is None or described.revision != revision:
            return []
        return await self._store.describes(asset_id, revision=revision)

    async def describes_many(self, asset_ids: Sequence[str]) -> dict[str, list[float]]:
        """What each of these files looks like, as numbers, keyed by file; see the store."""
        revision = await self._reading()
        if revision is None:
            return {}
        return await self._store.describes_many(asset_ids, revision=revision)

    async def describes_anything(self) -> bool:
        """Whether the model in use has described anything at all, in one read."""
        revision = await self._reading()
        if revision is None:
            return False
        return await self._records.describes_anything(revision)

    async def similar_to(
        self, asset_id: str, *, limit: int = CANDIDATES, asker: Viewer | None = None
    ) -> Similar:
        """What else looks like this file, by whichever tier answers, among what `asker` may see."""
        revision = await self._reading() if self._store.available else None
        if revision is not None:
            vector = await self._frames_of(asset_id, revision)
            if vector:
                found = await self._store.nearest_files(
                    vector, revision=revision, limit=limit + 1, asker=asker
                )
                return Similar(
                    tier=Tier.LOOKS,
                    # Every file is nearest to itself, and that is not an answer.
                    neighbours=tuple(one for one in found if one[0] != asset_id)[:limit],
                )
        if self._similar is None:
            return Similar(tier=Tier.MATCHES, neighbours=())
        return await self._similar.perceptual(asset_id, limit=limit, asker=asker)

    async def nearest(
        self, vector: list[float], *, limit: int, asker: Viewer | None = None
    ) -> list[Neighbour]:
        """The files `asker` may see whose frames sit nearest these numbers, for the model used."""
        revision = await self._reading()
        if revision is None:
            return []
        return await self._store.nearest(vector, revision=revision, limit=limit, asker=asker)

    async def describe_frames(self, asset_id: str, frames: list[tuple[int, np.ndarray]]) -> int:
        """Describe one file's sampled frames and keep them; nothing while off. Returns how many."""
        if not frames:
            return 0
        if not await self.enabled():
            return 0
        embedder = await self.embedder()
        described = await embedder.describe_pictures([frame for _, frame in frames])
        await self._store.put(
            asset_id,
            # `strict`: a missing description would shift every later moment onto the wrong picture.
            [(at_ms, vector) for (at_ms, _), vector in zip(frames, described, strict=True)],
            revision=embedder.revision,
        )
        return len(described)

    # --- obtaining the models -------------------------------------------------------------------

    async def install_from_file(self, weight_id: str, source: Path) -> None:
        """Take one of the files from a copy the operator already has. The offline answer."""
        weight = weights.CATALOG.get(weight_id)
        if weight is None:
            raise WeightError(f"there is no model called {weight_id!r}")
        await weights.store(self._settings).install_from_file(weight, source)

    async def install_models(
        self,
        *,
        progress: Progress | None = None,
        force: bool = False,
        on_file: Callable[[int, int, str], None] | None = None,
    ) -> list[str]:
        """Fetch the chosen set's missing files; `force` refetches, `on_file` names each one."""
        configured = await self.configured()
        store = weights.store(self._settings)
        wanted = [
            weight
            for weight in weights.working_set(configured.family)
            if force or not store.installed(weight)
        ]
        installed: list[str] = []
        for index, weight in enumerate(wanted):
            # Announced before the transfer, so a screen can say which of three files this is.
            if on_file is not None:
                on_file(index, len(wanted), weight.role)
            await store.fetch(weight, progress=progress, fresh=force)
            installed.append(weight.id)
        return installed

    async def clear_index(self, by: Actor | None = None) -> None:
        """Throw the whole index away, numbers and the record of them; not the off switch."""
        await self._store.clear()
        await self._records.forget_all(by)
        log.info("semantic.index.removed")

    # --- describing the library ------------------------------------------------------------------

    async def viewer_for(self, user_id: str) -> Viewer | None:
        """The user a queued job acts for, or None; hidden files included: a job has no session."""
        return await self._repository.load_viewer(user_id, show_hidden=True)

    async def waiting_count(self, viewer: Viewer) -> int:
        """How many read files this user can see that the configured model has not described."""
        embedder = await self.embedder()
        # One count in the kernel, the Build sheet's own term, so the two numbers agree.
        lack = replace(self._records.lack(embedder.revision), product=VerdictProduct.MEANING)
        counted = await self._content.count_lacking_visible(
            viewer.id, [lack], admin=viewer.is_admin
        )
        return counted.files

    async def unread_count(self) -> int:
        """How many files not read yet will want describing, which `waiting_count` cannot see."""
        return await self._unread.get()

    async def coverage(self, viewer: Viewer) -> Coverage:
        """How far the describing has got for whoever is asking, in one scoped query."""
        embedder = await self.embedder()
        counted = await self._content.count_lacking_visible(
            viewer.id, [self._records.lack(embedder.revision), EVERY_READ_FILE]
        )
        undescribed, library = counted.each
        return Coverage(described=library - undescribed, library=library)

    async def waiting_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files the configured model has not described; empty while it can't run."""
        if not await self.enabled():
            return set()
        readiness = await self.readiness()
        if not readiness.ready:
            return set()
        embedder = await self.embedder()
        return await self._records.unsettled_among(asset_ids, embedder.revision)

    async def lack(self) -> Lack | None:
        """Not described by the configured model, as a term of the Build's count; None while off."""
        if not await self.enabled():
            return None
        readiness = await self.readiness()
        if not readiness.ready:
            return None
        embedder = await self.embedder()
        return self._records.lack(embedder.revision)

    async def described_count(self) -> int:
        """How many files the configured model has described; an older model's do not count."""
        embedder = await self.embedder()
        return await self._records.described_count(embedder.revision)

    async def described_by_others(self) -> int:
        """How many files a previous model described: counted among what is still to do."""
        embedder = await self.embedder()
        return await self._records.described_by_others(embedder.revision)

    async def prune_index(self) -> int:
        """Drop the vectors of files gone from the library, as the kernel says. Returns how many."""
        held = await self._store.held_ids()
        if not held:
            return 0
        alive = await self._content.existing_ids(held)
        return await self._store.prune([asset_id for asset_id in held if asset_id not in alive])

    async def indexed_frames(self) -> int:
        """How many moments are held. Zero where the index cannot exist at all."""
        return await self._store.count()

    async def describe_asset(self, asset_id: str) -> int:
        """Read one file, describe its moments and keep them; an unreadable file gets a verdict."""
        readiness = await self.readiness()
        if not readiness.ready:
            return 0

        embedder = await self.embedder()
        # Not described again if this revision described it since the last read (`probed_at`): an
        # edit rewrites the bytes, and the re-probe is what refreshes the description.
        on_record = await self._records.described(asset_id)
        asset = await self._content.get(asset_id)
        read_at_ms = (asset.probed_at * 1000) if asset is not None and asset.probed_at else 0
        if (
            on_record is not None
            and on_record.revision == embedder.revision
            and on_record.at_ms >= read_at_ms
        ):
            log.info("semantic.describe.already", asset_id=asset_id, frames=on_record.frames)
            return on_record.frames
        try:
            source = await media.resolve_decodable(self._content, asset_id, settings=self._settings)
        except media.NoReadableCopy:
            # No copy to read now: a transient verdict, cleared by the next scan that sees the file.
            await self._give_up(
                asset_id,
                code="no_copy",
                reason="No copy of this file could be read.",
                transient=True,
            )
            return 0
        try:
            moments = await self._reader.read(
                source.path,
                media_type=str(source.asset.media_type),
                duration_ms=int(source.asset.duration_ms or 0),
            )
        except media.FFmpegError as error:
            # Damaged bytes do not mend: one verdict, in the decoder's words, and no retry.
            if not media.is_broken_data(str(error)):
                raise
            await self._give_up(
                asset_id,
                code="no_frame" if source.asset.media_type != "video" else "no_frame_decoded",
                reason=pictures.damaged(str(error)),
                transient=False,
            )
            return 0
        if not moments:
            await self._give_up(
                asset_id,
                code="no_frame_decoded",
                reason="No moment of this file could be decoded, so nothing in it could be described.",
                transient=False,
            )
            return 0
        described = await self.describe_frames(
            asset_id, [(moment.at_ms, moment.pixels) for moment in moments]
        )
        await self._records.mark(
            asset_id, revision=embedder.revision, frames=described, at_ms=now_ms()
        )
        return described

    async def _give_up(self, asset_id: str, *, code: str, reason: str, transient: bool) -> None:
        """Write down that this file could not be described, and why. See `file_verdicts`."""
        await self._content.record_verdict(
            asset_id, VerdictProduct.MEANING, code=code, reason=reason, transient=transient
        )
        log.info("semantic.describe.gave_up", asset_id=asset_id, code=code, transient=transient)


SERVICE: Part[SemanticService] = Part("semantic")
