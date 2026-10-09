# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each build step hands to the steps after it, in small frozen groups."""

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
    """Every way the application can stop being usable, each with something watching it."""

    watchdog: LoopWatchdog
    threads: ThreadPoolWatch
    reads: ReadPoolWatch
    backlog: LoopBacklogWatch
    slowest: SlowestWork
    widest: WidestReads
