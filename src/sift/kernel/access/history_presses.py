# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who pressed the passes over a file, and the line each press says where no pass line claims it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache

from sift.kernel import presses
from sift.kernel.access import sentences as say
from sift.kernel.access.history_actors import _names_of, _Who
from sift.kernel.access.history_events import (
    FEED_FOLD_GAP,
    LedgerEvent,
    presses_of_asset,
)
from sift.kernel.access.history_line import Actor, Event, by_of
from sift.kernel.access.sentences import SIFT
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, point_read

#: How far outside a press's stretch a pass's own row may be dated and still be that press's work,
#: in seconds: the clock on a machine can step backwards by a few seconds, and a pass may date
#: its row a moment before the worker took its job.
PRESS_SLACK = 60


@dataclass(frozen=True, slots=True)
class Press:
    """One press of a pass over one file, as the ledger keeps it (`kernel.presses`): the act's id,
    who pressed, the passes the job ran, and the stretch its work ran in."""

    id: str
    user_id: str
    passes: tuple[str, ...]
    began: int
    ended: int


def press_of(event: LedgerEvent) -> Press | None:
    """The press a `pressed` act records, or None for an act whose payload says no pass."""
    payload = say.payload_of(event.payload)
    passes = payload.get(presses.PASSES)
    began = payload.get(presses.BEGAN)
    if event.actor_id is None or not isinstance(passes, list):
        return None
    named = tuple(one for one in passes if isinstance(one, str))
    if not named:
        return None
    return Press(
        id=event.id,
        user_id=event.actor_id,
        passes=named,
        began=min(began, event.at) if isinstance(began, int) else event.at,
        ended=event.at,
    )


@dataclass(frozen=True, slots=True)
class Pressers:
    """Who pressed which pass on one file, as one reader may be told it. Empty: nobody pressed any.

    A pass's line names its presser where the pass's own row was written inside the stretch the
    pressed job ran in (`of`); a row outside it reads as Sift. Who is said by `_Who`. `claimed` is
    the presses a pass line has said so far; the rest are lines of their own (`press_lines`).
    """

    presses: Sequence[Press] = ()
    who: _Who | None = None
    claimed: set[str] = field(default_factory=set)

    def of(self, said_by: str, at: int | None) -> tuple[Actor, str | None] | None:
        """Who pressed the pass whose line is read off `said_by` (a History read, or a ledger act
        as `ledger:<verb>`) and dated `at`, as an actor and a name; None where Sift ran it."""
        if at is None or self.who is None:
            return None
        passes = passes_said_by(said_by)
        found: Press | None = None
        for press in self.presses:
            if passes.isdisjoint(press.passes):
                continue
            if not press.began - PRESS_SLACK <= at <= press.ended + PRESS_SLACK:
                continue
            if found is None or press.ended > found.ended:
                found = press
        if found is None:
            return None
        self.claimed.add(found.id)
        return self.who.of(found.user_id)

    def of_verdict(self, product: str, at: int | None) -> tuple[Actor, str | None] | None:
        """Who pressed the pass that gave up on this file, by the product its verdict names."""
        said_by = verdict_said_by(product)
        return None if said_by is None else self.of(said_by, at)


#: The answer where nobody pressed anything; safe to share, since nothing is ever added to `claimed`.
NOBODY_PRESSED = Pressers()


async def pressers_of(
    database: Database, viewer: Viewer, asset_id: str, *, present: set[str]
) -> Pressers:
    """Who pressed the passes on this file, read once for the whole pane. Empty where nothing is
    recorded, and where the record is not there at all (a process with no ledger)."""
    if not {"workbench_decisions", "workbench_decision_subjects"} <= present:
        return NOBODY_PRESSED
    found = [press_of(one) for one in await presses_of_asset(database, viewer, asset_id)]
    pressed = [one for one in found if one is not None]
    if not pressed:
        return NOBODY_PRESSED
    names = await _names_of(database, [one.user_id for one in pressed])
    return Pressers(presses=pressed, who=_Who(viewer=viewer, names=names))


def press_lines(pressers: Pressers) -> list[Event]:
    """Every press no pass line said, as a line of its own: "You had Sift look for faces in this
    file". Presses of the same passes by one user within `FEED_FOLD_GAP` fold into one line. A pass's
    row keeps only its latest result, so the line says the press and no more.
    """
    if pressers.who is None:
        return []
    left = sorted(
        (one for one in pressers.presses if one.id not in pressers.claimed),
        key=lambda one: (one.ended, one.id),
    )
    runs: list[list[Press]] = []
    for press in left:
        last = runs[-1][-1] if runs else None
        if (
            last is not None
            and (last.user_id, last.passes) == (press.user_id, press.passes)
            and press.ended - last.ended <= FEED_FOLD_GAP
        ):
            runs[-1].append(press)
        else:
            runs.append([press])
    lines: list[Event] = []
    for run in runs:
        newest = run[-1]
        actor, name = pressers.who.of(newest.user_id)
        lines.append(
            Event(
                at=newest.ended,
                actor=actor,
                actor_name=name,
                kind="pressed",
                pieces=say.pressed_here(by_of(actor, name) or SIFT, newest.passes, len(run)),
            )
        )
    return lines


