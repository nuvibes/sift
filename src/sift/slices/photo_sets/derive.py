# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rules that make a photo set without anybody asking: a folder of pictures, or an archive."""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.content import ContentStore
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.photo_sets import MIN_PICTURES
from sift.kernel.vocabulary import VIA_STASH_LIBRARY
from sift.slices.photo_sets.service import PhotoSet, PhotoSetService

log = get_logger(__name__)


async def set_from_shoot(
    asset_ids: Sequence[str],
    *,
    name: str,
    content: ContentStore,
    service: PhotoSetService,
) -> str | None:
    """A proposed shoot somebody agreed is one, as a set: the id of the set, or None."""
    made = await _one_set(
        asset_ids,
        name=name,
        origin="shoot",
        url=None,
        content=content,
        service=service,
        why="photo_sets.derived_from_shoot",
    )
    return None if made is None else made.id


async def set_from_stash_library(
    asset_ids: Sequence[str],
    *,
    name: str,
    content: ContentStore,
    service: PhotoSetService,
) -> str | None:
    """A gallery a Stash library kept, as a set of its pictures that are here: the id, or None."""
    made = await _one_set(
        asset_ids,
        name=name,
        origin=VIA_STASH_LIBRARY,
        url=None,
        content=content,
        service=service,
        why="photo_sets.derived_from_stash_library",
    )
    return None if made is None else made.id


async def set_from_post(
    asset_ids: Sequence[str],
    *,
    name: str,
    content: ContentStore,
    service: PhotoSetService,
) -> PhotoSet | None:
    """One post's pictures as a set: the row, so a receipt names what was really made, or None."""
    return await _one_set(
        asset_ids,
        name=name,
        origin="filename",
        url=None,
        content=content,
        service=service,
        why="photo_sets.derived_from_post",
    )


async def _one_set(
    asset_ids: Sequence[str],
    *,
    name: str,
    origin: str,
    url: str | None,
    content: ContentStore,
    service: PhotoSetService,
    why: str,
) -> PhotoSet | None:
    """The body both sequence rules share: guard, create, fill, cover; the guards are the rule."""
    if len(asset_ids) < MIN_PICTURES:
        return None
    stills = await content.stills_among(asset_ids)
    if len(stills) != len(asset_ids):
        return None
    photo_set = await service.create(name=name, origin=origin, origin_url=url)
    # Sift worked this grouping out, so the record says Sift and which rule, by the origin word.
    await service.add(photo_set.id, stills, actor=Actor.sift(origin))
    await service.set_cover(photo_set.id, stills[0], actor=Actor.sift(origin))
    log.info(why, photo_set=photo_set.id, pictures=len(stills))
    return photo_set


async def set_from_folder(
    folder_id: str,
    *,
    name: str,
    content: ContentStore,
    service: PhotoSetService,
) -> str | None:
    """A folder of pictures and nothing else as a set: the id, or None; one video refuses it."""
    media = await content.folder_media(folder_id)
    if media.moving or len(media.still_ids) < MIN_PICTURES:
        return None
    photo_set, added = await service.from_folder(folder_id, name, media.still_ids)
    if added:
        log.info("photo_sets.derived_from_folder", photo_set=photo_set.id, pictures=added)
    return photo_set.id


async def set_from_archive(
    asset_ids: Sequence[str],
    *,
    root_id: str,
    rel_path: str,
    name: str,
    content: ContentStore,
    service: PhotoSetService,
) -> str | None:
    """One archive's pictures as a set, keyed on library and path: the id, or None."""
    if len(asset_ids) < MIN_PICTURES:
        return None
    stills = await content.stills_among(asset_ids)
    if len(stills) != len(asset_ids):
        return None
    photo_set, added = await service.from_archive(root_id, rel_path, name, stills)
    if added:
        log.info("photo_sets.derived_from_archive", photo_set=photo_set.id, pictures=added)
    return photo_set.id
