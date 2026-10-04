# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happened to a site, a tag, a shelf or a Photo Set, in order.

`history.py` asks that question of a FILE and `history_person.py` of a PERSON. This asks it of the
four remaining things in the catalog that have a page of their own. Same `Event`, same actors, same
ordering rule: four different subjects. A record somebody can read about one kind of thing and
not about the next one along reads as the tab being broken.

## One module and not four, and the tables are why

The shape is genuinely shared rather than merely similar. Every one of the four answers the same
three questions (when was it made, what has been put on it, and who has it been shared with), and
two of them answer a fourth, which is whether a stash-box knows it. What differs between them is a
table name, a column name and a run of words, and every one of those is a value. So this is four
public functions over one private shape, with what differs kept in values rather than in a protocol:
adding a fifth kind is a row, not a file. Four separate modules would be four copies of the grant
read, four copies of the day-grouping, and four chances for one of them to forget that a grant is
an admin's to see.

A USERNAME has no thread of its own: it is shown under its person and its site. What was posted
under a username is on its site's thread, and a username joined to somebody or taken off them is a
ledger act that names the person, so it is on theirs.

## What each of them actually holds, from the tables and not from what would be nice

**A tag** was made at a moment (`tags.created_at`), is put on files by somebody or by a pass
(`asset_tags.source` and `.decided_at`), and may be linked to a stash-box (`tag_stash_box_links`).

**A site** carries `created_at` from v41 of the catalog, nullable: an older row's moment was read
off the earliest signal it had, and a row with no signal has none, so the arrival event is drawn
only where there is a moment to draw it at. A made-up date on a screen somebody is reading to find
out what really happened is the one thing a history may not do. It also holds the usernames added
to it (`usernames.created_at`), the filings made under those usernames (`asset_usernames`), and a
stash-box link (`site_stash_box_links`).

**A shelf** and **a Photo Set** were each made at a moment and hold items, and their membership
tables record when each file went in (`added_at`, catalog v47). A shelf records WHO made it
(`collections.owner_id`) and a Photo Set records HOW it was made (`photo_sets.origin`), and each
of those is said in the sentence.

## What each statement costs

EXPLAIN QUERY PLAN against an initialized schema:

    the tag, the site, the shelf, the set   SEARCH ... USING INDEX sqlite_autoindex_<table>_1 (id=?)
    files a tag is on                       SEARCH link USING INDEX ix_asset_tags_tag (tag_id=?)
    usernames on a site                     SEARCH usernames USING INDEX ix_usernames_site
    filings under a site                    SEARCH ac USING INDEX ix_usernames_site, then
                                            SEARCH link USING INDEX ix_asset_usernames_username
    a stash-box link                        SEARCH l USING INDEX sqlite_autoindex_..._1, then the
    box who it is shared with                   SEARCH g USING INDEX ix_acl_object

Every one of them is a seek. The ones that group add `USE TEMP B-TREE FOR GROUP BY`, which is the
sort of what the seek found and not a second pass over a table. The two that count across the
library (what a tag is on and what is filed under a site) GROUP in SQL for the reason a person's
history does: a tag on four thousand files is not four thousand lines anybody can read, and what
crosses out of SQLite is one row per day rather than one row per file.

## Why none of these is declared a `point_read`

Because `point_read` is a claim that a statement is constant-time and may therefore run on the event
loop, and the two counting reads are not. They seek and then aggregate over every row the seek
found, which on a tag somebody has put on a large part of their library is real work. A seek is not
the same claim as a bounded one, and declaring it here would put that work on the loop on exactly
the install where it is biggest. `history_person.py` reaches the same conclusion about the same
shape; the file history's reads ARE declared, and every one of them answers about one file.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol

