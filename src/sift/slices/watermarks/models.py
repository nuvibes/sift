# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the endpoints here send back."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class WatermarkStatus(Wire):
    """What the settings screen draws, and what the feature can currently do.

    Enabled without ready is the ordinary state just after it is turned on, and reads as "fetch
    the models". No `supported`: this needs nothing the application does not already have.
    """

    enabled: bool
    ready: bool
    device: str
    read_files: int = 0
    marks_found: int = 0
    waiting_files: int = 0
    #: Files not read yet, which `waiting_files` cannot see.
    unread_files: int = 0
    running_jobs: int = 0
    problem: str | None = None
    installed: list[str] = Field(default=[])


class ModelsFetching(Wire):
    """The job downloading the models."""

    job_id: str


class ReadingsRemoved(Wire):
    """How many marks were thrown away."""

    removed: int
