# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one rule for "this file has a copy that is there to read", as the fragment every statement
that asks it splices in.

A file whose every copy is marked missing (a drive unplugged, a share that went away) is not
work anything can do: every pass that opens it fails with "none of the known copies of this file
could be opened". So a pass over the library hands such a file nothing, and a count of work still to
do leaves it out, until the copy is marked present again.

Written out by hand in each statement, the rule would be missing from some: with most of a library
on a drive that is not connected, a count would say tens of thousands of hover previews are still
to make while the pass hands out a few hundred. One fragment, so a count and the pass it describes
cannot come to draw two different lines.

It reads the file as `a`, the alias every statement over the file table uses. It is a module
constant, spliced in with `kernel.sql_splice.splice`; nothing that arrives at run time goes into it.
"""

from __future__ import annotations

#: True of the file `a` while at least one of its copies is marked present.
HAS_A_PRESENT_COPY = (
    "EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = a.id AND l.status = 'present')"
)