# The wording, and the addresses the numbers in it go to. One table for all three histories (see
# that module's header for why the words moved out of the reads that say them).
from sift.kernel.access import sentences as say
from sift.kernel.access.history import (
    DEFAULT_LIMIT,
    MADE_BY_BOX,
    MAX_LIMIT,
    VIAS,
    Actor,
    Detail,
    Event,
    Link,
    _decision_events,
    _names_of,
    _via_of_source,
    _Who,
    actor_of_source,
    by_of,
    files_of_decisions,
    files_of_filing,
    kept_events,
    ledger_events,
    maker_of,
    one_line_per_kept,
    ordered,
    runs_not_drawn,
    share_makers,
    stash_box_tables_in,
)
from sift.kernel.access.history_boxes import BOX_OF_A_ROW, thing_linked
from sift.kernel.access.history_events import NOTHING_HIDDEN, verdict_of
from sift.kernel.access.history_folds import (
    NO_RECEIPT,
    RECEIPT_OF_A_FILING,
    RECEIPT_OF_A_TAGGING,
    Counted,
    counted_apart_from_receipts,
    drawn_receipts,
)
from sift.kernel.access.history_sources import _folders_seen
from sift.kernel.access.repository import Repository
from sift.kernel.access.sentences import (
    SIFT,
    VANTAGE_ENTITY,
    Line,
    Piece,
    files,
    username_opens,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.access.visibility import FILE_SEEN_BY_VIEWER, seen_by
from sift.kernel.access.worded import decided_said, lines_under
from sift.kernel.db import Database, Row
from sift.kernel.records import Subject
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import LEDGER_QUEUE
from sift.kernel.when import day_number
from sift.kernel.where import folder_said

if TYPE_CHECKING:  # pragma: no cover (the registry type, for the annotation only)
    from sift.kernel.workbench import Workbench

#: The tables a FEATURE owns that these reads touch, and which may therefore not be there.
#: Same guard and same reason as every other history: a process that never imported the stash-box
#: slice has never registered its schema, so `tag_stash_box_links` is genuinely absent and a
#: statement naming it is a hard error rather than an empty answer.
_FEATURE_TABLES = (
    # The receipt source. Both, because `_DECIDED` names both.
    "workbench_decisions",
    "workbench_decision_subjects",
    "tag_stash_box_links",
    "site_stash_box_links",
    "stash_boxes",
    # Needed so a line names its box: the counting reads choose the box-naming statement by
    # `stash_box_tables_in(here)`, which asks for this table AND `stash_boxes`, and `here` only ever
    # holds what is listed here: without it every thread here would take the NULL-box statement
    # and say "A stash-box put it on 200 files". It matters twice: a line's count opens its own
    # files, and the box is part of which files those are.
    "asset_stash_box_matches",
    # A box disagreed about one of this thing's fields and the answer was to keep what was here:
    # the half of enrichment that says somebody judged.
    "stash_box_kept",
)

_TABLES = "SELECT name FROM sqlite_master WHERE type = 'table'"


#: EVERY BULK JUDGEMENT THAT NAMED ONE OF THESE FOUR THINGS, newest last.
#: The receipt source, as a file's history and a person's read it: a decision a person took ON one
#: of these, with an Undo sitting on the board, belongs on the page somebody stands on when they
#: wonder why it changed. Nothing new is recorded for this.
#:
#: The kind is BOUND rather than written in, unlike the file's and the person's: this one statement
#: serves four pages, and a copy per kind would be four places to forget the reserved word below.
#:
#: NOT FILTERED TO ONE QUEUE, for the reason the person's is not: queue names belong to the slices
#: that register them, and a kernel read naming one would hold a copy of a slice's vocabulary.
#:
#: The SEARCH is `ix_workbench_subject (kind, subject_id)`, the same index the other two use.
#:
#: !! AND IT MUST NOT DRAW AN EVENT. The ledger is built by widening this table, so every act a
#: writer records lands here, and read straight out, an event draws as a bulk judgement with an
#: Undo on it, settled at a workbench nobody went to, offering a button the workbench refuses. The
#: word `LEDGER_QUEUE` is reserved so no queue may claim it, which makes the exclusion exact. An
#: event carrying a RECEIPT is written under that receipt's own queue and still draws, which is
#: right: it is a decision and it can be taken back. `_ledger_events` draws the rest.
#:
#: Only a receipt whose every file this viewer may be shown: its title's number was written when it
#: was taken and cannot be scoped when it is read. See `history_person._DECIDED`, which carries the
#: whole reasoning; the rule is the ledger's own, spliced from `history_events`.
_DECIDED = splice(
    """
SELECT d.id AS id, d.title AS title, d.queue AS queue, d.user_id AS user_id,
       d.actor_kind AS actor_kind, d.actor_id AS actor_id,
       d.decided_at AS decided_at, d.reversed_at AS reversed_at, d.verb AS verb,
       d.object_kind AS object_kind, d.object_id AS object_id, d.payload AS payload,
       d.detail AS detail
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = :kind AND s.subject_id = :subject AND d.queue <> :ledger
{{NOTHING_HIDDEN}}
 ORDER BY d.decided_at ASC, d.id ASC
""",
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

#: What one of the grouped statements below answers with: who did it, the name to show beside it,
#: and the sentence. Three values rather than an object, because it is what `Event` takes.
_Said = Callable[[str | None, Piece, int, str | None], "tuple[Actor, str | None, Line]"]

#: The maker columns ride along with the name and the moment, because the arrival event needs all
#: three and a second read for them would be a second point read per page. `created_by_act` is
#: which act made the file a tag Sift made for a copy came from (`sentences.FROM_ACT`).
_TAG = (
    "SELECT name, created_at, created_by_kind, created_by_via, created_by_user_id,"
    " created_by_box_id, created_by_act"
    " FROM tags WHERE id = ?"
)
_COLLECTION = "SELECT name, owner_id, created_at FROM collections WHERE id = ?"
_PHOTO_SET = (
    "SELECT s.name AS name, s.origin AS origin, s.created_at AS created_at,"
    " s.folder_id AS folder_id, f.root_id AS folder_root, f.rel_path AS folder_path,"
    " f.name AS folder_name"
    " FROM photo_sets s LEFT JOIN folders f ON f.id = s.folder_id WHERE s.id = ?"
)
_SONG = (
    "SELECT name, created_at, created_by_kind, created_by_via, created_by_user_id"
    " FROM songs WHERE id = ?"
)

# EVERY FILE PUT IN A SHELF OR A PHOTO SET THAT THE LEDGER DID NOT RECORD.
# The membership tables carry the moment a file went in (`added_at`, catalog v47), so a shelf's own
# History can say every addition. The ledger says the ones it recorded, with who did
# them, and those lines are drawn from it; these are the REST (additions from before the ledger,
# and a Photo Set's files put there when a task made it), read off the row, so no addition is said
# twice and none is missing. Only files the viewer may see; one row per file, grouped by day below.
_SHELF_ADDITIONS = splice(
    """
SELECT link.asset_id AS id, COALESCE(NULLIF(a.title, ''), a.original_filename) AS name,
       link.added_at AS at
  FROM collection_items link
  JOIN assets a ON a.id = link.asset_id
 WHERE link.collection_id = :subject AND {{SEEN}}
   AND NOT EXISTS (SELECT 1 FROM workbench_decision_subjects s
                     JOIN workbench_decisions d ON d.id = s.decision_id
                    WHERE s.kind = 'asset' AND s.subject_id = link.asset_id
                      AND d.verb = 'linked' AND d.object_kind = 'collection'
                      AND d.object_id = link.collection_id)
 ORDER BY link.added_at, link.asset_id
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)
_SET_ADDITIONS = splice(
    """
SELECT link.asset_id AS id, COALESCE(NULLIF(a.title, ''), a.original_filename) AS name,
       link.added_at AS at
  FROM photo_set_items link
  JOIN assets a ON a.id = link.asset_id
 WHERE link.photo_set_id = :subject AND {{SEEN}}
   AND NOT EXISTS (SELECT 1 FROM workbench_decision_subjects s
                     JOIN workbench_decisions d ON d.id = s.decision_id
                    WHERE s.kind = 'asset' AND s.subject_id = link.asset_id
                      AND d.verb = 'linked' AND d.object_kind = 'photo_set'
                      AND d.object_id = link.photo_set_id)
 ORDER BY link.added_at, link.asset_id
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

# EVERY FILE A SONG WAS NAMED ON THAT THE LEDGER DID NOT RECORD AGAINST THE SONG.
#
# A song is named on a file by Sift (AcoustID, a Site's page, the same music as another file) or by
# a person's hand, and the membership row says which, with when and, for a name carried from the
# same music, the file it came from (`song_files`). Sift's acts are recorded on the FILE (one
# `song_named` each, which a song's id is not a subject of), and a name typed into a file's Music
# field is that file's edit; so the song's own thread reads them off the rows, grouped by day and
# source, and draws from the ledger only what a person did ON the song (`linked` with it as the
# object, from its own page), which is why those rows are left out here. Only files the viewer may
# see, and the file a name came from only where they may see that too.
_SONG_ADDITIONS = splice(
    """
SELECT link.asset_id AS id, COALESCE(NULLIF(a.title, ''), a.original_filename) AS name,
       link.added_at AS at, link.source AS source,
       CASE WHEN EXISTS (SELECT 1 FROM viewer_assets so
                          WHERE so.asset_id = link.from_asset_id AND so.user_id = :viewer
                            AND (:reveal = 1 OR so.concealed = 0))
            THEN link.from_asset_id END AS origin,
       COALESCE(NULLIF(o.title, ''), o.original_filename) AS origin_name
  FROM song_files link
  JOIN assets a ON a.id = link.asset_id
  LEFT JOIN assets o ON o.id = link.from_asset_id
 WHERE link.song_id = :subject AND {{SEEN}}
   AND NOT EXISTS (SELECT 1 FROM workbench_decision_subjects s
                     JOIN workbench_decisions d ON d.id = s.decision_id
                    WHERE s.kind = 'asset' AND s.subject_id = link.asset_id
                      AND d.verb = 'linked' AND d.object_kind = 'song'
                      AND d.object_id = link.song_id)
 ORDER BY link.added_at, link.asset_id
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

#: The most files one day's addition names; past it the line counts them. Four thousand files a
#: task put in one Photo Set are one line saying how many, not a list running off the row.
_ADDITIONS_NAMED = 50

#: How many of a counted day's files its "Show each" lists, newest first: the feed's cap for the
#: same list (`history_events.FEED_FOLD_SHOWN`).
_ADDITIONS_LISTED = 100
#: The row is read even without a moment, because whether it EXISTS decides between an empty history
#: and no history at all. `created_at` (catalog v41) is nullable on this one table, so an arrival
#: with no moment is drawn as "before this was recorded" where the row says who made it; a site
#: whose row carries no signal at all keeps the empty history it had.
_SITE = (
    "SELECT name, created_at, created_by_kind, created_by_via, created_by_user_id,"
    " created_by_box_id"
    " FROM sites WHERE id = ?"
)

#: Every file this tag was put on, gathered by WHAT decided it and by the DAY it was decided.
#:
#: The day is the machine's local day (`kernel/when.py`). It is null for a row written before
#: `decided_at` existed, so those rows fall
#: into a group of their own and come back with no time, which is exactly what they know.
#:
#: Only the files this viewer may be shown, counted as they are read: the reasoning is
#: `history_person._NAMED`'s, and so is the fragment.
_TAG_FILES_SQL = """
SELECT link.source AS source,
       {{BOX}} AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM asset_tags link
 WHERE link.tag_id = :subject AND {{SEEN}}
 GROUP BY source, box, day, receipt
"""
_TAG_FILES = splice(
    _TAG_FILES_SQL, BOX=BOX_OF_A_ROW, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=RECEIPT_OF_A_TAGGING
)
_TAG_FILES_NO_LEDGER = splice(
    _TAG_FILES_SQL, BOX=BOX_OF_A_ROW, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=NO_RECEIPT
)

#: The same, on a database without the stash-box tables (a process that never imported that
#: slice): NULL where the box would be. Fixed strings rather than one built from a fragment,
#: which is the rule query text lives under here. Each tagging carries its receipt where the
#: record of decisions is here (`history_folds.RECEIPT_OF_A_NAMING`, the one fold).
_TAG_FILES_NO_BOX_SQL = """
SELECT link.source AS source,
       NULL AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM asset_tags link
 WHERE link.tag_id = :subject AND {{SEEN}}
 GROUP BY source, box, day, receipt
"""
_TAG_FILES_NO_BOX = splice(
    _TAG_FILES_NO_BOX_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=RECEIPT_OF_A_TAGGING
)
_TAG_FILES_NO_BOX_NO_LEDGER = splice(
    _TAG_FILES_NO_BOX_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=NO_RECEIPT
)

# A FILING THAT IS A DOWNLOAD'S OWN ACT. A download files its file under the Site it came from in
# the same press (`download/service.attribute`), so a Site's thread would say "3 files were filed
# under it" beside "Sift downloaded 3 files from it", one act twice. The download's line is drawn
# from the ledger's `downloaded` event (`history._downloads_by_day`), and a filing is the
# download's where ITS ROW SAYS SO: the writer names `download` as the source, and catalog v67
# marks every older row a `downloaded` event vouches for. A filing the row does not
# claim for a download (one somebody made by hand, or a download from before the ledger) is
# counted here, as the only line saying it.
#
# Not by the clock: a filing whose row carries no time would never match, and a hand filing under
# the Site inside the minute would be swallowed. The row is the record.
_NOT_A_DOWNLOAD = "(link.source IS NULL OR link.source <> 'download')"

#: Every filing made under this site, through the usernames that belong to it.
#:
#: Two hops rather than one, because an asset has no site: it has usernames, and a username belongs
#: to a site. That is the same shape the permission resolver walks, and it is why the join is here
#: rather than a column being read off a row.
_SITE_FILES_SQL = """
SELECT link.source AS source,
       {{BOX}} AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM usernames ac
  JOIN asset_usernames link ON link.username_id = ac.id
 WHERE ac.site_id = :subject AND {{SEEN}} AND {{DOWNLOADS}}
 GROUP BY source, box, day, receipt
"""
_SITE_FILES = splice(
    _SITE_FILES_SQL,
    BOX=BOX_OF_A_ROW,
    SEEN=FILE_SEEN_BY_VIEWER,
    DOWNLOADS=_NOT_A_DOWNLOAD,
    RECEIPT=RECEIPT_OF_A_FILING,
)
_SITE_FILES_NO_LEDGER = splice(
    _SITE_FILES_SQL,
    BOX=BOX_OF_A_ROW,
    SEEN=FILE_SEEN_BY_VIEWER,
    DOWNLOADS=_NOT_A_DOWNLOAD,
    RECEIPT=NO_RECEIPT,
)

#: The same, on a database without the stash-box tables (a process that never imported that
#: slice): NULL where the box would be. Two fixed strings rather than one built from a fragment,
#: which is the rule query text lives under here.
_SITE_FILES_NO_BOX_SQL = """
SELECT link.source AS source,
       NULL AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM usernames ac
  JOIN asset_usernames link ON link.username_id = ac.id
 WHERE ac.site_id = :subject AND {{SEEN}} AND {{DOWNLOADS}}
 GROUP BY source, box, day, receipt
"""
_SITE_FILES_NO_BOX = splice(
    _SITE_FILES_NO_BOX_SQL,
    SEEN=FILE_SEEN_BY_VIEWER,
    DOWNLOADS=_NOT_A_DOWNLOAD,
    RECEIPT=RECEIPT_OF_A_FILING,
)
_SITE_FILES_NO_BOX_NO_LEDGER = splice(
    _SITE_FILES_NO_BOX_SQL, SEEN=FILE_SEEN_BY_VIEWER, DOWNLOADS=_NOT_A_DOWNLOAD, RECEIPT=NO_RECEIPT
)

#: The usernames on this site, one row each, oldest first, grouped by DAY in Python.
#:
#: Not a count per day: "36 usernames added to it" says nothing about which, so the line names them
#: and the names have to cross. That costs one row per username on the site, off
#: `ix_usernames_site`, bounded by the site and read once per pane, the same bound the site's own
#: People tab reads its usernames under.
#:
#: THE ROW WITH NO NAME IS NOT A USERNAME and is left out. It is the library's way of writing
#: "from this site, poster unknown" (`filed_sentence`), which a filing makes for itself; it has no
#: name to say, and counting it would make a site read "1 username added to it" when nobody had
#: added one. Its files are on the thread already, in the filings' own lines.
#:
#: The person rides along because a username has no page of its own: pressing one goes to its
#: person, or to the files under it where nobody is said (`sentences.username_opens`).
#:
#: AND ONLY THE USERNAMES THIS VIEWER MAY BE TOLD ABOUT. The line names each one, and a username
#: whose every file is kept from the viewer is a name that exists only on files they were not shown:
#: the same disclosure as a count of them. So a guest is told a username that has a file they may
#: be shown, off the stored per-username count; an admin is also told a username typed in with no
#: file yet. A NAME, so the vault flag is the strict one (`:reveal_named`, the vault really open):
#: with "Show a locked tile" on, a username whose every file is locked is not named either.
_SITE_USERNAMES = """
SELECT un.id AS id, un.name AS name, un.person_id AS person_id, un.created_at AS created_at
  FROM usernames un
 WHERE un.site_id = :subject AND un.name <> ''
   AND ((:admin = 1 AND NOT EXISTS (SELECT 1 FROM asset_usernames any_file
                                    WHERE any_file.username_id = un.id))
        OR EXISTS (SELECT 1 FROM viewer_entity_counts c
                    WHERE c.user_id = :viewer AND c.kind = 'username' AND c.object_id = un.id
                      AND c.permitted - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed END > 0))
 -- ordered by the clock: not every username has an id Sift minted in order; an old migration
 -- wrote random hex ones (access schema `_SITE_ONLY_ACCOUNT`)
 ORDER BY un.created_at ASC, COALESCE(un.name_sort, un.name) ASC, un.id ASC
"""

#: HOW EACH USERNAME ON THIS SITE ARRIVED, from its `added` event (subject the username, object the
#: Site; `catalog._seed_username_on` writes one, and workbench v14 backfilled the rest). Off
#: `ix_workbench_object`. The Site's own thread is where this is said: the event is otherwise drawn
#: only in the feed (`history._drawn_elsewhere` gives `added` to the arrival line, which on a Site
#: is the username line below). Newest first, so the first event per username is its latest word.
_ARRIVALS = """
SELECT s.subject_id AS id, d.actor_kind AS actor_kind, d.actor_id AS actor_id,
       d.payload AS payload
  FROM workbench_decisions d
  JOIN workbench_decision_subjects s ON s.decision_id = d.id AND s.kind = 'username'
 WHERE d.object_kind = 'site' AND d.object_id = ? AND d.verb = 'added'
 ORDER BY d.decided_at DESC, d.id DESC
"""

#: Grants on the thing itself, by the word `acl_grants` files it under.
#:
#: The object type is BOUND rather than written into four statements, which is the one place this
#: module's shared shape is load-bearing rather than tidy: the rule that a grant is an admin's to
#: read is written once, so there is no fourth copy of it to forget.
_GRANTS = """
SELECT u.username AS username, g.effect AS effect, g.created_at AS created_at,
       g.subject_user_id AS user_id
  FROM acl_grants g
  JOIN users u ON u.id = g.subject_user_id
 WHERE g.object_type = ? AND g.object_id = ?
 ORDER BY g.id ASC
"""


def _tagged_sentence(
    source: str | None, counted: Piece, count: int, box: str | None = None
) -> tuple[Actor, str | None, Line]:
    """Who put this tag on a run of files, and how the app says it.

    The words are `sentences.put_on`'s; what is decided here is the ACTOR, which is a fact about the
    source word rather than about the sentence. A word this build has never heard of still produces
    an event, attributed to Sift (which is what every non-null source means), because an unknown
    pass is still a pass and dropping the row would lose files somebody can see on screen.
    """
    actor, name = actor_of_source(source, box)
    return actor, name, say.put_on(by_of(actor, name), source, counted, count)


def _filed_sentence(
    source: str | None, counted: Piece, count: int, box: str | None = None
) -> tuple[Actor, str | None, Line]:
    """Who filed a run of files under this site, and how the app says it.

    `asset_usernames.source` is the third table to carry that column and it means what it means on
    the other two, so the folder arm is here and not in `_tagged_sentence`: a folder read files a
    site, and nothing reads a folder name and reaches a tag.
    """
    actor, name = actor_of_source(source, box)
    return actor, name, say.filed_under(by_of(actor, name), source, counted, count)


#: How a Photo Set came to be, in the words `photo_sets.origin` holds, and who that makes the actor.
#:
#: A set assembled by hand was made by somebody and the row does not record which user; the other
#: three were made by a pass Sift ran. The word is a CHECK constraint on the column, so this table
#: is closed by the schema rather than by hope, and a word outside it falls through to the
#: plainest sentence rather than to nothing.
_ORIGINS: dict[str, tuple[Actor, str]] = {
    "manual": (Actor.SOMEBODY, ""),
    "download": (Actor.SIFT, " from a download"),
    "folder": (Actor.SIFT, " from a folder"),
    "archive": (Actor.SIFT, " from an archive"),
    # A post read out of the files' own names, and a shoot. The sentence for the
    # first says NAMES rather than a download, because the task that makes it read a name and
    # downloaded nothing: a set claiming a download made it would name a task that never ran.
    "filename": (Actor.SIFT, " from the files' names"),
    "shoot": (Actor.SIFT, " from a shoot"),
}


async def _from_the_folder(access: Repository, viewer: Viewer, found: Row) -> Line | None:
    """ " from the folder Beach", the folder a set was made from named and linked to its files.

    None where the folder is gone or this reader may not see it all the way down (`kernel.where`),
    and the line keeps "from a folder", the words it has without one.
    """
    if found["folder_name"] is None:
        return None
    path, name = str(found["folder_path"] or ""), str(found["folder_name"])
    seen = await _folders_seen(access, viewer)
    if path and folder_said(path, seen=seen.get(str(found["folder_root"]), frozenset())) != path:
        return None
    return say.said(" from the folder ", say.folder_named(str(found["folder_id"]), name))


def _counted_events(
    rows: Sequence[Counted], kind: str, said_as: _Said, *, subject: str, parameter: str
) -> list[Event]:
    """One event per group, from a statement that counts files by source and by day.

    THE NUMBER IS THE WAY TO WHAT IT COUNTS: "Put on 4,000 files" with no way to those files is a
    number somebody then has to go and reproduce by hand. The link's name is the phrase the sentence
    already carries, so it is placed where it sits in the line, so nothing has to look for it.

    **What the address is, said plainly:** the files this GROUP counted, and no others (the Files
    wall filtered by `parameter` (`filed` or `tagged`) to this line's source, day and box,
    `files_of_filing`), never the whole set, which would open 500 files from a line saying 4.
    """
    events: list[Event] = []
    for row in rows:
        source = None if row["source"] is None else str(row["source"])
        count = int(row["files"])
        # Which box, by the filing or the file's one applied match (`history.BOX_OF_A_ROW`).
        box = None if row["box"] is None else str(row["box"])
        counted = say.thing(
            "files", subject, files(count), href=files_of_filing(parameter, subject, row)
        )
        actor, actor_name, line = said_as(source, counted, count, box)
        events.append(
            Event(
                at=None if row["at"] is None else int(row["at"]),
                actor=actor,
                actor_name=actor_name,
                kind=kind,
                pieces=line,
                via=_via_of_source(source),
            )
        )
    return events


def _arrival_of(row: Row | None, viewer: Viewer) -> tuple[Actor, str, bool]:
    """Who a username's arrival names, the task's phrase, and whether it is a backfill that could
    not say how, from its `added` event, or nobody where there is none."""
    if row is None:
        return Actor.SOMEBODY, "", False
    kind, who = row["actor_kind"], row["actor_id"]
    if kind == "sift" and who:
        return Actor.SIFT, str(who), False
    if kind == "user" and who:
        return (Actor.YOU if str(who) == viewer.id else Actor.ANOTHER_USER), "", False
    backfilled = '"backfilled"' in str(row["payload"] or "")
    return Actor.SOMEBODY, "", backfilled


def _username_events(
    rows: Sequence[Row],
    arrivals: Sequence[Row] = (),
    viewer: Viewer | None = None,
) -> list[Event]:
    """One line per DAY of usernames put on a site, naming each of them. Rows oldest first.

    Each name in the sentence is a link, and where it goes is the kernel's one rule for a username
    (`username_opens`): its person, or the files posted under it. Past `sentences.FEED_MOST` the
    line names five and says "and N more", and the "N more" is a fold that opens the rest IN PLACE
    (`sentences.listed`): one piece carrying them, rather than a detail group the client had to
    find by matching its words in the sentence.

    HOW EACH ARRIVED is its `added` event's (`_ARRIVALS`): a day's
    usernames are one line per way they came ("Sift added the usernames a and b to it from file
    names", "You added the username c to it"), and one the backfill could not read says it arrived
    "before Sift recorded how". A username with no event at all is passive, as the table alone says
    nothing about who made it.
    """
    told: dict[str, Row] = {}
    for one in arrivals:
        told.setdefault(str(one["id"]), one)
    days: dict[tuple[int, Actor, str, bool], list[Row]] = {}
    for row in rows:
        actor, via, untold = (
            _arrival_of(told.get(str(row["id"])), viewer)
            if viewer is not None
            else (Actor.SOMEBODY, "", False)
        )
        days.setdefault((day_number(int(row["created_at"])), actor, via, untold), []).append(row)
    events: list[Event] = []
    for (_day, actor, via, untold), day_rows in days.items():
        named = [
            say.thing(
                "username",
                str(row["id"]),
                str(row["name"]),
                href=username_opens(
                    str(row["id"]), None if row["person_id"] is None else str(row["person_id"])
                ),
            )
            for row in day_rows
        ]
        how = say.from_pass(via, len(named))
        events.append(
            Event(
                at=max(int(row["created_at"]) for row in day_rows),
                actor=actor,
                actor_name=SIFT if actor is Actor.SIFT else None,
                kind="filed",
                pieces=say.usernames_added(
                    by_of(actor, None), named, how if actor is Actor.SIFT else "", untold=untold
                ),
                # The mark's word only where the client has one for it (`history.VIAS`).
                via=via if via in VIAS else None,
            )
        )
    return events


#: How long after a task made a Photo Set a file going into it is still that task's filling. The
#: fill is written in the same press (`photo_sets/service._fill`), so a minute is the feed's
#: one-press gap (`history_events.FEED_FOLD_GAP`) rather than a new number; anything later nobody
#: recorded.
_FILLED_BY_THE_TASK = 60


def _addition_events(
    rows: Sequence[Row],
    *,
    actor: Actor = Actor.SOMEBODY,
    how: say.Part = "",
    until: int | None = None,
) -> list[Event]:
    """One line per DAY of files put in a shelf or a Photo Set that the record did not say, from
    the rows of `_SHELF_ADDITIONS` / `_SET_ADDITIONS`. Rows oldest first.

    Each file is named and linked, five and then "and N more" opening in place
    (`sentences.listed`); past `_ADDITIONS_NAMED` in a day the line counts them. The actor is who
    the ROW can say, and only that: a Photo Set a task made was filled by that task, so a file that
    went in by `until` (its making, and `_FILLED_BY_THE_TASK` after) is the task's (`actor`, `how`);
    every other addition (a shelf's, a set assembled by hand, a file put in a task's set later)
    records when it happened and never who did it, so its line is passive rather than guessing
    "You".
    """
    days: dict[tuple[bool, int | None], list[Row]] = {}
    for row in rows:
        at = None if row["at"] is None else int(row["at"])
        theirs = until is not None and at is not None and at <= until
        days.setdefault((theirs, None if at is None else day_number(at)), []).append(row)
    events: list[Event] = []
    for (theirs, _day), day_rows in days.items():
        who = actor if theirs else Actor.SOMEBODY
        count = len(day_rows)
        what: Line = (
            say.listed(
                [(say.thing("asset", str(row["id"]), str(row["name"] or "")),) for row in day_rows]
            )
            if count <= _ADDITIONS_NAMED
            else say.said(files(count))
        )
        moments = [int(row["at"]) for row in day_rows if row["at"] is not None]
        # A COUNTED day opens to its files under the line, as a day of downloads does
        # (`history._downloads_by_day`): the newest `_ADDITIONS_LISTED` of them, saying so.
        listed = tuple(
            Link(kind="asset", id=str(row["id"]), name=str(row["name"] or ""))
            for row in reversed(day_rows[-_ADDITIONS_LISTED:])
        )
        heading = (
            files(count)
            if len(listed) == count
            else f"The newest {len(listed):,} of {files(count)}"
        )
        events.append(
            Event(
                at=max(moments) if moments else None,
                actor=who,
                actor_name=SIFT if who is Actor.SIFT else None,
                kind="added",
                pieces=say.members_added(by_of(who, None), what, count, how if theirs else ""),
                detail=(
                    (Detail(kind="asset", words=heading, links=listed),)
                    if count > _ADDITIONS_NAMED
                    else ()
                ),
            )
        )
    return events


async def _grant_events(
    database: Database, viewer: Viewer, object_type: str, object_id: str
) -> list[Event]:
    """Who this was shared with or kept from. ADMIN ONLY: the caller decides, never this.

    Said the way the file's history says it, because it is the same fact about the same table.
    THE ACTOR IS WHO MADE THE SHARE, as the ledger recorded it, and SOMEBODY where nothing did
    (`history.share_makers`), never the recipient in the "by" position.
    """
    granted = list(await database.fetch_all(_GRANTS, (object_type, object_id)))
    makers = await share_makers(database, viewer, object_type, object_id, granted)
    return [
        Event(
            at=int(row["created_at"]),
            actor=maker,
            actor_name=maker_name,
            kind="shared",
            pieces=say.shared_with(
                by_of(maker, maker_name),
                str(row["username"]),
                share=str(row["effect"]) == "share",
                here="it",
            ),
        )
        for row, (maker, maker_name) in zip(granted, makers, strict=True)
    ]


async def _who_made(
    database: Database, viewer: Viewer, row: Row, *, here: set[str]
) -> tuple[Actor, str | None]:
    """The maker of one row, with the box's name looked up only when the row names a box."""
    box = row["created_by_box_id"]
    name = None
    if box is not None and "stash_boxes" in here:
        found = await database.fetch_one(MADE_BY_BOX, (str(box),))
        name = None if found is None else str(found["name"])
    return maker_of(row, viewer, name)


def _made_event(
    kind: str,
    subject_id: str,
    name: str,
    at: int | None,
    actor: Actor,
    how: say.Part = "",
    actor_name: str | None = None,
    *,
    created: bool = False,
    via: str | None = None,
    act: str | None = None,
) -> Event:
    """The arrival, with the thing itself linked.

    The subject of its own history carries a link to itself, for the reason the person's does: this
    read does not know which screen is drawing it, and a sentence that names something should carry
    the way to it wherever it is read. `via` and `act` are the pass that made it and the act that
    pass took, where the mark should say them (`Event.via`, `Event.how`).
    """
    made = say.entity_created if created else say.entity_added
    return Event(
        at=at,
        actor=actor,
        actor_name=actor_name or (SIFT if actor is Actor.SIFT else None),
        kind="added",
        pieces=made(by_of(actor, actor_name), say.thing(kind, subject_id, name), how),
        via=via,
        how=act,
    )


async def _ledger_events(
    database: Database,
    viewer: Viewer,
    kind: str,
    entity_id: str,
    kept: int,
    linked: Sequence[Event] = (),
    *,
    name_now: str | None = None,
) -> list[Event]:
    """What the ledger recorded about one of these four things, said in this thread's voice.

    A rename, an edit, a delete of something it was linked to, a refusal to let it be sent outside
    the machine, a share taken back: acts that overwrite a column or remove a row, and which this
    page therefore could not say had ever happened. The rule that decides which of them are drawn
    is `history.ledger_events` and is shared with the other two histories, because it is the same
    rule (see that function).

    The grants word is passed for all four, which is what stops a standing share being drawn twice:
    `_grant_events` below already draws it from the row while the row is there.

    `linked` is the link table's box lines, one per box and each its LATEST run. They are drawn
    HERE, beside the ledger, because the two are one source for one act: a box line absorbs the
    record writers' nameless `enriched` event (`history._drawn_elsewhere`), and a press the ledger
    draws on its own line takes the place of that box's table line (`history.runs_not_drawn`).
    Answered as the lines to draw, both halves.

    `name_now` is given by a Site's thread, the one of the four a merge folds into: see
    `history._taken_as`.
    """
    ledger = await ledger_events(
        database,
        viewer,
        here=VANTAGE_ENTITY,
        kind=kind,
        subject_id=entity_id,
        grants_as=kind,
        limit=kept,
        box_said=bool(linked),
        name_now=name_now,
    )
    return [*runs_not_drawn(linked, ledger), *ledger]


async def _decided_events(
    database: Database,
    viewer: Viewer,
    kind: str,
    entity_id: str,
    here: set[str],
    final_queues: Sequence[str],
    bench: Workbench | None = None,
) -> list[Event]:
    """The receipts a queue wrote about one of these four things, with their Undo and reversal.

    Modelled on the person thread's, and sharing its body deliberately: `_decision_events` is
    reached past the underscore because a bulk judgement is ONE act and a tag's thread and a file's
    thread are two views of it: the same title, the same Undo, the same reversal line underneath,
    the same rule about which user may be named. Written again here they would be two usernames
    of one thing, and the way that fails is silent.

    Both tables are checked because the statement names both: a query naming a missing table is a
    hard error rather than an empty answer, and a library whose workbench has never been registered
    genuinely has neither.
    """
    if not {"workbench_decisions", "workbench_decision_subjects"} <= here:
        return []
    decided = list(
        await database.fetch_all(
            _DECIDED,
            {**verdict_of(viewer), "kind": kind, "subject": entity_id, "ledger": LEDGER_QUEUE},
        )
    )
    # Read BEFORE the actors are resolved, so a decision's user is one of the names asked for in
    # the single lookup rather than a second one: the file's history's own arrangement.
    who = _Who(
        viewer=viewer,
        names=await _names_of(
            database, [str(row["user_id"]) for row in decided if row["user_id"] is not None]
        ),
    )
    # "here" in a saved title is the FILE, and this page is not it (see `files_of_decisions`).
    # Every decision is worded the one way the feed and the decision record word it, this thing said
    # as "it" (`worded.decided_said`); a row its area cannot word keeps that stored title.
    said = await decided_said(
        database, bench, viewer, decided, here=(kind, entity_id, say.HERE[say.VANTAGE_ENTITY])
    )
    return _decision_events(
        decided,
        who,
        final=frozenset(final_queues),
        files=await files_of_decisions(database, decided),
        lines={key: line.pieces for key, line in said.items()},
        under=lines_under(said),
    )


def _ordered(events: list[Event], kept: int) -> list[Event]:
    """The four entity threads below, in the one order every history uses (`history.ordered`),
    capped to the NEWEST so a long thread loses its beginning rather than its end.

    A stash-box answer kept and the receipt of that press are made one line first, before the cap
    counts them (`one_line_per_kept`).
    """
    events = ordered(one_line_per_kept(events))
    return events[-kept:]


def _bounded(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


async def _here(database: Database) -> set[str]:
    present = {str(row["name"]) for row in await database.fetch_all(_TABLES)}
    return {name for name in _FEATURE_TABLES if name in present}


async def history_of_tag(
    database: Database,
    viewer: Viewer,
    tag_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
) -> list[Event]:
    """Everything that happened to one tag, oldest first.

    UNSCOPED, and safe because of where it is called: the route resolves the tag through the scoped
    lookup every other by-id route on it uses, so a viewer who may not be shown it never reaches
    this. A second scoping rule written here would be a second place to get it wrong.

    The viewer is read for ONE thing, which is the sharing: who else signs in to this install
    is not something a tag a guest may see should disclose. That is the same half the file's history
    withholds, and it is the only half here that is about users rather than about the tag.
    """
    kept = _bounded(limit)
    here = await _here(database)
    tag = await database.fetch_one(_TAG, (tag_id,))
    if tag is None:
        return []

    # Who made the tag is asked of the row: v41 of the catalog records whether somebody typed the
    # name or a pass wrote it. SOMEBODY is the answer for every tag made before that.
    actor, actor_name = await _who_made(database, viewer, tag, here=here)
    # A tag Sift made for a copy names the act that made the copy and wears that act's mark: the
    # pass (`via`) and the act (`how`), which the client draws as the act's own verb glyph.
    made_a_copy = actor is Actor.SIFT and tag["created_by_via"] == "produced"
    events = [
        _made_event(
            "tag",
            tag_id,
            str(tag["name"]),
            int(tag["created_at"]),
            actor,
            say.from_maker(tag["created_by_via"], tag["created_by_act"])
            if actor is Actor.SIFT
            else "",
            actor_name,
            via="produced" if made_a_copy else None,
            act=tag["created_by_act"] if made_a_copy else None,
        )
    ]
    # The receipts first, so a tagging a card on this page already says leaves the count (the one
    # fold, `history_folds.counted_apart_from_receipts`).
    decided = await _decided_events(database, viewer, "tag", tag_id, here, final_queues, bench)
    events.extend(
        _counted_events(
            counted_apart_from_receipts(
                await database.fetch_all(
                    _tag_files(here),
                    {**seen_by(viewer), "subject": tag_id, "ledger": LEDGER_QUEUE},
                ),
                drawn_receipts(decided),
            ),
            "tagged",
            _tagged_sentence,
            subject=tag_id,
            parameter="tagged",
        )
    )
    linked = (
        await thing_linked(database, Subject.TAG, tag_id)
        if {"tag_stash_box_links", "stash_boxes"} <= here
        else []
    )
    if {"stash_box_kept", "stash_boxes"} <= here:
        events.extend(await kept_events(database, "tag", tag_id))
    if viewer.is_admin:
        events.extend(await _grant_events(database, viewer, "tag", tag_id))
    events.extend(decided)
    events.extend(await _ledger_events(database, viewer, "tag", tag_id, kept, linked))
    return _ordered(events, kept)


def _receipts_in(here: set[str]) -> bool:
    """Whether the record of decisions is here, so a counted row can name its receipt."""
    return {"workbench_decisions", "workbench_decision_subjects"} <= here


def _tag_files(here: set[str]) -> str:
    """Which of the four tag-files statements this process can run: with the box's name where the
    stash-box tables are here, and each row's receipt where the record of decisions is."""
    if stash_box_tables_in(here):
        return _TAG_FILES if _receipts_in(here) else _TAG_FILES_NO_LEDGER
    return _TAG_FILES_NO_BOX if _receipts_in(here) else _TAG_FILES_NO_BOX_NO_LEDGER


def _site_files(here: set[str]) -> str:
    """Which of the four Site-files statements this process can run: with the box's name where the
    stash-box tables are here, and each row's receipt where the record of decisions is."""
    if stash_box_tables_in(here):
        return _SITE_FILES if _receipts_in(here) else _SITE_FILES_NO_LEDGER
    return _SITE_FILES_NO_BOX if _receipts_in(here) else _SITE_FILES_NO_BOX_NO_LEDGER


async def history_of_site(
    database: Database,
    viewer: Viewer,
    site_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
) -> list[Event]:
    """Everything that happened to one site, oldest first.

    The arrival event is drawn at its moment, and without one ("before this was recorded") where the
    row says who made it but not when: `created_at` (catalog v41) is nullable on this one table,
    because an older row's value was read off the earliest signal the row had and some rows had
    none. A site with nothing recorded still has an empty history, which is what makes it different
    from a site that is not there at all: the route turns the second into a 404.
    """
    kept = _bounded(limit)
    here = await _here(database)
    site = await database.fetch_one(_SITE, (site_id,))
    if site is None:
        return []

    events: list[Event] = []
    if site["created_at"] is not None or site["created_by_kind"] is not None:
        actor, actor_name = await _who_made(database, viewer, site, here=here)
        events.append(
            _made_event(
                "site",
                site_id,
                str(site["name"]),
                None if site["created_at"] is None else int(site["created_at"]),
                actor,
                say.from_pass(site["created_by_via"]) if actor is Actor.SIFT else "",
                actor_name,
            )
        )
    events.extend(
        _username_events(
            await database.fetch_all(
                _SITE_USERNAMES,
                {
                    "viewer": viewer.id,
                    "reveal_named": 1 if viewer.show_hidden else 0,
                    "subject": site_id,
                    "admin": 1 if viewer.is_admin else 0,
                },
            ),
            (
                await database.fetch_all(_ARRIVALS, (site_id,))
                if {"workbench_decisions", "workbench_decision_subjects"} <= here
                else []
            ),
            viewer,
        )
    )
    decided = await _decided_events(database, viewer, "site", site_id, here, final_queues, bench)
    events.extend(
        _counted_events(
            counted_apart_from_receipts(
                await database.fetch_all(
                    _site_files(here),
                    {**seen_by(viewer), "subject": site_id, "ledger": LEDGER_QUEUE},
                ),
                drawn_receipts(decided),
            ),
            "filed",
            _filed_sentence,
            subject=site_id,
            parameter="filed",
        )
    )
    linked = (
        await thing_linked(database, Subject.SITE, site_id)
        if {"site_stash_box_links", "stash_boxes"} <= here
        else []
    )
    if {"stash_box_kept", "stash_boxes"} <= here:
        events.extend(await kept_events(database, "site", site_id))
    if viewer.is_admin:
        events.extend(await _grant_events(database, viewer, "site", site_id))
    events.extend(decided)
    events.extend(
        await _ledger_events(
            database, viewer, "site", site_id, kept, linked, name_now=str(site["name"])
        )
    )
    return _ordered(events, kept)


async def history_of_collection(
    database: Database,
    viewer: Viewer,
    collection_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
) -> list[Event]:
    """Everything that happened to one shelf, oldest first.

    Every addition is drawn: the ledger's, with who did it, and the rest off the row
    (`_SHELF_ADDITIONS`, reading `collection_items.added_at` from catalog v47). Beside them, when
    the shelf was made and who has been given it.
    """
    kept = _bounded(limit)
    here = await _here(database)
    shelf = await database.fetch_one(_COLLECTION, (collection_id,))
    if shelf is None:
        return []

    # The user who made the shelf is named only to an admin, and not by reading `_Who`: a shelf's
    # owner is one user id on one row, so the whole of that rule here is "a user who is not you is
    # named only to an admin", and a guest is told a shelf was made without being told by whom.
    owner = None if shelf["owner_id"] is None else str(shelf["owner_id"])
    mine = owner is not None and owner == viewer.id
    events = [
        _made_event(
            "collection",
            collection_id,
            str(shelf["name"]),
            int(shelf["created_at"]),
            Actor.YOU if mine else Actor.SOMEBODY,
            created=True,
        )
    ]
    if "workbench_decisions" in here:
        events.extend(
            _addition_events(
                await database.fetch_all(
                    _SHELF_ADDITIONS, {**seen_by(viewer), "subject": collection_id}
                ),
            )
        )
    if viewer.is_admin:
        events.extend(await _grant_events(database, viewer, "collection", collection_id))
    events.extend(
        await _decided_events(
            database, viewer, "collection", collection_id, here, final_queues, bench
        )
    )
    events.extend(await _ledger_events(database, viewer, "collection", collection_id, kept))
    return _ordered(events, kept)


async def history_of_photo_set(
    database: Database,
    viewer: Viewer,
    photo_set_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
    access: Repository | None = None,
) -> list[Event]:
    """Everything that happened to one Photo Set, oldest first.

    Every file added, as a shelf's (`_SET_ADDITIONS`), with one thing a shelf has not got:
    `origin` says HOW the set came to be, and a set Sift assembled from a folder and one somebody
    put together by hand are different enough that the first line should say which, and that the
    files the task put in it say it too.
    """
    kept = _bounded(limit)
    here = await _here(database)
    found = await database.fetch_one(_PHOTO_SET, (photo_set_id,))
    if found is None:
        return []

    actor, worded = _ORIGINS.get(str(found["origin"]), (Actor.SOMEBODY, ""))
    how: say.Part = worded
    if found["origin"] == "folder" and access is not None:
        how = await _from_the_folder(access, viewer, found) or worded
    events = [
        _made_event(
            "photo_set",
            photo_set_id,
            str(found["name"]),
            int(found["created_at"]),
            actor,
            how,
            created=True,
        )
    ]
    if "workbench_decisions" in here:
        events.extend(
            _addition_events(
                await database.fetch_all(
                    _SET_ADDITIONS, {**seen_by(viewer), "subject": photo_set_id}
                ),
                actor=actor,
                how=how,
                until=(
                    None
                    if actor is not Actor.SIFT
                    else int(found["created_at"]) + _FILLED_BY_THE_TASK
                ),
            )
        )
    if viewer.is_admin:
        events.extend(await _grant_events(database, viewer, "photo_set", photo_set_id))
    events.extend(
        await _decided_events(
            database, viewer, "photo_set", photo_set_id, here, final_queues, bench
        )
    )
    events.extend(await _ledger_events(database, viewer, "photo_set", photo_set_id, kept))
    return _ordered(events, kept)


def _song_events(rows: Sequence[Row]) -> list[Event]:
    """One line per DAY, SOURCE and file a name came from, of the files a song was named on, from
    the rows of `_SONG_ADDITIONS` (oldest first). Each file is named and linked, five and then "and
    N more" in place; past `_ADDITIONS_NAMED` the line counts them and opens to the newest of them,
    as a shelf's additions do (`_addition_events`). Sift's three sources are Sift's acts; a person's
    hand records nobody and is said without an actor.
    """
    groups: dict[tuple[str | None, str | None, int | None], list[Row]] = {}
    for row in rows:
        at = None if row["at"] is None else int(row["at"])
        source = None if row["source"] is None else str(row["source"])
        origin = None if row["origin"] is None else str(row["origin"])
        groups.setdefault((source, origin, None if at is None else day_number(at)), []).append(row)
    events: list[Event] = []
    for (source, origin, _day), day_rows in groups.items():
        count = len(day_rows)
        what: Line = (
            say.listed(
                [(say.thing("asset", str(row["id"]), str(row["name"] or "")),) for row in day_rows]
            )
            if count <= _ADDITIONS_NAMED
            else say.said(files(count))
        )
        came_from = (
            None
            if origin is None
            else say.thing("asset", origin, str(day_rows[0]["origin_name"] or ""))
        )
        sift = source is not None
        moments = [int(row["at"]) for row in day_rows if row["at"] is not None]
        listed = tuple(
            Link(kind="asset", id=str(row["id"]), name=str(row["name"] or ""))
            for row in reversed(day_rows[-_ADDITIONS_LISTED:])
        )
        heading = (
            files(count)
            if len(listed) == count
            else f"The newest {len(listed):,} of {files(count)}"
        )
        events.append(
            Event(
                at=max(moments) if moments else None,
                actor=Actor.SIFT if sift else Actor.SOMEBODY,
                actor_name=SIFT if sift else None,
                kind="added",
                pieces=say.song_named_here(SIFT if sift else None, what, count, source, came_from),
                detail=(
                    (Detail(kind="asset", words=heading, links=listed),)
                    if count > _ADDITIONS_NAMED
                    else ()
                ),
            )
        )
    return events


#: Who made a song, as its own row says: Sift and the task, the user asking, or somebody. The
#: task's phrase is the one every Created by line reads (`sentences.from_pass`).
def _song_maker(viewer: Viewer, row: Row) -> tuple[Actor, str]:
    kind = row["created_by_kind"]
    if kind == "sift":
        return Actor.SIFT, say.from_pass(row["created_by_via"])
    if kind == "user" and row["created_by_user_id"] == viewer.id:
        return Actor.YOU, ""
    return Actor.SOMEBODY, ""


async def history_of_song(
    database: Database,
    viewer: Viewer,
    song_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
) -> list[Event]:
    """Everything that happened to one song, oldest first.

    When it was made and by what (Sift from AcoustID or a download, or somebody), every file it was
    named on, by day and by where the name came from (`_SONG_ADDITIONS`), and what the ledger
    recorded about the song itself: a rename, a note, a cover, a merge, files put on it or taken off
    it from its page. UNSCOPED like every entity thread; the route resolves the song first.
    """
    kept = _bounded(limit)
    here = await _here(database)
    found = await database.fetch_one(_SONG, (song_id,))
    if found is None:
        return []
    actor, how = _song_maker(viewer, found)
    events = [
        _made_event(
            "song",
            song_id,
            str(found["name"]),
            int(found["created_at"]),
            actor,
            how,
            created=True,
            via=None if found["created_by_via"] is None else str(found["created_by_via"]),
        )
    ]
    if "workbench_decisions" in here:
        events.extend(
            _song_events(
                await database.fetch_all(_SONG_ADDITIONS, {**seen_by(viewer), "subject": song_id})
            )
        )
    events.extend(
        await _decided_events(database, viewer, "song", song_id, here, final_queues, bench)
    )
    events.extend(await _ledger_events(database, viewer, "song", song_id, kept))
    return _ordered(events, kept)


#: What every one of the four reads above looks like from outside.
#:
#: Positional-only up to the cap, deliberately: each of them names its own subject (`tag_id`,
#: `site_id`), and a protocol that named one would fit exactly one of the four.
class _ReadsAHistory(Protocol):
    async def __call__(
        self, database: Database, viewer: Viewer, entity_id: str, /, *, limit: int
    ) -> list[Event]: ...


#: Which read answers for which kind of entity, in the server's own word for it.
#:
#: A mapping rather than a chain of `elif`s, for the reason this module's `_ORIGINS` is one and the
#: related route's `walls` is: a kind added above and forgotten here fails LOUDLY at the caller
#: instead of quietly counting nothing. A silent nought on a tab is the exact state the counts
#: endpoint exists to end.
_HISTORIES: dict[str, _ReadsAHistory] = {
    "tag": history_of_tag,
    "site": history_of_site,
    "collection": history_of_collection,
    "photo_set": history_of_photo_set,
    "song": history_of_song,
}


async def history_count_of_entity(
    database: Database, viewer: Viewer, kind: str, entity_id: str, *, limit: int = DEFAULT_LIMIT
) -> int:
    """How many lines one entity's thread has, for the number beside the word History.

    ## Why this is the history itself and not a COUNT statement

    The same reason `history_count_of_person` gives next door, and it is the rule the related-counts
    route is built around: a number beside a list must come from the read that draws the list. A
    history is not a table (it is several statements GROUPED by day and by what decided them,
    capped, and sorted), so there is no statement that counts what it comes to. Anything simpler
    would count ROWS where the thread counts days, and be wrong by however many files were tagged in
    one afternoon.

    So this asks the same question at the same cap and measures the answer. The two cannot differ.

    An unknown kind raises rather than answering nought, for the reason the route refuses one: a
    strip drawn from a silent nothing is indistinguishable from a tab with nothing behind it.

    UNSCOPED, like the four reads it calls. The caller resolves the subject first (see
    `history_of_tag`, which is where that rule and its reason live).
    """
    read = _HISTORIES.get(kind)
    if read is None:
        raise KeyError(f"nothing knows the history of a {kind!r}")
    return len(await read(database, viewer, entity_id, limit=limit))
