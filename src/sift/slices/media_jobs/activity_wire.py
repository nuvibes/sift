# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Activity screen's routes answer with: a page of work, its rows, and its passes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.budget import STEP_BACK_SHARE
from sift.kernel.jobs import (
    JobState,
)
from sift.kernel.wire import Wire


class Stopped(Wire):
    """How many jobs were stopped."""

    stopped: int


class FailureLine(Wire):
    """Why a family failed, in one line: the newest failed row's name, reason, file and tries."""

    name: str = Field(description="What the failed row was doing, in its handler's words.")
    reason: str = Field(
        description="Why, in plain words where the failure is a kind Sift knows, else the last "
        "line of the tool's own words."
    )
    subject: str | None = Field(
        default=None, description="The file it was on, or null for work about no one file."
    )
    attempts: int = Field(description="How many times it was tried.")


class StepSummary(Wire):
    """A top row's family, folded: what it started, counted, and the one state the row shows.

    Sent on every top row of a folded page (`GET /jobs?fold=true`) and on nothing else. The steps
    themselves are not here: they are asked for when somebody opens the row (`GET /jobs/{id}/steps`),
    so a page of fifty downloads costs fifty counts rather than four hundred rows.
    """

    count: int = Field(
        description="How many steps the top started, however deep: a download's probe and the "
        "seven steps under it are 8. The top itself is not one. A floor when `at_least` is true."
    )
    by_state: dict[str, int] = Field(
        description="The steps by state, e.g. {'done': 7, 'running': 1}. A state with no step in "
        "it is absent. Each count stops at `cap`."
    )
    at_least: bool = Field(
        description="True when a count reached `cap` and stopped, so it and `count` are floors: "
        "'1,000+ steps'. Only a whole-library pass is that big."
    )
    cap: int = Field(description="Where each count stops. See `at_least`.")
    state: JobState = Field(
        description="The ONE state the folded row shows, from its own state and every step's: "
        "failed if anything in the family failed (folding never hides a failure), else running, "
        "paused, blocked, queued in that order, else canceled if the top itself was, else done."
    )
    subject: str | None = Field(
        default=None,
        description="The file the row is about, named once: the top's own subject when it has "
        "one, else the one file its steps are all about (a download names the file it made). "
        "Null when the steps are about more than one file, or none.",
    )
    subject_id: str | None = Field(
        default=None, description="That file's id, for the link on the name. Null as `subject` is."
    )
    failure: FailureLine | None = Field(
        default=None, description="The family's newest failure, or null when nothing in it failed."
    )


class JobView(Wire):
    """One row of the dashboard.

    The payload is deliberately not here. It carries ids that mean nothing on screen, so a row
    says what the job is, what it is about, and how it is going, and nothing else.

    `subject` is that "what it is about": the file being worked on, or the folder being scanned.
    Resolved from the payload rather than carried in it, because the payload holds identifiers and
    a name copied into it at the time a job was queued would be the name the file had then. Twenty
    files being read is twenty rows that differ, which is the whole reason the page exists.
    """

    id: str
    parent_id: str | None
    type: str
    #: What this job is called on screen, from the declaration beside its handler.
    name: str
    #: The file or folder it is about, or null for whole-library work that is about no one thing.
    subject: str | None
    #: The file that name belongs to, when it is a file. What it is for is the link on a finished
    #: row: work that has ended is work whose result somebody wants to look at, and the name is the
    #: thing on the row they would reach for. Null for a folder, and null while the file is gone.
    subject_id: str | None = None
    state: JobState
    progress: float
    attempts: int
    max_attempts: int
    error: str | None
    #: What the job did, when it has something to say a progress bar cannot. Mostly absent.
    note: str | None
    #: When a job becomes eligible to be taken, as unix SECONDS, or null for one that is eligible
    #: now. A job waiting for its moment is not work in progress: without this the indicator that
    #: says Sift is busy would turn for the whole minute a settled batch waits, with nothing
    #: running.
    run_after: int | None
    #: WHERE IT IS IN THE LINE: 1 is the next job a free worker takes. Null for anything not
    #: waiting in the line (running, finished, blocked, or waiting for its moment), because
    #: those are not positions and a nought would read as "next".
    #: Counted in the order the queue is claimed in, so it can go UP as well as down.
    position: int | None = None
    created_at: int
    updated_at: int
    #: The family folded under this row, on a folded page only (`?fold=true`); null on every other
    #: read. See `StepSummary`.
    steps: StepSummary | None = None
    #: Parked until somebody gives the password (`WaitingForPassword`): `error` is the sentence that
    #: says what is sealed, and the row offers the password field beside it. False for every other
    #: wait, which keeps its own words and its own door.
    waits_for_password: bool = False
    #: Why it failed, in one line (see `FailureLine.reason`); null for a row that did not fail.
    reason: str | None = None


