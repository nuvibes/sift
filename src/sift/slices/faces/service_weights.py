# SPDX-License-Identifier: AGPL-3.0-or-later
"""The recognition models: whether the chosen pair is here and can run on this device, loading and
fetching it, and measuring the stored faces again when the model changes.
"""

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

#: What a job waiting for the recognition models says while it waits.
#:
#: The same sentence the readiness answer gives the Jobs screen for the IDENTIFY family, because
#: they are the same state and a person reading one row after the other must not be told two
#: things. It is declared here, in the feature that knows, so the composition root's copy can be
#: repointed at it rather than kept in step by hand.
MODELS_NOT_INSTALLED = "The recognition models haven't been downloaded yet. This will run as soon as they are downloaded."

#: What a PRESS answers when the chosen models are missing, and it cannot be the sentence above.
#:
#: That one is true of a task already in the queue, which waits and runs when the models arrive.
#: A press refused here queues nothing, so "this will run as soon as they are downloaded" would promise a
#: run nobody started. It names the models chosen (the Recognition models setting's own word for
#: them) and the two ways out the Faces screen offers, in that screen's words.
MODELS_MISSING_REFUSAL = (
    "Sift hasn't downloaded the {family} recognition models yet, so nothing was started. Choose"
    " Download the models under Faces, or copy the model files to this device."
)

#: How many files the FIRST page of measuring again takes, before anything has been timed.
#:
#: A page is not seconds: every file is settled and announced, and the machine is shared with
#: whatever else is running, so a page of 100 files can take over ten minutes (seconds an item)
#: with a scan beside it, and a bar that moves once every ten minutes cannot say where it is.
#:
#: A fixed number cannot be right, because the per-file cost moves by an order of magnitude with
#: what else the machine is doing. So this is only where the sizing STARTS: each page is timed and
#: the next one is sized from that clock by `next_remeasure_page`. It opens small on purpose:
#: the opening page is the one page nothing can size, so it is the one page that must not be
#: allowed to be long. Ten against the worst rate measured is about a minute; the price of opening
#: small on an idle machine is three extra turns through the queue to reach the ceiling.
REMEASURE_PAGE = 10

#: What one page of measuring again aims to cost.
#:
#: Half a minute, chosen against what the number is FOR rather than against the work: it is how
#: often the job's bar moves and how long a cancel takes to be noticed. Longer and the screen
#: stops looking alive; much shorter and the job pays a claim, a write and a re-queue for a few
#: files' worth of arithmetic.
REMEASURE_TARGET_SECONDS = 30.0

#: The smallest and largest page the sizing may choose.
#:
#: The floor exists because a page of nothing is a job that queues itself for ever, and the
#: ceiling because the target is an estimate from ONE page: a library whose next files are slower
#: than its last ones must not be able to turn a fast page into an hour-long one.
REMEASURE_PAGE_MIN = 5
REMEASURE_PAGE_MAX = 500

#: The most one page may grow over the one before it.
#:
#: Growth is bounded and SHRINKING IS NOT, and the asymmetry is the point. Being too small costs
#: one extra round trip through the queue; being too large costs a page nobody can watch and a
#: cancel nobody can press, which is what this prevents. So a page that came back slow is
#: allowed to fall as far as the clock says in one step, while a page that came back fast climbs
#: 20, 80, 320, 500: three extra round trips to reach the ceiling, against the risk of one noisy
#: measurement putting the whole library in a single page.
REMEASURE_GROWTH = 4


