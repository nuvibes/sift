# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making a browser forget every picture it was given.

Generated pictures are served at an address naming their contents, so a browser keeps its copy for
a week without asking; a copy served from its own store reaches no permission check. So concealed
things are never given a keepable address, and everything else carries this per-user number in its
address: raising it makes every address that user holds unreachable immediately. Blunt on purpose (one
hide re-fetches the whole grid once) because a stamp per file would walk a whole tree on every hide.
The change for everyone a tag, person, collection or site decides for reads the grants table, so it
lives in `sift.kernel.access.stamps`.

Every function takes the caller's open write connection, so the change and the raise land together
or not at all (a hide committed first leaves its pictures reachable for a moment), and the write
guard is not reentrant, so opening its own would deadlock. It sits in the kernel, not the access
layer, because the content layer under the access layer raises it too.
"""

from __future__ import annotations

from sift.kernel.audience import Audience
from sift.kernel.db import Connection

#: One user's number, handing back the user it touched so the change bus tells the same audience
#: without a second copy of the rule that decides who is in it.
_BUMP_ONE = "UPDATE users SET cache_stamp = cache_stamp + 1 WHERE id = ? RETURNING id"

#: Everybody's.
_BUMP_ALL = "UPDATE users SET cache_stamp = cache_stamp + 1 RETURNING id"


async def bump_cache_stamp(connection: Connection, user_id: str) -> Audience:
    """Make every picture address this one user holds unreachable, and say whose view moved.

    For a change personal to them: a hide, an un-hide, or a share made or taken away. An id naming
    nobody gives an empty audience.
    """
    return Audience.of(await connection.execute_fetchall(_BUMP_ONE, (user_id,)))


async def bump_every_cache_stamp(connection: Connection) -> Audience:
    """Make every picture address everybody holds unreachable, and say so is everybody.

    For a change nobody owns, a file leaving the library: Sift cannot know whose browser holds it.
    Affordable because it is rare and costs one re-fetch per user, and because the live beat
    coalesces, so a sweep of thousands of files is one message per connection.
    """
    return Audience.of(await connection.execute_fetchall(_BUMP_ALL))
