# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading back what Sift wrote down about itself.

A thin slice, and it owns nothing. The log's two settings (how much it records and how much disk
it may take) are declared in `kernel/log_settings.py` beside the logger they configure, and both
already take effect on a running application. This is the other half: **a way to READ the log
without going to the machine and opening the file**, which an application somebody runs on a box
in a cupboard needs.

Admin-only, all of it, for the reason the rest of Maintenance is: a log line can carry a library
path, and the honest answer to a guest asking what the installation has been doing is no.
"""

from __future__ import annotations

from sift.slices.logs.router import router
from sift.slices.logs.tail import LINE_CAP, MOST_LINES, tail_of

__all__ = ["LINE_CAP", "MOST_LINES", "router", "tail_of"]
