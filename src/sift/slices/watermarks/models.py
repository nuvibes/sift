# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the endpoints here send back."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class WatermarkStatus(Wire):
    """What the settings screen draws, and what the feature can currently do.

    `enabled` and `ready` are two different things and the screen needs both. Enabled without ready
    is the ordinary state one second after somebody turns it on, and has to read as "fetch the
    models" rather than as something broken.

    There is no `supported`, unlike search-by-meaning: that feature can be impossible on a machine
    whose database cannot hold its index, and this one needs nothing the application does not
    already have.
    """

    enabled: bool
    ready: bool
    device: str
    read_files: int = 0
    marks_found: int = 0
    waiting_files: int = 0
    running_jobs: int = 0
    problem: str | None = None
    installed: list[str] = Field(default=[])


class ModelsFetching(Wire):
    """The job downloading the models."""

    job_id: str


class ReadingsRemoved(Wire):
    """How many marks were thrown away."""

    removed: int
