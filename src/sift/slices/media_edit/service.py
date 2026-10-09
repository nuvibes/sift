# SPDX-License-Identifier: AGPL-3.0-or-later
"""Answering what a compression would do, starting one, and recording what came out of it.
Answers come from the index; the work is a job per file."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sift.kernel.access import Repository, Viewer
from sift.kernel.content import Asset
from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue
from sift.kernel.log import get_logger
from sift.kernel.reach import OUT_OF_REACH, VAULT_LOCKED, conceals
from sift.kernel.seams import LibraryWriteSeam
from sift.kernel.wiring import Part
from sift.slices.media_edit import plan, provenance, tuning
from sift.slices.media_edit.models import (
    CompressRequest,
    CompressStarted,
    FileVerdict,
    MadeCopy,
    Preflight,
    Produced,
    SampleStarted,
)
from sift.slices.media_edit.refusals import NotAllowed, NotFound, Refused
from sift.slices.media_edit.settings import (
    BYTES_PER_MEGABYTE,
    KEY_FOR_PRESET,
    Preset,
    resolve_target_mb,
)

log = get_logger(__name__)

COMPRESS = "compress"
COMPRESS_SAMPLE = "compress_sample"

#: A photograph is absent: everything here is about running time, frame rate and sound.
_IMAGE = "image"
COMPRESSIBLE = frozenset({"video", "gif"})

#: An ordinary tag, found by name, so renaming it means the next copy creates a new one.
PRODUCED_TAG = "Compressed"

OPERATION = "compress"


@dataclass(frozen=True, slots=True)
class Target:
    """A resolved request: how many bytes, whether it must play anywhere, and what to call it."""

    bytes: int | None
    compatibility: bool
    preset: Preset | None

    @property
    def label(self) -> str:
        """The size in the filename, so two targets of one file do not collide."""
        if self.bytes is None:
            return "compatible"
        megabytes = self.bytes / BYTES_PER_MEGABYTE
        whole = int(megabytes)
        return f"{whole}MB" if megabytes == whole else f"{megabytes:.1f}MB"


class CompressService:
    """The feature's one object. Held at `app.state.compressor`."""

    def __init__(
        self,
        database: Database,
        access: Repository,
        queue: JobQueue,
        writer: LibraryWriteSeam,
        read_app_setting: Callable[[str], Awaitable[Any]],
        *,
        clock: Callable[[], float],
    ) -> None:
        self._db = database
        self._access = access
        self._queue = queue
        self._writer = writer
        self._read_app_setting = read_app_setting
        self._clock = clock

    def now(self) -> int:
        return int(self._clock())

    async def resolve_target(self, request: CompressRequest) -> Target:
        """Turn a preset name into the number it currently stands for, read at the moment of asking."""
        if request.preset is None:
            return Target(bytes=None, compatibility=request.compatibility, preset=None)
        if request.preset is Preset.CUSTOM:
            wanted = request.custom_target_mb or 0
            return Target(
                bytes=wanted * BYTES_PER_MEGABYTE,
                compatibility=request.compatibility,
                preset=Preset.CUSTOM,
            )
        stored = await self._read_app_setting(KEY_FOR_PRESET[request.preset])
        megabytes = resolve_target_mb(stored, request.preset)
        return Target(
            bytes=megabytes * BYTES_PER_MEGABYTE,
            compatibility=request.compatibility,
            preset=request.preset,
        )

    async def preflight(self, request: CompressRequest, *, viewer: Viewer) -> Preflight:
        """Every file's answer, before anything is encoded. Writability is advisory, asked once per
        root."""
        target = await self.resolve_target(request)
        verdicts: list[FileVerdict] = []
        writable_by_root: dict[str, str | None] = {}

        for asset_id in request.asset_ids:
            verdicts.append(
                await self._verdict(asset_id, target, viewer=viewer, cache=writable_by_root)
            )

        eligible = [each for each in verdicts if each.skip_reason is None]
        unreachable = [each for each in eligible if not each.reachable]
        return Preflight(
            target_bytes=target.bytes,
            files=verdicts,
            eligible_count=len(eligible),
            unreachable_count=len(unreachable),
            copy_only_count=sum(1 for each in eligible if each.copy_only),
            audio_conversion_count=sum(1 for each in eligible if each.converts_audio),
            suggested_target_bytes=_suggested(unreachable),
        )

    async def _verdict(
        self,
        asset_id: str,
        target: Target,
        *,
        viewer: Viewer,
        cache: dict[str, str | None],
    ) -> FileVerdict:
        asset = await self._access.open_asset(viewer, asset_id)
        if asset is None:
            # One answer for missing and hidden, except this user's own vault (`kernel.reach`).
            return FileVerdict(
                asset_id=asset_id,
                reachable=False,
                skip_reason=(
                    VAULT_LOCKED if await conceals(self._access, viewer, asset_id) else OUT_OF_REACH
                ),
            )
        if asset.media_type not in COMPRESSIBLE:
            return FileVerdict(
                asset_id=asset_id,
                reachable=False,
                skip_reason=(
                    "A photograph is resized rather than compressed \u2014 open it and use the editor."
                    if asset.media_type == _IMAGE
                    else "Sift cannot compress this kind of file."
                ),
            )

        locations = await self._access.locations(viewer, asset_id)
        if not locations:  # pragma: no cover - an openable asset always has a visible location
            # The access layer guarantees a location; a sentence rather than an index error if not.
            return FileVerdict(
                asset_id=asset_id,
                reachable=False,
                skip_reason="Sift cannot find that file where it expects it to be.",
            )
        filename = locations[0].filename
        root_id = locations[0].root_id
        if root_id not in cache:
            cache[root_id] = await self._writer.writable_beside(asset_id, actor=viewer)
        refusal = cache[root_id]
        if refusal is not None:
            return FileVerdict(
                asset_id=asset_id, filename=filename, reachable=False, skip_reason=refusal
            )

        facts = source_facts(asset)
        verdict = plan.judge(facts, target_bytes=target.bytes, compatibility=target.compatibility)
        return FileVerdict(
            asset_id=asset_id,
            filename=filename,
            output_filename=output_filename(filename or asset.original_filename, target),
            reachable=verdict.reachable,
            predicted_bytes=verdict.predicted_bytes,
            smallest_reachable_bytes=verdict.smallest_reachable_bytes,
            reason=verdict.reason,
            copy_only=verdict.copy_only,
            rewrap_only=(
                not verdict.copy_only and plan.rewrap_is_enough(facts, target_bytes=target.bytes)
            ),
            converts_audio=target.compatibility and plan.needs_audio_conversion(facts),
        )

    async def start(self, request: CompressRequest, *, viewer: Viewer) -> CompressStarted:
        """Queue one job per file. Files Sift will not act on are skipped, as are unreachable ones
        unless forced."""
        if not viewer.is_admin:
            raise NotAllowed("Only an admin can compress files.")

        target = await self.resolve_target(request)
        preflight = await self.preflight(request, viewer=viewer)
        job_ids: list[str] = []
        skipped = 0

        for verdict in preflight.files:
            if verdict.skip_reason is not None or verdict.copy_only:
                skipped += 1
                continue
            if not verdict.reachable and not request.force:
                skipped += 1
                continue
            job_ids.append(
                await self._queue.enqueue(
                    COMPRESS,
                    {
                        "asset_id": verdict.asset_id,
                        "actor_id": viewer.id,
                        "target_bytes": target.bytes,
                        "compatibility": target.compatibility,
                        "preset": target.preset.value if target.preset else None,
                        "filename": verdict.output_filename,
                    },
                )
            )

        log.info("compress.started", queued=len(job_ids), skipped=skipped, actor=viewer.id)
        return CompressStarted(job_ids=job_ids, started=len(job_ids), skipped=skipped)

    async def sample(
        self, asset_id: str, request: CompressRequest, *, viewer: Viewer
    ) -> SampleStarted:
        """Queue a few seconds encoded the way the whole file would be, so it can be looked at."""
        # Visibility before permission: 'not an admin' would confirm the file exists.
        asset = await self._access.open_asset(viewer, asset_id)
        if asset is None:
            raise NotFound(OUT_OF_REACH)
        if not viewer.is_admin:
            raise NotAllowed("Only an admin can compress files.")
        if asset.media_type not in COMPRESSIBLE:
            raise Refused("There is nothing to sample on this kind of file.")

        target = await self.resolve_target(request)
        job_id = await self._queue.enqueue(
            COMPRESS_SAMPLE,
            {
                "asset_id": asset_id,
                "actor_id": viewer.id,
                "target_bytes": target.bytes,
                "compatibility": target.compatibility,
            },
        )
        return SampleStarted(job_id=job_id)

    async def record(
        self,
        *,
        asset_id: str,
        source_asset_id: str,
        preset: str | None,
        target_bytes: int | None,
        actor_id: str | None,
    ) -> None:
        """Write down that this file was compressed out of that one."""
        await provenance.record(
            self._db,
            asset_id=asset_id,
            source_asset_id=source_asset_id,
            operation=OPERATION,
            preset=preset,
            target_bytes=target_bytes,
            actor_id=actor_id,
            now=self.now(),
        )

    async def produced_for(self, asset_id: str, *, viewer: Viewer) -> Produced | None:
        """Where this file came from, for its own page. None when it was not produced by Sift."""
        return await provenance.produced_for(self._db, self._access, asset_id, viewer=viewer)

    async def made_from(self, asset_id: str, *, viewer: Viewer) -> list[MadeCopy]:
        """The copies Sift made from this file, for its own page. Empty for most files."""
        return await provenance.made_from(self._db, self._access, asset_id, viewer=viewer)


def source_facts(asset: Asset) -> plan.SourceFacts:
    """What the arithmetic needs, straight off the indexed row. No file is opened."""
    return plan.SourceFacts(
        width=asset.width,
        height=asset.height,
        duration_ms=asset.duration_ms,
        fps=asset.fps,
        size_bytes=asset.size_bytes,
        vcodec=asset.vcodec,
        acodec=asset.acodec,
        container=asset.container,
        hdr=asset.is_hdr,
    )


def output_filename(source_name: str | None, target: Target) -> str:
    """The copy's name: `beach trip.mp4` at ten megabytes becomes `beach trip-10MB.mp4`."""
    stem = (source_name or "file").rsplit(".", 1)[0] or "file"
    return f"{stem}-{target.label}.{tuning.COMPATIBLE_CONTAINER}"


def _suggested(unreachable: list[FileVerdict]) -> int | None:
    """The smallest target every file could meet: the largest of their individual smallests."""
    smallest = [
        each.smallest_reachable_bytes
        for each in unreachable
        if each.smallest_reachable_bytes is not None
    ]
    return max(smallest) if smallest else None


COMPRESSOR: Part[CompressService] = Part("compressor")