class StepsOfJob(Wire):
    """A page of one top row's steps, for the row somebody opened. See `GET /jobs/{id}/steps`."""

    jobs: list[JobView] = Field(
        description="The steps in the order they were handed out, each with its own `parent_id` "
        "(so the tree can be drawn) and its own file name (so a step reads on its own)."
    )
    total: int = Field(description="How many steps there are. A floor when `at_least` is true.")
    at_least: bool = Field(description="True when the count stopped at its cap.")


class KindOfWork(Wire):
    """One kind of job in the run being watched: what is done, what is left, and how quickly."""

    done: int
    outstanding: int
    failed: int
    waiting: int | None = Field(
        default=None,
        description="Files that still need this work, counted from the library rather than from "
        "the queue, so it INCLUDES the outstanding jobs and everything that has no job yet. "
        "Null where nothing can count this kind, in which case a screen falls back to the queue.",
    )
    left_units: float = Field(
        default=0.0,
        description="How many files the outstanding jobs still have in front of them, weighed by"
        " what each has reported done. Files, not rows.",
    )
    total: int | None = Field(
        default=None,
        description="How many files want this kind of work at all, done or not, counted from the "
        "library. The denominator of a bar, and defined whether anything is running or not. Null "
        "where nothing can count it: the folder walk, which has no record of a file nobody has "
        "seen yet.",
    )
    # No rate of finished jobs is sent: a client dividing "files left" by it would be a second
    # estimate of time remaining beside the one `FamilyOfWork` carries from the ledger
    # (`quick_seconds`, `slow_seconds`). A family with no pace yet says nothing about time.


class PartOfWork(Wire):
    """One kind of work inside a family, counted on its own: what its bar and its line say.

    A family adds several kinds together (Identify is the faces pass AND the watermark read),
    and one figure over both ("4,000 of 200,000") is a figure about neither: the denominator is
    the library counted once per kind. Each kind with a count of its own travels here, so the
    screen can say "4,000 of 100,000 files looked at for faces" and "0 of 100,000 files read for
    watermarks" instead.
    """

    type: str
    caption: str = Field(
        description="What one file of this work is called, declared beside its handler: 'files "
        "looked at for faces'. The job's own name where the handler declared no words."
    )
    done: int = Field(description="Files that have this work, counted from the library.")
    total: int = Field(description="Files that want this work at all, done or not.")
    on: bool = Field(
        default=True,
        description="False for a sub-task switched off with none of its work queued: drawn, and "
        "left out of its family's status, count and time left.",
    )
    paused: bool = Field(default=False, description="Whether somebody paused this sub-task.")


class RunPress(Wire):
    """One task Run now presses for a pass: its id on Tasks, and the parts of it, or all of it."""

    task: str
    parts: list[str] | None = None


