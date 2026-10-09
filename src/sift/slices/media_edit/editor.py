# SPDX-License-Identifier: AGPL-3.0-or-later
"""Answering what an edit would do, and starting one.
A Save is an ordered list of steps producing one file, refused or allowed as a whole."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sift.kernel import filenames
from sift.kernel.access import Repository, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore
from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue
from sift.kernel.log import get_logger
from sift.kernel.media import MissingAsset, NoReadableCopy, resolve
from sift.kernel.reach import OUT_OF_REACH
from sift.kernel.seams import LibraryWriteSeam
from sift.kernel.wiring import Part
from sift.slices.media_edit import operations, orientation
from sift.slices.media_edit.models import (
    EditFrame,
    EditRequest,
    EditStarted,
    EditStep,
    EditVerdict,
)
from sift.slices.media_edit.operations import ON_MOVING, ON_STILLS, QUARTER_TURNS, Operation
from sift.slices.media_edit.orientation import Orientation
from sift.slices.media_edit.refusals import NotAllowed, NotFound, Refused
from sift.slices.media_edit.settings import (
    GIF_FORMAT_KEY,
    GIF_FORMAT_NAMES,
    resolve_gif_format,
)
from sift.slices.media_edit.tuning import LONGEST_EXACT_CUT_MS, LONGEST_GIF_MS

log = get_logger(__name__)

EDIT = "edit"

#: Its own tag: what was changed is a different question from what was made smaller.
EDITED_TAG = "Edited"

#: The provenance verb when one Save did several things.
SEVERAL_OPERATIONS = "edit"

_IMAGE = "image"
_VIDEO = "video"
_GIF = "gif"

#: A note cannot change under an asset id, so this cache never goes stale; the bound is memory.
_REMEMBERED_ORIENTATIONS = 256


@dataclass(frozen=True, slots=True)
class Shape:
    """A picture's size at some point in the list of steps. Always as it is seen."""

    width: int
    height: int


