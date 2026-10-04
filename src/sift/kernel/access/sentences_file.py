# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lines a file's own thread says: where it came from, what was put on it, and what was asked about it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.access.sentences_pieces import (
    HERE,
    THE_TOP,
    VANTAGE_FILE,
    Line,
    Part,
    Piece,
    _were,
    and_then,
    capitalized,
    folder_named,
    many,
    said,
    text_of,
)
from sift.kernel.access.sentences_who import _BOX_WORDS, _active, from_pass, had_sift

# --- a file's own thread -------------------------------------------------------------------------


def added(
    original_filename: object,
    arrived_in: str | None = None,
    shown: str | None = None,
    folder_id: str | None = None,
) -> Line:
    """The arrival: under the name it arrived with and from the folder it was found in, where known.
    A downloaded file arrived under no name, and the line says only what is known."""
    where: Part = said(" from ", folder_of(arrived_in, shown, folder_id)) if arrived_in else None
    if original_filename:
        return said(f"Sift added this file to the library as {original_filename}", where)
    return said("Sift added this file to the library", where)


def moved_into(to_rel_path: str) -> str:
    """The folder a move landed in: the path's folder, or the top where there is none."""
    if "/" not in to_rel_path:
        return THE_TOP
    return to_rel_path.rsplit("/", 1)[0] or THE_TOP


def folder_of(to_rel_path: str, shown: str | None = None, folder_id: str | None = None) -> Part:
    """The folder a move landed in, as a way there, or plain words for the top of the library.

    `shown` is that folder as the reader may be told it (`kernel.where.folder_said`): where it
    differs, part of the way is one they may not see, so it is said as those words and not linked.
    `folder_id` is the folder's id where it is still here: the link is by its id, and a folder
    that has gone is said in words.
    """
    where = moved_into(to_rel_path)
    if where == THE_TOP:
        return where
    if shown is not None and shown != where:
        return shown
    return where if folder_id is None else folder_named(folder_id, where)


def moved(
    by: str | None, to_rel_path: str, shown: str | None = None, folder_id: str | None = None
) -> Line:
    """Where a file went. See `folder_of` for `shown` and `folder_id`."""
    where = folder_of(to_rel_path, shown, folder_id)
    return _active(by, said("moved this file to ", where), said("this file was moved to ", where))


#: The `file_moves.reason` of a rename that took a download tool's id out of a file's name.
RENAMED_FOR_TOOL_ID = "download_tool_id"

#: Why Sift renamed a file on its own, by the word `file_moves.reason` stores. A person's rename
#: needs no reason given; a word this build has no phrase for says nothing rather than the token.
RENAMED_BECAUSE: Mapping[str, str] = {
    RENAMED_FOR_TOOL_ID: "to remove the download tool's id from its name",
}


def renamed(
    by: str | None, to_rel_path: str, reason: object = None, from_rel_path: object = None
) -> Line:
    """What a file was called, what it is called now, and why, where Sift recorded a reason.

    The old name is the move's own record (`file_moves.from_rel_path`), so the line says what the
    rename changed, as the feed's does ("renamed X from Y"): "renamed this file from a.mp4 to
    b.mp4". A move with no old name, or one whose old name is the new one, says the new name alone.
    """
    name = to_rel_path.rsplit("/", 1)[-1]
    was = str(from_rel_path).rsplit("/", 1)[-1] if from_rel_path else ""
    change = f"from {was} to {name}" if was and was != name else f"to {name}"
    why = RENAMED_BECAUSE.get(str(reason)) if reason is not None else None
    tail = f" {why}" if why else ""
    return _active(by, f"renamed this file {change}{tail}", f"this file was renamed {change}{tail}")


#: What was put back, by the kind of thing it was. The reverser is never recorded, so these are
#: passive: carrying the original actor across would name somebody for an act they may not have
#: taken.
UNDONE: Mapping[str, str] = {
    "move": "That move was undone",
    "rename": "That rename was undone",
    "decision": "That decision was undone",
    "song_name": "That song name was undone",
}


def undone(what: str) -> Line:
    """A move, a rename or a decision that was put back."""
    return said(UNDONE.get(what, "That was undone"))


