# SPDX-License-Identifier: AGPL-3.0-or-later
"""Python's crash recorder, switched on by importing this module before any native library.

A process that dies in native code runs no handler of its own; with this on, every thread's stack
goes to standard error first, which the desktop app keeps in its log.
"""

from __future__ import annotations

import faulthandler

faulthandler.enable(all_threads=True)
