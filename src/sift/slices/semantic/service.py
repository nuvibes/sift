# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the feature can do right now, and the two things it does that nothing else does.

Everything here answers one of two questions:

**"Can this run at all, and if not, why?"** There are four separate ways it cannot, and they need
four different sentences because they need four different actions from whoever is reading. The
add-on could not be loaded, so this machine cannot hold an index at all. The switch is off. The
models have not been obtained yet. Or the device it was set to use is not there. Collapsing those
into "unavailable" is how somebody ends up restarting a container to fix a switch that is off.

**"Turn this into numbers."** Describing a typed query, and describing the frames of one file:
both off the event loop, because running a model holds the interpreter for its whole duration and
everything else in the process stops while it does.

**Nothing here runs unless the switch is on.** Not a model load, not a download, not a row. Every
entry point asks first. A job queued before somebody switched it off finds it off and stops rather
than doing the work its payload describes.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
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


#: A term true of every file the content store counts, so its count is the DENOMINATOR of the two
#: the same statement answers. See `SemanticService.coverage`.
#:
#: A `Lack` is a condition on the assets row, and the store already restricts the rows it counts to
#: the files Sift has read and, in the scoped form, to the ones a user may see. A condition of
#: `1` therefore names exactly that population and needs no second statement, no second moment and
#: no second rule about who may see what, which is the whole reason the total is asked for here
#: rather than counted anywhere else.
EVERY_READ_FILE = Lack("1")