def named_sentence(
    by: str | None,
    source: str | None,
    who: Sequence[Piece] | str,
    count: int = 1,
    folder: Piece | None = None,
) -> Line:
    """One naming on a file, or a whole press of them said once. `by` None is a NULL source, said in
    the passive; the task that read the name is said at the back, or the folder where known."""
    if by is None:
        return said(who, f" {_were(count)} named here")
    if folder is not None:
        # "Sift named Neve Alder in this file from the folder Shoots": the folder itself, never
        # "a folder name" where the caller knows which.
        where = f" in {HERE[VANTAGE_FILE]}" if count == 1 else " here"
        return said(by, " named ", who, where, " from the folder ", folder)
    tail = "" if source in _BOX_WORDS else from_pass(source)
    if count == 1:
        # ONE ACT, said as the feed says it with "this file" for the file (`FEED`'s naming).
        return said(by, " named ", who, f" in {HERE[VANTAGE_FILE]}", tail)
    return said(by, " named ", who, " here", tail)


def tagged_sentence(
    by: str | None, source: str | None, tag: Sequence[Piece] | str, count: int = 1
) -> Line:
    """One tagging on a file, or a whole press of them: "Sift added the tag beach here ...".

    "Added", the verb table's word for a thing joining a file; the tag is named "the tag" so a name
    that is not an obvious tag still reads as one.
    """
    tail = "" if source in _BOX_WORDS else from_pass(source)
    what = said("the tag ", tag) if count == 1 else said(tag)
    if by is None:
        return capitalized(said(what, f" {_were(count)} added here"))
    if count == 1:
        # ONE ACT, said as the feed says it with "this file" for the file (`FEED`'s tagging).
        return said(by, " added ", what, f" to {HERE[VANTAGE_FILE]}", tail)
    return said(by, " added ", what, " here", tail)


def _filed_where(username: Part, site: Part) -> Line:
    """ "harlowquin on Studio", "Studio" or "harlowquin": whatever the filing can name."""
    if not username:
        return said(site) if site else said("a Site")
    if site:
        return said(username, " on ", site)
    return said(username)


def filed_sentence(
    by: str | None,
    username: Part,
    site: Part,
    source: str | None = None,
    *,
    here: str = "this file",
) -> Line:
    """Where a file was filed: under which username on which Site, and which task decided it.

    The empty username is the library's way of writing "from this Site, poster unknown", so it never
    becomes part of a sentence. A Site can be gone (`usernames.site_id` is `ON DELETE SET NULL`).
    """
    where = _filed_where(username, site)
    tail = "" if source in _BOX_WORDS else from_pass(source)
    return _active(
        by,
        said(f"filed {here} under ", where, tail),
        said(f"{here} was filed under ", where),
    )


def _as_asked(pressed: bool | None) -> str:
    """Whether somebody pressed it, said in words at the end of a box's line, or nothing.

    Silence on an older row is the honest drawing: every match applied before this was recorded
    says nothing about who set it going. Words, not a dash before "by hand": no dash bolts a second
    fact onto the line.
    """
    if pressed is None:
        return ""
    return " when you applied its answer" if pressed else " automatically"


#: HOW SURE A STASH-BOX MATCH WAS, by the grade Sift stored beside it (`asset_stash_box_matches`):
#: its own reading of the evidence, never the box's.
MATCH_GRADES: Mapping[str, str] = {
    "certain": ", a certain match,",
    "likely": ", a likely match,",
    "unsure": ", an unsure match,",
}


def recognized(
    box: str,
    pressed: bool | None,
    wrote: Sequence[str] | None = None,
    *,
    here: str = "this file",
    grade: str | None = None,
) -> Line:
    """A stash-box recognized this file, how sure the match was, and what it wrote, counted.

    `wrote` is the phrases the fold counted, or the run's fields; None is a match from before that
    was recorded, empty a run with nothing to write. `grade` is the match's (`MATCH_GRADES`).
    """
    sure = MATCH_GRADES.get(grade or "", "")
    if wrote is None:
        return said(f"{box} recognized {here}{sure} before Sift recorded what a match writes")
    if not wrote:
        return said(f"{box} recognized {here}{sure} and changed nothing{_as_asked(pressed)}")
    return said(f"{box} recognized {here}{sure} and wrote {and_then(wrote)}{_as_asked(pressed)}")


