# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a cover sits in its frame: the window of its picture an entity is drawn as.

Its own module rather than a part of `kernel.covers`, and the reason is the dependency direction:
the entity VIEWS carry a cover's frame to every screen, those views are built inside
`kernel.access`, and `kernel.covers` imports `kernel.access`. So the shape and the two functions
that store and read it sit below both, importing nothing of either.
"""

from __future__ import annotations

import json

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from sift.kernel.wire import Wire

# --- how a cover sits in its frame ---------------------------------------------------------------
#
# A cover is drawn in a portrait box. Left to the box, WHICH PART of the picture fills it is the
# middle, cut to fit, and a face near the top of a landscape still loses the top of its head on
# every card. So a cover can carry a FRAME: the window of the picture it is drawn as, the way a
# phone frames a profile picture: a zoom and a position, chosen by a person, applied here when the
# picture is sent.
#
# FRACTIONS OF THE PICTURE, never pixels. The same cover is served from a still 480 tall, from an
# uploaded picture 720 tall, and from a moment's still that may be rebuilt at another size; a window
# in pixels would be right for one of them. A fraction is right for all three.

#: The smallest side a frame may have, as a fraction of the picture. A frame is a person's choice
#: and a window a hundredth of the picture across is already a zoom nobody asks for on purpose; the
#: floor is what stops a crop of one pixel, which ffmpeg refuses and the screen could not show.
FRAME_SMALLEST = 0.01

#: How far past an edge a frame may reach before it is refused rather than pulled back. A client
#: working in floating point lands a hair outside the picture as often as inside it; that is pulled
#: in. Anything further is a frame that is not inside the picture, and says so.
_FRAME_SLACK = 1e-3

#: Every stored and sent fraction is rounded to this many places, so the address token the client
#: composes from the view (`coverToken` in `lib/entity/art.ts`) and the one the server compares it with
#: are made from the SAME digits. Unrounded, one side's 0.12345 is the other's 0.1234 or 0.1235
#: depending on whose rounding rule ran, and the promise a kept picture makes is refused for ever.
_FRAME_PLACES = 4


class CoverFrame(Wire):
    """The window of a cover's picture that it is drawn as: `x`, `y`, `w`, `h`, each 0..1.

    `x` and `y` are the window's top-left corner and `w` and `h` its size, all as fractions of the
    picture's own width and height. The whole picture is `(0, 0, 1, 1)`, and no frame at all means
    the same thing: every cover chosen before there were frames is drawn exactly as it was.

    One shape for the three places a frame travels (the PUT that writes it, the view that
    carries it to the screens, and the row that stores it) because three copies of four numbers
    and their bounds is two chances to accept on the way in what is refused on the way out.

    The window's SHAPE is not checked here, and that is deliberate: the server does not know how
    wide the picture is without reading it, and a frame of another shape is still a window of the
    picture: the box it is drawn in cuts it to fit, exactly as it cut the whole picture before.
    The editor keeps the window at the box's shape; this keeps it inside the picture.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)

    @field_validator("x", "y", "w", "h")
    @classmethod
    def _rounded(cls, value: float) -> float:
        return round(value, _FRAME_PLACES)

    @model_validator(mode="after")
    def _inside(self) -> CoverFrame:
        if self.w < FRAME_SMALLEST or self.h < FRAME_SMALLEST:
            raise ValueError("A frame has to be at least a hundredth of the picture on each side.")
        if self.x + self.w > 1.0 + _FRAME_SLACK or self.y + self.h > 1.0 + _FRAME_SLACK:
            raise ValueError("A frame has to be inside the picture.")
        # Pulled in by the slack, never further: see `_FRAME_SLACK`. Written with
        # `object.__setattr__` because the model is frozen, and frozen is what makes it safe to
        # share between a view and the row it came from.
        if self.x + self.w > 1.0:
            object.__setattr__(self, "w", round(1.0 - self.x, _FRAME_PLACES))
        if self.y + self.h > 1.0:
            object.__setattr__(self, "h", round(1.0 - self.y, _FRAME_PLACES))
        return self

    @property
    def is_whole(self) -> bool:
        """Whether this window is the whole picture, which is the same instruction as no frame."""
        return self.x == 0.0 and self.y == 0.0 and self.w == 1.0 and self.h == 1.0

    @property
    def token(self) -> str:
        """What this frame adds to its cover's address: `f` and the four in ten-thousandths.

        The client composes the same string from the same rounded numbers (`frameToken` in
        `lib/entity/cover-frame.ts`), so a reframe moves the address by itself (see `names_its_cover`).
        """
        parts = (round(one * 10**_FRAME_PLACES) for one in (self.x, self.y, self.w, self.h))
        return "f" + "-".join(str(one) for one in parts)

    def crop(self) -> str:
        """The ffmpeg filter that cuts this window out of a picture of any size.

        In terms of `iw` and `ih` rather than pixels, so the one filter is right for a still 480
        tall and an upload 720 tall alike. ffmpeg rounds the window to whole pixels, and to the
        chroma grid for a subsampled picture, which moves it by at most a pixel, invisibly.
        """
        return f"crop=w=iw*{self.w}:h=ih*{self.h}:x=iw*{self.x}:y=ih*{self.y}"


