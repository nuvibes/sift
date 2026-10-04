# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folding a thread so that one act is one line.

## One act is one line

A pass that files a file writes TWO rows, in two tables, and both on purpose: the filing itself in
`asset_usernames`, and a receipt in `workbench_decisions` so the press can be taken back one file at
a time. Read straight out, that is two lines in a row saying the same thing: "Filed under
<username> on <site> from the file's own name", carrying an Undo, and "Filed under <username> on
<site>" carrying none.

They are folded into one line, and **the rule is the RECEIPT'S OWN ID**. The decision carries it;
so does the link row's line, because the ledger records what the act was done WITH (the username a
file was filed under, the person named in it), and that is the same thing the link row holds. The
survivor is the one that says more: the decision, which says HOW it happened and carries the Undo.
It takes the dropped line's `via` mark and the way to whatever that line named, so nothing leaves
the pane but the repetition. `history_count` follows, because the count is the length of this same
list.

**Not the clock.** The filing and its receipt are written inside one transaction but ask the clock
twice, so about half share a second with their receipt and the rest are a second apart. A fold that
fires on half the rows is worse than no fold, because the pane then looks arbitrary.

**Not the sentence.** A fold on "this line's sentence contains that one" freezes the wording of
every line whose title is already in the database. The ledger's `object` pairs a receipt with a line
instead: a subject says what an act TOUCHED and an object says what it was done WITH. Four writers
say it (the file-name and watermark filings, the face attach, the duplicate carry), and nothing here
reads a word of English.

Only `filed`, `named`, `tagged` and `matched` fold, and the list is closed for the reason `KINDS`
is. A folder claim reading "<person>, 47 files" is a receipt about a FOLDER and pairs with no line
on this file, so it does not fold and must not: the two lines there say different things, and
dropping either would lose the half the other cannot say.

## One SOURCE's act is one line too, and it is a second fold beside the first

The fold above drops a line another line already contains. This one is the other half: a stash-box
that names seventeen people in one press would otherwise write seventeen lines, one under the other,
burying everything that happened to the file before or after them. Nothing there repeats ANOTHER
line (each names a different person), so the receipt test cannot see it.

What makes them one act is the SOURCE. `asset_people.source`, `asset_tags.source` and
`asset_usernames.source` carry the word for the pass that decided the row, and every row a single
pass wrote on a single file is one press. So the namings from one source fold into one line that
counts them, the taggings likewise, and the names go into `detail`, the list the row opens to
show.

**A stash-box's own line absorbs them, and that is why `enriched` has a sentence.** A box that
recognised a file wrote the people, the tags and the site in one act, and its line already says so,
so its namings and taggings are folded INTO it rather than into a line beside it, which would be
the same duplicate attribution the first fold exists to end. The sentence then says what was
written: "Northlight recognized this file and wrote 3 people, the site and 4 tags".

It needs to know WHICH box. A row names its box (`box_id`), and folds into that box's line; a row
naming none folds only where the file has exactly ONE applied match. With two applied boxes and
nothing on the row, nothing is absorbed and the fold by source says "A stash-box named 3 people in
this file" instead: the count is still true and the attribution stops short of naming a box that
may not have done it.

## What is NOT here

**The list is what the FILE's own rows can account for, and it is not every field a box wrote.**
The match's payload is what a box OFFERED, and a sentence built from it would claim a title that was
never written. What CAN be named is what carries the box's word on the file itself (the people,
the tags and the filing), and the title, the details and the dates are written with nothing
recording who wrote them, so they are not claimed. A file with an applied match and no attributable
row says what the run recorded, or that it recorded nothing (see `sentences.recognized`).

The run's own record of the fields it filled in (`enrichment_runs.applied`, catalog version 52) says
the rest, and the box's line takes it from there (`_by_the_box`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from typing import Any

from sift.kernel.access import sentences as say
from sift.kernel.access.history_line import (
    Actor,
    Detail,
    Event,
    FaceAnswer,
    KeptAnswer,
    Link,
    by_of,
    piece_of,
)
from sift.kernel.access.history_receipts import titled
from sift.kernel.access.sentences import SIFT, Line
from sift.kernel.db import Database, Row
from sift.kernel.vocabulary import KEPT_BY_THE_PRESS, LEDGER_QUEUE, VIA_DOWNLOAD


