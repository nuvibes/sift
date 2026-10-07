# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pictures behind a face: the recognizer's square on disk, and a display-sized cover cut from
the frame the face was found in.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from sift.kernel.media import NoReadableCopy, resolve_decodable
from sift.slices.faces import crop as cropping
from sift.slices.faces import frames, tuning
from sift.slices.faces.frames import Reader
from sift.slices.faces.models import Box
from sift.slices.faces.service_base import FaceServiceBase
from sift.slices.faces.store import clearest

if TYPE_CHECKING:  # numpy is only needed for a signature
    import numpy as np


def _place(destination: Path, picture: bytes) -> None:
    """Write a cut picture where it belongs, making the directory for it. Off the loop. See the
    face store, where the same pair of calls is made for a whole pass in one go."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(picture)


class PicturesMixin(FaceServiceBase):
    """Serving and cutting a face's pictures."""

    async def crop_file(self, track_id: str) -> Path | None:
        """The picture behind one appearance, on disk. None when there is nothing to serve.

        **Says nothing about permission and is not to be called before `may_see_crop`.** It is
        split that way on purpose: a function that both checked and fetched would be one whose
        callers could not be read at a glance to see whether the check happened, and this is the
        surface where that matters most.

        The clearest of the appearance's few faces, chosen exactly as matching chooses it, so the
        picture a screen shows is the one the decision was made from.
        """
        faces = await self._store.faces_of(track_id)
        if not faces:
            return None
        best = clearest(faces)

        def located() -> Path | None:
            picture = self._store.resolve(best.crop_path)
            return picture if picture.is_file() else None

        return await asyncio.to_thread(located)

    async def cover_file(self, track_id: str) -> Path | None:
        """A display-sized picture of this face, cut from the frame it was found in.

        Cut once and kept. The aligned square the recognizer reads is 112 pixels across and cropped
        to the eyes, nose and mouth: a measurement, which blown up as a cover could not look like
        anything else.

        Cut here rather than when the cover is chosen, so a cover set earlier is fixed by being
        looked at instead of by having to be set again. The cost is one decode, once, per face that
        is ever used as a cover.

        None when there is nothing to cut from: no such face, the file has gone, or the decoder
        could not produce the frame. The caller falls back to the recognizer's square, which is
        better than a hole.
        """
        await self._require_enabled()
        stored = self._store.cover_path(track_id)
        if await asyncio.to_thread(stored.is_file):
            return stored

        track = await self._store.track(track_id)
        if track is None:
            return None
        faces = await self._store.faces_of(track_id)
        if not faces:
            return None
        best = clearest(faces)

        try:
            source = await resolve_decodable(self._content, track.asset_id, settings=self._settings)
        except (LookupError, NoReadableCopy):
            # A drive that is not plugged in is the commonest of these and is not an error worth
            # raising here: the caller falls back to the recognizer's square. A cover is worth one
            # decode, never a 500.
            return None
        # No guard on the kind of file. Every asset is a video, an image or a GIF (the
        # table refuses anything else), so a check here would be a branch nothing can reach and a
        # reader of this function would reasonably assume it can.
        asset = source.asset
        reader = Reader(self._settings)
        picture: np.ndarray | None = None
        async for frame in reader.stream(
            source.path,
            media_type=asset.media_type,
            width=asset.width or 0,
            height=asset.height or 0,
            timestamps=[best.timestamp_ms],
            long_side=tuning.COVER_LONG_SIDE,
        ):
            picture = frame.pixels
            break
        if picture is None:
            return None

        # The box was found in a frame reduced to the pass's size, and this one was decoded
        # larger, so it has to be carried across before it means anything here. Read off the two
        # frames rather than recomputed from the settings: the number that matters is what this
        # picture actually is, and a second derivation of it is a second thing to keep in step.
        scanned_width, _ = frames.output_size(asset.width or 0, asset.height or 0)
        scale = (picture.shape[1] / scanned_width) if scanned_width else 1.0
        box = Box(
            x=int(best.box.x * scale),
            y=int(best.box.y * scale),
            width=max(1, int(best.box.width * scale)),
            height=max(1, int(best.box.height * scale)),
        )

        written = await cropping.encode_portrait(cropping.portrait(picture, box), self._settings)
        if written is None:
            return None
        await asyncio.to_thread(_place, stored, written)
        return stored
