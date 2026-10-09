# SPDX-License-Identifier: AGPL-3.0-or-later
"""Faces: the questions, the people they name, tidying."""

from __future__ import annotations

from tests.gates.authz.seeds import (
    A_DELETABLE_COLLECTION,
    A_DELETABLE_PHOTO_SET,
    A_DELETABLE_SITE,
    A_DELETABLE_SONG,
    A_FACE,
    A_LOOP,
    A_MERGED_SONG,
    A_PERSON,
    A_SONG,
    FAILED_JOB,
    Case,
    Policy,
)

ROWS: dict[tuple[str, str], Case] = {
    # A count of unnamed people in a library is a fact about the library.
    ("GET", "/api/faces/groups"): Case(Policy.ADMIN),
    # A crop is a fragment of its file, served only to somebody who may open the file. A_FACE
    # names no appearance, so every caller gets the 404 a concealed one gets.
    ("GET", "/api/faces/{track_id}/crop"): Case(Policy.AUTHENTICATED, params={"track_id": A_FACE}),
    # Each decision resolves the person or crop first, so an admin without Hidden open gets the 404
    # a stranger would.
    ("POST", "/api/faces/{track_id}/confirm"): Case(Policy.ADMIN, params={"track_id": A_FACE}),
    ("POST", "/api/faces/{track_id}/reject"): Case(Policy.ADMIN, params={"track_id": A_FACE}),
    ("POST", "/api/faces/groups/{pile_id}/ignore"): Case(
        Policy.ADMIN, params={"pile_id": "01HX0000000000000000000009"}
    ),
    ("GET", "/api/faces/groups/{pile_id}"): Case(
        Policy.ADMIN, params={"pile_id": "01HX0000000000000000000009"}
    ),
    # `require_admin`, or it would be the one read of the review queue a guest could call.
    ("GET", "/api/faces/groups/{pile_id}/tracks"): Case(
        Policy.ADMIN,
        params={"pile_id": "01HX0000000000000000000009"},
        answers_anyway=404,
    ),
    ("GET", "/api/faces/identified/people"): Case(Policy.ADMIN),
    # Lists of people a guest may have no business knowing exist.
    ("GET", "/api/faces/to-check"): Case(Policy.ADMIN),
    # A person this user may not be told about has no rows and answers "nothing changed".
    ("GET", "/api/faces/disagreements"): Case(Policy.ADMIN),
    ("GET", "/api/faces/disagreements/{person_id}"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON}
    ),
    ("POST", "/api/faces/disagreements/{person_id}"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON}, body={"yes": False}
    ),
    ("GET", "/api/faces/identified/people/{person_id}"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON}
    ),
    # A fragment of a file, as the crop is.
    ("GET", "/api/faces/{track_id}/cover"): Case(Policy.AUTHENTICATED, params={"track_id": A_FACE}),
    # A count, about a person the caller can already see.
    ("GET", "/api/people/{person_id}/recognition"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    # Unlike the single-person count: a list covering people a guest may not know exist.
    ("GET", "/api/faces/references/strength"): Case(Policy.ADMIN),
    # A count of the library and of when it is being worked on.
    ("GET", "/api/faces/work-left"): Case(Policy.ADMIN),
    ("POST", "/api/faces/name"): Case(Policy.ADMIN),
    ("POST", "/api/faces/accept"): Case(Policy.ADMIN),
    ("POST", "/api/faces/set-aside"): Case(Policy.ADMIN),
    # The faces bulk acts below put names on files everybody sees or teach Sift for ever. A person
    # this user may not be told about answers "nothing changed", never 404. A `{person_id}` without
    # params makes `fill` raise and fails every whole-matrix test, so params are required.
    ("POST", "/api/faces/look-alikes/{person_id}/confirm"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON}
    ),
    ("POST", "/api/faces/look-alikes/{person_id}/reject"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON}
    ),
    ("POST", "/api/faces/may-be/{person_id}/confirm"): Case(
        Policy.ADMIN,
        params={"person_id": A_PERSON},
        body={"pile_ids": ["no-such-group"], "track_ids": ["no-such-face"]},
    ),
    ("POST", "/api/faces/may-be/{person_id}/reject"): Case(
        Policy.ADMIN,
        params={"person_id": A_PERSON},
        body={"pile_ids": ["no-such-group"], "track_ids": ["no-such-face"]},
    ),
    ("POST", "/api/faces/people/{person_id}/confirm-matches"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON}
    ),
    ("POST", "/api/faces/people/{person_id}/reject-matches"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON}
    ),
    # Overrides the grouping everybody reviews.
    ("POST", "/api/faces/move"): Case(Policy.ADMIN),
    ("POST", "/api/faces/remove"): Case(Policy.ADMIN),
    # Real work on the machine.
    ("POST", "/api/faces/regroup"): Case(Policy.ADMIN),
    ("POST", "/api/faces/groups/{pile_id}/restore"): Case(
        Policy.ADMIN, params={"pile_id": "01HX0000000000000000000009"}
    ),
    # Counts across the whole install, and clearing removes for good.
    ("GET", "/api/tidy"): Case(Policy.ADMIN),
    ("POST", "/api/tidy/survey"): Case(Policy.ADMIN),
    ("POST", "/api/tidy/{name}"): Case(Policy.ADMIN, params={"name": "stranded-assets"}),
    # Removes nothing, but holds the database while it runs.
    ("POST", "/api/tidy/database/optimize"): Case(Policy.ADMIN),
    # Names what somebody downloaded onto the server.
    ("GET", "/api/faces/settings"): Case(Policy.ADMIN),
    # Queues a job that reaches the internet; it costs a row here, not a download.
    ("POST", "/api/faces/weights/fetch"): Case(Policy.ADMIN),
    ("POST", "/api/faces/scan"): Case(Policy.ADMIN),
    ("GET", "/api/faces/starters"): Case(Policy.ADMIN),
    ("POST", "/api/faces/starters"): Case(Policy.ADMIN),
    ("GET", "/api/faces/known"): Case(Policy.ADMIN),
    ("POST", "/api/faces/packs/import"): Case(Policy.ADMIN),
    ("GET", "/api/faces/fingerprints/offers"): Case(Policy.ADMIN),
    ("GET", "/api/faces/fingerprints/waiting"): Case(Policy.ADMIN),
    ("DELETE", "/api/faces/fingerprints/waiting/{entry_id}"): Case(
        Policy.ADMIN, params={"entry_id": "no-such-entry"}, answers_anyway=404
    ),
    ("POST", "/api/faces/fingerprints/{entry_id}/person"): Case(
        Policy.ADMIN, params={"entry_id": "no-such-entry"}
    ),
    # Decides what every other user is told about who is in a file.
    ("POST", "/api/faces/references/folder"): Case(Policy.ADMIN),
    ("POST", "/api/faces/references/folder/path"): Case(Policy.ADMIN, body={"path": "/nowhere"}),
    ("GET", "/api/faces/references/folder/{job_id}/left-out"): Case(
        Policy.ADMIN, params={"job_id": FAILED_JOB}
    ),
    ("POST", "/api/faces/packs/export"): Case(Policy.ADMIN),
    # Empties the tables the routes above read.
    ("POST", "/api/faces/forget"): Case(Policy.ADMIN, destructive=True),
    ("DELETE", "/api/people/{person_id}"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON}, destructive=True
    ),
    ("DELETE", "/api/collections/{collection_id}"): Case(
        Policy.ADMIN, params={"collection_id": A_DELETABLE_COLLECTION}, destructive=True
    ),
    ("DELETE", "/api/photo-sets/{photo_set_id}"): Case(
        Policy.ADMIN, params={"photo_set_id": A_DELETABLE_PHOTO_SET}, destructive=True
    ),
    ("DELETE", "/api/songs/{song_id}"): Case(
        Policy.ADMIN, params={"song_id": A_DELETABLE_SONG}, destructive=True
    ),
    ("POST", "/api/songs/merge"): Case(
        Policy.ADMIN, body={"into": A_SONG, "songs": [A_MERGED_SONG]}, destructive=True
    ),
    # AUTHENTICATED: making a Loop is not admin-only, so removing your own is not. A row neither
    # yours nor an admin's is skipped and counted; "not yours" and "no such Loop" are one answer.
    # Pointed at `A_LOOP` because destructive entries run last, so it really forgets one.
    ("POST", "/api/loops/forget"): Case(
        Policy.AUTHENTICATED, body={"loop_ids": [A_LOOP]}, destructive=True
    ),
    ("DELETE", "/api/sites/{site_id}"): Case(
        Policy.ADMIN, params={"site_id": A_DELETABLE_SITE}, destructive=True
    ),
}
