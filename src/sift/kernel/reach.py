# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a viewer can reach, and the one wording for what they cannot, so screens agree."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, status

from sift.kernel.access import Actionable, Repository, Viewer
from sift.kernel.wire import Wire

VAULT_LOCKED = "It is in your vault. Unlock the vault to include it."
"""Why an item was left out, when the reason is the viewer's own locked vault.

One sentence, here, because seven routes and one screen have to say the same thing about the same
situation. Addressed to the person who put the file there: the vault conceals from onlookers, not
from its owner, so this tells them something they already know and can act on.

Singular. `VAULT_LOCKED_MANY` is the same sentence about more than one; see `BulkWriteDone.reason`
for why both are sent rather than the screen inflecting one.
"""

VAULT_LOCKED_MANY = "They are in your vault. Unlock the vault to include them."
"""The same refusal, about more than one item."""

OUT_OF_REACH = "Sift could not find the file."
"""Why an item was left out, for every reason that is not the vault.

Deliberately one sentence for two situations (a file that is not there, and a file belonging to
somebody else) because telling those apart is exactly what the access model refuses to do. A
message that said "you may not have this one" would confirm to a stranger that the id names
something real.

**It says what SIFT could not do, never what is or is not there, and that is the whole of the
wording.** *"There is no such file."* is a correct sentence in one of the two situations and a
false statement in the other: told to somebody being denied, it asserts that a file they can see
the padlock of does not exist. The whole point of one wording for both is that the wording gives
nothing away, and one that lies in half the cases gives away that it is lying. So there is one of
these, here, and every service imports it.

Singular; `OUT_OF_REACH_MANY` is its plural. See `BulkWriteDone.reason`.
"""

OUT_OF_REACH_MANY = "Sift could not find the files."
"""The same refusal, about more than one file."""

KEPT_LOCAL_LEFT_OUT = "It is kept local \u2014 nothing about it leaves this machine."
"""Why a file was left out of a question to a stash-box: somebody said "Do not enrich".

Here beside the other two reasons because it answers in the same place and the same shape (a
selection of five where one was held back says "Asking about 4 of 5." and then this) and the
screens pick between the singular and the plural the way they already do for the vault.

Not `OUT_OF_REACH`, which would tell somebody who marked a file kept local that Sift could not FIND
it: a lie about the one file whose whereabouts they are sure of, and no hint that the switch they
pressed is doing its job. It says where the thing stays rather
than which switch to press, because the switch may not be on the file at all: a file under a
Site, a person or a tag that is kept local is kept local too, and "Allow enrichment on it" would
send somebody to a row that changes nothing.
"""

KEPT_LOCAL_LEFT_OUT_MANY = "They are kept local \u2014 nothing about them leaves this machine."
"""The same reason, about more than one file."""


class BulkWriteDone(Wire):
    """How much a write over a selection changed, how many items it skipped, and one reason."""

    changed: int = 0
    skipped: int = 0
    #: The first skip's words for one item; only the client knows the count, so both are sent.
    reason: str | None = None
    #: The same reason worded for more than one; set exactly when `reason` is.
    reason_many: str | None = None
    #: The viewer's own vault, the one reason they can undo; a flag, as `reason` is free text.
    vault_locked: bool = False

    @classmethod
    def after(cls, actionable: Actionable, changed: int) -> BulkWriteDone:
        """The reply from what access allowed; the vault wins, the only reason one can act on."""
        if actionable.concealed:
            reason: str | None = VAULT_LOCKED
            many: str | None = VAULT_LOCKED_MANY
        elif actionable.refused:
            reason, many = OUT_OF_REACH, OUT_OF_REACH_MANY
        else:
            reason = many = None
        return cls(
            changed=changed,
            skipped=actionable.skipped,
            reason=reason,
            reason_many=many,
            vault_locked=bool(actionable.concealed),
        )


class ConcealedByVault(Exception):
    """A service's refusal that is the asker's own locked vault, so it is not told as missing."""


async def conceals(access: Repository, viewer: Viewer, asset_id: str) -> bool:
    """Whether this viewer's own vault keeps the file from them; asked on the refusal path only."""
    return not viewer.show_hidden and await access.is_concealed(viewer.id, asset_id)


def vault_locked() -> HTTPException:
    """423 for one item in the viewer's own vault, which hides from onlookers, not its owner."""
    return HTTPException(status.HTTP_423_LOCKED, VAULT_LOCKED)


async def require_reachable(
    access: Repository, viewer: Viewer, asset_id: str, missing: Callable[[], HTTPException]
) -> str:
    """The asset id if this viewer may act on it: 423 for their own vault, else `missing`."""
    asset = await access.open_asset(viewer, asset_id)
    if asset is not None:
        return asset.id
    raise await refuse_one(access, viewer, asset_id, missing)


async def refuse_one(
    access: Repository, viewer: Viewer, asset_id: str, missing: Callable[[], HTTPException]
) -> HTTPException:
    """423 for the viewer's own vault, else the caller's own 404, so no two 404s differ."""
    if await conceals(access, viewer, asset_id):
        return vault_locked()
    return missing()


__all__ = [
    "OUT_OF_REACH",
    "OUT_OF_REACH_MANY",
    "VAULT_LOCKED",
    "VAULT_LOCKED_MANY",
    "BulkWriteDone",
    "ConcealedByVault",
    "conceals",
    "refuse_one",
    "require_reachable",
    "vault_locked",
]
