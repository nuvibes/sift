# SPDX-License-Identifier: AGPL-3.0-or-later
"""A decision's line on the record, worded when it is SHOWN from what the decision recorded.

A receipt's title is composed at the press and stored, so it keeps its words for good: "Kept your
answer for Ada Byron" about a person since renamed Esme Wrenfield, "A group of 90 faces set aside"
after the word became Discarded, a field's key with its raw values in it. So saved text is rendered
when it is shown rather than rewritten once or left: see `kernel.workbench.Recorded`.

This is the reading half. The WORDS are the area's (only the area that wrote a payload can read
it, which is the rule `pictures_of` already keeps), and what this module adds is everything no
area can know: who is reading (so the doer is "You" to the person who did it), and what each thing
a line names is called NOW. A person renamed since is said by the name they have; one merged away
is said as merged into the one that absorbed them, linked; one deleted keeps the name it had, as
plain words, with that said. A stale name presented as current is the fault this closes.

## Why the stored title is still the answer for some rows

An area that has no `worded`, or answers None, keeps its stored title and detail: that is an old
row that recorded nothing else, and its own words are the only account of it left. A gate
(`tests/gates/test_decisions_are_worded.py`) counts the areas still answering that way, and the
count may only fall.

## One reader, in the kernel

Every screen says a decision in the same words, never "Took FansDB's answer for Ada Byron" on her
page beside "You chose FansDB's breast type for Ada Byron, Natural, over your Fake" on the card.
Everything it reads is the kernel's (the registry is `kernel.workbench`, the names are
`history_events`), so it lives here and every reader asks it: `WorkbenchService.worded` (Recent
decisions and the feed) and `decided_said` (every History page, through
`history._decision_events`). A page says a thing whose page it is by its vantage word ("them", "it",
"this file"): see `decided_said`.

## Cost

One read of the ledger's own columns and one of the subjects for the page's receipts, then one
probe per KIND of thing the page names, bounded by the page, the same shape the feed pays
(`history_events.subjects_present` / `names_now`). A page of twenty rows is a handful of reads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from sift.kernel.access import Viewer
from sift.kernel.access import sentences as say
from sift.kernel.access.history import face_sures, is_face_match
from sift.kernel.access.history_events import (
    actor_names,
    can_be_found,
    subjects_present,
)
from sift.kernel.access.history_names import names_now
from sift.kernel.access.sentences import A_GONE, A_THING, LINKED_KINDS, SIFT
from sift.kernel.db import Database, Row, in_clause
from sift.kernel.ledger import ACTOR_BOX, ACTOR_SIFT, ACTOR_USER
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import Subject
from sift.kernel.wire import HistoryLink
from sift.kernel.workbench import Doer, Named, Piece, Recorded, Worded, Words, Workbench

log = get_logger(__name__)


class Stored(Protocol):
    """A receipt as it was stored: the columns a line is worded from, whoever read the row."""

    @property
    def id(self) -> str: ...
    @property
    def queue(self) -> str: ...
    @property
    def user_id(self) -> str | None: ...
    @property
    def title(self) -> str: ...
    @property
    def detail(self) -> str: ...
    @property
    def payload(self) -> str: ...
    @property
    def decided_at(self) -> int: ...


@dataclass(frozen=True, slots=True)
class StoredRow:
    """A receipt read by a History page's own statement, as the `Stored` a line is worded from."""

    id: str
    queue: str
    user_id: str | None
    title: str
    detail: str
    payload: str
    decided_at: int


#: What the ledger recorded beside each receipt, for the page's receipts only.
_FACTS = (
    "SELECT id, user_id, verb, actor_kind, actor_id, object_kind, object_id, object_name, touched"
    " FROM workbench_decisions WHERE id IN (?*)"
)
_SUBJECTS = (
    "SELECT decision_id, kind, subject_id, name FROM workbench_decision_subjects"
    " WHERE decision_id IN (?*)"
)

#: WHERE A THING THAT IS GONE WENT, when it went by being merged. The ledger's `merged` event names
#: the one going as its subject and the one kept as its object (`people/merge.py`,
#: `people/site_merge.py`), so this is a read of the record rather than of any feature's table.
#: Newest first: a person merged into one who was later merged again is followed one step here,
#: and the step after is followed by asking again (see `_merged_into`).
_MERGED = (
    "SELECT s.kind AS kind, s.subject_id AS id, d.object_id AS into_id, d.object_name AS into_name"
    " FROM workbench_decision_subjects s JOIN workbench_decisions d ON d.id = s.decision_id"
    " WHERE d.verb = 'merged' AND d.object_id IS NOT NULL AND s.subject_id IN (?*)"
    " ORDER BY d.decided_at DESC, d.id DESC"
)

