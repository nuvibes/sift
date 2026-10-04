# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the search-by-meaning endpoints send and accept.

Nothing here carries a vector: the numbers are large, useless to a screen, and a derived
measurement of somebody's library, so no shape has anywhere to put them.
"""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class SemanticStatus(Wire):
    """What the settings screen draws, and what the feature can currently do.

    Three different states: not supported means no switch will help on this machine; enabled
    without ready is the ordinary moment after turning it on, and reads as "fetch the models".
    """

    supported: bool
    enabled: bool
    ready: bool
    family: str
    device: str
    #: How many moments are described: what the index costs; files are the next field.
    indexed_frames: int = 0
    described_files: int = 0
    waiting_files: int = 0
    #: This feature's unfinished jobs: tells "work left" from "work left and being done", which a
    #: progress bar must not confuse.
    running_jobs: int = 0
    #: Why it cannot run, in words, or null when it can.
    problem: str | None = None
    #: Files described by a previous model: out of every search until Build describes them again,
    #: already counted in `waiting_files`.
    described_by_another_model: int = 0
    installed: list[str] = Field(default=[])


class SemanticAvailable(Wire):
    """Whether searching by meaning can answer anything right now.

    One boolean for anybody signed in, apart from the admin-only status, whose reasons nobody else
    needs: whether the search box offers the control. Asking anyway is safe and gets the ordinary
    order.
    """

    available: bool


class SemanticCoverage(Wire):
    """How much of the library a search by meaning can currently reach.

    Two counts and no percentage, which nobody could check. Scoped to what the asker may see, so
    readable by anybody signed in: the denominator of the honest sentence under results, since
    meaning can only answer from files the background pass has reached.
    """

    #: Files this user can see that the model in use has described.
    described: int = 0
    #: Files this user can see at all. The described ones are among them.
    library: int = 0


class ModelsFetchStarted(Wire):
    """The job now downloading the models.

    The id only: the screen watches the bar and cancel every job already has.
    """

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
    #: The token that lets a browser keep this file's pictures for a week, as on the grid; absent
    #: until the catch-up pass records the derivative. Without it every visit re-fetches the still.
    art: str | None = None


class SimilarPage(Wire):
    """What looks like this file, and which of the two ways found it.

    `tier` is reported, never smoothed over: nearly matching perceptual hashes and a model's
    resemblance are different answers, and a screen must not present them alike.
    """

    tier: str
    items: list[SimilarItem] = Field(default=[])
