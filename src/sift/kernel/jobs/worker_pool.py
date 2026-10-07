# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workers, and the handlers they run.

A handler is a plain async function that a feature registers under a job type. The kernel knows
nothing about what any of them do: it claims a row, calls the function, and writes down what
happened. That is the whole of the coupling, and it is why scanning, downloading and transcoding
can be written independently of each other and of this.

Each worker runs one job at a time and heartbeats while it does. The heartbeat is not just a
liveness signal: it is fenced on the claim, so it fails the moment the job stops belonging to
this worker, because the watchdog gave up on it, or because someone cancelled it. When that
happens the handler is cancelled mid-flight rather than left to finish work that another worker
is already redoing.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.ids import new_id
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import CURRENT_FAMILY, Ledger
from sift.kernel.jobs.queue import (
    STOP_TO_CANCEL,
    TERMINAL_STATES,
    Job,
    JobBlocked,
    JobCanceled,
    JobFailedPermanently,
    JobHeld,
    JobPaused,
    JobQueue,
    JobState,
)
from sift.kernel.jobs.tuning import (
    HEARTBEAT_SECONDS,
    IDLE_POLL_SECONDS,
    PROGRESS_INTERVAL_SECONDS,
    RECONFIGURE_SECONDS,
    SHUTDOWN_GRACE_SECONDS,
    STALE_AFTER_SECONDS,
    SWEEP_INTERVAL_SECONDS,
)
from sift.kernel.jobs.waking import Listen, Waking, first_of
from sift.kernel.jobs.watchdog import run_watchdog
from sift.kernel.jobs.workspaces import Workspaces
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.presses import Pressed, pressed_job

log = get_logger(__name__)

_NO_HANDLER = "this job's type no longer exists in this version of Sift"
_WAITING_FOR_LOGIN = "waiting for someone to log in"

_NO_WORKSPACES = (
    "this worker pool was built without a place for a job to keep what it is part way through "
    "writing. Pass capabilities=SystemCapabilities(..., workspaces=Workspaces(path)) to "
    "WorkerPool; the composition root does this at boot (sift/wiring/workers.py)."
)

_PAUSED_MID_JOB = "stopped part way through because somebody paused it"

_NO_CAPABILITIES = (
    "this worker pool was built without system capabilities, so its handlers cannot reach the "
    "content store or the master key. Pass capabilities=SystemCapabilities(...) to WorkerPool; "
    "the composition root does this at boot (sift/wiring/workers.py)."
)


@runtime_checkable
class SystemSecrets(Protocol):
    """The master key a job needs to open a stored secret, without a request to get it from.

    A route reads the key from the session of whoever is asking. A job has no session and no
    asker, so the key has to be handed to it, but only the user's password can unwrap it,
    which means the key exists in this process only after someone has logged in, and is gone
    again after a restart. `None` is therefore an ordinary answer, not an error: it means nobody
    has logged in since the process started. A handler that gets `None` raises `JobBlocked` and
    waits for a login rather than failing.

    This is a Protocol because the key store belongs to the auth slice and the kernel may not
    import a slice. The shape is named here; the implementation is handed in at boot.
    """

    async def master_key(self) -> bytes | None: ...


@dataclass(frozen=True, slots=True)
class SystemCapabilities:
    """What a handler may do as the system: no viewer, and so no permission check.

    This is the deliberate difference between a job and a route, and the reason it is spelled out
    in one place rather than reached for. A route always acts for somebody, so it reads assets
    through `app.state.access`, which scopes every row to that viewer. A job acts for nobody: a
    scan indexes files before any user has been granted them, and a thumbnail is built for an
    asset the requester may not be allowed to see. There is no viewer to scope to, so the scoped
    path cannot be used and the unscoped store is the honest answer.

    That makes this the one place in Sift where content is reachable with no permission check, so
    it is handed out only from inside a handler, and only because the kernel put it there. The
    obvious way back out is closed: `app.state.content` is refused outside the kernel by the
    semgrep rule that already guards it, and that rule matches the assignment as well as the read,
    so a handler cannot park the store on the application for a route to pick up.

    What that does not do is stop a slice keeping the store in a module-level global of its own,
    and no rule here can: at that point the code is deliberately routing around its own access
    layer, which review catches and a pattern matcher does not. The boundary this holds is the
    accidental one: nobody reaches an unscoped store without first being handed it as a job.
    """

    content: ContentStore
    #: The roots and the folder tree. A job that indexes a library needs to know where the library
    #: is, and the root's path lives in a table nothing outside the kernel may read.
    library: LibraryStore
    # Left unbacked until the slice that needs it supplies one. An interface with no backend has
    # no backend: a job asking for a secret today gets None and blocks, which is the same answer
    # it would get from a real store with nobody logged in.
    secrets: SystemSecrets | None = None
    #: Where a handler keeps what it is part way through writing, one directory per job. None for a
    #: pool built without one, in which case asking for a workspace is an error rather than a
    #: temporary directory nobody would sweep. See `kernel/jobs/workspaces.py`.
    workspaces: Workspaces | None = None