def next_remeasure_page(*, size: int, seconds: float) -> int:
    """How big the next page of measuring again should be, given what the last one cost.

    Sized from the previous page's own clock rather than from a rate the self-test measured,
    because the cost that matters is the cost HERE, NOW: the same page is seconds on an idle
    machine and minutes underneath a scan, and nothing about the machine predicts which of those
    is happening at the moment the pass runs.

    A page that took no measurable time reports no rate, so it is grown by the bound rather than
    divided by zero. That is the ordinary case on a fast machine with a small opening page.
    """
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
        """Why no pass can be made with the models this install is set to use, or None.

        Asked BEFORE a file is opened, for the reason `device_problem` is asked before a setting
        is saved: the answer is two names and two files, and finding it out by loading a model
        turns a state somebody can fix into a failure recorded against a file that was never the
        problem.

        A model family changed while a sweep is running can take minutes for its models to arrive,
        and every scan in that window would otherwise fail three times over about files that are
        perfectly fine. The screen hides its scan buttons in that state; this stands in front of the
        work already in the queue, and of a file arriving from a watched folder.

        The RUN's family when a run is named, never today's, because the run's is the one the scan
        is about to load. See `_as_the_run_started`.

        An unknown family is left to raise. It is not a wait: no download makes a name that is not
        in the catalog resolve, so parking a job on it would park it for ever.
        """
        configured = await self.configuration()
        if run is not None:
            configured = await self._as_the_run_started(configured, run)
        pair = weights.pairing(configured.family)
        if all(weights.installed(self._settings, weight) for weight in pair):
            return None
        return MODELS_NOT_INSTALLED

    async def device_problem(self) -> str | None:
        """Why recognition cannot run on the device it is set to, or None if it can.

        Asked by the settings screen rather than discovered by a scan, which is the whole point.
        Set to a card this machine does not have, every scan would fail, correctly and with a good
        message, into the job log, while the screen said "Ready", the next sweep queued nothing
        because nothing had been recorded, and the only visible sign was a library that never got
        any faces. The answer is knowable before a single file is opened, so it
        is answered where somebody is looking when they change the setting.
        """
        if not await self.enabled():
            return None
        # A card that died underneath a running session stays dead until the hold passes, whatever
        # the runtime would say about opening it again; the runner remembers why.
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
        """Why a scan pressed now could not run on this device, in a sentence, or None if it can.

        The one question every door that starts a scan asks before it queues anything: the Tasks
        screen's Run now for faces, a file's own press, and the library route. Asking
        `device_problem` alone would let Run now start a library pass whose every file waits on
        models nobody is fetching while the Faces screen says not ready.

        In the order `recognition_can_run` asks it for the Activity screen (switched off, then a
        device that is not there, then models that have not arrived), so the sentence names the
        first thing standing in the way. The switch is asked here too because nothing else stands
        between the Tasks screen's Run now and the queue: a pass the switch would refuse there
        finds no file lacking faces and answers with nothing queued and nothing said.
        """
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
        """Load the pair, once, on a thread and one load at a time: it waits on the model process.
        Changing either setting loads the new pair on the next use."""
        async with self._loading:
            return await asyncio.to_thread(self._load_models, configured)

    def _load_models(self, configured: Configured) -> tuple[detect.Detector, Recognizer]:
        """The pair for these settings, loaded now unless they already are.

        The device is resolved here, when the model is loaded, rather than being decided when the
        image was built, which is what makes adding a second kind of accelerator one more
        implementation rather than a second way of installing Sift.
        """
        if (
            self._detector is not None
            and self._recognizer is not None
            and self._loaded_family == configured.family
            and self._loaded_device == configured.device
        ):
            return self._detector, self._recognizer

        detector_weight, recognizer_weight = weights.pairing(configured.family)
        # In a process of its own, below normal priority, restarted when its device dies. The
        # in-process `Runner` has the same surface and is what a test stands the runtime in for.
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
        """How many reference faces another model described that cannot be described again,
        because they arrived as numbers alone. Out of the gallery for good; see the store."""
        configured = await self.configuration()
        return await self._store.count_unmeasurable_references(configured.recognizer)

    async def remeasure(self, *, limit: int = REMEASURE_PAGE) -> Remeasure:
        """Describe a page of the library's stored faces again, with the model now set.

        What changing the model family does, and the whole of what the setting's disclosure
        promises: every face found so far is measured again FROM THE PICTURE SIFT KEPT, so no
        file is opened. The aligned square a recognizer reads is stored beside every face for
        exactly this (the note on `face_detections` in the schema says so), and a reference
        face keeps its picture for the same reason. A reference that arrived as numbers alone
        cannot be measured again and is counted rather than pretended about.

        A page, because a library is tens of thousands of faces and the job that asks for this
        asks again while any remain. Nothing here opens a media file; a share that is away cannot
        stall it.
        """
        await self._require_enabled()
        configured = await self.configuration()
        _, recognizer = await self._models(configured)
        files = 0
        for asset_id in await self._store.measured_by_others(recognizer.revision, limit=limit):
            await self._remeasure_file(asset_id, recognizer)
            # The file's attributions moved (what arithmetic decided was undone), so its
            # status and the People on it are brought into line now, not left for a re-match
            # that only visits files it attributes something in.
            await self._settle(asset_id)
            files += 1

        references = 0
        listed = await self._store.references_measured_by_others(recognizer.revision, limit=limit)
        if listed:
            described = await self._describe_stored([stored for _, stored in listed], recognizer)
            for (reference_id, _), description in zip(listed, described, strict=True):
                if description is None:
                    # The picture IS the reference; without it there is nothing to describe and
                    # the old numbers would be noise against everything else.
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

        # Off the loop, and in one run of the model: the model reads a batch, and a file with sixty
        # faces as sixty runs gives the same answers several times slower on a card; see
        # `recognize.Recognizer.embed_many`.
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

        **Only what is missing.** A model already on disk was either downloaded and checked, or put
        there by hand by somebody who could not reach the internet, and fetching it again would
        undo the second case for no gain.

        `force` fetches both anyway, because "already here" only means the file EXISTS: a damaged
        model is installed, refuses to load, and says to fetch it again. The old file is replaced
        only once the new one's digest matches.

        The progress callback is handed the running byte count for the WHOLE set rather than for
        each file, because what somebody is watching is one download of two things. Returning False
        from it stops the transfer (there is nothing to interrupt, the reader simply stops asking)
        and a stopped transfer leaves its partial file, so the next attempt resumes.

        `session_factory` is how this is tested without touching the network, and that is the whole
        reason it is a parameter rather than something reached for inside. A test that had to reach
        github to prove the resume logic would be a test that fails on a train.
        """
        await self._require_enabled()
        configured = await self.configuration()
        wanted = [
            weight
            for weight in weights.pairing(configured.family)
            if force or not weights.installed(self._settings, weight)
        ]
        # Reported as one download: the sizes are known before anything is asked for, so the bar
        # does not jump back to the start when the second file begins.
        # Against what comes DOWN, not the size of what is kept: two of these travel inside
        # archives twice their size, and a bar drawn against the members would sit at 100%
        # for the last part of the download.
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
            # A partial file left beside it means the transfer stopped: on a forced fetch the old
            # model is still in place, so being installed does not say this one arrived.
            partial = weights.path_of(self._settings, weight).with_suffix(".part")
            if not weights.installed(self._settings, weight) or await asyncio.to_thread(
                partial.exists
            ):
                # The transfer was stopped. Its partial file stays, so this is a pause rather than
                # a failure, and saying so is the difference between "try again" and "something
                # broke".
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