def picture_named(*, asset_id: str | None, at_ms: int | None, upload_id: str | None) -> str | None:
    """Which picture a cover's pointers name, as one string: what a stored frame is bound to.

    An upload wins, exactly as it does when the picture is served; a file names its moment too,
    because two moments of one clip are two different pictures and a window chosen on one of them
    is not a window of the other. None where the row names no picture at all.
    """
    if upload_id is not None:
        return f"upload:{upload_id}"
    if asset_id is not None:
        return f"asset:{asset_id}@{'' if at_ms is None else at_ms}"
    return None


def stored_frame(
    frame: CoverFrame | None,
    *,
    asset_id: str | None,
    at_ms: int | None,
    upload_id: str | None,
) -> str | None:
    """What goes in an entity's `cover_frame` column: the window, and WHICH PICTURE it is a window of.

    ## Why the picture is written beside the window

    Five statements write a cover's pointers with a frame in hand, and at least seven more change
    them without one: a face pass giving somebody their first cover, a merge moving a cover onto a
    survivor, a stash-box filling a gap, a collection losing the item its cover was, and SQLite
    itself: `ON DELETE SET NULL` empties a cover when its file is deleted, and no statement runs
    at all. Every one of those would leave a window chosen on one picture laid over the next, and
    teaching seven writers (and a foreign key) to clear a column they have never heard of is seven
    places to forget it.

    So a frame names the picture it was chosen on, and `frame_of` honours it ONLY while the row
    still names that picture. Any writer that moves the cover (including one written next year
    that knows nothing of frames) turns the old window off simply by moving it. The column can
    then hold a stale window, which is inert and costs a few bytes, rather than a wrong picture.

    None, and so no stored window, for the whole picture as well as for no frame: they are the same
    instruction, and one spelling of it is one fewer thing for a reader to compare.
    """
    picture = picture_named(asset_id=asset_id, at_ms=at_ms, upload_id=upload_id)
    if frame is None or frame.is_whole or picture is None:
        return None
    return json.dumps(
        {"of": picture, "x": frame.x, "y": frame.y, "w": frame.w, "h": frame.h},
        separators=(",", ":"),
    )


def frame_of(
    raw: object,
    *,
    asset_id: str | None,
    at_ms: int | None,
    upload_id: str | None,
) -> CoverFrame | None:
    """The window a row's `cover_frame` holds, if it is a window of the picture the row now names.

    None for no frame, for a frame of another picture (see `stored_frame` for why that is the rule),
    and for anything that does not read as one: a column is data, and a restored backup or a
    hand-edited database is exactly where a row that predates a guard meets the code that trusts it.
    A frame that cannot be read is the whole picture, which is how the cover was drawn before.

    Read with the pointers the reader was GIVEN, never re-read here: a view whose file was withheld
    from this viewer has already had `cover_asset_id` taken off, so the frame goes with it and says
    nothing about a file the viewer may not see.
    """
    if not isinstance(raw, str) or not raw:
        return None
    picture = picture_named(asset_id=asset_id, at_ms=at_ms, upload_id=upload_id)
    if picture is None:
        return None
    try:
        said = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(said, dict) or said.get("of") != picture:
        return None
    try:
        frame = CoverFrame.model_validate({key: said.get(key) for key in ("x", "y", "w", "h")})
    except ValidationError:
        return None
    return None if frame.is_whole else frame
