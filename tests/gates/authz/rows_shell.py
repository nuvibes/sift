# SPDX-License-Identifier: AGPL-3.0-or-later
"""Theater, the remote, backups, libraries, the desktop shell, migration and updates."""

from __future__ import annotations

from tests.gates.authz.seeds import (
    AN_ASSET,
    AN_UNMARKED_BACKUP,
    Case,
    Policy,
)

ROWS: dict[tuple[str, str], Case] = {
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
}