def one_line_per_kept(events: Sequence[Event]) -> list[Event]:
    """ONE LINE PER PRESS for a stash-box answer kept: the kept line and its receipt are one act.

    "Keep yours" writes two rows in one press (the answer, `stash_box_kept`, drawn as "Your title,
    X, was kept over Northlight's Y", and a receipt with the Undo), and a thread reading both would
    draw one above the other: a file's pane, a person's, a Site's and a tag's all read the two rows.

    Matched on the answer itself: the receipt declares what it kept (`vocabulary.RECEIPT_KEPT`,
    carried here as `Event.kept`) and the kept line carries its row's key (`Event.answer`), so no
    sentence and no moment is compared. Only a STANDING receipt drawn on this page counts, the
    newest where several name one answer: a receipt taken back no longer describes the answer, and
    one this reader may not be shown is not on the page to carry it: the kept line then stands on
    its own.

    Either way the receipt is the line and the kept line goes: its moment, its actor, its Undo, and
    the words the area puts together from what it recorded: the words the feed says
    (`worded.decided_said`), with a value said by the one rule (`records.value_said`: "Natural",
    never "NATURAL"). One case more: a receipt too old for its area to word keeps its STORED title,
    which carries the raw values; where that press WAS the keeping, the line takes the kept line's
    words instead, which are built from the record by the same rule. A take's stored title still
    says what it set aside, so it stands as written.
    """
    standing: dict[KeptAnswer, tuple[int, str]] = {}
    for at, one in enumerate(events):
        if one.reversed:
            continue
        for answer, how in one.kept:
            held = standing.get(answer)
            if held is None or (one.at or 0) >= (events[held[0]].at or 0):
                standing[answer] = (at, how)
    if not standing:
        return list(events)
    out = list(events)
    gone: set[int] = set()
    for at, one in enumerate(events):
        if one.kind != "kept_mine" or one.answer is None or one.answer not in standing:
            continue
        into, how = standing[one.answer]
        if how == KEPT_BY_THE_PRESS and out[into].stored_words:
            out[into] = replace(out[into], pieces=one.pieces)
        gone.add(at)
    return [one for at, one in enumerate(out) if at not in gone]


#: The kinds of a face's own line, the ones `one_line_per_face_answer` folds into a receipt.
_FACE_ANSWER_KINDS = frozenset({"confirmed", "rejected"})


def one_line_per_face_answer(
    events: Sequence[Event], *, reword: Callable[[Event, int], Event] | None = None
) -> list[Event]:
    """ONE LINE PER PRESS for a Yes or a No on faces: the answer's own line and its receipt are one.

    A Yes writes `face_confirmations` and a No `face_rejections`, each drawn as its own line ("A
    face here was agreed to be Ada Lumen"; a person's "3 faces were confirmed as them"), and the
    same press writes a receipt with the Undo, so a thread reading both would say "You said 1 face
    is not ..." and "1 face was marked as not them" at the same minute.

    The shape of `one_line_per_kept`, and for its reasons: matched on the answer itself, which the
    receipt declares (`vocabulary.RECEIPT_FACES`, carried as `Event.faces`) and the face's line
    carries, so no sentence and no moment is compared; and only a STANDING receipt on this page
    counts, since one taken back no longer describes the answer and one this reader may not be
    shown is not here to carry it. The receipt is the line: its words, its actor, its Undo.

    Counted, not matched once: one receipt may answer several faces on one file, and a face's line
    on a person's page counts a DAY's faces. Each receipt's face is spent on one face of one line;
    a line all of whose faces are spent goes; a line with some left is said again for what is left
    by `reword` (the person's day line), and one with none spent stands as it was.
    """
    standing: Counter[FaceAnswer] = Counter()
    for one in events:
        if one.receipt is None or one.reversed:
            continue
        standing.update(one.faces)
    if not standing:
        return list(events)
    out: list[Event] = []
    for one in events:
        if one.receipt is not None or not one.faces or one.kind not in _FACE_ANSWER_KINDS:
            out.append(one)
            continue
        left = 0
        for face in one.faces:
            if standing[face] > 0:
                standing[face] -= 1
            else:
                left += 1
        if left == len(one.faces):
            out.append(one)
        elif left:
            out.append(reword(one, left) if reword is not None else one)
    return out


#: The kinds a receipt can have written, which is which lines may be folded into one. See
#: `_one_line_per_act`.
#:
#: Closed and short, for the reason `KINDS` is closed: folding is a line LEAVING the pane, so which
#: lines may leave is a decision written down here rather than a condition that happens to hold.
#: These four are the ones an area writes beside a receipt in the same press (a filing, a naming,
#: a tagging, a match), and the rest are either the receipt itself or something no receipt
#: restates.
#:
#: `matched` is the fourth because the scan path writes a receipt for an automatic attach. A
#: RE-MATCH's receipt is deliberately not that shape: it
#: says "N more faces" about a whole run over many files, is about no single file's act, and both
#: lines stand, which is the split the pass that writes it states in its own words.
_FOLDS_INTO_A_DECISION = ("filed", "named", "tagged", "matched")


