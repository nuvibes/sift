# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every route, called over HTTP as each kind of user, answers what `MATRIX` says it should.

A behavioural gate rather than a lint: a static check proves a route has an authorization check,
not that the check answers right. A route missing from `MATRIX` fails the build, so adding a route
means deciding, in writing, who may call it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.routing import APIWebSocketRoute
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from starlette.routing import WebSocketRoute

from sift.kernel.access import Role, Viewer
from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.slices.backup import recycle
from sift.slices.media_edit.jobs import SAMPLES_DIRECTORY
from sift.slices.media_edit.service import COMPRESS_SAMPLE
from sift.testing.auth import establish_session
from sift.testing.authz import booted, shared_client
from sift.testing.jobs import seed_job, seed_run
from sift.testing.library import (
    attach_person,
    attach_tag,
    attach_username,
    seed_alias,
    seed_art,
    seed_asset,
    seed_claim,
    seed_collection,
    seed_cover_picture,
    seed_decision,
    seed_face,
    seed_grant,
    seed_guest_user,
    seed_link,
    seed_loop,
    seed_loop_still,
    seed_move,
    seed_person,
    seed_photo_set,
    seed_quarantined,
    seed_root,
    seed_site,
    seed_song,
    seed_stash_box,
    seed_stash_box_link,
    seed_tag,
    seed_username,
    share_folder,
)
from sift.testing.settings import set_app_setting
from sift.wiring.routes import API_PREFIX

pytestmark = [pytest.mark.gate, pytest.mark.integration]


class Policy(StrEnum):
    """Who may call a route."""

    PUBLIC = "public"
    """No session needed. Everything here is visible to the internet: assume it is read by
    someone who has not logged in and never will."""

    AUTHENTICATED = "authenticated"
    """Any signed-in user, guest included. The route still has to scope what it returns."""

    ADMIN = "admin"
    """Admins only, enforced by the server. Hiding the button is not this."""


#: Every id below names a real seeded row. A route answers 404 for an id that names nothing, and
#: this table reads 404 as a refusal, so a made-up id would pass for every role and prove nothing.
#: A destructive route gets a row of its own, so the routes called after it still find theirs.
#:
#: Two jobs because retry needs a failed one and cancel an unfinished one; the cancellable one is
#: `blocked`, because the booted workers would claim and fail a `queued` one first.
FAILED_JOB = "01HX0000000000000000000001"
CANCELABLE_JOB = "01HX0000000000000000000002"
A_FINISHED_RUN = "01HX0000000000000000000003"

#: The quarantine route takes a bare filename, not an id (see `quarantine.resolve`).
A_QUARANTINED = "seeded-refusal.bin"
#: A backup with no library's mark on its name, asked for by the name the unmarked list shows.
AN_UNMARKED_BACKUP = "sift-backup-20260720-141500-1.0.0.zip"
A_ROOT = "01HX0000000000000000000003"
A_FOLDER = "01HX0000000000000000000004"
DELETABLE_ROOT = "01HX0000000000000000000005"
A_GRANT = "01HX0000000000000000000008"
A_TAG = "01HX0000000000000000000008"
DELETABLE_FOLDER = "01HX0000000000000000000006"

#: In A_FOLDER, shared with whoever signs in; its original, thumbnail and preview are all seeded.
AN_ASSET = "01HX0000000000000000000007"

#: Differs from AN_ASSET before its last character: `seed_asset` derives the location id by
#: replacing that character, and two equal location ids fail a unique constraint.
DELETABLE_ASSET = "01HX0000000000000000000080"

#: One found face on `AN_ASSET`, with a stored picture for the crop route.
A_FACE = "01HX0000000000000000000016"

#: Two guest users, never signed in as: the DELETE takes one and the sharing routes name the other,
#: since deleting a guest also takes their grants.
A_GUEST_USER = "01HX0000000000000000000090"
A_DELETABLE_GUEST_USER = "01HX0000000000000000000091"

#: A person, an alias, a site and a username. The site is seeded with no usernames: one with some
#: answers an admin 409, a refusal of the database's state that reads here as one of the caller.
A_PERSON = "01HX0000000000000000000010"

#: Their own pair because a merge removes one of the two and runs in the late phase that deletes
#: `A_PERSON`; sharing rows would pass or fail by ordering rather than by permission.
MERGE_FROM = "01HX0000000000000000000091"
MERGE_INTO = "01HX0000000000000000000092"

#: The same pair for SITES. Not `A_SITE`, which every other site route names, nor
#: `A_DELETABLE_SITE`, which a DELETE sorted before this POST removes. Neither carries a saved
#: login: two that did would answer an admin 409, since only one credential survives a merge.
SITE_MERGE_FROM = "01HX0000000000000000000095"
SITE_MERGE_INTO = "01HX0000000000000000000096"

#: A link goes with its person, and deleting `A_PERSON` sorts before forgetting a link.
A_LINKED_PERSON = "01HX0000000000000000000093"
AN_ALIAS = "01HX0000000000000000000011"
A_LINK = "01HX0000000000000000000019"
A_SITE = "01HX0000000000000000000012"

#: One uploaded cover picture, worn by all six things that carry a cover. Without it the six cover
#: routes answer 404 for everybody: "no cover" and "not for you" are the same 404 by design.
A_COVER_PICTURE = "01HX0000000000000000000094"
A_USERNAME = "01HX0000000000000000000013"

A_DELETABLE_SITE = "01HX0000000000000000000014"
A_DELETABLE_USERNAME = "01HX0000000000000000000015"
#: Both collections hold `AN_ASSET`: a collection is scoped by what the caller can see inside it,
#: so an empty one would answer a guest by that rule rather than by the route's policy.
A_COLLECTION = "01HX0000000000000000000016"
A_DELETABLE_COLLECTION = "01HX0000000000000000000017"

#: A photo set, and one to delete; both hold `AN_ASSET` for the reason the collections do.
A_PHOTO_SET = "01HX0000000000000000000020"
A_DELETABLE_PHOTO_SET = "01HX0000000000000000000021"

#: A song that holds `AN_ASSET`, so a guest is answered by the policy rather than by the rule that
#: a song is seen through its files; the two an admin deletes and merges away hold nothing.
A_SONG = "01HX0000000000000000000023"
A_DELETABLE_SONG = "01HX0000000000000000000024"
A_MERGED_SONG = "01HX0000000000000000000025"

#: A Loop on `AN_ASSET`, and a second one to delete. Seeded with NO owner: moving or removing a
#: Loop is its maker's or an admin's, so an ownerless one is refused to a guest and allowed to an
#: admin, which is what those routes claim.
A_LOOP = "01HX0000000000000000000022"

#: A recap id nobody's recap wears; well formed, so what answers is the route's own 404.
A_RECAP = "01HX0000000000000000000097"

#: One configured stash-box, with no key.
A_STASH_BOX = "01HX0000000000000000000095"

A_DELETABLE_STASH_BOX = "01HX0000000000000000000096"

#: Deliberately not seeded: an admin reaching the resolve route's 404 has passed the door.
A_DEDUP_CANDIDATE = "01HX0000000000000000000091"

#: Two folder claims, one per route, because answering one settles it.
A_SUGGESTION = "01HX0000000000000000000093"
ANOTHER_SUGGESTION = "01HX0000000000000000000094"

#: One recorded move whose location does not exist: an admin gets a 409 about the database's
#: state, a guest the 403 this table asks about.
A_MOVE = "01HX0000000000000000000092"

#: One recorded decision with an empty payload, so an admin gets a refusal about the record.
A_DECISION = "01HX0000000000000000000094"

#: A finished sample-encode job with a few bytes behind it: the route answers 404 for a swept
#: sample too. The bytes are handed over, never decoded, so they need not be media.
A_SAMPLE_JOB = "01HX0000000000000000000093"

#: A site and a creator Sift has kept a picture for. The creator is stored as `site:username` and
#: asked for by username alone, so the two constants are not interchangeable.
ART_SITE = "youtube"
ART_CREATOR = "someone"
ART_CREATOR_SCOPE = f"{ART_SITE}:{ART_CREATOR}"


#: The pseudo-method a WebSocket route is declared under. A WebSocket route carries no `methods`,
#: so a gate walking routes for verbs would skip it while the socket serves.
WEBSOCKET = "WS"


@dataclass(frozen=True)
class Case:
    policy: Policy
    #: Values for any `{placeholder}` in the path.
    params: dict[str, str] = field(default_factory=dict)
    #: Values for any parameter the route REQUIRES in the query string: without them it answers 422
    #: before its permission check, and a 422 is not a refusal.
    query: dict[str, str] = field(default_factory=dict)
    #: Called after every other route. The sweep runs in one session against one database, so a
    #: route that removes rows other routes name (emptying the bin, clearing failed jobs) must come
    #: last.
    destructive: bool = False
    #: A body for a route that decides inside its handler ("yours, or an admin's"): that check runs
    #: only once the body parses, so without one the route answers 422 and the check never runs.
    #: Give the smallest body that parses.
    body: dict[str, object] | None = None
    #: A status this route honestly answers to somebody who IS allowed, which would otherwise read
    #: as a refusal (the stash-box picture proxy's box is seeded at a `.invalid` address, so an
    #: admin is told 404). Only ever loosens the ALLOWED side: a caller who is not allowed must
    #: still be denied.
    answers_anyway: int | None = None


