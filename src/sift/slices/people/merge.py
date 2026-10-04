# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two people who turn out to be one.

A library ends up with the same human twice for ordinary reasons: a folder spelled their name one
way, a download spelled it another, and a stash-box calls them a third thing. Merging is what makes
the library say what is true, and it is the one operation here that touches nearly every table in
Sift: twenty of them point at a person, by a key or by an id with a kind beside it.

## Every table, written out

There is no ORM and no statement is assembled from a table name, so each of them has its own
literal pair: move what can move, then clear what could not. The pairs are held in a list rather
than repeated as code, which keeps the whole set readable as ONE thing. The question a reader has
is "does this cover everything", and that question is answered by looking at the list.

The two-step exists because most of these tables are keyed BY the person. A file that already
carries the survivor cannot also carry them a second time, so `UPDATE OR IGNORE` moves the rows that
can move and leaves the rest sitting on the person who is about to go; the sweep after it clears
those. Without the sweep the delete would take them, which is the same outcome, but only because
of a cascade three files away, and a merge that depends on that is a merge nobody can reason about.

## One person or six, it is one act

There is one entry point and one transaction. A pairwise merge beside a set merge, each with its
own route and its own copy of the sequence, would be two copies of a twenty-table procedure, a
procedure that eventually only half of somebody edits. A set of one is a set.

Folding several in one transaction is not an economy either. A merge cannot be taken back, so four
of them taken as four calls is four chances to stop halfway, and halfway through folding four
people into one is a library where two are gone, two are still there, and nothing anywhere says
which state it is in or how to finish.

## A merge cannot be taken back, and it says so before it happens

Every other bulk decision in Sift leaves a receipt that reverses. This one does not, and pretending
otherwise would be worse than saying so: putting a merge back would mean recreating a deleted person
and then deciding, per row, which of the survivor's rows had been theirs, and a half-accurate undo
of an identity is a library that looks correct and is quietly wrong.

So the guard is BEFORE the fact rather than after it. `weigh` counts exactly what would move, the
screen shows those numbers beside the two names, and nothing happens until somebody has read them.
That is the same shape deleting a person already has, and a merge is the same class of act.

**Nothing becomes unfindable.** The name that goes is written onto the survivor as an also-known-as,
so every search, filename and folder that found them before still does.

**And nothing becomes unknown.** The tables are only the half of a person that points at
them; the other half is on their own row: a birthdate, a nationality, measurements, a hair
colour, a cover. Left there, the delete at the end would take it, so merging somebody who had been
looked up into somebody who had not would destroy everything learned about them, permanently, on
an operation with no undo. The survivor's blanks are filled from the person going before the row is
removed; anything the survivor already knows is left exactly as it is.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sift.kernel.access import ObjectType, Repository
from sift.kernel.access.default_covers import assign_if_empty
from sift.kernel.access.merged import follow
from sift.kernel.access.sentences import MERGE_NAMES_KEPT
from sift.kernel.changes import About, announce, who_may_see_a_file
from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.log import get_logger
from sift.kernel.records import Kind as RecordKind
from sift.kernel.records import Subject as RecordSubject
from sift.kernel.records import field as record_field
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import Subject

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Moves:
    """One table's share of a merge: what it holds, how to move it, and how to clear the rest.

    `move` is bound (keeping, losing). For a table holding a judgement about the person as a whole
    rather than something of theirs, it is a DELETE of both people's rows: a merge changes what the
    judgement was about, so neither row is true of the survivor any more.

    `sweep` is None for a table whose rows cannot collide (a username belongs to one person and
    nothing stops the survivor holding two of them), and a statement for every table keyed by the
    person, where the survivor may already have the row being moved.
    """

    what: str
    move: str
    sweep: str | None = None
    #: How many rows the person being merged away holds. What the confirm screen counts.
    count: str = ""
    #: Run before the move, bound (keeping, losing): what the survivor's own rows have to take
    #: from the rows about to collide with them.
    before: str | None = None


