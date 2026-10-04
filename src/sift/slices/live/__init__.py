# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live: telling a browser that what it is looking at has changed, without it having to ask.

Everything in Sift is scoped, and most of it changes underneath somebody. A share taken back, a
folder removed, a file put into a collection or taken out of one: each of those changes what
several screens should be showing, and none of them produces anything a screen would notice.
Without this the answer would be somebody pressing F5, or a screen asking the server the same
question over and over on a timer in case the answer had moved.

This slice is the connection and nothing else. What may be seen is the permission layer's; who a
change moved is resolved by the write that made it; the bus that holds them together is the
kernel's. What is here is one socket per browser, re-authorized on every beat, and one plain read
saying where a user stands, which is what the socket sends, so the client's types are
generated from the server's own schema rather than written twice.

Guests get this exactly as admins do, and that is the design driver rather than a later refinement:
a guest's screen goes stale in the same ways and for the same reasons.
"""

from __future__ import annotations

from sift.slices.live.router import LiveState, router
from sift.slices.live.tuning import MAX_CONNECTIONS_PER_USER

__all__ = [
    "MAX_CONNECTIONS_PER_USER",
    "LiveState",
    "router",
]