class SettingsReader(Protocol):
    """Reading a global preference.

    Declared here rather than imported from the feature that stores preferences, because a feature
    may not import another feature. An implementation is handed in when the app is assembled.
    """

    async def get_app(self, key: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class Configured:
    """The settings one piece of work runs under, read at the start of it.

    Read once per piece of work rather than once per frame: a change made halfway through a file
    would otherwise describe its first half with one model and its second with another, and the two
    halves would not be comparable with each other, let alone with anything else.
    """

    enabled: bool
    family: str
    device: str


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of what one user can see has been described, as a fraction it can be told.

    Both numbers over the same population (the files this user may see that Sift has read),
    so the sentence built from them cannot compare two different libraries. A concealed file is in
    neither: what the vault holds back is not this user's business until it is opened, and a
    denominator that moved when the vault did would say so out loud.
    """

    described: int
    library: int


@dataclass(frozen=True, slots=True)
class Readiness:
    """Whether the feature can do anything, and the sentence to show when it cannot.

    `ready` is not the same as `enabled`, and a screen needs both: switched on with no models on
    disk is the ordinary state one second after somebody turns it on, and it has to read as "fetch
    the models" rather than as something broken.
    """

    supported: bool
    enabled: bool
    ready: bool
    family: str
    device: str
    problem: str | None = None
    #: Files whose description is a previous model's. In the index, out of every search, and
    #: counted among what is still to do: the screen says so rather than letting results be
    #: quietly partial.
    described_by_another_model: int = 0


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
        self._repository = repository
        self._preferences = preferences
        self._settings = settings
        self._hardware = hardware
        self._reader = reader or Reader(settings)
        # The cheap way of answering "what looks like this". Optional because it reads the content
        # layer and a test of the model half has no business seeding fingerprints.
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
        """The loaded models, made once and rebuilt if the settings behind them changed.

        Rebuilt rather than reconfigured: the family and the device are both baked into a prepared
        session, and quietly keeping the old one is how a change to either takes effect for
        everything except the thing already running.
        """
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
        # No count here, deliberately. Readiness is asked on every search phrase, every draw of
        # the search box, every Build page and every described file, and counting the whole vector
        # table each time is linear: tens of milliseconds cold at sixty thousand rows, seconds per
        # draw at a couple of million files. Only the settings screen wants the number, and it
        # asks `indexed_frames` for it.

        problem: str | None = None
        ready = False
        described_by_another_model = 0
        if not supported:
            try:
                self._store.require()
            except VectorStoreUnavailable as failure:
                problem = str(failure)
        elif not configured.enabled:
            problem = None
        else:
            embedder = await self.embedder()
            described_by_another_model = await self._records.described_by_others(embedder.revision)
            if not embedder.installed():
                problem = (
                    "The models have not been obtained yet. Sift does not include them; they are "
                    "fetched once, from their publisher, when you ask for them."
                )
            elif embedder.broken is not None:
                # The card died underneath a running session. The runtime would happily open it
                # again; the runner knows better, and says what to do.
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
            described_by_another_model=described_by_another_model,
        )

    async def _check_device(self, device: str) -> None:
        """Whether the chosen device is really there, asked before any work is queued against it.

        Asked here rather than discovered when a job runs. A card that is not there would make
        every scan fail into the job log while the settings screen said everything was fine, which
        is a fault nobody can see from the screen that caused it.
        """
        available = await asyncio.to_thread(devices_here, self._settings, FEATURE)
        resolve_provider(device, self._hardware, available, feature=FEATURE)

    # --- turning things into numbers -----------------------------------------------------------

    async def describe_query(self, text: str) -> list[float] | None:
        """A typed query as numbers, or None when this install cannot answer by meaning.

        None rather than an exception: a search box asking for something the install cannot do is
        an ordinary state, not a fault, and the caller falls back to the ordinary order.
        """
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
        """The revision a read of the index answers by, or None while the switch is off.

        The ONE place every answer the index gives passes through, so the switch governs them all:
        what a file looks like, what is near some numbers, whether anything is described, and the
        better tier of "what looks like this". Asked by each reader rather than by each caller,
        because a caller that forgets to ask goes on answering by the description model with Smart
        Search switched off, and "Similar to this" under every file is such a caller. Off answers as
        an index that holds nothing does, which every caller treats as "fall back to the cheaper
        way".

        The bookkeeping reads (how many are described, what is waiting, the sweep) read the
        revision directly: they count what is kept, and answer nothing about what a file looks like.
        """
        if not await self.enabled():
            return None
        return (await self.embedder()).revision

    async def describes(self, asset_id: str) -> list[float]:
        """What one file looks like, as numbers, or empty when the model in use has not
        described it. Another model's numbers are not an answer; see the store."""
        revision = await self._reading()
        if revision is None:
            return []
        return await self._frames_of(asset_id, revision)

    async def _frames_of(self, asset_id: str, revision: str) -> list[float]:
        """One file's pooled description, asking the RECORD first whether there is one.

        The record is an ordinary table keyed by the file, so this costs a primary-key lookup. The
        vectors are in a `vec0` virtual table where the file is a plain column and only the
        revision partitions it, so reading them is a scan of that whole partition: over a hundred
        milliseconds on a large library, the SAME whether the file has frames or none at all. A pass
        that asks about one file after another would pay a scan per file to be told nothing, and
        most files are undescribed until the background pass has been round the library: minutes of
        scanning for an answer this read gives in no measurable time.

        The record is the authority on what has been described, not a cache of it. Frames whose
        file has no record are either orphaned by a deleted file (nothing cascades off a virtual
        table, see the store) or left by a stop between writing the frames and marking the file,
        and in both cases treating the file as undescribed is what the rest of the feature already
        does: the sweep describes it again, and `put` replaces what is there.
        """
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
        """Whether the model in use has described anything at all.

        One read, for a caller about to ask about a file at a time. See `SemanticSeam.can_answer`.
        """
        revision = await self._reading()
        if revision is None:
            return False
        return await self._records.describes_anything(revision)

    async def similar_to(
        self, asset_id: str, *, limit: int = CANDIDATES, asker: Viewer | None = None
    ) -> Similar:
        """What else looks like this file, by whichever tier can answer, ranked only among what
        `asker` may see.

        The better tier first, and it is skipped rather than failed when it cannot answer, which
        is the ordinary state for a file the background pass has not reached yet, not an error. The
        cheap tier then answers the same question with what every file already carries.

        Which one answered comes back with the result: the two are not interchangeable.
        """
        revision = await self._reading() if self._store.available else None
        if revision is not None:
            vector = await self._frames_of(asset_id, revision)
            if vector:
                found = await self._store.nearest(
                    vector, revision=revision, limit=limit, asker=asker
                )
                return Similar(
                    tier=Tier.LOOKS,
                    neighbours=tuple(
                        (neighbour.asset_id, neighbour.distance)
                        for neighbour in found
                        # Every file is nearest to itself, and that is not an answer.
                        if neighbour.asset_id != asset_id
                    ),
                )
        if self._similar is None:
            return Similar(tier=Tier.MATCHES, neighbours=())
        return await self._similar.perceptual(asset_id, limit=limit, asker=asker)

    async def nearest(
        self, vector: list[float], *, limit: int, asker: Viewer | None = None
    ) -> list[Neighbour]:
        """The files `asker` may see whose frames sit nearest these numbers, among those the model
        in use described, as only its own frames can be near them (see `describe_query`)."""
        revision = await self._reading()
        if revision is None:
            return []
        return await self._store.nearest(vector, revision=revision, limit=limit, asker=asker)

    async def describe_frames(self, asset_id: str, frames: list[tuple[int, np.ndarray]]) -> int:
        """Describe one file's sampled frames and keep them. Returns how many were kept.

        Does nothing at all when the feature is off, which is what a job queued before somebody
        switched it off must do.
        """
        if not frames:
            return 0
        if not await self.enabled():
            return 0
        embedder = await self.embedder()
        described = await embedder.describe_pictures([frame for _, frame in frames])
        await self._store.put(
            asset_id,
            # `strict` because a description missing for a frame is not a shorter answer, it is a
            # moment silently attached to the wrong picture, and every one after it too.
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
        """Fetch whichever of the chosen set's three files are not already here.

        Already-present files are skipped rather than fetched again: the two sets share a
        vocabulary, so switching between them is one file's difference and not a second gigabyte.

        `force` fetches them anyway, and it exists because "already here" is a weaker statement
        than it sounds: a file is called installed if it EXISTS, and whether it is the right file
        is a separate question answered by reading the whole of it. So a truncated or replaced
        model is installed, refuses to load, and says "delete it and fetch it again": an
        instruction that could only be followed at a shell. This is that instruction, as a button.

        Without it, offering to fetch them again would do nothing at all: every file is present,
        every one is skipped, and the job finishes instantly having downloaded nothing while the
        screen reports success.

        `on_file` is called as each transfer begins, with its position, how many there are, and what
        it is for. The set is three files (a picture reader, a word reader and a vocabulary) of
        very different sizes, so `progress` alone runs to a hundred percent and back to zero three
        times with nothing to say why.
        """
        configured = await self.configured()
        store = weights.store(self._settings)
        wanted = [
            weight
            for weight in weights.working_set(configured.family)
            if force or not store.installed(weight)
        ]
        installed: list[str] = []
        for index, weight in enumerate(wanted):
            # Announced BEFORE the transfer starts, so a screen can say which file this is before
            # the first byte rather than after the last. A working set is three separate downloads
            # of wildly different sizes, and a bar that restarted at zero three times with nothing
            # to say why reads as a download that keeps failing and starting over.
            if on_file is not None:
                on_file(index, len(wanted), weight.role)
            await store.fetch(weight, progress=progress, fresh=force)
            installed.append(weight.id)
        return installed

    async def clear_index(self, by: Actor | None = None) -> None:
        """Throw the whole index away. Deliberate, and separate from switching the feature off.

        Both halves go: the numbers, and the record of which files they came from. Dropping only
        one leaves an index that reports itself complete and finds nothing, or a work list that
        will not run because every file is already marked done. `by` is who pressed it, written
        down in History with the second half ("You deleted everything in Smart Search").
        """
        await self._store.clear()
        await self._records.forget_all(by)
        log.info("semantic.index.removed")

    # --- describing the library ------------------------------------------------------------------

    async def viewer_for(self, user_id: str) -> Viewer | None:
        """The user a queued job is acting for, or None if it has gone.

        A job outlives the request that started it, so the user it names may have been deleted
        or turned off in between. Resolved through the access layer, which is what decides what
        that user can see: the job holds an id and never an answer.

        **Hidden files are included, and that is the one deliberate difference from a request.**
        Whether the vault is open is a fact about a browsing session, and a job has no session, so
        a sweep rebuilt from a user id alone would always run with it shut, and a hidden file would
        never be described no matter what the person who pressed the button was looking at.

        Including them changes what gets INDEXED, not what gets SHOWN. Every search settles its
        results against whoever is asking, so a file found this way stays exactly as hidden as it
        was; what it stops is a file being permanently invisible to the machinery.
        """
        return await self._repository.load_viewer(user_id, show_hidden=True)

    async def waiting_count(self, viewer: Viewer) -> int:
        """How many files this user can see that the configured model has NOT described.

        The number a person reads as "still to do", and it has to be that number rather than
        something near it. The size of the page walk is how many files there are ALTOGETHER, shown
        as "still to do", a library that had just been fully described would read "508 described,
        505 still to do" and look stuck at the exact moment it had finished.

        One query, and the set of described ids is handed to it as a filter to be excluded. That
        set is already read whole into memory for the sweep, for the reason written over
        `settled_ids`, so this is the same decision applied once more rather than a new one. Asking
        the access layer rather than counting rows means the answer is scoped to what this user
        may see, which is what the sweep will queue and therefore what is genuinely left.
        """
        embedder = await self.embedder()
        # One count in the kernel, over what this user can see: the same term the Build's sheet
        # counts the library by, over the files Sift has read, with the feature's verdicts
        # excluded the same way, so this number and the sheet's Meaning row agree, without handing
        # the whole settled set to the access layer as one JSON array (tens of megabytes at a couple
        # of million files) to be excluded row by row.
        lack = replace(self._records.lack(embedder.revision), product=VerdictProduct.MEANING)
        counted = await self._content.count_lacking_visible(viewer.id, [lack])
        return counted.files

    async def coverage(self, viewer: Viewer) -> Coverage:
        """How far the describing has got, for whoever is asking, in one query.

        Written for a sentence under a search by meaning. Searching by meaning can only answer out
        of what has been described, and until the background pass has been round the library that
        can be a small minority of it. Without the fraction on screen, a short answer to a good
        phrase reads as Sift not having the files.

        Two terms of one statement rather than two statements, because they have to agree: the
        denominator is a term true of every read file, and the numerator is the rest of it once the
        undescribed are taken off. Counting them separately means two scans of the library and two
        moments, and a pass that describes a file between them makes the pair say something neither
        would say alone.

        **Scoped, and to the same rule the wall is.** `count_lacking_visible` joins the stored
        visibility, so this is what this user can see rather than what exists: the only figure
        that can be shown without answering "is there something hidden here".

        A file the feature has given up on (unreadable, or nothing in it to look at) counts as
        undescribed here, and deliberately: the term carries no product, so the verdict does not
        excuse it. "Still to do" on the settings screen leaves those out because nothing is going
        to do them; this sentence is about how much of the library a search can reach, and a file
        Sift cannot describe is a file a search by meaning will never return.
        """
        embedder = await self.embedder()
        counted = await self._content.count_lacking_visible(
            viewer.id, [self._records.lack(embedder.revision), EVERY_READ_FILE]
        )
        undescribed, library = counted.each
        return Coverage(described=library - undescribed, library=library)

    async def waiting_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files the configured model has not described. Empty while the feature
        is off or its models are not there: nothing is going to be described."""
        if not await self.enabled():
            return set()
        readiness = await self.readiness()
        if not readiness.ready:
            return set()
        embedder = await self.embedder()
        return await self._records.unsettled_among(asset_ids, embedder.revision)

    async def lack(self) -> Lack | None:
        """Not described by the configured model, as one term of the Build's count. None while the
        feature is off or its models are not there: the same answer `waiting_among` gives."""
        if not await self.enabled():
            return None
        readiness = await self.readiness()
        if not readiness.ready:
            return None
        embedder = await self.embedder()
        return self._records.lack(embedder.revision)

    async def described_count(self) -> int:
        """How many files the configured model has described. Files described by an older one do
        not count: their numbers are not comparable with anything being searched now."""
        embedder = await self.embedder()
        return await self._records.described_count(embedder.revision)

    async def prune_index(self) -> int:
        """Drop the vectors of files that have left the library. Returns how many files.

        Swept rather than hooked onto the delete, for two reasons that both point the same way: the
        vectors live in a virtual table, which takes no foreign key, so nothing cascades, and the
        feature that deletes files may not import this one, so there is nowhere to put the hook. Run
        at the start of every sweep, which is the pass that already walks the whole library, so how
        long dead weight can sit there is bounded by "until the next sweep" rather than "for ever".

        The question "is this still a file" is put to the kernel rather than answered here. The
        assets table carries permissions and a feature does not query it: there is a gate that
        refuses one.
        """
        held = await self._store.held_ids()
        if not held:
            return 0
        alive = await self._content.existing_ids(held)
        return await self._store.prune([asset_id for asset_id in held if asset_id not in alive])

    async def indexed_frames(self) -> int:
        """How many moments are held. Zero where the index cannot exist at all."""
        return await self._store.count()

    async def describe_asset(self, asset_id: str) -> int:
        """Read one file, describe its moments, and keep them. Returns how many were kept.

        Does nothing at all when the feature is off or cannot run, which is what a job queued
        before somebody switched it off has to do rather than doing the work its payload describes.

        **A file with nothing readable in it is written down as such, and not as done.** Marking
        it described with no frames would keep it off every later sweep, the right outcome, by
        recording a permanent yes about a file nothing had looked at, and would drop what an
        earlier read had found; a share stalled past its timeout would produce the same row as a
        corrupt file. It is a verdict instead: the sweep and the Build leave a verdicted file out,
        what was held about it stays, and a retry is one press away.
        """
        readiness = await self.readiness()
        if not readiness.ready:
            return 0

        embedder = await self.embedder()
        # A FILE DESCRIBED SINCE IT WAS LAST READ IS NOT DESCRIBED AGAIN.
        #
        # Without this, a second `semantic_describe` for one file pays the whole pass: reading
        # thirty moments out of it and putting each through the model. A retry after a failure
        # later in this method starts again from the top, and two probes of one file hand out two
        # of these, so the redundant ask is ordinary rather than exotic.
        #
        # THREE CONDITIONS, AND THE THIRD IS THE ONE THAT IS EASY TO LEAVE OUT.
        #
        # By REVISION, because a file described by a different model is one the index cannot search
        # and the Build exists to describe again: the same test `descriptions_of` applies reading
        # them back. And SINCE THE FILE WAS LAST READ, because the revision alone says nothing
        # about whether the description is still about the file: an edit that replaces a clip's
        # audio or compresses it rewrites the bytes under the same asset id, and no edit forgets
        # the description (`Records.forget` is called only by the look-again at HEIF photos read
        # from one tile, `whole_picture.py`, never on an edit). What
        # refreshes the index after an edit is the re-probe's fan-out reaching here, so a guard
        # on the revision alone would freeze every edited file's description for ever, quietly
        # and for exactly as long as nobody searched for the new content.
        #
        # `probed_at` is the read, and it moves on every probe: a file read again is described
        # again, a file merely asked about twice is not.
        #
        # The count that comes back is the one on record, so a caller logging "described N" says
        # what the file has rather than nought.
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
            # No copy to read just now: a transient verdict, cleared by the next scan that sees
            # the file, and the sweep and the Build leave it out until then.
            await self._give_up(
                asset_id,
                code="no_copy",
                reason="No copy of this file could be read.",
                transient=True,
            )
            return 0
        moments = await self._reader.read(
            source.path,
            media_type=str(source.asset.media_type),
            duration_ms=int(source.asset.duration_ms or 0),
        )
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


#: Searching by what a picture looks like.
SERVICE: Part[SemanticService] = Part("semantic")