#: Every table that points at a person, and what a merge does to each.
#:
#: Every one of them, and the list is the specification. A table added later that names a person and is not
#: here is a merge that silently leaves rows behind, which is why `test_merge.py` reads the schema
#: and holds this list to it, rather than trusting anybody to remember.
#:
#: The schema walk is `test_every_table_naming_a_person_is_one_this_merge_touches`. The stores
#: keyed by a person's id with a kind word beside it (no key to cascade, nothing to notice) are
#: in `kernel/access/merged.py`, shared with the Site merge and held by a schema walk of their own
#: (`test_every_store_naming_a_thing_by_a_kind_word_is_followed_or_says_why`).
_TABLES: tuple[_Moves, ...] = (
    _Moves(
        what="files",
        move="UPDATE OR IGNORE asset_people SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM asset_people WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM asset_people WHERE person_id = ?",
        # A file both of them were on keeps the survivor's row, and with it the survivor's word
        # for how the file got there. Where the person going was put there BY A PERSON and the
        # survivor by Sift, that word is the wrong one: the deliberate decision is the one that
        # holds, so it is carried over before the rows collide.
        before=(
            "UPDATE asset_people SET source = NULL WHERE person_id = ? AND source IS NOT NULL "
            "AND asset_id IN (SELECT asset_id FROM asset_people WHERE person_id = ? "
            "AND source IS NULL)"
        ),
    ),
    _Moves(
        what="usernames",
        move="UPDATE usernames SET person_id = ? WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM usernames WHERE person_id = ?",
    ),
    _Moves(
        what="aliases",
        move="UPDATE OR IGNORE people_aliases SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM people_aliases WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM people_aliases WHERE person_id = ?",
    ),
    _Moves(
        what="links",
        move="UPDATE OR IGNORE people_links SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM people_links WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM people_links WHERE person_id = ?",
    ),
    _Moves(
        what="tags",
        move="UPDATE OR IGNORE person_tags SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM person_tags WHERE person_id = ?",
    ),
    _Moves(
        what="faces",
        move="UPDATE face_tracks SET person_id = ? WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM face_tracks WHERE person_id = ?",
    ),
    _Moves(
        what="face references",
        move="UPDATE OR IGNORE face_references SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM face_references WHERE person_id = ?",
    ),
    _Moves(
        what="face confirmations",
        move="UPDATE face_confirmations SET person_id = ? WHERE person_id = ?",
    ),
    _Moves(
        what="face refusals",
        move="UPDATE OR IGNORE face_rejections SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM face_rejections WHERE person_id = ?",
    ),
    # The refusal MEMORY: a No is carried across a rescan by likeness, keyed by person, so it moves
    # with the person the way the live refusal does.
    _Moves(
        what="remembered face refusals",
        move="UPDATE face_rejected SET person_id = ? WHERE person_id = ?",
    ),
    # A folder's proposal that a group is this person: the losing person's proposals become the
    # survivor's; one already made for the survivor on the same group wins.
    _Moves(
        what="face group proposals",
        move="UPDATE OR IGNORE face_pile_proposals SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM face_pile_proposals WHERE person_id = ?",
    ),
    _Moves(
        what="faces on files",
        move="UPDATE OR IGNORE face_asset_people SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM face_asset_people WHERE person_id = ?",
    ),
    _Moves(
        what="refusals",
        move="UPDATE OR IGNORE asset_person_refusals SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM asset_person_refusals WHERE person_id = ?",
    ),
    _Moves(
        what="stash-box records",
        move="UPDATE OR IGNORE person_stash_box_links SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM person_stash_box_links WHERE person_id = ?",
    ),
    _Moves(
        what="folder answers",
        move="UPDATE OR IGNORE folder_people SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM folder_people WHERE person_id = ?",
    ),
    _Moves(
        what="folder claims",
        move="UPDATE folder_claims SET person_id = ? WHERE person_id = ?",
    ),
    # A folder taken back from the person going is never filed under the survivor that way either:
    # the No was about this human and this folder's files. Left behind it would cascade away, and
    # the next folder pass would file the folder under the survivor again.
    _Moves(
        what="folders taken back",
        move="UPDATE OR IGNORE folder_refusals SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM folder_refusals WHERE person_id = ?",
    ),
    _Moves(
        what="claimed pack entries",
        move="UPDATE pack_entries SET claimed_person_id = ? WHERE claimed_person_id = ?",
    ),
    # Hidden, favourited, rated: per user, so the survivor may already have their own.
    #
    # The survivor's own wins where both have one, which is the right way round: it is the opinion
    # of the person doing the merging about the person they are keeping, and overwriting it with an
    # opinion about somebody they have just decided was the same human would change a rating nobody
    # asked to change.
    _Moves(
        what="opinions",
        move="UPDATE OR IGNORE person_user_state SET person_id = ? WHERE person_id = ?",
        sweep="DELETE FROM person_user_state WHERE person_id = ?",
    ),
    # A SHOOT PROPOSED UNDER THEM. Its own row, so nothing collides; left out, it would cascade away
    # with the person going.
    _Moves(
        what="proposed shoots",
        move="UPDATE shoot_proposals SET person_id = ? WHERE person_id = ?",
    ),
    # THE NOTE THAT EVERY STASH-BOX PICTURE OF THEM WAS REFUSED, which keeps a person out of the
    # starter count. It is a judgement about one person's set of pictures, and the merge has just
    # changed that set: the survivor now holds both people's box records. Neither note describes the
    # union, so both go and the survivor is offered again; a run that refuses every picture again
    # writes the note back. Moved instead, the one going would keep the survivor's own untried
    # pictures out of the count for as long as the note stood.
    _Moves(
        what="refused starter pictures",
        move="DELETE FROM face_starter_refusals WHERE person_id IN (?, ?)",
    ),
    # A download keeps the person it was for by id, so the row follows them: left behind, the key's
    # `ON DELETE SET NULL` would empty it and the download would stop saying whose it was.
    _Moves(
        what="downloads",
        move="UPDATE downloads SET person_id = ? WHERE person_id = ?",
        count="SELECT COUNT(*) AS n FROM downloads WHERE person_id = ?",
    ),
    # WHAT A STASH-BOX WAS ASKED ABOUT THEM, what was settled about its answers, what happened to
    # them and what somebody thought of them are NOT here, and that is not an omission: they name a
    # person by an id with a kind word beside it rather than by a key, the Site merge has exactly the
    # same stores, and both merges run them through ONE list, `kernel/access/merged.py`. One list is
    # what keeps the two merges from coming apart.
)

