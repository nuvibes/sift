# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three answers this feature gives when it will not do what was asked, and the one it gives
when it tried and could not.

They live in a file of their own because two halves of this slice raise them (making a smaller
copy, and making an edited one) and a refusal named after one of the two reads as a mistake in
the other. The distinctions matter to a client rather than to a reader: each of the three becomes a
different status code and a different thing to draw.
"""

from __future__ import annotations

from sift.kernel.reach import ConcealedByVault


class Refused(ValueError):
    """The request will not be carried out, and the message says why.

    The base is the "reasonable request, and the answer is still no" case: nothing named, a
    target that cannot be met, a file of the wrong kind, a crop that falls outside the picture.
    The two below are the other two answers, and they are separate because a client acts on each
    of them differently.
    """


class NotFound(Refused):
    """There is nothing here, as far as the user asking is concerned.

    The same answer for "no such file" and "a file you may not see", deliberately: the second
    answer would let anybody confirm a file exists by asking to act on it.
    """


class NotAllowed(Refused):
    """The user may see the file but may not do this to it."""


class VaultLocked(NotFound, ConcealedByVault):
    """The one refusal a person is owed the truth about: their OWN vault is concealing it.

    A `NotFound` still, so everything that already catches one keeps working; the marker is what
    lifts the answer from "there is no such file" to "it is in your vault, and here is the way in".
    See `sift.kernel.reach.ConcealedByVault`, which carries the whole argument for why saying this
    to that one user gives nothing away.
    """


class ProductionFailed(Exception):
    """The copy could not be produced, and the message is written for whoever reads the job.

    Not a refusal. A refusal is settled before any work starts and is answered to somebody waiting
    for a reply; this is what is left when the work was started, was reasonable, and did not come
    out: a disk that filled, an ffmpeg that read the file and wrote nothing, a user who was
    deleted while its job sat in the queue.
    """
