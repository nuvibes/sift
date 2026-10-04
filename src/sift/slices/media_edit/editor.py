# SPDX-License-Identifier: AGPL-3.0-or-later
"""Answering what an edit would do, and starting one.

The other half of this slice. Compressing answers "make this smaller" for four hundred files at
once; editing answers "keep this rectangle" or "take this piece" for exactly one, and the two share
everything about HOW a produced file is written and nothing about what is being asked.

**A Save carries a list of operations, in order, and produces one file.** That is not a convenience:
every operation writes a new file, so asking for them one at a time leaves a copy on the disk for
each step and only the last one is what anybody wanted. The list is refused or allowed as a whole:
a third step that will not work means nothing is written, not that the first two already have been.

Nothing here opens a file except to read which way up it goes. Every other question the panel asks
(does this rectangle fit, is that width bigger than the photograph, does that clip run past the
end) is arithmetic over what probing already recorded, so the panel answers as fast as somebody
can drag, and every rule behind it can be tested without spawning a process.

Sizes here are the picture as it is SEEN, which is the size probing records. For a photograph a
camera turned that is the other way round from the size on disk, and the editor works in what is on
screen from end to end so that a rectangle dragged over the picture cuts the part of it that was
dragged over. See the orientation module.

The order of the refusals is the same order compressing uses, and it is not stylistic. Visibility
is settled first, so a file the user may not see gets the answer a missing file gets rather than
"admins only", which would confirm it is there. Then permission, then whether the operation means
anything for this kind of file, then whether the numbers are true of it, and last whether the
folder can be written to at all.
"""

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

#: The job type this half of the feature owns.
EDIT = "edit"

#: What every edited copy is tagged with. Its own tag rather than the compression one, because the
#: two answer different questions ("what have I made smaller" and "what have I changed") and a
#: single tag over both can answer neither.
EDITED_TAG = "Edited"

#: What the provenance row records when one Save did several things. The individual verbs are kept
#: for a Save that did one thing, because "Cropped from" says more than "Edited from" and there is
#: no reason to lose it, but a row cannot name four verbs and stay a sentence.
SEVERAL_OPERATIONS = "edit"

#: The media types each set of operations means anything for.
_IMAGE = "image"
_VIDEO = "video"
_GIF = "gif"