#: What the person going KNEW that the survivor does not, moved before they are deleted.
#:
#: The tables move and then the row itself is deleted, and the row is not empty. It carries
#: everything a person's record is made of, and none of it moves with the tables: left alone, a
#: merge would quietly destroy the birthdate, the nationality, the measurements, the hair colour
#: and the rest of whoever went, and a merge cannot be taken back.
#:
#: **Fill what is blank, never overwrite what is filled.** That is the same rule the opinions
#: table already follows here, and the same one the look-up sheet states in words: anything
#: already filled in starts untouched. It is also the only rule that cannot lose anything: where
#: both know something the survivor's own answer stands, and where only one of them knows it the
#: library ends up knowing it too.
#:
#: `COALESCE` is enough on its own because of how these columns are written: the record's writer
#: turns an emptied box into NULL rather than into an empty string, so "blank" is one value in the
#: column and not two. One literal statement rather than a loop building SQL from column names:
#: there is no ORM here and no statement is assembled from a table or column name.
_FILL_BLANKS_FROM = """
UPDATE people
   SET disambiguation     = COALESCE(disambiguation,     (SELECT disambiguation     FROM people WHERE id = :losing)),
       gender             = COALESCE(gender,             (SELECT gender             FROM people WHERE id = :losing)),
       birth_date         = COALESCE(birth_date,         (SELECT birth_date         FROM people WHERE id = :losing)),
       country            = COALESCE(country,            (SELECT country            FROM people WHERE id = :losing)),
       ethnicity          = COALESCE(ethnicity,          (SELECT ethnicity          FROM people WHERE id = :losing)),
       eye_color          = COALESCE(eye_color,          (SELECT eye_color          FROM people WHERE id = :losing)),
       hair_color         = COALESCE(hair_color,         (SELECT hair_color         FROM people WHERE id = :losing)),
       height_cm          = COALESCE(height_cm,          (SELECT height_cm          FROM people WHERE id = :losing)),
       measurements       = COALESCE(measurements,       (SELECT measurements       FROM people WHERE id = :losing)),
       breast_type        = COALESCE(breast_type,        (SELECT breast_type        FROM people WHERE id = :losing)),
       career_start_year  = COALESCE(career_start_year,  (SELECT career_start_year  FROM people WHERE id = :losing)),
       career_end_year    = COALESCE(career_end_year,    (SELECT career_end_year    FROM people WHERE id = :losing)),
       tattoos            = COALESCE(tattoos,            (SELECT tattoos            FROM people WHERE id = :losing)),
       piercings          = COALESCE(piercings,          (SELECT piercings          FROM people WHERE id = :losing)),
       notes              = COALESCE(notes,              (SELECT notes              FROM people WHERE id = :losing))
 WHERE id = :keeping
"""

#: The cover, moved as a PAIR and only onto a survivor that has none.
#:
#: Its own statement rather than two more lines above, because the two columns are one fact: a
#: face is a piece of a file, and whether it may be shown is decided on the file it came from. Two
#: independent COALESCEs could take the survivor's asset and the other person's face and produce a
#: cover naming a face that is not in that file.
#:
#: A cover somebody CHOSE, and only that. The picture the rule gave the person going (their first
#: file's, `cover_by_default`) is not theirs to hand on: carried, it would arrive as a chosen
#: cover, which the rule may never replace, and the line "cover picture filled in" would report a
#: choice nobody made. The survivor without one is given its own by the rule after the files have
#: moved (`assign_if_empty`, in the merge below).
_TAKE_COVER_FROM = """
UPDATE people
   SET cover_asset_id = (SELECT cover_asset_id FROM people WHERE id = :losing),
       cover_track_id = (SELECT cover_track_id FROM people WHERE id = :losing),
       cover_at_ms = (SELECT cover_at_ms FROM people WHERE id = :losing),
       cover_frame = (SELECT cover_frame FROM people WHERE id = :losing)
 WHERE id = :keeping
   AND cover_asset_id IS NULL
   AND cover_upload_id IS NULL
   AND (SELECT cover_asset_id FROM people WHERE id = :losing) IS NOT NULL
   AND NOT EXISTS (
         SELECT 1 FROM people gone
          WHERE gone.id = :losing AND gone.cover_by_default IS NOT NULL
            AND gone.cover_asset_id = gone.cover_by_default)
"""

