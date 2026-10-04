# SPDX-License-Identifier: AGPL-3.0-or-later
"""The desktop app on the computer running Sift, as an admin anywhere reaches it.

Starting with Windows and the firewall rule belong to the computer the library runs on, and only
the desktop app there can read or change them. An admin looking at this library from another
computer (the app in client mode, or a browser) acts on THAT computer through these routes: the
backend asks the app that started it, over the link the app opened for it (`shell.py`). The app on
the other computer never acts on its own machine for them.
"""

from __future__ import annotations

from sift.slices.desktop.router import router
from sift.slices.desktop.shell import NoShell, ShellLink, ShellUnreachable

__all__ = ["NoShell", "ShellLink", "ShellUnreachable", "router"]
