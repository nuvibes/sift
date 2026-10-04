# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the field registry looks like on the wire."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class FieldDescription(Wire):
    """One field, as a screen renders it."""

    key: str
    subject: str
    label: str
    kind: str
    shown: str
    #: Which half of the record this belongs to: `record` for what somebody wrote about the thing,
    #: `media` for what the file itself measures. A screen that draws the two halves apart reads
    #: this; one that draws them together ignores it.
    group: str = "record"
    editable: bool
    #: Whether an external stash-box may fill this in. Not the same question as `editable`: a
    #: person's usernames are written by an import and are never typed into the record form.
    imported: bool = False
    help: str | None = None
    #: Which of the library's own vocabularies this field completes from, if any: the same token
    #: the query language uses for it, so the form's list and the search box's list are the same
    #: list. Null for a field that is not the name of anything.
    suggests: str | None = None
    #: Which kind of entity the value names, where the value names one with a page behind it. The
    #: record then carries that thing's id under `<key>_id` and the screen draws the name as a way
    #: in to it. Null for a field that is only a word, which is nearly all of them.
    links_to: str | None = None
    #: What one entry of a list is called, for the adder's empty box: "another name", "a tattoo".
    #: Null for a field that is not a list somebody types into.
    entry: str | None = None
    #: Whether the order of a list's entries is stored, so the form offers to move one earlier or
    #: later. False on every list whose order means nothing.
    ordered: bool = False


class FieldRegistry(Wire):
    """Every described field, grouped by what it belongs to, each group in reading order."""

    subjects: dict[str, list[FieldDescription]] = Field(default={})
