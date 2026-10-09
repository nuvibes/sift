# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every part of the face service stands on: the switch, the settings a pass runs under,
the reference gallery, and settling a file after the names on its faces move."""

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
    """One `vocabulary.RECEIPT_FACES` entry: a face answered for, by its person, file and answer."""
    return {"person_id": person_id, "asset_id": asset_id, "how": how}


class FacesDisabled(Exception):
    """Something asked the face feature to work while it is switched off."""


class SettingsReader(Protocol):
    """Reading a global preference; declared here, since a feature may not import another."""

    async def get_app(self, key: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class Configured:
    """The settings one pass over one file runs under, read at its start, as stored."""

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
        """The quality a face has to reach, the same at every depth (`Bar.of`)."""
        return Bar.of(self.level)

    @property
    def density(self) -> float:
        """How many moments a file gets under this depth."""
        return self.sampling * (face_settings.DEEP_FACTOR if self.depth is Depth.DEEP else 1.0)

    @property
    def recognizer(self) -> str:
        """The model this family describes faces with, knowable without loading it."""
        return weights.pairing(self.family)[1].revision

    def pinned(self) -> dict[str, Any]:
        """What a run keeps, so a mid-run change does not alter the rest: what to scan and what a
        pass accepts. The machine limits stay live."""
        return {
            "family": self.family,
            "device": self.device,
            "depth": self.depth.value,
            "level": list(self.level),
            "sampling": self.sampling,
        }

    def with_pinned(self, kept: Mapping[str, Any]) -> Configured:
        """These settings with a run's own tuning put back over the top."""
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
        """A short tag for the tuning a result came from: every floor a face was judged against."""
        from sift.kernel.content.hashing import fingerprint

        return fingerprint(f"{self.shape}|{self.depth}|{self.density}")

    @property
    def shape(self) -> str:
        """The tuning without how much of a file is looked at, which has an order: more finds more.

        The sampler's version is in; the family's and the floor's slots hold fixed words, since a
        family change re-measures and a floor change has its own pass.
        """
        from sift.kernel.content.hashing import fingerprint

        return fingerprint(
            f"{_SLOT_THE_FAMILY_HELD}|{_SLOT_THE_FLOOR_HELD}|"
            f"{self.bar.min_sharpness}|{self.bar.min_frontality}|{tuning.MIN_CONTAINMENT}|"
            f"{FACE_SAMPLING_VERSION}|{tuning.QUALITY_VERSION}"
        )


#: The model family's slot in the fingerprint (`Configured.shape`): fixed.
_SLOT_THE_FAMILY_HELD = "accurate"

#: The size floor's slot, fixed: the floor pass reads what a lower floor changes.
_SLOT_THE_FLOOR_HELD = tuning.MIN_PIXELS


def _standing(tracks: Sequence[StoredTrack]) -> tuple[ScanStatus, int]:
    """Where a file stands and how many appearances have somebody; one place for both recounts."""
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
        # The reference gallery and the stamp it was built from (`_gallery_for`).
        self._gallery: tuple[tuple[int, str, int], int, str, matching.Gallery] | None = None
        self._runner: RunnerLike | None = None
        self._detector: detect.Detector | None = None
        self._recognizer: Recognizer | None = None
        self._loaded_family: str | None = None
        self._loaded_device: str | None = None
        self._loading = asyncio.Lock()

    async def enabled(self) -> bool:
        return bool(await self._preferences.get_app(face_settings.ENABLED_KEY))

    async def _require_enabled(self) -> None:
        if not await self.enabled():
            raise FacesDisabled(FACES_SWITCHED_OFF)

    async def frame_requests(self, facts: media.FileFacts) -> list[media.FrameRequest]:
        """What a pass over this video would read now, so a Build reads the file once."""
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
        """The settings this run began under, or today's if none were kept for it."""
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
        """Everybody's reference faces by `recognizer`, arranged for matching, kept until the
        references or the grouping setting move."""
        stamp = await self._store.reference_stamp()
        held = self._gallery
        if held is not None and held[0] == stamp and held[1] == groups and held[2] == recognizer:
            return held[3]
        stored = await self._store.reference_gallery(recognizer)
        # Off the loop: clustering every person's references is real arithmetic.
        gallery = await asyncio.to_thread(matching.build_gallery, stored, groups=groups)
        self._gallery = (stamp, groups, recognizer, gallery)
        return gallery

    async def refresh_status(self, asset_id: str) -> ScanStatus:
        """Work out where a file stands, counting appearances rather than faces."""
        status, identified = _standing(await self._store.tracks_of(asset_id))
        await self._store.set_status(asset_id, status, identified)
        return status

    async def _settle_all(self, asset_ids: Sequence[str]) -> None:
        """`_settle` for a batch of files, in two reads and two transactions whatever its size."""
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
            # After the write; a failing reindex is swallowed by the seam.
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
        """Everything that follows from one file's attributions changing: its status, its People,
        and the search index when a name moved."""
        status = await self.refresh_status(asset_id)
        added, removed = await self._store.reconcile_people(asset_id)
        if added or removed:
            # After the write; a failing reindex is swallowed by the seam.
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
        """The install's paths and limits, read-only."""
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