#: WHAT AN ATTRIBUTION'S RECEIPT WAS ABOUT, by the kind of line the attribution draws.
#:
#: The ledger's object is the thing an act was done WITH (the person a file was linked to, the
#: username it was filed under, the tag put on it), and it is exactly what the link row beside it
#: holds. So a naming and the receipt for that naming meet on `('person', <person id>)` and nowhere
#: else. Written down rather than inferred from the kind, because `matched` and `named` are two
#: kinds of line about one kind of object and a mapping from the kind alone could not say so.
_RECEIPT_OBJECT_OF_KIND: Mapping[str, str] = {
    "named": "person",
    "matched": "person",
    "tagged": "tag",
    "filed": "username",
}


def _receipt_of_object(rows: Sequence[Row]) -> dict[tuple[str, str], str]:
    """Which receipt each thing this file was linked to was linked by, as `(kind, id) -> receipt`.

    Not by comparing English: see the module header for why neither the clock nor the sentence can
    pair a line with its receipt. Every receipt carries the ledger's verb and the object it was
    about, so the receipt for a naming and the naming itself meet on an id.

    FIRST WINS, in the read's own order, which is oldest first. Two receipts about the same person
    on the same file is a person named, unnamed and named again, and the line the pane draws is
    the one link row that survived all of it, which belongs to the act that put it there first.

    A receipt with no object is skipped rather than guessed at: every one written before its area
    went through the ledger has none, and inventing a key for it would fold a line into whichever
    receipt happened to be next.
    """
    found: dict[tuple[str, str], str] = {}
    for row in rows:
        if row["object_kind"] is None or row["object_id"] is None:
            continue
        found.setdefault((str(row["object_kind"]), str(row["object_id"])), str(row["id"]))
    return found


def _receipt_for(by_object: Mapping[tuple[str, str], str], kind: str, object_id: str) -> str | None:
    """The receipt that wrote one attribution, or None where the record does not say.

    `kind` is one of `_RECEIPT_OBJECT_OF_KIND`'s: a line of any other kind has no receipt to find.
    """
    return by_object.get((_RECEIPT_OBJECT_OF_KIND[kind], object_id))


# ONE FOLD FOR EVERY THREAD: a link row's line belongs to the receipt on that file whose OBJECT is
# the thing the row links, matched by id.
#
# The file tab, the person tab and the tag and Site tabs all say the same attribution: a file's
# page draws one line per link row, and the other three COUNT the rows by source and day. Each of
# them folds the row into a receipt drawn on the same pane, and all four ask the one question
# below, so the file tab and the person tab can never disagree about whether a naming was a
# decision's own act.
#
# THE RULE: the receipt is the OLDEST receipt still standing on that file whose ledger object is
# the linked thing (`_RECEIPT_OBJECT_OF_KIND` says which kind). Oldest, because a row named, taken
# back and named again belongs to the act that put it there first (see `_receipt_of_object`).
# Standing, because a receipt that was taken back removed its rows, so a row there now was put
# there by something else and must not leave the pane inside a card that says "taken back". Any
# reader's receipts, because a rule that depended on what one reader may see would be a different
# rule per reader: a receipt the reader is not shown is not drawn, so the row it would absorb stays
# on the pane (the fold only ever drops a line INTO a line that is there).
#
# The counted statements carry this as a column (`receipt`) and group by it, and
# `counted_apart_from_receipts` drops the groups whose receipt is drawn on the pane and adds the
# rest back together by source, box and day. The object terms carry a unary plus so the planner
# seeks the file's own receipts (`ix_workbench_subject`, a handful a file) and never walks every
# receipt about a person from `ix_workbench_object` once per row.
RECEIPT_OF_A_NAMING = """(SELECT r.id FROM workbench_decision_subjects rs
     JOIN workbench_decisions r ON r.id = rs.decision_id
    WHERE rs.kind = 'asset' AND rs.subject_id = link.asset_id
      AND r.queue <> :ledger AND r.reversed_at IS NULL
      AND +r.object_kind = 'person' AND +r.object_id = link.person_id
    ORDER BY r.decided_at ASC, r.id ASC LIMIT 1)"""