@dataclass(slots=True)
class JobContext:
    """What a handler is given: its job, and the few things it may do to it.

    Not frozen, for one field: when progress was last written, which `report_progress` keeps so
    a handler looping over a library does not write a transaction per step.
    """

    job: Job
    worker_id: str
    queue: JobQueue
    capabilities: SystemCapabilities | None = None
    #: Who pressed the work this job carries out (`WorkerPool._pressed_by`), or None for Sift's own;
    #: what a file's History says had Sift look (`kernel.presses`).
    pressed_by: str | None = None
    _progress_written: float = field(default=-1.0e9, repr=False)
    #: What the handler said the job is about, or the row's own count until it does.
    _units: int | None = field(default=None, repr=False)
    #: Files this job handed to other jobs of its own family. See `units_done`.
    _handed_on: int = field(default=0, repr=False)
    #: Files this job brought into the library for the first time. See `arrived`.
    _arrived: int = field(default=0, repr=False)
    #: The last note this job wrote, for the ledger.
    noted: str | None = field(default=None, repr=False)
    #: What the last heartbeat heard somebody asking of this job. See `stopping`.
    _stop: str | None = field(default=None, repr=False)
    #: Whether this handler asked for its workspace, so one that never did costs no sweep.
    _workspace_used: bool = field(default=False, repr=False)

    @property
    def content(self) -> ContentStore:
        """The content store, unscoped. See `SystemCapabilities` for why a job gets one."""
        if self.capabilities is None:
            raise RuntimeError(_NO_CAPABILITIES)
        return self.capabilities.content

    @property
    def library(self) -> LibraryStore:
        """The roots and the folder tree, unscoped. Same reasoning as `content`."""
        if self.capabilities is None:
            raise RuntimeError(_NO_CAPABILITIES)
        return self.capabilities.library

    async def master_key(self) -> bytes | None:
        """The master key for the system's secrets, or None if nobody has logged in.

        `None` is the answer to wait on, not to fail on: raise `JobBlocked` and the kernel parks
        the job until a login releases it. It is also the answer when no secret store is wired at
        all, which keeps a handler's blocked path honest before that slice exists.
        """
        if self.capabilities is None:
            raise RuntimeError(_NO_CAPABILITIES)
        if self.capabilities.secrets is None:
            return None
        return await self.capabilities.secrets.master_key()

    @property
    def workspace(self) -> Path:
        """A directory of this job's own, made on the first ask, for work in progress.

        IT OUTLIVES THE ATTEMPT, which is the whole reason it is here rather than a temporary
        directory of the handler's own. A handler that is paused half way through a download leaves
        its bytes here and finds them at the same path when the job is resumed; the kernel removes
        the directory when the job reaches a state it is not coming back from (done, failed or
        canceled), and never while it is paused, blocked or waiting to be tried again.

        A handler that wants somewhere to put a file for the length of one call still wants its own
        `TemporaryDirectory`. This is for what must survive one.
        """
        if self.capabilities is None or self.capabilities.workspaces is None:
            raise RuntimeError(_NO_WORKSPACES)
        self._workspace_used = True
        return self.capabilities.workspaces.of(self.job.id)

    @property
    def used_a_workspace(self) -> bool:
        """Whether this handler ever asked for one. Read by the pool when the job settles."""
        return self._workspace_used

    def told_to_stop(self, reason: str | None) -> None:
        """What the last heartbeat heard. The worker pool's to call and nobody else's: it is the
        only thing holding the beat that produced the answer."""
        self._stop = reason

    def stopping(self) -> str | None:
        """What this job is being asked to do, as of the last heartbeat: None, `pause` or `cancel`.

        A request made through the queue makes that heartbeat happen immediately (see
        `WorkerPool._stop_asked`), so this is current to within one beat of the press, not to within
        a heartbeat interval. A handler reading it every couple of seconds hears a pause in about
        that time without writing anything itself.

        `pause` means stop soon and keep what you have written: the job is claimed again with the
        same payload. `cancel` means the job is no longer this worker's: nothing it writes can land.
        Ignoring it is safe and costs only the time between the press and the stop.
        """
        return self._stop

    @property
    def payload(self) -> dict[str, Any]:
        return self.job.payload

    def require_str(self, key: str, message: str) -> str:
        """A non-empty string from the job payload, or a `ValueError` carrying `message`.

        A handler reached with its payload missing the one field it needs was enqueued wrong (a
        bug, not a runtime condition), so this raises, and the raise settles the job as failed with
        a reason a person can read rather than returning None for the handler to trip over later.
        """
        value = self.payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(message)
        return value

    @property
    def attempt(self) -> int:
        """Which attempt this is, counting from 1. The last one is `job.max_attempts`."""
        return self.job.attempts

    async def set_progress(self, fraction: float) -> None:
        """Report progress, 0 to 1. What the dashboard's bar is reading."""
        await self.queue.set_progress(self.job.id, self.worker_id, fraction)

    async def set_units(self, units: int) -> None:
        """Say how many files this job is about, once that is known: a scan after its walk.

        Written once, so the row weighs what it should in every estimate from then on, and kept
        here so the ledger is told the same number when the job ends.
        """
        self._units = max(0, units)
        await self.queue.set_units(self.job.id, self.worker_id, self._units)

    @property
    def units(self) -> int:
        return self.job.units if self._units is None else self._units

    def arrived(self, count: int) -> None:
        """Say this job brought `count` files into the library that it did not hold before: new
        bytes, never a second copy of a file already here. Added up on its family's run and said
        on the run's line in History ("Sift ran Scan in 42 s: 1,200 files, 12 new"), which is the
        one figure a person reads a scan's line for and the run could not otherwise tell."""
        self._arrived += max(0, count)

    @property
    def files_arrived(self) -> int:
        """What `arrived` has been told, for the ledger."""
        return self._arrived

    @property
    def units_done(self) -> int:
        """How many files this job FINISHED, as against how many it was about.

        The two differ for a job that fans out. A scan's walk says it is about the files it is going
        to read and then hands every one of them to a probe of its own, and a probe is in the same
        family, so counting both would count each file twice, with the walk's whole count landing
        at the instant the walk ended: a Scan pace fifty times over at the start of a scan,
        converging downwards for ten minutes.

        Worse than a duplicate: what the family has LEFT is counted as files nothing has read yet,
        so taking a file in moves it INTO the pile rather than out of it. A pace that counts the
        creation of work as the completion of work divides the wrong number by the wrong number.

        Counted from `enqueue_child` rather than declared per job type, because that call IS the act
        of handing work on: there is nothing to keep in step, and a pass that gains a fan-out job
        later is right without anybody remembering this exists. A handler that enqueues through the
        queue directly is outside it, which is the same hole `parent_id` has and for the same
        reason: a child that is nobody's child is not rolled up anywhere.
        """
        return max(0, self.units - self._handed_on)

    async def report_progress(self, fraction: float) -> None:
        """Report progress from inside a loop over many small steps, writing rarely.

        `set_progress` is a write transaction; this writes at most once per
        `PROGRESS_INTERVAL_SECONDS`, and always for the last step.
        """
        now = time.monotonic()
        if fraction < 1.0 and now - self._progress_written < PROGRESS_INTERVAL_SECONDS:
            return
        self._progress_written = now
        await self.set_progress(fraction)

    async def set_note(self, note: str) -> None:
        """Say what this job did, in a sentence: what a bar cannot, such as finding nothing to do."""
        self.noted = note
        await self.queue.set_note(self.job.id, self.worker_id, note)

    async def enqueue_child(
        self, job_type: str, payload: Mapping[str, Any] | None = None, **options: Any
    ) -> str:
        """Fan out, as a scan hands out a probe per file; the child's progress rolls up into this job.

        A child in this job's OWN family is work handed on rather than work done, and is counted as
        such. See `units_done`. A child in another family is not: the two are counted separately
        on the screen and in the ledger, so a scan that asks for a thumbnail has not thereby done
        part of the thumbnail.

        `OTHER` is not a family and cannot be compared as one (a backup and a transcode are both
        in it and neither is handing work to the other), so for an unfamilied job the question is
        asked of the JOB TYPE instead: such a job leaves a run of its own, and a pass that fans out
        to more of itself would otherwise count every file twice in it.
        """
        mine = family_of(self.job.type)
        same = job_type == self.job.type if mine is Family.OTHER else family_of(job_type) is mine
        if same:
            handed = options.get("units", 1)
            self._handed_on += max(0, handed) if isinstance(handed, int) else 1
        # WORK HANDED ON IS STILL THE PRESS IT CAME FROM. A pass somebody pressed "now" hands out
        # pages and per-file tasks of its own family, and those are the pass: they run now too, and
        # a pass pressed "Run during quiet hours" pauses with its children at the close. Work in ANOTHER
        # family is not: a Scan pressed by somebody hands out thumbnails that follow Generate's
        # own When, for the reason `requested_by` stops at the same line. Asked by FAMILY here,
        # OTHER included, and not by the counting rule above: a stash-box sweep's per-file lookups
        # are a different type in the same unfamilied work, and they are the sweep somebody pressed.
        if self.job.timing is not None and family_of(job_type) is mine:
            options.setdefault("at", self.job.timing)
        return await self.queue.enqueue(job_type, payload, parent_id=self.job.id, **options)

    async def hold_own_family(self, *, spared: Sequence[str]) -> None:
        """Keep this job's family waiting until `lift_own_hold` or the job ends."""
        await self.queue.hold_family(self.job.id, spared=spared)

    def lift_own_hold(self) -> bool:
        return self.queue.lift_hold(self.job.id)

    async def raise_if_canceled(self) -> None:
        """Stop, if the job has been cancelled or taken away. Also counts as a heartbeat.

        Worth calling between the expensive steps of a long handler. Not calling it is safe:
        the worker cancels a handler that has lost its job anyway, but it takes until the next
        heartbeat, and nothing the handler writes in the meantime can land regardless.

        It also refreshes what `stopping` answers, because it is the same beat: a handler that
        calls this in its loop learns about a pause as promptly as it learns about a cancel.
        """
        beat = await self.queue.beat(self.job.id, self.worker_id)
        if beat is None:
            self._stop = STOP_TO_CANCEL
            raise JobCanceled(f"job {self.job.id} is no longer this worker's")
        self._stop = beat.stop


