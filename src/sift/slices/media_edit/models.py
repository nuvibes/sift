# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the compression panel sends and what it gets back.

The shape of a request is the same one tags, People and collections already use (a list of asset
ids with a declared maximum), because a fourth shape for "do this to what is selected" is a fourth
thing to keep in step.

The answer is deliberately per file rather than a summary. A single banner over forty files says
nothing anybody can act on: the interesting cases are the three that will not fit and the one that
needs nothing done to it, and a count alone cannot point at them.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from sift.kernel.filenames import MAX_FILENAME_LENGTH
from sift.kernel.wire import Wire
from sift.slices.media_edit.operations import ON_MOVING, Operation, Turn
from sift.slices.media_edit.settings import Preset
from sift.slices.media_edit.tuning import MAX_BULK_ASSETS, MAX_EDIT_STEPS


class CompressRequest(Wire):
    """What to compress, and to what.

    A size target, a compatibility target, or both, and at least one of them, because a request
    for neither is a request to re-encode a file into itself.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    #: Which named target, or None to ask only for compatibility.
    preset: Preset | None = None
    #: The number, when the preset is `custom`. Ignored otherwise.
    custom_target_mb: int | None = Field(default=None, ge=1)
    #: Make it play anywhere: a widely-supported container and codec, rewrapped where it can be.
    compatibility: bool = False
    #: Go ahead on files Sift has said cannot meet the target. The warning informs; it does not
    #: refuse. Nothing is pre-selected toward this: a caller has to send it.
    force: bool = False

    @model_validator(mode="after")
    def _asks_for_something(self) -> CompressRequest:
        if self.preset is None and not self.compatibility:
            raise ValueError("choose a size target, or ask for it to play anywhere, or both")
        if self.preset is Preset.CUSTOM and self.custom_target_mb is None:
            raise ValueError("a custom target needs a size")
        return self


class FileVerdict(Wire):
    """What will happen to one file, before anything happens to it."""

    asset_id: str
    filename: str | None = None
    #: What the copy will be called. Absent when this file is not going to produce one.
    output_filename: str | None = None
    #: False when Sift cannot meet the target for this file at any quality worth having.
    reachable: bool
    #: What the first attempt is expected to weigh. An estimate, and the panel says so.
    predicted_bytes: int | None = None
    #: The smallest this file could honestly be made, when the asked-for target is out of reach.
    smallest_reachable_bytes: int | None = None
    #: Why it cannot be met, in terms of this file. Only ever set alongside `reachable = false`.
    reason: str | None = None
    #: Nothing has to be re-encoded: it already meets the target and already plays anywhere.
    copy_only: bool = False
    #: The container is being changed and the streams copied. Quick, and loses nothing.
    rewrap_only: bool = False
    #: This file's sound cannot travel in the container that plays anywhere, so it is being
    #: converted. The one case where the sound is touched at all.
    converts_audio: bool = False
    #: Set when the file is not something this can act on at all: a photograph, a folder handed
    #: over read-only, something the user cannot see. The verb should not have been offered.
    skip_reason: str | None = None


class Preflight(Wire):
    """The whole answer for a selection: every file, and the few numbers worth showing above them."""

    target_bytes: int | None = None
    files: list[FileVerdict]
    #: How many will be compressed if this is accepted as it stands.
    eligible_count: int
    #: How many cannot meet the target. The number the warning above the list states.
    unreachable_count: int
    #: How many need nothing done to them at all.
    copy_only_count: int
    #: How many will have their sound converted, which otherwise never happens.
    audio_conversion_count: int
    #: The smallest target that every file in this selection could actually meet. Offered as the
    #: alternative, so a warning ends in something to press rather than in a dead end.
    suggested_target_bytes: int | None = None


class CompressStarted(Wire):
    """What was queued. One job per file, so one can fail without taking the rest with it."""

    job_ids: list[str]
    started: int
    skipped: int


class SampleStarted(Wire):
    """The job building a few seconds to look at, and where the result will be readable."""

    job_id: str


class EditStep(Wire):
    """One thing to do to the picture, and the numbers it runs on.

    Every operation's own numbers sit in this one shape rather than in five, with the rules about
    which of them are required enforced below. Five shapes would be five routes or a discriminated
    union in the address bar, and neither is worth it for what is at most four numbers.

    All of them are in the picture as it is SEEN rather than as it is stored, which is the same
    thing for almost every file and is not the same thing for a photograph a camera turned. See the
    orientation module for why the editor works in one and not the other.
    """

    operation: Operation
    #: Crop only: the rectangle to keep, in the picture's own pixels, from the top left.
    left: int | None = Field(default=None, ge=0)
    top: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    #: Rotate only. A mirror is one of these. See the operations module for why.
    turn: Turn | None = None
    #: Trim and clip: where the piece starts and how long it runs, in milliseconds.
    start_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _has_what_it_needs(self) -> EditStep:
        """Refuse a step that names an operation and not the numbers it runs on.

        Here rather than in the service because it is a fact about the shape rather than about the
        file: a crop with no rectangle is not a crop that might work on a bigger photograph, it is
        an incomplete sentence. What the numbers have to be TRUE OF (inside the picture, inside
        the running time) is the service's, because that needs the file.
        """
        if self.operation is Operation.CROP and None in (
            self.left,
            self.top,
            self.width,
            self.height,
        ):
            raise ValueError("a crop needs a rectangle")
        if self.operation is Operation.RESIZE and self.width is None:
            raise ValueError("a resize needs a width")
        if self.operation is Operation.ROTATE and self.turn is None:
            raise ValueError("a rotation needs a direction")
        if self.operation in ON_MOVING and self.duration_ms is None:
            raise ValueError("a cut needs a length")
        return self


class EditRequest(Wire):
    """One Save, of one file, carrying everything that was done to it.

    One file rather than a selection, and that is the difference from compressing. A compression is
    the same instruction to four hundred files: make each of these smaller. An edit is a rectangle
    over a particular photograph or a moment in a particular video, and there is no meaning to
    applying one of those to the next file along.

    **An ordered list rather than one operation, because every operation writes a new file.** Asked
    one at a time, cropping a photograph and then turning it leaves two copies on the disk and the
    first of them is something nobody wanted. So the steps arrive together, are refused or allowed
    together, and produce one file, and a list whose third step is refused produces nothing at all
    rather than a half-edited picture.
    """

    steps: list[EditStep] = Field(min_length=1, max_length=MAX_EDIT_STEPS)
    #: What to call the copy, without its extension. Absent leaves the name Sift would derive.
    #: The stem only: the extension is decided by what the copy is encoded as rather than by the
    #: person, and a perfectly good picture named `.txt` cannot be opened by name.
    #:
    #: Bounded here as well as checked below, so a megabyte of text is turned away by the shape
    #: rather than carried into a rule that was written to answer a person typing.
    filename: str | None = Field(default=None, max_length=MAX_FILENAME_LENGTH)
    #: Mark the produced file as a loop as well, covering the whole of it.
    #:
    #: A BOOLEAN and never a job name, deliberately. What the flag means (which handler runs) is
    #: named by the composition root, so a request cannot choose what the server executes and this
    #: slice never learns that loops exist.
    #:
    #: It is here rather than in a second call from the screen because the produced file does not
    #: exist yet when the button is pressed: the encode is a background job and the new id is not
    #: known until it lands.
    as_loop: bool = False

    @model_validator(mode="after")
    def _a_cut_travels_alone(self) -> EditRequest:
        """A cut is the whole request or none of it.

        Not a rule about what kind of file this is: that is the service's, which knows. This is
        the narrower fact that a cut and a still operation cannot compose at all: taking a piece out
        of a video copies the packets that are already there without decoding them, and a filter
        over the picture is only possible on a decoded frame. Asking for both is asking for the one
        thing trimming promises not to be.
        """
        cuts = [step for step in self.steps if step.operation in ON_MOVING]
        if cuts and len(self.steps) > 1:
            raise ValueError("a trim or a clip is done on its own")
        return self


class EditFrame(Wire):
    """How big the picture is as somebody SEES it, asked once when the editor opens.

    Its own answer rather than part of the verdict, because the panel needs it before there is
    anything to have a verdict about: a rectangle is dragged over a picture, and until the size of
    that picture is known there is nothing to drag over.

    For almost every file this is the size Sift already recorded. It is the other way round for a
    photograph a camera turned, and that is the whole reason this is asked.
    """

    asset_id: str
    width: int | None = None
    height: int | None = None


class EditVerdict(Wire):
    """What this edit would do, before it does it. The panel asks for one every time it changes."""

    asset_id: str
    #: True when this can go ahead as asked. Everything below says more about why, or about what
    #: it will produce.
    allowed: bool
    #: Why not, in terms of this file. Only ever set alongside `allowed = false`.
    reason: str | None = None
    #: What the copy will be called. Absent when there is not going to be one.
    output_filename: str | None = None
    #: Set when the produced file will not be in the format the source is, which happens for one
    #: format only: a HEIC comes back as a JPEG. Said before it runs, not discovered afterwards.
    converted_from: str | None = None
    #: Set when saving costs a generation of quality, so a rotation is not sold as free.
    lossy: bool = False
    #: Crops only: the rectangle that will REALLY be cut, which is the one that was asked for
    #: rounded to something the picture's colour blocks allow. The panel reports these rather than
    #: the numbers it sent, or it promises a size the file does not come out at.
    left: int | None = None
    top: int | None = None
    width: int | None = None
    height: int | None = None
    #: The picture as it is SEEN, which is what everything above is measured against. The same as
    #: the size Sift has recorded for almost every file, and the other way round for a photograph a
    #: camera turned, so the panel has one number to aim with rather than two that disagree.
    frame_width: int | None = None
    frame_height: int | None = None
    #: How big the copy comes out, once every step has been applied in order. The one number a
    #: person asks for after a crop and a resize together, and it is not either step's own.
    result_width: int | None = None
    result_height: int | None = None
    #: Cuts only. The copy may begin slightly before the moment asked for, because a stream copy
    #: can only start at a frame that does not depend on an earlier one. Reported as a fact rather
    #: than as a number of milliseconds: how far back the nearest one is depends on how the file
    #: was encoded, and Sift would have to read the file to know. Never late, only early.
    approximate_start: bool = False


class EditStarted(Wire):
    """The job doing the work. One file, so one job."""

    job_id: str
    output_filename: str


class MadeCopy(Wire):
    """One copy made from a file, for the ORIGINAL's page.

    The mirror of `Produced`: that says what a copy came from, this says what came from the file
    you are looking at. Both are the same row read from opposite ends, and both are scoped: a
    copy this user may not see is left out rather than named.
    """

    asset_id: str
    filename: str
    #: The verb somebody pressed, in the same vocabulary the copy's own line uses.
    operation: str
    produced_at: int


class MadeCopies(Wire):
    """Everything made from one file, newest first."""

    copies: list[MadeCopy]


class Produced(Wire):
    """Where a file came from, for the copy's own page.

    `source_asset_id` is null when the original has since been deleted. The copy is still a copy
    and still says so; there is simply nowhere for the link to go.
    """

    asset_id: str
    source_asset_id: str | None = None
    source_filename: str | None = None
    operation: str
    preset: str | None = None
    target_bytes: int | None = None
    produced_at: int