#: The survivor's record columns, in the order `_FILL_BLANKS_FROM` fills them, with the word the
#: record calls each one by: the registry's key, which is the column's name everywhere but
#: `notes`, kept under that name because renaming a column is a migration.
#:
#: !! THIS IS THE SECOND PLACE THE FILL IS SPELLED, and it is held to the first by a test rather
#: than trusted: `test_merge.py` fills every one of these on the person going, weighs, merges, and
#: requires the weigh to have named exactly the columns the merge then changed. A column added to
#: the statement and not here is a fact that moves without the screen saying so.
_RECORD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("disambiguation", "disambiguation"),
    ("gender", "gender"),
    ("birth_date", "birth_date"),
    ("country", "country"),
    ("ethnicity", "ethnicity"),
    ("eye_color", "eye_color"),
    ("hair_color", "hair_color"),
    ("height_cm", "height_cm"),
    ("measurements", "measurements"),
    ("breast_type", "breast_type"),
    ("career_start_year", "career_start_year"),
    ("career_end_year", "career_end_year"),
    ("tattoos", "tattoos"),
    ("piercings", "piercings"),
    ("notes", "details"),
)

#: One person's record, as the weigh compares it. A literal list, the same columns as above plus
#: the cover, which moves as a pair and is reported as one thing.
_RECORD_ROW = """
SELECT name, disambiguation, gender, birth_date, country, ethnicity, eye_color, hair_color,
       height_cm, measurements, breast_type, career_start_year, career_end_year, tattoos,
       piercings, notes, cover_asset_id, cover_by_default
  FROM people WHERE id = ?
"""

#: How many of each list the weigh names. The COUNT is always the whole number; this bounds only
#: the names that ride along with it, because a person can hold thousands of usernames and a
#: confirm sheet reads a handful and folds the rest. The screen says how many it was not told.
NAMED_AT_MOST = 50

#: The usernames that move with a person, and the site each one is on, the two words somebody
#: recognises a username by. Ordered so the same weigh names the same ones every time, by the
#: stored keys (`kernel.sorting`), so an accented name is named where it is filed.
_USERNAMES_NAMED = """
SELECT u.name AS name, s.name AS site
  FROM usernames u LEFT JOIN sites s ON s.id = u.site_id
 WHERE u.person_id = ?
 ORDER BY COALESCE(u.name_sort, u.name), COALESCE(s.name_sort, s.name), u.id
 LIMIT ?
"""

#: In the order the person's own page lists them (`service._ALIASES_OF_PERSON`), so the handful
#: named are the ones somebody would see first there.
_ALIASES_NAMED = """
SELECT alias AS name FROM people_aliases WHERE person_id = ?
 ORDER BY COALESCE(alias_sort, alias), id
 LIMIT ?
"""

#: A saved link is recognised by the SITE it is on where Sift knows that, and by its own label or
#: address where it does not. A label or an address has no stored key to be put in name order by,
#: so the handful named are taken in id order, which is stable; the person's own page orders links
#: by the clock because carried-over links have ids nothing minted in order, and a wall clock is
#: not an order this list may take up.
_LINKS_NAMED = """
SELECT COALESCE(s.name, l.label, l.url) AS name, l.url AS url
  FROM people_links l LEFT JOIN sites s ON s.id = l.site_id
 WHERE l.person_id = ?
 ORDER BY l.id
 LIMIT ?
"""

#: The tags that come over: the ones the person going carries and the survivor does not (a tag
#: both carry stays one tag, `_TABLES`). Counted whole, and named in name order up to the bound.
_TAGS_BROUGHT = """
SELECT COUNT(*) AS n FROM person_tags
 WHERE person_id = ? AND tag_id NOT IN (SELECT tag_id FROM person_tags WHERE person_id = ?)
"""
_TAGS_BROUGHT_NAMED = """
SELECT t.name AS name FROM person_tags pt JOIN tags t ON t.id = pt.tag_id
 WHERE pt.person_id = ? AND pt.tag_id NOT IN (SELECT tag_id FROM person_tags WHERE person_id = ?)
 ORDER BY COALESCE(t.name_sort, t.name), t.id
 LIMIT ?
"""

