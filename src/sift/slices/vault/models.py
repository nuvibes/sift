# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the vault's routes take and return."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class UnlockRequest(Wire):
    """The PIN, and nothing else.

    There is no "remember this" and no way to ask for the vault without sending one. Opening it is
    always a deliberate act, so the proof is always part of the request: a session that is already
    open still has to send the PIN again to reopen it, because the asking is the point.
    """

    pin: str = Field(min_length=1, max_length=32)


class VaultWrite(Wire):
    """Put something in the vault, or take it back out."""

    vault: bool


#: The most files one request may hide or bring back. The same ceiling every bulk write in Sift
#: declares, repeated rather than imported because a slice may not import another slice.
MAX_BULK_ASSETS = 500


class VaultMany(Wire):
    """Put a selection in the vault, or take all of it back out.

    `vault` is one value for the whole set and not a toggle each, which is the rule every other
    write over a selection follows: flipping each file into whatever it was not leaves a mixed
    selection more mixed than it started, and there is no sentence that describes what happened.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    vault: bool


class VaultState(Wire):
    """What the screen needs to know before it draws anything about the vault.

    `unlocked` is about this browser and this moment, not about the user: another device signed
    in as the same user has its own answer. `pin_set` is what the screens check before offering
    to hide anything, because hiding something with no PIN set would put it somewhere nothing can
    open.
    """

    unlocked: bool
    pin_set: bool
