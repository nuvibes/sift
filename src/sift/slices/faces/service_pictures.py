# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pictures behind a face: the recognizer's square on disk, and a display-sized cover cut from
its frame."""

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
    """Write a cut picture where it belongs, making its directory. Off the loop."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(picture)


class PicturesMixin(FaceServiceBase):
    """Serving and cutting a face's pictures."""

    async def crop_file(self, track_id: str) -> Path | None:
        """The picture behind one appearance, its clearest face, or None; checks no permission,
        so `may_see_crop` comes first."""
        faces = await self._store.faces_of(track_id)
        if not faces:
            return None
        best = clearest(faces)

        def located() -> Path | None:
            picture = self._store.resolve(best.crop_path)
            return picture if picture.is_file() else None

        return await asyncio.to_thread(located)

    async def cover_file(self, track_id: str) -> Path | None:
        """A display-sized picture of this face, cut from its frame once and kept, or None, where
        the caller falls back to the recognizer's square."""
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
            # A drive not plugged in is the commonest cause: a fallback, never a 500.
            return None
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

        # The box is in the reduced frame's pixels, so it is carried across, read off both frames.
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