#: The same question about a tag put on a file (`asset_tags`).
RECEIPT_OF_A_TAGGING = """(SELECT r.id FROM workbench_decision_subjects rs
     JOIN workbench_decisions r ON r.id = rs.decision_id
    WHERE rs.kind = 'asset' AND rs.subject_id = link.asset_id
      AND r.queue <> :ledger AND r.reversed_at IS NULL
      AND +r.object_kind = 'tag' AND +r.object_id = link.tag_id
    ORDER BY r.decided_at ASC, r.id ASC LIMIT 1)"""

#: The same question about a file filed under a username (`asset_usernames`).
RECEIPT_OF_A_FILING = """(SELECT r.id FROM workbench_decision_subjects rs
     JOIN workbench_decisions r ON r.id = rs.decision_id
    WHERE rs.kind = 'asset' AND rs.subject_id = link.asset_id
      AND r.queue <> :ledger AND r.reversed_at IS NULL
      AND +r.object_kind = 'username' AND +r.object_id = link.username_id
    ORDER BY r.decided_at ASC, r.id ASC LIMIT 1)"""

#: A database with no record of decisions: no row has a receipt.
NO_RECEIPT = "NULL"

#: The file tab's side of the same rule: every receipt still standing on one file that names an
#: object, oldest first, which `_receipt_of_object` turns into "first wins" per object.
_RECEIPTS_ON_A_FILE = """
SELECT d.id AS id, d.object_kind AS object_kind, d.object_id AS object_id
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id = :subject AND d.queue <> :ledger
   AND d.reversed_at IS NULL AND d.object_kind IS NOT NULL
 ORDER BY d.decided_at ASC, d.id ASC
"""


async def receipts_on_a_file(database: Database, asset_id: str) -> dict[tuple[str, str], str]:
    """Which receipt each thing linked to this file was linked by: the file tab's half of the one
    fold (see `RECEIPT_OF_A_NAMING`), so its answer and the counted threads' are one rule."""
    rows = await database.fetch_all(
        _RECEIPTS_ON_A_FILE, {"subject": asset_id, "ledger": LEDGER_QUEUE}
    )
    return _receipt_of_object(rows)


def drawn_receipts(events: Sequence[Event]) -> set[str | None]:
    """The receipts a pane draws, which are the only ones a line may fold into: the same test
    `_one_line_per_act` makes on the file tab (a `decided` line's own id)."""
    return {one.receipt for one in events if one.kind == "decided"}


#: One group of a counted thread after the fold: `source`, `box`, `day`, `files` and `at`, the
#: columns `files_of_filing` and the counted lines read.
Counted = Mapping[str, Any]


def counted_apart_from_receipts(
    rows: Sequence[Row], drawn: Collection[str | None]
) -> list[Counted]:
    """A counted thread's groups with every row a drawn receipt accounts for taken out.

    `rows` come grouped by source, box, day AND receipt (`RECEIPT_OF_A_NAMING` and its two
    siblings); `drawn` is the ids of the receipts this pane draws. A group whose receipt is drawn
    leaves the pane, because the card already says it with the Undo on it; the rest are added back
    together by source, box and day, so one day's line still counts every file no card accounts for.
    """
    groups: dict[tuple[object, object, object], dict[str, Any]] = {}
    for row in rows:
        receipt = row["receipt"]
        if receipt is not None and str(receipt) in drawn:
            continue
        key = (row["source"], row["box"], row["day"])
        at = None if row["at"] is None else int(row["at"])
        one = groups.get(key)
        if one is None:
            groups[key] = {
                "source": row["source"],
                "box": row["box"],
                "day": row["day"],
                "files": int(row["files"]),
                "at": at,
            }
            continue
        one["files"] += int(row["files"])
        if at is not None and (one["at"] is None or at > one["at"]):
            one["at"] = at
    return list(groups.values())


