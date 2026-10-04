# SPDX-License-Identifier: AGPL-3.0-or-later
"""Users and sessions."""

from __future__ import annotations

from fastapi import FastAPI

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobQueue
from sift.kernel.tunnels import TunnelStore
from sift.kernel.wiring import provide
from sift.slices import auth, settings_hub, vault
from sift.wiring.built import Storage


def build_auth(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    store: Storage,
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    master_keys: auth.MasterKeyStore,
    tunnels: TunnelStore,
) -> None:
    """Users and sessions.

    The hasher is tuned to this machine (a stronger box affords a costlier hash), the key store is
    empty until someone signs in, and the service reaches the queue so a login releases jobs that
    were waiting for one.
    """
    hasher = auth.Hasher(auth.resolve_argon2_params(hardware.total_ram_bytes))
    provide(app, auth.MASTER_KEYS, master_keys)

    async def may_reopen_with_pin(user_id: str) -> bool:
        """Has this user asked for its PIN to reopen a locked session?

        The setting belongs to the vault feature and the lock belongs to auth, and neither imports
        the other, so the question is assembled here, where both are in scope, and auth is handed
        something it can simply ask.
        """
        return bool(await hub.get_user(user_id, vault.APP_LOCK_ENABLED_KEY))

    provide(
        app,
        auth.SERVICE,
        auth.AuthService(
            store.database,
            hasher=hasher,
            master_keys=master_keys,
            # Which sessions have the vault open. In memory and nowhere else, so a restart brings
            # every session back locked. The PIN is short, and it is allowed to be short only
            # because it can never open anything a restart has shut.
            vault_unlocks=auth.VaultUnlockStore(),
            queue=queue,
            # The shipped length, and only ever the fallback: how long a sign-in really
            # lasts is read at the sign-in itself, from the setting on the Privacy screen.
            # See `auth.router._session_seconds`.
            session_ttl_seconds=auth.DEFAULT_SESSION_DAYS * 24 * 60 * 60,
            may_reopen_with_pin=may_reopen_with_pin,
            # A password login is the moment a sealed thing can be opened. Tunnels are the one kind
            # that has to be RUNNING rather than read on demand, so they are started here rather
            # than waiting for somebody to open a settings screen.
            on_key_available=tunnels.start_enabled,
        ),
    )
