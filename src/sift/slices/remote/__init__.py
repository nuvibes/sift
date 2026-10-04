# SPDX-License-Identifier: AGPL-3.0-or-later
"""The phone as a remote: an open player or Theater wall offers itself, and the phone commands it.

The screens are held in memory (`screens.py`); the commands ride the one live connection each tab
already holds, as the second thing on it that carries an answer rather than a question (see
`sift.kernel.changes`). Pairing is the sign-in: a phone sees its own user's screens and no one
else's, and there is no code to exchange and no second socket to secure.
"""

from __future__ import annotations

from sift.slices.remote.router import router
from sift.slices.remote.screens import SCREENS, Screens

__all__ = ["SCREENS", "Screens", "router"]
