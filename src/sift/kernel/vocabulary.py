# SPDX-License-Identifier: AGPL-3.0-or-later
"""The closed word lists the record is written in, in a leaf so any kernel module can use them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The passes that invent rows: the `source` words of the filings, plus `download` and `mirror`.
VIA_DOWNLOAD = "download"
VIA_FOLDER = "folder"
VIA_FILENAME = "filename"
VIA_MIRROR = "mirror"
VIA_FACES = "faces"
VIA_STASH = "stash"
VIA_WATERMARK = "watermark"
VIA_PRODUCED = "produced"
VIA_ARCHIVE = "archive"
#: Pictures' own metadata naming a site's account number; `enriched:metadata` finds them.
VIA_METADATA = "metadata"
VIA_SHOOT = "shoot"
#: A swap with another install; its payload holds the other device's id, never an address.
VIA_SWAP = "swap"
#: A Stash library imported: not `stash`, since no stash-box said any of it.
VIA_STASH_LIBRARY = "stash_library"
#: Stash rows attached to nothing, marked so the walls can list them for cleanup.
VIA_STASH_UNATTACHED = "stash_unattached"
VIA_MUSIC_LOOKUP = "music_lookup"
#: A person made from imported pictures that faces here matched; not `faces`.
VIA_FACIAL_FINGERPRINTS = "facial_fingerprints"

#: Closed, so adding a pass is deliberate; the schema gate pins the column, this its words.
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

#: Which verb made a `produced` row, so its Created by line wears that verb's glyph.
ACT_COMPRESS = "compress"
ACT_EDIT = "edit"

MADE_ACTS = (ACT_COMPRESS, ACT_EDIT)


#: Tasks that act without making a row; each needs a sentence in `sentences.SIFT_FROM`.
VIA_FINGERPRINT = "fingerprint"
#: Shared by the writer and the wording of a `scanned` event with every fingerprint empty.
FINGERPRINTS_EMPTY = "empty"
DEPARTURES_KEPT = "departures_kept"
BOXES_RECORDED = "boxes_recorded"
VIA_PHOTO_SET_FLOOR = "photo_set_floor"
#: A one-time step changing a setting a person set writes `edited`, so it can be put back.
VIA_UPDATE = "update"
UPDATE_TO = "update_to"
VIA_BACKUP = "backup"

VIA_BENCHMARK = "benchmark"

VIA_INSIGHTS = "insights"

TASK_VIAS = (
    VIA_FINGERPRINT,
    VIA_PHOTO_SET_FLOOR,
    VIA_UPDATE,
    VIA_BACKUP,
    VIA_BENCHMARK,
    VIA_INSIGHTS,
)

SIFT_ACTS_BY = MADE_VIAS + TASK_VIAS


#: The `queue` of an event no card can take back; a word, not NULL, so it partitions cleanly.
LEDGER_QUEUE = "ledger"

#: Payload key for the answers a receipt kept over a stash-box's, so History draws them.
RECEIPT_KEPT = "kept_answers"
KEPT_BY_THE_PRESS = "kept"
KEPT_BY_TAKING_ANOTHER = "set_aside"

#: Payload key for the faces a receipt answered, by file, since a rescan replaces tracks.
RECEIPT_FACES = "faces_answered"
FACE_SAID_YES = "confirmed"
FACE_SAID_NO = "refused"

#: Every kind an event may name; the door alone enforces it, as the column is plain TEXT.
SubjectKind = Literal[
    "asset",
    "person",
    "folder",
    "pile",
    # Older rows' `account` is rewritten by the workbench's `_ACCOUNTS_ARE_USERNAMES`.
    "username",
    "tag",
    "site",
    "collection",
    "photo_set",
    "song",
    "shoot",
    "box",
    "grant",
    "setting",
    "download",
    # A sign-in; not `username`, so one word never names two tables.
    "login",
    "run",
    "swap",
    "backup",
    "database_file",
    "computer",
    "saved_filter",
    "recap",
]


@dataclass(frozen=True, slots=True)
class Subject:
    """One thing a decision was about, kept beside the payload so anything may read it."""

    kind: SubjectKind
    id: str
    name: str | None = None