def _merged(decision: Event, absorbed: Event) -> Event:
    """The decision's line, carrying what the line folded into it was the only one saying.

    The `via` mark and the links, and nothing else. The mark is the whole reason the dropped line
    was worth reading (a filing says WHICH pass filed it and a receipt's title does not carry the
    filter's word), and the links are the way to the site or the person it named, which would
    otherwise be a name in a sentence with nowhere to go. `or` rather than a replacement, because a
    decision that already carries a mark has one that is about the decision.

    Everything else stays the decision's: its moment, its actor, its Undo and whether it was taken
    back. A folded line must still be the line the workbench can put back.

    **The survivor is the one that says more, and `_KEEPS_ITS_TITLE` is what holds that.** The
    filing, the carry and the match receipts carry the ledger's verb and object, and rewording them
    from that verb would keep the generic line and drop the one carrying the source, the folder and
    the confidence. The wording rule is what keeps them apart: a receipt about a person, a tag or a
    username keeps the sentence it was written with.
    """
    return replace(
        decision,
        via=decision.via or absorbed.via,
        # THE LINE BUILT FROM THE RECORD: one sentence per act. The absorbed line is built from
        # the rows as they stand, with each thing placed where it sits and the task said at the
        # back, so it is the sentence the person's page and the feed say about the same act; the
        # decision keeps its moment, its Undo and whether it was taken back.
        pieces=_the_carry(decision, absorbed) if absorbed.source == "copy" else absorbed.pieces,
    )


def _the_carry(decision: Event, absorbed: Event) -> Line:
    """A CARRY's receipt keeps its title, which names the file the row was carried FROM (a fact the
    row does not hold), with the one thing the carried row names placed on its own words.

    The title was written by `sentences.carried_sentence` with that very name in it, so this is the
    receipt's declared words, placed once (`titled`), not a search for a name that might be
    anywhere.
    """
    named = absorbed.links[0] if absorbed.links else None
    return titled(decision.what, named)


def _one_line_per_act(events: Sequence[Event]) -> list[Event]:
    """One line for one act: drop a line whose receipt is already on the pane.

    **THE TEST IS THE RECEIPT'S ID, NOT THE SENTENCE.** Both lines carry it (`Event.receipt`), so
    this is an equality on an id: the sentences may be reworded freely, two acts that happen to read
    alike are not one, and an act whose wording changed between builds still folds. See the module
    header for why a sentence test cannot work.

    A line with no receipt is never folded. That is the failure this rule is allowed to have: a
    receipt with no object cannot say which of its lines belongs to it, and the pane shows both.

    Cheap enough to do plainly: a history is at most `MAX_LIMIT` lines and the decisions on one file
    are a handful, so this is a pass over a list somebody is about to read rather than anything the
    library's size reaches.
    """
    folded = list(events)
    receipts = {one.receipt: at for at, one in enumerate(folded) if one.kind == "decided"}
    absorbed: set[int] = set()
    for position, event in enumerate(folded):
        if event.kind not in _FOLDS_INTO_A_DECISION or event.receipt is None:
            continue
        into = receipts.get(event.receipt)
        if into is None:
            continue
        folded[into] = _merged(folded[into], event)
        absorbed.add(position)
    return [one for position, one in enumerate(folded) if position not in absorbed]


#: The face acts that already account for a bare naming of the person they are about.
#:
#: Closed and short, for the reason `KINDS` is closed: folding is a line LEAVING the pane. Both of
#: these PUT a name on the file (somebody agreed to an appearance, or Sift matched one), and the
#: `asset_people` row that follows is the same act said with less in it. `rejected` is deliberately
#: not one: refusing a face says "this is not them" and puts no name anywhere, so a naming beside it
#: is a different act and the only line saying so.
_FACE_ACTS_THAT_NAME = ("confirmed", "matched")


def _one_line_per_face_act(events: Sequence[Event]) -> list[Event]:
    """Drop a bare naming that a face act on the same file already accounts for.

    ## The two lines are one act

    A face agreed to be somebody writes two rows in one press: `face_confirmations`, which this pane
    reads as "A face here was agreed to be Ada Lumen", and (through the reconciliation that brings
    a file's People into line with the faces in it) the `asset_people` row this pane reads as "Ada
    Lumen was named in this file". Two sentences, one press, and the second says strictly less than
    the first. The naming row carries no source, and where it carries a moment it is the
    confirmation's own second.

    A naming with no `decided_at` is an OLD row: no current writer leaves it empty (the
    reconciliation, the catalog's own attribution and the lineage carry all write the second). What
    is repetitive is the pair, not the gap, and it is repetitive on rows written this minute too.

    ## A MATCH IS THE SAME PAIR, and it is the larger half of the two

    The same reconciliation runs after a match, so an appearance Sift attached on its own also
    leaves an `asset_people` row with no source on it, and "Neve Alder was named here" beside
    "Sift matched Neve Alder here, 92% sure" is the same duplicate attribution, with the half that
    says who decided it drawn second. A naming row that DOES carry a source (`folder`, `username`)
    keeps its line for the reason below: a pass that got there first is an act this pane has no
    other way of saying.

    ## Why a naming with a SOURCE keeps its line

    `asset_people` takes one row per file and person and the first writer wins, so a file where a
    face was agreed to be somebody has exactly one naming row for them however many things touched
    it, and `source` is what the first writer said it was. A row naming a folder, a stash-box, a
    file name, a watermark or a copy is an act that happened elsewhere and is the only line saying
    so; a row naming nothing is one no other line in this pane accounts for, and a confirmation or a
    match beside it does.

    **What that gives up, said plainly:** somebody who types a name onto a file by hand and later
    agrees to a face as the same person loses the moment of the typing, because the hand path
    records no source either. One line is drawn where there were two and it is the one that says
    more. Keeping both would repeat every such act, and the discriminator that would separate them
    does not exist on the row.
    """
    answered = {
        link.id
        for one in events
        if one.kind in _FACE_ACTS_THAT_NAME
        for link in one.links
        if link.kind == "person"
    }
    if not answered:
        return list(events)
    return [
        one
        for one in events
        if not (
            one.kind == "named"
            and one.source is None
            and any(link.kind == "person" and link.id in answered for link in one.links)
        )
    ]