#: A value long enough to be a paragraph (a person's details) is cut here rather than sent
#: whole: the line it lands in says which box is filled and from whom, and the whole text is one
#: press away on the record once it is there.
_VALUE_AT_MOST = 120


_NAME = "SELECT name FROM people WHERE id = ?"
#: The loser's name, kept as an alias on the keeper. `added_at` is the moment of the MERGE and not
#: the loser's own creation: the keeper did not answer to this name until now, and the row records
#: when it was put there rather than when the word was first used of somebody.
_ADD_ALIAS = (
    "INSERT OR IGNORE INTO people_aliases (id, person_id, alias, alias_sort, added_at)"
    " VALUES (?, ?, ?, ?, ?)"
)
_ALIAS_HELD = "SELECT 1 FROM people_aliases WHERE person_id = ? AND alias = ? COLLATE NOCASE"
_REMOVE = "DELETE FROM people WHERE id = ? RETURNING id"


@dataclass(frozen=True, slots=True)
class Named:
    """One thing a merge moves, BY NAME, and whose it was.

    The counts alone say "1 username they post from moves too" and leave the question somebody
    actually has unanswered: which one, on which site? A merge cannot be taken back, so the sheet
    names what it can.
    """

    name: str
    #: Where it is, where that is a second word it is known by: a username's site.
    where: str | None
    #: The one going it belonged to, by name.
    whose: str


@dataclass(frozen=True, slots=True)
class Counted:
    """How many of something one of the people going holds: their confirmed faces."""

    whose: str
    count: int


@dataclass(frozen=True, slots=True)
class Filled:
    """One box on the survivor's record that is blank now and filled by the merge.

    `label` is the record's own word for it and `value` what lands there, so the line can say
    "Birthdate, missing here, is filled in from Jane: 1998-04-02". `value` is empty for the
    cover, which is a picture and not a word.
    """

    key: str
    label: str
    value: str
    whose: str


def fill_value(kind: RecordKind | None, value: object) -> str:
    """A stored value as the line on the sheet says it, by the KIND the record declares the field.

    Read off the registry rather than off a column list of this file's own: a list of names is
    stored as JSON text (a person's tattoos and piercings), and a line reading `["rose, left
    wrist"]` is the database talking. A length is a height in centimetres. Shared with the site
    merge.
    """
    said = str(value)
    if kind is RecordKind.NAMES:
        try:
            listed = json.loads(said)
        except ValueError:
            listed = None
        if isinstance(listed, list):
            said = ", ".join(str(one) for one in listed)
    elif kind is RecordKind.LENGTH:
        said = f"{value} cm"
    if len(said) > _VALUE_AT_MOST:
        return said[: _VALUE_AT_MOST - 1].rstrip() + "\u2026"
    return said


@dataclass(frozen=True, slots=True)
class Weighed:
    """What a merge would move, counted before anything moves.

    This is the whole of the guard. A merge cannot be taken back, so the numbers have to be on the
    screen beside the two names before the button is pressed, and they have to be counted rather
    than estimated, because the one that matters is how many files change hands.
    """

    from_name: str
    into_name: str
    files: int
    usernames: int
    aliases: int
    links: int
    faces: int
    #: How many things the survivor does not know and the person going does: a birthdate, a
    #: nationality, a hair colour. Counted because it is the half of a merge that would otherwise be
    #: destroyed silently with the row. A number on the confirm screen is how somebody sees it
    #: happening.
    facts: int = 0
    #: The same moves BY NAME, each list bounded by `NAMED_AT_MOST` while its count is whole.
    usernames_named: tuple[Named, ...] = ()
    aliases_named: tuple[Named, ...] = ()
    links_named: tuple[Named, ...] = ()
    #: Whose confirmed faces move, and how many of each person's.
    faces_from: tuple[Counted, ...] = ()
    #: Every blank on the survivor that the merge fills, with the value and whose it was. `facts`
    #: is how many of these there are.
    filled: tuple[Filled, ...] = ()


async def _named(
    database: Database, statement: str, one: str, whose: str, *, where: str | None = None
) -> tuple[Named, ...]:
    rows = await database.fetch_all(statement, (one, NAMED_AT_MOST))
    return tuple(
        Named(
            name=str(row["name"]),
            where=None if where is None or row[where] is None else str(row[where]),
            whose=whose,
        )
        for row in rows
    )