def _check_concurrency(concurrency: int) -> None:
    if concurrency < 1:
        raise ValueError("a worker pool needs at least one worker")


def _check_limits(limits: Mapping[str, int] | None) -> None:
    """Refuse a nonsense cap. Zero is not nonsense (see below).

    Recognition's "only overnight" setting resolves to zero outside its hours, deliberately, because
    stopping is what that setting means. Refused, it would become a `ValueError` inside the pool's
    own reconfigure, where it is caught, logged and dropped, so **every** live setting would stop
    taking effect, including the worker count.

    So zero means paused, and only a negative is a mistake.
    """
    for job_type, limit in (limits or {}).items():
        if limit < 0:
            raise ValueError(
                f"the limit for job type {job_type!r} is {limit}, and a negative cap is not a "
                "smaller one. Use 0 to pause the type, or leave it out to leave it uncapped."
            )


Handler = Callable[[JobContext], Awaitable[None]]

# Registered by the features that own the work, at boot. Process-global, like the schema registry
# and for the same reason: which handlers exist is a property of the code that is running, not of
# any particular database or queue.
_HANDLERS: dict[str, Handler] = {}


#: What each job type is called on screen, declared where the handler is.
#:
#: A name is required rather than optional, and that is the whole design. A map of type to name kept
#: on the dashboard would show every job it did not know by its internal name (`compress_sample`,
#: `reclassify`) to whoever was reading the queue. A map on the screen cannot be right,
#: because the screen does not own the work and would be wrong the first time a job was renamed.
#: Declared here, a job type cannot exist without a name for it.
_NAMES: dict[str, str] = {}

#: Which long pass each job type belongs to, declared where the handler is (see `families`).
_FAMILIES: dict[str, Family] = {}

#: What each job type comes AFTER, declared where the handler is. See `register_handler`.
#:
#: **The order a file's work happens in is a decision, not an accident of the code.** A probe hands
#: out a thumbnail, a hover clip, a scrub strip, a face pass and a description in one loop; they
#: share a priority and a second, so without this the order they ran in would be the order of a
#: tuple in one module. Reordering the tuple would change the library's behaviour with nothing
#: anywhere saying so, and the repaired-copy job, which is a whole second copy of the file, would go
#: out BEFORE the thumbnail the grid is waiting on, because it is enqueued a few lines earlier.
#:
#: A type names the type it follows, the chain gives every type a rank, and the fan-out hands its
#: children out in rank order. What that buys is exact and worth stating: the queue is claimed in
#: `(priority, id)` order, ids are minted under a floor that never goes down, and the
#: children of one parent share a priority, so children handed out in rank order are CLAIMED in
#: rank order, whatever else lands in the same second.
#:
#: **Deliberately not folded into the priority**, which was the other way to write it. Adding a
#: small rank to each child's priority number would make the rank beat arrival for the whole
#: library, so every thumbnail in a hundred thousand files would run before any face pass, and a
#: library that keeps receiving files would never recognise a face at all. Rank is about the order
#: of one file's work; priority is about whose work it is. Two questions, two fields.
_FOLLOWS: dict[str, str] = {}

#: The most urgent a job type ever runs, as a priority number, for the types that have one.
#:
#: **One urgency per kind of work, enforced where the work is declared rather than at each place
#: that asks for it.** The duplicate sweep and the shoots pass are each asked for in two ways (a
#: button and a settle a minute after an import), and two urgencies would give one pass two. With
#: `alone=True` beside it that is worse than untidy: the settle's row and the button's row are two
#: rows of a pass that may only have one running, so the library is swept twice, back to back, for
#: one answer. `BACKGROUND_PRIORITY` names these three passes by name as the work nobody is sitting
#: in front of; this is what holds them to it however they are asked for.
#:
#: A clamp rather than a default, because a default can be overridden by a caller that does not know
#: it is doing it.
_URGENCY: dict[str, int] = {}

#: The job types that are claimed whether or not their family can run on this machine.
#:
#: Readiness is declared per FAMILY because a capability belongs to a feature, and two kinds of
#: job sit in a family without needing the capability. The one that FETCHES the thing the family is
#: waiting for is already outside it (`face_fetch_weights` and `semantic_fetch_models` are both
#: `OTHER`, which is what stops the queue holding back the only job that could ever release it);
#: the other is the Build's own two types, which coordinate several products and are in the family
#: their bar is drawn under. An Identify run asked for the meaning of a library, on a machine with
#: no face weights, is work that can be done, so it is not held back over a capability only one
#: of its products wants. Each product reads its own switch and its own readiness inside.
_NOT_GATED: set[str] = set()

#: The job types whose payload names the PRODUCTS a task makes (`products: [...]`), so a screen
#: can say which family's work is running rather than which coordinator enqueued it: a Build
#: filtered to Smart Search is Smart Search's work, whatever its task type is called.
_CARRIERS: set[str] = set()

#: The job types that may only have ONE running at a time, declared where the handler is.
#:
#: **It is a property of the work, not a setting of the machine.** A pass that reads the whole
#: library and writes one answer (the duplicate sweep, the People suggestions, the shoots look)
#: does exactly the same work twice when two of them run together, and there is no machine on which
#: that is worth doing. The per-type caps next door come from the settings and answer a
#: different question (how much of this machine may this kind of work take); this one cannot be
#: turned up, because there is nothing to turn up to.
#:
#: A follow-on is asked for by `enqueue_when_settled`, which collapses onto a job that is still
#: WAITING and deliberately not onto one that is RUNNING. During an import the watcher fires a scan
#: per folder and each settles into two whole-library passes, so without this the waiting row is
#: claimed within seconds, which frees the collapse and lets the next settle queue another, until
#: several copies of one pass hold the pool while file reads sit unclaimed.
#:
#: Declared here, the claim leaves the waiting row alone until the running one is done, so the
#: shape a settle-driven pass actually needs falls out of it: one running, at most one waiting
#: behind it, and everything that settles in the meantime collapses onto that one. Work that
#: arrived during the pass is covered by the run after it.
_ALONE: set[str] = set()

#: The job types that have the whole queue to themselves: while one is running nothing else is
#: claimed, and while one is waiting it is the only thing that can be. For work that MEASURES the
#: machine (the benchmark of this device), whose answer is worthless if anything else was using it
#: at the time. Read by the queue's claim (`JobQueue.claim`), where the hold is.
_EXCLUSIVE: set[str] = set()

#: The job types that go after EVERY other piece of one file's work, declared or not.
#:
#: A chain cannot say "last": `follows` names one type, and the passes owned by other features
#: (faces, meaning, marks, the sound fingerprint) declare nothing, so they sort at
#: `UNDECLARED_RANK`, AFTER every declared type. A repaired copy declared to follow the scrub strip
#: would go out before a single face pass or description, while the reason it sits at the end of
#: its own feature's chain is that it is a whole second copy of the file and nothing on any screen
#: waits for it. That reason is not about the pictures; it is about everything.
#:
#: So a type can say it trails, where its handler is registered, and the feature that knows why
#: needs to know nothing about what else exists. Nothing else names it, and the composition root
#: does not have to learn one feature's reasoning to order another's.
_TRAILS: set[str] = set()

