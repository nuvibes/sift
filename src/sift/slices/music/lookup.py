# SPDX-License-Identifier: AGPL-3.0-or-later
"""The AcoustID lookup, as a task: one file, one answer, only when an admin has said yes.

Its own task with its own When, never a step of the fingerprint's, since it sends the fingerprint
out. A file is queued where its pairing settles (when the When starts it on its own) or by a press
of the task. The switch and the key are read again when the task runs. The file's own length is sent
first, then 13 seconds either side, which with the server's 7-second reach covers 20 seconds each
way.
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

#: One press of the lookup task: one walk queuing a lookup per owed file (`LookupStarter.catch_up`).
MUSIC_LOOKUP_CATCH_UP = "music_lookup_catch_up"

#: The lookup's task id; never renamed, as its When is stored under `tasks.<id>.when`.
LOOKUP_TASK = "music-lookup"

#: What a press says while nothing could be sent: every file would only be refused.
NOT_READY = "Turn on Name songs with AcoustID and save your AcoustID key first."

#: How many files the catch-up reads at a time.
CATCH_UP_PAGE = 500

#: The lengths sent, as seconds either side of the file's own, in order.
LENGTH_STEPS: tuple[int, ...] = (0, -13, 13)

#: How long an answer of "not known" stands before Ask again asks again, in days; a file's own menu
#: asks whatever the age.
ASK_AGAIN_AFTER_DAYS = 30
_DAY_S = 86_400

#: What a press of Ask again says while every file AcoustID did not know was asked too lately.
NOTHING_TO_ASK_AGAIN = "No file is waiting to be asked about again."

#: The shortest file worth asking about, in milliseconds.
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
    """The lengths, in whole seconds, a lookup of a file this long sends, in order; never one twice."""
    own = round(duration_ms / 1000)
    out: list[int] = []
    for step in LENGTH_STEPS:
        length = own + step
        if length > 0 and length not in out:
            out.append(length)
    return tuple(out)


class LookupStarter:
    """Whether a file should be looked up, and the queueing of it: where a pairing settles, and on a
    press.

    `consider` queues only where the task's When starts it on its own; `start_catch_up` queues one
    walk carrying the press, and `catch_up` queues each file's lookup under it. `plan` is the dry
    run.
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
        """The moment a "not known" answer must be older than for Ask again to ask again."""
        return int(self._clock()) - ASK_AGAIN_AFTER_DAYS * _DAY_S

    async def consider(self, asset_id: str) -> bool:
        """Queue a lookup where a pairing settled, if the task starts on its own and the file wants
        one.
        """
        if not await self.starts_on_its_own():
            return False
        if not await self._ready() or not await self._worth_asking(asset_id):
            return False
        await self._enqueue(
            MUSIC_LOOKUP, {"asset_id": asset_id}, priority=BACKGROUND_PRIORITY, dedupe=True
        )
        return True

    async def starts_on_its_own(self) -> bool:
        """Whether the When lets a settling pairing queue a lookup; anything else is Only when I press
        it.
        """
        return await self._settings.get_app(when_key(LOOKUP_TASK)) in (WHEN_WORK, WHEN_QUIET)

    async def owed(self) -> int:
        """How many files a press would ask AcoustID about, from the walk itself (`plan`); nought while
        the lookup is off or has no key.
        """
        return (await self.plan(first=0)).files

    async def not_known(self) -> int:
        """How many files AcoustID did not know, counted as Ask again counts them."""
        return (await self.plan_again()).not_known

    async def plan_again(self) -> LookupPlan:
        """The walk Ask again takes, with nothing queued or sent."""
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
        """A press of Ask again: one walk over the files not known for more than
        `ASK_AGAIN_AFTER_DAYS`. The walk's id or None, and how many it will ask about; refused with
        `LookupNotReady`.
        """
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
        """Queue the one walk a press of the lookup task is; its id, or None when no file is owed one.
        Refused with `LookupNotReady`; a second press collapses onto it.
        """
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
        """A press of Enrich on some files: AcoustID asked about those files, as one press of the task.

        Each is asked what the task asks (`_worth_asking`); `again` asks only the files AcoustID did
        not know, whatever the age. Refused with `LookupNotReady`.
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
        """Walk the files still owed a lookup and queue one for each that wants it, under this job; the
        switch is asked again per file.
        """
        after = ""
        queued = 0
        # A press of Enrich carries its files (`start_for_files`).
        chosen = context.job.payload.get("files")
        # A press of Ask again, or a file's own: the files AcoustID did not know.
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
        """What a press would ask AcoustID about, with nothing queued or sent."""
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
        """Whether this file still wants a lookup: no song, long enough, no answer kept, not kept
        local.
        """
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
        """Whether this file may be asked again: not known (before `before`), no song since, long
        enough, not kept local.
        """
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
        """Look one file up; sends nothing while the switch is off."""
        asset_id = str(context.payload.get("asset_id") or "")
        if not asset_id:
            raise ValueError("a music lookup needs the id of its file")
        opened = await self._opened_key(context, asset_id)
        if opened is None:
            return
        # Asked again: a file not known, at any age, since the press chose it.
        again = bool(context.payload.get("again"))
        wanted = (
            await self._names.wants_asking_again(asset_id, shortest_ms=SHORTEST_MS, before=0)
            if again
            else await self._names.wants_lookup(asset_id, shortest_ms=SHORTEST_MS)
        )
        if not wanted:
            return
        # Kept local covers a fingerprint of the file's sound too.
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

    async def _opened_key(self, context: JobContext, asset_id: str) -> bytes | None:
        """The AcoustID key, opened, or None (logged) where nothing may be sent."""
        # The switch, at run time, before the key is opened.
        if not bool(await self._settings.get_app(LOOKUP_KEY)):
            log.info("music.lookup_off", asset_id=asset_id, reason="switched off")
            return None
        secret_id = await self._names.key_id()
        if secret_id is None:
            log.info("music.lookup_off", asset_id=asset_id, reason="no key")
            return None
        master = await context.master_key()
        if master is None:
            raise WaitingForPassword("the AcoustID key")
        opened = await self._secrets.open(secret_id, master)
        if opened is None:
            log.info("music.lookup_off", asset_id=asset_id, reason="key unreadable")
            return None
        return opened

    async def _keep(self, asset_id: str, sent: tuple[int, ...], chosen: Chosen | None) -> None:
        """The answer, and the name where there is one, in one transaction; then the spread.

        The row records what AcoustID said; the file takes the name only through the kernel's
        writer, into an empty field and where no refusal holds it.
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
                # Through the song's one door (`kernel/content/songs.py`).
                written = await seed_music_on(
                    connection,
                    asset_id,
                    song,
                    source=MUSIC_FROM_ACOUSTID,
                    facts={"score": round(chosen.score, 3), "recording": chosen.recording_id},
                    recording_id=chosen.recording_id,
                    score=chosen.score,
                )
            # AcoustID's artists, where the song credits nobody yet (`songs.credit_where_none`).
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
                # Told as a record edit is.
                announce(await who_may_see_a_file(connection), About.LIBRARY)
            # And the counts beside the lookup (`NameStore.keep_lookup`).
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
        # One walk at a time: the queue's dedupe folds a second press into the first.
        alone=True,
    )


class LookupSettings:
    """The key and the check behind Settings > Stash-boxes > Music lookup. Admin routes only.

    The key is sealed like a stash-box's and never read back or shown.
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
        """One lookup of AcoustID's own example with the key that is set, so a key can be proved first."""
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