#: How many merges one name is followed through. A chain longer than this is a library that merged
#: the same person over and over; the line then names the last one it reached, which is still true.
_MOST_HOPS = 4


@dataclass(frozen=True, slots=True)
class Line:
    """A decision's line as it is drawn: the words, the things in them, and the line under it."""

    said: str
    links: tuple[HistoryLink, ...] = ()
    more: str = ""
    #: THE LINE AS PIECES, the shape every other History screen hands over (`sentences.Piece`):
    #: each thing it names placed where it sits, so the client draws it and searches for nothing.
    #: `said` and `links` are the same line as words and a list, for a reader of the old shape.
    pieces: tuple[say.Piece, ...] = ()


@dataclass(frozen=True, slots=True)
class _Now:
    """What the reader learned about the things a page names. See `_learn`."""

    present: frozenset[tuple[str, str]]
    names: Mapping[tuple[str, str], str]
    merged: Mapping[tuple[str, str], tuple[str, str, str | None]]
    actors: Mapping[tuple[str, str], str]
    #: THE PAGE'S OWN THING and its vantage word, `(kind, id, word)`, where a History page asks: a
    #: line on a person's page says "them" where it would name her. None on the decision record
    #: and the feed, which are nobody's page.
    here: tuple[str, str, str] | None = None


async def lines_of(
    database: Database,
    bench: Workbench,
    viewer: Viewer,
    receipts: Sequence[tuple[Stored, int]],
    *,
    here: tuple[str, str, str] | None = None,
) -> dict[str, Line]:
    """The worded line of every receipt on a page whose area can word it, keyed by receipt id.

    Each receipt comes with how many the row stands for (`Recorded.run`). `here` is the page's own
    thing and its vantage word (see `_Now.here`).

    A receipt absent from the answer keeps its stored title and detail (see the module's head).
    An area that raises is treated as one that could not word the row: a record somebody opened to
    check a decision must never fail to draw because one line's words could not be made.
    """
    if not receipts:
        return {}
    recorded = await _recorded(database, receipts)
    worded: dict[str, tuple[Worded, Recorded]] = {}
    # A FACE MATCH IS SAID THE ONE WAY every screen says it (`sentences.recognized_face`), "Sift
    # recognized Ada Lumen in image.png, 75% sure", with how sure read off the faces as they
    # stand, not the words its receipt was stored with. Only a receipt about exactly one file: a
    # match is a person in a file.
    faces: dict[str, tuple[str, str]] = {}
    for one in recorded:
        files = [thing for thing in one.subjects if thing.kind == "asset"]
        if (
            is_face_match(one.verb, one.object_kind, one.actor_kind, one.actor_id)
            and one.object_id
            and len(files) == 1
            and one.run == 1
        ):
            faces[one.id] = (files[0].id, one.object_id)
            worded[one.id] = (
                Worded(
                    said=(
                        Named(kind="person", id=one.object_id, recorded=one.object_name),
                        Named(kind="asset", id=files[0].id, recorded=files[0].name),
                    )
                ),
                one,
            )
    for one in recorded:
        if one.id in faces:
            continue
        # Asked as an `object`: `Words` is a capability a reverser MAY have, and narrowing from
        # `Reverser` would read to the checker as two unrelated protocols that cannot meet.
        area: object = bench.reverser(one.queue)
        if not isinstance(area, Words):
            continue
        try:
            said = area.worded(one if here is None else replace(one, page=(here[0], here[1])))
        except Exception:
            said = None
        if said is not None and said.said:
            worded[one.id] = (said, one)
    if not worded:
        return {}
    now = replace(
        await _learn(
            database, [said for said, _ in worded.values()], [r for _, r in worded.values()]
        ),
        here=here,
    )
    sures = await face_sures(database, list(faces.values()))
    return {
        key: (
            _face_line(said, sures.get(faces[key], []), now)
            if key in faces
            else _line(said, one, viewer, now)
        )
        for key, (said, one) in worded.items()
    }


def _face_line(said: Worded, sures: Sequence[float | None], now: _Now) -> Line:
    """A face match's receipt as the one face-match sentence, its person and its file linked."""
    person, file = (piece for piece in said.said if isinstance(piece, Named))
    _who, person_link, person_line = _named(person, now)
    _where, file_link, file_line = _named(file, now)
    pieces = say.recognized_face(person_line, say.said("in ", file_line), sures)
    return Line(
        said=say.text_of(pieces),
        links=tuple(one for one in (person_link, file_link) if one is not None),
        pieces=pieces,
    )


