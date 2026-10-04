# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two-tier delete: forget the file, or remove it.

Sift indexes in place and writes nothing into a library. This is the one feature that breaks that
rule, on purpose and only when asked, and everything about the way it is built follows from the
same idea: the safe thing should be the easy thing.

So *remove from Sift*, which forgets the index entry and leaves every byte alone, is the
default in the API, in the confirm dialog, and in the wording of both. *Delete from disk* is a
different operation rather than a stronger setting of the same one: admin-only, refused on any
folder that was not handed over read-write, and permanent.

**There is no bin.** No restore, no purge sweep, no retention preference. What stands in for one
is the interface saying plainly what the button does and asking twice. That is a real trade rather
than a simplification: a bin is a way to take back a mistake, and without one there is nothing
between a wrong click and a file that is not there any more. It is written here so that anybody
adding a caller of `remove` knows exactly what `mode="disk"` means.

Nothing else in Sift removes a file. Other features that need one gone call `remove` here, and a
rule in the build refuses any code outside this package that unlinks, renames, or moves.
"""

from __future__ import annotations

from sift.slices.delete.router import router
from sift.slices.delete.service import (
    DELETER,
    Deleter,
    DeleteRefused,
    Mode,
    NotAllowed,
    NotFound,
)

__all__ = [
    "DELETER",
    "DeleteRefused",
    "Deleter",
    "Mode",
    "NotAllowed",
    "NotFound",
    "router",
]