class FamilyOfWork(Wire):
    """One of the long passes as a whole: which job types it is, and how it is going.

    The families are declared where each handler is registered, so the screen draws whatever the
    server says the families are rather than keeping a list of its own: a list kept on the screen
    can leave a stage in no family, and a stage that takes a worker continuously is then invisible
    on the screen that answers "what is Sift doing".
    """

    label: str
    outstanding: int = Field(
        default=0,
        description="Jobs of this family's own kinds still queued or running, PLUS every task of a "
        "product-carrying kind whose products belong to this family, so a Build narrowed to one "
        "product counts as that product's family, whatever its task is called. The screen reads "
        "this rather than summing `work` by type, which would put a pressed Smart Search under "
        "Identify.",
    )
    waiting: int = Field(
        default=0,
        description="Files that still need this family's work, counted from the library where a "
        "kind can be and from the queue where it cannot, attributed the same way as `outstanding`.",
    )
    types: list[str]
    on: bool = Field(
        default=True,
        description="Whether this pass is switched on. True for a family with no switch, which is "
        "what having no switch means. The KEY is deliberately not sent: the settings screen draws "
        "every switch from the registry it is declared in, so a copy of the key here would be a "
        "field nothing reads.",
    )
    ready: bool = Field(
        default=True,
        description="Whether this family's work can run on this machine at all. False while a "
        "runtime or a set of model files is missing, or while the feature behind it is off.",
    )
    problem: str | None = Field(
        default=None,
        description="Why it cannot run, in the feature's own words, or null. A family whose "
        "answer could not be read is `ready` with no problem: not known is not the same as broken.",
    )
    quick_seconds: int | None = Field(
        default=None,
        description="The quick end of the estimate: what is left, priced at the first quarter of "
        "what an item has been costing, divided by the workers this family can occupy. Null when "
        "the sample cannot say: fewer than twenty items, or none since the settings changed.",
    )
    slow_seconds: int | None = Field(
        default=None,
        description="The slow end of the same estimate. Equal to the quick end where the sample "
        "is runs' own means and there is only one run, which is a figure rather than a range.",
    )
    at_least: bool = Field(
        default=False,
        description="The time left is the least the work takes, priced from the benchmark before "
        "any run of this pass.",
    )
    sample: int = Field(
        default=0,
        description="How many finished items the estimate was priced from. Nought is no estimate, "
        "and it is the honest answer rather than a whole library priced from one file.",
    )
    at_once: int = Field(
        default=0,
        description="How many workers this family can occupy, which the estimate divides by. It "
        "travels with the figure because the figure assumes it.",
    )
    done: int = Field(
        default=0,
        description="Files that have this family's work, counted from the library. With `total` "
        "it is the bar, and both are defined at rest, so a finished library draws a full bar "
        "rather than an empty one whenever nothing is outstanding.",
    )
    total: int = Field(
        default=0, description="Files that want this family's work at all, done or not."
    )
    parts: list[PartOfWork] = Field(
        default_factory=list,
        description="Each kind of this family's work that has a count of its own, in the order "
        "the family's types are listed. `done` and `total` above are these added together; a "
        "screen drawing more than one part draws each on its own line rather than the sum.",
    )
    reason: str | None = Field(
        default=None,
        description="Why this pass is not running, in one sentence, or null while it is. Said "
        "here rather than worked out on the screen: only the server knows whether a family's "
        "types are capped to nothing for the night or held behind another pass.",
    )
    task: str | None = Field(
        description="The task this pass IS on the Tasks screen, by its id (where it is run and "
        "when it runs is chosen), or null for a pass that is more than one task (Identify is "
        "faces and watermarks) or none.",
    )
    time_unknown: str | None = Field(
        default=None,
        description="The sentence the time left says instead of a time, or null: while a folder "
        "waits to be counted, nothing after the walk has a total to price.",
    )
    pace: str | None = Field(
        default=None,
        description="What sets the pace, in one sentence, or null: on the read, the network share "
        "whose readers waited most of the last minute; on a pass after it, the share whose files "
        "are read first. Each named by its library folders.",
    )
    for_task: str | None = Field(
        default=None,
        description="How many files wait for their task's own run, in one sentence said after the "
        "time left, or null: while only arriving files run, the time left is theirs alone.",
    )
    failed: int = Field(
        default=0,
        description="This pass's failed runs in the Failed list that a person can still act on: "
        "none over a folder no longer in the library, and none a later run made good.",
    )
    last_error: str | None = Field(
        default=None,
        description="Why the newest of them failed, in plain words, or null while none stands.",
    )
    running: int = Field(default=0, description="How many of this pass's tasks are running now.")
    paused: bool = Field(
        default=False, description="Whether somebody paused this pass: no new task of it starts."
    )
    runs: list[RunPress] = Field(
        default=[],
        description="What Run now on this pass presses: each task, or the part of it that is this "
        "pass's, leaving out a sub-task switched off.",
    )


class Chore(Wire):
    """One piece of housekeeping on the Activity screen: what it is, how it is going, its last run.

    Beside the passes rather than among them, because the question is the same (is this doing
    anything, and when will it be done?), and the answer comes from the same two places. What is
    NOT the same is the denominator: a pass is measured against the library, and a duplicate sweep
    or a folder pass is measured against nothing at all, so a chore says how its last run went
    rather than how far through it is.

    Shown because housekeeping can take most of a machine's time (a folder pass or a long query),
    and the Activity screen is what answers "what is Sift doing". See `HOUSEKEEPING`.
    """

    job_type: str
    label: str
    running: int = Field(default=0, description="How many of these are being worked on now.")
    outstanding: int = Field(
        default=0, description="How many are queued, running, blocked or held, all together."
    )
    failed: int = Field(default=0, description="How many have failed and are still on the table.")
    quick_seconds: int | None = Field(
        default=None, description="The quick end of how long the outstanding ones will take."
    )
    slow_seconds: int | None = Field(default=None, description="The slow end of the same.")
    last_started_at: int | None = Field(
        default=None,
        description="When the last FINISHED run began, as unix seconds, or null for one that has "
        "never finished one: the run of the chore's task (its own job type where it is no task), "
        "read through the same function as that task's row on Tasks, so the two say one run. A "
        "finished job row is kept for a week, and each kind's newest run for as long as it is the "
        "newest.",
    )
    last_seconds: int | None = Field(
        default=None, description="How long that run took, or null when there is none."
    )
    last_state: str | None = Field(
        default=None, description="How it ended: done, failed or canceled."
    )
    last_error: str | None = Field(
        default=None,
        description="Why that run failed, in plain words keyed by the kind of failure (the tool's "
        "own text stays on the job's row in the Failed list), or null for a run that did not fail.",
    )
    last_job: str | None = Field(
        default=None, description="The job that run was, by id, or null when there is none."
    )
    task: str | None = Field(
        description="The task this chore IS on the Tasks screen, by its id, or null for work that "
        "is no task (a download, a transcode).",
    )
    reason: str | None = Field(
        default=None,
        description="Why this chore is not running although work is outstanding, in one sentence "
        "('Waiting for quiet hours.' while its task waits for them and the range is shut), or "
        "null. The same sentence a long pass carries in its own `reason`.",
    )


