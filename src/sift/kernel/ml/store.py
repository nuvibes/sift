# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a library once kept its own copy of the models, named for the passes that step over it.

Older versions kept a copy of the face, Smart Search and watermark models in each library
(`<data folder>/<feature>/models`), and of the graphics-card runtime (`<data folder>/accel`). They
live once per device now, beside the libraries (`Settings.models_dir`). A one-time move carried a
library's copy into the device's store, and is retired.

What it could leave behind stays: a file the store held a different copy of was left in the
library rather than chosen between by deleting, so a sweep over a feature's corner of the data
folder still has to know that folder holds models, not leftovers.
"""

from __future__ import annotations

__all__ = ["LIBRARY_MODELS"]

#: The folder the old rule put under each feature's corner of the data folder. Public because a
#: move could leave files in it (a part that failed to move, or a file the store held a different
#: copy of), and a sweep over that corner has to know they are models rather than leftovers.
LIBRARY_MODELS = "models"