def open_on(box: str) -> str:
    """The words of the way to a stash-box's own page for what a line is about: "Open on StashDB"."""
    return f"Open on {box}"


def asked_and_found_nothing(box: str, asks: int = 1, *, by: str | None = None) -> Line:
    """A stash-box was asked about this file and had never heard of it: how often, where more.
    `by` is who pressed the last ask, where somebody did (`had_sift`)."""
    if by is not None:
        if asks > 1:
            return had_sift(by, f"ask {box} again", f"nothing matched in {many(asks)} asks")
        return had_sift(by, f"ask {box}", "nothing matched")
    if asks > 1:
        return said(f"Sift asked {box} {many(asks)} times and nothing matched")
    return said(f"Sift asked {box} and nothing matched")


def asked_and_waiting(box: str, *, by: str | None = None) -> Line:
    """A stash-box was asked about this file and matched it, and nobody has answered the match yet:
    the third answer an ask can have, beside a match applied and nothing matched. `by` is who
    pressed the ask, where somebody did."""
    if by is not None:
        return had_sift(by, f"ask {box}", "its match is waiting to be checked")
    return said(f"Sift asked {box} and its match is waiting to be checked")


@dataclass(frozen=True, slots=True)
class Refusals:
    """Why a look for faces refused the ones it found, told apart. Each None where the scan
    predates it, which is "not known" and words as the plainer line.

    `largest` is the long side, in the file's own pixels, of the biggest face refused for size;
    the others split the closer look's refusals by the reason it gave.
    """

    largest: int | None = None
    blurred: int | None = None
    turned: int | None = None
    edge: int | None = None


def _refused_because(small: int | None, closer: int | None, why: Refusals) -> list[str]:
    """The reasons, in the words the line says them: size first, then the closer look's."""
    reasons: list[str] = ["too small"] if small else []
    split = (why.blurred, why.turned, why.edge)
    if closer and None not in split and any(split):
        reasons += [
            words
            for count, words in zip(
                split,
                ("too blurred", "turned too far away", "too far off the edge of the picture"),
                strict=True,
            )
            if count
        ]
    elif closer:
        reasons.append("too unclear")
    return reasons


def looked_for_faces(
    found: int,
    seen: Line,
    small: int | None = None,
    closer: int | None = None,
    why: Refusals | None = None,
    *,
    by: str | None = None,
) -> Line:
    """What a look for faces came to: WHO it found, with a way to each, or why it found nobody.

    `by` is who pressed the look (`had_sift`). `seen` is the people found and the unnamed faces as
    one phrase; the count is the fallback. `small`, `closer` and `why` say which reason a look that
    found nobody had; the one number said is the size of the biggest face too small.
    """
    if found == 0:
        why = why or Refusals()
        reasons = _refused_because(small, closer, why)
        if not reasons:
            if by is not None:
                return had_sift(by, "look for faces here", "it found none")
            return said("Sift looked for faces here and found none")
        faces = "a face" if (small or 0) + (closer or 0) == 1 else "faces"
        listed = reasons[0] if len(reasons) == 1 else ", ".join(reasons[:-1]) + " or " + reasons[-1]
        size = ""
        if small and why.largest:
            size = (
                f", {'the largest ' if faces == 'faces' else ''}{many(why.largest)} pixels across"
            )
        if by is not None:
            return had_sift(
                by, "look for faces here", f"it found {faces} {listed} to recognize{size}"
            )
        return said(f"Sift found {faces} here {listed} to recognize{size}")
    if by is not None:
        return had_sift(by, "look for faces here", said("it found ", seen or many(found)))
    return said("Sift looked for faces here and found ", seen or many(found))


def how_sure(sures: Sequence[float | None]) -> str:
    """ ", 92% sure", ", between 84% and 92% sure", or nothing where no figure is stored."""
    known = sorted(round(one * 100) for one in sures if one is not None)
    if not known:
        return ""
    if known[0] == known[-1]:
        return f", {known[0]}% sure"
    return f", between {known[0]}% and {known[-1]}% sure"