#: Which kinds are one act when they share a source. The filings are absent on purpose: a file is
#: filed under one username per press, so a group of them is several presses rather than one, and
#: a file carrying two filings at all is rare.
_FOLDS_BY_SOURCE = ("named", "tagged")


def _one_press(members: Sequence[Event], line: Line, kind: str, words: str) -> Event:
    """The one line a source's press becomes: its sentence, and every name it touched in `detail`.

    The MOMENT is the latest of them, because that is when the press finished: an act is one thing
    and the pane puts it where it ended. None only where every row in it predates the column that
    records a moment, which keeps those rows where they belong: first, above everything with a time.

    The actor is the first member's, and they cannot disagree: the group is keyed on the source word
    and the actor is decided from that word alone.

    `kind` and `words` are handed in rather than read off the links, and for two different reasons.
    The KIND is the act's: one press writes one kind of row, and the caller is where the act is
    known, so a group whose links happened to be missing cannot make this guess wrong. The WORDS are
    the phrase the caller put in `what`, so the heading over the group and the count in the sentence
    are the same string rather than two computations of it. See `Detail`.
    """
    first = members[0]
    if len(members) == 1:
        return replace(first, pieces=line)
    times = [one.at for one in members if one.at is not None]
    links = tuple(link for one in members for link in one.links)
    return replace(
        first,
        at=max(times) if times else None,
        # The line counts them; the names go under it, where they are listed rather than said.
        pieces=line,
        detail=(Detail(kind=kind, words=words, links=links),) if links else (),
    )


#: WHAT A BOX WROTE, in the order the sentence says it: the act, the kind of row it writes, and the
#: words that count it.
#:
#: A table rather than three `if`s, because the sentence and the groups under it are built from it
#: in ONE pass (see `_by_the_box`). Written as three branches they would be two lists assembled
#: beside each other, and the day an act is added to one and not the other is the day a line counts
#: three things and opens to two.
_BOX_WROTE: tuple[tuple[str, str, Callable[[int], str]], ...] = (
    ("named", "person", say.people),
    # "the site" rather than "1 site", because the sentence is a list of things and a numeral in
    # front of the only one of them reads as a count of the whole list.
    ("filed", "site", lambda count: "the site" if count == 1 else f"{count} sites"),
    ("tagged", "tag", say.tags),
)


def _by_the_box(box: Event, taken: Sequence[Event]) -> Event:
    """The stash-box's own line, saying what it wrote and listing it under itself in groups.

    The counts come from the lines being absorbed, so the sentence can only ever name what the file
    itself can account for (see the module docstring for the fields it deliberately does not
    claim). A box whose match wrote nothing this read can see keeps the sentence it always had.

    The phrase this puts in the sentence is the SAME STRING it puts on the group under it, which is
    the whole reason the two are built together here. See `Detail`.

    `taken` is never empty and holds only acts `_BOX_WROTE` counts (`_said_by`), so the sentence
    always has something to say.
    """
    parts: list[str] = []
    groups: list[Detail] = []
    for act, kind, counted in _BOX_WROTE:
        members = [one for one in taken if one.kind == act]
        if not members:
            continue
        words = counted(len(members))
        parts.append(words)
        links = tuple(link for one in members for link in one.links)
        # An act with nothing to open to is counted and not listed: a filing whose site has been
        # deleted has no page to offer. See `Detail`.
        if links:
            groups.append(Detail(kind=kind, words=words, links=links))
    # AND WHAT THE ROWS CANNOT ACCOUNT FOR, from the run's own record of what it filled in
    # (catalog version 52), so the line can finish: "recognised this file and wrote 3 people, the
    # site, 4 tags and the title". Nothing is invented: the list is what the WRITER said it wrote,
    # not what the box offered.
    #
    # LAST, after the rows. The rows are what somebody opens the line to look at, so they lead;
    # these have no group under them because a title is not a thing with a page.
    parts.extend(box.wrote)
    return replace(
        box,
        pieces=say.recognized(str(box.actor_name), box.by_hand, parts, grade=box.grade),
        detail=box.detail + tuple(groups),
    )