# One line per route, and no route without a line.
#
# The API docs (/docs, /redoc, /openapi.json) are absent: they mount only when the operator sets
# SIFT_ENABLE_DOCS. Setup and login are public because signing in cannot require being signed in.
MATRIX: dict[tuple[str, str], Case] = {
    ("GET", "/health"): Case(Policy.PUBLIC),
    # The sign-in screen asks this before anyone has signed in.
    ("GET", "/api/auth/status"): Case(Policy.PUBLIC),
    ("POST", "/api/auth/setup"): Case(Policy.PUBLIC),
    ("POST", "/api/auth/login"): Case(Policy.PUBLIC),
    # The first-run admin form's strength meter asks before any session exists; it names no user.
    ("POST", "/api/auth/password/check"): Case(Policy.PUBLIC),
    ("POST", "/api/auth/logout"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/auth/me"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/auth/password"): Case(Policy.AUTHENTICATED),
    # Puts a signed-in user's master key back after a restart, for the password sign-in takes.
    ("POST", "/api/auth/unlock-secrets"): Case(Policy.AUTHENTICATED),
    ("PUT", "/api/auth/pin"): Case(Policy.AUTHENTICATED),
    # The app lock: unlocking is reachable from a session that is already locked.
    ("POST", "/api/auth/lock"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/auth/unlock"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/auth/unlock/password"): Case(Policy.AUTHENTICATED),
    # User management, admin throughout: who else has a user here is a fact about the household.
    ("GET", "/api/auth/users"): Case(Policy.ADMIN),
    ("POST", "/api/auth/users"): Case(Policy.ADMIN),
    ("POST", "/api/auth/users/generate"): Case(Policy.ADMIN),
    # Renaming acts on the caller's OWN user, so it is open to every user ("you, or an admin").
    ("POST", "/api/auth/users/{user_id}/username"): Case(
        Policy.AUTHENTICATED, params={"user_id": A_GUEST_USER}
    ),
    ("PUT", "/api/auth/users/{user_id}/disabled"): Case(
        Policy.ADMIN, params={"user_id": A_GUEST_USER}
    ),
    ("POST", "/api/auth/users/{user_id}/password"): Case(
        Policy.ADMIN, params={"user_id": A_GUEST_USER}
    ),
    ("DELETE", "/api/auth/users/{user_id}"): Case(
        Policy.ADMIN, params={"user_id": A_DELETABLE_GUEST_USER}, destructive=True
    ),
    # Sharing, the reads included: who else something is shared with is somebody else's access.
    ("GET", "/api/sharing/users"): Case(Policy.ADMIN),
    ("GET", "/api/sharing"): Case(Policy.ADMIN),
    ("GET", "/api/sharing/sources"): Case(Policy.ADMIN),
    # A list of what the OTHER users can see, named.
    ("GET", "/api/sharing/reach"): Case(Policy.ADMIN),
    ("GET", "/api/sharing/reach/through"): Case(
        Policy.ADMIN,
        query={"object_type": "person", "object_id": A_PERSON, "user": A_GUEST_USER},
    ),
    # Names what the CALLER has hidden, and answers nothing while their Hidden is shut.
    ("GET", "/api/sharing/hidden-by"): Case(Policy.AUTHENTICATED),
    ("PUT", "/api/sharing"): Case(Policy.ADMIN),
    ("POST", "/api/sharing/revoke"): Case(Policy.ADMIN),
    # A guest may write their own settings; a global key is refused inside the handler with a 403.
    ("GET", "/api/settings"): Case(Policy.AUTHENTICATED),
    ("PUT", "/api/settings"): Case(Policy.AUTHENTICATED),
    # Each user's own rail, guest included.
    ("GET", "/api/settings/interface"): Case(Policy.AUTHENTICATED),
    ("PUT", "/api/settings/interface"): Case(Policy.AUTHENTICATED),
    # Admin work; an empty board for a guest would still tell them the queues exist.
    ("GET", "/api/workbench"): Case(Policy.ADMIN),
    # Names which user decided what. `folders` is the queue every install has.
    ("POST", "/api/workbench/decisions/{decision_id}/undo"): Case(
        Policy.ADMIN, params={"decision_id": A_DECISION}
    ),
    # The feed is admin-only, and a bulk door must be no easier to reach than its single one.
    ("POST", "/api/ledger/{event_id}/undo-all"): Case(
        Policy.ADMIN, params={"event_id": A_DECISION}
    ),
    # Every user's acts in one list; a guest's own record is a file's history, scoped by the file.
    ("GET", "/api/ledger"): Case(Policy.ADMIN),
    # The jobs dashboard, admin throughout: what Sift does with the files is a picture of them.
    ("GET", "/api/jobs"): Case(Policy.ADMIN),
    ("GET", "/api/jobs/{job_id}/steps"): Case(Policy.ADMIN, params={"job_id": FAILED_JOB}),
    ("POST", "/api/jobs/{job_id}/retry"): Case(Policy.ADMIN, params={"job_id": FAILED_JOB}),
    ("POST", "/api/jobs/{job_id}/cancel"): Case(Policy.ADMIN, params={"job_id": CANCELABLE_JOB}),
    # The bulk queue acts below are destructive: they move or delete the rows per-job cases name.
    ("POST", "/api/jobs/retry-failed"): Case(Policy.ADMIN, destructive=True),
    ("POST", "/api/jobs/clear-failed"): Case(Policy.ADMIN, destructive=True),
    ("POST", "/api/jobs/cancel-all"): Case(Policy.ADMIN, destructive=True),
    ("POST", "/api/jobs/retry-canceled"): Case(Policy.ADMIN, destructive=True),
    ("POST", "/api/jobs/clear-canceled"): Case(Policy.ADMIN, destructive=True),
    # The whole installation's pool.
    ("POST", "/api/jobs/full-amount"): Case(Policy.ADMIN, body={"on": False}),
    # A guest's screen goes stale as an admin's does; a message names a kind, never carries a row.
    ("GET", "/api/live"): Case(Policy.AUTHENTICATED),
    (WEBSOCKET, "/api/live/stream"): Case(Policy.AUTHENTICATED),
    # The count describes the whole library, and the run is one job per file.
    ("GET", "/api/jobs/rebuild-thumbnails"): Case(Policy.ADMIN),
    ("POST", "/api/jobs/rebuild-thumbnails"): Case(Policy.ADMIN),
    ("GET", "/api/jobs/rebuild-previews"): Case(Policy.ADMIN),
    ("POST", "/api/jobs/rebuild-previews"): Case(Policy.ADMIN),
    # Every task acts on the whole installation, and the reply names its schedule.
    ("GET", "/api/tasks"): Case(Policy.ADMIN),
    ("POST", "/api/tasks/{task_id}/run"): Case(
        Policy.ADMIN, params={"task_id": "backup"}, body={"at": "quiet"}
    ),
    # Everything about a ROOT, the folder picker and the grants is admin-only: a root is a directory
    # on the server's disk, and the picker enumerates what is mounted.
    ("GET", "/api/library/grants"): Case(Policy.ADMIN),
    ("POST", "/api/library/grants"): Case(Policy.ADMIN, body={"path": "/nowhere/that/exists"}),
    # How a file is handled on arrival changes what the machine does for everybody.
    ("GET", "/api/importing/folders"): Case(Policy.ADMIN),
    ("PUT", "/api/importing/folders/{root_id}"): Case(
        Policy.ADMIN, params={"root_id": A_ROOT}, body={"answers": {}}
    ),
    ("GET", "/api/importing/build"): Case(Policy.ADMIN),
    ("POST", "/api/importing/build"): Case(Policy.ADMIN, body={"products": ["pictures"]}),
    ("POST", "/api/importing/build/retry"): Case(Policy.ADMIN, body={"products": ["pictures"]}),
    # Real work on the machine; a 409 tells an admin the file is already waiting.
    ("GET", "/api/importing/run-now"): Case(Policy.ADMIN),
    ("POST", "/api/assets/run"): Case(
        Policy.ADMIN, body={"run": "details", "asset_ids": [AN_ASSET]}, answers_anyway=409
    ),
    ("GET", "/api/library/browse"): Case(Policy.ADMIN),
    ("GET", "/api/library/roots"): Case(Policy.ADMIN),
    ("POST", "/api/library/roots"): Case(Policy.ADMIN),
    ("PATCH", "/api/library/roots/{root_id}"): Case(Policy.ADMIN, params={"root_id": A_ROOT}),
    ("DELETE", "/api/library/roots/{root_id}"): Case(
        Policy.ADMIN, params={"root_id": DELETABLE_ROOT}
    ),
    ("POST", "/api/library/roots/{root_id}/rescan"): Case(Policy.ADMIN, params={"root_id": A_ROOT}),
    # Carries a path on the machine in its body, as adding a root does.
    ("POST", "/api/library/roots/{root_id}/moved"): Case(
        Policy.ADMIN, params={"root_id": A_ROOT}, body={"abs_path": "/nowhere/at/all"}
    ),
    # A refusal names a file by its path inside somebody's library.
    ("POST", "/api/library/roots/{root_id}/rejections/allow"): Case(
        Policy.ADMIN, params={"root_id": A_ROOT}, body={"rel_path": "nothing/was/refused.bin"}
    ),
    ("GET", "/api/library/quarantine"): Case(Policy.ADMIN),
    # The one route whose job is deleting the file it is handed.
    ("DELETE", "/api/library/quarantine/{name}"): Case(
        Policy.ADMIN, params={"name": A_QUARANTINED}, destructive=True
    ),
    # FOLDER reads are for anyone signed in and scoped in the handler: a hidden folder is absent.
    ("GET", "/api/library/folders"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/library/folders/{folder_id}"): Case(
        Policy.AUTHENTICATED, params={"folder_id": A_FOLDER}
    ),
    # These count what a folder physically holds, not what the caller may open.
    ("GET", "/api/library/folders/facts"): Case(Policy.ADMIN),
    # Disk facts and physical counts, the line `FolderDetail`'s file count draws.
    ("GET", "/api/library/folders/{folder_id}/properties"): Case(
        Policy.ADMIN, params={"folder_id": A_FOLDER}
    ),
    # Writes into somebody's library; a folder not handed over read-write is refused in the handler.
    ("POST", "/api/library/folders"): Case(
        Policy.ADMIN, body={"parent_id": A_FOLDER, "name": "nothing"}
    ),
    ("POST", "/api/library/folders/placed"): Case(
        Policy.ADMIN, body={"parent_id": A_FOLDER, "name": "nothing"}
    ),
    ("PATCH", "/api/library/folders/{folder_id}"): Case(
        Policy.ADMIN, params={"folder_id": A_FOLDER}, body={"name": "nothing"}
    ),
    # Organizing changes a file somebody else put there. Reading whether a file *can* be organized
    # must first settle whether the caller can see it, so it is authenticated.
    ("GET", "/api/assets/{asset_id}/organize"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("POST", "/api/assets/{asset_id}/rename"): Case(Policy.ADMIN, params={"asset_id": AN_ASSET}),
    ("POST", "/api/assets/move"): Case(
        Policy.ADMIN, body={"asset_ids": [AN_ASSET], "folder_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/moves/{move_id}/undo"): Case(Policy.ADMIN, params={"move_id": A_MOVE}),
    # Admin at the door: per-file refusals are answered in the rows, so a guest would get a 200.
    ("POST", "/api/organize/rename/preview"): Case(
        Policy.ADMIN, body={"asset_ids": [AN_ASSET], "template": "{name}"}
    ),
    ("POST", "/api/organize/rename"): Case(
        Policy.ADMIN, body={"asset_ids": [AN_ASSET], "template": "{name}"}
    ),
    # Works the machine hard and recommends instance-wide settings; starting one starts one.
    ("POST", "/api/performance/self-test"): Case(Policy.ADMIN, destructive=True),
    ("GET", "/api/performance/self-test"): Case(Policy.ADMIN),
    ("GET", "/api/performance/benchmark"): Case(Policy.ADMIN),
    # A log line names files, users and paths, and what OTHER people did.
    ("GET", "/api/logs"): Case(Policy.ADMIN),
    ("GET", "/api/logs/archive"): Case(Policy.ADMIN),
    ("GET", "/api/performance/hardware"): Case(Policy.ADMIN),
    # A run says how big the library is and names the machine.
    ("GET", "/api/performance/runs/{run_id}/report"): Case(
        Policy.ADMIN, params={"run_id": A_FINISHED_RUN}
    ),
    # The graphics-card runtime: reading names the card; the others spend bandwidth, disk and time.
    ("GET", "/api/performance/accelerator"): Case(Policy.ADMIN),
    ("POST", "/api/performance/accelerator"): Case(Policy.ADMIN),
    ("POST", "/api/performance/accelerator/test"): Case(Policy.ADMIN),
    ("DELETE", "/api/performance/accelerator"): Case(Policy.ADMIN),
    # Takes the library off the air for everybody. Not `destructive`: nothing supervises a test
    # process, so it answers 409 and stops nothing.
    ("POST", "/api/performance/restart"): Case(Policy.ADMIN),
    # Compressing reads or writes somebody's files. A route naming an asset settles visibility
    # first, so a guest is told there is no such file rather than that one exists.
    ("POST", "/api/compress/preflight"): Case(Policy.ADMIN),
    ("POST", "/api/compress"): Case(Policy.ADMIN),
    ("POST", "/api/assets/{asset_id}/compress/sample"): Case(
        Policy.ADMIN, params={"asset_id": AN_ASSET}
    ),
    # Editing produces a new file, so it is an admin's; the door is in the service, which settles
    # visibility first. The frame size is asked only in order to edit.
    ("GET", "/api/assets/{asset_id}/edit/frame"): Case(Policy.ADMIN, params={"asset_id": AN_ASSET}),
    ("POST", "/api/assets/{asset_id}/edit/preflight"): Case(
        Policy.ADMIN, params={"asset_id": AN_ASSET}
    ),
    ("POST", "/api/assets/{asset_id}/edit"): Case(Policy.ADMIN, params={"asset_id": AN_ASSET}),
    # A fact about a file the user can already see; an original they may not see is not named.
    ("GET", "/api/assets/{asset_id}/produced"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("GET", "/api/assets/{asset_id}/made-from"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # A few seconds of somebody's file.
    ("GET", "/api/compress/samples/{job_id}"): Case(Policy.ADMIN, params={"job_id": A_SAMPLE_JOB}),
    # The downloader, admin permanently: it reaches the internet and holds the site logins.
    ("POST", "/api/downloads"): Case(Policy.ADMIN),
    ("GET", "/api/downloads"): Case(Policy.ADMIN),
    ("GET", "/api/downloads/facets"): Case(Policy.ADMIN, query={"facet": "site"}),
    ("GET", "/api/downloads/glance"): Case(Policy.ADMIN),
    ("POST", "/api/downloads/seen"): Case(Policy.ADMIN),
    ("POST", "/api/downloads/{download_id}/cancel"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/downloads/{download_id}/anyway"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    # A site Sift does not take whole answers 400, past the door.
    ("POST", "/api/downloads/bulk-preview"): Case(Policy.ADMIN),
    ("POST", "/api/downloads/bulk"): Case(Policy.ADMIN),
    ("POST", "/api/downloads/links"): Case(Policy.ADMIN),
    ("POST", "/api/downloads/{download_id}/retry"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/downloads/{download_id}/first"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/downloads/{download_id}/remove"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    # The id resolves to nothing: pause and resume answer 409, and the restore's honest 404 is
    # declared with `answers_anyway`.
    ("POST", "/api/downloads/{download_id}/pause"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/downloads/{download_id}/resume"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/downloads/{download_id}/restore"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}, answers_anyway=404
    ),
    ("GET", "/api/supported-sites"): Case(Policy.ADMIN),
    ("GET", "/api/download-tools"): Case(Policy.ADMIN),
    # The one route here that opens a connection out; without a body an admin reaches a 422.
    ("POST", "/api/download-tools/latest"): Case(Policy.ADMIN),
    # Changes what every download on this install produces.
    ("GET", "/api/site-options"): Case(Policy.ADMIN),
    ("PUT", "/api/site-options/{scope}"): Case(Policy.ADMIN, params={"scope": "youtube"}),
    ("DELETE", "/api/site-options/{scope}"): Case(Policy.ADMIN, params={"scope": "youtube"}),
    ("POST", "/api/site-options/preview"): Case(Policy.ADMIN),
    # A name Sift holds a picture for is a name somebody downloaded from.
    ("GET", "/api/creator-art"): Case(Policy.ADMIN),
    ("GET", "/api/creator-art/{username}"): Case(Policy.ADMIN, params={"username": ART_CREATOR}),
    # The names still come through the access layer.
    ("GET", "/api/downloads/{download_id}/files"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    # The stash-boxes, reads included: these routes make Sift ask somebody else's service with an
    # admin's own key. Nothing here is destructive: no other route reads a keyless box's row.
    ("GET", "/api/stash-boxes"): Case(Policy.ADMIN),
    ("POST", "/api/stash-boxes"): Case(
        Policy.ADMIN, body={"name": "A box", "endpoint": "https://example.invalid/graphql"}
    ),
    ("PUT", "/api/stash-boxes/{box_id}"): Case(
        Policy.ADMIN, params={"box_id": A_STASH_BOX}, body={"enabled": True}
    ),
    ("DELETE", "/api/stash-boxes/{box_id}"): Case(
        Policy.ADMIN, params={"box_id": A_DELETABLE_STASH_BOX}
    ),
    ("PUT", "/api/stash-boxes/{box_id}/key"): Case(
        Policy.ADMIN, params={"box_id": A_STASH_BOX}, body={"api_key": "not-a-key"}
    ),
    ("DELETE", "/api/stash-boxes/{box_id}/answers"): Case(
        Policy.ADMIN, params={"box_id": A_STASH_BOX}
    ),
    ("POST", "/api/stash-boxes/{box_id}/check"): Case(Policy.ADMIN, params={"box_id": A_STASH_BOX}),
    # Served from Sift's own address because the pages run under `img-src 'self'`.
    ("GET", "/api/stash-boxes/{box_id}/picture"): Case(
        Policy.ADMIN,
        params={"box_id": A_STASH_BOX},
        query={"url": "https://stash-box.invalid/img/1"},
        # `.invalid` resolves nowhere, so an admin is honestly told there is no picture.
        answers_anyway=404,
    ),
    # The address comes from Sift's copy of what the box said; the seeded subject is not linked to
    # the seeded box, so an admin's honest answer is 404.
    ("POST", "/api/stash-boxes/{box_id}/picture/keep"): Case(
        Policy.ADMIN,
        params={"box_id": A_STASH_BOX},
        body={"subject": "person", "local_id": MERGE_FROM},
        answers_anyway=404,
    ),
    # There is no `GET /api/stash-boxes/unasked`: the two rows below are what the screen reads.
    ("POST", "/api/stash-boxes/enrich"): Case(
        Policy.ADMIN,
        body={"subject": "person", "ids": [MERGE_FROM]},
        # Queued, and no box is switched on in this sweep, so nothing is asked of anybody.
    ),
    # No box is switched on in this sweep, so the look-ups answer an empty list.
    ("GET", "/api/stash-boxes/look-up"): Case(Policy.ADMIN, query={"term": "nobody"}),
    ("GET", "/api/stash-boxes/recognise/{asset_id}"): Case(
        Policy.ADMIN, params={"asset_id": AN_ASSET}
    ),
    # Signed-in: no network, no key, Sift's own tables, and every row resolved through the access
    # layer. The totals are unscoped, as the reclaim list's are.
    ("GET", "/api/stash-boxes/linked"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/stash-boxes/undecided"): Case(Policy.AUTHENTICATED),
    # Either answer moves a Site's files for everybody; the seeded Site is no question, so 404.
    ("GET", "/api/stash-boxes/creator-sites"): Case(Policy.ADMIN),
    ("POST", "/api/stash-boxes/creator-sites/{site_id}/username"): Case(
        Policy.ADMIN, params={"site_id": A_SITE}, answers_anyway=404
    ),
    ("POST", "/api/stash-boxes/creator-sites/{site_id}/site"): Case(
        Policy.ADMIN, params={"site_id": A_SITE}, answers_anyway=404
    ),
    # The one stash-box route a guest may call: no network, no key, and the subject is resolved
    # through the access layer first.
    ("GET", "/api/stash-boxes/links/{subject}/{local_id}"): Case(
        Policy.AUTHENTICATED, params={"subject": "person", "local_id": A_PERSON}
    ),
    # Reading is a guest's as the links are; changing it is a decision every pass reads.
    ("GET", "/api/stash-boxes/enrichment/{subject}/{local_id}"): Case(
        Policy.AUTHENTICATED, params={"subject": "person", "local_id": A_PERSON}
    ),
    ("PUT", "/api/stash-boxes/enrichment/{subject}/{local_id}/keep-local"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": A_PERSON},
        body={"kept_local": True},
    ),
    # Searching, linking and refreshing spend an admin's key; unlinking edits a record.
    ("GET", "/api/stash-boxes/search/{subject}"): Case(
        Policy.ADMIN, params={"subject": "person"}, query={"term": "nobody"}
    ),
    ("PUT", "/api/stash-boxes/links/{subject}/{local_id}/{box_id}"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": A_LINKED_PERSON, "box_id": A_STASH_BOX},
        body={"remote_id": "not-a-real-id"},
    ),
    ("POST", "/api/stash-boxes/links/{subject}/{local_id}/{box_id}/refresh"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": A_LINKED_PERSON, "box_id": A_STASH_BOX},
    ),
    ("GET", "/api/stash-boxes/record/{subject}/{local_id}"): Case(
        Policy.ADMIN, params={"subject": "person", "local_id": A_PERSON}
    ),
    ("POST", "/api/stash-boxes/links/{subject}/{local_id}/{box_id}/take"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": A_LINKED_PERSON, "box_id": A_STASH_BOX},
        body={"keys": []},
    ),
    # Removes a row a later case reads.
    ("DELETE", "/api/stash-boxes/links/{subject}/{local_id}/{box_id}"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": A_LINKED_PERSON, "box_id": A_STASH_BOX},
        destructive=True,
    ),
    # The bulk pass spends an admin's key. Starting a scan answers 409 while the feature is off, as
    # it is on a fresh application; turning it on here would arrange the thing being measured.
    ("POST", "/api/stash-boxes/scan"): Case(Policy.ADMIN, answers_anyway=409),
    ("GET", "/api/stash-boxes/matches"): Case(Policy.ADMIN),
    ("POST", "/api/stash-boxes/matches/apply"): Case(Policy.ADMIN, body={"matches": []}),
    ("POST", "/api/stash-boxes/matches/refuse"): Case(Policy.ADMIN, body={"matches": []}),
    # Folding people who are one: the weighing, then the merge. The merge is destructive and removes
    # one of the pair, so the weighing must be asked first.
    ("POST", "/api/people/weigh-merge"): Case(
        Policy.ADMIN, body={"into": MERGE_INTO, "people": [MERGE_FROM, MERGE_INTO]}
    ),
    ("POST", "/api/people/merge"): Case(
        Policy.ADMIN,
        body={"into": MERGE_INTO, "people": [MERGE_FROM, MERGE_INTO]},
        destructive=True,
    ),
    # The same two acts for SITES. The weighing answers with counts about sites somebody may not
    # see, and a count is a fact about a library.
    ("POST", "/api/sites/weigh-merge"): Case(
        Policy.ADMIN,
        body={"into": SITE_MERGE_INTO, "sites": [SITE_MERGE_FROM, SITE_MERGE_INTO]},
    ),
    ("POST", "/api/sites/merge"): Case(
        Policy.ADMIN,
        body={"into": SITE_MERGE_INTO, "sites": [SITE_MERGE_FROM, SITE_MERGE_INTO]},
        destructive=True,
    ),
    ("GET", "/api/stash-boxes/disagreements"): Case(Policy.ADMIN),
    # A subject nothing is linked to and one that is not there answer the same empty list.
    ("GET", "/api/stash-boxes/disagreements/{subject}/{local_id}"): Case(
        Policy.ADMIN, params={"subject": "person", "local_id": A_PERSON}
    ),
    # The route looks the row up again rather than trusting the body, so on a fresh library an
    # admin's honest answer is 404.
    ("POST", "/api/stash-boxes/disagreements/settle"): Case(
        Policy.ADMIN,
        body={
            "subject": "person",
            "local_id": A_PERSON,
            "key": "name",
            "box_id": A_STASH_BOX,
            "take_theirs": False,
        },
        answers_anyway=404,
    ),
    ("GET", "/api/site-connections"): Case(Policy.ADMIN),
    ("POST", "/api/site-connections"): Case(Policy.ADMIN),
    # The cookies are an admin's and the answer describes them.
    ("POST", "/api/site-connections/preview"): Case(Policy.ADMIN),
    # Reads a sealed secret and sends a request to somebody else's site.
    ("POST", "/api/site-connections/{connection_id}/check"): Case(
        Policy.ADMIN, params={"connection_id": "01HX0000000000000000000009"}
    ),
    ("DELETE", "/api/site-connections/{connection_id}"): Case(
        Policy.ADMIN, params={"connection_id": "01HX0000000000000000000009"}
    ),
    # A tunnel holds a VPN provider's private key; which sites avoid the machine's own address is a
    # picture of what somebody is careful about.
    ("GET", "/api/tunnels"): Case(Policy.ADMIN),
    ("POST", "/api/tunnels"): Case(Policy.ADMIN),
    ("PATCH", "/api/tunnels/{tunnel_id}"): Case(
        Policy.ADMIN, params={"tunnel_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/tunnels/{tunnel_id}/config"): Case(
        Policy.ADMIN, params={"tunnel_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/tunnels/{tunnel_id}/start"): Case(
        Policy.ADMIN, params={"tunnel_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/tunnels/{tunnel_id}/stop"): Case(
        Policy.ADMIN, params={"tunnel_id": "01HX0000000000000000000009"}
    ),
    ("DELETE", "/api/tunnels/{tunnel_id}"): Case(
        Policy.ADMIN, params={"tunnel_id": "01HX0000000000000000000009"}
    ),
    ("GET", "/api/download-routes"): Case(Policy.ADMIN),
    ("PUT", "/api/download-routes/{scope}"): Case(Policy.ADMIN, params={"scope": "reddit"}),
    ("DELETE", "/api/download-routes/{scope}"): Case(Policy.ADMIN, params={"scope": "reddit"}),
    # Adding content decides what sits in the library, and a link reaches the downloader.
    ("POST", "/api/capture/import/file"): Case(Policy.ADMIN),
    ("POST", "/api/capture/import/url"): Case(Policy.ADMIN),
    ("POST", "/api/capture/import/clipboard"): Case(Policy.ADMIN),
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
    # Tags are shared, and one can carry an access grant; listing is scoped inside.
    ("GET", "/api/tags"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/tags"): Case(Policy.ADMIN),
    ("GET", "/api/tags/{tag_id}"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    ("GET", "/api/tags/{tag_id}/history"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    ("PUT", "/api/tags/{tag_id}"): Case(Policy.ADMIN, params={"tag_id": A_TAG}),
    # "DELETE" sorts before "GET", so unmarked it would take the tag before the by-id read.
    ("DELETE", "/api/tags/{tag_id}"): Case(
        Policy.ADMIN, params={"tag_id": A_TAG}, destructive=True
    ),
    # A tag's cover is shared vocabulary, seen by everybody.
    ("PUT", "/api/tags/{tag_id}/cover"): Case(Policy.ADMIN, params={"tag_id": A_TAG}),
    ("POST", "/api/tags/{tag_id}/cover-picture"): Case(Policy.ADMIN, params={"tag_id": A_TAG}),
    # For the reason written on the person's cover.
    ("GET", "/api/tags/{tag_id}/cover"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    # Hiding is the caller's own. Scoped: an unseen tag answers as an unknown one does.
    ("PUT", "/api/tags/{tag_id}/vault"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    # An opinion is one row per user and reaches nobody else; scoped, or a write would confirm the
    # id exists.
    ("PUT", "/api/tags/{tag_id}/favorite"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    ("PUT", "/api/tags/{tag_id}/rating"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    ("PUT", "/api/tags/{tag_id}/pin"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    ("POST", "/api/assets/tags"): Case(Policy.ADMIN),
    # A heart and a rating belong to whoever set them; scoped, or a write would confirm the id.
    ("GET", "/api/assets/{asset_id}/tags"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("PUT", "/api/assets/{asset_id}/rating"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("PUT", "/api/assets/{asset_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # The O counter: one row per user, as the heart is. One route for the three acts, a word apart.
    ("PUT", "/api/assets/{asset_id}/o-count"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}, body={"change": "up"}
    ),
    # A pin moves a file on THIS viewer's wall only. `_require_visible` also resolves the id: a
    # state row on an id that names nothing could never be cleaned up.
    ("PUT", "/api/assets/{asset_id}/pin"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}, body={"pinned": True}
    ),
    # Over a SELECTION: an asset the caller may not act on is skipped and counted, hence 200.
    ("POST", "/api/assets/rating"): Case(
        Policy.AUTHENTICATED, body={"asset_ids": [AN_ASSET], "rating": 3}
    ),
    ("POST", "/api/assets/favorite"): Case(
        Policy.AUTHENTICATED, body={"asset_ids": [AN_ASSET], "favorite": True}
    ),
    ("POST", "/api/assets/pin"): Case(
        Policy.AUTHENTICATED, body={"asset_ids": [AN_ASSET], "pinned": True}
    ),
    # People, Usernames and Sites are shared as tags are and can carry an access grant. Reading is
    # scoped inside: a vaulted person is absent from the list, the count and the resolver.
    ("GET", "/api/people"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/people"): Case(Policy.ADMIN),
    ("GET", "/api/people/{person_id}"): Case(Policy.AUTHENTICATED, params={"person_id": A_PERSON}),
    ("PUT", "/api/people/{person_id}"): Case(Policy.ADMIN, params={"person_id": A_PERSON}),
    # Hiding somebody is the viewer's own opinion, as on a site, a tag or a collection.
    ("PUT", "/api/people/{person_id}/vault"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    # Favouriting and rating are the viewer's own; tagging and the cover curate what everyone sees.
    # Without a body those two answer 422, past the door, so their id need not resolve.
    ("PUT", "/api/people/{person_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("PUT", "/api/people/{person_id}/rating"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("PUT", "/api/people/{person_id}/pin"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("GET", "/api/people/{person_id}/tags"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("POST", "/api/people/{person_id}/tags"): Case(Policy.ADMIN, params={"person_id": A_PERSON}),
    ("PUT", "/api/people/{person_id}/cover"): Case(Policy.ADMIN, params={"person_id": A_PERSON}),
    # The only route family where a stranger's bytes are the request body; the entity is resolved
    # against the viewer before a byte is read.
    ("POST", "/api/people/{person_id}/cover-picture"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON}
    ),
    # The person's cover: a cover is a fragment of a file, so `serve_cover` reads through the scoped
    # repository and a cover on something unseen is a 404. All six covers answer this way.
    ("GET", "/api/people/{person_id}/cover"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    # Names people, so it answers the list's rule.
    ("GET", "/api/people/resolve"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/people/{person_id}/links"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("POST", "/api/people/{person_id}/links"): Case(
        Policy.ADMIN,
        params={"person_id": A_PERSON},
    ),
    # Not destructive: it removes one row nothing else names, and the person delete cascades it.
    ("DELETE", "/api/people/{person_id}/links/{link_id}"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON, "link_id": A_LINK}
    ),
    # What was decided about a person is part of their record.
    ("GET", "/api/people/{person_id}/history"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    # The sharing half is about USERS, and the kernel leaves it out for anybody but an admin.
    ("GET", "/api/sites/{site_id}/history"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("GET", "/api/people/{person_id}/aliases"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("POST", "/api/people/{person_id}/aliases"): Case(Policy.ADMIN, params={"person_id": A_PERSON}),
    # Details is part of a person's record; writing it is on the person route.
    ("GET", "/api/people/{person_id}/notes"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("DELETE", "/api/people/{person_id}/aliases/{alias_id}"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON, "alias_id": AN_ALIAS}
    ),
    ("GET", "/api/assets/{asset_id}/people"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("POST", "/api/assets/people"): Case(Policy.ADMIN),
    ("POST", "/api/assets/sites"): Case(Policy.ADMIN),
    # Where a file came from is part of it; taking off a filing that is not there is a 204.
    ("GET", "/api/assets/{asset_id}/filings"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("DELETE", "/api/assets/{asset_id}/filings/{username_id}"): Case(
        Policy.ADMIN, params={"asset_id": AN_ASSET, "username_id": A_USERNAME}
    ),
    # What a field IS, never a value, so there is nothing to scope.
    ("GET", "/api/records/fields"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/collections"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/collections"): Case(Policy.ADMIN),
    ("GET", "/api/collections/{collection_id}"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("GET", "/api/collections/{collection_id}/history"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("GET", "/api/collections/{collection_id}/made-by"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}"): Case(
        Policy.ADMIN, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}/vault"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}/rating"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}/pin"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    # A tag is shared vocabulary.
    ("GET", "/api/collections/{collection_id}/tags"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("POST", "/api/collections/{collection_id}/tags"): Case(
        Policy.ADMIN, params={"collection_id": A_COLLECTION}
    ),
    ("GET", "/api/collections/{collection_id}/items"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("POST", "/api/collections/{collection_id}/items"): Case(
        Policy.ADMIN, params={"collection_id": A_COLLECTION}
    ),
    # For the reason written on the person's cover.
    ("GET", "/api/collections/{collection_id}/cover"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}/cover"): Case(
        Policy.ADMIN, params={"collection_id": A_COLLECTION}
    ),
    ("POST", "/api/collections/{collection_id}/cover-picture"): Case(
        Policy.ADMIN, params={"collection_id": A_COLLECTION}
    ),
    # --- photo sets: what the library HOLDS is an admin's, what one user THINKS of it is theirs.
    ("GET", "/api/photo-sets"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/photo-sets"): Case(Policy.ADMIN),
    # Deriving a set from a folder writes a grouping over the library.
    ("GET", "/api/photo-sets/{photo_set_id}"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("GET", "/api/photo-sets/{photo_set_id}/history"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("GET", "/api/photo-sets/{photo_set_id}/made-by"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}/notes"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    # For the reason written on the person's cover.
    ("GET", "/api/photo-sets/{photo_set_id}/cover"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}/cover"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("POST", "/api/photo-sets/{photo_set_id}/cover-picture"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    # The vault, the heart and the stars are one row per user.
    ("PUT", "/api/photo-sets/{photo_set_id}/vault"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}/rating"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}/pin"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("POST", "/api/photo-sets/{photo_set_id}/items"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("GET", "/api/photo-sets/{photo_set_id}/tags"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("POST", "/api/photo-sets/{photo_set_id}/tags"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    # --- songs: the photo sets' split. No vault grant on a song: its files decide who sees it.
    ("GET", "/api/songs"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/songs/facets"): Case(Policy.AUTHENTICATED, query={"facet": "created"}),
    ("POST", "/api/songs"): Case(Policy.ADMIN),
    ("GET", "/api/songs/{song_id}"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("GET", "/api/songs/{song_id}/history"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("GET", "/api/songs/{song_id}/made-by"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}/notes"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("GET", "/api/songs/{song_id}/cover"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}/cover"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("POST", "/api/songs/{song_id}/cover-picture"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"song_id": A_SONG}
    ),
    ("PUT", "/api/songs/{song_id}/rating"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}/pin"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("POST", "/api/songs/{song_id}/files"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}/vault"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    # Shared rows.
    ("PUT", "/api/songs/{song_id}/artists"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("PUT", "/api/artists/{artist_id}"): Case(Policy.ADMIN, params={"artist_id": A_SONG}),
    # Writes nothing, but only an admin may then merge.
    ("POST", "/api/songs/weigh-merge"): Case(
        Policy.ADMIN, body={"into": A_SONG, "songs": [A_MERGED_SONG]}
    ),
    # --- loops. Making one is not an admin's: it points at one file the user can already see.
    # Moving or removing one is its maker's or an admin's; the seeded Loops are ownerless.
    ("GET", "/api/loops"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/loops"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/loops/{loop_id}"): Case(Policy.AUTHENTICATED, params={"loop_id": A_LOOP}),
    # "Yours, or an admin's" is asked inside the handler, which runs only once the body parses.
    ("PUT", "/api/loops/{loop_id}"): Case(Policy.ADMIN, params={"loop_id": A_LOOP}, body={}),
    # The Loop is resolved through the visible set before a picture is looked for.
    ("GET", "/api/loops/{loop_id}/thumb"): Case(Policy.AUTHENTICATED, params={"loop_id": A_LOOP}),
    ("GET", "/api/loops/{loop_id}/tags"): Case(Policy.AUTHENTICATED, params={"loop_id": A_LOOP}),
    # A tag is shared vocabulary, whoever made the Loop.
    ("POST", "/api/loops/{loop_id}/tags"): Case(Policy.ADMIN, params={"loop_id": A_LOOP}),
    # --- tab strip numbers, from the same scoped listing as the wall: a guest gets noughts, not
    # the size of what is kept back.
    ("GET", "/api/related/{kind}/{entity_id}"): Case(
        Policy.AUTHENTICATED, params={"kind": "person", "entity_id": A_PERSON}
    ),
    # The vault. Shutting it is open to everybody, so a lock trigger is never refused; opening it
    # reveals only what that user hid.
    ("GET", "/api/vault"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/vault/unlock"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/vault/lock"): Case(Policy.AUTHENTICATED),
    ("PUT", "/api/assets/{asset_id}/vault"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # Hiding is per user; the way IN needs a PIN the seeded users do not hold.
    ("POST", "/api/assets/vault"): Case(
        Policy.AUTHENTICATED, body={"asset_ids": [AN_ASSET], "vault": False}
    ),
    ("PUT", "/api/folders/{folder_id}/vault"): Case(
        Policy.AUTHENTICATED, params={"folder_id": A_FOLDER}
    ),
    # Usernames: reading is scoped in the statement; saying who one belongs to decides whose name
    # is indexed on every file under it.
    ("GET", "/api/usernames"): Case(Policy.AUTHENTICATED),
    ("PUT", "/api/usernames/{username_id}"): Case(Policy.ADMIN, params={"username_id": A_USERNAME}),
    ("POST", "/api/usernames/{username_id}/person"): Case(
        Policy.ADMIN,
        params={"username_id": A_USERNAME},
        body={"person_id": A_PERSON},
    ),
    ("DELETE", "/api/usernames/{username_id}/person"): Case(
        Policy.ADMIN, params={"username_id": A_USERNAME}, destructive=True
    ),
    ("GET", "/api/sites"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/sites"): Case(Policy.ADMIN),
    ("GET", "/api/sites/{site_id}"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("PUT", "/api/sites/{site_id}"): Case(Policy.ADMIN, params={"site_id": A_SITE}),
    ("PUT", "/api/sites/{site_id}/vault"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    # The person's split: favorite and rating are the viewer's own; tags, cover and details curate.
    ("PUT", "/api/sites/{site_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"site_id": A_SITE}
    ),
    ("PUT", "/api/sites/{site_id}/rating"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("PUT", "/api/sites/{site_id}/pin"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("GET", "/api/sites/{site_id}/tags"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("POST", "/api/sites/{site_id}/tags"): Case(Policy.ADMIN, params={"site_id": A_SITE}),
    # For the reason written on the person's cover.
    ("GET", "/api/sites/{site_id}/cover"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("PUT", "/api/sites/{site_id}/cover"): Case(Policy.ADMIN, params={"site_id": A_SITE}),
    ("POST", "/api/sites/{site_id}/cover-picture"): Case(Policy.ADMIN, params={"site_id": A_SITE}),
    ("PUT", "/api/sites/{site_id}/details"): Case(Policy.ADMIN, params={"site_id": A_SITE}),
    # The pack that ships with Sift holds nothing of the library. `onlyfans` is in the pack, so the
    # answer is a 200 worth asserting.
    ("GET", "/api/sites/icons/{slug}"): Case(Policy.AUTHENTICATED, params={"slug": "onlyfans"}),
    ("GET", "/api/sites/icons/for"): Case(Policy.AUTHENTICATED, query={"host": "onlyfans.com"}),
    # Search is scoped inside the server. A search box takes a GUESS, so three channels stay shut
    # for a guest: the results, the COUNT, and the SUGGESTIONS. Results come from the grid's own
    # address. The history is filtered by the asking user, so no id can name another's row.
    ("GET", "/api/search/suggest"): Case(Policy.AUTHENTICATED),
    # Reads no rows; still not a parser left open to anybody.
    ("GET", "/api/search/parse"): Case(Policy.AUTHENTICATED),
    # Reads the same suggesters the dropdown does.
    ("GET", "/api/search/named"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/search/names-now"): Case(Policy.AUTHENTICATED),
    # A guest keeps their own recent searches. A POST, so another site cannot land an entry.
    ("POST", "/api/search/history"): Case(Policy.AUTHENTICATED),
    ("DELETE", "/api/search/history"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/search/opened"): Case(Policy.AUTHENTICATED),
    # The viewer's own; a guessed id reaches no row, so the delete answers 204.
    ("GET", "/api/search/saved"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/search/saved"): Case(Policy.AUTHENTICATED),
    ("DELETE", "/api/search/saved/{saved_id}"): Case(
        Policy.AUTHENTICATED, params={"saved_id": "01HX0000000000000000000091"}
    ),
    # A rename must say whether it happened, and "no such search of yours" confirms nothing else.
    ("PATCH", "/api/search/saved/{saved_id}"): Case(
        Policy.AUTHENTICATED, params={"saved_id": "01HX0000000000000000000091"}
    ),
    # Theater's saved walls belong to their maker, scoped in each statement's WHERE clause.
    ("GET", "/api/theater/arrangements"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/theater/arrangements"): Case(Policy.AUTHENTICATED),
    ("PATCH", "/api/theater/arrangements/{arrangement_id}"): Case(
        Policy.AUTHENTICATED, params={"arrangement_id": "01HX0000000000000000000092"}
    ),
    ("DELETE", "/api/theater/arrangements/{arrangement_id}"): Case(
        Policy.AUTHENTICATED, params={"arrangement_id": "01HX0000000000000000000092"}
    ),
    # Every field is optional, so an empty body is a whole report, keyed on the asker.
    ("POST", "/api/theater/watching"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/theater/sessions/{session}"): Case(
        Policy.AUTHENTICATED, params={"session": "an-evening-wall"}
    ),
    # The phone as a remote: every screen is held under the user who offered it; no body, so 422.
    ("GET", "/api/remote/screens"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/remote/screens/{screen}"): Case(
        Policy.AUTHENTICATED, params={"screen": "a-matrix-screen"}
    ),
    ("DELETE", "/api/remote/screens/{screen}"): Case(
        Policy.AUTHENTICATED, params={"screen": "a-matrix-screen"}
    ),
    ("POST", "/api/remote/screens/{screen}/commands"): Case(
        Policy.AUTHENTICATED, params={"screen": "a-matrix-screen"}
    ),
    ("PUT", "/api/remote/screens/{screen}/controllers/{controller}"): Case(
        Policy.AUTHENTICATED, params={"screen": "a-matrix-screen", "controller": "a-matrix-phone"}
    ),
    ("DELETE", "/api/remote/screens/{screen}/controllers/{controller}"): Case(
        Policy.AUTHENTICATED, params={"screen": "a-matrix-screen", "controller": "a-matrix-phone"}
    ),
    # Only that the tab exists is kept.
    ("PUT", "/api/remote/quiet/{screen}"): Case(
        Policy.AUTHENTICATED, params={"screen": "a-matrix-screen"}
    ),
    ("DELETE", "/api/remote/quiet/{screen}"): Case(
        Policy.AUTHENTICATED, params={"screen": "a-matrix-screen"}
    ),
    # Backup: an export copies the entire database whatever the resolver says, and a restore
    # replaces it. The restore is called with no file, so an admin gets 422 past the door.
    ("POST", "/api/backup/export"): Case(Policy.ADMIN),
    ("POST", "/api/backup/restore"): Case(Policy.ADMIN),
    ("GET", "/api/backup/schedule"): Case(Policy.ADMIN),
    ("PUT", "/api/backup/schedule"): Case(Policy.ADMIN),
    ("GET", "/api/backup/contents"): Case(Policy.ADMIN),
    # These read the backup folder, which describes the machine.
    ("GET", "/api/backup/unmarked"): Case(Policy.ADMIN),
    # Removes what the seeding put there, so the copy below is asked for first.
    ("DELETE", "/api/backup/unmarked/{name}"): Case(
        Policy.ADMIN, params={"name": AN_UNMARKED_BACKUP}, destructive=True
    ),
    ("GET", "/api/backup/saved/{name}"): Case(Policy.ADMIN, params={"name": AN_UNMARKED_BACKUP}),
    # The libraries: the list names folders on the machine, and a switch takes the install off the
    # air. With no body or supervisor an admin gets 422 or 409, past the door.
    ("GET", "/api/libraries"): Case(Policy.ADMIN),
    ("POST", "/api/libraries"): Case(Policy.ADMIN),
    ("POST", "/api/libraries/open"): Case(Policy.ADMIN),
    ("POST", "/api/libraries/import"): Case(Policy.ADMIN),
    ("POST", "/api/libraries/delete"): Case(Policy.ADMIN),
    ("POST", "/api/libraries/forget"): Case(Policy.ADMIN),
    ("PUT", "/api/libraries/opens-at-start"): Case(Policy.ADMIN),
    # The desktop app's computer is the install as a whole; no app started this backend, so 409.
    ("GET", "/api/desktop"): Case(Policy.ADMIN),
    ("PUT", "/api/desktop/start-with-windows"): Case(Policy.ADMIN),
    ("GET", "/api/desktop/firewall"): Case(Policy.ADMIN),
    ("POST", "/api/desktop/firewall"): Case(Policy.ADMIN),
    ("PUT", "/api/desktop/sharing"): Case(Policy.ADMIN, body={"on": True}),
    ("GET", "/api/desktop/storage"): Case(Policy.ADMIN),
    ("POST", "/api/desktop/storage/move"): Case(Policy.ADMIN, body={"folder": "E:\\Sift"}),
    ("POST", "/api/desktop/update"): Case(Policy.ADMIN),
    ("GET", "/api/desktop/log"): Case(Policy.ADMIN),
    ("GET", "/api/desktop/libraries"): Case(Policy.ADMIN),
    ("POST", "/api/desktop/libraries/open"): Case(
        Policy.ADMIN, body={"data_dir": "D:\\Other\\data"}
    ),
    ("GET", "/api/stash-migration"): Case(Policy.ADMIN),
    ("GET", "/api/stash-migration/waiting"): Case(Policy.ADMIN),
    ("POST", "/api/stash-migration/read"): Case(Policy.ADMIN),
    ("POST", "/api/stash-migration/run"): Case(Policy.ADMIN),
    ("POST", "/api/stash-migration/new-library"): Case(Policy.ADMIN),
    # A library, its Users and its passwords are the install's.
    ("GET", "/api/libraries/duplicate"): Case(Policy.ADMIN),
    ("POST", "/api/libraries/duplicate"): Case(Policy.ADMIN),
    # The update check: the route that makes the server open a connection out.
    ("GET", "/api/update/check"): Case(Policy.ADMIN),
    ("POST", "/api/update/dismiss"): Case(Policy.ADMIN),
    # Makes no outbound request; signed in rather than PUBLIC, because a version number tells an
    # attacker which published flaws apply.
    ("GET", "/api/update/version"): Case(Policy.AUTHENTICATED),
    # Faces: READING is any signed-in user's and scoped inside, as tags on a file are; an appearance
    # whose person the viewer hid comes back unnamed. Deciding, and the queue, are an admin's.
    ("GET", "/api/assets/{asset_id}/faces"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
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
    # --- Get to know Sift: each path is worked out for whoever asks.
    ("GET", "/api/insights/path"): Case(Policy.AUTHENTICATED),
    # --- Insights: every read is narrowed to the asker; what Sift did is left out of a guest's page
    # inside the route.
    ("GET", "/api/insights"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/insights/visits"): Case(Policy.AUTHENTICATED),
    ("DELETE", "/api/insights/history"): Case(Policy.AUTHENTICATED),
    # --- Recaps: asked with the caller's id beside the recap's, so another User's recap answers the
    # 404 an unminted id does. The sweep's users own none, so both roles get that 404.
    ("GET", "/api/insights/recaps"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/insights/recaps/{recap_id}"): Case(
        Policy.AUTHENTICATED, params={"recap_id": A_RECAP}, answers_anyway=404
    ),
    ("POST", "/api/insights/recaps/{recap_id}/dismiss"): Case(
        Policy.AUTHENTICATED, params={"recap_id": A_RECAP}, answers_anyway=404
    ),
    # The browser client holds nothing; every endpoint it calls decides for itself.
    ("GET", "/{path:path}"): Case(Policy.PUBLIC, params={"path": ""}),
}

#: What being turned away looks like. Which of these a route returns is the route's business:
#: an object nobody may see is a 404 rather than a 403, because a 403 confirms it exists.
DENIED = frozenset({401, 403, 404})

#: Named once so the app-lock tests can set a PIN with it.
MATRIX_PASSWORD = "Matrix-Test-Passw0rd!"

#: HEAD runs the GET handler and OPTIONS is answered before any handler, so the matrix is written
#: in handlers rather than verbs.
MIRRORED_METHODS = frozenset({"HEAD", "OPTIONS"})

#: A path placeholder, `{id}` or `{path:path}`: str.format reads `:path` as a format spec.
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^{}]+)?\}")


def fill(path: str, params: Mapping[str, str], query: Mapping[str, str] | None = None) -> str:
    """Substitute a route's placeholders from a Case's params, and add anything it requires."""

    def one(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(
                f"{path} has a {{{name}}} placeholder and its matrix entry gives no value for it; "
                f"add params={{'{name}': ...}} to its Case."
            )
        return params[name]

    filled = _PLACEHOLDER.sub(one, path)
    if not query:
        return filled
    return f"{filled}?{urlencode(query)}"


def _leaf_routes(routes: object, prefix: str = "") -> Iterator[tuple[str, object]]:
    """Every route with a path and methods, descending through mounts and included routers.

    Yields the path the app really answers on: newer FastAPI keeps an included router as a wrapper
    holding the prefix, so the prefix is carried down."""
    for route in routes:  # type: ignore[attr-defined]
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            yield from _leaf_routes(
                included.routes, prefix + (getattr(context, "prefix", "") or "")
            )
        elif hasattr(route, "routes") and not hasattr(route, "methods"):
            yield from _leaf_routes(route.routes, prefix + str(getattr(route, "path", "")))
        else:
            yield prefix + str(getattr(route, "path", "")), route


def _methods_of(route: object) -> set[str]:
    """The ways a route can be called, as the matrix names them.

    A WebSocket route has no `methods`; it is recognized by its websocket-scope ASGI handler.
    """
    methods = getattr(route, "methods", None)
    if methods:
        return {method for method in methods if method not in MIRRORED_METHODS}
    if isinstance(route, APIWebSocketRoute | WebSocketRoute):
        return {WEBSOCKET}
    return set()


def mounted_routes(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method, path) for path, route in _leaf_routes(app.routes) for method in _methods_of(route)
    }


def seed_the_library(db_path: Path, tmp_path: Path) -> None:
    """Everything the routes in `MATRIX` are asked about, written into one booted app."""
    seed_job(db_path, FAILED_JOB, state="failed")
    seed_job(db_path, CANCELABLE_JOB, state="blocked")
    seed_run(db_path, A_FINISHED_RUN)
    seed_root(db_path, A_ROOT, folder_id=A_FOLDER, path=tmp_path / "library")
    seed_grant(db_path, A_GRANT, path=tmp_path / "granted")
    seed_root(db_path, DELETABLE_ROOT, folder_id=DELETABLE_FOLDER, path=tmp_path / "library-two")
    seed_asset(
        db_path,
        AN_ASSET,
        root_id=A_ROOT,
        folder_id=A_FOLDER,
        root_path=tmp_path / "library",
        cache_dir=tmp_path / "cache",
    )
    seed_asset(
        db_path,
        DELETABLE_ASSET,
        root_id=A_ROOT,
        folder_id=A_FOLDER,
        root_path=tmp_path / "library",
        cache_dir=tmp_path / "cache",
        filename="deletable.mp4",
    )
    seed_move(db_path, A_MOVE, root_id=A_ROOT)
    seed_decision(db_path, A_DECISION)
    seed_claim(db_path, A_SUGGESTION, folder_id=A_FOLDER, proposed="Seeded Claim")
    seed_claim(db_path, ANOTHER_SUGGESTION, folder_id=A_FOLDER, proposed="Second Claim")
    seed_job(db_path, A_SAMPLE_JOB, state="done", job_type=COMPRESS_SAMPLE)
    samples = tmp_path / "cache" / SAMPLES_DIRECTORY
    samples.mkdir(parents=True, exist_ok=True)
    (samples / f"{A_SAMPLE_JOB}.mp4").write_bytes(b"not really a video")
    seed_tag(db_path, A_TAG)
    # On the visible asset, or the by-id read would answer by the scoping rule.
    attach_tag(db_path, AN_ASSET, A_TAG)
    seed_art(db_path, ART_SITE, cache_dir=tmp_path / "cache")
    seed_art(db_path, ART_CREATOR_SCOPE, cache_dir=tmp_path / "cache")
    seed_face(db_path, A_FACE, AN_ASSET, data_dir=tmp_path / "data")
    seed_person(db_path, A_PERSON)
    seed_person(db_path, MERGE_FROM)
    seed_person(db_path, MERGE_INTO)
    seed_person(db_path, A_LINKED_PERSON)
    # On the visible asset, so the people routes answer by their own rule.
    attach_person(db_path, AN_ASSET, A_PERSON)
    seed_alias(db_path, AN_ALIAS, A_PERSON)
    seed_link(db_path, A_LINK, A_PERSON)
    seed_site(db_path, A_SITE)
    seed_username(db_path, A_USERNAME, A_SITE)
    attach_username(db_path, AN_ASSET, A_USERNAME)
    seed_collection(db_path, A_COLLECTION, holding=AN_ASSET)
    seed_photo_set(db_path, A_PHOTO_SET, holding=AN_ASSET)
    seed_photo_set(db_path, A_DELETABLE_PHOTO_SET, name="deletable photo set", holding=AN_ASSET)
    seed_song(db_path, A_SONG, holding=AN_ASSET)
    seed_song(db_path, A_DELETABLE_SONG, name="deletable song")
    seed_song(db_path, A_MERGED_SONG, name="merged song")
    seed_cover_picture(
        db_path,
        A_COVER_PICTURE,
        cache_dir=tmp_path / "cache",
        wearing={
            "people": A_PERSON,
            "sites": A_SITE,
            "tags": A_TAG,
            "collections": A_COLLECTION,
            "photo_sets": A_PHOTO_SET,
            "songs": A_SONG,
        },
    )
    seed_loop(db_path, A_LOOP, AN_ASSET)
    seed_stash_box(db_path, A_STASH_BOX)
    seed_stash_box_link(db_path, A_LINKED_PERSON, A_STASH_BOX)
    seed_quarantined(tmp_path / "data", A_QUARANTINED)
    # Read from the folder used when none is chosen.
    (tmp_path / "data" / "backups").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "backups" / AN_UNMARKED_BACKUP).write_bytes(b"seeded backup")
    seed_stash_box(
        db_path,
        A_DELETABLE_STASH_BOX,
        name="deletable stash-box",
        endpoint="https://other-stash-box.invalid/graphql",
    )
    # The Loop's own still; the job that builds one does not run here.
    seed_loop_still(db_path, tmp_path / "cache", AN_ASSET)
    seed_collection(db_path, A_DELETABLE_COLLECTION, name="deletable collection", holding=AN_ASSET)
    seed_site(db_path, A_DELETABLE_SITE, name="deletable site")
    seed_site(db_path, SITE_MERGE_FROM, name="merging site")
    seed_site(db_path, SITE_MERGE_INTO, name="surviving site")
    seed_username(db_path, A_DELETABLE_USERNAME, A_DELETABLE_SITE, name="deletable_username")
    seed_guest_user(db_path, A_GUEST_USER)
    seed_guest_user(db_path, A_DELETABLE_GUEST_USER, username="deletable guest")
    # Left at its default the capability refuses every guest, and the route would look
    # role-restricted when it is not.
    set_app_setting(db_path, "guests.can_save_to_device", "true")
    # A gate whose result depends on the machine having a network is not a gate.
    set_app_setting(db_path, "updates.check_for_new_versions", "false")


#: One app, booted and seeded once for the module, for every case that only reads.
library = shared_client(seed_the_library, name="library")


@pytest.fixture
def app(library: TestClient) -> FastAPI:
    """The shared app, for the cases that read its route table."""
    assert isinstance(library.app, FastAPI)
    return library.app


@pytest.fixture
def own_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An app and a seeded library of the case's own, for the sweep that signs in.

    A deleted backup would go to the real Recycle Bin of whoever runs the suite on Windows, so the
    drive is told it has none.
    """
    monkeypatch.setattr(recycle, "has_recycle_bin", lambda _place: False)
    with booted(tmp_path, seed_the_library) as client:
        yield client


@pytest.fixture
def own_app(tmp_path: Path) -> Iterator[TestClient]:
    """An app of the case's own and no library: a locked session is refused before any lookup."""
    with booted(tmp_path) as client:
        yield client


@pytest.fixture
def app_to_plant_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """An app of the case's own, never started, for a case that plants a route."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


def call(
    client: TestClient, method: str, path: str, body: Mapping[str, object] | None = None
) -> int:
    """Make the request and report a status, whatever protocol it took.

    A WebSocket is accepted or closed, so its answer is translated into a status and held to the
    same table as every other route.
    """
    if method != WEBSOCKET:
        if body is None:
            return client.request(method, path).status_code
        return client.request(method, path, json=body).status_code
    try:
        with client.websocket_connect(path):
            return 200
    except WebSocketDisconnect:
        return 403


def sign_in(client: TestClient, viewer: Viewer) -> str:
    """Give the client a real session of a fresh user of this role.

    The user and session rows are written as the auth slice writes them, so routes resolve the
    role the ordinary way. The passed viewer's id is ignored.
    """
    role = viewer.role.value
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path,
        role=role,
        username=f"matrix-{role}",
        password=MATRIX_PASSWORD,
    )
    # A folder nobody shared is *absent* to a guest, which this file cannot tell from a refusal.
    share_folder(db_path, A_FOLDER, user_id, grant_id=f"01HX{user_id[4:]}")
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


# --- the gate -------------------------------------------------------------------------------


def test_every_mounted_route_is_in_the_matrix(app: FastAPI) -> None:
    """The one that makes all the others matter."""
    undeclared = mounted_routes(app) - set(MATRIX)
    assert not undeclared, (
        f"these routes are mounted and nobody has said who may call them: {sorted(undeclared)}. "
        "Add a line to MATRIX saying whether it is public, for any signed-in user, or admin-only."
    )


def test_the_matrix_has_no_entries_for_routes_that_are_gone(app: FastAPI) -> None:
    """A stale entry is a claim about a route that no longer exists, and it reads as coverage."""
    stale = set(MATRIX) - mounted_routes(app)
    assert not stale, f"the matrix describes routes that are not mounted: {sorted(stale)}"


def test_the_route_walk_agrees_with_the_schema(app: FastAPI) -> None:
    """Everything the generated schema lists is something the route walk found.

    The walk depends on where the framework keeps an included router's prefix; the schema is the
    supported answer. Not the reverse: the client's pages are served outside the schema.
    """
    from_schema = {
        (method.upper(), path)
        for path, operations in app.openapi().get("paths", {}).items()
        for method in operations
        if method.upper() not in MIRRORED_METHODS
    }
    missed = from_schema - mounted_routes(app)
    assert not missed, (
        f"the schema says the app serves {sorted(missed)} and the route walk did not find them. "
        "The walk has drifted from how the framework stores routes, so every path it reports is "
        "suspect: fix `_leaf_routes` before trusting this matrix again."
    )


def check(method: str, path: str, case: Case, status: int, *, role: Role | None) -> None:
    """Compare one answer with what the matrix promised; raises AssertionError if it disagrees.

    A function, so it can be pointed at a deliberately broken route and shown to notice.
    """
    who = "an anonymous caller" if role is None else f"a {role.value}"
    denied = status in DENIED

    allowed_to_call = case.policy is Policy.PUBLIC or (
        role is not None and (case.policy is Policy.AUTHENTICATED or role is Role.ADMIN)
    )

    if allowed_to_call:
        # Read as an answer rather than a refusal, and only here. See `Case.answers_anyway`.
        denied = denied and status != case.answers_anyway
        assert not denied, (
            f"{method} {path} is declared {case.policy.value} but turned {who} away ({status})"
        )
    else:
        assert denied, (
            f"{method} {path} is declared {case.policy.value} but served {who} ({status})"
        )


@pytest.mark.parametrize(
    ("method", "path"),
    sorted(MATRIX),
    ids=[f"{method} {path}" for method, path in sorted(MATRIX)],
)
def test_every_route_answers_an_anonymous_caller_the_way_the_matrix_says(
    library: TestClient, method: str, path: str
) -> None:
    """No cookie, no header, no session: the request a stranger with curl makes, the jar cleared."""
    library.cookies.clear()
    assert CSRF_HEADER_NAME not in library.headers
    case = MATRIX[(method, path)]
    check(
        method,
        path,
        case,
        call(library, method, fill(path, case.params, case.query), case.body),
        role=None,
    )


# --- the gate can fail ------------------------------------------------------------------------


def test_the_addresses_retired_with_the_old_words_answer_as_unknown(library: TestClient) -> None:
    """The old addresses for sites, usernames and sign-in accounts are retired, not redirected.

    An old address answers 404, or where it now matches another route's shape (`/assets/platforms`
    is the file called `platforms`), that route's refusal of a stranger. Never a move.
    """
    for was in (
        "/platforms",
        "/platforms/merge",
        "/assets/platforms",
        "/accounts/some-id",
        "/auth/accounts",
        "/sharing/accounts",
    ):
        answer = library.get(API_PREFIX + was, follow_redirects=False)
        assert answer.status_code in (401, 404), was
        assert "location" not in answer.headers, was


def test_a_route_nobody_declared_fails_the_gate(app_to_plant_on: FastAPI) -> None:
    """A route planted on the real application fails the check that guards the build."""

    @app_to_plant_on.get("/planted-by-a-test")
    async def planted() -> dict[str, str]:
        return {}

    undeclared = mounted_routes(app_to_plant_on) - set(MATRIX)
    assert undeclared == {("GET", "/planted-by-a-test")}


def test_a_websocket_nobody_declared_fails_the_gate(app_to_plant_on: FastAPI) -> None:
    """A planted WebSocket fails the gate too.

    A socket has no HTTP methods, and OpenAPI does not describe WebSockets, so the schema
    cross-check cannot catch one."""

    @app_to_plant_on.websocket("/planted-socket")
    async def planted(websocket: WebSocket) -> None:  # pragma: no cover
        await websocket.accept()

    undeclared = mounted_routes(app_to_plant_on) - set(MATRIX)
    assert undeclared == {(WEBSOCKET, "/planted-socket")}


def test_a_route_that_disappears_is_noticed(app: FastAPI) -> None:
    """The other direction: an entry describing a route that is not there."""
    matrix = {**MATRIX, ("GET", "/removed-last-week"): Case(Policy.ADMIN)}
    assert set(matrix) - mounted_routes(app) == {("GET", "/removed-last-week")}


def test_a_path_with_a_converter_can_be_asked_for() -> None:
    """`fill` handles a converter: str.format reads `{path:path}`'s second half as a format spec."""
    assert fill("/{path:path}", {"path": "browse"}) == "/browse"
    assert fill("/{path:path}", {"path": ""}) == "/"
    assert fill("/assets/{asset_id}", {"asset_id": "01HX"}) == "/assets/01HX"
    assert fill("/health", {}) == "/health"

    with pytest.raises(ValueError, match="Invalid format specifier"):
        "/{path:path}".format(path="browse")

    with pytest.raises(KeyError, match="gives no value for it"):
        fill("/{asset_id}", {})


def test_the_matrix_notices_an_admin_route_that_serves_a_guest() -> None:
    """`check` refuses an admin-only route that answers a guest."""
    case = Case(Policy.ADMIN)

    # The route forgot its check and returned 200 to a guest.
    with pytest.raises(AssertionError, match="served a guest"):
        check("GET", "/jobs", case, 200, role=Role.GUEST)

    # The same route, doing its job.
    check("GET", "/jobs", case, 403, role=Role.GUEST)
    check("GET", "/jobs", case, 200, role=Role.ADMIN)
    check("GET", "/jobs", case, 401, role=None)


def test_the_matrix_notices_a_public_route_that_turns_everyone_away() -> None:
    """Declaring an admin-only route public to dodge the role check fails the anonymous check."""
    with pytest.raises(AssertionError, match="turned an anonymous caller away"):
        check("GET", "/jobs", Case(Policy.PUBLIC), 401, role=None)


# --- each role, on a library of its own


def signed_in_as(client: TestClient, role: Role, user_id: str) -> bool:
    """Whether the client still holds an open, unlocked session of this user and role."""
    answer = client.get("/api/auth/me")
    if answer.status_code != 200:
        return False
    me = answer.json()
    return me["id"] == user_id and me["role"] == role.value and not me["locked"]


@pytest.mark.parametrize("role", list(Role))
def test_every_route_answers_each_role_the_way_the_matrix_says(
    own_library: TestClient, role: Role
) -> None:
    """Signed in as each kind of user, every route that needs a session answers as declared.

    Not parametrized per route: an empty parameter set makes pytest skip, and a skipped
    authorization gate reads like a passed one.
    """
    protected = sorted(
        (entry for entry in MATRIX if MATRIX[entry].policy is not Policy.PUBLIC),
        # Destructive last, then by name: one boot for the whole sweep.
        key=lambda entry: (MATRIX[entry].destructive, entry),
    )
    if not protected:
        return

    viewer = Viewer(id=role.value, role=role)
    user_id = sign_in(own_library, viewer)
    for method, path in protected:
        # A route that ends or locks its session (logout does) is followed by a fresh one.
        if not signed_in_as(own_library, role, user_id):
            user_id = sign_in(own_library, viewer)
        case = MATRIX[(method, path)]
        check(
            method,
            path,
            case,
            call(own_library, method, fill(path, case.params, case.query), case.body),
            role=role,
        )


#: A tag reaching no file a guest may see; only the case below seeds it, in its own library.
AN_UNSEEN_TAG = "01HX0000000000000000000098"

#: An id shaped like a tag's that names nothing.
AN_UNKNOWN_TAG = "01HX00000000000000000000T9"


def test_a_guest_cannot_tell_an_unseen_tag_from_an_unknown_one(own_library: TestClient) -> None:
    """The per-user tag writes answer an unseen tag exactly as one that is not there.

    The tag is looked up the way the Tags wall looks it up, or trying ids would teach which exist.
    The tag the guest CAN see answers 204, so the 404s are the scoping rule.
    """
    db_path = own_library.app.state.database.path  # type: ignore[attr-defined]
    seed_tag(db_path, AN_UNSEEN_TAG, name="unseen")
    sign_in(own_library, Viewer(id=Role.GUEST.value, role=Role.GUEST))

    def vault(tag_id: str) -> int:
        return own_library.put(f"/api/tags/{tag_id}/vault", json={"vault": False}).status_code

    assert vault(A_TAG) == 204
    assert vault(AN_UNSEEN_TAG) == vault(AN_UNKNOWN_TAG) == 404


# --- the app lock, swept the same way
#
# The lock is a claim about the SERVER: while a session is locked, every authenticated route on it
# is refused, whatever client holds the cookie. So it is asked of every mounted route.

#: The routes a locked session may still call: the PIN and the password unlocks, signing out,
#: `me` (what the lock screen reads; nothing about the library), and locking, so a panic control
#: pressed twice is no worse than pressed once.
UNLOCKED_WHILE_LOCKED = frozenset(
    {
        ("POST", "/api/auth/lock"),
        ("POST", "/api/auth/unlock"),
        ("POST", "/api/auth/unlock/password"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/me"),
    }
)

#: Its own status, so a client can tell "shut" from "signed out" and draw a PIN box.
LOCKED = 423


def lock_this_session(client: TestClient) -> None:
    """Lock Sift on the session the client holds, through the real route.

    With the setting off, locking ends the session; the shape under test is a session shut but
    alive, so the setting is turned on first.
    """
    pin = client.put("/api/auth/pin", json={"pin": "246810", "current_password": MATRIX_PASSWORD})
    assert pin.status_code == 204, pin.text
    allowed = client.put("/api/settings", json={"values": {"vault.app_lock_enabled": True}})
    assert allowed.status_code == 204, allowed.text
    assert client.post("/api/auth/lock").json()["outcome"] == "locked"


def test_a_locked_session_is_refused_every_authenticated_route(own_app: TestClient) -> None:
    """Every authenticated or admin route answers 423 to a locked session's credential.

    Replayed straight at the API, with no browser. Public routes answer a stranger anyway.
    """
    sign_in(own_app, Viewer(id="", role=Role.ADMIN))
    lock_this_session(own_app)

    served: list[str] = []
    for (method, path), case in sorted(MATRIX.items()):
        if case.policy is Policy.PUBLIC or (method, path) in UNLOCKED_WHILE_LOCKED:
            continue
        if method == WEBSOCKET:
            # A socket is refused by being closed, which `call` reports as 403.
            assert call(own_app, method, fill(path, case.params, case.query), case.body) != 200, (
                f"{method} {path}"
            )
            continue
        status_code = call(own_app, method, fill(path, case.params, case.query), case.body)
        if status_code != LOCKED:
            served.append(f"{method} {path} -> {status_code}")

    assert not served, (
        "a locked session reached these routes; the lock is a cover rather than a lock: "
        + ", ".join(served)
    )


def test_the_locked_sweep_notices_a_route_that_lets_a_locked_session_through(
    own_app: TestClient,
) -> None:
    """A route excused from the lock through the allow-list is shown answering while locked."""
    sign_in(own_app, Viewer(id="", role=Role.ADMIN))
    lock_this_session(own_app)

    assert own_app.get("/api/auth/me").status_code == 200
    assert own_app.get("/api/assets").status_code == LOCKED


def test_a_locked_session_can_still_sign_out(own_app: TestClient) -> None:
    """Signing out needs no PIN, so a forgotten PIN is never a lock on your own library."""
    sign_in(own_app, Viewer(id="", role=Role.ADMIN))
    lock_this_session(own_app)

    assert own_app.post("/api/auth/logout").status_code == 204
    assert own_app.get("/api/auth/me").status_code == 401


def test_a_pin_is_never_a_way_in_for_a_client_with_no_session(own_app: TestClient) -> None:
    """The PIN unlocks and never authenticates: a client with no session gets nothing from it."""
    sign_in(own_app, Viewer(id="", role=Role.ADMIN))
    assert (
        own_app.put(
            "/api/auth/pin", json={"pin": "246810", "current_password": MATRIX_PASSWORD}
        ).status_code
        == 204
    )
    signed_out = TestClient(own_app.app)

    assert signed_out.post("/api/auth/unlock", json={"pin": "246810"}).status_code in DENIED
    assert signed_out.get("/api/auth/me").status_code == 401
    assert signed_out.get("/api/assets").status_code == 401


# --- one app at a time


def test_booting_an_app_stops_the_shared_one_and_its_client_says_so(
    library: TestClient, own_app: TestClient
) -> None:
    """The shared library and a case's own app never run side by side.

    A boot takes over the process's thread pools and its shutdown closes them, so a shared app is
    stopped, and its client refuses, rather than answering from a closed pool.
    """
    assert library.portal is None
    with pytest.raises(RuntimeError, match="another app booted after it"):
        library.get("/health")
    assert own_app.get("/health").status_code == 200
