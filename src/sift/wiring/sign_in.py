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
    """Users and sessions, with a hasher tuned to this machine."""
    hasher = auth.Hasher(auth.resolve_argon2_params(hardware.total_ram_bytes))
    provide(app, auth.MASTER_KEYS, master_keys)

    async def may_reopen_with_pin(user_id: str) -> bool:
        """Has this user asked for its PIN to reopen a locked session? Asked across two features."""
        return bool(await hub.get_user(user_id, vault.APP_LOCK_ENABLED_KEY))

    provide(
        app,
        auth.SERVICE,
        auth.AuthService(
            store.database,
            hasher=hasher,
            master_keys=master_keys,
            # In memory only, so a restart locks every session: what lets the PIN be short.
            vault_unlocks=auth.VaultUnlockStore(),
            queue=queue,
            # The shipped length, only a fallback for the Privacy screen's setting.
            session_ttl_seconds=auth.DEFAULT_SESSION_DAYS * 24 * 60 * 60,
            may_reopen_with_pin=may_reopen_with_pin,
            # A password login can open sealed things, so running tunnels start here.
            on_key_available=tunnels.start_enabled,
        ),
    )
