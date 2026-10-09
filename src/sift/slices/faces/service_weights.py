# SPDX-License-Identifier: AGPL-3.0-or-later
"""The recognition models: whether the chosen pair is here and can run, loading and fetching it,
and measuring the stored faces again when the model changes."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.log import get_logger
from sift.kernel.ml.child import devices_here
from sift.kernel.settings_registry import get_registered
from sift.slices.faces import crop as cropping
from sift.slices.faces import detect, recognize, weights
from sift.slices.faces import settings as face_settings
from sift.slices.faces.models import Description
from sift.slices.faces.recognize import Recognizer
from sift.slices.faces.runner import FEATURE, ChildRunner, DeviceUnavailable, resolve_provider
from sift.slices.faces.service_base import Configured, FacesDisabled, FaceServiceBase
from sift.slices.faces.store import Remeasured

log = get_logger(__name__)

#: What a job waiting for the models says; the readiness answer's sentence, declared here.
MODELS_NOT_INSTALLED = "The recognition models haven't been downloaded yet. This will run as soon as they are downloaded."

#: What a refused press says when the models are missing: it queued nothing, so it promises no run.
MODELS_MISSING_REFUSAL = (
    "Sift hasn't downloaded the {family} recognition models yet, so nothing was started. Choose"
    " Download the models under Faces, or copy the model files to this device."
)

#: The first page of measuring again, small because nothing has been timed yet.
REMEASURE_PAGE = 10

#: What one page aims to cost: how often the bar moves and how soon a cancel is noticed.
REMEASURE_TARGET_SECONDS = 30.0

#: The smallest and largest page the sizing may choose.
REMEASURE_PAGE_MIN = 5
REMEASURE_PAGE_MAX = 500

#: The most one page may grow over the last; shrinking is unbounded: a long page cannot be watched.
REMEASURE_GROWTH = 4


def next_remeasure_page(*, size: int, seconds: float) -> int:
    """How big the next page of measuring again should be, from the last page's own clock."""
    if size <= 0:  # pragma: no cover (the caller's floor is REMEASURE_PAGE_MIN)
        size = REMEASURE_PAGE_MIN
    ceiling = min(REMEASURE_PAGE_MAX, size * REMEASURE_GROWTH)
    if seconds <= 0:
        return ceiling
    aimed = int(size * REMEASURE_TARGET_SECONDS / seconds)
    return max(REMEASURE_PAGE_MIN, min(aimed, ceiling))


@dataclass(frozen=True, slots=True)
class Remeasure:
    """What one page of measuring the library again did, and whether any is left."""

    files: int
    references: int
    remaining: int


