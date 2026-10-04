# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clearing up what a library leaves behind.

A thin slice on purpose. It owns no tables and no rules about what a leftover is: each area
registers its own tidying with the kernel, and this is the surface that shows them and runs them.
Written that way because the alternative (one module that knows how to count face pictures, cache
files, stranded rows and settled failures) would have to import half the application, and would
be the place every future feature has to remember to edit.

Admin-only, all of it, and nothing runs unless somebody asks for it by name.
"""

from __future__ import annotations

from sift.slices.tidy.jobs import TIDY_SURVEY, register_handlers
from sift.slices.tidy.router import router

__all__ = ["TIDY_SURVEY", "register_handlers", "router"]