#: How many files' orientation is remembered at once.
#:
#: The note cannot change under an asset id: a file's identity in Sift IS its bytes, so a file
#: whose note changed is a different asset with a different id. So this is a cache with no
#: staleness question attached to it, and all the bound is protecting is memory on a machine where
#: somebody has opened the editor on a great many photographs without restarting.
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
        """Which format a GIF is written in, read at the moment of asking.

        Read rather than held, the same as the compression targets and for the same reason: an
        edited setting has to take effect on the next press rather than on the next restart.
        """
        return resolve_gif_format(await self._read_app_setting(GIF_FORMAT_KEY))

    # --- what would happen ----------------------------------------------------------------------

    async def verdict(self, asset_id: str, request: EditRequest, *, viewer: Viewer) -> EditVerdict:
        """What this edit would produce, or the sentence explaining why it will not.

        Two kinds of no, and they leave here differently. A file this user may not touch, or an
        operation that means nothing for this kind of file, is raised: the panel should never have
        offered it, and something is wrong beyond the numbers on screen. A rectangle that falls off
        the edge of the photograph comes back as an answer with `allowed` false and a sentence,
        because that is somebody dragging, and it will be true again a moment later.
        """
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
        # The column carries a CHECK constraint naming exactly the three kinds above, so a row
        # cannot currently reach here. Kept rather than removed: a fourth kind is one migration
        # away, and the alternative to this line is falling off the end of the function and handing
        # back None to a caller with no idea what to do with one.
        raise Refused("Sift cannot edit this kind of file.")  # pragma: no cover

    async def frame_of(self, asset_id: str, *, viewer: Viewer) -> EditFrame:
        """How big this picture is as it is seen. Asked once, when the editor opens.

        The recorded size, which is already the size as seen: probing records a photograph's size
        after its note's turn and a video's after its own. Turning it again here would hand the
        panel the size the picture is stored at.
        """
        asset = await self._settled(asset_id, viewer=viewer)
        return EditFrame(asset_id=asset_id, width=asset.width, height=asset.height)

    async def settle(self, asset_id: str, *, viewer: Viewer) -> None:
        """Establish that this user may act on this file, and nothing else.

        Its own call because the routes need it BEFORE they look at what was sent. A request with
        no body is a 409, and answered first it becomes a different reply for a well-formed request
        than for a broken one, from a route the caller may not use either way, which is how the
        shape of a request turns into a way of asking whether a file exists.
        """
        await self._settled(asset_id, viewer=viewer)

    async def _settled(self, asset_id: str, *, viewer: Viewer) -> Asset:
        """The asset, once it is established that this user may see it and may act on it.

        Visibility before permission. The other order answers "only an admin can do that" to
        somebody who was not allowed to know the file exists, which is the answer telling them.
        """
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
        """Which way up this photograph really goes, read once and then remembered.

        Anything that stops the file being read at all answers upright, which is what every other
        part of Sift already assumes about every file: it leaves the editor no better than it is
        today on that one photograph, where refusing would make it worse on the ones that were never
        affected.
        """
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
            # Probing fills these in, and until it has there is no frame to measure a rectangle
            # against. A refusal that fixes itself, so it is worded as one that will.
            return _refused(asset.id, "Sift has not measured this picture yet. Try again shortly.")

        # The recorded size is the size as seen (see `frame_of`).
        seen = Shape(asset.width, asset.height)

        # Settled before anything is checked or named, because the rectangle that gets cut is not
        # always the one that was drawn, and every later answer has to be about the real one.
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
            # Named only when the produced file really is a different format, so the panel has
            # nothing to say in the ordinary case.
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
        # A cut travels alone, which the request shape has already established, so there is exactly
        # one step here to look at.
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

        # Which format a GIF is written in, asked once. Everything below that differs
        # between the three (the extension on the copy's name, and how long one may be), follows
        # from it, so a screen never says `.gif` about a file that is about to be an AVIF.
        gif_format = await self._gif_format() if step.operation is Operation.GIF else None

        # How long a piece may be. A trim is deliberately unbounded: it copies the packets already
        # in the file, so a trim of a two-hour video costs almost nothing. The other two are not
        # copies.
        if (too_long := _too_long(step.operation, length, gif_format)) is not None:
            return _refused(asset.id, too_long)

        if (written := await self._writability(asset.id, viewer=viewer)) is not None:
            return _refused(asset.id, written)

        # A GIF is the one operation here whose output format is not the input's, so its
        # extension comes from the chosen format rather than from the source's container.
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
            # A GIF is rebuilt frame by frame from the moment asked for, so it begins exactly
            # where it was marked: the same as a clip and unlike a copied trim.
            approximate_start=start > 0 and step.operation is Operation.TRIM,
        )

    async def _unless_taken(self, verdict: EditVerdict, *, viewer: Viewer) -> EditVerdict:
        """The same answer, unless the copy would land on a name that is already there.

        **Here rather than in each branch, because it is one rule.** Every other reason an edit
        cannot happen is answered before anybody presses anything; answered only inside the job,
        this one would come minutes later and out of sight: press Save, read "saving it as a new
        file", and nothing ever appears, with the refusal sitting in a log. A loop saved a second
        time does exactly this, producing the same name because it is the same cut of the same
        moment. That collision is CORRECT; being silent about it is not.

        Not a guarantee, and it does not need to be. Something can arrive in that folder between
        this answer and the encode finishing, and the write seam still refuses atomically at the
        end. This is the half that can be read by a person.

        A verdict that is already a no is handed back untouched: the first reason is the useful one,
        and asking the filesystem about a name the caller is not going to use is a stat for nothing.
        """
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
        """Whether a produced file may be written beside this one, asked of the one thing that
        decides. The job asks again when it runs, and its answer is the one that counts."""
        return await self._writer.writable_beside(asset_id, actor=viewer)

    def _name_for(
        self,
        request: EditRequest,
        *,
        source_name: str | None,
        extension: str,
        gif_format: str | None = None,
    ) -> str:
        """What the copy is called: what somebody typed, or what Sift derives when they did not.

        A typed name is the STEM only. The extension comes from what the copy is encoded as, which
        is not the person's to choose: a HEIC comes back as a JPEG whatever anybody types, and a
        perfectly good picture named `.txt` cannot be opened by name.

        The rules a typed name has to satisfy are the kernel's, the same ones renaming a file uses,
        so a name one screen accepts is a name the other accepts.

        `gif_format` travels with the extension and for the same reason: the stem a derived name is
        built from says which format the copy is, and a photograph's name has no GIF in it.
        """
        if request.filename is None:
            return operations.output_filename(
                source_name,
                suffix_text=derived_suffix(request.steps, gif_format=gif_format),
                extension=extension,
            )
        try:
            # Twice, over the two things that both have to be true. The STEM has to be a name on
            # its own, or an empty one silently produces a file called nothing but its extension:
            # a hidden file with no name, which is not what anybody typed. The whole thing has to
            # be a name as well, because the length cap is on the name and the extension is part of
            # it.
            stem = filenames.check_filename(request.filename)
            return filenames.check_filename(f"{stem}.{extension}")
        except filenames.InvalidFilename as refused:
            raise Refused(str(refused)) from refused

    # --- starting it ----------------------------------------------------------------------------

    async def start(self, asset_id: str, request: EditRequest, *, viewer: Viewer) -> EditStarted:
        """Queue the edit. One file, one job, and the answer above is asked again first.

        Asked again rather than trusted from the panel, because between the panel drawing and the
        button being pressed the folder can have been made read-only and the numbers on screen can
        have been changed by anything. A client is where a request comes from, never where a rule
        lives.
        """
        answer = await self.verdict(asset_id, request, viewer=viewer)
        if not answer.allowed or answer.output_filename is None:
            raise Refused(answer.reason or "Sift will not do that to this file.")

        # The SETTLED request, not the one that arrived. The name in `answer` was built from the
        # settled one, so queueing the original would produce a file whose size contradicts its own
        # name, which is the fault this whole path exists to avoid.
        asked = settled(request)
        # Only a photograph is opened to find out which way up it is. A cut carries the note
        # through untouched (the packets are copied, not decoded), and the same reasoning that
        # keeps `frame_of` from reading one applies here: it would be a frame decoded out of a
        # two-hour video to answer a question this job never asks.
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
                # Carried rather than read again in the job, so that the picture the person aimed at
                # is the picture the encoder is pointed at. Reading it twice is two answers to one
                # question and a way for them to differ.
                "quarter_turns": turned.quarter_turns,
                "mirrored": turned.mirrored,
                # Carried for exactly the reason the turn above is: the NAME in `answer` already
                # carries this format's extension, so a job that read the setting again could build
                # an AVIF into a file called `.gif` if somebody changed it in between.
                "gif_format": await self._gif_format(),
                # Carried as the intent that was asked for, never as the name of what runs. What
                # this means is decided outside this slice. See `register_handlers`.
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