async def _filled(
    database: Database, *, going: Sequence[str], keeping: str
) -> tuple[Filled, ...] | None:
    """Every blank on the survivor that folding `going` in, IN THAT ORDER, would fill.

    Worked out the way the fill decides, left to right: `merge_many` folds the people going in the
    order given, and `COALESCE` leaves a box alone once it holds something, so where two of them
    know the same fact the FIRST one named fills it and the second is never read. Summing each
    pair's gain separately would count that one box twice.

    None if any id names nobody.
    """
    kept = await database.fetch_one(_RECORD_ROW, (keeping,))
    if kept is None:
        return None
    rows = []
    for one in going:
        row = await database.fetch_one(_RECORD_ROW, (one,))
        if row is None:
            return None
        rows.append(row)
    filled: list[Filled] = []
    for column, key in _RECORD_COLUMNS:
        if kept[column] is not None:
            continue
        giver = next((row for row in rows if row[column] is not None), None)
        if giver is None:
            continue
        declared = record_field(RecordSubject.PERSON, key)
        filled.append(
            Filled(
                key=key,
                label=declared.label if declared is not None else key.replace("_", " "),
                value=fill_value(declared.kind if declared is not None else None, giver[column]),
                whose=str(giver["name"]),
            )
        )
    # The cover as ONE thing, onto a survivor with none, from the first of them that CHOSE one,
    # exactly what `_TAKE_COVER_FROM` does, one person at a time. The rule's own pick is not a
    # thing brought over: the survivor gets its own from the files it holds afterwards.
    if kept["cover_asset_id"] is None:
        giver = next(
            (
                row
                for row in rows
                if row["cover_asset_id"] is not None
                and row["cover_asset_id"] != row["cover_by_default"]
            ),
            None,
        )
        if giver is not None:
            filled.append(
                Filled(key="cover", label="Cover picture", value="", whose=str(giver["name"]))
            )
    return tuple(filled)


async def weigh(database: Database, *, losing: str, keeping: str) -> Weighed | None:
    """Count what merging one person into another would move. Nothing is written.

    None when either id names nobody, which is the answer a screen wants: a merge whose halves
    cannot both be found is not a merge that failed, it is one that was never possible.
    """
    names = {}
    for person_id in (losing, keeping):
        row = await database.fetch_one(_NAME, (person_id,))
        if row is None:
            return None
        names[person_id] = str(row["name"])

    counted: dict[str, int] = {}
    for table in _TABLES:
        if table.count:
            # Unpacked rather than guarded: every one of these is a bare COUNT, which answers
            # with exactly one row whether or not the person has anything of that kind.
            (row,) = await database.fetch_all(table.count, (losing,))
            counted[table.what] = int(row["n"])
    whose = names[losing]
    filled = await _filled(database, going=[losing], keeping=keeping) or ()
    faces = counted.get("faces", 0)
    return Weighed(
        from_name=whose,
        into_name=names[keeping],
        files=counted.get("files", 0),
        usernames=counted.get("usernames", 0),
        aliases=counted.get("aliases", 0),
        links=counted.get("links", 0),
        faces=faces,
        facts=len(filled),
        usernames_named=await _named(database, _USERNAMES_NAMED, losing, whose, where="site"),
        aliases_named=await _named(database, _ALIASES_NAMED, losing, whose),
        links_named=await _named(database, _LINKS_NAMED, losing, whose),
        faces_from=(Counted(whose=whose, count=faces),) if faces else (),
        filled=filled,
    )


async def _one_into(
    connection: Connection, *, losing: str, keeping: str, name: str
) -> list[object]:
    """One person folded into another, inside a transaction somebody else opened.

    Every merge goes through here, whether one person is being folded in or six. A second, pairwise
    path with its own copy of the sequence would be a second definition of what a merge IS, free to
    come apart the first time one of them gained a table and the other did not. A set of one is a
    set.
    """
    for table in _TABLES:
        if table.before is not None:
            await connection.execute(table.before, (keeping, losing))
        await connection.execute(table.move, (keeping, losing))
        if table.sweep is not None:
            await connection.execute(table.sweep, (losing,))
    # Before the delete, and inside the same transaction as everything else. What the person going
    # knew is on their own ROW, so it is the one part of them that no table above moves and that
    # the delete below takes with it.
    where = {"keeping": keeping, "losing": losing}
    await connection.execute(_FILL_BLANKS_FROM, where)
    await connection.execute(_TAKE_COVER_FROM, where)
    # A survivor still without one gets its first file's, from everything it holds now. After the
    # carry, so a picture the person going wore is kept over a frame (see `default_covers`).
    await assign_if_empty(connection, "person", keeping)
    # The record follows them, name kept, before the row goes. See `kernel/access/merged.py`.
    await follow(connection, kind="person", losing=losing, keeping=keeping, name=name)
    await _keep_the_name(connection, name=name, on=keeping)
    return list(await connection.execute_fetchall(_REMOVE, (losing,)))