def recognized_face(
    person: Part, where: Part, sures: Sequence[float | None] = (), *, count: int | None = None
) -> Line:
    """ONE face match, said once for every screen that draws it: only the vantage changes.

    On the file: "Sift recognized Ada Lumen here, 75% sure". On her page: "Sift recognized them in
    image.png, 75% sure". In the feed and its Decisions: "Sift recognized Ada Lumen in image.png,
    75% sure". `person` and `where` are the thing or the vantage word; `where` is "here" or "in
    <file>". Several appearances of one person are one act and say how many.
    """
    appearances = len(sures) if count is None else count
    if appearances > 1:
        return said(
            f"Sift recognized {many(appearances)} faces ", where, " as ", person, how_sure(sures)
        )
    return said("Sift recognized ", person, " ", where, how_sure(sures))


def matched_sentence(name: str, sures: Sequence[float | None]) -> str:
    """The file's face-match line as words, for the receipt a scan titles with it."""
    return text_of(recognized_face(name, "here", sures))


def agreed_to_be(person: Part, where: str = "here") -> Line:
    """Somebody confirmed a face as this person. The table records no user, so it is passive."""
    return said(f"A face {where} was confirmed as ", person)


def refused_as(person: Part, where: str = "here") -> Line:
    """Somebody said a face is not this person. Passive, for the same reason."""
    return said(f"A face {where} was marked as not ", person)


#: WHICH VERB MADE A COPY, in the word the person pressed, lower case because the actor comes first.
#: `produced_files.operation` is read straight back out; a word this build has no verb for says
#: "created", which is true of every copy.
COPY_VERBS: Mapping[str, str] = {
    "trim": "trimmed",
    "clip": "clipped",
    "gif": "animated",
    "compress": "compressed",
    "crop": "cropped",
    "resize": "resized",
    "rotate": "rotated",
}

#: The verb for a copy whose operation has no word of its own.
CREATED = "created"


def copy_verb(operation: object) -> str:
    """The verb for one produced row, or `created` where there is no better word."""
    return COPY_VERBS.get(str(operation), CREATED)


def copied_from(by: str | None, operation: object, source: Piece | None) -> Line:
    """What this file was made out of: "You trimmed this file from beach.mp4"."""
    verb = copy_verb(operation)
    origin: Part = source if source is not None else "another file"
    return _active(
        by,
        said(f"{verb} this file from ", origin),
        said(f"this file was {verb} from ", origin),
    )


def copied_into(by: str | None, operation: object, made: Piece) -> Line:
    """What was made out of this file: "You trimmed this file into beach-cut.mp4"."""
    verb = copy_verb(operation)
    if verb == CREATED:
        # "created this file into X" is not a sentence: a copy nothing has a verb for is said
        # the other way round: what was created, and from what.
        return _active(
            by, said("created ", made, " from this file"), said(made, " was created from this file")
        )
    return _active(
        by, said(f"{verb} this file into ", made), said(f"this file was {verb} into ", made)
    )


def shared_with(
    by: str | None, guest: Part, *, share: bool = True, here: str = "this file"
) -> Line:
    """A thing was shared with a guest, or kept private from one. `acl_grants` records who a grant
    is FOR and never who wrote it, so the line is passive; the guest is never the actor."""
    if share:
        return _active(
            by,
            said(f"shared {here} with guest ", guest),
            said(f"{here} was shared with guest ", guest),
        )
    return _active(
        by,
        said(f"kept {here} private from guest ", guest),
        said(f"{here} was kept private from guest ", guest),
    )


def carried_sentence(
    *,
    from_name: str,
    person: str | None = None,
    username: str | None = None,
    site: object = None,
) -> str:
    """What a receipt is titled when an attribution was carried onto a file from a copy of it: a
    stored title, so the file it came from is named and not linked."""
    if person is not None:
        return f"Sift named {person} here, carried from {from_name}"
    where = text_of(_filed_where(username or "", None if site is None else str(site)))
    return f"Sift filed this file under {where}, carried from {from_name}"