#: How far apart a download and the filing it wrote can be and still be one press: the feed's gap
#: (`history_events.FEED_FOLD_GAP`). In practice they are 0 to 1 second apart.
def _one_line_per_download(events: Sequence[Event]) -> list[Event]:
    """Drop the filing a download wrote, which its download line already says.

    A download files its file under the Site it came from in the same press
    (`download/service.attribute`), so the file would read "Filed under Discord" beside "Sift
    downloaded this from Discord". The download's line names the Site, the username and the
    Downloads row, so it is the survivor. A filing is the download's where ITS ROW SAYS SO: the
    writer names `download` as its source, and catalog v67 marks every older row a `downloaded`
    event vouches for. Dropped only while a download line is here to say it.
    A download-sourced filing with no download line in this thread (a download from before the
    ledger) is the only line saying it, and stays. The same rule the Site's own thread keeps
    (`history_entity._NOT_A_DOWNLOAD`).

    Not by the clock: a filing whose row records no time would never match, and one somebody made by
    hand under that Site inside the minute would be swallowed.
    """
    if not any(one.kind == "downloaded" for one in events):
        return list(events)
    # The download that brought the file in says its arrival as well: one act, one line.
    arrived = any(one.kind == "downloaded" and one.landed for one in events)
    return [
        one
        for one in events
        if not (one.kind == "filed" and one.source == VIA_DOWNLOAD)
        and not (arrived and one.kind == "added")
    ]


#: How far apart two routine steps can be and still be one sitting of housekeeping, in seconds.
EPISODE_GAP = 600


def episodes(moments: Sequence[int | None]) -> list[list[int]]:
    """The positions of `moments`, in time order, split wherever the next is more than
    `EPISODE_GAP` after the one before it. The moments nobody recorded are one episode, first."""
    order = sorted(range(len(moments)), key=lambda at: (moments[at] is not None, moments[at] or 0))
    runs: list[list[int]] = []
    last: int | None = None
    for position in order:
        moment = moments[position]
        opens = (
            not runs
            or (moment is None) != (last is None)
            or (moment is not None and last is not None and moment - last > EPISODE_GAP)
        )
        if opens:
            runs.append([])
        runs[-1].append(position)
        last = moment
    return runs


def _one_processed_line(events: Sequence[Event]) -> list[Event]:
    """The file's routine lines (`Event.routine`) as one line per episode, "Sift processed this
    file", each opening to its steps in time order under "Show each".

    An episode is a sitting (`episodes`), and its line is dated at its FIRST step: the housekeeping
    at arrival reads beside the arrival, and a stash-box asked a week later is a line of its own
    then. One line per sitting rather than one for the whole file. A sitting of one step is drawn as
    that step: it says what it is better than a line that opens to it.
    """
    routine = [one for one in events if one.routine]
    kept = [one for one in events if not one.routine]
    for run in episodes([one.at for one in routine]):
        steps = [routine[position] for position in run]
        if len(steps) < 2:
            kept.extend(steps)
            continue
        links = tuple(Link(kind="step", id=str(at), name=one.what) for at, one in enumerate(steps))
        kept.append(
            Event(
                at=steps[0].at,
                actor=Actor.SIFT,
                actor_name=SIFT,
                kind="ready",
                pieces=say.processed(),
                detail=(Detail(kind="step", words=say.processed_steps(len(links)), links=links),),
            )
        )
    return kept


def _group_of(event: Event) -> tuple[str, str, str]:
    """The group a row folds into: its kind and source word, and for a stash-box the box it names,
    so two boxes' rows on one file are two lines, each naming its box."""
    box = (event.actor_name or "") if event.via == "stash" else ""
    return event.kind, event.source or "", box