class WeightsMixin(FaceServiceBase):
    """Which models are loaded, whether they can run, and measuring again after a change of model."""

    async def ready(self) -> bool:
        """Whether both models this install is set to use are actually present."""
        if not await self.enabled():
            return False
        configured = await self.configuration()
        return all(
            weights.installed(self._settings, weight)
            for weight in weights.pairing(configured.family)
        )

    async def weights_problem(self, *, run: str | None = None) -> str | None:
        """Why no pass can be made with the models set (the run's, when one is named), or None.

        Asked before a file is opened, so a missing model parks work rather than failing files.
        """
        configured = await self.configuration()
        if run is not None:
            configured = await self._as_the_run_started(configured, run)
        pair = weights.pairing(configured.family)
        if all(weights.installed(self._settings, weight) for weight in pair):
            return None
        return MODELS_NOT_INSTALLED

    async def device_problem(self) -> str | None:
        """Why recognition cannot run on the device it is set to, or None if it can."""
        if not await self.enabled():
            return None
        # A card that died underneath a session stays dead until the hold passes.
        if self._runner is not None and self._runner.broken is not None:
            return self._runner.broken
        configured = await self.configuration()
        try:
            available = await asyncio.to_thread(devices_here, self._settings, FEATURE)
            resolve_provider(configured.device, self._hardware, available)
        except DeviceUnavailable as refused:
            return str(refused)
        return None

    async def cannot_scan(self) -> str | None:
        """Why a scan pressed now could not run, in a sentence, or None: the switch, the device,
        then the models, in `recognition_can_run`'s order."""
        try:
            await self._require_enabled()
        except FacesDisabled as off:
            return str(off)
        problem = await self.device_problem()
        if problem is not None:
            return problem
        if await self.weights_problem() is None:
            return None
        family = (await self.configuration()).family
        declared = get_registered(face_settings.MODEL_KEY)
        labels = (
            dict(zip(declared.choices, declared.choice_labels, strict=True))
            if declared is not None
            and declared.choices is not None
            and declared.choice_labels is not None
            else {}
        )
        return MODELS_MISSING_REFUSAL.format(family=labels.get(family, family))

    async def _models(self, configured: Configured) -> tuple[detect.Detector, Recognizer]:
        """Load the pair once, on a thread and one load at a time; a setting change reloads it."""
        async with self._loading:
            return await asyncio.to_thread(self._load_models, configured)

    def _load_models(self, configured: Configured) -> tuple[detect.Detector, Recognizer]:
        """The pair for these settings, loaded now unless they already are, on the device chosen."""
        if (
            self._detector is not None
            and self._recognizer is not None
            and self._loaded_family == configured.family
            and self._loaded_device == configured.device
        ):
            return self._detector, self._recognizer

        detector_weight, recognizer_weight = weights.pairing(configured.family)
        # In a process of its own, below normal priority, restarted when its device dies.
        runner = ChildRunner(self._settings, self._hardware, device=configured.device)
        detector = detect.build(runner, runner.load(detector_weight))
        recognizer = Recognizer(runner, runner.load(recognizer_weight))

        self._runner = runner
        self._detector = detector
        self._recognizer = recognizer
        self._loaded_family = configured.family
        self._loaded_device = configured.device
        return detector, recognizer

    def release(self) -> None:
        """Drop the loaded models, so switching the feature off gives the memory back."""
        if self._runner is not None:
            self._runner.unload()
        self._runner = None
        self._detector = None
        self._recognizer = None
        self._loaded_family = None
        self._loaded_device = None

    async def measured_by_another_model(self) -> int:
        """How many files' faces are still described by a model other than the one set."""
        configured = await self.configuration()
        return await self._store.count_measured_by_others(configured.recognizer)

    async def references_without_pictures(self) -> int:
        """How many references another model described as numbers alone, out of the gallery."""
        configured = await self.configuration()
        return await self._store.count_unmeasurable_references(configured.recognizer)

    async def remeasure(self, *, limit: int = REMEASURE_PAGE) -> Remeasure:
        """Describe a page of the stored faces again with the model now set, from the kept
        pictures, so no file is opened; a reference with no picture is counted."""
        await self._require_enabled()
        configured = await self.configuration()
        _, recognizer = await self._models(configured)
        files = 0
        for asset_id in await self._store.measured_by_others(recognizer.revision, limit=limit):
            await self._remeasure_file(asset_id, recognizer)
            # Its attributions moved, so the file is settled now.
            await self._settle(asset_id)
            files += 1

        references = 0
        listed = await self._store.references_measured_by_others(recognizer.revision, limit=limit)
        if listed:
            described = await self._describe_stored([stored for _, stored in listed], recognizer)
            for (reference_id, _), description in zip(listed, described, strict=True):
                if description is None:
                    # The picture is the reference; without it the old numbers are noise.
                    await self._store.forget_reference(reference_id)
                    log.warning("faces.remeasure.reference_picture_missing", reference=reference_id)
                    continue
                await self._store.remeasure_reference(
                    reference_id,
                    embedding=recognize.pack(description.vector),
                    recognizer=recognizer.revision,
                )
                references += 1

        remaining = await self._store.count_measured_by_others(recognizer.revision) + len(
            await self._store.references_measured_by_others(recognizer.revision, limit=1)
        )
        log.info(
            "faces.remeasure.page",
            files=files,
            references=references,
            remaining=remaining,
            recognizer=recognizer.revision,
        )
        return Remeasure(files=files, references=references, remaining=remaining)

    async def _remeasure_file(self, asset_id: str, recognizer: Recognizer) -> None:
        """Describe one file's stored faces again with this model, carrying every decision."""
        stored = await self._store.detections_of_file(asset_id)
        described = await self._describe_stored([path for _, path, _ in stored], recognizer)
        measured: list[Remeasured] = []
        gone: list[str] = []
        for (face_id, _, previous), description in zip(stored, described, strict=True):
            if description is None:
                gone.append(face_id)
                continue
            measured.append(
                Remeasured(
                    id=face_id,
                    previous=previous,
                    embedding=recognize.pack(description.vector),
                    strength=description.strength,
                )
            )
        removed = await self._store.remeasure_file(
            asset_id, measured, gone=gone, recognizer=recognizer.revision
        )
        if gone:
            log.warning(
                "faces.remeasure.pictures_missing",
                asset_id=asset_id,
                faces=len(gone),
                appearances_removed=removed,
            )

    async def _describe_stored(
        self, stored: Sequence[str], recognizer: Recognizer
    ) -> list[Description | None]:
        """Each stored picture as this model describes it, or None where the picture has gone."""
        pictures = await self._store.read_pictures(stored)
        present = [picture for picture in pictures if picture is not None]
        chips = await cropping.decode(present, self._settings)

        def describe() -> list[Description]:
            return recognizer.embed_many(chips)

        # Off the loop, in one batched run of the model.
        descriptions = iter(await asyncio.to_thread(describe))
        return [None if picture is None else next(descriptions) for picture in pictures]

    async def install_models(
        self,
        *,
        progress: weights.Progress | None = None,
        session_factory: weights.SessionFactory | None = None,
        force: bool = False,
    ) -> list[str]:
        """Fetch whichever of the configured pair is not here yet. Reports what it installed.

        `force` fetches both; progress is one byte count for the set, False stops it and leaves
        the partial file to resume. `session_factory` keeps tests off the network.
        """
        await self._require_enabled()
        configured = await self.configuration()
        wanted = [
            weight
            for weight in weights.pairing(configured.family)
            if force or not weights.installed(self._settings, weight)
        ]
        # One download against what comes down, so the bar neither jumps back nor sits at 100%.
        expected = weights.download_bytes(wanted)
        done = 0
        installed: list[str] = []
        for weight in wanted:
            carried = done

            def relay(written: int, _total: int, *, carried: int = carried) -> bool:
                return progress is None or progress(carried + written, expected)

            await weights.fetch(
                self._settings,
                weight,
                session_factory=session_factory,
                progress=relay,
                fresh=force,
            )
            # A partial file beside it means the transfer stopped.
            partial = weights.path_of(self._settings, weight).with_suffix(".part")
            if not weights.installed(self._settings, weight) or await asyncio.to_thread(
                partial.exists
            ):
                # Stopped, not failed: the partial file stays for the next attempt.
                log.info("faces.weights.stopped", weight=weight.id, installed=len(installed))
                return installed
            done += weight.archive_bytes or weight.size_bytes
            await self._store.record_weight(
                weight.id,
                weight.revision,
                weights.digest_of(weights.path_of(self._settings, weight)),
                weight.size_bytes,
            )
            installed.append(weight.id)
        log.info("faces.weights.installed", count=len(installed), family=configured.family)
        return installed