async def weigh_many(database: Database, *, losing: Sequence[str], keeping: str) -> Weighed | None:
    """What folding several people into one would move, added up.

    One set of numbers rather than one per pair, because that is the question being asked: somebody
    who has picked four rows wants to know what happens when they press the button, not four
    answers they have to add up themselves.

    `from_name` is how many are going rather than a name, since there is no one name to give. The
    screen words it; this says how many.

    None if any id names nobody, or if the survivor is among the ones going. Both are the same
    class of answer: not a merge that failed, one that was never possible.
    """
    going = [one for one in dict.fromkeys(losing) if one != keeping]
    if not going:
        return None
    into = await database.fetch_one(_NAME, (keeping,))
    if into is None:
        return None
    total = Weighed(
        from_name="", into_name=str(into["name"]), files=0, usernames=0, aliases=0, links=0, faces=0
    )
    for one in going:
        weighed = await weigh(database, losing=one, keeping=keeping)
        if weighed is None:
            return None
        total = Weighed(
            from_name=weighed.from_name if not total.from_name else total.from_name,
            into_name=total.into_name,
            files=total.files + weighed.files,
            usernames=total.usernames + weighed.usernames,
            aliases=total.aliases + weighed.aliases,
            links=total.links + weighed.links,
            faces=total.faces + weighed.faces,
            usernames_named=(total.usernames_named + weighed.usernames_named)[:NAMED_AT_MOST],
            aliases_named=(total.aliases_named + weighed.aliases_named)[:NAMED_AT_MOST],
            links_named=(total.links_named + weighed.links_named)[:NAMED_AT_MOST],
            faces_from=total.faces_from + weighed.faces_from,
        )
    # The fills over the WHOLE set, not added up per pair: see `_filled`.
    filled = await _filled(database, going=going, keeping=keeping)
    if filled is None:  # pragma: no cover (every id was resolved by `weigh` a moment ago)
        return None
    return replace(total, facts=len(filled), filled=filled)


async def merge_many(
    database: Database,
    access: Repository,
    *,
    losing: Sequence[str],
    keeping: str,
    actor: Actor,
) -> Weighed | None:
    """Fold several people into one, in ONE transaction.

    Not a loop over `merge` at the caller, and the difference is the whole reason this exists. A
    merge cannot be taken back, so four merges done as four calls is four chances to stop halfway,
    and halfway through folding four people into one is a library where two of them are gone,
    two are still there, and nothing anywhere says which state it is in or how to finish. One
    transaction makes the whole set an act somebody either took or did not.

    The counts come back added up, so the toast afterwards says what happened to the set rather
    than what happened to the last one.

    The order within the transaction matters and is the order given: each person is folded onto the
    survivor in turn, so where two of them know the same fact the FIRST one named wins the blank:
    the same "fill what is blank" rule, applied left to right rather than in whichever order the
    database happened to return rows.
    """
    going = [one for one in dict.fromkeys(losing) if one != keeping]
    if not going:
        return None
    weighed = await weigh_many(database, losing=going, keeping=keeping)
    if weighed is None:
        return None

    # Before the transaction, and for every one of them, for the reason a single merge gives: a
    # grant is a decision somebody took about one specific person, and forgetting only ever removes
    # access, the safe direction to be wrong in.
    for one in going:
        await access.forget_object(ObjectType.PERSON, one)

    names = {}
    for one in going:
        row = await database.fetch_one(_NAME, (one,))
        if row is None:  # pragma: no cover (weighed above resolved every one of them)
            return None
        names[one] = str(row["name"])
    brought = await _what_each_brings(database, going=going, keeping=keeping)
    if brought is None:  # pragma: no cover (weighed above resolved every one of them)
        return None

    async with database.write() as connection:
        for one in going:
            if not await _one_into(connection, losing=one, keeping=keeping, name=names[one]):
                # Refuse the whole set rather than commit part of it. Leaving the block by raising
                # is what rolls the transaction back; returning would commit what had been done.
                raise _PartialMerge(one)
            # ONE event per person folded in, in the transaction that folded them, and the loser is
            # the SUBJECT. A merge moves every table above and deletes a row, and without this
            # nothing would be written down anywhere: the person who went is the one nobody can
            # look up afterwards, so theirs is the history this line has to appear in, with the name
            # they had, because the row is gone by the time anybody reads it. The survivor's page
            # reads the same event from its object side.
            #
            # WHAT CAME OVER rides in the payload: the counts the confirm sheet showed for this one
            # person, and which of the survivor's blanks they filled. The sheet is gone once the
            # button is pressed and nothing else writes those numbers down, so "was merged into
            # them" could otherwise say that it happened and never what it brought.
            await record_event(
                connection,
                actor=actor,
                verb="merged",
                subject=Subject(kind="person", id=one, name=names[one]),
                object=Object(kind="person", id=keeping, name=weighed.into_name),
                payload=brought[one],
            )
        # TOLD, on the commit, from inside the write that did it. The grant-forgetting write above
        # commits BEFORE this transaction opens, so a screen re-reading on that alone could draw the
        # person going as still there and never be told again. On a library whose queue holds the
        # write lock, the gap is however long this transaction waits for it.
        #
        # Who: everyone a change to a file could concern, not only admins. A merge moves files
        # between people, so a user who was given a person, or a folder somebody in it is on, has a
        # wall that changes too; `who_may_see_a_file` is the cheap wide answer the kernel names for
        # exactly that question, and a merge is rare enough that wide costs nothing.
        announce(await who_may_see_a_file(connection), About.LIBRARY)

    log.info("people.merged_many", people=len(going), files=weighed.files)
    return weighed