def _taken_by_boxes(events: Sequence[Event]) -> dict[int, list[Event]]:
    """The stash-box rows each box line absorbs, keyed by the `id` of that line.

    A row goes to the line of the box it names (`box_id`, carried as the actor's name), and a row
    naming none to the one box line where there is exactly one applied match to hang it on. With
    two boxes and nothing on the row it stays out, and the fold by source says "a stash-box"
    rather than naming a box that may not have written it. A stash-box's filing is absorbed too,
    after the namings and taggings, though a filing is never grouped by source.
    """
    boxes = [one for one in events if one.kind == "enriched"]
    by_name = {one.actor_name: one for one in boxes if one.actor_name}

    def box_for(event: Event) -> Event | None:
        if event.via != "stash":
            return None
        if event.actor_name:
            return by_name.get(event.actor_name)
        return boxes[0] if len(boxes) == 1 else None

    taken: dict[int, list[Event]] = {}
    for event in events:
        if event.kind in _FOLDS_BY_SOURCE and (box := box_for(event)) is not None:
            taken.setdefault(id(box), []).append(event)
    for one in events:
        if one.kind == "filed" and (box := box_for(one)) is not None:
            taken.setdefault(id(box), []).append(one)
    return taken


def _said_by(events: Sequence[Event]) -> list[Event]:
    """One line per pass per act, each saying which pass it was.

    Two folds in one walk, and they are one pass because they ask one question (which pass decided
    this row) of the same word on the same event. Split in two, the second would have to be told
    what the first had already taken.

    A stash-box's rows go to its box's line (`_taken_by_boxes`). Everything else is grouped by
    `via`, which is that source word, and by the box it names, and each group becomes one line. A
    group of ONE is still rewritten, because a single naming has a source to say too.

    EVERY LINE KEEPS ITS PLACE, and a group takes the place of its FIRST member. The caller sorts by
    time straight afterwards and that sort is stable, so the order this hands back is what decides
    which of two lines written in the same second is drawn first: a list rebuilt in a tidier order
    would shuffle those pairs about with nothing saying why.
    """
    taken = _taken_by_boxes(events)
    gone = {id(one) for absorbed in taken.values() for one in absorbed}
    groups: dict[tuple[str, str, str], list[Event]] = {}
    for event in events:
        if event.kind in _FOLDS_BY_SOURCE and id(event) not in gone:
            groups.setdefault(_group_of(event), []).append(event)

    written: dict[tuple[str, str, str], Event] = {}
    for (kind, source, _box), members in groups.items():
        # THE ROW'S OWN SOURCE WORD, and the actor it makes: which task a line names and how it
        # says it are one decision in one place (`sentences.FROM_PASS`). Grouped on the stored
        # word rather than on the filter's `via`, which has no word for a username or a copy, so
        # those would regroup as a passive line with nobody in it.
        first = members[0]
        by = by_of(first.actor, first.actor_name)
        naming = kind == "named"
        # Only the people or the tags: a naming read from a folder names the folder as well, and
        # that is where it was read, not one more person named.
        wanted = "person" if naming else "tag"
        things = [piece_of(link) for one in members for link in one.links if link.kind == wanted]
        # The folder said at the back only where every naming in the group was read from the same
        # one; otherwise the line says what is true of all of them ("from folder names").
        places = [[link for link in one.links if link.kind == "folder"] for one in members]
        shared = {(place[0].id, place[0].name) for place in places if len(place) == 1}
        folder = (
            piece_of(places[0][0])
            if naming and len(shared) == 1 and all(len(place) == 1 for place in places)
            else None
        )
        # The phrase the sentence counts them in, and the kind of row the act writes: the heading
        # of the group under a line that counts, and the same string in the line. See `Detail`.
        words = say.people(len(things)) if naming else say.tags(len(things))
        who: Line | str = (things[0],) if len(things) == 1 else words
        line = (
            say.named_sentence(by, source or None, who, len(things), folder)
            if naming
            else say.tagged_sentence(by, source or None, who, len(things))
        )
        written[(kind, source, _box)] = _one_press(
            members, line, "person" if naming else "tag", words
        )

    kept: list[Event] = []
    for event in events:
        if id(event) in gone:
            continue
        if event.kind == "enriched" and id(event) in taken:
            kept.append(_by_the_box(event, taken[id(event)]))
            continue
        if event.kind not in _FOLDS_BY_SOURCE:
            kept.append(event)
            continue
        key = _group_of(event)
        one_line = written.pop(key, None)
        if one_line is not None:
            kept.append(one_line)
    return kept
