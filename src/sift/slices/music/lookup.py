# SPDX-License-Identifier: AGPL-3.0-or-later
"""The AcoustID lookup, as a task: one file, one answer, only when an admin has said yes.

## A task of its own, and never a step of the fingerprint's

Making a fingerprint reads a file's sound on this device and sends nothing anywhere. Asking
AcoustID sends that fingerprint and the file's length to somebody else's service. Those are two
decisions, so they are two tasks with two Whens (`LOOKUP_TASK`, registered beside the music task in
`music/__init__.py`), and pressing Generate music fingerprints asks AcoustID about nothing.

A file reaches this task one of two ways, and each is the lookup task's own job, never work done
inside the fingerprint job:

* **Its pairing settles** (`names.SongNames.on_pairs_settled` calls `LookupStarter.consider`) and
  the lookup task's When says it starts on its own: As files arrive, or During quiet hours, where
  the claim holds the job until the range opens. While the When says Only when I press it, the
  out-of-the-box answer, nothing is queued and the file waits for a press.
* **Somebody presses the task** (Run now, Run during quiet hours): one walk over the files still
  owed a lookup (`LookupStarter.catch_up`) queues one lookup per file under itself, carrying the
  press, so the whole run is one family on Activity and the task's row reads its last run.

Each lookup asks about a file that still has no song: a fingerprint with something in it, long
enough for a song, nobody having taken a name off it, and no answer kept.

## Nothing leaves this device without the switch, and that is asked when the task RUNS

The switch and the key are read when the task starts, not only when it was queued. A task that
waited overnight behind a library's worth of work and found the switch turned off in the meantime
sends nothing; turning the switch off is a promise about what happens next, and the queue is next.
A key that is set but locked (this device restarted and nobody has signed in) parks the task until
somebody does, exactly as a stash-box's task waits.

## Which lengths, and why that list

AcoustID's server considers only recordings within 7 seconds of the length it is sent. The file's
own length is sent first, and names most full-length files whose songs are known. A music video
often runs a little longer or shorter than its song (a file of 187 s over a song of 200.5 s), so
the retry is the file's length 13 seconds either side: with the server's 7-second reach each side,
the three lookups cover every length from 20 seconds under to 20 seconds over with a second of
overlap for the rounding. A list of 2, 5 and 10 seconds either side (seven lookups) re-asks inside
the first lookup's own reach four times; this asks three times and reaches further. A mix far
longer than its song is out of any small range, and gets its name through the file it pairs with
instead.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sift.kernel.access.catalog import kept_local_over
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce, who_may_see_a_file
from sift.kernel.content import songs
from sift.kernel.content.identity import MUSIC_FROM_ACOUSTID, cleaned_song, seed_music_on
from sift.kernel.jobs import (
    BACKGROUND_PRIORITY,
    WAITED_ON_PRIORITY,
    JobContext,
    JobFailedPermanently,
    WaitingForPassword,
    register_handler,
)
from sift.kernel.jobs.quiet_hours import WHEN_QUIET, WHEN_WORK
from sift.kernel.jobs.schedules import when_key
from sift.kernel.log import get_logger
from sift.kernel.seams import SettingsSeam
from sift.kernel.secret_store import SecretStore
from sift.kernel.wiring import Part
from sift.slices.music.acoustid import (
    FIRST_VALUES,
    AcoustIDClient,
    AcoustIDRefused,
    AcoustIDUnreachable,
    Chosen,
    choose,
    compress,
)
from sift.slices.music.settings import LOOKUP_KEY, LOOKUP_ROUTE_KEY
from sift.slices.music.store import LookupKept, NameStore, OwedPage

log = get_logger(__name__)

MUSIC_LOOKUP = "music_lookup"

#: One press of the lookup task: one job that walks the files still owed a lookup and queues a
#: lookup for each under itself (`LookupStarter.catch_up`).
MUSIC_LOOKUP_CATCH_UP = "music_lookup_catch_up"

#: The lookup's task, on Settings > Tasks and on the Music pane. Never renamed once shipped: its
#: When is stored under `tasks.<id>.when`.
LOOKUP_TASK = "music-lookup"

#: What a press says while nothing could be sent: every file would only be refused.
NOT_READY = "Turn on Name songs with AcoustID and save your AcoustID key first."

#: How many files the catch-up reads at a time. A page is one statement and one read of their
#: songs; the walk is one job, so this bounds a statement's size, not how much is queued.
CATCH_UP_PAGE = 500

#: The lengths sent, as seconds either side of the file's own, in the order they are tried. See the
#: module docstring for where each number comes from.
LENGTH_STEPS: tuple[int, ...] = (0, -13, 13)

#: ASK AGAIN: how long an answer of "not known" stands before a press of Ask again asks about the
#: file again, in days. AcoustID learns songs as people submit fingerprints linked to recordings,
#: so a file it did not know may be known later; but what it knows moves over weeks, not hours, and
#: a press repeated the next day would send the same fingerprints for the same answer. Thirty days
#: makes a monthly press worth making and a daily one ask nothing. One file's own menu asks again
#: whatever the age: somebody chose that file.
ASK_AGAIN_AFTER_DAYS = 30
_DAY_S = 86_400

#: What a press of Ask again says while every file AcoustID did not know was asked too lately.
NOTHING_TO_ASK_AGAIN = "No file is waiting to be asked about again."

#: The shortest file worth asking about, in milliseconds. AcoustID names whole songs within 7
#: seconds of the length sent, and a song shorter than this is rare enough that asking about every
#: clip under it would be requests spent on an answer that is nothing (clips and excerpts of a
#: few seconds name nothing).
SHORTEST_MS = 30_000

#: Later than any answer, for a walk of the not-known files whatever their age.
_ANY_AGE = 2**62


class FingerprintOf(Protocol):
    """The fingerprint kept for one file (`MusicStore.fingerprint_of`)."""

    async def __call__(self, asset_id: str) -> Any: ...


#: Queue one lookup: the job queue's `enqueue`, bound by the composition root.
Enqueue = Callable[..., Awaitable[str]]

#: Giving a file's new name to the files that share its song (`names.SongNames.spread`).
Spread = Callable[[str], Awaitable[Sequence[str]]]


class LookupNotReady(Exception):
    """A press of the lookup task while nothing could be sent. The message is meant to be read."""


@dataclass(frozen=True, slots=True)
class LookupPlan:
    """What a press of the lookup task would ask AcoustID about, worked out and sent nowhere."""

    #: How many files a press would ask about.
    files: int
    #: The first of them, by id, in the order the walk meets them.
    first: tuple[str, ...] = ()
    #: Ask again's walk only: the files AcoustID did not know at any age, less the kept-local ones.
    not_known: int = 0


def lengths_for(duration_ms: int) -> tuple[int, ...]:
    """The lengths, in whole seconds, a lookup of a file this long sends, in order. None under 1 s,
    and never the same length twice."""
    own = round(duration_ms / 1000)
    out: list[int] = []
    for step in LENGTH_STEPS:
        length = own + step
        if length > 0 and length not in out:
            out.append(length)
    return tuple(out)


class LookupStarter:
    """Whether a file should be looked up, and the queueing of it: where a pairing settles, and on
    a press of the lookup task.

    The switch and the key are asked here as well as in the task. Here, so a library with the
    switch off never has a queue full of tasks that will each refuse; in the task, because the
    switch can change while one waits.

    ## Where a pairing settles, only when the lookup task starts on its own

    `consider` is called from inside the fingerprint job, once a file's pairs are written. It
    queues the lookup task's own job and sends nothing itself, and it queues one only where the
    lookup task's When is As files arrive or During quiet hours. While it says Only when I press it,
    which is where every install starts, a fingerprint made by any press (Run now, the Build, a
    file's Run task, the card on Organize) leaves the file owed, counted beside the task, until
    somebody presses the lookup task or chooses a When for it.

    ## A press of the lookup task

    `start_catch_up` queues one walk, carrying the press, and `catch_up` walks the files still owed
    a lookup and queues one for each as its own child, so each lookup is the press too: Run now runs
    them now and Run during quiet hours holds them to the range, whatever the When says. `plan` is
    the same walk with nothing queued, for the task's dry run. A press of the task is a press: a
    pass over the library never starts on its own, and turning the switch on is an answer about
    what may be sent, not a request to send it all now.
    """

    def __init__(
        self,
        names: NameStore,
        settings: SettingsSeam,
        *,
        enqueue: Enqueue,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._names = names
        self._settings = settings
        self._enqueue = enqueue
        self._clock = clock

    def asked_before(self) -> int:
        """The moment an answer of "not known" must be older than for a press of Ask again to ask
        about its file again. See `ASK_AGAIN_AFTER_DAYS`."""
        return int(self._clock()) - ASK_AGAIN_AFTER_DAYS * _DAY_S

    async def consider(self, asset_id: str) -> bool:
        """Queue a lookup of this file where its pairing settled, if the lookup task starts on its
        own, an admin has allowed it and the file wants one. Whether one was."""
        if not await self.starts_on_its_own():
            return False
        if not await self._ready() or not await self._worth_asking(asset_id):
            return False
        await self._enqueue(
            MUSIC_LOOKUP, {"asset_id": asset_id}, priority=BACKGROUND_PRIORITY, dedupe=True
        )
        return True

    async def starts_on_its_own(self) -> bool:
        """Whether the lookup task's When lets a settling pairing queue a lookup: As files arrive
        or During quiet hours. Anything else, a value never written included, is Only when I press
        it, so a When nobody chose never sends anything."""
        return await self._settings.get_app(when_key(LOOKUP_TASK)) in (WHEN_WORK, WHEN_QUIET)

    async def owed(self) -> int:
        """How many files a press would ask AcoustID about: the count said beside the lookup task on
        the Music pane. Nought while the lookup is off or has no key, because nothing could be asked
        then.

        THE WALK'S OWN ANSWER (`plan`), file by file, and not the page's: a file kept local, or
        one whose own length is under a song's, is on the page and is never asked about, so a count
        made of the page would say "3 files have no song yet" after every press, for ever. A file
        AcoustID answered (named, or did not know) is not on the page at all; one whose ask failed
        is, and stays owed until an ask gets an answer.
        """
        return (await self.plan(first=0)).files

    async def not_known(self) -> int:
        """How many files AcoustID did not know, counted as Ask again counts them (`plan_again`)."""
        return (await self.plan_again()).not_known

    async def plan_again(self) -> LookupPlan:
        """The walk a press of Ask again takes, with nothing queued and nothing sent: the files it
        would ask about, and every not-known file it would leave alone only for its age."""
        ready = await self._ready()
        files = unknown = 0
        after = ""
        before = self.asked_before()
        while True:
            page = await self._names.not_known_page(
                after=after, limit=CATCH_UP_PAGE, shortest_ms=SHORTEST_MS, before=_ANY_AGE
            )
            kept = await self._kept_local_among(page.files)
            asked = [one for one in page.files if one not in kept]
            unknown += len(asked)
            if ready and asked:
                files += len(
                    await self._names.wanting_asking_again(
                        asked, shortest_ms=SHORTEST_MS, before=before
                    )
                )
            if page.last is None:
                return LookupPlan(files=files, not_known=unknown)
            after = page.last

    async def start_again(self, *, requested_by: str) -> tuple[str | None, int]:
        """A press of Ask again: one walk, as a press of the lookup task is, over the files AcoustID
        did not know and was last asked about more than `ASK_AGAIN_AFTER_DAYS` ago. The walk's id
        (None where no file is waiting to be asked again) and how many it will ask about, counted
        before it starts. Refused with `LookupNotReady` while the lookup is off or has no key."""
        refused = await self.cannot_run()
        if refused is not None:
            raise LookupNotReady(refused)
        files = (await self.plan_again()).files
        if files == 0:
            return None, 0
        job_id = await self._enqueue(
            MUSIC_LOOKUP_CATCH_UP,
            {"again": True},
            priority=WAITED_ON_PRIORITY,
            requested_by=requested_by,
            dedupe=True,
        )
        return job_id, files

    async def cannot_run(self) -> str | None:
        """Why a press of the lookup task would send nothing, in words, or None when it can run."""
        return None if await self._ready() else NOT_READY

    async def start_catch_up(self, *, at: str, requested_by: str, priority: int) -> str | None:
        """Queue the one walk a press of the lookup task is, marked as that press. Its id, or None
        when no file is owed a lookup: a press with nothing to ask queues nothing, and the press
        says so, rather than a walk that asks nobody.

        Refused with `LookupNotReady` while the lookup is off or has no key, when each file would
        only be refused. A second press while one waits collapses onto it (`dedupe`)."""
        refused = await self.cannot_run()
        if refused is not None:
            raise LookupNotReady(refused)
        if (await self.plan(first=0)).files == 0:
            return None
        return await self._enqueue(
            MUSIC_LOOKUP_CATCH_UP,
            {},
            priority=priority,
            requested_by=requested_by,
            at=at,
            dedupe=True,
        )

    async def start_for_files(
        self, asset_ids: Sequence[str], *, requested_by: str, again: bool = False
    ) -> tuple[str | None, int]:
        """A press of Enrich on some files: AcoustID asked about THOSE files, as one press of the
        lookup task. The walk's id (None where none of them wants a lookup) and how many it will
        ask about.

        The same walk a press of the task queues (`start_catch_up`), carrying the files rather
        than walking the library, so Activity draws one press with one lookup under it per file,
        and every lookup is the press: never refused under "Only when I press it". Refused with
        `LookupNotReady` while the lookup is off or has no key, as every press is. Each file is
        asked what a press of the task asks of it (`_worth_asking`): a file that carries a song, is
        too short, is kept local, or was asked and answered (named, or not known to AcoustID) is
        left out, so a file AcoustID did not know is not asked again by this press either. The
        caller has already left out every file this user may not act on (a file in Hidden while it
        is shut among them).

        `again` is a file's own Ask again: only the files AcoustID did not know are asked, and
        whatever the age of that answer, because somebody chose them.
        """
        refused = await self.cannot_run()
        if refused is not None:
            raise LookupNotReady(refused)
        wanted = [
            one
            for one in dict.fromkeys(asset_ids)
            if (
                await self._worth_asking_again(one, before=0)
                if again
                else await self._worth_asking(one)
            )
        ]
        if not wanted:
            return None, 0
        job_id = await self._enqueue(
            MUSIC_LOOKUP_CATCH_UP,
            {"files": wanted, "again": True} if again else {"files": wanted},
            priority=WAITED_ON_PRIORITY,
            requested_by=requested_by,
            dedupe=True,
        )
        return job_id, len(wanted)

    async def catch_up(self, context: JobContext) -> None:
        """Walk the files still owed a lookup and queue one for each that wants it, under this job.

        Each file is asked what a settling pairing asks of it (the switch, the key, a song since
        the page was read, kept local), and the switch is asked again on every file for the same
        reason the task asks it when it runs: turning it off part-way through stops the rest. Each
        lookup is queued as this job's child, so it carries the press this walk carries.
        """
        after = ""
        queued = 0
        # A press of Enrich on some files carries them (`start_for_files`): those, asked the same
        # questions, and no walk of the library.
        chosen = context.job.payload.get("files")
        # A press of Ask again (`start_again`), or a file's own: the files AcoustID did not know,
        # each lookup marked so the task asks about a file that already has an answer.
        again = bool(context.job.payload.get("again"))
        before = 0 if isinstance(chosen, list) else self.asked_before()
        while True:
            if isinstance(chosen, list):
                page = OwedPage(files=tuple(str(one) for one in chosen), last=None)
            elif again:
                page = await self._names.not_known_page(
                    after=after, limit=CATCH_UP_PAGE, shortest_ms=SHORTEST_MS, before=before
                )
            else:
                page = await self._names.owed_lookups(
                    after=after, limit=CATCH_UP_PAGE, shortest_ms=SHORTEST_MS
                )
            for asset_id in page.files:
                worth = (
                    await self._worth_asking_again(asset_id, before=before)
                    if again
                    else await self._worth_asking(asset_id)
                )
                if await self._ready() and worth:
                    await context.enqueue_child(
                        MUSIC_LOOKUP,
                        {"asset_id": asset_id, "again": True} if again else {"asset_id": asset_id},
                        priority=context.job.priority,
                        dedupe=True,
                    )
                    queued += 1
            if page.last is None:
                break
            after = page.last
        log.info("music.lookup_catch_up", queued=queued)
        await context.set_note(_queued(queued))
        await context.set_progress(1.0)

    async def plan(self, *, first: int) -> LookupPlan:
        """What a press would ask AcoustID about: the walk `catch_up` takes, with nothing queued
        and nothing sent. Nothing at all while the lookup is off or has no key."""
        if not await self._ready():
            return LookupPlan(files=0)
        files = 0
        named: list[str] = []
        after = ""
        while True:
            page = await self._names.owed_lookups(
                after=after, limit=CATCH_UP_PAGE, shortest_ms=SHORTEST_MS
            )
            worth = await self._worth_asking_among(page.files)
            for asset_id in page.files:
                if asset_id in worth:
                    files += 1
                    if len(named) < first:
                        named.append(asset_id)
            if page.last is None:
                return LookupPlan(files=files, first=tuple(named))
            after = page.last

    async def _worth_asking(self, asset_id: str) -> bool:
        """Whether this file still wants a lookup and may be asked about at all: no song, long
        enough, no answer kept, and not kept local."""
        if not await self._names.wants_lookup(asset_id, shortest_ms=SHORTEST_MS):
            return False
        return not await kept_local_over(self._names.database, asset_id)

    async def _worth_asking_among(self, asset_ids: Sequence[str]) -> set[str]:
        """`_worth_asking` of a page of files, a statement per question whatever its length."""
        wanted = await self._names.wanting_lookup(asset_ids, shortest_ms=SHORTEST_MS)
        return wanted - await self._kept_local_among(list(wanted))

    async def _kept_local_among(self, asset_ids: Sequence[str]) -> set[str]:
        return {one for one in asset_ids if await kept_local_over(self._names.database, one)}

    async def _worth_asking_again(self, asset_id: str, *, before: int) -> bool:
        """Whether this file may be asked about again: AcoustID did not know it (answered before
        `before`, nought for any age), no song since, long enough, and not kept local."""
        if not await self._names.wants_asking_again(
            asset_id, shortest_ms=SHORTEST_MS, before=before
        ):
            return False
        return not await kept_local_over(self._names.database, asset_id)

    async def _ready(self) -> bool:
        return (
            bool(await self._settings.get_app(LOOKUP_KEY))
            and await self._names.key_id() is not None
        )


def _queued(count: int) -> str:
    """What one press of the lookup task did, for its row on Tasks."""
    if count == 0:
        return "No file was waiting to be looked up on AcoustID."
    files = "1 file" if count == 1 else f"{count:,} files"
    return f"Queued {files} to be looked up on AcoustID."


class LookupTask:
    """One file's lookup: the fingerprint compressed, the lengths tried, the answer kept."""

    def __init__(
        self,
        names: NameStore,
        settings: SettingsSeam,
        secrets: SecretStore,
        client: AcoustIDClient,
        *,
        fingerprint_of: FingerprintOf,
        spread: Spread,
        touched: Callable[[Sequence[str]], Awaitable[None]],
    ) -> None:
        self._names = names
        self._settings = settings
        self._secrets = secrets
        self._client = client
        self._fingerprint_of = fingerprint_of
        self._spread = spread
        self._touched = touched

    async def run(self, context: JobContext) -> None:
        """Look one file up. See the module docstring for when this sends nothing at all."""
        asset_id = str(context.payload.get("asset_id") or "")
        if not asset_id:
            raise ValueError("a music lookup needs the id of its file")
        # THE SWITCH, AT RUN TIME. Before the key is opened and before anything is read.
        if not bool(await self._settings.get_app(LOOKUP_KEY)):
            log.info("music.lookup_off", asset_id=asset_id, reason="switched off")
            return
        secret_id = await self._names.key_id()
        if secret_id is None:
            log.info("music.lookup_off", asset_id=asset_id, reason="no key")
            return
        master = await context.master_key()
        if master is None:
            raise WaitingForPassword("the AcoustID key")
        opened = await self._secrets.open(secret_id, master)
        if opened is None:
            log.info("music.lookup_off", asset_id=asset_id, reason="key unreadable")
            return
        # Asked AGAIN (a press of Ask again, or a file's own): a file AcoustID did not know, at any
        # age, since the press already chose it by age. The earlier answer stands until this one is
        # kept over it.
        again = bool(context.payload.get("again"))
        wanted = (
            await self._names.wants_asking_again(asset_id, shortest_ms=SHORTEST_MS, before=0)
            if again
            else await self._names.wants_lookup(asset_id, shortest_ms=SHORTEST_MS)
        )
        if not wanted:
            return
        # KEPT LOCAL means nothing about this file leaves this device, and a fingerprint of its
        # sound is something about it. The flag was written for the stash-boxes; this is the second
        # sender of the same class and it keeps the same promise, read from the same rule.
        if await kept_local_over(self._names.database, asset_id):
            log.info("music.lookup_off", asset_id=asset_id, reason="kept local")
            return

        kept = await self._fingerprint_of(asset_id)
        asset = await context.content.get(asset_id)
        if kept is None or asset is None or not kept.values:
            return
        duration_ms = asset.duration_ms or kept.duration_ms
        fingerprint = compress(kept.values[:FIRST_VALUES], kept.algorithm)
        route = await self._settings.get_app(LOOKUP_ROUTE_KEY)

        sent: list[int] = []
        chosen: Chosen | None = None
        try:
            for length in lengths_for(int(duration_ms)):
                sent.append(length)
                answer = await self._client.ask(
                    opened.decode("utf-8"), fingerprint, length, route=route
                )
                chosen = choose(answer.results)
                if chosen is not None:
                    break
        except AcoustIDUnreachable as failure:
            status = "refused" if isinstance(failure, AcoustIDRefused) else "failed"
            await self._names.keep_lookup(
                asset_id, LookupKept(status=status, lengths_sent=tuple(sent))
            )
            log.warning("music.lookup_" + status, asset_id=asset_id, sent=len(sent))
            raise JobFailedPermanently(str(failure)) from failure
        await context.set_progress(1.0)
        await self._keep(asset_id, tuple(sent), chosen)

    async def _keep(self, asset_id: str, sent: tuple[int, ...], chosen: Chosen | None) -> None:
        """The answer, and the name where there is one, in one transaction; then the spread.

        The lookup's row records what AcoustID SAID, so it says `named` with the recording even
        where the file is not given it: a name arrived through its group in the meantime, or
        somebody took that song off this file. The file is given the name only through the
        kernel's writer, which fills only an empty field, and only where no refusal holds it.
        """
        if chosen is None:
            await self._names.keep_lookup(asset_id, LookupKept(status="nothing", lengths_sent=sent))
            log.info("music.lookup_nothing", asset_id=asset_id, sent=len(sent))
            return
        song = cleaned_song(chosen.song)
        async with self._names.database.write() as connection:
            await NameStore.keep_lookup_on(
                connection,
                asset_id,
                LookupKept(
                    status="named",
                    lengths_sent=sent,
                    recording_id=chosen.recording_id,
                    title=chosen.title,
                    artists=chosen.artists,
                    score=chosen.score,
                ),
            )
            written = False
            if song and not await NameStore.refused_on(connection, asset_id, song):
                # Through the song's one door: the recording finds its song, or makes it, and
                # the file carries it with the score (`kernel/content/songs.py`).
                written = await seed_music_on(
                    connection,
                    asset_id,
                    song,
                    source=MUSIC_FROM_ACOUSTID,
                    facts={"score": round(chosen.score, 3), "recording": chosen.recording_id},
                    recording_id=chosen.recording_id,
                    score=chosen.score,
                )
            # The artists AcoustID named, credited on the song that IS this recording where it
            # credits nobody yet: the song the file just took, or the one the recording had. A
            # song somebody credited by hand keeps their list (`songs.credit_where_none`).
            credited = False
            song_id = await songs.song_of_recording(connection, chosen.recording_id)
            if song_id is not None:
                credited = await songs.credit_where_none(
                    connection,
                    song_id,
                    list(chosen.artist_names) or songs.split_artists(chosen.artists),
                    source=songs.CREDIT_FROM_ACOUSTID,
                )
            if written or credited:
                # The file's song, its Music field and its History changed, or the song's artists:
                # told as a record edit is.
                announce(await who_may_see_a_file(connection), About.LIBRARY)
            # And the counts beside the lookup, named or not, as every kept answer is
            # (`NameStore.keep_lookup`).
            announce(EVERY_ADMIN, About.JOBS)
        log.info("music.lookup_named", asset_id=asset_id, written=written, sent=len(sent))
        if written:
            await self._touched([asset_id])
            # A name AcoustID gave one file is a name for every file sharing its song.
            await self._spread(asset_id)


def register_handlers(task: LookupTask, starter: LookupStarter) -> None:
    register_handler(
        MUSIC_LOOKUP,
        task.run,
        name="Looking up song on AcoustID",
        counts="files looked up on AcoustID",
    )
    register_handler(
        MUSIC_LOOKUP_CATCH_UP,
        starter.catch_up,
        name="Choosing files to look up on AcoustID",
        # One walk at a time: a second press while one runs would queue nothing new, and the
        # queue's dedupe already folds the second request into the first.
        alone=True,
    )


class LookupSettings:
    """The key and the check behind Settings > Stash-boxes > Music lookup. Admin routes only.

    The key is sealed on the way in through the same store a stash-box's key uses, and is never
    written anywhere else, never read back and never shown: the routes say whether one is set and
    whether it can be opened right now, which are the two different facts a stash-box's screen
    tells apart (`StashBoxService.key_ready`).
    """

    def __init__(
        self, names: NameStore, secrets: SecretStore, client: AcoustIDClient, settings: SettingsSeam
    ) -> None:
        self._names = names
        self._secrets = secrets
        self._client = client
        self._settings = settings

    async def state(self, master_key: bytes | None) -> dict[str, object]:
        """Whether the lookup is on, whether a key is set and can be opened now, and the route."""
        secret_id = await self._names.key_id()
        ready = (
            secret_id is not None
            and master_key is not None
            and await self._secrets.open(secret_id, master_key) is not None
        )
        return {
            "on": bool(await self._settings.get_app(LOOKUP_KEY)),
            "key_set": secret_id is not None,
            "key_ready": ready,
            "route": await self._settings.get_app(LOOKUP_ROUTE_KEY),
        }

    async def set_key(self, key: str, master_key: bytes) -> None:
        """Seal a new key and point at it. The one it replaces is forgotten, not left behind."""
        cleaned = key.strip()
        if not cleaned:
            raise ValueError("an AcoustID key cannot be empty")
        before = await self._names.key_id()
        secret_id = await self._secrets.seal(cleaned.encode("utf-8"), master_key)
        await self._names.set_key_id(secret_id)
        if before is not None and before != secret_id:
            await self._secrets.forget(before)
        log.info("music.lookup_key_set")

    async def forget_key(self) -> bool:
        """Remove the key. Whether there was one."""
        before = await self._names.key_id()
        if before is None:
            return False
        await self._names.drop_key_id()
        await self._secrets.forget(before)
        log.info("music.lookup_key_removed")
        return True

    async def check(self, master_key: bytes | None) -> tuple[bool, str]:
        """One lookup of AcoustID's own example with the key that is set. Nothing of the library
        is sent, and it works whether the switch is on or off: proving a key is what somebody
        does BEFORE turning the lookup on."""
        secret_id = await self._names.key_id()
        if secret_id is None:
            return False, "No AcoustID key is set."
        if master_key is None:
            return False, (
                "The AcoustID key is locked because Sift restarted. Enter your password under "
                "Unlock in Settings > Stash-boxes."
            )
        opened = await self._secrets.open(secret_id, master_key)
        if opened is None:
            return False, "The AcoustID key cannot be read. Delete it and enter it again."
        route = await self._settings.get_app(LOOKUP_ROUTE_KEY)
        return await self._client.check(opened.decode("utf-8"), route=route)


#: The lookup's settings behind the routes, on the application.
LOOKUP: Part[LookupSettings] = Part("music_lookup_settings")

#: The catch-up's count and its press, behind the routes.
LOOKUP_STARTER: Part[LookupStarter] = Part("music_lookup_starter")
