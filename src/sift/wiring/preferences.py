# SPDX-License-Identifier: AGPL-3.0-or-later
"""Preference storage, and the update check built directly on it."""

from __future__ import annotations

from functools import partial
from typing import Any

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.config import get_settings
from sift.kernel.log import get_logger
from sift.kernel.wiring import provide
from sift.slices import faces, semantic, settings_hub, update_notify, watermarks
from sift.slices.download.sources.net import guarded_session
from sift.wiring.built import Storage

#: What the desktop app holds off for one start after Sift kept stopping; nothing stored changes.
HELD_OFF = frozenset({faces.ENABLED_KEY, semantic.ENABLED_KEY, watermarks.ENABLED_KEY})


class HoldingOff(settings_hub.SettingsService):
    """The settings, with the optional features read as off."""

    async def get_app(self, key: str) -> Any:
        return False if key in HELD_OFF else await super().get_app(key)


def build_preferences(
    app: FastAPI, store: Storage, release_feed: str | None
) -> settings_hub.SettingsService:
    """Preference storage, and the one thing built directly on it.

    Reads the kernel registry every slice populated at import, and reads and writes the values in
    its own tables. Built early because almost everything after this takes a reader for it: a route
    or a job reaches a setting without a database handle of its own.

    The update check is here because the one thing it remembers, the version somebody dismissed,
    is a preference. It reads a public release feed and compares versions; it cannot apply anything,
    and nothing is wired here that could. The connector is handed in rather than reached for: a
    slice does not import a slice, and the address rule that refuses a private one lives in a single
    place. This is where the two meet.
    """
    holding = get_settings().hold_optional_features
    hub = (HoldingOff if holding else settings_hub.SettingsService)(store.database)
    if holding:
        get_logger(__name__).warning("boot.optional_features_held", keys=list(HELD_OFF))
    provide(app, settings_hub.SERVICE, hub)
    provide(app, wiring.SETTINGS_HUB, hub)
    provide(
        app,
        update_notify.SERVICE,
        update_notify.UpdateService(
            hub, partial(update_notify.fetch_release, guarded_session, release_feed)
        ),
    )
    return hub