#: What one file of a kind of work is called under a bar: "files looked at for faces".
#:
#: A family's bar is several kinds of work added together (Identify is the faces pass AND the
#: watermark read) and a single "9,000 of 200,000" over the two is a figure about neither: the
#: second number is the library counted twice. Each kind that has a count of its own is drawn as
#: its own line, and this is the line's words, declared beside the handler so the feature that
#: knows what its work IS says what a finished file of it means. A type that declares none is
#: captioned with its `name`.
_COUNTS: dict[str, str] = {}

#: THE WORK SIFT DOES IN THE BACKGROUND, left off the list of what is happening now on Activity.
#:
#: Checking for a new version and the backup are the machine's own upkeep: nobody waits on them and
#: nobody opens Activity to watch one. Drawn there, a check that takes a second and a backup that
#: comes round once a night would sit among the work somebody is watching, and each one's next run
#: would wait on the list as a queued row for the whole of the day. Each still keeps its row on the
#: Tasks screen (its schedule, its last run, its Run now), which reads the job row directly, and
#: History records what it did. Declared where the handler is, so the feature that knows its work is
#: upkeep says so, and the listing reads the one set (`unlisted_job_types`).
_UNLISTED: set[str] = set()

#: THE WORK THAT RUNS BY ITSELF AS FILES ARRIVE, left off Activity's Now unless a run holds it.
#:
#: A file landing hands out its own chain (the read, the thumbnail, the hover clip, the scrub
#: strip, the fingerprints, the face scan, the description), and the fingerprint pass fills in
#: what a scan left behind a page at a time. Nobody pressed any of it and no task started it as a
#: run, yet each piece that heads its own row would sit on Now as one: a library taking in files
#: would draw "Fingerprinting for duplicates, Done" every minute, and the rows somebody is watching
#: would scroll away under it. So a row of one of these types that heads its own family and was
#: pressed by nobody is left off Now while it is waiting, running or done. A step of a download, a
#: scan or a Build stays folded in that run's row; a failed or canceled one stays listed, because
#: the bulk actions act on every failed and every canceled row and a count that left some out
#: would name a pile they do not clear. The type is still offered in the list's Type choice and a
#: caller naming it reads every row; the family's bar and History's run line say what it did.
#: Declared where the handler is, like `_UNLISTED`, and read through `by_itself_job_types`.
_BY_ITSELF: set[str] = set()


def register_handler(
    job_type: str,
    handler: Handler,
    *,
    name: str,
    family: Family = Family.OTHER,
    alone: bool = False,
    follows: str | None = None,
    trails: bool = False,
    counts: str | None = None,
    urgency: int | None = None,
    needs_ready: bool = True,
    carries_products: bool = False,
    unlisted: bool = False,
    by_itself: bool = False,
    exclusive: bool = False,
) -> None:
    """Claim a job type, and say what it is called. Registering the same one twice is a bug, not
     an override.

     `name` is what a person reads in the queue: a verb and its object, impersonal, with no article:
    "Generating thumbnail", "Scanning folder". It is required so that adding a job cannot ship
     its internal name to the screen by omission.

     `family` is which of the long passes the job is part of, for the screen's bars, the ledger's
     runs and the estimate of time left. `OTHER` is the honest default for housekeeping.

     `alone` says only one of this type may run at a time. See `_ALONE`. For a pass that reads
     the whole library and writes one answer, where a second copy repeats the first one's work.

     `follows` names the job type this one comes after when both are handed out for the same file.
    See `_FOLLOWS`. The type named need not be registered yet, and need never be: a chain is
     read when it is asked for, so the order two features declare their handlers in cannot change
     the order their work runs in.

     `trails=True` says this type goes after every other piece of one file's work, whatever it
     declared. See `_TRAILS`. It takes the place of `follows`, and naming both is refused.

     `counts` is what one file of this work is called under a bar (see `_COUNTS`).

     `urgency` is the most urgent this kind of work ever runs, as a priority number. A caller
     asking for anything more urgent is held to it (see `_URGENCY`).

     `needs_ready=False` says this type is claimed whether or not its family can run here. See
     `_NOT_GATED`. For a job that coordinates several products rather than doing the family's own
     work.

     `carries_products=True` says the payload of every job of this type names the products the
     task makes, under `products`, so the Activity screen can lay the work at each product's
     family rather than at the coordinator's. See `registered_product_carriers`.

     `unlisted=True` says this type is background upkeep, left off Activity's list of what is
     happening now. See `_UNLISTED`.

     `by_itself=True` says this type is work that runs by itself as files arrive: a row of it that
     heads its own family and that nobody pressed is left off Now while it waits, runs or is done.
     See `_BY_ITSELF`. Naming both this and `unlisted` is refused: a type is either never listed or
     listed inside the run that holds it.

     `exclusive=True` says this type has the queue to itself while it waits or runs. See
     `_EXCLUSIVE`.
    """
    if job_type in _HANDLERS:
        raise ValueError(f"a handler for job type {job_type!r} is already registered")
    if not name.strip():
        raise ValueError(f"job type {job_type!r} needs a name to show on screen")
    if follows == job_type:
        raise ValueError(f"job type {job_type!r} cannot follow itself")
    if unlisted and by_itself:
        raise ValueError(
            f"job type {job_type!r} is either upkeep that is never listed or work that runs by"
            " itself, not both"
        )
    if trails and follows is not None:
        raise ValueError(
            f"job type {job_type!r} trails everything, so it follows nothing in particular"
        )
    _HANDLERS[job_type] = handler
    _NAMES[job_type] = name
    _FAMILIES[job_type] = family
    if follows is not None:
        _FOLLOWS[job_type] = follows
    else:
        _FOLLOWS.pop(job_type, None)
    if urgency is not None:
        _URGENCY[job_type] = urgency
    else:
        _URGENCY.pop(job_type, None)
    if needs_ready:
        _NOT_GATED.discard(job_type)
    else:
        _NOT_GATED.add(job_type)
    if carries_products:
        _CARRIERS.add(job_type)
    else:
        _CARRIERS.discard(job_type)
    if alone:
        _ALONE.add(job_type)
    else:
        _ALONE.discard(job_type)
    if trails:
        _TRAILS.add(job_type)
    else:
        _TRAILS.discard(job_type)
    if counts is not None:
        _COUNTS[job_type] = counts
    else:
        _COUNTS.pop(job_type, None)
    if unlisted:
        _UNLISTED.add(job_type)
    else:
        _UNLISTED.discard(job_type)
    if by_itself:
        _BY_ITSELF.add(job_type)
    else:
        _BY_ITSELF.discard(job_type)
    if exclusive:
        _EXCLUSIVE.add(job_type)
    else:
        _EXCLUSIVE.discard(job_type)


def registered_handlers() -> dict[str, Handler]:
    """The registry, copied. Nothing mutates it through here."""
    return dict(_HANDLERS)


def registered_families() -> dict[str, Family]:
    """Which family every claimed job type belongs to, copied."""
    return dict(_FAMILIES)


def counted_as(job_type: str) -> str:
    """What one file of this work is called under a bar: its declared words, or its name."""
    return _COUNTS.get(job_type) or _NAMES.get(job_type, job_type)


def registered_alone() -> set[str]:
    """The job types that may only have one running at a time, copied. See `_ALONE`."""
    return set(_ALONE)


def gated_by_readiness(job_type: str) -> bool:
    """Whether this type waits for its family to be able to run here. See `_NOT_GATED`."""
    return job_type not in _NOT_GATED


def unlisted_job_types() -> frozenset[str]:
    """The job types left off Activity's list of what is happening now. See `_UNLISTED`."""
    return frozenset(_UNLISTED)


