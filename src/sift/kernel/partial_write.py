# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telling "the caller did not mention this field" apart from "clear it".

Every partial write in the application faces the same trap: a request model whose fields are all
optional and all default to None is a FULL-row writer wearing a partial writer's clothes. A caller
that sends a title alone reaches a method whose other arguments are None, and None reads as "blank
it", so correcting one field silently erases every field beside it.

So the two meanings get two values. None means clear; this sentinel means the caller
said nothing, and the writer skips the column entirely. It is a TYPE rather than a string or a
module-level `object()` so that confusing the two is a type error at the call site rather than a
blanked column discovered a week later.

Here rather than in whichever slice needed it first. Two slices write partial records now (a
file's, and a handle's). A slice may not import another slice, and a second copy of a sentinel is
two sentinels that are not each other, which fails `isinstance` in a way that looks like the field
simply not being written.
"""

from __future__ import annotations


class Unchanged:
    """ "The caller did not mention this field", which is not the same as "clear it".

    There is one instance and it is `UNCHANGED`; the class is exported only so a signature can
    name it.
    """

    __slots__ = ()


UNCHANGED = Unchanged()

__all__ = ["UNCHANGED", "Unchanged"]