def by_pressed(pressed: tuple[Actor, str | None] | None) -> tuple[Actor, str | None, str | None]:
    """A pass line's actor, its name and the presser's word for the sentence (`by_of`): Sift's own
    and no word where nobody pressed it."""
    if pressed is None:
        return Actor.SIFT, SIFT, None
    actor, name = pressed
    return actor, name, by_of(actor, name)


#: WHAT A PASS GAVE UP ON for this file, one row per product, so a file a pass could not do reads
#: differently from one nothing tried.
_LEFT_OUT = point_read(
    "history.left_out",
    "SELECT product, transient, at FROM file_verdicts WHERE asset_id = ?",
)

# --- WHAT SAYS THAT A PASS RAN ON A FILE ---------------------------------------------------------
#
# Each pass names the reads, or the ledger's verbs, that say on the file that it ran, its empty
# answer included, so no pass runs in silence (`test_every_pass_says_so_on_a_files_history.py`).
# Keyed by the pass's own key; a read by its registered name, a ledger act as `ledger:<verb>`.

#: The reads and acts that say a pass ran on a file, by the pass.
SAID_ON_A_FILE: Mapping[str, tuple[str, ...]] = {
    # The pictures, as products and as jobs, and the copies Sift makes to play a file.
    "thumbnails": ("history.derivatives", "history.left_out"),
    "previews": ("history.derivatives", "history.left_out"),
    "sprites": ("history.derivatives", "history.left_out"),
    "thumbnail": ("history.derivatives", "history.left_out"),
    "preview": ("history.derivatives", "history.left_out"),
    "sprite": ("history.derivatives", "history.left_out"),
    # A Loop's still is a still of the file at the Loop's moment, kept with the file's pictures.
    "loop_thumbnail": ("history.derivatives",),
    "remux": ("history.derivatives",),
    # The fingerprints: the act `identity.record_fingerprints` records, or the verdict. An arriving
    # file's are part of taking it in and say nothing of their own.
    "fingerprints": ("ledger:scanned", "history.left_out"),
    "fingerprint_file": ("ledger:scanned", "history.left_out"),
    "fingerprint_stash_box": ("ledger:scanned", "history.left_out"),
    # The music fingerprint, made or empty.
    "music": ("history.music_fingerprint",),
    "audio_fingerprint": ("history.music_fingerprint",),
    # AcoustID asked about the file's music: its answer, or the song it named on the file.
    "music_lookup": ("history.music_lookup", "ledger:song_named"),
    # The three looks that identify a file: found, not found, or could not look.
    "faces": ("ledger:face_run", "history.face_scan", "history.left_out"),
    "face_scan": ("ledger:face_run", "history.face_scan", "history.left_out"),
    "meaning": ("history.indexed", "history.left_out"),
    "semantic_describe": ("history.indexed", "history.left_out"),
    "watermarks": ("history.watermark_scan", "history.watermark_read", "history.left_out"),
    "watermark_read": ("history.watermark_scan", "history.watermark_read", "history.left_out"),
    # A file's details read again from disk: Run task's File details, and the arriving file's own
    # first job, whose read is its arrival line.
    "details": ("history.details_read", "history.left_out"),
    "probe": ("history.details_read", "history.left_out"),
    # Enrich on a file: the box's answer applied, nothing matched, or a match waiting.
    "stash_box_scan": ("history.enriched", "history.asked", "history.asked_waiting"),
}


@cache
def passes_said_by(said_by: str) -> frozenset[str]:
    """Every pass whose line is read off this source, by the passes' declaration above: the keys a
    press is recorded under (`kernel.presses`) for the line drawn from it."""
    return frozenset(name for name, sources in SAID_ON_A_FILE.items() if said_by in sources)


def verdict_said_by(product: str) -> str | None:
    """The source a pass's own line is read off, for the product a verdict of it names: the line
    a pass that gave up would have drawn had it done its work. None for a product no pass declares
    (`identity`, the read that tells a file apart), whose verdict is said as Sift's."""
    return next((one for one in SAID_ON_A_FILE.get(product, ()) if one != _LEFT_OUT.name), None)