# --- the rules, as plain functions --------------------------------------------------------------


def settled(request: EditRequest) -> EditRequest:
    """The request as it will really be carried out, which for a crop is not what was sent.

    A rectangle has to land on the picture's colour blocks, so the numbers move. Doing it here
    (once, before anything is checked, named, reported or queued) is what keeps the panel's "you
    are keeping 784 by 604" the same rectangle as the file that appears. Rounded inside the
    command builder instead, the screen would promise 605.
    """
    return request.model_copy(update={"steps": [_settled_step(step) for step in request.steps]})


def _settled_step(step: EditStep) -> EditStep:
    if step.operation is not Operation.CROP:
        return step
    left, top, width, height = operations.snap_to_even(
        step.left or 0, step.top or 0, step.width or 0, step.height or 0
    )
    return step.model_copy(update={"left": left, "top": top, "width": width, "height": height})


def walk_steps(steps: Sequence[EditStep], frame: Shape) -> tuple[str | None, Shape]:
    """Every step in turn against the picture it will really be handed, and what comes out.

    The point of walking rather than checking each step against the original: a crop to 400 across
    followed by a resize to 800 is an enlargement, and it does not look like one until the two are
    read in order. Checked against the original photograph both steps pass, and the copy comes back
    bigger and softer than anything anybody asked for.

    The first step that will not work stops the walk and its sentence is the answer. A list is
    refused as a whole, so there is nothing to be gained by collecting the rest.

    Named `walk_steps` rather than `walk` because the gate that keeps blocking calls off the event
    loop matches bare names, and `walk` is one of the standard library's own directory walks. This
    one touches nothing but its arguments; the collision was in the name alone.
    """
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
        # The height follows the width, which is what `scale=W:-1` does. Rounded the way ffmpeg
        # rounds it, so the number reported is the number that lands.
        return Shape(width, max(1, round(shape.height * width / shape.width)) if shape.width else 1)
    if step.turn in QUARTER_TURNS:
        return Shape(shape.height, shape.width)
    # A half turn and both mirrors leave the picture the same size as they found it.
    return shape


