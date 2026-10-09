# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the rows name: who may call a route, a row's shape, and the ids the seeded library holds."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from enum import StrEnum


class Policy(StrEnum):
    PUBLIC = "public"
    """No session needed: read by the internet."""

    AUTHENTICATED = "authenticated"
    """Any signed-in user, guest included; the route still scopes what it returns."""

    ADMIN = "admin"


#: Every id names a seeded row: a made-up id's 404 reads as a refusal for every role.
FAILED_JOB = "01HX0000000000000000000001"
CANCELABLE_JOB = "01HX0000000000000000000002"
A_FINISHED_RUN = "01HX0000000000000000000003"

A_QUARANTINED = "seeded-refusal.bin"
AN_UNMARKED_BACKUP = "sift-backup-20260720-141500-1.0.0.zip"
A_ROOT = "01HX0000000000000000000003"
A_FOLDER = "01HX0000000000000000000004"
DELETABLE_ROOT = "01HX0000000000000000000005"
A_GRANT = "01HX0000000000000000000008"
A_TAG = "01HX0000000000000000000008"
DELETABLE_FOLDER = "01HX0000000000000000000006"

AN_ASSET = "01HX0000000000000000000007"

DELETABLE_ASSET = "01HX0000000000000000000080"

A_FACE = "01HX0000000000000000000016"

#: Two guests: deleting one takes its grants, so the sharing routes name the other.
A_GUEST_USER = "01HX0000000000000000000090"
A_DELETABLE_GUEST_USER = "01HX0000000000000000000091"

#: The site has no usernames: one with some answers an admin 409, which reads as a refusal.
A_PERSON = "01HX0000000000000000000010"

#: A merge removes one of its pair, so the pair is nobody else's.
MERGE_FROM = "01HX0000000000000000000091"
MERGE_INTO = "01HX0000000000000000000092"

#: The same for sites; neither carries a login, since only one survives a merge.
SITE_MERGE_FROM = "01HX0000000000000000000095"
SITE_MERGE_INTO = "01HX0000000000000000000096"

A_LINKED_PERSON = "01HX0000000000000000000093"
AN_ALIAS = "01HX0000000000000000000011"
A_LINK = "01HX0000000000000000000019"
A_SITE = "01HX0000000000000000000012"

#: Worn by all six things with a cover: "no cover" and "not for you" are the same 404.
A_COVER_PICTURE = "01HX0000000000000000000094"
A_USERNAME = "01HX0000000000000000000013"

A_DELETABLE_SITE = "01HX0000000000000000000014"
A_DELETABLE_USERNAME = "01HX0000000000000000000015"
#: Both hold `AN_ASSET`: an empty collection answers a guest by its scope, not the policy.
A_COLLECTION = "01HX0000000000000000000016"
A_DELETABLE_COLLECTION = "01HX0000000000000000000017"

A_PHOTO_SET = "01HX0000000000000000000020"
A_DELETABLE_PHOTO_SET = "01HX0000000000000000000021"

#: Holds `AN_ASSET`, so a guest is answered by the policy, not by the song's files.
A_SONG = "01HX0000000000000000000023"
A_DELETABLE_SONG = "01HX0000000000000000000024"
A_MERGED_SONG = "01HX0000000000000000000025"

#: Seeded with no owner: refused to a guest, allowed to an admin, as the routes claim.
A_LOOP = "01HX0000000000000000000022"

A_RECAP = "01HX0000000000000000000097"

A_STASH_BOX = "01HX0000000000000000000095"

A_DELETABLE_STASH_BOX = "01HX0000000000000000000096"

#: Not seeded: an admin reaching the 404 has passed the door.
A_DEDUP_CANDIDATE = "01HX0000000000000000000091"

A_SUGGESTION = "01HX0000000000000000000093"
ANOTHER_SUGGESTION = "01HX0000000000000000000094"

#: Its location does not exist: an admin gets a 409, a guest the 403 asked about.
A_MOVE = "01HX0000000000000000000092"

A_DECISION = "01HX0000000000000000000094"

#: A finished sample encode with bytes behind it: a swept sample answers 404 too.
A_SAMPLE_JOB = "01HX0000000000000000000093"

#: Stored as `site:username`, asked for by username alone.
ART_SITE = "youtube"
ART_CREATOR = "someone"
ART_CREATOR_SCOPE = f"{ART_SITE}:{ART_CREATOR}"


#: A WebSocket route carries no `methods`, so it is declared under this one.
WEBSOCKET = "WS"


@dataclass(frozen=True)
class Case:
    policy: Policy
    #: Values for any `{placeholder}` in the path.
    params: dict[str, str] = field(default_factory=dict)
    #: Required query parameters: without them a 422 comes before the permission check.
    query: dict[str, str] = field(default_factory=dict)
    #: Called last: the sweep shares one database, and this route removes rows others name.
    destructive: bool = False
    #: The smallest body that parses, for a route whose check runs only after the body does.
    body: dict[str, object] | None = None
    #: A status an allowed caller is honestly told that would read as a refusal; never loosens denial.
    answers_anyway: int | None = None