class JobsPage(Wire):
    jobs: list[JobView]
    total: int
    #: Every ROW the list could draw, by its own state: a step of a run counted on its own. What
    #: the bulk actions act on and say ("Clear the 1,200 canceled"), which is rows. Not the
    #: tallies above the list, which are `tallies`.
    counts: dict[str, int]
    tallies: dict[str, int] = Field(
        default={},
        description="The numbers above the list, in the one universe the list itself draws: on a "
        "folded page (`fold`), FAMILIES, each counted once under the state its folded row shows "
        "and never a step on its own; on a page of rows, the rows of the kind asked for (`type`, "
        "`older`) or of every kind. Never narrowed by `state`, so every tab reads one answer, and "
        "`all` is the states added together, so All is always their sum. Empty beside "
        "`parent_id`, whose rows no tally is drawn over.",
    )
    #: The same tally split by kind of work, for the summary bars above the table.
    #:
    #: The whole queue, not the page: with fifty rows on screen and the queue holding tens of
    #: thousands, a count of the page is a number nobody asked for (see `counts` below).
    by_type: dict[str, dict[str, int]]
    #: What each kind in `by_type` is called on screen, from the declaration beside its handler: the
    #: choices of the list's Type narrowing, which has no row to read a name off for a kind that is
    #: not on the page in hand.
    names: dict[str, str] = Field(default={})
    #: The kinds in `by_type` this version of Sift has no handler for: rows older releases left
    #: in the table. The Type choice offers them as ONE entry, "Older tasks" (`?older=true`),
    #: never by their stored ids, and `names` does not name them.
    older: list[str] = Field(default=[])
    #: What each kind of job has to do IN THE CURRENT RUN, and how fast it is going.
    #:
    #: The batch, not every row the table remembers (`by_type`): everything created since the
    #: oldest unfinished, due job, so the queue draining completely starts a new one.
    work: dict[str, KindOfWork]
    #: The long passes, each with the kinds it is made of and its estimate of time left.
    families: dict[str, FamilyOfWork] = Field(default={})
    #: The housekeeping beside them, in the order the screen draws it. See `Chore`.
    housekeeping: list[Chore] = Field(default=[])
    stepping_back: bool = Field(
        default=False,
        description="Whether fewer tasks are running than usual because somebody is using the "
        "computer (Settings > Performance). See `kernel.attention`.",
    )
    turbo_mode: bool = Field(
        default=False,
        description="Whether every task is running although somebody is using the computer, "
        "because a person pressed for turbo mode (`POST /jobs/turbo-mode`). Never true "
        "beside `stepping_back`; both are false while nobody is at the keyboard.",
    )
    step_back_share: int = Field(
        default=STEP_BACK_SHARE,
        description="The share of this device, in percent, background work keeps to while it "
        "steps back (Settings > Performance). What the leaf and the Activity line say.",
    )
    step_back_for: Literal["input", "playing", "others"] | None = Field(
        default=None,
        description="Why Sift is in eco mode while `stepping_back` or `turbo_mode` is true: "
        "somebody at this device (input), a video playing in Sift on any device or a Theater "
        "wall open (playing), or other programs busy (others).",
    )
    step_back_over: list[Literal["processor", "graphics", "memory"]] = Field(
        default_factory=list,
        description="What other programs keep busy while `step_back_for` is others: the CPU "
        "(processor), the GPU (graphics) or memory. What the leaf and the Activity line name.",
    )
    password_wanted: int = Field(
        default=0,
        description="How many tasks, over the whole queue, are parked until somebody gives the "
        "password: a saved key sealed since Sift started. What the unlock bar at the top of every "
        "screen follows, so a window told Not now asks again when work stops for the key.",
    )
    paused: bool = Field(
        default=False,
        description="Whether somebody paused the whole queue: no new task starts until Resume.",
    )
