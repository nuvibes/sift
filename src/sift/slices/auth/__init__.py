# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authentication and first-run: users, sessions, the PIN, and the master-key envelope.

This is the one slice the others are allowed to import, and only for what it exposes here: the
`current_viewer` dependency every scoped route needs, `viewer_on` for the two features that hold a
socket rather than a request, `master_key` for the one feature that decrypts saved site logins, and
the handful of vault names below. Everything else (the hashing,
the sessions table, the throttles) is this slice's own and stays behind this interface.

The vault names are here because unlocking it is a session fact, and a session is resolved in this
slice. `current_viewer` reads the unlock and the concealment mode and settles both into the viewer
every scoped query is answered against; `session_key` lets the vault feature open and close that
state for one browser; `require_vault_pin` is the refusal that stops anything being concealed by a
user with no PIN to open it again.

Importing the package registers the `sessions` schema. The `users` table is the kernel's; sessions
hang off it.
"""

from __future__ import annotations

from sift.slices.auth import schema
from sift.slices.auth.crypto import Argon2Params, Hasher, resolve_argon2_params
from sift.slices.auth.errors import LockedOut
from sift.slices.auth.keys import MASTER_KEYS, MasterKeyStore
from sift.slices.auth.router import (
    SessionViewer,
    csrf_protect,
    current_viewer,
    master_key,
    optional_viewer,
    require_admin,
    require_vault_pin,
    router,
    session_key,
    viewer_on,
)
from sift.slices.auth.service import SERVICE, AuthService
from sift.slices.auth.tuning import (
    DEFAULT_SESSION_DAYS,
    SESSION_DAYS_KEY,
    VAULT_CONCEALMENT_KEY,
    session_seconds_from,
)
from sift.slices.auth.unlocks import VaultUnlockStore

__all__ = [
    "DEFAULT_SESSION_DAYS",
    "MASTER_KEYS",
    "SERVICE",
    "SESSION_DAYS_KEY",
    "VAULT_CONCEALMENT_KEY",
    "Argon2Params",
    "AuthService",
    "Hasher",
    "LockedOut",
    "MasterKeyStore",
    "SessionViewer",
    "VaultUnlockStore",
    "csrf_protect",
    "current_viewer",
    "master_key",
    "optional_viewer",
    "require_admin",
    "require_vault_pin",
    "resolve_argon2_params",
    "router",
    "schema",
    "session_key",
    "session_seconds_from",
    "viewer_on",
]
