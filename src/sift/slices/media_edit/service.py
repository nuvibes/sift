# SPDX-License-Identifier: AGPL-3.0-or-later
"""Answering what a compression would do, starting one, and recording what came out of it.

Nothing here encodes anything. The panel's questions are answered from what is already indexed, so
a selection of four hundred files gets an answer immediately and every rule behind it can be tested
without spawning a process; the work itself is a job per file, which is what lets one fail without
taking the other three hundred and ninety-nine with it.

Three refusals decide whether a file is offered the verb at all, and all three are answered here
rather than on screen, because a screen that decides for itself eventually decides differently from
the server. A photograph is not compressed: everything in the design is about running time, frame
rate and sound, and a picture's answer is to resize it. A file in a folder handed over read-only is
not written beside, and that is the SAME question moving a file asks, asked of the same function. A
file the user cannot see does not exist as far as this is concerned.
"""

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

#: The job types this feature owns.
COMPRESS = "compress"
COMPRESS_SAMPLE = "compress_sample"

#: What the media types Sift holds are called, and which of them this acts on.
#:
#: A photograph is deliberately absent. Every mechanism here (the running time the estimate
#: divides by, the frame rate the size model multiplies by, the sound that is copied through) is
#: about something that moves. A still's answer is to resize it, which is a different action.
_IMAGE = "image"
COMPRESSIBLE = frozenset({"video", "gif"})

#: What every copy is tagged with. An ordinary tag: it filters, sorts and searches like any other,
#: and it can be taken off a file or deleted outright. Found by name, so renaming it means the next
#: copy makes a new one. See the kernel helper, which says why that is the right behaviour.
PRODUCED_TAG = "Compressed"

#: What the `operation` column records for this feature's outputs.
OPERATION = "compress"


@dataclass(frozen=True, slots=True)
class Target:
    """A resolved request: how many bytes, whether it must play anywhere, and what to call it."""

    bytes: int | None
    compatibility: bool
    preset: Preset | None

    @property
    def label(self) -> str:
        """What goes in the output's filename, so the two are told apart in a file manager.

        The size rather than the word "compressed", because compressing one file to two different
        targets is an ordinary thing to do and two files called the same thing is not: the second
        would be refused as a name already taken, and the person would have to rename the first by
        hand to get it.
        """
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

    # --- resolving what was asked for ---------------------------------------------------------

    async def resolve_target(self, request: CompressRequest) -> Target:
        """Turn a preset name into the number it currently stands for.

        Read at the moment of asking rather than held anywhere, because the numbers are settings
        and an edited one has to take effect on the next press, not on the next restart.
        """
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

    # --- what would happen --------------------------------------------------------------------

    async def preflight(self, request: CompressRequest, *, viewer: Viewer) -> Preflight:
        """Every file's answer, before anything is encoded.

        The writability question is asked once per library ROOT rather than once per file, and that
        is an approximation deliberately taken: it costs a disk check each time and a selection can
        be five hundred files. It is coarser than the question really is (one folder inside a root
        can be read-only while the rest is not), and it is safe anyway because it is advisory. The
        job asks again, per file, and its answer is the one that decides; the worst a stale answer
        here can do is offer a file the job then declines, with the reason.
        """
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
            # The same answer for "no such file" and "a file you may not see". Telling them apart
            # would let anybody confirm a file exists by asking to compress it.
            #
            # The ONE exception is this user's own vault, and it is the same exception the rest
            # of the application makes (`kernel.reach`): the vault conceals a file from onlookers
            # and has never claimed to keep it from the person who put it there. Telling them it
            # does not exist says their file has been deleted, which is a lie and the more alarming
            # reading, and it is unactionable, where the real reason comes with a PIN.
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
            # Kept rather than assumed away, the same way the write seam keeps its own impossible
            # case. The guarantee belongs to the access layer (it resolves an asset THROUGH its
            # locations, so one it will open has at least one), and this file is not where that
            # guarantee is made. A caller reaching here without it would otherwise get an index
            # error rather than a sentence.
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

    # --- starting it --------------------------------------------------------------------------

    async def start(self, request: CompressRequest, *, viewer: Viewer) -> CompressStarted:
        """Queue one job per file. Files Sift will not act on are skipped, not failed.

        A file whose target is out of reach is skipped unless the request says to go ahead anyway.
        That is the whole of what the warning does: it informs, and a person who has read it and
        pressed the button again gets the closest Sift can come to what they asked for.
        """
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
        """Queue a few seconds encoded the way the whole file would be, so it can be looked at.

        A prediction is a guess and a sample is slow, which is why there are both: the number
        appears immediately and this is for the moment somebody wants to see what they are about to
        commit four minutes to.
        """
        # Visibility before permission, so somebody who cannot see the file is told it is not
        # there rather than that they are not an admin: the second answer confirms it is.
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

    # --- what came out of it ------------------------------------------------------------------

    async def record(
        self,
        *,
        asset_id: str,
        source_asset_id: str,
        preset: str | None,
        target_bytes: int | None,
        actor_id: str | None,
    ) -> None:
        """Write down that this file was compressed out of that one.

        The writing itself is shared with the editor, which makes the same kind of row about a file
        it made the same way. What is this feature's own is the verb it records.
        """
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
        """Where this file came from, for its own page. None when it was not produced by Sift.

        Answers for an edited copy as readily as for a compressed one: the question is about the
        file rather than about which half of this feature made it.
        """
        return await provenance.produced_for(self._db, self._access, asset_id, viewer=viewer)

    async def made_from(self, asset_id: str, *, viewer: Viewer) -> list[MadeCopy]:
        """The copies Sift made from this file, for its own page. Empty for most files."""
        return await provenance.made_from(self._db, self._access, asset_id, viewer=viewer)


# --- shared shaping ---------------------------------------------------------------------------


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
    """What the copy is called: the original's name, the target, and the container's extension.

    `beach trip.mp4` compressed to ten megabytes becomes `beach trip-10MB.mp4`, which sorts beside
    the original and says at a glance which is which, in a file manager, not only in Sift.
    """
    stem = (source_name or "file").rsplit(".", 1)[0] or "file"
    return f"{stem}-{target.label}.{tuning.COMPATIBLE_CONTAINER}"


def _suggested(unreachable: list[FileVerdict]) -> int | None:
    """The smallest target every file in the selection could actually meet.

    The largest of their individual smallests, because a target has to clear the worst file in the
    set to be an answer for the set. None when nothing was out of reach.
    """
    smallest = [
        each.smallest_reachable_bytes
        for each in unreachable
        if each.smallest_reachable_bytes is not None
    ]
    return max(smallest) if smallest else None


#: Making a smaller copy of a file, or one that plays anywhere.
COMPRESSOR: Part[CompressService] = Part("compressor")
