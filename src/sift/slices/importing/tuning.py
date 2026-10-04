# SPDX-License-Identifier: AGPL-3.0-or-later
"""Numbers the catch-up pass is built around."""

from __future__ import annotations

#: How many files of one kind a single catch-up page offers.
#:
#: The same figure the face sweep pages by, and for the same reason: a page is a unit of work
#: somebody can watch land on the dashboard, and one small enough that stopping it loses seconds
#: rather than minutes. It is deliberately not tied to the browse page cap: that number is about
#: what a screen can draw, and tying the two together would make a sweep report the wrong total.
SWEEP_PAGE = 200
