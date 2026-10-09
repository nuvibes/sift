# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a cover sits in its frame, below both `kernel.access` and `kernel.covers`."""

from __future__ import annotations

import json

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from sift.kernel.wire import Wire

# A frame is in fractions of the picture, never pixels: one cover is served at several sizes.

#: Stops a crop of one pixel, which ffmpeg refuses.
FRAME_SMALLEST = 0.01

#: Floating-point drift past an edge is pulled in; anything further is refused.
_FRAME_SLACK = 1e-3

#: Both sides round to these places, so the client's `coverToken` matches the server's.
_FRAME_PLACES = 4


class CoverFrame(Wire):
    """The window of a cover's picture it is drawn as: `x`, `y`, `w`, `h`, each 0..1."""

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
        # Pulled in by the slack only; the model is frozen, hence `object.__setattr__`.
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
        """`f` and the four in ten-thousandths, matching the client's `frameToken`."""
        parts = (round(one * 10**_FRAME_PLACES) for one in (self.x, self.y, self.w, self.h))
        return "f" + "-".join(str(one) for one in parts)

    def crop(self) -> str:
        """The ffmpeg filter that cuts this window from a picture of any size."""
        return f"crop=w=iw*{self.w}:h=ih*{self.h}:x=iw*{self.x}:y=ih*{self.y}"


def picture_named(*, asset_id: str | None, at_ms: int | None, upload_id: str | None) -> str | None:
    """Which picture a cover's pointers name, as the string a stored frame is bound to."""
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
    """The window and the picture it was chosen on, so moving the cover retires the window."""
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
    """The window a row holds if it is of the picture the row now names; unreadable is None."""
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