class EditService:
    """The editor's one object. Held at `app.state.editor`."""

    def __init__(
        self,
        database: Database,
        access: Repository,
        queue: JobQueue,
        writer: LibraryWriteSeam,
        content: ContentStore,
        settings: Settings,
        read_app_setting: Callable[[str], Awaitable[object]],
        *,
        clock: Callable[[], float],
    ) -> None:
        self._db = database
        self._access = access
        self._queue = queue
        self._writer = writer
        self._content = content
        self._settings = settings
        self._read_app_setting = read_app_setting
        self._clock = clock
        self._orientations: dict[str, Orientation] = {}

    def now(self) -> int:
        return int(self._clock())

    async def _gif_format(self) -> str:
        """Which format a GIF is written in, read at the moment of asking so a changed setting
        applies."""
        return resolve_gif_format(await self._read_app_setting(GIF_FORMAT_KEY))

    async def verdict(self, asset_id: str, request: EditRequest, *, viewer: Viewer) -> EditVerdict:
        """What this edit would produce, or why not: refusals raise, a dragged rectangle off the
        edge answers."""
        asset = await self._settled(asset_id, viewer=viewer)
        filename = await self._filename(asset_id, viewer=viewer)

        if asset.media_type == _IMAGE:
            return await self._unless_taken(
                await self._still_verdict(asset, request, filename=filename, viewer=viewer),
                viewer=viewer,
            )
        if asset.media_type == _VIDEO:
            return await self._unless_taken(
                await self._cut_verdict(asset, request, filename=filename, viewer=viewer),
                viewer=viewer,
            )
        if asset.media_type == _GIF:
            raise Refused(
                "Sift can't edit a GIF. Trimming one would mean rebuilding every frame, "
                "and this only ever copies them."
            )
        # A CHECK constraint names exactly the three kinds; kept for when a fourth arrives.
        raise Refused("Sift cannot edit this kind of file.")  # pragma: no cover

    async def frame_of(self, asset_id: str, *, viewer: Viewer) -> EditFrame:
        """How big this picture is as it is seen. Asked once, when the editor opens."""
        asset = await self._settled(asset_id, viewer=viewer)
        return EditFrame(asset_id=asset_id, width=asset.width, height=asset.height)

    async def settle(self, asset_id: str, *, viewer: Viewer) -> None:
        """Establish that this user may act on this file, before the route reads the body."""
        await self._settled(asset_id, viewer=viewer)

    async def _settled(self, asset_id: str, *, viewer: Viewer) -> Asset:
        """The asset, once it is established that this user may see it, and then act on it."""
        asset = await self._access.open_asset(viewer, asset_id)
        if asset is None:
            raise NotFound(OUT_OF_REACH)
        if not viewer.is_admin:
            raise NotAllowed("Only an admin can edit files.")
        return asset

    async def _filename(self, asset_id: str, *, viewer: Viewer) -> str | None:
        locations = await self._access.locations(viewer, asset_id)
        return locations[0].filename if locations else None

    async def orientation_of(self, asset_id: str) -> Orientation:
        """Which way up this photograph really goes, read once and remembered; upright when
        unreadable."""
        remembered = self._orientations.get(asset_id)
        if remembered is not None:
            return remembered
        try:
            source = await resolve(self._content, asset_id)
        except (MissingAsset, NoReadableCopy):
            return orientation.UPRIGHT
        found = await orientation.read_orientation(source.path, settings=self._settings)
        if len(self._orientations) >= _REMEMBERED_ORIENTATIONS:
            self._orientations.clear()
        self._orientations[asset_id] = found
        return found

    async def _still_verdict(
        self, asset: Asset, request: EditRequest, *, filename: str | None, viewer: Viewer
    ) -> EditVerdict:
        if any(step.operation not in ON_STILLS for step in request.steps):
            raise Refused("A photograph is cropped, resized or rotated, not cut.")
        fmt = operations.still_format_for(asset.mime)
        if fmt is None:
            raise Refused("Sift cannot save a picture in that format.")
        if asset.width is None or asset.height is None:
            # A refusal that fixes itself once probing has run.
            return _refused(asset.id, "Sift has not measured this picture yet. Try again shortly.")

        seen = Shape(asset.width, asset.height)

        # The rectangle cut is not always the one drawn; every later answer is about the real one.
        asked = settled(request)
        refusal, result = walk_steps(asked.steps, seen)
        if refusal is not None:
            return _refused(asset.id, refusal)

        if (written := await self._writability(asset.id, viewer=viewer)) is not None:
            return _refused(asset.id, written)

        source_extension = (filename or "").rsplit(".", 1)[-1].lower()
        cropped = next((step for step in asked.steps if step.operation is Operation.CROP), None)
        return EditVerdict(
            asset_id=asset.id,
            allowed=True,
            output_filename=self._name_for(
                asked, source_name=filename or asset.original_filename, extension=fmt.extension
            ),
            converted_from=source_extension if source_extension != fmt.extension else None,
            lossy=fmt.lossy,
            left=cropped.left if cropped else None,
            top=cropped.top if cropped else None,
            width=cropped.width if cropped else None,
            height=cropped.height if cropped else None,
            frame_width=seen.width,
            frame_height=seen.height,
            result_width=result.width,
            result_height=result.height,
        )

    async def _cut_verdict(
        self, asset: Asset, request: EditRequest, *, filename: str | None, viewer: Viewer
    ) -> EditVerdict:
        # The request shape guarantees a cut travels alone.
        step = request.steps[0]
        if step.operation not in ON_MOVING:
            raise Refused("A video is trimmed or clipped, not cropped.")
        fmt = operations.moving_format_for(asset.mime)
        if fmt is None:
            raise Refused("Sift cannot cut a file in that container without re-encoding it.")

        start = step.start_ms or 0
        length = step.duration_ms or 0
        if asset.duration_ms is None:
            return _refused(asset.id, "Sift has not measured this video yet. Try again shortly.")
        if start >= asset.duration_ms:
            return _refused(asset.id, "That starts after the end of the video.")
        if start + length > asset.duration_ms:
            return _refused(asset.id, "That runs past the end of the video.")

        # Asked once, so the copy's name and its limits follow the same format.
        gif_format = await self._gif_format() if step.operation is Operation.GIF else None

        # A trim copies packets and is unbounded; the other two are not copies.
        if (too_long := _too_long(step.operation, length, gif_format)) is not None:
            return _refused(asset.id, too_long)

        if (written := await self._writability(asset.id, viewer=viewer)) is not None:
            return _refused(asset.id, written)

        extension = (
            operations.GIF_FORMATS[gif_format].extension
            if gif_format is not None
            else fmt.extension
        )
        return EditVerdict(
            asset_id=asset.id,
            allowed=True,
            output_filename=self._name_for(
                request,
                source_name=filename or asset.original_filename,
                extension=extension,
                gif_format=gif_format,
            ),
            # A GIF is rebuilt from the marked moment, so it starts exactly there.
            approximate_start=start > 0 and step.operation is Operation.TRIM,
        )

    async def _unless_taken(self, verdict: EditVerdict, *, viewer: Viewer) -> EditVerdict:
        """The same answer, unless the copy would land on a name already there; said before Save,
        not in a log."""
        if not verdict.allowed or not verdict.output_filename:
            return verdict
        taken = await self._writer.name_taken_beside(
            verdict.asset_id, filename=verdict.output_filename, actor=viewer
        )
        if not taken:
            return verdict
        return _refused(
            verdict.asset_id,
            f"There is already a file called \u2018{verdict.output_filename}\u2019 beside this "
            "one. Rename it, or move the old copy, and Sift will save this.",
        )

    async def _writability(self, asset_id: str, *, viewer: Viewer) -> str | None:
        """Whether a produced file may be written beside this one; the job asks again."""
        return await self._writer.writable_beside(asset_id, actor=viewer)

    def _name_for(
        self,
        request: EditRequest,
        *,
        source_name: str | None,
        extension: str,
        gif_format: str | None = None,
    ) -> str:
        """What the copy is called: the typed stem with the encoded extension, or a derived name."""
        if request.filename is None:
            return operations.output_filename(
                source_name,
                suffix_text=derived_suffix(request.steps, gif_format=gif_format),
                extension=extension,
            )
        try:
            # Both the stem and the whole name have to be valid: an empty stem would be a hidden
            # file.
            stem = filenames.check_filename(request.filename)
            return filenames.check_filename(f"{stem}.{extension}")
        except filenames.InvalidFilename as refused:
            raise Refused(str(refused)) from refused

    async def start(self, asset_id: str, request: EditRequest, *, viewer: Viewer) -> EditStarted:
        """Queue the edit after asking the verdict again; the client is never where a rule lives."""
        answer = await self.verdict(asset_id, request, viewer=viewer)
        if not answer.allowed or answer.output_filename is None:
            raise Refused(answer.reason or "Sift will not do that to this file.")

        # The settled request, which the name in `answer` was built from.
        asked = settled(request)
        # Only a photograph is opened for its note; a cut copies packets.
        turned = (
            orientation.UPRIGHT
            if any(step.operation in ON_MOVING for step in asked.steps)
            else await self.orientation_of(asset_id)
        )
        job_id = await self._queue.enqueue(
            EDIT,
            {
                "asset_id": asset_id,
                "actor_id": viewer.id,
                "steps": [step.model_dump(mode="json") for step in asked.steps],
                # Carried, so the encoder is pointed at the picture the person aimed at.
                "quarter_turns": turned.quarter_turns,
                "mirrored": turned.mirrored,
                # Carried: the name already has this format's extension.
                "gif_format": await self._gif_format(),
                # The intent, never the name of what runs. See `register_handlers`.
                "as_loop": asked.as_loop,
                "filename": answer.output_filename,
            },
        )
        log.info(
            "edit.started",
            asset_id=asset_id,
            operations=[step.operation.value for step in asked.steps],
            actor=viewer.id,
        )
        return EditStarted(job_id=job_id, output_filename=answer.output_filename)


