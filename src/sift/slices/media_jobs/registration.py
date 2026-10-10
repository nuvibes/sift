# SPDX-License-Identifier: AGPL-3.0-or-later
"""Claiming this feature's job types at boot, each bound to what it needs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial

from sift.kernel.config import Settings
from sift.kernel.content import (
    VerdictProduct,
)
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import (
    BACKGROUND_PRIORITY,
    JobContext,
    register_handler,
)
from sift.kernel.jobs.families import Family
from sift.kernel.media import (
    Accelerator,
)
from sift.slices.media_jobs.corrections import reclassify, reidentify
from sift.slices.media_jobs.fingerprints import fingerprint_arrival, fingerprint_stash_box
from sift.slices.media_jobs.job_types import (
    FINGERPRINT_FILE,
    FINGERPRINT_FOR_STASH_BOXES,
    KEEP_PROBES,
    LOOP_THUMBNAIL,
    PREVIEW,
    PROBE,
    READ_UNREAD,
    REBUILD_PREVIEWS,
    REBUILD_THUMBNAILS,
    RECLASSIFY,
    REIDENTIFY,
    REMUX,
    SPRITE,
    THUMBNAIL,
    ChosenShape,
    FollowOnJobs,
    FollowOnPayloads,
    SettlingJobs,
    ShouldGenerate,
)
from sift.slices.media_jobs.previews import preview, rebuild_previews
from sift.slices.media_jobs.probing import keep_probes, probe, read_unread
from sift.slices.media_jobs.repairs import remux
from sift.slices.media_jobs.shared import recording_verdicts
from sift.slices.media_jobs.sprites import ReadRatesFor, sprite
from sift.slices.media_jobs.thumbnails import loop_thumbnail, rebuild_thumbnails, thumbnail

#: The types here that may only have ONE running at a time: two fingerprint passes would read the
#: same page of files and decode the same videos.
_ALONE = frozenset({FINGERPRINT_FOR_STASH_BOXES})

#: What each of this slice's products comes after, in the order a file's work is worth doing: the
#: thumbnail a wall waits on first, then the other pictures somebody sees, then the fingerprints,
#: which only the duplicate sweep and the stash-box ask read. Other features declare their own
#: place where their handlers are registered. A chain rather than a number per type, so a new
#: product names the one it follows instead of renumbering a list.
_ORDER: dict[str, str] = {
    THUMBNAIL: PROBE,
    PREVIEW: THUMBNAIL,
    SPRITE: PREVIEW,
    FINGERPRINT_FILE: SPRITE,
    FINGERPRINT_FOR_STASH_BOXES: SPRITE,
}

#: The work that runs by itself as files arrive (`register_handler`'s `by_itself`): nobody presses
#: it, so a row that heads its own family is left off Activity's Now. The sweeps a start or a
#: press asks for are not in it.
_BY_ITSELF = frozenset(
    {PROBE, THUMBNAIL, LOOP_THUMBNAIL, PREVIEW, SPRITE, REMUX, FINGERPRINT_FILE}
    | {FINGERPRINT_FOR_STASH_BOXES}
)

#: The repaired copy goes last of all work, not last of this slice's pictures: it is a whole second
#: copy of the file that nothing on screen waits for. See `_TRAILS`.
_TRAILING = frozenset({REMUX})

#: What one file of each counted kind is called under its bar on the Activity screen (see
#: `register_handler`'s `counts`).
_COUNTED: dict[str, str] = {
    PROBE: "files read",
    THUMBNAIL: "files with a thumbnail",
    PREVIEW: "files with a hover preview",
    SPRITE: "files with a scrubbing preview",
    FINGERPRINT_FOR_STASH_BOXES: "files fingerprinted",
    FINGERPRINT_FILE: "files fingerprinted",
}


def register_handlers(
    *,
    settings: Settings,
    hardware: HardwareReport,
    should_generate: ShouldGenerate | None = None,
    chosen_shape: ChosenShape | None = None,
    accelerator: Accelerator | None = None,
    follow_on: FollowOnJobs = (),
    follow_on_payloads: FollowOnPayloads | None = None,
    settles_into: SettlingJobs = (),
    fingerprints_settle_into: SettlingJobs = (),
    read_rates: ReadRatesFor | None = None,
) -> None:
    """Claim the job types this feature owns. Called once, at boot.

    The settings and the hardware report are bound here rather than reached for inside a handler,
    so no handler reads a module-level global a test would have to swap. `should_generate` is
    probing's alone and `chosen_shape` the preview's, both read per file. `accelerator` is the
    preview's too, and SHARED with the player: one graphics card, one memory of its faults; a
    test in the composition root proves the real application passes it. And
    `fingerprints_settle_into` is what reads the fingerprints, asked for by whatever wrote them.
    `read_rates` is this machine's measured rates for a file, which let the strip and the
    fingerprints share one decode; the thumbnail and the strip ask `should_generate` whether the
    fingerprints that ride on their decode are wanted.
    """
    _register_probing(
        settings=settings,
        hardware=hardware,
        should_generate=should_generate,
        follow_on=follow_on,
        follow_on_payloads=follow_on_payloads,
        settles_into=settles_into,
    )
    # Each picture is refused under its own verdict; the other two are wrapped by the table below.
    register_handler(
        PREVIEW,
        recording_verdicts(
            partial(
                preview,
                settings=settings,
                hardware=hardware,
                chosen_shape=chosen_shape,
                accelerator=accelerator,
            ),
            VerdictProduct.PREVIEWS,
        ),
        name="Generating preview",
        family=Family.GENERATE,
        follows=_ORDER[PREVIEW],
        counts=_COUNTED[PREVIEW],
        by_itself=True,
    )
    register_handler(
        FINGERPRINT_FILE,
        partial(fingerprint_arrival, settings=settings, settles_into=fingerprints_settle_into),
        name="Fingerprinting file",
        # The product's own family (`PRODUCT_FAMILIES`), which is also where a pressed Generate
        # counts this work, so one fingerprint sits on one row however it was asked for.
        family=Family.FINGERPRINT,
        follows=_ORDER[FINGERPRINT_FILE],
        counts=_COUNTED[FINGERPRINT_FILE],
        by_itself=True,
    )
    register_handler(
        REBUILD_PREVIEWS,
        partial(rebuild_previews, settings=settings, hardware=hardware, chosen_shape=chosen_shape),
        name="Recreating hover previews",
        family=Family.GENERATE,
    )
    _register_the_rest(
        settings=settings,
        hardware=hardware,
        fingerprints_settle_into=fingerprints_settle_into,
        should_generate=should_generate,
        read_rates=read_rates,
    )


def _register_probing(
    *,
    settings: Settings,
    hardware: HardwareReport,
    should_generate: ShouldGenerate | None,
    follow_on: FollowOnJobs,
    follow_on_payloads: FollowOnPayloads | None,
    settles_into: SettlingJobs,
) -> None:
    """The probe's handler, bound to what it reads per file and what follows it."""
    # Probing's verdict is the read itself, wrapped here because the table below does not hold it.
    register_handler(
        PROBE,
        recording_verdicts(
            partial(
                probe,
                settings=settings,
                hardware=hardware,
                should_generate=should_generate,
                follow_on=follow_on,
                follow_on_payloads=follow_on_payloads,
                settles_into=settles_into,
            ),
            VerdictProduct.PROBE,
        ),
        name="Probing file",
        family=Family.SCAN,
        counts=_COUNTED[PROBE],
        by_itself=True,
    )


