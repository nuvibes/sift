# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lines a delete says, and where what it ended had been."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sift.kernel.access.sentences_pieces import Line, Part, Piece, and_then, many, said
from sift.kernel.text import non_empty_str

#: How many of the things a deleted file was on its own line names before it counts the rest.
MOST_ON = 2

#: What a delete says on the page of something the file was on, after the file's name; a person
#: HAS files, said before the name instead ("their file d0dd.jpg").
DELETED_ON: Mapping[str, str] = {
    "tag": ", which had this tag",
    "site": ", which was filed under it",
    "collection": ", which was in it",
    "photo_set": ", which was in it",
    "song": ", which carried it",
}

#: WHERE A DELETE TOOK THE FILE, by its payload's `from`; an older delete says the plain act.
DELETED_FROM: Mapping[str, str] = {
    "disk": " from the disk",
    "sift": " from Sift only",
}

#: A PICTURE INSIDE AN ARCHIVE, removed from Sift (`{"archive": true}`): its bytes stay in the
#: archive, which Sift does not change, and the scan leaves it out from then on.
DELETED_FROM_ARCHIVE = " from Sift only and kept it out of later scans; its ZIP file wasn't changed"

#: WHY SIFT DELETED A PERSON OR A SITE, by the payload's `why`: the one reason there is today is a
#: stash-box answer that was taken back after it had made the row, which left it on no file.
DELETED_WHY: Mapping[str, str] = {
    "taken_back": ", created by a stash-box answer that was later undone",
}


def deleted_where(payload: Mapping[str, object]) -> str:
    """ " from the disk", " from Sift only", or nothing; a floor task's Photo Set, and a person or
    Site deleted for a reason, say why instead (`DELETED_WHY`)."""
    floor = payload.get("under_floor")
    if isinstance(floor, int) and not isinstance(floor, bool):
        return f" because it had fewer than {many(floor)} photos"
    why = DELETED_WHY.get(non_empty_str(payload.get("why")) or "")
    if why:
        return why
    where = non_empty_str(payload.get("from")) or ""
    if where == "sift" and payload.get("archive") is True:
        return DELETED_FROM_ARCHIVE
    return DELETED_FROM.get(where, "")


def deleted_on(by: str, kind: str, file: Piece | None, payload: Mapping[str, object]) -> Line:
    """A delete read on the page of something the file was on. Where the file went comes LAST,
    after the clause saying why the line is on this page."""
    where = deleted_where(payload)
    if kind == "person":
        if file is None:
            return said(by, " deleted a file of theirs", where)
        return said(by, " deleted their file ", file, where)
    what: Part = file if file is not None else "a file"
    clause = DELETED_ON.get(kind, "")
    return said(by, " deleted ", what, clause, "," if clause and where else "", where)


def deleted_here(by: str, on: Sequence[str], more: int, payload: Mapping[str, object]) -> Line:
    """A delete on the file's own page, naming what the file was on, in words, never a dash."""
    left = more + max(0, len(on) - MOST_ON)
    named = [*on[:MOST_ON], f"{many(left)} more"] if left else list(on[:MOST_ON])
    tail = f", which was on {and_then(named)}" if named else ""
    where = deleted_where(payload)
    return said(by, " deleted this file", tail, "," if tail and where else "", where)