def settled(request: EditRequest) -> EditRequest:
    """The request as it will really be carried out: a crop snapped onto the colour blocks, once."""
    return request.model_copy(update={"steps": [_settled_step(step) for step in request.steps]})


def _settled_step(step: EditStep) -> EditStep:
    if step.operation is not Operation.CROP:
        return step
    left, top, width, height = operations.snap_to_even(
        step.left or 0, step.top or 0, step.width or 0, step.height or 0
    )
    return step.model_copy(update={"left": left, "top": top, "width": width, "height": height})


def walk_steps(steps: Sequence[EditStep], frame: Shape) -> tuple[str | None, Shape]:
    """Every step in turn against the picture it is handed, and what comes out; stops at the first
    refusal.
    Not `walk`, which the blocking-call gate matches by name."""
    shape = frame
    for step in steps:
        refusal = _step_refusal(step, shape)
        if refusal is not None:
            return refusal, shape
        shape = _after(step, shape)
    return None, shape


def _after(step: EditStep, shape: Shape) -> Shape:
    """The picture's size once this step has been applied to it."""
    if step.operation is Operation.CROP:
        return Shape(step.width or shape.width, step.height or shape.height)
    if step.operation is Operation.RESIZE:
        width = step.width or shape.width
        # Rounded the way ffmpeg's `scale=W:-1` rounds it.
        return Shape(width, max(1, round(shape.height * width / shape.width)) if shape.width else 1)
    if step.turn in QUARTER_TURNS:
        return Shape(shape.height, shape.width)
    return shape