async def _what_each_brings(
    database: Database, *, going: Sequence[str], keeping: str
) -> dict[str, str] | None:
    """What each person going brings to the survivor, as the payload of their `merged` event.

    Counted exactly as the confirm sheet counts it (`weigh`), and the blanks worked out in the order
    the fold runs: where two of them know the same fact the first one named fills it, so each key is
    credited to the person whose fold wrote it: `_filled` over the set up to them, less what was
    already filled before them. Read before the transaction for the reason the weigh is: it is the
    state the merge is about to act on.

    The usernames, other names, links and tags that move are written down BY NAME as well
    (`named`, the first `MERGE_NAMES_KEPT` of each), because once the rows are on the survivor
    nothing tells which of hers came from whom, and "Brought over 3 other names" could never say
    which. The count stays the whole number.

    None if any id names nobody.
    """
    brought: dict[str, str] = {}
    filled_before: set[str] = set()
    for at, one in enumerate(going):
        each = await weigh(database, losing=one, keeping=keeping)
        upto = await _filled(database, going=going[: at + 1], keeping=keeping)
        if each is None or upto is None:
            return None
        filled = [one_fill.key for one_fill in upto if one_fill.key not in filled_before]
        filled_before.update(filled)
        (tagged,) = await database.fetch_all(_TAGS_BROUGHT, (one, keeping))
        tags = await database.fetch_all(_TAGS_BROUGHT_NAMED, (one, keeping, MERGE_NAMES_KEPT))

        def kept(named: Sequence[Named]) -> list[dict[str, str | None]]:
            return [{"name": n.name, "where": n.where} for n in named[:MERGE_NAMES_KEPT]]

        brought[one] = json.dumps(
            {
                "files": each.files,
                "usernames": each.usernames,
                "aliases": each.aliases,
                "links": each.links,
                "tags": int(tagged["n"]),
                "faces": each.faces,
                "filled": filled,
                "named": {
                    "usernames": kept(each.usernames_named),
                    "aliases": kept(each.aliases_named),
                    "links": kept(each.links_named),
                    "tags": [{"name": str(row["name"]), "where": None} for row in tags],
                },
            },
            sort_keys=True,
        )
    return brought


class _PartialMerge(RuntimeError):
    """One of a set could not be removed, so none of them is. Never reaches a person: the ids were
    all resolved a moment earlier, so this is the impossible case being made impossible rather
    than a failure with a message."""


async def _keep_the_name(connection: Connection, *, name: str, on: str) -> None:
    """Write the name that is going onto the survivor, unless they already answer to it.

    Checked rather than left to `INSERT OR IGNORE` alone. The unique constraint folds case, so the
    insert would be quietly correct, but the survivor's own NAME is not in the alias table, and
    merging `Jane Doe` into `jane doe` would otherwise write an alias identical to the name it sits
    under. An alias that repeats the name adds nothing to find them by and reads as a mistake.
    """
    row = await (await connection.execute(_NAME, (on,))).fetchone()
    if row is not None and str(row["name"]).casefold() == name.casefold():
        return
    held = await (await connection.execute(_ALIAS_HELD, (on, name))).fetchone()
    if held is not None:
        return
    await connection.execute(_ADD_ALIAS, (new_id(), on, name, sort_key(name), int(time.time())))


def tables_that_move() -> frozenset[str]:
    """Every table a merge touches, for the check that holds this list to the schema.

    Read out of the statements themselves rather than written beside them. A list of table names
    kept next to the statements that name them is a list that drifts the first time somebody adds a
    table and edits only one of the two.
    """
    return frozenset(
        one.move.split("UPDATE OR IGNORE ")[-1]
        .split("UPDATE ")[-1]
        .split("DELETE FROM ")[-1]
        .split()[0]
        for one in _TABLES
    )
