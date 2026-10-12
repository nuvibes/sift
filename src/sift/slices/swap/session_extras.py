# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the manifest screens draw of a running session from memory: its facts and extras."""

from __future__ import annotations

from dataclasses import dataclass

from sift.slices.swap.frames import Chunk as Chunk
from sift.slices.swap.frames import Conn as Conn
from sift.slices.swap.frames import ProtocolError as ProtocolError
from sift.slices.swap.handshake import (
    _STATE_OF as _STATE_OF,
)
from sift.slices.swap.handshake import (
    CUT_OFF_NOTE as CUT_OFF_NOTE,
)
from sift.slices.swap.handshake import (
    DISK_FULL as DISK_FULL,
)
from sift.slices.swap.handshake import (
    ENDED_BY_THEM as ENDED_BY_THEM,
)
from sift.slices.swap.handshake import (
    EXPIRED as EXPIRED,
)
from sift.slices.swap.handshake import (
    OLDER as OLDER,
)
from sift.slices.swap.handshake import (
    PACE_WINDOWS as PACE_WINDOWS,
)
from sift.slices.swap.handshake import (
    REASON_DONE as REASON_DONE,
)
from sift.slices.swap.handshake import (
    REASONS as REASONS,
)
from sift.slices.swap.handshake import (
    REDIAL_LIMIT as REDIAL_LIMIT,
)
from sift.slices.swap.handshake import (
    STREAMS_CAP as STREAMS_CAP,
)
from sift.slices.swap.handshake import (
    STREAMS_START as STREAMS_START,
)
from sift.slices.swap.handshake import (
    USED as USED,
)
from sift.slices.swap.handshake import (
    WRONG_DEVICE as WRONG_DEVICE,
)
from sift.slices.swap.handshake import (
    code_of as code_of,
)
from sift.slices.swap.handshake import (
    hello as hello,
)
from sift.slices.swap.handshake import (
    read_hello as read_hello,
)
from sift.slices.swap.live import (
    Ladder as Ladder,
)
from sift.slices.swap.live import (
    Watchdog as Watchdog,
)
from sift.slices.swap.manifest import (
    Grouping,
    Progress,
    held_as,
    manifest_of,
)
from sift.slices.swap.models import (
    CrossingNow,
    ManifestGroup,
    Offer,
    OfferScreen,
    RefusedFile,
)
from sift.slices.swap.receipt import filed_here
from sift.slices.swap.receiving import (
    _Receiving,
)
from sift.slices.swap.sending import _Sending


@dataclass(frozen=True, slots=True)
class LiveFacts:
    """What only memory knows about a running session: for the screen, never for a row."""

    code: str | None
    token: str | None
    wanted_bytes: int
    moved_bytes: int
    #: The session's pace (`_Live.pace_bps`), the rate its estimate reads.
    rate_bps: int | None
    #: The offer screen of what the other side offered, once its offer has arrived and been read:
    #: the guest's, and the host's too in a swap that sends and receives.
    screen: OfferScreen | None
    unwanted: int
    #: Whether files go both ways, and whether this side has answered the other's offer.
    two_way: bool = False
    answered: bool = False
    #: The direction from the guest to the host, in a swap that sends and receives: what was
    #: wanted, what has moved, its pace, and whether its files are moving yet.
    back_wanted_bytes: int = 0
    back_moved_bytes: int = 0
    back_rate_bps: int | None = None
    back_moving: bool = False
    #: Whether the files from the host to the guest are moving yet.
    moving: bool = False
    #: The files RECEIVED so far, each counted when its last piece is in and the whole file's digest
    #: matches (`_Receiving.verified`): on the guest, from the host; with `back_`, on the host of a
    #: swap that sends and receives, from the guest. Landing (the strip, the gate, the import, the
    #: filing) comes after, one file at a time, and the row counts a file only once it is filed, so
    #: the receiver's screen says both and the two meet at the end.
    received_files: int = 0
    back_received_files: int = 0
    #: The streams carrying each direction: host to guest, and with `back_`, guest to host. A
    #: stream carries one direction only.
    streams: int | None = None
    back_streams: int | None = None


@dataclass(frozen=True, slots=True)
class LiveExtras:
    """What the manifest screens draw of a running session beyond its counts, from memory."""

    manifest: list[ManifestGroup] | None = None
    now: CrossingNow | None = None
    streams: int | None = None
    moving_since: int | None = None
    token_expires: int | None = None
    refused: tuple[RefusedFile, ...] = ()
    refused_count: int = 0
    kept_files: int = 0
    kept_bytes: int = 0
    retry_at: int | None = None


def crossing_now(
    offer: Offer, keys: tuple[tuple[str, ...], ...], index: int | None
) -> CrossingNow | None:
    if index is None or not 0 <= index < len(offer.files):
        return None
    one = offer.files[index]
    return CrossingNow(title=one.title, size=one.size, group=keys[index][0])


def receiving_extras(
    receiving: _Receiving, offer: Offer, groups: Grouping
) -> tuple[list[ManifestGroup], CrossingNow | None]:
    screen = receiving.screen
    rows = {} if screen is None else {row.index: row for row in (screen.everyone or screen.rows)}
    held = held_as({} if screen is None else {one.key: one.reason for one in screen.held})
    settled = receiving.done_files | receiving.failed_files
    # Verified and waiting to be filed is still on its way: it has not landed yet.
    crossing = set(receiving.incoming) | (receiving.verified - settled)
    rows_now = manifest_of(
        offer,
        groups,
        Progress(
            answered=receiving.diff is not None,
            wanted=receiving.wanted,
            done=receiving.done_files,
            failed=receiving.failed_files,
            crossing=crossing,
        ),
        held=held,
        rows=rows,
        local=filed_here(receiving, offer, groups)[0],
    )
    now = next((index for index in reversed(receiving.opened) if index in crossing), None)
    return rows_now, crossing_now(offer, groups.of_file, now)


def sending_extras(
    sending: _Sending, offer: Offer, groups: Grouping
) -> tuple[list[ManifestGroup], CrossingNow | None]:
    rows_now = manifest_of(
        offer,
        groups,
        Progress(
            answered=sending.answered,
            wanted=sending.wanted,
            done=sending.done_files,
            failed=sending.failed_files,
            crossing=sending.outgoing.keys(),
        ),
        held=None,
    )
    now = next(reversed(sending.outgoing), None) if sending.outgoing else None
    return rows_now, crossing_now(offer, groups.of_file, now)
