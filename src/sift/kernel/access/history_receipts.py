# SPDX-License-Identifier: AGPL-3.0-or-later
"""A workbench decision as a History line.

A receipt keeps the title it was written with, or is worded from its verb and object where its area
records one. It carries the one link its writer declared, the Undo the workbench can honor, and
what it says it kept or answered, which is how the folds pair it with the lines it stands for.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from sift.kernel.access import sentences as say
from sift.kernel.access.history_actors import _actor_of_act, _Who
from sift.kernel.access.history_line import (
    LINK_KINDS,
    Actor,
    Event,
    FaceAnswer,
    KeptAnswer,
    Link,
    Undo,
    by_of,
    piece_of,
)
from sift.kernel.access.history_reads import _text_or_none
from sift.kernel.access.sentences import (
    A_GONE,
    A_THING,
    SIFT,
    VANTAGE_FILE,
    Line,
    Piece,
    today_words,
)
from sift.kernel.db import Database, Row, in_clause
from sift.kernel.vocabulary import FACE_SAID_NO, FACE_SAID_YES, RECEIPT_FACES, RECEIPT_KEPT

#: WHAT ELSE A RECEIPT NAMED, for the receipts that carry a real verb. See `_receipt_objects`.
#:
#: The asset is left out because the caller already has it: this asks the other half of the question
#: `_DECIDED` answers: that read finds the receipts about this file, and this finds what each of
#: them was about BESIDES the file. Bounded by that read, so it is page-sized however large the
#: table grows.
_RECEIPT_NAMED = (
    "SELECT decision_id, kind, subject_id, name FROM workbench_decision_subjects"
    " WHERE decision_id IN (?*) AND kind <> 'asset'"
)


async def _receipt_objects(
    database: Database, receipt_ids: Sequence[str]
) -> dict[str, tuple[str, str, str | None]]:
    """What each of these receipts was about besides this file, where it still exists.

    Read at all because a receipt that carries a real verb is worded from the ledger's own table
    rather than from its stored title, and a verb with nothing to name says "Added to the library"
    about a file that was added to a Photo Set. So the thing is fetched and the sentence names it.

    One per receipt, which is what the shape of these decisions is: a pass that groups files writes
    the grouping and the files. A receipt naming two of them keeps the first, in the table's own
    order, rather than growing a sentence that lists them.

    Filtered through `subjects_present` for the reason every link out of the record is: an event
    outlives its subject, so a receipt can name a Photo Set somebody has since unmade, and a link to
    it lands on "no such Photo Set", which reads as a broken screen rather than as a library that
    has moved on. A thing that is gone is left out here and the receipt keeps its own title.
    """
    from sift.kernel.access.history_events import subjects_present

    statement, bound = in_clause(_RECEIPT_NAMED, sorted(set(receipt_ids)))
    rows = list(await database.fetch_all(statement, bound))
    if not rows:
        return {}
    wanted: dict[str, list[str]] = {}
    for row in rows:
        wanted.setdefault(str(row["kind"]), []).append(str(row["subject_id"]))
    present = await subjects_present(database, wanted)
    named: dict[str, tuple[str, str, str | None]] = {}
    for row in rows:
        key = str(row["decision_id"])
        kind, subject_id = str(row["kind"]), str(row["subject_id"])
        if key in named or (kind, subject_id) not in present:
            continue
        named[key] = (kind, subject_id, None if row["name"] is None else str(row["name"]))
    return named


#: THE ONE KEY THIS READ TAKES OUT OF A RECEIPT'S OTHERWISE OPAQUE PAYLOAD.
#:
#: **And the opacity is not being broken, which is worth saying plainly because it looks like it
#: is.** The payload is the writing area's own business (how to put its decision BACK), and
#: nothing here understands a face decision or a filing. This is the opposite direction: a receipt
#: that wants its own sentence to carry a way somewhere puts one here, in a shape written down in
#: the kernel, and a receipt that does not puts nothing and draws exactly as it did. Sift reads one
#: key and never the shape around it.
#:
#: It exists because a receipt's sentence is the one line in a history the kernel cannot reword: it
#: was composed by the area, at the moment of the press, and stored. So the WAY THERE cannot be
#: assembled here either: "Sift matched 300 more faces to Ilva Brennan" is a number about a run of
#: files, and the only thing that knows which 300 is the pass that attached them.
_RECEIPT_LINK = "link"


def kept_answers_of(payload: object) -> tuple[tuple[KeptAnswer, str], ...]:
    """The stash-box answers a receipt says it kept, each with how, off the declared key alone.

    `vocabulary.RECEIPT_KEPT` is the one key read, for the reason `_RECEIPT_LINK` gives: the rest of
    the payload is its queue's own business. An entry missing any of its five words is skipped
    rather than half-read: a kept line that meets no receipt still stands, which is the failure
    this is allowed to have. Anything unreadable is nothing.
    """
    if not payload:
        return ()
    try:
        held = json.loads(str(payload))
    except ValueError:
        return ()
    listed = held.get(RECEIPT_KEPT) if isinstance(held, dict) else None
    out: list[tuple[KeptAnswer, str]] = []
    for one in listed if isinstance(listed, list) else []:
        if not isinstance(one, dict):
            continue
        words = [one.get(name) for name in ("subject", "local_id", "box_id", "key", "how")]
        if not all(isinstance(word, str) and word for word in words):
            continue
        subject, local_id, box_id, key, how = (str(word) for word in words)
        out.append(((subject, local_id, box_id, key), how))
    return tuple(out)


def faces_answered_of(payload: object) -> tuple[FaceAnswer, ...]:
    """The faces a receipt says it answered for, off the declared key alone.

    `vocabulary.RECEIPT_FACES` is the one key read, as `kept_answers_of` reads its own: the rest of
    the payload is the faces slice's business. An entry missing a word, or with an answer that is
    neither of the two, is skipped: a face's line that meets no receipt still stands, which is
    what it did before the key existed. Anything unreadable is nothing.
    """
    if not payload:
        return ()
    try:
        held = json.loads(str(payload))
    except ValueError:
        return ()
    listed = held.get(RECEIPT_FACES) if isinstance(held, dict) else None
    out: list[FaceAnswer] = []
    for one in listed if isinstance(listed, list) else []:
        if not isinstance(one, dict):
            continue
        words = [one.get(name) for name in ("person_id", "asset_id", "how")]
        if not all(isinstance(word, str) and word for word in words):
            continue
        person_id, asset_id, how = (str(word) for word in words)
        if how not in (FACE_SAID_YES, FACE_SAID_NO):
            continue
        out.append((person_id, asset_id, how))
    return tuple(out)


def _receipt_link(payload: object) -> Link | None:
    """The way somewhere a receipt wrote into its own payload, or None.

    Four fields, all required, and a payload missing any of them draws no link rather than half of
    one: the kind a client draws from, the id, the WORDS as they appear in the receipt's own title,
    and the address. The words are checked against the title by the caller, because a link a client
    cannot find in the sentence is a link that is never drawn and never noticed.

    Anything unreadable is nothing. A payload is a string a queue wrote and this read has no say
    over its shape, so a row that is not JSON, or is JSON of another shape, is a receipt with no
    link, never an error on a screen somebody opened to find out what happened.
    """
    if not payload:
        return None
    try:
        held = json.loads(str(payload))
    except ValueError:
        return None
    if not isinstance(held, dict):
        return None
    link = held.get(_RECEIPT_LINK)
    if not isinstance(link, dict):
        return None
    kind, link_id = link.get("kind"), link.get("id")
    words, href = link.get("words"), link.get("href")
    if not all(isinstance(one, str) and one for one in (kind, link_id, words, href)):
        return None
    if kind not in LINK_KINDS:
        return None
    return Link(kind=str(kind), id=str(link_id), name=str(words), href=str(href))


#: WHAT A RECEIPT CAN BE ABOUT AND STILL KEEP THE WORDS IT WAS WRITTEN WITH.
#:
#: A person, a tag or a username: the three things a file's own pane already draws a LINE for,
#: out of the link tables next door. The receipt beside that line is the half that says HOW it was
#: decided and how sure: "Sift matched Ilva Brennan here, 92% sure", "Filed under harlowquin on
#: Studio from the file's name", "Sift matched 300 more faces to Ilva Brennan". Reworded from the
#: verb and the object those become "Ilva Brennan was named in this file" and "Filed under
#: harlowquin". True, and each of them the poorer of the two sentences on a pane that then shows
#: only that one, because the richer line is the one the fold drops.
#:
#: Everything else is worded here. A Photo Set a filename pass made has no line of its own on the
#: file (nothing but the receipt records it), so the ledger's verb and object are what there is,
#: and being assembled at READ time is what makes those words improvable rather than frozen in a
#: column, which is the whole argument the ledger was built on.
_KEEPS_ITS_TITLE = ("person", "tag", "username", "site")


def _worded_from_ledger(row: Row, about: tuple[str, str, str | None]) -> bool:
    """Whether this receipt's line is assembled here, or is the words it was stored with.

    Four conditions and each is a thing the assembled sentence would otherwise get wrong:

    - **A verb.** Sixteen of the seventeen areas that write receipts have not been through the
      ledger and say the vague `decided`; a sentence built from that word says less than the title
      it replaced.
    - **Something to name**, which is `about`: the caller asks this only of a receipt whose thing
      is still here. A verb with nothing behind it says "Added to the library" about a file that
      was added to a Photo Set, which is a true sentence about the wrong act. A receipt whose
      object has since been deleted is the same case and keeps its title, which is then the only
      thing left describing what happened.
    - **NOTHING THE PANE ALREADY DRAWS A LINE FOR.** See `_KEEPS_ITS_TITLE`.
    - **NO WAY SOMEWHERE OF ITS OWN.** A receipt that wrote an address into its payload did it
      because its own sentence says something the verb and the object cannot: "Sift matched 300
      more faces to Ilva Brennan" is a number over a run of files, and reworded from `linked` and a
      person it becomes "Ilva Brennan was named in this file", the count gone, and the address
      with nothing left in the line to hang on. The title and the way into it travel together.
    """
    verb = row["verb"]
    if verb is None or str(verb) == "decided":
        return False
    if about[0] in _KEEPS_ITS_TITLE:
        return False
    return _receipt_link(row["payload"]) is None


def _decided_line(
    row: Row, named: Mapping[str, tuple[str, str, str | None]], by: str | None = None
) -> Line:
    """What one receipt says, as pieces.

    ## Why nearly every receipt keeps its stored title

    The title was composed when the decision was taken, and the titles already written are in the
    database for ever. Two things depend on those exact words: `_one_line_per_act` drops a line
    whose whole sentence a title contains, so rewording a title stops the fold and puts the
    duplicate line back on every file it ever folded; and a receipt is a record of what somebody
    READ when they pressed it. Sixteen of the seventeen areas that write receipts have not been
    through the ledger, so their word is still the vague `decided`, and a sentence assembled from
    that word would say less than the title it replaced.

    ## And why the one that carries a real verb does not

    A receipt whose area has said WHAT it did carries a verb and an object like any other event, so
    its line can come out of the one sentence table with every other line, which is what makes the
    words improvable afterwards instead of frozen in a column. The Photo Set a filename pass makes
    is the first of them and the pattern for the rest: the verb is `added`, the object is the set,
    and the file's thread says what the file was added to, with the way to it.

    Both halves are needed before the swap: a verb with nothing to name would say "Added to the
    library" about a file that was added to a Photo Set, which is a true sentence about the wrong
    act. So a receipt whose object has been deleted keeps its title, which is also the only thing
    left that describes what happened.
    """
    about = named.get(str(row["id"]))
    if about is None or not _worded_from_ledger(row, about):
        # Today's words for the few phrases a saved title can carry that are retired (see
        # `sentences.STALE_IN_A_TITLE`).
        title = today_words(str(row["title"]))
        # THE RECEIPT'S OWN WAY SOMEWHERE, where it wrote one down, placed where its declared words
        # sit in the title. The writer DECLARED those words when it wrote both, so this is the
        # receipt saying where its own link goes, not a search for a name that might be anywhere.
        return titled(title, _receipt_link(row["payload"]))
    verb = str(row["verb"])
    kind, subject_id, name = about
    return say.event_said(
        verb,
        by=by or SIFT,
        here=VANTAGE_FILE,
        # Which task took it, said at the back where Sift did: the receipt's own actor columns.
        task=_text_or_none(row["actor_id"]) if row["actor_kind"] in (None, "sift") else None,
        object_kind=kind,
        object_id=subject_id,
        object_name=name,
    ).pieces


def titled(title: str, link: Link | None) -> Line:
    """A stored title as a line, with the one link its writer declared placed on its words."""
    if link is None or not link.name:
        return say.said(title)
    at = title.find(link.name)
    if at < 0:
        return say.said(title)
    return say.said(title[:at], piece_of(link), title[at + len(link.name) :])


#: WHAT A FILE IS CALLED in a line about it, in THE FILE PAGE'S ORDER (`Repository.names_on_disk`,
#: which the file page, Downloads and Insights read): the title somebody typed, then its name in
#: the folder now (the first present copy, the one that would open), then the name it arrived
#: under, then the name of any copy Sift has seen (a file Sift downloaded itself arrived under
#: none, and a file whose every copy is missing still has a name). Not the imported name second,
#: which exists nowhere once a file is renamed on disk and would let History and the file page
#: call one file two things.
_FILE_NAMES = (
    "SELECT a.id AS id, COALESCE(NULLIF(a.title, ''),"
    " (SELECT l.filename FROM asset_locations l WHERE l.asset_id = a.id AND l.status = 'present'"
    " ORDER BY l.first_seen_at, l.id LIMIT 1),"
    " a.original_filename,"
    " (SELECT l.filename FROM asset_locations l WHERE l.asset_id = a.id"
    " ORDER BY l.first_seen_at, l.id LIMIT 1)) AS name"
    " FROM assets a WHERE a.id IN (?*)"
)


async def files_called(database: Database, asset_ids: Sequence[str]) -> dict[str, str]:
    """What each of these files is called in a line about it (`_FILE_NAMES`), for those that are
    still there and are called anything.

    For a writer composing a line before it records one (a song's name shared from one file to
    another names the file it came from, in the receipt's own words), so the line it writes calls
    the file what History calls it, from the one statement that says so.
    """
    if not asset_ids:
        return {}
    statement, bound = in_clause(_FILE_NAMES, sorted(set(asset_ids)))
    return {
        str(row["id"]): str(row["name"])
        for row in await database.fetch_all(statement, bound)
        if row["name"]
    }


#: The word a receipt's saved title uses for the file it was written on. See `the_file_named`.
_HERE_IN_A_TITLE = re.compile(r"\bhere\b")


async def files_of_decisions(database: Database, rows: Sequence[Row]) -> dict[str, Link | str]:
    """The ONE file each of these decisions was about, for a page that is not that file's.

    A receipt's title was written for the file's own pane ("Sift named Ilva Brennan here, 76%
    sure"), and on a person's or a tag's page "here" would mean the page somebody is reading, which
    is not the file. A decision about exactly one file is
    answered with that file as a link, by the name the feed gives it (`history_names.names_now`),
    or with the words for a file that has gone; one about several files, or none, is left out and
    keeps its title, because "here" in it is not one file.

    Two reads for the page, both bounded by it. Every file a receipt on these pages names is one the
    viewer may be shown (the `_DECIDED` reads splice the vault's rule), so a name read here is
    never one this viewer was not already shown.
    """
    from sift.kernel.access.history_events import subjects_of

    if not rows:
        return {}
    subjects = await subjects_of(database, [str(row["id"]) for row in rows])
    # The ONE file, with the name the event wrote down for it where it wrote one: an event outlives
    # its file, and the snapshot is what the record says it was called (the feed's own rule).
    one_file: dict[str, tuple[str, str | None]] = {}
    for decision, things in subjects.items():
        files = {thing.id: thing.name for thing in things if thing.kind == "asset"}
        if len(files) == 1:
            one_file[decision] = next(iter(files.items()))
    if not one_file:
        return {}
    statement, bound = in_clause(_FILE_NAMES, sorted({one for one, _ in one_file.values()}))
    named = {str(row["id"]): row["name"] for row in await database.fetch_all(statement, bound)}
    answer: dict[str, Link | str] = {}
    for decision, (asset_id, snapshot) in one_file.items():
        if asset_id not in named:
            # Gone: its name as the record kept it, as plain words: there is nowhere to go.
            answer[decision] = snapshot or A_GONE["asset"]
            continue
        answer[decision] = Link(
            kind="asset", id=asset_id, name=str(snapshot or named[asset_id] or A_THING["asset"])
        )
    return answer


def the_file_named(line: Line, file: Link | str) -> Line:
    """A saved title's "here" said as the file it meant, linked. See `files_of_decisions`.

    Only the first "here", only as a whole word, and only in the title's own plain words; a title
    with none is returned as it was. The file is PLACED where "here" stood, never searched for.
    """
    named: Piece | str = piece_of(file) if isinstance(file, Link) else file
    for at, one in enumerate(line):
        if one.kind is not None:
            continue
        found = _HERE_IN_A_TITLE.search(one.text)
        if found is None:
            continue
        return say.said(
            *line[:at],
            one.text[: found.start()],
            "in ",
            named,
            one.text[found.end() :],
            *line[at + 1 :],
        )
    return line


def _decision_events(
    rows: Sequence[Row],
    who: _Who,
    *,
    final: frozenset[str],
    named: Mapping[str, tuple[str, str, str | None]] | None = None,
    files: Mapping[str, Link | str] | None = None,
    lines: Mapping[str, Line] | None = None,
    under: Mapping[str, str] | None = None,
) -> list[Event]:
    """A bulk judgement that named this file, plus the moment it was taken back.

    The same two-event shape a move has, and for the same reason: a decision that was reversed keeps
    its place in the order, because a history that quietly loses its reversals reads as though
    nothing ever happened.

    **The Undo is offered on three conditions, and each is a refusal the server would otherwise
    make.** Only to an admin, because the workbench's undo route is admin-only. Only where the
    decision has not already been reversed, which `reversed_at` says outright. And only where the
    queue that wrote it can take any of its decisions back at all (`final`), which is the one the
    record could not answer for itself and had to be handed: a copy released from a disk is gone,
    and offering to put it back would be a button whose only way of saying no is being pressed.

    The reversal names nobody. `workbench_decisions` records who DECIDED and the moment it was
    reversed, and nothing at all about who reversed it, so carrying the decider across would say a
    named user did something it may not have done.
    """
    events: list[Event] = []
    for row in rows:
        actor, name = _actor_of_act(
            _text_or_none(row["actor_kind"]),
            _text_or_none(row["actor_id"]),
            _text_or_none(row["user_id"]),
            who,
        )
        reversed_at = row["reversed_at"]
        offered = who.viewer.is_admin and reversed_at is None and str(row["queue"]) not in final
        line = _decided_line(row, named or {}, by_of(actor, name))
        if lines is not None and str(row["id"]) in lines:
            # THE ACT BUILT FROM THE RECORD, where one is: a face match read on a page that is not
            # the file says the one face-match sentence with this page's word. See
            # `recognized_lines`.
            line = lines[str(row["id"])]
        elif files is not None and str(row["id"]) in files:
            # Read on a page that is not the file: "here" is said as the file. See
            # `files_of_decisions`, which the person's and the entity threads hand in.
            line = the_file_named(line, files[str(row["id"])])
        events.append(
            Event(
                at=int(row["decided_at"]),
                actor=actor,
                actor_name=name,
                kind=_RECEIPT_MARKS.get(str(row["verb"] or ""), "decided"),
                pieces=line,
                undo=Undo(kind="decision", id=str(row["id"])) if offered else None,
                reversed=reversed_at is not None,
                # Its OWN id, always: an Undo is offered on three conditions and this is not one
                # of them. The fold is about which act a line belongs to; whether the act can be
                # taken back is a separate question and a decision this viewer may not undo still
                # absorbs the line that says the same thing.
                receipt=str(row["id"]),
                kept=kept_answers_of(row["payload"]),
                faces=faces_answered_of(row["payload"]),
                stored_words=lines is None or str(row["id"]) not in lines,
                more=(under or {}).get(str(row["id"]), ""),
            )
        )
        if reversed_at is None:
            continue
        events.append(
            Event(
                at=int(reversed_at),
                actor=Actor.SOMEBODY,
                actor_name=None,
                kind="undone",
                # A song name put back is said as one; every other decision as a decision.
                pieces=say.undone("song_name" if str(row["verb"]) == "song_named" else "decision"),
            )
        )
    return events


#: WHICH MARK EACH ACT WEARS. See `KINDS`, which is where each of these words is declared.
#:
#: A word rather than a mark, because the client draws the glyph: what is decided here is which
#: FAMILY an act belongs to. Two verbs share a kind where they are two outcomes of one switch and
#: the sentence says which: a share and a revoke are both `shared`, a forget is a `deleted`.
#: A receipt whose act has a mark of its own wears it rather than the Organize tray: a shared song
#: name is an undoable receipt, but it was never decided in Organize, and a line dressed as one
#: would say "Decided in Organize" of a thing nobody decided.
_RECEIPT_MARKS: Mapping[str, str] = {"song_named": "song_named"}
