# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's pictures and edits, semantic search, shoots, music, swap, dedup and the suggestions."""

from __future__ import annotations

from tests.gates.authz.seeds import (
    A_DEDUP_CANDIDATE,
    A_FOLDER,
    A_SUGGESTION,
    AN_ASSET,
    ANOTHER_SUGGESTION,
    DELETABLE_ASSET,
    Case,
    Policy,
)

ROWS: dict[tuple[str, str], Case] = {
    # The grid: readable by any signed-in user and scoped by the access layer. The save log is the
    # exception: who saved what is about other people.
    ("GET", "/api/assets"): Case(Policy.AUTHENTICATED),
    # Scoped in the same statement that picks the files: a count is a disclosure of its own.
    ("GET", "/api/assets/facets"): Case(Policy.AUTHENTICATED, query={"facet": "media"}),
    # The five entity walls' facets, counted over the rows the asking user may see.
    ("GET", "/api/people/facets"): Case(Policy.AUTHENTICATED, query={"facet": "gender"}),
    ("GET", "/api/sites/facets"): Case(Policy.AUTHENTICATED, query={"facet": "linked"}),
    ("GET", "/api/tags/facets"): Case(Policy.AUTHENTICATED, query={"facet": "category"}),
    ("GET", "/api/collections/facets"): Case(Policy.AUTHENTICATED, query={"facet": "mine"}),
    # A guest asking the admin-only facet is told it is unknown (422).
    ("GET", "/api/photo-sets/facets"): Case(Policy.AUTHENTICATED, query={"facet": "sharing"}),
    # Only the pickers draw it, and the pickers are admin verbs.
    ("POST", "/api/assets/memberships"): Case(Policy.ADMIN, body={"asset_ids": [AN_ASSET]}),
    # The server chooses from what THAT user may see.
    ("GET", "/api/assets/random"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/assets/{asset_id}"): Case(Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}),
    # What a file is called and where it came from are statements about the library.
    ("PUT", "/api/assets/{asset_id}"): Case(
        Policy.ADMIN, params={"asset_id": AN_ASSET}, body={"title": "A name"}
    ),
    # What was decided about a file is part of it; the sharing half is dropped inside the read.
    ("GET", "/api/assets/{asset_id}/history"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/assets/{asset_id}/thumb"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/assets/{asset_id}/preview"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # Its own handler, so its refusal is proved separately.
    ("GET", "/api/assets/{asset_id}/sprite"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # Guests are refused by a capability an admin sets inside the handler, not by role.
    ("GET", "/api/assets/{asset_id}/save-to-device"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/save-log"): Case(Policy.ADMIN),
    # Playback returns the media itself. Four handlers, each scoped inside and listed on its own.
    ("POST", "/api/assets/{asset_id}/playback"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/assets/{asset_id}/stream"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # A copy of a picture is not a lesser secret.
    ("GET", "/api/assets/{asset_id}/rendition"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # The authority to watch a thing is the authority to download it.
    ("GET", "/api/assets/{asset_id}/local-file"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/assets/{asset_id}/outgoing"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/assets/{asset_id}/hls/master.m3u8"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/assets/{asset_id}/hls/index.m3u8"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/assets/{asset_id}/hls/{segment}"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET, "segment": "0.m4s"}
    ),
    # Scoped, or accepting a view would confirm that an asset id exists.
    ("POST", "/api/assets/{asset_id}/view"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # Per user, as the heart and the stars are, and scoped through `_open`.
    ("GET", "/api/assets/{asset_id}/replays"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # Both delete modes are admin-only, refused in the service after visibility is settled, so a
    # guest naming a file they cannot see is told it is not there rather than "admins only".
    ("DELETE", "/api/assets/{asset_id}"): Case(Policy.ADMIN, params={"asset_id": DELETABLE_ASSET}),
    # A whole FOLDER, refused the same way. `A_FOLDER` is shared with whoever signs in, so a guest
    # gets the 403; an admin gets a 409, since Sift stops reading a library folder.
    ("DELETE", "/api/folders/{folder_id}"): Case(Policy.ADMIN, params={"folder_id": A_FOLDER}),
    # `require_admin` at the door: `NotAllowed` is a `DeleteRefused`, so without it the loop would
    # count a guest's refusal and reply with success. A blanket check names no file, so it leaks
    # none. Pointed at the asset the single DELETE already took, so an admin gets `refused: 1`.
    ("POST", "/api/assets/delete"): Case(
        Policy.ADMIN,
        body={"asset_ids": [DELETABLE_ASSET], "mode": "sift"},
        destructive=True,
    ),
    # What the delete sheet asks before it draws Delete from disk: a count, admin-only like both
    # deletes, and of the files THIS user may see. Writes nothing.
    ("POST", "/api/assets/delete/check"): Case(Policy.ADMIN, body={"asset_ids": [AN_ASSET]}),
    # Duplicate-finding and meaning search are admin-only: they describe the whole library and
    # name no asset the caller chose. Lookalikes are a way of browsing, handed to the ordinary read.
    ("GET", "/api/assets/{asset_id}/similar"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/semantic/status"): Case(Policy.ADMIN),
    # One boolean for the search box, never the reason behind it.
    ("GET", "/api/semantic/available"): Case(Policy.AUTHENTICATED),
    # Both counts are scoped by the statement that produces them.
    ("GET", "/api/semantic/coverage"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/semantic/models/fetch"): Case(Policy.ADMIN),
    # Answers with the feature off: clearing up is what somebody does after switching it off.
    ("DELETE", "/api/semantic/index"): Case(Policy.ADMIN),
    # Watermark reads and proposed shoots are admin-only. A proposal holding a file this user may
    # not see answers the 404 a missing one does.
    ("GET", "/api/shoots"): Case(Policy.ADMIN),
    ("GET", "/api/shoots/{proposal_id}"): Case(
        Policy.ADMIN,
        params={"proposal_id": "no-such-shoot"},
        answers_anyway=404,
    ),
    ("POST", "/api/shoots/{proposal_id}/make"): Case(
        Policy.ADMIN,
        params={"proposal_id": "no-such-shoot"},
        answers_anyway=404,
    ),
    ("POST", "/api/shoots/{proposal_id}/refuse"): Case(
        Policy.ADMIN,
        params={"proposal_id": "no-such-shoot"},
        answers_anyway=404,
    ),
    ("POST", "/api/shoots/{proposal_id}/name-the-rest"): Case(
        Policy.ADMIN,
        params={"proposal_id": "no-such-shoot"},
        answers_anyway=404,
    ),
    # A way of browsing, like the lookalikes; every file in a group is scoped.
    ("GET", "/api/assets/{asset_id}/same-music"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # The AcoustID key decides that something may leave this device. Setting it is destructive: with
    # a key set, the check would send a real request.
    ("GET", "/api/music/lookup"): Case(Policy.ADMIN),
    ("PUT", "/api/music/lookup/key"): Case(
        Policy.ADMIN, body={"key": "not-a-key"}, destructive=True
    ),
    ("DELETE", "/api/music/lookup/key"): Case(Policy.ADMIN),
    ("POST", "/api/music/lookup/check"): Case(Policy.ADMIN),
    # Off in a fresh library, so an admin is told 409 in words.
    ("POST", "/api/music/lookup/files"): Case(
        Policy.ADMIN, body={"asset_ids": [AN_ASSET]}, answers_anyway=409
    ),
    ("POST", "/api/music/lookup/again"): Case(Policy.ADMIN, answers_anyway=409),
    # A swap decides what leaves this device. Start and Join are swept with no body (422), so no
    # tunnel restarts; the device routes answer "locked" because no password was entered.
    ("POST", "/api/swap/start"): Case(Policy.ADMIN),
    ("POST", "/api/swap/join"): Case(Policy.ADMIN),
    ("GET", "/api/swap/sessions/{session_id}"): Case(
        Policy.ADMIN, params={"session_id": "no-such-swap"}, answers_anyway=404
    ),
    ("POST", "/api/swap/sessions/{session_id}/code"): Case(
        Policy.ADMIN,
        params={"session_id": "no-such-swap"},
        body={"match": True},
        answers_anyway=404,
    ),
    ("POST", "/api/swap/sessions/{session_id}/end"): Case(
        Policy.ADMIN, params={"session_id": "no-such-swap"}, answers_anyway=404
    ),
    ("GET", "/api/swap/device"): Case(Policy.ADMIN),
    ("GET", "/api/swap/keep-out/{subject}/{local_id}"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": "no-such-person"},
        answers_anyway=404,
    ),
    ("PUT", "/api/swap/keep-out/{subject}/{local_id}"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": "no-such-person"},
        body={"kept_out": True},
        answers_anyway=404,
    ),
    ("POST", "/api/swap/device/reset"): Case(Policy.ADMIN, destructive=True),
    ("POST", "/api/swap/sessions/{session_id}/take"): Case(
        Policy.ADMIN,
        params={"session_id": "no-such-swap"},
        body={"skipped": [], "unticked": []},
        answers_anyway=404,
    ),
    ("POST", "/api/swap/sessions/{session_id}/weigh"): Case(
        Policy.ADMIN,
        params={"session_id": "no-such-swap"},
        body={"skipped": [], "unticked": []},
        answers_anyway=404,
    ),
    ("POST", "/api/swap/weigh"): Case(Policy.ADMIN, body={"chosen": []}),
    ("GET", "/api/swap/tunnels"): Case(Policy.ADMIN),
    ("GET", "/api/swap/people/{person_id}/held-faces"): Case(
        Policy.ADMIN, params={"person_id": "no-such-person"}, answers_anyway=404
    ),
    ("POST", "/api/swap/people/{person_id}/held-faces"): Case(
        Policy.ADMIN, params={"person_id": "no-such-person"}, answers_anyway=404
    ),
    ("GET", "/api/watermarks/status"): Case(Policy.ADMIN),
    ("POST", "/api/watermarks/models/fetch"): Case(Policy.ADMIN),
    # Answers with the feature off, as removing the meaning index does; filings are kept.
    ("DELETE", "/api/watermarks/reads"): Case(Policy.ADMIN),
    # There is no per-file watermark route. `confirm` DELETES FILES FROM THE DISK, so both writes
    # are declared with an EMPTY page: the gate proves the door without deleting anything.
    ("GET", "/api/dedup/groups"): Case(Policy.ADMIN),
    ("GET", "/api/dedup/groups/{method}/{first}"): Case(
        Policy.ADMIN,
        params={"method": "phash", "first": A_DEDUP_CANDIDATE},
        answers_anyway=404,
    ),
    ("POST", "/api/dedup/groups/confirm"): Case(Policy.ADMIN, body={"groups": []}),
    ("POST", "/api/dedup/groups/dismiss"): Case(Policy.ADMIN, body={"groups": []}),
    # The GET says how many files know less than a twin does, a statement about the library.
    ("GET", "/api/dedup/carry"): Case(Policy.ADMIN),
    ("POST", "/api/dedup/carry/all"): Case(Policy.ADMIN),
    # Folder-tree suggestions; the service also resolves every row against the user asking.
    ("GET", "/api/suggestions"): Case(Policy.ADMIN),
    ("GET", "/api/suggestions/filed"): Case(Policy.ADMIN),
    ("GET", "/api/suggestions/filenames"): Case(Policy.ADMIN),
    ("POST", "/api/suggestions/filenames/{username_id}/undo"): Case(
        Policy.ADMIN, params={"username_id": "01ZZZZZZZZZZZZZZZZZZZZZZZZ"}
    ),
    # It WRITES: it takes a person off files and forgets a folder's answer.
    ("POST", "/api/suggestions/filed/{folder_id}/people/{person_id}/undo"): Case(
        Policy.ADMIN,
        params={
            "folder_id": "01ZZZZZZZZZZZZZZZZZZZZZZZZ",
            "person_id": "01ZZZZZZZZZZZZZZZZZZZZZZZY",
        },
    ),
    ("POST", "/api/suggestions/{claim_id}/confirm"): Case(
        Policy.ADMIN, params={"claim_id": A_SUGGESTION}
    ),
    ("POST", "/api/suggestions/{claim_id}/reject"): Case(
        Policy.ADMIN, params={"claim_id": ANOTHER_SUGGESTION}
    ),
    # It WRITES an attribution, the path a confirmation takes.
    ("POST", "/api/suggestions/folder/{folder_id}"): Case(
        Policy.ADMIN, params={"folder_id": "01ZZZZZZZZZZZZZZZZZZZZZZZZ"}
    ),
    ("GET", "/api/reclaim"): Case(Policy.ADMIN),
    # Removes one copy of an asset that has several; AN_ASSET has none, so no file moves.
    ("POST", "/api/reclaim/{asset_id}/release"): Case(Policy.ADMIN, params={"asset_id": AN_ASSET}),
    # An EMPTY page proves the door without anything being let go of.
    ("POST", "/api/reclaim/release-many"): Case(Policy.ADMIN, body={"releases": []}),
}
