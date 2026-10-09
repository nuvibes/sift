# SPDX-License-Identifier: AGPL-3.0-or-later
"""Everything that removes, moves or produces a file."""

from __future__ import annotations

import time

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.config import Settings
from sift.kernel.content import DuplicateReads
from sift.kernel.jobs import JobQueue
from sift.kernel.wiring import provide
from sift.slices import (
    dedup,
    delete,
    library_roots,
    loops,
    media_edit,
    media_jobs,
    organize,
    player,
    settings_hub,
    workbench,
)
from sift.wiring.built import Storage


def _build_media_edit(
    app: FastAPI,
    settings: Settings,
    store: Storage,
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    organizer: organize.Organizer,
    duplicates: dedup.DedupService,
) -> None:
    # Producing a file: written only through the organizer seam, which never overwrites.
    compressor = media_edit.CompressService(
        store.database,
        store.access,
        queue,
        organizer,
        hub.get_app,
        clock=time.time,
    )
    # Cropping, resizing, rotating and cutting, through the same three seams.
    editor = media_edit.EditService(
        store.database,
        store.access,
        queue,
        organizer,
        # The content store, for the camera's orientation note on the file itself.
        store.content,
        settings,
        hub.get_app,
        clock=time.time,
    )
    provide(app, media_edit.COMPRESSOR, compressor)
    provide(app, media_edit.EDITOR, editor)
    media_edit.register_handlers(
        settings=settings,
        access=store.access,
        writer=organizer,
        service=compressor,
        editor=editor,
        duplicates=duplicates,
        reindexer=wiring.part_of_app(app, wiring.REINDEXER),
        database=store.database,
        # What a newly produced file is worth starting: the probe, another feature's job type.
        follow_on=(media_jobs.PROBE,),
        # What "Save as Loop" means, named here so neither slice imports the other.
        also_if_asked=(loops.LOOP_WHOLE,),
    )


def build_file_actions(
    app: FastAPI,
    settings: Settings,
    store: Storage,
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    recorder: workbench.Store,
) -> None:
    """Everything that removes, moves or produces a file, each ability handed to who needs it."""
    # The one thing in Sift that removes a file; nothing else may unlink.
    deleter = delete.Deleter(
        store.database,
        store.content,
        store.library,
        store.access,
        wiring.part_of_app(app, player.SEGMENT_CACHE),
        wiring.part_of_app(app, library_roots.SERVICE),
    )
    provide(app, delete.DELETER, deleter)

    # The one thing in Sift that renames or moves a file somebody else put there.
    organizer = organize.Organizer(store.database, store.content, store.library, store.access)
    provide(app, organize.ORGANIZER, organizer)
    organize.register_handlers(
        organizer=organizer,
        database=store.database,
        recorder=recorder,
        access=store.access,
        touched=wiring.part_of_app(app, wiring.REINDEXER).touched_many,
    )

    # Duplicate-finding, handed the deleter so it can remove a copy no other way.
    duplicates = dedup.DedupService(
        store.database,
        DuplicateReads(store.database),
        _RemovalSeam(deleter),
        # Written inside the transaction that settles the pair, so it can be taken back.
        recorder=recorder,
    )
    provide(app, dedup.SERVICE, duplicates)
    dedup.register_handlers(service=duplicates)

    _build_media_edit(app, settings, store, queue, hub, organizer, duplicates)


class _RemovalSeam:
    """The deleter in the shape duplicate-finding asked for, translating its refusals to HTTP."""

    def __init__(self, deleter: delete.Deleter) -> None:
        self._deleter = deleter

    async def remove(
        self,
        asset_id: str,
        *,
        mode: delete.Mode,
        actor: Viewer,
        location_id: str | None = None,
    ) -> None:
        try:
            await self._deleter.remove(asset_id, mode=mode, actor=actor, location_id=location_id)
        except delete.NotFound as refused:
            raise dedup.NotFound(str(refused)) from refused
        except delete.NotAllowed as refused:
            raise dedup.NotAllowed(str(refused)) from refused
        except delete.DeleteRefused as refused:
            raise dedup.RemovalRefused(str(refused)) from refused