def _refused(asset_id: str, reason: str) -> EditVerdict:
    return EditVerdict(asset_id=asset_id, allowed=False, reason=reason)


def _step_refusal(step: EditStep, shape: Shape) -> str | None:
    """Whether one still step's numbers are true of the picture it is handed. None when they are.

    Resizing REFUSES to enlarge rather than warning about it. Enlarging adds no detail (there is
    none to add) and hands back a bigger file that looks softer than the one it came from, so
    there is no version of the request that was worth carrying out. It is the same reasoning as the
    floor a compression will not go under, and the opposite of an unreachable size target, which
    warns and obeys: a target out of reach still has a best answer, and an enlargement does not.
    """
    if step.operation is Operation.CROP:
        left, top = step.left or 0, step.top or 0
        box_width, box_height = step.width or 0, step.height or 0
        if box_width < 2 or box_height < 2:
            # What is left of a rectangle a pixel or two across once it has been snapped onto the
            # picture's colour blocks. Almost always a tap rather than a drag.
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
    """Whether this piece is longer than its kind of production allows, and what to say if it is.

    Both numbers are in `tuning.py` beside the reasoning for them. Said as a sentence with the
    limit IN it, because "that is too long" leaves somebody dragging a handle to find out where the
    edge is, and the edge is not visible on the timeline.

    A GIF's ceiling is per FORMAT, because the reason is the format: a GIF stores every
    frame whole and grows with the number of them, and the other two compress between frames the
    way a video does.

    The format is NAMED rather than shouted, and it takes its own article: built out of the stored
    key (upper-cased, with "A" in front of it), it would read "A AVIF can be at most 60 seconds"
    and "A WEBP" beside a menu that says WebP. Both live in `settings.GIF_FORMAT_NAMES`, so a
    sentence and a menu cannot disagree about what a format is called.
    """
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
    """What Sift calls the copy when nobody typed a name.

    One step keeps the name that says which edit it was, because that is what makes an edited copy
    recognizable in a file manager. Several cannot: four names end to end is not recognizable and
    would not fit inside the length a name is allowed to be.

    `gif_format` is which format a GIF is being written in. Carried rather than read here,
    because it is settled once, before anything is named or refused, and reading it a second time
    is a second answer to one question and a way for the name and the file to differ.
    """
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


#: Cropping, resizing and rotating a photograph, and taking a piece out of a video.
EDITOR: Part[EditService] = Part("editor")
