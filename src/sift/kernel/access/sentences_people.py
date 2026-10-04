# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lines a person's thread and an entity's thread say."""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.access.sentences_file import _as_asked
from sift.kernel.access.sentences_ledger import MOST_FIELDS, FilledField, filled_fields
from sift.kernel.access.sentences_pieces import (
    _BE,
    _SUBJECT_FORM,
    SIFT,
    YOU,
    Line,
    Part,
    Piece,
    _were,
    and_then,
    capitalized,
    counted_faces,
    files,
    listed,
    many,
    said,
)
from sift.kernel.access.sentences_songs import SONG_FROM_ACOUSTID, SONG_FROM_SAME_MUSIC
from sift.kernel.access.sentences_who import _active, from_pass

# --- a person's own thread -----------------------------------------------------------------------


def person_added(by: str | None, person: Part, via: object = None) -> Line:
    """A person arriving: who added them, and the task that read the name where Sift did."""
    tail = from_pass(via) if by == SIFT else ""
    return _active(
        by,
        said("added ", person, " to the library", tail),
        said(person, " was added to the library"),
    )


def named_on(
    by: str | None,
    source: str | None,
    count: Part,
    folder: Part = None,
    known: int = 0,
    *,
    whom: str = "them",
) -> Line:
    """Who put this person on a run of files, and where the name was read.

    `count` is the number of files, a way to exactly those files. Three folder arms and they say
    exactly what is known: ONE folder answered as them is named; SEVERAL say so, because a link on
    "their folders" would take somebody to one of them; NONE recorded says only what it can.
    """
    if by is None:
        return said(f"{_SUBJECT_FORM.get(whom, 'They')} {_BE.get(whom, 'were')} named on ", count)
    if source == "folder":
        if folder:
            return said(by, f" named {whom} on ", count, " from the folder ", folder)
        if known > 1:
            return said(by, f" named {whom} on ", count, " from their folders")
        return said(by, f" named {whom} on ", count, " from a folder name")
    if source == "username":
        return said(by, f" named {whom} on ", count, " from their username")
    if source == "filename":
        return said(by, f" named {whom} on ", count, " from their name in the file names")
    return said(by, f" named {whom} on ", count)


def faces_agreed(count: Part, faces_count: int) -> Line:
    """Faces somebody confirmed as this person. No user recorded, so passive."""
    return said(count, f" {_were(faces_count)} confirmed as them")


def faces_refused(faces_count: int) -> Line:
    """Faces somebody said are not this person. No wall of them to go to, so no link."""
    return said(f"{counted_faces(faces_count)} {_were(faces_count)} marked as not them")


def taught_by(faces_count: int) -> Line:
    """Pictures Sift now recognizes this person by."""
    return said(f"Sift learned to recognize them from {counted_faces(faces_count)}")


def ruled_out_of(file_count: int) -> Line:
    """Files somebody said this person is not in. No user recorded, so passive."""
    return said(f"They were marked as not in {files(file_count)}")


def box_line(
    box: str,
    pressed: bool | None = None,
    filled: Sequence[str] | None = None,
    whose: str = "its",
    again: bool = False,
    *,
    whom: str = "it",
    named: Sequence[FilledField] | None = None,
) -> tuple[str, Line]:
    """A stash-box that knows this thing, what its press filled in, and WHO the line is about.

    Returns the actor's word beside the line: a press that filled something is the box's act, a
    bare link the linking person's or Sift's. Past `MOST_FIELDS` the fields are counted and listed
    under the line (`filled_folded`); `again` is a re-ask; `named` is the fields with their values.
    """
    linker = YOU if pressed else SIFT if pressed is False else None
    if again and not filled:
        if linker is None:
            return "", said(f"{box} was checked again and had nothing new")
        return linker, said(f"{linker} checked {box} again and it had nothing new")
    if filled is None or not filled:
        tail = "" if filled is None else ", which had nothing new to fill in"
        if linker is None:
            return "", said(
                f"{_SUBJECT_FORM.get(whom, 'It')} {_BE.get(whom, 'was')} linked to {box}{tail}"
            )
        return linker, said(f"{linker} linked {whom} to {box}{tail}")
    folded = filled_folded(filled)
    if folded is None and named:
        return box, said(f"{box} filled in {whose} ", filled_fields(named), _as_asked(pressed))
    what = folded if folded is not None else f"{whose} {and_then(filled)}"
    return box, said(f"{box} filled in {what}{_as_asked(pressed)}")


def filled_folded(filled: Sequence[str] | None) -> str | None:
    """The phrase a box's line counts its fields in, or None where the line names them instead.

    One answer for two readers: the sentence and the heading of the fold under it, so the count in
    the line and the heading over the list cannot come to disagree.
    """
    if filled is None or len(filled) <= MOST_FIELDS:
        return None
    return f"{many(len(filled))} details"