def _refused(asset_id: str, reason: str) -> EditVerdict:
    return EditVerdict(asset_id=asset_id, allowed=False, reason=reason)


def _step_refusal(step: EditStep, shape: Shape) -> str | None:
    """Whether one still step's numbers are true of the picture it is handed. Enlarging is refused."""
    if step.operation is Operation.CROP:
        left, top = step.left or 0, step.top or 0
        box_width, box_height = step.width or 0, step.height or 0
        if box_width < 2 or box_height < 2:
            # A rectangle that snapped to nothing; usually a tap.
            return "That rectangle is too small to keep anything."
        if left + box_width > shape.width or top + box_height > shape.height:
            return (
                f"That rectangle falls outside the picture, which is "
                f"{shape.width} by {shape.height}."
            )
        return None
    if step.operation is Operation.RESIZE:
        if (step.width or 0) > shape.width:
            return (
                f"This picture is only {shape.width} pixels across. Making it wider cannot add "
                "detail that is not there, and the file would be bigger and softer."
            )
        return None
    return None


def _too_long(operation: Operation, length_ms: int, gif_format: str | None) -> str | None:
    """Whether this piece is longer than its kind allows, and the sentence with the limit in it."""
    if operation is Operation.GIF and gif_format is not None:
        allowed = LONGEST_GIF_MS[gif_format]
        if length_ms > allowed:
            reason = (
                "A GIF stores every frame whole, so a longer one is enormous."
                if gif_format == "gif"
                else "Longer than that is not really a GIF any more."
            )
            named = GIF_FORMAT_NAMES[gif_format]
            return (
                f"{named.article.capitalize()} {named.spoken} can be at most "
                f"{allowed // 1000} seconds. {reason}"
            )
    if operation is Operation.CLIP and length_ms > LONGEST_EXACT_CUT_MS:
        return (
            f"A clip can be at most {LONGEST_EXACT_CUT_MS // 60_000} minutes, because it is "
            "rebuilt frame by frame so it begins exactly where you marked it. Trim it instead to "
            "take a longer piece."
        )
    return None


def derived_suffix(steps: Sequence[EditStep], *, gif_format: str | None = None) -> str:
    """What Sift calls the copy when nobody typed a name."""
    if len(steps) != 1:
        return operations.SEVERAL
    step = steps[0]
    return operations.suffix(
        step.operation,
        turn=step.turn,
        width=step.width,
        height=step.height if step.operation is Operation.CROP else None,
        start_ms=step.start_ms,
        gif_format=gif_format,
    )


def recorded_operation(steps: Sequence[EditStep]) -> str:
    """The verb the provenance row carries. The one that was done, or the word for several."""
    return steps[0].operation.value if len(steps) == 1 else SEVERAL_OPERATIONS


EDITOR: Part[EditService] = Part("editor")