async def _recorded(database: Database, receipts: Sequence[tuple[Stored, int]]) -> list[Recorded]:
    """Each receipt with the ledger columns and subjects it carries. Two reads for the page."""
    ids = [one.id for one, _run in receipts]
    statement, bound = in_clause(_FACTS, ids)
    facts = {str(row["id"]): dict(row) for row in await database.fetch_all(statement, bound)}
    statement, bound = in_clause(_SUBJECTS, ids)
    subjects: dict[str, list[Subject]] = {}
    for row in await database.fetch_all(statement, bound):
        subjects.setdefault(str(row["decision_id"]), []).append(
            Subject(
                kind=str(row["kind"]),  # type: ignore[arg-type]
                id=str(row["subject_id"]),
                name=None if row["name"] is None else str(row["name"]),
            )
        )
    out: list[Recorded] = []
    for one, run in receipts:
        fact = facts.get(one.id, {})
        out.append(
            Recorded(
                id=one.id,
                queue=one.queue,
                payload=one.payload,
                title=one.title,
                detail=one.detail,
                decided_at=one.decided_at,
                user_id=one.user_id,
                verb=_text(fact.get("verb")),
                actor_kind=_text(fact.get("actor_kind")),
                actor_id=_text(fact.get("actor_id")),
                object_kind=_text(fact.get("object_kind")),
                object_id=_text(fact.get("object_id")),
                object_name=_text(fact.get("object_name")),
                count=None if fact.get("touched") is None else int(fact["touched"]),
                subjects=tuple(subjects.get(one.id, ())),
                run=run,
            )
        )
    return out


def _text(value: object) -> str | None:
    return None if value is None or value == "" else str(value)


async def _learn(
    database: Database, worded: Sequence[Worded], recorded: Sequence[Recorded]
) -> _Now:
    """Everything the page's lines need to know about the present, in a read per kind."""
    wanted: dict[str, set[str]] = {}
    for said in worded:
        for piece in (*said.said, *said.more):
            if isinstance(piece, Named) and piece.id:
                wanted.setdefault(piece.kind, set()).add(piece.id)
    boxes = wanted.pop("box", set())
    asked = {kind: sorted(ids) for kind, ids in wanted.items() if ids}
    present = frozenset(await subjects_present(database, asked))
    names = dict(await names_now(database, asked))
    merged = await _merged_into(database, asked, present)
    # The things merged INTO were not on the page's list, so they are asked about too: what each
    # is called now, and whether it is still there to be linked.
    targets: dict[str, list[str]] = {}
    for kind, into_id, _name in merged.values():
        targets.setdefault(kind, []).append(into_id)
    if targets:
        present = present | frozenset(await subjects_present(database, targets))
        names.update(await names_now(database, targets))
    users = sorted(
        {
            str(one.actor_id or one.user_id)
            for one in recorded
            if _doer_kind(one) == ACTOR_USER and (one.actor_id or one.user_id)
        }
    )
    boxes |= {
        str(one.actor_id) for one in recorded if _doer_kind(one) == ACTOR_BOX and one.actor_id
    }
    actors = await actor_names(database, {"user": users, "box": sorted(boxes)})
    return _Now(present=present, names=names, merged=merged, actors=actors)


async def _merged_into(
    database: Database, asked: Mapping[str, Sequence[str]], present: frozenset[tuple[str, str]]
) -> dict[tuple[str, str], tuple[str, str, str | None]]:
    """For each named thing that is gone: the thing it was merged into, where it was merged.

    Followed a few steps, so a person merged into one who was later merged again is said as the one
    that is still here. Only the gone are asked about: a thing that is there was not merged away.
    """
    gone = [(kind, one) for kind, ids in asked.items() for one in ids if (kind, one) not in present]
    found: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    frontier = {one: (kind, one) for kind, one in gone}
    for _hop in range(_MOST_HOPS):
        if not frontier:
            break
        statement, bound = in_clause(_MERGED, sorted(frontier))
        rows = await database.fetch_all(statement, bound)
        seen: dict[str, tuple[str, str | None]] = {}
        for row in rows:
            seen.setdefault(str(row["id"]), (str(row["into_id"]), _text(row["into_name"])))
        following: dict[str, tuple[str, str]] = {}
        for at, origin in frontier.items():
            step = seen.get(at)
            if step is None:
                continue
            kind = origin[0]
            found[origin] = (kind, step[0], step[1])
            if (kind, step[0]) not in present:
                following[step[0]] = origin
        frontier = following
    return found