def box_filled_named(box: str, fields: Sequence[FilledField] | None) -> Line:
    """What one box filled in, every field NAMED WITH ITS VALUES, for a list of links whose rows
    already name the thing: "FansDB filled in 7 aliases (Ada, Bea, ...), gender (Female) and
    breast type (Natural)". A run that filled nothing says so; None is a bare link, which fills
    nothing because filling is a separate act."""
    if fields is None:
        return said(f"{box} was linked without filling anything in")
    if not fields:
        return said(f"{box} was linked and had nothing new to fill in")
    return said(f"{box} filled in ", filled_fields(fields))


def linked_before_recorded(box: str, matching: Sequence[FilledField]) -> Line:
    """A link made before Sift recorded what a stash-box fills in, said as exactly that, and which
    of the record's values agree with the box today: agreement, not a claim of who wrote them."""
    line = said(f"{box} was linked before Sift recorded what a stash-box fills in")
    if not matching:
        return line
    # "The record" is the machinery's word, and a History line never says it: the box is the
    # subject, and it still agrees on these today.
    return said(line, ", and agrees on ", filled_fields(matching))


# --- an entity's own thread ----------------------------------------------------------------------


def entity_added(by: str | None, entity: Part, how: Part = "") -> Line:
    """A tag or a Site arriving: "Sift added poolside to the library from a folder name"."""
    return _active(
        by,
        said("added ", entity, " to the library", how),
        said(entity, " was added to the library", how),
    )


def entity_created(by: str | None, entity: Part, how: Part = "") -> Line:
    """A Collection or a Photo Set being made: "Sift created Beach from a download"."""
    return _active(by, said("created ", entity, how), said(entity, " was created", how))


def members_added(by: str | None, what: Part, count: int, how: Part = "") -> Line:
    """Files put in a shelf or a Photo Set, on its own page: "Sift added 6 files to it from a
    folder", or "photo.jpg was added to it" where the row records nobody."""
    return _active(
        by,
        said("added ", what, " to it", how),
        said(what, " was added to it" if count == 1 else " were added to it", how),
    )


def song_named_here(
    by: str | None, what: Part, count: int, source: str | None, origin: Part
) -> Line:
    """Files a song was named on, said on the SONG'S own page, in the family every song's naming
    is said in (`_song_line`): "Sift named this song on 3 files from AcoustID", "... on x.mp4, from
    the same music as y.mp4", "... on x.mp4 from its download page". A name somebody chose by hand
    records nobody, so it is said without an actor: "This song was named on x.mp4".

    `source` is the membership's (`kernel/content/songs.py`); `origin` the file a name carried from
    the same music came from, already scoped by the caller (None where this viewer may not see it,
    which reads "another file").
    """
    if source == "acoustid":
        return said(by or SIFT, " named this song on ", what, SONG_FROM_ACOUSTID)
    if source == "site":
        page = " from its download page" if count == 1 else " from their download pages"
        return said(by or SIFT, " named this song on ", what, page)
    if source == "shared":
        return said(
            by or SIFT,
            " named this song on ",
            what,
            SONG_FROM_SAME_MUSIC,
            origin if origin is not None else "another file",
        )
    return _active(by, said("named this song on ", what), said("this song was named on ", what))


def put_on(by: str | None, source: str | None, count: Part, file_count: int = 1) -> Line:
    """Who put this tag on a run of files, and which task decided it."""
    tail = "" if source in (None, "stash_box") else from_pass(source, file_count)
    return _active(by, said("added it to ", count, tail), said("it was added to ", count))


def filed_under(by: str | None, source: str | None, count: Part, file_count: int) -> Line:
    """Who filed a run of files under this Site, and which task decided it."""
    if source == "folder" and by == SIFT:
        return said("Sift filed ", count, " under it from folder names")
    tail = "" if source in (None, "stash_box") else from_pass(source, file_count)
    return _active(
        by,
        said("filed ", count, " under it", tail),
        said(count, f" {_were(file_count)} filed under it"),
    )


def usernames_added(
    by: str | None, names: Sequence[Piece], how: str = "", *, untold: bool = False
) -> Line:
    """The usernames added to this Site in one day, BY NAME, folded past `FEED_MOST`, and how they
    arrived where recorded. `by` None is passive; `untold` counts backfilled arrivals."""
    one = len(names) == 1
    what = said("the username " if one else "the usernames ", listed([(n,) for n in names]))
    if untold:
        return capitalized(said(what, f" {_were(len(names))} added to it before Sift recorded how"))
    return _active(
        by,
        said("added ", what, " to it", how),
        said(what, f" {_were(len(names))} added to it", how),
    )
