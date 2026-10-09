# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the compression panel sends and what it gets back, answered per file."""

from __future__ import annotations

from pydantic import Field, model_validator

from sift.kernel.filenames import MAX_FILENAME_LENGTH
from sift.kernel.wire import Wire
from sift.slices.media_edit.operations import ON_MOVING, Operation, Turn
from sift.slices.media_edit.settings import Preset
from sift.slices.media_edit.tuning import MAX_BULK_ASSETS, MAX_EDIT_STEPS


class CompressRequest(Wire):
    """What to compress, and to what: a size target, a compatibility target, or both."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    #: None asks only for compatibility.
    preset: Preset | None = None
    custom_target_mb: int | None = Field(default=None, ge=1)
    compatibility: bool = False
    #: Go ahead on files Sift has said cannot meet the target.
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
    output_filename: str | None = None
    reachable: bool
    predicted_bytes: int | None = None
    smallest_reachable_bytes: int | None = None
    reason: str | None = None
    #: Already meets the target and already plays anywhere.
    copy_only: bool = False
    #: Container changed, streams copied.
    rewrap_only: bool = False
    #: The one case where the sound is touched at all.
    converts_audio: bool = False
    #: Not something this can act on at all; the verb should not have been offered.
    skip_reason: str | None = None


class Preflight(Wire):
    """The whole answer for a selection: every file, and the few numbers worth showing above them."""

    target_bytes: int | None = None
    files: list[FileVerdict]
    eligible_count: int
    unreachable_count: int
    copy_only_count: int
    audio_conversion_count: int
    #: The smallest target every file here could meet, offered beside the warning.
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
    """One thing to do to the picture, measured in the picture as it is SEEN."""

    operation: Operation
    #: Crop only: the rectangle to keep, from the top left.
    left: int | None = Field(default=None, ge=0)
    top: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    #: Rotate only; a mirror is one of these.
    turn: Turn | None = None
    #: Trim and clip, in milliseconds.
    start_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _has_what_it_needs(self) -> EditStep:
        """Refuse a step that names an operation and not the numbers it runs on."""
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
    """One Save of one file: ordered steps that produce one copy, or nothing if any is refused."""

    steps: list[EditStep] = Field(min_length=1, max_length=MAX_EDIT_STEPS)
    #: The stem only; the extension follows what the copy is encoded as.
    filename: str | None = Field(default=None, max_length=MAX_FILENAME_LENGTH)
    #: Also mark the produced file as a Loop. A flag, never a job name, so a caller cannot pick the
    #: handler.
    as_loop: bool = False

    @model_validator(mode="after")
    def _a_cut_travels_alone(self) -> EditRequest:
        """A cut copies packets and a filter needs decoded frames, so a cut is the whole request or
        none of it."""
        cuts = [step for step in self.steps if step.operation in ON_MOVING]
        if cuts and len(self.steps) > 1:
            raise ValueError("a trim or a clip is done on its own")
        return self


class EditFrame(Wire):
    """How big the picture is as somebody SEES it, asked once when the editor opens."""

    asset_id: str
    width: int | None = None
    height: int | None = None


class EditVerdict(Wire):
    """What this edit would do, before it does it. The panel asks for one every time it changes."""

    asset_id: str
    allowed: bool
    reason: str | None = None
    output_filename: str | None = None
    #: Set for one format only: a HEIC comes back as a JPEG.
    converted_from: str | None = None
    #: Saving costs a generation of quality.
    lossy: bool = False
    #: Crops only: the rectangle really cut, rounded to what the picture's colour blocks allow.
    left: int | None = None
    top: int | None = None
    width: int | None = None
    height: int | None = None
    #: The picture as it is SEEN, which everything above is measured against.
    frame_width: int | None = None
    frame_height: int | None = None
    #: The copy's size after every step.
    result_width: int | None = None
    result_height: int | None = None
    #: Cuts only: a stream copy may start early, at the nearest frame that stands alone. Never late.
    approximate_start: bool = False


class EditStarted(Wire):
    """The job doing the work. One file, so one job."""

    job_id: str
    output_filename: str


class MadeCopy(Wire):
    """One copy made from a file, for the original's page; copies the user may not see are left out."""

    asset_id: str
    filename: str
    operation: str
    produced_at: int


class MadeCopies(Wire):
    """Everything made from one file, newest first."""

    copies: list[MadeCopy]


class Produced(Wire):
    """Where a file came from, for the copy's own page; the source is null once deleted."""

    asset_id: str
    source_asset_id: str | None = None
    source_filename: str | None = None
    operation: str
    preset: str | None = None
    target_bytes: int | None = None
    produced_at: int
