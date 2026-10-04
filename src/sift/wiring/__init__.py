# SPDX-License-Identifier: AGPL-3.0-or-later
"""The composition root, one module per thing it wires.

Every slice is named in this package and nowhere else: a slice never imports another slice, so
these modules are the one place where two features meet. Each module wires one concern and exposes
the steps `lifespan` calls, in order; a step that has to follow another takes that step's result as
an argument, so the order is held by the signatures rather than by a comment.

The wiring is written out. Every router, handler and service is named on a line of its own, and
nothing is found by scanning, so reading these modules is reading the whole application.

Nothing here imports `sift.main`. The server started as `python -m sift.main` runs that module as
`__main__`, and importing it again by name would run its module-level lines a second time.
"""
