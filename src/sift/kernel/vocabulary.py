# SPDX-License-Identifier: AGPL-3.0-or-later
"""The closed word lists the record is written in, and nothing else.

A leaf that imports nothing from Sift, which is why it exists: the ledger's door validates every
event against these lists (`MADE_VIAS`, `SubjectKind`), and any module in the kernel must be able
to call that door at module level, so the words sit underneath everything that reads them.
`kernel/access/catalog` and `kernel/workbench` re-offer the same names for their many call sites;
this is where they are decided.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# --- WHICH PASS OF SIFT'S OWN --------------------------------------------------------------------
#
#: The passes that invent rows, one constant each so a caller names a pass rather than a string:
#: the words the `source` columns of the filings carry, plus `download` (a pasted address) and
#: `mirror` (a folder naming one username on a re-hosting site), which create without filing.
#: A stash-box is `stash`, one word per thing, as the screens draw it.
VIA_DOWNLOAD = "download"
VIA_FOLDER = "folder"
VIA_FILENAME = "filename"
VIA_MIRROR = "mirror"
VIA_FACES = "faces"
VIA_STASH = "stash"
VIA_WATERMARK = "watermark"
VIA_PRODUCED = "produced"
VIA_ARCHIVE = "archive"
#: The pass that reads pictures' own metadata (`Artist`, `ImageDescription`) to learn what a site's
#: account number is called: its own word because the file name alone could not have filed it, and
#: `enriched:metadata` finds those files again.
VIA_METADATA = "metadata"
#: The pass that groups one sitting's pictures into a Photo Set (`slices/shoots`), which it makes.
VIA_SHOOT = "shoot"
#: A swap with another install (`slices/swap`): it makes the People, Sites and Usernames a file
#: arrived under where this library had none, and is the actor of that file's `added`, with the
#: other device's id and the session in the payload, never an address.
VIA_SWAP = "swap"
#: A Stash library imported (`slices/stash_migration`): its own word, not `stash`, because the
#: library is the person's own record and no stash-box said any of it.
VIA_STASH_LIBRARY = "stash_library"
#: The same, for a person, Site or tag Stash attached to nothing: marked apart so the walls can list
#: exactly those (`created=stash_unattached`) to look over once and delete where they are clutter.
VIA_STASH_UNATTACHED = "stash_unattached"
#: The online song lookup (`slices/music/lookup.py`): it makes a song row for a new recording.
VIA_MUSIC_LOOKUP = "music_lookup"
#: A person Sift made from imported pictures (a facial fingerprints file or a folder of somebody)
#: that faces in the library matched: not `faces`, because no face of the library was named.
VIA_FACIAL_FINGERPRINTS = "facial_fingerprints"

#: Every word a `created_by_via` may hold, closed so adding a pass is a deliberate act with a
#: sentence written for it. `tests/gates/schema_shape.json` pins the column; this pins its words.
MADE_VIAS = (
    VIA_DOWNLOAD,
    VIA_FOLDER,
    VIA_FILENAME,
    VIA_MIRROR,
    VIA_FACES,
    VIA_STASH,
    VIA_WATERMARK,
    VIA_PRODUCED,
    VIA_ARCHIVE,
    VIA_METADATA,
    VIA_SHOOT,
    VIA_SWAP,
    VIA_STASH_LIBRARY,
    VIA_STASH_UNATTACHED,
    VIA_MUSIC_LOOKUP,
    VIA_FACIAL_FINGERPRINTS,
)

#: Which verb made a row the `produced` pass made (`tags.created_by_act`), so its Created by line
#: wears that verb's glyph: Compress, or the editor's Modify, which every editor operation shares.
ACT_COMPRESS = "compress"
ACT_EDIT = "edit"

#: Every word a `created_by_act` may hold. Closed for the reason `MADE_VIAS` is.
MADE_ACTS = (ACT_COMPRESS, ACT_EDIT)


# --- WHICH TASK OF SIFT'S OWN TOOK AN ACT -----------------------------------------------------------
#
#: The tasks that act on the library WITHOUT making a row. A word from `MADE_VIAS` would give a file
#: a false provenance, but which task acted is still owed to the record, so the ledger's door takes
#: these beside `MADE_VIAS` for a Sift actor. Closed for the same reason: each needs a sentence
#: (`sentences.SIFT_FROM`), and until one is written the reader says "Sift".
#:
#: The pass that reads every file's fingerprints (the Fingerprint family). It writes `scanned`.
VIA_FINGERPRINT = "fingerprint"
#: The word a `scanned` event's payload carries when every fingerprint came back empty, shared by
#: the writer (`content.identity.record_fingerprints`) and the wording (`access.sentences`).
FINGERPRINTS_EMPTY = "empty"
#: The key the count of kept departures is written under by Insights' one-time step
#: (`slices/insights/schema.keep_what_is_still_known`), read by `sentences.departures_kept`.
DEPARTURES_KEPT = "departures_kept"
#: The key `slices/stash_boxes/filed_by.backfill` writes its count of files given their box under,
#: read by `sentences.boxes_recorded`.
BOXES_RECORDED = "boxes_recorded"
#: The one pass that takes a raised Photo Set floor to sets already made
#: (`slices/photo_sets/jobs.dissolve_under_floor`). It writes `deleted`, with the floor it applied.
VIA_PHOTO_SET_FLOOR = "photo_set_floor"
#: Sift updating itself: a one-time step that changes something a person SET writes `edited` with
#: the value before and after, so the change shows on History and can be put back.
VIA_UPDATE = "update"
#: The release a changed default's line names its update by (`settings_hub.defaults`).
UPDATE_TO = "update_to"
#: Backup and restore (`slices/backup`): the scheduled backup's deletions of its oldest files, a
#: restore by somebody the restored library has no row for, and a library imported on the Database
#: Switcher (`libraries.record_origin`), since whoever pressed Import has no row there yet.
VIA_BACKUP = "backup"

#: The benchmark of this device (`slices/performance/benchmark.py`) run by itself on the first
#: library folder: one `edited` receipt on the settings it chose, whose Undo puts them back.
VIA_BENCHMARK = "benchmark"

TASK_VIAS = (
    VIA_FINGERPRINT,
    VIA_PHOTO_SET_FLOOR,
    VIA_UPDATE,
    VIA_BACKUP,
    VIA_BENCHMARK,
)

#: Every word a Sift actor's id may be on a ledger event: a pass that makes rows, or a task that
#: acts without making one. See `kernel/ledger.Actor`.
SIFT_ACTS_BY = MADE_VIAS + TASK_VIAS


# --- WHAT AN EVENT CAN BE ABOUT -------------------------------------------------------------------

#: The `queue` of an event not taken on a queue: most of the record is acts (a grant revoked, a
#: title edited) that no card can take back. `workbench.RESERVED_NAMES` keeps any queue from
#: claiming it, so undo is refused with the sentence it has for that. A word rather than NULL,
#: because the record's store partitions by `queue`, and a word groups predictably.
LEDGER_QUEUE = "ledger"

#: The answers a receipt kept over a stash-box's, under this payload key: a list of `{"subject",
#: "local_id", "box_id", "key", "how"}`, each the key of one `stash_box_kept` row. Declared here,
#: as `history._RECEIPT_LINK` is, so the History threads can draw the press once beside its kept
#: row without learning the queue's payload. A receipt without it kept nothing.
RECEIPT_KEPT = "kept_answers"
#: `how` on a `RECEIPT_KEPT` entry: the press kept this answer. See `RECEIPT_KEPT`.
KEPT_BY_THE_PRESS = "kept"
#: `how` on a `RECEIPT_KEPT` entry: the press took another box's answer and set this one aside.
KEPT_BY_TAKING_ANOTHER = "set_aside"

#: The faces a receipt answered for, under this payload key: a list of `{"person_id", "asset_id",
#: "how"}`, one per face. Declared here for the reason `RECEIPT_KEPT` is, so a Yes or No is said
#: once on History (`history.one_line_per_face_answer`). By file, not track, because a rescan
#: replaces the tracks and puts each answer back on the new one.
RECEIPT_FACES = "faces_answered"
#: `how` on a `RECEIPT_FACES` entry: the face was confirmed as the person.
FACE_SAID_YES = "confirmed"
#: `how` on a `RECEIPT_FACES` entry: the face was refused as the person.
FACE_SAID_NO = "refused"

#: Every kind of thing an event may name, one closed list every reader learns at once. A kind with
#: no page is still a true subject: the Settings feed draws events naming anything.
SubjectKind = Literal[
    "asset",
    "person",
    "folder",
    "pile",
    # One person's name on one site, the thing files are filed under (older rows' `account` is
    # rewritten by the workbench's `_ACCOUNTS_ARE_USERNAMES`).
    "username",
    "tag",
    "site",
    "collection",
    "photo_set",
    # One piece of music and the files that carry it (`kernel/content/songs.py`).
    "song",
    "shoot",
    "box",
    "grant",
    "setting",
    # A row on the Downloads queue: a download that never landed made no file, so this is all an
    # event about it can name. Its page is that row picked out (`_ADDRESS` in the ledger's router).
    "download",
    # A sign-in (a User, on screen). Not `username`: one word naming two tables would resolve a
    # sharing event's user against usernames (see `_LOGINS_WRITTEN_AS_ACCOUNTS`).
    "login",
    # One finished pass over the library (`work_runs`), the only thing a `ran` event can name; no
    # page. The kind columns are plain TEXT with no CHECK: the door alone enforces this list.
    "run",
    # One swap with another Sift (`swap_sessions`); lines name the OTHER device from the payload.
    "swap",
    # One backup file, restored or deleted by the kept count; its id is the file's name. No page.
    "backup",
    # One database file a library was made from on the Database Switcher (`adopted`). No page.
    "database_file",
    # The computer running Sift, changed from a window (`kernel/machine_acts.py`); named by the
    # name the machine gives itself. No page.
    "computer",
]


@dataclass(frozen=True, slots=True)
class Subject:
    """One thing a decision was about, as the kind of thing it is and its id.

    Beside `payload`, not inside it: the payload says how to put a decision back and only its own
    area reads it; subjects say what it was about and anything may read them. The id is never
    resolved here, so a decision outlives what it names, and `name` is what the row was called at
    that moment: the difference between "a person was removed" and "Ilva Brennan was removed",
    which cannot be looked up afterwards.
    """

    kind: SubjectKind
    id: str
    #: What it was called then; None where the writer had no name to hand.
    name: str | None = None