def _doer_kind(recorded: Recorded) -> str:
    """Who acted, as one of the ledger's three kinds, reading a pre-ledger row by its user."""
    if recorded.actor_kind in (ACTOR_SIFT, ACTOR_USER, ACTOR_BOX):
        return recorded.actor_kind
    return ACTOR_USER if recorded.user_id else ACTOR_SIFT


def _doer(recorded: Recorded, viewer: Viewer, now: _Now) -> str:
    """Who took the decision, in the words the person reading should see."""
    kind = _doer_kind(recorded)
    if kind == ACTOR_USER:
        who = recorded.actor_id or recorded.user_id
        if who is not None and who == viewer.id:
            return "You"
        named = now.actors.get(("user", str(who)))
        return named if named else _capital(A_GONE["login"])
    if kind == ACTOR_BOX:
        return now.actors.get(("box", str(recorded.actor_id)), _capital(A_THING["box"]))
    return SIFT


def _capital(words: str) -> str:
    return words[:1].upper() + words[1:]


def _line(said: Worded, recorded: Recorded, viewer: Viewer, now: _Now) -> Line:
    text, links, pieces = _draw(said.said, recorded, viewer, now)
    # The line under is drawn plain: only the title line links.
    more = _draw(said.more, recorded, viewer, now)[0] if said.more else ""
    return Line(said=text, links=links, more=more, pieces=pieces)


def _draw(
    pieces: Sequence[Piece], recorded: Recorded, viewer: Viewer, now: _Now
) -> tuple[str, tuple[HistoryLink, ...], tuple[say.Piece, ...]]:
    """A worded line as the History pieces every screen draws, and as words with their links.

    The doer is words; a thing is a piece placed where it sits, linked where it has a page and is
    still there; "(now X)" and "(since merged into X)" are words, the thing, and ")".

    A line opening on this application's own words ("a group of faces that is gone") gets its
    capital; one opening on a NAME keeps the name exactly as it is spelled: a username is
    lower case on purpose, and "Admin-7" is not what somebody called themselves "admin-7".
    """
    links: list[HistoryLink] = []
    runs: list[say.Part] = []
    named_first = False
    for at, piece in enumerate(pieces):
        if isinstance(piece, Doer):
            runs.append(_doer(recorded, viewer, now))
            named_first = named_first or (at == 0 and _doer_kind(recorded) != ACTOR_SIFT)
        elif isinstance(piece, Named):
            words, link, said = _named(piece, now)
            if link is not None:
                links.append(link)
            if (
                piece.kind_said
                and not _is_here(piece, now)
                and words not in (A_THING.get(piece.kind), A_GONE.get(piece.kind))
            ):
                # Its kind before its NAME only: the two fallbacks already say the kind.
                runs.append(say.KIND_BEFORE.get(piece.kind, ""))
            runs.append(said)
            named_first = named_first or (at == 0 and (link is not None or piece.kind == "box"))
        else:
            runs.append(piece)
    line = say.said(*runs)
    if not named_first:
        line = say.capitalized(line)
    return say.text_of(line), tuple(links), line


def _is_here(piece: Named, now: _Now) -> bool:
    """Whether a thing is the page's own, said by its vantage word, never where its recorded name
    IS the fact the line is about (`Named.as_recorded`): "You kept the name Esme Wrenfield" on her
    page does not become "You kept the name them"."""
    return (
        now.here is not None
        and not piece.as_recorded
        and (piece.kind, piece.id) == (now.here[0], now.here[1])
    )


def _named(piece: Named, now: _Now) -> tuple[str, HistoryLink | None, say.Line]:
    """One thing, as it is now: its words, its link, and the pieces that place it in a line. See
    `kernel.workbench.Named` for the three answers."""
    kind, key = piece.kind, (piece.kind, piece.id)
    if _is_here(piece, now) and now.here is not None:
        # The page's own thing, by its vantage word: "them", "it", "this file".
        return now.here[2], None, say.said(now.here[2])
    if kind == "box":
        words = now.actors.get(("box", piece.id), piece.recorded or A_THING["box"])
        return words, None, say.said(words)
    linked = LINKED_KINDS.get(kind)
    if not piece.id:
        words = piece.recorded or A_THING.get(kind, "something")
        return words, None, say.said(words)
    if key in now.present or not can_be_found(kind):
        return _named_now(piece, now, kind, key, linked)
    merged = now.merged.get(key)
    if merged is not None:
        return _named_merged(piece, now, kind, linked, merged)
    if piece.recorded:
        words = say.since_deleted(piece.recorded)
        return words, None, say.said(words)
    words = A_GONE.get(kind, "something that is gone")
    return words, None, say.said(words)


