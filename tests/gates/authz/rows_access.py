# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in, users, sharing, settings, the workbench, jobs and tasks."""

from __future__ import annotations

from tests.gates.authz.seeds import (
    A_DECISION,
    A_DELETABLE_GUEST_USER,
    A_GUEST_USER,
    A_PERSON,
    CANCELABLE_JOB,
    FAILED_JOB,
    WEBSOCKET,
    Case,
    Policy,
)

ROWS: dict[tuple[str, str], Case] = {
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
    ("GET", "/api/ledger"): Case(Policy.ADMIN),
    # The jobs dashboard, admin throughout: what Sift does with the files is a picture of them.
    ("GET", "/api/jobs"): Case(Policy.ADMIN),
    ("GET", "/api/jobs/{job_id}/steps"): Case(Policy.ADMIN, params={"job_id": FAILED_JOB}),
    ("POST", "/api/jobs/{job_id}/retry"): Case(Policy.ADMIN, params={"job_id": FAILED_JOB}),
    ("POST", "/api/jobs/{job_id}/cancel"): Case(Policy.ADMIN, params={"job_id": CANCELABLE_JOB}),
    ("POST", "/api/jobs/retry-failed"): Case(Policy.ADMIN, destructive=True),
    ("POST", "/api/jobs/clear-failed"): Case(Policy.ADMIN, destructive=True),
    ("POST", "/api/jobs/pause"): Case(Policy.ADMIN, body={}),
    ("POST", "/api/jobs/resume"): Case(Policy.ADMIN, body={}),
    ("POST", "/api/jobs/cancel-work"): Case(Policy.ADMIN, destructive=True, body={"type": "scan"}),
    ("POST", "/api/jobs/cancel-all"): Case(Policy.ADMIN, destructive=True),
    ("POST", "/api/jobs/retry-canceled"): Case(Policy.ADMIN, destructive=True),
    ("POST", "/api/jobs/clear-canceled"): Case(Policy.ADMIN, destructive=True),
    ("POST", "/api/jobs/turbo-mode"): Case(Policy.ADMIN, body={"on": False}),
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
}
