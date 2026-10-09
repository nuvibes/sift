# SPDX-License-Identifier: AGPL-3.0-or-later
"""The answers this feature gives when it will not do what was asked, or tried and could not."""

from __future__ import annotations

from sift.kernel.reach import ConcealedByVault


class Refused(ValueError):
    """The request will not be carried out, and the message says why."""


class NotFound(Refused):
    """Nothing here for this user: "no such file" and "not yours" are one answer."""


class NotAllowed(Refused):
    """The user may see the file but may not do this to it."""


class VaultLocked(NotFound, ConcealedByVault):
    """The user's own vault is concealing it: still a `NotFound`, marked so they are told."""


class ProductionFailed(Exception):
    """The copy could not be produced once started; the message is for whoever reads the job."""