def _named_now(
    piece: Named, now: _Now, kind: str, key: tuple[str, str], linked: str | None
) -> tuple[str, HistoryLink | None, say.Line]:
    called = now.names.get(key)
    words = called or piece.recorded or A_THING.get(kind, "something")
    if piece.as_recorded and piece.recorded and called and called != piece.recorded:
        # The recorded name is the fact; the name it has now follows it, and is the link.
        if key in now.present and linked is not None:
            return (
                f"{piece.recorded} (now {called})",
                HistoryLink(kind=linked, id=piece.id, name=called),
                say.said(f"{piece.recorded} (now ", say.thing(linked, piece.id, called), ")"),
            )
        words = f"{piece.recorded} (now {called})"
        return words, None, say.said(words)
    if key in now.present and linked is not None:
        return (
            words,
            HistoryLink(kind=linked, id=piece.id, name=words),
            (say.thing(linked, piece.id, words),),
        )
    return words, None, say.said(words)


def _named_merged(
    piece: Named, now: _Now, kind: str, linked: str | None, merged: tuple[str, str, str | None]
) -> tuple[str, HistoryLink | None, say.Line]:
    into_kind, into_id, into_name = merged
    into = now.names.get((into_kind, into_id)) or into_name or A_THING.get(into_kind, "")
    was = piece.recorded or A_THING.get(kind, "something")
    if linked is not None and into and (into_kind, into_id) in now.present:
        return (
            f"{was} (since merged into {into})",
            HistoryLink(kind=linked, id=into_id, name=into),
            say.said(f"{was} (since merged into ", say.thing(linked, into_id, into), ")"),
        )
    words = f"{was} (since merged into {into})"
    return words, None, say.said(words)


async def worded_or_stored(
    database: Database,
    bench: Workbench,
    viewer: Viewer,
    receipts: Sequence[tuple[Stored, int]],
    *,
    here: tuple[str, str, str] | None = None,
) -> dict[str, Line]:
    """`lines_of`, where a failure is a page of stored titles rather than a page that does not draw.

    The words are an improvement on a record that was already readable, and a record somebody
    opened to check a decision must never be the thing that breaks. The ONE guard every reader goes
    through: the decision record (`WorkbenchService.worded`), the feed, and every History page.
    """
    try:
        return await lines_of(database, bench, viewer, receipts, here=here)
    except Exception as exc:
        log.warning("workbench.words_unavailable", detail=type(exc).__name__)
        return {}


def lines_under(said: Mapping[str, Line]) -> dict[str, str]:
    """The line under each worded line that has one, keyed by receipt id."""
    return {key: line.more for key, line in said.items() if line.more}


async def decided_said(
    database: Database,
    bench: Workbench | None,
    viewer: Viewer,
    rows: Sequence[Row],
    *,
    here: tuple[str, str, str],
) -> dict[str, Line]:
    """The worded line of each receipt a History page read with its own statement, keyed by id.

    The same reader as the decision record and the feed (`lines_of`), with the page's own thing said by
    its vantage word. A FACE MATCH is left out: a page says it as the one face-match sentence with
    its word ("Sift recognized them in image.png") through `history.recognized_lines`, and a file's
    own tab through its own line ("... here"). None where the page was drawn without the registry
    (a caller that has none keeps every stored title).

    Each row is one receipt (`run` 1): a page lists a decision once per act.

    THE ROW CARRIES ITS DETAIL. An older settled disagreement keeps its two values only in its
    detail (`stash_boxes.reconcile._settled_said`), so a page that read every column but that one
    would say the stored "Took FansDB's answer for Ada Byron" while the feed says "You chose
    FansDB's height for Ada Byron, 157 cm, over your 177 cm". Each page's `_DECIDED` selects
    `detail` for this.
    """
    if bench is None or not rows:
        return {}
    read = [dict(row) for row in rows]
    receipts: list[tuple[Stored, int]] = [
        (
            StoredRow(
                id=str(row["id"]),
                queue=str(row["queue"]),
                user_id=None if row.get("user_id") is None else str(row["user_id"]),
                title=str(row.get("title") or ""),
                detail=str(row.get("detail") or ""),
                payload=str(row.get("payload") or ""),
                decided_at=int(str(row["decided_at"])),
            ),
            1,
        )
        for row in read
        if not is_face_match(
            row.get("verb"), row.get("object_kind"), row.get("actor_kind"), row.get("actor_id")
        )
    ]
    lines = await worded_or_stored(database, bench, viewer, receipts, here=here)
    return {key: line for key, line in lines.items() if line.pieces}