def _register_the_rest(
    *,
    settings: Settings,
    hardware: HardwareReport,
    fingerprints_settle_into: SettlingJobs,
    should_generate: ShouldGenerate | None = None,
    read_rates: ReadRatesFor | None = None,
) -> None:
    """The handlers bound to the settings and the hardware report alone."""
    for job_type, handler, name, family in (
        (THUMBNAIL, thumbnail, "Generating thumbnail", Family.GENERATE),
        (LOOP_THUMBNAIL, loop_thumbnail, "Generating a still for a loop", Family.GENERATE),
        (SPRITE, sprite, "Generating scrubbing previews", Family.GENERATE),
        (REMUX, remux, "Repackaging a video so it skips smoothly", Family.GENERATE),
        (
            FINGERPRINT_FOR_STASH_BOXES,
            fingerprint_stash_box,
            "Fingerprinting for duplicates",
            Family.FINGERPRINT,
        ),
        (REBUILD_THUMBNAILS, rebuild_thumbnails, "Rebuilding thumbnails", Family.GENERATE),
        (REIDENTIFY, reidentify, "Re-identifying files", Family.OTHER),
        (READ_UNREAD, read_unread, "Reading files that were never read", Family.SCAN),
        (KEEP_PROBES, keep_probes, "Keeping file details", Family.OTHER),
        (RECLASSIFY, reclassify, "Reclassifying files", Family.OTHER),
    ):
        bound: Callable[[JobContext], Awaitable[None]] = partial(
            handler, settings=settings, hardware=hardware
        )
        if job_type == FINGERPRINT_FOR_STASH_BOXES:
            bound = partial(
                fingerprint_stash_box,
                settings=settings,
                hardware=hardware,
                settles_into=fingerprints_settle_into,
            )
        # The two pictures this table claims are refused under their own verdicts.
        if job_type == THUMBNAIL:
            bound = recording_verdicts(
                partial(
                    thumbnail,
                    settings=settings,
                    hardware=hardware,
                    should_generate=should_generate,
                    fingerprints_settle_into=fingerprints_settle_into,
                ),
                VerdictProduct.THUMBNAILS,
            )
        elif job_type == SPRITE:
            bound = recording_verdicts(
                partial(
                    sprite,
                    settings=settings,
                    hardware=hardware,
                    should_generate=should_generate,
                    read_rates=read_rates,
                    fingerprints_settle_into=fingerprints_settle_into,
                ),
                VerdictProduct.SPRITES,
            )
        register_handler(
            job_type,
            bound,
            name=name,
            family=family,
            alone=job_type in _ALONE,
            follows=_ORDER.get(job_type),
            trails=job_type in _TRAILING,
            counts=_COUNTED.get(job_type),
            # ONE URGENCY FOR THE WHOLE FINGERPRINT CHAIN, held at the declaration rather than at
            # each place that asks for it: work nobody is sitting in front of.
            urgency=(BACKGROUND_PRIORITY if job_type == FINGERPRINT_FOR_STASH_BOXES else None),
            by_itself=job_type in _BY_ITSELF,
        )