def by_itself_job_types() -> frozenset[str]:
    """The job types that run by themselves as files arrive. See `_BY_ITSELF`."""
    return frozenset(_BY_ITSELF)


def exclusive_job_types() -> frozenset[str]:
    """The job types that have the queue to themselves. See `_EXCLUSIVE`."""
    return frozenset(_EXCLUSIVE)


def registered_product_carriers() -> frozenset[str]:
    """The job types whose payload names the products a task makes. See `_CARRIERS`."""
    return frozenset(_CARRIERS)


def products_named(job_type: str, payload: Mapping[str, Any]) -> list[str] | None:
    """The products a carrier's payload names, or None for a job that is not a carrier (whose
    product, if it is the maker of one, the ledger knows by its type). See `Ledger.started`."""
    if job_type not in _CARRIERS:
        return None
    named = payload.get("products")
    return [str(one) for one in named] if isinstance(named, list) else []


def registered_follows() -> dict[str, str]:
    """What each job type that declared one comes after, copied. See `_FOLLOWS`."""
    return dict(_FOLLOWS)


def registered_urgency(job_type: str) -> int | None:
    """The most urgent this type ever runs, or None for a type that never said. See `_URGENCY`."""
    return _URGENCY.get(job_type)


#: Failures that mean THIS MACHINE cannot do the work just now, and for how many seconds to hold
#: it, declared by the composition root, which knows both the queue and the thing that fails (a
#: graphics card that stopped answering). Such a failure is a `JobHeld`, not an attempt spent.
_HOLDS: dict[type[BaseException], float] = {}


def hold_on(error: type[BaseException], *, seconds: float) -> None:
    """Declare a failure that holds a job for `seconds` instead of failing it. See `_HOLDS`."""
    if seconds <= 0:
        raise ValueError("a hold needs a positive number of seconds")
    _HOLDS[error] = seconds


def held_for(error: BaseException) -> float | None:
    """How long this failure holds its job, or None where it is an ordinary failure."""
    if isinstance(error, JobHeld):
        return error.retry_in
    for kind, seconds in _HOLDS.items():
        if isinstance(error, kind):
            return seconds
    return None


#: Where a job type that declared nothing sorts. After everything that did, never before it.
#:
#: **Not zero, and the difference is the whole behaviour.** Zero would put every type that never
#: said anything at the FRONT, so adding the declaration to three of a file's eight products would
#: have moved the other five in front of them: a reordering nobody asked for, arriving as the
#: side effect of documenting the ones that were already right. Last is the reading that leaves
#: undeclared work exactly where the list that named it put it.
UNDECLARED_RANK = 1_000_000

#: Where a type that trails sorts: after even the types that declared nothing. See `_TRAILS`.
TRAILING_RANK = UNDECLARED_RANK + 1


def claim_rank(job_type: str) -> int:
    """How far down its declared chain a job type sits, for ordering one file's work.

    1 for a type that follows something nothing else follows, and up from there.
    `UNDECLARED_RANK` for a type that named nothing, and `TRAILING_RANK` for one that trails.

    A cycle is refused rather than walked. It cannot happen through one `register_handler` call
    (that refuses a type that follows itself), and it can happen across two features that each
    declared the other, which is a mistake nobody would find from the outside: the symptom would be
    a hang at the moment a file was handed out. The chain is at most as long as the registry, so
    passing that length is the cycle.
    """
    if job_type in _TRAILS:
        return TRAILING_RANK
    if job_type not in _FOLLOWS:
        return UNDECLARED_RANK
    rank = 1
    seen = _FOLLOWS[job_type]
    while seen in _FOLLOWS:
        seen = _FOLLOWS[seen]
        rank += 1
        if rank > len(_FOLLOWS):
            raise ValueError(f"the job types {job_type!r} follows run in a circle")
    return rank


def in_claim_order(job_types: Sequence[str]) -> list[str]:
    """These job types in the order their work should be handed out. See `_FOLLOWS`.

    A stable sort, so types that declared nothing keep the order they were given in, which is how
    a list the composition root names stays a declaration in its own right rather than being
    silently reshuffled by a rank it never set.
    """
    return sorted(job_types, key=claim_rank)


def family_of(job_type: str) -> Family:
    """Which family one job type belongs to; `OTHER` for a type nothing has claimed."""
    return _FAMILIES.get(job_type, Family.OTHER)


def registered_job_names() -> dict[str, str]:
    """What every claimed job type is called on screen, copied."""
    return dict(_NAMES)


def job_name(job_type: str) -> str:
    """What one job type is called, or its own name when nothing has claimed it.

    The fallback exists for a row left in the queue by a version that had a job this one does not:
    the work cannot run, and a blank line would say less about that than the type does.
    """
    return _NAMES.get(job_type, job_type)


@dataclass(slots=True)
class _Worker:
    """One running worker task and the switch that retires it, without touching the others.

    A single pool-wide stop ends everything at shutdown. Retiring *one* worker (which is what
    shrinking the pool while it runs means) needs a switch per worker, so the ones that stay are
    untouched and the one that goes finishes the job in its hand first.
    """

    task: asyncio.Task[None]
    stop: asyncio.Event


