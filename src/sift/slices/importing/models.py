# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Importing screen sends and receives."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.wire import Wire


class FolderAnswers(Wire):
    """One library folder, and where it disagrees with the library.

    `answers` holds only the keys this folder overrides. An absent key is not "off": it is
    "whatever the library says", which is a third state and the one almost every folder is in.
    """

    root_id: str
    name: str
    answers: dict[str, bool]


class FolderList(Wire):
    """Every folder, and which switches may be answered per folder.

    `keys` comes from the same map that gates the jobs, so the screen cannot offer a switch that
    nothing reads or miss one that something does.
    """

    folders: list[FolderAnswers]
    keys: list[str]
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="What each of `keys` is called on screen, by key. A key retired into a task's "
        "When (a folder's answer is still stored under it) is called by what a folder's answer "
        "does, which is only what happens as a file is imported.",
    )
    helps: dict[str, str] = Field(
        default_factory=dict,
        description="What a folder's answer to a key does, said under it, by key. Only the keys "
        "that have something to add.",
    )


class SetFolderAnswers(Wire):
    """Change one folder's answers. A key set to null goes back to following the library."""

    answers: dict[str, bool | None]


class BuildRow(Wire):
    """One product on the Build sheet: what it is, whether the switches want it, and what it
    would cost to make for every file that lacks it."""

    key: str
    label: str
    help: str
    #: Whether the library's own switches say this is wanted for files as they arrive. The sheet
    #: ticks the row to match; the person may untick it for one run.
    switched_on: bool
    #: How many files in the library lack it, counted now. Exact, before anything starts.
    files: int
    #: What one file cost a WORKER the last time this machine built this, in seconds, or null where
    #: no Build has finished on this machine yet: a guess would read as a measurement. How hard
    #: the machine worked per file, not how long anybody waited: several jobs run at the same time,
    #: so this is several times the wall clock. Not what the estimate below is made of.
    seconds_per_file: float | None = None
    #: How long `files` of them would take ON THE CLOCK, at the cheapest and at the dearest stretch
    #: of this machine's recent runs that made it: each run's wall time shared between the products
    #: it made, divided by how many files each got. Null where those runs made too few to say.
    #:
    #: Not `files` times the figure above, which is worker-seconds presented as wall time and out by
    #: the number of jobs running at the same time.
    quick_seconds: int | None = None
    slow_seconds: int | None = None
    #: How many jobs ran at the same time during the newest run the window was priced from. It
    #: assumes the next run gets the same, so the sentence on screen says the number rather than
    #: leaving somebody to guess what it was measured under. Null where the run predates this being
    #: recorded, or where there is no run.
    jobs_at_once: int | None = None
    #: How many files this product has given up on (a file that will not decode, one with no
    #: frame to cut), which a Build leaves out. Its own line on the row, with a way to try them
    #: again; folded into `files` they would be offered on every Build for ever.
    #:
    #: COUNTED AS THE WALL IT OPENS COUNTS THEM: the line's count is a link to the Files wall
    #: filtered `left_out:<key>`, so it is that wall's own total for this viewer (the vault's rule
    #: included), never the table's. A count that outran its wall would say, in the difference,
    #: how many files a shut vault holds back. Try again still forgets every one of them.
    cannot: int = 0


class BuildSheet(Wire):
    """What a Build would do, laid out before anybody presses it."""

    rows: list[BuildRow]
    #: How many files lack at least one product the switches want: the union, so a file lacking
    #: two is one file. What the run is weighed by.
    files: int
    #: Files still to be brought forward to the sampled identity. Zero once the
    #: background pass after an upgrade has finished, and on every fresh library.
    identifying: int = 0
    #: Whether a Build is already going or waiting. A second one queued behind it would walk the
    #: same library for the same gaps.
    running: bool = False
    #: Files that are in the library and have never been READ, which is a different fault from a
    #: missing product and has a different answer: a file nobody has read has no dimensions at all,
    #: so nothing can be built for it, and only a scan of its folder can fix it. Counted here
    #: because this is the one thing the Importing pane reads, and these files are drawn on the
    #: wall and included in every total, so a library can be four-fifths unread and look full.
    unread: int = 0
    #: When quiet hours begin, as "HH:MM" on this device's clock: the one range chosen on Tasks,
    #: which a Build asked for "Run during quiet hours" waits for and pauses at the end of.
    night_start: str = "23:00"
    #: Whether this machine has never been measured, so the run measures it first (a few
    #: minutes before the first file) and the sheet can say so before anybody presses.
    measure_first: bool = False


