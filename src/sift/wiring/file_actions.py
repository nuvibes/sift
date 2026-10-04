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
    # Making a smaller copy of a file, or one that plays anywhere. The first feature in Sift that
    # PRODUCES a file rather than reading one, and it is wired with that in mind.
    #
    # It is handed the organizer as a write seam, not as itself. The seam's whole promise is that a
    # produced file is claimed into a name nothing else holds, so this feature cannot write over
    # anything, and cannot be changed into something that can without changing the seam. It is
    # handed the duplicate service the same way, because a compressed copy resembles its original by
    # every measure duplicate detection has, and saying so at the moment it is made is what stops
    # forty copies becoming forty questions.
    #
    # The settings reader is a function rather than the settings feature: the four target sizes are
    # editable numbers, read at the moment somebody presses the button so an edit takes effect on
    # the next press rather than the next restart.
    compressor = media_edit.CompressService(
        store.database,
        store.access,
        queue,
        organizer,
        hub.get_app,
        clock=time.time,
    )
    # The other half of the same feature: cropping, resizing and rotating a photograph, and taking
    # a piece out of a video. It is given the same three seams for the same three reasons: it
    # produces a file, so it writes through the organizer and can no more overwrite than the
    # compressor can, and what it produces resembles the file it came from just as closely.
    #
    # It has ONE number to read: which format a GIF is written in, a setting because the three
    # differ in size and in nothing else that matters, read at the moment of asking.
    editor = media_edit.EditService(
        store.database,
        store.access,
        queue,
        organizer,
        # The content store, because the editor reads one thing off the file itself: the note a
        # camera leaves saying which way up a photograph goes. Everything else it answers comes
        # from what the probe already recorded.
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
        # What a newly produced file is worth starting. The probe, which works out what the file is
        # and starts the thumbnail, the preview and the strip itself. Named from out here because
        # a feature never imports another, and it is another feature's job type.
        follow_on=(media_jobs.PROBE,),
        # What "Save as Loop" means, named HERE and nowhere else. The editor cuts the clip and
        # knows only that the request asked for something extra; this is the only place that knows
        # the something is a loop. Neither slice imports the other: the same rule the still that a
        # mark asks for already follows.
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
    """Everything that removes, moves or produces a file.

    Three of them are the only things in Sift permitted to do what they do, and each is handed to
    whoever needs the ability rather than being reachable directly. That is what keeps the ability
    bounded: a feature that can ask for a copy to be removed still cannot unlink anything.
    """
    # The one thing in Sift that removes a file. Held on the application so a feature that needs
    # something gone (clearing a redundant copy, reclaiming space) calls it rather than
    # unlinking, which nothing else in the codebase is permitted to do.
    # Handed the segment cache rather than reaching for it, exactly as duplicate-finding is handed
    # the deleter below: what it needs is one verb (throw away the transcoded pieces of a file
    # that has ended), and being given it is what keeps this feature unable to touch playback any
    # other way. Playback is wired before this, so the cache is already on the application.
    deleter = delete.Deleter(
        store.database,
        store.content,
        store.library,
        store.access,
        wiring.part_of_app(app, player.SEGMENT_CACHE),
        # The scanner's memory: forgetting a picture inside an archive is written there.
        wiring.part_of_app(app, library_roots.SERVICE),
    )
    provide(app, delete.DELETER, deleter)

    # The one thing in Sift that renames or moves a file somebody else put there. Same rule as the
    # deleter: held here so anything needing a file moved asks for it, and a rule in the build
    # refuses any other code that renames or moves one at all.
    organizer = organize.Organizer(store.database, store.content, store.library, store.access)
    provide(app, organize.ORGANIZER, organizer)
    # A batch rename above the in-request size runs as a task, and its receipt is taken back
    # file by file through the same organizer; the reindexer was provided with the downloads.
    organize.register_handlers(
        organizer=organizer,
        database=store.database,
        recorder=recorder,
        access=store.access,
        touched=wiring.part_of_app(app, wiring.REINDEXER).touched_many,
    )

    # Duplicate-finding. It is handed the deleter rather than reaching for it, because the seam it
    # depends on is "something that can remove a copy", and being handed it is what keeps this
    # feature unable to remove one any other way. The content reads it gets are the two the kernel
    # allows it: what might look alike, and what already sits in more than one place.
    duplicates = dedup.DedupService(
        store.database,
        DuplicateReads(store.database),
        _RemovalSeam(deleter),
        # Reviewing a near duplicate is a judgement, so it goes in the record with the rest of them
        # and can be taken back. Written inside the transaction that settles the pair, which is why
        # the service is handed the recorder rather than the route writing a receipt afterwards.
        recorder=recorder,
    )
    provide(app, dedup.SERVICE, duplicates)
    dedup.register_handlers(service=duplicates)

    _build_media_edit(app, settings, store, queue, hub, organizer, duplicates)


class _RemovalSeam:
    """The deleter, wearing the shape duplicate-finding asked for.

    Duplicate-finding depends on removal as a protocol rather than on the feature that implements
    it, which is what lets it be tested against something that records instead of deletes, and
    what stops it importing a sibling feature. The one thing a protocol cannot carry is which
    exceptions come back, so the translation happens here, where the two are wired together and
    both are already in scope.

    Without it the delete feature's refusals travel up through a router that has never heard of
    them and land on the generic handler: a read-only folder answers 500 instead of 403, which
    reads as "Sift is broken" rather than "that drive is read-only".
    """

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
