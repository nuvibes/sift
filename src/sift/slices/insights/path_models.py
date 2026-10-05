# SPDX-License-Identifier: AGPL-3.0-or-later
"""Get to know Sift as it crosses the wire: the learning paths and their goals.

Every sentence is pieces (`HistoryPiece`), built on the server and drawn by the client's
`HistorySentence`, which composes nothing. A title is a short label and crosses as a string.
"""

from __future__ import annotations

from sift.kernel.wire import HistoryPiece, Wire


class Goal(Wire):
    """One goal of a learning path, judged from the record: a first time, or a milestone frozen once."""

    id: str
    title: str
    #: One sentence of help, in Sift's words.
    help: list[HistoryPiece]
    done: bool
    #: When the record says it was first done (seconds), or null when it has not been.
    done_at: int | None = None
    #: Where it is done: a screen in the client.
    href: str


class LearningPath(Wire):
    """One learning path: its name, the sentence that says what it teaches, its goals in order."""

    id: str
    title: str
    sentence: list[HistoryPiece]
    goals: list[Goal]


class Path(Wire):
    """Get to know Sift: every path this User can walk."""

    paths: list[LearningPath]