class BuildRequest(Wire):
    """Which rows were ticked, and whether to start now or in quiet hours."""

    products: list[str]
    #: `now`, or `quiet`: wait for quiet hours and pause when they close.
    at: Literal["now", "quiet"] = "now"
    #: An older spelling of `at: "quiet"`, still read so a client that sends it keeps working; the
    #: task's Run now | Run during quiet hours sends `at`.
    tonight: bool = False


class RetryRequest(Wire):
    """Which products to forget the verdicts of, so the next Build offers those files again."""

    products: list[str]


class RetryResult(Wire):
    """How many verdicts were forgotten, per product."""

    forgotten: dict[str, int]


class BuildStarted(Wire):
    """A Build has been asked for. It runs in the background like every other long pass."""

    queued: bool
    #: How many files it will touch, counted before it started. Zero is a real answer and is what
    #: lets the screen say the library is already built rather than offering a button that would
    #: do nothing.
    files: int
    #: The first run queued, kept for the screens that read one. `job_ids` is all of them: a
    #: request naming products of both families starts a Generate run and an Identify run.
    job_id: str | None = None
    job_ids: list[str] = Field(default=[])
    #: When it will begin, as seconds since the epoch, where it was asked for tonight.
    starts_at: int | None = None


#: HOW MANY FILES ONE "Run task" PRESS MAY NAME. The cap every bulk sheet in Sift already holds a
#: selection to (`MAX_BULK_ASSETS` in browse, organize, delete, tags), and for their reason: the
#: request writes one task per file on the one write connection before it answers, and a selection
#: is what a person picked on a wall, which is never a library. Anything wider is the Importing
#: pane's own pass, which walks the library from a worker.
MAX_RUN_FILES = 500


class RunNowPass(Wire):
    """One thing a press can do to a file, in the words Settings uses for it."""

    key: str = Field(description="What the press names. A product's key, or a reading's.")
    label: str = Field(description="The Importing pane's own word for it: 'Thumbnails', 'Faces'.")
    help: str


class RunNowGroup(Wire):
    """One stage of the Importing pane, and the passes under it that can run for one file."""

    family: str = Field(description="scan, generate or identify: the stage, as the queue names it.")
    label: str = Field(description="The stage's press, as Settings words it: 'Identify now'.")
    every: RunNowPass = Field(
        description=(
            "The press that runs every pass below together ('Identify all'), drawn first in the"
            " stage's flyout. Its key names the stage, and the server expands it into the passes."
        )
    )
    passes: list[RunNowPass]


class RunNowPasses(Wire):
    """Every pass a press on a file or a selection can start, grouped the way Importing draws them.

    Only the passes that run PER FILE. The rest of what Importing's stages do (walking the folders,
    looking for near duplicates, grouping faces, suggesting people) is a question about the whole
    library, and one file is not a smaller copy of it.
    """

    groups: list[RunNowGroup]


class RunNowRequest(Wire):
    """Run one pass now, for these files."""

    run: str = Field(
        description="The pass, or a stage's every pass, by the key `RunNowPasses` gave it."
    )
    asset_ids: list[str] = Field(min_length=1, max_length=MAX_RUN_FILES)


class RunNowStarted(Wire):
    """What one press did, counted, and the sentence the screen says about it.

    A stage's every-pass press counts FILES, never tasks: a file handed three passes is one file
    queued, and a file left out of any pass for a reason is counted once under that reason.
    """

    queued: int = Field(description="Files handed to the queue by this press.")
    waiting: int = Field(
        description="Files left out because this same work was already waiting for them."
    )
    had: int = Field(
        description="Files left out because they already have it and the pass does not make it again."
    )
    refused: int = Field(
        description="Files left out because a folder they sit in has this switched off."
    )
    said: str = Field(description="One sentence: what was started, and what was left out and why.")
    passes: list[str] = Field(
        default=[],
        description=(
            "The passes this press queued work for, by key: the one named, or those of a stage's"
            " every-pass press that were not refused or left with nothing to do."
        ),
    )
