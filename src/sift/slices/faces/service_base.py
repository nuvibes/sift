# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every part of the face service stands on: the switch, the settings a pass runs under, the
reference gallery, and settling a file after the names on its faces move.

`FaceService` is put together from mixins, one module per responsibility, and each mixin names the
parts it relies on as its bases. This is the one they all share.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Protocol

from sift.kernel import media
from sift.kernel.access import Repository
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, VerdictProduct
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger
from sift.kernel.memo import PacedAnswer
from sift.kernel.sampling import FACE_SAMPLING_VERSION
from sift.kernel.seams import ReindexSeam
from sift.kernel.workbench import Recorder
from sift.slices.faces import detect, frames, matching, tuning, weights
from sift.slices.faces import settings as face_settings
from sift.slices.faces.models import Depth, ScanStatus
from sift.slices.faces.pipeline import Bar
from sift.slices.faces.recognize import Recognizer
from sift.slices.faces.runner import RunnerLike
from sift.slices.faces.store import Store, StoredTrack

log = get_logger(__name__)


def _face_answered(person_id: str, asset_id: str, how: str) -> dict[str, str]:
    """One `vocabulary.RECEIPT_FACES` entry: a face answered for, by its person, file and answer.

    Written into every receipt of a Yes or a No on faces, so the History threads can draw the press
    and the answer's own line as one (`history.one_line_per_face_answer`).
    """
    return {"person_id": person_id, "asset_id": asset_id, "how": how}


class FacesDisabled(Exception):
    """Something asked the face feature to work while it is switched off."""


