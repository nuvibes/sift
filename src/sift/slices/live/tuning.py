# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers the live feed runs on.

None is a setting: they describe what a live connection is, and two are security controls, which a
screen must not be able to raise.
"""

from __future__ import annotations

from sift.kernel.changes import LIVE_INTERVAL_SECONDS

#: How often an open connection is looked at, and its permission re-read: the job feed's beat, so a
#: revoked permission outlives itself by the same interval on every socket.
BEAT_SECONDS = LIVE_INTERVAL_SECONDS

#: How long one push may wait on a reader that is not reading: one beat, since a connection that
#: cannot take a message before the next is due will not catch up (the client resyncs on a close).
SEND_DEADLINE_SECONDS = BEAT_SECONDS

#: Open connections one user may hold, a security control since each costs reads every second: far
#: above a person's tabs and devices, far below a script. `GET /live` answers 429 here.
MAX_CONNECTIONS_PER_USER = 16

#: The `Retry-After` sent with that 429: the cap clears when a tab elsewhere closes, which is a
#: human timescale.
FULL_AGAIN_SECONDS = 30