class WorkerPool:
    """Runs jobs until told to stop, and resizes itself while it runs.

    `concurrency` is how many run together. It is asked for rather than decided here: the right
    number depends on the machine, and this class has no business knowing what machine it is on.

    `limits` caps particular types below that: one transcode at a time however many workers are
    free, because two transcodes on a small box is slower than one, and a burst of them would
    otherwise fill every worker and starve everything else.

    Both are live. `reconcile` changes either without a restart, so the person running Sift can dial
    the machine's effort up or down and have it take effect on the jobs already queued. When a
    `read_config` reader is supplied it is polled on a timer and the pool converges on whatever it
    returns: the same pull-and-settle shape the settings themselves use, so there is no push to
    wire from the settings screen and nothing to go stale. `reconcile` is safe to call directly too,
    which is what the tests do.
    """

    def __init__(
        self,
        queue: JobQueue,
        *,
        concurrency: int,
        capabilities: SystemCapabilities | None = None,
        limits: Mapping[str, int] | None = None,
        poll_interval: float = IDLE_POLL_SECONDS,
        heartbeat_interval: float = HEARTBEAT_SECONDS,
        shutdown_grace: float = SHUTDOWN_GRACE_SECONDS,
        watchdog: bool = True,
        watchdog_interval: float = SWEEP_INTERVAL_SECONDS,
        stale_after: int = STALE_AFTER_SECONDS,
        read_config: Callable[[], Awaitable[tuple[int, Mapping[str, int]]]] | None = None,
        reconcile_interval: float = RECONFIGURE_SECONDS,
        ledger: Ledger | None = None,
        woken_by: Sequence[Listen] = (),
    ) -> None:
        _check_concurrency(concurrency)
        _check_limits(limits)

        self._queue = queue
        self._concurrency = concurrency
        self._capabilities = capabilities
        self._limits = dict(limits or {})
        self._poll_interval = poll_interval
        self._heartbeat_interval = heartbeat_interval
        self._shutdown_grace = shutdown_grace
        self._watchdog = watchdog
        self._watchdog_interval = watchdog_interval
        self._stale_after = stale_after
        self._read_config = read_config
        self._reconcile_interval = reconcile_interval
        #: Where what each run cost is written down, or None for a pool nobody is keeping books on.
        self._ledger = ledger
        self._stop = asyncio.Event()
        self._waking = Waking(woken_by)
        #: The workers that should be running. Mutated only from `reconcile` and `start`/`stop`, all
        #: of which are synchronous up to the point they hand off, so the set never changes under
        #: an await, and nothing has to lock it.
        self._workers: list[_Worker] = []
        #: Workers told to retire that are still draining a last job. They are off the roster above
        #: the instant they are signalled, so a re-grow does not count them, but they are held here
        #: until they exit so shutdown can wait for them and their tasks are not orphaned.
        self._retiring: list[_Worker] = []
        self._watchdog_task: asyncio.Task[None] | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        #: The switch that makes each running job's heartbeat beat NOW, by job id. Set when somebody
        #: asks a running job to stop (see `_stop_asked`), so a pause or a cancel reaches the
        #: handler in the time one beat takes rather than up to a whole heartbeat interval later.
        #: Entries live exactly as long as the attempt: added and removed in `_run`, with no await
        #: between the removal and the attempt ending.
        self._wake: dict[str, asyncio.Event] = {}
        #: How this pool stops hearing the queue's stop requests. None while it is not running.
        self._unlisten: Callable[[], None] | None = None

    @property
    def concurrency(self) -> int:
        """How many workers are meant to be running right now."""
        return self._concurrency

    @property
    def limits(self) -> dict[str, int]:
        """The per-type caps in force right now, copied."""
        return dict(self._limits)

    @property
    def workspaces(self) -> Workspaces | None:
        """Where each job keeps what it has half-written, or None for a pool built without them.

        The same instance the handlers are handed, so a screen asking how much a paused job kept
        reads the directory the job wrote into and not a second idea of where that is.
        """
        return None if self._capabilities is None else self._capabilities.workspaces

    async def start(self) -> None:
        if self._workers or self._watchdog_task or self._supervisor_task:
            raise RuntimeError("the worker pool is already running")

        self._stop.clear()
        await self._sweep_stale_workspaces()
        self._unlisten = self._queue.listen_for_stops(self._stop_asked)
        self._waking.listen(self._queue.listen_for_work)
        for _ in range(self._concurrency):
            self._spawn_worker()
        if self._watchdog:
            self._watchdog_task = asyncio.create_task(
                run_watchdog(
                    self._queue,
                    self._stop,
                    stale_after=self._stale_after,
                    interval=self._watchdog_interval,
                ),
                name="jobs.watchdog",
            )
        if self._read_config is not None:
            self._supervisor_task = asyncio.create_task(
                self._supervise(self._read_config), name="jobs.pool.super"
            )
        log.info("jobs.pool.start", worker_count=self._concurrency, limits=self._limits)

    async def _sweep_stale_workspaces(self) -> None:
        """Remove the working directories of jobs that are not coming back, at boot.

        The per-job sweep at the end of an attempt covers every job a worker actually finished. It
        cannot cover the two that end with nobody running them: a paused job somebody then cancels
        (the cancel is a row somewhere else, and there is no worker to notice) and a job whose
        process died between doing the work and recording it. Both leave a directory nothing owns.

        Boot is where that is answered, because it is the one moment the question has a stable
        answer: nothing is claimed yet, so a directory whose job is not still outstanding is one
        nothing is going to want again. A failure here is logged and nothing else: the pool has to
        start whatever the state of a cache directory.
        """
        workspaces = None if self._capabilities is None else self._capabilities.workspaces
        if workspaces is None:
            return
        try:
            # Off the loop: a cache directory can hold thousands of entries, and this runs at boot
            # on whatever disk the cache is on.
            named = await asyncio.to_thread(_directories_under, workspaces.root)
            if named:
                workspaces.sweep_all_but(await self._queue.unfinished_among(named))
        except FileNotFoundError:
            return  # nothing has ever asked for a workspace on this machine
        except Exception:
            log.exception("jobs.workspaces.sweep_failed")

    async def stop(self) -> None:
        """Stop taking work, let what is in flight finish, then go.

        A job that does not finish in time is handed back rather than left where it stood, and the
        attempt it was charged when it was claimed is handed back with it. An attempt is counted at
        claim time, which is what stops a handler that kills the process being retried forever,
        but it means a restart charges a job for something that was not its fault. Deploy a few
        times during a pass over a large library and the file being read at each of those moments
        is eventually given up on permanently, having never once done anything wrong.

        A shutdown is the one interruption this process knows is its own doing, so it is the one
        that can safely say so. A crash or a power cut leaves the rows `running` and boot recovery
        re-runs them, charging the attempt as before.

        Everything is waited on together: the workers that were running, the ones that were part way
        through retiring, and the watchdog and supervisor. A retiring worker is still a worker with
        a job in its hand until it exits, so leaving it out would be dropping the very drain this
        waits for.
        """
        self._stop.set()
        tasks = [worker.task for worker in (*self._workers, *self._retiring)]
        if self._watchdog_task is not None:
            tasks.append(self._watchdog_task)
        if self._supervisor_task is not None:
            tasks.append(self._supervisor_task)
        self._workers = []
        self._retiring = []
        self._watchdog_task = None
        self._supervisor_task = None
        if not tasks:
            self._stop_listening()
            return

        _, unfinished = await asyncio.wait(tasks, timeout=self._shutdown_grace)
        for task in unfinished:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._stop_listening()
        # After the gather, not before: a worker that finished inside its grace has already settled
        # its job to done or failed, so what is still `running` at this point is only what the
        # shutdown actually interrupted.
        await self._queue.release_running()
        log.info("jobs.pool.stop")

    def _stop_listening(self) -> None:
        if self._unlisten is not None:
            self._unlisten()
            self._unlisten = None
        self._waking.stop_listening()

    def _stop_asked(self, job_ids: Sequence[str]) -> None:
        """Somebody just asked these jobs to stop: beat now for any of them this pool is running.

        What the beat hears is the row's answer (pause, cancel, a withdrawn pause, or the claim
        gone), so nothing here decides anything; it only moves the next beat to now, rather than a
        pause taking up to a heartbeat interval (and hundreds of megabytes of a download) to reach
        the handler. Synchronous and cheap, as `listen_for_stops` requires: a cancel of the
        whole queue hands this every id it stopped, and each is one dict lookup.
        """
        for job_id in job_ids:
            wake = self._wake.get(job_id)
            if wake is not None:
                wake.set()

    # --- live reconfigure ------------------------------------------------------------------

    def reconcile(
        self, *, concurrency: int | None = None, limits: Mapping[str, int] | None = None
    ) -> None:
        """Bring the running pool in line with a new worker count, new caps, or both.

        Synchronous on purpose. It rebinds the caps, spawns or retires workers, and returns, with
        no `await` anywhere in it, so the event loop cannot run anything else part way through and
        the worker set is never seen half-changed. The retiring is where the care is: a worker being
        shrunk away is told to stop and left to finish the job it holds, never cancelled out from
        under one.

        Idempotent. Called with the numbers already in force it does nothing, which is what makes it
        safe to poll on a timer.
        """
        if self._stop.is_set():
            # Shutting down. Spawning a worker now would be a task nothing waits for, and retiring
            # one is what stop() is already doing to all of them.
            return
        if limits is not None:
            self._set_limits(limits)
        if concurrency is not None:
            self._set_concurrency(concurrency)

    def _set_limits(self, limits: Mapping[str, int]) -> None:
        _check_limits(limits)
        # A fresh dict, never a mutation of the old one: a worker hands `self._limits` to `claim`
        # and `claim` reads it across awaits, so the dict a claim is holding must not change under
        # it. Rebinding leaves that claim on the old dict and puts the next one on the new: both
        # whole.
        self._limits = dict(limits)

    def _set_concurrency(self, target: int) -> None:
        _check_concurrency(target)
        self._reap_retired()
        current = len(self._workers)
        if target > current:
            for _ in range(target - current):
                self._spawn_worker()
        elif target < current:
            retiring = self._workers[target:]
            self._workers = self._workers[:target]
            for worker in retiring:
                # It finishes the job in its hand and exits at the top of its next loop. Not
                # cancelled: cancelling would abandon that job to be redone at the next boot.
                worker.stop.set()
            self._retiring.extend(retiring)
        self._concurrency = target

    def _spawn_worker(self) -> None:
        stop = asyncio.Event()
        worker_id = new_id()
        task = asyncio.create_task(self._work(worker_id, stop), name=f"jobs.worker.{worker_id}")
        self._workers.append(_Worker(task=task, stop=stop))

    def _reap_retired(self) -> None:
        """Drop the retiring workers that have finished draining. Their tasks return normally (the
        loop swallows every error a worker can hit), so a done one needs nothing but forgetting."""
        self._retiring = [worker for worker in self._retiring if not worker.task.done()]

    def work_arrived(self) -> None:
        self._waking.work_arrived()

    async def _supervise(
        self, read_config: Callable[[], Awaitable[tuple[int, Mapping[str, int]]]]
    ) -> None:
        """Poll the config reader and converge the pool on what it says.

        The reader is passed in rather than read off `self` so it is plainly non-optional here: it
        is only spawned when one was given. A read that fails or a reconcile that raises is logged
        and the loop goes on (a bad settings row must not take the pool's workers down with it)
        and the wait is on the stop event so shutdown does not sit through a full interval.
        """
        while not self._stop.is_set():
            await first_of((self._stop, self._waking.reconfigure), self._reconcile_interval)
            if self._stop.is_set():
                continue  # shutdown began during the wait; the loop condition ends it
            # Cleared before the read, so a press landing during it is another wake, not lost.
            self._waking.reconfigure.clear()
            try:
                concurrency, limits = await read_config()
                self.reconcile(concurrency=concurrency, limits=limits)
            except Exception:
                log.exception("jobs.pool.reconcile_failed")

    # --- The loop --------------------------------------------------------------------------

    async def _work(self, worker_id: str, own_stop: asyncio.Event) -> None:
        """Claim, run, settle, repeat, and survive anything that goes wrong doing it.

        The whole loop is guarded, not just the claim. Writing the *outcome* of a job is a database
        write like any other and can fail like any other, and an exception let out of here kills
        the worker task for good: the pool runs one short for the rest of the process's life, the
        queue quietly stops draining, and nothing anywhere says why. There is no error here worth
        a worker.

        `own_stop` is this worker's own retirement switch, checked at the top of the loop alongside
        the pool-wide one. When the pool is shrunk this worker is told to stop and the check here is
        what lets it finish the job in its hand before it goes: the current `_run` runs to the
        end, and only the *next* iteration sees the switch and returns. It is never cancelled to
        shrink the pool, because cancelling would abandon a job that has to be redone.

        `CancelledError` is not an `Exception` and so passes straight through, which is what makes
        shutdown work.
        """
        while not self._stop.is_set() and not own_stop.is_set():
            try:
                job = await self._queue.claim(worker_id, limits=self._limits)
                if job is None:
                    await self._idle(own_stop)
                    continue
                await self._run(job, worker_id)
            except Exception:
                log.exception("jobs.worker_failed", worker_id=worker_id)
                await self._idle(own_stop)

    async def _idle(self, own_stop: asyncio.Event) -> None:
        """Wait out the poll interval, but wake early for either stop or for work arriving.

        A retiring worker sitting idle would otherwise hang about for a whole poll interval before
        noticing it was told to go. Waking on its own switch as well as the pool's keeps a shrink
        prompt.
        """
        await first_of((self._stop, own_stop, self._waking.arrived), self._poll_interval)

    async def _run(self, job: Job, worker_id: str) -> None:
        # ASKED AGAIN, BECAUSE A SWITCH MOVES WHILE A QUEUE IS FULL. The enqueue is the gate that
        # stops work being written down; it cannot speak for a row already sitting there. A pass
        # over a library hands out thousands of jobs, so switching it off has to stop the ones
        # already queued as well or the switch means "stop in an hour".
        #
        # Cancelled rather than failed: nothing went wrong, and a stopped job belongs in the pile a
        # single press clears, not in the pile that asks somebody to look at it. Cancelling takes
        # the tree with it, which is what stops a walk's children one press behind their parent. A
        # PRESS IS NOT ASKED. The switch says whether work starts on its own; a row somebody
        # pressed, or one handed out by a pressed pass, was started by them. See
        # `Switchboard.refusal`.
        refused = await self._queue.switchboard.refusal(job.type, pressed=job.timing is not None)
        if refused is not None:
            log.info("job.switched_off", job_id=job.id, job_type=job.type, worker_id=worker_id)
            await self._queue.cancel(job.id)
            return

        handler = _HANDLERS.get(job.type)
        if handler is None:
            # The type was in the database before it was taken out of the code: an upgrade that
            # dropped a feature, most likely. Retrying finds the same nothing, so it does not.
            await self._queue.fail(job.id, worker_id, _NO_HANDLER, permanent=True)
            return

        context = JobContext(
            job=job,
            worker_id=worker_id,
            queue=self._queue,
            capabilities=self._capabilities,
            pressed_by=await self._pressed_by(job),
        )
        if self._ledger is not None:
            self._ledger.started(
                job.type,
                requested_by=job.requested_by,
                products=products_named(job.type, job.payload),
            )
        began = time.monotonic()
        wake = self._wake[job.id] = asyncio.Event()
        runner = asyncio.create_task(self._invoke(handler, context), name=f"job.{job.type}")
        beat = asyncio.create_task(self._beat(context, wake), name=f"job.beat.{job.id}")

        try:
            await asyncio.wait({runner, beat}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Three ways out (the handler returned, the heartbeat found the job gone, or the
            # pool cancelled this worker), and none of them may leave either task running.
            # Not ours to remove where the job was reclaimed after losing its heartbeat and another
            # worker of this pool claimed it again before this one wound down: that run's event
            # stands. Only a stale reclaim racing this `finally` reaches the other side.
            if self._wake.get(job.id) is wake:  # pragma: no branch
                del self._wake[job.id]
            beat.cancel()
            if not runner.done():
                runner.cancel()
            await asyncio.gather(runner, beat, return_exceptions=True)

        if runner.cancelled():
            # The heartbeat stopped matching: the job was cancelled, or the watchdog took it back
            # and gave it to someone else. Write nothing. The fence would refuse it anyway, and
            # whatever the row says now is the truth.
            log.info("job.lost", job_id=job.id, job_type=job.type, worker_id=worker_id)
            await self._sweep_workspace(context)
            return

        await self._record(
            job,
            worker_id,
            runner.exception(),
            took_ms=(time.monotonic() - began) * 1000,
            units=context.units_done,
            arrived=context.files_arrived,
            pressed=pressed_job(job.type, job.payload, context.pressed_by, job.started_at),
            noted=context.noted,
        )
        await self._sweep_workspace(context)

    async def _pressed_by(self, job: Job) -> str | None:
        """Who pressed the work this job carries out, or None where Sift started it by itself.

        The job's own `requested_by` where it has one: a press of a file's menu, a selection's
        bar, or a Run task over some files, and work already waiting that a press took over.
        Otherwise the press at the top of its tree, but only where the press reached it: a child
        does not carry `requested_by` (see the schema), and `timing` is what follows a press down
        through its own family and stops at the edge of it (`JobContext.enqueue_child`). So a
        Generate task a pressed Generate run handed out is that press, and the thumbnails a
        pressed Scan's arriving files are given are not: they follow Generate's own When, and
        are Sift's. One read, and only for a job a press reached.
        """
        if job.requested_by is not None:
            return job.requested_by
        if job.timing is None:
            return None
        top = await self._queue.top_of(job.id)
        if top is None:
            return None
        top_type, presser = top
        return presser if family_of(top_type) is family_of(job.type) else None

    async def _sweep_workspace(self, context: JobContext) -> None:
        """Remove this job's working directory, unless the job is coming back to it.

        THE ROW IS ASKED rather than the outcome guessed at, and the difference is the point: a
        handler that raised may have been retried into `queued` by the attempt just recorded, a
        paused one is waiting to be started again, and both of those still own what they wrote. Only
        a job that has reached `done`, `failed` or `canceled` has finished with it.

        Costs a point read, and only for the handlers that asked for a workspace at all, which is
        the download and nothing else today. A job that never asked has no directory to remove and
        nothing to ask about.
        """
        workspaces = None if self._capabilities is None else self._capabilities.workspaces
        if workspaces is None or not context.used_a_workspace:
            return
        job = await self._queue.get(context.job.id)
        if job is None or job.state in TERMINAL_STATES:
            workspaces.sweep(context.job.id)

    async def _record(
        self,
        job: Job,
        worker_id: str,
        error: BaseException | None,
        *,
        took_ms: float = 0.0,
        units: int = 1,
        arrived: int = 0,
        pressed: Pressed | None = None,
        noted: str | None = None,
    ) -> None:
        if isinstance(error, JobCanceled):
            # The handler asked, and stopped. The row already says what it needs to.
            log.info("job.lost", job_id=job.id, job_type=job.type, worker_id=worker_id)
            return

        # A HANDLER THAT DID AS IT WAS ASKED. It saw `stopping()` say pause, wound itself up, and
        # said so, so the row is parked with everything it had, including the workspace below.
        paused = False
        held = False
        state: JobState | None = None
        if isinstance(error, JobPaused):
            landed = await self._queue.pause_running(
                job.id, worker_id, str(error) or _PAUSED_MID_JOB
            )
            paused = landed
        elif error is None:
            # A handler that RETURNED finished the work, and finishing is not something a pause
            # undoes: there is nothing left to stop, so the job is done and the request goes with
            # the claim.
            landed = await self._queue.complete(job.id, worker_id, pressed=pressed)
        elif isinstance(error, JobBlocked):
            landed = await self._queue.block(job.id, worker_id, str(error) or _WAITING_FOR_LOGIN)
        elif (hold := held_for(error)) is not None:
            # Waiting on the machine, not failing: back in the line with the attempt handed back.
            held = await self._queue.hold(job.id, worker_id, str(error), retry_in=hold)
            landed = held
        elif isinstance(error, JobFailedPermanently):
            # No retry can fix it: failed once, for that reason, not "failed too often" later.
            state = await self._queue.fail(job.id, worker_id, str(error), permanent=True)
            landed = state is not None
        else:
            # Raised after ignoring a pause asked of it: `fail` reads the ask off the row, where a
            # pause asked since the last heartbeat is written, and answers `paused`.
            state = await self._queue.fail(job.id, worker_id, f"{type(error).__name__}: {error}")
            landed = state is not None
            paused = state is JobState.PAUSED

        if not landed:
            # The fence refused it: the job stopped being this worker's while the handler was
            # finishing (someone cancelled it, or the watchdog took it back) and the outcome
            # has nowhere to go. Say so. Silently dropping it is how "the job ran, but the row
            # says otherwise" becomes a mystery instead of a log line.
            log.info("job.lost", job_id=job.id, job_type=job.type, worker_id=worker_id)
            return
        # A paused attempt is left out of the books for the reason a blocked one is: the ledger
        # records what work COST, and an attempt somebody stopped part way through is not a run of
        # anything. Counted, it would report the download that was paused at a tenth of a second a
        # file and drag every estimate built on the figure down with it.
        if (
            self._ledger is not None
            and not isinstance(error, JobBlocked)
            and not paused
            and not held
        ):
            await self._account(
                self._ledger,
                job,
                took_ms=took_ms,
                ok=error is None,
                units=units,
                arrived=arrived,
                failed_with=f"{type(error).__name__}: {error}"
                if state is JobState.FAILED
                else None,
                noted=noted,
            )

    async def _account(
        self,
        ledger: Ledger,
        job: Job,
        *,
        took_ms: float,
        ok: bool,
        units: int = 1,
        arrived: int = 0,
        failed_with: str | None = None,
        noted: str | None = None,
    ) -> None:
        """Tell the ledger what this job was about, its file's kind and size read off the file's
        own row where the payload names one. Never the reason a job fails."""
        media_type: str | None = None
        size_bytes: int | None = None
        asset_id = job.payload.get("asset_id")
        if isinstance(asset_id, str) and self._capabilities is not None:
            try:
                asset = await self._capabilities.content.get(asset_id)
            except Exception:
                log.exception("ledger.subject_unread", job_id=job.id)
            else:
                if asset is not None:
                    media_type = str(asset.media_type)
                    size_bytes = asset.size_bytes
        ledger.finished(
            job.type,
            duration_ms=took_ms,
            ok=ok,
            media_type=media_type,
            size_bytes=size_bytes,
            units=units,
            arrived=arrived,
            failed_with=failed_with,
            noted=noted,
        )

    async def _invoke(self, handler: Handler, context: JobContext) -> None:
        # The family rides in the task's context so the timing hook can file each stage of the
        # work against the run it belongs to, without any handler knowing a ledger exists.
        token = CURRENT_FAMILY.set(family_of(context.job.type))
        try:
            with timing_hook("job", job_type=context.job.type, job_id=context.job.id):
                await handler(context)
        finally:
            CURRENT_FAMILY.reset(token)
            self._queue.lift_hold(context.job.id)

    async def _beat(self, context: JobContext, wake: asyncio.Event | None = None) -> None:
        """Keep saying the job is alive, and return the moment it is no longer ours.

        Once a heartbeat interval, or immediately when `wake` is set, which is how a pause or a
        cancel reaches the handler in the time one beat takes (see `_stop_asked`). The event is
        cleared BEFORE the beat is read, so a request landing while the beat is in flight sets it
        again and is read by the next one rather than lost.

        Returning is the signal. The worker waits on this task and the handler's together, so this
        one coming back first is what tells it the job has been taken away.

        A heartbeat that fails to write is not that signal. The database being briefly unavailable
        says nothing about who owns the job, and killing a running job over it would be its own
        outage. If it stays unavailable the beat simply never lands, and the watchdog reclaims the
        row, which is exactly what the watchdog is for.
        """
        job_id, worker_id = context.job.id, context.worker_id
        wake = wake or asyncio.Event()
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=self._heartbeat_interval)
            wake.clear()
            try:
                beat = await self._queue.beat(job_id, worker_id)
            except Exception:
                log.exception("job.heartbeat_failed", job_id=job_id, worker_id=worker_id)
                continue
            if beat is None:
                # The job is somebody else's now, or nobody's. Say so to the handler as well as to
                # the worker: one that is between beats in a loop of its own can stop on the word
                # rather than wait to be cancelled out from under whatever it is holding.
                context.told_to_stop(STOP_TO_CANCEL)
                return
            context.told_to_stop(beat.stop)


def _directories_under(root: Path) -> list[str]:
    """The names of the directories directly under `root`. Raises `FileNotFoundError` like the walk."""
    return [entry.name for entry in root.iterdir() if entry.is_dir()]