class SettingsReader(Protocol):
    """Reading a global preference.

    Declared here rather than imported from the feature that stores preferences, because a feature
    may not import another feature. An implementation is handed in when the app is assembled.
    """

    async def get_app(self, key: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class Configured:
    """The settings one pass over one file runs under, read at the start of it.

    Read once per file rather than once per face. **Not once per run**, and the difference is
    visible from outside: a setting changed while a sweep is going takes effect on the very next
    file, so a run started at one setting can finish at another and the library ends up part at
    each. Nothing is corrupted by that (what every result is recorded under is what actually
    produced it), but the run does not mean one thing, and the list of files the sweep decided to
    queue was decided under the settings at the moment it started.

    What is stored is what the settings say; how hard to look is applied on the way out, in
    `density`. That is the difference between a value and a value something has already been done
    to: keeping the derived form would mean a request for a deeper look at one file had to undo
    whatever the setting had already applied, which cannot be done from the result alone.
    """

    family: str
    device: str
    depth: Depth
    level: tuple[int, float, float]
    """The quality preset, before how hard to look is taken into account."""
    sampling: float
    """The sampling preset, likewise."""
    suggest_above: float
    attach_above: float
    groups: int
    budget_seconds: float | None

    @property
    def bar(self) -> Bar:
        """The quality a face has to reach. The same at every depth. See `Bar.of`."""
        return Bar.of(self.level)

    @property
    def density(self) -> float:
        """How many moments a file gets under this depth. A deeper look samples several times as
        closely, and that is now the whole of what it accepts differently."""
        return self.sampling * (face_settings.DEEP_FACTOR if self.depth is Depth.DEEP else 1.0)

    @property
    def recognizer(self) -> str:
        """Which model this family describes faces with: the stamp every description carries.

        Knowable without loading anything, which matters: the reads that filter by it run on
        every screen and every re-match, on installs whose models may not be to hand.
        """
        return weights.pairing(self.family)[1].revision

    def pinned(self) -> dict[str, Any]:
        """What a run keeps, so a setting changed halfway does not change the rest of it.

        Only what decides WHICH files are worth scanning and WHAT a pass would accept from them.
        The limits on how hard the machine may be worked (the share of it, the overnight window,
        the ceiling on one file) are deliberately absent and stay live: "stop taking my whole
        processor" is not a request about some future run, and the pool re-reads those on its own
        timer for that reason.

        The device is in here, and it is the one somebody notices: changed mid-run it would take
        effect on the very next file, so a sweep switched to a card this machine does not have
        would fail every file it had left.
        """
        return {
            "family": self.family,
            "device": self.device,
            "depth": self.depth.value,
            "level": list(self.level),
            "sampling": self.sampling,
        }

    def with_pinned(self, kept: Mapping[str, Any]) -> Configured:
        """These settings, with a run's own tuning put back over the top.

        Everything absent from the snapshot is left as it was just read, which is what keeps the
        machine limits live while the tuning is frozen.
        """
        level = kept.get("level")
        return replace(
            self,
            family=str(kept.get("family", self.family)),
            device=str(kept.get("device", self.device)),
            depth=Depth(str(kept.get("depth", self.depth.value))),
            level=(int(level[0]), float(level[1]), float(level[2])) if level else self.level,
            sampling=float(kept.get("sampling", self.sampling)),
        )

    @property
    def digest(self) -> str:
        """A short tag for the tuning that produced a result, stored beside it.

        Two files scanned under different quality bars are not comparable, and without this there
        would be no way to tell which of them is stale after a change.

        **Every floor a face was judged against belongs in here, including the ones that are not
        settings.** The containment floor is a constant rather than a preset, and leaving it out
        would mean a change to it made no file stale, so a sweep would find the whole library
        already settled and the squares a raised floor rejects would stay exactly where they were,
        indefinitely. A fix nothing re-scans is not a fix.
        """
        from sift.kernel.content.hashing import fingerprint

        return fingerprint(f"{self.shape}|{self.depth}|{self.density}")

    @property
    def shape(self) -> str:
        """The tuning WITHOUT how much of a file gets looked at.

        What separates "this file was scanned under different settings" from "this file was already
        scanned under settings that would find at least as much". Everything in here changes what a
        pass would ACCEPT, and two results that differ on any of it are incomparable in a way no
        ordering can rescue. What is left out is `density`, which is the one axis with an order to
        it: more moments can only find more.

        **The density is left out whole**, sampling and depth together, because they are one
        control whose steps are 0.5, 1 and 3. With either half in, two efforts would have different
        shapes and the ordering could not be applied between them: going from deep to fast would
        re-read the whole library at a shallower setting, which is the work that ordering exists to
        prevent.

        The sampler's version is in here rather than beside the density, and deliberately so. The
        density is the multiplier a pass was ASKED for; how many moments that turns into is the
        sampler's business, and a change to the sampler changes what a pass finds without changing
        anything either fingerprint would otherwise notice. Left out, a better sampler would ship
        and nothing would ever be looked at with it.

        **The model family is not in here.** With it in, changing the family would make every
        scan stale and the next sweep read the whole library again: the opposite of what the
        setting's own disclosure promises, which is that the pictures Sift kept are measured again
        and no file is opened. Which model described a file is on its scan row instead, and a file
        described by another model is measured again from its stored squares by
        `FaceService.remeasure`. What that deliberately gives up: faces the other family's
        detector did not find are not found by a family change. They are found by looking again.

        **The slot the family occupied holds a fixed word**, so the fingerprint of every scan
        already recorded stays the same; otherwise every scanned file would read as scanned under
        different settings and be offered again. It decides nothing.
        """
        from sift.kernel.content.hashing import fingerprint

        return fingerprint(
            f"{_SLOT_THE_FAMILY_HELD}|{_SLOT_THE_FLOOR_HELD}|"
            f"{self.bar.min_sharpness}|{self.bar.min_frontality}|{tuning.MIN_CONTAINMENT}|"
            f"{FACE_SAMPLING_VERSION}|{tuning.QUALITY_VERSION}"
        )


#: The model family's slot in the tuning fingerprint (`Configured.shape`): fixed, so every scan
#: recorded stays comparable.
_SLOT_THE_FAMILY_HELD = "accurate"

#: The size floor's slot, for the family's reason. A floor that moves is no reason to look at the
#: whole library again: a
#: scan writes down the biggest face it refused for size (`face_scans.refused_largest`), so the
#: files a lower floor can change are known exactly, and the floor pass looks at those again and
#: nothing else (`FaceService.under_an_earlier_floor`). The presets still differ in the slots after
#: this one, so moving between them still offers the library again.
_SLOT_THE_FLOOR_HELD = tuning.MIN_PIXELS


def _standing(tracks: Sequence[StoredTrack]) -> tuple[ScanStatus, int]:
    """Where a file stands and how many of its appearances have somebody, from its faces.

    One place, because two callers need it and they must agree: the file-at-a-time recount and the
    batched one. Written out twice they are two answers to "is this file identified", and the second
    one would be the one nobody looked at again.

    Counted by APPEARANCE rather than by face, which is the rule `status_of` states and the reason
    it is a function at all. See it for what counting the other way does.
    """
    identified = sum(1 for track in tracks if track.person_id is not None)
    return status_of(len(tracks), identified), identified


def status_of(tracks: int, identified: int) -> ScanStatus:
    """The five states, counted by appearance."""
    if tracks == 0:
        return ScanStatus.NO_FACES
    if identified == 0:
        return ScanStatus.NONE_IDENTIFIED
    if identified >= tracks:
        return ScanStatus.ALL_IDENTIFIED
    return ScanStatus.SOME_IDENTIFIED


#: The one sentence every refusal under the switch says, wherever it is said.
FACES_SWITCHED_OFF = "Recognizing faces is switched off. Turn it on under Import tasks to use this."


class FaceServiceBase:
    """The state the face service holds, and the steps every part of it shares."""

    def __init__(
        self,
        *,
        store: Store,
        content: ContentStore,
        repository: Repository,
        preferences: SettingsReader,
        settings: Settings,
        hardware: HardwareReport,
        reindexer: ReindexSeam,
        recorder: Recorder | None = None,
    ) -> None:
        self._store = store
        self._content = content
        self._unread = PacedAnswer(partial(content.coming_count, VerdictProduct.FACES.value))
        self._repository = repository
        self._preferences = preferences
        self._settings = settings
        self._hardware = hardware
        self._reindexer = reindexer
        self._recorder = recorder
        # The reference gallery, and the stamp it was built from. Rebuilt only when the references
        # or the grouping setting have actually moved. See `_gallery`.
        self._gallery: tuple[tuple[int, str, int], int, str, matching.Gallery] | None = None
        self._runner: RunnerLike | None = None
        self._detector: detect.Detector | None = None
        self._recognizer: Recognizer | None = None
        self._loaded_family: str | None = None
        self._loaded_device: str | None = None
        # One load of the models at a time. See `_models`.
        self._loading = asyncio.Lock()

    async def enabled(self) -> bool:
        return bool(await self._preferences.get_app(face_settings.ENABLED_KEY))

    async def _require_enabled(self) -> None:
        if not await self.enabled():
            raise FacesDisabled(FACES_SWITCHED_OFF)

    async def frame_requests(self, facts: media.FileFacts) -> list[media.FrameRequest]:
        """What a pass over this video would read, at the density the settings say now, so a
        Build can read the file once for everything. See `frames.frame_requests`."""
        configured = await self.configuration()
        return frames.frame_requests(facts, density=configured.density)

    async def configuration(self) -> Configured:
        """Everything a pass needs to know, read together."""
        get = self._preferences.get_app
        chosen, sampling = face_settings.EFFORT_LEVELS[str(await get(face_settings.EFFORT_KEY))]
        depth = Depth(chosen)
        level = face_settings.QUALITY_BARS[str(await get(face_settings.QUALITY_KEY))]
        budget = int(await get(face_settings.FILE_BUDGET_KEY))
        return Configured(
            family=str(await get(face_settings.MODEL_KEY)),
            device=str(await get(face_settings.DEVICE_KEY)),
            depth=depth,
            level=level,
            sampling=sampling,
            # Read from the module each time rather than bound at import, so one place holds them.
            suggest_above=tuning.SUGGEST_CONFIDENCE,
            attach_above=tuning.AUTO_APPLY_CONFIDENCE,
            groups=tuning.MATCH_GROUPS,
            budget_seconds=float(budget) if budget > 0 else None,
        )

    async def start_run(self, run_id: str) -> None:
        """Keep the tuning this sweep is running under, before it queues anything."""
        configured = await self.configuration()
        await self._store.remember_run(run_id, json.dumps(configured.pinned()))

    async def _as_the_run_started(self, configured: Configured, run: str) -> Configured:
        """The settings this run began under, or today's if there is nothing kept for it.

        A missing snapshot is not an error. It means the run is older than a week or was never
        kept, and the live settings are then the answer.
        """
        kept = await self._store.run_tuning(run)
        if kept is None:
            return configured
        try:
            return configured.with_pinned(json.loads(kept))
        except (ValueError, TypeError, KeyError, IndexError):
            log.warning("faces.run.tuning_unreadable", run=run)
            return configured

    async def last_run_at(self) -> int | None:
        """When the library was last swept, in milliseconds, or None if it never has been."""
        return await self._store.last_run_at()

    async def last_run_canceled(self) -> bool:
        """Whether the last library pass was stopped or canceled rather than finished."""
        return await self._store.last_run_canceled()

    async def _gallery_for(self, groups: int, recognizer: str) -> matching.Gallery:
        """Everybody's reference faces, arranged for matching. Built once and kept until they move.

        Only the references described by `recognizer`: the model set now. A gallery is compared
        against faces the same model described, and one that blended two models' numbers matched
        nothing in particular and said nothing about it.

        Not rebuilt once per scanned file: the table is small, but the cost is the clustering
        arithmetic, done again on every read over rows that have not changed.

        Held against a stamp taken from the table rather than against a flag somebody has to
        remember to clear. And keyed by the grouping setting as well: an admin changing how many
        groups a person's faces are reduced to would otherwise keep matching against a gallery
        built to the old number, with nothing on screen to say the setting had not taken.
        """
        stamp = await self._store.reference_stamp()
        held = self._gallery
        if held is not None and held[0] == stamp and held[1] == groups and held[2] == recognizer:
            return held[3]
        stored = await self._store.reference_gallery(recognizer)
        # Off the loop: clustering every person's reference faces is real arithmetic, and with six
        # hundred people it is that arithmetic once each.
        gallery = await asyncio.to_thread(matching.build_gallery, stored, groups=groups)
        self._gallery = (stamp, groups, recognizer, gallery)
        return gallery

    async def refresh_status(self, asset_id: str) -> ScanStatus:
        """Work out where a file stands, counting appearances rather than faces.

        Counted the other way (faces seen against distinct people matched), a thirty-frame clip of
        one person would read as one of thirty and stay "partly identified" for ever, whatever
        anybody did about it.
        """
        status, identified = _standing(await self._store.tracks_of(asset_id))
        await self._store.set_status(asset_id, status, identified)
        return status

    async def _settle_all(self, asset_ids: Sequence[str]) -> None:
        """Everything that follows from a BATCH of files having had their attributions changed.

        The same three things `_settle` does, for a set of files, in the smallest number of turns at
        the database rather than one set of turns per file.

        Not `_settle` in a loop: naming a group of faces touches every file those faces are in, so
        a loop over a group across twenty-four files would take twenty-four reads of that file's
        faces, twenty-four turns at the single writer to store its status, twenty-four more to bring
        its People into line, and ring the change bus twenty-four times, which is twenty-four
        re-fetches in every open browser for one press.

        Two reads and two transactions, whatever the size of the batch. The search index is
        still told per file, because it is told about a FILE and there is nothing to batch.
        """
        wanted = list(dict.fromkeys(asset_ids))
        if not wanted:
            return
        held = await self._store.tracks_in_files(wanted)
        await self._store.set_statuses(
            {asset_id: _standing(tracks) for asset_id, tracks in held.items()}
        )
        moved = await self._store.reconcile_people_of(wanted)
        for asset_id, (added, removed) in moved.items():
            if not added and not removed:
                continue
            # After the write and swallowed by the seam if it fails, for the reason `_settle`
            # gives: the person IS on the file by now, and a briefly stale search box is a smaller
            # failure than an error on a decision that already took effect.
            await self._reindexer.touched(asset_id)
            log.info(
                "faces.people_on_file",
                asset_id=asset_id,
                added=len(added),
                removed=len(removed),
            )

    async def _give_up(self, asset_id: str, *, code: str, reason: str, transient: bool) -> None:
        """Write down that this file could not be looked at, and why. See `file_verdicts`."""
        await self._content.record_verdict(
            asset_id, VerdictProduct.FACES, code=code, reason=reason, transient=transient
        )
        log.info("faces.scan.gave_up", asset_id=asset_id, code=code, transient=transient)

    async def _settle(self, asset_id: str) -> ScanStatus:
        """Everything that follows from one file's attributions having changed.

        Three things, and they are here together rather than at each call site because leaving one
        out is invisible: the status is recounted, the People on the file are brought into line
        with the faces in it, and the search index is told if that changed anything.

        The index is told only when a name actually moved. A re-match sweeps every unattributed
        appearance in the library and settles each file it touched; reindexing the ones whose list
        of People came out identical would be a write per file for no change.
        """
        status = await self.refresh_status(asset_id)
        added, removed = await self._store.reconcile_people(asset_id)
        if added or removed:
            # After the write, and swallowed by the seam if it fails: the person IS on the file by
            # now, and a briefly stale search box is a smaller failure than an error on a decision
            # that already took effect.
            await self._reindexer.touched(asset_id)
            log.info(
                "faces.people_on_file",
                asset_id=asset_id,
                added=len(added),
                removed=len(removed),
            )
        return status

    @property
    def settings(self) -> Settings:
        """The install's paths and limits, for a caller that has to ask where a model file would
        be. Read-only: nothing outside this feature configures it."""
        return self._settings

    async def _faces_answered(
        self, track_ids: Sequence[str], person_id: str, how: str
    ) -> list[dict[str, str]]:
        """The `RECEIPT_FACES` entries for these faces, each by the file it is in. One read."""
        found = await self._store.tracks(track_ids)
        return [
            _face_answered(person_id, found[track_id].asset_id, how)
            for track_id in dict.fromkeys(track_ids)
            if track_id in found
        ]
