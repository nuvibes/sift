# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the tidy-up section of Maintenance is sent."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class LeftoversView(Wire):
    """One kind of leftover, counted."""

    name: str = Field(description="What to name in a request to run this one.")
    title: str
    detail: str = Field(description="What these are, and what removing them costs.")
    noun: str = Field(description='What one of the things counted is called ("job").')
    nouns: str = Field(description='What more than one is called ("jobs"), for the count\'s noun.')
    count: int | None = Field(
        description=(
            "How many, or null for a count that reads the disk and has never been taken. Ask for "
            "a survey to take one."
        )
    )
    frees_bytes: int | None = Field(
        default=None,
        description=(
            "Disk this would free, or null when the answer is not about disk. Rows in a table "
            "take no space worth quoting."
        ),
    )
    surveyed_at: int | None = Field(
        default=None,
        description=(
            "When a kept count was taken, as a unix time, or null for one taken just now. Only a "
            "count that reads the disk is kept."
        ),
    )


class TidyView(Wire):
    """Everything that could be tidied, having looked and removed nothing."""

    leftovers: list[LeftoversView]
    surveying: bool = Field(
        description="Whether a survey of the counts that read the disk is waiting or under way."
    )


class SurveyStarted(Wire):
    """A survey has been asked for. It runs in the background like every other long pass."""

    queued: bool = Field(
        description="False when one was already waiting or under way, which is not an error."
    )


class TidyResult(Wire):
    """What one run removed, and where everything stands afterwards.

    The fresh survey comes back with the result rather than being asked for separately: removing
    rows strands the files they named, so the counts genuinely do move in ways a client could not
    predict, and a screen that showed its old numbers after a run would be wrong every time.
    """

    removed: int
    leftovers: list[LeftoversView]
    surveying: bool


class OptimizeResult(Wire):
    """What settling the database down actually did.

    Sizes rather than a "done", because this is the one control on the screen whose effect nobody
    can see: the database still works exactly as it did, and the only honest report is a number that
    moved. A run that freed nothing says so with two equal figures rather than with a tick.
    """

    was_bytes: int = Field(description="How large the database file was before.")
    now_bytes: int = Field(description="How large it is now.")
    freed_bytes: int = Field(
        description="The difference, never below zero: a database can legitimately grow slightly."
    )
