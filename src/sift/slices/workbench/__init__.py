# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shell around every queue of work waiting on somebody.

Deliberately thin. It knows what a queue IS (the shape declared in the kernel) and nothing
about what any particular queue holds. A panel arriving in a later version registers itself and
changes nothing in here.

The internal name is `workbench` while the screen is called Organize, and the two are different on
purpose: `organize` is already the feature that renames and moves a file on disk, and one word for
two unrelated things is how a conversation about either of them goes wrong.
"""

from __future__ import annotations

from sift.slices.workbench import schema  # noqa: F401 (imported so the table registers itself)
from sift.slices.workbench.models import (
    BoardView,
    LedgerPage,
    QueueView,
    UndoneView,
)
from sift.slices.workbench.router import ledger_router, router
from sift.slices.workbench.service import SERVICE, NotFound, WorkbenchError, WorkbenchService
from sift.slices.workbench.store import Decision, Store

__all__ = [
    "SERVICE",
    "BoardView",
    "Decision",
    "LedgerPage",
    "NotFound",
    "QueueView",
    "Store",
    "UndoneView",
    "WorkbenchError",
    "WorkbenchService",
    "ledger_router",
    "router",
]
