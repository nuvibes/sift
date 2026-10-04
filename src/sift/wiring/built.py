# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each build step hands to the steps after it.

Small groups rather than one bag, because what a step needs is a few named things, and taking
the whole application back is how a step comes to depend on something built after it. Frozen, so
a later step cannot quietly replace a part an earlier one settled.
"""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.access import Repository
from sift.kernel.content import ContentStore, LibraryStore, UserStateStore
from sift.kernel.db import Database
from sift.kernel.diagnostics import (
    LoopBacklogWatch,
    LoopWatchdog,
    ReadPoolWatch,
    SlowestWork,
    ThreadPoolWatch,
    WidestReads,
)
from sift.kernel.tunnels import EgressRouter, TunnelStore
from sift.kernel.workbench import Workbench
from sift.slices import download, faces, semantic, watermarks


@dataclass(frozen=True, slots=True)
class Storage:
    """The database and the four ways into it. Everything else is built on these."""

    database: Database
    content: ContentStore
    access: Repository
    library: LibraryStore
    user_state: UserStateStore


@dataclass(frozen=True, slots=True)
class Downloads:
    """The downloader, and the two things that decide how a fetch leaves this machine."""

    secrets: download.SecretStore
    service: download.DownloadService
    tunnels: TunnelStore
    egress: EgressRouter


@dataclass(frozen=True, slots=True)
class Understanding:
    """The long passes that read a whole library, and the board they report to."""

    faces: faces.FaceService
    semantic: semantic.SemanticService
    watermarks: watermarks.WatermarkService
    workbench: Workbench


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """Every way the application can stop being usable, each with something watching it.

    Four of these measure WAITING: for the loop, for a thread, for a connection, and for the
    loop's own queue to drain. The fifth measures the WORK, because a request that waits for
    nothing and does a small thing several thousand times is slow and appears on none of the four.

    The sixth measures none of that. Every reading above is a DURATION, and a duration is a fact
    about the machine as much as about the query, and all five can read healthy through an
    application that is unusable. How many rows a read hands back is the same number busy or idle, is knowable
    before anything is slow, and is what actually decides whether a screen survives an import.
    """

    watchdog: LoopWatchdog
    threads: ThreadPoolWatch
    reads: ReadPoolWatch
    backlog: LoopBacklogWatch
    slowest: SlowestWork
    widest: WidestReads
