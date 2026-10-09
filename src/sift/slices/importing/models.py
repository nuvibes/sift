# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Importing screen sends and receives."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.wire import Wire


class FolderAnswers(Wire):
    """One library folder, and where it disagrees with the library: an absent key follows it."""

    root_id: str
    name: str
    answers: dict[str, bool]


class FolderList(Wire):
    """Every folder, and which switches may be answered per folder."""

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
    """One product on the Build sheet: what it is, whether wanted, and what making it would cost."""

    key: str
    label: str
    help: str
    #: Whether the library's switches want it for arriving files; the sheet ticks to match.
    switched_on: bool
    #: How many files in the library lack it, counted now.
    files: int
    #: Worker-seconds per file on this machine's last Build, or null before one: not wall time.
    seconds_per_file: float | None = None
    #: Wall time for `files` of them at the quickest and slowest of recent runs, or null.
    quick_seconds: int | None = None
    slow_seconds: int | None = None
    #: Jobs running together in the newest priced run, so the screen can name it; null if unknown.
    jobs_at_once: int | None = None
    #: Files this product gave up on, counted as the `left_out:<key>` wall counts them.
    cannot: int = 0


class BuildSheet(Wire):
    """What a Build would do, laid out before anybody presses it."""

    rows: list[BuildRow]
    #: Files lacking at least one wanted product, each once: what the run is weighed by.
    files: int
    #: Files still to be brought forward to the sampled identity.
    identifying: int = 0
    #: Whether a Build is already going or waiting.
    running: bool = False
    #: Files never read: nothing can be built for them until a scan of their folder reads them.
    unread: int = 0
    #: When quiet hours begin, "HH:MM" on this device's clock.
    night_start: str = "23:00"
    #: Whether this machine was never measured, so the run measures it first.
    measure_first: bool = False


class BuildRequest(Wire):
    """Which rows were ticked, and whether to start now or in quiet hours."""

    products: list[str]
    at: Literal["now", "quiet"] = "now"
    #: An older spelling of `at: "quiet"`, still read for older clients.
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
    #: How many files it will touch, counted before it started; zero is a real answer.
    files: int
    #: The first run queued; `job_ids` holds every one.
    job_id: str | None = None
    job_ids: list[str] = Field(default=[])
    starts_at: int | None = None


#: How many files one "Run task" press may name: the bulk cap, since each file is one write.
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
    """Every pass a press on a file can start, grouped the way Importing draws them."""

    groups: list[RunNowGroup]


class RunNowRequest(Wire):
    """Run one pass now, for these files."""

    run: str = Field(
        description="The pass, or a stage's every pass, by the key `RunNowPasses` gave it."
    )
    asset_ids: list[str] = Field(min_length=1, max_length=MAX_RUN_FILES)


class RunNowStarted(Wire):
    """What one press did, counted in files, and the sentence the screen says about it."""

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
