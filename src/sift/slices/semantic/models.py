# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the search-by-meaning endpoints send and accept; none carries a vector."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class SemanticStatus(Wire):
    """What the settings screen draws, and what the feature can currently do."""

    supported: bool
    enabled: bool
    ready: bool
    family: str
    device: str
    indexed_frames: int = 0
    described_files: int = 0
    waiting_files: int = 0
    unread_files: int = 0
    running_jobs: int = 0
    problem: str | None = None
    #: Files from a previous model: out of every search until Build describes them again.
    described_by_another_model: int = 0
    installed: list[str] = Field(default=[])


class SemanticAvailable(Wire):
    """Whether searching by meaning can answer anything right now."""

    available: bool


class SemanticCoverage(Wire):
    """How much of the library a search by meaning can currently reach, for this viewer."""

    described: int = 0
    library: int = 0


class ModelsFetchStarted(Wire):
    """The job now downloading the models."""

    job_id: str


class IndexRemoved(Wire):
    """What removing the index will take, and the job taking it."""

    removed_frames: int = 0
    job_id: str | None = None


class SimilarItem(Wire):
    """One file that looks like the one asked about."""

    id: str
    media_type: str = ""
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    art: str | None = None


class SimilarPage(Wire):
    """What looks like this file, and which of the two ways found it."""

    tier: str
    items: list[SimilarItem] = Field(default=[])
